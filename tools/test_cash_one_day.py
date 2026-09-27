"""Старший забирает выручку либо всю, либо ровно за один день (владелец, 28 сен 2026).

«Сделай возможность в сборе старшему либо забрать всё, что не забрал, либо
забрать именно за определённый день.»

Главная опасность здесь — цепочка переноса: она идёт назад от вчера и
обрывается на первом дне, где выручку получили. Отметить средний день
обычным способом значило бы спрятать всё, что старше, вместе с деньгами.
Поэтому такой день помечается solo: свой день он закрывает, а цепочку не
рвёт, и остаётся на экране строкой «получено» — чтобы отметку можно было
снять.

  • забрали за один день — остальные дни остались в переносе и видны;
  • забранный день ушёл из «к сбору», но виден отдельной строкой;
  • снять отметку — день возвращается в перенос;
  • строка «получено» живёт на том дне, с которого забрали, и не тащится
    за собой дальше — иначе экран зарос бы отметками за месяц;
  • «Получил» целиком по-прежнему закрывает всё и НЕ трогает забранное
    поимённо;
  • забрать «только за сегодня» не закрывает район целиком;
  • чужой или будущий день ручка не принимает.

    python3 tools/test_cash_one_day.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
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
D1, D2, D3, D4 = "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"
own.CASH_CARRY_FROM = D1
ORDERS = {}
def заказ(oid, day, total):
    ORDERS[oid] = {"order_id": oid, "office_id": "jvc", "status": "delivered",
                   "total": total, "payment_method": "cash", "_day": day}
async def orders_from(since): return dict(ORDERS)
own.db.orders_from = orders_from
own._order_day = lambda o: o.get("_day")
staff.drivers = lambda: [{"name": "Водитель А", "district": "jvc", "operator": "Оператор"}]
own._biz_date = lambda *_: __import__("datetime").date.fromisoformat(D4)


async def main():
    db._db = AsyncMongoMockClient()["ambar_cash_one"]
    заказ("a", D1, 100); заказ("b", D2, 200); заказ("c", D3, 300); заказ("d", D4, 400)
    app = web.Application(); own.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(p, **q):
            r = await cl.get(p, params=q, headers={"Authorization": "tma 1"}); return await r.json()
        async def post(p, b):
            r = await cl.post(p, json=b, headers={"Authorization": "tma 1"}); return r.status, await r.json()
        рн = lambda cr: next(x for x in cr["districts"] if x["id"] == "jvc")

        print("── что лежит на столе ─────────────────────────────────────────")
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("сегодня своё 400, в переносе три дня", (j["net"], [(c["day"], c["net"]) for c in j["carry"]]),
           (400, [(D3, 300), (D2, 200), (D1, 100)]))
        eq("всего к сбору", j["net_all"], 1000)

        print("── забрали ровно за 25-е (средний день!) ──────────────────────")
        st, _ = await post("/api/owner/checklist/mark",
                           {"day": D4, "item": "cash:jvc", "done": True, "only": D2})
        eq("ручка приняла", st, 200)
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("СТАРШИЕ ДНИ НЕ ПРОПАЛИ", [(c["day"], c["net"]) for c in j["carry"]],
           [(D3, 300), (D1, 100)])
        eq("к сбору стало меньше ровно на 200", j["net_all"], 800)
        eq("забранный день виден строкой", [(c["day"], c["net"]) for c in j["taken"]], [(D2, 200)])
        eq("район целиком не закрыт", j["done"], False)

        print("── сняли отметку — день вернулся ──────────────────────────────")
        await post("/api/owner/checklist/mark",
                   {"day": D4, "item": "cash:jvc", "done": False, "only": D2})
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("вернулся в перенос", [(c["day"], c["net"]) for c in j["carry"]],
           [(D3, 300), (D2, 200), (D1, 100)])
        eq("и сумма вернулась", j["net_all"], 1000)
        eq("строки «получено» больше нет", j["taken"], [])

        print("── забрали только за сегодня ──────────────────────────────────")
        await post("/api/owner/checklist/mark",
                   {"day": D4, "item": "cash:jvc", "done": True, "only": D4})
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("сегодня отмечен", (j["done"], j["solo_today"]), (True, True))
        eq("а перенос остался на месте", [(c["day"], c["net"]) for c in j["carry"]],
           [(D3, 300), (D2, 200), (D1, 100)])
        # назавтра старые дни обязаны доехать, а не оборваться на сегодняшней отметке
        own._biz_date = lambda *_: __import__("datetime").date.fromisoformat("2026-09-28")
        j2 = рн(await get("/api/owner/cash-round", day="2026-09-28"))
        eq("НАЗАВТРА ПЕРЕНОС НЕ ОБОРВАЛСЯ", [(c["day"], c["net"]) for c in j2["carry"]],
           [(D3, 300), (D2, 200), (D1, 100)])
        eq("и назавтра забранное не висит строкой", j2["taken"], [])
        own._biz_date = lambda *_: __import__("datetime").date.fromisoformat(D4)
        await post("/api/owner/checklist/mark",
                   {"day": D4, "item": "cash:jvc", "done": False, "only": D4})

        print("── «Получил» целиком поверх забранного за день ────────────────")
        await post("/api/owner/checklist/mark",
                   {"day": D4, "item": "cash:jvc", "done": True, "only": D2})
        await post("/api/owner/checklist/mark", {"day": D4, "item": "cash:jvc", "done": True})
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("забрал всё, что оставалось", (j["done"], j["net_all"]), (True, 800))
        eq("а 25-е так и числится забранным отдельно",
           [c["day"] for c in j["taken"]], [D2])
        await post("/api/owner/checklist/mark", {"day": D4, "item": "cash:jvc", "done": False})
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("сняли общую — 25-е осталось забранным",
           ([(c["day"], c["net"]) for c in j["carry"]], [c["day"] for c in j["taken"]]),
           ([(D3, 300), (D1, 100)], [D2]))
        await post("/api/owner/checklist/mark",
                   {"day": D4, "item": "cash:jvc", "done": False, "only": D2})

        print("── «Получил» целиком ──────────────────────────────────────────")
        await post("/api/owner/checklist/mark", {"day": D4, "item": "cash:jvc", "done": True})
        j = рн(await get("/api/owner/cash-round", day=D4))
        eq("закрыл всё", (j["done"], j["net_all"]), (True, 1000))
        c25 = рн(await get("/api/owner/cash-round", day=D2))
        eq("и прошлые дни тоже закрыты", c25["done"], True)

        print("── чушь в запросе ─────────────────────────────────────────────")
        st, b = await post("/api/owner/checklist/mark",
                           {"day": D4, "item": "cash:jvc", "done": True, "only": "позавчера"})
        eq("кривая дата — отказ", (st, b.get("error")), (400, "bad_day"))
        st, b = await post("/api/owner/checklist/mark",
                           {"day": D4, "item": "cash:jvc", "done": True, "only": "2026-10-05"})
        eq("будущий день — отказ", (st, b.get("error")), (400, "future"))

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
