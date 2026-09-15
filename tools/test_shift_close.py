"""Закрытие смены водителем (владелец, 15 сен 2026): заполненных расходов
достаточно — без решения старшего и без закрытия дня оператором. Без базы."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from aiohttp.test_utils import make_mocked_request
import driver_routes as dr
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D = {}
async def gdd(day, name): return dict(D)
async def sdd(day, name, fields): D.update(fields)
async def sfd(day): return {}                                   # оператор день НЕ закрыл
async def none(*a, **k): return []
dr.db.get_driver_day = gdd; dr.db.save_driver_day = sdd; dr.db.shifts_for_day = sfd; dr._in_route = none
h = dr.handle_shift_close
while hasattr(h, "__wrapped__"): h = h.__wrapped__
def req():
    r = make_mocked_request("POST", "/x"); r["driver"] = {"name": "Худоба", "district": "jvc"}; return r
async def close():
    resp = await h(req()); return resp.status, json.loads(resp.text).get("error") or "ок"
async def main():
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [
        {"id": "a", "kind": "fuel", "amount": 100, "status": "pending"},
        {"id": "b", "kind": "wash", "amount": 50, "status": "pending"}], "no_expense": {"parking": True}})
    eq("расходы на согласовании, день оператором не закрыт → закрыть можно", await close(), (200, "ок"))
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [
        {"id": "a", "kind": "fuel", "amount": 100, "status": "rejected"}], "no_expense": {"wash": True, "parking": True}})
    eq("отклонённый расход тоже считается ответом", await close(), (200, "ок"))
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [], "no_expense": {"wash": True}})
    eq("не ответил про бензин и парковку → нельзя", await close(), (409, "expenses_left"))
    D.clear(); D.update({"working": True, "shift_open_at": None})
    eq("смена не открыта → нельзя", await close(), (409, "not_open"))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
