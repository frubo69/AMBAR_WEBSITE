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
    DRIVER_DAYS.append(dict(day=day, driver="Али", working=True,
                            extras=[dict(id=f"e{n}", amount=220, kind="fuel", status="approved")]))
    if r["ordered"]:
        pid = f"p{n}"; COST[pid] = float(r["ordered"]); CATALOG[pid] = {"id": pid, "name": "Товар", "price_full": 100}
        SUPPLIES.append(dict(supply_id=f"S{n}", day=day, status="done", items=[dict(id=pid, qty=1, asked=1, got={"jbr": 1})]))
    if n == 5:
        SUPPLIES.append(dict(supply_id="X5", day=day, kind="extra", base="Спиннейс", status="done",
                             items=[dict(id="p5", qty=2, asked=2)], buys={"p5": {"price": 410, "qty": 2}}))
    m = {}
    if n < today_n:                   # сегодня ещё не разложено
        m.update(aside=r["aside"], collected=r["collected"],
                 handed_fact=r["aside"] + r["collected"] + r["np_plus"])
    if r["pay_b"]: m["pay_b"] = r["pay_b"]
    if r["pay_b_extra"]: m["pay_b_extra"] = r["pay_b_extra"]
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
