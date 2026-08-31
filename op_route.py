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
"""
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
    свой_чат = staff.operator_tg(свой) if свой else 0

    if свой_чат and not await hidden(свой):
        out.append({"chat_id": свой_чат, "prefix": ""})
        взято.add(свой_чат)
    elif свой_чат:
        # Свой спрятался — ищем ближайшего, кто на связи. Порядок задаёт
        # география, а не список: подменять должен сосед, а не дальний угол.
        for сосед in nearest_offices(district):
            имя = staff.base_operator(сосед)
            чат = staff.operator_tg(имя) if имя else 0
            if not чат or чат in взято or await hidden(имя):
                continue
            где = f"{OFFICE_CODES.get(district, '')} {OFFICE_NAMES.get(district, district)}".strip()
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


async def send(text: str, district: str = "", parse_mode: str = "HTML",
               reply_markup: dict = None, register: bool = True) -> dict:
    """Разослать по маршруту. Возвращает {chat_id: message_id} — по ним заказ
    потом правят и по ним же чистят чат, если человек уйдёт в скрытый режим."""
    from api_server import tg_send, OPERATOR_BOT_TOKEN
    from datetime import datetime, timezone
    out = {}
    if not OPERATOR_BOT_TOKEN:
        return out
    for цель in await chats(district):
        try:
            res = await tg_send(OPERATOR_BOT_TOKEN, цель["chat_id"],
                                (цель["prefix"] or "") + text,
                                parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:                   # noqa: BLE001
            log.error(f"[route] {цель['chat_id']}: {e}")
            continue
        if not (res or {}).get("ok"):
            log.error(f"[route] {цель['chat_id']} отказ: {(res or {}).get('description')}")
            continue
        mid = ((res or {}).get("result") or {}).get("message_id")
        out[str(цель["chat_id"])] = mid
        # В реестр чата: штора стирает только то, что записано при отправке.
        if register and mid:
            try:
                await db.drv_msg_add(int(цель["chat_id"]), int(mid),
                                     datetime.now(timezone.utc))
            except Exception as e:               # noqa: BLE001
                log.debug(f"[route] реестр {цель['chat_id']}: {e}")
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
