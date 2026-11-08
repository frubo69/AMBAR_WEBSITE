"""Заявка на другую базу: пиво считается коробками до половины (владелец,
8 ноя 2026, по снимку мастера «Новая заявка»: «Почему считает бутылками,
должно же по полкоробки считать»). mongomock + настоящие обработчики:
  • создание: у пива 2,5 и «0,5» принимаются как есть, у бутылок 2,5 → 3;
    итог заявки и задач — до полкоробки;
  • задача района видит план 2,5 (раньше int резал до 2, а 0,5 — до нуля);
  • приёмка: пять кодов пива ложатся (2,5), шестой — «строка полна»;
  • цена на базе нужна и строке в полкоробки;
  • правка строк старшим: у пива на базе 1,5 так и ложится, у бутылки 1,5 → 2,
    в основной заявке пиво — целыми (2);
  • строка истории и хронология: «7,5 бутылки · 2 позиции».

    python3 tools/test_extra_half.py
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, supply_routes as sr, supply_log                        # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

BEER, VODKA = "p47", "p1"           # Carlsberg 0.5 can (коробка), Absolut 1 ltr (бутылка)

async def call(handler, body, sid=""):
    r = make_mocked_request("POST", "/x", match_info={"sid": sid} if sid else {})
    r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    async def _json(): return body
    r.json = _json
    h = handler
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)

async def main():
    db._db = AsyncMongoMockClient()["ambar_extra_half"]
    import owner_routes
    async def _say(*a, **k): return []
    owner_routes.notify_owners = _say; owner_routes.notify_owners_force = _say
    sr._costs_refresh = _say
    import stock_routes
    eq("каталог: пиво — коробка, водка — бутылка",
       (stock_routes._unit(stock_routes._catalog()[BEER]) > 1, stock_routes._unit(stock_routes._catalog()[VODKA])), (True, 1))

    print("Создание")
    st, r = await call(sr.handle_extra_create, {"base": "Blue Mart", "as": "Старший", "items": [
        {"id": BEER, "by_district": {"bbay": 2.5, "jvc": "0,5"}},
        {"id": VODKA, "by_district": {"bbay": 2.5}}]})
    eq("создана", (st, r.get("ok")), (200, True))
    sid = r["supply_id"]
    sup = await db.supply_get(sid)
    its = {i["id"]: i for i in sup["items"]}
    eq("пиво — до полкоробки, как набрали", (its[BEER]["by_district"], its[BEER]["qty"]), ({"bbay": 2.5, "jvc": 0.5}, 3))
    eq("бутылки — целыми, 2,5 → 3", (its[VODKA]["by_district"], its[VODKA]["qty"]), ({"bbay": 3}, 3))
    eq("задачи районов до полкоробки", {o: t["qty"] for o, t in sup["tasks"].items()}, {"bbay": 5.5, "jvc": 0.5})
    eq("итог заявки", (sup["total_qty"], r["total_qty"]), (6, 6))

    print("\nЗадача района и приёмка")
    v = sr._task_view(sid, sup, "bbay", sup["tasks"]["bbay"], "Худоба")
    eq("план пива в задаче 2,5, а не 2", [(l["id"], l["plan"], l["need"]) for l in v["lines"] if l["id"] == BEER], [(BEER, 2.5, 2.5)])
    eq("итог задачи 5,5", v["need"], 5.5)
    vj = sr._task_view(sid, sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("полкоробки в районе — строка есть (раньше int давал 0 и строка пропадала)", [l["id"] for l in vj["lines"]], [BEER])
    eq("цена на базе нужна и строке в полкоробки", sr._prices_missing(sup, "jvc"), [BEER])
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.bbay.driver": "Худоба", "tasks.bbay.driver_id": 7}})
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"buys": {BEER: {"price": 90}, VODKA: {"price": 40}}}})
    out = []
    for k in range(6):
        res = await sr.task_scan(sid, "bbay", BEER, f"HALF-{k}", "Худоба", 7)
        out.append(res.get("verdict") if not res.get("ok") else "ok")
    eq("пять кодов пива легли, шестой — строка полна", out, ["ok"] * 5 + ["full"])
    sup = await db.supply_get(sid)
    eq("принято 2,5 — ровно план", {i["id"]: i["got"]["bbay"] for i in sup["items"]}[BEER], 2.5)
    eq("строка истории: план и принято до полкоробки", (sr._sup_brief(sup)["total_qty"], sr._sup_brief(sup)["took"]), (6, 2.5))

    print("\nПравка строк старшим")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.jvc.started_at": None}})
    st, r = await call(sr.handle_own_lines, {"district": "jvc", "as": "Старший", "lines": [
        {"product_id": BEER, "qty": 1.5}, {"product_id": VODKA, "qty": "1,5"}]}, sid)
    eq("ответ 200", st, 200)
    sup = await db.supply_get(sid)
    its = {i["id"]: i for i in sup["items"]}
    eq("пиво на базе 1,5 так и легло, бутылка 1,5 → 2", (its[BEER]["by_district"]["jvc"], its[VODKA]["by_district"]["jvc"]), (1.5, 2))
    now = datetime.now(timezone.utc)
    await db._db.supplies.insert_one({"_id": "S-MAIN", "status": "open", "at": now, "day": "2026-11-08", "kind": "main",
        "items": [{"id": BEER, "name": "Carlsberg 0.5 can", "qty": 4, "asked": 4, "by_district": {"jvc": 4}, "got": {"jvc": 0}}],
        "tasks": {"jvc": {"driver": "", "driver_id": 0, "claimed_at": None, "started_at": None, "noscan_at": None,
                          "done_at": None, "cancelled_at": None, "scanned": 0}}})
    st, r = await call(sr.handle_own_line, {"district": "jvc", "product_id": BEER, "qty": 1.5, "as": "Старший"}, "S-MAIN")
    sup = await db.supply_get("S-MAIN")
    eq("в основной заявке пиво — целыми: 1,5 → 2", (st, sup["items"][0]["by_district"]["jvc"]), (200, 2))

    print("\nСлова")
    eq("хронология: полкоробки словом", supply_log.what_note({"total_qty": 7.5, "items": [1, 2]}), "7,5 бутылки · 2 позиции")
    eq("целое — как раньше", supply_log.what_note({"total_qty": 7, "items": [1]}), "7 бутылок · 1 позиция")
    eq("_qs", (sr._qs(2.5), sr._qs(7.0), sr._qs("3")), ("2,5", "7", "3"))
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
