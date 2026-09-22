"""Чай оператора в его приложении (владелец, 22 сен 2026: «чтобы операторы у
себя в приложении видели свой чай, который они заработали»). mongomock +
настоящие db, op_tea и ручка /api/operator/tea:
  • чай — ставка «чайной» позиции каталога × бутылки плюс чай клиента, только
    с доставленных заказов и только своего учётного дня;
  • кому — оператору района из открытия смены того дня, без открытия — тому,
    на кого отправлен заказ, иначе нынешней раскладке;
  • сегодня: сумма, разбивка по районам, «в работе» — заказы, которые везут;
  • месяц: дни сверху вниз, в каждом — заказы с бутылками;
  • телефон оператора видит только свой чай (as чужого не действует), за
    планшетом — выбранного, чужое имя — отказ; старший — всех строками и любого
    по op; brief=1 — одна сегодняшняя сумма; будущий месяц — текущий."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, bizday, op_tea, cash_math
import operator_routes as opr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

TODAY = "2026-09-22"
bizday.biz_day = lambda *a, **k: TODAY
WINE, WINE2, VODKA = "p104", "p109", "p1"          # две «чайные» (50) и обычная
RATES = cash_math.tea_rates()
assert RATES.get(WINE) == 50 and RATES.get(WINE2) == 50 and not RATES.get(VODKA), RATES


def order(oid, day, office, items, status="delivered", **kw):
    at = f"{day}T15:{len(oid) % 50:02d}:00"             # 19:xx по Дубаю — внутри учётного дня
    return {"order_id": oid, "timestamp": at, "confirmed_at": at, "delivered_at": at, "day": day,
            "status": status, "office_id": office, "tip": 0, "total": 500,
            "items": [{"id": i, "name": n, "qty": q, "price": 100} for i, n, q in items], **kw}


async def call(query, uid=900, test=False):
    r = make_mocked_request("GET", "/api/operator/tea?" + "&".join(f"{k}={v}" for k, v in query.items()))
    r["op_user"] = {"id": uid, "first_name": "x"}; r["op_id"] = uid; r["op_test"] = test
    h = opr.handle_tea
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_tea"]; d = db._db
    async def fresh(): return opr._districts()
    opr._fresh_districts = fresh
    opr._staff_mod.OPERATOR_BY_ID = {501: "Умар"}           # телефон Умара; 900 — планшет
    await d.orders.insert_many([
        # 22-е, сегодня: B1 Умара — вино ×2 и водка; B5 Умара — вино; B2 Джанабиля — вино
        order("A1", TODAY, "jvc", [(WINE, "Oyster Bay", 2), (VODKA, "Absolut", 3)]),
        order("A2", TODAY, "tecom", [(WINE2, "Saint Leon", 1)]),
        order("A3", TODAY, "bbay", [(WINE, "Oyster Bay", 1)]),
        order("A4", TODAY, "jvc", [(VODKA, "Absolut", 1)]),                         # без чая
        order("A5", TODAY, "jvc", [(WINE, "Oyster Bay", 1)], status="cancelled"),   # отменён
        order("A6", TODAY, "jvc", [(WINE, "Oyster Bay", 2)], status="approved"),    # везут
        order("A7", TODAY, "bbay", [(WINE, "Oyster Bay", 1)], status="pending"),    # чужой, ждёт
        # 21-е: смену B1 открыли на Джанабиля (перестановка) — его чай, не Умара
        order("B1", "2026-09-21", "jvc", [(WINE, "Oyster Bay", 1)]),
        # 20-е: смена B1 Умара; чай клиента тоже считается
        order("C1", "2026-09-20", "jvc", [(WINE, "Oyster Bay", 1)], tip=20),
        # 19-е: смены нет — по заказу (Фарух), а без него — по раскладке (Умар)
        order("D1", "2026-09-19", "jvc", [(WINE, "Oyster Bay", 1)], dispatch_operator="Фарух"),
        order("D2", "2026-09-19", "jvc", [(WINE2, "Saint Leon", 2)]),
        # 31 авг — другой месяц, в сентябрь не входит
        order("E1", "2026-08-31", "jvc", [(WINE, "Oyster Bay", 4)]),
        # тест-заказ боевой чай не трогает
        order("T1", TODAY, "jvc", [(WINE, "Oyster Bay", 5)], test=True),
    ])
    await d.shift_opens.insert_many([
        {"_id": f"{TODAY}:jvc", "day": TODAY, "district": "jvc", "operator": "Умар"},
        {"_id": f"{TODAY}:tecom", "day": TODAY, "district": "tecom", "operator": "Умар"},
        {"_id": f"{TODAY}:bbay", "day": TODAY, "district": "bbay", "operator": "Джанабиль"},
        {"_id": "2026-09-21:jvc", "day": "2026-09-21", "district": "jvc", "operator": "Джанабиль"},
        {"_id": "2026-09-20:jvc", "day": "2026-09-20", "district": "jvc", "operator": "Умар"},
        {"_id": "*:gate", "day": "2026-09-01", "district": "*", "since": "2026-09-01"},
    ])

    print("── откуда и кому ───────────────────────────────────────────────")
    ops = await db.shift_opens_between("2026-09-01", TODAY)
    eq("открытия смен одним запросом, служебные записи мимо", sorted(ops),
       sorted([(TODAY, "jvc"), (TODAY, "tecom"), (TODAY, "bbay"), ("2026-09-21", "jvc"), ("2026-09-20", "jvc")]))
    data = await op_tea.month("2026-09")
    who = {r["id"]: (r["day"], r["who"], r["aed"]) for r in data["rows"]}
    eq("в месяце только доставленные чайные заказы сентября, без теста", sorted(who),
       ["A1", "A2", "A3", "B1", "C1", "D1", "D2"])
    eq("сегодня B1 — Умара, 2 бутылки × 50; водка чая не даёт", who["A1"], (TODAY, "Умар", 100))
    eq("21-е по открытию смены — Джанабиля", who["B1"], ("2026-09-21", "Джанабиль", 50))
    eq("20-е: 50 за бутылку и 20 чая клиента", who["C1"], ("2026-09-20", "Умар", 70))
    eq("19-е без смены: по заказу — Фарух", who["D1"][1], "Фарух")
    eq("19-е без смены и без отметки в заказе — по раскладке, Умар", who["D2"][1:], ("Умар", 100))
    eq("в работе: заказы, которые везут и ждут", sorted((x["who"], x["aed"]) for x in data["live"]),
       [("Джанабиль", 50), ("Умар", 100)])

    print("── экран оператора ─────────────────────────────────────────────")
    v = op_tea.person(data, "Умар")
    eq("сегодня 150: B1 100, B5 50", (v["day"]["aed"], v["day"]["bottles"], v["day"]["by"]),
       (150, 3, [{"code": "B1", "aed": 100}, {"code": "B5", "aed": 50}]))
    eq("в работе у Умара — 100, один заказ", v["day"]["live"], {"aed": 100, "n": 1})
    eq("месяц: 150 + 70 + 100 = 320, три дня", (v["total"]["aed"], v["total"]["days"]), (320, 3))
    eq("дни — сверху сегодня", [x["day"] for x in v["days"]], [TODAY, "2026-09-20", "2026-09-19"])
    a1 = next(o for o in v["days"][0]["orders"] if o["id"] == "A1")
    eq("заказ строкой: район и за какие бутылки", (a1["code"], a1["lines"], a1["bottles"]),
       ("B1", [{"name": "Oyster Bay", "qty": 2, "aed": 100}], 2))
    c1 = v["days"][1]["orders"][0]
    eq("чай клиента — отдельной строкой", [x["name"] for x in c1["lines"]], ["Oyster Bay", "Чаевые клиента"])

    print("── ручка: кто что видит ────────────────────────────────────────")
    st, b = await call({"as": "Джанабиль"}, uid=501)
    eq("телефон Умара: as чужого не действует — только его чай", (st, b["of"], b["day"]["aed"], b["codes"]),
       (200, "Умар", 150, ["B1", "B5"]))
    st, b = await call({"as": "Джанабиль"})
    eq("планшет: выбранный Джанабиль — сегодня 50, месяц 100", (st, b["of"], b["day"]["aed"], b["total"]["aed"]),
       (200, "Джанабиль", 50, 100))
    st, b = await call({"as": "Кто-то"})
    eq("чужое имя — отказ", (st, b.get("error")), (403, "not_yours"))
    st, b = await call({"as": "Парвиз"})
    eq("старший — все районные операторы строками",
       (st, b["senior"], [(o["name"], o["today"], o["month"]) for o in b["ops"]]),
       (200, True, [("Умар", 150, 320), ("Джанабиль", 50, 100), ("Фарух", 0, 50)]))
    eq("у старшего итог — все районы", (b["day"]["aed"], b["total"]["aed"]), (200, 470))
    st, b = await call({"as": "Парвиз", "op": "Джанабиль"})
    eq("старший открывает любого по op", (st, b["of"], b["total"]["aed"]), (200, "Джанабиль", 100))
    st, b = await call({"as": "Умар", "brief": "1"})
    eq("brief=1 — одна сегодняшняя сумма", (st, sorted(b), b["day"]["aed"]), (200, ["day", "senior", "who"], 150))
    st, b = await call({"as": "Умар", "month": "2026-10"})
    eq("будущий месяц — текущий", (st, b["month"]), (200, "2026-09"))
    st, b = await call({"as": "Умар", "month": "2026-08"})
    eq("август: 31-е — 200, сегодняшнего и «в работе» нет",
       (b["month"], b["total"]["aed"], b["day"]["aed"], b["day"]["live"]["aed"]), ("2026-08", 200, 0, 0))
    st, b = await call({"as": "Тест"}, test=True)
    eq("тест-оператор: только тест-заказы", (st, b["day"]["aed"], b["total"]["aed"]), (200, 250, 250))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
