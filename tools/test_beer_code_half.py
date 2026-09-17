"""Код пива — полкоробки (владелец, 17 сен 2026: «на каждой коробке по 2 кода,
каждое сканирование пива это шаг +0.5»). mongomock + настоящие
qr_routes.handle_scan, supply_routes.task_scan, stock_routes._district_base и
перенос старых кодов tools/beer_codes_half.run:
  • qty решает сервер: пиво 0.5 при любом qty из приложения, бутылка 1;
  • приёмка пива: план 2 коробки = 4 кода, пятый — «полна»;
  • перенос: склад и приход по районам те же, реестр пива вдвое меньше, долг
    «QR код не внесён» растёт на вторые коды; повтор ничего не меняет; откат
    возвращает как было; переезд сканом по пиву — стоп без записи."""
import asyncio, json, os, sys, tempfile
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, qr_routes as QR, supply_routes as sr
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import beer_codes_half as M
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D = "2026-09-16"; T0 = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
def T(m): return T0 + timedelta(minutes=m)
async def notify(*a, **k): return None
sr._notify_done = notify
SR._biz_day = lambda *a, **k: D
class Req(dict):
    method = "POST"
    def __init__(self, body, **kw): super().__init__(**kw); self._b = body
    async def json(self): return self._b
async def owner_scan(code, pid, district, qty, mode="new"):
    r = await QR.handle_scan.__wrapped__(Req({"code": code, "product_id": pid, "district": district,
                                              "mode": mode, "qty": qty}, owner_id=1))
    return json.loads(r.text)
def supply(sid, oid, pid, n, status="open", done=False):
    return {"_id": sid, "status": status, "at": T(1), "day": D,
            "items": [{"id": pid, "name": pid, "qty": n, "by_district": {oid: n}, "got": {oid: n if done else 0}}],
            "tasks": {oid: {"driver": "Худоба", "driver_id": 1, "claimed_at": T(1), "started_at": T(2) if done else None,
                            "noscan_at": None, "done_at": T(30) if done else None, "cancelled_at": None,
                            "erev": 0, "scanned": n if done else 0, "qty": n, "positions": 1}}}
async def have(oid, pid):
    SR.base_drop(); return (await SR._district_base(D))[oid]["have_exact"].get(pid, 0)
async def debt(oid):
    u, _ = await QR.unscanned_by_district(None, {}); return u.get(oid, 0)
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    for oid in ("jvc", "bbay"):
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(), "first_time": True,
            "counted_by": 0, "lines": [{"id": pid, "name": pid, "price": 100, "unit": SR._unit(SR._catalog()[pid]),
                                        "actual": q, "counted": True} for pid, q in (("p1", 10), ("p31", 8))]})
    # ── правило на сервере ─────────────────────────────────────────────────
    eq("code_qty: пиво 0.5, водка 1", (SR.code_qty(SR._catalog()["p31"]), SR.code_qty(SR._catalog()["p1"])), (0.5, 1))
    r = await owner_scan("hb1", "p31", "jvc", "1", mode="cover")
    c = await d.qr_codes.find_one({"_id": "hb1"})
    eq("«Внести товар» пиво с qty 1 из приложения → код 0.5", (r.get("new"), r.get("qty"), c["qty"]), (True, 0.5, 0.5))
    r = await owner_scan("va1", "p1", "jvc", "0.5", mode="cover")
    eq("«Внести товар» водка с qty 0.5 из приложения → код 1", (r.get("qty"), (await d.qr_codes.find_one({"_id": "va1"}))["qty"]), (1, 1))
    await owner_scan("hn1", "p31", "jvc", None, mode="new"); await owner_scan("hn2", "p31", "jvc", None, mode="new")
    eq("новый товар пивом: два кода = +1 коробка на складе JVC (8 → 9)", await have("jvc", "p31"), 9)
    await d.supplies.insert_one(supply("S1", "jvc", "p31", 2))
    got = []
    for i in range(4):
        x = await sr.task_scan("S1", "jvc", "p31", f"in{i}", "Худоба", 1, "", False); got.append((x["got"], x["left"], x["qty"]))
    eq("приёмка пива: план 2 коробки — 4 кода по 0.5", got, [(0.5, 1.5, 0.5), (1, 1, 0.5), (1.5, 0.5, 0.5), (2, 0, 0.5)])
    x = await sr.task_scan("S1", "jvc", "p31", "in9", "Худоба", 1, "", False)
    eq("пятый код — строка полна", (x["ok"], x.get("verdict")), (False, "full"))
    eq("склад JVC после приёмки: 9 + 2 = 11", await have("jvc", "p31"), 11)
    u = await sr.task_undo("S1", "jvc", "in3", "Худоба", False)
    s = await db.supply_get("S1")
    eq("отмена одного скана снимает 0.5", (u.get("ok"), s["items"][0]["got"]["jvc"], await have("jvc", "p31")), (True, 1.5, 10.5))

    # ── перенос старых кодов (как на сервере 17 сен) ───────────────────────
    for i in range(4):
        await d.qr_codes.insert_one({"_id": f"cov{i}", "status": "active", "product_id": "p31", "product_name": "Heineken",
                                     "district": "bbay", "origin": "bbay", "src": "cover", "qty": 1, "at": T(40 + i)})
    await d.qr_codes.insert_one({"_id": "covdel", "status": "deleted", "product_id": "p31", "district": "bbay",
                                 "origin": "bbay", "src": "cover", "qty": 1, "at": T(45)})
    await d.supplies.insert_one(supply("S0", "bbay", "p31", 3, status="done", done=True))
    for i in range(3):
        await d.qr_codes.insert_one({"_id": f"old{i}", "status": "active", "product_id": "p31", "product_name": "Heineken",
                                     "district": "bbay", "origin": "bbay", "src": "intake", "supply_id": "S0",
                                     "qty": 1, "at": T(20 + i)})
    await d.qr_codes.insert_one({"_id": "vod", "status": "active", "product_id": "p1", "district": "bbay",
                                 "origin": "bbay", "src": "cover", "qty": 1, "at": T(50)})
    h0, d0 = await have("bbay", "p31"), await debt("bbay")
    reg0 = (await db.qr_by_product_district_all())["bbay"]["p31"]
    eq("до переноса: склад BBay 8 + 3 приёмки = 11, в реестре 7 коробок", (h0, reg0), (11, 7))
    tmp = tempfile.mkdtemp()
    res = await M.run(db, SR, QR, apply=False, backup_dir=tmp, say=lambda s: None)
    eq("пробный прогон: 8 кодов пива к переводу, ничего не записано",
       (res["ok"], res["rows"], (await d.qr_codes.find_one({"_id": "cov0"}))["qty"]), (True, 8, 1))
    res = await M.run(db, SR, QR, apply=True, backup_dir=tmp, say=lambda s: None)
    codes = {c["_id"]: c async for c in d.qr_codes.find({})}
    eq("перенос: все 8 кодов пива по 0.5", sorted({codes[k]["qty"] for k in ("cov0", "cov3", "covdel", "old0", "old2")}), [0.5])
    eq("у кодов приёмки intake_extra 0.5, у cover его нет", (codes["old1"].get("intake_extra"), "intake_extra" in codes["cov1"]), (0.5, False))
    eq("водка не тронута", codes["vod"]["qty"], 1)
    eq("склад BBay пива тот же — 11", await have("bbay", "p31"), 11)
    reg1 = (await db.qr_by_product_district_all())["bbay"]["p31"]
    eq("реестр пива BBay вдвое меньше: 3.5", reg1, 3.5)
    eq("долг «QR код не внесён» BBay вырос на 3.5 (вторые коды)", round(await debt("bbay") - d0, 2), 3.5)
    eq("склад JVC не задет (новые коды уже были 0.5)", await have("jvc", "p31"), 10.5)
    res2 = await M.run(db, SR, QR, apply=True, backup_dir=tmp, say=lambda s: None)
    eq("повторный запуск ничего не меняет", (res2["ok"], res2["rows"]), (True, 0))
    await M.rollback(db, res["backup"], say=lambda s: None)
    codes = {c["_id"]: c async for c in d.qr_codes.find({})}
    eq("откат: qty 1 и без intake_extra, склад 11",
       (codes["old1"]["qty"], "intake_extra" in codes["old1"], codes["cov2"]["qty"], await have("bbay", "p31")), (1, False, 1, 11))
    # ── перемещение сканом: количество и подписи словами склада ─────────────
    m1 = await SR.move_by_code("hn1", "silicon", 1, "STAR", "owner")
    m2 = await SR.move_by_code("hn2", "silicon", 1, "STAR", "owner")
    mv = await SR.move_by_code("va1", "silicon", 1, "STAR", "owner")
    eq("ответ переезда: пиво qty 0.5 unit 24, водка qty 1 unit 1",
       (m1["verdict"], m1["qty"], m1["unit"], mv["qty"], mv["unit"]), ("ok", 0.5, 24, 1, 1))
    g = {x["product_id"]: x for x in SR.group_transfers(await db.get_stock_transfers(D), 0)}
    eq("книга переездов: пиво 2 кода = 1 коробка (unit 24), водка 1 бутылка (unit 1)",
       (g["p31"]["bottles"], g["p31"]["qty"], g["p31"]["unit"], g["p1"]["bottles"], g["p1"]["qty"], g["p1"]["unit"]),
       (2, 1, 24, 1, 1, 1))
    await db.add_stock_transfer({"day": D, "from": "bbay", "to": "jvc", "product_id": "p31", "qty": 1,
                                 "src": "qr", "code": "cov0", "at": T(60).isoformat()})
    res3 = await M.run(db, SR, QR, apply=True, backup_dir=tmp, say=lambda s: None)
    eq("переезд пива сканом со старым qty — стоп, ничего не записано",
       (res3["ok"], bool(res3.get("stop")), (await d.qr_codes.find_one({"_id": "cov1"}))["qty"]), (False, True, 1))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
