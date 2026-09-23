"""Незавершённая ревизия не пропадает назавтра (владелец, 23 сен 2026: «вчера
с телефона через AMBAR STAR проводилась ревизия, она была незавершена, но
сегодня исчезла — можешь вернуть, чтобы я её закончил?»).

Ревизия привязана ко дню: начали вчера, «Завершить» не нажали — сегодня экран
спрашивает сегодняшний день, и вчерашняя со всеми сканами уходит с глаз.
Ничего не терялось: сканы лежат под своим днём. Теперь список районов сам
говорит о ней, и открыть её можно ЕЁ днём — считали вчера, значит и сверять
надо со вчерашним складом.

mongomock + настоящий stock_routes.handle_status.

    python3 tools/test_audit_resume.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone                        # noqa: E402
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
from aiohttp.test_utils import make_mocked_request              # noqa: E402
import db, stock_routes as sr                                   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ВЧЕРА, СЕГОДНЯ = "2026-09-22", "2026-09-23"


async def районы():
    r = make_mocked_request("GET", f"/api/owner/stock/status?day={СЕГОДНЯ}")
    r["owner_id"] = 1
    h = sr.handle_status
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return {d["id"]: d for d in json.loads(resp.text)["districts"]}


async def main():
    db._db = AsyncMongoMockClient()["ambar_aud"]
    d = db._db
    at = datetime.now(timezone.utc)
    await d.stock_audits.insert_many([
        # вчерашняя, не завершена — её и надо вернуть
        {"district": "jvc", "day": ВЧЕРА, "started_at": at.isoformat(), "started_by_name": "STAR"},
        # вчерашняя, но завершена — возвращать нечего
        {"district": "bbay", "day": ВЧЕРА, "started_at": at.isoformat(),
         "finished_at": at.isoformat(), "finished_by_name": "STAR"},
        # сегодня уже идёт своя — про вчерашнюю не зовём, чтобы не путать
        {"district": "tecom", "day": ВЧЕРА, "started_at": at.isoformat()},
        {"district": "tecom", "day": СЕГОДНЯ, "started_at": at.isoformat()}])
    await d.audit_scans.insert_many(
        [{"_id": f"jvc:{ВЧЕРА}:c{i}", "district": "jvc", "day": ВЧЕРА, "code": f"c{i}", "at": at}
         for i in range(584)])

    r = await районы()
    о = (r.get("jvc") or {}).get("audit_open") or {}
    eq("вчерашняя незавершённая — на месте", (о.get("day"), о.get("scans")), (ВЧЕРА, 584))
    eq("и сегодня по этому району ревизия «не начата»", (r.get("jvc") or {}).get("audit"), "idle")
    eq("завершённую вчера не зовём", "audit_open" in (r.get("bbay") or {}), False)
    eq("если сегодня уже идёт своя — вчерашнюю не показываем",
       ["audit_open" in (r.get("tecom") or {}), (r.get("tecom") or {}).get("audit")], [False, "running"])
    eq("район без ревизий молчит",
       ["audit_open" in (r.get("silicon") or {}), (r.get("silicon") or {}).get("audit")], [False, "idle"])

    # Лист ревизии за тот день — с теми же сканами: открыть её можно.
    лист = await sr.audit_sheet("jvc", ВЧЕРА)
    eq("лист за вчера открывается и помнит проход камерой",
       (лист["day"], (лист.get("scan") or {}).get("total"), (лист.get("audit") or {}).get("state")),
       (ВЧЕРА, 584, "running"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
