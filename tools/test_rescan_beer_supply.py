"""Пересканирование пива закрытой приёмки (tools/rescan_beer_supply.py) на
mongomock с настоящими supply_routes.task_scan / tasks_for_driver /
intake_by_district и stock_routes._district_base: склад района не сдвигается
ни при открытии, ни по ходу сканирования, ни после; водка той же приёмки не
тронута; водитель видит задачу у себя; на последнем коде задача и заявка
закрываются сами; откат возвращает как было; незакрытую задачу не трогаем."""
import asyncio, os, sys, tempfile
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, qr_routes as QR, supply_routes as sr
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescan_beer_supply as M
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
D = "2026-09-17"; T0 = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
async def notify(*a, **k): return None
sr._notify_done = notify
SR._biz_day = lambda *a, **k: D
async def have(pid):
    SR.base_drop(); return (await SR._district_base(D))["bbay"]["have_exact"].get(pid, 0)
async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    await db.save_stock_count("bbay", "2026-09-14", {"district": "bbay", "day": "2026-09-14", "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0, "lines": [{"id": "p31", "name": "Heineken", "price": 100, "unit": 24, "actual": 10, "counted": True},
                                                       {"id": "p1", "name": "Absolut", "price": 100, "unit": 1, "actual": 5, "counted": True}]})
    t_in = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)
    await d.supplies.insert_one({"_id": "S1", "status": "done", "at": t_in, "day": D, "done_at": t_in + timedelta(minutes=20),
        "items": [{"id": "p31", "name": "Heineken 0.33 can", "qty": 2, "by_district": {"bbay": 2}, "got": {"bbay": 2}, "scanned": 2},
                  {"id": "p1", "name": "Absolut 1 ltr", "qty": 1, "by_district": {"bbay": 1}, "got": {"bbay": 1}, "scanned": 1}],
        "tasks": {"bbay": {"driver": "Парвиз", "driver_id": 7, "claimed_at": t_in, "started_at": t_in, "noscan_at": None,
                           "done_at": t_in + timedelta(minutes=20), "cancelled_at": None, "erev": 0, "scanned": 3, "qty": 3,
                           "positions": 2, "gaps": [], "note": "", "flags": []}}})
    for i, (code, pid, q, extra) in enumerate((("h1", "p31", 0.5, 0.5), ("h2", "p31", 0.5, 0.5), ("a1", "p1", 1, None))):
        doc = {"_id": code, "status": "active", "product_id": pid, "product_name": pid, "district": "bbay", "origin": "bbay",
               "src": "intake", "supply_id": "S1", "driver": "Парвиз", "qty": q, "at": t_in + timedelta(minutes=i + 1)}
        if extra: doc["intake_extra"] = extra
        await d.qr_codes.insert_one(doc)
    h0, a0 = await have("p31"), await have("p1")
    eq("до: склад Heineken 10 + 2 приёмки = 12, Absolut 5 + 1 = 6", (h0, a0), (12, 6))
    tmp = tempfile.mkdtemp(); quiet = lambda s: None
    res = await M.run(db, SR, QR, "S1", "bbay", apply=False, backup_dir=tmp, say=quiet)
    eq("пробный прогон: 2 кода пива, ничего не записано", (res["ok"], res["codes"], await d.qr_codes.count_documents({})), (True, 2, 3))
    res = await M.run(db, SR, QR, "S1", "bbay", apply=True, backup_dir=tmp, say=quiet)
    s = await db.supply_get("S1"); t = s["tasks"]["bbay"]
    eq("открыто: заявка open, задача без сканирования на Парвизе", (res["ok"], s["status"], t["done_at"], bool(t["noscan_at"]), t["driver"]), (True, "open", None, True, "Парвиз"))
    eq("коды пива удалены, водка осталась", sorted(c["_id"] for c in await d.qr_codes.find({}).to_list(10)), ["a1"])
    eq("склад тот же: Heineken 12, Absolut 6", (await have("p31"), await have("p1")), (12, 6))
    lst = await sr.tasks_for_driver("Парвиз", "bbay")
    mine = [(v["supply_id"], v["left"], [(l["id"], l["left"]) for l in v["lines"] if l["left"]]) for v in lst["mine"]]
    eq("водитель видит у себя: осталось 2 коробки Heineken", mine, [("S1", 2, [("p31", 2)])])
    ib = next(x for x in (await sr.intake_by_district())["districts"] if x["id"] == "bbay")
    eq("STAR «с незавершённого приёма»: Бизнес Бей осталось 2", (ib["left"], len(ib["tasks"])), (2, 1))
    live = lambda L: [(x["supply_id"], x["status"], x["driver"], [(l["id"], l["got"], l["need"]) for l in x["lines"]]) for x in L]
    eq("статус у старшего: ждёт, только пиво, 0 из 2", live(await sr.intake_live()), [("S1", "wait", "Парвиз", [("p31", 0, 2)])])
    steps = []
    for i in range(4):
        r = await sr.task_scan("S1", "bbay", "p31", f"n{i}", "Парвиз", 7, "", False)
        steps.append((r["ok"], r["got"], r["left"], r["finished"], await have("p31")))
        if i == 1:
            L = await sr.intake_live()
            eq("статус после двух сканов: сканирует сейчас, 1 из 2, последний скан есть",
               (live(L), bool(L[0]["last_at"])), ([("S1", "live", "Парвиз", [("p31", 1, 2)])], True))
    eq("4 кода по 0,5: склад всё время 12, на последнем задача закрылась сама", steps,
       [(True, 0.5, 1.5, False, 12), (True, 1, 1, False, 12), (True, 1.5, 0.5, False, 12), (True, 2, 0, True, 12)])
    s = await db.supply_get("S1")
    eq("заявка снова принята", (s["status"], bool(s["tasks"]["bbay"]["done_at"])), ("done", True))
    L = await sr.intake_live()
    eq("статус после последнего кода: завершено, 2 из 2, время закрытия есть",
       (live(L), bool(L[0]["done_at"])), ([("S1", "done", "Парвиз", [("p31", 2, 2)])], True))
    eq("после: склад Heineken 12, Absolut 6", (await have("p31"), await have("p1")), (12, 6))
    reg = (await db.qr_by_product_district_all())["bbay"]
    eq("в реестре Heineken 2 коробки (4 кода), Absolut 1", (reg.get("p31"), reg.get("p1")), (2, 1))
    res2 = await M.run(db, SR, QR, "S1", "bbay", apply=False, backup_dir=tmp, say=quiet)
    eq("повтор по закрытой задаче: план есть, но коды — уже новые (4)", (res2["ok"], res2["codes"]), (True, 4))
    await M.rollback(db, SR, res["backup"], say=quiet)
    s = await db.supply_get("S1")
    eq("откат: заявка done, принято 2, коды h1 h2 на месте, склад 12",
       (s["status"], s["items"][0]["got"]["bbay"], sorted(c["_id"] for c in await d.qr_codes.find({"_id": {"$in": ["h1", "h2"]}}).to_list(5)), await have("p31") >= 12),
       ("done", 2, ["h1", "h2"], True))
    await d.supplies.update_one({"_id": "S1"}, {"$set": {"status": "open", "tasks.bbay.done_at": None}})
    res3 = await M.run(db, SR, QR, "S1", "bbay", apply=True, backup_dir=tmp, say=quiet)
    eq("задача не закрыта — стоп без записи", (res3["ok"], bool(res3.get("stop"))), (False, True))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
