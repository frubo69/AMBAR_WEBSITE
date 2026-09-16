"""Склад и статус заказа (владелец, 16 сен 2026): доставлен — списано; вернули в
доставку — вернулось на склад сразу; доставлен снова — снова списано; отменён
после доставки — вернулось; принят до пересчёта, довезён после — списано;
тестовый и чужого района — не трогают. mongomock + настоящие db.sold_since /
delivered_stamp и основа _district_base."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
DAY = "2026-09-16"
async def have(pid="p1", oid="jvc"):
    SR.base_drop(); b = await SR._district_base(DAY); return b[oid]["have_exact"].get(pid)
async def stamp(): return await db.delivered_stamp("2026-09-13T00:00:00")
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    await db.save_stock_count("jvc", DAY, {"district": "jvc", "day": DAY, "counted_at": "2026-09-16T06:00:00+00:00", "first_time": True, "counted_by": 0,
        "lines": [{"id": "p1", "name": "Absolut", "price": 100, "unit": 1, "actual": 10, "counted": True},
                  {"id": "p31", "name": "Heineken", "price": 250, "unit": 24, "actual": 2, "counted": True}]})
    await d.orders.insert_one({"id": "O1", "timestamp": "2026-09-16T11:00:00", "status": "approved", "office_id": "jvc", "updated_at": "2026-09-16T11:00:00",
                               "items": [{"id": "p1", "qty": 3}, {"id": "p31", "qty": 1, "pcs": 12}, {"id": "p1", "qty": 1, "price": 0, "gift": True}]})
    eq("принят, не доставлен — на складе всё: 10 и 2 коробки", (await have(), await have("p31")), (10, 2))
    s0 = await stamp()
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "delivered", "delivered_at": "2026-09-16T11:30:00+00:00", "updated_at": "2026-09-16T11:30:00"}})
    eq("доставлен → списано: 10 − 3 − подарок 1 = 6, пиво 2 − 0,5 = 1,5", (await have(), await have("p31")), (6, 1.5))
    s1 = await stamp(); eq("метка доставок изменилась → кэш основы сброшен", s1 != s0, True)
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "approved", "updated_at": "2026-09-16T11:40:00"}})
    eq("вернули в доставку → вернулось на склад сразу", (await have(), await have("p31")), (10, 2))
    s2 = await stamp(); eq("метка изменилась и на возврате", s2 != s1, True)
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "delivered", "updated_at": "2026-09-16T11:50:00"}})
    eq("доставлен снова (бот оператора: без новой delivered_at) → снова списано", (await have(), await have("p31")), (6, 1.5))
    s3 = await stamp(); eq("метка отличается от прежней доставки — по updated_at", s3 != s1 and s3 != s2, True)
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "cancelled", "cancelled_at": "2026-09-16T12:00:00", "updated_at": "2026-09-16T12:00:00"}})
    eq("отменён после доставки → бутылки снова на складе", (await have(), await have("p31")), (10, 2))
    await d.orders.insert_one({"id": "O2", "timestamp": "2026-09-16T05:00:00", "status": "delivered", "office_id": "jvc", "delivered_at": "2026-09-16T07:00:00+00:00", "updated_at": "2026-09-16T07:00:00", "items": [{"id": "p1", "qty": 2}]})
    eq("принят до пересчёта, довезён после — списан по моменту доставки: 8", await have(), 8)
    await d.orders.insert_one({"id": "O3", "timestamp": "2026-09-16T12:10:00", "status": "delivered", "office_id": "jvc", "test": True, "delivered_at": "2026-09-16T12:20:00+00:00", "items": [{"id": "p1", "qty": 5}]})
    await d.orders.insert_one({"id": "O4", "timestamp": "2026-09-16T12:10:00", "status": "delivered", "office_id": "bbay", "delivered_at": "2026-09-16T12:20:00+00:00", "items": [{"id": "p1", "qty": 5}]})
    eq("тестовый заказ и заказ другого района JVC не трогают: 8", await have(), 8)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
