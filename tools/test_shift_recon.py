"""Сверка смены (shift_recon.py, владелец, 9 окт 2026): водитель сверяет
деньги, заказы и товар тремя шагами, несовпадение уходит оператору ярким
сообщением, правки товара не применяются сами, подтверждённый факт едет
старшему и в книгу дня.

  • {cash} без отметки — тревоги нет; отметил «Деньги» с разницей — оператору
    сообщение, второй раз то же самое не шлётся;
  • правки → fixes_status = sent, шаг «Товар» отметить нельзя (fixes_open);
  • оператор принял «+1» — заказ той же смены этим водителем, доставлен,
    backfilled; «по приложению» выросло, разница сократилась; водителю сказано;
  • все три отметки — confirmed; «Так и есть» — op_fact и op_gap; недостача —
    штраф на решение с суммой недостачи;
  • закрытие района: 409 recon_open с текстом, force_recon — закрывает;
  • star_rows / book_gaps / checklist_items: факт старшего главнее оператора.

    python3 tools/test_shift_recon.py
"""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, date, timezone                     # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, shift_recon as R, driver_routes as dr, operator_routes as opr   # noqa: E402
import config_staff as staff, op_route, fines_auto                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = "2026-10-09"
SENT, TOLD = [], []
КАТАЛОГ = [{"id": "p10", "name": "Red Label 1 ltr", "cat": "Виски", "price": 100, "stock": True},
           {"id": "p78", "name": "Baileys 1 ltr", "cat": "Ликёр", "price": 180, "stock": True}]


async def _all_orders(*a, **k):
    return {o["order_id"]: o async for o in db._db.orders.find({}, {"_id": 0})}
async def _range(*a, **k):
    return list((await _all_orders()).values())
async def _send(text, district="", **k): SENT.append((district, text)); return {"1": 1}
async def _tell(name, text): TOLD.append((name, text))
async def _none(*a, **k): return None


def setup():
    db._db = AsyncMongoMockClient()["recon"]
    db.orders_from = _all_orders; db.get_all_orders = _all_orders; db.get_orders_in_range = _range
    staff.district_map = lambda: {"Али": "b2", "Фарух": "b2", "Даврон": "b2"}
    staff.base_district = lambda n: "b2"
    staff.is_away = lambda n: False
    staff.is_test_driver = lambda n: False
    opr._load_catalog = lambda: КАТАЛОГ
    op_route.send = _send
    opr.tell_driver = _tell
    dr._biz_day = lambda *a, **k: D
    dr._tq = lambda me: False
    opr._fresh_districts = lambda: asyncio.sleep(0, [{"id": "b2", "code": "B2", "name": "Marina", "operator": "Умар", "drivers": ["Али", "Фарух", "Даврон"]}])
    opr._people_for = lambda r, d: []
    opr._scope = lambda p, w, d: {"b2"}
    opr._biz_date = lambda dt: date(2026, 10, 9)
    opr._op_name = lambda u: "Умар"
    opr._build_items = lambda raw, source="manual": ([{"id": r["id"], "name": next(p["name"] for p in КАТАЛОГ if p["id"] == r["id"]),
                                                       "qty": int(r["qty"]), "price": 100, "line_total": 100 * int(r["qty"])} for r in raw], "")
    async def _total(items): return sum(i["line_total"] for i in items)
    opr._pos_total = _total
    opr._staff_mod.stock_office = lambda driver, office: "b2"
    opr._staff_mod.DISTRICT_DRIVERS = {"b2": ["Али", "Фарух", "Даврон"]}
    import owner_routes
    owner_routes.notify_owners_force = _none
    sys.modules.setdefault("api_server", types.SimpleNamespace(_FOUNDER_ID=0, _PREMIUM_IDS=set(), _WORLDWIDE_IDS=set()))


def заказ(oid, driver, items, total, pay="cash"):
    return {"order_id": oid, "status": "delivered", "driver": driver, "office_id": "b2", "payment_method": pay,
            "items": items, "total": total, "timestamp": f"{D}T09:00:00+00:00", "confirmed_at": f"{D}T09:00:00+00:00",
            "delivered_at": f"{D}T10:00:00+00:00", "address": "Marina · Dusit"}


async def drv(name, **body):
    r = make_mocked_request("POST", "/x"); r["driver"] = {"name": name, "district": "b2", "telegram_id": 1}
    async def js(): return body
    r.json = js
    h = dr.handle_shift_recon
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def op(handler, **body):
    r = make_mocked_request("POST", "/x"); r["op_id"] = 5; r["op_user"] = {"first_name": "Умар"}
    async def js(): return {"as": "Умар", **body}
    r.json = js
    h = handler
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    setup()
    await db._db.orders.insert_many([
        заказ("A1", "Али", [{"id": "p10", "qty": 2, "price": 100, "line_total": 200}], 200),
        заказ("A2", "Али", [{"id": "p78", "qty": 2, "price": 180, "line_total": 360}], 360),
        заказ("F1", "Фарух", [{"id": "p10", "qty": 5, "price": 100, "line_total": 500}], 500)])
    await db._db.driver_days.insert_many([{"day": D, "driver": "Али", "working": True, "shift_open_at": "x"},
                                          {"day": D, "driver": "Фарух", "working": True, "shift_open_at": "x"}])
    orders = await _range()

    print("── что по приложению ───────────────────────────────────────")
    eq("наличных на руках по приложению — без валюты", R.cash_app(orders, {"working": True}, "Али", D), 560)
    sold = R.sold_lines(R.mine(orders, "Али", D))
    eq("продано по позициям, порядок каталога", [(x["id"], x["qty"], x["aed"], x["cat"]) for x in sold], [("p10", 2, 200, "Виски"), ("p78", 2, 360, "Ликёр")])
    eq("заказы строками", [(x["t"], x["a"], x["pay"], x["aed"]) for x in R.order_rows(R.mine(orders, "Али", D))],
       [("14:00", "Marina · Dusit", "", 200), ("14:00", "Marina · Dusit", "", 360)])

    print("── водитель: деньги ────────────────────────────────────────")
    код, тело = await drv("Али", cash=860)
    v = тело["recon"]
    eq("вписал наличные: разница +300, шаг не отмечен, тревоги нет", (код, v["cash"], v["cash_app"], v["diff"], v["ok"], len(SENT)), (200, 860, 560, 300, [], 0))
    код, тело = await drv("Али", step=1, ok=True)
    v = тело["recon"]
    eq("отметил «Деньги» с разницей — оператору сообщение", (код, v["ok"], v["mismatch"], v["alert"], len(SENT)), (200, [1], True, True, 1))
    eq("сообщение яркое, с суммами", ("🔴" in SENT[0][1], "860" in SENT[0][1], "+300" in SENT[0][1], SENT[0][0]), (True, True, True, "b2"))
    код, тело = await drv("Али", step=1, ok=True)
    eq("та же отметка второй раз — сообщения нет", (код, len(SENT)), (200, 1))
    код, тело = await drv("Али", cash=560)
    eq("поправил число до совпадения — отметка снята, разница 0", (тело["recon"]["ok"], тело["recon"]["diff"]), ([], 0))
    код, тело = await drv("Али", cash=860); код, тело = await drv("Али", step=1, ok=True)
    eq("вернул несовпадение — то же сообщение не дублируется", len(SENT), 1)
    код, тело = await drv("Али", step=5, ok=True)
    eq("чужой шаг — 400", (код, тело.get("error")), (400, "bad_step"))

    print("── водитель: заказы и товар ────────────────────────────────")
    код, тело = await drv("Али", step=2, ok=True)
    eq("отметил «Заказы»", тело["recon"]["ok"], [1, 2])
    код, тело = await drv("Али", fixes=[{"pid": "p10", "delta": 1}, {"pid": "zzz", "delta": 3}, {"pid": "p78", "delta": 0}])
    v = тело["recon"]
    eq("правки ушли на сверку: только настоящие, статус sent, оператору второе сообщение",
       (код, [(f["pid"], f["delta"], f["ok"]) for f in v["fixes"]], v["fixes_status"], v["fix_open"], len(SENT)), (200, [("p10", 1, None)], "sent", 1, 2))
    eq("в сообщении — правка", "+1 Red Label 1 ltr" in SENT[1][1], True)
    код, тело = await drv("Али", step=3, ok=True)
    eq("пока правки без ответа, «Товар» не отметить", (код, тело.get("error")), (400, "fixes_open"))
    al = await R.alerts_for(D, {"b2"}, orders)
    eq("панели: район горит, у водителя разница и правка", [(a["district"], [(d["name"], d["diff"], d["fixes"]) for d in a["drivers"]]) for a in al], [("b2", [("Али", 300, 1)])])

    print("── оператор: правка принята → заказ ────────────────────────")
    код, тело = await op(opr.handle_recon_fix, driver="Али", pid="p10", delta=1, ok=True)
    v = тело["recon"]
    new = await db._db.orders.find_one({"recon": True}, {"_id": 0})
    eq("200, заказ создан: доставлен, тот же день, водитель, наличные, backfilled",
       (код, bool(тело.get("order_id")), new and (new["status"], new["day"], new["driver"], new["payment_method"], new["backfilled"], new["total"])),
       (200, True, ("delivered", D, "Али", "cash", True, 100)))
    eq("правка отвечена, статус done, «по приложению» выросло, разница сократилась",
       (v["fixes"][0]["ok"], v["fixes"][0]["order_id"] == тело["order_id"], v["fixes_status"], v["cash_app"], v["diff"]), (True, True, "done", 660, 200))
    eq("водителю сказано", (len(TOLD), TOLD[-1][0], "принял" in TOLD[-1][1]), (1, "Али", True))
    код, тело = await op(opr.handle_recon_fix, driver="Али", pid="p10", delta=1, ok=True)
    eq("повторный ответ — 409", (код, тело.get("error")), (409, "answered"))
    код, тело = await drv("Али", step=3, ok=True)
    eq("теперь «Товар» отмечен, итоги подтверждены", (тело["recon"]["ok"], тело["recon"]["confirmed"]), ([1, 2, 3], True))
    eq("несовпадение по деньгам всё ещё горит", (тело["recon"]["mismatch"], тело["recon"]["diff"]), (True, 200))

    print("── оператор: «так и есть» ───────────────────────────────────")
    код, тело = await op(opr.handle_recon_fact, driver="Али")
    v = тело["recon"]
    eq("факт записан: 860, разница +200, тревога снята", (код, v["op_fact"], v["op_gap"], v["op_fact_by"], v["mismatch"], v["alert"]), (200, 860, 200, "Умар", False, False))
    eq("водителю сказано про подтверждение", "подтвердил" in TOLD[-1][1], True)
    fines = await db._db.fine_pending.find({}).to_list(10)
    eq("излишек — не штраф", fines, [])
    # недостача у Фаруха: на руках 400, по приложению 500
    await drv("Фарух", cash=400); await drv("Фарух", step=1, ok=True)
    код, тело = await op(opr.handle_recon_fact, driver="Фарух")
    fines = await db._db.fine_pending.find({}).to_list(10)
    eq("недостача −100 — штраф на решение с суммой недостачи", (код, тело["recon"]["op_gap"], [(f["kind"], f["name"], f["amount"], f["status"]) for f in fines]),
       (200, -100, [("cash_short", "Фарух", 100, "pending")]))
    код, тело = await op(opr.handle_recon_fact, driver="Даврон")
    eq("сверки нет — 404", (код, тело.get("error")), (404, "not_found"))
    al = await R.alerts_for(D, {"b2"}, orders)
    eq("после ответов оператора район не горит", al, [])

    print("── закрытие района ─────────────────────────────────────────")
    probs = await R.district_problems(D, "b2", ["Али", "Фарух", "Даврон"], orders)
    eq("что мешает: Фарух не подтвердил итоги, Даврон не сверял", [(p["driver"], p["kind"]) for p in probs], [("Фарух", "unconfirmed"), ("Даврон", "unconfirmed")])
    eq("текст вопроса", R.problems_text(probs), "Фарух: итоги не подтвердил; Даврон: итоги не подтвердил")
    СОСТ = {"district": "b2", "operator": "Умар", "orders": 3, "revenue": 1060, "open": 0, "open_ids": [], "in_route": 0}
    ЗАКРЫТО = []
    async def _state(day, districts, ids): return {"districts": [dict(СОСТ)]}
    async def _close(day, oid, fields): ЗАКРЫТО.append(oid); return True
    opr._shift_state = _state; db.shift_close = _close; db.driver_track_clear = lambda names: asyncio.sleep(0, 0)
    opr._hours_in = lambda d: 20.0
    код, тело = await op(opr.handle_shift_close, district="b2")
    # Даврон смену не открывал — его в вопросе нет; Фарух на смене и итоги не подтвердил.
    eq("409 recon_open с текстом", (код, тело.get("error"), тело.get("text"), ЗАКРЫТО), (409, "recon_open", "Фарух: итоги не подтвердил", []))
    код, тело = await op(opr.handle_shift_close, district="b2", force_recon=1)
    eq("с force_recon закрывается", (код, ЗАКРЫТО), (200, ["b2"]))

    print("── старшему: сбор выручки, книга, чек-лист ─────────────────")
    rows = await R.star_rows(D, orders, {})
    b2 = rows["b2"]
    eq("строки водителей района и сумма подтверждённых разниц", ([(r["name"], r["op_fact"], r["op_gap"], r["confirmed"]) for r in b2["rows"]], b2["op_gap"], b2["op_n"], b2["fact"]),
       ([("Али", 860, 200, True), ("Фарух", 400, -100, False)], 100, 2, None))
    eq("книга: разница дня из фактов оператора", await R.book_gaps(D, D), {D: 100})
    await db.checklist_put(D, "cashfact:b2", {"fact": 1000, "gap": 50, "by": "1"})
    rows = await R.star_rows(D, orders, await db.checklist_get(D))
    eq("вписанное старшим главнее", (rows["b2"]["fact"], rows["b2"]["fact_gap"]), (1000, 50))
    eq("книга: разница старшего перебивает оператора", await R.book_gaps(D, D), {D: 50})
    items = await R.checklist_items(D, orders)
    eq("чек-лист: излишек жёлтым, недостача красным", [(x["text"], x["bad"]) for x in items], [("Али +200 по факту", False), ("Фарух −100 по факту", True)])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
