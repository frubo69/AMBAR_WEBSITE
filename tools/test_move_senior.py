"""Старший берёт перемещение на себя — по району, откуда везут (владелец,
18 сен 2026: «сделай возможность старшему взять заявку на перемещение так же на
себя на какие-то определённые районы»; «старший — тот, кто пользуется AMBAR
STAR»; «он видит всё, что должен с JVC взять и куда отвезти, как у
водителей»). mongomock + настоящие move_routes:
  • «Взять на себя» район — все его передачи во всех открытых заявках, ещё не
    отданные целиком; достаётся одному входу STAR, второй видит, кто взял;
  • пока взял старший, водителям этого района карточек «отдать» нет, «Начать»
    и скан им отвечают «взял старший», смену их это не держит; передачи других
    районов живут как жили;
  • старший сканирует по каждой передаче — куда везёт: бутылка с другого
    района или в чужую (не взятую им) передачу не уходит;
  • получатель принимает как обычно — «Принял» после того, как отдано всё;
  • «Снять с себя» — дальше отдают водители района, отсканированное остаётся."""
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
    for oid, per in {"jvc": {vodka: 0, gin: 0}, "bbay": {vodka: 4, gin: 0}, "alguses": {vodka: 0, gin: 1},
                     "tecom": {vodka: 0, gin: 0}}.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": 1, "actual": q, "counted": True}
                      for p, q in per.items()]})
    for i in range(4): await code(d, f"v{i}", vodka, "bbay")
    await code(d, "g0", gin, "alguses")
    r = await MV.create([{"from": "bbay", "to": "jvc", "id": vodka, "qty": 2},
                         {"from": "alguses", "to": "jvc", "id": gin, "qty": 1},
                         {"from": "bbay", "to": "tecom", "id": vodka, "qty": 1}], by="STAR")
    mid = r["move_id"]

    print("── взять на себя район ────────────────────────────────────────────")
    a = await MV.senior_take("bbay", "STAR", 7)
    eq("старший взял всё с Бизнес Бея — две передачи", (a["ok"], a["took"]), (True, 2))
    b = await MV.senior_take("bbay", "STAR-2", 8)
    eq("второй вход STAR — «уже взял» и кто", (b["ok"], b["error"], b["senior"]), (False, "taken", "STAR"))
    eq("тот же вход — повторное нажатие не ломает", (await MV.senior_take("bbay", "STAR", 7))["ok"], True)
    eq("взять с района, откуда ничего не везут, — нечего", (await MV.senior_take("tecom", "STAR", 7))["error"], "nothing")

    print("── водители ───────────────────────────────────────────────────────")
    eq("у Бизнес Бея карточек «отдать» нет", (await MV.tasks_for_driver("Бахадыр", "bbay"))["give"], [])
    eq("у Алгусеса его передача на месте", [x["to_code"] for x in (await MV.tasks_for_driver("Даврон", "alguses"))["give"]], ["B1"])
    eq("«Начать» водителю Бизнес Бея — «взял старший»",
       (await MV.give_start(mid, "jvc", "Бахадыр", 11, "bbay")).get("error"), "senior_took")
    s = await MV.scan(mid, "jvc", "v0", "Бахадыр", 11, "bbay")
    eq("скан водителя — «взял старший», склад стоит", (s["verdict"], s.get("senior"), await shelf("jvc", vodka)),
       ("senior_took", "STAR", 0))
    eq("смену Бизнес Бея это не держит", await MV.pending_for_district("bbay"), [])
    eq("а Алгусес держит его собственная передача", [x["side"] for x in await MV.pending_for_district("alguses")], ["give"])
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("получатель JVC видит, кто везёт: из Бизнес Бея — старший",
       sorted((x["from_code"], x["senior"]) for x in v["take"]), [("B2", "STAR"), ("B4", "")])

    print("── старший сканирует по каждой передаче ───────────────────────────")
    x = await MV.scan(mid, "jvc", "v0", "STAR-2", 8, "bbay", senior=True)
    eq("чужую передачу не сканирует", (x["verdict"], x.get("senior")), ("senior_other", "STAR"))
    x = await MV.scan(mid, "jvc", "g0", "STAR", 7, "alguses", senior=True)
    eq("не взятую передачу Алгусеса — «сначала возьмите»", x["verdict"], "not_taken")
    x = await MV.scan(mid, "jvc", "g0", "STAR", 7, "bbay", senior=True)
    eq("бутылка Алгусеса в передачу Бизнес Бея — не с того района", (x["verdict"], x.get("from_code")), ("other_district", "B4"))
    x = await MV.scan(mid, "jvc", "v0", "STAR", 7, "bbay", senior=True)
    eq("Бизнес Бей → JVC: ушла", (x["ok"], x["from_code"], x["to_code"], x["task"]["giver"]), (True, "B2", "B1", "STAR"))
    eq("та же бутылка второй раз — «уже отдали»", (await MV.scan(mid, "jvc", "v0", "STAR", 7, "bbay", senior=True))["verdict"], "given")
    x = await MV.scan(mid, "jvc", "v1", "STAR", 7, "bbay", senior=True)
    eq("в JVC отдано всё", (x["ok"], x["finished"]), (True, True))
    x = await MV.scan(mid, "tecom", "v2", "STAR", 7, "bbay", senior=True)
    eq("Бизнес Бей → Тиком: тем же старшим", (x["ok"], x["to_code"], x["finished"]), (True, "B5", True))
    eq("склад: JVC +2, Тиком +1, Бизнес Бей −3",
       (await shelf("jvc", vodka), await shelf("tecom", vodka), await shelf("bbay", vodka)), (2, 1, 1))
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("у получателя: из Бизнес Бея отдано — «Принял», Алгусес ждёт",
       sorted((t["from_code"], t["status"]) for t in v["take"]), [("B2", "given"), ("B4", "wait")])

    print("── снять с себя ───────────────────────────────────────────────────")
    r2 = await MV.create([{"from": "bbay", "to": "silicon", "id": vodka, "qty": 1}], by="STAR")
    eq("новая заявка с Бизнес Бея: старший берёт снова", (await MV.senior_take("bbay", "STAR", 7))["took"], 1)
    eq("чужой не снимет", (await MV.senior_drop("bbay", "STAR-2"))["ok"], False)
    dr = await MV.senior_drop("bbay", "STAR")
    eq("снял с себя", (dr["ok"], dr["dropped"]), (True, 1))
    g = await MV.tasks_for_driver("Бахадыр", "bbay")
    eq("передача вернулась водителям Бизнес Бея", [(x["to_code"], x["senior"]) for x in g["give"]], [("B3", "")])
    eq("старший её больше не сканирует",
       (await MV.scan(r2["move_id"], "silicon", "v3", "STAR", 7, "bbay", senior=True))["verdict"], "not_taken")

    print("── принимает получатель, как всегда ───────────────────────────────")
    await MV.accept(mid, "jvc", "bbay", "Худоба", 5, "jvc")
    await MV.accept(mid, "tecom", "bbay", "Алишер", 6, "tecom")
    eq("Тиком принял — его задача закрыта", bool((await db.move_order_get(mid))["tasks"]["tecom"]["done_at"]), True)
    eq("JVC держит передача Алгусеса", bool((await db.move_order_get(mid))["tasks"]["jvc"].get("done_at")), False)
    tr = await d.stock_transfers.find({"by_kind": "move"}).to_list(length=10)
    eq("в книге переездов — отдавал старший", sorted((x["by_name"], x["from"], x["to"]) for x in tr),
       [("STAR", "bbay", "jvc"), ("STAR", "bbay", "jvc"), ("STAR", "bbay", "tecom")])

    print("── старший начал, вернул на середине — доделали водители ──────────")
    # Владелец, 18 сен 2026: «сможет старший взять на себя JVC, отсканировать и
    # переместить товар на определённые районы, а потом полувыполненную заявку
    # вернуть, чтобы кто-то другой её закончил из водителей?»
    for i in range(6): await code(d, f"j{i}", gin, "jvc")
    await db.save_stock_count("jvc", D, {"district": "jvc", "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": gin, "name": gin, "price": 100, "unit": 1, "actual": 6, "counted": True}]})
    r3 = await MV.create([{"from": "jvc", "to": "bbay", "id": gin, "qty": 2},
                          {"from": "jvc", "to": "tecom", "id": gin, "qty": 2},
                          {"from": "jvc", "to": "silicon", "id": gin, "qty": 2}], by="STAR")
    m3 = r3["move_id"]
    eq("старший взял JVC — три передачи", (await MV.senior_take("jvc", "STAR", 7))["took"], 3)
    await MV.scan(m3, "bbay", "j0", "STAR", 7, "jvc", senior=True)
    x = await MV.scan(m3, "bbay", "j1", "STAR", 7, "jvc", senior=True)
    eq("в Бизнес Бей отдал всё", x["finished"], True)
    x = await MV.scan(m3, "tecom", "j2", "STAR", 7, "jvc", senior=True)
    eq("в Тиком — одну из двух", (x["ok"], x["task"]["got"], x["finished"]), (True, 1, False))
    dr = await MV.senior_drop("jvc", "STAR")
    eq("вернул водителям: Тиком (начатая) и Силикон (не начатая)", (dr["ok"], dr["dropped"]), (True, 2))
    g = await MV.tasks_for_driver("Худоба", "jvc")
    eq("у водителя JVC: Тиком — пауза, отдано 1 из 2; Силикон — ждёт",
       sorted((x["to_code"], x["status"], x["got"], x["need"], x["giver"], x["senior"]) for x in g["give"]),
       [("B3", "wait", 0, 2, "", ""), ("B5", "pause", 1, 2, "", "")])
    v = await MV.tasks_for_driver("Бахадыр", "bbay")
    eq("Бизнес Бей видит: старший отдал всё — можно «Принял»",
       [(t["from_code"], t["status"], t["giver"], t["senior"]) for t in v["take"]], [("B1", "given", "STAR", "STAR")])
    eq("Тиком видит: отдали 1 из 2, ждёт водителя JVC",
       [(t["from_code"], t["status"], t["got"]) for t in (await MV.tasks_for_driver("Алишер", "tecom"))["take"]
        if t["move_id"] == m3], [("B1", "pause", 1)])
    eq("старший больше не сканирует в вернутое", (await MV.scan(m3, "tecom", "j3", "STAR", 7, "jvc", senior=True))["verdict"],
       "not_taken")
    eq("JVC снова держит смену своими передачами этой заявки",
       sorted((x["side"], x["to_code"]) for x in await MV.pending_for_district("jvc") if x["move_id"] == m3),
       [("give", "B3"), ("give", "B5")])
    await MV.give_start(m3, "tecom", "Худоба", 21, "jvc")
    x = await MV.scan(m3, "tecom", "j3", "Худоба", 21, "jvc")
    eq("водитель JVC доотдал в Тиком", (x["ok"], x["task"]["got"], x["finished"], x["task"]["giver"]), (True, 2, True, "Худоба"))
    await MV.give_start(m3, "silicon", "Фарух", 22, "jvc")
    for c in ("j4", "j5"):
        x = await MV.scan(m3, "silicon", c, "Фарух", 22, "jvc")
    eq("другой водитель JVC отдал в Силикон", x["finished"], True)
    for to, who in (("bbay", "Бахадыр"), ("tecom", "Алишер"), ("silicon", "Азиз")):
        await MV.accept(m3, to, "jvc", who, 5, to)
    doc = await db.move_order_get(m3)
    eq("все приняли — заявка закрыта", doc["status"], "done")
    eq("склад: из JVC ушло 6, в каждый район пришло по 2",
       (await shelf("jvc", gin), await shelf("bbay", gin), await shelf("tecom", gin), await shelf("silicon", gin)),
       (0, 2, 2, 2))
    tr = await d.stock_transfers.find({"by_kind": "move", "from": "jvc"}).to_list(length=20)
    eq("в книге переездов видно, кто что отдал",
       sorted((x_["by_name"], x_["to"]) for x_ in tr),
       [("STAR", "bbay"), ("STAR", "bbay"), ("STAR", "tecom"), ("Фарух", "silicon"), ("Фарух", "silicon"),
        ("Худоба", "tecom")])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
