"""Недоехавшее возвращается отдающему (владелец, 24 сен 2026: «если водитель
пишет что принял неровно и какой-то товар не доехал — где логика?»).

Логики не было: бутылка переезжает в момент скана ОТДАЮЩЕГО, а «принял
неровно» оставалось запиской. Непришедшее продолжало числиться за получателем
и вылезало недостачей на его ревизии — за товар, которого он не видел.

Теперь неподтверждённые бутылки уезжают обратно на район отдающего: поимённо
кодами, а чего кодов не хватило — числом. «Пришло всё» возвращает их назад.

    python3 tools/test_move_short.py
"""
import asyncio, os, sys
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

MID, КУДА, ОТКУДА = "MV-SHORT-1", "tecom", "alguses"
КОДЫ = {"101": "p59", "102": "p5", "103": "p5", "104": "p7"}   # 104 получатель отсканировал


async def завести(recv_codes=("104",), codes=tuple(КОДЫ)):
    db._db = AsyncMongoMockClient()["ambar_move_short"]
    sr.base_drop()
    for c, pid in КОДЫ.items():
        await db._db.qr_codes.insert_one({"_id": c, "status": "active", "product_id": pid,
                                          "district": КУДА, "qty": 1, "moves": [
                                              {"from": ОТКУДА, "to": КУДА, "at": "2026-09-20T10:55:00"}]})
    recv = {c for c in recv_codes}
    await db._db.move_orders.insert_one({
        "_id": MID, "day": "2026-09-19", "status": "open", "by": "оператор",
        "tasks": {КУДА: {"lines": [
            {"from": ОТКУДА, "id": "p59", "name": "Tanqueray 1 ltr", "qty": 1, "got": 1.0,
             "recv": 1.0 if "101" in recv else None},
            {"from": ОТКУДА, "id": "p5", "name": "Smirnoff Vodka 1 ltr", "qty": 2, "got": 2.0,
             "recv": float(len(recv & {"102", "103"})) or None},
            {"from": ОТКУДА, "id": "p7", "name": "Grey Goose 1 ltr", "qty": 1, "got": 1.0,
             "recv": 1.0 if "104" in recv else None},
        ], "give": {ОТКУДА: {"driver": "Худоба", "claimed_at": "x", "started_at": "x",
                             "done_at": "2026-09-20T10:56:00",
                             "codes": list(codes), "codes_q": 4.0,
                             "recv_codes": list(recv)}}}}})


async def где(c):
    q = await db._db.qr_codes.find_one({"_id": c})
    return (q or {}).get("district")


async def main():
    await завести()
    r = await mv.accept(MID, КУДА, ОТКУДА, "Алишер", 7, КУДА, ok=False, note="одной коробки нет")
    eq("приняли неровно", r.get("ok"), True)
    eq("непринятые бутылки уехали обратно на район отдающего",
       [await где(c) for c in ("101", "102", "103")], [ОТКУДА, ОТКУДА, ОТКУДА])
    eq("принятая осталась у получателя", await где("104"), КУДА)
    переезды = [t async for t in db._db.stock_transfers.find({"to": ОТКУДА})]
    eq("в книге переездов это видно", len(переезды), 3)
    eq("и помечено, что это возврат передачи, а не чей-то переезд",
       sorted({(bool(t.get("move_back")), t.get("move_id")) for t in переезды}), [(True, MID)])

    doc = await db.move_order_get(MID)
    g = doc["tasks"][КУДА]["give"][ОТКУДА]
    eq("в передаче записано, что вернулось", sorted(g.get("back_codes") or []), ["101", "102", "103"])
    eq("числом возвращать не пришлось — хватило кодов", g.get("back_qty"), [])

    # «Пришло всё» — товар был у получателя, возвращаем обратно к нему.
    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("расхождение снято", r.get("ok"), True)
    eq("бутылки вернулись получателю",
       [await где(c) for c in ("101", "102", "103", "104")], [КУДА] * 4)
    g = (await db.move_order_get(MID))["tasks"][КУДА]["give"][ОТКУДА]
    eq("отметка о возврате снята", (g.get("back_codes"), g.get("back_qty")), ([], []))

    # Передача старого образца: коды не писали — возвращаем числом.
    await завести(recv_codes=(), codes=())
    r = await mv.accept(MID, КУДА, ОТКУДА, "Алишер", 7, КУДА, ok=False, note="не довезли")
    g = (await db.move_order_get(MID))["tasks"][КУДА]["give"][ОТКУДА]
    eq("без кодов возвращаем числом по позициям",
       sorted((x["id"], x["qty"]) for x in g.get("back_qty") or []),
       [("p5", 2), ("p59", 1), ("p7", 1)])
    строки = [t async for t in db._db.stock_transfers.find({"to": ОТКУДА, "src": "move_back"})]
    eq("и это обычные переезды в книге", len(строки), 3)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
