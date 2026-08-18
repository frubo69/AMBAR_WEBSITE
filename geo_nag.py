"""Напоминания о живой трансляции водителя.

Зачем
-----
Оператор должен видеть, где машины. Координаты даёт живая геопозиция телеграма,
но у неё есть потолок — восемь часов, а смена идёт с полудня до шести утра,
восемнадцать. Значит за ночь трансляцию включают два-три раза, и каждый раз это
надо вспомнить самому, за рулём, посреди работы. Не вспомнит никто.

Поэтому вспоминает программа: за десять минут до конца трансляции водителю
приходит одно сообщение — включи заново. И одно в начале смены тому, кто не
включил вовсе.

Чего здесь нет
--------------
Ни принуждения, ни повторов по кругу. Трансляцию включает человек, и если он её
выключил — это его решение, а не сбой; напомним один раз за смену и замолчим.
Сообщение всегда одно живое: перед новым старое удаляется, иначе к утру в чате
висит десяток одинаковых.
"""
import logging
from datetime import datetime, timedelta, timezone

import db
import config_staff as staff

log = logging.getLogger("geo")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе
WORK_FROM = 12                 # смена идёт с полудня
WORK_UNTIL = 6                 # и до шести утра
WARN_MIN = 10                  # за сколько минут до конца предупредить
STALE_MIN = 20                 # молчит дольше этого — считаем, что не транслирует
ASK_EVERY_H = 4                # как часто напоминать тому, кто вовсе не включил

_LAST = {}                     # chat_id → {"at": datetime, "mid": int, "why": str}


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _working_hours(now: datetime) -> bool:
    """Смена: с полудня до шести утра следующего дня."""
    return now.hour >= WORK_FROM or now.hour < WORK_UNTIL


HOW = ("Скрепка → «Геопозиция» → «Транслировать» → 8 часов.")


async def _say(token: str, chat_id: int, text: str, now: datetime, why: str) -> bool:
    """Одно живое напоминание на водителя: старое убираем, новое шлём."""
    from api_server import tg_send, tg_delete
    prev = _LAST.get(chat_id)
    if prev and prev.get("mid"):
        await tg_delete(token, chat_id, prev["mid"])
    res = await tg_send(token, chat_id, text)
    mid = ((res or {}).get("result") or {}).get("message_id")
    _LAST[chat_id] = {"at": now, "mid": mid, "why": why}
    return bool((res or {}).get("ok"))


async def tick(now: datetime = None) -> dict:
    """Один проход. Возвращает, что нашли — этим же пользуется проверка."""
    import os
    now = now or datetime.now(DUBAI_TZ)
    day = _biz_day(now)
    if not _working_hours(now):
        return {"skip": "не смена", "day": day}

    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if not token:
        return {"skip": "нет токена", "day": day}
    try:
        staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
    except Exception as e:
        log.warning(f"[geo] перестановка не прочитана: {e}")

    # Спрашиваем только с тех, кого старший отметил вышедшими: у неотмеченного
    # мы не знаем даже, работает ли он сегодня.
    working = []
    for d in await db.get_driver_days(day):
        if d.get("working") is not True:
            continue
        name = (d.get("driver") or "").strip()
        if name and name in staff.DRIVER_IDS:
            working.append(name)
    if not working:
        return {"day": day, "working": 0, "sent": 0}

    rows = {r["driver"]: r for r in await db.driver_pos_all(working)}
    utc = now.astimezone(timezone.utc)
    ends, silent, sent = [], [], 0

    for name in working:
        cid = staff.DRIVER_IDS[name]
        r = rows.get(name) or {}
        until = _dt(r.get("until"))
        at = _dt(r.get("at"))
        fresh = at and (utc - at).total_seconds() < STALE_MIN * 60

        if fresh and until:
            left = (until - utc).total_seconds() / 60
            if 0 < left <= WARN_MIN:
                ends.append(name)
                # Про конец трансляции говорим один раз на эту трансляцию:
                # ключом служит её же срок.
                key = f"end:{until.isoformat()}"
                if (_LAST.get(cid) or {}).get("why") != key:
                    txt = (f"⏳ Трансляция геопозиции кончается через "
                           f"{int(left) or 1} мин.\n\n"
                           f"Включите заново, чтобы оператор видел, где вы:\n{HOW}")
                    if await _say(token, cid, txt, utc, key):
                        sent += 1
            continue

        # Не транслирует. Напоминаем редко: человек мог выключить нарочно.
        silent.append(name)
        prev = _LAST.get(cid)
        if prev and prev["why"] == "off" and \
           (utc - prev["at"]).total_seconds() < ASK_EVERY_H * 3600:
            continue
        # Точка из приложения — это не «не видно»: она приходит, пока
        # приложение открыто. Врать про это нельзя, иначе человек справедливо
        # перестанет верить напоминаниям.
        if fresh:
            txt = ("📍 Оператор видит вас, только пока открыто приложение.\n\n"
                   f"Чтобы видел всю смену — включите трансляцию:\n{HOW}")
        else:
            txt = ("📍 Геопозиция не транслируется — оператор не видит, где вы.\n\n"
                   f"{HOW}\n\n"
                   "Маршрут за смену стирается, когда её закрывают.")
        if await _say(token, cid, txt, utc, "off"):
            sent += 1

    if sent:
        log.info(f"[geo] {day}: на смене {len(working)}, "
                 f"кончается у {len(ends)}, молчат {len(silent)}, послано {sent}")
    return {"day": day, "working": len(working), "ends": ends,
            "silent": silent, "sent": sent}


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


async def loop(app):
    """Раз в минуту смотрим, у кого кончается. Сама редкость — в tick."""
    import asyncio
    await asyncio.sleep(35)            # дать серверу подняться
    while True:
        try:
            await tick()
        except Exception as e:
            log.error(f"[geo] {e}")
        await asyncio.sleep(60)
