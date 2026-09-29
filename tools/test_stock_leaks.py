"""Каждая бутылка, ушедшая с полки, уходит и со склада — ровно один раз.

Владелец, 30 сен 2026: «мы же за это всех спрашиваем? А как мы будем это
делать, если не уверены, что сами правильно считаем». Повод — заказ на
1150 AED с вином в подарок: оператор сменил водителя, и подарок стал платной
бутылкой (итог 1250).

Здесь — все пути, которыми бутылка покидает полку или остаётся на ней, и что
после каждого показывает склад (stock_routes._district_base, тот же расчёт,
что карточка «Склад», заявка и ревизия):

  продажа · подарок · заказ «без оплаты» · пачка пива из бота (p31_12) ·
  пачка из приложения (pcs) · тест-заказ · отменённый · возвращённый в
  доставку · списание ждёт / согласовано / отклонено · продажа глубже нуля

и отдельно — что правка заказа оператором и пересчёт в боте подарок не
трогают.

    python3 tools/test_stock_leaks.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as SR                                     # noqa: E402

DAY = "2026-09-21"
T0 = datetime.now(timezone.utc) - timedelta(hours=6)
WINE, VODKA, BEER = "p105", "p1", "p31"
COUNT = {WINE: 10, VODKA: 10, BEER: 10}           # пиво — в коробках по 24
FAIL = []
N = [0]


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "")


async def order(items, status="delivered", office="jvc", at=60, **kw):
    N[0] += 1
    doc = {"order_id": f"T{N[0]:04d}", "status": status, "office_id": office,
           "timestamp": iso(at - 20), "items": items, **kw}
    if status == "delivered":
        doc.setdefault("delivered_at", iso(at))
    await db._db.orders.insert_one(doc)
    return doc["order_id"]


async def have(o="jvc"):
    SR.base_drop()
    h = ((await SR._district_base(DAY)).get(o) or {}).get("have_exact") or {}
    return {p: float(h.get(p, 0)) for p in (WINE, VODKA, BEER)}


async def clean():
    await db._db.orders.delete_many({})
    await db._db.writeoffs.delete_many({})


def left(**minus):
    return {p: float(COUNT[p] - minus.get(n, 0)) for p, n in ((WINE, "wine"), (VODKA, "vodka"), (BEER, "beer"))}


async def main():
    db._db = AsyncMongoMockClient()["ambar_leaks"]
    cat = SR._catalog()
    SR._biz_day = lambda *a, **k: DAY
    for o in ("jvc", "tecom"):
        await db.save_stock_count(o, "2026-09-20", {
            "district": o, "day": "2026-09-20", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": p, "name": cat[p]["name"], "price": 100,
                                         "unit": SR._unit(cat[p]), "actual": q, "counted": True}
                                        for p, q in COUNT.items()]})
    eq("пересчёт — отправная точка", await have(), left())

    print("\nПродажа и подарок")
    await clean()
    await order([{"id": VODKA, "qty": 2, "price": 100}])
    eq("две бутылки проданы", await have(), left(vodka=2))
    await clean()
    await order([{"id": VODKA, "qty": 11, "price": 100},
                 {"id": WINE, "qty": 1, "price": 0, "line_total": 0, "gift": True}])
    eq("подарок списан вместе с заказом, один раз", (await have())[WINE], 9.0)
    await clean()
    await order([{"id": WINE, "qty": 1, "price": 100},
                 {"id": WINE, "qty": 1, "price": 0, "gift": True}])
    eq("купил такое же вино и получил подарок — ушли обе", (await have())[WINE], 8.0)
    await clean()
    await order([{"id": WINE, "qty": 3, "price": 100}], payment_method="free")
    eq("заказ «без оплаты» — бутылки ушли", (await have())[WINE], 7.0)
    await clean()
    await order([{"id": WINE, "qty": 3, "price": 100}], payment_method="debt")
    eq("заказ в долг — бутылки ушли", (await have())[WINE], 7.0)

    print("\nПиво")
    await clean()
    await order([{"id": BEER, "qty": 1, "pcs": 12, "price": 100}])
    eq("пачка 12 из приложения — полкоробки", (await have())[BEER], 9.5)
    await clean()
    await order([{"id": BEER + "_12", "qty": 2, "price": 100}])
    eq("две пачки по 12 из бота оператора — коробка", (await have())[BEER], 9.0)
    await clean()
    await order([{"id": BEER, "qty": 1, "pcs": 24, "price": 200}])
    eq("пачка 24 — коробка", (await have())[BEER], 9.0)

    print("\nЧто со склада НЕ уходит")
    await clean()
    await order([{"id": VODKA, "qty": 2}], test=True)
    eq("тест-заказ", await have(), left())
    for st in ("pending", "approved", "cancelled", "declined"):
        await clean()
        await order([{"id": VODKA, "qty": 2}], status=st)
        eq(f"заказ «{st}»", await have(), left())
    await clean()
    await order([{"id": VODKA, "qty": 2}], at=-30)
    eq("доставлен ДО пересчёта — пересчёт его уже видел", await have(), left())
    await clean()
    await order([{"id": VODKA, "qty": 2}], office="tecom")
    eq("продажа другого района", await have(), left())

    print("\nВернули в доставку и доставили снова")
    await clean()
    oid = await order([{"id": VODKA, "qty": 2}])
    await db._db.orders.update_one({"order_id": oid}, {"$set": {"status": "approved"},
                                                       "$unset": {"delivered_at": ""}})
    eq("вернули в доставку — бутылки снова на складе", await have(), left())
    await db._db.orders.update_one({"order_id": oid}, {"$set": {"status": "delivered",
                                                                "delivered_at": iso(90)}})
    eq("доставили — списаны один раз", await have(), left(vodka=2))

    print("\nСписания")
    now = T0 + timedelta(minutes=30)
    for state, ждём in (("pending", 0), ("no", 0), ("ok", 1), (None, 1)):
        await clean()
        w = {"district": "jvc", "item": VODKA, "qty": 1, "at": now, "kind": "бой"}
        if state:
            w["state"] = state
        await db._db.writeoffs.insert_one(w)
        eq(f"списание «{state or 'старое, без решения'}»", await have(), left(vodka=ждём))
    await clean()
    await db._db.writeoffs.insert_one({"district": "jvc", "item": VODKA, "qty": 1, "at": now,
                                       "state": "ok", "src": "audit"})
    eq("недостача ревизии второй раз не вычитается", await have(), left())

    print("\nПродано больше, чем числилось")
    await clean()
    await order([{"id": WINE, "qty": 12}])
    eq("склад не уходит в минус", (await have())[WINE], 0.0)

    print("\nПравка заказа подарок не трогает")
    import operator_routes as opr
    paid = opr._build_items([{"id": VODKA, "qty": 11}], "app")[0]
    gift = {"id": WINE, "name": cat[WINE]["name"], "qty": 1, "price": 0, "line_total": 0, "gift": True}
    было = paid + [gift]
    # Панель о подарке не знает и присылает его обычной строкой.
    эхо = opr._build_items([{"id": VODKA, "qty": 11}, {"id": WINE, "qty": 1}], "app")[0]
    стало = opr._keep_gift(было, эхо)
    eq("смена водителя: состав тот же", [(i["id"], i["qty"], i["price"], bool(i.get("gift"))) for i in стало],
       [(VODKA, 11, paid[0]["price"], False), (WINE, 1, 0, True)])
    eq("и итог тот же", await opr._order_total_for({"source": "app", "tip": 0}, стало),
       await opr._order_total_for({"source": "app", "tip": 0}, было))
    эхо = opr._build_items([{"id": VODKA, "qty": 11}, {"id": WINE, "qty": 3}], "app")[0]
    стало = opr._keep_gift(было, эхо)
    eq("оператор добавил две такие же — две платные и подарок",
       sorted((i["id"], i["qty"], bool(i.get("gift"))) for i in стало),
       sorted([(VODKA, 11, False), (WINE, 2, False), (WINE, 1, True)]))
    эхо = opr._build_items([{"id": VODKA, "qty": 5}], "app")[0]
    eq("подарок не прислали вовсе — он остаётся",
       [(i["id"], bool(i.get("gift"))) for i in opr._keep_gift(было, эхо)], [(VODKA, False), (WINE, True)])
    eq("заказ без подарка не меняется", opr._keep_gift(paid, эхо), эхо)
    import operator_bot as ob
    o = ob.recalc_order({"items": [dict(paid[0]), dict(gift, price=100, line_total=100)], "tip": 0})
    eq("пересчёт в боте оператора: подарок бесплатен",
       (o["items"][1]["line_total"], o["total"]), (0, o["items"][0]["line_total"]))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
