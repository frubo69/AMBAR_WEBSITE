"""Брошенный заход ревизии убирается с глаз, а сканы остаются (владелец,
25 сен 2026: «убери вот это не завершена 22 сентября — это неактуальная
ревизия, которая ломает логику»).

Район посчитали заново и довели до конца, а прежний заход так и висит над
карточкой строкой «не завершена». Убираем именно строку: под таким заходом
бывают сотни сканов, и часть кодов в новую ревизию не попала — стирать их
кнопкой нельзя.

  • ручка ставит пометку, и незавершённая перестаёт отдаваться приложению;
  • сканы после этого на месте — все до одного;
  • завершённую убрать нельзя: она живёт в истории (409);
  • второй раз — не ошибка, отвечаем «уже убрана»;
  • чужой район и несуществующая ревизия — отказ, а не тихое «ок».

    python3 tools/test_audit_drop.py
"""
import asyncio, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone                            # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as sr                                      # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

БРОШЕН, ГОТОВ, СЕГОДНЯ = "2026-09-22", "2026-09-23", "2026-09-25"
РАЙОН = "tecom"


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def drop(district, day):
    req = make_mocked_request("POST", "/x")
    req._read_bytes = json.dumps({"district": district, "day": day, "as": "STAR"}).encode()
    req["owner_id"] = 1
    r = await raw(sr.handle_audit_drop)(req)
    return r.status, json.loads(r.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_audit_drop"]
    now = datetime.now(timezone.utc).isoformat()

    # брошенный заход с сотней сканов и завершённая ревизия следующего дня
    await db.audit_set(РАЙОН, БРОШЕН, {"started_at": now, "started_by": 1, "started_by_name": "STAR"})
    for i in range(100):
        await db._db.audit_scans.insert_one(
            {"district": РАЙОН, "day": БРОШЕН, "code": f"c{i}", "at": now, "verdict": "ok"})
    await db.audit_set(РАЙОН, ГОТОВ, {"started_at": now, "finished_at": now, "finished_by": 1})

    print("── до того, как убрали ────────────────────────────────────────")
    старое = await db.audits_unfinished(СЕГОДНЯ)
    eq("незавершённая видна приложению", sorted(старое), [РАЙОН])
    eq("и это именно брошенный день", старое[РАЙОН]["day"], БРОШЕН)
    eq("сканов под ней", (await db.audit_scan_stats(РАЙОН, БРОШЕН))["total"], 100)

    print("── убираем ────────────────────────────────────────────────────")
    st, r = await drop(РАЙОН, БРОШЕН)
    eq("ручка ответила", (st, r.get("ok"), r.get("scans")), (200, True, 100))
    eq("из незавершённых ушла", sorted(await db.audits_unfinished(СЕГОДНЯ)), [])
    eq("СКАНЫ НА МЕСТЕ — все сто", (await db.audit_scan_stats(РАЙОН, БРОШЕН))["total"], 100)
    a = await db.audit_get(РАЙОН, БРОШЕН)
    eq("сама запись цела, на ней пометка", (bool(a), bool(a.get("dropped_at")), a.get("dropped_by")),
       (True, True, 1))
    eq("завершённая не тронута", bool((await db.audit_get(РАЙОН, ГОТОВ)).get("finished_at")), True)

    print("── что убрать нельзя ──────────────────────────────────────────")
    st, r = await drop(РАЙОН, ГОТОВ)
    eq("завершённую — 409", (st, r.get("error")), (409, "already_finished"))
    st, r = await drop(РАЙОН, "2026-09-01")
    eq("которой нет — 404", (st, r.get("error")), (404, "no_audit"))
    st, r = await drop("нет-такого-района", БРОШЕН)
    eq("чужой район — 400", (st, r.get("error")), (400, "unknown_district"))
    st, r = await drop(РАЙОН, БРОШЕН)
    eq("второй раз — не ошибка", (st, r.get("ok"), r.get("already")), (200, True, True))

    print("── вернуть можно, сняв пометку ────────────────────────────────")
    await db.audit_unset(РАЙОН, БРОШЕН, ["dropped_at", "dropped_by", "dropped_by_name"])
    eq("снова видна", sorted(await db.audits_unfinished(СЕГОДНЯ)), [РАЙОН])

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
