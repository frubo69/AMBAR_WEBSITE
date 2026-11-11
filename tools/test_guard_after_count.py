"""Бутылка охране, согласованная ПОСЛЕ снимка ревизии, вычиталась со склада
дважды (разбор ревизии B4 за учётный день 9 ноя 2026, найдено 11 ноя).

Что было. Водитель отдал охране полкоробки Corona 9-го — расход ждёт решения
старшего, со склада ничего не уходит. Ревизия 10-го в 07:27 камерой бутылку
уже не видела: снимок пересчёта лёг без неё (4,5), а в строках стояла
«недостача 0,5». Старший согласовал расход в 07:41 — списание легло временем
решения, ПОСЛЕ снимка, и остаток района (снимок − списания после counted_at)
вычел полкоробки второй раз: числилось 4,0 при 4,5 на полке.

Что стало. Списание датируется временем самого расхода — моментом, когда
бутылка ушла с полки (expense_routes._guard_bottle_gone); день записи — день
расхода, как был. А в строках идущей ревизии бутылка, отданная охране и
ждущая решения, — зачёт, не недостача (stock_routes._audit_lines, поле guard,
входит в went).

Сценарии на mongomock с настоящими ручками (handle_expense_add водителя,
handle_extra_decide старшего, audit_start/audit_scan/audit_finish,
handle_audit_reopen, _audit_lines, _district_base):
  A. расход 9-го ждёт → ревизия 10-го 07:20–07:27, камера 4,5 из 5 → строка:
     зачёт «охране» 0,5, недостачи нет → снимок временем последнего скана, 4,5,
     ревизия закрыта → согласование 07:41 → списание временем и днём расхода →
     числится 4,5, не 4,0 → возобновить и завершить заново → 4,5.
  B. без снимка между расходом и решением: 5 → 4,5 ровно один раз.
  C. расход после снимка: снимок видел все 5 → согласование → 4,5 (один раз).
  D. согласовали по ходу ревизии, после последнего скана: 4,5.
  E. отклонённый расход зачётом не считается: недостача 0,5, склад не тронут.

    python3 tools/test_guard_after_count.py
"""
import asyncio, json, os, sys, types
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as SR, stock_value as SV                    # noqa: E402
import driver_routes as DR, expense_routes as ER                    # noqa: E402
import owner_routes, pay_notify, fines_auto                         # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

U = timezone.utc
D = "2026-11-09"                      # учётный день: расход, ревизия и решение — все в нём (граница 10:00 Дубай)
РАЙОН, ПИВО, ВОДИТЕЛЬ = "alguses", "p43", "Тест-водитель 1"
def T(d, h, m): return datetime(2026, 11, d, h, m, tzinfo=U)      # UTC; Дубай = +4
T0      = T(8, 6, 0)        # прежний пересчёт района: 5 коробок Corona
ОТДАЛ   = T(9, 16, 0)       # 9 ноя 20:00 Дубай — водитель отдал охране полкоробки
СКАН_1  = T(10, 3, 20)      # 10 ноя 07:20 Дубай — первый скан ревизии
СКАН_N  = T(10, 3, 27)      # 07:27 — последний скан, им ложится снимок
РЕШЕНИЕ = T(10, 3, 41)      # 07:41 — старший согласовал расход

NOW = {"t": ОТДАЛ}
class _DT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW["t"] if tz else NOW["t"].replace(tzinfo=None)
for _m in (DR, ER, SR):
    _m.datetime = _DT
SR._biz_day = DR._biz_day = ER._biz_day = lambda *a, **k: D

async def cost_map(): return {ПИВО: 142.0}
SV.cost_map = cost_map
async def _nop(*a, **k): return None
# Всё, что ходит наружу: телеграм владельцу и водителю, отметка задним числом, штраф.
owner_routes.notify_owners = _nop
pay_notify.tell_safe = _nop
fines_auto.propose = _nop
ER.staff = types.SimpleNamespace(drivers=lambda: [{"name": ВОДИТЕЛЬ}],
                                 all_drivers=lambda: [{"name": ВОДИТЕЛЬ}], DRIVER_IDS={})
ER._day_row = lambda base, saved: {}
ER.backdate = types.SimpleNamespace(notify=_nop)
ER._tell_driver = _nop
SR.backdate = types.SimpleNamespace(notify=_nop)

МЕНЯ = {"name": ВОДИТЕЛЬ, "district": РАЙОН, "district_code": "B4", "telegram_id": 0}

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

def req(path, body, match=None, **поля):
    r = make_mocked_request("POST", path, match_info=match or {})
    r._read_bytes = json.dumps(body).encode()
    for k, v in поля.items(): r[k] = v
    return r

async def call(h, r):
    resp = await unwrap(h)(r); return resp.status, json.loads(resp.text)

def ts(v):
    """Момент из базы — строкой до минуты, в UTC."""
    if v is None: return None
    v = v if v.tzinfo else v.replace(tzinfo=U)
    return v.astimezone(U).isoformat()[:16]


async def мир(имя):
    db._db = AsyncMongoMockClient()[f"ambar_guard_after_count_{имя}"]
    SR.base_drop()
    cat = SR._catalog()
    await db.save_stock_count(РАЙОН, "2026-11-08", {
        "district": РАЙОН, "day": "2026-11-08", "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": ПИВО, "name": cat[ПИВО]["name"], "price": 150, "unit": 24, "actual": 5, "counted": True}]})
    for i in range(10):                      # десять наклеек по полкоробки — 5 коробок
        await db.qr_add(f"G{i}", ПИВО, cat[ПИВО]["name"], РАЙОН, 1, T0 + timedelta(minutes=5), f"G{i}#1",
                        extra={"src": "cover", "qty": 0.5, "origin": РАЙОН})
    await db._db.driver_days.insert_one({"day": D, "driver": ВОДИТЕЛЬ, "shift_open_at": T0.isoformat()})
    return cat

async def числится():
    SR.base_drop(); return (await SR._district_base(D))[РАЙОН]["have_exact"].get(ПИВО, 0)
async def кодов():
    return (await db.qr_by_product_district(РАЙОН)).get(ПИВО, 0)
async def журнал(**q):
    return await db._db.writeoffs.find_one(q) or {}

async def отдал(code="G0", когда=ОТДАЛ):
    NOW["t"] = когда
    st, r = await call(DR.handle_expense_add,
                       req("/api/driver/expenses", {"kind": "guard", "comment": "Менеджер", "code": code}, driver=МЕНЯ))
    assert st == 200, r
    return r["item"]["id"]

async def решил(eid, action="approve", когда=РЕШЕНИЕ):
    NOW["t"] = когда
    return await call(ER.handle_extra_decide,
                      req(f"/api/owner/expenses/extra/{eid}/{action}", {"day": D, "driver": ВОДИТЕЛЬ},
                          match={"item_id": eid, "action": action}, owner_id=1))

async def ревизия(коды, первый=СКАН_1, последний=СКАН_N):
    """Старт и сканы: первый код в `первый`, последний в `последний`, остальные между."""
    NOW["t"] = первый - timedelta(minutes=1)
    st, _ = await SR.audit_start(РАЙОН, D, 1, "Тест")
    assert st == 200
    n = len(коды)
    for i, code in enumerate(коды):
        NOW["t"] = первый + (последний - первый) * i / max(1, n - 1)
        r = await SR.audit_scan(РАЙОН, D, code, 1, "Тест")
        assert r.get("verdict") == "ok", r

async def строка():
    SR.base_drop()
    lines, _ = await SR._audit_lines(РАЙОН, D)
    return next(x for x in lines if x["id"] == ПИВО)

async def снимок():
    c = await db.get_stock_count(РАЙОН, D) or {}
    return (str(c.get("counted_at") or "")[:16],
            next((l["actual"] for l in c.get("lines") or [] if l["id"] == ПИВО), None))


async def main():
    print("── A. расход ждёт → снимок ревизии без бутылки → согласование ────")
    cat = await мир("a")
    eq("код пива несёт полкоробки", SR.code_units(await db.qr_get("G0"), cat[ПИВО]), 0.5)
    eq("старт: числится 5, кодов на 5", (await числится(), await кодов()), (5, 5))
    eid = await отдал("G0")
    eq("расход ждёт решения: склад и код не тронуты", (await числится(), await кодов()), (5, 5))
    eq("db.guard_pending_codes: эта бутылка, этот район, момент расхода",
       [(x["code"], x["district"], x["at"][:16]) for x in await db.guard_pending_codes()],
       [("G0", РАЙОН, "2026-11-09T16:00")])
    await ревизия([f"G{i}" for i in range(1, 10)])          # камера видела девять наклеек — 4,5
    l = await строка()
    eq("СТРОКА РЕВИЗИИ: числится 5, камера 4,5, отдано охране 0,5 — НЕДОСТАЧИ НЕТ",
       (l["expected"], l["actual"], l.get("guard", 0), l["went"], l["gone"], l["diff"]), (5, 4.5, 0.5, 0.5, 0.5, 0))
    NOW["t"] = СКАН_N + timedelta(minutes=1)
    st, _ = await SR.audit_finish(РАЙОН, D, 1, "Тест")
    a = await db.audit_get(РАЙОН, D) or {}
    eq("завершена: недостачи нет, закрыта сама", (st, a.get("short"), bool(a.get("closed_at"))), (200, None, True))
    eq("в сохранённых строках — отдано охране 0,5",
       next(x.get("guard") for x in a.get("lines") or [] if x["id"] == ПИВО), 0.5)
    eq("СНИМОК: временем последнего скана, 4,5", await снимок(), ("2026-11-10T03:27", 4.5))
    eq("числится 4,5 — как увидела камера", await числится(), 4.5)
    st, r = await решил(eid)
    eq("согласовано", (st, r.get("ok")), (200, True))
    w = await журнал(expense=eid)
    eq("СПИСАНИЕ ДАТИРОВАНО РАСХОДОМ, А НЕ РЕШЕНИЕМ; день — день расхода",
       (ts(w.get("at")), ts(w.get("decided_at")), w.get("day"), w.get("qty"), w.get("kind"), w.get("state")),
       ("2026-11-09T16:00", "2026-11-10T03:41", D, 0.5, "охрана", "ok"))
    eq("ЧИСЛИТСЯ 4,5, НЕ 4,0: снимок бутылку уже не видел, второй раз не вычли",
       (await числится(), await кодов()), (4.5, 4.5))
    eq("код помечен списанным", (await db.qr_get("G0")).get("status"), "written")
    rows = await db.writeoff_list(day=D)
    eq("лента списаний дня: одна запись, по дню расхода", (len(rows), rows[0].get("day")), (1, D))
    row = SR._wo_row(rows[0], cat)
    eq("строка списания STAR: момент — расход, день — расход, 0,5",
       (row["at"][:16], row["day"], row["qty"], row["decided_at"][:16]), ("2026-11-09T16:00", D, 0.5, "2026-11-10T03:41"))
    eq("«Расходы смены», удержания: без удержаний пусто, не падает", await ER._held(D, D), [])
    eq("«мои списания» водителя видят запись", [m.get("qty") for m in await db.writeoff_list(since=T0, by=ВОДИТЕЛЬ)], [0.5])
    eq("списания после прежнего пересчёта: полкоробки один раз", await db.writeoff_since({РАЙОН: T0}), {РАЙОН: {ПИВО: 0.5}})
    eq("списания после снимка ревизии: ничего", await db.writeoff_since({РАЙОН: СКАН_N}), {})

    print("   возобновили ревизию и завершили заново")
    NOW["t"] = T(10, 3, 50)
    st, r = await call(SR.handle_audit_reopen, req("/api/owner/stock/audit/reopen", {"district": РАЙОН, "day": D}, owner_id=1))
    eq("ревизия возобновлена, снимок стёрт", (st, r.get("ok"), await db.get_stock_count(РАЙОН, D)), (200, True, None))
    l = await строка()
    eq("строки заново: числится 4,5 (списание в ленте ДО снимка), камера 4,5, зачёт уже не нужен",
       (l["expected"], l["actual"], l.get("guard", 0), l["diff"]), (4.5, 4.5, 0, 0))
    NOW["t"] = T(10, 3, 51)
    st, _ = await SR.audit_finish(РАЙОН, D, 1, "Тест")
    eq("завершена заново: снимок 4,5 тем же последним сканом, числится 4,5",
       (st, await снимок(), await числится()), (200, ("2026-11-10T03:27", 4.5), 4.5))

    print("── B. без снимка между расходом и решением ───────────────────")
    cat = await мир("b")
    eid = await отдал("G0")
    st, r = await решил(eid)
    eq("согласовано", (st, r.get("ok")), (200, True))
    eq("5 → 4,5 — ровно один раз", (await числится(), await кодов()), (4.5, 4.5))
    w = await журнал(expense=eid)
    eq("списание временем расхода, днём расхода", (ts(w.get("at")), w.get("day")), ("2026-11-09T16:00", D))
    eq("списания после прежнего пересчёта: полкоробки", await db.writeoff_since({РАЙОН: T0}), {РАЙОН: {ПИВО: 0.5}})

    print("── C. расход после снимка: вычитается один раз, как было ──────")
    cat = await мир("c")
    await ревизия([f"G{i}" for i in range(10)])             # камера видела все пять
    NOW["t"] = СКАН_N + timedelta(minutes=1)
    st, _ = await SR.audit_finish(РАЙОН, D, 1, "Тест")
    eq("снимок: все 5", (st, await снимок()), (200, ("2026-11-10T03:27", 5)))
    eid = await отдал("G0", когда=T(10, 5, 0))                # 09:00 Дубай, тот же учётный день
    st, r = await решил(eid, когда=T(10, 5, 30))
    eq("5 → 4,5: списание после снимка вычтено один раз", (st, await числится(), await кодов()), (200, 4.5, 4.5))
    eq("списание временем расхода — после снимка", ts((await журнал(expense=eid)).get("at")), "2026-11-10T05:00")

    print("── D. согласовали по ходу ревизии, после последнего скана ─────")
    cat = await мир("d")
    eid = await отдал("G0")
    await ревизия([f"G{i}" for i in range(1, 10)])
    st, r = await решил(eid, когда=T(10, 3, 29))              # до «Завершить»
    eq("согласовано, списание временем расхода", (st, ts((await журнал(expense=eid)).get("at"))), (200, "2026-11-09T16:00"))
    l = await строка()
    eq("строки: числится 4,5 (списание уже в ленте), камера 4,5, недостачи нет",
       (l["expected"], l["actual"], l.get("guard", 0), l["diff"]), (4.5, 4.5, 0, 0))
    NOW["t"] = T(10, 3, 30)
    st, _ = await SR.audit_finish(РАЙОН, D, 1, "Тест")
    eq("снимок 4,5, числится 4,5", (st, await снимок(), await числится()), (200, ("2026-11-10T03:27", 4.5), 4.5))

    print("── E. отклонённый расход зачётом не считается ─────────────────")
    cat = await мир("e")
    eid = await отдал("G0")
    await ревизия([f"G{i}" for i in range(1, 10)])
    l = await строка()
    eq("пока ждёт: зачёт 0,5, недостачи нет", (l.get("guard", 0), l["diff"]), (0.5, 0))
    st, r = await решил(eid, "reject", когда=T(10, 3, 28))
    eq("отклонено", st, 200)
    l = await строка()
    eq("отклонили — охране не отдавали, бутылки нет: недостача 0,5",
       (l.get("guard", 0), l["diff"], l["actual"], l["expected"]), (0, 0.5, 4.5, 5))
    eq("склад и код не тронуты", (await числится(), await кодов(), await журнал(expense=eid)), (5, 5, {}))

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
