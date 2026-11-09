"""Сумма «по прайсу» у списания считается в тех же единицах, что и количество
(владелец, 9 ноя 2026: «чини» — на слова «в сообщении о списании пива и в
списке списаний сумма по прайсу считается за бутылки, а количество стоит в
коробках. Полкоробки Corona там покажет 6 AED вместо 150»).

Что было. Количество списания у пива — в коробках, половина коробки — обычное
дело; закупка и удержание давно считаются «цена единицы × количество». А сумма
«по прайсу» в трёх местах делила прайс коробки на 24, как если бы количество
было в банках: сообщение владельцу о заявке водителя
(driver_routes._writeoff_tell), подпись после решения
(stock_routes._writeoff_after) и строка списка в STAR (_wo_row.aed — из неё же
итоги, суммы по видам и по водителям). Коробка пива стоила в списании как одна
банка. Заодно полкоробки показывалась нулём в списке «мои списания» у водителя
и в разборе удержаний (int от 0,5).

Настоящие ручки на mongomock:
  • заявка водителя руками (пиво 1,5 коробки, бутылки 2 шт) и сканом (код
    пива) — сумма в сообщении владельцу;
  • список списаний в STAR: суммы строк, ждущих решения и итог;
  • решение — подпись после него; удержание по правилу — закупка × количество;
  • «мои списания» у водителя и разбор удержаний — количество, а не ноль.

    python3 tools/test_writeoff_units.py
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
SR._biz_day = lambda *a, **k: D; DR._biz_day = lambda *a, **k: D; ER._biz_day = lambda *a, **k: D
async def cost_map(): return {ПИВО: 142.0, БУТЫЛКА: 62.0}
SV.cost_map = cost_map
async def _nop(*a, **k): return None
owner_routes.notify_owners = _nop
ЗАЯВКИ, РЕШЕНИЯ = [], []
async def _photo(key, cap, photo, reply_markup=None, **k):
    ЗАЯВКИ.append(cap); return [{"chat_id": 1, "message_id": len(ЗАЯВКИ)}]
owner_routes.notify_owners_photo = _photo
async def _edit_caption(token, chat, mid, cap, **k): РЕШЕНИЯ.append(cap)
owner_routes.tg_edit_caption = _edit_caption
pay_notify.tell_safe = _nop
SR.backdate = types.SimpleNamespace(notify=_nop)

КАДР = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"x" * 2400).decode()
МЕНЯ = {"name": ВОДИТЕЛЬ, "district": РАЙОН, "district_code": "B4", "telegram_id": 0}

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h
def req(метод, path, body=None, match=None, **поля):
    r = make_mocked_request(метод, path, match_info=match or {})
    r._read_bytes = json.dumps(body or {}).encode()
    for k, v in поля.items(): r[k] = v
    return r
async def call(h, r):
    resp = await unwrap(h)(r); return resp.status, json.loads(resp.text)
def строка(cap):
    """Вторая строка подписи: «название × количество · сумма AED»."""
    return cap.split("\n")[1].split(" × ", 1)[1]


async def main():
    db._db = AsyncMongoMockClient()["ambar_writeoff_units"]
    cat = SR._catalog()
    коробка = int(cat[ПИВО]["price_24_full"])
    бутылка = int(cat[БУТЫЛКА].get("price_full") or cat[БУТЫЛКА]["price"])
    eq("прайс: у пива за коробку из 24, у бутылки за штуку", (SR._unit(cat[ПИВО]), SR._unit(cat[БУТЫЛКА])), (24, 1))
    eq("проверка имеет смысл: полкоробки и полбанки стоят по-разному",
       round(коробка * 0.5) != round(коробка / 24 * 0.5), True)
    await db.save_stock_count(РАЙОН, D, {"district": РАЙОН, "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": ПИВО, "name": "Corona", "price": 100, "unit": 24, "actual": 5, "counted": True},
                  {"id": БУТЫЛКА, "name": "Absolut", "price": 100, "unit": 1, "actual": 10, "counted": True}]})
    await db.qr_add("W1", ПИВО, cat[ПИВО]["name"], РАЙОН, 1, T0 + timedelta(minutes=5), "W1#1",
                    extra={"src": "cover", "qty": 0.5, "origin": РАЙОН})

    print("── заявки водителя: сообщение владельцу ───────────────────────")
    st, r1 = await call(DR.handle_writeoff_add, req("POST", "/api/driver/writeoff",
                        {"item": ПИВО, "qty": 1.5, "kind": "бой", "photo": КАДР}, driver=МЕНЯ))
    st2, r2 = await call(DR.handle_writeoff_add, req("POST", "/api/driver/writeoff",
                         {"item": БУТЫЛКА, "qty": 2, "kind": "брак", "photo": КАДР}, driver=МЕНЯ))
    st3, r3 = await call(DR.handle_writeoff_scan, req("POST", "/api/driver/writeoff/scan",
                         {"code": "W1", "kind": "бой", "photo": КАДР}, driver=МЕНЯ))
    eq("три заявки приняты", (st, st2, st3, r3.get("verdict")), (200, 200, 200, "ok"))
    eq("ПИВО РУКАМИ, 1,5 КОРОБКИ: ПРАЙС КОРОБКИ × 1,5", строка(ЗАЯВКИ[0]), f"1.5 · {round(коробка * 1.5)} AED")
    eq("бутылки, 2 шт: прайс × 2, как было", строка(ЗАЯВКИ[1]), f"2 · {бутылка * 2} AED")
    eq("ПИВО СКАНОМ, ПОЛКОРОБКИ: ПРАЙС КОРОБКИ × 0,5", строка(ЗАЯВКИ[2]), f"0.5 · {round(коробка * 0.5)} AED")

    print("── список списаний в STAR ─────────────────────────────────────")
    st, L = await call(SR.handle_writeoffs, req("GET", f"/api/owner/stock/writeoffs?days=30&day={D}", owner_id=1))
    ждут = {x["id"]: x for x in L["pending"]}
    eq("строки ждущих: количество и сумма по прайсу",
       [(ждут[i]["qty"], ждут[i]["aed"]) for i in (r1["id"], r2["id"], r3["id"])],
       [(1.5, коробка * 1.5), (2, бутылка * 2), (0.5, коробка * 0.5)])
    eq("ждёт решения всего", (L["pending_qty"], L["pending_aed"]), (4.0, коробка * 2 + бутылка * 2))
    eq("закупка и удержание по правилу — в тех же единицах",
       [(ждут[i]["loss"], ждут[i]["due"]) for i in (r1["id"], r3["id"])], [(213, 213), (71, 71)])

    print("── решение ────────────────────────────────────────────────────")
    st, r = await call(SR.handle_writeoff_decide, req("POST", f"/api/owner/stock/writeoff/{r3['id']}/decide",
                       {"ok": True}, match={"wid": r3["id"]}, owner_id=1))
    eq("подпись после решения — та же сумма за полкоробки",
       (строка(РЕШЕНИЯ[-1]), "Согласовано" in РЕШЕНИЯ[-1]), (f"0.5 · {round(коробка * 0.5)} AED", True))
    st, r = await call(SR.handle_writeoff_decide, req("POST", f"/api/owner/stock/writeoff/{r1['id']}/decide",
                       {"ok": True, "blame": True, "who": ВОДИТЕЛЬ}, match={"wid": r1["id"]}, owner_id=1))
    eq("1,5 коробки с виной: удержание по закупке 142 × 1,5 = 213, подпись на 1,5 коробки",
       ((r.get("comp") or {}).get("amount"), строка(РЕШЕНИЯ[-1])), (213, f"1.5 · {round(коробка * 1.5)} AED"))
    st, L = await call(SR.handle_writeoffs, req("GET", f"/api/owner/stock/writeoffs?days=30&day={D}", owner_id=1))
    eq("итог списанного: 2 коробки пива по прайсу", (L["total_qty"], L["total_aed"]), (2.0, коробка * 2))
    eq("по видам: бой — те же 2 коробки", [(k["kind"], k["qty"], k["aed"]) for k in L["by_kind"]], [("бой", 2.0, коробка * 2)])

    print("── где половина показывалась нулём ────────────────────────────")
    st, M = await call(DR.handle_writeoffs, req("GET", "/api/driver/writeoffs", driver=МЕНЯ))
    eq("«мои списания» у водителя: 1,5 · 2 · 0,5", sorted(x["qty"] for x in M["rows"]), [0.5, 1.5, 2])
    held = await ER._held(D, D)
    eq("разбор удержаний: 213 AED за 1,5 коробки, не за 1",
       [(h["who"], h["amount"], [i["qty"] for i in h["items"]]) for h in held], [(ВОДИТЕЛЬ, 213, [1.5])])

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
