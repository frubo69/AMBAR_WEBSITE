"""Сбор выручки: с идущей смены не берут (владелец, 4 ноя 2026).

«Сейчас в сборе выручки её можно забрать не только с закрытых смен, но и с
текущей, это неправильно». «Поэтому в начале дня, пока мы в минусе на 920
дирхам, оно вычитает из выручки те деньги из закрытых смен».

mongomock + настоящие ручки STAR (/api/owner/cash-round, /checklist/mark,
/cash-round/fact, /cash-round/later):
  • смена района идёт (оператор открыл и не закрыл, или водитель открыл свою
    и не закрыл) — сегодняшних денег в сборе нет: в выручке района только
    перенос, питание дня из перенесённого не вычитается, итоги и «нужно
    получить» без него;
  • «Получил» у такого района забирает перенос по дням (solo), сегодняшней
    отметки не ставит; район после этого получен (got), «снять отметку» общей
    нет — у каждого дня своя;
  • «забрать за сегодня» и «сдали по факту» за сегодня — отказ shift_open;
    за прошлый день — принимаются;
  • «соберу завтра» откладывает перенос; район без переноса откладывать нечего;
  • оператор закрыл район, водитель свою смену — нет: смена идёт, виден он;
    водитель закрыл — сегодняшнее появилось в сборе;
  • прошлые сутки с забытым закрытием отработаны: собираются как обычно;
  • старая отметка дня (поставлена до правила) при идущей смене не теряется —
    и снимается;
  • в готовности дней идущая смена в сумму дня не входит.

    python3 tools/test_cash_shift_open.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timezone                               # noqa: E402
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
D0, D1, D2 = "2026-11-01", "2026-11-02", "2026-11-03"      # D2 — сегодня
own.CASH_CARRY_FROM = D0
ORDERS = {}
def заказ(oid, day, office, total):
    ORDERS[oid] = {"order_id": oid, "office_id": office, "status": "delivered",
                   "total": total, "payment_method": "cash", "_day": day}
async def orders_from(since): return dict(ORDERS)
own.db.orders_from = orders_from
own._order_day = lambda o: o.get("_day")
staff.drivers = lambda: [{"name": "Водитель А", "district": "jvc", "operator": "Оператор"},
                         {"name": "Водитель Б", "district": "bbay", "operator": "Оператор"}]
own._biz_date = lambda *_: __import__("datetime").date.fromisoformat(D2)
MEAL = staff.meal_of({"working": True})
NOW = datetime.now(timezone.utc)
B1, B2 = own.OFFICE_CODES["jvc"], own.OFFICE_CODES["bbay"]


async def main():
    db._db = AsyncMongoMockClient()["ambar_cash_shift_open"]
    # Вчера: JVC 1000, Бизнес Бей 500 — никто не забрал. Сегодня JVC уже
    # отвёз 300, оператор открыл район, водитель на смене (питание MEAL).
    заказ("a", D1, "jvc", 1000); заказ("b", D1, "bbay", 500); заказ("c", D2, "jvc", 300)
    await db.shift_open(D2, "jvc", {"opened_at": NOW, "operator": "Оператор", "drivers": {"Водитель А": True}})
    await db.save_driver_day(D2, "Водитель А", {"working": True, "shift_open_at": NOW, "shift_close_at": None})
    app = web.Application(); own.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(p, **q):
            r = await cl.get(p, params=q, headers={"Authorization": "tma 1"}); return await r.json()
        async def post(p, b):
            r = await cl.post(p, json=b, headers={"Authorization": "tma 1"}); return r.status, await r.json()
        рн = lambda cr, oid: next(x for x in cr["districts"] if x["id"] == oid)

        print("── смена идёт: сегодняшнего в сборе нет ────────────────────────")
        cr = await get("/api/owner/cash-round", day=D2)
        j, b = рн(cr, "jvc"), рн(cr, "bbay")
        eq("JVC: смена идёт, имён нет (район открыт оператором)", (j["shift"], j["on"]), ("open", []))
        eq("своё сегодняшнее отдано как есть: 300 − питание", j["net"], 300 - MEAL)
        eq("а в выручке района — только вчерашняя 1000", (j["net_all"], [(c["day"], c["net"]) for c in j["carry"]]), (1000, [(D1, 1000)]))
        eq("Бизнес Бей: смена не шла, перенос 500 как обычно", (b["shift"], b["net_all"], b["empty"]), ("closed", 500, False))
        eq("итоги без сегодняшнего: 1500, расход 0, наличные 1500", (cr["net_total"], cr["spend_total"], cr["cash_total"], cr["carry_total"]), (1500, 0, 1500, 1500))
        eq("нужно получить от двух, получено 0", (cr["need"], cr["done"]), (2, 0))
        сегодня = next(x for x in cr["days"] if x["day"] == D2)
        eq("готовность: сегодня ждёт JVC, сумма дня без идущей смены", (сегодня["ready"], сегодня["wait"], сегодня["net"]), (False, [B1], 0))

        print("── «Получил» забирает только перенос ───────────────────────────")
        st, _ = await post("/api/owner/checklist/mark", {"day": D2, "item": "cash:jvc", "done": True})
        eq("ручка приняла", st, 200)
        j = рн(await get("/api/owner/cash-round", day=D2), "jvc")
        eq("перенос забран поимённо, район получен (got)", ([(c["day"], c["net"]) for c in j["taken"]], j["carry"], j["done"], j["got"]), ([(D1, 1000)], [], True, True))
        eq("время получения — от забранного дня", bool(j["done_at"]), True)
        eq("сегодняшней отметки нет", (await db.checklist_get(D2)).get("cash:jvc"), None)
        eq("вчерашняя помечена solo с сегодняшней страницы", ((await db.checklist_get(D1)).get("cash:jvc") or {}).get("solo"), D2)
        cr = await get("/api/owner/cash-round", day=D2)
        eq("получено 1 из 2, к сбору осталось 500", (cr["done"], cr["need"], cr["net_total"]), (1, 2, 500))

        print("── за сегодня — отказ ──────────────────────────────────────────")
        st, r = await post("/api/owner/checklist/mark", {"day": D2, "item": "cash:jvc", "done": True, "only": D2})
        eq("забрать за сегодня нельзя", (st, r.get("error")), (409, "shift_open"))
        st, r = await post("/api/owner/cash-round/fact", {"day": D2, "district": "jvc", "fact": 220})
        eq("«сдали по факту» за сегодня нельзя", (st, r.get("error")), (409, "shift_open"))
        st, _ = await post("/api/owner/cash-round/fact", {"day": D1, "district": "jvc", "fact": 1000, "page": D2})
        eq("за вчера — можно", st, 200)
        eq("сегодняшней отметки по-прежнему нет", (await db.checklist_get(D2)).get("cash:jvc"), None)

        print("── «соберу завтра» ─────────────────────────────────────────────")
        st, r = await post("/api/owner/cash-round/later", {"day": D2, "on": True})
        eq("отложен только Бизнес Бей: у JVC перенос уже забран", r.get("changed"), ["bbay"])
        await post("/api/owner/cash-round/later", {"day": D2, "on": False})

        print("── район закрыт оператором, водитель ещё на смене ──────────────")
        await db.shift_close(D2, "jvc", {"closed_at": NOW, "by_name": "Оператор"})
        j = рн(await get("/api/owner/cash-round", day=D2), "jvc")
        eq("смена идёт, держит её водитель — назван", (j["shift"], j["on"]), ("open", ["Водитель А"]))

        print("── водитель закрыл смену — сегодняшнее появилось ───────────────")
        await db.save_driver_day(D2, "Водитель А", {"shift_close_at": NOW})
        cr = await get("/api/owner/cash-round", day=D2)
        j = рн(cr, "jvc")
        eq("смена закрыта, выручка района = сегодняшнее", (j["shift"], j["net_all"], j["done"], j["got"]), ("closed", 300 - MEAL, False, False))
        eq("вчерашнее так и стоит забранным", [(c["day"], c["net"]) for c in j["taken"]], [(D1, 1000)])
        eq("итоги: сегодняшнее JVC + вчерашнее Бизнес Бея, расход — питание", (cr["net_total"], cr["spend_total"]), (300 - MEAL + 500, MEAL))
        st, _ = await post("/api/owner/cash-round/fact", {"day": D2, "district": "jvc", "fact": 200})
        eq("«сдали по факту» за сегодня теперь принимается", st, 200)
        await post("/api/owner/cash-round/fact", {"day": D2, "district": "jvc", "fact": None})

        print("── прошлые сутки с забытым закрытием — отработаны ──────────────")
        await db.shift_open(D1, "bbay", {"opened_at": NOW, "operator": "Оператор", "drivers": {"Водитель Б": True}})
        await db.save_driver_day(D1, "Водитель Б", {"working": True, "shift_open_at": NOW, "shift_close_at": None})
        b = рн(await get("/api/owner/cash-round", day=D1), "bbay")
        eq("на странице вчерашнего дня Бизнес Бей собирается", (b["shift"], b["net_all"], b["empty"]), ("closed", 500 - MEAL, False))
        b = рн(await get("/api/owner/cash-round", day=D2), "bbay")
        eq("и сегодня едет переносом", (b["shift"], [(c["day"], c["net"]) for c in b["carry"]]), ("closed", [(D1, 500 - MEAL)]))

        print("── старая отметка дня при идущей смене ─────────────────────────")
        await db.save_driver_day(D2, "Водитель А", {"shift_close_at": None})      # смена снова идёт
        await db.checklist_set(D2, "cash:jvc", True, "1")                          # отметка до правила
        j = рн(await get("/api/owner/cash-round", day=D2), "jvc")
        eq("отметка видна: получен, не got — общая кнопка «снять» есть", (j["shift"], j["done"], j["got"]), ("open", True, False))
        st, _ = await post("/api/owner/checklist/mark", {"day": D2, "item": "cash:jvc", "done": False})
        eq("снята", (st, (await db.checklist_get(D2)).get("cash:jvc")), (200, None))
        j = рн(await get("/api/owner/cash-round", day=D2), "jvc")
        eq("после снятия: забранное вчерашнее на месте, район получен (got)", (j["done"], j["got"], [c["day"] for c in j["taken"]]), (True, True, [D1]))

        print("── «Вернуть за вчера» при идущей смене ─────────────────────────")
        st, _ = await post("/api/owner/checklist/mark", {"day": D2, "item": "cash:jvc", "done": False, "only": D1})
        j = рн(await get("/api/owner/cash-round", day=D2), "jvc")
        eq("вчерашнее вернулось в перенос, район снова ждёт", (st, [c["day"] for c in j["carry"]], j["done"], j["net_all"]), (200, [D1], False, 1000))

    print("\nвсё прошло" if not FAIL else f"\nПРОВАЛЕНО: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
