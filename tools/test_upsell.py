"""Бонус водителю за допродажу (владелец, 17 сен 2026): 5% от цены позиций,
вверх до пяти дирхамов («если 3 дирхама — водителю 5»),
добавленных к заказу после того, как водитель его взял. На заказе копится
upsell, в день водителя ложится расход «Бонус за допродажу» (остаётся у него),
отмена заказа снимает запись. mongomock + настоящие db.* и operator_routes."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, operator_routes as op
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    old = [{"id": "p1", "name": "Absolut", "qty": 1, "price": 100}, {"id": "p31", "name": "Heineken", "qty": 1, "pcs": 12, "price": 100},
           {"id": "p96", "name": "Jacob", "qty": 1, "price": 0, "gift": True}]
    new = [{"id": "p1", "name": "Absolut", "qty": 3, "price": 100}, {"id": "p31", "name": "Heineken", "qty": 1, "pcs": 24, "price": 200},
           {"id": "p10", "name": "Red Label", "qty": 1, "price": 100}, {"id": "p96", "name": "Jacob", "qty": 1, "price": 0, "gift": True}]
    lines = op._added_lines(old, new)
    eq("добавлено: +2 Absolut, пиво 12→24 как +100, Red Label; подарок не в счёт", [(l["id"], l["qty"], l["aed"]) for l in lines], [("p1", 2, 200.0), ("p31", 1, 100.0), ("p10", 1, 100.0)])
    await d.orders.insert_one({"order_id": "O1", "status": "approved", "driver": "Худоба", "office_id": "jvc", "source": "manual",
                               "timestamp": "2026-09-17T08:00:00", "confirmed_at": "2026-09-17T08:00:00+00:00", "day": "2026-09-17", "items": old, "total": 200})
    order = await db.get_order("O1")
    r = await op._upsell_credit("O1", order, old, new, "Худоба", "Оператор", "2026-09-17T08:30:00+00:00")
    eq("бонус: 5% от 400 = 20 AED за 4 позиции", (r["aed"], r["bonus"], r["n"]), (400.0, 20, 4))
    o = await db.get_order("O1")
    eq("на заказе записана допродажа", (o["upsell"]["aed"], o["upsell"]["bonus"], len(o["upsell"]["lines"])), (400.0, 20, 3))
    dd = await db.get_driver_day("2026-09-17", "Худоба")
    ex = [x for x in (dd or {}).get("extras") or [] if x.get("kind") == "upsell"]
    eq("в дне водителя расход «Бонус за допродажу» 20, согласован, по заказу", (len(ex), ex[0]["amount"], ex[0]["kind_t"], ex[0]["status"], ex[0]["order"], ex[0]["auto"]), (1, 20, "Бонус за допродажу", "approved", "O1", True))
    r2 = await op._upsell_credit("O1", o, new, new + [{"id": "p2", "name": "Stoli", "qty": 1, "price": 100}], "Худоба", "Оператор", "2026-09-17T08:40:00+00:00")
    o = await db.get_order("O1")
    eq("вторая правка копится: +5 → 25", (r2["bonus"], o["upsell"]["bonus"], len(o["upsell"]["events"])), (5, 25, 2))
    dd = await db.get_driver_day("2026-09-17", "Худоба")
    eq("в дне две строки бонуса", sum(x["amount"] for x in dd["extras"] if x.get("kind") == "upsell"), 25)
    # Округление вверх до пяти (владелец, 17 сен 2026: «если 3 дирхама — водителю 5»).
    await d.orders.insert_one({"order_id": "O2", "status": "approved", "driver": "Алишер", "office_id": "tecom", "source": "manual",
                               "timestamp": "2026-09-17T18:00:00", "confirmed_at": "2026-09-17T18:00:00+00:00", "day": "2026-09-17",
                               "items": [{"id": "p1", "name": "Absolut", "qty": 1, "price": 100}], "total": 100})
    o2 = await db.get_order("O2")
    r3 = await op._upsell_credit("O2", o2, o2["items"], o2["items"] + [{"id": "p97", "name": "Marlboro Gold", "qty": 1, "price": 50}],
                                 "Алишер", "Умар", "2026-09-17T19:40:00+00:00")
    eq("добавили на 50 → 5% это 2,5, бонус 5", (r3["aed"], r3["bonus"]), (50.0, 5))
    o2 = await db.get_order("O2")
    r4 = await op._upsell_credit("O2", o2, o2["items"], o2["items"] + [{"id": "p2", "name": "Stoli", "qty": 1, "price": 220}],
                                 "Алишер", "Умар", "2026-09-17T19:50:00+00:00")
    eq("добавили на 220 → 5% это 11, бонус 15", (r4["aed"], r4["bonus"]), (220.0, 15))
    dd2 = await db.get_driver_day("2026-09-17", "Алишер")
    eq("в дне Алишера две строки бонуса на 20",
       sum(x["amount"] for x in (dd2 or {}).get("extras") or [] if x.get("kind") == "upsell"), 20)
    eq("правка без добавлений — бонуса нет", await op._upsell_credit("O1", o, new, new, "Худоба", "Оператор", "x"), None)
    eq("убавили — бонуса нет", await op._upsell_credit("O1", o, new, old, "Худоба", "Оператор", "x"), None)
    n = await db.driver_expense_pull_order("2026-09-17", "Худоба", "O1", "upsell")
    dd = await db.get_driver_day("2026-09-17", "Худоба")
    eq("отмена заказа снимает строки бонуса", (n, sum(x["amount"] for x in dd["extras"] if x.get("kind") == "upsell")), (1, 0))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
