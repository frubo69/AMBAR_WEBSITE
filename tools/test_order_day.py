"""День заказа = смена, в которой его приняли (bizday.py), сквозь модули:
финансы, оператор, владелец, водитель. Случай владельца от 11 сен 2026: заказ
создан в 10:31, смену открыли в 12:01, приняли в 12:08 — это сегодняшняя
смена, а не вчерашняя. Запуск: python3 tools/test_order_day.py"""
import asyncio, os, sys, json, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from datetime import datetime, timedelta, timezone
import bizday, db

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<66} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

# заказ владельца: создан 10:31 Дубай (06:31 UTC), принят 12:08 (08:08 UTC)
O = dict(order_id="AMB0831827D", timestamp="2026-09-11T06:31:58.070213+00:00",
         confirmed_at="2026-09-11T08:08:33.391950+00:00", status="delivered", office_id="jvc", total=615)
print("— bizday")
eq("сутки: 11:59 Дубай — ещё вчера", bizday.biz_day(datetime(2026, 9, 11, 7, 59, tzinfo=timezone.utc)), "2026-09-10")
eq("сутки: 12:00 Дубай — уже сегодня", bizday.biz_day(datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)), "2026-09-11")
eq("день заказа — по принятию, не по созданию", bizday.order_day(O), "2026-09-11")
eq("без принятия — по созданию", bizday.order_day({"timestamp": O["timestamp"]}), "2026-09-10")
eq("подписанный день главнее", bizday.order_day({**O, "day": "2026-09-12"}), "2026-09-12")
eq("наивная строка без Z — UTC", bizday.day_of("2026-09-11T08:08:33"), "2026-09-11")
eq("окно дня: с запасом сутки назад, до полудня следующего", bizday.window_utc("2026-09-11", "2026-09-11"),
   ("2026-09-10T08:00:00", "2026-09-12T08:00:00"))
eq("in_days", (bizday.in_days(O, "2026-09-11", "2026-09-11"), bizday.in_days(O, "2026-09-10", "2026-09-10")), (True, False))

print("— db.order_day_now: смена закрыта → следующий день")
class _Coll:
    def __init__(self, ids): self.ids = ids
    async def find_one(self, q, proj=None): return {"_id": q["_id"]} if q["_id"] in self.ids else None
class _DB:
    def __init__(self, ids): self.shift_days = _Coll(ids)
today = bizday.biz_day()
db._db_or_none = lambda: _DB({f"{today}:jvc"})
async def _t1():
    eq("jvc закрыт за сегодня → завтра", await db.order_day_now("jvc"), bizday.next_day(today))
    eq("bbay не закрыт → сегодня", await db.order_day_now("bbay"), today)
    eq("без района → сегодня", await db.order_day_now(""), today)
asyncio.run(_t1())

print("— финансы: заказ попадает в 11 сентября")
import finance_routes as fr
ORDERS = [O, dict(order_id="x2", timestamp="2026-09-10T16:00:00", status="delivered", office_id="jvc", total=100)]
async def orders_between(a, b): return [o for o in ORDERS if a <= o["timestamp"] < b]
db.orders_between = orders_between
async def _t2():
    s = await fr._sales(["2026-09-10", "2026-09-11"])
    eq("10 сен: только вечерний заказ", (s["2026-09-10"]["orders"], s["2026-09-10"]["cash"]), (1, 100))
    eq("11 сен: утренний заказ, принятый после открытия", (s["2026-09-11"]["orders"], s["2026-09-11"]["cash_by"]), (1, {"jvc": 615}))
asyncio.run(_t2())

print("— оператор: очередь и закрытие смены считают по дню заказа")
import operator_routes as op
from datetime import date
eq("_biz_date_of — 11 сентября", op._biz_date_of(O), date(2026, 9, 11))
eq("_biz_date_of без дат — None", op._biz_date_of({}), None)

print("— владелец: окно суток по дню заказа, порядок по созданию")
import owner_routes as ow
start = bizday.day_start("2026-09-11"); end = start + timedelta(days=1)
allo = {"a": O, "b": ORDERS[1], "c": dict(order_id="c", timestamp="2026-09-11T09:00:00", status="delivered", total=5)}
pairs = ow._orders_in_window(allo, start, end)
eq("в окне 11 сен: утренний заказ и дневной", sorted(o["order_id"] for _, o in pairs), ["AMB0831827D", "c"])
pairs = ow._orders_in_window(allo, start - timedelta(days=1), start)
eq("в окне 10 сен: только вечерний", [o["order_id"] for _, o in pairs], ["x2"])
eq("столбики 7 дней: заказ в последнем столбике", ow._last_7_days(allo, start)[6], 620)
print()
print("FAILED:", fails) if fails else print("ALL OK — день заказа считается по смене")
sys.exit(1 if fails else 0)
