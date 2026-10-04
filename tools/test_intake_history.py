"""История приёмок (владелец, 4 окт 2026: «кто, что, сколько и когда
принимал»): ручка GET /api/owner/supply/intake/history на mongomock.

Что проверяется:
  • строка — задача «поставка × район», по которой что-то было: принятый
    сканом район (с одним «убрано»), район «без сканирования» с отчётом о
    недовозе, район другой базы в работе; района, которого никто не брал, и
    отменённого без бутылок в истории нет;
  • поставка старше окна не попадает; days режется в 1…180, мусор → 60;
  • числа: принято и план в единицах склада (пиво — коробками), коды,
    «убрано», кто сколько отсканировал (по кодам), время взял/начал/закрыл;
  • в ответе нет ни телефонов, ни телеграм-id, ни байтов.

    python3 tools/test_intake_history.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, supply_routes as sr, stock_routes as SR                # noqa: E402

TODAY = "2026-10-03"
NOW = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr",
         "p31": "Heineken 0.33 can"}
FAIL = []
N = [0]


def eq(имя, дали, ждём):
    if дали != ждём:
        FAIL.append(имя); print(f"  FAIL {имя}: {дали!r} != {ждём!r}")
    else:
        print(f"  ok   {имя}")


# mongomock: позиционный items.$ бьёт в первый элемент — те же условия по индексу
# (как в tools/test_intake_life.py).
async def _take(sid, district, product_id, room, now, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") != "open":
        return None
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) > room:
        return None
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": qty, f"items.{i}.scanned": 1,
                 f"tasks.{district}.scanned": 1, f"tasks.{district}.rev": 1},
        "$set": {f"tasks.{district}.last_at": now}})
    return await db.supply_get(sid)


async def _untake(sid, district, product_id, qty=1):
    s = await db.supply_get(sid)
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) < qty:
        return False
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": -qty, f"items.{i}.scanned": -1,
                 f"tasks.{district}.scanned": -1, f"tasks.{district}.undo": 1,
                 f"tasks.{district}.rev": 1}})
    return True


def code():
    N[0] += 1
    return f"h{N[0]:06d}"


async def add_supply(sid, day, at, plan, drivers, kind="main", base="", buys=None):
    pids = sorted({p for o in plan for p in plan[o]})
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": at, "day": day, "kind": kind, "base": base,
        "buys": buys or {},
        "items": [{"id": pid, "name": NAMES[pid], "qty": sum(plan[o].get(pid, 0) for o in plan),
                   "by_district": {o: plan[o][pid] for o in plan if pid in plan[o]},
                   "got": {o: 0 for o in plan if pid in plan[o]}} for pid in pids],
        "tasks": {o: {"qty": sum(plan[o].values()), "driver": drivers.get(o, ""),
                      "driver_id": 777 if drivers.get(o) else 0,
                      "claimed_at": at if drivers.get(o) else None, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0, "undo": 0, "flags": []} for o in plan}})
    SR.base_drop()


async def unhold(sid, o):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who, c=None):
    await unhold(sid, o)
    r = await sr.task_scan(sid, o, pid, c or code(), who, 1, "", False)
    assert r.get("ok"), (sid, o, pid, r)
    return r


async def call(qs=""):
    req = make_mocked_request("GET", "/api/owner/supply/intake/history" + qs)
    h = sr.handle_intake_history
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    res = await h(req)
    return json.loads(res.body)


async def main():
    db._db = AsyncMongoMockClient()["ambar_ih"]
    db.supply_take, db.supply_untake = _take, _untake
    SR._biz_day = lambda *a, **k: TODAY
    import owner_routes, pay_notify
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    pay_notify.tell_safe = _tell
    _noscan = sr.task_noscan
    sr.task_noscan = lambda *a, **k: _noscan(*a, **{"photo": b"\xff\xd8" + b"x" * 40, **k})

    # S1 — магазин, сегодня: JVC сканом (с одним «убрано»), Бизнес Бей без
    # сканирования с недовозом, Тиком никто не брал, Алгусес отменён пустым.
    t1 = NOW - timedelta(hours=3)
    await add_supply("S1", TODAY, t1,
                     {"jvc": {"p1": 3, "p31": 2}, "bbay": {"p2": 2}, "tecom": {"p5": 1}, "alguses": {"p1": 1}},
                     {"jvc": "Худоба", "bbay": "Авазбек"})
    await db._db.supplies.update_one({"_id": "S1"}, {"$set": {"tasks.alguses.cancelled_at": t1}})
    первый = code()
    await scan("S1", "jvc", "p1", "Худоба", первый)
    await scan("S1", "jvc", "p1", "Худоба")
    await unhold("S1", "jvc")
    r = await sr.task_undo("S1", "jvc", первый, "Худоба")
    eq("убрать бутылку прошло", r.get("ok"), True)
    await scan("S1", "jvc", "p1", "Худоба")
    await scan("S1", "jvc", "p1", "Худоба")
    for _ in range(4):                                     # пиво: код = полкоробки
        await scan("S1", "jvc", "p31", "Худоба")
    await unhold("S1", "jvc")
    r = await sr.task_finish("S1", "jvc", "Худоба")
    eq("JVC закрыт", r.get("ok"), True)
    await unhold("S1", "bbay")
    r = await sr.task_noscan("S1", "bbay", "Авазбек",
                             short={"lines": [{"id": "p2", "qty": 1}], "note": "дали одну"})
    eq("Бизнес Бей без сканирования", r.get("ok"), True)

    # S2 — другая база, вчера: Силикон в работе (одна из двух).
    t2 = NOW - timedelta(days=1, hours=2)
    await add_supply("S2", "2026-10-02", t2, {"silicon": {"p5": 2}}, {"silicon": "Сунат"},
                     kind="extra", base="База Х", buys={"p5": {"price": 55}})
    await scan("S2", "silicon", "p5", "Сунат")

    # S3 — давно: в окно 60 дней не попадает.
    await add_supply("S3", "2026-07-01", NOW - timedelta(days=94), {"jvc": {"p1": 1}}, {"jvc": "Худоба"})
    await scan("S3", "jvc", "p1", "Худоба")

    d = await call("?days=60")
    rows = d["rows"]
    print("\nСтроки:")
    for r in rows:
        print(f"   {r['day']} {r['code']} {r['name']:<11} {r['driver'] or r['noscan_by']:<8} {r['status']:<9} "
              f"{r['got']} из {r['plan']} · кодов {r['codes']} · убрано {r['undo']} · "
              f"сканировали {[(w['who'], w['n']) for w in r['scanners']]}")
    eq("окно: today/since", (d["today"], d["since"]), (TODAY, "2026-08-05"))
    eq("строк три (без Тикома, Алгусеса и старой S3)", len(rows), 3)
    eq("порядок: сегодня сверху, вчера ниже", [r["day"] for r in rows], [TODAY, TODAY, "2026-10-02"])
    jvc = next(r for r in rows if r["district"] == "jvc")
    eq("JVC: статус принято", jvc["status"], "done")
    eq("JVC: кто", jvc["driver"], "Худоба")
    eq("JVC: принято 5 из 5 единиц (3 бутылки + 2 коробки)", (jvc["got"], jvc["plan"]), (5, 5))
    eq("JVC: кодов 7, убрано 1", (jvc["codes"], jvc["undo"]), (7, 1))
    eq("JVC: сканировал один человек, 7 кодов, 5 единиц",
       [(w["who"], w["n"], w["units"]) for w in jvc["scanners"]], [("Худоба", 7, 5)])
    eq("JVC: время взял и закрыл есть", bool(jvc["claimed_at"]) and bool(jvc["done_at"]), True)
    eq("JVC: позиции по алфавиту с принятым", [(l["name"], l["got"], l["plan"]) for l in jvc["lines"]],
       [("Absolut 1 ltr", 3, 3), ("Heineken 0.33 can", 2, 2)])
    bb = next(r for r in rows if r["district"] == "bbay")
    eq("Бизнес Бей: статус без сканирования", bb["status"], "noscan")
    eq("Бизнес Бей: кто отметил", bb["noscan_by"], "Авазбек")
    eq("Бизнес Бей: принято 0 из 2", (bb["got"], bb["plan"]), (0, 2))
    eq("Бизнес Бей: отчёт о недовозе ждёт решения", (bb["short"] or {}).get("status"), "pending")
    eq("Бизнес Бей: сканеров нет", bb["scanners"], [])
    sl = next(r for r in rows if r["district"] == "silicon")
    eq("Силикон: другая база, в работе, 1 из 2", (sl["kind"], sl["base"], sl["status"], sl["got"], sl["plan"]),
       ("extra", "База Х", "live", 1, 2))
    eq("Силикон: сканировал Сунат", [(w["who"], w["n"]) for w in sl["scanners"]], [("Сунат", 1)])
    blob = json.dumps(d, ensure_ascii=False)
    eq("ни id, ни телефонов в ответе", ("driver_id" in blob, "777" in blob, "phone" in blob), (False, False, False))
    eq("days режется: 1000 → 180", (await call("?days=1000"))["days"], 180)
    eq("days мусор → 60", (await call("?days=abc"))["days"], 60)
    eq("окно в один день — только сегодняшние", len((await call("?days=1"))["rows"]), 2)
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
