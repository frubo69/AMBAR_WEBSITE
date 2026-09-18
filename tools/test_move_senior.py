"""Старший берёт заявку на перемещение на себя (владелец, 18 сен 2026: «сделай
возможность старшему взять заявку на перемещение так же на себя на какие-то
определённые районы»; «старший — тот, кто пользуется AMBAR STAR»: имя — кем
подписан вход STAR, к конкретному человеку не привязано). mongomock +
настоящие move_routes:
  • «Взять на себя» — задача района-получателя достаётся одному старшему;
  • пока взял старший, водителям отдающих районов карточки нет, «Начать» и
    скан им отвечают «взял старший», смену их эта задача не держит;
  • старший сканирует с любого района, откуда везут в эту задачу, — и только
    оттуда; чужую задачу (не взятую им) не сканирует;
  • получатель принимает как обычно — «Принял» после того, как отдано всё;
  • «Снять с себя» — дальше отдают водители, отсканированное остаётся."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, move_routes as MV

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"; T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D

async def code(d, cid, pid, district, qty=1):
    await d.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                                 "product_name": SR._catalog()[pid]["name"], "district": district,
                                 "origin": district, "src": "cover", "qty": qty, "at": T0})

async def shelf(oid, pid):
    SR.base_drop()
    return float(((await SR._district_base(D))[oid].get("have_exact") or {}).get(pid) or 0)

async def main():
    db._db = AsyncMongoMockClient()["ambar_senior"]; d = db._db
    vodka, gin = "p1", "p55"
    for oid, per in {"jvc": {vodka: 0, gin: 0}, "bbay": {vodka: 2, gin: 0}, "alguses": {vodka: 0, gin: 1},
                     "tecom": {vodka: 1, gin: 0}}.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": 1, "actual": q, "counted": True}
                      for p, q in per.items()]})
    await code(d, "v0", vodka, "bbay"); await code(d, "v1", vodka, "bbay")
    await code(d, "g0", gin, "alguses"); await code(d, "vt", vodka, "tecom")
    r = await MV.create([{"from": "bbay", "to": "jvc", "id": vodka, "qty": 2},
                         {"from": "alguses", "to": "jvc", "id": gin, "qty": 1}], by="STAR")
    mid = r["move_id"]

    print("── взять на себя ──────────────────────────────────────────────────")
    a = await MV.senior_take(mid, "jvc", "STAR", 7)
    eq("старший взял задачу B1", (a["ok"], a["task"]["senior"]), (True, "STAR"))
    b = await MV.senior_take(mid, "jvc", "Другой старший", 8)
    eq("второй вход STAR — «уже взял» и кто", (b["ok"], b.get("senior")), (False, "STAR"))
    eq("тот же старший — повторное нажатие не ломает", (await MV.senior_take(mid, "jvc", "STAR", 7))["ok"], True)

    print("── водители отдающих районов ──────────────────────────────────────")
    eq("у Бизнес Бея карточки «отдать» нет", (await MV.tasks_for_driver("Бахадыр", "bbay"))["give"], [])
    eq("у Алгусеса тоже", (await MV.tasks_for_driver("Даврон", "alguses"))["give"], [])
    eq("«Начать» водителю — «взял старший»",
       (await MV.give_start(mid, "jvc", "Бахадыр", 11, "bbay")).get("error"), "senior_took")
    s = await MV.scan(mid, "jvc", "v0", "Бахадыр", 11, "bbay")
    eq("скан водителя — «взял старший», склад стоит", (s["verdict"], s.get("senior"), await shelf("jvc", vodka)),
       ("senior_took", "STAR", 0))
    eq("смену отдающих эта задача не держит",
       (await MV.pending_for_district("bbay"), await MV.pending_for_district("alguses")), ([], []))
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("получатель видит обе серые карточки", sorted((x["from_code"], x["status"], x["senior"]) for x in v["take"]),
       [("B2", "wait", "STAR"), ("B4", "wait", "STAR")])

    print("── старший сканирует ──────────────────────────────────────────────")
    x = await MV.scan(mid, "jvc", "v0", "Другой старший", 8, senior=True)
    eq("чужую задачу старший не сканирует", (x["verdict"], x.get("senior")), ("senior_other", "STAR"))
    x = await MV.scan(mid, "jvc", "vt", "STAR", 7, senior=True)
    eq("бутылка с района, откуда в задачу не везут, — отказ", (x["verdict"], x.get("from_code")), ("other_district", "B5"))
    x = await MV.scan(mid, "jvc", "v0", "STAR", 7, senior=True)
    eq("с Бизнес Бея — ушла на JVC", (x["ok"], x["from_code"], x["to_code"], x["task"]["giver"]), (True, "B2", "B1", "STAR"))
    x = await MV.scan(mid, "jvc", "v0", "STAR", 7, senior=True)
    eq("та же бутылка второй раз — «уже отдали»", x["verdict"], "given")
    x = await MV.scan(mid, "jvc", "g0", "STAR", 7, senior=True)
    eq("с Алгусеса — тоже, в одном проходе", (x["ok"], x["from_code"], x["finished"]), (True, "B4", True))
    eq("склад: JVC +2, отдающие −1 каждый",
       (await shelf("jvc", vodka), await shelf("jvc", gin), await shelf("bbay", vodka), await shelf("alguses", gin)),
       (1, 1, 1, 0))
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("у получателя: Алгусес отдан — «Принял», Бизнес Бей ещё отдают",
       sorted((t["from_code"], t["status"], t["giver"]) for t in v["take"]),
       [("B2", "live", "STAR"), ("B4", "given", "STAR")])

    print("── снять с себя ───────────────────────────────────────────────────")
    eq("чужой не снимет", (await MV.senior_drop(mid, "jvc", "Другой старший"))["ok"], False)
    dr = await MV.senior_drop(mid, "jvc", "STAR")
    eq("снял с себя", (dr["ok"], dr["task"]["senior"]), (True, ""))
    g = await MV.tasks_for_driver("Бахадыр", "bbay")
    eq("карточка вернулась Бизнес Бею, отсканированное осталось",
       [(x["to_code"], x["got"], x["need"]) for x in g["give"]], [("B1", 1, 2)])
    eq("старший больше не сканирует", (await MV.scan(mid, "jvc", "v1", "STAR", 7, senior=True))["verdict"], "not_taken")
    s = await MV.scan(mid, "jvc", "v1", "Бахадыр", 11, "bbay")
    eq("водитель Бизнес Бея доотдал", (s["ok"], s["finished"]), (True, True))

    print("── принимает получатель, как всегда ───────────────────────────────")
    for src in ("bbay", "alguses"):
        await MV.accept(mid, "jvc", src, "Худоба", 5, "jvc")
    doc = await db.move_order_get(mid)
    eq("приняли всё — задача и заявка закрыты", (bool(doc["tasks"]["jvc"]["done_at"]), doc["status"]), (True, "done"))
    eq("во взятую закрытую задачу старший не возьмёт", (await MV.senior_take(mid, "jvc", "STAR", 7))["error"], "gone")
    tr = await d.stock_transfers.find({"by_kind": "move"}).to_list(length=10)
    eq("в книге переездов — кто что отдал", sorted((x["by_name"], x["from"]) for x in tr),
       [("STAR", "alguses"), ("STAR", "bbay"), ("Бахадыр", "bbay")])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
