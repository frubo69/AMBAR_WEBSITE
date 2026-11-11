"""Решение по недостаче ревизии: строка ровно в полкоробки пива списанием
не записывалась (найдено на проде 11 ноя 2026 при закрытии пяти ревизий:
B2 Stella 0,5; B4 Budweiser банка, Corona, Stella по 0,5 — отчёт решён
целиком, а в ленте списаний этих строк нет).

Что было. handle_audit_short пропускал строку, если int(qty) == 0, — у пива
количество в коробках, и половина коробки превращалась в ноль. Удержание
при этом ставится на первое списание: ревизия из одной такой строки
осталась бы и без удержания.

Что стало. Проверка через _num: половина — не ноль.

Сценарии на mongomock с настоящими ручками (handle_audit_short,
handle_audit_short_undo):
  A. строки Absolut 1 и Corona 0,5, без виновного → два списания «недостача»
     (1 и 0,5), у полкоробки в строке STAR половина прайса коробки, ревизия
     закрыта → «выбрать заново» стирает оба, ревизия снова ждёт решения.
  B. одна строка Corona 0,5 с виновным → списание есть и удержание на нём.

    python3 tools/test_audit_short_half.py
"""
import asyncio, json, os, sys, types
from datetime import timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as SR                                      # noqa: E402
import owner_routes, pay_notify                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

U = timezone.utc
D = "2026-11-09"
РАЙОН, ПИВО, БУТЫЛКА, ВОДИТЕЛЬ = "alguses", "p43", "p1", "Тест-водитель 1"

async def _nop(*a, **k): return None
# Всё, что ходит наружу: телеграм владельцам и водителю.
owner_routes.notify_owners = _nop
pay_notify.tell_safe = _nop
SR._writeoff_comp_tell = _nop
SR.backdate = types.SimpleNamespace(notify=_nop)

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

def req(path, body, **поля):
    r = make_mocked_request("POST", path)
    r._read_bytes = json.dumps(body).encode()
    for k, v in поля.items(): r[k] = v
    return r

async def call(h, r):
    resp = await unwrap(h)(r); return resp.status, json.loads(resp.text)

async def мир(имя, строки):
    """Завершённая ревизия района с недостачей по строкам (позиция, количество)."""
    db._db = AsyncMongoMockClient()[f"ambar_audit_short_half_{имя}"]
    SR.base_drop()
    cat = SR._catalog()
    lines = [{"id": pid, "name": cat[pid]["name"], "qty": q, "price": SR._price(cat[pid]),
              "due": SR._price(cat[pid]) * q, "loss": 0, "unit": 1 if pid == БУТЫЛКА else 24}
             for pid, q in строки]
    await db.audit_set(РАЙОН, D, {
        "started_at": "2026-11-10T02:30:00+00:00", "finished_at": "2026-11-10T03:28:00+00:00",
        "finished_by_name": "STAR", "result": {}, "lines": [], "over": None, "alien": 0,
        "short": {"qty": sum(q for _, q in строки), "aed": sum(l["due"] for l in lines), "lines": lines}})
    return cat

async def лента():
    return sorted((w["item"], w["qty"]) for w in await db.writeoff_list(day=D))

async def main():
    print("A. Absolut 1 + Corona 0,5, без виновного")
    cat = await мир("a", [(БУТЫЛКА, 1), (ПИВО, 0.5)])
    st, r = await call(SR.handle_audit_short,
                       req("/api/owner/stock/audit/short", {"district": РАЙОН, "day": D, "blame": False, "as": "STAR"}, owner_id=1))
    eq("A: ответ", st, 200)
    a = await db.audit_get(РАЙОН, D) or {}
    sh = a.get("short") or {}
    eq("A: решена без виновного", (bool(sh.get("resolved_at")), sh.get("blame"), sh.get("by_name")), (True, False, "STAR"))
    eq("A: списаний в решении", len(sh.get("writeoffs") or []), 2)
    eq("A: закрыта", bool(a.get("closed_at")), True)
    eq("A: лента: обе строки, полкоробки тоже", await лента(), [(БУТЫЛКА, 1), (ПИВО, 0.5)])
    rows = await db.writeoff_list(day=D)
    eq("A: вид, источник, состояние", {(w["kind"], w.get("src"), w.get("state")) for w in rows}, {("недостача", "audit", "ok")})
    corona = next((w for w in rows if w["item"] == ПИВО), None)
    eq("A: полкоробки по прайсу — половина коробки", SR._wo_row(corona, cat)["aed"] if corona else None, SR._price(cat[ПИВО]) * 0.5)
    st, r = await call(SR.handle_audit_short_undo, req("/api/owner/stock/audit/short/undo", {"district": РАЙОН, "day": D}, owner_id=1))
    eq("A: «выбрать заново»", st, 200)
    eq("A: лента после отмены пуста", await лента(), [])
    a = await db.audit_get(РАЙОН, D) or {}
    eq("A: снова ждёт решения", (bool((a.get("short") or {}).get("resolved_at")), bool(a.get("closed_at"))), (False, False))

    print("B. одна строка Corona 0,5, с виновным")
    cat = await мир("b", [(ПИВО, 0.5)])
    st, r = await call(SR.handle_audit_short,
                       req("/api/owner/stock/audit/short",
                           {"district": РАЙОН, "day": D, "blame": True, "who": ВОДИТЕЛЬ, "amount": 71, "as": "STAR"}, owner_id=1))
    eq("B: ответ", st, 200)
    eq("B: списание полкоробки есть", await лента(), [(ПИВО, 0.5)])
    a = await db.audit_get(РАЙОН, D) or {}
    wids = (a.get("short") or {}).get("writeoffs") or []
    doc = (await db.writeoff_get(wids[0])) if wids else {}
    eq("B: удержание на этом списании", ((doc.get("comp") or {}).get("amount"), (doc.get("comp") or {}).get("who")), (71, ВОДИТЕЛЬ))
    eq("B: в ответе удержание", bool(r.get("comp")), True)
    eq("B: решена с виновным", ((a.get("short") or {}).get("blame"), (a.get("short") or {}).get("amount"), (a.get("short") or {}).get("who")), (True, 71, ВОДИТЕЛЬ))

    print(("ВСЁ ПРОШЛО" if not FAIL else f"ПРОВАЛЕНО: {len(FAIL)} — " + ", ".join(FAIL)))
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
