"""Нулевой остаток на полке — заказ не создать (владелец, 8 ноя 2026: «если
на районе числится 0 товара, они не могли его продать… чтобы им выскакивал
алерт операторам, когда вручную заказ составляют, что товар закончился»).

Настоящие operator_routes.handle_create / handle_patch, остаток подменён.

  • позиция с нулём на полке водителя — 409 out_of_stock: район полки, код,
    название и сами позиции; заказ не сохранён;
  • позиция с остатком, позиция, которой в район не вносили (прочерк), — как
    раньше, 200;
  • пиво: в ответе имя позиции без «×24»;
  • заказ задним числом — уже случившаяся продажа, ноль его не держит;
  • склад не прочитался — заказ не держим;
  • правка заказа: добавленная позиция с нулём — 409; строка, которая уже
    лежала в заказе, ноль не держит (смена адреса проходит); прибавка по
    позиции с остатком проходит.

    python3 tools/test_op_stock_zero.py
"""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import date                                         # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, operator_routes as opr, stock_routes as SR             # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


СЕГ = "2026-11-08"
ВОД, ОП = "Тест-водитель 4", "Тест-оператор 2"
ИМЯ = {"p1": "Absolut 1 ltr", "p2": "Red Label 1 ltr", "p9": "Jameson 1 ltr", "b1": "Heineken 0.33 can"}
# Полка B2: Absolut кончился, Red Label 3, пиво 0 коробок, Jameson не вносили
BASE = {"bbay": {"have_exact": {"p1": 0.0, "p2": 3.0, "b1": 0.0}, "counted_at": "x"},
        "jvc": {"have_exact": {"p1": 5.0}, "counted_at": "x"}}
СОСТ = {"base": BASE, "boom": False}
SAVED, UPD = [], []
async def _none(*a, **k): return None
async def _no(*a, **k): return False
async def _dict(*a, **k): return {}

opr._district = lambda did: {"id": "bbay", "name": "Бизнес Бей", "operator": ОП, "drivers": [ВОД]}
opr._drivers_of = lambda *a, **k: {ВОД}
opr._released_now = _no
def _build(raw, source="manual"):
    out = []
    for l in raw or []:
        pid = str(l.get("id")); q = int(l.get("qty") or 0); pcs = l.get("pcs")
        out.append({"id": pid, "name": ИМЯ[pid] + (f" ×{pcs}" if pcs else ""), "price": 100, "qty": q,
                    "line_total": 100 * q, **({"pcs": pcs} if pcs else {})})
    return out, ""
opr._build_items = _build
async def _total(items): return sum(i["line_total"] for i in items)
opr._pos_total = _total
async def _total_for(order, items): return sum(i["line_total"] for i in items)
opr._order_total_for = _total_for
opr._tflag = lambda r: False
opr._op_name = lambda u: ОП
opr._biz_date = lambda dt: date.fromisoformat(СЕГ)
opr._fanout_new = _dict
opr.notify_driver = _none
opr._refresh_cards = _none
opr._pay_told = _none
opr._staff_mod.stock_office = lambda driver, office: "bbay" if driver == ВОД else office
db.shift_day_get = _none
async def _odn(district): return СЕГ
db.order_day_now = _odn
async def _save(oid, order): SAVED.append(order)
db.save_order = _save
async def _upd(oid, **f): UPD.append((oid, f))
db.update_order = _upd
import owner_routes                                               # noqa: E402
owner_routes.notify_owners_force = _none
sys.modules.setdefault("api_server", types.SimpleNamespace(_FOUNDER_ID=0, _PREMIUM_IDS=set(), _WORLDWIDE_IDS=set()))
SR._biz_day = lambda *a, **k: СЕГ
async def _base(day):
    if СОСТ["boom"]:
        raise RuntimeError("склад лёг")
    return СОСТ["base"]
SR._district_base = _base
ЗАКАЗ = {}
async def _get(oid): return json.loads(json.dumps(ЗАКАЗ)) if ЗАКАЗ else None
opr._get_pos_order = _get


def голый(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h


def ид(тело): return [i["id"] for i in (тело.get("items") or [])]


async def создать(items, **body):
    r = make_mocked_request("POST", "/x")
    r["op_id"] = 1; r["op_user"] = {}
    async def js(): return {"district_id": "bbay", "driver": ВОД, "items": items, "payment_method": "cash", **body}
    r.json = js
    resp = await голый(opr.handle_create)(r)
    return resp.status, json.loads(resp.text)


async def править(body):
    r = make_mocked_request("PATCH", "/x", match_info={"oid": "AMB1"})
    r["op_id"] = 1; r["op_user"] = {}
    async def js(): return body
    r.json = js
    resp = await голый(opr.handle_patch)(r)
    return resp.status, json.loads(resp.text)


async def main():
    print("── новый заказ ──────────────────────────────────────────────")
    код, тело = await создать([{"id": "p1", "qty": 1}])
    eq("Absolut на B2 ноль — 409 out_of_stock", (код, тело.get("error")), (409, "out_of_stock"))
    eq("в отказе район полки, код, название", (тело.get("district"), тело.get("district_code"), тело.get("district_name")),
       ("bbay", opr.OFFICE_CODES.get("bbay", ""), opr.OFFICE_NAMES.get("bbay", "bbay")))
    eq("и сами позиции", тело.get("items"), [{"id": "p1", "name": "Absolut 1 ltr"}])
    eq("заказ не сохранён", len(SAVED), 0)

    код, тело = await создать([{"id": "p2", "qty": 2}])
    eq("Red Label 3 на полке — создан", (код, len(SAVED), SAVED[-1]["items"][0]["id"]), (200, 1, "p2"))

    код, тело = await создать([{"id": "p9", "qty": 1}])
    eq("Jameson в район не вносили (прочерк) — не ноль, создан", (код, len(SAVED)), (200, 2))

    код, тело = await создать([{"id": "p2", "qty": 1}, {"id": "p1", "qty": 2}, {"id": "b1", "qty": 1, "pcs": 24}])
    eq("смешанный чек — 409, в отказе только нулевые, пиво без «×24»",
       (код, тело.get("items")), (409, [{"id": "p1", "name": "Absolut 1 ltr"}, {"id": "b1", "name": "Heineken 0.33 can"}]))
    eq("заказ не сохранён", len(SAVED), 2)

    код, тело = await создать([{"id": "p1", "qty": 1}], back_date="2026-11-05")
    eq("задним числом — продажа уже была, ноль не держит", (код, SAVED[-1]["status"], SAVED[-1]["day"]), (200, "delivered", "2026-11-05"))

    СОСТ["boom"] = True
    код, тело = await создать([{"id": "p1", "qty": 1}])
    eq("склад не прочитался — заказ не держим", (код, len(SAVED)), (200, 4))
    СОСТ["boom"] = False

    print("── правка заказа ────────────────────────────────────────────")
    ЗАКАЗ.update({"order_id": "AMB1", "status": "approved", "source": "manual", "district_id": "bbay", "office_id": "bbay",
                  "driver": ВОД, "items": _build([{"id": "p1", "qty": 1}, {"id": "p2", "qty": 1}])[0],
                  "total": 200, "payment_method": "cash"})
    код, тело = await править({"items": [{"id": "p1", "qty": 1}, {"id": "p2", "qty": 1}], "address": "Marina Gate 2"})
    eq("Absolut уже лежал в заказе — смена адреса проходит", (код, тело.get("ok")), (200, True))

    код, тело = await править({"items": [{"id": "p1", "qty": 2}, {"id": "p2", "qty": 1}]})
    eq("прибавили Absolut, а его ноль — 409", (код, тело.get("error"), ид(тело)), (409, "out_of_stock", ["p1"]))

    код, тело = await править({"items": [{"id": "p1", "qty": 1}, {"id": "p2", "qty": 3}]})
    eq("прибавили Red Label (3 на полке) — проходит", (код, тело.get("ok")), (200, True))

    код, тело = await править({"items": [{"id": "p1", "qty": 1}, {"id": "p2", "qty": 1}, {"id": "b1", "qty": 1, "pcs": 12}]})
    eq("добавили пиво, а его 0 коробок — 409", (код, ид(тело)), (409, ["b1"]))

    код, тело = await править({"items": [{"id": "p1", "qty": 1}, {"id": "p2", "qty": 1}], "driver": ВОД, "district_id": "bbay"})
    eq("переназначение без прибавки — проходит", (код, тело.get("ok")), (200, True))

    ЗАКАЗ["backfilled"] = True; ЗАКАЗ["status"] = "delivered"
    код, тело = await править({"items": [{"id": "p1", "qty": 3}, {"id": "p2", "qty": 1}]})
    eq("заказ закрытого дня — ноль не держит", (код, тело.get("ok")), (200, True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0



sys.exit(asyncio.run(main()))
