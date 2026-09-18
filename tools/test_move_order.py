"""Заявка на перемещение между районами (владелец, 18 сен 2026). mongomock +
настоящие move_routes и stock_routes.move_by_code:
  • строки расчёта собираются в задачи по району-ПОЛУЧАТЕЛЮ;
  • взять может любой водитель района, но достаётся одному — второй получает
    отказ, даже если нажал сразу следом;
  • отпустить может сам водитель, задача снова свободна;
  • скан переносит бутылку и двигает счёт строки; чужой район, чужая позиция
    и лишний скан отвечают словами, а не ошибкой;
  • собрал всё — задача закрывается сама, заявка следом;
  • пока задача жива, район не закрывает смену."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
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

async def code(d, cid, pid, district, qty=1):
    await d.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                                 "product_name": SR._catalog()[pid]["name"], "district": district,
                                 "origin": district, "src": "cover", "qty": qty, "at": T0})

async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    vodka, beer = "p1", "p31"                      # Absolut (бутылка), Heineken (коробка)
    # На полках: у Бизнес Бея четыре бутылки, у Силикона коробка пива, у JVC пусто.
    shelf = {"jvc": {vodka: 0, beer: 0}, "bbay": {vodka: 4, beer: 0}, "silicon": {vodka: 0, beer: 1}}
    for oid, per in shelf.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]),
                       "actual": q, "counted": True} for p, q in per.items()]})
    for i in range(4): await code(d, f"v{i}", vodka, "bbay")
    for i in range(2): await code(d, f"b{i}", beer, "silicon", 0.5)
    await code(d, "vj", vodka, "jvc")              # своя бутылка, её везти не надо

    # ── сборка заявки ──────────────────────────────────────────────────────
    r = await MV.create([{"from": "bbay", "to": "jvc", "id": vodka, "qty": 2},
                         {"from": "bbay", "to": "jvc", "id": vodka, "qty": 1},   # та же пара
                         {"from": "silicon", "to": "jvc", "id": beer, "qty": 1},
                         {"from": "jvc", "to": "jvc", "id": vodka, "qty": 5}])   # сам себе — мимо
    mid = r["move_id"]
    doc = await db.move_order_get(mid)
    task = doc["tasks"]["jvc"]
    eq("одна задача — на районе-получателе", list(doc["tasks"]), ["jvc"])
    eq("одинаковые пары сложились в одну строку", len(task["lines"]), 2)
    eq("сам себе — не строка", len(r["skipped"]), 1)
    eq("в строке столько, сколько просили", sorted(l["qty"] for l in task["lines"]), [1, 3])

    # ── кто видит ──────────────────────────────────────────────────────────
    v = await MV.tasks_for_driver("Алишер", "jvc")
    eq("получателю — свободная задача", (len(v["free"]), len(v["mine"])), (1, 0))
    g = await MV.tasks_for_driver("Худоба", "bbay")
    eq("отдающему — что у него заберут", (len(g["give"]), g["give"][0]["need"] if g["give"] else 0), (1, 3))
    eq("отдающий видит только свои строки", len(g["give"][0]["lines"]), 1)
    eq("чужому району не видно ничего", await MV.tasks_for_driver("Фарух", "tecom"),
       {"mine": [], "free": [], "taken": [], "give": []})

    # ── захват: достаётся одному ───────────────────────────────────────────
    a = await MV.claim(mid, "jvc", "Алишер", 11)
    b = await MV.claim(mid, "jvc", "Авазбек", 12)
    eq("первый взял", (a["ok"], a["task"]["driver"]), (True, "Алишер"))
    eq("второй получил отказ и видит, кто взял", (b["ok"], b.get("driver")), (False, "Алишер"))
    v = await MV.tasks_for_driver("Авазбек", "jvc")
    eq("у второго задача ушла из свободных", (len(v["free"]), len(v["taken"])), (0, 1))
    await MV.release(mid, "jvc", "Алишер")
    v = await MV.tasks_for_driver("Авазбек", "jvc")
    eq("отпустил — снова свободна", len(v["free"]), 1)
    await MV.claim(mid, "jvc", "Авазбек", 12)

    # ── сканирование ───────────────────────────────────────────────────────
    r = await MV.scan(mid, "jvc", "vj", "Авазбек", 12)
    eq("своя бутылка не из заявки", r["verdict"], "other_district")
    r = await MV.scan(mid, "jvc", "v0", "Алишер", 11)
    eq("чужую задачу не сканируют", r["verdict"], "not_mine")
    r = await MV.scan(mid, "jvc", "v0", "Авазбек", 12)
    eq("скан: бутылка переехала, счёт строки пошёл",
       (r["ok"], r["line"]["got"], r["line"]["qty"], r["task"]["got"]), (True, 1, 3, 1))
    eq("счётчик показывает позицию словами", r["line"]["name"], SR._catalog()[vodka]["name"])
    SR.base_drop()
    base = await SR._district_base(D)
    eq("склад сразу видит переезд: JVC +1, Бизнес Бей −1",
       (base["jvc"]["have_exact"][vodka], base["bbay"]["have_exact"][vodka]), (1, 3))
    await MV.scan(mid, "jvc", "v1", "Авазбек", 12)
    r = await MV.scan(mid, "jvc", "v2", "Авазбек", 12)
    eq("строка добрана", (r["line"]["got"], r["line"]["done"]), (3, True))
    r = await MV.scan(mid, "jvc", "v3", "Авазбек", 12)
    eq("лишний скан по добранной строке", r["verdict"], "full")
    eq("задача ещё не закрыта — пиво не увезли", r.get("finished"), None)

    # ── замок смены ────────────────────────────────────────────────────────
    left = await MV.pending_for_district("jvc")
    eq("пока не всё — смену не закрыть", (len(left), left[0]["left"]), (1, 1))
    eq("у соседей замка нет", await MV.pending_for_district("tecom"), [])

    # ── пиво: код это полкоробки, задача закрывается сама ──────────────────
    r = await MV.scan(mid, "jvc", "b0", "Авазбек", 12)
    eq("первый код пива — полкоробки", (r["qty"], r["line"]["got"]), (0.5, 0.5))
    r = await MV.scan(mid, "jvc", "b1", "Авазбек", 12)
    eq("второй код добрал коробку и закрыл задачу", (r["line"]["got"], bool(r["finished"])), (1, True))
    doc = await db.move_order_get(mid)
    eq("заявка закрылась следом", doc["status"], "done")
    eq("замок смены снят", await MV.pending_for_district("jvc"), [])
    v = await MV.tasks_for_driver("Авазбек", "jvc")
    eq("закрытая задача ушла из списка", (len(v["mine"]), len(v["free"])), (0, 0))
    r = await MV.scan(mid, "jvc", "v3", "Авазбек", 12)
    eq("в закрытую заявку не досканировать", r["verdict"], "gone")
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
