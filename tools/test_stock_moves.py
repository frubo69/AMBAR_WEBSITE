"""Переезд между районами двигает остаток обоих районов сразу, без пересчёта
(владелец, 16 сен 2026: «перемещение должно происходить мгновенно»).
На mongomock: настоящие db.save_stock_count / add_stock_transfer /
delete_stock_transfer / sold_since и настоящая основа _district_base."""
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
def count(oid, lines, at):
    return {"district": oid, "day": DAY, "counted_at": at, "first_time": True, "counted_by": 0,
            "lines": [{"id": pid, "name": pid, "price": 100, "unit": SR._unit(SR._catalog()[pid]),
                       "actual": q, "counted": True} for pid, q in lines.items()]}
async def base():
    SR.base_drop()
    return await SR._district_base(DAY)
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db.save_stock_count("jvc", DAY, count("jvc", {"p1": 10}, "2026-09-16T10:00:00+00:00"))
    await db.save_stock_count("silicon", DAY, count("silicon", {"p1": 5, "p31": 2}, "2026-09-16T10:00:00+00:00"))
    b = await base()
    eq("до переездов: JVC 10, Силикон 5", (b["jvc"]["have_exact"]["p1"], b["silicon"]["have_exact"]["p1"]), (10, 5))
    t1 = await db.add_stock_transfer({"day": DAY, "from": "jvc", "to": "silicon", "product_id": "p1", "qty": 2, "at": "2026-09-16T11:00:00+00:00"})
    b = await base()
    eq("переезд 2 после пересчёта: JVC 8, Силикон 7 — сразу", (b["jvc"]["have_exact"]["p1"], b["silicon"]["have_exact"]["p1"]), (8, 7))
    eq("чистый итог переездов виден основе", (b["jvc"]["moved"].get("p1"), b["silicon"]["moved"].get("p1")), (-2, 2))
    await db.add_stock_transfer({"day": DAY, "from": "jvc", "to": "silicon", "product_id": "p1", "qty": 3, "at": "2026-09-16T09:00:00+00:00"})
    b = await base()
    eq("переезд ДО пересчёта уже в остатке — не трогаем", (b["jvc"]["have_exact"]["p1"], b["silicon"]["have_exact"]["p1"]), (8, 7))
    await db.add_stock_transfer({"day": "2026-09-15", "from": "jvc", "to": "silicon", "product_id": "p1", "qty": 4, "at": "2026-09-16T11:30:00+00:00"})
    b = await base()
    eq("переезд задним числом за день до пересчёта — тоже уже в остатке", (b["jvc"]["have_exact"]["p1"], b["silicon"]["have_exact"]["p1"]), (8, 7))
    t5 = await db.add_stock_transfer({"day": DAY, "from": "jvc", "to": "bbay", "product_id": "p1", "qty": 1, "at": "2026-09-16T11:40:00+00:00"})
    b = await base()
    eq("в район без пересчёта бутылка приезжает и видна", (b["jvc"]["have_exact"]["p1"], b["bbay"]["have_exact"].get("p1")), (7, 1))
    for i in range(12):
        await db.add_stock_transfer({"day": DAY, "from": "silicon", "to": "jvc", "product_id": "p31", "qty": 1 / 24, "src": "qr", "code": f"c{i}", "at": f"2026-09-16T11:50:{i:02d}+00:00"})
    b = await base()
    eq("12 банок пива сканом = полкоробки: Силикон 1,5, JVC 0,5", (b["silicon"]["have_exact"]["p31"], b["jvc"]["have_exact"].get("p31")), (1.5, 0.5))
    await db._db.orders.insert_one({"timestamp": "2026-09-16T12:00:00", "status": "delivered", "office_id": "silicon", "items": [{"id": "p1", "qty": 3}]})
    b = await base()
    eq("продажа после переезда списывает с уже увеличенного остатка: Силикон 7 − 3 = 4", b["silicon"]["have_exact"]["p1"], 4)
    await db.delete_stock_transfer(t5)
    b = await base()
    eq("переезд отменён (удалён) — JVC вернул бутылку, у BBay её нет", (b["jvc"]["have_exact"]["p1"], "p1" in b["bbay"]["have_exact"]), (8, False))
    await db.add_stock_transfer({"day": DAY, "from": "bbay", "to": "jvc", "product_id": "p1", "qty": 5, "at": "2026-09-16T12:10:00+00:00"})
    b = await base()
    eq("отдать из района без пересчёта нечего, принимающий получает", (b["bbay"]["have_exact"].get("p1"), b["jvc"]["have_exact"]["p1"]), (None, 13))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
