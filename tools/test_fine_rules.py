"""Правила штрафов: прейскурант правится в STAR, а не в коде.

    python3 tools/test_fine_rules.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, fine_rules as fr                                        # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def main():
    db._db = AsyncMongoMockClient()["ambar_rules"]
    r = await fr.get()
    eq("по умолчанию — семь разделов в нашем порядке", [c["id"] for c in r], fr.CAT_IDS)
    eq("у скорости пять степеней", len(next(i for i in r[0]["items"] if i["id"] == "speed")["tiers"]), 5)
    eq("геолокация — 200", await fr.amount("geo_off"), 200)

    print("── старший правит ───────────────────────────────────────────")
    cats = await fr.get()
    cats[3]["items"] = [{"t": "  Опоздание   на смену ", "amount": "150"},
                        {"t": "Грубость", "tiers": [{"t": "первый раз", "amount": 100}, {"t": "", "amount": 5}]},
                        {"t": "", "amount": 10}]
    next(c for c in cats if c["id"] == "sys")["items"] = [{"id": "geo_off", "t": "ПЕРЕИМЕНОВАЛ", "amount": 300},
                                                          {"id": "чужое", "t": "x", "amount": 1}]
    cats.append({"id": "новый раздел", "t": "x", "items": [{"t": "y"}]})
    saved = await fr.save(cats, "Старший")
    d = saved[3]["items"]
    eq("название подчищено, сумма числом", (d[0]["t"], d[0]["amount"]), ("Опоздание на смену", 150))
    eq("пустая степень и пустое нарушение отброшены", (len(d), d[1]["tiers"]), (2, [{"t": "первый раз", "amount": 100}]))
    eq("чужой раздел не появился", [c["id"] for c in saved], fr.CAT_IDS)
    s_ = next(c for c in saved if c["id"] == "sys")["items"]
    eq("в автоматических меняется только сумма", [(i["id"], i["t"], i["amount"]) for i in s_][0],
       ("geo_off", "Отключение геолокации", 300))
    eq("состав автоматических — за программой", [i["id"] for i in s_],
       ["geo_off", "exp_rejected", "noscan_debt", "shift_left"])
    eq("сумма читается из сохранённого", await fr.amount("geo_off"), 300)
    eq("прочитали то же, что сохранили", await fr.get(), saved)

    print("── мусор не ломает ──────────────────────────────────────────")
    eq("не список — правила по умолчанию остаются формой", [c["id"] for c in fr.clean("мусор")], fr.CAT_IDS)
    eq("безумная сумма — как пустая", "amount" in fr.clean([{"id": "docs", "items": [{"t": "x", "amount": 10**9}]}])[1]["items"][0], False)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
