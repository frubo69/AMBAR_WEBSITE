"""Расхождение при приёме — ошибка приёма (владелец, 24 сен 2026: «при
перемещении приняли весь товар, то что здесь написано это ошибка»).

Отдающий сканирует каждую бутылку — в этот момент она и переезжает. Получатель
сканирует при приёме для сверки; не поднёс к камере — передача помечается
«приняли неровно», хотя товар приехал. Склад от этой пометки не зависит, и
снятие её склад тоже не двигает: это проверяется здесь отдельно, иначе
«выравнивание» однажды начнут считать способом поправить остаток.

    python3 tools/test_move_even.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db, move_routes as mv                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

MID, КУДА, ОТКУДА = "MV-TEST-1", "tecom", "alguses"


async def завести(accept_ok=False):
    db._db = AsyncMongoMockClient()["ambar_move_even"]
    await db._db.move_orders.insert_one({
        "_id": MID, "day": "2026-09-19", "status": "done", "by": "оператор",
        "tasks": {КУДА: {"lines": [
            {"from": ОТКУДА, "id": "p59", "name": "Tanqueray 1 ltr", "qty": 1, "got": 1.0, "recv": None},
            {"from": ОТКУДА, "id": "p5", "name": "Smirnoff Vodka 1 ltr", "qty": 2, "got": 2.0, "recv": None},
            {"from": ОТКУДА, "id": "p7", "name": "Grey Goose 1 ltr", "qty": 5, "got": 5.0, "recv": 5.0},
        ], "give": {ОТКУДА: {
            "driver": "Алишер", "done_at": "2026-09-20T10:56:00",
            "accepted_at": "2026-09-20T15:39:47", "accepted_by": "Алишер",
            "accept_ok": accept_ok,
            "accept_lines": [{"id": "p59", "name": "Tanqueray 1 ltr", "sent": 1, "got": 0},
                             {"id": "p5", "name": "Smirnoff Vodka 1 ltr", "sent": 2, "got": 0}],
            "codes": ["14106", "19560", "19563", "11", "12", "13", "14", "15"],
            "codes_q": 8.0, "recv_codes": ["11", "12", "13", "14", "15"]}}}}})


async def main():
    await завести()
    складских = await db._db.stock_transfers.count_documents({})
    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("расхождение снято", r.get("ok"), True)

    task = (await db.move_order_get(MID))["tasks"][КУДА]
    g = task["give"][ОТКУДА]
    eq("передача считается принятой ровно", g.get("accept_ok"), True)
    eq("строк расхождения не осталось", g.get("accept_lines"), [])
    eq("прежние цифры сохранены рядом",
       [(l["id"], l["sent"], l["got"]) for l in g.get("accept_lines_was") or []],
       [("p59", 1, 0), ("p5", 2, 0)])
    eq("кто выровнял — записано", (g.get("evened_by"), bool(g.get("evened_at"))), ("fixxxik", True))
    eq("принято = отдано по каждой строке",
       [(l["id"], l.get("got"), l.get("recv")) for l in task["lines"]],
       [("p59", 1.0, 1.0), ("p5", 2.0, 2.0), ("p7", 5.0, 5.0)])
    eq("коды приёма сошлись с отданными", len(g.get("recv_codes") or []), len(g.get("codes") or []))
    eq("склад не тронут: бутылка переехала сканом отдающего",
       await db._db.stock_transfers.count_documents({}), складских)

    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("второй раз снимать нечего", (r.get("ok"), r.get("error")), (False, "not_diff"))

    await завести(accept_ok=True)
    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("ровно принятую не трогаем", (r.get("ok"), r.get("error")), (False, "not_diff"))

    await завести()
    r = await mv.accept_even("MV-НЕТ-ТАКОЙ", КУДА, ОТКУДА, "fixxxik", 1)
    eq("чужой заявки нет", (r.get("ok"), r.get("error")), (False, "gone"))
    await db._db.move_orders.update_one(
        {"_id": MID}, {"$set": {f"tasks.{КУДА}.give.{ОТКУДА}.accepted_at": None}})
    r = await mv.accept_even(MID, КУДА, ОТКУДА, "fixxxik", 1)
    eq("непринятую выравнивать нельзя", (r.get("ok"), r.get("error")), (False, "not_accepted"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
