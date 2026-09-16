"""Приёмка в единицах склада (владелец, 16 сен 2026): QR клеится на коробку —
код несёт 1 (коробка) или 0.5 (полкоробки), банки не сканируют. План строки —
единицы, принятое — сумма qty кодов, строка закрывается ровно на плане, коробка
через план не перелезает; недобор при завершении — в единицах. mongomock +
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
async def scan(sid, pid, code, qty=None):
    return await sr.task_scan(sid, "jvc", pid, code, "Худоба", 1, "", False, qty=qty)
async def view(sid):
    s = await db.supply_get(sid); return s, sr._task_view(sid, s, "jvc", s["tasks"]["jvc"], "Худоба")
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db._db.supplies.insert_one(sup("S1", "p31", "Heineken", 2))
    await db._db.supplies.insert_one(sup("S2", "p1", "Absolut", 3))
    s, v = await view("S1"); eq("план: пиво 2 коробки", (v["need"], v["lines"][0]["left"]), (2, 2))
    r = await scan("S1", "p31", "box1")
    eq("код на коробке — принята 1 коробка, осталось 1", (r["ok"], r["got"], r["left"], r["qty"]), (True, 1, 1, 1))
    r = await scan("S1", "p31", "half1", qty="0.5")
    eq("код на полкоробки — 1,5, осталось 0,5", (r["got"], r["left"]), (1.5, 0.5))
    r = await scan("S1", "p31", "box2")
    eq("целая коробка через план не перелезает: полна", (r["ok"], r["verdict"]), (False, "full"))
    r = await scan("S1", "p31", "half2", qty="0,5")
    eq("полкоробки закрывают строку: 2 из 2", (r["ok"], r["got"], r["left"]), (True, 2, 0))
    codes = {c["_id"]: c for c in await db._db.qr_codes.find({}).to_list(length=20)}
    eq("коды в реестре несут qty: коробка 1, полкоробки 0.5", (codes["box1"]["qty"], codes["half1"]["qty"]), (1, 0.5))
    u = await sr.task_undo("S1", "jvc", "half2", "Худоба", False)
    s, v = await view("S1")
    eq("отмена скана полкоробки снимает 0,5: снова осталось 0,5", (u.get("ok"), v["got"], v["lines"][0]["left"]), (True, 1.5, 0.5))
    eq("список поставок: принято по району 1,5 единицы", sr._task_units_got(s, "jvc"), 1.5)
    fin = await sr.task_finish("S1", "jvc", "Худоба", "", False)
    s = await db.supply_get("S1"); gaps = s["tasks"]["jvc"]["gaps"]
    eq("завершили с недобором в единицах: пиво 0,5", (fin.get("ok"), [(g["id"], g["gap"]) for g in gaps]), (True, [("p31", 0.5)]))
    r = await scan("S2", "p1", "a1", qty="0.5")
    eq("у водки полкоробки не бывает — код несёт 1", (r["ok"], r["got"], r["qty"]), (True, 1, 1))
    r = await scan("S2", "p1", "a2")
    s, v = await view("S2"); eq("водка: принято 2 из 3, осталось 1", (v["got"], v["lines"][0]["left"]), (2, 1))
    fin = await sr.task_finish("S2", "jvc", "Худоба", "", False)
    s = await db.supply_get("S2"); eq("недобор водки 1", [(g["id"], g["gap"]) for g in s["tasks"]["jvc"]["gaps"]], [("p1", 1)])
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
