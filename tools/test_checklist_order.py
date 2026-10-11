"""Чек-лист: заявка в магазин и поставка по шагам (владелец, 11 окт 2026).

  • не отправлена → «не отправлена», строки «Поставка принята» нет;
  • отметка «отправлена» → «отправлена · ждём ответ магазина»;
  • магазин ответил, заявки не у всех районов → «у 3 из 5», не сделано;
  • у всех → зелёная, появляется «Поставка принята» со своим состоянием;
  • «сегодня не заказываем» → обе строки зелёные «сегодня заявки нет»;
  • выручка за прошлые дни не получена → строка «Выручка получена» красная
    сразу (_cash_hot), получена или «соберу завтра» — нет.

    python3 tools/test_checklist_order.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime                                        # noqa: E402
import owner_routes as OR                                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = "2026-10-11"
NOW = datetime(2026, 10, 11, 14, 0, tzinfo=OR.DUBAI_TZ)            # 14:00 — окно заявки открыто
NONE = {"exists": False, "free": 0, "gap": 0, "done": False, "total": 0, "planned": 0}
PART = {"exists": True, "free": 1, "gap": 0, "done": False, "total": 3, "planned": 5}
ALL = {"exists": True, "free": 2, "gap": 0, "done": False, "total": 5, "planned": 5}
CLOSED = {**ALL, "free": 0, "done": True}
view = lambda rows: [(r["id"], r["s"], r["state"]) for r in rows]

eq("не отправлена: одна строка, пора", view(OR._order_rows(NONE, {}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "не отправлена", "now")])
eq("отправлена — ждём ответ", view(OR._order_rows(NONE, {"order_sent": {"done": True}}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "отправлена · ждём ответ магазина", "now")])
eq("ответил, заявки не у всех — не сделано, поставки ещё нет", view(OR._order_rows(PART, {"order_sent": {"done": True}}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "магазин ответил · заявки у 3 из 5 районов", "now")])
eq("ответил, у всех — зелёная и появилась поставка", view(OR._order_rows(ALL, {}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "магазин ответил · заявки у всех районов", "done"), ("supply_in", "2 районов без водителя", "now")])
eq("приём закрыт — обе зелёные", view(OR._order_rows(CLOSED, {}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "магазин ответил · заявки у всех районов", "done"), ("supply_in", "приём закрыт", "done")])
eq("сегодня не заказываем — обе зелёные с подписью", view(OR._order_rows(NONE, {"order_skip": {"done": True}}, NOW, D, OR.CHK_PLAN)),
   [("order_sent", "сегодня заявки нет", "done"), ("supply_in", "сегодня заявки нет", "done")])
LATE = datetime(2026, 10, 11, 16, 0, tzinfo=OR.DUBAI_TZ)
eq("после 15:00 без ответа — красная", view(OR._order_rows(NONE, {}, LATE, D, OR.CHK_PLAN))[0][2], "late")
eq("выручка за прошлые дни не получена — красная сразу", OR._cash_hot({"carry_total": 4310, "all_done": False, "settled": False}), True)
eq("получена или «соберу завтра» — нет", (OR._cash_hot({"carry_total": 4310, "all_done": True}), OR._cash_hot({"carry_total": 4310, "settled": True}), OR._cash_hot({"carry_total": 0})), (False, False, False))
print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
