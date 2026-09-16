"""Ревизия при складе, заведённом количеством (владелец, 16 сен 2026: «у нас 16
бутылок на районе, а не 2»): числится всё, недостача — только по кодовым,
бутылки без кодов после завершения остаются на складе. mongomock + настоящие
db.save_stock_count / qr_add и настоящие _audit_expected/_audit_lines."""
import asyncio, os, sys
from datetime import datetime, timezone
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
SCANS = {}
async def scan_counts(district, day): return dict(SCANS)
db.audit_scan_counts = scan_counts
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    await db.save_stock_count("jvc", DAY, {"district": "jvc", "day": DAY, "counted_at": "2026-09-16T06:00:00+00:00", "first_time": True, "counted_by": 0,
        "lines": [{"id": "p1", "name": "Absolut", "price": 100, "unit": 1, "actual": 16, "counted": True},
                  {"id": "p31", "name": "Heineken", "price": 250, "unit": 24, "actual": 2, "counted": True}]})
    at = datetime(2026, 9, 16, 7, 0, tzinfo=timezone.utc)
    for i in range(2):     # два Absolut заведены кодами (режим cover — остаток не растёт)
        await db.qr_add(f"a{i}", "p1", "Absolut", "jvc", 1, at, "", extra={"src": "cover"})
    SR.base_drop()
    lines, counted = await SR._audit_lines("jvc", DAY)
    r = {l["id"]: l for l in lines}
    eq("Absolut: числится 16, кодовых 2, без кодов 14", (r["p1"]["expected"], r["p1"]["coded"], r["p1"]["noqr"]), (16, 2, 14))
    eq("Heineken: числится 2 коробки, кодовых 0, без кодов 2", (r["p31"]["expected"], r["p31"]["coded"], r["p31"]["noqr"]), (2, 0, 2))
    eq("до сканов «не хватает» ровно кодовых (их ещё не увидели), без кодов пропавшими не считаются", (r["p1"]["diff"], r["p31"]["diff"]), (2, 0))
    SCANS.update({"p1": 1})
    lines, _ = await SR._audit_lines("jvc", DAY); r = {l["id"]: l for l in lines}
    eq("камера увидела 1 из 2 кодовых → недостача 1", (r["p1"]["actual"], r["p1"]["diff"]), (1, 1))
    t = SR._audit_totals(lines)
    eq("итоги: числится 18 единиц, кодовых 2, недостача 1", (t["expected"], t["coded"], t["short_qty"]), (18, 2, 1))
    snap = {l["id"]: l for l in SR._audit_snapshot_lines(lines)}
    eq("снимок по завершении: Absolut 15 (14 без кодов + 1 увиденная), не 1", snap["p1"]["actual"], 15)
    eq("снимок: Heineken 2 коробки остаются, не 0", snap["p31"]["actual"], 2)
    eq("снимок: разница = только кодовая, в единицах", (snap["p1"]["diff"], snap["p31"]["diff"]), (1, 0))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
