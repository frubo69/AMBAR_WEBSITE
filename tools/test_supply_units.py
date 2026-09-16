"""Приёмка в единицах склада (владелец, 16 сен 2026: «только в ящиках»): план
строки пива — коробки, сканы — по банке; строка закрывается на 48 банках, а не
на двух; «осталось» — до полкоробки вверх; недобор при завершении — в
единицах. mongomock + настоящие supply_routes.task_scan/_task_view/task_finish."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, supply_routes as sr, driver_routes as dr
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
async def notify(*a, **k): return None
sr._notify_done = notify
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db._db.supplies.insert_one({
        "_id": "S1", "status": "open", "at": NOW, "day": "2026-09-16",
        "items": [{"id": "p31", "name": "Heineken", "qty": 2, "by_district": {"jvc": 2}, "got": {"jvc": 0}},
                  {"id": "p1", "name": "Absolut", "qty": 3, "by_district": {"jvc": 3}, "got": {"jvc": 0}}],
        "tasks": {"jvc": {"driver": "Худоба", "driver_id": 1, "claimed_at": NOW, "started_at": None, "noscan_at": None,
                          "done_at": None, "cancelled_at": None, "erev": 0, "scanned": 0, "qty": 5, "positions": 2}}})
    sup = await db.supply_get("S1"); v = sr._task_view("S1", sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("план: пиво 2 коробки, водка 3; всего 5 единиц", (v["need"], {l["id"]: l["need"] for l in v["lines"]}), (5, {"p31": 2, "p1": 3}))
    for i in range(3):
        r = await sr.task_scan("S1", "jvc", "p31", f"h{i}", "Худоба", 1, None, "", False)
    eq("3 банки пива — принято 0 коробок, осталось 2 (не 0!)", (r["ok"], r["got"], r["left"]), (True, 0, 2))
    for i in range(3, 12):
        r = await sr.task_scan("S1", "jvc", "p31", f"h{i}", "Худоба", 1, None, "", False)
    eq("12 банок — полкоробки, осталось 1,5", (r["got"], r["left"]), (0.5, 1.5))
    for i in range(12, 48):
        r = await sr.task_scan("S1", "jvc", "p31", f"h{i}", "Худоба", 1, None, "", False)
    eq("48 банок — 2 коробки, строка закрыта", (r["got"], r["left"]), (2, 0))
    r = await sr.task_scan("S1", "jvc", "p31", "h48", "Худоба", 1, None, "", False)
    eq("49-я банка не принимается: строка полна", (r["ok"], r["verdict"]), (False, "full"))
    for i in range(2):
        r = await sr.task_scan("S1", "jvc", "p1", f"a{i}", "Худоба", 1, None, "", False)
    sup = await db.supply_get("S1"); v = sr._task_view("S1", sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("вид задачи: принято 4 из 5 единиц, водке осталось 1", (v["got"], v["need"], {l["id"]: l["left"] for l in v["lines"]}), (4, 5, {"p31": 0, "p1": 1}))
    lst = await db.supply_list(limit=5); eq("список поставок: принято по району 4 единицы", sr._task_units_got({**lst[0], "_id": "S1"}, "jvc"), 4)
    fin = await sr.task_finish("S1", "jvc", "Худоба", "", False)
    sup = await db.supply_get("S1"); gaps = sup["tasks"]["jvc"]["gaps"]
    eq("завершили с недобором: водка 1 единица, пиво без недобора", (fin.get("ok"), [(g["id"], g["gap"]) for g in gaps]), (True, [("p1", 1)]))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
