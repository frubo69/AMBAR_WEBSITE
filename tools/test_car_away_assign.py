"""Машина уехавшего водителя (владелец, 4 окт 2026: «почему не могу закрепить
за Диловаром свободную машину»): экран показывал машину Фаредуна свободной
(он уехал), а закрепить её не давало — «список обновлён» на каждое нажатие.
Причина: экран считал держателя без уехавших, а проверка при закреплении —
по всему реестру. Теперь правило одно.

    python3 tools/test_car_away_assign.py
"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, config_staff as staff, owner_routes as orr              # noqa: E402
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def main():
    db._db = AsyncMongoMockClient()["car_away"]
    staff.AWAY.clear()
    for n, d in (("Фаредун", "jvc"), ("Диловар", "silicon"), ("Худоба", "tecom")):
        await db.driver_add(n, d, 1)
    await staff.sync(force=True)
    staff.AWAY["Фаредун"] = "2026-10-01"                      # уехал
    cid = await db.car_add("Toyota Fortuner", "", "15788")
    await db.car_set_driver(cid, "Фаредун")
    cid2 = await db.car_add("Hyundai Elantra", "", "97448")
    await db.car_set_driver(cid2, "Худоба")

    cars, by = await orr._fleet_view()
    eq("экран: машина уехавшего свободна, машина Худобы — за ним",
       ({c["plate"]: c["driver"] for c in cars}), {"15788": "", "97448": "Худоба"})
    eq("снять машину с уехавшего — ничего не меняет", await orr._car_assign(cid, "", "take", expect=""), "")
    eq("в базе она по-прежнему за ним", (await db._db.cars.find_one({"_id": cid}))["driver"], "Фаредун")
    eq("закрепить свободную (на экране) машину за Диловаром — можно", await orr._car_assign(cid, "Диловар", "take", expect=""), "")
    eq("в базе теперь Диловар", (await db._db.cars.find_one({"_id": cid}))["driver"], "Диловар")
    eq("машина Худобы с устаревшим экраном («свободна») — всё ещё «список обновлён»",
       await orr._car_assign(cid2, "Диловар", "take", expect=""), "changed")
    eq("а с верным ожиданием — обмен проходит", await orr._car_assign(cid2, "Диловар", "swap", expect="Худоба"), "")
    got = {c["driver"]: c["plate"] async for c in db._db.cars.find({})}
    eq("после обмена: Диловар на Элантре, Худоба на Фортунере", (got.get("Диловар"), got.get("Худоба")), ("97448", "15788"))
    eq("уехавший как водитель машину не получает", await orr._car_assign(cid, "Фаредун", "take", expect="Худоба") in ("", "changed"), True)
    staff.AWAY.clear()
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
