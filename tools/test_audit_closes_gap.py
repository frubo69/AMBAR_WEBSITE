"""Закрытие ревизии сводит книги с полкой (владелец, 24 сен 2026: «убедись,
что это исправляет ситуацию со складом, что исправляются и недостачи»).

Сквозной ход одного района: пересчёт → продажи и переезд → ревизия камерой →
завершение → решение по недостаче. Проверяем главное: после закрытия склад
показывает ровно то, что увидела камера, а списания недостачи не вычитают
ничего второй раз.

    python3 tools/test_audit_closes_gap.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as sr                                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

РАЙОН, ДЕНЬ, ПОЗ = "tecom", "2026-09-23", "p1"


async def склад(pid=ПОЗ):
    sr.base_drop()
    b = (await sr._district_base(sr._biz_day())).get(РАЙОН) or {}
    return float((b.get("have_exact") or {}).get(pid) or 0)


async def подготовить(на_полке: int, отсканируют: int):
    db._db = AsyncMongoMockClient()["ambar_audit_gap"]
    sr.base_drop()
    t0 = datetime.now(timezone.utc) - timedelta(days=5)
    # Пересчёт: шесть бутылок.
    await db._db.stock_counts.insert_one({
        "district": РАЙОН, "day": "2026-09-14", "counted_at": t0.isoformat(),
        "lines": [{"id": ПОЗ, "actual": 6}]})
    # Полка: бутылки с кодами.
    for i in range(на_полке):
        await db._db.qr_codes.insert_one({
            "_id": f"c{i}", "status": "active", "product_id": ПОЗ, "district": РАЙОН,
            "qty": 1, "label": f"abs#{i:04d}", "at": t0 + timedelta(hours=i), "origin": РАЙОН})
    # Одну продали вчера — склад об этом знает.
    await db._db.orders.insert_one({
        "order_id": "AMB1", "office_id": РАЙОН, "status": "delivered",
        "timestamp": (t0 + timedelta(days=1)).isoformat(),
        "delivered_at": (t0 + timedelta(days=1)).isoformat(),
        "items": [{"id": ПОЗ, "name": "Absolut", "qty": 1, "price": 100}]})
    # Ревизия: камера видит часть.
    await db.audit_set(РАЙОН, ДЕНЬ, {"started_at": datetime.now(timezone.utc).isoformat(),
                                     "started_by": 1, "started_by_name": "STAR"})
    for i in range(отсканируют):
        await db._db.audit_scans.insert_one({
            "district": РАЙОН, "day": ДЕНЬ, "code": f"c{i}", "product_id": ПОЗ,
            "qty": 1, "verdict": "ok", "at": datetime.now(timezone.utc)})


async def main():
    # Числится 5 (6 минус продажа), на полке физически 3 — камера видит 3.
    await подготовить(на_полке=6, отсканируют=3)
    eq("до ревизии склад считает 5", await склад(), 5.0)
    строки, _ = await sr._audit_lines(РАЙОН, ДЕНЬ)
    r = next(x for x in строки if x["id"] == ПОЗ)
    eq("ревизия видит недостачу 2", (r["expected"], r["actual"], r["diff"]), (5, 3, 2))
    eq("и говорит, каких не нашли", len(r.get("missing") or []), 3)

    st, res = await sr.audit_finish(РАЙОН, ДЕНЬ, 1, "fixxxik")
    eq("ревизия завершена", st, 200)
    a = res["audit"]
    eq("ждёт решения по недостаче",
       (a["state"], a["short"]["qty"], [l["id"] for l in a["short"]["lines"]]),
       ("pending", 2, [ПОЗ]))
    eq("СКЛАД СОШЁЛСЯ С ПОЛКОЙ", await склад(), 3.0)

    # Решение: виноват водитель — списания недостачи не должны вычесть ещё раз.
    было = await склад()
    doc = await db.audit_get(РАЙОН, ДЕНЬ)
    # Решение по недостаче пишет списания вида «недостача» (src=audit). Склад
    # они не двигают: пересчёт ревизии уже записан без этих бутылок.
    for l in doc["short"]["lines"]:
        await db.writeoff_add({"district": РАЙОН, "day": ДЕНЬ, "item": l["id"],
                               "name": l["name"], "qty": l["qty"], "kind": "недостача",
                               "src": "audit", "state": "ok",
                               "at": datetime.now(timezone.utc).isoformat()})
    eq("списание недостачи склад второй раз не трогает", await склад(), было)

    # Бутылки нашлись и их досканировали — недостача уходит сама.
    await подготовить(на_полке=6, отсканируют=3)
    for i in (3, 4):
        await db._db.audit_scans.insert_one({
            "district": РАЙОН, "day": ДЕНЬ, "code": f"c{i}", "product_id": ПОЗ,
            "qty": 1, "verdict": "ok", "at": datetime.now(timezone.utc)})
    строки, _ = await sr._audit_lines(РАЙОН, ДЕНЬ)
    r = next(x for x in строки if x["id"] == ПОЗ)
    eq("досканировали — недостачи нет", (r["actual"], r["diff"]), (5, 0))
    eq("и искать больше нечего", r.get("missing"), None)
    st, res = await sr.audit_finish(РАЙОН, ДЕНЬ, 1, "fixxxik")
    eq("такая ревизия закрывается сразу", (res["audit"]["state"], res["audit"]["short"]), ("closed", None))
    eq("склад остался прежним — терять нечего", await склад(), 5.0)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
