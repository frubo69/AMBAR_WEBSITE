"""Согласование водитель ↔ оператор сквозь обе стороны на подменённой базе:
правка состава → применена / отклонена, отмена → заказ отменён, доставка →
закрыта, сообщение → принято; повторная просьба, «Доставил» при открытой
правке, отзыв, чат туда и обратно. Запуск: python3 tools/test_driver_operator_flow.py"""
import asyncio, os, sys, json, logging, copy, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from aiohttp.test_utils import make_mocked_request
import db, driver_routes as dr, operator_routes as op, api_server

CAT = [{"id": "gin", "name": "Джин", "cat": "Джин", "price": 95, "price_full": 100, "stock": True},
       {"id": "vod", "name": "Водка", "cat": "Водка", "price": 60, "price_full": 65, "stock": True}]
op._load_catalog = lambda: CAT
op._catalog_by_id = lambda: {p["id"]: p for p in CAT}
api_server._load_catalog_by_id = lambda: {p["id"]: p for p in CAT}
ORDERS, TOLD, CHAT_TOLD = {}, [], []
async def get_order(oid): return copy.deepcopy(ORDERS.get(oid))
async def update_order(oid, **kw): ORDERS[oid].update(kw)
async def order_chat_add(oid, msg):
    o = ORDERS[oid]; o.setdefault("chat", []).append(msg); o["chat_at"] = msg["at"]; o[f"chat_seen_{msg['by']}"] = msg["at"]
    return copy.deepcopy(o)
async def order_chat_seen(oid, who, now): ORDERS[oid][f"chat_seen_{who}"] = now
async def add_debt(*a, **k): pass
async def panic_get(name): return False
for n, f in dict(get_order=get_order, update_order=update_order, order_chat_add=order_chat_add,
                 order_chat_seen=order_chat_seen, add_debt=add_debt, panic_get=panic_get).items():
    setattr(db, n, f)
async def _noop(*a, **k): return None
dr._notify_operators = _noop; dr._notify_chat = _noop
op._refresh_cards = _noop; op._customer_card = _noop; op.notify_driver = _noop
async def tell_driver(name, text): TOLD.append(text)
op.tell_driver = tell_driver
async def _close_delivered(oid, order, who, by=""): ORDERS[oid]["status"] = "delivered"; ORDERS[oid]["delivered_by_driver"] = by
async def _do_cancel(oid, order, who, reason=""): ORDERS[oid]["status"] = "cancelled"; ORDERS[oid]["cancel_reason"] = reason
op._close_delivered = _close_delivered; op._do_cancel = _do_cancel
ME = {"name": "Али", "district": "a", "district_code": "B1"}

def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt: return h
        h = nxt
async def drv(h, body=None, oid="o1", method="POST"):
    req = make_mocked_request(method, "/x", match_info={"oid": oid})
    req._read_bytes = json.dumps(body or {}).encode(); req["driver"] = ME; req["tg"] = {"id": 1}
    r = await raw(h)(req); return r.status, json.loads(r.text)
async def opr(h, body=None, oid="o1"):
    req = make_mocked_request("POST", "/x", match_info={"oid": oid})
    req._read_bytes = json.dumps(body or {}).encode(); req["op_user"] = {"id": 7, "first_name": "Парвиз"}
    r = await raw(h)(req); return r.status, json.loads(r.text)

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<64} got {got!r}  want {want!r}")
    if not ok: fails.append(name)
def fresh(source="app"):
    ORDERS.clear(); TOLD.clear()
    ORDERS["o1"] = {"order_id": "o1", "status": "approved", "driver": "Али", "source": source, "tip": 0,
                    "items": [{"id": "gin", "name": "Джин", "qty": 1, "price": 95, "line_total": 95}],
                    "total": 95, "address": "ул", "chat": []}

async def main():
    print("— правка состава: водитель просит, оператор применяет — цены приложения")
    fresh()
    st, r = await drv(dr.handle_edit_request, {"items": [{"id": "gin", "qty": 1}, {"id": "vod", "qty": 2}]})
    eq("просьба открыта, итог 95 + 2×60 = 215", (st, r["driver_req"]["status"], r["driver_req"]["total"]), (200, "open", 215.0))
    v = dr._order_view(ORDERS["o1"])
    eq("водитель видит: ждём оператора, состав прежний", (v["driver_req"]["status"], len(v["items"])), ("open", 1))
    st, r = await opr(op.handle_driver_req, {"act": "approve", "as": "Парвиз"})
    eq("применено: items 2, total 215", (st, r["status"], len(ORDERS["o1"]["items"]), ORDERS["o1"]["total"]), (200, "applied", 2, 215.0))
    eq("водителю сказали", any("изменён оператором" in t for t in TOLD), True)
    v = dr._order_view(ORDERS["o1"])
    eq("водитель видит applied и новый состав", (v["driver_req"]["status"], [i["id"] for i in v["items"]]), ("applied", ["gin", "vod"]))
    st, r = await opr(op.handle_driver_req, {"act": "approve"})
    eq("второй раз — нечего решать (409)", (st, r.get("error")), (409, "no_open_request"))
    print("— оператор правит по-своему и одобряет (телефонный заказ — полные цены)")
    fresh("manual")
    await drv(dr.handle_edit_request, {"items": [{"id": "vod", "qty": 1}]})
    st, r = await opr(op.handle_driver_req, {"act": "approve", "items": [{"id": "vod", "qty": 3}]})
    eq("применён состав оператора: водка ×3 по 65 = 195", (ORDERS["o1"]["items"][0]["qty"], ORDERS["o1"]["total"]), (3, 195))
    print("— отказ")
    fresh()
    await drv(dr.handle_edit_request, {"items": [{"id": "vod", "qty": 1}]})
    st, r = await opr(op.handle_driver_req, {"act": "reject"})
    v = dr._order_view(ORDERS["o1"])
    eq("rejected, состав прежний, водителю сказали", (r["status"], v["driver_req"]["status"], len(v["items"]), any("отклонена" in t for t in TOLD)), ("rejected", "rejected", 1, True))
    print("— просьба отменить → оператор отменяет")
    fresh()
    st, r = await drv(dr.handle_edit_request, {"kind": "cancel", "text": "Клиент отказался"})
    eq("открыта просьба cancel", (st, ORDERS["o1"]["driver_req"]["kind"]), (200, "cancel"))
    st, r = await drv(dr.handle_delivered, {"settled": True})
    eq("«Доставил» при открытой отмене — 409", (st, r.get("error")), (409, "req_open"))
    st, r = await opr(op.handle_driver_req, {"act": "approve"})
    eq("заказ отменён с причиной", (ORDERS["o1"]["status"], ORDERS["o1"]["cancel_reason"]), ("cancelled", "Клиент отказался"))
    print("— доставка → оператор подтверждает")
    fresh()
    st, r = await drv(dr.handle_delivered, {"settled": True})
    eq("просьба delivered", (st, ORDERS["o1"]["driver_req"]["kind"]), (200, "delivered"))
    st, r = await drv(dr.handle_edit_request, {"items": [{"id": "vod", "qty": 1}]})
    eq("правка после «Доставил» перебивает слот (осознанно: водитель ещё у клиента)", ORDERS["o1"]["driver_req"]["kind"], "edit")
    st, r = await drv(dr.handle_req_withdraw, {})
    eq("отозвал правку", ORDERS["o1"]["driver_req"]["status"], "withdrawn")
    st, r = await drv(dr.handle_delivered, {"settled": True})
    st, r = await opr(op.handle_driver_req, {"act": "approve"})
    eq("доставка закрыта оператором", (st, ORDERS["o1"]["status"]), (200, "delivered"))
    print("— сообщение → принято; чат туда и обратно")
    fresh()
    st, r = await drv(dr.handle_edit_request, {"kind": "note", "text": "Дверь не открывают"})
    st, r = await opr(op.handle_driver_req, {"act": "approve"})
    eq("note applied", ORDERS["o1"]["driver_req"]["status"], "applied")
    st, r = await drv(dr.handle_chat_send, {"kind": "client"})
    eq("водитель: «Клиент не отвечает»", (st, ORDERS["o1"]["chat"][-1]["text"]), (200, "Клиент не отвечает"))
    st, r = await opr(op.handle_chat_send, {"text": "Звоню клиенту", "as": "Парвиз"})
    eq("оператор ответил, водителю в телеграм ушло", (st, ORDERS["o1"]["chat"][-1]["by"], any("Звоню клиенту" in t for t in TOLD)), (200, "operator", True))
    v = dr._order_view(ORDERS["o1"])
    eq("на карточке: непрочитанное 1 и последний ответ", (v["chat_new"], v["chat_last"]["text"]), (1, "Звоню клиенту"))
    st, r = await drv(dr.handle_chat, method="GET")
    v = dr._order_view(ORDERS["o1"])
    eq("открыл разговор — прочитано", v["chat_new"], 0)
    print()
    print("FAILED:", fails) if fails else print("ALL OK — согласование водитель ↔ оператор")
    sys.exit(1 if fails else 0)
asyncio.run(main())
