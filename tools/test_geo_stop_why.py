"""Выключил сам или вышел срок — это разные вещи (владелец, 24 сен 2026:
«мы же с айпада не выключали геопозицию, а он всё равно пишет „геолокация
выключена“… как нам ругать водителей, будучи неуверенным, что они сами её
отключили»).

Телеграм присылает ОДНУ И ТУ ЖЕ правку (точка без live_period) и когда
человек нажал «Стоп», и когда у трансляции вышел срок. Различаем по сроку:
правка пришла на его конце — трансляция кончилась сама.

Правило денег: штраф 200 — только за выключение руками. Вышел срок или мы
просто не видим точек — сообщение без штрафа.

    python3 tools/test_geo_stop_why.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, geo_watch, fines_auto                                  # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-24"
T0 = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


async def чисто():
    db._db = AsyncMongoMockClient()["ambar_stop_why"]


async def main():
    print("── чем кончилась трансляция ────────────────────────────────────")
    await чисто()
    # Срочная трансляция на час: правка приходит ровно на её конце.
    await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0, until=T0 + timedelta(hours=1))
    why = await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0 + timedelta(hours=1),
                                  stop_live=True)
    eq("правка на конце срока — трансляция кончилась сама", why, "expired")
    r = await db._db.driver_pos.find_one({"_id": "Азиз"})
    eq("и это записано", r.get("stopped_why"), "expired")
    eq("срок снят", r.get("until"), None)

    await чисто()
    await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0, until=T0 + timedelta(hours=8))
    why = await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0 + timedelta(minutes=20),
                                  stop_live=True)
    eq("правка посреди срока — выключил руками", why, "self")

    await чисто()
    # Бессрочная («пока не выключу»): конца у неё нет, значит только руками.
    вечно = T0 + timedelta(days=25000)
    await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0, until=вечно)
    why = await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0 + timedelta(hours=3),
                                  stop_live=True)
    eq("бессрочную можно только выключить", why, "self")

    await чисто()
    why = await db.driver_pos_set("Азиз", ДЕНЬ, 25.1, 55.2, T0, stop_live=True)
    eq("про которую мы ничего не знали — считаем выключением", why, "self")

    print("── штраф ───────────────────────────────────────────────────────")
    ШТРАФЫ = []
    async def штраф(name, district, day, at_hm, by_signal=True):
        ШТРАФЫ.append((name, at_hm, by_signal)); return True
    fines_auto.geo_off = штраф
    ВЛАДЕЛЬЦАМ = []
    async def owners(text, event, reply_markup=None, exclude=None, meta=None):
        ВЛАДЕЛЬЦАМ.append(text); return 1
    geo_watch._owners = owners
    geo_watch.staff.is_test_driver = lambda n: False
    async def sync(*a, **k): return None
    geo_watch.staff.sync = sync

    await чисто()
    await db.save_driver_day(ДЕНЬ, "Азиз", {"working": True, "shift_open_at": T0})
    geo_watch._biz_day = lambda *a, **k: ДЕНЬ

    await geo_watch.on_stream("Азиз", on=False, now=T0 + timedelta(hours=2), why="self")
    eq("выключил руками — штраф", len(ШТРАФЫ), 1)
    eq("и сказано прямо", "выключил геопозицию" in ВЛАДЕЛЬЦАМ[-1], True)

    ШТРАФЫ.clear(); ВЛАДЕЛЬЦАМ.clear()
    await чисто()
    await db.save_driver_day(ДЕНЬ, "Азиз", {"working": True, "shift_open_at": T0})
    await geo_watch.on_stream("Азиз", on=False, now=T0 + timedelta(hours=2), why="expired")
    eq("вышел срок — штрафа нет", ШТРАФЫ, [])
    eq("но старшему сказали, и другими словами",
       ("вышел срок" in ВЛАДЕЛЬЦАМ[-1], "Пока не выключу" in ВЛАДЕЛЬЦАМ[-1]), (True, True))

    ШТРАФЫ.clear(); ВЛАДЕЛЬЦАМ.clear()
    await чисто()
    await db.save_driver_day(ДЕНЬ, "Азиз", {"working": False})
    await geo_watch.on_stream("Азиз", on=False, now=T0, why="expired")
    eq("вне смены срок тоже не штраф", ШТРАФЫ, [])
    await geo_watch.on_stream("Азиз", on=False, now=T0, why="self")
    eq("а выключение вне смены — штраф", len(ШТРАФЫ), 1)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
