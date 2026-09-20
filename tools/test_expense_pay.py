"""Как платил водитель — наличными или безналом (владелец, 18 сен 2026: «когда
водитель заполняет расход, заставь его где-то выбрать: он заплатил наличными
или безналичная оплата была»). mongomock + настоящий обработчик:
  • новое приложение шлёт поле pay всегда — без ответа (или с чепухой) запись
    не принимается: 400 pay_required; старое приложение поле не знает — его
    запись принимается и считается наличной, как раньше;
  • не спрашиваем у охраны (бутылка или наличные), «нам должны» (не платёж) и
    с 20 сен 2026 у возвратов и долгов — «мы вернули», «нам вернули», «мы
    должны»: это всегда наличные из рук в руки (владелец: «отсюда убери
    способы оплаты»);
  • правка записи меняет ответ, пересъёмка чека без поля — оставляет прежний;
  • виды расходов отдают флаг pay — что спрашивать в приложении;
  • итоги смены: безнал в «на руках» не входит (spent_card / got_card);
  • сбор выручки: «к сдаче» = наличные − расход наличными, безнал отдельно.
(Выручка дня в «Финансах» — в tools/test_finance_routes.py.)"""
import asyncio, base64, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, driver_routes as dr, owner_routes as own
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"
dr._biz_day = lambda *a, **k: D
async def _quiet(*a, **k): pass
import owner_routes
owner_routes.notify_owners = _quiet
PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"\x00" * 3000).decode()
ME = {"name": "Худоба", "district": "jvc", "district_code": "B1"}


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def post(body):
    req = make_mocked_request("POST", "/x")
    req._read_bytes = json.dumps(body).encode()
    req["driver"] = ME; req["tg"] = {"id": 1}
    r = await raw(dr.handle_expense_add)(req)
    return r.status, json.loads(r.text)


async def items():
    return {x["id"]: x for x in ((await db.get_driver_day(D, "Худоба")) or {}).get("extras") or []}


async def main():
    db._db = AsyncMongoMockClient()["ambar_pay"]

    print("── приложение обязано спросить ────────────────────────────────")
    st, r = await post({"kind": "kfc", "amount": 30, "comment": "Али", "pay": ""})
    eq("поле есть, ответа нет — 400 pay_required", (st, r.get("error")), (400, "pay_required"))
    st, r = await post({"kind": "kfc", "amount": 30, "comment": "Али", "pay": "bank"})
    eq("чепуха вместо ответа — тоже 400", (st, r.get("error")), (400, "pay_required"))
    st, r = await post({"kind": "parking", "amount": 20, "comment": "Marina", "pay": None})
    eq("парковка без ответа — 400 раньше, чем про чек", (st, r.get("error")), (400, "pay_required"))
    st, r = await post({"kind": "kfc", "amount": 30, "comment": "Али", "pay": "card"})
    eq("безналом — принято", (st, r["item"].get("pay")), (200, "card"))
    kfc = r["item"]["id"]
    st, r = await post({"kind": "parking", "amount": 20, "comment": "Marina", "pay": "cash", "photo": PHOTO})
    eq("парковка наличными с чеком — принято", (st, r["item"].get("pay")), (200, "cash"))
    st, r = await post({"kind": "we_got", "amount": 50, "comment": "Ахмед", "pay": "card"})
    eq("«нам вернули»: способ не спрашиваем — запись наличная, даже если прислали card",
       (st, "pay" in r["item"]), (200, False))

    print("── старое приложение и виды без вопроса ───────────────────────")
    st, r = await post({"kind": "fuel", "amount": 100, "comment": "", "photo": PHOTO})
    eq("старое приложение без поля — принято, ответа нет (= наличные)", (st, "pay" in r["item"]), (200, False))
    fuel = r["item"]["id"]
    st, r = await post({"kind": "guard", "amount": 40, "comment": "охранник", "pay": "card"})
    eq("охрана: не спрашиваем, ответ не пишем", (st, "pay" in r["item"]), (200, False))
    st, r = await post({"kind": "owed_us", "amount": 25, "comment": "Карим", "pay": ""})
    eq("«нам должны»: не платёж — пустой ответ не мешает", (st, "pay" in r["item"]), (200, False))

    print("── правка записи ──────────────────────────────────────────────")
    st, r = await post({"kind": "fuel", "amount": 120, "comment": "", "pay": "card"})
    eq("бензин поправили — безналом", (st, (await items())[fuel].get("pay"), (await items())[fuel]["amount"]),
       (200, "card", 120))
    st, r = await post({"kind": "fuel", "amount": 120, "comment": "", "photo": PHOTO})
    eq("пересняли чек без поля — ответ прежний", (st, (await items())[fuel].get("pay")), (200, "card"))
    st, r = await post({"kind": "kfc", "id": kfc, "amount": 35, "comment": "Али", "pay": "cash"})
    eq("запись «Прочего» по id — ответ сменился", (st, (await items())[kfc].get("pay")), (200, "cash"))
    st, r = await post({"kind": "kfc", "id": kfc, "amount": 35, "comment": "Али", "pay": "x"})
    eq("и правка без внятного ответа — 400", (st, r.get("error")), (400, "pay_required"))

    print("── виды для приложения ────────────────────────────────────────")
    req = make_mocked_request("GET", "/x"); req["driver"] = ME; req["tg"] = {"id": 1}
    v = json.loads((await raw(dr.handle_expenses)(req)).text)
    eq("pay: спрашивать у всех, кроме охраны, «нам должны», бонуса, возвратов, долгов и аванса",
       sorted(k["id"] for k in v["kinds"] if not k["pay"]),
       ["advance", "advance_bonus", "guard", "owed_us", "upsell", "we_gave", "we_got", "we_owe"])

    print("── итоги смены: на руках только наличное ──────────────────────")
    # в дне: kfc 35 наличными, парковка 20 наличными, приход 50 наличными
    # («нам вернули» — всегда наличные), бензин 120 безналом, охрана 40
    # (наличные), «нам должны» 25
    async def get_orders_in_range(a, b, *x, **k):
        return [{"order_id": "1", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "jvc",
                 "total": 500, "tip": 0, "payment_method": "cash"}]
    async def writeoff_list(**k): return []
    dr.db.get_orders_in_range, dr.db.writeoff_list = get_orders_in_range, writeoff_list
    dr.bizday.order_day = lambda o: D
    m = await dr._shift_summary(ME)
    eq("расход наличными 35 + 20 + 40 + 25, приход наличными 50", (m["spent"], m["got"]), (120, 50))
    eq("безналом: расход 120, прихода безналом больше не бывает", (m["spent_card"], m["got_card"]), (120, 0))
    eq("на руках = 500 − 120 + 50", m["on_hand"], 430)

    print("── сбор выручки по районам ────────────────────────────────────")
    for x in (await items()).values():
        await db._db.driver_days.update_one({"day": D, "driver": "Худоба", "extras.id": x["id"]},
                                            {"$set": {"extras.$.status": "approved"}})
    async def orders_from(since): return {"1": {"order_id": "1", "status": "delivered", "office_id": "jvc",
                                                "total": 500, "tip": 0, "payment_method": "cash"}}
    async def checklist_get(day): return {}
    own.db.orders_from, own.db.checklist_get = orders_from, checklist_get
    own._order_day = lambda o: D
    staff.drivers = lambda: [{"name": "Худоба", "district": "jvc", "operator": "Умар"}]
    cr = await own.cash_round(D)
    j = next(x for x in cr["districts"] if x["id"] == "jvc")
    # расход наличными 35 + 20 + 40 + 25 минус приход наличными 50
    eq("к сдаче = 500 − (расход наличными − приход наличными), без питания: отметки нет",
       (j["spend"], j["net"]), (70, 430))
    eq("безналом отдельно: только бензин 120", j["spend_card"], 120)
    eq("строки знают, как платили", sorted((x["kind"], x["pay"]) for x in j["items"]),
       [("fuel", "card"), ("guard", ""), ("kfc", "cash"), ("owed_us", ""), ("parking", "cash"), ("we_got", "")])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
