"""Штраф из места нарушения: программа ставит «на решение» сама.

Владелец, 1 окт 2026 («1, 2, 7»): отклонённый расход, недосканированная
приёмка, незакрытая смена — строкой в «Штрафах, требующих решения», с суммой
из правил. Сумма 0 в правилах — не предлагать вовсе.

    python3 tools/test_fines_propose.py
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, fines_auto as fa, fine_rules as fr, config_staff as staff   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-10-01"


async def цены(**k):
    cats = await fr.get()
    for i in next(c for c in cats if c["id"] == "sys")["items"]:
        if i["id"] in k: i["amount"] = k[i["id"]]
    await fr.save(cats, "тест")


async def main():
    db._db = AsyncMongoMockClient()["ambar_propose"]
    staff.AWAY.clear(); staff.AWAY["Уехавший"] = "2026-09-24"
    staff.district_map = lambda: {"Худоба": "jvc", "Фарух": "jvc"}

    print("── пока сумма ноль — программа молчит ───────────────────────")
    eq("отклонённый расход не предложен", await fa.propose("exp_rejected", "Худоба", "jvc", ДЕНЬ, "Мойка 40 AED", key="e1"), False)
    eq("ждущих нет", len(await fa.pending()), 0)

    print("── старший вписал суммы в правилах ─────────────────────────")
    await цены(exp_rejected=100, shift_left=50, noscan_debt=150)
    eq("предложен", await fa.propose("exp_rejected", "Худоба", "jvc", ДЕНЬ, "Мойка 40 AED · чек не тот", key="e1"), True)
    eq("повтор того же расхода второй записи не даёт",
       await fa.propose("exp_rejected", "Худоба", "jvc", ДЕНЬ, "Мойка 40 AED", key="e1"), False)
    eq("другой расход — другая запись", await fa.propose("exp_rejected", "Худоба", "jvc", ДЕНЬ, "Бензин 90 AED", key="e2"), True)
    eq("уехавшему не предлагаем", await fa.propose("exp_rejected", "Уехавший", "jvc", ДЕНЬ, "x", key="e3"), False)
    p = await fa.pending()
    eq("в окошке — фраза целиком и сумма из правил",
       sorted((x["text"].replace(" ", " "), x["amount"], x["action"]) for x in p),
       [("Отклонённый расход — Бензин 90 AED", 100, "fine"), ("Отклонённый расход — Мойка 40 AED · чек не тот", 100, "fine")])

    print("── обход закончившихся суток ────────────────────────────────")
    now = datetime.now(timezone.utc)
    await db._db.driver_days.insert_many([
        {"day": ДЕНЬ, "driver": "Худоба", "shift_open_at": now, "shift_close_at": now},      # закрыл
        {"day": ДЕНЬ, "driver": "Фарух", "shift_open_at": now},                              # не закрыл
        {"day": ДЕНЬ, "driver": "Уехавший", "shift_open_at": now}])
    await db._db.supplies.insert_one({"_id": "S1", "status": "open", "day": ДЕНЬ, "at": now,
        "items": [{"id": "p1", "by_district": {"jvc": 10, "tecom": 4}, "got": {"jvc": 4, "tecom": 4}}],
        "tasks": {"jvc": {"driver": "Худоба", "noscan_at": now, "noscan_by": "Худоба", "done_at": None},
                  "tecom": {"driver": "Фарух", "noscan_at": now, "done_at": now}}})
    r = await fa.sweep(ДЕНЬ)
    eq("нашёл незакрытую смену и недосканированную приёмку", r, {"shift_left": 1, "noscan_debt": 1})
    eq("второй обход ничего не удваивает", await fa.sweep(ДЕНЬ), {"shift_left": 0, "noscan_debt": 0})
    by = {x["kind"]: x for x in await fa.pending() if x["kind"] != "exp_rejected"}
    eq("за смену — тому, кто не закрыл", (by["shift_left"]["name"], by["shift_left"]["amount"]), ("Фарух", 50))
    eq("за приёмку — тому, кто принял, с остатком",
       (by["noscan_debt"]["name"], by["noscan_debt"]["amount"], "осталось 6 шт" in by["noscan_debt"]["text"]), ("Худоба", 150, True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
