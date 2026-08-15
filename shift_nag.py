"""Напоминания о несданной смене.

Зачем
-----
Смену закрывают руками — и это правильно: только человек знает, будет ли ещё
звонок. Но руками же её и забывают. Забытая смена стоит дороже, чем кажется:
пока хоть один район открыт, продажи дня не окончательны, заявка в магазин не
собирается, и старший просыпается без файла.

Поэтому с шести утра тем, кто не сдал своё, каждые пять минут приходит
напоминание. Не «уведомление о том, что неплохо бы», а именно напоминание в
телеграм: смена не закрыта — иди и закрой.

Кому и о чём
------------
  • операторам — на планшет, за которым они сидят, и старшему: какие районы
    ещё открыты;
  • водителю — лично: по бензину и мойке нет ответа. Ответ — это сумма или
    «не было»; молчание не ответ, потому что забытую заправку через неделю уже
    не вспомнить.

Своего телеграма у районных операторов нет — заказы принимают с общего
планшета, туда и пишем. Это не обезличка: в сообщении перечислены районы, а
кто за какой отвечает, они знают лучше нас.

Когда молчим
------------
Между полуночью и шестью утра — смена ещё идёт. После полудня — начались новые
рабочие сутки, и вчерашние напоминания стали шумом: район всё равно уже не
закроют задним числом кнопкой. Всё сдано — тоже молчим, и это главный
выключатель: напоминание прекращается ровно тогда, когда человек сделал дело.

Одно сообщение на адресата: перед новым старое удаляется. Иначе к десяти утра
в чате висит полсотни одинаковых строк, и читать их перестают на третьей.
"""
import logging
from datetime import datetime, timedelta, timezone

import db
import config_staff as staff
from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES

log = logging.getLogger("nag")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе
NAG_FROM = 6                   # с этого часа по Дубаю
NAG_UNTIL = 12                 # и до начала новых суток
EVERY_MIN = 5

# Что послали в прошлый раз: кому, когда и каким сообщением. В памяти, а не в
# базе: перезапуск сервиса — это лишнее напоминание, а не потерянные данные.
_LAST = {}                     # chat_id → {"at": datetime, "mid": int, "text": str}


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


async def _open_districts(day: str) -> list:
    """Районы, где смену ещё не закрыли."""
    closed = await db.shifts_for_day(day)
    return [o for o in OFFICE_IDS if o not in closed]


async def _silent_drivers(day: str) -> list:
    """Кто не ответил по обязательным расходам.

    Спрашиваем только у тех, кого старший отметил вышедшими: у неотмеченного
    мы не знаем даже, работал ли он, и требовать с него отчёт не за что."""
    from driver_routes import MUST_ANSWER, _kind_of
    out = []
    for d in await db.get_driver_days(day):
        if d.get("working") is not True:
            continue
        name = d.get("driver") or ""
        if not name or name not in staff.DRIVER_IDS:
            continue                       # нет доступа в приложение — нечем и отвечать
        no = d.get("no_expense") or {}
        extras = d.get("extras") or []
        left = [k for k in MUST_ANSWER
                if not no.get(k) and not any(_kind_of(x) == k for x in extras)]
        if left:
            out.append((name, staff.DRIVER_IDS[name], left))
    return out


async def _ping(token: str, chat_id: int, text: str, now: datetime):
    """Одно живое напоминание на адресата: старое убираем, новое шлём."""
    from api_server import tg_send, tg_delete
    prev = _LAST.get(chat_id)
    if prev and prev.get("mid"):
        await tg_delete(token, chat_id, prev["mid"])
    res = await tg_send(token, chat_id, text)
    mid = ((res or {}).get("result") or {}).get("message_id")
    _LAST[chat_id] = {"at": now, "mid": mid, "text": text}
    return bool((res or {}).get("ok"))


def _due(chat_id: int, now: datetime) -> bool:
    prev = _LAST.get(chat_id)
    return not prev or (now - prev["at"]).total_seconds() >= EVERY_MIN * 60


def _forget(chat_id: int):
    _LAST.pop(chat_id, None)


async def tick(now: datetime = None) -> dict:
    """Один проход. Возвращает, что нашли — этим же пользуется проверка."""
    import os
    now = now or datetime.now(DUBAI_TZ)
    day = _biz_day(now)
    if not (NAG_FROM <= now.hour < NAG_UNTIL):
        return {"skip": "не время", "day": day}

    # Сутки, которые закончились раньше, чем появилась сама кнопка, не
    # напоминаем: закрыть их было нечем, а человек, разбуженный требованием
    # сделать то, чего вчера не существовало,в следующий раз просто отключит
    # уведомления. Первый увиденный день записываем и пропускаем — работать
    # начинаем со следующей смены.
    first = await db.shift_nag_since(day)
    if first and day <= first:
        return {"skip": "первый день", "day": day}

    op_token = os.getenv("OPERATOR_BOT_TOKEN", "")
    drv_token = os.getenv("DRIVER_BOT_TOKEN", "")
    try:
        staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
    except Exception as e:
        log.warning(f"[nag] перестановка не прочитана: {e}")

    open_d = await _open_districts(day)
    silent = await _silent_drivers(day)
    sent = 0

    # ── операторам: какие районы открыты ────────────────────────────────────
    op_chats = [d["telegram_id"] for d in staff.DEVICES] + list(staff.SENIOR_IDS)
    if open_d and op_token:
        names = ", ".join(f"{OFFICE_CODES.get(o,'')} {OFFICE_NAMES.get(o,o)}".strip()
                          for o in open_d)
        text = ("⏰ *Смена не закрыта*\n"
                f"Открыты: {names}\n\n"
                "Закройте в панели — «Смена» в шапке. Пока район открыт, продажи "
                "дня не окончательны и заявка в магазин не собирается.")
        for cid in op_chats:
            if _due(cid, now) and await _ping(op_token, cid, text, now):
                sent += 1
    else:
        for cid in op_chats:
            _forget(cid)

    # ── водителям: чего не хватает лично ────────────────────────────────────
    if drv_token:
        for name, cid, left in silent:
            if not _due(cid, now):
                continue
            what = " и ".join({"fuel": "бензину", "wash": "мойке"}.get(k, k) for k in left)
            text = ("⏰ *Расходы за смену не сданы*\n"
                    f"Нет ответа по {what}.\n\n"
                    "Впишите сумму или нажмите «не было» — вкладка «Расходы». "
                    "Пустой ответ и забытый расход выглядят одинаково, поэтому "
                    "ответить надо в любом случае.")
            if await _ping(drv_token, cid, text, now):
                sent += 1
    done = {cid for _, cid, _ in silent}
    for cid in [c for c in list(_LAST) if c not in done and c not in op_chats]:
        _forget(cid)

    if sent:
        log.info(f"[nag] {day}: открыто районов {len(open_d)}, "
                 f"молчат водителей {len(silent)}, послано {sent}")
    return {"day": day, "open": open_d, "silent": [s[0] for s in silent], "sent": sent}


async def loop(app):
    """Раз в минуту смотрим, кому пора напомнить. Сама частота — в _due."""
    import asyncio
    await asyncio.sleep(20)            # дать серверу подняться
    while True:
        try:
            await tick()
        except Exception as e:
            log.error(f"[nag] {e}")
        await asyncio.sleep(60)
