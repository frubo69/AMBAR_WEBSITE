"""Закрытие смены водителем: решение старшего по расходам не требуется —
заполненные (в том числе «на согласовании» и отклонённые) считаются ответом;
закрытие дня оператором по-прежнему обязательно (владелец, 13 и 15 сен 2026).
Без базы."""
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
D, DAY = {}, {"jvc": {"closed_at": "x"}}
async def gdd(day, name): return dict(D)
async def sdd(day, name, fields): D.update(fields)
async def sfd(day): return dict(DAY)
async def none(*a, **k): return []
INTAKE = []
REAL_INTAKE = dr._intake_left          # настоящая — для проверки отбора ниже
async def intake(me): return list(INTAKE)
dr.db.get_driver_day = gdd; dr.db.save_driver_day = sdd; dr.db.shifts_for_day = sfd; dr._in_route = none; dr._intake_left = intake
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
    eq("расходы на согласовании, день закрыт оператором → закрыть можно", await close(), (200, "ок"))
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [
        {"id": "a", "kind": "fuel", "amount": 100, "status": "rejected"}], "no_expense": {"wash": True, "parking": True}})
    eq("отклонённый расход тоже считается ответом", await close(), (200, "ок"))
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [], "no_expense": {"wash": True}})
    eq("не ответил про бензин и парковку → нельзя", await close(), (409, "expenses_left"))
    DAY.clear()
    D.clear(); D.update({"working": True, "shift_open_at": "x", "extras": [], "no_expense": {"fuel": True, "wash": True, "parking": True}})
    eq("оператор не закрыл день → нельзя", await close(), (409, "day_open"))
    DAY.update({"jvc": {"closed_at": "x"}})
    INTAKE.append({"sid": "S1", "district": "jvc", "code": "JVC", "left": 5})
    eq("взятая приёмка не завершена → нельзя (16 сен 2026)", await close(), (409, "intake_open"))
    INTAKE.clear()
    eq("приёмка завершена → можно", await close(), (200, "ок"))
    # что считается незавершённой приёмкой
    SUPS = [{"_id": "S1", "status": "open",
             "items": [{"id": "gin", "by_district": {"jvc": 12, "bbay": 3}, "got": {"jvc": 7, "bbay": 0}}],
             "tasks": {"jvc": {"driver": "Худоба", "started_at": "x"},
                       "bbay": {"driver": "Фарух"}}},
            {"_id": "S2", "status": "open", "items": [], "tasks": {"jvc": {"driver": "Худоба", "noscan_at": "x"}}},
            {"_id": "S3", "status": "open", "items": [], "tasks": {"jvc": {"driver": "Худоба", "done_at": "x"}}},
            {"_id": "S4", "status": "open", "items": [], "tasks": {"jvc": {"driver": "Худоба", "cancelled_at": "x"}}}]
    async def sups(limit=10): return list(SUPS)
    dr.db.supplies_with_open_tasks = sups
    got = await REAL_INTAKE({"name": "Худоба"})
    eq("своя начатая — в списке с остатком; чужая, без сканирования, закрытая, отменённая — нет",
       [(x["sid"], x["code"], x["left"], x["need"], x["started"]) for x in got], [("S1", "B1", 5, 12, True)])
    eq("тест-водителю приёмок нет", await REAL_INTAKE({"name": "Тест", "test": True}), [])
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
