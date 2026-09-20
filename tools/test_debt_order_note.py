"""Заказ в долг — «Нам должны» водителю сам собой (владелец, 20 сен 2026: «когда
такой клиент заказывает у водителя, пусть автоматически заполняется „нам
должны“ за этот заказ и отправляется старшему»; «но есть же и ambar, который
просто без оплаты может брать — эти деньги вообще никуда учитывать не надо»).

mongomock + настоящие модули:
  • доставили заказ в долг — у водителя в дне запись «Нам должны» на сумму
    заказа, ждёт решения старшего, с номером заказа и именем клиента;
  • «без оплаты», тест-заказ и заказ без водителя — записи нет;
  • деньги от неё не двигаются: «сдать» у водителя, «к сдаче» у старшего и
    расход дня в «Финансах» те же, что без неё, — по такому заказу наличных
    никто не брал, а в выручку дня он уже посчитан;
  • старший её всё равно видит строкой (nocash) и в «ждут решения» она не
    висит — сбор выручки из-за неё не стоит;
  • отмена заказа или снятая доставка — запись уходит."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, expense_routes as exp, cash_math as cm, owner_routes as own, finance_routes as fin
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-20"
DRV = "Худоба"
ORDER = {"order_id": "AMB77", "driver": DRV, "office_id": "jvc", "status": "delivered",
         "total": 300, "payment_method": "debt", "customer_name": "Ахмед", "items": []}
CASH = {"order_id": "AMB78", "driver": DRV, "office_id": "jvc", "status": "delivered",
        "total": 500, "payment_method": "cash", "items": []}


async def extras():
    r = await db.get_driver_day(D, DRV)
    return list((r or {}).get("extras") or [])


async def main():
    db._db = AsyncMongoMockClient()["ambar_debt_note"]
    import bizday
    bizday.order_day = lambda o: D
    bizday.biz_day = lambda *a, **k: D

    print("── запись появляется сама ─────────────────────────────────────")
    eq("заказ в долг — записали", await exp.note_debt_order(ORDER, who="Оператор"), True)
    rows = await extras()
    e = rows[0] if rows else {}
    eq("одна строка: вид, сумма, ждёт решения, привязана к заказу",
       (len(rows), e.get("kind"), e.get("amount"), e.get("status"), e.get("order"), e.get("auto"), e.get("nocash")),
       (1, "owed_us", 300, "pending", "AMB77", True, True))
    eq("в комментарии — заказ и клиент", e.get("comment"), "#AMB77 · Ахмед · заказ в долг")

    print("── чего записывать не надо ────────────────────────────────────")
    eq("«без оплаты» — ничего", await exp.note_debt_order({**ORDER, "order_id": "AMB79", "payment_method": "free"}), False)
    eq("тест-заказ — ничего", await exp.note_debt_order({**ORDER, "order_id": "AMB80", "test": True}), False)
    eq("заказ без водителя — ничего", await exp.note_debt_order({**ORDER, "order_id": "AMB81", "driver": ""}), False)
    eq("наличный заказ — ничего", await exp.note_debt_order(CASH), False)
    eq("лишних строк не завелось", len(await extras()), 1)

    print("── деньги от неё не двигаются ─────────────────────────────────")
    rows = await extras()
    без = cm.piles([CASH], [], meal=80)
    с_ней = cm.piles([CASH, ORDER], rows, meal=80)
    eq("у водителя «сдать» то же самое", (с_ней["revenue"], с_ней["spent_sum"], с_ней["in_hand"]),
       (без["revenue"], без["spent_sum"], без["in_hand"]))

    # у старшего: строка видна, но ни в расход, ни в «ждут решения» не идёт
    async def orders_from(since):
        return {"1": {**CASH, "tip": 0}, "2": {**ORDER, "tip": 0}}
    async def checklist_get(day): return {}
    own.db.orders_from, own.db.checklist_get = orders_from, checklist_get
    own._order_day = lambda o: D
    staff.drivers = lambda: [{"name": DRV, "district": "jvc", "operator": "Умар"}]
    cr = await own.cash_round(D)
    j = next(x for x in cr["districts"] if x["id"] == "jvc")
    стр = [x for x in j["items"] if x["kind"] == "owed_us"]
    eq("у старшего строка видна и помечена «мимо наличных»",
       (len(стр), стр and стр[0]["amount"], стр and стр[0]["nocash"], стр and стр[0]["status"]),
       (1, 300, True, "pending"))
    eq("но в расход и в «ждут решения» не идёт: к сдаче 500 − питание 0",
       (j["spend"], j["spend_pending"], j["net"]), (0, 0, 500))

    # в «Финансах» расход дня тоже не растёт
    sp = await fin._spend([D])
    eq("расход дня в «Финансах» — только питание (отметки нет → 0)", sp[D]["spend"], 0)

    print("── отмена и снятая доставка ───────────────────────────────────")
    eq("сняли — строка ушла", await exp.drop_debt_order(ORDER), 1)
    eq("в дне пусто", len(await extras()), 0)
    eq("снимать нечего — и не падаем", await exp.drop_debt_order(ORDER), 0)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
