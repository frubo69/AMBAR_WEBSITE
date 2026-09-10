"""Заказ у водителя на подменённой базе: состав с id и ценой, цены правки по
источнику (приложение / телефон), подарок остаётся, «Доставил» при открытой
правке, отзыв просьбы, курсы и оплата валютой, расчёт в валюте.
Запуск: python3 tools/test_driver_order.py"""
import asyncio, os, sys, json, logging, copy, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from aiohttp.test_utils import make_mocked_request
import db, driver_routes as dr, operator_routes as op, rates

CAT = [{"id": "gin", "name": "Джин", "cat": "Джин", "price": 95, "price_full": 100, "stock": True},
       {"id": "beer", "name": "Пиво", "cat": "Пиво", "price": 50, "price_full": 55, "price_12_full": 55,
        "price_24_full": 105, "stock": True}]
op._load_catalog = lambda: CAT
op._catalog_by_id = lambda: {p["id"]: p for p in CAT}
import api_server
api_server._load_catalog_by_id = lambda: {p["id"]: p for p in CAT}
ORDERS = {}
async def get_order(oid): return copy.deepcopy(ORDERS.get(oid))
async def update_order(oid, **kw): ORDERS[oid].update(kw)
async def add_debt(*a, **k): pass
async def panic_get(name): return False
db.get_order, db.update_order, db.add_debt, db.panic_get = get_order, update_order, add_debt, panic_get
async def _notify_operators(*a, **k): pass
dr._notify_operators = _notify_operators
async def get_rates(force=False):
    return {"rates": [{"code": "USD", "name": "Доллар", "aed": 3.6725, "cash_aed": 3.67},
                      {"code": "EUR", "name": "Евро", "aed": 4.0}], "main": ["USD", "EUR"], "fetched_iso": "t"}
rates.get_rates = get_rates
ME = {"name": "Али", "district": "a", "district_code": "B1"}

def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt: return h
        h = nxt
async def call(h, body=None, oid="o1", method="POST"):
    req = make_mocked_request(method, "/x", match_info={"oid": oid})
    req._read_bytes = json.dumps(body or {}).encode()
    req["driver"] = ME; req["tg"] = {"id": 1}
    r = await raw(h)(req)
    return r.status, json.loads(r.text)

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<62} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

def fresh(source="app"):
    ORDERS.clear()
    ORDERS["o1"] = {"order_id": "o1", "status": "approved", "driver": "Али", "source": source, "tip": 5,
                    "items": [{"id": "gin", "name": "Джин", "qty": 1, "price": 95, "line_total": 95},
                              {"id": "beer", "name": "Пиво ×12", "qty": 1, "pcs": 12, "price": 50, "line_total": 50, "gift": True}],
                    "total": 100, "address": "ул", "chat": [{"by": "operator", "name": "Оп", "text": "Ждите", "at": "t1"}],
                    "chat_seen_driver": ""}

async def main():
    print("— вид заказа")
    fresh()
    v = dr._order_view(ORDERS["o1"])
    eq("items с id, price, gift", [(i["id"], i["price"], i["gift"]) for i in v["items"]], [("gin", 95, False), ("beer", 50, True)])
    eq("source, tip, chat_last", (v["source"], v["tip"], v["chat_last"]["text"]), ("app", 5, "Ждите"))
    print("— правка состава: цены по источнику, подарок остаётся")
    st, r = await call(dr.handle_edit_request, {"items": [{"id": "gin", "qty": 2}]})
    eq("app: джин по 95, итог 95×2 + чай 5 = 195", (st, r["driver_req"]["items"][-1]["price"], r["driver_req"]["total"]), (200, 95, 195))
    eq("подарок в составе просьбы первым", r["driver_req"]["items"][0].get("gift"), True)
    eq("diff: только qty джина", [(d["kind"], d["name"]) for d in r["driver_req"]["diff"]], [("qty", "Джин")])
    fresh("manual")
    st, r = await call(dr.handle_edit_request, {"items": [{"id": "gin", "qty": 2}, {"id": "beer", "qty": 1, "pcs": 24}]})
    eq("manual: джин 100, пиво ×24 105, итог 305 без чая", (r["driver_req"]["items"][-2]["price"], r["driver_req"]["items"][-1]["price"], r["driver_req"]["total"]), (100, 105, 305))
    print("— «Доставил» при открытой правке — 409, после отзыва — можно")
    st, r = await call(dr.handle_delivered, {"settled": True})
    eq("409 req_open", (st, r.get("error")), (409, "req_open"))
    st, r = await call(dr.handle_req_withdraw, {})
    eq("отозвано", (st, ORDERS["o1"]["driver_req"]["status"]), (200, "withdrawn"))
    st, r = await call(dr.handle_delivered, {"settled": True})
    eq("доставка ушла оператору", (st, ORDERS["o1"]["driver_req"]["kind"]), (200, "delivered"))
    print("— курсы и оплата валютой")
    st, r = await call(dr.handle_rates, method="GET")
    eq("USD по наличному 3.67, EUR по рынку 4.0, основные первыми", [(x["code"], x["rate"], x["cash"]) for x in r["rates"]], [("USD", 3.67, True), ("EUR", 4.0, False)])
    fresh()
    st, r = await call(dr.handle_fx, {"code": "usd"})
    eq("USD: курс 3.67, сумма 100/3.67", (st, r["pay_fx"]["code"], r["pay_fx"]["rate"], r["pay_fx"]["amount"]), (200, "USD", 3.67, 27.25))
    ORDERS["o1"]["total"] = 200
    v = dr._order_view(ORDERS["o1"])
    eq("итог изменился — сумма в валюте пересчиталась, курс тот же", (v["pay_fx"]["amount"], v["pay_fx"]["rate"]), (54.5, 3.67))
    st, r = await call(dr.handle_fx, {"code": "XXX"})
    eq("нет курса → 503", (st, r.get("error")), (503, "no_rate"))
    st, r = await call(dr.handle_fx, {"code": ""})
    eq("снова дирхамы", (st, r["pay_fx"], ORDERS["o1"].get("pay_fx")), (200, None, None))
    ORDERS["o1"]["payment_method"] = "debt"
    st, r = await call(dr.handle_fx, {"code": "USD"})
    eq("в долг — валюта не нужна", (st, r.get("error")), (400, "not_cash"))
    print("— расчёт в валюте")
    fresh(); ORDERS["o1"]["total"] = 100
    await call(dr.handle_fx, {"code": "USD"})
    st, r = await call(dr.handle_settle, {"fx": {"code": "USD", "amount": 30}})
    eq("30 $ = 110.1 AED, сдача 10.1", (st, ORDERS["o1"]["settle"]["taken"], r["diff"], ORDERS["o1"]["settle"]["fx"]["amount"]), (200, 110.1, 10.1, 30))
    st, r = await call(dr.handle_settle, {"fx": {"code": "EUR", "amount": 30}})
    eq("другая валюта, чем у заказа → no_fx", (st, r.get("error")), (400, "no_fx"))
    print("— оператор: итог правки по источнику, сводка несёт валюту и расчёт")
    s2 = op._summary(ORDERS["o1"])
    eq("summary pay_fx/settle", (s2["pay_fx"]["code"], s2["settle"]["taken"]), ("USD", 110.1))
    tot = await op._order_total_for(ORDERS["o1"], [{"id": "gin", "qty": 2}])
    eq("app total 2×95+5", tot, 195.0)
    tot = await op._order_total_for({"source": "manual"}, [{"id": "gin", "qty": 2}])
    eq("manual total 2×100", tot, 200)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — заказ у водителя")
    sys.exit(1 if fails else 0)
asyncio.run(main())
