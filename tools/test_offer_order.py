#!/usr/bin/env python3
"""Заказ по персональному предложению через POST /api/order (api_server +
offers.py), на mongomock, с заглушками Telegram:

  • полный набор + код → 200; на заказе offer{code, discount, gift}, в составе
    строки клиента + подарок (gift, offer_gift) + скидка (минус), итог = прайс − скидка,
    второй бутылки от config_gift нет; операторам в сообщении «СПЕЦПРЕДЛОЖЕНИЕ
    КОМПАНИИ», скидка и код; владельцу — offer; оффер занят этим заказом;
  • тот же код второй раз → 409 offer_used; не весь набор → 400 offer_items_missing;
    криптой → 400 offer_no_crypto; истёк → 409 offer_expired; нет такого → 409 offer_unknown;
    при отказе заказа нет и код не занят;
  • без кода: присланные клиентом подарок, скидка и минус отбрасываются, подарок
    за тысячу — как обычно;
  • отмена заказа возвращает код, заказ по нему проходит снова; сбой записи
    заказа отпускает код.

    python3 tools/test_offer_order.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
for k in ("OPERATOR_BOT_TOKEN", "DRIVER_BOT_TOKEN", "AMBAR_OWNER_BOT_TOKEN", "BOT_TOKEN"):
    os.environ[k] = "x"
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timezone, timedelta                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                  # noqa: E402
from mongomock_motor import AsyncMongoMockClient                     # noqa: E402
import db, offers, api_server, op_route, owner_routes as owr         # noqa: E402
import tobacco, config_offices, geo_watch                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

SENT = []        # сообщения операторам
OWNER = []       # notify_new_order kwargs
async def _none(*a, **k): return None
async def _tg(token, chat_id, text, *a, **k): return {"ok": True, "result": {"message_id": 1}}
async def _ops(text, district="", parse_mode="HTML", reply_markup=None, register=True, test=False):
    SENT.append(text); return {"7": 1}
async def _owner_new(*a, **k): OWNER.append(k)
async def _owners(*a, **k): return []
api_server.tg_send = _tg; api_server.tg_edit = _tg; api_server.tg_delete = _none
api_server.notify_owners = _owners; api_server.notify_owners_force = _owners
owr.notify_new_order = _owner_new; owr.notify_owners = _owners; owr.tg_send = _tg
op_route.send = _ops
tobacco.order_blocked = lambda *a, **k: False
config_offices.resolve_office = lambda src: ("alguses", "Алгусес", "form")
geo_watch.geo_bot_link = _none

# Состав оффера 31 окт 2026: по прайсу 1 950 за платные строки, скидка 200 → 1 750, Pinot Grigio подарком.
SPEC = [{"id": "p6", "qty": 1}, {"id": "p81", "qty": 2}, {"id": "p95", "qty": 3}, {"id": "p14", "qty": 1},
        {"id": "p80", "qty": 2}, {"id": "p43", "qty": 1, "pcs": 12}, {"id": "p1", "qty": 1}, {"id": "p97", "qty": 1}]
CAT = api_server._load_catalog_by_id()
def line(it):
    p = CAT[it["id"]]; u = p["price_12"] if it.get("pcs") == 12 and p.get("price_12") else p["price"]
    return {"id": it["id"], "name": p["name"], "qty": it["qty"], "price": u, "line_total": u * it["qty"], "pcs": it.get("pcs")}
CART = [line(it) for it in SPEC]
LIST_PRICE = sum(l["line_total"] for l in CART)


async def order(uid, items, **extra):
    api_server.validate_init_data = lambda s: {"id": uid, "first_name": "Т", "username": "t"}
    # Заказ принимается только с подтверждённым номером — проверяем не это.
    if not await db.get_user(uid):
        await db._db.users.insert_one({"telegram_id": uid, "first_name": "Т", "phone_verified": "+971500000000",
                                       "verified": True, "orders_total": 3})
    body = {"initData": "x", "items": items, "phone": "+971500000000", "address": "ул. Тест 1",
            "office_id": "alguses", "lang": "ru", "subtotal": sum(i.get("line_total", 0) for i in items), **extra}
    req = make_mocked_request("POST", "/api/order"); req._read_bytes = json.dumps(body).encode()
    r = await api_server.handle_create_order(req)
    return r.status, json.loads(r.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]
    now = datetime.now(timezone.utc)
    await db.offer_create(offers.new_doc(SPEC, 200, {"id": "p97", "qty": 1}, total=1750, now=now, code="TESTCODE"))
    await db.offer_create(offers.new_doc(SPEC, 200, now=now - timedelta(days=5), code="OLDCODE1"))
    await db.offer_create(offers.new_doc(SPEC, 200, {"id": "p97", "qty": 1}, now=now, code="SECOND22"))

    print("заказ по коду")
    eq("прайс платных строк", LIST_PRICE, 1950)
    st, r = await order(11, CART, offer="offer_testcode")
    eq("200", st, 200)
    oid = r.get("order_id")
    o = await db.get_order(oid)
    eq("offer на заказе", o.get("offer"), {"code": "TESTCODE", "discount": 200, "title": "Personal offer",
                                            "gift": {"id": "p97", "qty": 1}})
    eq("итог = прайс − скидка", o.get("total"), 1750)
    ids = [(i["id"], bool(i.get("gift")), bool(i.get("offer"))) for i in o["items"]]
    eq("состав: клиент + подарок + скидка", ids, [(l["id"], False, False) for l in CART]
       + [("p97", True, False), ("offer", False, True)])
    eq("второй бутылки (Shiraz) нет", any(i["id"] == "p105" for i in o["items"]), False)
    eq("скидка минусом", next(i for i in o["items"] if i.get("offer"))["line_total"], -200)
    eq("подарок бесплатный", next(i for i in o["items"] if i.get("gift"))["line_total"], 0)
    # Первый заказ нового клиента операторам не уходит, а ждёт проверки — текст
    # лежит на заказе; у проверенного уходит сразу. Баннер один и тот же.
    txt = SENT[-1] if SENT else (o.get("op_text") or "")
    eq("операторам: баннер", ("СПЕЦПРЕДЛОЖЕНИЕ КОМПАНИИ" in txt, "200 AED" in txt,
                              "Код TESTCODE" in txt, "Pinot Grigio" in txt), (True, True, True, True))
    eq("владельцу: offer", OWNER[-1].get("offer", {}).get("code"), "TESTCODE")
    d = await db.offer_get("TESTCODE")
    eq("оффер занят этим заказом", (d["status"], d["order_id"], d["used_by"]), ("ordered", oid, 11))

    print("отказы")
    eq("тот же код второй раз", await order(12, CART, offer="TESTCODE"), (409, {"error": "offer_used"}))
    eq("не весь набор", await order(13, CART[1:], offer="SECOND22"), (400, {"error": "offer_items_missing"}))
    eq("другая фасовка пива", await order(13, [dict(l, pcs=24) if l["id"] == "p43" else l for l in CART], offer="SECOND22"),
       (400, {"error": "offer_items_missing"}))
    eq("криптой", (await order(14, CART, offer="SECOND22", payment_method="crypto"))[1], {"error": "offer_no_crypto"})
    eq("истёк", await order(15, CART, offer="OLDCODE1"), (409, {"error": "offer_expired"}))
    eq("нет такого", await order(16, CART, offer="NOPE1234"), (409, {"error": "offer_unknown"}))
    eq("после отказов код свободен", (await db.offer_get("SECOND22"))["status"], "new")
    n_before = await db._db.orders.count_documents({})
    eq("заказов от отказов нет", n_before, 1)

    print("без кода")
    SENT.clear()
    st, r = await order(17, CART + [{"id": "offer", "name": "x", "qty": 1, "price": -500, "line_total": -500},
                                    {"id": "p97", "name": "x", "qty": 1, "price": 0, "line_total": 0, "gift": True}])
    o = await db.get_order(r["order_id"])
    eq("200 без кода", st, 200)
    eq("минус и чужой подарок отброшены, Shiraz за тысячу как обычно",
       [(i["id"], bool(i.get("gift"))) for i in o["items"] if i.get("gift") or i.get("offer") or i["id"] == "offer"], [("p105", True)])
    eq("итог по прайсу", o.get("total"), LIST_PRICE)
    eq("offer на заказе нет", "offer" in o, False)
    eq("баннера нет", "СПЕЦПРЕДЛОЖЕНИЕ" in (SENT[-1] if SENT else (o.get("op_text") or "")), False)

    print("отмена возвращает код")
    await db.update_order(oid, status="cancelled")
    eq("код снова новый", (await db.offer_get("TESTCODE"))["status"], "new")
    st, r = await order(18, CART, offer="TESTCODE")
    eq("заказ по нему проходит", st, 200)
    eq("занят новым заказом", (await db.offer_get("TESTCODE"))["order_id"], r["order_id"])
    await db.update_order(r["order_id"], status="delivered")
    eq("доставлен — закрыт", await order(19, CART, offer="TESTCODE"), (409, {"error": "offer_used"}))

    print("сбой записи отпускает код")
    real = api_server._finalize_accepted_order
    async def boom(*a, **k): raise RuntimeError("db down")
    api_server._finalize_accepted_order = boom
    try:
        try:
            await order(20, CART, offer="SECOND22"); eq("исключение наружу", False, True)
        except RuntimeError:
            eq("исключение наружу", True, True)
    finally:
        api_server._finalize_accepted_order = real
    eq("код отпущен", (await db.offer_get("SECOND22"))["status"], "new")
    st, r = await order(21, CART, offer="SECOND22")
    eq("и снова проходит", st, 200)


asyncio.run(main())
print("\nFAIL:", FAIL if FAIL else "нет")
sys.exit(1 if FAIL else 0)
