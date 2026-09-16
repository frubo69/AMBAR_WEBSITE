"""Карточка «Склад» — количественный остаток, а не только внесённое кодами;
«QR код не внесён» только там, где после пересчёта заводят коды (16 сен 2026).
Без базы."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import stock_routes as SR, stock_value as SV, qr_routes as QR, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
CAT = {"p1": {"id": "p1", "name": "Absolut", "price_full": 100, "price": 100},
       "p31": {"id": "p31", "name": "Heineken can", "price_full": 100, "price_24_full": 250, "price": 100}}
BASE = {"jvc": {"have": {"p1": 20, "p31": 3}, "have_exact": {"p1": 20, "p31": 3.5}}, "bbay": {"have": {"p1": 5}}, "silicon": {"have": {}}}
SR._catalog = lambda: CAT
SR.OFFICE_IDS = ["jvc", "bbay", "silicon"]; SR.OFFICE_CODES = {"jvc": "B1", "bbay": "B2", "silicon": "B3"}; SR.OFFICE_NAMES = dict(SR.OFFICE_CODES)
async def base(day): return BASE
SR._district_base = base
async def cost_map(): return {"p1": 60.0}
SV.cost_map = cost_map
CODES = {"jvc": {"p1": 4, "p31": 10}, "bbay": {}}
ADDED = {"jvc": 2}                      # после пересчёта коды заводили только в JVC
async def by_pd(): return CODES
async def last_count(oid, before_day=None): return {"counted_at": "2026-09-14T07:59:00+00:00"} if oid != "silicon" else None
async def added_since(since): return ADDED
db.qr_by_product_district_all = by_pd; db.get_last_stock_count = last_count; db.qr_added_since = added_since
async def main():
    v = await SV.build("2026-09-16")
    eq("количество в учётных единицах, пиво коробками (20 + 3,5 + 5), а не банками и не по кодам", v["totals"]["bottles"], 28.5)
    eq("по районам: JVC 23,5, BBay 5 — целое отдаётся целым", (v["by_district"]["jvc"]["bottles"], v["by_district"]["bbay"]["bottles"]), (23.5, 5))
    eq("полкоробки не теряется: строка пива 3,5 коробки", next(r["bottles"] for r in v["items"] if r["id"] == "p31"), 3.5)
    eq("прайс от количества: 25×100 + 3,5×250", v["totals"]["list"], 25 * 100 + 3.5 * 250)
    d = {}
    u, no = await QR.unscanned_by_district(None, d)
    eq("JVC: без кодов в единицах — 16 бутылок + (84 − 10 банок)/24 = 19.08", u.get("jvc"), 19.08)
    eq("BBay без единого кода — долг всё равно виден: 5", u.get("bbay"), 5)
    eq("Силикон без пересчёта и кодов → «не считали»", no, ["silicon"])
    eq("разбор по позициям — в бутылках, у пива в банках", (d["jvc"], d["bbay"]), ({"p1": 16, "p31": 74}, {"p1": 5}))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
