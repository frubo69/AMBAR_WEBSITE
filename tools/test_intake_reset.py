"""tools/intake_reset.py — обнулить приёмку районов, как будто её не начинали
(владелец, 21 сен 2026: B1 и B5 пересканируют заново).

mongomock + настоящие supply_routes._task_view / task_scan и склад
(_district_base):
  • коды районов стёрты, соседний район не тронут ни кодом, ни числом;
  • строки: принятое районом → 0, счётчик сканов строки − его коды;
  • задача: пустая, как новая; водитель остаётся; замок снят; версия +1;
  • склад района вернулся к тому, что был до приёмки; бутылку можно
    отсканировать заново, и она снова ложится на склад;
  • отказ без единого изменения: идёт скан, бутылку продали, перевезли,
    списали, прошли ревизией, район закрыт, строки не сходятся с реестром.
"""
import asyncio, copy, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, supply_routes as sr, stock_routes as SR
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_reset as IR

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

NOW = datetime.now(timezone.utc)
T0 = NOW - timedelta(days=2)
SID = "S1"


def задача(driver, **kw):
    return {"driver": driver, "driver_id": 7 if driver else 0, "claimed_at": NOW if driver else None,
            "started_at": NOW - timedelta(hours=1), "noscan_at": None, "done_at": None,
            "cancelled_at": None, "erev": 0, "qty": 5, "positions": 2, **kw}


def код(cid, pid, oid, qty=1, **kw):
    return {"_id": cid, "status": "active", "product_id": pid, "product_name": pid, "district": oid,
            "origin": oid, "src": "intake", "supply_id": SID, "qty": qty, "at": NOW - timedelta(minutes=30),
            "label": cid, **kw}


async def заново():
    d = db._db
    await d.supplies.delete_many({}); await d.qr_codes.delete_many({})
    await d.stock_transfers.delete_many({}); await d.writeoffs.delete_many({}); await d.audit_scans.delete_many({})
    await d.supplies.insert_one({
        "_id": SID, "status": "open", "at": NOW - timedelta(hours=2), "day": "2026-09-20",
        "items": [{"id": "p1", "name": "Absolut", "qty": 7, "scanned": 4,
                   "by_district": {"jvc": 3, "tecom": 2, "bbay": 2}, "got": {"jvc": 2, "tecom": 0, "bbay": 2}},
                  {"id": "p31", "name": "Heineken", "qty": 3, "scanned": 2,
                   "by_district": {"jvc": 1, "tecom": 1, "bbay": 1}, "got": {"jvc": 0.5, "tecom": 0.5, "bbay": 0}}],
        "tasks": {"jvc": задача("", scanned=3, undo=1, flags=[{"kind": "undo"}], last_at=NOW, rev=5),
                  "tecom": задача("Алишер", scanned=1, last_at=NOW),
                  "bbay": задача("Авазбек", scanned=2, rev=2)}})
    for c in [код("j1", "p1", "jvc"), код("j2", "p1", "jvc"), код("j3", "p31", "jvc", 0.5),
              код("t1", "p31", "tecom", 0.5), код("b1", "p1", "bbay"), код("b2", "p1", "bbay")]:
        await d.qr_codes.insert_one(c)
    for o, n in (("jvc", 10), ("tecom", 4), ("bbay", 6)):
        await db.save_stock_count(o, "2026-09-18", {"district": o, "day": "2026-09-18",
            "counted_at": T0.isoformat(), "first_time": False, "counted_by": 0,
            "lines": [{"id": "p1", "name": "Absolut", "price": 100, "unit": 1, "actual": n, "counted": True},
                      {"id": "p31", "name": "Heineken", "price": 100, "unit": 24, "actual": 1, "counted": True}]})
    SR.base_drop()


async def склад():
    SR.base_drop()
    b = await SR._district_base("2026-09-21")
    return {o: (b[o]["have_exact"].get("p1"), b[o]["have_exact"].get("p31")) for o in ("jvc", "tecom", "bbay")}


async def main():
    db._db = AsyncMongoMockClient()["ambar_reset"]
    d = db._db
    SR._biz_day = lambda *a, **k: "2026-09-21"

    print("── обнулили B1 и B5 ──────────────────────────────────────────")
    await заново()
    было = await склад()
    eq("склад до: пересчёт + приход", было, {"jvc": (12, 1.5), "tecom": (4, 1.5), "bbay": (8, 1)})
    res, нельзя = await IR.plan(SID, ["jvc", "tecom"])
    eq("можно", нельзя, [])
    await IR.apply(SID, *res)
    s = await d.supplies.find_one({"_id": SID})
    eq("коды B1 и B5 стёрты, B2 на месте",
       sorted(c["_id"] for c in await d.qr_codes.find({}).to_list(None)), ["b1", "b2"])
    eq("строки: принятое B1/B5 — ноль, B2 как было",
       [(it["got"]["jvc"], it["got"]["tecom"], it["got"]["bbay"]) for it in s["items"]], [(0, 0, 2), (0, 0, 0)])
    eq("счётчики сканов строк — минус коды B1/B5", [it["scanned"] for it in s["items"]], [2, 0])
    tj, tt, tb = s["tasks"]["jvc"], s["tasks"]["tecom"], s["tasks"]["bbay"]
    eq("задача B1 как новая, свободной и осталась",
       (tj["driver"], tj["scanned"], tj["undo"], tj["started_at"], tj["last_at"], tj["flags"], tj["gaps"], tj["rev"]),
       ("", 0, 0, None, None, [], [], 6))
    eq("задача B5 как новая, Алишер остался, версия пошла с нуля вверх",
       (tt["driver"], tt["scanned"], tt["started_at"], tt["rev"]), ("Алишер", 0, None, 1))
    eq("B2 не тронута", (tb["scanned"], tb["rev"], tb["started_at"] is not None), (2, 2, True))
    v = sr._task_view(SID, s, "tecom", tt, "Алишер")
    eq("водитель видит: ничего не принято, осталось всё, приёмка не начата",
       (v["got"], v["left"], v["need"], bool(v.get("started_at"))), (0, 3, 3, False))
    eq("склад B1/B5 — как до приёмки, B2 прежний", await склад(), {"jvc": (10, 1), "tecom": (4, 1), "bbay": (8, 1)})
    r = await sr.task_scan(SID, "tecom", "p31", "t1", "Алишер", 7, "", False)
    eq("ту же бутылку снова принимают", (r.get("ok"), r.get("got")), (True, 0.5))
    eq("и она снова на складе", (await склад())["tecom"], (4, 1.5))

    print("── когда нельзя — и ничего не тронуто ─────────────────────────")
    async def отказ(name, portit):
        await заново()
        await portit()
        до_s = await d.supplies.find_one({"_id": SID})
        до_c = sorted(c["_id"] for c in await d.qr_codes.find({}).to_list(None))
        res, нельзя = await IR.plan(SID, ["jvc", "tecom"])
        eq(f"{name}: отказ", bool(нельзя), True)
        eq(f"{name}: поставка и коды не тронуты",
           (await d.supplies.find_one({"_id": SID}) == до_s,
            sorted(c["_id"] for c in await d.qr_codes.find({}).to_list(None)) == до_c), (True, True))
    await отказ("идёт скан", lambda: d.supplies.update_one({"_id": SID}, {"$set": {"tasks.tecom.hold": {
        "who": "Алишер", "kind": "driver", "at": NOW, "until": NOW + timedelta(seconds=30)}}}))
    await отказ("бутылку продали", lambda: d.qr_codes.update_one({"_id": "j1"}, {"$set": {"status": "written"}}))
    await отказ("бутылку перевезли", lambda: d.qr_codes.update_one({"_id": "j2"}, {"$set": {"district": "silicon"}}))
    await отказ("есть перемещение", lambda: d.stock_transfers.insert_one({"code": "t1", "from": "tecom", "to": "jvc"}))
    await отказ("есть списание", lambda: d.writeoffs.insert_one({"_id": "w", "code": "j3", "district": "jvc"}))
    await отказ("прошла ревизией", lambda: d.audit_scans.insert_one({"_id": "jvc:2026-09-21:j1", "code": "j1"}))
    await отказ("район закрыт", lambda: d.supplies.update_one({"_id": SID}, {"$set": {"tasks.jvc.done_at": NOW}}))
    await отказ("строка не сходится с реестром", lambda: d.qr_codes.delete_one({"_id": "j2"}))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
