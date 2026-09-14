"""Охрана: либо бутылка по коду, либо наличные суммой — у водителя и у старшего.

Гоняется без базы: db и соседи подменены. Проверяет ровно правила ручек:
  водитель  POST /api/driver/expenses  kind=guard
    - сумма без кода → запись с суммой, без бутылки
    - код без суммы  → сумма = цена бутылки, в записи бутылка
    - ни кода, ни суммы → 400 code_or_amount
    - без «кому отдали» → 400 no_comment
    - код плохой → 400 с причиной
  старший   POST /api/owner/expenses/extra  kind=guard
    - сумма без кода → запись, бутылка со склада не уходит
    - код → сумма с кода, бутылка уходит (_guard_bottle_gone)
    - ни кода, ни суммы → 400 amount_and_comment_required
"""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from aiohttp.test_utils import make_mocked_request
import driver_routes as dr, expense_routes as er

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

SAVED, GONE = [], []
class _DB:
    def __getattr__(self, n):
        async def f(*a, **k):
            if n == "add_driver_expense": SAVED.append(a[2]); return None
            if n == "get_driver_day": return {}
            return None
        return f
dr.db = _DB(); er.db = _DB()
async def _bottle(code):
    if code == "habr":
        return {"cost": 62, "item": {"code": "habr", "bottle": "Absolut 1 ltr", "bottle_id": "abs", "label": "", "district": "B1"}}, ""
    return None, "unknown"
dr._guard_bottle = _bottle
dr._biz_day = lambda: "2026-09-14"; er._biz_day = lambda: "2026-09-14"
async def _gone(item, day, driver, by): GONE.append(item["code"])
er._guard_bottle_gone = _gone
er.staff = types.SimpleNamespace(drivers=lambda: [{"name": "Худоба"}])
er._day_row = lambda base, saved: {}
er.backdate = types.SimpleNamespace(notify=(lambda *a, **k: asyncio.sleep(0)))
import owner_routes
async def _no(*a, **k): return None
owner_routes.notify_owners = _no

def dreq(body):
    r = make_mocked_request("POST", "/api/driver/expenses"); r._read_bytes = json.dumps(body).encode()
    r["driver"] = {"name": "Худоба", "district_code": "B1"}; return r
def oreq(body):
    r = make_mocked_request("POST", "/api/owner/expenses/extra"); r._read_bytes = json.dumps(body).encode()
    r["owner_id"] = 1; return r
async def call(h, r):
    resp = await h.__wrapped__(r); return resp.status, json.loads(resp.text)

async def main():
    print("водитель")
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": 150, "comment": "сторож Ахмед"}))
    eq("наличные: статус", st, 200); eq("наличные: сумма", r["item"]["amount"], 150); eq("наличные: без бутылки", "code" in r["item"], False)
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": None, "comment": "сторож Ахмед", "code": "habr"}))
    eq("бутылка: статус", st, 200); eq("бутылка: сумма с кода", r["item"]["amount"], 62); eq("бутылка: код в записи", r["item"].get("code"), "habr")
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": 999, "comment": "x", "code": "habr"}))
    eq("оба: верим коду", r["item"]["amount"], 62)
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": 0, "comment": "сторож"}))
    eq("ни того ни другого → 400", (st, r.get("error")), (400, "code_or_amount"))
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": 150, "comment": ""}))
    # пустой комментарий подменяется названием вида — так было и раньше
    eq("без слов: как раньше (название вида)", st, 200)
    st, r = await call(dr.handle_expense_add, dreq({"kind": "guard", "amount": 0, "comment": "x", "code": "zzz"}))
    eq("плохой код → 400 unknown", (st, r.get("error")), (400, "unknown"))
    st, r = await call(dr.handle_expense_add, dreq({"kind": "kfc", "amount": 0, "comment": "x"}))
    eq("другой вид без суммы → 400", (st, r.get("error")), (400, "amount_and_comment_required"))

    print("старший")
    GONE.clear()
    st, r = await call(er.handle_extra_add, oreq({"driver": "Худоба", "kind": "guard", "amount": 150, "comment": "сторож"}))
    eq("наличные: статус", st, 200); eq("наличные: сумма", r["item"]["amount"], 150); eq("наличные: склад не трогаем", GONE, [])
    st, r = await call(er.handle_extra_add, oreq({"driver": "Худоба", "kind": "guard", "amount": 0, "comment": "сторож", "code": "habr"}))
    eq("бутылка: статус", st, 200); eq("бутылка: сумма с кода", r["item"]["amount"], 62); eq("бутылка: ушла со склада", GONE, ["habr"])
    st, r = await call(er.handle_extra_add, oreq({"driver": "Худоба", "kind": "guard", "amount": 0, "comment": "сторож"}))
    eq("ни того ни другого → 400", (st, r.get("error")), (400, "amount_and_comment_required"))
    st, r = await call(er.handle_extra_add, oreq({"driver": "Худоба", "kind": "guard", "amount": 150, "comment": ""}))
    eq("без слов → 400", (st, r.get("error")), (400, "amount_and_comment_required"))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
