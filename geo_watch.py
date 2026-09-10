"""Сторож геопозиции на смене.

Зачем
-----
Водитель на смене обязан быть виден: оператор отдаёт заказ тому, кто ближе,
и невидимая машина для него не существует. Напоминание самому водителю уже
есть (geo_nag), но напоминание — это просьба, а здесь нужно правило. Правило
такое: пропала геопозиция — старший узнаёт сразу; не вернулась до конца
смены — вход в приложение закрыт, пока старший его не откроет.

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
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе
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


def text_stream_off(name: str, opened: bool) -> str:
    tail = ("Если до конца смены не включит — вход в приложение закроется."
            if opened else "Смена у него не открыта.")
    return f"📍 *{_n(name)}*: выключил трансляцию геопозиции\n{tail}"


def text_stream_on(name: str) -> str:
    return f"📍 *{_n(name)}*: включил трансляцию геопозиции"


def _since_still(geo: dict, now: datetime = None) -> datetime:
    """С какой минуты стоит: сейчас минус длительность стояния."""
    utc = now or datetime.now(timezone.utc)
    return utc - timedelta(seconds=int(geo.get("still_sec") or 0))


def text_off(name: str, why: str, geo: dict, now: datetime = None) -> str:
    if why == "stream":
        return (f"📍 *{_n(name)}*: трансляция геопозиции выключена\n"
                "Оператор больше не видит, где он. Если до конца смены не "
                "включит — вход в приложение закроется.")
    return (f"📍 *{_n(name)}*: два часа без движения\n"
            f"На одном месте с {_hhmm(_since_still(geo, now))}. Если до конца смены "
            "не поедет — вход в приложение закроется.")


def text_back(name: str, gone_sec: float, why: str = "") -> str:
    if why == "still":
        return f"📍 *{_n(name)}*: снова в движении\nСтоял {_dur(gone_sec)}."
    return f"📍 *{_n(name)}*: трансляция геопозиции снова идёт\nНе было {_dur(gone_sec)}."


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


def text_senior_off(name: str, why: str, geo: dict, now: datetime = None) -> str:
    if why == "stream":
        return f"📍 {_sn(name)}: трансляция геопозиции выключена"
    if why == "never":
        return f"📍 {_sn(name)}: геопозиция не видна\nС начала смены не было ни одной точки."
    if why == "still":
        return (f"📍 {_sn(name)}: два часа без движения\n"
                f"На одном месте с {_hhmm(_since_still(geo, now))}.")
    age = int(geo.get("age_sec") or 0)
    if why == "silent":
        last = (now or datetime.now(timezone.utc)) - timedelta(seconds=age)
        return (f"📍 {_sn(name)}: телефон два часа не присылает точку\n"
                f"Трансляция включена, последняя точка была в {_hhmm(last)}.")
    mins = age // 60
    # until пустой — трансляции не было вовсе (точки шли из панели).
    head = "трансляция геопозиции кончилась" if geo.get("until") else "геопозиция не видна"
    return f"📍 {_sn(name)}: {head}\nПоследняя точка {mins} мин назад."


def text_senior_on(name: str, gone_sec: float = 0, why: str = "") -> str:
    if why == "still":
        return f"📍 {_sn(name)}: снова в движении\nСтоял {_dur(gone_sec)}."
    if why == "silent":
        return f"📍 {_sn(name)}: точки снова идут\nНе было {_dur(gone_sec)}."
    return (f"📍 {_sn(name)}: геопозиция снова видна"
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


async def _owners(text: str, event: str, reply_markup: dict = None,
                  exclude: set = None) -> int:
    """Всем владельцам, мимо настроек и тихих часов: это правило, а не новость.

    exclude — кому не слать: о пропаже старшего пишем владельцам, а не ему."""
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    try:
        await db.insert_notification(event, text)
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
        return False                       # не на работе — его трансляция его дело
    st = await db.geo_watch_get(name)
    if st.get("locked_at"):
        return False
    opened = bool(d.get("shift_open_at")) and not d.get("shift_close_at")
    off_since = _dt(st.get("off_since")) if st.get("day") == day else None

    if on:
        if off_since and st.get("off_why") == "still":
            # Стоит на месте и перезапустил трансляцию: стоять не перестал,
            # «снова в движении» скажет проход, когда поедет.
            log.info(f"[geo-watch] {name}: перезапустил трансляцию, стоит дальше")
            return False
        if off_since:
            await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
            await _owners(text_back(name, (utc - off_since).total_seconds()), EVENT_ON)
        else:
            await _owners(text_stream_on(name), EVENT_ON)
        log.info(f"[geo-watch] {name}: включил трансляцию")
        return True

    # Выключил. После закрытой смены это нормально — молчим; на открытой
    # запоминаем минуту: с неё считается «не вернулась до конца смены».
    if d.get("shift_close_at"):
        return False
    if off_since and st.get("off_why") == "stream":
        return False                       # уже сказали про это же
    if opened:
        fields = {"day": day, "off_why": "stream"}
        if not off_since or st.get("off_why") == "still":
            fields["off_since"] = utc          # выключил — с этой минуты, а не с начала стоянки
        await db.geo_watch_set(name, fields)
    await _owners(text_stream_off(name, opened), EVENT_OFF)
    log.info(f"[geo-watch] {name}: выключил трансляцию")
    return True


# ── старший ──────────────────────────────────────────────────────────────────
# Правило другое, чем у водителей: замка нет, смены нет, трансляция не
# обязательна — старший работает и без неё. Есть одно: владельцы должны
# знать, когда его не видно. «Видно» — свежая точка любым путём: из панели,
# пока она открыта, или из трансляции в чате STAR-бота.
def _senior_ids() -> set:
    return set(staff.SENIOR_STAR_IDS.values())


async def _seniors_tick(now: datetime, utc: datetime, day: str, out: dict) -> None:
    from driver_routes import _geo_state
    if not _working_hours(now):
        return
    for name in list(staff.SENIOR_STAR_IDS):
        key = SENIOR_PREFIX + name
        g = await _geo_state(key)
        # Старший сидит на базе часами — это работа. При живой трансляции он
        # виден, пока телефон присылает точку хоть раз в два часа; без
        # трансляции — по свежей точке из панели.
        silent = g["stream"] and (g["age_sec"] is None or g["age_sec"] >= db.GEO_LOST_SEC)
        visible = (not silent) if g["stream"] else g["fresh"]
        st = await db.geo_watch_get(key)
        off_since = _dt(st.get("off_since")) if st.get("day") == day else None
        if not visible and not off_since:
            seen_today = st.get("day") == day and bool(st.get("seen"))
            # Сегодня ещё не видели — «с начала смены ни одной точки», а не
            # «два часа не присылает» по вчерашней точке.
            why = ("stream" if st.get("stream_off") else "never" if not seen_today
                   else "silent" if silent else "stale")
            # Молчание считается с последней точки, а не с минуты, когда заметили.
            since = utc - timedelta(seconds=g["age_sec"]) if why == "silent" and g["age_sec"] else utc
            await db.geo_watch_set(key, {"day": day, "off_since": since, "off_why": why},
                                   unset=["stream_off"])
            await _owners(text_senior_off(name, why, g, utc), EVENT_SENIOR, exclude=_senior_ids())
            log.info(f"[geo-watch] старший {name}: не виден ({why})")
            out.setdefault("senior_off", []).append(name)
        elif visible:
            fields = {"day": day, "seen": True}
            if off_since:
                why = st.get("off_why") or ""
                await db.geo_watch_set(key, fields, unset=["off_since", "off_why"])
                await _owners(text_senior_on(name, (utc - off_since).total_seconds(), why),
                              EVENT_SENIOR, exclude=_senior_ids())
                log.info(f"[geo-watch] старший {name}: снова виден")
                out.setdefault("senior_on", []).append(name)
            elif st.get("day") != day or not st.get("seen"):
                await db.geo_watch_set(key, fields)


async def on_senior_stream(name: str, on: bool, now: datetime = None) -> bool:
    """Старший включил или выключил трансляцию в чате STAR-бота — владельцам
    в ту же секунду. Выключение запоминаем: проход через четверть часа
    иначе написал бы «точек нет», хотя причина известна."""
    utc = now or datetime.now(timezone.utc)
    day = _biz_day(utc.astimezone(DUBAI_TZ))
    key = SENIOR_PREFIX + name
    st = await db.geo_watch_get(key)
    off_since = _dt(st.get("off_since")) if st.get("day") == day else None
    if on:
        if off_since:
            await db.geo_watch_set(key, {"day": day, "seen": True},
                                   unset=["off_since", "off_why", "stream_off"])
            await _owners(text_senior_on(name, (utc - off_since).total_seconds()),
                          EVENT_SENIOR, exclude=_senior_ids())
        else:
            await db.geo_watch_set(key, {"day": day, "seen": True}, unset=["stream_off"])
            await _owners(f"📍 {_sn(name)}: включил трансляцию геопозиции",
                          EVENT_SENIOR, exclude=_senior_ids())
        return True
    if off_since and st.get("off_why") == "stream":
        return False
    # Точка из панели могла прийти минуту назад — тогда «не виден» ещё рано,
    # но о выключении сказать надо: следующий проход допишет остальное.
    await db.geo_watch_set(key, {"day": day, "stream_off": True})
    await _owners(text_senior_off(name, "stream", {}), EVENT_SENIOR, exclude=_senior_ids())
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
        staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
    except Exception as e:
        log.warning(f"[geo-watch] перестановка не прочитана: {e}")

    from driver_routes import _geo_state

    out = {"day": day}
    try:
        await _seniors_tick(now, utc, day, out)
    except Exception as e:                       # noqa: BLE001
        log.error(f"[geo-watch] старший: {e}")

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
    if len(live) > 1 and not any(geos[n]["fresh"] or geos[n]["watch_ok"] for n in live):
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
            if not g["watch_ok"] and not off_since:
                # Выключение трансляции обычно уже ушло мгновенным путём
                # (on_stream); здесь оно ловится, только если бот водителя
                # в тот момент лежал. Второй случай — два часа без движения.
                why = "stream" if not g["stream"] else "still"
                # Стояние — с якоря, а не с минуты, когда заметили: «стоял N»
                # и «без движения с» должны сходиться с «на одном месте с».
                since = _since_still(g, utc) if why == "still" else utc
                await db.geo_watch_set(name, {"day": day, "off_since": since, "off_why": why})
                await _owners(text_off(name, why, g, utc), EVENT_OFF)
                log.info(f"[geo-watch] {name}: геопозиция пропала ({why})")
                out["off"].append(name)
            elif g["watch_ok"] and off_since:
                why = st.get("off_why") or ""
                await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
                await _owners(text_back(name, (utc - off_since).total_seconds(), why), EVENT_ON)
                log.info(f"[geo-watch] {name}: геопозиция вернулась")
                out["back"].append(name)
            continue

        # Смена кончилась. Пропажа, которая так и длится, — замок. Стояние
        # замка не даёт: закрыл смену и стоит — значит, приехал; не закрыл
        # и стоит до утра — уснул дома, а не спрятался.
        if not off_since:
            continue
        why = st.get("off_why") or ""
        if why == "still":
            await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
            continue
        if g["watch_ok"]:
            await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
            await _owners(text_back(name, (utc - off_since).total_seconds(), why), EVENT_ON)
            out["back"].append(name)
            continue
        key = await db.geo_lock_set(name, utc, why)
        await _owners(text_lock(name, off_since, why), EVENT_LOCK, unlock_keyboard(key))
        await _driver(name, text_lock_driver(off_since, why))
        log.warning(f"[geo-watch] {name}: вход закрыт — геопозиции нет с {_hhmm(off_since)}")
        out["locked"].append(name)

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
