"""Две пачки водителя — на примерах владельца (19 сен 2026):
  • «заработал 1000, заправился на 100, помылся на 50, и ещё 50 дирхам — чай
    операторов с заказов → сдаёт две пачки: 850 AED и 50 AED чая» (чай сидит
    в цене бутылки: взято 1050, из них 50 — чай);
  • «если у него наличкой 1000, а он заправился на 200 дирхам картой — он
    всё равно сдаёт 1000»;
  • «если есть валюта — сколько в валюте, сколько в дирхамах и сколько чай»;
  • «не забудь про бонус водителя, 5% с допродажи» — и питание: остаются у
    водителя, из выручки вычитаются.
И то же — у старшего в «Сборе выручки»: выручка = наличные − чай − расход,
чай отдельно, валюта строкой. Без базы, кроме «Сбора выручки» (mongomock)."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
import cash_math as cm

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

TEA = next(iter(cm.tea_rates()))            # «чайная» бутылка из каталога — 50 AED
def order(total, **kw):
    return {"total": total, "payment_method": "cash", "status": "delivered", **kw}
def exp(kind, amount, **kw):
    return {"kind": kind, "amount": amount, "status": "approved", **kw}


print("── пример владельца: 1000, бензин 100, мойка 50, чай 50 ─────────")
h = cm.piles([order(1050, items=[{"id": TEA, "qty": 1}])], [exp("fuel", 100), exp("wash", 50)])
eq("выручка 850 и чай 50 — две пачки", (h["revenue"], h["tea"]), (850, 50))
eq("всего в руках 900", h["in_hand"], 900)

print("── заправился картой — сдаёт всю наличку ──────────────────────")
h = cm.piles([order(1000)], [exp("fuel", 200, pay="card")])
eq("наличные 1000 — сдаёт 1000", (h["revenue"], h["card_spent"]), (1000, 200))
h = cm.piles([order(1000)], [exp("fuel", 200, pay="cash")])
eq("а наличными — сдаёт 800", h["revenue"], 800)
h = cm.piles([order(1000)], [exp("fuel", 200)])
eq("старая запись без ответа — как наличные", h["revenue"], 800)

print("── валюта: сколько в валюте, сколько в дирхамах, сколько чай ──")
orders = [order(1050, items=[{"id": TEA, "qty": 1}]),                               # дирхамами, с чаем
          order(367, pay_fx={"code": "USD", "rate": 3.67}),                          # ровно $100
          order(400, pay_fx={"code": "EUR", "rate": 4.0},
                settle={"taken": 420.0, "diff": 20.0, "fx": {"code": "EUR", "amount": 105.0, "rate": 4.0}})]
h = cm.piles(orders, [exp("fuel", 100), exp("wash", 50)])
eq("валютой: $100 и €105 (по курсу 367 и 420)",
   [(x["code"], x["sym"], x["amount"], x["aed"]) for x in h["fx"]], [("EUR", "€", 105.0, 420.0), ("USD", "$", 100.0, 367.0)])
eq("дирхамами в выручке: 1050 − чай 50 − 150 = 850", h["revenue_aed"], 850)
eq("выручка всего по курсу: 850 + 367 + 420", h["revenue"], 1637)
eq("чай 50 — отдельно", h["tea"], 50)

print("── бонус за допродажу и питание — остаются у водителя ─────────")
h = cm.piles([order(1050, items=[{"id": TEA, "qty": 1}])],
             [exp("fuel", 100), exp("wash", 50), exp("upsell", 15)], meal=80)
eq("выручка = 1050 − 50 − 150 − 80 − 15", h["revenue"], 755)
eq("своё: питание 80 + бонус 15", (h["meal"], h["bonus"], h["keep"]), (80, 15, 95))
eq("в руках: выручка + чай + своё = 1050 − 150", h["in_hand"], 900)

print("── оплаченное онлайн, в долг, без оплаты ──────────────────────")
h = cm.piles([order(500), {"total": 300, "payment_method": "crypto", "paid": True, "items": [{"id": TEA, "qty": 2}]},
              {"total": 200, "payment_method": "debt"}, {"total": 90, "payment_method": "free"}], [])
eq("наличными только 500; чай — со всех (как в «Обзоре»): 100, из них онлайн 100",
   (h["taken"], h["tea"], h["tea_other"], h["revenue"]), (500, 100, 100, 400))

print("── приход, отклонённое, на согласовании ───────────────────────")
h = cm.piles([order(1000)], [exp("we_got", 30), exp("we_owe", 20, pay="card"), exp("kfc", 40, status="pending")])
eq("приход наличными +30, на карту — мимо; ждущее решения вычтено и помечено",
   (h["got"], h["card_got"], h["revenue"], h["pending"]), (30, 20, 990, 1))


async def cash_round():
    print("── «Сбор выручки» у старшего — та же арифметика ───────────────")
    from mongomock_motor import AsyncMongoMockClient
    import db, owner_routes as own, config_staff as staff
    db._db = AsyncMongoMockClient()["ambar_piles"]
    D = "2026-09-19"
    orders = {"1": order(1050, order_id="1", office_id="jvc", items=[{"id": TEA, "qty": 1}]),
              "2": order(367, order_id="2", office_id="jvc", pay_fx={"code": "USD", "rate": 3.67}),
              "3": {"order_id": "3", "office_id": "jvc", "status": "delivered", "total": 90, "payment_method": "free"}}
    async def orders_from(since): return orders
    async def checklist_get(day): return {}
    own.db.orders_from, own.db.checklist_get = orders_from, checklist_get
    own._order_day = lambda o: D
    staff.drivers = lambda: [{"name": "Худоба", "district": "jvc", "operator": "Умар"}]
    await db.add_driver_expense(D, "Худоба", {"id": "a", "kind": "fuel", "amount": 100, "status": "approved"})
    await db.add_driver_expense(D, "Худоба", {"id": "b", "kind": "wash", "amount": 50, "status": "approved"})
    await db.add_driver_expense(D, "Худоба", {"id": "c", "kind": "fuel", "amount": 200, "status": "approved", "pay": "card"})
    j = next(x for x in (await own.cash_round(D))["districts"] if x["id"] == "jvc")
    eq("наличные 1050 + $100 (367); «без оплаты» не наличные", j["cash"], 1417)
    eq("выручка = 1417 − чай 50 − расход 150 (картой 200 — мимо)", (j["net"], j["tips"], j["spend"], j["spend_card"]),
       (1217, 50, 150, 200))
    eq("валюта строкой", [(f["code"], f["amount"]) for f in j["fx"]], [("USD", 100.0)])


asyncio.run(cash_round())
print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
