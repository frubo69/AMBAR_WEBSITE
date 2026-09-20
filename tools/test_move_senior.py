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
  • «Снять с себя» — дальше отдают водители района, отсканированное остаётся;
  • по районам, куда везут (владелец, 18 сен 2026: «старший взял себе на
    перемещение два района — остальные остаются видны для водителей и свободны
    для принятия»): «Взять на себя · в B2» и «Вернуть» — у каждой передачи
    свои; и случай с боя: старший взял JVC целиком старым приложением,
    отдал в B2 и B4, вернул B3 и B5 — отданное остаётся за ним и принимается,
    остальное доотдают водители, товар доезжает весь."""
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

async def recv_all(mid, to, src, who, tgid):
    """Получатель сканирует всё, что ему отдали по паре src → to (с 19 сен
    2026 «Принял» — только так): последний скан принимает сам."""
    doc = await db.move_order_get(mid)
    r = None
    for c in (((doc["tasks"][to].get("give") or {}).get(src) or {}).get("codes") or []):
        r = await MV.receive(mid, to, src, c, who, tgid, to)
    return r

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
    eq("отданное старшим получатель сканирует так же — коды старшего в передаче",
       (await recv_all(mid, "jvc", "bbay", "Худоба", 5))["task"]["status"], "done")
    await recv_all(mid, "tecom", "bbay", "Алишер", 6)
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
        await recv_all(m3, to, "jvc", who, 5)
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

    print("── по районам, куда везут: взял два — остальные у водителей ───────")
    vod2 = "p2"   # свой товар — прошлые сценарии его не двигали
    for i in range(8): await code(d, f"k{i}", vod2, "jvc")
    await db.save_stock_count("jvc", D, {"district": "jvc", "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": vod2, "name": vod2, "price": 100, "unit": 1, "actual": 8, "counted": True}]})
    base = {o: await shelf(o, vod2) for o in ("jvc", "bbay", "tecom", "silicon", "alguses")}
    r4 = await MV.create([{"from": "jvc", "to": to, "id": vod2, "qty": 2} for to in ("bbay", "alguses", "silicon", "tecom")],
                         by="STAR")
    m4 = r4["move_id"]
    a = await MV.senior_take("jvc", "STAR", 7, to="bbay", mid=m4)
    eq("«Взять на себя · в B2» — одна передача", (a["ok"], a["took"]), (True, 1))
    a = await MV.senior_take("jvc", "STAR", 7, to="alguses", mid=m4)
    eq("и «в B4» — вторая", (a["ok"], a["took"]), (True, 1))
    g = await MV.tasks_for_driver("Худоба", "jvc")
    eq("водители JVC видят остальные два района и могут отдавать",
       sorted((x["to_code"], x["senior"]) for x in g["give"] if x["move_id"] == m4), [("B3", ""), ("B5", "")])
    eq("смену JVC держат только эти два",
       sorted(x["to_code"] for x in await MV.pending_for_district("jvc") if x["move_id"] == m4), ["B3", "B5"])
    b = await MV.senior_take("jvc", "STAR-2", 8, to="bbay", mid=m4)
    eq("второй вход STAR: B2 уже взят — «уже взял» и кто", (b["ok"], b["error"], b["senior"]), (False, "taken", "STAR"))
    b = await MV.senior_take("jvc", "STAR-2", 8, to="silicon", mid=m4)
    eq("а свободный B3 второй вход STAR взять может", (b["ok"], b["took"]), (True, 1))
    eq("теперь водителям JVC остаётся B5",
       [x["to_code"] for x in (await MV.tasks_for_driver("Худоба", "jvc"))["give"] if x["move_id"] == m4], ["B5"])
    eq("STAR-2 вернул B3 — снова у водителей", (await MV.senior_drop("jvc", "STAR-2", to="silicon", mid=m4))["dropped"], 1)
    eq("вернуть не своё — нечего", (await MV.senior_drop("jvc", "STAR-2", to="bbay", mid=m4))["ok"], False)
    eq("взять в район, куда из JVC не везут, — нечего",
       (await MV.senior_take("jvc", "STAR", 7, to="jvc", mid=m4))["error"], "nothing")
    # Водитель JVC начал отдавать в Тиком, старший забирает эту передачу у него.
    await MV.give_start(m4, "tecom", "Фарух", 22, "jvc")
    x = await MV.scan(m4, "tecom", "k0", "Фарух", 22, "jvc")
    eq("водитель JVC отдал в Тиком одну", (x["ok"], x["task"]["got"]), (True, 1))
    eq("старший забрал у него Тиком посреди", (await MV.senior_take("jvc", "STAR", 7, to="tecom", mid=m4))["took"], 1)
    eq("следующий скан водителя — «взял старший», склад стоит",
       ((await MV.scan(m4, "tecom", "k1", "Фарух", 22, "jvc"))["verdict"], await shelf("tecom", vod2) - base["tecom"]),
       ("senior_took", 1))
    eq("B3 у водителей — Фарух отдаёт туда, как обычно", (await MV.give_start(m4, "silicon", "Фарух", 22, "jvc"))["ok"], True)
    for c_, to in (("k1", "tecom"), ("k2", "bbay"), ("k3", "bbay"), ("k4", "alguses"), ("k5", "alguses")):
        x = await MV.scan(m4, to, c_, "STAR", 7, "jvc", senior=True)
        eq(f"старший: {c_} → {to}", x["ok"], True)
    for c_ in ("k6", "k7"):
        x = await MV.scan(m4, "silicon", c_, "Фарух", 22, "jvc")
    eq("водитель JVC отдал в B3 всё", x["finished"], True)
    for to, who in (("bbay", "Бахадыр"), ("alguses", "Даврон"), ("silicon", "Азиз"), ("tecom", "Алишер")):
        eq(f"«Принял» в {to} — отсканировал всё", (await recv_all(m4, to, "jvc", who, 5))["task"]["status"], "done")
    eq("заявка закрыта", (await db.move_order_get(m4))["status"], "done")
    eq("склад: из JVC ушло 8, в каждый район по 2",
       tuple([await shelf(o, vod2) - base[o] for o in ("jvc", "bbay", "alguses", "silicon", "tecom")]), (-8, 2, 2, 2, 2))

    print("── случай с боя: взял JVC целиком, отдал в B2 и B4, вернул B3 и B5 ─")
    vod3 = "p3"
    # 18 сен 2026 вечером: старший нажал «Взять на себя» старым приложением
    # (весь JVC), отсканировал в Бизнес Бей и Алгусес и повёз. Владелец: «не
    # сломай этим нововведением их перемещение, товар у них должен в итоге
    # переместиться успешно».
    for i in range(8): await code(d, f"q{i}", vod3, "jvc")
    await db.save_stock_count("jvc", D, {"district": "jvc", "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": vod3, "name": vod3, "price": 100, "unit": 1, "actual": 8, "counted": True}]})
    base = {o: await shelf(o, vod3) for o in ("jvc", "bbay", "tecom", "silicon", "alguses")}
    r5 = await MV.create([{"from": "jvc", "to": to, "id": vod3, "qty": 2} for to in ("bbay", "alguses", "silicon", "tecom")],
                         by="STAR")
    m5 = r5["move_id"]
    eq("старое приложение: весь JVC — четыре передачи", (await MV.senior_take("jvc", "STAR", 7))["took"], 4)
    for c_, to in (("q0", "bbay"), ("q1", "bbay"), ("q2", "alguses"), ("q3", "alguses")):
        await MV.scan(m5, to, c_, "STAR", 7, "jvc", senior=True)
    eq("вернуть отданный B2 нельзя — он ждёт «Принял»",
       (await MV.senior_drop("jvc", "STAR", to="bbay", mid=m5))["ok"], False)
    for to in ("silicon", "tecom"):
        eq(f"вернул {to} водителям", (await MV.senior_drop("jvc", "STAR", to=to, mid=m5))["dropped"], 1)
    doc = await db.move_order_get(m5)
    eq("B2 и B4 — отданы старшим, за ним и остались",
       [(to, MV.give_view(m5, doc, to, doc["tasks"][to], "jvc")["status"],
         MV.give_view(m5, doc, to, doc["tasks"][to], "jvc")["senior"]) for to in ("bbay", "alguses")],
       [("bbay", "given", "STAR"), ("alguses", "given", "STAR")])
    eq("водителям JVC — B3 и B5",
       sorted(x["to_code"] for x in (await MV.tasks_for_driver("Худоба", "jvc"))["give"] if x["move_id"] == m5), ["B3", "B5"])
    eq("Бизнес Бей видит «Принял»",
       [(t["status"], t["senior"]) for t in (await MV.tasks_for_driver("Бахадыр", "bbay"))["take"] if t["move_id"] == m5],
       [("given", "STAR")])
    eq("B2 принял — отсканировал обе", (await recv_all(m5, "bbay", "jvc", "Бахадыр", 5))["task"]["status"], "done")
    eq("B4 отсканировал одну из двух", (await MV.receive(m5, "alguses", "jvc", "q2", "Даврон", 5, "alguses"))["ok"], True)
    a4 = await MV.accept(m5, "alguses", "jvc", "Даврон", 5, "alguses", ok=False, note="одной нет")
    eq("B4 — «не всё пришло»: отдали 2, пришла 1",
       (a4["ok"], a4["task"]["accept_lines"][0]["sent"], a4["task"]["accept_lines"][0]["got"]), (True, 2, 1))
    for to, cs in (("silicon", ("q4", "q5")), ("tecom", ("q6", "q7"))):
        await MV.give_start(m5, to, "Худоба", 21, "jvc")
        for c_ in cs:
            x = await MV.scan(m5, to, c_, "Худоба", 21, "jvc")
        eq(f"водитель JVC отдал в {to}", x["finished"], True)
    for to, who in (("silicon", "Азиз"), ("tecom", "Алишер")):
        await recv_all(m5, to, "jvc", who, 5)
    doc = await db.move_order_get(m5)
    eq("заявка закрыта, B4 — «принято неровно»",
       (doc["status"], MV.task_view(m5, doc, "alguses", doc["tasks"]["alguses"], "")["diff"]), ("done", True))
    eq("склад: из JVC ушло 8, в каждый район по 2",
       tuple([await shelf(o, vod3) - base[o] for o in ("jvc", "bbay", "alguses", "silicon", "tecom")]), (-8, 2, 2, 2, 2))
    eq("смену никому не держит", [x for x in await MV.pending_for_district("jvc") if x["move_id"] == m5]
       + [x for o in ("bbay", "alguses", "silicon", "tecom") for x in await MV.pending_for_district(o) if x["move_id"] == m5], [])

    # Приём сканом из STAR (владелец, 20 сен 2026: «почему из АМБАР СТАР нельзя
    # отсканировать товар, который я принимаю? у нас же и принимающая, и
    # отдающая сторона сканирует»). Старший принимает за район-получатель: свой
    # район ему не нужен, правила те же, что у водителя.
    print("── старший принимает сканом из STAR ───────────────────────────")
    import json as _json
    from aiohttp.test_utils import make_mocked_request
    async def own(path, body, mid):
        r = make_mocked_request("POST", path, match_info={"mid": mid})
        r["owner_id"] = 1; r._read_bytes = _json.dumps(body).encode()
        h = MV.handle_own_receive if path.endswith("receive") else MV.handle_own_accept
        return _json.loads((await h(r)).text)

    vod9 = "p1"
    for i, c_ in enumerate(("s1", "s2")):
        await code(d, c_, vod9, "jvc")
    m9 = (await MV.create([{"from": "jvc", "to": "bbay", "id": vod9, "qty": 2}], by="STAR"))["move_id"]
    await MV.give_start(m9, "bbay", "Худоба", 21, "jvc")
    for c_ in ("s1", "s2"):
        await MV.scan(m9, "bbay", c_, "Худоба", 21, "jvc")
    r1 = await own(f"/api/owner/move/{m9}/receive", {"district": "bbay", "from": "jvc", "code": "s1", "as": "Старший"}, m9)
    eq("старший отсканировал первую: принято 1 из 2", (r1["ok"], r1["task"]["recv"], r1["finished"]), (True, 1, False))
    r2 = await own(f"/api/owner/move/{m9}/receive", {"district": "bbay", "from": "jvc", "code": "s2", "as": "Старший"}, m9)
    eq("вторая закрывает передачу саму", (r2["ok"], r2["finished"], r2["task"]["status"]), (True, True, "done"))
    r3 = await own(f"/api/owner/move/{m9}/receive", {"district": "bbay", "from": "jvc", "code": "s1", "as": "Старший"}, m9)
    # Эта заявка была из одной пары — с её приёмом закрылась и она сама,
    # поэтому повторный скан отвечает «заявка закрыта».
    eq("повтор после приёма — больше не принимаем", (r3["ok"], r3["verdict"]), (False, "gone"))

    # «Не всё пришло» из STAR: отдали две, старший отсканировал одну.
    for c_ in ("s3", "s4"):
        await code(d, c_, vod9, "jvc")
    m10 = (await MV.create([{"from": "jvc", "to": "alguses", "id": vod9, "qty": 2}], by="STAR"))["move_id"]
    await MV.give_start(m10, "alguses", "Худоба", 21, "jvc")
    for c_ in ("s3", "s4"):
        await MV.scan(m10, "alguses", c_, "Худоба", 21, "jvc")
    await own(f"/api/owner/move/{m10}/receive", {"district": "alguses", "from": "jvc", "code": "s3", "as": "Старший"}, m10)
    a = await own(f"/api/owner/move/{m10}/accept", {"district": "alguses", "from": "jvc", "ok": False, "as": "Старший"}, m10)
    eq("«не всё пришло» из STAR: отдали 2, пришла 1",
       (a["ok"], a["task"]["accept_lines"][0]["sent"], a["task"]["accept_lines"][0]["got"], a["task"]["status"]),
       (True, 2, 1, "diff"))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
