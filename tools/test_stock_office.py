"""Две дыры склада, найденные разбором ревизии JVC 25 сен 2026 (владелец,
6 окт: «закрывай»):

1. Заказ записан на район адреса, а бутылки везёт водитель со своей полки.
   Заказ на Tecom развёз водитель JVC винами JVC — списалось с Tecom, где этих
   вин было ноль (то есть ни с кого), а ревизия JVC нашла «пропавшие».
   Теперь при «доставлен» заказ получает stock_office — район водителя, и
   склад списывает по нему; списанное сверх остатка (упёрлось в ноль)
   считается и видно в строках ревизии и в ленте.
2. «Внести новый товар» во время ревизии прибавлял к ожиданию, а «Убрать из
   реестра» код листа или приёмки ожидание не трогал (и не должен: убрал код —
   бутылка на полке остаётся, правило 19 сен). Теперь новый код рядом с
   убранным того же товара в том же районе — переклеенный стикер той же
   бутылки, не +1; в ленте оба видны нулём.

    python3 tools/test_stock_office.py
"""
import asyncio, json, os, sys
from datetime import datetime, timezone, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, stock_routes as SR, config_staff as staff               # noqa: E402
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def T(h, m=0, d=2):
    return datetime(2026, 10, d, h, m, tzinfo=timezone.utc)


async def flow(day, pid, district=""):
    q = f"?day={day}&product={pid}" + (f"&district={district}" if district else "")
    h = SR.handle_flow
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    res = await h(make_mocked_request("GET", "/api/owner/stock/flow" + q))
    return json.loads(res.body)


async def count(district, day, at, lines):
    await db.save_stock_count(district, day, {
        "district": district, "day": day, "counted_at": at.isoformat(), "first_time": False, "counted_by": 0,
        "lines": [{"id": p, "name": p, "price": 100, "unit": 1, "actual": a, "counted": True} for p, a in lines.items()]})


async def main():
    db._db = AsyncMongoMockClient()["stock_office"]
    SR._biz_day = lambda *a, **k: "2026-10-03"
    staff.district_map = lambda: {"Худоба": "jvc", "Файзуло": "tecom"}

    # ── 1. район водителя при «доставлен»
    eq("район водителя из реестра", staff.stock_office("Худоба", "tecom"), "jvc")
    eq("водитель не из реестра → район заказа", staff.stock_office("Некто", "tecom"), "tecom")
    eq("без водителя → район заказа", staff.stock_office("", "tecom"), "tecom")

    # пересчёт 1 окт: в JVC 5 вин и 3 виски, в Tecom вина нет
    await count("jvc", "2026-10-01", T(7, d=1), {"p1": 5, "p2": 3})
    await count("tecom", "2026-10-01", T(7, d=1), {"p1": 0, "p2": 2})
    # 2 окт: заказ на Tecom, везёт Худоба (JVC) — старый заказ без stock_office и новый с ним
    await db._db.orders.insert_many([
        {"order_id": "OLD", "status": "delivered", "office_id": "tecom", "driver": "Худоба",
         "timestamp": "2026-10-02T09:00:00", "delivered_at": "2026-10-02T10:00:00",
         "items": [{"id": "p2", "qty": 1, "price": 100}]},
        {"order_id": "NEW", "status": "delivered", "office_id": "tecom", "stock_office": "jvc", "driver": "Худоба",
         "timestamp": "2026-10-02T11:00:00", "delivered_at": "2026-10-02T12:00:00",
         "items": [{"id": "p1", "qty": 2, "price": 100}]},
    ])
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("новый заказ списался с полки водителя (JVC), не с района адреса", (base["jvc"]["have_exact"]["p1"], base["tecom"]["have_exact"].get("p1", 0)), (3, 0))
    eq("старый заказ без отметки — как раньше, с района заказа", (base["tecom"]["have_exact"]["p2"], base["jvc"]["have_exact"]["p2"]), (1, 3))
    eq("сверх остатка нигде нет", (base["jvc"].get("zero"), base["tecom"].get("zero")), ({}, {}))
    d = await flow("2026-10-02", "p1")
    jvc = next(x for x in d["districts"] if x["id"] == "jvc"); tec = next(x for x in d["districts"] if x["id"] == "tecom")
    eq("лента: продажа у JVC с пометкой «заказ B5», у Tecom пусто",
       ([(e["kind"], e["qty"], e.get("office")) for e in jvc["events"]], tec["events"]), ([("sale", -2, "B5")], []))

    # ── 2. списание сверх остатка: заказ на Tecom без отметки, вина там ноль
    await db._db.orders.insert_one(
        {"order_id": "ZERO", "status": "delivered", "office_id": "tecom", "driver": "Худоба",
         "timestamp": "2026-10-02T13:00:00", "delivered_at": "2026-10-02T14:00:00",
         "items": [{"id": "p1", "qty": 1, "price": 100}]})
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("полка в минус не уходит, но «сверх остатка» посчитано", (base["tecom"]["have_exact"].get("p1", 0), base["tecom"]["zero"]), (0, {"p1": 1}))
    d = await flow("2026-10-02", "p1", "tecom")
    eq("лента: у события «сверх остатка 1»", [(e["kind"], e["qty"], e.get("zero")) for e in d["districts"][0]["events"]], [("sale", -1, 1)])
    lines = await SR._audit_lines("tecom", "2026-10-03")
    ln = next(l for l in lines[0] if l["id"] == "p1") if isinstance(lines, tuple) else next(l for l in lines if l["id"] == "p1")
    eq("строка ревизии несёт «сверх остатка»", (ln["expected"], ln["zero"]), (0, 1))

    # ── 3. убранный код бутылку не снимает; новый код рядом с убранным — переклеенный стикер
    await db._db.qr_codes.insert_many([
        {"_id": f"k{i}", "status": "active", "product_id": "p2", "district": "jvc", "origin": "jvc",
         "by": 1, "at": T(15, i), "src": "intake", "supply_id": "S1", "qty": 1} for i in range(2)])
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("приёмка двух кодов: виски 3 → 5", base["jvc"]["have_exact"]["p2"], 5)
    await db.qr_drop("k1", 1, T(16))
    base = await SR._base_calc("2026-10-03")
    eq("убрали код — бутылка на полке остаётся: 5 (правило 19 сен)", base["jvc"]["have_exact"]["p2"], 5)
    d = await flow("2026-10-02", "p2", "jvc")
    eq("лента: приёмка +2, убран код — нулём", [(e["kind"], e["qty"], e.get("ref")) for e in d["districts"][0]["events"]], [("intake", 2, "S1"), ("drop", 0, "k1")])
    # новый стикер на ту же бутылку: «внести новый товар» — пара с убранным, не +1
    await db._db.qr_codes.insert_one({"_id": "n1", "status": "active", "product_id": "p2", "district": "jvc", "origin": "jvc",
                                      "by": 1, "at": T(16, 5), "src": "new", "qty": 1, "driver": "STAR"})
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("новый код после убранного — переклеенный стикер: 5, не 6", base["jvc"]["have_exact"]["p2"], 5)
    d = await flow("2026-10-02", "p2", "jvc")
    eq("лента: стикер переклеен нулём, «внесено руками» нет", [(e["kind"], e["qty"], e.get("ref")) for e in d["districts"][0]["events"]],
       [("intake", 2, "S1"), ("drop", 0, "k1"), ("relabel", 0, "n1")])
    # второй новый код без пары — настоящая новая бутылка
    await db._db.qr_codes.insert_one({"_id": "n2", "status": "active", "product_id": "p2", "district": "jvc", "origin": "jvc",
                                      "by": 1, "at": T(17), "src": "new", "qty": 1})
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("второй новый код без убранного — +1: 6", base["jvc"]["have_exact"]["p2"], 6)
    d = await flow("2026-10-02", "p2", "jvc")
    eq("лента: пара — нулём, лишний — приход", [(e["kind"], e["qty"]) for e in d["districts"][0]["events"]],
       [("intake", 2), ("drop", 0), ("relabel", 0), ("manual", 1)])
    before = await SR._base_calc("2026-10-02", until=T(16, 30))
    eq("на момент до второго кода: 5", before["jvc"]["have_exact"]["p2"], 5)
    eq("пара в любом порядке: новый раньше убранного", SR._pair_relabels([(T(10),), (T(12),)], [(T(11),)]), {0})
    eq("двух убранных на один новый — пара одна", SR._pair_relabels([(T(10),)], [(T(9),), (T(11),)]), {0})
    # код, убранный ДО пересчёта, пару не образует: пересчёт его уже не видел
    await count("silicon", "2026-10-01", T(7, d=1), {"p3": 7})
    await db._db.qr_codes.insert_many([
        {"_id": "cov0", "status": "deleted", "was": "active", "product_id": "p3", "district": "silicon", "origin": "silicon", "by": 1, "at": T(8, d=1), "src": "cover", "qty": 1, "del_at": T(6, 30, d=1)},
        {"_id": "new0", "status": "active", "product_id": "p3", "district": "silicon", "origin": "silicon", "by": 1, "at": T(9), "src": "new", "qty": 1}])
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("убранный до пересчёта — не пара, новый код +1: 8", base["silicon"]["have_exact"]["p3"], 8)
    # сценарий JVC 25 сен: лист 7, код листа убрали, стикер внесли «новым» — 7, не 8
    await db._db.qr_codes.insert_many([
        {"_id": "cov1", "status": "active", "product_id": "p3", "district": "silicon", "origin": "silicon", "by": 1, "at": T(8, d=1), "src": "cover", "qty": 1},
        {"_id": "new1", "status": "active", "product_id": "p3", "district": "silicon", "origin": "silicon", "by": 1, "at": T(10, 2), "src": "new", "qty": 1}])
    await db.qr_drop("cov1", 1, T(10))
    SR.base_drop()
    base = await SR._base_calc("2026-10-03")
    eq("JVC 25 сен: убран код листа + «новый товар» = как было (8, не 9)", base["silicon"]["have_exact"]["p3"], 8)
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
