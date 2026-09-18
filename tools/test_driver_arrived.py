"""«На месте» у водителя (владелец, 18 сен 2026: «добавь кнопку „на месте“ у
водителя, чтобы, когда он подъезжал, его оператору приходило сообщение с
текстом „Идемте выходите пожалуйста, <марка и номер машины>“ — чтобы оператор
мог этот текст взять и скопировать»; «в карточке активного заказа, вместо
кнопки „Доставил“; нажал „На месте“ — та же кнопка меняется на „Доставил“»).
mongomock + настоящий обработчик и настоящий маршрут операторам (op_route):
  • оператору района заказа (все его устройства) и старшим — фраза с маркой и
    номером машины водителя, моноширинно (копируется нажатием), в личке —
    кнопка «Скопировать текст»; в общем чате — без кнопки;
  • в заказе отметка — в карточке водителя вместо «На месте» «Доставил»;
  • второе нажатие и два нажатия разом — сообщение одно;
  • машина не закреплена — фраза без неё и подсказка оператору;
  • переназначили заказ — новый водитель жмёт заново, со своей машиной;
  • тест-заказ — только тест-операторам; свой оператор в скрытом режиме —
    ближайшему; чужой / закрытый заказ — отказ; адрес экранируется."""
import asyncio, copy, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, config, api_server, driver_routes as dr
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

SENT = []
_mid = [100]
NO_COPY_BTN = set()                              # чаты, где телеграм не принял бы кнопку
async def tg_send(token, chat, text, parse_mode=None, reply_markup=None, **k):
    if int(chat) in NO_COPY_BTN and "copy_text" in json.dumps(reply_markup or {}):
        return {"ok": False, "description": "Bad Request: BUTTON_TYPE_INVALID"}
    _mid[0] += 1
    SENT.append({"chat": int(chat), "text": text, "kb": reply_markup})
    return {"ok": True, "result": {"message_id": _mid[0]}}
api_server.tg_send = tg_send
api_server.OPERATOR_BOT_TOKEN = "x"
api_server.OPERATOR_IDS = [900, -555]            # старший и общий чат-планшет
config.TEST_OPERATOR_IDS = {777}
JVC_OP = staff.base_operator("jvc")
BBAY_OP = staff.base_operator("bbay")
staff.OPERATOR_BY_ID.clear()
staff.OPERATOR_BY_ID.update({701: JVC_OP, 702: JVC_OP, 711: BBAY_OP})


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def tap(me, oid="o1"):
    req = make_mocked_request("POST", "/x", match_info={"oid": oid})
    req._read_bytes = b"{}"
    req["driver"] = me; req["tg"] = {"id": 1}
    r = await raw(dr.handle_arrived)(req)
    return r.status, json.loads(r.text)

ХУДОБА = {"name": "Худоба", "district": "jvc", "district_code": "B1"}
ФАРУХ = {"name": "Фарух", "district": "jvc", "district_code": "B1"}
ДАВРОН = {"name": "Даврон", "district": "alguses", "district_code": "B4"}


async def main():
    db._db = AsyncMongoMockClient()["ambar_arrived"]; d = db._db
    for name, model, plate in (("Худоба", "Hyundai Elantra", "97448"), ("Фарух", "Hyundai Kona", "65149")):
        cid = await db.car_add(model, "серый", plate)
        await db.car_set_driver(cid, name)
    async def order(oid, driver, office, **kw):
        await d.orders.insert_one({"order_id": oid, "status": "approved", "driver": driver, "office_id": office,
                                   "address": "Marina <b>Tower</b> 5", "total": 300, **kw})

    print("── первое нажатие ─────────────────────────────────────────────")
    await order("o1", "Худоба", "jvc")
    st, r = await tap(ХУДОБА)
    eq("200 и фраза с маркой и номером", (st, r["phrase"]), (200, "Идемте выходите пожалуйста, Hyundai Elantra 97448"))
    eq("ушло оператору JVC на оба устройства, старшему и в общий чат",
       sorted(m["chat"] for m in SENT), [-555, 701, 702, 900])
    eq("ответ — сколько чатов получили", r["sent"], 4)
    m = next(x for x in SENT if x["chat"] == 701)
    eq("фраза моноширинно — копируется нажатием",
       "<code>Идемте выходите пожалуйста, Hyundai Elantra 97448</code>" in m["text"], True)
    eq("видно, какой заказ и кто", ("заказ #o1" in m["text"], "Худоба (B1)" in m["text"]), (True, True))
    eq("адрес экранирован", ("&lt;b&gt;Tower&lt;/b&gt;" in m["text"], "<b>Tower" in m["text"]), (True, False))
    eq("в личке — кнопка «Скопировать текст» с той же фразой",
       m["kb"]["inline_keyboard"][0][0], {"text": "Скопировать текст",
                                           "copy_text": {"text": "Идемте выходите пожалуйста, Hyundai Elantra 97448"}})
    eq("в общем чате — без кнопки", next(x for x in SENT if x["chat"] == -555)["kb"], None)
    o = await db.get_order("o1")
    eq("в заказе отметка — у водителя вместо «На месте» будет «Доставил»",
       (bool(o.get("driver_arrived_at")), bool(dr._order_view(o)["arrived_at"])), (True, True))

    print("── повторное нажатие и два разом ──────────────────────────────")
    n = len(SENT)
    st, r = await tap(ХУДОБА)
    eq("второе нажатие — ок, повторно не шлёт", (st, r.get("again"), len(SENT) - n), (200, True, 0))
    await order("o2", "Худоба", "jvc")
    n = len(SENT)
    res = await asyncio.gather(tap(ХУДОБА, "o2"), tap(ХУДОБА, "o2"))
    eq("два нажатия разом — сообщение одно (4 чата)", (len(SENT) - n, sorted(bool(x[1].get("again")) for x in res)),
       (4, [False, True]))

    print("── переназначили заказ ────────────────────────────────────────")
    await d.orders.update_one({"order_id": "o1"}, {"$set": {"driver": "Фарух"}})
    o = await db.get_order("o1")
    eq("у нового водителя снова «На месте»", dr._order_view(o)["arrived_at"], "")
    SENT.clear()
    st, r = await tap(ФАРУХ)
    eq("новый водитель — со своей машиной", (st, r["phrase"]), (200, "Идемте выходите пожалуйста, Hyundai Kona 65149"))
    eq("и ушло", len(SENT), 4)

    print("── машина не закреплена ───────────────────────────────────────")
    await order("o3", "Даврон", "alguses")
    SENT.clear()
    st, r = await tap(ДАВРОН, "o3")
    eq("фраза без машины", (st, r["phrase"]), (200, "Идемте выходите пожалуйста"))
    eq("оператору — подсказка уточнить марку и номер",
       all("Машина за водителем не закреплена" in x["text"] for x in SENT) and bool(SENT), True)

    print("── тест-заказ ─────────────────────────────────────────────────")
    await order("o4", "Худоба", "jvc", test=True)
    SENT.clear()
    st, r = await tap(ХУДОБА, "o4")
    eq("только тест-операторам, с пометкой", ([x["chat"] for x in SENT], SENT[0]["text"].startswith("🧪 <b>ТЕСТ</b>")),
       ([777], True))

    print("── свой оператор в скрытом режиме ─────────────────────────────")
    await db.panic_set("op:" + JVC_OP, True, "t")
    await order("o5", "Худоба", "jvc")
    SENT.clear()
    st, r = await tap(ХУДОБА, "o5")
    got = sorted(x["chat"] for x in SENT)
    eq("своему не пишем, ближайшему — с пометкой о чужом районе",
       (701 in got, 702 in got, any("его оператор сейчас недоступен" in x["text"] for x in SENT)),
       (False, False, True))
    await db.panic_set("op:" + JVC_OP, False, "t")

    print("── телеграм не принял кнопку ──────────────────────────────────")
    NO_COPY_BTN.add(702)
    await order("o7", "Худоба", "jvc")
    SENT.clear()
    st, r = await tap(ХУДОБА, "o7")
    m = [x for x in SENT if x["chat"] == 702]
    eq("этому чату — тот же текст без кнопки, остальным — с кнопкой",
       (len(m), m and m[0]["kb"], m and "<code>" in m[0]["text"], r["sent"]), (1, None, True, 4))
    NO_COPY_BTN.clear()

    print("── отказы ─────────────────────────────────────────────────────")
    await order("o6", "Худоба", "jvc")
    st, r = await tap(ФАРУХ, "o6")
    eq("чужой заказ — 403", (st, r.get("error")), (403, "not_your_order"))
    await d.orders.update_one({"order_id": "o6"}, {"$set": {"status": "delivered"}})
    st, r = await tap(ХУДОБА, "o6")
    eq("закрытый заказ — 409", (st, r.get("error")), (409, "wrong_status"))
    st, r = await tap(ХУДОБА, "нет")
    eq("нет такого заказа — 403", st, 403)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
