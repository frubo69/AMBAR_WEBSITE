"""Перемещение: бутылка пришла, а наклейка не сканируется (владелец, 16 ноя
2026, на «Начать чинить уже сработавшие ошибки по списку?»: «Да, чини»).

Что было. Получатель принимает перемещение сканом. Наклейку камера не берёт —
оставалось «Не всё пришло»: непринятое возвращается отдающему (правило 24 сен).
5 ноя 2026, B1 → B5: получатель написал «всё получил, одну не сканируется» —
Absolut ушёл обратно на B1 по бумагам, лежал на B5 и вышел недостачей на
ревизии B1 11 ноя. Теперь в листе «Не всё пришло» у строки есть «Не
сканируется»: отмеченное остаётся за получателем, назад уходит только то, чего
правда нет.

    python3 tools/test_move_keep.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db, move_routes as mv, stock_routes as sr                # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

import bizday                                                    # noqa: E402

U = timezone.utc
DAY = bizday.biz_day(datetime.now(timezone.utc))    # история кладёт пару днём приёма
MID, КУДА, ОТКУДА = "MV-KEEP-1", "tecom", "jvc"
ДЕНЬ_СНИМКА = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
# Передача: Absolut 3 (два отсканированы получателем), Heineken коробка (две
# наклейки, одна отсканирована), Tanqueray 1 (не отсканирован, его правда нет).
КОДЫ = {"A1": ("p1", 1), "A2": ("p1", 1), "A3": ("p1", 1), "H1": ("p31", 0.5), "H2": ("p31", 0.5),
        "T1": ("p59", 1)}
ПРИНЯТЫ = ("A1", "A2", "H1")


async def завести():
    db._db = AsyncMongoMockClient()["ambar_move_keep"]
    sr.base_drop()
    старо = datetime.now(U) - timedelta(days=5)
    for o in (КУДА, ОТКУДА):
        await db.save_stock_count(o, ДЕНЬ_СНИМКА, {
            "district": o, "day": ДЕНЬ_СНИМКА, "counted_at": старо.isoformat(), "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": 24 if p == "p31" else 1,
                       "actual": (3 if p == "p1" else 1) if o == ОТКУДА else 0, "counted": True}
                      for p in ("p1", "p31", "p59")]})
    час = (datetime.now(U) - timedelta(hours=1)).isoformat()
    for c, (pid, q) in КОДЫ.items():
        await db._db.qr_codes.insert_one({"_id": c, "status": "active", "product_id": pid,
                                          "district": КУДА, "origin": ОТКУДА, "qty": q, "src": "cover",
                                          "at": старо, "moves": [{"from": ОТКУДА, "to": КУДА, "at": час}]})
        await db._db.stock_transfers.insert_one({"day": DAY, "from": ОТКУДА, "to": КУДА, "product_id": pid,
                                                 "qty": q, "src": "qr", "code": c, "at": час, "move_id": MID})
    recv = set(ПРИНЯТЫ)
    await db._db.move_orders.insert_one({
        "_id": MID, "day": DAY, "status": "open", "by": "оператор",
        "tasks": {КУДА: {"lines": [
            {"from": ОТКУДА, "id": "p1", "name": "Absolut 1 ltr", "qty": 3, "got": 3.0, "recv": 2.0, "unit": 1},
            {"from": ОТКУДА, "id": "p31", "name": "Heineken 0.33 can", "qty": 1, "got": 1.0, "recv": 0.5, "unit": 24},
            {"from": ОТКУДА, "id": "p59", "name": "Tanqueray 1 ltr", "qty": 1, "got": 1.0, "recv": None, "unit": 1},
        ], "give": {ОТКУДА: {"driver": "Худоба", "claimed_at": "x", "started_at": "x",
                             "done_at": час, "codes": list(КОДЫ), "codes_q": 5.0,
                             "recv_codes": list(recv)}}}}})


async def где(*codes):
    return [((await db._db.qr_codes.find_one({"_id": c})) or {}).get("district") for c in codes]


async def склад(o, *pids):
    sr.base_drop()
    have = ((await sr._district_base(DAY)).get(o) or {}).get("have_exact") or {}
    return tuple(float(have.get(p, 0)) for p in pids)


async def main():
    sr._biz_day = lambda *a, **k: DAY

    print("Отмечено «не сканируется»: Absolut 1 и полкоробки Heineken; Tanqueray правда нет")
    await завести()
    eq("до приёма: всё отданное числится у получателя", (await склад(КУДА, "p1", "p31", "p59"), await склад(ОТКУДА, "p1", "p31", "p59")),
       ((3.0, 1.0, 1.0), (0.0, 0.0, 0.0)))
    r = await mv.accept(MID, КУДА, ОТКУДА, "Муин", 7, КУДА, ok=False, note="всё получил, одну не сканируется",
                        kept=[{"id": "p1", "qty": 1}, {"id": "p31", "qty": 0.5}])
    eq("принято как есть", (r.get("ok"), r["task"]["status"]), (True, "diff"))
    eq("Absolut и Heineken с нечитаемой наклейкой остались у получателя", await где("A3", "H2"), [КУДА, КУДА])
    eq("Tanqueray, которого нет, вернулся отдающему", await где("T1"), [ОТКУДА])
    eq("отсканированное — у получателя", await где(*ПРИНЯТЫ), [КУДА] * 3)
    g = (await db.move_order_get(MID))["tasks"][КУДА]["give"][ОТКУДА]
    eq("в записи приёма: что не сканируется и что вернулось",
       (sorted((l["id"], l["sent"], l["got"], l.get("kept", 0)) for l in g["accept_lines"]), g.get("back_codes"), g.get("accept_note")),
       ([("p1", 3, 2, 1), ("p31", 1, 0.5, 0.5), ("p59", 1, 0, 0)], ["T1"], "всё получил, одну не сканируется"))
    eq("склад: у получателя Absolut 3 и коробка Heineken, Tanqueray у отдающего (было: Absolut 2 / 1, Heineken 0,5 / 0,5)",
       (await склад(КУДА, "p1", "p31", "p59"), await склад(ОТКУДА, "p1", "p31", "p59")), ((3.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    hist = await mv.history(DAY)
    пара = next((p for r in hist.get("districts") or [] for p in r.get("pairs") or [] if p.get("mid") == MID), {})
    eq("история старшего: вернулась 1, у строк стоит «не сканируется»",
       (пара.get("back_n"), sorted((l["id"], l.get("kept", 0)) for l in пара.get("accept_lines") or [])),
       (1, [("p1", 1), ("p31", 0.5), ("p59", 0)]))

    print("\nБез отметок — как раньше: всё непринятое возвращается")
    await завести()
    await mv.accept(MID, КУДА, ОТКУДА, "Муин", 7, КУДА, ok=False, note="не довезли")
    eq("три непринятых кода ушли отдающему", await где("A3", "H2", "T1"), [ОТКУДА] * 3)

    print("\nОтмечено всё, чего не хватает по скану: назад не уходит ничего")
    await завести()
    r = await mv.accept(MID, КУДА, ОТКУДА, "Муин", 7, КУДА, ok=False, note="",
                        kept=[{"id": "p1", "qty": 1}, {"id": "p31", "qty": 0.5}, {"id": "p59", "qty": 1}])
    g = (await db.move_order_get(MID))["tasks"][КУДА]["give"][ОТКУДА]
    eq("все бутылки у получателя, возврата нет", (r.get("ok"), await где("A3", "H2", "T1"), g.get("back_codes")), (True, [КУДА] * 3, None))

    print("\nОтметить больше, чем не отсканировано, нельзя; чужая позиция и мусор не считаются")
    await завести()
    r = await mv.accept(MID, КУДА, ОТКУДА, "Муин", 7, КУДА, ok=False, note="x",
                        kept=[{"id": "p1", "qty": 9}, {"id": "p31", "qty": 0.3}, {"id": "p77", "qty": 2}, "мусор", {"id": "p59", "qty": "?"}])
    g = (await db.move_order_get(MID))["tasks"][КУДА]["give"][ОТКУДА]
    eq("Absolut остался 1 (а не 9), Heineken 0,3 — меньше наклейки, не оставлен; остальное вернулось",
       (r.get("ok"), await где("A3", "H2", "T1"), sorted((l["id"], l.get("kept", 0)) for l in g["accept_lines"])),
       (True, [КУДА, ОТКУДА, ОТКУДА], [("p1", 1), ("p31", 0), ("p59", 0)]))

    print("\n«Пришло всё — расхождения нет» после отметок")
    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("возвращённое снова у получателя", (r.get("ok"), await где("A3", "H2", "T1")), (True, [КУДА] * 3))

    print("\n«Принял» по-прежнему только когда отсканировано всё")
    await завести()
    r = await mv.accept(MID, КУДА, ОТКУДА, "Муин", 7, КУДА, ok=True, kept=[{"id": "p1", "qty": 1}])
    eq("отметка «не сканируется» не делает приём ровным", (r.get("ok"), r.get("error")), (False, "scan_all"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
