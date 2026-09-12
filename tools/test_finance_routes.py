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
FIN_DAYS = {"2026-09-03": dict(_id="2026-09-03", aside=100, collected=80, pay_b=60, note="тест", ok=True, ok_by="Ст"),
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
PHOTOS = {}
async def expense_photo_set(item_id, photo, thumb=""): PHOTOS[item_id] = photo; WRITES.append(("photo", item_id, len(photo), len(thumb)))
async def expense_photo_del(item_id): PHOTOS.pop(item_id, None); WRITES.append(("photo_del", item_id))
async def expense_photo(item_id): return PHOTOS.get(item_id, b"")
for n, f in dict(orders_between=orders_between, get_driver_days_range=get_driver_days_range,
                 supplies_between=supplies_between, fin_days_get=fin_days_get,
                 fin_entries_get=fin_entries_get, fin_month_get=fin_month_get,
                 fin_months_list=fin_months_list, checklist_get=checklist_get,
                 fin_day_set=fin_day_set, fin_month_set=fin_month_set, fin_entry_add=fin_entry_add,
                 fin_entry_get=fin_entry_get, fin_entry_del=fin_entry_del, fin_entries_where=fin_entries_where,
                 fin_carry_invalidate=fin_carry_invalidate, expense_photo_set=expense_photo_set,
                 expense_photo_del=expense_photo_del, expense_photo=expense_photo).items():
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
    eq("выручка = наличные 320 − чай 20 − расход 100", d3["handed"], 200)
    eq("ЧП+ = 200 − 100 − 80 (вписано руками)", d3["np_plus"], 20)
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
    eq("ЧП+ = 180 − 90 (половина Барракуде) − 0 (нормы нет)", (d4["aside"], d4["collected"], d4["np_plus"]), (90, 0, 90))
    eq("aside_src half / collected_src пусто; день ждёт подтверждения", (d4["aside_src"], d4["collected_src"], d4["ok"], d4["pending"]), ("half", "", False, True))
    eq("d3: вписанное руками главнее (manual), подтверждён → в деньгах; d4 — только предложение", (d3["aside_src"], d3["collected_src"], d3["ok"], d3["counted"], d4["counted"]), ("manual", "manual", True, True, False))
    eq("ordered_fact", d4["ordered"], 777); eq("ordered_auto (отменённая мимо)", d4["ordered_auto"], 0)
    eq("payouts_sum", d4["payouts_sum"], 7)
    eq("cash_need (marina + jbr дома, без отметок)", (d4["cash_need"], d4["cash_got"]), (2, 0))
    print("— бегущие остатки и переносы из августа (по факту пересчёта)")
    eq("opening carried: стопки августа (старый carry_np → ЧП), факты пересчёта больше не читаются", b["opening_carried"], dict(carry_np=0, safe_b_open=34500, debt_b_open=110198, rp_open=0, np_open=41030))
    eq("opening explicit (storage/факты — не поля)", b["opening_explicit"], {})
    eq("safe_b d3 = 34500 + 100 − 60", d3["safe_b"], 34540)
    eq("debt_b d3 = 110198 + 640 − 60", d3["debt_b"], 110778)
    eq("debt_b d4 = 110778 + 777 − 5", d4["debt_b"], 111550)
    eq("rp d4 = 80 (d3 подтверждён) + 10 − 25; РП+ дня 4 не считается", d4["rp"], 65)
    eq("np_acc d4 = 20 + 0 (д4 не подтверждён) − 7", d4["np_acc"], 13)
    eq("future flag on 11 sep", next(r for r in b["days"] if r["day"] == "2026-09-11")["future"], True)
    eq("today flag", next(r for r in b["days"] if r["day"] == "2026-09-10")["today"], True)
    print("— итоги месяца")
    eq("np.days = 20 (только подтверждённые дни)", b["np"]["days"], 20)
    eq("сейф три стопки (д4 не подтверждён — не в деньгах): Барракуда 34540, РП 80 + 10 − 25, ЧП 41030 + 20 − 7 − 5 (сверх Барракуде из ЧП)",
       (b["safe"]["b"], b["safe"]["rp"], b["safe"]["np"], b["safe"]["total"]), (34540, 65, 41038, 75643))
    eq("сейф: потоки и ждущие подтверждения дни (только 4 сен)",
       (b["safe"]["b_in"], b["safe"]["b_out"], b["safe"]["rp_in"], b["safe"]["rp_out"], b["safe"]["np_in"], b["safe"]["np_out"], b["safe"]["pending"]), (100, 60, 90, 25, 20, 12, 1))
    eq("стопки на день 4: после оплаты сверх 5 из ЧП", (d4["stack_b"], d4["stack_rp"], d4["stack_np"], d4["stack_total"]), (34540, 65, 41038, 75643))
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
       ([l["name"] for l in bu["lines"]], [x["name"] for x in bu["salary"]["people"]]), (["Аренда JVC", "Аренда офис"], ["Макар", "Парвиз", "Фарух", "Али"]))
    eq("«Аренда офис» — офис (по названию), JVC — билдинг", [(l["name"], l["office"]) for l in bu["lines"]], [("Аренда JVC", False), ("Аренда офис", True)])
    eq("код района и короткое имя: B1 JVC, офис — «Офис»", [(l["code"], l["short"]) for l in bu["lines"]], [("B1", "JVC"), ("", "Офис")])
    eq("«Аренда JVC» без поля group — в группе аренды по названию; итог группы с офисом", (bu["lines"][0]["group"], bu["rent"]), ("rent", dict(plan=31000, fact=300, left=30700, n=2)))
    eq("водителю — район (jbr → код из config_offices), остальным пусто", [(x["name"], x["district"]) for x in bu["salary"]["people"]], [("Макар", ""), ("Парвиз", ""), ("Фарух", ""), ("Али", "jbr")])
    eq("оклады в фонде: Али 100/день → 3000, Макар 1750 $ → 6422.5, без оклада — None и 0",
       [(x["rate"], x["unit"], x["cur"], x["plan"]) for x in bu["salary"]["people"]], [(1750, "month", "USD", 6422.5), (None, "month", "AED", 0), (None, "month", "AED", 0), (100, "day", "AED", 3000)])
    eq("факт: зарплаты 1000 + 900 + аванс 2000 (по виду записи), аренда 300, вне плана 25", (bu["salary"]["fact"], [l["fact"] for l in bu["lines"]], bu["off_plan"], bu["fact"]), (3900, [300, 0], 25, 4225))
    eq("d4: в РП+ по норме, но не больше остатка после половины: min(1400, 180 − 90) = 90", (d4["collected"], d4["collected_src"]), (90, "norm"))
    eq("d4: ЧП+ = 180 − 90 − 90 = 0", d4["np_plus"], 0)
    eq("d3: приход в фонд 500 записью, ins 1", (d3["extra_rp"], len(d3["ins"])), (500, 1))
    eq("d3: фонд = 80 + 500 − 25", d3["rp"], 555)
    eq("d4: фонд = 555 + 0 (не подтверждён) + 10 − (300 + 1000 + 2000)", d4["rp"], -2735)
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
    inner = {n: getattr(fr, n) for n in ("handle_day_set", "handle_day_ok", "handle_entry_add", "handle_entry_del", "handle_month_set")}
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
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month="2026-09", field="rp_open", value=2500)))
    eq("month_set (стопка РП на начало)", WRITES[-1], ("month", "2026-09", {"rp_open": 2500, "by": ""}, None))
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month="2026-09", field="safe_np_fact", value=2500)))
    eq("старое поле факта сейфа → 400", r.status, 400)
    r = await raw(inner["handle_day_ok"])(_req("POST", dict(day="2026-09-04", aside=90, collected=90, **{"as": "Ст"})))
    eq("подтверждение дня: суммы заморожены, ok", (r.status, WRITES[-1][0], WRITES[-1][2]["aside"], WRITES[-1][2]["collected"], WRITES[-1][2]["ok"], WRITES[-1][2]["ok_by"]), (200, "day", 90, 90, True, "Ст"))
    r = await raw(inner["handle_day_ok"])(_req("POST", dict(day="2026-09-20", aside=1, collected=1)))
    eq("подтверждение будущего дня → 400", r.status, 400)
    r = await raw(inner["handle_day_ok"])(_req("POST", dict(day="2026-09-04", aside=-1, collected=1)))
    eq("подтверждение с минусом → 400", r.status, 400)
    r = await raw(inner["handle_day_set"])(_req("POST", dict(day="2026-09-04", field="pay", value="40000", **{"as": "Ст"})))
    eq("оплата Барракуде одной суммой пишется полем pay", (r.status, WRITES[-2]), (200, ("day", "2026-09-04", {"pay": 40000, "by": "Ст"}, None)))
    FIN_DAYS["2026-09-04"]["pay"] = 40000
    bp = await fr.build(M); d4p = next(x for x in bp["days"] if x["day"] == "2026-09-04")
    eq("оплата делится сама: из стопки Барракуды 34540, остальное 5460 из ЧП", (d4p["pay"], d4p["pay_b"], d4p["pay_b_extra"], d4p["stack_b"]), (40000, 34540, 5460, 0))
    del FIN_DAYS["2026-09-04"]["pay"]
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
    eq("entry in ok (приход — без чека)", (r.status, WRITES[-2][1]["book"], WRITES[-2][1]["line"]), (200, "in", ""))
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=70, comment="симка")))
    eq("расход из РП без чека → 400 no_photo", (r.status, json.loads(r.text)["error"]), (400, "no_photo"))
    import base64
    jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"\x00" * 2500).decode()
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=70, comment="симка", photo=jpeg, thumb="data:image/jpeg;base64,AAAA")))
    ph_id = json.loads(r.text)["id"]
    eq("расход с чеком: снимок лёг под fin:<id>, запись помечена photo", (r.status, WRITES[-3][0], WRITES[-3][1], WRITES[-3][2], WRITES[-2][1]["photo"]), (200, "photo", "fin:" + ph_id, 2502, True))
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=70, comment="x", photo="data:image/jpeg;base64,AAAA")))
    eq("битый снимок → 400 bad_photo", (r.status, json.loads(r.text)["error"]), (400, "bad_photo"))
    # аренда платится кнопкой «Оплатил» — суммой из плана, чека к ней нет
    BUDGET.append(dict(_id="rentX", month="2026-09", name="Аренда B9", group="rent", plan=9000, ord=9))
    BUDGET.append(dict(_id="simX", month="2026-09", name="Пополнение", group="sim", plan=500, ord=10))
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=9000, line="rentX")))
    eq("аренда без чека → записана", (r.status, WRITES[-2][1]["line"], WRITES[-2][1]["photo"]), (200, "rentX", False))
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=500, line="simX")))
    eq("не аренда без чека → 400 no_photo", (r.status, json.loads(r.text)["error"]), (400, "no_photo"))
    BUDGET.append(dict(_id="carX", month="2026-09", name="Орион Рент", group="car", plan=20000, ord=11))
    r = await raw(inner["handle_entry_add"])(_req("POST", dict(day="2026-09-10", book="rp", amount=20000, line="carX")))
    eq("аренда машин без чека → записана", (r.status, WRITES[-2][1]["line"]), (200, "carX"))
    ENTRIES[:] = [e for e in ENTRIES if e.get("line") not in ("rentX", "carX")]
    BUDGET[:] = [l for l in BUDGET if l["_id"] not in ("rentX", "simX", "carX")]
    ENTRIES.append(dict(_id="ph1", day="2026-09-10", book="rp", amount=70, comment="симка", who="", by="Ст", at="t", photo=True))
    PHOTOS["fin:ph1"] = b"\xff\xd8" + b"\x00" * 10
    async def fin_entry_get2(eid): return next((e for e in ENTRIES if e["_id"] == eid), None)
    db.fin_entry_get = fin_entry_get2
    r = await raw(inner["handle_entry_del"])(_req("DELETE", dict(id="ph1")))
    eq("удаление записи уносит чек", (r.status, "fin:ph1" in PHOTOS, WRITES[-2][0]), (200, False, "photo_del"))
    db.fin_entry_get = fin_entry_get
    ENTRIES[:] = [e for e in ENTRIES if e["_id"] != "ph1"]
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Виза", plan="11000", due=20, **{"as": "Ст"})))
    eq("budget_set новая: ord 3", (r.status, WRITES[-1][0], WRITES[-1][1]["ord"], WRITES[-1][1]["plan"]), (200, "bset", 3, 11000))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="nope", name="X", plan=1)))
    eq("budget_set чужой id → 404", r.status, 404)
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="L2", name="Аренда JVC", plan=32000, due=15)))
    eq("budget_set правка: ord прежний 1", (r.status, WRITES[-1][1]["ord"], WRITES[-1][1]["plan"]), (200, 1, 32000))
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month=M, **{"from": "prev"})))
    eq("fill при непустом → 409", r.status, 409)
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month="2026-10", **{"from": "prev"})))
    eq("fill октября из сентября: 3 строки (JVC, офис, Виза; «Зарплаты» не копируются)", (r.status, json.loads(r.text)["n"]), (200, 3))
    r = await raw(inner2["handle_budget_fill"])(_req("POST", dict(month="2026-11", **{"from": "template"})))
    eq("fill по образцу: без «Зарплат», офис первым с kind office, пять зданий в группе rent",
       (any(w[0] == "bset" and w[1]["name"] == "Зарплаты" and w[1]["month"] == "2026-11" for w in WRITES),
        [(w[1]["name"], w[1]["kind"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11"][0],
        sorted(w[1]["group"] for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["name"].startswith("Аренда "))), (False, ("Аренда офис", "office"), ["rent"] * 6))
    eq("образец: Гараж и ТО, Парковка, Страховка/Пассинг — группа auto", [w[1]["name"] for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "auto"], ["Гараж и ТО", "Парковка", "Страховка/Пассинг"])
    eq("образец: три рента — группа car", [w[1]["name"] for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "car"], ["Орион Рент", "Алексей Рент", "Другой Рент"])
    eq("старое название «Авто» без поля group — всё ещё auto", fr._line_group({"name": "Авто"}), "auto")
    eq("«Аренда» без района и без поля group — auto, не rent", fr._line_group({"name": "Аренда"}), "auto")
    eq("«Аренда JVC» без поля group — rent", fr._line_group({"name": "Аренда JVC"}), "rent")
    eq("образец: гараж, парковка, страховка/пассинг — все без даты (pool)", [(w[1]["name"], w[1]["kind"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "auto"], [("Гараж и ТО", "pool"), ("Парковка", "pool"), ("Страховка/Пассинг", "pool")])
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Гараж и ТО", plan=3000, group="auto", period=3, next="2026-10-05", note="по чекам", **{"as": "Ст"})))
    pool_id = json.loads(r.text)["id"]
    eq("pool по названию: вид pool, график не принимается", (r.status, WRITES[-1][1]["kind"], WRITES[-1][1]["period"], WRITES[-1][1]["next"], WRITES[-1][1]["note"]), (200, "pool", 1, "", "по чекам"))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id=pool_id, name="Гараж и ТО", plan=4000, group="auto")))
    eq("pool правка без note: вид и заметка прежние", (r.status, WRITES[-1][1]["kind"], WRITES[-1][1]["note"], WRITES[-1][1]["plan"]), (200, "pool", "по чекам", 4000))
    pl = next(x for x in json.loads(r.text)["book"]["budget"]["lines"] if x["id"] == pool_id)
    eq("pool в книге: kind pool, период 1, без следующего платежа, в группе auto", (pl["kind"], pl["period"], pl["next_due"], pl["group"], pl["plan_m"]), ("pool", 1, "", "auto", 4000))
    eq("старая строка «Гараж и ТО» без kind — тоже pool", fr._is_pool({"name": "Гараж и ТО"}), True)
    eq("образец: Хоз. нужды, Продукты, Коммуналка — группа home («Бытовые расходы»), все без даты", [(w[1]["name"], w[1]["kind"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "home"], [("Хоз. нужды", "pool"), ("Продукты", "pool"), ("Коммуналка", "pool")])
    eq("старые «Продукты» без kind — тоже без даты", fr._is_pool({"name": "Продукты"}), True)
    eq("образец: Билеты, Визы, Бензин — без даты; «Sim» и «Реклама» — группы", [(w[1]["name"], w[1]["kind"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and not w[1]["group"]], [("Билеты", "pool"), ("Визы", "pool"), ("Бензин", "pool")])
    eq("образец: Покупка, Пополнение — группа sim, без даты", [(w[1]["name"], w[1]["kind"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "sim"], [("Покупка", "pool"), ("Пополнение", "pool")])
    BUDGET.append(dict(_id="L22", month=M, name="Пополнение", plan=300, due=0, note="", kind="pool", ord=22, group="sim", period=4, next="2026-12-01"))
    bs = (await fr.build(M))["budget"]
    eq("подрасход Sim без даты: график не считается, итог группы sim", (bs["sim"], next((l["kind"], l["period"], l["next_due"]) for l in bs["lines"] if l["id"] == "L22")), ({"plan": 300, "fact": 0, "left": 300, "n": 1}, ("pool", 1, "")))
    eq("образец: Посты, Интеграция бота — группа ads", [(w[1]["name"], w[1]["cur"]) for w in WRITES if w[0] == "bset" and w[1]["month"] == "2026-11" and w[1]["group"] == "ads"], [("Посты", "AED"), ("Интеграция бота", "AED")])
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Посты", plan=1000, group="ads", cur="usd", period=2, next="2026-10-15")))
    ads_id = json.loads(r.text)["id"]
    eq("реклама в $: валюта USD, график принят", (r.status, WRITES[-1][1]["cur"], WRITES[-1][1]["period"], WRITES[-1][1]["next"]), (200, "USD", 2, "2026-10-15"))
    al = next(x for x in json.loads(r.text)["book"]["budget"]["lines"] if x["id"] == ads_id)
    eq("в книге: 1000 $ = 3670 AED по курсу 3.67, доля в месяц 1835, группа ads", (al["plan"], al["cur"], al["plan_aed"], al["plan_m"], al["group"]), (1000, "USD", 3670, 1835, "ads"))
    eq("итог «Рекламы» — доля в AED", json.loads(r.text)["book"]["budget"]["ads"], {"plan": 1835, "fact": 0, "left": 1835, "n": 1})
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id=ads_id, name="Посты", plan=1200)))
    eq("правка без cur — валюта прежняя", (r.status, WRITES[-1][1]["cur"], WRITES[-1][1]["plan"]), (200, "USD", 1200))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="X", plan=1, cur="eur")))
    eq("чужая валюта → 400", r.status, 400)
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month="2026-12", name="Бензин", plan=700, period=2, next="2026-12-10")))
    eq("новая «Бензин» без группы — pool, график не принимается", (r.status, WRITES[-1][1]["kind"], WRITES[-1][1]["period"], WRITES[-1][1]["next"]), (200, "pool", 1, ""))
    BUDGET.append(dict(_id="L30", month=M, name="Продукты", plan=6000, due=0, note="", kind="", ord=30))
    bh = (await fr.build(M))["budget"]
    eq("старая строка «Продукты» без group — в бытовых; итог группы", (next(l["group"] for l in bh["lines"] if l["name"] == "Продукты"), bh["home"]), ("home", dict(plan=6000, fact=0, left=6000, n=1)))
    BUDGET.append(dict(_id="L9", month=M, name="Парковка", plan=1500, due=0, note="", kind="", ord=9))
    ba = (await fr.build(M))["budget"]
    eq("старая строка «Парковка» без поля group — в авто; итог группы (с «Гаражом» 4000)", (next(l["group"] for l in ba["lines"] if l["name"] == "Парковка"), ba["auto"]), ("auto", dict(plan=5500, fact=0, left=5500, n=2)))
    BUDGET.append(dict(_id="L21", month=M, name="Орион Рент", plan=6000, due=0, note="", kind="", ord=21, group="car", period=3, next="2026-10-20"))
    bc = (await fr.build(M))["budget"]
    eq("рент машин: треть в car и в итог «Расходов на автомобили», в списке с графиком", (bc["car"], bc["auto"]["plan"], next((l["group"], l["kind"], l["next_due"]) for l in bc["lines"] if l["id"] == "L21")),
       ({"plan": 2000, "fact": 0, "left": 2000, "n": 1}, 7500, ("car", "", "2026-10-20")))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Другой Рент", plan=900, group="car")))
    eq("новый рент — группа car", (r.status, WRITES[-1][1]["group"], WRITES[-1][1]["kind"]), (200, "car", ""))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Аренда склад", plan=900, group="rent")))
    eq("новая строка в группе аренды", (r.status, WRITES[-1][1]["group"]), (200, "rent"))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="X", plan=1, group="zzz")))
    eq("чужая группа → 400", r.status, 400)
    print("— график платежей: раз в N месяцев от даты, следующий считается сам")
    eq("_add_months: 31 янв + 1 = 28 фев, + 3 = 30 апр", (fr._add_months("2026-01-31", 1), fr._add_months("2026-01-31", 3)), ("2026-02-28", "2026-04-30"))
    q = dict(period=3, next="2026-10-01")
    eq("сентябрь: платежа нет, следующий 1 окт", fr._schedule(q, "2026-09", "2026-09-10"), dict(period=3, next="2026-10-01", next_due="2026-10-01", due_in=False))
    eq("октябрь: платёж в месяце", fr._schedule(q, "2026-10", "2026-10-01")["due_in"], True)
    eq("2 октября: следующий уже 1 января", fr._schedule(q, "2026-10", "2026-10-02")["next_due"], "2027-01-01")
    eq("июль (раньше даты): платёж был 1 июля — в месяце", fr._schedule(q, "2026-07", "2026-09-10")["due_in"], True)
    eq("без даты — ежемесячный", fr._schedule(dict(period=1), "2026-09", "2026-09-10"), dict(period=1, next="", next_due="", due_in=True))
    eq("без даты, но раз в 3 — плана в месяце нет", fr._schedule(dict(period=3), "2026-09", "2026-09-10")["due_in"], False)
    BUDGET.append(dict(_id="L20", month=M, name="Аренда Силикон", plan=15500, due=0, note="", kind="", ord=20, group="rent", period=3, next="2026-10-01"))
    bq = (await fr.build(M))["budget"]
    lq = next(l for l in bq["lines"] if l["id"] == "L20")
    eq("квартальная аренда в сентябре: платёж 15500, в план месяца треть 5166.67, следующий 1 окт", (lq["plan"], lq["plan_m"], lq["next_due"], lq["due_in"]), (15500, 5166.67, "2026-10-01", False))
    eq("итог аренды: 32000 JVC + 900 склад + треть 5166.67", bq["rent"]["plan"], 38066.67)
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="L20", name="Аренда Силикон", plan=15500, period=6, next="2026-12-15")))
    eq("правка графика: period 6, next 15 дек", (r.status, WRITES[-1][1]["period"], WRITES[-1][1]["next"]), (200, 6, "2026-12-15"))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, id="L20", name="Аренда Силикон", plan=16000)))
    eq("правка без графика — график не теряется", (WRITES[-1][1]["period"], WRITES[-1][1]["next"]), (6, "2026-12-15"))
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Y", plan=1, period=99)))
    eq("период 99 → 400", r.status, 400)
    r = await raw(inner2["handle_budget_del"])(_req("DELETE", dict(id="L2")))
    eq("budget_del", (r.status, WRITES[-1]), (200, ("bdel", "L2")))
    bu2 = json.loads(r.text)["book"]["budget"]
    eq("записи удалённой статьи: вне плана 25 + 300 и в факте", (bu2["off_plan"], bu2["fact"]), (325, 4225))
    eq("зарплаты не статья: строка kind=salary не создаётся", all(w[1].get("kind") != "salary" for w in WRITES if w[0] == "bset"), True)
    r = await raw(inner2["handle_budget_set"])(_req("POST", dict(month=M, name="Аренда офис 2", plan=5000, group="rent", office=True)))
    eq("новый офис: kind office", (r.status, WRITES[-1][1]["kind"]), (200, "office"))
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
    r = await raw(inner["handle_month_set"])(_req("POST", dict(month=M, field="norm", value=-5)))
    eq("норма базе отрицательная → 400", r.status, 400)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — сервер собирает книгу верно")
    sys.exit(1 if fails else 0)

def _req(method, body):
    req = make_mocked_request(method, "/x")
    req._read_bytes = json.dumps(body).encode()
    return req

asyncio.run(main())
