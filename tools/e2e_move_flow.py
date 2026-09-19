"""Сквозной прогон перемещений между районами — порядок с 18 сен 2026 (вечер):
сканирует тот, кто отдаёт, получатель принимает («Принял» / «Принял неровно»).

mongomock + настоящие move_routes, stock_routes, driver_routes. Проверяется не
«функция вернула словарь», а то, ради чего всё делалось: бутылка уехала ровно
одна, склад сошёлся, заявка закупки не попросила её купить и не забыла купить
взамен, смену не закрыли раньше времени, а двое водителей не отдали одно и то же.

Разделы:
    1. день целиком: расчёт → заявка → обе стороны видят → отдают → принимают
    2. гонки: двое отдающих на одну строку, один код дважды в одну секунду
    3. отказы сканера словами — склад стоит
    4. замок смены: держит обе стороны, тест-водителя не держит
    5. довозим до конца: «Принял», «Принял неровно», закрытие задачи и заявки
    6. сходимость: сеть не потеряла и не родила ни бутылки, заявка закупки сошлась
    7. заявка не пропадает со сменой суток
    8. старший снимает задачу на середине
    9. две заявки разом не путаются; одна передача — в два района
   10. экран смены водителя: шаг «Перемещение», кнопка «Закрыть» заперта
"""
import asyncio, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, move_routes as MV
from config_offices import OFFICE_IDS

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"
T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)   # раньше «сейчас» — переезд считается после пересчёта
SR._biz_day = lambda *a, **k: D
VODKA, BEER, WINE = "p1", "p31", "p98"


async def code(d, cid, pid, district, qty=1, status="active"):
    await d.qr_codes.insert_one({"_id": cid, "status": status, "product_id": pid,
                                 "product_name": SR._catalog()[pid]["name"], "district": district,
                                 "origin": district, "src": "cover", "qty": qty, "at": T0})


async def shelf(oid, pid):
    SR.base_drop()
    return float(((await SR._district_base(D))[oid].get("have_exact") or {}).get(pid) or 0)


async def total(pid):
    SR.base_drop()
    base = await SR._district_base(D)
    return sum(float((base[o].get("have_exact") or {}).get(pid) or 0) for o in OFFICE_IDS)


async def setup():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    # На полках: водка у Бизнес Бея, вино у Силикона, пиво у Алгусеса, JVC пуст.
    полки = {"jvc": {VODKA: 0, BEER: 0, WINE: 0}, "bbay": {VODKA: 6, BEER: 0, WINE: 0},
             "silicon": {VODKA: 0, BEER: 0, WINE: 4}, "alguses": {VODKA: 0, BEER: 2, WINE: 0},
             "tecom": {VODKA: 0, BEER: 0, WINE: 0}}
    for oid, per in полки.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]),
                       "actual": q, "counted": True} for p, q in per.items()]})
    for i in range(6): await code(d, f"v{i}", VODKA, "bbay")
    for i in range(4): await code(d, f"w{i}", WINE, "silicon")
    for i in range(4): await code(d, f"b{i}", BEER, "alguses", 0.5)
    # Нормы: JVC нужны 3 водки, 2 вина и коробка пива; у остальных — что лежит.
    for oid, per in (("jvc", {VODKA: 3, WINE: 2, BEER: 1}), ("bbay", {VODKA: 3, WINE: 0, BEER: 0}),
                     ("silicon", {VODKA: 0, WINE: 2, BEER: 0}), ("alguses", {VODKA: 0, WINE: 0, BEER: 1}),
                     ("tecom", {VODKA: 0, WINE: 0, BEER: 0})):
        for pid, v in per.items():
            await db.set_stock_norm(oid, pid, v, 0)
    return d


async def main():
    d = await setup()
    import driver_routes as DR
    print("── 1. день целиком ───────────────────────────────────────────────")
    plan = await MV.plan(D)
    eq("расчёт: три пары к JVC", sorted((r["from"], r["id"], r["qty"]) for r in plan["rows"]),
       sorted([("bbay", VODKA, 3), ("silicon", WINE, 2), ("alguses", BEER, 1)]))
    o = await SR.order_rows(D)
    до = {r["id"]: r["cells"]["jvc"]["calc"] for r in o["all_rows"] if r["cells"]["jvc"]["calc"]}
    eq("до перемещения заявка закупки просит всё купить", до, {VODKA: 3, WINE: 2, BEER: 1})
    r = await MV.create(plan["rows"], by="STAR")
    mid = r["move_id"]
    SR.base_drop()
    o = await SR.order_rows(D)
    после = {r2["id"]: r2["cells"]["jvc"]["calc"] for r2 in o["all_rows"] if r2["cells"]["jvc"]["calc"]}
    eq("создали перемещение — заявка закупки перестала просить эти бутылки", после, {})
    eq("и показывает, сколько в пути", o["moving_qty"], 6)
    eq("отдающие отдают излишек над нормой — взамен покупать нечего", o["leaving_need"], 0)
    eq("расчёт второй раз того же не предлагает", (await MV.plan(D))["rows"], [])

    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("JVC видит три серые карточки «забрать»",
       sorted((t["from_code"], t["status"]) for t in v["take"]), [("B2", "wait"), ("B3", "wait"), ("B4", "wait")])
    for oid, n in (("bbay", 3), ("silicon", 2), ("alguses", 1)):
        g = await MV.tasks_for_driver("Х", oid)
        eq(f"{oid}: карточка «отдать в JVC» на {n}", [(x["to_code"], x["need"], x["status"]) for x in g["give"]],
           [("B1", n, "wait")])

    print("\n── 2. гонки ──────────────────────────────────────────────────────")
    await MV.give_start(mid, "jvc", "Парвиз", 21, "bbay")
    await MV.scan(mid, "jvc", "v0", "Парвиз", 21, "bbay")
    await MV.scan(mid, "jvc", "v1", "Парвиз", 21, "bbay")
    # Осталась одна водка. Двое водителей Бизнес Бея сканируют две разные
    # бутылки в одну секунду — отдать можно только одну.
    s1, s2 = await asyncio.gather(MV.scan(mid, "jvc", "v2", "Парвиз", 21, "bbay"),
                                  MV.scan(mid, "jvc", "v3", "Баха", 22, "bbay"))
    eq("строку не перебрали: прошла одна из двух", sorted([s1["ok"], s2["ok"]]), [False, True])
    eq("вторая — «уже всё отдали»", (s1 if not s1["ok"] else s2)["verdict"], "full")
    eq("у JVC ровно три водки", await shelf("jvc", VODKA), 3)
    eq("у Бизнес Бея ровно три", await shelf("bbay", VODKA), 3)
    eq("в сети водки столько же", await total(VODKA), 6)
    # Один и тот же код дважды в одну секунду — одна передача.
    await MV.give_start(mid, "jvc", "Фаредун", 23, "silicon")
    a, b = await asyncio.gather(MV.scan(mid, "jvc", "w0", "Фаредун", 23, "silicon"),
                                MV.scan(mid, "jvc", "w0", "Фаредун", 23, "silicon"))
    eq("один код — одна передача", sorted([a["ok"], b["ok"]]), [False, True])
    eq("вино уехало одно", (await shelf("jvc", WINE), await shelf("silicon", WINE)), (1, 3))
    doc = await db.move_order_get(mid)
    got_w = next(l["got"] for l in doc["tasks"]["jvc"]["lines"] if l["id"] == WINE)
    eq("счёт строки вина — один, место за неудачный скан вернули", got_w, 1)

    print("\n── 3. отказы словами ─────────────────────────────────────────────")
    await code(d, "wrong", WINE, "tecom")
    eq("бутылка не с этого района", (await MV.scan(mid, "jvc", "wrong", "Фаредун", 23, "silicon"))["verdict"], "other_district")
    await code(d, "alien", "p12", "silicon")
    eq("позиции нет в передаче", (await MV.scan(mid, "jvc", "alien", "Фаредун", 23, "silicon"))["verdict"], "not_in_task")
    eq("кода нет в реестре", (await MV.scan(mid, "jvc", "нет-такого", "Фаредун", 23, "silicon"))["verdict"], "unknown")
    await code(d, "dead", WINE, "silicon", status="written")
    eq("списанную не отдать", (await MV.scan(mid, "jvc", "dead", "Фаредун", 23, "silicon"))["verdict"], "written")
    eq("получатель не сканирует", (await MV.scan(mid, "jvc", "w1", "Худоба", 11, "jvc"))["verdict"], "giver_scans")
    eq("чужой район в эту передачу не сканирует",
       (await MV.scan(mid, "jvc", "w1", "Азиз", 31, "tecom"))["verdict"], "not_giver")
    eq("после отказов склад не двинулся", (await shelf("jvc", WINE), await shelf("silicon", WINE)), (1, 3))

    print("\n── 4. замок смены ────────────────────────────────────────────────")
    me_j, me_s, me_t = {"name": "Худоба", "district": "jvc"}, {"name": "Фаредун", "district": "silicon"}, \
                       {"name": "Азиз", "district": "tecom"}
    eq("JVC держат три передачи", sorted((m["side"], m["code"], m["status"]) for m in await DR._moves_left(me_j)),
       [("take", "B2", "given"), ("take", "B3", "live"), ("take", "B4", "wait")])
    eq("Силикон держит его «отдать»", [(m["side"], m["code"]) for m in await DR._moves_left(me_s)], [("give", "B1")])
    eq("Бизнес Бей отдал всё — его не держит", await DR._moves_left({"name": "Парвиз", "district": "bbay"}), [])
    eq("у соседнего района замка нет", await DR._moves_left(me_t), [])
    eq("тест-водителю замок не ставим", await DR._moves_left({"name": "Т", "district": "jvc", "test": True}), [])

    print("\n── 5. довозим до конца ───────────────────────────────────────────")
    s = await MV.scan(mid, "jvc", "w1", "Фаредун", 23, "silicon")
    eq("вино отдано целиком", (s["finished"], s["task"]["status"]), (True, "given"))
    eq("Силикон больше не держит", await DR._moves_left(me_s), [])
    await MV.give_start(mid, "jvc", "Даврон", 24, "alguses")
    r1 = await MV.scan(mid, "jvc", "b0", "Даврон", 24, "alguses")
    eq("первый код пива — полкоробки", (r1["qty"], r1["line"]["got"], r1["finished"]), (0.5, 0.5, False))
    r2 = await MV.scan(mid, "jvc", "b1", "Даврон", 24, "alguses")
    eq("второй код добрал коробку", (r2["line"]["got"], r2["finished"]), (1, True))
    eq("пиво на месте", (await shelf("jvc", BEER), await shelf("alguses", BEER)), (1, 1))
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("у JVC все три карточки активные", sorted(t["status"] for t in v["take"]), ["given"] * 3)
    eq("задача ещё открыта — ничего не принято", (await db.move_order_get(mid))["tasks"]["jvc"].get("done_at"), None)
    for src in ("bbay", "silicon"):
        await MV.accept(mid, "jvc", src, "Худоба", 11, "jvc")
    eq("два приняли — замок держит третий",
       [(m["code"], m["status"]) for m in await DR._moves_left(me_j)], [("B4", "given")])
    last = await MV.accept(mid, "jvc", "alguses", "Фарух", 12, "jvc", ok=False,
                           lines=[{"id": BEER, "got": 0.5}], note="полкоробки мокрые")
    eq("третий — «Принял неровно», задача закрылась", (last["task"]["status"], last["task_done"]), ("diff", True))

    print("\n── 6. что стало после ────────────────────────────────────────────")
    eq("в сети ничего не появилось и не пропало",
       (await total(VODKA), await total(WINE), await total(BEER)), (6, 4, 2))
    eq("замок смены снят", await DR._moves_left(me_j), [])
    eq("заявка закрылась", (await db.move_order_get(mid))["status"], "done")
    SR.base_drop()
    o = await SR.order_rows(D)
    eq("заявка закупки ничего не просит: всё привезли от соседей",
       [r["id"] for r in o["all_rows"] if r["cells"]["jvc"]["calc"]], [])
    eq("и «в пути» обнулилось", (o["moving_qty"], o["leaving_qty"]), (0, 0))
    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("у водителей чисто", (v["take"], v["give"]), ([], []))
    l = await MV.live(D)
    t = l["tasks"][0]
    eq("у старшего: принято, есть расхождение, кто что сделал",
       (t["status"], t["diff"], [(s_["code"], s_["status"], s_["accepted_by"]) for s_ in t["sources"]]),
       ("done", True, [("B2", "done", "Худоба"), ("B3", "done", "Худоба"), ("B4", "diff", "Фарух")]))
    tr = await d.stock_transfers.find({"by_kind": "move"}).to_list(length=100)
    свои = {"bbay": {"Парвиз", "Баха"}, "silicon": {"Фаредун"}, "alguses": {"Даврон"}}
    eq("в книге переездов — семь сканов, каждый записан на отдающего своего района",
       (len(tr), all(x["to"] == "jvc" and x["by_name"] in свои.get(x["from"], ()) for x in tr)), (7, True))

    print("\n── 7. сутки сменились ────────────────────────────────────────────")
    r7 = await MV.create([{"from": "bbay", "to": "tecom", "id": VODKA, "qty": 1}], by="STAR")
    l7 = await MV.live("2026-09-19")
    живые = [t["move_id"] for t in l7["tasks"] if t["status"] not in ("done", "cancelled")]
    eq("вчерашняя открытая заявка видна и назавтра", r7["move_id"] in живые, True)
    eq("и обе стороны её видят",
       (len((await MV.tasks_for_driver("Алишер", "tecom"))["take"]),
        len((await MV.tasks_for_driver("Парвиз", "bbay"))["give"])), (1, 1))

    print("\n── 8. старший снимает на середине ────────────────────────────────")
    r8 = await MV.create([{"from": "bbay", "to": "tecom", "id": VODKA, "qty": 2}], by="STAR")
    await MV.give_start(r8["move_id"], "tecom", "Парвиз", 21, "bbay")
    s8 = await MV.scan(r8["move_id"], "tecom", "v4", "Парвиз", 21, "bbay")
    eq("одна отдана", (s8["ok"], await shelf("tecom", VODKA)), (True, 1))
    await db.move_order_cancel(r8["move_id"], "tecom", datetime.now(timezone.utc))
    eq("сняли — отданная осталась у получателя", await shelf("tecom", VODKA), 1)
    eq("снятую не досканировать", (await MV.scan(r8["move_id"], "tecom", "v5", "Парвиз", 21, "bbay"))["verdict"], "gone")
    eq("и не принять", (await MV.accept(r8["move_id"], "tecom", "bbay", "Алишер", 41, "tecom"))["error"], "gone")
    await db.move_order_cancel(r7["move_id"], "", datetime.now(timezone.utc))
    eq("снятые — ни у кого в списках",
       ((await MV.tasks_for_driver("Алишер", "tecom"))["take"], (await MV.tasks_for_driver("Парвиз", "bbay"))["give"]),
       ([], []))
    eq("и смену не держат", (await DR._moves_left({"name": "Алишер", "district": "tecom"}),
                             await DR._moves_left({"name": "Парвиз", "district": "bbay"})), ([], []))
    l8 = await MV.live(D)
    eq("снятая целиком заявка не висит у старшего живой",
       [t["status"] for t in l8["tasks"] if t["move_id"] == r7["move_id"]], ["cancelled"])

    print("\n── 9. две заявки и одна передача в два района ────────────────────")
    ra = await MV.create([{"from": "silicon", "to": "alguses", "id": WINE, "qty": 1},
                          {"from": "silicon", "to": "tecom", "id": WINE, "qty": 1}], by="Умар · оператор")
    rb = await MV.create([{"from": "bbay", "to": "tecom", "id": VODKA, "qty": 1}], by="STAR")
    g = await MV.tasks_for_driver("Азиз", "silicon")
    eq("Силикону — две карточки: в Алгусес и в Тиком", sorted(x["to_code"] for x in g["give"]), ["B4", "B5"])
    eq("Тикому — две серые: из Силикона и из Бизнес Бея",
       sorted(x["from_code"] for x in (await MV.tasks_for_driver("Алишер", "tecom"))["take"]), ["B2", "B3"])
    # Одно и то же вино уходит в два района: какой бутылке куда — решает карточка.
    await MV.give_start(ra["move_id"], "alguses", "Азиз", 31, "silicon")
    x1 = await MV.scan(ra["move_id"], "alguses", "w2", "Азиз", 31, "silicon")
    x2 = await MV.scan(ra["move_id"], "alguses", "w3", "Азиз", 31, "silicon")
    eq("в Алгусес ушла одна, вторая — «уже всё»", (x1["ok"], x2["verdict"]), (True, "full"))
    x3 = await MV.scan(ra["move_id"], "tecom", "w3", "Азиз", 31, "silicon")
    eq("вторая бутылка ушла по своей карточке — в Тиком", (x3["ok"], x3["to_code"]), (True, "B5"))
    eq("в чужую заявку не отсканируешь", (await MV.scan(rb["move_id"], "tecom", "w1", "Азиз", 31, "silicon"))["verdict"],
       "not_giver")

    print("\n── 10. экран смены ───────────────────────────────────────────────")
    today = SR._biz_day()
    await db.save_driver_day(today, "Алишер", {"working": True, "shift_open_at": datetime.now(timezone.utc)})
    DR._biz_day = lambda *a, **k: today

    async def _geo(me): return {"ok": True}
    DR._geo_for = _geo
    view = await DR._shift_view({"name": "Алишер", "district": "tecom"})
    eq("у водителя Тикома в смене — шаг «перемещение»",
       sorted((m["side"], m["code"], m["status"]) for m in view["moves"]),
       [("take", "B2", "wait"), ("take", "B3", "given")])
    eq("«Закрыть смену» заперта", view["can_close"], False)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
