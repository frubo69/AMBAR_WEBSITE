"""Чек-лист STAR: «Расходы водителей» видит ожидающий расход за любой день
(владелец, 7 ноя 2026: «не важно, за эту они смену или за любую другую, мне
нужно, чтобы при любом несогласованном расходе плашка горела красным … чтобы,
нажимая на плашку, меня переносило конкретно в ту смену, где расход не
утверждён»). mongomock + настоящий owner_routes.handle_checklist:
  • расход за вчера — строка красная («опаздывает»), счёт 1, подпись с датой,
    дни для перехода = [вчера];
  • плюс расход за сегодня — счёт 2, дни [вчера, сегодня], подпись «6 и 7 ноября»;
  • вчерашний решён — остался сегодняшний: красная «пора», дни [сегодня];
  • решены все — «все разобраны», зелёная, дней нет;
  • расход старше CHK_EXP_DAYS не считается.

    python3 tools/test_chk_expenses.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timedelta
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, owner_routes as orr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


async def row():
    r = make_mocked_request("GET", "/api/owner/checklist"); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = orr.handle_checklist
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return {x["id"]: x for x in json.loads(resp.text)["rows"]}["expenses"]


def ex(i, st, amount=15):
    return {"id": i, "kind": "other", "kind_t": "Что-то ещё", "amount": amount, "status": st, "pay": "cash"}


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk_exp"]; d = db._db
    today = orr._biz_day_start(datetime.now(orr.DUBAI_TZ)).date()
    yday = today - timedelta(days=1)
    old = today - timedelta(days=orr.CHK_EXP_DAYS + 10)
    iso = lambda x: x.isoformat()
    await d.driver_days.insert_many([
        {"day": iso(yday), "driver": "Водитель А", "working": True, "extras": [ex("e1", "pending"), ex("e2", "approved", 120)]},
        {"day": iso(today), "driver": "Водитель Б", "working": True, "extras": [ex("e3", "approved", 60)]},
        {"day": iso(old), "driver": "Водитель В", "working": True, "extras": [ex("e9", "pending", 500)]},
    ])
    r = await row()
    eq("вчерашний расход: красная, счёт 1, ведёт в расходы",
       (r["state"], r["n"], r["go"]), ("late", 1, "expenses"))
    eq("подпись с датой", r["s"], f"1 ждёт решения · {orr._day_human(iso(yday))}")
    eq("дни для перехода — вчера", r.get("days"), [iso(yday)])

    await d.driver_days.update_one({"day": iso(today), "driver": "Водитель Б"},
                                   {"$push": {"extras": ex("e4", "pending", 40)}})
    r = await row()
    same_month = yday.month == today.month
    ждём = (f"{yday.day} и {today.day} {orr._MONTHS[today.month - 1]}" if same_month
            else f"{orr._day_human(iso(yday))} и {orr._day_human(iso(today))}")
    eq("вчера и сегодня: счёт 2, красная, оба дня по порядку",
       (r["n"], r["state"], r.get("days")), (2, "late", [iso(yday), iso(today)]))
    eq("подпись «N и M месяца»", r["s"], f"2 ждут решения · {ждём}")

    await d.driver_days.update_one({"day": iso(yday), "driver": "Водитель А", "extras.id": "e1"},
                                   {"$set": {"extras.$.status": "approved"}})
    r = await row()
    eq("остался сегодняшний: красная «пора», день — сегодня",
       (r["n"], r["state"], r.get("days")), (1, "now", [iso(today)]))

    await d.driver_days.update_one({"day": iso(today), "driver": "Водитель Б", "extras.id": "e4"},
                                   {"$set": {"extras.$.status": "rejected"}})
    r = await row()
    eq("решены все: зелёная, «все разобраны», дней нет (старый за 70 дней не в счёт)",
       (r["state"], r["s"], r["n"], "days" in r), ("done", "все разобраны", 0, False))

    eq("дни словами: один / два / три / больше трёх",
       (orr._chk_days_human(["2026-11-06"]), orr._chk_days_human(["2026-11-05", "2026-11-06"]),
        orr._chk_days_human(["2026-11-03", "2026-11-05", "2026-11-06"]),
        orr._chk_days_human(["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]),
        orr._chk_days_human(["2026-10-31", "2026-11-06"])),
       ("6 ноября", "5 и 6 ноября", "3, 5 и 6 ноября", "с 3 по 6 ноября", "31 октября и 6 ноября"))
    eq("ждёт / ждут", (orr._pl_wait(1), orr._pl_wait(2), orr._pl_wait(11), orr._pl_wait(21)),
       ("ждёт", "ждут", "ждут", "ждёт"))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
