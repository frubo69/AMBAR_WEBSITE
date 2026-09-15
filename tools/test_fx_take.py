"""Оплата валютой на карточке: только USD/EUR/GBP/SAR по фиксированному курсу
приёма (владелец, 15 сен 2026); курс профиля не участвует. Без базы."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from aiohttp.test_utils import make_mocked_request
import driver_routes as dr
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
ORDER = {"order_id": "A1", "driver": "Худоба", "status": "approved", "total": 140, "payment_method": "cash", "items": []}
async def get_order(oid): return dict(ORDER)
async def update_order(oid, **kw): ORDER.update(kw)
dr.db.get_order = get_order; dr.db.update_order = update_order
def req(body):
    r = make_mocked_request("POST", "/x", match_info={"oid": "A1"}); r._read_bytes = json.dumps(body).encode()
    r["driver"] = {"name": "Худоба"}; return r
h = dr.handle_fx
while hasattr(h, "__wrapped__"): h = h.__wrapped__
async def main():
    eq("список приёма", [(r["code"], r["rate"]) for r in dr.fx_take_list()], [("USD", 3.5), ("EUR", 4.0), ("GBP", 4.5), ("SAR", 1.0)])
    for code, rate, amount in (("USD", 3.5, 40.0), ("EUR", 4.0, 35.0), ("GBP", 4.5, 31.11), ("SAR", 1.0, 140.0)):
        resp = await h(req({"code": code})); d = json.loads(resp.text)
        eq(f"{code}: курс и сумма к оплате", (resp.status, d["pay_fx"]["rate"], d["pay_fx"]["amount"]), (200, rate, amount))
    resp = await h(req({"code": "RUB"})); eq("рубль — нет в списке приёма", (resp.status, json.loads(resp.text).get("error")), (400, "no_rate"))
    resp = await h(req({"code": "usd"})); eq("код в нижнем регистре тоже берём", json.loads(resp.text)["pay_fx"]["code"], "USD")
    resp = await h(req({"code": ""})); eq("снова дирхамы", json.loads(resp.text)["pay_fx"], None)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
