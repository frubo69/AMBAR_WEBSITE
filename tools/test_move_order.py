"""Заявка на перемещение между районами — порядок с 18 сен 2026 (вечер).
mongomock + настоящие move_routes и stock_routes.move_by_code.

Владелец: «водитель Алгусеса сканирует товар, который надо переместить с его
района, и отдаёт водителю, который приехал с Тикома»; «заявка отобразится у
двух сторон; у отдающей она активная — „Начать перемещение“, потом
сканировать; после того как всё отсканировал водитель отдающей стороны, у
принимающего заявка из серой становится активной — „Принял“ / „Принял
неровно“».

  • строки собираются в задачи по району-получателю, пары «откуда × позиция»
    складываются; строка — целым числом кодов;
  • карточку видят обе стороны: отдающий — «отдать», получатель — «забрать»
    (серая, пока не отдали всё);
  • сканирует только отдающий и только своё: получатель, чужой район, чужая
    бутылка, лишний и повторный скан отвечают словами, склад не двигается;
  • скан сразу переносит бутылку на получателя; пиво — полкоробки на код;
  • отдали всё — у получателя «Принял» / «Принял неровно»; раньше — нельзя;
  • задача закрывается, когда приняли от всех, заявка — следом;
  • смену держат обе стороны: отдающий — пока не отдал, получатель — пока не принял;
  • заявка закупки считает полку так, будто перемещения уже сделаны: у
    получателя приход, у отдающего расход."""
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

# Пересчёт датируем началом суток: переезд считается тем, что случился ПОСЛЕ
# него, а «сейчас» у машины, на которой гоняют тест, всегда позже полуночи.
D = "2026-09-18"; T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D

async def code(d, cid, pid, district, qty=1, status="active"):
    await d.qr_codes.insert_one({"_id": cid, "status": status, "product_id": pid,
                                 "product_name": SR._catalog()[pid]["name"], "district": district,
                                 "origin": district, "src": "cover", "qty": qty, "at": T0})

async def shelf(oid, pid):
    SR.base_drop()
    return float(((await SR._district_base(D))[oid].get("have_exact") or {}).get(pid) or 0)

async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    vodka, beer = "p1", "p31"                      # Absolut (бутылка), Heineken (коробка)
    # На полках: у Бизнес Бея четыре бутылки, у Силикона коробка пива, у JVC пусто.
    shelf_ = {"jvc": {vodka: 0, beer: 0}, "bbay": {vodka: 4, beer: 0}, "silicon": {vodka: 0, beer: 1}}
    for oid, per in shelf_.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]),
                       "actual": q, "counted": True} for p, q in per.items()]})
    for i in range(4): await code(d, f"v{i}", vodka, "bbay")
    for i in range(2): await code(d, f"b{i}", beer, "silicon", 0.5)
    await code(d, "vj", vodka, "jvc")              # у JVC своя бутылка — её никто не отдаёт
    await code(d, "vt", vodka, "tecom")            # у Тикома своя

    print("── сборка ─────────────────────────────────────────────────────────")
    r = await MV.create([{"from": "bbay", "to": "jvc", "id": vodka, "qty": 2},
                         {"from": "bbay", "to": "jvc", "id": vodka, "qty": 1},   # та же пара
                         {"from": "silicon", "to": "jvc", "id": beer, "qty": 1},
                         {"from": "jvc", "to": "jvc", "id": vodka, "qty": 5},    # сам себе — мимо
                         {"from": "bbay", "to": "tecom", "id": vodka, "qty": 0.5}])  # полбутылки → бутылка
    mid = r["move_id"]
    doc = await db.move_order_get(mid)
    task = doc["tasks"]["jvc"]
    eq("задачи — по районам-получателям", sorted(doc["tasks"]), ["jvc", "tecom"])
    eq("одинаковые пары сложились в одну строку", len(task["lines"]), 2)
    eq("сам себе — не строка", len(r["skipped"]), 1)
    eq("в строке столько, сколько просили", sorted(l["qty"] for l in task["lines"]), [1, 3])
    eq("полбутылки не бывает — строка целым кодом", doc["tasks"]["tecom"]["lines"][0]["qty"], 1)

    print("── видят обе стороны ──────────────────────────────────────────────")
    g = await MV.tasks_for_driver("Худоба", "bbay")
    eq("Бизнес Бею — отдать в JVC и в Тиком",
       sorted((x["to_code"], x["need"], x["status"]) for x in g["give"]), [("B1", 3, "wait"), ("B5", 1, "wait")])
    eq("отдающему — только его строки", [len(x["lines"]) for x in g["give"] if x["district"] == "jvc"], [1])
    v = await MV.tasks_for_driver("Алишер", "jvc")
    eq("JVC — забрать из Бизнес Бея и из Силикона, обе серые",
       sorted((x["from_code"], x["status"]) for x in v["take"]), [("B2", "wait"), ("B3", "wait")])
    eq("старому приложению — пусто (кнопок старого порядка не будет)",
       (v["mine"], v["free"], v["taken"]), ([], [], []))
    eq("постороннему району не видно ничего", await MV.tasks_for_driver("Азиз", "alguses"),
       {"give": [], "take": [], "mine": [], "free": [], "taken": []})

    print("── кто сканирует ──────────────────────────────────────────────────")
    eq("получатель не сканирует", (await MV.scan(mid, "jvc", "v0", "Алишер", 11, "jvc"))["verdict"], "giver_scans")
    eq("посторонний район — не отдающий", (await MV.scan(mid, "jvc", "v0", "Азиз", 13, "alguses"))["verdict"], "not_giver")
    st = await MV.give_start(mid, "jvc", "Худоба", 12, "bbay")
    eq("«Начать перемещение»: отдающий виден", (st["ok"], st["task"]["giver"], st["task"]["status"]), (True, "Худоба", "live"))
    eq("получатель видит, кто отдаёт",
       [(x["giver"], x["status"]) for x in (await MV.tasks_for_driver("Алишер", "jvc"))["take"] if x["from"] == "bbay"],
       [("Худоба", "live")])
    eq("начать за получателя нельзя", (await MV.give_start(mid, "jvc", "Алишер", 11, "jvc"))["error"], "not_giver")
    eq("своя бутылка JVC — не из Бизнес Бея",
       (await MV.scan(mid, "jvc", "vj", "Худоба", 12, "bbay"))["verdict"], "other_district")
    eq("бутылка Тикома — тоже", (await MV.scan(mid, "jvc", "vt", "Худоба", 12, "bbay"))["verdict"], "other_district")
    eq("кода нет в реестре", (await MV.scan(mid, "jvc", "нет-такого", "Худоба", 12, "bbay"))["verdict"], "unknown")
    await code(d, "dead", vodka, "bbay", status="written")
    eq("списанную не отдать", (await MV.scan(mid, "jvc", "dead", "Худоба", 12, "bbay"))["verdict"], "written")
    await code(d, "gin", "p55", "bbay")
    eq("позиции нет в заявке", (await MV.scan(mid, "jvc", "gin", "Худоба", 12, "bbay"))["verdict"], "not_in_task")

    print("── скан: бутылка переходит сразу ──────────────────────────────────")
    s1 = await MV.scan(mid, "jvc", "v0", "Худоба", 12, "bbay")
    eq("скан: счёт строки пошёл", (s1["ok"], s1["line"]["got"], s1["line"]["qty"], s1["task"]["got"]), (True, 1, 3, 1))
    eq("склад сразу видит: JVC +1, Бизнес Бей −1", (await shelf("jvc", vodka), await shelf("bbay", vodka)), (1, 3))
    eq("повторный скан той же бутылки — уже отдали", (await MV.scan(mid, "jvc", "v0", "Худоба", 12, "bbay"))["verdict"], "given")
    eq("и склад не двинулся второй раз", (await shelf("jvc", vodka), await shelf("bbay", vodka)), (1, 3))
    eq("принять раньше, чем отдали всё, нельзя",
       (await MV.accept(mid, "jvc", "bbay", "Алишер", 11, "jvc"))["error"], "not_given")
    # Продолжить может другой водитель Бизнес Бея — не замок.
    s2 = await MV.scan(mid, "jvc", "v1", "Парвиз", 14, "bbay")
    eq("продолжил другой водитель района", (s2["ok"], s2["task"]["giver"]), (True, "Парвиз"))
    s3 = await MV.scan(mid, "jvc", "v2", "Парвиз", 14, "bbay")
    eq("передача Бизнес Бея закрыта", (s3["finished"], s3["task"]["status"]), (True, "given"))
    eq("лишний скан по добранной строке", (await MV.scan(mid, "jvc", "v3", "Парвиз", 14, "bbay"))["verdict"], "full")
    g = await MV.tasks_for_driver("Худоба", "bbay")
    eq("у отдающего карточка ушла, осталась только в Тиком", [x["to_code"] for x in g["give"]], ["B5"])
    v = await MV.tasks_for_driver("Алишер", "jvc")
    eq("у получателя — активная (отдали всё) и серая",
       sorted((x["from_code"], x["status"]) for x in v["take"]), [("B2", "given"), ("B3", "wait")])

    print("── замок смены — обе стороны ──────────────────────────────────────")
    pj = await MV.pending_for_district("jvc")
    eq("JVC держат две передачи — принять и дождаться", sorted((x["side"], x["from_code"], x["status"]) for x in pj),
       [("take", "B2", "given"), ("take", "B3", "wait")])
    pb = await MV.pending_for_district("bbay")
    eq("Бизнес Бей держит только то, что он ещё не отдал (Тиком)", [(x["side"], x["to_code"]) for x in pb], [("give", "B5")])
    ps = await MV.pending_for_district("silicon")
    eq("Силикон держит его передача", [(x["side"], x["status"]) for x in ps], [("give", "wait")])
    eq("у постороннего замка нет", await MV.pending_for_district("alguses"), [])

    print("── «Принял» ───────────────────────────────────────────────────────")
    a1 = await MV.accept(mid, "jvc", "bbay", "Алишер", 11, "jvc")
    eq("«Принял»", (a1["ok"], a1["task"]["status"], a1["task"]["accepted_by"]), (True, "done", "Алишер"))
    eq("принять за другой район нельзя",
       (await MV.accept(mid, "jvc", "silicon", "Худоба", 12, "bbay"))["error"], "not_your_district")
    eq("второй раз — уже принято, ничего не перезаписано",
       (await MV.accept(mid, "jvc", "bbay", "Файзуло", 15, "jvc", ok=False, note="x"))["already"], True)
    eq("задача ещё открыта — Силикон не отдал", (await db.move_order_get(mid))["tasks"]["jvc"].get("done_at"), None)

    print("── пиво полкоробками и «Принял неровно» ───────────────────────────")
    await MV.give_start(mid, "jvc", "Азиз", 16, "silicon")
    b0 = await MV.scan(mid, "jvc", "b0", "Азиз", 16, "silicon")
    eq("первый код пива — полкоробки", (b0["qty"], b0["line"]["got"], b0["finished"]), (0.5, 0.5, False))
    b1 = await MV.scan(mid, "jvc", "b1", "Азиз", 16, "silicon")
    eq("второй код добрал коробку — передача отдана", (b1["line"]["got"], b1["finished"]), (1, True))
    eq("«неровно» без поправки и без слова — нечего сказать",
       (await MV.accept(mid, "jvc", "silicon", "Алишер", 11, "jvc", ok=False, lines=[{"id": beer, "got": 1}]))["error"],
       "diff_empty")
    a2 = await MV.accept(mid, "jvc", "silicon", "Алишер", 11, "jvc", ok=False,
                         lines=[{"id": beer, "got": 0.5}], note="одна упаковка порвана")
    eq("«Принял неровно»: расхождение записано",
       (a2["task"]["status"], a2["task"]["accept_lines"][0]["sent"], a2["task"]["accept_lines"][0]["got"],
        a2["task"]["accept_note"], a2["task_done"]), ("diff", 1, 0.5, "одна упаковка порвана", True))
    eq("расхождение склад не двигает: коды уже у JVC", (await shelf("jvc", beer), await shelf("silicon", beer)), (1, 0))
    doc = await db.move_order_get(mid)
    eq("задача JVC закрыта; заявка ждёт Тиком", (bool(doc["tasks"]["jvc"]["done_at"]), doc["status"]), (True, "open"))
    lv = await MV.live(D)
    t = next(x for x in lv["tasks"] if x["district"] == "jvc")
    eq("у старшего: принято, есть расхождение, кто отдавал",
       (t["status"], t["diff"], sorted(t["givers"])), ("done", True, ["Азиз", "Парвиз"]))
    eq("замок JVC снят", await MV.pending_for_district("jvc"), [])
    eq("у водителя JVC чисто", (await MV.tasks_for_driver("Алишер", "jvc"))["take"], [])
    eq("в принятую передачу не досканировать", (await MV.scan(mid, "jvc", "v3", "Парвиз", 14, "bbay"))["verdict"], "gone")

    print("── заявка закупки: полка как после перемещений ────────────────────")
    await db.set_stock_norm("jvc", vodka, 5, 0)
    await db.set_stock_norm("bbay", vodka, 1, 0)
    await db.set_stock_norm("tecom", vodka, 1, 0)
    SR.base_drop()
    o = await SR.order_rows(D)
    row = next(r for r in o["all_rows"] if r["id"] == vodka)
    # Полка — из пересчёта, а не из кодов: у JVC 3 (привезли), у Тикома 0.
    # JVC: норма 5, есть 3, ничего не едет → 2.
    # Бизнес Бей: норма 1, есть 1, но одна уходит в Тиком → просит 1 взамен.
    # Тиком: норма 1, есть 0, одна едет → 0.
    eq("JVC просит норму минус привезённое", (row["cells"]["jvc"]["have"], row["cells"]["jvc"]["calc"]), (3, 2))
    eq("отдающий ниже нормы просит взамен того, что уходит",
       (row["cells"]["bbay"]["have"], row["cells"]["bbay"]["leaving"], row["cells"]["bbay"]["calc"]), (1, 1, 1))
    eq("получатель не просит того, что к нему едет",
       (row["cells"]["tecom"]["moving"], row["cells"]["tecom"]["calc"]), (1, 0))
    eq("в итогах: едет 1, из-за отдачи просим 1", (o["moving_qty"], o["leaving_qty"], o["leaving_need"]), (1, 1, 1))
    await db.move_order_cancel(mid, "tecom", T0)
    SR.base_drop()
    o = await SR.order_rows(D)
    row = next(r for r in o["all_rows"] if r["id"] == vodka)
    eq("сняли — отдающий больше не просит, получатель просит снова",
       (row["cells"]["bbay"]["calc"], row["cells"]["tecom"]["calc"]), (0, 1))
    eq("снятая последняя задача закрыла заявку", (await db.move_order_get(mid))["status"], "done")

    print("── расчёт не предлагает второй раз то, что уже едет ───────────────")
    # У JVC 3 при норме 2 — излишек одной; Тикому (0 при норме 1) не хватает одной.
    for oid, n in (("jvc", 2), ("bbay", 1), ("tecom", 1), ("silicon", 0), ("alguses", 0)):
        await db.set_stock_norm(oid, vodka, n, 0)
    SR.base_drop()
    p1 = await MV.plan(D)
    eq("план: JVC → Тиком одна", [(r_["from"], r_["to"], r_["qty"]) for r_ in p1["rows"] if r_["id"] == vodka],
       [("jvc", "tecom", 1)])
    r2 = await MV.create(p1["rows"])
    p2 = await MV.plan(D)
    eq("создали — расчёт этого второй раз не предлагает", [r_ for r_ in p2["rows"] if r_["id"] == vodka], [])
    b = await MV.board({"jvc"})
    pv = next(x for x in b["products"] if x["id"] == vodka)
    eq("у оператора видно, сколько из лежащего уже уходит", pv["out"], {"jvc": 1})
    await db.move_order_cancel(r2["move_id"], "", T0)
    eq("сняли заявку целиком — снова предлагает",
       [(r_["from"], r_["to"]) for r_ in (await MV.plan(D))["rows"] if r_["id"] == vodka], [("jvc", "tecom")])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
