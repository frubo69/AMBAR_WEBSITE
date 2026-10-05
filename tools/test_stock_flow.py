"""Движения позиции за день (владелец, 4 окт 2026: «развернуть позицию и
посмотреть, почему и за счёт чего менялся склад, куда ушли бутылки и откуда
пришли — за каждый день отдельно»). Ручка /api/owner/stock/flow на mongomock:
продажа, приёмка кодами, переезд, списание, внесённое руками, ревизия,
принятое без сканирования; остаток на начало и конец суток — тем же расчётом,
что экран склада, и лента с ним сходится.

    python3 tools/test_stock_flow.py
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, stock_routes as SR                                      # noqa: E402
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


async def main():
    db._db = AsyncMongoMockClient()["stock_flow"]
    SR._biz_day = lambda *a, **k: "2026-10-04"
    # пересчёт JVC 1 окт в 11:00 Дубай: Absolut 10, Heineken 2 коробки
    await db.save_stock_count("jvc", "2026-10-01", {
        "district": "jvc", "day": "2026-10-01", "counted_at": T(7, d=1).isoformat(), "first_time": False, "counted_by": 0,
        "lines": [{"id": "p1", "name": "Absolut 1 ltr", "price": 100, "unit": 1, "actual": 10, "counted": True},
                  {"id": "p31", "name": "Heineken 0.33 can", "price": 200, "unit": 24, "actual": 2, "counted": True}]})
    # 2 окт: продажа 2, приёмка 3 кодами, переезд 1 в B2, бой 1, внесено руками 1, ревизия в 16:00 насчитала 8
    await db._db.orders.insert_one({"order_id": "O1", "status": "delivered", "office_id": "jvc", "driver": "Худоба",
                                    "timestamp": "2026-10-02T09:00:00", "delivered_at": "2026-10-02T10:00:00",
                                    "items": [{"id": "p1", "qty": 2, "price": 100}]})
    await db._db.supplies.insert_one({"_id": "S1", "status": "done", "day": "2026-10-02", "items": [], "tasks": {"jvc": {"driver": "Худоба"}}})
    await db._db.qr_codes.insert_many([{"_id": f"c{i}", "status": "active", "product_id": "p1", "product_name": "Absolut 1 ltr",
                                        "district": "jvc", "origin": "jvc", "by": 1, "at": T(11, i), "src": "intake",
                                        "supply_id": "S1", "driver": "Худоба", "qty": 1} for i in range(3)])
    await db._db.stock_transfers.insert_one({"day": "2026-10-02", "from": "jvc", "to": "bbay", "product_id": "p1", "qty": "1.0",
                                             "by_name": "Худоба", "at": T(12).isoformat()})
    await db._db.writeoffs.insert_one({"_id": "w1", "item": "p1", "district": "jvc", "qty": "1", "kind": "бой", "at": T(13),
                                       "state": "ok", "by": "STAR", "day": "2026-10-02"})
    await db._db.qr_codes.insert_one({"_id": "m1", "status": "active", "product_id": "p1", "district": "jvc", "origin": "jvc",
                                      "by": 1, "at": T(14), "src": "new", "qty": 1})
    # пиво без сканирования: заявка S2, план 2 коробки в JVC, отметка в 15:00
    await db._db.supplies.insert_one({"_id": "S2", "status": "open", "day": "2026-10-02", "at": T(9),
                                      "items": [{"id": "p31", "name": "Heineken 0.33 can", "qty": 2, "by_district": {"jvc": 2}, "got": {"jvc": 0}}],
                                      "tasks": {"jvc": {"driver": "Худоба", "noscan_at": T(15), "noscan_by": "Худоба", "done_at": None}}})
    await db.save_stock_count("jvc", "2026-10-02", {
        "district": "jvc", "day": "2026-10-02", "counted_at": T(16).isoformat(), "first_time": False, "counted_by": 0,
        # diff в самой ревизии нарочно «не тот» (−1): лента обязана считать скачок
        # от своего бегущего остатка (10 → 8 = −2), а не верить записанной разнице
        "lines": [{"id": "p1", "name": "Absolut 1 ltr", "price": 100, "unit": 1, "expected": 9, "actual": 8, "diff": -1, "counted": True},
                  {"id": "p31", "name": "Heineken 0.33 can", "price": 200, "unit": 24, "expected": 4, "actual": 4, "diff": 0, "counted": True}]})
    SR.base_drop()

    d = await flow("2026-10-02", "p1", "jvc")
    j = d["districts"][0]
    print("JVC · Absolut · 2 окт:", [(e["kind"], e["qty"], e.get("who"), e.get("ref")) for e in j["events"]])
    eq("остаток на начало и конец суток", (j["open"], j["close"]), (10, 8))
    eq("события по порядку времени", [(e["kind"], e["qty"]) for e in j["events"]],
       [("sale", -2), ("intake", 3), ("move_out", -1), ("writeoff", -1), ("manual", 1), ("count", -2)])
    eq("кто и что: продажа — водитель, переезд — куда, списание — вид, приёмка — коды",
       [(e["kind"], e.get("who"), e.get("ref"), e.get("n")) for e in j["events"] if e["kind"] in ("sale", "move_out", "writeoff", "intake")],
       [("sale", "Худоба", "O1", None), ("intake", "Худоба", "S1", 3), ("move_out", "Худоба", "B2", None), ("writeoff", "STAR", "бой", None)])
    eq("сумма событий = конец − начало", round(sum(e["qty"] for e in j["events"]), 2), j["close"] - j["open"])
    eq("итоги по видам", (j["sum"]["sale"], j["sum"]["intake"], j["sum"]["move_out"], j["sum"]["writeoff"], j["sum"]["manual"], j["sum"]["count"]),
       (-2, 3, -1, -1, 1, -2))
    eq("у ревизии видно, сколько числилось к её минуте", next(e for e in j["events"] if e["kind"] == "count")["was"], 10)
    d = await flow("2026-10-02", "p1")
    bb = next(x for x in d["districts"] if x["id"] == "bbay")
    eq("все районы: в B2 приехала 1 из B1, остаток там неизвестен", ([(e["kind"], e["qty"], e["ref"]) for e in bb["events"]], bb["open"], bb["close"]),
       ([("move_in", 1, "B1")], None, None))
    eq("в ответе пять районов", [x["code"] for x in d["districts"]], ["B1", "B2", "B3", "B4", "B5"])
    d = await flow("2026-10-02", "p31", "jvc")
    j = d["districts"][0]
    eq("пиво: без сканирования +2 коробки, ревизия без разницы; 2 → 4", ([(e["kind"], e["qty"]) for e in j["events"]], j["open"], j["close"]), ([("noscan", 2), ("count", 0)], 2, 4))
    eq("единица позиции — коробка", d["product"]["unit"], 24)
    d = await flow("2026-10-03", "p1", "jvc")
    j = d["districts"][0]
    eq("следующий день: движений нет, 8 → 8", (j["events"], j["open"], j["close"]), ([], 8, 8))
    d = await flow("2026-10-02", "nope", "jvc")
    eq("неизвестная позиция → ошибка", d.get("error"), "unknown_product")
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
