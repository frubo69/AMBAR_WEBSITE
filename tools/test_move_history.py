"""История перемещений (владелец, 21 сен 2026: «сделай гораздо более
информативную историю перемещений, чтоб там был слайдер по датам, во-вторых
чёткое зонирование по районам, когда именно какое перемещение было
отсканировано отдающей стороной и когда принимающей»).

mongomock + настоящие move_routes: заявка собирается, отдающий сканирует,
получатель сканирует и принимает — и всё это должно прочитаться в истории.

  • день передачи — день её последнего события, а не день заявки: отдали
    вечером, приняли утром — передача стоит в том дне, когда её приняли;
  • зонирование по району-получателю, внутри — пары «откуда → куда»;
  • обе стороны скана: когда отдающий поднёс первую бутылку и когда последнюю,
    когда получатель сканировал и когда отметил «Принял», кто и когда проверял
    отложенное;
  • чего не было, того в истории нет: пара без единого скана не показывается
    ни открытой, ни снятой;
  • переезды сканом мимо заявок — отдельным списком, с ними приходит день;
  • числовой telegram-id проверявшего наружу не отдаётся.

Запуск: python3 tools/test_move_history.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import bizday, db, stock_routes as SR, move_routes as MV

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

# Дни берём от настоящих учётных суток: сканы пишутся временем «сейчас», и
# прибитая к коду дата развалила бы тест в любой другой день.
СЕГОДНЯ = bizday.biz_day()
ВЧЕРА = (datetime.strptime(СЕГОДНЯ, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
ВЧЕРА_Ч = bizday.day_start(ВЧЕРА) + timedelta(hours=4)     # середина вчерашней смены
T0 = bizday.day_start(ВЧЕРА) - timedelta(days=1)
SR._biz_day = lambda *a, **k: СЕГОДНЯ


async def code(d, cid, pid, district, qty=1):
    await d.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                                 "product_name": SR._catalog()[pid]["name"], "district": district,
                                 "origin": district, "src": "cover", "qty": qty, "at": T0})


async def полка(d, oid, per):
    await db.save_stock_count(oid, ВЧЕРА, {"district": oid, "day": ВЧЕРА,
        "counted_at": T0.isoformat(), "first_time": True, "counted_by": 0,
        "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]),
                   "actual": q, "counted": True} for p, q in per.items()]})


def пара(h, откуда, куда):
    for r in h["districts"]:
        if r["id"] == куда:
            for p in r["pairs"]:
                if p["from"] == откуда:
                    return p
    return None


async def main():
    db._db = AsyncMongoMockClient()["ambar_mv_hist"]; d = db._db
    водка = "p1"
    for oid, per in (("bbay", {водка: 4}), ("jvc", {водка: 0}), ("silicon", {водка: 2})):
        await полка(d, oid, per)
    for i in range(4): await code(d, f"v{i}", водка, "bbay")
    for i in range(2): await code(d, f"s{i}", водка, "silicon")

    print("── заявка: отдали и приняли ───────────────────────────────────")
    r = await MV.create([{"from": "bbay", "to": "jvc", "id": водка, "qty": 2},
                         {"from": "silicon", "to": "jvc", "id": водка, "qty": 1}])
    mid = r["move_id"]
    # Бизнес Бей отдаёт две бутылки, JVC обе принимает и подтверждает.
    for c in ("v0", "v1"):
        await MV.scan(mid, "jvc", c, "Худоба", 11, "bbay")
    for c in ("v0", "v1"):
        await MV.receive(mid, "jvc", "bbay", c, "Фарух", 22, "jvc")
    a = await MV.accept(mid, "jvc", "bbay", "Фарух", 22, "jvc")
    eq("передача принята", a.get("ok"), True)
    # Силикон отсканировал свою, но её ещё не приняли.
    await MV.scan(mid, "jvc", "s0", "Алишер", 33, "silicon")
    # Старший пересчитал отложенное у Силикона — проверка, а не приёмка.
    await MV.check_done(mid, "jvc", "silicon", "STAR", 987654321, 1, 0, [])

    h = await MV.history(СЕГОДНЯ)
    eq("район один — тот, куда везли", [r["code"] for r in h["districts"]], ["B1"])
    eq("в нём обе передачи", sorted(p["from_code"] for p in h["districts"][0]["pairs"]), ["B2", "B3"])
    bb, si = пара(h, "bbay", "jvc"), пара(h, "silicon", "jvc")
    eq("принятая — со статусом «принято»", bb["status"], "done")
    eq("отданная, но не принятая — «ждёт приёмки»", si["status"], "given")
    eq("у принятой есть обе стороны скана",
       (bool(bb["give_from"]), bool(bb["give_to"]), bool(bb["recv_at"]), bool(bb["accepted_at"])),
       (True, True, True, True))
    eq("и видно, кто отдал и кто принял", (bb["driver"], bb["accepted_by"]), ("Худоба", "Фарух"))
    eq("у непринятой приёмка пустая", (si["recv_at"], si["accepted_at"], si["accepted_by"]), ("", "", ""))
    eq("сколько отдали и сколько приняли", (bb["got"], bb["recv"], si["got"], si["recv"]), (2, 2, 1, 0))
    eq("позиции передачи видны", [(l["name"], l["got"]) for l in bb["lines"]],
       [(SR._catalog()[водка]["name"], 2)])
    eq("проверка отложенного записана", (si["check"]["by_name"], si["check"]["ok"]), ("STAR", True))
    eq("числового id проверявшего в ответе нет", "by" in si["check"], False)

    print("── чего не было, того в истории нет ───────────────────────────")
    r2 = await MV.create([{"from": "bbay", "to": "tecom", "id": водка, "qty": 1}])
    h = await MV.history(СЕГОДНЯ)
    eq("пара, где не отсканировали ни одной бутылки, не показывается",
       [r["code"] for r in h["districts"]], ["B1"])
    await db.move_order_cancel(r2["move_id"], "tecom", datetime.now(timezone.utc))
    h = await MV.history(СЕГОДНЯ)
    eq("снятая — тем более", [r["code"] for r in h["districts"]], ["B1"])

    print("── день передачи — день события ───────────────────────────────")
    # Отдали вчера, приняли сегодня: передача стоит в сегодняшнем дне.
    вчера_ч = ВЧЕРА_Ч
    await d.move_orders.update_one(
        {"_id": mid}, {"$set": {"tasks.jvc.give.bbay.started_at": вчера_ч,
                                "tasks.jvc.give.bbay.at": вчера_ч}})
    h = await MV.history(СЕГОДНЯ)
    eq("принятая сегодня — в сегодняшнем дне", bool(пара(h, "bbay", "jvc")), True)
    hy = await MV.history(ВЧЕРА)
    eq("во вчерашнем её нет", пара(hy, "bbay", "jvc"), None)
    # А та, которую только отдали вчера и не приняли, — во вчерашнем.
    await d.move_orders.update_one(
        {"_id": mid}, {"$set": {"tasks.jvc.give.silicon.started_at": вчера_ч,
                                "tasks.jvc.give.silicon.at": вчера_ч,
                                "tasks.jvc.give.silicon.check.at": вчера_ч}})
    hy = await MV.history(ВЧЕРА)
    eq("отданная вчера и не принятая — во вчерашнем дне", bool(пара(hy, "silicon", "jvc")), True)
    eq("в списке дней оба дня со счётом",
       {x["day"]: x["n"] for x in (await MV.history(СЕГОДНЯ))["days"]},
       {СЕГОДНЯ: 1, ВЧЕРА: 1})

    print("── переезды мимо заявок ───────────────────────────────────────")
    await db.add_stock_transfer({"day": СЕГОДНЯ, "from": "silicon", "to": "jvc",
                                 "product_id": водка, "product_name": SR._catalog()[водка]["name"],
                                 "qty": 1, "src": "qr", "code": "s1", "by": 1,
                                 "by_name": "STAR", "by_kind": "owner",
                                 "at": datetime.now(timezone.utc).isoformat()})
    h = await MV.history(СЕГОДНЯ)
    eq("свободный переезд — отдельным списком, не в районах",
       (len(h["free"]), h["free"][0]["to_code"] if h["free"] else ""), (1, "B1"))
    eq("и он посчитан в дне", {x["day"]: x["n"] for x in h["days"]}[СЕГОДНЯ], 2)
    eq("переезд по заявке в свободные не попал",
       all((x.get("by_kind") or "") != "move" for x in h["free"]), True)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
