"""Оплата валютой на карточке: только USD/EUR/GBP/SAR по нашему курсу приёма
(владелец, 15 сен 2026); курс обменника не участвует. С 19 сен 2026 курс
приёма живёт в базе и меняется в STAR (владелец: «добавь наш внутренний курс,
чтобы мы могли его посмотреть и менять»).

mongomock + настоящие ручки: водитель (handle_fx, список fx_take у заказов) и
STAR (GET /api/owner/rates → take, POST /api/owner/rates/take):
  • по умолчанию — прежние 3.5 / 4.0 / 4.5 / 1.0;
  • поменяли в STAR — водитель сразу называет новую сумму; уже выбранный на
    заказе курс не меняется; журнал «кто, с какого на какое»;
  • чужая валюта, ноль, больше 50, не число — отказ, курс прежний."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import make_mocked_request, TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, owner_auth
import driver_routes as dr
import rates as R
import fx_take

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
ORDER = {"order_id": "A1", "driver": "Худоба", "status": "approved", "total": 140, "payment_method": "cash", "items": []}
async def get_order(oid): return dict(ORDER)
async def update_order(oid, **kw): ORDER.update(kw)
dr.db.get_order = get_order; dr.db.update_order = update_order
def req(body):
    r = make_mocked_request("POST", "/x", match_info={"oid": "A1"}); r._read_bytes = json.dumps(body).encode()
    r["driver"] = {"name": "Худоба"}; return r
h = dr.handle_fx
while hasattr(h, "__wrapped__"): h = h.__wrapped__
# Курс обменника в этом тесте не нужен — ответ /rates без сети.
async def _no_rates(force=False): return {"rates": [], "ok": True}
R.get_rates = _no_rates


async def main():
    db._db = AsyncMongoMockClient()["fx_take"]
    fx_take.drop()
    print("── по умолчанию ─────────────────────────────────────────────")
    eq("список приёма", [(r["code"], r["rate"]) for r in await dr.fx_take_list()],
       [("USD", 3.5), ("EUR", 4.0), ("GBP", 4.5), ("SAR", 1.0)])
    for code, rate, amount in (("USD", 3.5, 40.0), ("EUR", 4.0, 35.0), ("GBP", 4.5, 31.11), ("SAR", 1.0, 140.0)):
        resp = await h(req({"code": code})); d = json.loads(resp.text)
        eq(f"{code}: курс и сумма к оплате", (resp.status, d["pay_fx"]["rate"], d["pay_fx"]["amount"]), (200, rate, amount))
    resp = await h(req({"code": "RUB"})); eq("рубль — нет в списке приёма", (resp.status, json.loads(resp.text).get("error")), (400, "no_rate"))
    resp = await h(req({"code": "usd"})); eq("код в нижнем регистре тоже берём", json.loads(resp.text)["pay_fx"]["code"], "USD")
    frozen = dict(ORDER["pay_fx"])
    resp = await h(req({"code": ""})); eq("снова дирхамы", json.loads(resp.text)["pay_fx"], None)

    print("── STAR: смотреть и менять ──────────────────────────────────")
    app = web.Application(); R.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def own(method, path, body=None):
            r = await cl.request(method, path, json=body, headers={"Authorization": "tma 1"})
            return r.status, await r.json()
        st, d = await own("GET", "/api/owner/rates")
        eq("в разделе курсов — наш курс для клиентов", [(t["code"], t["rate"]) for t in d.get("take", [])],
           [("USD", 3.5), ("EUR", 4.0), ("GBP", 4.5), ("SAR", 1.0)])
        st, d = await own("POST", "/api/owner/rates/take", {"code": "usd", "rate": "3,55", "as": "Владелец"})
        eq("поменяли доллар на 3.55 (запятая тоже)", (st, d["row"]["rate"], d["row"]["was"], d["row"]["by_name"]),
           (200, 3.55, 3.5, "Владелец"))
        for body, why in (({"code": "RUB", "rate": 0.05}, "рубля нет в списке приёма"), ({"code": "EUR", "rate": 0}, "ноль"),
                          ({"code": "EUR", "rate": 60}, "больше 50 — опечатка"), ({"code": "EUR", "rate": "abc"}, "не число")):
            st, d = await own("POST", "/api/owner/rates/take", body)
            eq(f"отказ: {why}", st, 400)
        st, d = await own("GET", "/api/owner/rates")
        eq("после отказов евро прежний, доллар новый", {t["code"]: t["rate"] for t in d["take"]}["EUR"], 4.0)
    log = await db._db.fx_take_log.find({}, {"_id": 0, "code": 1, "was": 1, "rate": 1, "by_name": 1}).to_list(10)
    eq("журнал: кто и с какого на какое", log, [{"code": "USD", "was": 3.5, "rate": 3.55, "by_name": "Владелец"}])

    print("── водитель после правки ────────────────────────────────────")
    eq("в списке у водителя — новый курс", [(r["code"], r["rate"]) for r in await dr.fx_take_list()][0], ("USD", 3.55))
    resp = await h(req({"code": "USD"})); d = json.loads(resp.text)
    eq("новый выбор — по 3.55: 140 AED = $39.44", (d["pay_fx"]["rate"], d["pay_fx"]["amount"]), (3.55, 39.44))
    eq("выбранный раньше курс на заказе был заморожен (3.5)", frozen["rate"], 3.5)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
