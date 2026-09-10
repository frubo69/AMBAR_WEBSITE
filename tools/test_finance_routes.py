"""Книга учёта на сервере с подменённой базой: заказы, расходы водителей,
поставки и ручные записи — и проверка, что build() собирает из них те же
поля, что compute() ждёт на входе. Запуск: python3 tools/test_finance_routes.py"""
import asyncio, os, sys, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
import db, finance_routes as fr, stock_value, stock_routes, config_staff
from aiohttp.test_utils import make_mocked_request

M = "2026-09"
ORDERS = [
    # 3 сен 20:00 Дубай = 16:00 UTC → день 2026-09-03, наличные 300 (+чай 20 внутри)
    dict(order_id="o1", timestamp="2026-09-03T16:00:00", status="delivered", total=320, tip=20, office_id="jbr"),
    # 4 сен 02:30 Дубай = 3 сен 22:30 UTC → ещё 2026-09-03, крипта 500
    dict(order_id="o2", timestamp="2026-09-03T22:30:00", status="delivered", total=500, tip=0, payment_method="crypto", paid=True, office_id="jbr"),
    # 4 сен 12:01 Дубай = 08:01 UTC → 2026-09-04, наличные 200, в долг 150, перевод 100
    dict(order_id="o3", timestamp="2026-09-04T08:01:00", status="delivered", total=200, tip=0, office_id="marina"),
    dict(order_id="o4", timestamp="2026-09-04T09:00:00", status="delivered", total=150, tip=0, payment_method="debt", office_id="marina"),
    dict(order_id="o5", timestamp="2026-09-04T10:00:00", status="delivered", total=100, tip=0, payment_method="transfer", office_id="marina"),
]
DRIVER_DAYS = [
    dict(day="2026-09-03", driver="Али", working=True, extras=[
        dict(id="e1", amount=50, kind="fuel", status="approved"),
        dict(id="e2", amount=30, kind="we_got", status="approved"),   # нам вернули → минус
        dict(id="e3", amount=999, kind="other", status="pending"),    # ждёт → не в счёт
    ]),
    dict(day="2026-09-04", driver="Али", working=False, extras=[]),
]
CATALOG = {"beer": {"id": "beer", "name": "Пиво", "price_24_full": 240},
           "gin": {"id": "gin", "name": "Джин", "price_full": 100}}
COST = {"beer": 120.0, "gin": 40.0}     # ящик пива 120, бутылка джина 40
SUPPLIES = [
    dict(supply_id="S1", day="2026-09-03", status="done", items=[
        dict(id="beer", qty=48, asked=48, got={"jbr": 48}),   # 2 ящика × 120 = 240
        dict(id="gin", qty=10, asked=10, got={}),             # 10 × 40 = 400
        dict(id="nocost", qty=5, asked=5, got={}),            # цены нет → мимо суммы
    ]),
    dict(supply_id="X1", day="2026-09-03", kind="extra", base="Спиннейс", status="done",
         items=[dict(id="gin", qty=3, asked=3)], buys={"gin": {"price": 55, "qty": 3}}),  # 165
    dict(supply_id="S2", day="2026-09-04", status="open", cancelled_at="x", items=[dict(id="gin", qty=100)]),
]
FIN_DAYS = {"2026-09-03": dict(_id="2026-09-03", aside=100, collected=80, pay_b=60, note="тест"),
            "2026-09-04": dict(_id="2026-09-04", handed_fact=180, ordered_fact=777, extra_rp=10, pay_b_extra=5)}
ENTRIES = [dict(_id="a1", day="2026-09-03", book="rp", amount=25, comment="бензин", who="", by="Ст", at="t"),
           dict(_id="a2", day="2026-09-04", book="np", amount=7, comment="", who="владелец", by="Ст", at="t")]
MONTHS = {"2026-09": dict(_id="2026-09", storage=1000, safe_np_fact=2000),
          "2026-08": dict(_id="2026-08", safe_b_open=34500, debt_b_open=110198, carry_np=41030,
                          safe_np_fact=45325, safe_b_fact=28000)}
CHECK = {"2026-09-03": {"cash:jbr": {"done": True}}}
WRITES = []

async def orders_between(a, b): return [o for o in ORDERS if a <= o["timestamp"] < b]
async def get_driver_days_range(a, b): return [r for r in DRIVER_DAYS if a <= r["day"] <= b]
async def supplies_between(a, b): return [json.loads(json.dumps(s)) for s in SUPPLIES if a <= s["day"] <= b]
async def fin_days_get(a, b): return {k: v for k, v in FIN_DAYS.items() if a <= k <= b}
async def fin_entries_get(a, b): return [e for e in ENTRIES if a <= e["day"] <= b]
async def fin_month_get(m): return dict(MONTHS.get(m) or {})
async def fin_months_list(): return sorted(MONTHS)
async def checklist_get(d): return CHECK.get(d, {})
async def cost_map(): return COST
async def fin_day_set(day, fields, unset=None): WRITES.append(("day", day, fields, unset))
async def fin_month_set(m, fields, unset=None): WRITES.append(("month", m, fields, unset))
async def fin_entry_add(doc): WRITES.append(("entry", doc))
async def fin_entry_get(eid): return ENTRIES[0] if eid == "a1" else None
async def fin_entry_del(eid): WRITES.append(("del", eid)); return True
async def notify(*a, **k): WRITES.append(("notify", a))
for n, f in dict(orders_between=orders_between, get_driver_days_range=get_driver_days_range,
                 supplies_between=supplies_between, fin_days_get=fin_days_get,
                 fin_entries_get=fin_entries_get, fin_month_get=fin_month_get,
                 fin_months_list=fin_months_list, checklist_get=checklist_get,
                 fin_day_set=fin_day_set, fin_month_set=fin_month_set, fin_entry_add=fin_entry_add,
                 fin_entry_get=fin_entry_get, fin_entry_del=fin_entry_del).items():
    setattr(db, n, f)
stock_value.cost_map = cost_map
stock_routes._catalog = lambda: CATALOG
config_staff.MEAL_WORKING, config_staff.MEAL_OFF = 80, 40
config_staff.drivers = lambda: [dict(name="Али", district="jbr")]
fr.backdate.notify = notify
fr._biz_day = lambda ref=None: "2026-09-10" if ref is None else ref.strftime("%Y-%m-%d") if ref.hour >= 12 else (ref.replace(day=ref.day) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<44} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

async def main():
    b = await fr.build(M)
    d3 = next(r for r in b["days"] if r["day"] == "2026-09-03")
    d4 = next(r for r in b["days"] if r["day"] == "2026-09-04")
    print("— день 3 сен: заказы по учётным суткам, наличные, расход, сдали")
    eq("orders (2: вечер + ночь)", d3["orders"], 2)
    eq("gross", d3["gross"], 820); eq("cash", d3["cash"], 320); eq("crypto", d3["crypto"], 500)
    eq("tips", d3["tips"], 20)
    eq("spend = 80 + 50 − 30", d3["spend"], 100); eq("spend_pending", d3["spend_pending"], 999)
    eq("handed = cash − spend", d3["handed"], 220)
    eq("np_plus = 220 − 100 − 80", d3["np_plus"], 40)
    eq("ordered auto = 240 + 400", d3["ordered_auto"], 640); eq("ordered", d3["ordered"], 640)
    eq("ordered_extra (по buys)", d3["ordered_extra"], 165)
    eq("cover: 58 из 63 бутылок", d3["ordered_cover"], 92)
    eq("supplies 2", len(d3["supplies"]), 2)
    eq("expenses_sum", d3["expenses_sum"], 25); eq("note", d3["note"], "тест")
    eq("cash_need/got", (d3["cash_need"], d3["cash_got"]), (1, 1))
    print("— день 4 сен: факт сдачи и счёт поставщика перебивают приложение")
    eq("cash (долг и перевод мимо)", d4["cash"], 200); eq("card", d4["card"], 100); eq("debt", d4["debt"], 150)
    eq("spend = 40 (дома)", d4["spend"], 40); eq("handed app", d4["handed"], 160)
    eq("handed_fact", d4["handed_fact"], 180); eq("gap = 160 − 180", d4["gap"], -20)
    eq("np_plus = 180 − 0 − 0", d4["np_plus"], 180)
    eq("ordered_fact", d4["ordered"], 777); eq("ordered_auto (отменённая мимо)", d4["ordered_auto"], 0)
    eq("payouts_sum", d4["payouts_sum"], 7)
    eq("cash_need (marina + jbr дома, без отметок)", (d4["cash_need"], d4["cash_got"]), (2, 0))
    print("— бегущие остатки и переносы из августа (по факту пересчёта)")
    eq("opening carried", b["opening_carried"], dict(carry_np=45325, safe_b_open=28000, debt_b_open=110198))
    eq("opening explicit", b["opening_explicit"], dict(storage=1000, safe_np_fact=2000))
    eq("safe_b d3 = 28000 + 100 − 60", d3["safe_b"], 28040)
    eq("debt_b d3 = 110198 + 640 − 60", d3["debt_b"], 110778)
    eq("debt_b d4 = 110778 + 777 − 5", d4["debt_b"], 111550)
    eq("rp d4 = 80 + 10 − 25", d4["rp"], 65)
    eq("np_acc d4 = 40 + 180 − 7", d4["np_acc"], 213)
    eq("future flag on 11 sep", next(r for r in b["days"] if r["day"] == "2026-09-11")["future"], True)
    eq("today flag", next(r for r in b["days"] if r["day"] == "2026-09-10")["today"], True)
    print("— итоги месяца")
    eq("np.profit = 220 + 65", b["np"]["profit"], 285)
    eq("np.should_be = 278 + 1000 + 45325", b["np"]["should_be"], 46603)
    eq("np.diff = 2000 − 46603", b["np"]["diff"], -44603)
    eq("b.ratio = 1417 / (1270/100)", b["b"]["ratio"], round(1417 / 12.7, 1))
    eq("b.paid", b["b"]["paid"], 65)
    eq("econ = 1270 − 1417 − 25 − 140 + 10", b["econ"], -302)
    print("— ручки: проверка тела и запись")
    async def call(h, method, body):
        req = make_mocked_request(method, "/x", headers={"Authorization": "tma x"})
        req._read_bytes = json.dumps(body).encode()
        req["owner_user"] = {}; req["is_owner"] = True
        return await h.__wrapped__(req) if hasattr(h, "__wrapped__") else await h(req)
    # require_owner заворачивает; зовём внутренний обработчик через замыкание
    import inspect
    inner = {n: getattr(fr, n) for n in ("handle_day_set", "handle_entry_add", "handle_entry_del", "handle_month_set")}
    def raw(h):
        cl = inspect.getclosurevars(h).nonlocals
        return cl.get("handler") or h
    r = await raw(inner["handle_day_set"])(_req("POST", dict(day="2026-09-03", field="aside", value="150", **{"as": "Ст"})))
    eq("day_set ok", json.loads(r.text)["ok"], True)
    eq("day_set write", WRITES[-2], ("day", "2026-09-03", {"aside": 150, "by": "Ст"}, None))
    eq("day_set notify (прошлый день)", WRITES[-1][0], "notify")
    r = await raw(inner["handle_day_set"])(_req("POST", dict(day="2026-09-03", field="aside", value="")))
    eq("day_set unset", WRITES[-2], ("day", "2026-09-03", {"by": ""}, ["aside"]))
    r = await raw(inner["handle_day_set"])(_req("POST", dict(day="2026-09-03", field="hack", value=1)))
    eq("bad field → 400", r.status, 400)
    r = await raw(inner["handle_day_set"])(_req("POST", dict(day="2026-09-03", field="aside", value="abc")))
    eq("bad number → 400", r.status, 400)
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="np", amount=0)))
    eq("entry amount 0 → 400", r.status, 400)
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="np", amount=100, who="владелец", **{"as": "Ст"})))
    eq("entry add ok", json.loads(r.text)["ok"], True)
    eq("entry doc", {k: WRITES[-2][1][k] for k in ("day", "book", "amount", "who")}, dict(day="2026-09-10", book="np", amount=100, who="владелец"))
    r = await raw(inner["handle_entry_del"])(_req("DELETE", dict(id="zzz")))
    eq("del unknown → 404", r.status, 404)
    r = await raw(inner["handle_entry_del"])(_req("DELETE", dict(id="a1")))
    eq("del ok", json.loads(r.text)["ok"], True)
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month="2026-09", field="safe_np_fact", value=2500)))
    eq("month_set", WRITES[-1], ("month", "2026-09", {"safe_np_fact": 2500, "by": ""}, None))
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month="2026-13", field="storage", value=1)))
    eq("bad month → 400", r.status, 400)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — сервер собирает книгу верно")
    sys.exit(1 if fails else 0)

def _req(method, body):
    req = make_mocked_request(method, "/x")
    req._read_bytes = json.dumps(body).encode()
    return req

asyncio.run(main())
