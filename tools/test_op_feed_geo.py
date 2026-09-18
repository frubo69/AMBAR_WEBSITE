"""«События» оператора: водители включили / выключили геопозицию (владелец,
19 сен 2026: «сделай так, чтобы операторы тоже получали такие сообщения, но
не в чат телеграма, а внутрь самого миниаппа, внутри уведомлений»).
mongomock + настоящий обработчик ленты и настоящая запись сторожа:
  • geo_watch._owners кладёт в запись уведомления водителя и район (meta);
  • лента оператора отдаёт geo — только водителей его районов, только за
    сегодняшнюю смену, свежие первыми, со словами «включил / выключил»;
  • старший за планшетом видит все районы; тест-оператору это не нужно."""
import asyncio, inspect, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_BOT_TOKEN", "")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, geo_watch, operator_routes as op, close_req
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def feed(who, test=False):
    req = make_mocked_request("GET", "/api/operator/feed?as=" + who)
    req["op_test"] = test
    r = await raw(op.handle_feed)(req)
    return json.loads(r.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_feed_geo"]
    async def no_orders(test=False): return {}
    async def no_close(day, scope, **k): return []
    async def no_support(limit=400): return []
    db.get_all_orders = no_orders
    close_req.open_for = no_close
    db.support_threads_brief = no_support

    districts = await op._fresh_districts()
    people = op._people_for(make_mocked_request("GET", "/"), districts)
    own = next(p for p in people if not p["senior"] and p["districts"])
    senior = next(p for p in people if p["senior"])
    mine = own["districts"][0]
    other = next(d["id"] for d in districts if d["id"] not in own["districts"])
    a = (staff.DISTRICT_DRIVERS.get(mine) or ["Свой"])[0]
    b = (staff.DISTRICT_DRIVERS.get(other) or ["Чужой"])[0]
    print(f"   оператор {own['name']} ({', '.join(own['districts'])}), свой водитель {a} ({mine}), чужой {b} ({other})")

    print("── запись сторожа несёт водителя и район ──────────────────────")
    await geo_watch._owners("📍 выкл", geo_watch.EVENT_OFF, meta=geo_watch.geo_meta(a, False))
    await geo_watch._owners("📍 вкл", geo_watch.EVENT_ON, meta=geo_watch.geo_meta(b, True))
    await geo_watch._owners("📍 вкл", geo_watch.EVENT_ON, meta=geo_watch.geo_meta(a, True))
    await geo_watch._owners("📍 не выкл", geo_watch.EVENT_OFF, meta=geo_watch.geo_meta(a, False, self_=False))
    # вчерашнее — до начала учётных суток
    await db._db.owner_notifications.insert_one({
        "event_key": geo_watch.EVENT_OFF, "text": "вчера", "owner_id": 0,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "meta": geo_watch.geo_meta(a, False)})
    # про старшего (без водителя) — не для ленты
    await geo_watch._owners("📍 старший", geo_watch.EVENT_SENIOR)
    doc = await db._db.owner_notifications.find_one({"text": "📍 выкл"})
    eq("meta в записи", doc["meta"], {"driver": a, "district": mine, "on": False, "self": True})

    print("── лента оператора ────────────────────────────────────────────")
    f = await feed(own["name"])
    g = f.get("geo") or []
    eq("только свой район и только сегодня — три события", [(x["driver"], x["on"]) for x in g],
       [(a, False), (a, True), (a, False)])
    eq("слова: выключил / включил / геопозиция выключена", [x["title"] for x in g],
       [f"{a} — геопозиция выключена", f"{a} включил геопозицию", f"{a} выключил геопозицию"])
    eq("свежие первыми", [x["at"] for x in g] == sorted([x["at"] for x in g], reverse=True), True)
    eq("код района у строки", {x["district"] for x in g}, {op._code_of(mine, districts)})
    eq("счётчик «требует внимания» геопозиция не трогает", f["count"], 0)
    fs = await feed(senior["name"])
    eq("старший за планшетом видит и чужой район", sorted({x["driver"] for x in fs.get("geo") or []}), sorted({a, b}))
    ft = await feed("Тест", test=True)
    eq("тест-оператору — пусто", ft.get("geo"), [])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
