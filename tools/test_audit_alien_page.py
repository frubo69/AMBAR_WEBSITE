"""Наклейка, отсканированная раньше, чем её код внесли в реестр (владелец,
3 ноя 2026: «верни то, чего хватало в той ревизии»).

Ревизия JVC 31 окт 2026: на бутылки без кодов клеили новые наклейки и
сканировали их сразу, а в реестр код ложился через минуту. Такой скан лежал
«чужим» без позиции, и дочитывался только при «Завершить» — страница же всё
это время показывала Absolut 14 из 16, Red Label 22 из 23, Bacardi Black 1
из 2, Corona полкоробки недостачей. Теперь лист ревизии дочитывает их сам,
тем же путём, что и завершение: внесли код — бутылка в счёте сразу, и с тем
количеством, что в реестре (полкоробки — 0,5, а не 1, как лёг скан).

Сценарий на mongomock с настоящими audit_sheet / _audit_refresh_alien.

    python3 tools/test_audit_alien_page.py
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
ДЕНЬ = "2026-11-02"
РАЙОН = "jvc"
def T(d, h, m=0, s=0): return datetime(2026, 11, d, h, m, s, tzinfo=U)


async def скан(code, pid, at, qty=1.0, verdict="ok"):
    await db._db.audit_scans.insert_one({
        "_id": db._audit_key(РАЙОН, ДЕНЬ, code), "district": РАЙОН, "day": ДЕНЬ,
        "code": code, "product_id": pid, "at": at, "verdict": verdict, "qty": qty})


async def наклейка(code, pid, at, qty=1, status="active"):
    """Код внесли в реестр как наклейку на бутылку, которая уже числится
    (src=cover — не приход)."""
    await db._db.qr_codes.insert_one({"_id": code, "status": status, "product_id": pid,
                                      "product_name": pid, "district": РАЙОН, "origin": РАЙОН,
                                      "at": at, "src": "cover", "qty": qty, "label": f"{pid}#{code}"})


async def строки():
    SR.base_drop()
    sheet = await SR.audit_sheet(РАЙОН, ДЕНЬ)
    return {l["id"]: l for l in sheet["rows"]}, sheet


async def скан_док(code):
    d = await db._db.audit_scans.find_one({"_id": db._audit_key(РАЙОН, ДЕНЬ, code)})
    return (d.get("verdict"), d.get("product_id"), d.get("qty"))


async def main():
    db._db = AsyncMongoMockClient()["ambar_alien_page"]
    SR._biz_day = lambda *a, **k: ДЕНЬ
    import qr_routes
    async def _none(*a, **k): return {}
    qr_routes.unscanned_by_district = _none

    await db.save_stock_count(РАЙОН, "2026-11-01", {
        "district": РАЙОН, "day": "2026-11-01", "counted_at": T(1, 6).isoformat(),
        "first_time": False, "counted_by": 0,
        "lines": [{"id": pid, "name": pid, "price": 100, "unit": 1, "actual": q, "counted": True}
                  for pid, q in {"p1": 3, "p31": 2}.items()]})
    await db._db.stock_audits.insert_one(
        {"district": РАЙОН, "day": ДЕНЬ, "started_at": T(2, 6).isoformat(), "started_by": 1})

    # p1 — 3 бутылки: две с кодами, третья без — наклеили n1 и сразу отсканировали
    await скан("sc1", "p1", T(2, 7, 0)); await скан("sc2", "p1", T(2, 7, 0))
    await скан("n1", "", T(2, 7, 5), verdict="alien")
    # p31 — пиво, 2 коробки: три кода по полкоробки + наклейка n2 на 12 банок
    for i in range(3):
        await скан(f"b{i}", "p31", T(2, 7, 10), qty=0.5)
    await скан("n2", "", T(2, 7, 10), verdict="alien")      # лёг как 1 — кода не знали
    await скан("n3", "", T(2, 7, 12), verdict="alien")      # так и не внесут
    await скан("n4", "", T(2, 7, 13), verdict="alien")      # внесут и уберут из реестра

    print("── до внесения кодов: наклейки — чужие, бутылки недостачей ──")
    r, sheet = await строки()
    eq("p1: камера 2 из 3", (r["p1"]["expected"], r["p1"]["actual"], r["p1"]["diff"]), (3, 2, 1))
    eq("p31: камера 1,5 из 2", (r["p31"]["expected"], r["p31"]["actual"], r["p31"]["diff"]), (2, 1.5, 0.5))
    eq("n1 так и лежит чужим", await скан_док("n1"), ("alien", "", 1.0))

    # через минуту коды внесли в реестр (src=cover — наклейка, не приход)
    await наклейка("n1", "p1", T(2, 7, 6))
    await наклейка("n2", "p31", T(2, 7, 11), qty=0.5)
    await наклейка("n4", "p1", T(2, 7, 14), status="deleted")

    print("── страница дочитала наклейки сама, без «Завершить» ──")
    r, sheet = await строки()
    eq("p1: все 3 на месте", (r["p1"]["expected"], r["p1"]["actual"], r["p1"]["diff"]), (3, 3, 0))
    eq("p31: полкоробки по реестру, не 1 как лёг скан",
       (r["p31"]["expected"], r["p31"]["actual"], r["p31"]["diff"]), (2, 2, 0))
    eq("наклейка — не приход: ожидание p1 не откатилось", r["p1"]["expected"], 3)
    eq("скан n1 стал бутылкой p1", await скан_док("n1"), ("ok", "p1", 1.0))
    eq("скан n2 стал полкоробки p31", await скан_док("n2"), ("ok", "p31", 0.5))
    eq("n3 — нет в реестре — так и чужой", await скан_док("n3"), ("alien", "", 1.0))
    eq("n4 — убран из реестра — так и чужой", await скан_док("n4"), ("alien", "", 1.0))

    print("── повторный заход ничего не меняет ──")
    eq("дочитывать больше нечего", await SR._audit_refresh_alien(РАЙОН, ДЕНЬ), 0)
    r, _ = await строки()
    eq("p1 и p31 как были", (r["p1"]["diff"], r["p31"]["diff"]), (0, 0))

    print("\nИТОГ:", "все прошли" if not FAIL else f"УПАЛО {len(FAIL)}: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
