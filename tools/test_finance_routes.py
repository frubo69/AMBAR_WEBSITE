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
async def fin_month_set(m, fields, unset=None):
    if "carry_cache" in fields: MONTHS.setdefault(m, {"_id": m})["carry_cache"] = fields["carry_cache"]; CACHE_W.append(m); return
    WRITES.append(("month", m, fields, unset)); MONTHS.setdefault(m, {"_id": m}).update(fields)
    for k in (unset or []): MONTHS[m].pop(k, None)
CACHE_W, INV = [], []
async def fin_entries_where(q): return [e for e in ENTRIES if all(e.get(k) == v for k, v in q.items())]
async def fin_carry_invalidate(m):
    INV.append(m)
    for k, d in MONTHS.items():
        if k >= m: d.pop("carry_cache", None)
async def fin_entry_add(doc): WRITES.append(("entry", doc))
async def fin_entry_get(eid): return ENTRIES[0] if eid == "a1" else None
async def fin_entry_del(eid): WRITES.append(("del", eid)); return True
async def notify(*a, **k): WRITES.append(("notify", a))
for n, f in dict(orders_between=orders_between, get_driver_days_range=get_driver_days_range,
                 supplies_between=supplies_between, fin_days_get=fin_days_get,
                 fin_entries_get=fin_entries_get, fin_month_get=fin_month_get,
                 fin_months_list=fin_months_list, checklist_get=checklist_get,
                 fin_day_set=fin_day_set, fin_month_set=fin_month_set, fin_entry_add=fin_entry_add,
                 fin_entry_get=fin_entry_get, fin_entry_del=fin_entry_del, fin_entries_where=fin_entries_where,
                 fin_carry_invalidate=fin_carry_invalidate).items():
    setattr(db, n, f)
stock_value.cost_map = cost_map
stock_routes._catalog = lambda: CATALOG
config_staff.MEAL_WORKING, config_staff.MEAL_OFF = 80, 40
config_staff.drivers = lambda: [dict(name="Али", district="jbr")]
fr.backdate.notify = notify
# ── бюджет и зарплаты: заполняются во второй фазе ──
BUDGET, PEOPLE, PAYM, ITEMS, SHIFTS = [], [], [], [], []
async def fin_budget_get(m): return [dict(l) for l in BUDGET if l["month"] == m]
async def fin_budget_line_get(lid): return next((dict(l) for l in BUDGET if l["_id"] == lid), None)
async def fin_budget_set(doc):
    WRITES.append(("bset", doc)); BUDGET[:] = [l for l in BUDGET if l["_id"] != doc["_id"]]; BUDGET.append(dict(doc))
async def fin_budget_del(lid): WRITES.append(("bdel", lid)); BUDGET[:] = [l for l in BUDGET if l["_id"] != lid]; return True
async def fin_people_get(): return [dict(p) for p in PEOPLE]
async def fin_person_set(name, fields, unset=None): WRITES.append(("person", name, fields))
async def fin_pay_months_upto(m): return [dict(d) for d in PAYM if d["month"] <= m]
async def fin_pay_month_set(m, name, fields, unset=None): WRITES.append(("paym", m, name, fields, unset))
async def fin_pay_items_get(): return [dict(i) for i in ITEMS]
async def fin_pay_item_add(doc): WRITES.append(("item", doc)); ITEMS.append(dict(doc))
async def fin_pay_item_get(iid): return next((dict(i) for i in ITEMS if i["_id"] == iid), None)
async def fin_pay_item_del(iid): WRITES.append(("idel", iid)); ITEMS[:] = [i for i in ITEMS if i["_id"] != iid]; return True
async def shift_days_worked(a, b): return [x for x in SHIFTS if a <= x[0] <= b]
for n, f in dict(fin_budget_get=fin_budget_get, fin_budget_line_get=fin_budget_line_get, fin_budget_set=fin_budget_set,
                 fin_budget_del=fin_budget_del, fin_people_get=fin_people_get, fin_person_set=fin_person_set,
                 fin_pay_months_upto=fin_pay_months_upto, fin_pay_month_set=fin_pay_month_set,
                 fin_pay_items_get=fin_pay_items_get, fin_pay_item_add=fin_pay_item_add, fin_pay_item_get=fin_pay_item_get,
                 fin_pay_item_del=fin_pay_item_del, shift_days_worked=shift_days_worked).items():
    setattr(db, n, f)
import types
_rates = types.ModuleType("rates")
async def _get_rates(force=False): return {"rates": [{"code": "USD", "aed": 3.6725, "cash_aed": 3.67}]}
_rates.get_rates = _get_rates
sys.modules["rates"] = _rates
config_staff.SENIOR_OPERATORS = [{"id": "parviz", "name": "Парвиз", "telegram_id": 1}]
config_staff.operators = lambda: [dict(name="Парвиз", senior=True, districts=["jbr", "marina"]),
                                  dict(name="Умар", senior=False, districts=["jbr"]),
                                  dict(name="Фарух", senior=False, districts=["marina"])]
config_staff.driver_names = lambda: ["Али"]
config_staff.operator_names = lambda: ["Умар", "Фарух", "Парвиз"]
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
    eq("np_plus = 180 − 777 (базе по заказу дня) − 0 (нормы нет)", d4["np_plus"], -597)
    eq("aside_src order / collected_src пусто", (d4["aside_src"], d4["collected_src"]), ("order", ""))
    eq("d3: вписанное руками главнее (manual)", (d3["aside_src"], d3["collected_src"]), ("manual", "manual"))
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
    eq("np_acc d4 = 40 − 597 − 7", d4["np_acc"], -564)
    eq("future flag on 11 sep", next(r for r in b["days"] if r["day"] == "2026-09-11")["future"], True)
    eq("today flag", next(r for r in b["days"] if r["day"] == "2026-09-10")["today"], True)
    print("— итоги месяца")
    eq("np.profit = (40 − 597) + 65", b["np"]["profit"], -492)
    eq("np.should_be = −492 − 7 + 1000 + 45325", b["np"]["should_be"], 45826)
    eq("np.diff = 2000 − 45826", b["np"]["diff"], -43826)
    eq("safe: база 28817, сейф 45826, всего", (b["safe"]["b"], b["safe"]["np"], b["safe"]["total"]), (28817, 45826, 74643))
    eq("b.ratio = 1417 / (1270/100)", b["b"]["ratio"], round(1417 / 12.7, 1))
    eq("b.paid", b["b"]["paid"], 65)
    eq("econ = 1270 − 1417 − 25 − 140 (приход не доход)", b["econ"], -312)
    print("— без бюджета: нормы нет, курс доллара с биржи, зарплаты пустые")
    eq("budget пустой, norm 0, prev_has False", (b["budget"]["empty"], b["budget"]["norm"], b["budget"]["prev_has"]), (True, 0, False))
    eq("usd с биржи (наличный курс)", (b["pay"]["usd"], b["pay"]["usd_set"]), (3.67, False))
    eq("люди из расписания: Парвиз, Умар, Фарух, Али", [p["name"] for p in b["pay"]["people"]], ["Парвиз", "Умар", "Фарух", "Али"])
    print("— с бюджетом, приходом в фонд и зарплатами")
    BUDGET[:] = [dict(_id="L1", month=M, name="Зарплаты", plan=10000, due=0, note="", kind="salary", ord=0),
                 dict(_id="L2", month=M, name="Аренда JVC", plan=31000, due=15, note="", kind="", ord=1),
                 dict(_id="L0", month=M, name="Аренда офис", plan=0, due=0, note="", kind="", ord=2)]
    ENTRIES.append(dict(_id="in1", day="2026-09-03", book="in", amount=500, comment="с крипты", who="", by="Ст", at="t"))
    ENTRIES.append(dict(_id="a3", day="2026-09-04", book="rp", amount=300, comment="аренда", who="", line="L2", by="Ст", at="t"))
    ENTRIES.append(dict(_id="s1", day="2026-09-04", book="rp", amount=1000, comment="Зарплата", who="Али", line="L1", kind="salary", pay_month=M, by="Ст", at="t"))
    # зарплата за август, выданная в сентябре — в сентябрьский фонд, но в августовскую ведомость
    ENTRIES.append(dict(_id="s0", day="2026-09-05", book="rp", amount=900, comment="Зарплата", who="Али", line="L1", kind="salary", pay_month="2026-08", by="Ст", at="t"))
    ENTRIES.append(dict(_id="adv1", day="2026-09-04", book="rp", amount=2000, comment="Аванс", who="Макар", line="L1", kind="advance", item="i2", by="Ст", at="t"))
    PEOPLE[:] = [dict(_id="Макар", role="senior", manual=True), dict(_id="Умар", role="operator", hidden=True)]
    PAYM[:] = [dict(_id="2026-08|Али", month="2026-08", name="Али", rate=100, unit="day", cur="AED"),
               dict(_id="2026-09|Макар", month=M, name="Макар", rate=1750, unit="month", cur="USD"),
               dict(_id="2026-09|Али", month=M, name="Али", days=20)]
    ITEMS[:] = [dict(_id="i1", name="Али", kind="fine", amount=4040, per_month=1000, **{"from": "2026-08"}, day="2026-08-20", note="кр. свет"),
                dict(_id="i2", name="Макар", kind="advance", amount=2000, per_month=0, **{"from": M}, day="2026-09-04", note="", entry="adv1")]
    SHIFTS[:] = [("2026-09-03", "jbr"), ("2026-09-04", "jbr")]
    b = await fr.build(M)
    d3 = next(r for r in b["days"] if r["day"] == "2026-09-03")
    d4 = next(r for r in b["days"] if r["day"] == "2026-09-04")
    bu = b["budget"]
    eq("бюджет: аренда 31000 + зарплатный фонд (Али 100×30 + Макар 1750$×3.67 = 9422.5) = 40422.5, / 30 дней, норма вверх до сотни 1400",
       (bu["total"], bu["salary"]["plan"], bu["days"], bu["norm_auto"], bu["norm"], bu["norm_set"]), (40422.5, 9422.5, 30, 1400, 1400, False))
    eq("строка «Зарплаты» из старого образца скрыта, люди в фонде: Макар (руками) первым, потом Парвиз, Фарух, Али; Умар скрыт",
       ([l["name"] for l in bu["lines"]], [x["name"] for x in bu["salary"]["people"]]), (["Аренда JVC"], ["Макар", "Парвиз", "Фарух", "Али"]))
    eq("«Аренда JVC» без поля group — в группе аренды по названию; пустая «Аренда офис» скрыта; итог группы", (bu["lines"][0]["group"], bu["rent"]), ("rent", dict(plan=31000, fact=300, left=30700, n=1)))
    eq("водителю — район (jbr → код из config_offices), остальным пусто", [(x["name"], x["district"]) for x in bu["salary"]["people"]], [("Макар", ""), ("Парвиз", ""), ("Фарух", ""), ("Али", "jbr")])
    eq("оклады в фонде: Али 100/день → 3000, Макар 1750 $ → 6422.5, без оклада — None и 0",
       [(x["rate"], x["unit"], x["cur"], x["plan"]) for x in bu["salary"]["people"]], [(1750, "month", "USD", 6422.5), (None, "month", "AED", 0), (None, "month", "AED", 0), (100, "day", "AED", 3000)])
    eq("факт: зарплаты 1000 + 900 + аванс 2000 (по виду записи), аренда 300, вне плана 25", (bu["salary"]["fact"], [l["fact"] for l in bu["lines"]], bu["off_plan"], bu["fact"]), (3900, [300], 25, 4225))
    eq("d4: в фонд по норме 1400 (collected_src norm)", (d4["collected"], d4["collected_src"]), (1400, "norm"))
    eq("d4: прибыль дня = 180 − 777 − 1400", d4["np_plus"], -1997)
    eq("d3: приход в фонд 500 записью, ins 1", (d3["extra_rp"], len(d3["ins"])), (500, 1))
    eq("d3: фонд = 80 + 500 − 25", d3["rp"], 555)
    eq("d4: фонд = 555 + 1400 + 10 − (300 + 1000 + 2000)", d4["rp"], -1335)
    d5 = next(r for r in b["days"] if r["day"] == "2026-09-05")
    eq("d5: зарплата за август — расход сентябрьского фонда", (d5["expenses_sum"], d5["salary_sum"]), (900, 900))
    eq("d4: зарплаты в расходах дня 3000", d4["salary_sum"], 3000)
    eq("запись несёт строку бюджета", next(e for e in d4["expenses"] if e["id"] == "a3")["line_name"], "Аренда JVC")
    P = {p["name"]: p for p in b["pay"]["people"]}
    eq("Умар скрыт, Макар (руками) есть", ("Умар" in P, P["Макар"]["manual"]), (False, True))
    a = P["Али"]
    eq("Али: ставка 100/день с августа, дней 20 (вписано), начислено 2000", (a["rate"], a["unit"], a["rate_month"], a["days"], a["days_set"], a["accrued"]), (100, "day", "2026-08", 20, True, 2000))
    eq("Али: дней по приложению 1 (working)", a["days_auto"], 1)
    eq("Али: штраф 4040 по 1000: август снял 1000, сентябрь 1000, останется 2040", (a["minus"], a["items"][0]["before"], a["items"][0]["due"], a["items"][0]["after"]), (1000, 3040, 1000, 2040))
    eq("Али: к выплате 1000, выплачено 1000 (за август — не сюда), остаток 0, долг 2040", (a["to_pay"], a["paid"], a["left"], a["debt"]), (1000, 1000, 0, 2040))
    b8 = await fr.build("2026-08")
    a8 = {p["name"]: p for p in b8["pay"]["people"]}["Али"]
    eq("август Али: выплачено 900 сентябрьской записью, штраф 1000", (a8["paid"], a8["minus"]), (900, 1000))
    eq("кэш переноса августа записан один раз и читается", (CACHE_W.count("2026-08"), isinstance(MONTHS["2026-08"].get("carry_cache"), dict)), (1, True))
    m_ = P["Макар"]
    eq("Макар: 1750 $ × 3.67 = 6422.5, аванс 2000 в этом месяце", (m_["rate_aed"], m_["accrued"], m_["minus"], m_["to_pay"], m_["left"]), (6422.5, 6422.5, 2000, 4422.5, 4422.5))
    eq("Макар: дней = дни смен (старший)", m_["days_auto"], 2)
    eq("Фарух: дней = смены его района (marina) 0", P["Фарух"]["days_auto"], 0)
    eq("итог к выплате", b["pay"]["totals"]["to_pay"], 5422.5)
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
    inner2 = {n: getattr(fr, n) for n in ("handle_budget_set", "handle_budget_del", "handle_budget_fill",
                                            "handle_pay_item_add", "handle_pay_item_del", "handle_pay_out",
                                            "handle_pay_month_set", "handle_pay_person")}
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=50, line="zzz")))
    eq("entry: чужая строка бюджета → 400", r.status, 400)
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=50, kind="salary")))
    eq("entry: зарплата без имени → 400", r.status, 400)
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="in", amount=250, comment="с крипты")))
    eq("entry in ok", (r.status, WRITES[-2][1]["book"], WRITES[-2][1]["line"]), (200, "in", ""))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Виза", plan="11000", due=20, **{"as": "Ст"})))
    eq("budget_set новая: ord 3", (r.status, WRITES[-1][0], WRITES[-1][1]["ord"], WRITES[-1][1]["plan"]), (200, "bset", 3, 11000))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="nope", name="X", plan=1)))
    eq("budget_set чужой id → 404", r.status, 404)
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="L2", name="Аренда JVC", plan=32000, due=15)))
    eq("budget_set правка: ord прежний 1", (r.status, WRITES[-1][1]["ord"], WRITES[-1][1]["plan"]), (200, 1, 32000))
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month=M, **{"from": "prev"})))
    eq("fill при непустом → 409", r.status, 409)
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month="2026-10", **{"from": "prev"})))
    eq("fill октября из сентября: 2 строки (старые «Зарплаты» и пустая «Аренда офис» не копируются)", (r.status, json.loads(r.text)["n"]), (200, 2))
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month="2026-11", **{"from": "template"})))
    eq("fill по образцу: без строки «Зарплаты» и без «Аренда офис», здания в группе rent",
       (any(w[0] == "bset" and w[1]["name"] in ("Зарплаты", "Аренда офис") and w[1]["month"] == "2026-11" for w in WRITES),
        sorted(w[1]["group"] for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["name"].startswith("Аренда "))), (False, ["rent"] * 5))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Аренда склад", plan=900, group="rent")))
    eq("новая строка в группе аренды", (r.status, WRITES[-1][1]["group"]), (200, "rent"))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="X", plan=1, group="zzz")))
    eq("чужая группа → 400", r.status, 400)
    r = await raw(inner2["handle_budget_del"])(_req("DELETE", dict(id="L2")))
    eq("budget_del", (r.status, WRITES[-1]), (200, ("bdel", "L2")))
    bu2 = json.loads(r.text)["book"]["budget"]
    eq("записи удалённой статьи: вне плана 25 + 300 и в факте", (bu2["off_plan"], bu2["fact"]), (325, 4225))
    eq("зарплаты не статья: kind в ручке не принимается, строка kind=salary не создаётся", all(w[1].get("kind") == "" for w in WRITES if w[0] == "bset"), True)
    eq("правка сбрасывает кэш переносов с этого месяца", INV[-1], M)
    r = await raw(inner2["handle_pay_item_add"])(_req("POST", dict(name="Али", kind="advance", amount=500, day="2026-09-10", **{"from": "next", "as": "Ст"})))
    eq("аванс: запись расхода (kind advance, who, строка зарплат) + удержание со следующего месяца",
       (r.status, WRITES[-3][0], WRITES[-3][1]["kind"], WRITES[-3][1]["who"], WRITES[-3][1]["line"], WRITES[-2][0], WRITES[-2][1]["from"], WRITES[-2][1]["entry"] == WRITES[-3][1]["_id"]),
       (200, "entry", "advance", "Али", "", "item", "2026-10", True))
    r = await raw(inner2["handle_pay_item_add"])(_req("POST", dict(name="Али", kind="fine", amount=300, per_month=100)))
    eq("штраф: без записи расхода, с этого месяца", (r.status, WRITES[-1][0], WRITES[-1][1]["from"], WRITES[-1][1]["entry"]), (200, "item", "2026-09", ""))
    r = await raw(inner2["handle_pay_item_add"])(_req("POST", dict(name="Али", kind="bad", amount=1)))
    eq("плохой вид → 400", r.status, 400)
    r = await raw(inner2["handle_pay_item_del"])(_req("DELETE", dict(id="i2", month=M)))
    eq("убрать аванс: удержание и запись расхода", (r.status, WRITES[-2], WRITES[-1]), (200, ("idel", "i2"), ("del", "adv1")))
    r = await raw(inner2["handle_pay_out"])(_req("POST", dict(name="Макар", amount=4000, day="2026-09-10", month="2026-08")))
    eq("выплата: запись kind salary без статьи, за август, ответ за август", (r.status, WRITES[-2][1]["kind"], WRITES[-2][1]["who"], WRITES[-2][1]["line"], WRITES[-2][1]["comment"], WRITES[-2][1]["pay_month"], json.loads(r.text)["book"]["month"]), (200, "salary", "Макар", "", "Зарплата", "2026-08", "2026-08"))
    r = await raw(inner2["handle_pay_item_add"])(_req("POST", dict(name="Али", kind="fine", amount=50, month="2026-08", **{"from": "next"})))
    eq("штраф из экрана августа «со следующего» → с сентября", WRITES[-1][1]["from"], "2026-09")
    r = await raw(inner2["handle_pay_month_set"])(_req("POST", dict(month=M, name="Али", field="rate", value="120")))
    eq("ставка месяца", WRITES[-1], ("paym", M, "Али", {"rate": 120, "by": ""}, None))
    r = await raw(inner2["handle_pay_month_set"])(_req("POST", dict(month=M, name="Али", field="rate", value="1000", cur="USD")))
    eq("ставка в долларах: rate и cur одной записью", WRITES[-1], ("paym", M, "Али", {"by": "", "cur": "USD", "rate": 1000}, None))
    r = await raw(inner2["handle_pay_month_set"])(_req("POST", dict(month=M, name="Али", field="rate", value="1000", cur="EUR")))
    eq("чужая валюта не пишется", "cur" in WRITES[-1][3], False)
    eq("в фонде есть курс", b["budget"]["salary"]["usd"], 3.67)
    r = await raw(inner2["handle_pay_month_set"])(_req("POST", dict(month=M, name="Али", field="unit", value="week")))
    eq("плохая единица → 400", r.status, 400)
    r = await raw(inner2["handle_pay_month_set"])(_req("POST", dict(month=M, name="Али", field="days", value="")))
    eq("снять дни → unset", WRITES[-1], ("paym", M, "Али", {"by": ""}, ["days"]))
    r = await raw(inner2["handle_pay_person"])(_req("POST", dict(name="Слон", role="senior", month=M)))
    eq("новый человек руками: manual True", (r.status, WRITES[-1][0], WRITES[-1][2]["manual"]), (200, "person", True))
    r = await raw(inner2["handle_pay_person"])(_req("POST", dict(name="Али", role="driver", hidden=True, month=M)))
    eq("скрыть водителя из расписания: manual False", (WRITES[-1][2]["hidden"], WRITES[-1][2]["manual"]), (True, False))
    r = await raw(inner2["handle_pay_person"])(_req("POST", dict(name="Али", hidden=True, month=M)))
    eq("скрыть без роли: роль и заметка не затираются", ("role" in WRITES[-1][2], "note" in WRITES[-1][2]), (False, False))
    r = await raw(inner2["handle_pay_person"])(_req("POST", dict(name="Али", month="2026-13")))
    eq("плохой месяц → 400, не 500", r.status, 400)
    PEOPLE[:] = [dict(_id="Макар", role="senior", manual=True, ord=1), dict(_id="Парвиз", role="senior", ord=0)]
    b2 = await fr.build(M)
    eq("порядок руками: Парвиз (ord 0) перед Макаром (ord 1), без ord — следом как были",
       [x["name"] for x in b2["budget"]["salary"]["people"]], ["Парвиз", "Макар", "Умар", "Фарух", "Али"])
    r = await raw(getattr(fr, "handle_pay_order"))(_req("POST", dict(names=["Умар", "Парвиз"], month=M, **{"as": "Ст"})))
    eq("порядок записан: ord 0 Умар, ord 1 Парвиз", (r.status, [(w[1], w[2]["ord"]) for w in WRITES[-2:]]), (200, [("Умар", 0), ("Парвиз", 1)]))
    r = await raw(getattr(fr, "handle_pay_order"))(_req("POST", dict(names=[])))
    eq("пустой порядок → 400", r.status, 400)
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month=M, field="norm", value=8000)))
    eq("норма в день", WRITES[-1], ("month", M, {"norm": 8000, "by": ""}, None))
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month=M, field="norm_b", value=-5)))
    eq("норма базе отрицательная → 400", r.status, 400)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — сервер собирает книгу верно")
    sys.exit(1 if fails else 0)

def _req(method, body):
    req = make_mocked_request(method, "/x")
    req._read_bytes = json.dumps(body).encode()
    return req

asyncio.run(main())
