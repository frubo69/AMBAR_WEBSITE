"""Визы (владелец, 6 окт 2026): срок у каждого, «за пределами ОАЭ», строка
«Визы» в чек-листе, пока у кого-то срок в ближайшие две недели или вышел.

    python3 tools/test_visas.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, config_staff as staff, owner_routes as orr              # noqa: E402
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def call(h, method, body=None, path="/x"):
    req = make_mocked_request(method, path)
    async def js(): return body or {}
    req.json = js
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    res = await h(req)
    return res.status, json.loads(res.body)


async def main():
    db._db = AsyncMongoMockClient()["visas"]
    staff.AWAY.clear()
    for n, d in (("Худоба", "jvc"), ("Фарух", "jvc"), ("Алишер", "tecom"), ("Фаредун", "bbay"), ("Азиз", "silicon")):
        await db.driver_add(n, d, 1)
    await staff.sync(force=True)
    staff.AWAY["Фаредун"] = "2026-09-29"
    DAY = "2026-10-06"
    st, r = await call(orr.handle_visas_set, "POST", {"name": "Фарух", "until": "2026-10-08", "note": "Анвар", "as": "Т"})
    eq("срок записан", st, 200)
    await call(orr.handle_visas_set, "POST", {"name": "Худоба", "until": "2026-10-14", "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": "Алишер", "until": "2026-11-20", "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": "Азиз", "until": "2026-10-01", "abroad": True, "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": "Фаредун", "until": "2026-10-02", "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": "Бахтиёр", "until": "2026-10-30", "as": "Т"})
    st, r = await call(orr.handle_visas_set, "POST", {"name": "Худоба", "until": "08.10.2026", "as": "Т"})
    eq("кривая дата → 400", (st, r["error"]), (400, "bad_date"))
    st, r = await call(orr.handle_visas_set, "POST", {"name": "", "until": "2026-10-08", "as": "Т"})
    eq("без имени → 400", (st, r["error"]), (400, "no_name"))
    people = await orr._visas_view(DAY)
    byname = {p["name"]: p for p in people}
    eq("дни до срока", (byname["Фарух"]["days"], byname["Худоба"]["days"], byname["Алишер"]["days"]), (2, 8, 45))
    eq("человек не из реестра тоже в списке", "Бахтиёр" in byname and byname["Бахтиёр"]["role"], "")
    names = [p["name"] for p in people]
    eq("порядок: ближайшие сверху, уехавшие и за пределами — в конце",
       (names[:3], set(names[-2:])), (["Фарух", "Худоба", "Бахтиёр"], {"Фаредун", "Азиз"}))
    due = await orr._visas_due(DAY)
    eq("в чек-лист: Фарух (2 дня) и Худоба (8 дней); Алишер далеко, Азиз за пределами, Фаредун уехал",
       [(v["name"], v["days"], v["until_t"]) for v in due], [("Фарух", 2, "08.10"), ("Худоба", 8, "14.10")])
    st, r = await call(orr.handle_visas, "GET")
    eq("GET отдаёт список и горизонт", (st, r["horizon"], {"Фарух", "Худоба", "Алишер", "Фаредун", "Азиз", "Бахтиёр"} <= {p["name"] for p in r["people"]}), (200, 14, True))
    await call(orr.handle_visas_set, "POST", {"name": "Фарух", "until": "2026-11-08", "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": "Худоба", "until": "2026-11-14", "as": "Т"})
    eq("продлили — строке не о ком", await orr._visas_due(DAY), [])
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
