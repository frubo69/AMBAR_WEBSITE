"""История сбора выручки и «Вернуть» из неё (владелец, 4 ноя 2026: «в сборе
выручки сделай историю и сделай возможность возвращать выручку назад» —
«Получил» дважды за три дня нажимали случайно, и 29 октября искали со
страницы 30-го).

mongomock + настоящие ручки STAR (/api/owner/cash-round, /checklist/mark,
/cash-round/fact, /cash-round/history, /cash-round/back):
  • каждое «Получил» и каждое снятие отметки — запись в истории: вид, со
    страницы, какие дни, суммы по дням, кто — именем;
  • «Вернуть» из истории снимает отметки ровно у названных дней; нажатие
    помнит, что ещё держит (left), возвращённое целиком — не активно;
  • ВОЗВРАЩЁННЫЙ ДЕНЬ СНОВА В ПЕРЕНОСЕ на текущей странице, даже когда дни
    после него получены (цепочка переноса полученный день больше не рвёт);
  • «забрать за день» / «вернуть за день» и «снять отметку» на странице —
    тоже в истории;
  • «сдали по факту» дня — в строке;
  • отметки, поставленные до журнала, восстанавливаются в нажатия (via /
    solo), старше окна — нет; такое нажатие тоже можно вернуть;
  • идущая смена: «Получил» берёт перенос по дням — своя запись, возврат
    кладёт его обратно;
  • кривые аргументы — 400, без авторизации — 401.

    python3 tools/test_cash_history.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timezone, timedelta                    # noqa: E402
from aiohttp import web                                               # noqa: E402
from aiohttp.test_utils import TestClient, TestServer                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
import db, owner_routes as own, config_staff as staff, owner_auth     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
D0, D1, D2, D3 = "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"      # D3 — сегодня
own.CASH_CARRY_FROM = D0
ORDERS = {}
def заказ(oid, day, office, total):
    ORDERS[oid] = {"order_id": oid, "office_id": office, "status": "delivered",
                   "total": total, "payment_method": "cash", "_day": day}
async def orders_from(since): return dict(ORDERS)
own.db.orders_from = orders_from
own._order_day = lambda o: o.get("_day")
staff.drivers = lambda: [{"name": "Водитель А", "district": "jvc", "operator": "Оператор"}]
staff.DISPLAY_NAMES[1] = "Старший"
own._biz_date = lambda *_: __import__("datetime").date.fromisoformat(D3)
NOW = datetime.now(timezone.utc)
MARK, ROUND, HIST, BACK = ("/api/owner/checklist/mark", "/api/owner/cash-round",
                           "/api/owner/cash-round/history", "/api/owner/cash-round/back")


async def main():
    db._db = AsyncMongoMockClient()["ambar_cash_history"]
    заказ("a", D0, "jvc", 100); заказ("b", D1, "jvc", 200); заказ("c", D2, "jvc", 300); заказ("d", D3, "jvc", 400)
    заказ("e", D1, "bbay", 500); заказ("f", D2, "bbay", 600); заказ("g", D3, "bbay", 700)
    заказ("h", D0, "silicon", 50); заказ("i", D1, "silicon", 60); заказ("j", D2, "silicon", 70)
    app = web.Application(); own.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(p, **q):
            r = await cl.get(p, params=q, headers={"Authorization": "tma 1"}); return await r.json()
        async def post(p, b):
            r = await cl.post(p, json=b, headers={"Authorization": "tma 1"}); return r.status, await r.json()
        рн = lambda cr, oid: next(x for x in cr["districts"] if x["id"] == oid)
        дни = lambda e: [d["day"] for d in e["days"]]

        print("── «Получил» целиком — одно нажатие в истории ───────────────────")
        h = await get(HIST, days=30)
        eq("история пуста", (h["n"], h["events"], h["days"]), (0, [], 30))
        await post(MARK, {"day": D2, "item": "cash:jvc", "done": True})
        h = await get(HIST, days=30); e = h["events"][0]
        eq("получил целиком за три дня со страницы 3 ноя", (h["n"], e["kind"], e["mode"], e["district"], e["page"], дни(e)),
           (1, "got", "all", "jvc", D2, [D0, D1, D2]))
        eq("суммы по дням, итог, чай", ([d["net"] for d in e["days"]], e["net"], e["tips"]), ([100, 200, 300], 600, 0))
        eq("держит все три, можно вернуть, кто — именем", (e["left"], e["active"], e["by"], e["code"]),
           ([D0, D1, D2], True, "Старший", own.OFFICE_CODES["jvc"]))

        print("── «Вернуть» из истории — один день ────────────────────────────")
        st, r = await post(BACK, {"district": "jvc", "page": D2, "days": [D0]})
        eq("вернули один день", (st, r["ok"], r["removed"]), (200, True, [D0]))
        b = r["events"][0]
        eq("первой — «вернул» из истории за 1 ноя", (b["kind"], b["mode"], дни(b), b["net"], b["by"]), ("back", "hist", [D0], 100, "Старший"))
        g = next(x for x in r["events"] if x["kind"] == "got")
        eq("нажатие «получил» держит ещё два дня", (g["left"], g["active"]), ([D1, D2], True))
        j = рн(await get(ROUND, day=D3), "jvc")
        eq("ВОЗВРАЩЁННЫЙ ДЕНЬ СНОВА В ПЕРЕНОСЕ, хотя 2–3 ноя получены", [c["day"] for c in j["carry"]], [D0])
        eq("и в выручке района: своё 400 + 100", j["net_all"], 500)

        print("── вернуть остальное ────────────────────────────────────────────")
        st, r = await post(BACK, {"district": "jvc", "page": D2, "days": [D1, D2]})
        g = next(x for x in r["events"] if x["kind"] == "got")
        eq("нажатие возвращено целиком — вернуть больше нечего", (r["removed"], g["left"], g["active"]), ([D1, D2], [], False))
        eq("две записи «вернул», одна «получил»", [x["kind"] for x in r["events"]], ["back", "back", "got"])
        j = рн(await get(ROUND, day=D3), "jvc")
        eq("всё снова в переносе", [c["day"] for c in j["carry"]], [D2, D1, D0])
        st, r = await post(BACK, {"district": "jvc", "page": D2, "days": [D1]})
        eq("второй раз возвращать нечего", (st, r["removed"], r["n"]), (200, [], 3))

        print("── кнопки на странице — тоже в истории ──────────────────────────")
        await post(MARK, {"day": D3, "item": "cash:jvc", "done": True, "only": D1})
        e = (await get(HIST, days=30))["events"][0]
        eq("«забрать за 2 ноя» со страницы сегодня — своё нажатие", (e["kind"], e["mode"], e["page"], дни(e), e["active"]),
           ("got", "solo", D3, [D1], True))
        await post(MARK, {"day": D3, "item": "cash:jvc", "done": False, "only": D1})
        h = await get(HIST, days=30)
        eq("«вернуть за 2 ноя» — записано", (h["events"][0]["kind"], h["events"][0]["mode"], дни(h["events"][0])), ("back", "solo", [D1]))
        eq("нажатие «забрать» день больше не держит",
           next(x for x in h["events"] if x["kind"] == "got" and x["mode"] == "solo")["active"], False)
        await post(MARK, {"day": D3, "item": "cash:jvc", "done": True})
        e = (await get(HIST, days=30))["events"][0]
        eq("получил целиком за четыре дня", (e["mode"], дни(e), e["net"], e["left"]), ("all", [D0, D1, D2, D3], 1000, [D0, D1, D2, D3]))
        await post(MARK, {"day": D3, "item": "cash:jvc", "done": False})
        e = (await get(HIST, days=30))["events"][0]
        eq("«снять отметку» — вернул все четыре", (e["kind"], e["mode"], дни(e)), ("back", "all", [D0, D1, D2, D3]))

        print("── сдали по факту ───────────────────────────────────────────────")
        await post(MARK, {"day": D3, "item": "cash:bbay", "done": True})
        await post("/api/owner/cash-round/fact", {"day": D2, "page": D3, "district": "bbay", "fact": 550})
        e = next(x for x in (await get(HIST, days=30))["events"] if x["district"] == "bbay")
        eq("пустой день района в нажатии не показан, факт дня виден",
           ([(d["day"], d["net"], d["fact"]) for d in e["days"]], e["net"]), ([(D1, 500, None), (D2, 600, 550), (D3, 700, None)], 1800))

        print("── отметки до журнала ───────────────────────────────────────────")
        at0 = (NOW - timedelta(days=2)).isoformat(); at1 = (NOW - timedelta(days=1)).isoformat()
        await db.checklist_put(D2, "cash:silicon", {"done": True, "by": "1", "at": at0})
        await db.checklist_put(D1, "cash:silicon", {"done": True, "by": "1", "via": D2, "at": at0})
        await db.checklist_put(D0, "cash:silicon", {"done": True, "solo": D3, "by": "1", "at": at1})
        await db.checklist_put("2026-10-01", "cash:silicon", {"done": True, "by": "1", "at": (NOW - timedelta(days=40)).isoformat()})
        s_ = [x for x in (await get(HIST, days=30))["events"] if x["district"] == "silicon"]
        eq("два нажатия из отметок: за день со страницы 4 ноя и целиком со страницы 3 ноя; старше окна — нет",
           [(x["kind"], x["mode"], x["page"], дни(x), x["active"]) for x in s_],
           [("got", "solo", D3, [D0], True), ("got", "all", D2, [D1, D2], True)])
        eq("время — от отметки, кто — именем", (s_[0]["at"], s_[0]["by"], s_[1]["net"]), (at1, "Старший", 130))
        st, r = await post(BACK, {"district": "silicon", "page": D2, "days": [D1, D2]})
        eq("старое нажатие возвращено: «вернул» записан, самого нажатия (из отметок) больше нет",
           (r["removed"], [(x["kind"], x["mode"]) for x in r["events"] if x["district"] == "silicon"]),
           ([D1, D2], [("back", "hist"), ("got", "solo")]))
        j = рн(await get(ROUND, day=D3), "silicon")
        eq("и дни на карточке, за ним — 1 ноя получено за день", ([c["day"] for c in j["carry"]], [t["day"] for t in j["taken"]]), ([D2, D1], [D0]))

        print("── идущая смена ─────────────────────────────────────────────────")
        await db.shift_open(D3, "jvc", {"opened_at": NOW, "operator": "Оператор", "drivers": {"Водитель А": True}})
        st, _ = await post(MARK, {"day": D3, "item": "cash:jvc", "done": True})
        e = (await get(HIST, days=30))["events"][0]
        eq("получил перенос при идущей смене — live, сегодняшнего нет", (st, e["mode"], дни(e), e["active"]), (200, "live", [D0, D1, D2], True))
        st, r = await post(BACK, {"district": "jvc", "page": D3, "days": e["left"]})
        eq("вернули перенос", (r["removed"], r["events"][0]["kind"], r["events"][0]["mode"]), ([D0, D1, D2], "back", "hist"))
        j = рн(await get(ROUND, day=D3), "jvc")
        eq("перенос снова на карточке", ([c["day"] for c in j["carry"]], j["done"]), ([D2, D1, D0], False))

        print("── ошибки ───────────────────────────────────────────────────────")
        eq("чужой район", (await post(BACK, {"district": "nope", "page": D3, "days": [D1]}))[0], 400)
        eq("кривая страница", (await post(BACK, {"district": "jvc", "page": "x", "days": [D1]}))[0], 400)
        eq("без дней", (await post(BACK, {"district": "jvc", "page": D3, "days": []}))[0], 400)
        eq("кривое окно истории — 30", (await get(HIST, days="abc"))["days"], 30)
        r1 = await cl.get(HIST); r2 = await cl.post(BACK, json={"district": "jvc", "page": D3, "days": [D1]})
        eq("без авторизации — 401", (r1.status, r2.status), (401, 401))

    print("\nИТОГ:", "всё сошлось" if not FAIL else f"НЕ СОШЛОСЬ: {FAIL}")
    return 0 if not FAIL else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
