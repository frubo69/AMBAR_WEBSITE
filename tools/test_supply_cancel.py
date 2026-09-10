"""Отмена заявки по районам (supply_routes.handle_cancel) на подменённой базе.
Запуск: python3 tools/test_supply_cancel.py"""
import asyncio, os, sys, json, logging, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from aiohttp.test_utils import make_mocked_request
import db, supply_routes as sr

DOCS = {}
TOLD = []
async def supply_get(sid): return copy.deepcopy(DOCS.get(sid))
async def supply_save(doc): DOCS[doc["_id"]] = copy.deepcopy(doc)
async def supplies_with_open_tasks(limit=12): return [copy.deepcopy(d) for d in DOCS.values() if d["status"] == "open"]
async def _cancel_tell(drivers, whole=True): TOLD.append((whole, list(drivers)))
db.supply_get, db.supply_save, db.supplies_with_open_tasks = supply_get, supply_save, supplies_with_open_tasks
sr._cancel_tell = _cancel_tell

def task(driver="", got=0, done=None):
    return {"qty": 10, "positions": 1, "scanned": got, "driver": driver, "driver_id": 0,
            "claimed_at": None, "started_at": None, "done_at": done, "last_at": None,
            "undo": 0, "note": "", "gaps": [], "flags": []}
def fresh():
    DOCS.clear(); TOLD.clear()
    DOCS["S1"] = {"_id": "S1", "status": "open", "day": "2026-09-11", "at": "t", "kind": "main",
                  "items": [{"id": "gin", "name": "Джин", "asked": 30, "qty": 30, "scanned": 0,
                             "by_district": {"a": 10, "b": 10, "c": 10}, "got": {"a": 0, "b": 4, "c": 10}}],
                  "tasks": {"a": task(), "b": task("Али", got=4), "c": task("Умар", got=10, done="d")},
                  "total_qty": 30, "asked_qty": 30, "gap_qty": 0}

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<62} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

def raw(h):
    import inspect
    return inspect.getclosurevars(h).nonlocals.get("handler") or h
async def cancel(body):
    req = make_mocked_request("POST", "/x", match_info={"sid": "S1"})
    req._read_bytes = json.dumps(body).encode()
    r = await raw(sr.handle_cancel)(req)
    return r.status, json.loads(r.text)

async def main():
    print("— один район без принятого: задача отменена, заявка открыта")
    fresh()
    st, r = await cancel({"districts": ["a"], "as": "Ст"})
    eq("200, cancelled=[a], статус open", (st, r["cancelled"], r["status"]), (200, ["a"], "open"))
    eq("задача a: cancelled_at, водителя нет", (bool(DOCS["S1"]["tasks"]["a"]["cancelled_at"]), DOCS["S1"]["tasks"]["a"].get("driver")), (True, None))
    eq("водителям ничего (район был свободен)", TOLD, [(False, [])])
    print("— район с принятым: без force 409, с force — снят с водителя, ему сказали")
    st, r = await cancel({"districts": ["b"]})
    eq("409 already_taken took=4", (st, r.get("error"), r.get("took")), (409, "already_taken", 4))
    st, r = await cancel({"districts": ["b"], "force": True, "as": "Ст"})
    eq("200; статус done (c принят, остальные отменены)", (st, r["status"]), (200, "done"))
    eq("Али сказали про район b", TOLD[-1], (False, [("Али", "b")]))
    eq("принятое b осталось", DOCS["S1"]["items"][0]["got"]["b"], 4)
    print("— принятый район и уже отменённый отменить нельзя")
    st, r = await cancel({"districts": ["c"], "force": True})
    eq("409 not_open (заявка уже done)", (st, r.get("error")), (409, "not_open"))
    fresh()
    st, r = await cancel({"districts": ["c"], "force": True})
    eq("409 nothing_to_cancel", (st, r.get("error")), (409, "nothing_to_cancel"))
    print("— вся заявка без принятого нигде → cancelled; с принятым → done")
    fresh(); DOCS["S1"]["tasks"]["c"] = task(); DOCS["S1"]["items"][0]["got"] = {"a": 0, "b": 0, "c": 0}
    st, r = await cancel({"as": "Ст"})
    eq("вся: статус cancelled, cancelled_by", (st, r["status"], DOCS["S1"]["cancelled_by"]), (200, "cancelled", "Ст"))
    eq("всем задачам проставлен cancelled_at", all(t.get("cancelled_at") for t in DOCS["S1"]["tasks"].values()), True)
    fresh()
    st, r = await cancel({"force": True})
    eq("вся при принятом c: статус done, took считает только отменяемые (4)", (r["status"], r["took"]), ("done", 4))
    print("— списки: водитель не видит отменённый район, строка закупа считает без него")
    fresh(); await cancel({"districts": ["a"]})
    lst = await sr.tasks_for_driver("Али", "a")
    eq("free пуст (a отменён), mine = b", ([t["district"] for t in lst["free"]], [t["district"] for t in lst["mine"]]), ([], ["b"]))
    brief = sr._sup_brief(DOCS["S1"])
    eq("districts=2, cancelled=1, done=1, free=0", (brief["districts"], brief["cancelled"], brief["done"], brief["free"]), (2, 1, 1, 0))
    v = sr._task_view("S1", DOCS["S1"], "a", DOCS["S1"]["tasks"]["a"])
    eq("вид задачи несёт cancelled_at", bool(v["cancelled_at"]), True)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — отмена по районам")
    sys.exit(1 if fails else 0)
asyncio.run(main())
