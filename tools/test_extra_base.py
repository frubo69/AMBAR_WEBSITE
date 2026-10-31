"""Доп. заявка: база по факту (владелец, 1 ноя 2026).

  • заявку собирают с пресетом «Не знаем» — где купили на самом деле,
    вписывают потом, хоть после приёмки (POST /api/owner/supply/{sid}/base);
  • цены, записанные этой заявкой при старом имени базы, переезжают под
    новое, чужие записи под старым именем остаются;
  • у следующей доп. заявки из её недобора «где уже искали» переписывается;
  • водитель видит новое имя в задаче; основной и отменённой — отказ.

    python3 tools/test_extra_base.py
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, supply_routes as sr                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr"}
PLAN = {"jvc": {"p1": 3, "p2": 2}}
DRV = {"jvc": "Худоба"}

def raw(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

async def post(sid, body):
    req = make_mocked_request("POST", "/x", match_info={"sid": sid})
    req._read_bytes = json.dumps(body).encode()
    req["owner_id"] = 1
    r = await raw(sr.handle_base_set)(req)
    return r.status, json.loads(r.text)

async def make(sid, base, status="open", kind="extra", **kw):
    now = datetime.now(timezone.utc)
    await db._db.supplies.insert_one({
        "_id": sid, "status": status, "at": now, "day": "2026-11-01", "kind": kind, "base": base,
        "items": [{"id": p, "name": NAMES[p], "qty": PLAN["jvc"][p], "asked": PLAN["jvc"][p],
                   "by_district": {"jvc": PLAN["jvc"][p]}, "got": {"jvc": 0}} for p in sorted(PLAN["jvc"])],
        "tasks": {"jvc": {"driver": DRV["jvc"], "driver_id": 1, "claimed_at": now, "started_at": None,
                          "noscan_at": None, "done_at": None, "cancelled_at": None, "scanned": 0}}, **kw})
    return sid

async def view(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])

async def main():
    db._db = AsyncMongoMockClient()["ambar_extra_base"]
    import owner_routes
    async def _say(key, text, **kw): return []
    owner_routes.notify_owners = _say; owner_routes.notify_owners_force = _say
    sr._buy_note = lambda *a, **k: None
    now = datetime.now(timezone.utc)

    print("База по факту")
    sid = await make("X1", "Не знаем")
    await sr.buy_set(await db.supply_get(sid), "p1", 40, 0, "Худоба", 1)
    eq("цена легла под пресет", [(p["item"], p["price"]) for p in await db.base_prices("не знаем")], [("p1", 40.0)])
    # чужая запись под тем же именем — другой заявки
    await db.base_price_set("не знаем", "Не знаем", "p2", 50, NAMES["p2"], now, "кто-то", "X4")
    # следующая доп. заявка из недобора этой — помнит, где искали
    await db._db.supplies.insert_one({"_id": "X2", "status": "draft", "at": now, "day": "2026-11-01", "kind": "extra",
                                      "base": "", "from_supply": sid, "tried_bases": ["Не знаем"],
                                      "items": [], "tasks": {}})
    eq("водитель видит пресет", (await view(sid, "jvc"))["base"], "Не знаем")

    st, r = await post(sid, {"base": "  Al   Hamra ", "as": "Старший"})
    eq("назвали по факту", (st, r.get("base")), (200, "Al Hamra"))
    eq("в заявке новое имя", (await db.supply_get(sid))["base"], "Al Hamra")
    eq("водитель видит новое имя", (await view(sid, "jvc"))["base"], "Al Hamra")
    eq("цена этой заявки переехала", [(p["base"], p["item"], p["price"], p["supply_id"]) for p in await db.base_prices("al hamra")],
       [("Al Hamra", "p1", 40.0, sid)])
    eq("чужая под старым именем осталась", [(p["item"], p["supply_id"]) for p in await db.base_prices("не знаем")], [("p2", "X4")])
    eq("следующей заявке переписали «где искали»", (await db.supply_get("X2"))["tried_bases"], ["Al Hamra"])

    st, r = await post(sid, {"base": "Al Hamra"})
    eq("то же имя — без изменений", (st, (await db.supply_get(sid))["base"]), (200, "Al Hamra"))
    eq("пусто — отказ", (await post(sid, {"base": "   "}))[0], 400)
    eq("после приёмки тоже можно", (await db.supply_set(sid, {"status": "done"}, only_open=False),
                                  (await post(sid, {"base": "Marina"}))[0], (await db.supply_get(sid))["base"]), (True, 200, "Marina"))

    print("\nКому нельзя")
    await make("S9", "", kind="main")
    eq("основной заявке — отказ", (await post("S9", {"base": "Al Hamra"}))[1].get("error"), "not_extra")
    await make("X3", "Где найдём", status="cancelled")
    eq("отменённой — отказ", (await post("X3", {"base": "Al Hamra"}))[0], 409)
    eq("нет такой — 404", (await post("NOPE", {"base": "Al Hamra"}))[0], 404)

    print("\nИТОГ:", "ПРОВАЛ " + ", ".join(FAIL) if FAIL else "всё сошлось")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
