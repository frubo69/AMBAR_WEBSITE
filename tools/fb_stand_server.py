"""Стенд книги учёта: настоящие finance_routes + finance_calc поверх базы в
памяти (июльские числа из Excel, разложенные на первые дни текущего месяца),
без авторизации и без Mongo. Отдаёт статику стенда из каталога, переданного
первым аргументом. Запуск: python3 tools/fb_stand_server.py <dir> [port]

Пишущие ручки меняют память — так экран проверяется настоящим путём:
запись → пересчёт → числа на месте."""
import asyncio, json, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.WARNING)
from datetime import datetime, timedelta, timezone
from aiohttp import web
import owner_auth
owner_auth.require_owner = lambda h: h            # стенд: без initData
import db, stock_value, stock_routes, config_staff, backdate
import finance_routes as fr

STATIC = sys.argv[1] if len(sys.argv) > 1 else "."
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8773
TODAY = fr._biz_day()
MONTH = TODAY[:7]
PREV = fr._prev_month(MONTH)
FX = json.load(open(os.path.join(os.path.dirname(__file__), "finance_july2026.json")))
ROWS = FX["rows"]

# ── база в памяти ────────────────────────────────────────────────────────────
ORDERS, DRIVER_DAYS, SUPPLIES, ENTRIES = [], [], [], []
FIN_DAYS, MONTHS, CHECK = {}, {}, {}
COST = {}
CATALOG = {}
COMMENTS = ["аренда", "бензин", "зарплата оператору", "ремонт машины", "связь", "реклама", "штраф"]
today_n = int(TODAY[8:10])
for r in ROWS:
    n = r["day"]
    if n > today_n:
        break
    day = f"{MONTH}-{n:02d}"
    ts = (datetime.strptime(day, "%Y-%m-%d").replace(hour=20, tzinfo=fr.DUBAI_TZ)
          .astimezone(timezone.utc).isoformat().replace("+00:00", ""))
    # наличные = сдали + расход водителя 300 (80 питание + 220 бензин)
    ORDERS.append(dict(order_id=f"o{n}", timestamp=ts, status="delivered", total=r["handed"] + 300,
                       tip=0, office_id="jbr"))
    if n % 3 == 0:
        ORDERS.append(dict(order_id=f"c{n}", timestamp=ts, status="delivered", total=700, tip=0,
                           payment_method="crypto", paid=True, office_id="marina"))
    if n % 4 == 0:
        ORDERS.append(dict(order_id=f"t{n}", timestamp=ts, status="delivered", total=250, tip=0,
                           payment_method="transfer", office_id="marina"))
    if n % 2: DRIVER_DAYS.append(dict(day=day, driver="Худоба", working=True, extras=[]))
    DRIVER_DAYS.append(dict(day=day, driver="Али", working=True,
                            extras=[dict(id=f"e{n}", amount=220, kind="fuel", status="approved")]))
    if r["ordered"]:
        pid = f"p{n}"; COST[pid] = float(r["ordered"]); CATALOG[pid] = {"id": pid, "name": "Товар", "price_full": 100}
        SUPPLIES.append(dict(supply_id=f"S{n}", day=day, status="done", items=[dict(id=pid, qty=1, asked=1, got={"jbr": 1})]))
    if n == 5:
        SUPPLIES.append(dict(supply_id="X5", day=day, kind="extra", base="Спиннейс", status="done",
                             items=[dict(id="p5", qty=2, asked=2)], buys={"p5": {"price": 410, "qty": 2}}))
    m = {}
    if n < today_n - 2:               # давние дни: раскладка подтверждена старшим
        m.update(aside=r["aside"], collected=r["collected"],
                 handed_fact=r["aside"] + r["collected"] + r["np_plus"], ok=True, ok_by="Старший")
    # два последних прошедших дня и сегодня — только предложение, ждут подтверждения
    if r["pay_b"] or r["pay_b_extra"]: m["pay"] = r["pay_b"] + r["pay_b_extra"]   # оплата одной суммой
    if r["extra_rp"]: m["extra_rp"] = r["extra_rp"]
    if n == 3: m["note"] = "пересчитали вдвоём"
    if m:
        FIN_DAYS[day] = dict(_id=day, **m)
    if r["expenses"]:
        parts = [r["expenses"]] if r["expenses"] < 2000 else [round(r["expenses"] * .6), r["expenses"] - round(r["expenses"] * .6)]
        for i, a in enumerate(parts):
            ENTRIES.append(dict(_id=f"a{n}{i}", day=day, book="rp", amount=a, comment=COMMENTS[(n + i) % len(COMMENTS)],
                                who="", by="Старший", at="t"))
    CHECK[day] = {"cash:jbr": {"done": True}} if n < today_n else {}
ENTRIES.append(dict(_id="np1", day=f"{MONTH}-04", book="np", amount=1000, comment="аванс", who="Владелец", by="Старший", at="t"))
MONTHS[PREV] = dict(_id=PREV, safe_b_open=30000, debt_b_open=105000, carry_np=38000,
                    safe_np_fact=41030, safe_b_fact=34500)
MONTHS[MONTH] = dict(_id=MONTH, storage=0)
# долг Б на начало прошлого месяца сложится так, чтобы на начало текущего вышло 110 198
# (как в таблице): в прошлом месяце ни заказов, ни оплат → долг переносится как есть.
MONTHS[PREV]["debt_b_open"] = 110198

async def orders_between(a, b): return [o for o in ORDERS if a <= o["timestamp"] < b]
async def get_driver_days_range(a, b): return [r for r in DRIVER_DAYS if a <= r["day"] <= b]
async def supplies_between(a, b): return [json.loads(json.dumps(s)) for s in SUPPLIES if a <= s["day"] <= b]
async def fin_days_get(a, b): return {k: dict(v) for k, v in FIN_DAYS.items() if a <= k <= b}
async def fin_entries_get(a, b): return [dict(e) for e in ENTRIES if a <= e["day"] <= b]
async def fin_month_get(m): return dict(MONTHS.get(m) or {})
async def fin_months_list(): return sorted(MONTHS)
async def checklist_get(d): return CHECK.get(d, {})
async def cost_map(): return COST
async def fin_day_set(day, fields, unset=None):
    d = FIN_DAYS.setdefault(day, {"_id": day}); d.update(fields)
    for k in (unset or []): d.pop(k, None)
async def fin_month_set(m, fields, unset=None):
    d = MONTHS.setdefault(m, {"_id": m}); d.update(fields)
    for k in (unset or []): d.pop(k, None)
async def fin_entry_add(doc): ENTRIES.append(doc)
async def fin_entry_get(eid): return next((e for e in ENTRIES if e["_id"] == eid), None)
async def fin_entry_del(eid):
    n = len(ENTRIES); ENTRIES[:] = [e for e in ENTRIES if e["_id"] != eid]; return len(ENTRIES) < n
async def notify(*a, **k): print("NOTIFY", a)
for n_, f in dict(orders_between=orders_between, get_driver_days_range=get_driver_days_range,
                  supplies_between=supplies_between, fin_days_get=fin_days_get,
                  fin_entries_get=fin_entries_get, fin_month_get=fin_month_get,
                  fin_months_list=fin_months_list, checklist_get=checklist_get,
                  fin_day_set=fin_day_set, fin_month_set=fin_month_set, fin_entry_add=fin_entry_add,
                  fin_entry_get=fin_entry_get, fin_entry_del=fin_entry_del).items():
    setattr(db, n_, f)
stock_value.cost_map = cost_map
stock_routes._catalog = lambda: CATALOG
config_staff.MEAL_WORKING, config_staff.MEAL_OFF = 80, 40
config_staff.drivers = lambda: [dict(name="Али", district="jbr")]
backdate.notify = notify

# ── бюджет, люди, зарплаты ──
BUDGET = [dict(_id="L1", month=MONTH, name="Зарплаты", plan=80000, due=0, note="", kind="salary", ord=0),
          dict(_id="L2", month=MONTH, name="Аренда офис", plan=26000, due=3, note="менеджеру, на 3 мес", kind="", ord=1),
          dict(_id="L3", month=MONTH, name="Аренда JVC", plan=31250, due=15, note="", kind="", ord=2),
          dict(_id="L4", month=MONTH, name="Аренда Бизнес Бей", plan=37500, due=5, note="", kind="", ord=3),
          dict(_id="L5", month=MONTH, name="Орион Рент", plan=20000, due=0, note="два рента", kind="", ord=4, group="car"),
          dict(_id="L6", month=MONTH, name="Билеты", plan=5600, due=0, note="", kind="", ord=5),
          dict(_id="L7", month=MONTH, name="Визы", plan=11000, due=0, note="5 чел", kind="", ord=6),
          dict(_id="L8", month=MONTH, name="Пополнение", plan=3500, due=0, note="", kind="pool", ord=7, group="sim"),
          dict(_id="L19", month=MONTH, name="Покупка", plan=0, due=0, note="", kind="pool", ord=18, group="sim"),
          dict(_id="L9", month=MONTH, name="Продукты", plan=6000, due=0, note="", kind="", ord=8),
          dict(_id="L10", month=MONTH, name="Бензин", plan=3000, due=0, note="", kind="", ord=9),
          dict(_id="L11", month=MONTH, name="Посты", plan=1000, due=0, note="", kind="", ord=10, group="ads", cur="USD"),
          dict(_id="L18", month=MONTH, name="Интеграция бота", plan=5000, due=0, note="", kind="", ord=17, group="ads", period=3, next="2026-11-01"),
          dict(_id="L12", month=MONTH, name="Гараж и ТО", plan=10000, due=0, note="Аслам, по чекам", kind="", ord=11),
          dict(_id="L13", month=MONTH, name="Парковка", plan=1500, due=0, note="", kind="", ord=12),
          dict(_id="L14", month=MONTH, name="Алексей Рент", plan=12000, due=0, note="", kind="", ord=13, group="car", period=3, next="2026-10-20"),
          dict(_id="L15", month=MONTH, name="Страховка/Пассинг", plan=0, due=0, note="", kind="", ord=14, group="auto"),
          dict(_id="L16", month=MONTH, name="Хоз. нужды", plan=1200, due=0, note="", kind="", ord=15, group="home"),
          dict(_id="L17", month=MONTH, name="Коммуналка", plan=0, due=0, note="", kind="", ord=16, group="home")]
LINE_BY = {"аренда": "L2", "бензин": "L10", "зарплата оператору": "L1", "ремонт машины": "L12", "связь": "L8", "реклама": "", "штраф": ""}
for e in ENTRIES:
    if e["book"] == "rp":
        e["line"] = LINE_BY.get(e["comment"], "")
        if e["comment"] == "зарплата оператору":
            e["kind"] = "salary"; e["who"] = "Умар"; e["comment"] = "Зарплата"; e["pay_month"] = MONTH
ENTRIES.append(dict(_id="in1", day=f"{MONTH}-02", book="in", amount=250, comment="перевод с крипты", who="", by="Старший", at="t"))
if today_n >= 8:
    ENTRIES.append(dict(_id="in2", day=f"{MONTH}-08", book="in", amount=340, comment="вернули депозит", who="", by="Старший", at="t"))
PEOPLE = [dict(_id="Макар", role="senior", manual=True, note="старший"), dict(_id="Слон", role="senior", manual=True)]
PAYM = [dict(_id=f"{PREV}|Макар", month=PREV, name="Макар", rate=1750, unit="month", cur="USD"),
        dict(_id=f"{PREV}|Слон", month=PREV, name="Слон", rate=1750, unit="month", cur="USD"),
        dict(_id=f"{PREV}|Али", month=PREV, name="Али", rate=110, unit="day", cur="AED"),
        dict(_id=f"{PREV}|Умар", month=PREV, name="Умар", rate=3000, unit="month", cur="AED"),
        dict(_id=f"{MONTH}|Умар", month=MONTH, name="Умар", days=22, note="")]
ITEMS = [dict(_id="i1", name="Али", kind="fine", amount=4040, per_month=1000, **{"from": PREV}, day=f"{PREV}-20", note="кр. свет", entry=""),
         dict(_id="i2", name="Макар", kind="advance", amount=2000, per_month=0, **{"from": MONTH}, day=f"{MONTH}-04", note="", entry="adv1"),
         dict(_id="i3", name="Умар", kind="bonus", amount=100, per_month=0, **{"from": MONTH}, day=f"{MONTH}-06", note="премия", entry=""),
         dict(_id="i4", name="Слон", kind="advance", amount=6450, per_month=0, **{"from": fr.pay.next_month(MONTH)}, day=f"{MONTH}-05", note="за следующий месяц", entry="adv2")]
ENTRIES.append(dict(_id="adv1", day=f"{MONTH}-04", book="rp", amount=2000, comment="Аванс", who="Макар", line="L1", kind="advance", item="i2", by="Старший", at="t"))
ENTRIES.append(dict(_id="adv2", day=f"{MONTH}-05", book="rp", amount=6450, comment="за следующий месяц", who="Слон", line="L1", kind="advance", item="i4", by="Старший", at="t"))
SHIFTS = [(f"{MONTH}-{n:02d}", d) for n in range(1, today_n + 1) for d in ("jvc", "bbay", "tecom")]
async def fin_budget_get(m): return [dict(l) for l in BUDGET if l["month"] == m]
async def fin_budget_line_get(lid): return next((dict(l) for l in BUDGET if l["_id"] == lid), None)
async def fin_budget_set(doc): BUDGET[:] = [l for l in BUDGET if l["_id"] != doc["_id"]]; BUDGET.append(dict(doc))
async def fin_budget_del(lid): BUDGET[:] = [l for l in BUDGET if l["_id"] != lid]; return True
async def fin_people_get(): return [dict(p) for p in PEOPLE]
async def fin_person_set(name, fields, unset=None):
    d = next((p for p in PEOPLE if p["_id"] == name), None)
    if d is None: d = {"_id": name}; PEOPLE.append(d)
    d.update(fields)
async def fin_pay_months_upto(m): return sorted([dict(d) for d in PAYM if d["month"] <= m], key=lambda d: d["month"])
async def fin_pay_month_set(m, name, fields, unset=None):
    d = next((x for x in PAYM if x["_id"] == f"{m}|{name}"), None)
    if d is None: d = {"_id": f"{m}|{name}", "month": m, "name": name}; PAYM.append(d)
    d.update(fields)
    for k in (unset or []): d.pop(k, None)
async def fin_pay_items_get(): return [dict(i) for i in ITEMS]
async def fin_pay_item_add(doc): ITEMS.append(dict(doc))
async def fin_pay_item_get(iid): return next((dict(i) for i in ITEMS if i["_id"] == iid), None)
async def fin_pay_item_del(iid): ITEMS[:] = [i for i in ITEMS if i["_id"] != iid]; return True
async def shift_days_worked(a, b): return [x for x in SHIFTS if a <= x[0] <= b]
async def fin_entries_where(q): return [dict(e) for e in ENTRIES if all(e.get(k) == v for k, v in q.items())]
async def fin_carry_invalidate(m):
    for k, d in MONTHS.items():
        if k >= m: d.pop('carry_cache', None)
for n_, f in dict(fin_budget_get=fin_budget_get, fin_budget_line_get=fin_budget_line_get, fin_budget_set=fin_budget_set,
                  fin_budget_del=fin_budget_del, fin_people_get=fin_people_get, fin_person_set=fin_person_set,
                  fin_pay_months_upto=fin_pay_months_upto, fin_pay_month_set=fin_pay_month_set,
                  fin_pay_items_get=fin_pay_items_get, fin_pay_item_add=fin_pay_item_add, fin_pay_item_get=fin_pay_item_get,
                  fin_pay_item_del=fin_pay_item_del, shift_days_worked=shift_days_worked,
                  fin_entries_where=fin_entries_where, fin_carry_invalidate=fin_carry_invalidate).items():
    setattr(db, n_, f)
import types
_rates = types.ModuleType("rates")
async def _get_rates(force=False): return {"rates": [{"code": "USD", "aed": 3.6725, "cash_aed": 3.67}]}
_rates.get_rates = _get_rates
sys.modules["rates"] = _rates
config_staff.SENIOR_OPERATORS = [{"id": "parviz", "name": "Парвиз", "telegram_id": 1}]
config_staff.operators = lambda: [dict(name="Парвиз", senior=True, districts=["jvc", "bbay", "tecom"]),
                                  dict(name="Умар", senior=False, districts=["jvc", "tecom"]),
                                  dict(name="Фарух", senior=False, districts=["bbay"])]
config_staff.drivers = lambda: [dict(name="Али", district="jbr"), dict(name="Худоба", district="jvc")]
config_staff.driver_names = lambda: ["Али", "Худоба"]
config_staff.operator_names = lambda: ["Умар", "Фарух", "Парвиз"]

async def static(request):
    path = request.match_info.get("path") or "stand.html"
    full = os.path.join(STATIC, path)
    if not os.path.isfile(full):
        return web.Response(status=404, text="no " + path)
    return web.FileResponse(full, headers={"Cache-Control": "no-cache"})

app = web.Application()
fr.setup(app)
app.router.add_get("/", static)
app.router.add_get("/{path:.+}", static)
print(f"stand: http://127.0.0.1:{PORT}/stand.html  today={TODAY}")
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
