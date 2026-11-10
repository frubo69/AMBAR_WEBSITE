"""Чек-лист: заявка в магазин и поставка по шагам (владелец, 11 окт 2026).

  • не отправлена → «не отправлена», строки «Принятие поставки» нет;
  • отметка «отправлена» → «отправлена · ждём ответ магазина»;
  • магазин ответил, заявки не у всех районов → «у 3 из 5», не сделано;
  • у всех → зелёная, появляется «Принятие поставки» со своим состоянием;
  • «сегодня не заказываем» → обе строки зелёные «сегодня заявки нет»;
  • выручка за прошлые дни не получена → строка «Сбор выручки» красная
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
# По строке на каждую открытую приёмку и на каждую свободную заявку без ответа
# (владелец, 10 ноя 2026: «несколько активных приёмок/заявок — попасть в любую» → «по строке»).
C = OR.OFFICE_CODES
TWO = {**NONE, "open_list": [
    {"supply_id": "S1", "day": "2026-10-10", "kind": "main", "base": "", "free_order": False, "left": ["jvc"], "free": 1},
    {"supply_id": "X2", "day": "2026-10-11", "kind": "extra", "base": "Al Ain", "free_order": False, "left": ["bbay", "tecom"], "free": 0}],
    "wait_list": [{"supply_id": "F3", "day": "2026-10-11", "sent_at": None, "qty": 12},
                  {"supply_id": "F4", "day": "2026-10-11", "sent_at": "2026-10-11T05:05:00+00:00", "qty": 30}]}
rows = OR._order_rows(TWO, {}, NOW, D, OR.CHK_PLAN)
eq("две открытые приёмки — две строки, у каждой свой номер и свой срок; две свободные заявки — две строки",
   [(r["id"], r.get("sid"), r["state"], r["go"]) for r in rows],
   [("order_sent", None, "now", "order"), ("supply_in", "S1", "late", "supply"), ("supply_in", "X2", "now", "supply"),
    ("free_order", "F3", "now", "free"), ("free_order", "F4", "soon", "free")])
eq("подписи — каждая про свою", [r["s"] for r in rows[1:]],
   [f"Барракуда 10 октября · не закрыт {C['jvc']} · без водителя 1", f"Al Ain 11 октября · не закрыты {C['bbay']}, {C['tecom']}",
    "Барракуда 11 октября · файл не выслан", "Барракуда 11 октября · файл выслан 09:05 · ждём ответ магазина"])
eq("n — незакрытые районы каждой", [r["n"] for r in rows[1:3]], [1, 2])
eq("«сегодня не заказываем» их не гасит", [(r["id"], r.get("sid"), r["state"]) for r in OR._order_rows(TWO, {"order_skip": {"done": True}}, NOW, D, OR.CHK_PLAN)],
   [("order_sent", None, "done"), ("supply_in", "S1", "late"), ("supply_in", "X2", "now"), ("free_order", "F3", "now"), ("free_order", "F4", "soon")])
eq("приём закрыт — строка несёт номер поставки", [r.get("sid") for r in OR._order_rows({**CLOSED, "supply_id": "S9"}, {}, NOW, D, OR.CHK_PLAN)], [None, "S9"])
eq("выручка за прошлые дни не получена — красная сразу", OR._cash_hot({"carry_total": 4310, "all_done": False, "settled": False}), True)
eq("получена или «соберу завтра» — нет", (OR._cash_hot({"carry_total": 4310, "all_done": True}), OR._cash_hot({"carry_total": 4310, "settled": True}), OR._cash_hot({"carry_total": 0})), (False, False, False))
print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
