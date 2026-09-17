"""Приёмка в единицах склада (владелец, 16–17 сен 2026): QR клеится на коробку,
на каждой коробке два кода — код пива несёт 0.5, код бутылки 1; банки не
сканируют, qty решает сервер. План строки — единицы, принятое — сумма qty
кодов, строка закрывается ровно на плане; недобор при завершении — в единицах. mongomock +
настоящие supply_routes.task_scan/_task_view/task_undo/task_finish. По одной
позиции на поставку: позиционный оператор items.$ у mongomock бьёт в первый
элемент массива, в настоящей Mongo — в найденный."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, supply_routes as sr
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
async def notify(*a, **k): return None
sr._notify_done = notify
def sup(sid, pid, name, n):
    return {"_id": sid, "status": "open", "at": NOW, "day": "2026-09-16",
            "items": [{"id": pid, "name": name, "qty": n, "by_district": {"jvc": n}, "got": {"jvc": 0}}],
            "tasks": {"jvc": {"driver": "Худоба", "driver_id": 1, "claimed_at": NOW, "started_at": None, "noscan_at": None,
                              "done_at": None, "cancelled_at": None, "erev": 0, "scanned": 0, "qty": n, "positions": 1}}}
async def scan(sid, pid, code):
    return await sr.task_scan(sid, "jvc", pid, code, "Худоба", 1, "", False)
async def view(sid):
    s = await db.supply_get(sid); return s, sr._task_view(sid, s, "jvc", s["tasks"]["jvc"], "Худоба")
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db._db.supplies.insert_one(sup("S1", "p31", "Heineken", 2))
    await db._db.supplies.insert_one(sup("S2", "p1", "Absolut", 3))
    s, v = await view("S1"); eq("план: пиво 2 коробки", (v["need"], v["lines"][0]["left"]), (2, 2))
    r = await scan("S1", "p31", "b1")
    eq("код пива — полкоробки: принято 0,5, осталось 1,5", (r["ok"], r["got"], r["left"], r["qty"]), (True, 0.5, 1.5, 0.5))
    r = await scan("S1", "p31", "b2")
    eq("второй код той же коробки — 1, осталось 1", (r["got"], r["left"]), (1, 1))
    r = await scan("S1", "p31", "b3")
    eq("третий — 1,5, осталось 0,5", (r["got"], r["left"]), (1.5, 0.5))
    r = await scan("S1", "p31", "b4")
    eq("четвёртый закрывает строку: 2 из 2", (r["ok"], r["got"], r["left"]), (True, 2, 0))
    r = await scan("S1", "p31", "b5")
    eq("пятый через план не перелезает: полна", (r["ok"], r["verdict"]), (False, "full"))
    codes = {c["_id"]: c for c in await db._db.qr_codes.find({}).to_list(length=20)}
    eq("коды пива в реестре несут 0.5", sorted({c["qty"] for c in codes.values()}), [0.5])
    u = await sr.task_undo("S1", "jvc", "b4", "Худоба", False)
    s, v = await view("S1")
    eq("отмена скана снимает 0,5: снова осталось 0,5", (u.get("ok"), v["got"], v["lines"][0]["left"]), (True, 1.5, 0.5))
    eq("список поставок: принято по району 1,5 единицы", sr._task_units_got(s, "jvc"), 1.5)
    fin = await sr.task_finish("S1", "jvc", "Худоба", "", False)
    s = await db.supply_get("S1"); gaps = s["tasks"]["jvc"]["gaps"]
    eq("завершили с недобором в единицах: пиво 0,5", (fin.get("ok"), [(g["id"], g["gap"]) for g in gaps]), (True, [("p31", 0.5)]))
    r = await scan("S2", "p1", "a1")
    eq("у водки код — бутылка: 1", (r["ok"], r["got"], r["qty"]), (True, 1, 1))
    r = await scan("S2", "p1", "a2")
    s, v = await view("S2"); eq("водка: принято 2 из 3, осталось 1", (v["got"], v["lines"][0]["left"]), (2, 1))
    fin = await sr.task_finish("S2", "jvc", "Худоба", "", False)
    s = await db.supply_get("S2"); eq("недобор водки 1", [(g["id"], g["gap"]) for g in s["tasks"]["jvc"]["gaps"]], [("p1", 1)])
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
