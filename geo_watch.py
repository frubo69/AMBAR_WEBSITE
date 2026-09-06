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
  • точки перестали приходить — трансляция на бумаге идёт, а координат нет
    дольше GEO_FRESH_SEC. Так выглядят выключенная геолокация в телефоне,
    убитый телеграм и, реже, туннель.

Смотрим только на тех, кто смену открыл: открыть её без живой трансляции
нельзя, значит у такого водителя геопозиция точно была — и её именно
потеряли, а не «ещё не включили». Отмеченный оператором, но не открывший
смену, сюда не попадает: с него спрашивает geo_nag.

Конец смены
-----------
Смена кончается, когда водитель её закрыл, или в шесть утра, если не закрыл.
Если в этот момент пропажа ещё длится — замок. Вернулась раньше — ничего,
кроме короткой строки старшему, что вернулась.

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
    from owner_routes import _md
    return _md(name)


def text_off(name: str, why: str, geo: dict) -> str:
    if why == "stream":
        return (f"📍 *{_n(name)}*: трансляция геопозиции выключена\n"
                "Оператор больше не видит, где он. Если до конца смены не "
                "включит — вход в приложение закроется.")
    mins = int((geo.get("age_sec") or 0) // 60)
    return (f"📍 *{_n(name)}*: местоположение потеряно\n"
            f"Трансляция включена, но точек нет уже {mins} мин. Если до конца "
            "смены не вернётся — вход в приложение закроется.")


def text_back(name: str, gone_sec: float) -> str:
    return f"📍 *{_n(name)}*: геопозиция снова идёт\nНе было {_dur(gone_sec)}."


def text_lock(name: str, since: datetime) -> str:
    return (f"⛔ *{_n(name)}*: вход в приложение закрыт\n"
            f"Геопозиция пропала в {_hhmm(since)} и не вернулась до конца смены.\n\n"
            "Пустить обратно — кнопкой ниже.")


def text_lock_driver(since: datetime) -> str:
    return (f"⛔ Вход в приложение закрыт: геопозиция пропала в {_hhmm(since)} "
            "и не вернулась до конца смены. Открыть доступ может старший.")


def unlock_keyboard(key: str) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть доступ",
                                  "callback_data": f"geo:un:{key}"}]]}


# ── отправка ─────────────────────────────────────────────────────────────────
async def _owners(text: str, event: str, reply_markup: dict = None) -> int:
    """Всем владельцам, мимо настроек и тихих часов: это правило, а не новость."""
    from owner_routes import OWNER_BOT_TOKEN, _send_md
    from api_server import tg_send
    try:
        await db.insert_notification(event, text)
    except Exception as e:
        log.error(f"[geo-watch] уведомление {event} не записано: {e}")
    if not OWNER_BOT_TOKEN:
        return 0
    try:
        ids = await db.get_all_manager_ids()
    except Exception as e:
        log.error(f"[geo-watch] владельцы не прочитаны: {e}")
        return 0
    sent = 0
    for oid in ids:
        try:
            if reply_markup:
                res = await tg_send(OWNER_BOT_TOKEN, oid, text, reply_markup=reply_markup)
            else:
                res = await _send_md(OWNER_BOT_TOKEN, oid, text)
            sent += bool((res or {}).get("ok"))
        except Exception as e:
            log.error(f"[geo-watch] {event} владельцу не ушло: {e}")
    return sent


async def _driver(name: str, text: str) -> None:
    """Водителю — его ботом. tg_send сам кладёт номер в реестр скрытого режима."""
    from api_server import tg_send
    tid = staff.DRIVER_IDS.get(name)
    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return
    try:
        await tg_send(token, tid, text, parse_mode=None)
    except Exception as e:
        log.warning(f"[geo-watch] {name}: сообщение о замке не ушло: {e}")


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

    on_shift = []
    for d in await db.get_driver_days(day):
        if d.get("working") is not True or not d.get("shift_open_at"):
            continue
        name = (d.get("driver") or "").strip()
        if name and name in staff.DRIVER_IDS:
            on_shift.append((name, d))
    if not on_shift:
        return {"day": day, "on_shift": 0}

    geos = {name: await _geo_state(name) for name, _ in on_shift}

    # Молчат разом все, кто на смене, — дело не в водителях, а в нас: так
    # выглядит перезапуск или отвалившаяся база. Считаем только открытые
    # смены: отмеченный, но не вышедший водитель ослеплял бы проверку вечно.
    live = [n for n, d in on_shift if not d.get("shift_close_at")]
    if len(live) > 1 and not any(geos[n]["fresh"] for n in live):
        log.warning("[geo-watch] точек нет ни у кого на смене — молчим, это похоже на нашу проблему")
        return {"day": day, "on_shift": len(on_shift), "blind": True}

    by_clock = not _working_hours(now)
    out = {"day": day, "on_shift": len(on_shift), "off": [], "back": [], "locked": []}
    for name, d in on_shift:
        g = geos[name]
        st = await db.geo_watch_get(name)
        if st.get("locked_at"):
            continue                          # уже заперт — сторожить нечего
        off_since = _dt(st.get("off_since")) if st.get("day") == day else None
        ended = bool(d.get("shift_close_at")) or by_clock

        if not ended:
            if not g["ok"] and not off_since:
                why = "stream" if not g["stream"] else "stale"
                await db.geo_watch_set(name, {"day": day, "off_since": utc, "off_why": why})
                await _owners(text_off(name, why, g), EVENT_OFF)
                log.info(f"[geo-watch] {name}: геопозиция пропала ({why})")
                out["off"].append(name)
            elif g["ok"] and off_since:
                await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
                await _owners(text_back(name, (utc - off_since).total_seconds()), EVENT_ON)
                log.info(f"[geo-watch] {name}: геопозиция вернулась")
                out["back"].append(name)
            continue

        # Смена кончилась. Пропажа, которая так и длится, — замок.
        if not off_since:
            continue
        if g["ok"]:
            await db.geo_watch_set(name, {"day": day}, unset=["off_since", "off_why"])
            await _owners(text_back(name, (utc - off_since).total_seconds()), EVENT_ON)
            out["back"].append(name)
            continue
        key = await db.geo_lock_set(name, utc, st.get("off_why") or "")
        await _owners(text_lock(name, off_since), EVENT_LOCK, unlock_keyboard(key))
        await _driver(name, text_lock_driver(off_since))
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
