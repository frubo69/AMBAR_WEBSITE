"""Сквозной прогон перемещений между районами (владелец, 18 сен 2026: «проверь,
что нигде нет противоречий и багов, что все бутылки благополучно переместятся»).

mongomock + настоящие move_routes, stock_routes, driver_routes. Проверяется не
«функция вернула словарь», а то, ради чего всё делалось: бутылка уехала ровно
одна, склад сошёлся, заявка не попросила её купить, смену не закрыли раньше
времени, а двое водителей не увезли одно и то же.

Разделы:
    1. день целиком: план → заявка → водитель → сканы → закрытие
    2. гонки: двое берут, двое сканируют один код
    3. отказы сканера словами
    4. отпустил и передал другому; старший снял задачу
    5. замок смены
    6. сходимость склада и заявки на каждом шаге
    7. пиво полукоробками
    8. тест-водитель и чужие районы
"""
import asyncio, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, move_routes as MV
from config_offices import OFFICE_IDS, OFFICE_CODES

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
def ok_(name, cond, note=""):
    eq(name, bool(cond), True) if not note else eq(name, (bool(cond), note), (True, note))

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
    print("── 1. день целиком ───────────────────────────────────────────────")
    plan = await MV.plan(D)
    eq("план: три строки к JVC", sorted((r["from"], r["id"], r["qty"]) for r in plan["rows"]),
       sorted([("bbay", VODKA, 3), ("silicon", WINE, 2), ("alguses", BEER, 1)]))
    o = await SR.order_rows(D)
    до = {r["id"]: r["cells"]["jvc"]["calc"] for r in o["all_rows"] if r["cells"]["jvc"]["calc"]}
    eq("до заявки на перемещение заявка просит всё купить", до, {VODKA: 3, WINE: 2, BEER: 1})

    r = await MV.create(plan["rows"], by="STAR")
    mid = r["move_id"]
    SR.base_drop()
    o = await SR.order_rows(D)
    после = {r2["id"]: r2["cells"]["jvc"]["calc"] for r2 in o["all_rows"] if r2["cells"]["jvc"]["calc"]}
    eq("создали перемещение — заявка перестала просить эти бутылки", после, {})
    eq("и показывает, сколько в пути", o["moving_qty"], 6)

    v = await MV.tasks_for_driver("Худоба", "jvc")
    eq("водитель JVC видит одну свободную задачу", (len(v["free"]), v["free"][0]["need"]), (1, 6))
    g = await MV.tasks_for_driver("Парвиз", "bbay")
    eq("у Бизнес Бея карточка «заберут»", (len(g["give"]), g["give"][0]["need"]), (1, 3))

    print("\n── 2. гонки ──────────────────────────────────────────────────────")
    a, b = await asyncio.gather(MV.claim(mid, "jvc", "Худоба", 11), MV.claim(mid, "jvc", "Фарух", 12))
    eq("взял ровно один", sorted([a["ok"], b["ok"]]), [False, True])
    кто = a["task"]["driver"] if a["ok"] else b["task"]["driver"]
    eq("второму сказали, кто взял", (a.get("driver") or b.get("driver")), кто)
    s1, s2 = await asyncio.gather(MV.scan(mid, "jvc", "v0", кто, 11), MV.scan(mid, "jvc", "v0", кто, 11))
    eq("один код — один переезд", sorted([s1["ok"], s2["ok"]]), [False, True])
    eq("бутылка уехала один раз", await shelf("jvc", VODKA), 1)
    eq("у отдающего стало на одну меньше", await shelf("bbay", VODKA), 5)
    eq("в сети водки столько же", await total(VODKA), 6)
    eq("счёт строки пошёл на один", (await MV.tasks_for_driver(кто, "jvc"))["mine"][0]["got"], 1)

    print("\n── 3. отказы словами ─────────────────────────────────────────────")
    await code(d, "wrong", VODKA, "tecom")
    eq("бутылка не из того района", (await MV.scan(mid, "jvc", "wrong", кто, 11))["verdict"], "other_district")
    await code(d, "alien", "p12", "bbay")
    eq("позиции нет в заявке", (await MV.scan(mid, "jvc", "alien", кто, 11))["verdict"], "not_in_task")
    eq("кода нет в реестре", (await MV.scan(mid, "jvc", "нет-такого", кто, 11))["verdict"], "unknown")
    await code(d, "dead", VODKA, "bbay", status="writeoff")
    eq("списанную не возьмём", (await MV.scan(mid, "jvc", "dead", кто, 11))["verdict"], "writeoff")
    другой = "Фарух" if кто == "Худоба" else "Худоба"
    eq("чужую задачу не сканируют", (await MV.scan(mid, "jvc", "v1", другой, 12))["verdict"], "not_mine")
    eq("после отказов склад не двинулся", (await shelf("jvc", VODKA), await shelf("bbay", VODKA)), (1, 5))

    print("\n── 4. отпустил и передал ─────────────────────────────────────────")
    await MV.release(mid, "jvc", кто)
    v = await MV.tasks_for_driver(другой, "jvc")
    eq("задача снова свободна, увезённое осталось", (len(v["free"]), v["free"][0]["got"]), (1, 1))
    await MV.claim(mid, "jvc", другой, 12)
    eq("подобрал другой", (await MV.tasks_for_driver(другой, "jvc"))["mine"][0]["driver"], другой)
    eq("первый её больше не видит своей", len((await MV.tasks_for_driver(кто, "jvc"))["mine"]), 0)

    print("\n── 5. замок смены ────────────────────────────────────────────────")
    import driver_routes as DR
    me = {"name": другой, "district": "jvc"}
    eq("смену не закрыть: перемещение живо", len(await DR._moves_left(me)), 1)
    eq("у соседнего района замка нет", len(await DR._moves_left({"name": "Азиз", "district": "tecom"})), 0)
    eq("тест-водителю замок не ставим", await DR._moves_left({"name": "Т", "district": "jvc", "test": True}), [])

    print("\n── 6. довозим до конца ───────────────────────────────────────────")
    for c in ("v1", "v2"):
        await MV.scan(mid, "jvc", c, другой, 12)
    eq("водка добрана", (await shelf("jvc", VODKA), await shelf("bbay", VODKA)), (3, 3))
    eq("лишний скан по добранной позиции", (await MV.scan(mid, "jvc", "v3", другой, 12))["verdict"], "full")
    for c in ("w0", "w1"):
        await MV.scan(mid, "jvc", c, другой, 12)
    eq("вино переехало", (await shelf("jvc", WINE), await shelf("silicon", WINE)), (2, 2))
    r1 = await MV.scan(mid, "jvc", "b0", другой, 12)
    eq("первый код пива — полкоробки", (r1["qty"], r1["line"]["got"]), (0.5, 0.5))
    eq("задача ещё не закрыта", bool(r1.get("finished")), False)
    r2 = await MV.scan(mid, "jvc", "b1", другой, 12)
    eq("второй код добрал коробку и закрыл задачу", (r2["line"]["got"], bool(r2["finished"])), (1, True))
    eq("пиво на месте", (await shelf("jvc", BEER), await shelf("alguses", BEER)), (1, 1))

    print("\n── 7. что стало после ────────────────────────────────────────────")
    eq("в сети ничего не появилось и не пропало",
       (await total(VODKA), await total(WINE), await total(BEER)), (6, 4, 2))
    eq("замок смены снят", await DR._moves_left(me), [])
    eq("заявка закрылась", (await db.move_order_get(mid))["status"], "done")
    SR.base_drop()
    o = await SR.order_rows(D)
    eq("заявка закупки ничего не просит: всё привезли от соседей",
       [r["id"] for r in o["all_rows"] if r["cells"]["jvc"]["calc"]], [])
    eq("и «в пути» обнулилось", o["moving_qty"], 0)
    eq("в закрытую заявку не досканировать", (await MV.scan(mid, "jvc", "v3", другой, 12))["verdict"], "gone")
    v = await MV.tasks_for_driver(другой, "jvc")
    eq("у водителя чисто", (len(v["mine"]), len(v["free"]), len(v["give"])), (0, 0, 0))
    l = await MV.live(D)
    eq("у старшего задача помечена выполненной", [t["status"] for t in l["tasks"]], ["done"])

    print("\n── 8. отмена задачи старшим на середине ──────────────────────────")
    # Тикому нужна водка, а Бизнес Бею столько больше не надо — появился излишек.
    await db.set_stock_norm("tecom", VODKA, 2, 0)
    await db.set_stock_norm("bbay", VODKA, 1, 0)
    plan2 = await MV.plan(D)
    eq("план нашёл, откуда везти", [(r["from"], r["to"], r["qty"]) for r in plan2["rows"]],
       [("bbay", "tecom", 2)])
    r2_ = await MV.create(plan2["rows"], by="STAR")
    mid2 = r2_["move_id"]
    await MV.claim(mid2, "tecom", "Алишер", 21)
    await MV.scan(mid2, "tecom", "v3", "Алишер", 21)
    eq("одна уехала в Тиком", await shelf("tecom", VODKA), 1)
    await db.move_order_cancel(mid2, "tecom", datetime.now(timezone.utc))
    SR.base_drop()
    o = await SR.order_rows(D)
    eq("сняли задачу — увезённое осталось на месте", await shelf("tecom", VODKA), 1)
    eq("а недовезённое вернулось в заявку закупки", o["all_rows"] and
       next(r["cells"]["tecom"]["calc"] for r in o["all_rows"] if r["id"] == VODKA), 1)
    eq("снятую задачу водитель не видит", len((await MV.tasks_for_driver("Алишер", "tecom"))["mine"]), 0)
    eq("и смену она не держит", await DR._moves_left({"name": "Алишер", "district": "tecom"}), [])

    # Снятая целиком заявка не висит у старшего свободной.
    p8 = await MV.plan(D)
    if p8["rows"]:
        r8 = await MV.create(p8["rows"], by="STAR")
        await db.move_order_cancel(r8["move_id"], "", datetime.now(timezone.utc))
        l8 = await MV.live(D)
        eq("снятая заявка не показывает живых задач",
           [t["status"] for t in l8["tasks"] if t["move_id"] == r8["move_id"] and t["status"] != "cancelled"], [])
        свободных = 0
        for o in OFFICE_IDS:
            свободных += len((await MV.tasks_for_driver("Х", o))["free"])
        eq("и у водителя её нет", свободных, 0)

    print("\n── 9. товар без кодов не планируем ───────────────────────────────")
    # У Тикома на полке есть, но в реестре нет — сканировать нечего.
    await db.save_stock_count("tecom", D, {"district": "tecom", "day": D, "counted_at": T0.isoformat(),
        "first_time": True, "counted_by": 0,
        "lines": [{"id": WINE, "name": WINE, "price": 100, "unit": 1, "actual": 5, "counted": True}]})
    await db.set_stock_norm("tecom", WINE, 1, 0)
    await db.set_stock_norm("alguses", WINE, 3, 0)
    eq("на полке Тикома вино есть", await shelf("tecom", WINE), 5)
    p9 = await MV.plan(D)
    eq("но в план оно не попало — кодов нет",
       [r for r in p9["rows"] if r["from"] == "tecom" and r["id"] == WINE], [])
    await code(d, "tw0", WINE, "tecom")
    p9 = await MV.plan(D)
    eq("внесли один код — ровно одну бутылку и планируем",
       [(r["from"], r["to"], r["qty"]) for r in p9["rows"] if r["id"] == WINE and r["from"] == "tecom"],
       [("tecom", "alguses", 1)])

    print("\n── 10. две заявки разом не путаются ──────────────────────────────")
    # Вино Силикону больше не нужно, Тикому нужно — вторая пара для второй заявки.
    # Тикому вино теперь ровно по полке — излишка нет, и в плане остаётся один
    # источник вина (Силикон). Иначе из прошлого раздела влезала бы третья пара.
    await db.set_stock_norm("tecom", WINE, 5, 0)
    await db.set_stock_norm("silicon", WINE, 0, 0)
    await db.set_stock_norm("alguses", WINE, 3, 0)
    await db.set_stock_norm("tecom", VODKA, 3, 0)
    p3 = await MV.plan(D)
    eq("в плане обе пары", sorted({(r["from"], r["id"]) for r in p3["rows"]}),
       sorted([("bbay", VODKA), ("silicon", WINE)]))
    r3 = await MV.create([x for x in p3["rows"] if x["id"] == WINE], by="STAR")
    r4 = await MV.create([x for x in p3["rows"] if x["id"] == VODKA], by="STAR")
    eq("номера разные", r3["move_id"] != r4["move_id"], True)
    eq("вино едет в Алгусес, водка в Тиком",
       (sorted({r["to"] for r in p3["rows"] if r["id"] == WINE}),
        sorted({r["to"] for r in p3["rows"] if r["id"] == VODKA})), (["alguses"], ["tecom"]))
    eq("у Алгусеса своя задача, у Тикома своя",
       (len((await MV.tasks_for_driver("Сунат", "alguses"))["free"]),
        len((await MV.tasks_for_driver("Алишер", "tecom"))["free"])), (1, 1))
    await MV.claim(r3["move_id"], "alguses", "Сунат", 31)
    eq("взял свою — у соседа его задача не пропала",
       (len((await MV.tasks_for_driver("Сунат", "alguses"))["mine"]),
        len((await MV.tasks_for_driver("Алишер", "tecom"))["free"])), (1, 1))
    eq("в чужую заявку не отсканируешь",
       (await MV.scan(r4["move_id"], "alguses", "w2", "Сунат", 31))["verdict"], "gone")

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
