"""Карточка дисциплины: что на человеке — штрафы, прощённое, ждущее.

    python3 tools/test_discipline.py
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, discipline                                              # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

СЕГОДНЯ = "2026-10-10"
now = datetime.now(timezone.utc)


async def main():
    db._db = AsyncMongoMockClient()["ambar_disc"]; d = db._db
    await d.fin_pay_items.insert_many([
        {"_id": "a", "name": "Худоба", "kind": "fine", "amount": 200, "day": "2026-10-05", "reason": "Отключение геолокации — в 19:40", "at": now},
        {"_id": "b", "name": "Худоба", "kind": "fine", "amount": 600, "day": "2026-09-20", "reason": "Превышение скорости · 20 – 30 км/ч", "at": now},
        {"_id": "c", "name": "Худоба", "kind": "fine", "amount": 200, "day": "2026-10-02", "reason": "Отключение геолокации", "at": now,
         "cancelled_at": now},
        {"_id": "d", "name": "Фарух", "kind": "hold", "amount": 86, "day": "2026-10-03", "reason": "", "at": now},
        {"_id": "e", "name": "Фарух", "kind": "advance", "amount": 500, "day": "2026-10-03", "at": now},     # не штраф
        {"_id": "f", "name": "Худоба", "kind": "fine", "amount": 999, "day": "2026-01-01", "reason": "давно", "at": now}])
    await d.fine_pending.insert_many([
        {"_id": "geo_off:2026-10-08:Худоба", "kind": "geo_off", "name": "Худоба", "day": "2026-10-08", "status": "pending", "amount": 200, "times": ["21:05"]},
        {"_id": "late_shift:2026-10-07:Фарух", "kind": "late_shift", "name": "Фарух", "day": "2026-10-07", "status": "assigned", "note": "открыл в 18:40, правило — до 18:00"},
        {"_id": "geo_off:2026-10-06:Фарух", "kind": "geo_off", "name": "Фарух", "day": "2026-10-06", "status": "declined", "amount": 200, "times": ["20:00"]},
        {"_id": "geo_off:2026-10-05:Худоба", "kind": "geo_off", "name": "Худоба", "day": "2026-10-05", "status": "assigned", "amount": 200, "item": "a"}])
    r = await discipline.build(90, СЕГОДНЯ)
    by = {p["name"]: p for p in r["people"]}
    h, f = by["Худоба"], by["Фарух"]
    eq("Худоба: два штрафа на 800, один ждёт, одна амнистия", (h["fines"], h["sum"], h["pending"], h["amnesty"]), (2, 800, 1, 1))
    eq("за этот месяц — один на 200", (h["month_fines"], h["month_sum"]), (1, 200))
    eq("назначенный по решению не посчитан дважды", sum(1 for x in h["rows"] if x["day"] == "2026-10-05"), 1)
    eq("виды — без подробностей, частые сверху", [(k["t"], k["n"]) for k in h["kinds"]],
       [("Отключение геолокации", 3), ("Превышение скорости", 1)])
    eq("давний штраф вне периода", any(x["t"] == "давно" for x in h["rows"]), False)
    eq("без нарушений — дней от последнего решённого", h["clean"], 5)
    eq("Фарух: удержание, прощённый, урезанное питание; аванс не в счёт",
       (f["fines"], f["sum"], f["forgiven"], f["meal"], f["events"]), (1, 86, 1, 1, 3))
    eq("сверху — у кого больше", [p["name"] for p in r["people"]], ["Худоба", "Фарух"])
    eq("итоги", r["totals"], {"fines": 3, "sum": 886, "pending": 1, "forgiven": 1, "meal": 1})
    eq("короткий период отрезает сентябрь", (await discipline.build(15, СЕГОДНЯ))["totals"]["fines"], 2)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
