"""Чек-лист STAR, 30 окт 2026 (владелец): «приёмка же не закончена, почему
нет алерта в чек-листе» и «когда магазин меняет цены … оно должно там гореть,
пока решение по новым ценам не принято».
mongomock + настоящий owner_routes.handle_checklist:
  • открытая приёмка позавчерашнего дня — строка «Поставка принята» есть,
    красная, с днём и незакрытыми районами, ведёт в ту поставку (sid);
  • район закрыли, поставка done — строки по ней нет;
  • новые цены магазина ждут решения — строка «Новые цены магазина», красная,
    со счётом и ценами, ведёт в прайс; решили все — строки нет.

    python3 tools/test_chk_supply_prices.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, owner_routes as orr, stock_value as sv

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


async def rows():
    r = make_mocked_request("GET", "/api/owner/checklist"); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = orr.handle_checklist
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return {x["id"]: x for x in json.loads(resp.text)["rows"]}


def task(driver="", done=False):
    return {"qty": 10, "positions": 1, "scanned": 0, "driver": driver, "driver_id": 1 if driver else 0,
            "claimed_at": None, "started_at": None, "done_at": datetime.now(timezone.utc) if done else None,
            "last_at": None, "undo": 0, "note": "", "gaps": [], "flags": [], "cancelled_at": None}


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk2"]; d = db._db
    now = datetime.now(orr.DUBAI_TZ)
    day = orr._biz_day_start(now).date().isoformat()
    позавчера = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    oid = list(orr.OFFICE_IDS)
    await d.supplies.insert_one({"_id": "S1", "at": datetime.now(timezone.utc) - timedelta(days=2), "status": "open",
                                 "day": позавчера, "kind": "main", "items": [],
                                 "tasks": {oid[0]: task("Худоба"), oid[1]: task(), oid[2]: task("Баха", done=True)}})

    print("── открытая приёмка позавчера ─────────────────────────────────")
    r = (await rows()).get("supply_in")
    коды = [orr.OFFICE_CODES.get(o, o) for o in (oid[0], oid[1])]
    eq("строка есть, не сделана, красная, ведёт в поставку",
       (r and r["t"], r and r["state"], r and r["go"], r and r.get("sid"), r and r["n"]),
       ("Поставка принята", "late", "supply", "S1", 2))
    eq("подпись: кто, день, незакрытые районы, без водителя",
       r and r["s"], f"Барракуда {orr._day_human(позавчера)} · не закрыты {', '.join(коды)} · без водителя 1")
    eq("просрочено по дню поставки — больше суток", r and r["late_min"] > 24 * 60, True)

    print("── «сегодня не заказываем» строку открытой приёмки не гасит ───")
    await db.checklist_set(day, "order_skip", True, "1")
    r = (await rows()).get("supply_in")
    eq("строка по-прежнему красная", (r and r["state"], r and r.get("sid")), ("late", "S1"))
    await db.checklist_set(day, "order_skip", False, "1")

    print("── закрыли последний район ────────────────────────────────────")
    await d.supplies.update_one({"_id": "S1"}, {"$set": {"status": "done",
        f"tasks.{oid[0]}.done_at": datetime.now(timezone.utc), f"tasks.{oid[1]}.done_at": datetime.now(timezone.utc)}})
    eq("строки нет — поставка не этих суток и закрыта", "supply_in" in await rows(), False)

    print("── новые цены магазина ────────────────────────────────────────")
    at = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    await db.setting_set(sv.PROPOSALS_KEY, {"rows": {
        "p1": {"id": "p1", "name": "Absolut", "ours": 40, "theirs": 43.5, "unit": 1, "state": "pending", "sid": "S1", "at": at},
        "p2": {"id": "p2", "name": "Red Label", "ours": 55, "theirs": 58, "unit": 1, "state": "pending", "sid": "S1", "at": at},
        "p3": {"id": "p3", "name": "Jack", "ours": 70, "theirs": 72, "unit": 1, "state": "kept", "sid": "S1", "at": at}}})
    r = (await rows()).get("prices_new")
    eq("строка есть: название, счёт без решённых, красная, в прайс",
       (r and r["t"], r and r["n"], r and r["state"], r and r["go"]), ("Новые цены магазина", 2, "late", "prices"))
    eq("подпись — позиции и цены", r and r["s"], "Absolut 40 → 43,5 · Red Label 55 → 58")
    eq("горит с минуты загрузки — около полутора часов", 85 <= (r["late_min"] if r else 0) <= 95, True)
    await sv.price_proposal_decide("p1", True, "тест"); await sv.price_proposal_decide("p2", False, "тест")
    eq("решили все — строки нет", "prices_new" in await rows(), False)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
