"""Сквозной прогон учёта склада на копии базы (владелец, 17 сен 2026: «от и до
убедись, что в самых разных сценариях всё работает без противоречий»).
Пиво — коробками: QR на коробке (1) и на половинке (0.5), заказ 12/24 шт =
0.5/1. После каждого шага — инварианты:
  • карточка «Склад» = сумма остатков основы по районам;
  • долг «QR не внесён» района = Σ max(0, остаток − коды) по позициям;
  • ревизия: числится = остаток, кодовых = остаток − без кодов;
  • заявка = ceil(норма − остаток) по клетке (правок нет).
Настоящие db.* и настоящие функции stock_routes / qr_routes / supply_routes /
stock_value; по одной позиции на поставку (items.$ у mongomock)."""
import asyncio, os, sys, math
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, qr_routes as QR, supply_routes as sr, stock_value as SV
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D = "2026-09-16"; T0 = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
async def cost_map(): return {}
SV.cost_map = cost_map
async def notify(*a, **k): return None
sr._notify_done = notify
def T(minutes): return T0 + timedelta(minutes=minutes)
def iso(dt): return dt.isoformat()
async def base():
    SR.base_drop(); return await SR._district_base(D)
async def have(oid, pid):
    return (await base())[oid]["have_exact"].get(pid, 0)
async def invariants(label):
    b = await base(); v = await SV.build(D)
    total = round(sum(sum(x.values()) for x in (b[o]["have_exact"] for o in SR.OFFICE_IDS)) * 2) / 2
    eq(f"[{label}] карточка «Склад» = сумма основы ({total})", v["totals"]["bottles"], int(total) if total == int(total) else total)
    codes = await db.qr_by_product_district_all()
    d = {}; u, no = await QR.unscanned_by_district(None, d)
    for oid in ("jvc", "silicon"):
        h = b[oid]["have_exact"]; c = codes.get(oid) or {}
        want = round(sum(max(0.0, float(h.get(pid, 0)) - float(c.get(pid, 0))) for pid in set(h) | set(c)) * 2) / 2
        eq(f"[{label}] долг без QR {oid} = остаток − коды ({want})", round(float(u.get(oid, 0)), 1), round(want, 1))
        lines, _ = await SR._audit_lines(oid, D)
        for l in lines:
            if l["expected"] or l["actual"]:
                eq(f"[{label}] ревизия {oid} {l['id']}: числится = остаток", l["expected"], SR._num(h.get(l["id"], 0)))
                eq(f"[{label}] ревизия {oid} {l['id']}: кодовых = остаток − без кодов", l["coded"], SR._num(max(0, l["expected"] - l["noqr"])))
    o = await SR.order_rows(D); norms = await db.get_stock_norms()
    for r in o["all_rows"]:
        for oid in ("jvc", "silicon"):
            c = r["cells"][oid]; n = norms.get(f"{oid}:{r['id']}")
            if n is None: continue
            want = int(math.ceil(round(max(0.0, float(n) - float(b[oid]["have_exact"].get(r["id"], 0))), 6)))
            if want or c["need"]:
                eq(f"[{label}] заявка {oid} {r['id']} = ceil(норма − остаток)", c["need"], want)
async def scan_code(sid, oid, pid, code, qty=None, who="Худоба"):
    return await sr.task_scan(sid, oid, pid, code, who, 1, "", False, qty=qty)
def supply(sid, oid, pid, name, n, when):
    return {"_id": sid, "status": "open", "at": when, "day": D,
            "items": [{"id": pid, "name": name, "qty": n, "by_district": {oid: n}, "got": {oid: 0}}],
            "tasks": {oid: {"driver": "Худоба", "driver_id": 1, "claimed_at": when, "started_at": None, "noscan_at": None,
                            "done_at": None, "cancelled_at": None, "erev": 0, "scanned": 0, "qty": n, "positions": 1}}}
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    # ── пересчёт по листу (снимок на 10:00) и норма = снимок ────────────────
    for oid, lines in (("jvc", {"p1": 10, "p31": 2}), ("silicon", {"p1": 5})):
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": iso(T0), "first_time": True, "counted_by": 0,
            "lines": [{"id": pid, "name": pid, "price": 100, "unit": SR._unit(SR._catalog()[pid]), "actual": q, "counted": True} for pid, q in lines.items()]})
        for pid, q in lines.items(): await db.set_stock_norm(oid, pid, q)
    await db.stock_norm_rule_set({"kind": "snapshot", "day": D})
    await invariants("старт")
    # ── заказы: доставлен / вернули / снова доставлен / отменён ─────────────
    await d.orders.insert_one({"id": "O1", "timestamp": iso(T(60)), "status": "approved", "office_id": "jvc", "updated_at": iso(T(60)),
                               "items": [{"id": "p1", "qty": 3}, {"id": "p31", "qty": 1, "pcs": 12}]})
    eq("заказ принят, не доставлен — склад не тронут", (await have("jvc", "p1"), await have("jvc", "p31")), (10, 2))
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "delivered", "delivered_at": iso(T(90)), "updated_at": iso(T(90))}})
    eq("доставлен: 10 − 3 = 7, пиво 2 − 0,5 = 1,5", (await have("jvc", "p1"), await have("jvc", "p31")), (7, 1.5))
    await invariants("после доставки")
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "approved", "updated_at": iso(T(95))}})
    eq("вернули в доставку — на складе снова 10 и 2", (await have("jvc", "p1"), await have("jvc", "p31")), (10, 2))
    await d.orders.update_one({"id": "O1"}, {"$set": {"status": "delivered", "updated_at": iso(T(100))}})
    eq("снова доставлен — снова 7 и 1,5", (await have("jvc", "p1"), await have("jvc", "p31")), (7, 1.5))
    # ── приёмка сканом: водка 3 кода, пиво коробка + половинка ──────────────
    await d.supplies.insert_one(supply("S1", "jvc", "p1", "Absolut", 3, T(120)))
    for i in range(3): r = await scan_code("S1", "jvc", "p1", f"a{i}")
    eq("приёмка водки: 3 кода → +3 на складе, задача полна", (await have("jvc", "p1"), r["left"]), (10, 0))
    await d.supplies.insert_one(supply("S2", "jvc", "p31", "Heineken", 2, T(121)))
    await scan_code("S2", "jvc", "p31", "box1"); r = await scan_code("S2", "jvc", "p31", "half1", qty="0.5")
    eq("приёмка пива: коробка + половинка → +1,5, осталось 0,5", (await have("jvc", "p31"), r["left"]), (3, 0.5))
    await invariants("после приёмки сканом")
    # ── приём без сканирования: товар на складе сразу, кодов нет ────────────
    await d.supplies.insert_one(supply("S3", "silicon", "p1", "Absolut", 4, T(130)))
    r = await sr.task_noscan("S3", "silicon", "Худоба", False)
    eq("без сканирования: Силикон 5 + 4 = 9 сразу", (r.get("ok"), await have("silicon", "p1")), (True, 9))
    u, _ = await QR.unscanned_by_district(None, {}); eq("долг без QR Силикон = 9 (кодов нет)", u["silicon"], 9)
    for i in range(2): await scan_code("S3", "silicon", "p1", f"s{i}")
    eq("досканировали 2 из 4: склад тот же 9, долг 7", (await have("silicon", "p1"), (await QR.unscanned_by_district(None, {}))[0]["silicon"]), (9, 7))
    await invariants("после приёма без сканирования")
    # ── внесение кодов по пересчёту (cover) и новый товар (new) ─────────────
    for i in range(2): await db.qr_add(f"c{i}", "p1", "Absolut", "jvc", 1, T(140), "", extra={"src": "cover", "qty": 1, "origin": "jvc"})
    eq("cover: склад не растёт (10), долг 10 − 5 = 5", (await have("jvc", "p1"), (await QR.unscanned_by_district(None, {}))[0]["jvc"]), (10, 5 + 1.5))
    await db.qr_add("n1", "p1", "Absolut", "jvc", 1, T(141), "", extra={"src": "new", "qty": 1, "origin": "jvc"})
    eq("новый товар: склад 11, долг тот же", (await have("jvc", "p1"), (await QR.unscanned_by_district(None, {}))[0]["jvc"]), (11, 6.5))
    await db.qr_remove("n1")
    eq("удалили код нового товара: склад 10", await have("jvc", "p1"), 10)
    await invariants("после внесения и удаления")
    # ── переезды: код сканом (qty кода) и руками полкоробки ─────────────────
    r = await SR.move_by_code("a0", "silicon", 1, "Худоба", "driver")
    eq("переезд кода a0 JVC → Силикон: 9 и 10", (r["verdict"], await have("jvc", "p1"), await have("silicon", "p1")), ("ok", 9, 10))
    await db.add_stock_transfer({"day": D, "from": "jvc", "to": "silicon", "product_id": "p31", "qty": 0.5, "at": iso(T(150))})
    eq("переезд полкоробки пива руками: 2,5 и 0,5", (await have("jvc", "p31"), await have("silicon", "p31")), (2.5, 0.5))
    await invariants("после переездов")
    # ── списания: руками (только после решения) и сканом кода ───────────────
    wid = await db.writeoff_add({"district": "jvc", "item": "p1", "name": "Absolut", "qty": 1, "kind": "бой", "at": T(160), "day": D, "state": "pending", "by": "Худоба"})
    eq("списание ждёт решения — склад не тронут", await have("jvc", "p1"), 9)
    await db.writeoff_decide(wid, True, 1, "STAR")
    eq("списание согласовано: 8", await have("jvc", "p1"), 8)
    wid2 = await db.writeoff_add({"district": "jvc", "item": "p1", "name": "Absolut", "qty": 1, "kind": "бой", "at": T(161), "day": D, "state": "ok", "by": "STAR", "code": "a1"})
    await db.qr_write_off("a1", wid2)
    eq("списание сканом: 7, код ушёл из реестра", (await have("jvc", "p1"), (await db.qr_by_product_district("jvc")).get("p1")), (7, 3))
    await invariants("после списаний")
    # ── ревизия JVC: числится всё, недостача только по кодовым ─────────────
    lines, _ = await SR._audit_lines("jvc", D); r1 = {l["id"]: l for l in lines}
    eq("ревизия: Absolut числится 7, кодовых 3 (a2, c0, c1), без кодов 4", (r1["p1"]["expected"], r1["p1"]["coded"], r1["p1"]["noqr"]), (7, 3, 4))
    eq("ревизия: Heineken числится 2,5, кодовых 1,5, без кодов 1", (r1["p31"]["expected"], r1["p31"]["coded"], r1["p31"]["noqr"]), (2.5, 1.5, 1))
    for code, pid, q in (("a2", "p1", 1), ("c0", "p1", 1), ("box1", "p31", 1), ("half1", "p31", 0.5)):
        await db.audit_scan_add("jvc", D, code, {"at": T(170), "by": 1, "product_id": pid, "verdict": "ok", "qty": q})
    lines, _ = await SR._audit_lines("jvc", D); r1 = {l["id"]: l for l in lines}
    eq("камера увидела 2 из 3 кодовых Absolut → недостача 1; пиво сошлось", (r1["p1"]["actual"], r1["p1"]["diff"], r1["p31"]["actual"], r1["p31"]["diff"]), (2, 1, 1.5, 0))
    snap = SR._audit_snapshot_lines(lines); s1 = {l["id"]: l for l in snap}
    eq("снимок по завершении: Absolut 6 (2 увидели + 4 без кодов), Heineken 2,5", (s1["p1"]["actual"], s1["p31"]["actual"]), (6, 2.5))
    await db.save_stock_count("jvc", D, {"district": "jvc", "day": D, "counted_at": iso(datetime.now(timezone.utc) + timedelta(seconds=1)), "first_time": False, "counted_by": 1, "lines": snap})
    eq("после ревизии склад JVC живёт от снимка: 6 и 2,5", (await have("jvc", "p1"), await have("jvc", "p31")), (6, 2.5))
    await invariants("после ревизии")
    # ── заявка: норма − остаток, пиво вверх до коробки ──────────────────────
    o = await SR.order_rows(D); rows = {r["id"]: r for r in o["all_rows"]}
    eq("заявка JVC: Absolut 10 − 6 = 4; Heineken 2 − 2,5 → 0; Силикон Absolut 5 − 10 → 0", (rows["p1"]["cells"]["jvc"]["need"], rows["p31"]["cells"]["jvc"]["need"], rows["p1"]["cells"]["silicon"]["need"]), (4, 0, 0))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL[:6]}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
