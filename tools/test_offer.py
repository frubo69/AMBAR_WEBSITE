#!/usr/bin/env python3
"""Персональное предложение (offers.py): ссылка из оффера, по которой корзина
собирается сама и скидка применяется сама, а заказ по коду — один (владелец,
31 окт 2026: «заказов по одному офферу программа должна пропускать только
один; вино в подарок уже идёт по акции — два бесплатно не отдавать; операторы
должны видеть, что заказ по спецпредложению; документ чей угодно; срок 3 дня»).

  • код: offer_XXXX / xxxx → XXXX, мусор → '';
  • документ: срок 3 дня, подарок; состояние active / used / expired / unknown;
  • наружу — состав, подарок, скидка, срок; кто заказал и номер заказа — нет;
  • набор: всё из оффера в нужном количестве и той же фасовкой пива; лишнее не
    мешает; подарочная строка в набор не засчитывается;
  • клиентские строки подарка, скидки и с минусом отбрасываются;
  • apply: строки клиента + подарок (gift, offer_gift) + скидка (offer, is_custom,
    минус); config_gift второй бутылки не кладёт; без подарка оффера — Shiraz как обычно;
  • keep_lines: правка состава (панель, водитель) скидку не снимает, эхо не удваивает;
  • итог заказа со строкой скидки — минус учтён; порог подарка считается без неё;
  • склад: строка скидки не списывается; панель: _build_items её пропускает;
  • база: claim атомарно — второй заказ по коду не проходит, истёкший не занимается,
    release только своим заказом; update_order(status=…) двигает оффер:
    отмена/отказ → new, доставка → delivered, чужой заказ не трогает;
  • GET /api/offer/{code}: active — состав; used / unknown — только состояние.

    python3 tools/test_offer.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timezone, timedelta                  # noqa: E402
from aiohttp import web                                             # noqa: E402
from aiohttp.test_utils import TestClient, TestServer                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                     # noqa: E402
import db, offers, config_gift, stock_routes                         # noqa: E402
import operator_routes as opr, api_server as api                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

CAT = api._load_catalog_by_id()                     # настоящий каталог: цены и наличие как в проде
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
CART = [{"id": "p6", "qty": 1, "price": 250, "line_total": 250},
        {"id": "p43", "qty": 1, "pcs": 12, "price": 150, "line_total": 150}]


def doc_(code="TESTCODE", now=NOW, **kw):
    return offers.new_doc([{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1, "pcs": 12}], 200,
                          {"id": "p97", "qty": 1}, total=200, now=now, code=code, **kw)


def pure():
    print("код")
    eq("offer_ в любом регистре", offers.norm_code("offer_ab12cd34"), "AB12CD34")
    eq("голый код", offers.norm_code(" ab12cd34 "), "AB12CD34")
    eq("короткий", offers.norm_code("offer_ab"), "")
    eq("пусто", offers.norm_code(None), "")
    eq("мусор", offers.norm_code("AB12CD34' or 1=1"), "")
    eq("новый код из алфавита без 0/O/1/I", all(ch in offers._ALPHABET for ch in offers.new_code()), True)
    eq("длина кода", len(offers.new_code()), 8)

    print("документ")
    d = doc_()
    eq("состав", d["items"], [{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1, "pcs": 12}])
    eq("статус", d["status"], "new")
    eq("срок 3 дня", d["expires_at"], (NOW + timedelta(days=3)).isoformat())
    eq("подарок", d["gift"], {"id": "p97", "qty": 1})
    eq("скидка целым", d["discount"], 200)
    eq("не занят", (d["order_id"], d["used_by"], d["used_at"]), (None, None, None))
    try:
        offers.new_doc([], 10); eq("пустой состав — ошибка", False, True)
    except ValueError:
        eq("пустой состав — ошибка", True, True)
    try:
        offers.new_doc([{"id": "p6", "qty": 1}], -5); eq("минус скидка — ошибка", False, True)
    except ValueError:
        eq("минус скидка — ошибка", True, True)

    print("состояние")
    eq("active", offers.state(d, NOW), "active")
    eq("expired ровно в срок", offers.state(d, NOW + timedelta(days=3)), "expired")
    eq("за минуту до срока", offers.state(d, NOW + timedelta(days=3, minutes=-1)), "active")
    eq("used (ordered)", offers.state({**d, "status": "ordered"}, NOW), "used")
    eq("used (delivered)", offers.state({**d, "status": "delivered"}, NOW), "used")
    eq("used важнее срока", offers.state({**d, "status": "delivered"}, NOW + timedelta(days=9)), "used")
    eq("unknown", offers.state(None), "unknown")

    print("наружу")
    v = offers.public_view(d, NOW)
    eq("поля", sorted(v), ["code", "discount", "expires_at", "gift", "items", "ok", "state", "title"])
    eq("title по умолчанию", v["title"], "Personal offer")
    eq("used — только состояние", offers.public_view({**d, "status": "ordered"}, NOW), {"ok": True, "state": "used"})
    eq("expired — только состояние", offers.public_view(d, NOW + timedelta(days=4)), {"ok": True, "state": "expired"})
    eq("unknown", offers.public_view(None), {"ok": True, "state": "unknown"})

    print("набор")
    eq("ровно", offers.covered(d, CART), True)
    eq("лишнее не мешает", offers.covered(d, CART + [{"id": "p1", "qty": 3}]), True)
    eq("больше — можно", offers.covered(d, [{"id": "p6", "qty": 2}, {"id": "p43", "qty": 1, "pcs": 12}]), True)
    eq("другая фасовка пива", offers.covered(d, [{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1, "pcs": 24}]), False)
    eq("без фасовки", offers.covered(d, [{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1}]), False)
    eq("не хватает позиции", offers.covered(d, [{"id": "p6", "qty": 1}]), False)
    eq("две строки одной позиции складываются", offers.covered(d, [{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1, "pcs": 12},]
                                                            + [{"id": "p6", "qty": 0}]), True)
    d2 = offers.new_doc([{"id": "p97", "qty": 2}], 50, now=NOW, code="TWOWINES")
    eq("подарочная строка в набор не идёт", offers.covered(d2, [{"id": "p97", "qty": 1}, {"id": "p97", "qty": 1, "gift": True}]), False)
    eq("пустая корзина", offers.covered(d, []), False)

    print("строки клиента")
    eq("подарок, скидка и минус — вон", offers.clean_client_items(
        [{"id": "p6", "qty": 1, "price": 250, "line_total": 250},
         {"id": "p105", "qty": 1, "gift": True, "price": 0},
         {"id": "offer", "qty": 1, "price": -200, "line_total": -200},
         {"id": "p1", "qty": 1, "price": -5, "line_total": -5},
         {"id": "p1", "qty": 1, "price": 100, "line_total": 100, "offer": True},
         {"id": "p1", "qty": 1, "price": "x"}, "junk", None]),
        [{"id": "p6", "qty": 1, "price": 250, "line_total": 250}])

    print("apply")
    out = offers.apply(CART, d, CAT, "ru")
    eq("четыре строки", len(out), 4)
    eq("клиентские как были", out[:2], CART)
    eq("подарок", out[2], {"id": "p97", "name": CAT["p97"]["name"], "qty": 1, "price": 0, "line_total": 0,
                            "gift": True, "offer_gift": True})
    eq("скидка", out[3], {"id": "offer", "name": "Персональное предложение", "qty": 1, "price": -200,
                           "line_total": -200, "offer": True, "is_custom": True, "code": "TESTCODE"})
    eq("скидка по-английски", offers.apply(CART, d, CAT, "en")[3]["name"], "Personal offer")
    eq("клиентский подарок при apply тоже вон", len(offers.apply(CART + [{"id": "p105", "gift": True, "qty": 1}], d, CAT)), 4)
    d0 = offers.new_doc([{"id": "p6", "qty": 1}], 0, now=NOW, code="NODISC00")
    eq("нулевая скидка — строки нет", [i["id"] for i in offers.apply(CART, d0, CAT)], ["p6", "p43"])
    eq("второй бутылки от config_gift нет", [i["id"] for i in config_gift.apply(out, 2050, CAT) if i.get("gift")], ["p97"])
    no_gift = offers.apply(CART, {**d, "gift": None}, CAT)
    eq("без подарка оффера — Shiraz как обычно",
       [i["id"] for i in config_gift.apply(no_gift, 2050, CAT) if i.get("gift")], ["p105"])
    eq("… и скидка на месте", [i["id"] for i in config_gift.apply(no_gift, 2050, CAT) if i.get("offer")], ["offer"])

    print("правка состава")
    new = [{"id": "p6", "qty": 2, "price": 250, "line_total": 500},
           {"id": "offer", "qty": 1, "price": -200, "line_total": -200}]
    eq("скидка возвращается, эхо не удваивается", offers.keep_lines(out, new), [new[0], out[3]])
    eq("без скидки в старом — ничего не добавляется", offers.keep_lines(CART, new[:1]), new[:1])
    eq("эхо с флагом тоже вон", offers.keep_lines(CART, [{"id": "p1", "qty": 1, "offer": True}]), [])
    kept = offers.keep_lines(out, opr._keep_gift(out, [{"id": "p6", "qty": 2, "price": 250, "line_total": 500}]))
    eq("панель: подарок и скидка переживают правку", [(i["id"], i.get("gift", False), i.get("offer", False)) for i in kept],
       [("p6", False, False), ("p97", True, False), ("offer", False, True)])

    print("заказ")
    eq("поля на заказе", offers.order_fields(d), {"code": "TESTCODE", "discount": 200, "title": "Personal offer",
                                                   "gift": {"id": "p97", "qty": 1}})
    eq("без подарка — без поля gift", "gift" in offers.order_fields({**d, "gift": None}), False)
    eq("отмена → new", offers.follow_status("cancelled"), "new")
    eq("отказ → new", offers.follow_status("declined"), "new")
    eq("доставка → delivered", offers.follow_status("delivered"), "delivered")
    eq("принят → ordered", offers.follow_status("approved"), "ordered")

    print("склад и панель")
    eq("строка скидки не списывается", stock_routes.sale_item(out[3], CAT), ("", 0.0))
    items, err = opr._build_items([{"id": "p6", "qty": 1}, {"id": "offer", "qty": 1, "offer": True},
                                   {"id": "offer", "qty": 1}], "app")
    eq("_build_items пропускает скидку", (err, [i["id"] for i in items]), ("", ["p6"]))


async def with_db():
    client = AsyncMongoMockClient(); db._db = client["ambar_test"]
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    d = doc_(now=now)
    out = offers.apply(CART, d, CAT, "ru")

    print("итог")
    eq("итог со скидкой", await api._recompute_order_total_aed(out, 0), 250 + 150 - 200)
    eq("чаевые сверху", await api._recompute_order_total_aed(out, 20), 250 + 150 - 200 + 20)
    eq("цена клиента не верится, скидка верится",
       await api._recompute_order_total_aed([{**CART[0], "price": 1, "line_total": 1}, out[3]], 0), 250 - 200)
    eq("порог подарка без скидки", await api._goods_subtotal_aed(out), 400.0)

    print("база")
    await db.offer_create(d)
    eq("get", (await db.offer_get("TESTCODE"))["status"], "new")
    eq("get пустого", await db.offer_get(""), None)
    got = await db.offer_claim("TESTCODE", "AMB1", 7, now_iso)
    eq("claim", (got["status"], got["order_id"], got["used_by"], got["used_at"]), ("ordered", "AMB1", 7, now_iso))
    eq("второй claim", await db.offer_claim("TESTCODE", "AMB2", 8, now_iso), None)
    eq("state после claim", offers.state(await db.offer_get("TESTCODE"), now), "used")
    eq("release чужим заказом", await db.offer_release("TESTCODE", "AMB2"), False)
    eq("release своим", await db.offer_release("TESTCODE", "AMB1"), True)
    eq("release повторно", await db.offer_release("TESTCODE", "AMB1"), False)
    eq("снова можно", (await db.offer_claim("TESTCODE", "AMB3", 9, now_iso))["order_id"], "AMB3")
    old = doc_(code="OLDCODE1", now=now - timedelta(days=4))
    await db.offer_create(old)
    eq("истёкший не занимается", await db.offer_claim("OLDCODE1", "AMB4", 1, now_iso), None)
    eq("нет такого", await db.offer_claim("NOPE1234", "AMB4", 1, now_iso), None)

    print("статус заказа ведёт оффер")
    await db._db.orders.insert_one({"order_id": "AMB3", "status": "pending", "offer": offers.order_fields(d)})
    await db.update_order("AMB3", status="approved")
    eq("принят — занят", (await db.offer_get("TESTCODE"))["status"], "ordered")
    await db.update_order("AMB3", status="cancelled")
    eq("отменён — в оборот", (await db.offer_get("TESTCODE"))["status"], "new")
    eq("… и claim проходит", (await db.offer_claim("TESTCODE", "AMB5", 1, now_iso))["order_id"], "AMB5")
    await db._db.orders.insert_one({"order_id": "AMB5", "status": "pending", "offer": offers.order_fields(d)})
    await db.update_order("AMB5", status="delivered")
    eq("доставлен — закрыт", (await db.offer_get("TESTCODE"))["status"], "delivered")
    await db.update_order("AMB3", status="delivered")
    eq("старый заказ оффер не трогает", (await db.offer_get("TESTCODE"))["status"], "delivered")
    await db.update_order("AMB5", status="cancelled")
    eq("отмена доставленного возвращает код", (await db.offer_get("TESTCODE"))["status"], "new")
    await db._db.orders.insert_one({"order_id": "AMB9", "status": "pending"})
    await db.update_order("AMB9", status="delivered")
    eq("заказ без оффера — ничего", (await db.offer_get("TESTCODE"))["status"], "new")
    await db.update_order("AMB5", driver="Али")
    eq("правка без статуса — ничего", (await db.offer_get("TESTCODE"))["status"], "new")

    print("GET /api/offer")
    app = web.Application()
    app.router.add_route("OPTIONS", "/api/offer/{code}", api.handle_offer_get)
    app.router.add_get("/api/offer/{code}", api.handle_offer_get)
    async with TestClient(TestServer(app)) as cl:
        j = await (await cl.get("/api/offer/offer_testcode")).json()
        eq("active: состав, подарок, скидка, срок", (j["state"], j["code"], j["discount"], j["items"], j["gift"], j["expires_at"]),
           ("active", "TESTCODE", 200, d["items"], d["gift"], d["expires_at"]))
        eq("чужого нет", any(k in j for k in ("order_id", "used_by", "used_at", "note", "status")), False)
        eq("истёкший", (await (await cl.get("/api/offer/OLDCODE1")).json()), {"ok": True, "state": "expired"})
        eq("нет такого", (await (await cl.get("/api/offer/NOPE1234")).json()), {"ok": True, "state": "unknown"})
        eq("мусор", (await (await cl.get("/api/offer/%27%20or")).json()), {"ok": True, "state": "unknown"})
        await db.offer_claim("TESTCODE", "AMB7", 1, now_iso)
        eq("занят", (await (await cl.get("/api/offer/TESTCODE")).json()), {"ok": True, "state": "used"})
        eq("OPTIONS", (await cl.options("/api/offer/TESTCODE")).status, 200)


pure()
asyncio.run(with_db())
print("\nFAIL:", FAIL if FAIL else "нет")
sys.exit(1 if FAIL else 0)
