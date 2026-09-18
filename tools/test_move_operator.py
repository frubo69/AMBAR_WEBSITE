"""Заявка на перемещение от оператора (владелец, 18 сен 2026: «сделай, чтобы
операторы могли создавать заявку на перемещение — ровно такую, как сегодня
водители видят, и предельно удобно»). mongomock + настоящие move_routes:
  • доска: по каждой позиции — сколько лежит и сколько кодов в каждом районе,
    норма; предложение «по норме» — только для выбранного района;
  • заявка — в свой район; в чужой нельзя; водители видят её как обычную;
  • «Сегодня»: оператору видно то, что везут к нему и от него, и только это;
  • снять можно неначатую задачу своего района; начатую — нет."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, move_routes as MV

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"; T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D
UMAR = {"jvc", "tecom"}                                  # районы Умара

async def main():
    db._db = AsyncMongoMockClient()["ambar_mvop"]; d = db._db
    vodka, gin = "p1", "p55"
    shelf = {"jvc": {vodka: 1, gin: 0}, "tecom": {vodka: 0, gin: 0},
             "bbay": {vodka: 9, gin: 4}, "silicon": {vodka: 0, gin: 6}, "alguses": {vodka: 0, gin: 0}}
    for oid, per in shelf.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]),
                       "actual": q, "counted": True} for p, q in per.items()]})
    for i in range(9):
        await d.qr_codes.insert_one({"_id": f"v{i}", "status": "active", "product_id": vodka,
            "product_name": SR._catalog()[vodka]["name"], "district": "bbay", "origin": "bbay",
            "src": "cover", "qty": 1, "at": T0})
    for oid, pid, n in (("jvc", vodka, 6), ("bbay", vodka, 4), ("jvc", gin, 3), ("silicon", gin, 2), ("bbay", gin, 2)):
        await d.stock_norms.insert_one({"district": oid, "product_id": pid, "norm": n})

    # ── доска ──────────────────────────────────────────────────────────────
    # Ручная заявка (владелец: «не умная — операторы должны собирать её вручную»):
    # на доске только то, что где лежит, без подсказок и норм.
    b = await MV.board(UMAR)
    pv = next(p for p in b["products"] if p["id"] == vodka)
    eq("по водке видно, где сколько лежит", (pv["have"]["jvc"], pv["have"]["bbay"]), (1, 9))
    eq("и сколько там кодов — столько сканером и возьмут", (pv["codes"]["bbay"], pv["codes"]["jvc"]), (9, 0))
    eq("подсказок нет: ни нормы, ни предложения", ("norm" in pv, "suggest" in b), (False, False))
    eq("свои районы отмечены", sorted(x["id"] for x in b["districts"] if x["mine"]), sorted(UMAR))
    eq("позиции, которых нигде нет, в списке не мешают",
       all(any(v for v in p_["have"].values()) for p_ in b["products"]), True)

    # ── создание ───────────────────────────────────────────────────────────
    r = await MV.create_by_operator("Умар", "bbay", [{"from": "silicon", "id": gin, "qty": 1}], "", UMAR)
    eq("чужие районы между собой — нельзя", r, {"ok": False, "error": "not_yours"})
    r = await MV.create_by_operator("Умар", "bbay", [{"from": "jvc", "id": vodka, "qty": 1}], "", UMAR)
    eq("из своего в чужой — можно (отдаёт свой)", (r["ok"], r["lines"]), (True, 1))
    await MV.cancel_by_operator(r["move_id"], "bbay", {"bbay"})
    r = await MV.create_by_operator("Умар", "jvc", [{"from": "bbay", "id": vodka, "qty": 5},
                                                    {"from": "silicon", "id": gin, "qty": 1},
                                                    {"from": "jvc", "id": gin, "qty": 3}], "", UMAR)
    eq("в свой — заявка, сам себе — мимо", (r["ok"], r["lines"], len(r["skipped"])), (True, 2, 1))
    mid = r["move_id"]
    doc = await db.move_order_get(mid)
    eq("кто создал — видно", doc["by"], "Умар · оператор")
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("водитель района видит её как обычную заявку", (len(v["free"]), v["free"][0]["need"]), (1, 6))
    g = await MV.tasks_for_driver("Парвиз", "bbay")
    eq("отдающий видит, что у него заберут", (len(g["give"]), g["give"][0]["need"]), (1, 5))

    # ── «Сегодня» ──────────────────────────────────────────────────────────
    lv = await MV.live_for(UMAR)
    live_ = lambda L: [(t["district"], t["side"]) for t in L["tasks"] if t["status"] != "cancelled"]
    eq("Умару видно своё: к нему", live_(lv), [("jvc", "in")])
    eq("снятая — тоже видна, как «снята»", [(t["district"], t["status"]) for t in lv["tasks"] if t["status"] == "cancelled"],
       [("bbay", "cancelled")])
    lb = await MV.live_for({"bbay"})
    eq("Джанабилю — от него", live_(lb), [("jvc", "out")])
    eq("постороннему — ничего", (await MV.live_for({"alguses"}))["tasks"], [])

    # ── снятие ─────────────────────────────────────────────────────────────
    eq("чужую задачу не снять", await MV.cancel_by_operator(mid, "jvc", {"bbay"}), {"ok": False, "error": "not_yours"})
    await MV.claim(mid, "jvc", "Худоба", 5)
    s1 = await MV.scan(mid, "jvc", "v0", "Худоба", 5)
    eq("водитель начал — одна бутылка уже в машине", s1["ok"], True)
    eq("начатую не снять", await MV.cancel_by_operator(mid, "jvc", UMAR), {"ok": False, "error": "started", "driver": "Худоба"})
    r2 = await MV.create_by_operator("Умар", "tecom", [{"from": "bbay", "id": gin, "qty": 1}], "ошибся районом", UMAR)
    eq("неначатую — снимается", await MV.cancel_by_operator(r2["move_id"], "tecom", UMAR), {"ok": True})
    eq("и у водителей её больше нет", (await MV.tasks_for_driver("Файзуло", "tecom"))["free"], [])
    rs = await MV.create(rows=[{"from": "bbay", "to": "jvc", "id": gin, "qty": 1}], by="STAR")
    eq("заявку владельца оператор не снимает", await MV.cancel_by_operator(rs["move_id"], "jvc", UMAR),
       {"ok": False, "error": "owner_order"})
    eq("второй раз снять нечего", (await MV.cancel_by_operator(r2["move_id"], "tecom", UMAR))["error"], "gone")

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
