"""Замок правок района с момента «Начать сканирование» (камера открыта), а
не с первой бутылки (владелец, 16 сен 2026). Остальные районы правятся.
На mongomock: тот же атомарный guard, что в бою."""
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
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db._db.supplies.insert_one({
        "_id": "S1", "status": "open", "at": NOW, "day": "2026-09-16",
        "items": [{"id": "gin", "name": "Джин", "qty": 12, "by_district": {"jvc": 6, "bbay": 6}, "got": {"jvc": 0, "bbay": 0}}],
        "tasks": {"jvc": {"driver": "Худоба", "driver_id": 1, "claimed_at": NOW, "started_at": None, "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0},
                  "bbay": {"driver": "", "driver_id": 0, "claimed_at": None, "started_at": None, "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0}}})
    ok = await db.supply_line_set("S1", "jvc", "gin", 7, "Джин", NOW)
    eq("до камеры: район правится (водитель взял, но не начал)", bool(ok), True)
    sup = await db.supply_get("S1")
    v = sr._task_view("S1", sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("вид: не заперт", (v["locked"], v["lock_why"]), (False, ""))
    r = await sr.task_hold("S1", "jvc", "Худоба", 1, True)
    eq("«Начать сканирование» (камера) — замок взят", r.get("ok"), True)
    sup = await db.supply_get("S1")
    eq("started_at поставлен камерой, до первой бутылки", bool(sup["tasks"]["jvc"]["started_at"]), True)
    ok = await db.supply_line_set("S1", "jvc", "gin", 8, "Джин", NOW)
    eq("правка района после камеры не проходит (guard)", ok, None)
    v = sr._task_view("S1", sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("вид старшему: заперт, причина «сканируют», время есть", (v["locked"], v["lock_why"], bool(v["lock_at"])), (True, "scan", True))
    ok = await db.supply_line_set("S1", "bbay", "gin", 9, "Джин", NOW)
    eq("другой район, который не начали, правится", bool(ok), True)
    r = await sr.task_hold("S1", "jvc", "Худоба", 1, False)
    sup = await db.supply_get("S1")
    eq("камеру закрыл без скана — замок правок остаётся", (r.get("ok"), bool(sup["tasks"]["jvc"]["started_at"])), (True, True))
    r = await sr.task_hold("S1", "jvc", "Худоба", 1, True)
    sup2 = await db.supply_get("S1")
    eq("второе открытие камеры время начала не двигает", sup2["tasks"]["jvc"]["started_at"], sup["tasks"]["jvc"]["started_at"])
    r = await sr.task_hold("S1", "bbay", "Фарух", 2, True)
    eq("чужую (не взятую) задачу камерой не запереть", r.get("verdict"), "not_mine")
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
