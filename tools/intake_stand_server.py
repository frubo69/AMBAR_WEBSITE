"""Стенд «история приёмок»: настоящие supply_routes поверх базы в памяти,
страница STAR — настоящая, из каталога owner. Имена водителей и числа
ВЫДУМАНЫ (стенд, не бой).

    python3 tools/intake_stand_server.py [port]        # по умолчанию 8792

Открыть http://127.0.0.1:8792/ и в консоли:
    document.getElementById('accOv').classList.add('show'); intHistory();
"""
import asyncio, os, sys, logging, random
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
logging.basicConfig(level=logging.WARNING)
from aiohttp import web                                            # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import owner_auth                                                  # noqa: E402
owner_auth.require_owner = lambda h: h            # стенд: без initData (как fb_stand_server)
import db, supply_routes as sr, stock_routes as SR, owner_routes, pay_notify   # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
DRV = ["Стенд А", "Стенд Б", "Стенд В", "Стенд Г"]
N = [0]


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
    return f"st{N[0]:06d}"


async def unhold(sid, o):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who):
    await unhold(sid, o)
    r = await sr.task_scan(sid, o, pid, code(), who, 1, "", False)
    return r


async def add_supply(sid, day, at, plan, drivers, cat, kind="main", base=""):
    pids = sorted({p for o in plan for p in plan[o]})
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": at, "day": day, "kind": kind, "base": base,
        "buys": {p: {"price": 60} for p in pids} if kind == "extra" else {},
        "items": [{"id": pid, "name": cat[pid].get("name", pid), "qty": sum(plan[o].get(pid, 0) for o in plan),
                   "by_district": {o: plan[o][pid] for o in plan if pid in plan[o]},
                   "got": {o: 0 for o in plan if pid in plan[o]}} for pid in pids],
        "tasks": {o: {"qty": sum(plan[o].values()), "driver": drivers.get(o, ""),
                      "driver_id": 1 if drivers.get(o) else 0,
                      "claimed_at": at if drivers.get(o) else None, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0, "undo": 0, "flags": []} for o in plan}})
    SR.base_drop()


async def shift(sid, delta):
    """Сдвинуть все моменты поставки назад на delta — стенд заводит всё «сейчас»."""
    doc = await db._db.supplies.find_one({"_id": sid})
    upd = {}
    for o, t in (doc.get("tasks") or {}).items():
        for k in ("claimed_at", "started_at", "done_at", "last_at", "noscan_at", "cancelled_at"):
            if isinstance(t.get(k), datetime):
                upd[f"tasks.{o}.{k}"] = t[k] - delta
        fl = []
        for f in t.get("flags") or []:
            f = dict(f)
            if isinstance(f.get("at"), datetime):
                f["at"] = f["at"] - delta
            fl.append(f)
        upd[f"tasks.{o}.flags"] = fl
    if isinstance(doc.get("at"), datetime):
        upd["at"] = doc["at"] - delta
    await db._db.supplies.update_one({"_id": sid}, {"$set": upd})
    async for c in db._db.qr_codes.find({"supply_id": sid}):
        if isinstance(c.get("at"), datetime):
            await db._db.qr_codes.update_one({"_id": c["_id"]}, {"$set": {"at": c["at"] - delta}})


async def seed():
    random.seed(7)
    db._db = AsyncMongoMockClient()["intake_stand"]
    db.supply_take, db.supply_untake = _take, _untake

    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    pay_notify.tell_safe = _tell
    _noscan = sr.task_noscan
    sr.task_noscan = lambda *a, **k: _noscan(*a, **{"photo": b"\xff\xd8" + b"x" * 40, **k})

    cat = SR._catalog()
    bottles = [p for p, v in cat.items() if SR._unit(v) == 1][:40]
    beers = [p for p, v in cat.items() if SR._unit(v) > 1][:6]
    today = datetime.strptime(SR._biz_day(), "%Y-%m-%d")
    offices = ["jvc", "bbay", "silicon", "alguses", "tecom"]
    for d in range(6):
        day = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        sid = f"T{d}"
        plan = {}
        for o in offices[: 3 + (d % 3)]:
            pl = {p: random.randint(1, 6) for p in random.sample(bottles, 5 + (d % 4))}
            if beers:
                pl[random.choice(beers)] = random.choice([1, 2, 3])
            plan[o] = pl
        drivers = {o: DRV[i % len(DRV)] for i, o in enumerate(plan)}
        if d == 1:
            drivers.pop(list(plan)[-1])            # район, которого никто не брал
        await add_supply(sid, day, datetime.now(timezone.utc) - timedelta(minutes=5), plan, drivers, cat)
        for i, (o, pl) in enumerate(plan.items()):
            who = drivers.get(o)
            if not who:
                continue
            if d == 0 and i == 0:
                # сегодня, в работе: половина
                for p, n in list(pl.items())[:3]:
                    for _ in range(max(1, n // 2) * (2 if SR._unit(cat[p]) > 1 else 1)):
                        await scan(sid, o, p, who)
                continue
            if i == 2 and d in (0, 2):
                await unhold(sid, o)
                await sr.task_noscan(sid, o, who, short={"lines": [{"id": list(pl)[0], "qty": 1}], "note": "коробка была вскрыта"})
                continue
            miss = 1 if (i == 1 and d % 2 == 0) else 0
            for j, (p, n) in enumerate(pl.items()):
                k = n * (2 if SR._unit(cat[p]) > 1 else 1)
                if j == 0 and miss:
                    k -= 1
                for _ in range(k):
                    r = await scan(sid, o, p, who)
                    if j == 1 and _ == 0 and d == 3:
                        await unhold(sid, o)
                        await sr.task_undo(sid, o, r.get("code"), who)
                        await scan(sid, o, p, who)
            await unhold(sid, o)
            await sr.task_finish(sid, o, who, note="всё по списку" if not miss else "", force=True)
        await shift(sid, timedelta(days=d, hours=1 + d % 3, minutes=7 * d))
    # доп. база — вчера
    await add_supply("X1", (today - timedelta(days=1)).strftime("%Y-%m-%d"),
                     datetime.now(timezone.utc) - timedelta(minutes=5),
                     {"alguses": {bottles[3]: 4, bottles[9]: 2}}, {"alguses": DRV[2]}, cat,
                     kind="extra", base="Стенд-база")
    for p, n in ((bottles[3], 4), (bottles[9], 2)):
        for _ in range(n):
            await scan("X1", "alguses", p, DRV[2])
    await unhold("X1", "alguses")
    await sr.task_finish("X1", "alguses", DRV[2], force=True)
    await shift("X1", timedelta(days=1, hours=4))


async def h_any(request):
    return web.json_response({"error": "stand"}, status=404)


def main():
    app = web.Application(client_max_size=20 * 1024 * 1024)

    async def _start(app):
        await seed()
    app.on_startup.append(_start)
    sr.setup(app)
    app.router.add_route("*", "/api/{tail:.*}", h_any)
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(ROOT, "owner", "index.html")))
    app.router.add_static("/", os.path.join(ROOT, "owner"))
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
