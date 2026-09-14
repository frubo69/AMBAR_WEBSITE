"""Итоги смены перед закрытием (владелец, 14 сен 2026). Без базы."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import driver_routes as dr
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D = "2026-09-14"
ORDERS = [
  {"order_id": "1", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B1", "total": 200, "tip": 20, "payment_method": "cash"},
  {"order_id": "2", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B1", "total": 150, "tip": 0, "payment_method": "cash", "settle": {"taken": 160}},
  {"order_id": "3", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B3", "total": 300, "tip": 30, "payment_method": "card", "paid": True},
  {"order_id": "4", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B1", "total": 400, "tip": 0, "payment_method": "crypto"},
  {"order_id": "5", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B1", "total": 100, "tip": 0, "payment_method": "debt"},
  {"order_id": "6", "driver": "Худоба", "status": "delivered", "day": D, "office_id": "B1", "total": 95, "tip": 10, "payment_method": "free"},
  {"order_id": "7", "driver": "Фарух",  "status": "delivered", "day": D, "office_id": "B1", "total": 500, "tip": 50, "payment_method": "cash"},
  {"order_id": "8", "driver": "Худоба", "status": "delivered", "day": "2026-09-13", "office_id": "B1", "total": 500, "tip": 50, "payment_method": "cash"},
  {"order_id": "9", "driver": "Худоба", "status": "approved",  "day": D, "office_id": "B1", "total": 500, "tip": 50, "payment_method": "cash"},
]
DAY = {"shift_open_at": None, "extras": [
  {"id": "a", "kind": "fuel", "amount": 150, "status": "approved"},
  {"id": "b", "kind": "parking", "amount": 36, "status": "pending"},
  {"id": "c", "kind": "we_got", "amount": 30, "status": "approved"},
  {"id": "d", "kind": "other", "amount": 500, "status": "rejected"},
]}
async def get_driver_day(day, name): return DAY
async def get_orders_in_range(a, b, *args, **kw): return ORDERS
async def writeoff_list(**kw): return [{"qty": 2, "state": "ok"}, {"qty": 1, "state": "no"}]
dr.db.get_driver_day = get_driver_day
dr.db.get_orders_in_range = get_orders_in_range
dr.db.writeoff_list = writeoff_list
dr.staff.base_operator = lambda d: {"B1": "Али", "B3": "Фарух"}.get(d, "")
dr._biz_day = lambda: D

async def main():
    m = await dr._shift_summary({"name": "Худоба", "district": "B1"})
    eq("взято наличными (200 + 160 по расчёту)", m["cash_taken"], 360)
    eq("расходы без отклонённых, приход отдельно", (m["spent"], m["got"]), (186, 30))
    eq("на руках = 360 − 186 + 30", m["on_hand"], 204)
    eq("чай: всего / наличными / безнал (без «без оплаты»)", (m["tips"], m["tips_cash"], m["tips_other"]), (50, 20, 30))
    eq("чай по операторам", m["tips_by"], [{"who": "Фарух", "aed": 30}, {"who": "Али", "aed": 20}])
    eq("заказов и сумма (без «без оплаты»)", (m["orders"], m["gross"]), (5, 1150))
    eq("по способам", {k: (v["n"], v["aed"]) for k, v in m["pay"].items()},
       {"cash": (2, 350), "app": (1, 300), "crypto": (1, 400), "debt": (1, 100), "free": (1, 95)})
    eq("расходы по видам", [(x["id"], x["aed"], x["plus"]) for x in m["expenses"]],
       [("fuel", 150, False), ("parking", 36, False), ("we_got", 30, True)])
    eq("на согласовании", (m["exp_n"], m["exp_pending"]), (3, 1))
    eq("списания (отклонённое не в счёт)", (m["writeoffs"], m["writeoff_qty"]), (1, 2))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
