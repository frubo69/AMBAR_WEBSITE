"""Оплата Барракуде, когда в её стопке не хватает (владелец, 3 окт 2026):
недостающее — из ЧП, из РП или понемногу отовсюду; взятое из РП фонд
возвращает тем, что в РП+ собрано сверх нормы, и до возврата РП+ дня
предлагается с долгом. Плюс «Забрали из РП» — РП− без чека, с комментарием.

    python3 tools/test_finance_bpay.py
"""
import asyncio, os, sys
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


print("— ядро: откуда недостающее")
op = dict(safe_b_open=10000, rp_open=20000, np_open=5000, debt_b_open=50000)
b = calc.compute([день(1, pay=16000)], op)["days"][0]
eq("без источника — из ЧП, как было", (b["pay_b"], b["pay_b_extra"], b["pay_rp"], b["stack_np"], b["stack_rp"]), (10000, 6000, 0, -1000, 20000))
b = calc.compute([день(1, pay=16000, pay_src="np")], op)["days"][0]
eq("np — из ЧП", (b["pay_b_extra"], b["pay_rp"]), (6000, 0))
b = calc.compute([день(1, pay=16000, pay_src="rp")], op)["days"][0]
eq("rp — всё недостающее из РП", (b["pay_b"], b["pay_b_extra"], b["pay_rp"], b["stack_rp"], b["stack_np"], b["debt_b"]),
   (10000, 0, 6000, 14000, 5000, 34000))
b = calc.compute([день(1, pay=16000, pay_src="mix", pay_rp=2500)], op)["days"][0]
eq("mix — из РП вписанное, остальное из ЧП", (b["pay_b_extra"], b["pay_rp"], b["stack_rp"], b["stack_np"]), (3500, 2500, 17500, 1500))
b = calc.compute([день(1, pay=16000, pay_src="mix", pay_rp=9999)], op)["days"][0]
eq("mix — из РП не больше недостающего", (b["pay_b_extra"], b["pay_rp"]), (0, 6000))
b = calc.compute([день(1, pay=8000, pay_src="rp")], op)["days"][0]
eq("хватило в стопке — из РП ничего", (b["pay_b"], b["pay_rp"], b["pay"]), (8000, 0, 8000))
b = calc.compute([день(1, pay_b=4000, pay_b_extra=300, pay_rp=700)], op)["days"][0]
eq("явные поля: pay_rp своей суммой", (b["pay"], b["pay_rp"], b["stack_rp"], b["debt_b"]), (5000, 700, 19300, 45000))
r = calc.compute([день(1, pay=16000, pay_src="rp")], op)
eq("итоги месяца: оплачено, из РП, сейф", (r["b"]["paid"], r["b"]["pay_rp"], r["safe"]["rp_b"], r["rp"]["to_b"]), (16000, 6000, 6000, 6000))

print("— ядро: долг фонда и возврат сверх нормы")
days = [день(1, cash=30000, handed=30000, aside=15000, collected=9000, pay=25000, pay_src="rp"),   # стопка 10000+15000=25000: хватает
        день(2, cash=30000, handed=30000, aside=15000, collected=9000, pay=31000, pay_src="rp"),   # стопка 15000 → 16000 из РП
        день(3, cash=30000, handed=30000, aside=15000, collected=15000),                            # сверх нормы 6000 → возврат
        день(4, cash=30000, handed=30000, aside=15000, collected=9000, collected_cr=4000),          # крипта тоже считается: +4000
        день(5, cash=30000, handed=30000, aside=15000, collected=30000, pending=True),              # ждёт: не возвращает
        день(6, cash=30000, handed=30000, aside=15000, collected=20000)]                            # 11000 сверх, долг 6000 → 0
r = calc.compute(days, op)
eq("день 1: хватило, долга нет", (r["days"][0]["pay_rp"], r["days"][0]["rp_owed"]), (0, 0))
eq("день 2: 16000 из РП — долг 16000", (r["days"][1]["pay_rp"], r["days"][1]["rp_owed_before"], r["days"][1]["rp_owed"]), (16000, 16000, 16000))
eq("день 3: вернулось 6000", (r["days"][2]["rp_back"], r["days"][2]["rp_owed"]), (6000, 10000))
eq("день 4: крипта сверх нормы — вернулось 4000", (r["days"][3]["rp_back"], r["days"][3]["rp_owed"]), (4000, 6000))
eq("день 5: ждёт подтверждения — не возвращает", (r["days"][4]["rp_back"], r["days"][4]["rp_owed"]), (0, 6000))
eq("день 6: вернул остаток, не больше долга", (r["days"][5]["rp_back"], r["days"][5]["rp_owed"]), (6000, 0))
eq("месяц: отдано / вернулось / не вернулось", (r["safe"]["rp_b"], r["safe"]["rp_back"], r["safe"]["rp_owed"]), (16000, 16000, 0))
eq("РП: начало + приход − расход − Барракуде = наличные + крипта", r["safe"]["open_rp"] + r["safe"]["open_rp_cr"] + r["safe"]["rp_in"] - r["safe"]["rp_out"] - r["safe"]["rp_b"], r["safe"]["rp"] + r["safe"]["rp_cr"])
r2 = calc.compute(days[:2], op)
eq("долг переезжает в следующий месяц", calc.carry_from(r2)["rp_owed_open"], 16000)
r3 = calc.compute([день(1, cash=30000, handed=30000, aside=15000, collected=12000)], dict(op, rp_owed_open=5000))
eq("перенесённый долг возвращается", (r3["days"][0]["rp_owed_before"], r3["days"][0]["rp_back"], r3["days"][0]["rp_owed"], r3["safe"]["open_rp_owed"]), (5000, 3000, 2000, 5000))
r4 = calc.compute([dict(день(1, cash=30000, handed=30000, aside=15000, collected=15000, pay=31000, pay_src="rp"), norm=0)], op)
eq("без нормы возврат не считается", (r4["days"][0]["pay_rp"], r4["days"][0]["rp_back"], r4["days"][0]["rp_owed"]), (6000, 0, 6000))
r5 = calc.compute([день(1, cash=30000, handed=30000, aside=15000, collected=15000, pay=31000, pay_src="rp")], op)
eq("отдал и собрал сверх нормы в тот же день — вернулось сразу", (r5["days"][0]["pay_rp"], r5["days"][0]["rp_back"], r5["days"][0]["rp_owed"]), (6000, 6000, 0))


async def сервер():
    db, fr = await FF._сервер_готовь()
    import owner_auth
    owner_auth.install_alerter(None)
    СЕГ = FF.СЕГОДНЯ; МЕС = FF.МЕС
    зови = FF._зови
    # стопки на начало: Барракуда 10 000, РП 20 000, ЧП 3 000; норма 9 000
    for f, v in (("safe_b_open", 10000), ("rp_open", 20000), ("np_open", 3000), ("debt_b_open", 50000), ("norm", 9000)):
        await зови(fr.handle_month_set, "POST", {"month": МЕС, "field": f, "value": v, "as": "Т"})
    print("— сервер: ручка оплаты")
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 8000, "src": "", "as": "Т"})
    eq("хватило в стопке → из стопки, без источника", (код, отв["from_b"], отв["from_np"], отв["from_rp"]), (200, 8000, 0, 0))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 16000, "src": "np", "as": "Т"})
    eq("из ЧП больше, чем в ЧП → 409 с остатками", (код, отв["error"], отв["short"], отв["b"], отв["np"], отв["rp"]), (409, "not_enough", 6000, 10000, 3000, 20000))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 16000, "src": "rp", "as": "Т"})
    eq("из РП — хватает → записано", (код, отв["from_b"], отв["from_np"], отв["from_rp"]), (200, 10000, 0, 6000))
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("книга: из РП 6000, стопка РП 14000, долг фонда 6000", (d["pay_rp"], d["stack_rp"], d["rp_owed"], d["manual"]["pay_src"]), (6000, 14000, 6000, "rp"))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 16000, "src": "mix", "rp": 7000, "as": "Т"})
    eq("mix: из РП больше недостающего → 400", (код, отв["error"]), (400, "bad_split"))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 16000, "src": "mix", "rp": 3500, "as": "Т"})
    eq("mix: 3500 из РП, 2500 из ЧП", (код, отв["from_np"], отв["from_rp"]), (200, 2500, 3500))
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("книга mix: pay_rp вписан, стопки", (d["manual"]["pay_src"], d["manual"]["pay_rp"], d["stack_rp"], d["stack_np"]), ("mix", 3500, 16500, 500))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 40000, "src": "np", "as": "Т"})
    eq("ЧП не хватает → 409", (код, отв["error"]), (409, "not_enough"))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 40000, "src": "np", "force": True, "as": "Т"})
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("force: записано, ЧП в минусе", (код, отв["from_np"], d["stack_np"]), (200, 30000, -27000))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": None, "as": "Т"})
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("снять оплату", (код, d["pay"], d["manual"]["pay"], d["manual"]["pay_src"]), (200, 0, None, None))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": "2026-10-02", "pay": 5, "as": "Т"})
    eq("будущий день → 400", (код, отв["error"]), (400, "future"))
    код, отв = await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": 16000, "src": "rp", "as": "Т"})
    код, отв = await зови(fr.handle_day_set, "POST", {"day": СЕГ, "field": "pay", "value": 16000, "as": "Т"})
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    eq("старый путь (поле pay) сбрасывает источник — из ЧП", (код, d["pay_b_extra"], d["pay_rp"], d["manual"]["pay_src"]), (200, 6000, 0, None))

    print("— сервер: предложение РП+ с возвратом долга")
    await зови(fr.handle_pay_b, "POST", {"day": "2026-09-20", "pay": 16000, "src": "rp", "as": "Т"})
    await зови(fr.handle_pay_b, "POST", {"day": СЕГ, "pay": None, "as": "Т"})
    # выручка 21 сен — заказ наличными 30 000
    await db._db.orders.insert_one({"order_id": "B1", "timestamp": "2026-09-21T10:00:00", "status": "delivered",
                                    "total": 30000, "tip": 0, "office_id": "jvc"})
    book = await fr.build(МЕС)
    d20 = next(x for x in book["days"] if x["day"] == "2026-09-20"); d21 = next(x for x in book["days"] if x["day"] == "2026-09-21")
    eq("20 сен: 6000 из РП (стопка 10000), долг фонда 6000", (d20["pay_rp"], d20["rp_owed"]), (6000, 6000))
    eq("21 сен: ждёт; РП+ предложен нормой + долг (9000 + 6000), в пределах выручки − Барракуда", (d21["pending"], d21["aside"], d21["collected"], d21["collected_src"], d21["rp_back_plan"]),
       (True, 15000, 15000, "norm", 6000))
    await db._db.orders.insert_one({"order_id": "B2", "timestamp": "2026-09-22T10:00:00", "status": "delivered",
                                    "total": 20000, "tip": 0, "office_id": "jvc"})
    book = await fr.build(МЕС)
    d22 = next(x for x in book["days"] if x["day"] == "2026-09-22")
    eq("22 сен тоже ждёт: долг пока не вернулся — предложение снова с ним, в пределах 10 000", (d22["collected"], d22["rp_back_plan"]), (10000, 1000))
    код, отв = await зови(fr.handle_day_ok, "POST", {"day": "2026-09-21", "aside": 15000, "collected": 15000, "as": "Т"})
    d21 = next(x for x in отв["book"]["days"] if x["day"] == "2026-09-21"); d22 = next(x for x in отв["book"]["days"] if x["day"] == "2026-09-22")
    eq("21 сен подтверждён: вернулось 6000, долг закрыт", (d21["rp_back"], d21["rp_owed"]), (6000, 0))
    eq("22 сен: предложение снова просто норма", (d22["collected"], d22["rp_back_plan"]), (9000, 0))
    s = отв["book"]["safe"]
    eq("сейф месяца: отдано 6000, вернулось 6000, долг 0", (s["rp_b"], s["rp_back"], s["rp_owed"]), (6000, 6000, 0))

    print("— сервер: «Забрали из РП»")
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "rp", "amount": 1500, "kind": "take", "comment": "", "as": "Т"})
    eq("без комментария → 400", (код, отв["error"]), (400, "no_comment"))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "rp", "amount": 1500, "kind": "take", "comment": "на ремонт", "as": "Т"})
    eq("без чека и статьи — записано", код, 200)
    d = next(x for x in отв["book"]["days"] if x["day"] == СЕГ)
    e = next(x for x in d["expenses"] if x["kind"] == "take")
    eq("в книге: вид и подпись", (e["kind_t"], e["comment"], e["amount"]), ("Забрали из РП", "на ремонт", 1500))
    eq("РП− дня и стопка РП", (d["expenses_sum"], d["stack_rp"] == отв["book"]["safe"]["rp"]), (1500, True))
    eq("бюджет: вне плана, не зарплата", (отв["book"]["budget"]["off_plan"], отв["book"]["budget"]["salary"]["fact"]), (1500, 0))
    код, отв = await зови(fr.handle_entry_add, "POST", {"day": СЕГ, "book": "np", "amount": 10, "kind": "take", "comment": "x", "as": "Т"})
    eq("take только в РП", код, 400)


asyncio.run(сервер())
print("\nИТОГ: " + ("все прошли" if not FAIL else f"ПРОВАЛЫ: {FAIL}"))
sys.exit(1 if FAIL else 0)
