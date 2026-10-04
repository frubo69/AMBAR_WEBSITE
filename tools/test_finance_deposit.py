"""Депозиты (владелец, 4 окт 2026): «там же, где указываем, за что оплатили,
доп. графа депозит; внесли — вычитается из РП, забрали — через Доп. РП+
возвращается». Депозит — не расход: деньги наши, лежат у контрагента.
Расходом становится только удержанное при возврате.

    python3 tools/test_finance_deposit.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import finance_calc as calc                                        # noqa: E402
import fuzz_finance as FF                                          # noqa: E402
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def день(d, **k):
    return {**dict(day=f"2026-10-{d:02d}", cash=0, spend=0, handed=0, ordered=0, ok=True, norm=9000), **k}


print("— ядро: депозит — не расход")
op = dict(safe_b_open=10000, rp_open=20000, np_open=5000, debt_b_open=50000)
plain = calc.compute([день(1, expenses=[{"amount": 3000}])], op)
dep = calc.compute([день(1, expenses=[{"amount": 3000, "deposit": True}])], op)
eq("из РП ушло одинаково", (plain["days"][0]["stack_rp"], dep["days"][0]["stack_rp"]), (17000, 17000))
eq("депозит лежит 3000, у обычного расхода — 0", (dep["days"][0]["dep"], plain["days"][0]["dep"]), (3000, 0))
eq("прибыль по расчёту депозитом не уменьшена", dep["econ"] - plain["econ"], 3000)
eq("сейф месяца: внесено 3000, лежит 3000", (dep["safe"]["dep_paid"], dep["safe"]["dep"], dep["rp"]["dep"]), (3000, 3000, 3000))
days = [день(1, expenses=[{"amount": 3000, "deposit": True}]),
        день(2, extra_rp=2000, dep_back=2000),
        день(3, extra_rp=500, dep_back=500, dep_lost=500)]
r = calc.compute(days, op)
eq("день 2: вернулось 2000 — РП 19000, лежит 1000", (r["days"][1]["stack_rp"], r["days"][1]["dep"]), (19000, 1000))
eq("день 3: вернулось 500, удержано 500 — РП 19500, лежит 0", (r["days"][2]["stack_rp"], r["days"][2]["dep"]), (19500, 0))
eq("месяц: внесено / вернулось / удержано", (r["safe"]["dep_paid"], r["safe"]["dep_back"], r["safe"]["dep_lost"]), (3000, 2500, 500))
r0 = calc.compute([день(1), день(2), день(3)], op)
eq("удержанное — расход: прибыль ниже ровно на 500", r0["econ"] - r["econ"], 500)
eq("РП: начало + приход − расход = наличные", r["safe"]["open_rp"] + r["safe"]["rp_in"] - r["safe"]["rp_out"], r["safe"]["rp"])
c = calc.carry_from(calc.compute(days[:1], op))
eq("депозит переезжает в следующий месяц", c["dep_open"], 3000)
r2 = calc.compute([день(1, extra_rp=1000, dep_back=1000)], dict(op, dep_open=3000))
eq("перенесённый депозит возвращается", (r2["safe"]["open_dep"], r2["days"][0]["dep"], r2["safe"]["dep"]), (3000, 2000, 2000))


async def сервер():
    db, fr = await FF._сервер_готовь()
    import owner_auth
    owner_auth.install_alerter(None)
    СЕГ = FF.СЕГОДНЯ; МЕС = FF.МЕС
    ВЧЕРА = (datetime.strptime(СЕГ, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    зови = FF._зови
    for f, v in (("safe_b_open", 10000), ("rp_open", 20000), ("np_open", 3000), ("debt_b_open", 50000), ("norm", 9000)):
        await зови(fr.handle_month_set, "POST", {"month": МЕС, "field": f, "value": v, "as": "Т"})
    await db.fin_budget_set({"_id": "L1", "month": МЕС, "name": "Орион Рент", "group": "car", "plan": 25000,
                             "due": "", "note": "", "kind": "", "ord": 1, "by": 1, "at": datetime.now()})
    print("— сервер: внесли депозит")
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "rp", "amount": 3000, "line": "L1", "who": "Орион",
                                                       "comment": "депозит за машину", "deposit": True, "photo": FF.КАДР, "as": "Т"})
    eq("записан", код, 200)
    book = отв.get("book") or await fr.build(МЕС)
    d = next(x for x in book["days"] if x["day"] == СЕГ)
    eq("РП 17000, лежит 3000, расход дня 3000", (d["stack_rp"], d["dep"], d["expenses_sum"]), (17000, 3000, 3000))
    eq("у записи отметка депозита", [e["deposit"] for e in d["expenses"]], [True])
    deps = book["deposits"]
    eq("в списке один открытый: Орион, 3000", [(x["who"], x["amount"], x["left"], x["open"], x["line_name"]) for x in deps],
       [("Орион", 3000, 3000, True, "Орион Рент")])
    dep_id = deps[0]["id"]
    eq("депозит с видом (зарплата) отметку не берёт", (await зови(fr.handle_entry_add, "POST",
       {"day": СЕГ, "book": "rp", "amount": 100, "kind": "salary", "who": "Иван", "deposit": True, "as": "Т"}))[1]["book"]["safe"]["dep_paid"], 3000)

    print("— сервер: возврат через Доп. РП+")
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 1000, "src": "deposit", "as": "Т"})
    eq("без депозита → 400", (код, отв["error"]), (400, "bad_deposit"))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 1000, "src": "deposit", "dep_of": "нет", "as": "Т"})
    eq("чужой id → 404", (код, отв["error"]), (404, "no_deposit"))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": ВЧЕРА, "book": "in", "amount": 1000, "src": "deposit", "dep_of": dep_id, "as": "Т"})
    eq("раньше дня внесения → 400", (код, отв["error"]), (400, "before_paid"))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 4000, "src": "deposit", "dep_of": dep_id, "as": "Т"})
    eq("больше, чем лежит → 409 с остатком", (код, отв["error"], отв["have"]), (409, "too_much", 3000))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 1000, "src": "deposit", "dep_of": dep_id,
                                                       "comment": "Возврат депозита · Орион", "as": "Т"})
    eq("вернули 1000", код, 200)
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    # РП меньше на 100: выше записана зарплата 100 (проверка, что вид не берёт отметку)
    eq("РП 17900, лежит 2000, приход 1000", (d["stack_rp"], d["dep"], d["extra_rp"], d["dep_back"]), (17900, 2000, 1000, 1000))
    x = отв["book"]["deposits"][0]
    eq("в списке: вернули 1000, осталось 2000", (x["back"], x["left"], x["open"]), (1000, 2000, True))
    econ_before = отв["book"]["econ"]
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 1500, "src": "deposit", "dep_of": dep_id,
                                                       "lost": 500, "as": "Т"})
    eq("вернули 1500, удержали 500", код, 200)
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("РП 19400, лежит 0, удержано 500", (d["stack_rp"], d["dep"], d["dep_lost"]), (19400, 0, 500))
    eq("прибыль по расчёту упала на удержанное", econ_before - отв["book"]["econ"], 500)
    x = отв["book"]["deposits"][0]
    eq("депозит закрыт: вернули 2500, удержали 500", (x["back"], x["lost"], x["left"], x["open"]), (2500, 500, 0, False))
    eq("у записи возврата видно, сколько удержали", [(e["src"], e["dep_of"] == dep_id, e["lost"]) for e in d["ins"]][-1], ("deposit", True, 500))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "in", "amount": 1, "src": "deposit", "dep_of": dep_id, "as": "Т"})
    eq("закрытый депозит больше не вернуть", (код, отв["have"]), (409, 0))

    print("— сервер: убрать записи")
    код, отв = await зови(fr.handle_entry_del, "DELETE", {"id": dep_id, "as": "Т"})
    eq("депозит с возвратами не убрать → 409", (код, отв["error"], отв["n"]), (409, "dep_used", 2))
    ret_id = [e["id"] for e in d["ins"] if e["src"] == "deposit"][-1]
    код, отв = await зови(fr.handle_entry_del, "DELETE", {"id": ret_id, "as": "Т"})
    eq("возврат убран — депозит снова открыт на 2000", (код, отв["book"]["deposits"][0]["left"], отв["book"]["safe"]["dep"]), (200, 2000, 2000))
    eq("нет данных не для книги: ни байтов, ни id телеграма", "driver_id" in str(отв["book"]["deposits"]), False)


asyncio.run(сервер())
print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
