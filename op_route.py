"""
AMBAR — кому показывать заказ.

Раньше ответ был один: всем сразу. Новый заказ уходил в каждый чат из
OPERATOR_IDS, потому что районный оператор — это была подпись под заказом, а не
адресат: своего входа в телеграм у него не было.

Как только у оператора появляется id (AMBAR_OPERATOR_IDS), появляется и выбор:
заказ района идёт его оператору. А вместе с выбором появляется вопрос, что
делать, когда этот человек скрылся, — и ответ здесь такой: заказ подхватывает
оператор ближайшего района. Не «кто-нибудь», а именно ближайший: подменяющему
ехать туда же, куда ехал бы свой, и лишние километры здесь не абстракция.

Старший и планшет получают всё и всегда — они на то и старшие. Скрытый режим
глушит любой чат: писать в него нельзя, даже если человек единственный, кто
остался. Сообщение всплывёт баннером и выдаст маскировку с головой.

Рассылка идёт всем чатам маршрута одновременно, а не по очереди. 17 ноя 2026
телеграм одиннадцать раз подряд ответил операторскому боту «Bad Gateway», на
каждый ответ уходило по 15 секунд, и заказ простоял 2 минуты 50 секунд: всё
это время приложение клиента ждало ответа, владельцу сторож написал только
после последней попытки, а повторять было некому. Теперь ждём одну попытку, а
не одиннадцать, а карточку нового заказа, которую телеграм не принял,
повторяем в фоне (send_order).
"""
import asyncio
import logging

import db
import config_staff as staff
from config_offices import nearest_offices, OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger("op_route")


def panic_key(name: str) -> str:
    """Ключ скрытого режима. С приставкой, потому что имена у операторов и
    водителей совпадают: Фарух водит на B1 и ведёт B3."""
    return "op:" + (name or "").strip()


async def hidden(name: str) -> bool:
    if not name:
        return False
    try:
        return bool(await db.panic_get(panic_key(name)))
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[route] скрытый режим {name}: {e}")
        return False


async def _seniors() -> list:
    """Чаты, которые получают всё: старшие и общий планшет, кроме спрятанных."""
    from api_server import OPERATOR_IDS
    out = []
    for chat in OPERATOR_IDS:
        имя = staff.operator_by_tg(chat)
        if имя and await hidden(имя):
            continue
        out.append(int(chat))
    return out


async def chats(district: str = "") -> list:
    """Кому уходит заказ этого района: список {chat_id, prefix}.

    prefix — строка, которую подменяющий должен увидеть раньше самого заказа:
    без неё он читает чужой район как свой и едет не туда.
    """
    district = (district or "").strip()
    out, взято = [], set()

    свой = staff.base_operator(district) if district else ""
    свои_чаты = staff.operator_chats(свой) if свой else []

    if свои_чаты and not await hidden(свой):
        # Все устройства этого оператора: он мог сесть за любое из них, и
        # угадывать, за каким он сейчас, — значит иногда не показать заказ.
        for чат in свои_чаты:
            out.append({"chat_id": чат, "prefix": ""})
            взято.add(чат)
    elif свои_чаты:
        # Свой спрятался — ищем ближайшего, кто на связи. Порядок задаёт
        # география, а не список: подменять должен сосед, а не дальний угол.
        for сосед in nearest_offices(district):
            имя = staff.base_operator(сосед)
            чаты = staff.operator_chats(имя) if имя else []
            if not чаты or await hidden(имя):
                continue
            где = f"{OFFICE_CODES.get(district, '')} {OFFICE_NAMES.get(district, district)}".strip()
            for чат in чаты:
                if чат in взято:
                    continue
                out.append({"chat_id": чат, "prefix":
                            f"↪️ <b>Заказ района {где}</b> — его оператор сейчас недоступен\n"})
                взято.add(чат)
            break
        else:
            log.warning(f"[route] {district}: подменить некому, заказ только старшему")

    for chat in await _seniors():
        if chat not in взято:
            out.append({"chat_id": chat, "prefix": ""})
            взято.add(chat)
    return out


async def own_chats(district: str) -> list:
    """Только оператор района — все его устройства, без старших, планшета и
    подмены соседом: так уходят вещи, которые касаются одного района и не
    требуют действия от остальных (отчёт ревизии водителя, владелец, 19 сен
    2026: «отчёт операторам только оператору района»). Оператор района — по
    сегодняшней расстановке; спрятался — никому."""
    district = (district or "").strip()
    имя = staff.DISTRICT_OPERATOR.get(district) or staff.base_operator(district)
    if not имя:
        return []
    if await hidden(имя):
        log.info(f"[route] {district}: оператор {имя} в скрытом режиме — не пишем")
        return []
    return [{"chat_id": чат, "prefix": ""} for чат in staff.operator_chats(имя)]


# Сколько запрос ждёт ответа телеграма по карточке нового заказа. Дольше нельзя:
# всё это время приложение клиента стоит на оформлении. Кто не успел ответить —
# не брошен: его ответ дождётся фон.
FIRST_WAIT = 8.0
# Когда повторять недошедшее — секунды от конца первой попытки. Десять минут:
# дольше новый заказ без оператора не живёт — его либо приняли из приложения,
# либо клиент отменил, и тогда карточка «новый заказ» уже не нужна.
RETRY_AT = (5, 15, 35, 65, 125, 185, 245, 365, 485, 605)

_BG = set()        # фоновые задачи: без ссылки их уберёт сборщик мусора посреди работы


def _bg(coro):
    t = asyncio.ensure_future(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)
    return t


def _retryable(res) -> bool:
    """Отказ, после которого стоит пробовать ещё: сбой у телеграма (5xx),
    «слишком часто» (429) или ответа нет вовсе (сеть, таймаут). 400 и 403 —
    чат не примет и со второго раза: бота заблокировали, чата нет."""
    if not res:
        return True
    try:
        code = int(res.get("error_code"))
    except (TypeError, ValueError):
        return True
    return code >= 500 or code == 429


async def _one(цель: dict, text: str, parse_mode, reply_markup, register: bool,
               retry_plain: bool) -> tuple:
    """Одна отправка в один чат: (дошло, message_id, повторять ли)."""
    from api_server import tg_send, OPERATOR_BOT_TOKEN
    from datetime import datetime, timezone
    try:
        разметка = (reply_markup(цель["chat_id"]) if callable(reply_markup)
                    else reply_markup)
        res = await tg_send(OPERATOR_BOT_TOKEN, цель["chat_id"],
                            (цель["prefix"] or "") + text,
                            parse_mode=parse_mode, reply_markup=разметка)
        if retry_plain and разметка and not (res or {}).get("ok"):
            log.warning(f"[route] {цель['chat_id']}: с кнопками не принял "
                        f"({(res or {}).get('description')}) — шлём без них")
            res = await tg_send(OPERATOR_BOT_TOKEN, цель["chat_id"],
                                (цель["prefix"] or "") + text, parse_mode=parse_mode)
    except Exception as e:                       # noqa: BLE001
        log.error(f"[route] {цель['chat_id']}: {e}")
        return False, None, True
    if not (res or {}).get("ok"):
        log.error(f"[route] {цель['chat_id']} отказ: {(res or {}).get('description')}")
        return False, None, _retryable(res)
    mid = ((res or {}).get("result") or {}).get("message_id")
    # В реестр чата: штора стирает только то, что записано при отправке.
    if register and mid:
        try:
            await db.drv_msg_add(int(цель["chat_id"]), int(mid),
                                 datetime.now(timezone.utc))
        except Exception as e:                   # noqa: BLE001
            log.debug(f"[route] реестр {цель['chat_id']}: {e}")
    return True, mid, False


def _итог(задача) -> tuple:
    try:
        return задача.result()
    except Exception as e:                       # noqa: BLE001  — _one сам ловит всё; это на крайний случай
        log.error(f"[route] отправка упала: {e}")
        return False, None, True


async def send(text: str, district: str = "", parse_mode: str = "HTML",
               reply_markup=None, register: bool = True, test: bool = False,
               retry_plain: bool = False, own_only: bool = False,
               to=None, wait: float = None, chase: dict = None) -> dict:
    """Разослать по маршруту. Возвращает {chat_id: message_id} — по ним заказ
    потом правят и по ним же чистят чат, если человек уйдёт в скрытый режим.

    Всем чатам сразу, а не по очереди: один зависший чат не держит остальных.

    reply_markup — словарь на всех или функция от chat_id: кнопка мини-аппа
    живёт только в личке, и в групповом чате её надо заменить, а не потерять
    вместе со всем сообщением.

    retry_plain — чат не принял сообщение с кнопками: отправить ещё раз без
    них. Для сообщений, где кнопка — удобство, а главное — текст.

    own_only — только оператору района (own_chats), мимо старших и планшета.

    to — готовый список чатов вместо маршрута района.

    wait — сколько секунд ждать ответов телеграма. None — дождаться всех
    (каждая отправка и так ограничена таймаутом tg_send). С числом в ответ
    попадают те, кто успел; остальные долетают в фоне.

    chase — что делать с недошедшим (только вместе с wait):
    {"alive": корутина без аргументов → ещё нужно ли слать,
     "late": корутина (chat_id, message_id, первый_ли, попытка, секунд)}.
    Недошедшее повторяется по RETRY_AT, пока alive() говорит «да»."""
    from api_server import OPERATOR_BOT_TOKEN
    out = {}
    if not OPERATOR_BOT_TOKEN:
        return out
    if to is not None:
        targets = [{"chat_id": int(c), "prefix": ""} for c in to]
    elif test:
        # Тест-заказ — только тест-операторам, минуя районы и старших.
        from config import TEST_OPERATOR_IDS
        targets = [{"chat_id": c, "prefix": ""} for c in sorted(TEST_OPERATOR_IDS)]
    else:
        targets = await (own_chats(district) if own_only else chats(district))
    if not targets:
        return out
    как = dict(text=text, parse_mode=parse_mode, reply_markup=reply_markup,
               register=register, retry_plain=retry_plain)
    t0 = asyncio.get_running_loop().time()
    задачи = [asyncio.ensure_future(_one(цель, **как)) for цель in targets]
    _, летят = await asyncio.wait(задачи, timeout=wait)
    хвост = []                 # (цель, задача или None): ещё летит — или упало, но повторить можно
    for цель, з in zip(targets, задачи):
        if з in летят:
            хвост.append((цель, з))
            continue
        ok, mid, ещё = _итог(з)
        if ok:
            out[str(цель["chat_id"])] = mid
        elif ещё:
            хвост.append((цель, None))
    if хвост and chase:
        _bg(_chase(хвост, как, chase, not out, t0))
    else:
        for _, з in хвост:
            if з is not None:
                _BG.add(з); з.add_done_callback(_BG.discard)
    return out


async def _chase(хвост: list, как: dict, chase: dict, пусто: bool, t0: float):
    """Довести недошедшее. Сначала дождаться тех, кто ещё летит с первой
    попытки: их ответ может оказаться «дошло», и слать второй раз — значит
    положить оператору две карточки. Потом повторять упавших по RETRY_AT, пока
    это ещё нужно.

    пусто — к концу ожидания не дошло никому: первый, до кого дойдёт, — событие
    (владельцу уже написали, что заказ не доставлен)."""
    loop = asyncio.get_running_loop()
    alive, late = chase.get("alive"), chase.get("late")
    дошло = 0

    async def _позже(цель, mid, попытка):
        nonlocal дошло
        дошло += 1
        if not late:
            return
        try:
            await late(цель["chat_id"], mid, пусто and дошло == 1, попытка,
                       int(round(loop.time() - t0)))
        except Exception as e:                   # noqa: BLE001
            log.error(f"[route] {цель['chat_id']}: дошло, но не записано: {e}")

    try:
        ждут = []
        for цель, з in хвост:
            if з is None:
                ждут.append(цель)
                continue
            try:
                ok, mid, ещё = await з
            except Exception as e:               # noqa: BLE001
                log.error(f"[route] отправка упала: {e}")
                ok, mid, ещё = False, None, True
            if ok:
                await _позже(цель, mid, 1)
            elif ещё:
                ждут.append(цель)
        старт = loop.time()
        for n, когда in enumerate(RETRY_AT, 2):
            if not ждут:
                return
            await asyncio.sleep(max(0.0, когда - (loop.time() - старт)))
            if alive:
                try:
                    нужно = await alive()
                except Exception as e:           # noqa: BLE001
                    log.warning(f"[route] повтор: не узнали, нужен ли ещё: {e}")
                    нужно = True
                if not нужно:
                    log.info(f"[route] повтор не нужен: осталось {len(ждут)}, дошло позже {дошло}")
                    return
            log.info(f"[route] повтор {n}: {len(ждут)}")
            итоги = await asyncio.gather(*[_one(цель, **как) for цель in ждут])
            дальше = []
            for цель, (ok, mid, ещё) in zip(ждут, итоги):
                if ok:
                    await _позже(цель, mid, n)
                elif ещё:
                    дальше.append(цель)
            ждут = дальше
        if ждут:
            log.error(f"[route] так и не дошло после {len(RETRY_AT) + 1} попыток: {len(ждут)}")
    except Exception as e:                       # noqa: BLE001
        log.error(f"[route] повтор оборвался: {e}")


async def send_order(oid: str, text: str, district: str = "", reply_markup=None,
                     test: bool = False, to=None) -> dict:
    """Карточка нового заказа операторам — то, что обязано дойти.

    Всем сразу; запрос клиента ждёт ответа телеграма не дольше FIRST_WAIT.
    Что телеграм не принял (сбой у него, сеть, «слишком часто»), повторяется в
    фоне, пока заказ ждёт оператора (status pending), — до десяти минут. Номера
    сообщений пишутся в op_msg_ids заказа по одному ключу на чат: дошедшее
    позже дописывается, а не затирает дошедшее раньше.

    Возвращает то, что дошло за FIRST_WAIT. Пусто — зовущий пишет владельцам
    «НЕ доставлен ни одному оператору»; если потом всё-таки дойдёт, владельцам
    уходит вторая строка — что дошло, с какой попытки и через сколько."""
    async def alive():
        o = await db.get_order(oid)
        return bool(o) and (o.get("status") or "") == "pending"

    async def late(chat_id, mid, первый, попытка, сек):
        if not await alive():
            # Пока карточка шла, заказ приняли из приложения или клиент его
            # отменил. «Новый заказ» в чате теперь только собьёт с толку, а
            # править её при приёме и отмене уже некому — убираем.
            log.info(f"[order] #{oid} дошёл позже, но уже не ждёт оператора — карточку убираем")
            if mid:
                from api_server import tg_delete, OPERATOR_BOT_TOKEN
                await tg_delete(OPERATOR_BOT_TOKEN, chat_id, mid)
            return
        if mid:
            await db.update_order(oid, **{f"op_msg_ids.{chat_id}": mid})
        log.info(f"[order] #{oid} дошёл позже: попытка {попытка}, через {сек} с")
        if not первый or test:
            return
        from api_server import OPERATOR_IDS
        if not OPERATOR_IDS:
            return
        try:
            from owner_routes import notify_owners_force
            await notify_owners_force(
                "orders.opFail",
                f"✅ *Заказ #{oid} дошёл до операторов.*\n"
                f"Попытка {попытка}, через {сек} с.")
        except Exception as e:                   # noqa: BLE001
            log.error(f"[owner-notif] opFail follow-up failed: {e}")

    out = await send(text, district=district, parse_mode="HTML", reply_markup=reply_markup,
                     test=test, to=to, wait=FIRST_WAIT, chase={"alive": alive, "late": late})
    if out:
        try:
            await db.update_order(oid, **{f"op_msg_ids.{c}": m for c, m in out.items()})
        except Exception as e:                   # noqa: BLE001
            log.error(f"[order] #{oid}: номера карточек не записаны: {e}")
    return out


async def missed(name: str) -> list:
    """Активные заказы районов этого оператора — чтобы отдать их после выхода
    из скрытого режима: пока он прятался, его карточки уходили соседу."""
    районы = [s["district"] for s in staff.DISTRICT_STAFF
              if s["operator"] == (name or "").strip()]
    if not районы:
        return []
    try:
        все = await db.get_all_orders()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[route] активные заказы: {e}")
        return []
    живые = ("pending", "approved")
    out = []
    for оid, o in (все or {}).items():
        if (o.get("status") or "") in живые and (o.get("office_id") or "") in районы:
            out.append({**o, "order_id": o.get("order_id") or оid})
    out.sort(key=lambda o: str(o.get("timestamp") or ""))
    return out
