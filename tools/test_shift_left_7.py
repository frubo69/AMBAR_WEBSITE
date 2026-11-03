"""Смена не закрыта к 07:00 — штраф 200 на решение (владелец, 3 ноя 2026:
«сделай на незакрытие смены штраф 200 дирхам, время до 7 утра её закрыть»).

Учётные сутки начинаются в 10:00; смену, открытую в них, надо закрыть до
семи утра. В семь (fines_auto.tick из loop, раз в десять минут, отметка
once_mark — один раз за сутки) открытые и не закрытые смены уходят в «Штрафы,
требующие решения» с суммой из правил, старшему — сообщение с кнопкой.
Обход после смены суток (sweep) — страховка: та же запись, второй не даёт.

Сценарии на mongomock с настоящими fines_auto / fine_rules.

    python3 tools/test_shift_left_7.py
"""
import asyncio, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["OWNER_WEBAPP_URL"] = "https://star.example.test/owner/"
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                        # noqa: E402
import db, fines_auto as fa, fine_rules as fr, config_staff as staff, bizday, owner_routes  # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

def D(day, h, m=0): return datetime(2026, 11, day, h, m, tzinfo=bizday.DUBAI_TZ)
ДЕНЬ = "2026-11-02"                     # сутки 2 ноя 10:00 → 3 ноя 10:00

SENT = []
async def _notify(key, text, parse_mode="Markdown", meta=None, test=False, reply_markup=None):
    SENT.append({"key": key, "text": text, "btn": reply_markup}); return []
owner_routes.notify_owners = _notify


async def смена(name, day, opened, closed=None):
    await db._db.driver_days.insert_one({"day": day, "driver": name, "working": True,
                                         "shift_open_at": opened, **({"shift_close_at": closed} if closed else {})})


async def main():
    db._db = AsyncMongoMockClient()["ambar_shift7"]
    staff.AWAY.clear(); staff.AWAY["Уехавший"] = "2026-10-01"
    staff.district_map = lambda: {"Фарух": "jvc", "Худоба": "tecom", "Алишер": "jvc", "Уехавший": "jvc"}
    staff.test_driver_names = lambda: {"Тест-водитель"}

    print("── правило ─────────────────────────────────────────────────")
    eq("сумма по умолчанию 200", await fr.amount("shift_left", 0), 200)
    eq("срок — семь утра", fa.SHIFT_CLOSE_BY, 7)
    eq("до семи — не время", fa.close_by_day(D(3, 6, 59)), None)
    eq("в семь — сутки 2 ноя", fa.close_by_day(D(3, 7, 0)), ДЕНЬ)
    eq("без десяти десять — те же сутки", fa.close_by_day(D(3, 9, 59)), ДЕНЬ)
    eq("с десяти — новые сутки, проверять нечего", fa.close_by_day(D(3, 10, 0)), None)
    eq("днём — не время", fa.close_by_day(D(2, 12, 0)), None)

    await смена("Фарух", ДЕНЬ, D(2, 18, 5))                     # не закрыл
    await смена("Худоба", ДЕНЬ, D(2, 17, 50), D(3, 5, 40))        # закрыл
    await смена("Тест-водитель", ДЕНЬ, D(2, 18, 0))               # тест — мимо
    await смена("Уехавший", ДЕНЬ, D(2, 18, 0))                    # уехал — мимо
    await смена("Алишер", "2026-11-01", D(1, 18, 0))              # вчерашние сутки — не эти

    print("── без десяти семь — рано ───────────────────────────────────")
    eq("ничего", await fa.tick(D(3, 6, 50)), 0)
    eq("ждущих нет", len(await fa.pending()), 0)
    eq("старшему ничего", len(SENT), 0)

    print("── пять минут восьмого ──────────────────────────────────────")
    eq("один не закрыл", await fa.tick(D(3, 7, 5)), 1)
    p = await fa.pending()
    eq("на решение — Фарух, 200, штраф", [(x["name"], x["amount"], x["action"], x["day"]) for x in p],
       [("Фарух", 200, "fine", ДЕНЬ)])
    eq("формулировка целиком", p[0]["text"].replace(" ", " "),
       "Смена не закрыта — открыта в 18:05, к 07:00 не закрыта")
    eq("старшему одно сообщение", len(SENT), 1)
    m = SENT[0]
    eq("ключ события", m["key"], "driver.shift_left")
    eq("в сообщении — срок, кто, где, во сколько открыл, сумма",
       all(x in m["text"] for x in ("Смена не закрыта к 07:00", "Фарух", "B1", "открыта в 18:05", "Штраф 200 AED ждёт решения")), True)
    eq("кнопка в «Штрафы»", "go=fines" in str(m["btn"]), True)

    print("── через десять минут и после закрытия ─────────────────────")
    eq("повтор за те же сутки — ничего", await fa.tick(D(3, 7, 15)), 0)
    eq("сообщений не прибавилось", len(SENT), 1)
    await db._db.driver_days.update_one({"day": ДЕНЬ, "driver": "Фарух"}, {"$set": {"shift_close_at": D(3, 7, 30)}})
    eq("закрыл в 07:30 — штраф на решении остаётся", [(x["name"], x["status"]) for x in await fa.pending()], [("Фарух", "pending")])

    print("── страховка: обход после смены суток ───────────────────────")
    await смена("Алишер", ДЕНЬ, D(2, 19, 0))                      # в семь служба не работала
    r = await fa.sweep(ДЕНЬ)
    eq("обход нашёл Алишера, Фаруха второй раз не пишет", r["shift_left"], 1)
    eq("сказал старшему", (len(SENT), "Алишер" in SENT[-1]["text"]), (2, True))
    eq("в формулировке тот же срок", [x["text"].replace(" ", " ") for x in await fa.pending() if x["name"] == "Алишер"],
       ["Смена не закрыта — открыта в 19:00, к 07:00 не закрыта"])
    eq("второй обход — ничего", (await fa.sweep(ДЕНЬ))["shift_left"], 0)

    print("── двое сразу и выключатель ─────────────────────────────────")
    ДЕНЬ2 = "2026-11-03"
    await смена("Фарух", ДЕНЬ2, D(3, 18, 0)); await смена("Худоба", ДЕНЬ2, D(3, 18, 10))
    eq("двое не закрыли", await fa.tick(D(4, 7, 1)), 2)
    eq("одно сообщение на двоих, во множественном числе",
       (len(SENT), "Штрафы по 200 AED ждут решения" in SENT[-1]["text"], "Худоба" in SENT[-1]["text"]), (3, True, True))
    cats = await fr.get()
    for i in next(c for c in cats if c["id"] == "sys")["items"]:
        if i["id"] == "shift_left": i["amount"] = 0
    await fr.save(cats, "тест")
    ДЕНЬ3 = "2026-11-04"
    await смена("Фарух", ДЕНЬ3, D(4, 18, 0))
    eq("сумма 0 в правилах — не предлагаем и не пишем", (await fa.tick(D(5, 7, 1)), len(SENT)), (0, 3))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
