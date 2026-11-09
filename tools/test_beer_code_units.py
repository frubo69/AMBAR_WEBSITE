"""Код пива несёт полкоробки — и в расходе «Охрана», и в списании сканом
(владелец, 9 ноя 2026, по записи «Охрана · Corona Extra · 6 AED»: «водители
отсканили охране пиво, почему пишется 6 дирхам??» · «упаковку из 12 · один код
на пиве - всегда полкоробки»).

Что было. Наклейка пива стоит на коробке и несёт 0,5 (stock_routes.code_qty),
но расход охране ставил цену ОДНОЙ бутылки (коробка 142 ÷ 24 = 6), а в журнал
списаний при согласовании уходила единица — у пива это коробка: остаток района
падал на 1 при коде на 0,5. Три числа за одно действие: 6 AED в расходе,
полкоробки в реестре, коробка в остатке. Ту же единицу писало списание сканом
(водитель и старший).

Проверяем настоящими ручками на mongomock:
  • цена с кода: пиво — закупка коробки × 0,5, бутылка — закупка бутылки;
  • заявка водителя ждёт решения — склад и код не тронуты;
  • согласование: в журнале столько, сколько нёс код, остаток падает на
    столько же, код выходит из реестра;
  • расход старшего (сразу согласован) — то же;
  • списание сканом у водителя и у старшего — то же, владельцу уходит то же
    количество;
  • удержание по правилу считается от половины, подпись после решения её
    не обнуляет.

    python3 tools/test_beer_code_units.py
"""
import asyncio, base64, json, os, sys, types
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as SR, stock_value as SV                    # noqa: E402
import driver_routes as DR, expense_routes as ER                    # noqa: E402
import owner_routes, pay_notify                                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = "2026-11-09"; T0 = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)
РАЙОН, ПИВО, БУТЫЛКА = "alguses", "p43", "p1"
ВОДИТЕЛЬ = "Тест-водитель 1"
КОРОБКА, ЦЕНА_БУТЫЛКИ = 142.0, 62.0
SR._biz_day = lambda *a, **k: D; DR._biz_day = lambda *a, **k: D; ER._biz_day = lambda *a, **k: D

async def cost_map(): return {ПИВО: КОРОБКА, БУТЫЛКА: ЦЕНА_БУТЫЛКИ}
SV.cost_map = cost_map
async def _nop(*a, **k): return None
# Всё, что ходит наружу: телеграм владельцу и водителю, отметка задним числом.
owner_routes.notify_owners = _nop
ПОДПИСИ, ВОДИТЕЛЮ, ВЛАДЕЛЬЦУ = [], [], []
async def _edit_caption(token, chat, mid, cap, **k): ПОДПИСИ.append(cap)
owner_routes.tg_edit_caption = _edit_caption
async def _tell_safe(кому, текст, **k): ВОДИТЕЛЮ.append(текст)
pay_notify.tell_safe = _tell_safe
async def _tell(wid, me, p, qty, kind, note, photo): ВЛАДЕЛЬЦУ.append(qty)
DR._writeoff_tell = _tell
ER.staff = types.SimpleNamespace(drivers=lambda: [{"name": ВОДИТЕЛЬ}],
                                 all_drivers=lambda: [{"name": ВОДИТЕЛЬ}], DRIVER_IDS={})
ER._day_row = lambda base, saved: {}
ER.backdate = types.SimpleNamespace(notify=_nop)
ER._tell_driver = _nop
SR.backdate = types.SimpleNamespace(notify=_nop)

КАДР = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"x" * 2400).decode()

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

def req(path, body, match=None, **поля):
    r = make_mocked_request("POST", path, match_info=match or {})
    r._read_bytes = json.dumps(body).encode()
    for k, v in поля.items(): r[k] = v
    return r

async def call(h, r):
    resp = await unwrap(h)(r); return resp.status, json.loads(resp.text)

МЕНЯ = {"name": ВОДИТЕЛЬ, "district": РАЙОН, "district_code": "B4", "telegram_id": 0}

async def числится(pid):
    SR.base_drop(); return (await SR._district_base(D))[РАЙОН]["have_exact"].get(pid, 0)
async def кодов(pid):
    return (await db.qr_by_product_district(РАЙОН)).get(pid, 0)
async def код(code, pid, имя, сколько):
    await db.qr_add(code, pid, имя, РАЙОН, 1, T0 + timedelta(minutes=5), code + "#1",
                    extra={"src": "cover", "qty": сколько, "origin": РАЙОН})
async def журнал(**q):
    return await db._db.writeoffs.find_one(q) or {}


async def main():
    db._db = AsyncMongoMockClient()["ambar_beer_code_units"]; d = db._db
    cat = SR._catalog()
    eq("позиции теста: пиво коробкой, бутылка штукой", (SR._unit(cat[ПИВО]), SR._unit(cat[БУТЫЛКА])), (24, 1))
    await db.save_stock_count(РАЙОН, D, {"district": РАЙОН, "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": ПИВО, "name": "Corona", "price": 100, "unit": 24, "actual": 5, "counted": True},
                  {"id": БУТЫЛКА, "name": "Absolut", "price": 100, "unit": 1, "actual": 10, "counted": True}]})
    for code in ("G1", "W1", "W2"): await код(code, ПИВО, cat[ПИВО]["name"], 0.5)
    for code in ("G2", "W3"): await код(code, БУТЫЛКА, cat[БУТЫЛКА]["name"], 1)
    await d.driver_days.insert_one({"day": D, "driver": ВОДИТЕЛЬ, "shift_open_at": T0.isoformat()})
    eq("старт: пиво 5 коробок, три кода по 0,5; бутылок 10, два кода",
       (await числится(ПИВО), await кодов(ПИВО), await числится(БУТЫЛКА), await кодов(БУТЫЛКА)), (5, 1.5, 10, 2))

    print("── сколько несёт код ──────────────────────────────────────────")
    eq("код пива с полем — что записано", SR.code_units({"qty": 0.5}, cat[ПИВО]), 0.5)
    eq("код пива без поля — правило позиции", SR.code_units({}, cat[ПИВО]), 0.5)
    eq("код бутылки", (SR.code_units({"qty": 1}, cat[БУТЫЛКА]), SR.code_units(None, cat[БУТЫЛКА])), (1, 1))

    print("── цена с кода ────────────────────────────────────────────────")
    б, беда = await DR._guard_bottle("G1")
    eq("ПИВО: ЗАКУПКА КОРОБКИ × 0,5 = 71, не 142 ÷ 24 = 6", (б["cost"], беда), (71, ""))
    б2, _ = await DR._guard_bottle("G2")
    eq("бутылка: закупка бутылки, как была", б2["cost"], 62)

    print("── водитель отдал охране упаковку пива ────────────────────────")
    st, r = await call(DR.handle_expense_add,
                       req("/api/driver/expenses", {"kind": "guard", "comment": "Менеджер", "code": "G1"}, driver=МЕНЯ))
    eid = (r.get("item") or {}).get("id")
    eq("заявка принята, сумма с кода", (st, r["item"]["amount"], r["item"]["status"], r["item"].get("code")),
       (200, 71, "pending", "G1"))
    eq("до решения склад и код не тронуты", (await числится(ПИВО), await кодов(ПИВО)), (5, 1.5))
    st, r = await call(ER.handle_extra_decide,
                       req(f"/api/owner/expenses/extra/{eid}/approve", {"day": D, "driver": ВОДИТЕЛЬ},
                           match={"item_id": eid, "action": "approve"}, owner_id=1))
    eq("согласовано", (st, r.get("ok")), (200, True))
    w = await журнал(expense=eid)
    eq("В ЖУРНАЛЕ ПОЛКОРОБКИ, НЕ КОРОБКА", (w.get("qty"), w.get("kind"), w.get("state"), w.get("item")),
       (0.5, "охрана", "ok", ПИВО))
    eq("ОСТАТОК 5 → 4,5, код вышел из реестра", (await числится(ПИВО), await кодов(ПИВО)), (4.5, 1.0))
    eq("код помечен списанным", (await db.qr_get("G1")).get("status"), "written")
    saved = await db.get_driver_day(D, ВОДИТЕЛЬ)
    x = next(e for e in saved["extras"] if e["id"] == eid)
    eq("расход в записи водителя: 71 AED, принят", (x["amount"], x["status"]), (71, "approved"))

    print("── старший сам записал бутылку охране (сразу согласовано) ─────")
    st, r = await call(ER.handle_extra_add,
                       req("/api/owner/expenses/extra", {"driver": ВОДИТЕЛЬ, "kind": "guard", "comment": "сторож",
                                                         "code": "G2", "day": D}, owner_id=1))
    w = await журнал(expense=(r.get("item") or {}).get("id"))
    eq("бутылка: сумма 62, в журнале 1", (st, r["item"]["amount"], w.get("qty")), (200, 62, 1))
    eq("бутылок 10 → 9, код ушёл", (await числится(БУТЫЛКА), await кодов(БУТЫЛКА)), (9, 1))

    print("── списание сканом: водитель, бой пива ────────────────────────")
    st, r = await call(DR.handle_writeoff_scan,
                       req("/api/driver/writeoff/scan", {"code": "W1", "kind": "бой", "photo": КАДР}, driver=МЕНЯ))
    wid = r.get("id")
    w = await журнал(_id=wid)
    eq("заявка ждёт: в журнале полкоробки", (r.get("verdict"), w.get("qty"), w.get("state")), ("ok", 0.5, "pending"))
    eq("владельцу ушло то же количество", ВЛАДЕЛЬЦУ, [0.5])
    eq("до решения склад и код не тронуты", (await числится(ПИВО), await кодов(ПИВО)), (4.5, 1.0))
    await d.writeoffs.update_one({"_id": wid}, {"$set": {"msgs": [{"chat_id": 1, "message_id": 2}]}})
    st, r = await call(SR.handle_writeoff_decide,
                       req(f"/api/owner/stock/writeoff/{wid}/decide", {"ok": True, "blame": True, "who": ВОДИТЕЛЬ},
                           match={"wid": wid}, owner_id=1))
    eq("согласовано с удержанием", (st, r.get("state")), (200, "ok"))
    eq("УДЕРЖАНИЕ ПО ПРАВИЛУ — ЗА ПОЛКОРОБКИ: 142 × 0,5 = 71", (r.get("comp") or {}).get("amount"), 71)
    eq("остаток 4,5 → 4, код вышел из реестра", (await числится(ПИВО), await кодов(ПИВО)), (4, 0.5))
    eq("подпись после решения говорит «× 0.5», не «× 0»", [("× 0.5" in c, "× 0 " in c) for c in ПОДПИСИ], [(True, False)])
    eq("водителю о решении — то же количество", any("0.5" in t for t in ВОДИТЕЛЮ), True)

    print("── списание сканом: старший ───────────────────────────────────")
    st, r = await call(SR.handle_writeoff_scan,
                       req("/api/owner/stock/writeoff/scan", {"code": "W2", "kind": "бой", "photo": КАДР, "day": D},
                           owner_id=1))
    w = await журнал(_id=r.get("id"))
    eq("пиво: в журнале полкоробки, сразу учтено", (r.get("verdict"), w.get("qty"), w.get("state")), ("ok", 0.5, "ok"))
    eq("остаток 4 → 3,5, кодов пива не осталось", (await числится(ПИВО), await кодов(ПИВО)), (3.5, 0))
    st, r = await call(SR.handle_writeoff_scan,
                       req("/api/owner/stock/writeoff/scan", {"code": "W3", "kind": "брак", "photo": КАДР, "day": D},
                           owner_id=1))
    w = await журнал(_id=r.get("id"))
    eq("бутылка: в журнале 1, как было", (r.get("verdict"), w.get("qty")), ("ok", 1))
    eq("бутылок 9 → 8, кодов не осталось", (await числится(БУТЫЛКА), await кодов(БУТЫЛКА)), (8, 0))

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
