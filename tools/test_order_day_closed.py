"""Ручной заказ после закрытия смены района (владелец, 9 окт 2026).

Два ночных заказа B5 вписали в 06:13 и 06:15 — через четверть часа после
закрытия смены в 05:57, — и правило «после закрытия — следующий день»
(db.order_day_now) увело их в 9 октября. «Это же вчерашние заказы». Запоздалая
запись закрытой смены или настоящий утренний заказ — знает только оператор:
сервер спрашивает (409 day_closed), ответ приходит в day_pick.

  • смена не закрыта — как раньше, без вопросов;
  • закрыта, ответа нет — 409 day_closed: день, следующий, когда закрыли, район;
  • «closed» — заказ той смены: день закрытой смены, сразу доставлен, помечен
    backfilled, время — настоящее, а не 20:00, как у заказа задним числом;
  • «next» — новый: следующим днём, обычным путём (в работу);
  • «задним числом» за прошлый день вопроса не вызывает.

    python3 tools/test_order_day_closed.py
"""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, date, timezone                     # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, operator_routes as opr                                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


СЕГ = "2026-10-08"                      # учётные сутки: 06:13 девятого — ещё восьмое
СОСТ = {"closed": False}
SAVED = []
async def _none(*a, **k): return None
async def _no(*a, **k): return False
async def _dict(*a, **k): return {}

opr._district = lambda did: {"id": "tecom", "name": "Tecom", "operator": "Умар", "drivers": ["Алишер"]}
opr._drivers_of = lambda *a, **k: {"Алишер"}
opr._released_now = _no
opr._build_items = lambda raw, source="manual": ([{"id": "p56", "name": "Castel Barreyres 0.75", "qty": 1, "price": 200}], "")
async def _total(items): return 200
opr._pos_total = _total
opr._tflag = lambda r: False
opr._op_name = lambda u: "Умар"
opr._biz_date = lambda dt: date.fromisoformat(СЕГ)
opr._fanout_new = _dict
opr.notify_driver = _none
opr._staff_mod.stock_office = lambda driver, office: "tecom"
async def _shift(day, district):
    return {"_id": f"{day}:{district}", "closed_at": "2026-10-09T01:57:00+00:00"} if СОСТ["closed"] and day == СЕГ else None
db.shift_day_get = _shift
async def _odn(district): return "2026-10-09" if СОСТ["closed"] else СЕГ
db.order_day_now = _odn
async def _save(oid, order): SAVED.append(order)
db.save_order = _save
db.update_order = _none
# уведомления владельцу и импорт api_server внутри обработчика — заглушки
import owner_routes                                               # noqa: E402
owner_routes.notify_owners_force = _none
owner_routes.notify_new_order = _none
sys.modules.setdefault("api_server", types.SimpleNamespace(_FOUNDER_ID=0, _PREMIUM_IDS=set(), _WORLDWIDE_IDS=set()))


async def создать(**body):
    r = make_mocked_request("POST", "/x")
    r["op_id"] = 1; r["op_user"] = {}
    async def js(): return {"district_id": "tecom", "driver": "Алишер", "items": [{"id": "p56", "qty": 1}],
                            "payment_method": "crypto", **body}
    r.json = js
    h = opr.handle_create
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    print("── смена не закрыта ─────────────────────────────────────────")
    код, тело = await создать()
    eq("обычный заказ: создан, в работу", (код, SAVED[-1]["status"], SAVED[-1]["day"]), (200, "approved", СЕГ))

    print("── смена закрыта, ответа нет ────────────────────────────────")
    СОСТ["closed"] = True; n = len(SAVED)
    код, тело = await создать()
    eq("409 day_closed", (код, тело.get("error")), (409, "day_closed"))
    eq("в отказе: день, следующий, когда закрыли, район",
       (тело.get("day"), тело.get("next_day"), тело.get("closed_hm"), тело.get("district")), (СЕГ, "2026-10-09", "05:57", "Tecom"))
    eq("заказ не сохранён", len(SAVED), n)

    print("── «вчерашняя»: заказ закрытой смены ───────────────────────")
    код, тело = await создать(day_pick="closed")
    o = SAVED[-1]
    eq("200, как заказ задним числом за закрытый день", (код, тело.get("back_date")), (200, СЕГ))
    eq("день — закрытая смена, сразу доставлен, помечен", (o["day"], o["status"], o["backfilled"], o["backfill_day"]), (СЕГ, "delivered", True, СЕГ))
    # Настоящее время, а не 20:00 той смены: сравниваем с «сейчас» (проверка по
    # часу суток мигала бы ровно в 16:00 UTC — тогда «сейчас» и есть 20:00 Дубая).
    ts = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00"))
    eq("время настоящее, а не 20:00 той смены", (o["timestamp"] == o["delivered_at"], abs((datetime.now(timezone.utc) - ts).total_seconds()) < 120), (True, True))
    eq("полка — район водителя", o.get("stock_office"), "tecom")

    print("── «новая»: следующим днём, обычным путём ───────────────────")
    код, тело = await создать(day_pick="next")
    o = SAVED[-1]
    eq("200, в работу, день следующий", (код, o["status"], o["day"], o.get("backfilled")), (200, "approved", "2026-10-09", None))

    print("── задним числом за прошлый день — без вопроса ─────────────")
    код, тело = await создать(back_date="2026-10-05")
    eq("принят как раньше", (код, SAVED[-1]["day"], SAVED[-1]["status"]), (200, "2026-10-05", "delivered"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
