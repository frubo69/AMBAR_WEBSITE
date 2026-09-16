"""Заявка от нормы-снимка (владелец, 16 сен 2026): нормой назначен склад на
начало смены; заявка = норма − остаток = ровно то, что продали с тех пор; не
поехали — назавтра просит за два дня; правка заявки живёт день и норму не
трогает; ноль — тоже норма; полкоробки пива в заявке — коробка. На mongomock
с настоящими db.* и stock_routes.order_rows."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR
from datetime import datetime, timezone
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D1, D2 = "2026-09-16", "2026-09-17"
async def sug(district, day, detail=False):      # «условная норма» по продажам — её быть не должно в заявке
    return {"p2": {"norm": 5} if detail else 5} if district == "jvc" else {}
SR._suggested_norms = sug
def count(oid, lines, at):
    return {"district": oid, "day": D1, "counted_at": at, "first_time": True, "counted_by": 0,
            "lines": [{"id": pid, "name": pid, "price": 100, "unit": SR._unit(SR._catalog()[pid]),
                       "actual": q, "counted": True} for pid, q in lines.items()]}
async def rows(day):
    SR.base_drop()
    d = await SR.order_rows(day)
    return {r["id"]: r for r in d["all_rows"]}, d
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db.save_stock_count("jvc", D1, count("jvc", {"p1": 10, "p31": 2.5, "p2": 0}, "2026-09-16T06:00:00+00:00"))
    for pid, n in (("p1", 10), ("p31", 2.5), ("p2", 0)):
        await db.set_stock_norm("jvc", pid, n)
    await db.stock_norm_rule_set({"kind": "snapshot", "day": D1})
    r, d = await rows(D1)
    eq("склад = норма → заявка пустая", (r["p1"]["cells"]["jvc"]["need"], r["p31"]["cells"]["jvc"]["need"], d["total_qty"]), (0, 0, 0))
    eq("норма-ноль уважается, расчёт 5 не подставляется", (r["p2"]["cells"]["jvc"]["norm"], r["p2"]["cells"]["jvc"]["suggested"], r["p2"]["cells"]["jvc"]["need"]), (0, 5, 0))
    eq("правило норм уходит на экран", d["norm_rule"].get("kind"), "snapshot")
    await db._db.orders.insert_one({"timestamp": "2026-09-16T12:00:00", "status": "delivered", "office_id": "jvc",
                                    "items": [{"id": "p1", "qty": 3}, {"id": "p31", "qty": 1, "pcs": 12}]})
    r, d = await rows(D1)
    c1, c31 = r["p1"]["cells"]["jvc"], r["p31"]["cells"]["jvc"]
    eq("продали 3 → заявка 3; остаток и норма точные", (c1["need"], c1["have"], c1["norm"]), (3, 7, 10))
    eq("12 банок = полкоробки → в заявке коробка, остаток 2", (c31["need"], c31["have"], c31["norm"]), (1, 2, 2.5))
    await db.zayavka_edit_set(D1, "p1", "jvc", 1)
    r, d = await rows(D1)
    eq("правка на день: просим 1, расчёт 3 рядом, норма не тронута", (r["p1"]["cells"]["jvc"]["need"], r["p1"]["cells"]["jvc"]["calc"], r["p1"]["cells"]["jvc"]["edited"], (await db.get_stock_norms())["jvc:p1"]), (1, 3, True, 10))
    r, d = await rows(D2)
    eq("назавтра без закупки заявка снова смотрит на остаток: 3", (r["p1"]["cells"]["jvc"]["need"], r["p1"]["cells"]["jvc"]["edited"]), (3, False))
    await db._db.orders.insert_one({"timestamp": "2026-09-17T12:00:00", "status": "delivered", "office_id": "jvc", "items": [{"id": "p1", "qty": 2}]})
    r, d = await rows(D2)
    eq("два дня без закупки — заявка за два дня: 5", r["p1"]["cells"]["jvc"]["need"], 5)
    for i in range(5):
        await db._db.qr_codes.insert_one({"code": f"c{i}", "district": "jvc", "product_id": "p1", "src": "intake", "status": "active", "at": datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)})
    r, d = await rows(D2)
    eq("приняли 5 → остаток вернулся к норме, заявка 0", (r["p1"]["cells"]["jvc"]["have"], r["p1"]["cells"]["jvc"]["need"]), (10, 0))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
