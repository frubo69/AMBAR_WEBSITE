"""Раздельная оплата (владелец, 9 окт 2026): «клиент заказал на 200 — 175
криптой, 25 наличными». У заказа раскладка pay_parts = {cash, crypto, transfer},
сумма частей равна итогу; через руки водителя идёт только наличная часть.

  • cash_math: наличных по заказу — наличная часть; крипта и перевод — свои;
    piles считает «на руках» только по наличным частям;
  • создание заказа оператором с раскладкой: payment_method cash, paid нет,
    pay_parts записана; раскладка одной частью — обычный заказ этим способом;
    сумма не сходится — 400 parts_sum;
  • POST /orders/{oid}/pay: меняет раскладку у заказа в работе и у
    доставленного, пишет pay_log, говорит водителю и владельцу; долг и «без
    оплаты» не трогает (409);
  • расчёт водителя у двери — от наличной части;
  • книга дня (_sales): части по своим столбцам, наличная — в наличные района;
  • подписи: «150 нал · 100 крипта».

    python3 tools/test_pay_parts.py
"""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import date                                          # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, cash_math as CM, operator_routes as opr, driver_routes as dr   # noqa: E402
import shift_recon as R, finance_routes as fin                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

TOLD, OWNER = [], []
async def _tell(name, text): TOLD.append((name, text))
async def _own(*a, **k): OWNER.append(a[1] if len(a) > 1 else "")
async def _none(*a, **k): return None
async def _dict(*a, **k): return {}

SPLIT = {"order_id": "S1", "status": "delivered", "driver": "Али", "office_id": "b2", "total": 200,
         "payment_method": "cash", "pay_parts": {"cash": 25, "crypto": 175, "transfer": 0},
         "items": [{"id": "p10", "name": "Red Label 1 ltr", "qty": 2, "price": 100, "line_total": 200}],
         "timestamp": "2026-10-09T09:00:00+00:00", "confirmed_at": "2026-10-09T09:00:00+00:00", "delivered_at": "2026-10-09T10:00:00+00:00"}
CASH = {**SPLIT, "order_id": "C1", "pay_parts": None, "total": 300}
CRYPTO = {**SPLIT, "order_id": "K1", "pay_parts": None, "payment_method": "crypto", "paid": True, "total": 400}


async def op(handler, oid=None, **body):
    r = make_mocked_request("POST", "/x", match_info={"oid": oid} if oid else {})
    r["op_id"] = 5; r["op_user"] = {"first_name": "Умар"}
    async def js(): return {"as": "Умар", **body}
    r.json = js
    h = handler
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["pay"]
    print("── арифметика ──────────────────────────────────────────────")
    eq("раскладка читается, одна часть — не раскладка", (CM.pay_parts(SPLIT), CM.pay_parts(CASH), CM.pay_parts({"pay_parts": {"cash": 0, "crypto": 400}})), ({"cash": 25, "crypto": 175, "transfer": 0}, None, None))
    eq("наличных по заказу: часть / весь / ноль", (CM.cash_due(SPLIT), CM.cash_due(CASH), CM.cash_due(CRYPTO)), (25.0, 300.0, 0.0))
    eq("криптой: часть / ноль / весь", (CM.crypto_part(SPLIT), CM.crypto_part(CASH), CM.crypto_part(CRYPTO)), (175.0, 0.0, 400.0))
    eq("деньги в руках — наличная часть", CM.order_money(SPLIT)["aed"], 25.0)
    eq("подпись", (CM.parts_label(SPLIT), CM.parts_label(CASH), R.pay_label(SPLIT), R.pay_label(CRYPTO)), ("25 нал · 175 крипта", "", "25 нал · 175 крипта", "крипта"))
    h = CM.piles([SPLIT, CASH, CRYPTO], [], 80)
    eq("на руках по приложению: 25 + 300, крипта мимо", (h["taken"], h["orders_cash"], h["in_hand"]), (325.0, 2, 325.0))
    eq("книга: части по столбцам", None, None)
    async def _between(*a, **k): return [SPLIT, CASH, CRYPTO]
    db.orders_between = _between
    s = (await fin._sales(["2026-10-09"]))["2026-10-09"]
    eq("книга дня: наличные 325, крипта 575, перевод 0, вал 900, наличные района", (s["cash"], s["crypto"], s["card"], s["gross"], s["cash_by"]), (325, 575, 0, 900, {"b2": 325}))

    print("── оператор: новый заказ с раскладкой ──────────────────────")
    SAVED = []
    async def _save(oid, order): SAVED.append(order)
    db.save_order = _save; db.update_order = _none
    opr._district = lambda did: {"id": "b2", "name": "Marina", "operator": "Умар", "drivers": ["Али"]}
    opr._drivers_of = lambda *a, **k: {"Али"}
    opr._released_now = lambda *a, **k: asyncio.sleep(0, False)
    opr._build_items = lambda raw, source="manual": ([{"id": "p10", "name": "Red Label 1 ltr", "qty": 2, "price": 100, "line_total": 200}], "")
    async def _total(items): return 200
    opr._pos_total = _total
    opr._tflag = lambda r: False
    opr._op_name = lambda u: "Умар"
    opr._biz_date = lambda dt: date(2026, 10, 9)
    opr._fanout_new = _dict; opr.notify_driver = _none; opr.tell_driver = _tell
    opr._staff_mod.stock_office = lambda d, o: "b2"
    db.shift_day_get = _none
    async def _odn(d): return "2026-10-09"
    db.order_day_now = _odn
    import owner_routes
    owner_routes.notify_owners_force = _own; owner_routes.notify_new_order = _none
    sys.modules.setdefault("api_server", types.SimpleNamespace(_FOUNDER_ID=0, _PREMIUM_IDS=set(), _WORLDWIDE_IDS=set()))
    код, тело = await op(opr.handle_create, district_id="b2", driver="Али", items=[{"id": "p10", "qty": 2}], payment_method="crypto", pay_parts={"cash": 25, "crypto": 175})
    o = SAVED[-1]
    eq("создан: cash как способ, paid нет, раскладка", (код, o["payment_method"], o.get("paid"), o["pay_parts"]), (200, "cash", None, {"cash": 25, "crypto": 175, "transfer": 0}))
    код, тело = await op(opr.handle_create, district_id="b2", driver="Али", items=[{"id": "p10", "qty": 2}], payment_method="cash", pay_parts={"cash": 25, "crypto": 100})
    eq("сумма частей не равна итогу — 400 parts_sum", (код, тело.get("error")), (400, "parts_sum"))
    код, тело = await op(opr.handle_create, district_id="b2", driver="Али", items=[{"id": "p10", "qty": 2}], payment_method="cash", pay_parts={"cash": 0, "crypto": 200})
    o = SAVED[-1]
    eq("одна часть — обычный криптозаказ без раскладки", (код, o["payment_method"], o.get("paid"), "pay_parts" in o), (200, "crypto", True, False))
    eq("подгонка раскладки под новый итог: наличные берут разницу", opr._fit_parts({"cash": 25, "crypto": 175, "transfer": 0}, 260), {"cash": 85, "crypto": 175, "transfer": 0})
    eq("подгонка: наличных не хватило — схлопнулось в крипту", opr._fit_parts({"cash": 25, "crypto": 175, "transfer": 0}, 150), {"_only": "crypto"})

    print("── оператор: поменять оплату у заказа ──────────────────────")
    await db._db.orders.insert_many([dict(CASH, status="approved"), dict(SPLIT), {**CASH, "order_id": "D1", "payment_method": "debt"}])
    async def _upd(oid, **kw): await db._db.orders.update_one({"order_id": oid}, {"$set": kw})
    db.update_order = _upd
    opr._refresh_cards = _none
    код, тело = await op(opr.handle_pay, oid="C1", payment_method="cash", pay_parts={"cash": 200, "crypto": 100})
    saved = await db._db.orders.find_one({"order_id": "C1"}, {"_id": 0})
    eq("заказ в работе: раскладка записана, способ cash, paid False, журнал", (код, saved["pay_parts"], saved["payment_method"], saved["paid"], saved["pay_log"][0]["to"]), (200, {"cash": 200, "crypto": 100, "transfer": 0}, "cash", False, "200 нал · 100 крипта"))
    eq("водителю сказано, сколько брать наличными", (TOLD[-1][0], "200" in TOLD[-1][1] and "Наличными взять" in TOLD[-1][1]), ("Али", True))
    eq("владельцу событие", len(OWNER) >= 1 and "Оплата заказа #C1" in OWNER[-1], True)
    eq("в ответе — сводка с раскладкой", (тело["order"]["pay_parts"], тело["order"]["cash_due"]), ({"cash": 200, "crypto": 100, "transfer": 0}, 200))
    код, тело = await op(opr.handle_pay, oid="S1", payment_method="crypto")
    saved = await db._db.orders.find_one({"order_id": "S1"}, {"_id": 0})
    eq("доставленный: раскладка снята, целиком криптой", (код, saved["pay_parts"], saved["payment_method"], saved["paid"]), (200, None, "crypto", True))
    код, тело = await op(opr.handle_pay, oid="D1", payment_method="cash")
    eq("долг не трогаем — 409", (код, тело.get("error")), (409, "fixed_payment"))
    код, тело = await op(opr.handle_pay, oid="C1", payment_method="cash", pay_parts={"cash": 100, "crypto": 100})
    eq("части не сходятся — 400", (код, тело.get("error")), (400, "parts_sum"))

    print("── водитель: расчёт у двери от наличной части ──────────────")
    await db._db.orders.insert_one({**SPLIT, "order_id": "S2", "status": "approved", "customer_id": 0})
    async def _get(oid): return await db._db.orders.find_one({"order_id": oid}, {"_id": 0})
    db.get_order = _get
    r = make_mocked_request("POST", "/x", match_info={"oid": "S2"}); r["driver"] = {"name": "Али"}
    async def js(): return {"taken": 30}
    r.json = js
    h = dr.handle_settle
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r); тело = json.loads(resp.text)
    eq("взял 30 при наличной части 25 — сдача 5, не 170", (resp.status, тело.get("diff")), (200, 5.0))
    v = dr._order_view({**SPLIT, "order_id": "S3"}) if hasattr(dr, "_order_view") else None
    if v is not None:
        eq("карточка водителя: раскладка и сколько брать", (v["pay_parts"], v["cash_due"], v["prepaid"]), ({"cash": 25, "crypto": 175, "transfer": 0}, 25, False))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
