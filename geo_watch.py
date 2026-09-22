"""Сторож геопозиции на смене.

Зачем
-----
Водитель на смене обязан быть виден: оператор отдаёт заказ тому, кто ближе,
и невидимая машина для него не существует. Напоминание самому водителю уже
есть (geo_nag), но напоминание — это просьба, а здесь нужно правило. Правило
такое: пропала геопозиция — старший узнаёт сразу. Замка нет: раньше
невернувшаяся геопозиция запирала вход до утра, владелец это снял
(11 сен 2026) — сообщение есть, наказания нет.

Что считаем пропажей
--------------------
Два случая, и старшему говорим, который из них:
  • трансляцию выключили — телеграм сообщает об этом сам, правкой сообщения;
  • два часа без движения — трансляция идёт, а якорь стояния
    (db.driver_pos_set) не переезжал дольше db.GEO_LOST_SEC.

Чего пропажей НЕ считаем (владелец, 11 сен 2026): редкие точки. Телефон в
кармане присылает точку раз в три-пять минут, а лежащий на столе — раз в
десять; раньше сторож через четверть часа писал «точек нет», а панель через
две минуты гасила метку, и человек, который просто сидел на месте, выглядел
пропавшим. Пока трансляция идёт и человек двигался меньше двух часов назад,
он на связи — в панели «на связи · на месте N мин», без сообщений.

Смотрим только на тех, кто смену открыл: открыть её без живой трансляции
нельзя, значит у такого водителя геопозиция точно была — и её именно
потеряли, а не «ещё не включили». Отмеченный оператором, но не открывший
смену, сюда не попадает: с него спрашивает geo_nag.

Конец смены
-----------
Смена кончается, когда водитель её закрыл, или в шесть утра, если не закрыл.
Если в этот момент пропажа ещё длится — замок. Вернулась раньше — ничего,
кроме короткой строки старшему, что вернулась.

Два пути к старшему
-------------------
Мгновенный: телеграм сам присылает боту «трансляцию включили» и «выключили»
— правкой того же сообщения, в ту же секунду. Бот водителя зовёт on_stream,
и сообщение старшему уходит сразу, без ожидания прохода. Минутный проход
(tick) остаётся для того, что по сигналу не поймать: трансляция числится
включённой, а точек нет.

Отправляем сами, без api_server: этот модуль живёт и в API, и в боте
водителя, а бот тащить за собой весь сервер не должен. Номера сообщений
кладём в реестры скрытого режима — и владельца, и водителя.

Только включил и выключил (владелец, 19 сен 2026)
--------------------------------------------------
«Присылай только сообщения о том, что водители включили или выключили
геопозицию; больше не надо „снова в движении“ или „на месте 3 ч“». Стоянка
— не событие: водитель, который стоит, геопозицию не выключал, он в сети.
Поэтому «два часа без движения», «снова в движении», «телефон не присылает
точку» больше не пишем — ни про водителей, ни про старшего. Остались:
«включил геопозицию» (после выключения — сколько её не было) и «выключил
геопозицию» (или «геопозиция выключена», если сигнал телеграма прошёл мимо,
а заметил проход). Те же события видят операторы — не в телеграме, а в
«Событиях» своего приложения: запись уведомления несёт водителя и район
(meta), лента оператора берёт своих.

Чего здесь нет
--------------
Повторов: одна пропажа — одно сообщение. Автоматического открытия: замок
снимает человек кнопкой под сообщением бота, и это намеренно — иначе правило
превращается обратно в напоминание. И суждений в первые минуты после старта:
свежесть точек читаем из базы, а база могла только что перезапуститься.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import db
import config_staff as staff

log = logging.getLogger("geo")

DUBAI_TZ = timezone(timedelta(hours=4))
from bizday import SHIFT_START_HOUR      # граница суток одна на всю систему (bizday)
WORK_FROM = 12                 # смена идёт с полудня
WORK_UNTIL = 6                 # и до шести утра
GRACE_MIN = 15                 # после старта не судим никого

EVENT_OFF = "drivers.geo_off"
EVENT_ON = "drivers.geo_on"
EVENT_LOCK = "drivers.geo_lock"
EVENT_SENIOR = "drivers.senior_geo"

# Точка старшего лежит там же, где точки водителей, но под своим ключом:
# среди водителей бывает тёзка, и имя само по себе ключом быть не может.
SENIOR_PREFIX = "op:"
# Устройства (планшеты) лежат там же, под своим ключом: в локаторе это
# отдельная группа, и с людьми они не путаются. Транслируют они в отдельный
# бот устройств (device_bot.py); старший и водители — в бот геопозиции.
DEVICE_PREFIX = "dev:"


_BOT_LINK: dict = {}


async def _bot_link(env: str) -> str:
    """Ссылка на бота по токену из .env. Имя спрашиваем у телеграма один раз и
    помним; токена нет или телеграм не ответил — пусто, и тексты обходятся
    без ссылки (повтор не раньше чем через пять минут)."""
    import time as _t
    now = _t.monotonic()
    c = _BOT_LINK.setdefault(env, {"at": 0.0, "link": ""})
    if c["link"] or now - c["at"] < 300:
        return c["link"]
    c["at"] = now
    token = os.getenv(env, "")
    if not token:
        return ""
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(f"https://api.telegram.org/bot{token}/getMe") as r:
                d = await r.json()
        u = ((d or {}).get("result") or {}).get("username") or ""
        if u:
            c["link"] = f"https://t.me/{u}"
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[geo] имя бота ({env}) не узнали: {e}")
    return c["link"]


async def geo_bot_link() -> str:
    """Бот геопозиции — туда водители и старший включают трансляцию."""
    return await _bot_link("AMBAR_GEO_BOT_TOKEN")


async def device_bot_link() -> str:
    """Бот устройств — туда транслируют планшеты."""
    return await _bot_link("AMBAR_DEVICE_BOT_TOKEN")


async def geo_how() -> str:
    """Как включить трансляцию — словами, с ссылкой на бот, если она известна."""
    link = await geo_bot_link()
    return ("В боте геопозиции" + (f" {link}" if link else "") + ": "
            "📎 → «Геопозиция» → «Транслировать» → «Пока не выключу». Один раз.")


_STARTED = None


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _working_hours(now: datetime) -> bool:
    return now.hour >= WORK_FROM or now.hour < WORK_UNTIL


def _dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", ""))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _hhmm(dt: datetime) -> str:
    return dt.astimezone(DUBAI_TZ).strftime("%H:%M")


def _dur(sec: float) -> str:
    m = int(sec // 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин" if m else "меньше минуты"


# ── тексты ───────────────────────────────────────────────────────────────────
# Имя — как зовут в работе, без телеграма и без номеров. Экранируем на случай
# подчёркивания в имени: иначе телеграм отвергнет всё сообщение целиком.
def _n(name: str) -> str:
    out = str(name or "")
    for ch in ("_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


async def old_stream_end(key: str, chat, mid) -> bool:
    """Правка без срока пришла по сообщению, которое трансляцию уже не ведёт.

    Включил заново — телеграм гасит прежнее сообщение той же правкой, что и
    выключение. Гасить по ней новую трансляцию нельзя: человек только что
    включил, а его уже «выключили», приложение просит точку снова, он
    включает опять — и так по кругу (тест-водитель, 15 сен 2026: включил
    10:33 — «выключил» через 17 секунд, включил 10:36 — то же самое)."""
    try:
        cur = await db.driver_pos_live(key)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[geo-watch] текущая трансляция {key} не прочиталась: {e}")
        return False
    if not cur:
        return False                             # старая запись без сообщения — как раньше
    return int(cur.get("mid") or 0) != int(mid or 0) or int(cur.get("chat") or 0) != int(chat or 0)


def text_stream_off(name: str, opened: bool) -> str:
    tail = "Оператор его не видит." if opened else "Смена у него не открыта."

    return f"📍 *{_n(name)}*: выключил геопозицию\n{tail}"


def text_stream_on(name: str, gone_sec: float = 0) -> str:
    return (f"📍 *{_n(name)}*: включил геопозицию"
            + (f"\nНе было {_dur(gone_sec)}." if gone_sec else ""))


def text_gone(name: str) -> str:
    """Трансляции нет, а сигнала телеграма мы не получили (бот лежал, срок
    вышел): не «выключил» — мы не знаем, он ли."""
    return f"📍 *{_n(name)}*: геопозиция выключена\nОператор его не видит."


def text_lock(name: str, since: datetime, why: str = "") -> str:
    what = (f"Трансляция выключена с {_hhmm(since)}" if why == "stream"
            else f"Без движения с {_hhmm(since)}")
    return (f"⛔ *{_n(name)}*: вход в приложение закрыт\n"
            f"{what} и до конца смены ничего не изменилось.\n\n"
            "Пустить обратно — кнопкой ниже.")


def text_lock_driver(since: datetime, why: str = "") -> str:
    what = (f"трансляция выключена с {_hhmm(since)}" if why == "stream"
            else f"без движения с {_hhmm(since)}")
    return (f"⛔ Вход в приложение закрыт: {what} и до конца смены ничего "
            "не изменилось. Открыть доступ может старший.")


def _sn(name: str) -> str:
    """Старший в сообщении: имя и роль, а если имени нет — одна роль."""
    return f"*{_n(name)}* (старший)" if name.lower() != "старший" else "*Старший*"


def text_senior_off(name: str) -> str:
    return f"📍 {_sn(name)}: выключил геопозицию"


def text_senior_on(name: str, gone_sec: float = 0) -> str:
    return (f"📍 {_sn(name)}: включил геопозицию"
            + (f"\nНе было {_dur(gone_sec)}." if gone_sec else ""))


def unlock_keyboard(key: str) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть доступ",
                                  "callback_data": f"geo:un:{key}"}]]}


# ── отправка ─────────────────────────────────────────────────────────────────
async def _post(token: str, chat_id: int, text: str, reply_markup: dict = None,
                parse_mode: str = "Markdown") -> dict:
    """Одно сообщение телеграму. Разметка не разобралась — шлём как есть:
    промах в форматировании не должен стоить старшему сообщения."""
    import json
    import aiohttp
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json=payload) as r:
                res = await r.json()
    except Exception as e:
        log.warning(f"[geo-watch] телеграм не ответил: {e}")
        return {}
    if not (res or {}).get("ok") and parse_mode:
        return await _post(token, chat_id, text, reply_markup, parse_mode=None)
    return res or {}


def geo_meta(name: str, on: bool, self_: bool = True) -> dict:
    """Кто и в каком районе — по записи уведомления «Событиям» оператора
    видно, чей это водитель (район — где он сейчас, после перестановок).
    self_ — выключил сам (сигнал телеграма) или заметили проходом."""
    district = next((d for d, names in (staff.DISTRICT_DRIVERS or {}).items() if name in (names or [])), "")
    return {"driver": name, "district": district, "on": bool(on), "self": bool(self_)}


async def _owners(text: str, event: str, reply_markup: dict = None,
                  exclude: set = None, meta: dict = None) -> int:
    """Всем владельцам, мимо настроек и тихих часов: это правило, а не новость.

    exclude — кому не слать: о пропаже старшего пишем владельцам, а не ему.
    meta — водитель и район: по ним событие видят операторы в приложении."""
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    try:
        await db.insert_notification(event, text, meta=meta)
    except Exception as e:
        log.error(f"[geo-watch] уведомление {event} не записано: {e}")
    if not token:
        return 0
    try:
        ids = [i for i in await db.get_all_manager_ids() if i not in (exclude or set())]
    except Exception as e:
        log.error(f"[geo-watch] владельцы не прочитаны: {e}")
        return 0
    sent = 0
    for oid in ids:
        res = await _post(token, oid, text, reply_markup)
        mid = ((res or {}).get("result") or {}).get("message_id")
        if not mid:
            log.error(f"[geo-watch] {event} владельцу не ушло: {(res or {}).get('description')}")
            continue
        sent += 1
        # Реестр AMBAR STAR: свипер и тревога стирают только записанное.
        try:
            await db.owner_msg_add(int(oid), int(mid), event)
        except Exception as e:
            log.debug(f"[geo-watch] реестр владельца: {e}")
    return sent


async def _driver(name: str, text: str) -> None:
    """Водителю — его ботом; номер в реестр скрытого режима."""
    tid = staff.DRIVER_IDS.get(name)
    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return
    res = await _post(token, tid, text, parse_mode=None)
    mid = ((res or {}).get("result") or {}).get("message_id")
    if not mid:
        log.warning(f"[geo-watch] {name}: сообщение не ушло: {(res or {}).get('description')}")
        return
    try:
        await db.drv_msg_add(int(tid), int(mid), datetime.now(timezone.utc))
    except Exception as e:
        log.debug(f"[geo-watch] реестр водителя: {e}")


# ── штраф на решение ─────────────────────────────────────────────────────────
# Владелец, 22 сен 2026: «по отключению геолокации — фиксированный штраф 200
# дирхам, но его можно будет редактировать»; «у нас в принципе запрещено
# отключать геолокацию». Выключилась — на смене или вне её — штраф ждёт
# решения старшего в «Штрафах» (fines_auto.py), один за день.
def text_off_duty(name: str) -> str:
    return f"📍 *{_n(name)}*: выключил геопозицию\nНе на смене."


async def _off_duty(name: str, utc: datetime, day: str) -> bool:
    """Выключил вне смены (не вышел или смена закрыта). Раньше это было его
    дело, но у нас в принципе запрещено отключать геолокацию (владелец,
    22 сен 2026): штраф на решение и сообщение старшему — один раз за день.
    Включение вне смены — не событие."""
    line, kb = await _fine(name, utc, day)
    if not line:
        return False
    await _owners(text_off_duty(name) + line, EVENT_OFF, reply_markup=kb, meta=geo_meta(name, False))
    log.info(f"[geo-watch] {name}: выключил трансляцию вне смены — штраф на решение")
    return True


async def _fine(name: str, utc: datetime, day: str, by_signal: bool = True) -> tuple:
    """(строка к сообщению, кнопка) — если штраф записан впервые за день.
    by_signal: сигнал пришёл с телефона (выключил сам) или пропажу заметил
    проход — в штрафе это называется по-разному."""
    if staff.is_test_driver(name):
        return "", None
    import fines_auto
    district = geo_meta(name, False).get("district") or ""
    if not await fines_auto.geo_off(name, district, day, _hhmm(utc), by_signal=by_signal):
        return "", None
    return (f"\nШтраф {fines_auto.GEO_OFF_FINE} AED ждёт решения — в «Штрафах».",
            fines_auto.open_button("Решить по штрафу"))


# ── мгновенный путь: сигнал телеграма ────────────────────────────────────────
async def on_stream(name: str, on: bool, now: datetime = None) -> bool:
    """Телеграм сказал: трансляцию включили (on) или выключили.

    Зовёт бот водителя из обработчика точки — в ту же секунду, что и сигнал.
    Отсрочки после старта здесь нет: это не догадка по свежести точек, а
    прямое слово телеграма. Возвращает, ушло ли что-то старшему."""
    utc = now or datetime.now(timezone.utc)
    day = _biz_day(utc.astimezone(DUBAI_TZ))
    d = await db.get_driver_day(day, name) or {}
    if d.get("working") is not True:
        # не на работе: включил — не событие, выключил — штраф на решение
        return False if on else await _off_duty(name, utc, day)
    st = await db.geo_watch_get(name)
    if st.get("locked_at"):
        return False
    opened = bool(d.get("shift_open_at")) and not d.get("shift_close_at")
    off_since = _dt(st.get("off_since")) if st.get("day") == day else None
    try:
        await staff.sync()                   # район — после сегодняшних перестановок
    except Exception as e:                   # noqa: BLE001
        log.debug(f"[geo-watch] перестановка не прочитана: {e}")

    if on:
        # «Не было N» — только после выключения. Старая отметка стоянки (до
        # 19 сен 2026) не пропажа: снимаем её без счёта.
        gone = (utc - off_since).total_seconds() if off_since and st.get("off_why") != "still" else 0
        if off_since:
            await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
        await _owners(text_stream_on(name, gone), EVENT_ON, meta=geo_meta(name, True))
        log.info(f"[geo-watch] {name}: включил трансляцию")
        return True

    # Выключил. После закрытой смены — как вне смены: штраф на решение; на
    # открытой запоминаем минуту: с неё считается «не вернулась до конца смены».
    if d.get("shift_close_at"):
        return await _off_duty(name, utc, day)
    if off_since and st.get("off_why") == "stream":
        return False                       # уже сказали про это же
    if opened:
        fields = {"day": day, "off_why": "stream"}
        if not off_since or st.get("off_why") == "still":
            fields["off_since"] = utc          # выключил — с этой минуты, а не с начала стоянки
        await db.geo_watch_set(name, fields)
    line, kb = await _fine(name, utc, day)     # и до открытия смены — отключать нельзя вообще
    await _owners(text_stream_off(name, opened) + line, EVENT_OFF, reply_markup=kb, meta=geo_meta(name, False))
    log.info(f"[geo-watch] {name}: выключил трансляцию")
    return True


# ── старший ──────────────────────────────────────────────────────────────────
# Правило другое, чем у водителей: замка нет, смены нет, трансляция не
# обязательна — старший работает и без неё. С 19 сен 2026 и про него только
# «включил» и «выключил» — по сигналу телеграма из чата STAR-бота; проходом
# («два часа без точки», «не видно с начала смены») больше не пишем.
def _senior_ids() -> set:
    return set(staff.SENIOR_STAR_IDS.values())


async def on_senior_stream(name: str, on: bool, now: datetime = None) -> bool:
    """Старший включил или выключил трансляцию в чате STAR-бота — владельцам
    (кроме самих старших) в ту же секунду."""
    utc = now or datetime.now(timezone.utc)
    day = _biz_day(utc.astimezone(DUBAI_TZ))
    key = SENIOR_PREFIX + name
    st = await db.geo_watch_get(key)
    off_since = _dt(st.get("off_since")) if st.get("day") == day else None
    if on:
        gone = (utc - off_since).total_seconds() if off_since and st.get("off_why") == "stream" else 0
        await db.geo_watch_set(key, {"day": day, "seen": True},
                               unset=["off_since", "off_why", "stream_off"])
        await _owners(text_senior_on(name, gone), EVENT_SENIOR, exclude=_senior_ids())
        return True
    if off_since and st.get("off_why") == "stream":
        return False                             # уже сказали про это же
    await db.geo_watch_set(key, {"day": day, "off_since": utc, "off_why": "stream"},
                           unset=["stream_off"])
    await _owners(text_senior_off(name), EVENT_SENIOR, exclude=_senior_ids())
    return True


# ── проход ───────────────────────────────────────────────────────────────────
async def tick(now: datetime = None) -> dict:
    """Один проход. Возвращает, что нашли — этим же пользуется проверка."""
    now = now or datetime.now(DUBAI_TZ)
    utc = now.astimezone(timezone.utc)
    day = _biz_day(now)
    if _STARTED and (utc - _STARTED).total_seconds() < GRACE_MIN * 60:
        return {"day": day, "grace": True}
    try:
        await staff.sync()
    except Exception as e:
        log.warning(f"[geo-watch] перестановка не прочитана: {e}")

    from driver_routes import _geo_state

    out = {"day": day}

    on_shift = []
    for d in await db.get_driver_days(day):
        if d.get("working") is not True or not d.get("shift_open_at"):
            continue
        name = (d.get("driver") or "").strip()
        if name and name in staff.DRIVER_IDS:
            on_shift.append((name, d))
    if not on_shift:
        out["on_shift"] = 0
        return out

    # Стояние считаем с открытия смены: что стоял дома до неё — не в счёт.
    geos = {name: await _geo_state(name, since=_dt(d.get("shift_open_at")))
            for name, d in on_shift}

    # Молчат разом все, кто на смене, — дело не в водителях, а в нас: так
    # выглядит перезапуск или отвалившаяся база. Считаем только открытые
    # смены: отмеченный, но не вышедший водитель ослеплял бы проверку вечно.
    live = [n for n, d in on_shift if not d.get("shift_close_at")]
    if len(live) > 1 and not any(geos[n]["fresh"] or geos[n]["stream"] for n in live):
        log.warning("[geo-watch] точек нет ни у кого на смене — молчим, это похоже на нашу проблему")
        out.update({"on_shift": len(on_shift), "blind": True})
        return out

    by_clock = not _working_hours(now)
    out.update({"on_shift": len(on_shift), "off": [], "back": [], "locked": []})
    for name, d in on_shift:
        g = geos[name]
        st = await db.geo_watch_get(name)
        if st.get("locked_at"):
            continue                          # уже заперт — сторожить нечего
        off_since = _dt(st.get("off_since")) if st.get("day") == day else None
        ended = bool(d.get("shift_close_at")) or by_clock

        if not ended:
            if off_since and st.get("off_why") == "still":
                # Отметка «два часа без движения» из прежнего правила: стоянка
                # — не пропажа (19 сен 2026). Снимаем молча; выключена ли
                # трансляция на самом деле — скажет следующий проход.
                await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
            elif not g["stream"] and not off_since:
                # Выключение обычно уже ушло мгновенным путём (on_stream);
                # здесь оно ловится, только если бот водителя в тот момент
                # лежал или у трансляции вышел срок.
                await db.geo_watch_set(name, {"day": day, "off_since": utc, "off_why": "stream"})
                line, kb = await _fine(name, utc, day, by_signal=False)
                await _owners(text_gone(name) + line, EVENT_OFF, reply_markup=kb,
                              meta=geo_meta(name, False, self_=False))
                log.info(f"[geo-watch] {name}: геопозиция выключена")
                out["off"].append(name)
            elif g["stream"] and off_since:
                await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
                await _owners(text_stream_on(name, (utc - off_since).total_seconds()), EVENT_ON,
                              meta=geo_meta(name, True))
                log.info(f"[geo-watch] {name}: геопозиция вернулась")
                out["back"].append(name)
            continue

        # Смена кончилась. Замка больше нет (владелец, 11 сен 2026: «не надо
        # ничего закрывать»): пропажа просто снимается, вернулась — короткая
        # строка владельцам.
        if not off_since:
            continue
        why = st.get("off_why") or ""
        await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
        if g["stream"] and why == "stream":
            await _owners(text_stream_on(name, (utc - off_since).total_seconds()), EVENT_ON,
                          meta=geo_meta(name, True))
            out["back"].append(name)

    if out["off"] or out["back"] or out["locked"]:
        log.info(f"[geo-watch] {day}: пропала у {out['off']}, вернулась у {out['back']}, "
                 f"заперты {out['locked']}")
    return out


async def loop(app):
    """Раз в минуту. Сама редкость сообщений — в записи о пропаже."""
    import asyncio
    global _STARTED
    _STARTED = datetime.now(timezone.utc)
    await asyncio.sleep(40)            # дать серверу подняться
    while True:
        try:
            await tick()
        except Exception as e:
            log.error(f"[geo-watch] {e}")
        await asyncio.sleep(60)
