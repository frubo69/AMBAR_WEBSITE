"""Аванс зарплаты водитель заводит сам (владелец, 20 сен 2026: «аванс зарплаты
он сможет заполнять сам в расходах, потому что берёт из тех денег, что у него
на руках… ушло на согласование старшему; если старший одобрил — у водителя
правильно пишет, сколько денег он отдаёт, а в его зарплате пишется, что в
следующем месяце он получает меньше на сумму аванса»).

mongomock + настоящие ручки водителя, старшего и расчёт зарплаты:
  • водитель отправляет «Аванс зарплаты» — запись ждёт решения, способ оплаты
    не спрашивается (деньги и так наличные из смены), в ведомости пока пусто;
  • старший согласовал — запись легла в ведомость авансом «часть зарплаты» с
    пометкой src=driver и БЕЗ расхода фонда: деньги не из сейфа, сейф просто
    получил меньше;
  • у водителя «сдать» уменьшилось ровно на аванс, у старшего «к сдаче» —
    столько же: обе стороны считают одинаково;
  • в зарплате за месяц аванс стоит в удержаниях, к выплате — меньше на него;
  • отказ или стирание записи убирает аванс из ведомости;
  • премию водитель тоже берёт вперёд — «Аванс премии» (владелец, 20 сен 2026:
    «аванс премия тоже — не тот, что KFC премия, просто премия»): считается так
    же, но в ведомости подписано, за что брали."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, driver_routes as dr, expense_routes as exp, owner_routes as own
import cash_math as cm, finance_pay as pay
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D, M = "2026-09-20", "2026-09"
DRV = "Худоба"
ORDER = {"order_id": "A", "driver": DRV, "office_id": "jvc", "status": "delivered",
         "total": 1000, "payment_method": "cash", "tip": 0, "items": []}
dr._biz_day = lambda *a, **k: D
exp._biz_day = lambda *a, **k: D
async def _quiet(*a, **k): pass
own.notify_owners = _quiet
import backdate; backdate.notify = _quiet
import pay_notify; pay_notify.tell_safe = _quiet


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        if "handler" in cl: h = cl["handler"]
        elif "fn" in cl: h = cl["fn"]
        else: return h


async def extras():
    r = await db.get_driver_day(D, DRV)
    return list((r or {}).get("extras") or [])


async def main():
    db._db = AsyncMongoMockClient()["ambar_drv_adv"]
    staff.drivers = lambda: [{"name": DRV, "district": "jvc", "operator": "Умар"}]
    staff.DRIVER_IDS = {}

    print("── водитель заводит аванс сам ─────────────────────────────────")
    req = make_mocked_request("POST", "/x")
    req["driver"] = {"name": DRV, "district": "jvc"}; req["tg"] = {"id": 7}
    req._read_bytes = json.dumps({"kind": "advance", "amount": 400, "comment": "на квартиру"}).encode()
    resp = await raw(dr.handle_expense_add)(req)
    e = (await extras())[0]
    eq("запись создана и ждёт решения", (resp.status, e["kind"], e["amount"], e.get("status")),
       (200, "advance", 400, "pending"))
    eq("способ оплаты не спрашивали", "pay" in e, False)
    eq("в ведомости пока пусто", len(await db.fin_pay_items_get()), 0)

    print("── старший согласовал ─────────────────────────────────────────")
    dreq = make_mocked_request("POST", f"/x/{e['id']}/approve",
                               match_info={"item_id": e["id"], "action": "approve"})
    dreq["owner_id"] = 1
    dreq._read_bytes = json.dumps({"day": D, "driver": DRV, "as": "Старший"}).encode()
    resp = await raw(exp.handle_extra_decide)(dreq)
    eq("решение принято", resp.status, 200)
    items = await db.fin_pay_items_get()
    it = items[0] if items else {}
    eq("в ведомости аванс: часть зарплаты, из наличных водителя, без расхода фонда",
       (len(items), it.get("kind"), it.get("amount"), it.get("mode"), it.get("src"), it.get("entry")),
       (1, "advance", 400, "part", "driver", ""))
    e = (await extras())[0]
    eq("запись знает свой аванс в ведомости", bool(e.get("pay_item")), True)

    print("── деньги: обе стороны считают одинаково ──────────────────────")
    без = cm.piles([ORDER], [])
    с_ним = cm.piles([ORDER], await extras())
    сдать = lambda h: (h["taken_aed"] or 0) + (h["got"] or 0) - (h["spent_sum"] or 0) - (h["bonus"] or 0)
    eq("у водителя «сдать» меньше ровно на аванс", (сдать(без), сдать(с_ним)), (1000.0, 600.0))
    async def orders_from(since): return {"A": dict(ORDER)}
    async def checklist_get(day): return {}
    own.db.orders_from, own.db.checklist_get = orders_from, checklist_get
    own._order_day = lambda o: D
    cr = await own.cash_round(D)
    j = next(x for x in cr["districts"] if x["id"] == "jvc")
    eq("у старшего к сдаче столько же", (j["spend"], j["net"]), (400, 600))

    print("── зарплата за месяц ──────────────────────────────────────────")
    p = {"name": DRV, "role": "driver"}
    eff = {"rate": 3000, "unit": "month", "cur": "AED"}
    m = pay.person_month(p, M, eff, 26, [dict(it)], [], usd=3.677)
    eq("аванс — в удержаниях, к выплате меньше на него",
       (m["accrued"], m["advance"], m["minus"], m["to_pay"]), (3000, 400, 400, 2600))

    print("── отказ и стирание ───────────────────────────────────────────")
    dreq = make_mocked_request("POST", f"/x/{e['id']}/reject",
                               match_info={"item_id": e["id"], "action": "reject"})
    dreq["owner_id"] = 1
    dreq._read_bytes = json.dumps({"day": D, "driver": DRV, "note": "не сейчас"}).encode()
    await raw(exp.handle_extra_decide)(dreq)
    eq("отказали — аванс из ведомости ушёл", len(await db.fin_pay_items_get()), 0)
    e = (await extras())[0]
    eq("и запись об этом знает", (e.get("status"), e.get("pay_item")), ("rejected", ""))

    print("── аванс премии — тем же путём ────────────────────────────────")
    req = make_mocked_request("POST", "/x")
    req["driver"] = {"name": DRV, "district": "jvc"}; req["tg"] = {"id": 7}
    req._read_bytes = json.dumps({"kind": "advance_bonus", "amount": 250, "comment": ""}).encode()
    resp = await raw(dr.handle_expense_add)(req)
    b = next(x for x in await extras() if x["kind"] == "advance_bonus")
    eq("запись премии создана и ждёт решения", (resp.status, b["amount"], b.get("status")),
       (200, 250, "pending"))
    dreq = make_mocked_request("POST", f"/x/{b['id']}/approve",
                               match_info={"item_id": b["id"], "action": "approve"})
    dreq["owner_id"] = 1
    dreq._read_bytes = json.dumps({"day": D, "driver": DRV, "as": "Старший"}).encode()
    await raw(exp.handle_extra_decide)(dreq)
    it2 = next(x for x in await db.fin_pay_items_get() if x.get("amount") == 250)
    eq("в ведомости — тот же аванс, но подписано «в счёт премии»",
       (it2.get("kind"), it2.get("mode"), it2.get("src"), it2.get("entry"), it2.get("note")),
       ("advance", "part", "driver", "", "В счёт премии · из наличных смены"))
    m2 = pay.person_month({"name": DRV, "role": "driver"}, M,
                          {"rate": 3000, "unit": "month", "cur": "AED", "bonus": 500},
                          26, [dict(it2)], [], usd=3.677)
    eq("премия 500 начислена, из неё 250 уже взято вперёд",
       (m2["bonus_month"], m2["advance"], m2["to_pay"]), (500, 250, 3250))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
