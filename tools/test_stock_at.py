"""Склад на начало смены прошедшего дня (владелец, 21 сен 2026: «какой толк
там от сегодня, вчера, если остаток остаётся таким же… очень важно листать и
видеть, какой остаток по районам был вчера в начале смены»).

mongomock + настоящие stock_routes (_district_base, stock_at) и stock_value.build.

Хронология (сутки с 10:00 Дубая = 06:00 UTC):
  1 сен 05:00  пересчёт: JVC Absolut 10, BBay Absolut 20
  1 сен 15:00  продажа JVC 2            20:00 списание JVC 1 (+ одно ждёт решения)
  2 сен 07:00  приёмка JVC без сканирования: 4       09:00 первый код из них
  2 сен 10:00  BBay внесли руками 1      12:00 переезд BBay → JVC 3
  2 сен 16:00  продажа JVC 1             18:00 пересчёт BBay: 50
  3 сен 07:00  продажа JVC 1             08:00 досканировали 3 кода, задача закрыта

  • каждое утро — свои цифры: 1-го 10/20, 2-го 7/20, 3-го 13/50, 4-го 12/50;
  • пересчёт, сделанный вечером, утро того же дня не трогает;
  • принятое без сканирования лежит на полке и до того, как его досканировали;
  • сегодня — живая цифра, и она та же, что у заявки;
  • раньше первого пересчёта остатка нет — прочерк, а не ноль.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, stock_value as SV

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

U = timezone.utc
def T(d, h, m=0): return datetime(2026, 9, d, h, m, tzinfo=U)
def iso(dt): return dt.isoformat()


async def пересчёт(oid, day, at, lines):
    await db.save_stock_count(oid, day, {
        "district": oid, "day": day, "counted_at": iso(at), "first_time": False, "counted_by": 0,
        "lines": [{"id": pid, "name": pid, "price": 100, "unit": 1, "actual": q, "counted": True}
                  for pid, q in lines.items()]})


async def продажа(oid, pid, n, at, k=[0]):
    k[0] += 1
    await db._db.orders.insert_one({
        "order_id": f"S{k[0]}", "status": "delivered", "office_id": oid,
        "items": [{"id": pid, "qty": n}],
        "timestamp": at.isoformat().replace("+00:00", ""),
        "delivered_at": at.isoformat().replace("+00:00", "")})


async def код(cid, oid, pid, at, src, **kw):
    await db._db.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                                      "product_name": pid, "district": oid, "origin": oid,
                                      "at": at, "src": src, "qty": 1, **kw})


def у(base, oid, pid="p1"):
    return (base.get(oid) or {}).get("have_exact", {}).get(pid)


async def main():
    db._db = AsyncMongoMockClient()["ambar_stock_at"]
    d = db._db
    SR._biz_day = lambda *a, **k: "2026-09-05"          # «сегодня» — после всей хронологии

    await пересчёт("jvc", "2026-08-31", T(1, 5), {"p1": 10})
    await пересчёт("bbay", "2026-08-31", T(1, 5), {"p1": 20})
    await продажа("jvc", "p1", 2, T(1, 15))
    await d.writeoffs.insert_one({"_id": "w1", "district": "jvc", "item": "p1", "qty": 1,
                                  "at": T(1, 20), "state": "ok", "src": "driver"})
    await d.writeoffs.insert_one({"_id": "w2", "district": "jvc", "item": "p1", "qty": 1,
                                  "at": T(1, 21), "state": "pending", "src": "driver"})
    await d.supplies.insert_one({
        "_id": "S1", "status": "done", "at": T(2, 6, 30), "day": "2026-09-02",
        "items": [{"id": "p1", "name": "Absolut", "qty": 4, "by_district": {"jvc": 4}, "got": {"jvc": 4}}],
        "tasks": {"jvc": {"driver": "Тест-водитель", "noscan_at": T(2, 7), "started_at": T(2, 9),
                          "done_at": T(3, 8, 5), "cancelled_at": None}}})
    await код("c1", "jvc", "p1", T(2, 9), "intake", supply_id="S1")
    await код("m1", "bbay", "p1", T(2, 10), "new")
    await d.stock_transfers.insert_one({"product_id": "p1", "qty": 3, "at": iso(T(2, 12)),
                                        "day": "2026-09-02", "from": "bbay", "to": "jvc"})
    await продажа("jvc", "p1", 1, T(2, 16))
    await пересчёт("bbay", "2026-09-02", T(2, 18), {"p1": 50})
    await продажа("jvc", "p1", 1, T(3, 7))
    for i in range(3):
        await код(f"c{i + 2}", "jvc", "p1", T(3, 8), "intake", supply_id="S1")

    print("── каждое утро — свои цифры ───────────────────────────────────")
    b1 = await SR.stock_at("2026-09-01")
    eq("1 сен: сразу после пересчёта", (у(b1, "jvc"), у(b1, "bbay")), (10, 20))
    b2 = await SR.stock_at("2026-09-02")
    eq("2 сен: минус продажа и согласованное списание 1-го (ждущее — нет)",
       (у(b2, "jvc"), у(b2, "bbay")), (7, 20))
    eq("вечерний пересчёт BBay утро 2-го не трогает", b2["bbay"]["counted"], "2026-08-31")
    b3 = await SR.stock_at("2026-09-03")
    eq("3 сен: принятое без скана лежит целиком (1 код + 3 ещё без кодов), переезд +3, продажа −1",
       у(b3, "jvc"), 13)
    eq("3 сен: BBay — от вечернего пересчёта, внесённое и переезд до него уже в нём",
       у(b3, "bbay"), 50)
    b4 = await SR.stock_at("2026-09-04")
    eq("4 сен: досканировали — те же четыре, уже кодами; продажа 3-го −1", у(b4, "jvc"), 12)

    print("── сегодня — живая цифра, как у заявки ────────────────────────")
    SR.base_drop()
    live = await SR._district_base("2026-09-05")
    eq("живая = утро 4-го (после него ничего не было)", (у(live, "jvc"), у(live, "bbay")), (12, 50))
    eq("норма считается только у живой", (bool(live["jvc"].get("sug") is not None), b4["jvc"]["sug"]),
       (True, {}))
    await продажа("jvc", "p1", 1, T(4, 9))
    SR.base_drop()
    live = await SR._district_base("2026-09-05")
    b4 = await SR.stock_at("2026-09-04")
    eq("продали днём 4-го: живая 11, утро 4-го по-прежнему 12", (у(live, "jvc"), у(b4, "jvc")), (11, 12))

    print("── карточка склада ────────────────────────────────────────────")
    v = await SV.build("2026-09-03")
    jvc = next(r for r in v["items"] if r["id"] == "p1")
    eq("прошедший день: не живая, момент — 10:00 Дубая",
       (v["live"], v["at"][:16]), (False, "2026-09-03T10:00"))
    eq("по районам — утро 3-го", (jvc["have"]["jvc"], jvc["have"]["bbay"]), (13, 50))
    v = await SV.build("")
    jvc = next(r for r in v["items"] if r["id"] == "p1")
    eq("без дня — сегодня, живая", (v["live"], v["at"], jvc["have"]["jvc"]), (True, "", 11))
    v = await SV.build("2026-09-05")
    eq("сегодняшний день — тоже живая", v["live"], True)

    print("── до первого пересчёта остатка нет ───────────────────────────")
    v = await SV.build("2026-08-31")
    jvc = next(r for r in v["items"] if r["id"] == "p1")
    eq("прочерк, а не ноль", (jvc["have"]["jvc"], jvc["have"]["bbay"], v["totals"]["bottles"]),
       (None, None, 0))
    eq("и сказано, что склада тогда не было и с какого он есть",
       (v["known"], [x["known"] for x in v["districts"] if x["id"] in ("jvc", "bbay")], v.get("since", "")[:10]),
       (False, [False, False], "2026-09-01"))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
