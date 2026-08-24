#!/usr/bin/env python3
"""Восстановить, кто работал, а кто был дома — по доставленным заказам.

Расход на питание считается по отметке в driver_days: работал — 80 дирхам,
был дома — 40. Отметку ставит человек, и за прошлые месяцы её почти нигде нет:
в панели у таких дней расход показывался нулём, хотя людей кормили каждый день.

Заказы, впрочем, помнят, кто их вёз — с того дня, как перестали быть
обезличенными. Значит день восстанавливается: кто вёз заказы, тот работал;
кто в этот день не вёз ни одного, но вёл их до и после — был дома.

Осторожность, ради которой всё это писалось отдельным скриптом:
  • ответ человека не трогаем никогда — заполняем только пустые дни;
  • водителю не приписываем дни до его первой и после последней доставки:
    иначе тем, кто ещё не работал или уже ушёл, задним числом начислится еда;
  • текущие сутки пропускаем: они ещё идут, и «не вёз» пока ничего не значит;
  • всё записанное помечается `filled: "orders"` — видно, что это восстановление,
    и его можно снять одной командой.

    python3 tools/backfill_driver_days.py --dry     # посчитать, ничего не меняя
    python3 tools/backfill_driver_days.py           # записать
    python3 tools/backfill_driver_days.py --undo    # снять всё восстановленное
"""
import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/ambar")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db                                                      # noqa: E402
import config_staff as staff                                   # noqa: E402

DUBAI = timezone(timedelta(hours=4))
SHIFT_START = 12
MEAL_WORK = staff.MEAL_WORKING
MEAL_OFF = staff.MEAL_OFF


def biz_day(ts) -> str | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(DUBAI)
    if d.hour < SHIFT_START:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


async def undo() -> None:
    await db.connect()
    d = db._db_or_none()
    res = await d.driver_days.update_many(
        {"filled": "orders"},
        {"$unset": {"working": "", "filled": "", "filled_at": ""}})
    print(f"снято восстановленных отметок: {res.modified_count}")


async def run(dry: bool) -> None:
    await db.connect()
    d = db._db_or_none()

    orders = await d.orders.find(
        {"driver": {"$nin": ["", None]}},
        {"_id": 0, "driver": 1, "timestamp": 1, "status": 1}).to_list(20000)

    by_day: dict = defaultdict(set)
    seen_first: dict = {}
    seen_last: dict = {}
    for o in orders:
        day = biz_day(o.get("timestamp"))
        name = (o.get("driver") or "").strip()
        if not (day and name):
            continue
        by_day[day].add(name)
        seen_first[name] = min(seen_first.get(name, day), day)
        seen_last[name] = max(seen_last.get(name, day), day)

    if not by_day:
        print("в заказах нет имён водителей — восстанавливать нечего")
        return

    today = biz_day(datetime.now(timezone.utc).isoformat())
    days = sorted(day for day in by_day if day != today)
    print(f"дни с именами водителей: {days[0]} … {days[-1]} ({len(days)})")

    existing: dict = defaultdict(dict)
    for r in await d.driver_days.find({}, {"_id": 0}).to_list(20000):
        existing[r.get("day")][r.get("driver")] = r

    work = off = kept = 0
    money = 0
    per_day = []
    for day in days:
        worked = by_day[day]
        # Дома — только те, кто в этот период вообще работал: между первой и
        # последней доставкой. Остальным еду задним числом не назначаем.
        home = {n for n in seen_first
                if n not in worked and seen_first[n] <= day <= seen_last[n]}
        dw = dof = 0
        for name, is_work in [(n, True) for n in sorted(worked)] + [(n, False) for n in sorted(home)]:
            prev = existing.get(day, {}).get(name)
            if prev and prev.get("working") is not None:
                kept += 1
                continue                       # ответ человека не трогаем
            if not dry:
                await db.save_driver_day(day, name, {
                    "working": is_work, "filled": "orders",
                    "filled_at": datetime.now(timezone.utc).isoformat()})
            if is_work:
                work += 1; dw += 1; money += MEAL_WORK
            else:
                off += 1; dof += 1; money += MEAL_OFF
        per_day.append((day, dw, dof, dw * MEAL_WORK + dof * MEAL_OFF))

    print("\nдень          работали  дома   питание")
    for day, dw, dof, m in per_day:
        print(f"{day}      {dw:5}  {dof:4}   {m:6} AED")

    print(f"\n{'посчитано' if dry else 'записано'}: рабочих дней {work}, выходных {off}, "
          f"чужих отметок не тронуто {kept}")
    print(f"расход на питание за период: {money} AED")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    asyncio.run(undo() if a.undo else run(a.dry))


if __name__ == "__main__":
    main()
