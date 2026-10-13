"""Книжка клиентов (владелец, 13 окт 2026): телефон запоминается один раз,
подсказки по цифрам, нажал — всё заполнилось; личный ценник оптовика.

mongomock + настоящие clients / clients_routes / operator_routes:
  • номер в любом написании — один ключ; книжка наполняется с каждого заказа:
    новый адрес дописывается, имя не затирается, счётчики растут;
  • подсказки: цифры сужают список, имя и адрес ищутся словами, пусто — недавние;
  • заказ оператора: клиент из книжки или по номеру; у оптовика цены строк —
    личные, снимок в заказе; итог сервер считает по ним; правка ценника потом
    заказ не меняет, новая строка берёт цену из текущего ценника;
  • сборка книжки из прошлых заказов — один раз;
  • STAR: ценник правилом (−15% на категорию, до 5) и руками, выше обычной
    не принимается; карточка с заказами, скидкой и маржой;
  • оператор: заведение рукой, занятый номер — 409, адреса с подписями.

    python3 tools/test_clients_book.py
"""
import asyncio, json, os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import date                                          # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, clients as C, clients_routes as CR, operator_routes as opr   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

async def _none(*a, **k): return None
async def _dict(*a, **k): return {}


async def call(handler, method="POST", cid=None, oid=None, query="", owner=False, **body):
    mi = {}
    if cid: mi["cid"] = cid
    if oid: mi["oid"] = oid
    r = make_mocked_request(method, "/x" + (("?" + query) if query else ""), match_info=mi)
    if owner:
        r["owner_id"] = 1
    else:
        r["op_id"] = 5; r["op_user"] = {"first_name": "Умар"}
    async def js(): return {"as": "Умар", **body}
    r.json = js
    h = handler
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["clients_book"]
    print("── телефон ─────────────────────────────────────────────────")
    ключи = {C.norm_phone(x)[0] for x in ("+971 50 123 4567", "0501234567", "971501234567", "00971501234567", "+971-50-123-45-67", "501234567")}
    eq("шесть написаний — один ключ", ключи, {"971501234567"})
    eq("красиво", C.norm_phone("0501234567")[1], "+971 50 123 4567")
    eq("мусор — пусто", (C.norm_phone("abc"), C.norm_phone("00"), C.norm_phone("")), (("", ""), ("", ""), ("", "")))

    print("── книжка наполняется с заказов ────────────────────────────")
    o1 = {"order_id": "A1", "phone": "+971 50 123 4567", "customer_name": "Иван С.", "address": "Marina Gate 2, apt 1804", "total": 600, "source": "manual"}
    await db._db.orders.insert_one(dict(o1))
    c = await C.remember(o1, by="Умар")
    eq("новый клиент: имя, номер, адрес, счётчик", (c["name"], c["phones"][0]["pretty"], len(c["addresses"]), c["orders_n"], c["total_sum"]), ("Иван С.", "+971 50 123 4567", 1, 1, 600.0))
    eq("ключ клиента в заказе", (await db._db.orders.find_one({"order_id": "A1"}))["client_id"], c["_id"])
    o2 = {"order_id": "A2", "phone": "0501234567", "customer_name": "Иван", "address": "Bay Square 7, office 402", "total": 400, "source": "manual"}
    await db._db.orders.insert_one(dict(o2))
    c2 = await C.remember(o2, by="Умар")
    eq("тот же номер иначе — тот же клиент, второй адрес, имя не затёрто, aka", (c2["_id"] == c["_id"], len(c2["addresses"]), c2["name"], c2.get("aka"), c2["orders_n"], c2["total_sum"]), (True, 2, "Иван С.", ["Иван"], 2, 1000.0))
    o3 = {"order_id": "A3", "phone": "+971501234567", "customer_name": "—", "address": "marina gate 2,  apt 1804", "total": 100}
    await db._db.orders.insert_one(dict(o3))
    c3 = await C.remember(o3)
    eq("адрес тот же с другим регистром — не дублируется, used растёт", (len(c3["addresses"]), max(a["used"] for a in c3["addresses"])), (2, 2))
    app_o = {"order_id": "B1", "phone": "+971 55 777 0303", "customer_name": "Алина К.", "customer_id": 42, "address": "Marina, Villa 12", "total": 900, "source": "app"}
    await db._db.orders.insert_one(dict(app_o))
    ca = await C.remember(app_o, by="app")
    eq("клиент приложения: tg_id записан", (ca["name"], ca.get("tg_id")), ("Алина К.", 42))
    eq("без телефона — не запоминаем", await C.remember({"order_id": "Z", "phone": "—", "customer_name": "Кто-то"}), None)
    eq("тестовый заказ — мимо", await C.remember({"order_id": "T", "phone": "+971501110000", "test": True}), None)

    print("── подсказки ───────────────────────────────────────────────")
    await asyncio.sleep(0.01)           # разные миллисекунды — у недавних есть порядок
    await db._db.orders.insert_one({"order_id": "D1", "phone": "+971 52 198 0000", "customer_name": "Дмитрий М.", "address": "JVC, Diamond Views", "total": 300})
    await C.remember({"order_id": "D1", "phone": "+971 52 198 0000", "customer_name": "Дмитрий М.", "address": "JVC, Diamond Views", "total": 300})
    names = lambda rows: [r["name"] for r in rows]
    r50 = await C.search("50")
    eq("«50» — есть у Ивана (971 50…) и нет у Алины (55)", ("Иван С." in names(r50), "Алина К." in names(r50)), (True, False))
    eq("«+971 5012» сужает до Ивана", names(await C.search("+971 5012")), ["Иван С."])
    eq("«0501234567» целиком — Иван", names(await C.search("0501234567")), ["Иван С."])
    eq("по имени", names(await C.search("алин")), ["Алина К."])
    eq("по адресу", names(await C.search("diamond")), ["Дмитрий М."])
    eq("пусто — недавние, свежий первым", names(await C.search(""))[0], "Дмитрий М.")
    s = (await C.search("иван"))[0]
    eq("подсказка несёт последний адрес и число адресов", (s["address"], s["addresses_n"], s["phone"]), ("Marina Gate 2, apt 1804", 2, "+971 50 123 4567"))

    print("── заказ оператора с личным ценником ───────────────────────")
    _n = {"i": 0}
    def _oid():
        _n["i"] += 1
        return f"T{_n['i']:04d}"
    opr._new_oid = _oid                 # в тесте заказы рождаются в одну миллисекунду
    opr._district = lambda did: {"id": "jvc", "name": "JVC", "operator": "Умар", "drivers": ["Али"]}
    opr._drivers_of = lambda *a, **k: {"Али"}
    opr._released_now = _none
    opr._tflag = lambda r: False
    opr._op_name = lambda u: "Умар"
    opr._biz_date = lambda dt: date(2026, 10, 13)
    opr._fanout_new = _dict; opr.notify_driver = _none; opr.tell_driver = _none; opr._refresh_cards = _none
    opr._staff_mod.stock_office = lambda d, o: "jvc"
    import owner_routes
    owner_routes.notify_owners_force = _none; owner_routes.notify_new_order = _none
    sys.modules.setdefault("api_server", types.SimpleNamespace(_FOUNDER_ID=0, _PREMIUM_IDS=set(), _WORLDWIDE_IDS=set()))
    # ценник Ивана: Don Julio Reposado 420 вместо 450, Patron Silver без скидки
    await db.client_set(c["_id"], {"wholesale": {"active": True, "prices": {"p70": 420, "p73": 1530}, "rule": None}})
    код, тело = await call(opr.handle_create, district_id="jvc", driver="Али", items=[{"id": "p70", "qty": 2}, {"id": "p67", "qty": 1}],
                            phone="050 123 4567", customer_name="Иван С.", address="Marina Gate 2, apt 1804")
    o = await db._db.orders.find_one({"order_id": тело.get("order_id")}, {"_id": 0})
    eq("по номеру узнан оптовик: 2×420 + 350 = 1190", (код, тело.get("total"), o["total"]), (200, 1190, 1190))
    eq("строки: своя цена и обычная рядом, снимок в заказе", (o["items"][0]["price"], o["items"][0]["reg"], o["items"][0]["own"], o["items"][1].get("own"), o["own_prices"], o["wholesale"], o["client_id"] == c["_id"]),
       (420, 450, True, None, {"p70": 420}, True, True))
    eq("счётчик клиента вырос", (await db.client_get(c["_id"]))["orders_n"], 4)
    код, тело = await call(opr.handle_create, district_id="jvc", driver="Али", items=[{"id": "p70", "qty": 1}],
                            client_id=c["_id"], customer_name="", address="")
    o2 = await db._db.orders.find_one({"order_id": тело.get("order_id")}, {"_id": 0})
    eq("выбран из книжки без телефона в форме — ценник всё равно его", (код, o2["total"], o2["client_id"] == c["_id"]), (200, 420, True))
    код, тело = await call(opr.handle_create, district_id="jvc", driver="Али", items=[{"id": "p70", "qty": 1}],
                            phone="+971 58 643 1000", customer_name="Наталья П.", address="Silicon Oasis, Tower B")
    o3 = await db._db.orders.find_one({"order_id": тело.get("order_id")}, {"_id": 0})
    нат = await C.by_phone("0586431000")
    eq("незнакомый номер: обычная цена, клиент заведён сам", (o3["total"], "own_prices" in o3, нат["name"], нат["orders_n"], o3["client_id"] == нат["_id"]), (450, False, "Наталья П.", 1, True))

    print("── правка заказа: снимок главнее ───────────────────────────")
    oid = o["order_id"]
    await db.client_set(c["_id"], {"wholesale": {"active": True, "prices": {"p70": 400, "p67": 300}, "rule": None}})
    код, тело = await call(opr.handle_patch, oid=oid, items=[{"id": "p70", "qty": 2}, {"id": "p67", "qty": 2}])
    o = await db._db.orders.find_one({"order_id": oid}, {"_id": 0})
    eq("Reposado остался по снимку 420, Patron — по новому ценнику 300: 840 + 600", (код, o["total"], o["own_prices"]), (200, 1440, {"p70": 420, "p67": 300}))
    v = opr._summary(o)
    eq("сводка: клиент, оптовик, строки с обычной ценой", (v["client_id"] == c["_id"], v["wholesale"], v["items"][0]["reg"], v["items"][0]["own"]), (True, True, 450, True))
    код, тело = await call(opr.handle_patch, oid=oid, address="Bay Square 7, office 402", phone="0501234567")
    eq("правка адреса не удваивает заказ в счётчике", (код, (await db.client_get(c["_id"]))["orders_n"]), (200, 5))

    print("── сборка книжки из прошлых заказов ────────────────────────")
    db._db = AsyncMongoMockClient()["clients_seed"]
    await db._db.orders.insert_many([
        {"order_id": "S1", "phone": "+971 50 999 0001", "customer_name": "Олег Л.", "address": "Silicon Oasis", "total": 300, "status": "delivered", "timestamp": "2026-09-01T10:00:00"},
        {"order_id": "S2", "phone": "0509990001", "customer_name": "Олег", "address": "silicon oasis", "total": 200, "status": "delivered", "timestamp": "2026-09-05T10:00:00"},
        {"order_id": "S3", "phone": "+971509990001", "customer_name": "Олег Л.", "address": "Marina 1", "total": 500, "status": "cancelled", "timestamp": "2026-09-06T10:00:00"},
        {"order_id": "S4", "phone": "+971 56 052 2000", "customer_name": "Robert B.", "customer_id": 7, "address": "Executive Towers", "total": 900, "status": "delivered", "timestamp": "2026-09-07T10:00:00"},
        {"order_id": "S5", "phone": "—", "customer_name": "Без номера", "address": "x", "total": 100, "status": "delivered", "timestamp": "2026-09-08T10:00:00"},
        {"order_id": "S6", "phone": "+971 50 999 0001", "customer_name": "", "address": "", "total": 50, "status": "delivered", "timestamp": "2026-09-09T10:00:00", "test": True},
    ])
    r = await C.seed()
    cl = await C.by_phone("0509990001")
    eq("собрано: двое, у Олега два заказа в счёт (отменённый нет), два адреса, последнее имя", (r["clients"], cl["name"], cl["orders_n"], cl["total_sum"], len(cl["addresses"])), (2, "Олег Л.", 2, 500.0, 2))
    eq("заказы получили ключ клиента", (await db._db.orders.count_documents({"client_id": cl["_id"]})), 3)
    eq("клиент приложения — с tg_id", (await C.by_phone("0560522000")).get("tg_id"), 7)
    eq("второй раз — пропуск", (await C.seed()).get("skipped"), True)

    print("── STAR: ценник ────────────────────────────────────────────")
    cid = cl["_id"]
    код, тело = await call(CR.own_prices, cid=cid, owner=True, rule={"pct": 15, "cats": ["Текила"], "round": 5}, prices={"p73": 1530})
    w = (await db.client_get(cid))["wholesale"]
    eq("правило −15% до 5 на текилу + рука: Reposado 385, Silver 300, Azul 1530", (код, w["prices"].get("p70"), w["prices"].get("p67"), w["prices"].get("p73"), "p31" in w["prices"], w["active"]), (200, 385, 300, 1530, False, True))
    код, тело = await call(CR.own_prices, cid=cid, owner=True, prices={"p70": 460})
    eq("выше обычной — отказ", (код, тело.get("error"), тело["bad"][0]["error"]), (400, "bad_prices", "above_regular"))
    код, тело = await call(CR.own_prices, cid=cid, owner=True, prices={"p70": 420}, active=False)
    eq("выключенный ценник не применяется", (код, C.wholesale_active(await db.client_get(cid))), (200, False))
    await db._db.orders.insert_one({"order_id": "S7", "client_id": cid, "status": "delivered", "total": 1190, "timestamp": "2026-10-13T10:00:00", "day": "2026-10-13", "district": "JVC",
                                    "items": [{"id": "p70", "name": "Don Julio Reposado", "qty": 2, "price": 420, "reg": 450, "own": True}, {"id": "p67", "name": "Patron Silver", "qty": 1, "price": 350}]})
    код, тело = await call(CR.own_card, method="GET", cid=cid, owner=True)
    eq("карточка: заказы, скидка 60, маржа по закупке (2×(420−250) + (350−144))", (код, тело["stats"]["orders_n"], тело["stats"]["discount"], тело["stats"]["margin"], len(тело["editor"]["rows"]) > 100, "Текила" in тело["editor"]["cats"]), (200, 3, 60, 546, True, True))
    eq("строки ценника несут обычную цену и пачки пива", ([r for r in тело["editor"]["rows"] if r["key"] == "p31_24"][0]["regular"], [r for r in тело["editor"]["rows"] if r["key"] == "p70"][0]["regular"]), (200, 450))

    print("── оператор: завести рукой, адреса ─────────────────────────")
    код, тело = await call(CR.op_save, name="Наталья П.", phone="058 643 1000", note="звонить заранее")
    nid = тело["client"]["id"]
    eq("заведён рукой", (код, тело["client"]["name"], тело["client"]["phone"]), (200, "Наталья П.", "+971 58 643 1000"))
    код, тело = await call(CR.op_save, name="Другая", phone="+971509990001")
    eq("номер занят — 409 и кто держит", (код, тело.get("error"), тело["client"]["name"]), (409, "phone_taken", "Олег Л."))
    код, тело = await call(CR.op_address, cid=nid, text="Silicon Oasis, Tower B, 1205", label="Дом", note="код домофона 1205")
    aid = тело["client"]["addresses"][0]["id"]
    eq("адрес с подписью и заметкой", (код, тело["client"]["addresses"][0]["label"], тело["client"]["addresses"][0]["note"]), (200, "Дом", "код домофона 1205"))
    код, тело = await call(CR.op_address, cid=nid, id=aid, label="Офис")
    eq("подпись поправлена", тело["client"]["addresses"][0]["label"], "Офис")
    код, тело = await call(CR.op_address, cid=nid, id=aid, delete=True)
    eq("адрес убран", (код, len(тело["client"]["addresses"])), (200, 0))
    код, тело = await call(CR.op_search, method="GET", query="q=5864")
    eq("подсказка оператору", (код, [x["name"] for x in тело["clients"]]), (200, ["Наталья П."]))
    код, тело = await call(CR.op_card, method="GET", cid=cid)
    eq("карточка оператору: все заказы, и отменённый, статистика без маржи", (код, len(тело["orders"]), тело["stats"]["orders_n"], тело["stats"]["margin"]), (200, 4, 3, None))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
