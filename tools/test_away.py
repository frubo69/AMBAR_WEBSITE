"""Уехавший исчезает из рабочих списков, но остаётся в команде (владелец,
24 сен 2026: «они улетели — они должны отовсюду исчезать, пока снова не
прилетели… оставляй их в команде, просто пиши серым, что уехал; но в расходы
смены его даже включать не надо»).

Один ответ на всю систему — `config_staff.AWAY`, он же у панели оператора,
у локатора, у проверки бутылок и у напоминаний.

    python3 tools/test_away.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, config_staff as staff                                  # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-25"


async def завести():
    db._db = AsyncMongoMockClient()["ambar_away"]
    await db._db.fin_people.insert_many([
        {"_id": "Работает", "work": [{"from": "2026-01-01", "to": ""}]},
        {"_id": "Уехал", "work": [{"from": "2026-01-01", "to": "2026-09-24"}]},
        {"_id": "Сегодня", "work": [{"from": "2026-01-01", "to": ДЕНЬ}]},
        {"_id": "Вернулся", "work": [{"from": "2026-01-01", "to": "2026-08-01"},
                                     {"from": "2026-09-20", "to": ""}]},
    ])
    await staff.sync_away(ДЕНЬ)


async def main():
    await завести()
    eq("уехавшие найдены", sorted(staff.AWAY), ["Сегодня", "Уехал"])
    eq("день отъезда записан", staff.away_since("Уехал"), "2026-09-24")
    eq("работающий на месте", staff.is_away("Работает"), False)
    eq("вернувшийся на месте", staff.is_away("Вернулся"), False)
    eq("кого нет в «Зарплатах» — считаем на месте", staff.is_away("Неизвестный"), False)
    eq("список без уехавших",
       staff.here(["Работает", "Уехал", "Вернулся", "Сегодня"]), ["Работает", "Вернулся"])

    # Расходы смены: строка уехавшего не должна появиться вовсе.
    staff.DISTRICT_STAFF[0]["drivers"] = ["Работает", "Уехал"]   # состав района
    строки = [d for d in staff.drivers() if not d.get("away")]
    eq("в расходах смены только те, кто здесь",
       [d["name"] for d in строки if d["name"] in ("Работает", "Уехал")], ["Работает"])
    eq("а в команде он есть, с пометкой",
       [(d["name"], d["away"], d["away_since"]) for d in staff.drivers() if d["name"] == "Уехал"],
       [("Уехал", True, "2026-09-24")])

    # Вернулся — и снова везде.
    await db._db.fin_people.update_one({"_id": "Уехал"},
                                       {"$push": {"work": {"from": ДЕНЬ, "to": ""}}})
    await staff.sync_away(ДЕНЬ)
    eq("прилетел — снова в списках", staff.is_away("Уехал"), False)
    eq("и в рабочем списке", staff.here(["Работает", "Уехал"]), ["Работает", "Уехал"])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
