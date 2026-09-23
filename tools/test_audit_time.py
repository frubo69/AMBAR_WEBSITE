"""Ревизия, растянутая во времени (владелец, 23 сен 2026: «он начал ревизию
вчера, закончил сегодня… программа пишет недостачу, хотя этот товар же уже
продали»).

Ревизия — не мгновение, а часы, иногда сутки. Раньше каждую позицию сравнивали
с остатком «сейчас», и всё, что случилось с полкой ПОСЛЕ подсчёта, становилось
выдуманным расхождением: ночная продажа — излишком, утренняя поставка —
недостачей. Теперь позиция сравнивается с тем, что числилось в момент, когда
её считали: события после этого момента отматываются назад.

Двенадцать сценариев на mongomock с настоящими _audit_lines / audit_sheet /
audit_finish. У каждой позиции своя история; там, где раньше выходила
выдумка, в скобках указано, что показывала программа до правки.

    python3 tools/test_audit_time.py
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db, stock_routes as SR                                   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

U = timezone.utc
ДЕНЬ = "2026-09-22"
РАЙОН = "jvc"
def T(d, h, m=0): return datetime(2026, 9, d, h, m, tzinfo=U)


async def пересчёт(строки):
    await db.save_stock_count(РАЙОН, "2026-09-21", {
        "district": РАЙОН, "day": "2026-09-21", "counted_at": T(21, 6).isoformat(),
        "first_time": False, "counted_by": 0,
        "lines": [{"id": pid, "name": pid, "price": 100, "unit": 1, "actual": q, "counted": True}
                  for pid, q in строки.items()]})


async def продажа(pid, n, at, отметили=None, k=[0]):
    """Заказ приняли в момент at, доставку отметили тогда же или позже."""
    k[0] += 1
    await db._db.orders.insert_one({
        "order_id": f"S{k[0]}", "status": "delivered", "office_id": РАЙОН,
        "items": [{"id": pid, "qty": n}],
        "timestamp": at.isoformat().replace("+00:00", ""),
        "delivered_at": (отметили or at).isoformat().replace("+00:00", "")})


async def код(cid, pid, at, src, qty=1):
    await db._db.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                                      "product_name": pid, "district": РАЙОН, "origin": РАЙОН,
                                      "at": at, "src": src, "qty": qty})


async def скан(pid, at, n=1, qty=1.0, k=[0]):
    """n бутылок позиции, увиденных камерой в этот момент."""
    for _ in range(n):
        k[0] += 1
        await db._db.audit_scans.insert_one({
            "_id": f"{РАЙОН}:{ДЕНЬ}:sc{k[0]}", "district": РАЙОН, "day": ДЕНЬ,
            "code": f"sc{k[0]}", "product_id": pid, "at": at, "verdict": "ok", "qty": qty})


async def строки():
    SR.base_drop()
    lines, _ = await SR._audit_lines(РАЙОН, ДЕНЬ)
    return {l["id"]: l for l in lines}


async def main():
    db._db = AsyncMongoMockClient()["ambar_audit_time"]
    SR._biz_day = lambda *a, **k: ДЕНЬ
    # Невнесённых бутылок в этом стенде нет: всё, что числится, заведено кодами.
    import qr_routes
    async def _none(*a, **k): return {}
    qr_routes.unscanned_by_district = _none

    await пересчёт({"p1": 10, "p2": 10, "p3": 5, "p4": 5, "p5": 4, "p6": 6,
                    "p7": 5, "p8": 3, "p9": 2, "p10": 3, "p11": 1, "p12": 5, "p31": 2})
    await db._db.stock_audits.insert_one(
        {"district": РАЙОН, "day": ДЕНЬ, "started_at": T(22, 16).isoformat(), "started_by": 1})

    # ── что происходило с каждой позицией ────────────────────────────────
    await скан("p1", T(22, 17), 10)                       # посчитали всё
    await продажа("p1", 2, T(22, 20))                     # продали ПОСЛЕ подсчёта

    await продажа("p2", 2, T(22, 16, 30))                 # продали ДО подсчёта
    await скан("p2", T(22, 17), 8)

    await скан("p3", T(22, 17), 5)
    await код("i1", "p3", T(23, 9), "intake")             # приёмка ПОСЛЕ подсчёта
    await код("i2", "p3", T(23, 9), "intake")
    await код("i3", "p3", T(23, 9), "intake")

    await код("i4", "p4", T(22, 16, 30), "intake")        # приёмка ДО подсчёта,
    await код("i5", "p4", T(22, 16, 30), "intake")        # привезённое не посчитали
    await код("i6", "p4", T(22, 16, 30), "intake")
    await скан("p4", T(22, 17), 5)

    # p5 — не сканировали вовсе
    await скан("p6", T(22, 17), 4)                        # настоящая пропажа: 6 − 4

    await скан("p7", T(22, 17), 5)
    await db._db.stock_transfers.insert_one(             # уехало ПОСЛЕ подсчёта
        {"product_id": "p7", "qty": 2, "at": T(22, 21).isoformat(), "day": ДЕНЬ,
         "from": РАЙОН, "to": "bbay"})

    await скан("p8", T(22, 17), 3)
    await db._db.writeoffs.insert_one(                    # бой ПОСЛЕ подсчёта
        {"_id": "w1", "district": РАЙОН, "item": "p8", "qty": 1, "at": T(22, 22),
         "state": "ok", "src": "driver"})

    await скан("p9", T(22, 17), 2)
    await код("m1", "p9", T(22, 23), "new")               # внесли руками ПОСЛЕ подсчёта

    await скан("p10", T(22, 17), 3)                       # ничего не происходило
    await скан("p11", T(22, 17), 1)
    for i in range(5):                                    # приёмка ПОСЛЕ, много
        await код(f"i7{i}", "p11", T(23, 9), "intake")

    # Заказ приняли ДО подсчёта, а «доставлено» водитель отметил ночью:
    # бутылка уехала с полки раньше, чем её считали.
    await продажа("p12", 1, T(22, 16, 30), отметили=T(22, 23))
    await скан("p12", T(22, 17), 4)

    await скан("p31", T(22, 17), 4, qty=0.5)              # пиво: 4 кода по полкоробки
    await продажа("p31", 12, T(22, 20))                   # продали 12 банок = полкоробки

    r = await строки()
    print("── что случилось ПОСЛЕ подсчёта — расхождением быть не должно ──")
    eq("продали после подсчёта (было: излишек 2)", (r["p1"]["expected"], r["p1"]["actual"], r["p1"]["diff"]), (10, 10, 0))
    eq("приёмка после подсчёта (было: недостача 3)", (r["p3"]["expected"], r["p3"]["actual"], r["p3"]["diff"]), (5, 5, 0))
    eq("уехало в другой район после подсчёта (было: излишек 2)", (r["p7"]["expected"], r["p7"]["diff"]), (5, 0))
    eq("бой после подсчёта (было: излишек 1)", (r["p8"]["expected"], r["p8"]["diff"]), (3, 0))
    eq("внесли руками после подсчёта (было: недостача 1)", (r["p9"]["expected"], r["p9"]["diff"]), (2, 0))
    eq("пиво: продали полкоробки после подсчёта (было: излишек 0.5)",
       (r["p31"]["expected"], r["p31"]["actual"], r["p31"]["diff"]), (2, 2, 0))

    print("── что случилось ДО подсчёта — считается как считалось ────────")
    eq("продали до подсчёта — полка меньше, и камера видит столько же", (r["p2"]["expected"], r["p2"]["diff"]), (8, 0))
    eq("привезли до подсчёта, а не посчитали — недостача 3", (r["p4"]["expected"], r["p4"]["diff"]), (8, 3))
    eq("позицию не сканировали вовсе — недостача на весь остаток", (r["p5"]["expected"], r["p5"]["diff"]), (4, 4))
    eq("настоящая пропажа остаётся недостачей", (r["p6"]["expected"], r["p6"]["diff"]), (6, 2))
    eq("заказ принят до подсчёта, «доставлено» отметили ночью — расхождения нет",
       (r["p12"]["expected"], r["p12"]["actual"], r["p12"]["diff"]), (4, 4, 0))
    eq("где ничего не происходило — как было", (r["p10"]["expected"], r["p10"]["diff"]), (3, 0))
    eq("откат не уходит в минус и не выдумывает излишков", (r["p11"]["expected"], r["p11"]["diff"]), (1, 0))

    print("── итог и завершение ──────────────────────────────────────────")
    lines, _ = await SR._audit_lines(РАЙОН, ДЕНЬ)
    t = SR._audit_totals(lines)
    eq("в недостаче только настоящее: p4 (3) + p5 (4) + p6 (2)", t["short_qty"], 9)
    st, rep = await SR.audit_finish(РАЙОН, ДЕНЬ, 1, "Тест")
    eq("ревизия завершилась", st, 200)
    сохр = {l["id"]: l for l in ((await db.audit_get(РАЙОН, ДЕНЬ)) or {}).get("lines", [])}
    eq("в отчёте те же числа, что на экране", (сохр["p1"]["diff"], сохр["p4"]["diff"]), (0, 3))

    # После завершения жизнь идёт дальше — отчёт обязан остаться прежним.
    await продажа("p1", 3, T(23, 12))
    await код("i9", "p3", T(23, 13), "intake")
    SR.base_drop()
    sheet = await SR.audit_sheet(РАЙОН, ДЕНЬ)
    s2 = {l["id"]: l for l in sheet["rows"]}
    eq("завершённая ревизия не поехала от новых продаж и поставок",
       (s2["p1"]["diff"], s2["p3"]["diff"], sheet["audit"]["state"]), (0, 0, "pending"))
    eq("и её итоги те же", sheet["totals"]["short_qty"], 9)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
