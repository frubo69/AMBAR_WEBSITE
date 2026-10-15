"""«Учёт с» (fin_start) — чистый старт книги финансов (владелец, 14 окт 2026).

  • дни раньше дня старта: видны, но не ждут подтверждения, в стопки и итоги
    не входят; их расход по статье — только в факт бюджета;
  • подтвердить, оплатить Барракуде, вписать поле дня до старта — 409
    before_start; расход статьи записать можно, движение денег — нет;
  • вписанное ЧП на начало главнее переноса (сентябрь −60 200 → октябрь 0);
  • чай операторов (по каталогу) вычтен из выручки дня;
  • крипта: свободная считается с дня старта, пришедшее до — «до учёта»;
  • сбор выручки: перенос не раньше дня старта;
  • пересчёт сейфа по стопкам: разница с книгой на день пересчёта.

    python3 tools/test_fin_start.py
"""
import asyncio, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_finance as FF                                          # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

СЕГОДНЯ = "2026-10-15"
СТАРТ = "2026-10-14"


async def main():
    db, fr = await FF._сервер_готовь()
    import fin_start, crypto_book as cb, cash_math, owner_routes as own, wallet_routes as wr, pay_notify
    fr._biz_day = lambda *a, **k: СЕГОДНЯ
    own._biz_day = lambda *a, **k: СЕГОДНЯ if hasattr(own, "_biz_day") else None
    cash_math.tea_rates = lambda: {"gin": 50}           # чай операторов: 50 за бутылку джина
    wr._rate = lambda: 3.5
    async def _тихо(*a, **k): return None
    pay_notify.tell_safe = _тихо
    зови = FF._зови
    m = СЕГОДНЯ[:7]

    async def заказ(day, total, items=None, **kw):
        await db._db.orders.insert_one({"order_id": f"o{int(time.time()*1e6)%10**9}{day[-2:]}", "timestamp": f"{day}T12:00:00",
                                        "status": "delivered", "total": total, "tip": 0, "office_id": "jbr",
                                        "items": items or [], **kw})
    # до старта: 12 и 13 октября; после: 14 и 15
    await заказ("2026-10-12", 5000)
    await заказ("2026-10-13", 4000, items=[{"id": "gin", "qty": 2}])
    await заказ("2026-10-14", 3000, items=[{"id": "gin", "qty": 1}])
    await заказ("2026-10-15", 2000)
    # сентябрь без прихода, с зарплатой — перенос ЧП −500; октябрь вписан руками
    await db.fin_entry_add({"_id": "s1", "day": "2026-09-30", "book": "rp", "amount": 500, "comment": "Зарплата",
                            "who": "Али", "kind": "salary", "pay_month": "2026-09", "by": "т", "at": "t"})
    await db.fin_month_set(m, {"safe_b_open": 1000, "rp_open": 2000, "np_open": 0, "debt_b_open": 300, "norm": 100})

    print("— без дня старта")
    eq("учёт с —", await fin_start.get(), None)
    b = await fr.build(m, light=True)
    d12 = next(x for x in b["days"] if x["day"] == "2026-10-12")
    eq("12 окт ждёт подтверждения", d12["pending"], True)
    eq("перенос ЧП из сентября без явного… вписан 0 → carry 0", b["np"]["carry"], 0)
    eq("start_day в книге", b.get("start_day"), None)

    print("— учёт с 14 октября")
    st, r = await зови(fr.handle_start_set, "POST", {"day": "2026-10-20", "as": "Ст"})
    eq("будущий день — 400", st, 400)
    st, r = await зови(fr.handle_start_set, "POST", {"day": "2026-10-16", "as": "Ст"})
    eq("завтрашняя смена — можно (назначают накануне)", (st, r.get("day")), (200, "2026-10-16"))
    st, r = await зови(fr.handle_start_set, "POST", {"day": СТАРТ, "as": "Ст"})
    eq("ручка «Учёт с»", (st, r.get("day")), (200, СТАРТ))
    eq("настройка записана", await fin_start.get(), СТАРТ)
    eq("before()", (fin_start.before("2026-10-13", СТАРТ), fin_start.before("2026-10-14", СТАРТ)), (True, False))
    b = r["book"]
    eq("start_day в книге", b["start_day"], СТАРТ)
    d13 = next(x for x in b["days"] if x["day"] == "2026-10-13")
    d14 = next(x for x in b["days"] if x["day"] == "2026-10-14")
    eq("13 окт до учёта", (d13["before"], d13["pending"], d13["counted"]), (True, False, False))
    eq("13 окт выручка видна: 4000 − чай 100", d13["handed"], 3900)
    eq("13 окт раскладка пустая", (d13["aside"], d13["collected"], d13["np_plus"]), (0, 0, 0))
    eq("13 окт стопки = открытие", (d13["stack_b"], d13["stack_rp"], d13["stack_np"]), (1000, 2000, 0))
    eq("14 окт ждёт", (d14["before"], d14["pending"]), (False, True))
    eq("14 окт выручка: 3000 − чай 50", (d14["handed"], d14["tea"]), (2950, 50))
    eq("итоги месяца без дней до учёта", (b["totals"]["gross"], b["totals"]["cash"], b["totals"]["pending"]), (5000, 5000, 2))
    eq("долг Барракуде не трогали", d13["debt_b"], 300)

    print("— до старта деньгами не двигать")
    st, r = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-13", "aside": 100, "collected": 100, "as": "Ст"})
    eq("подтвердить 13 окт — 409", (st, r.get("error"), r.get("from")), (409, "before_start", СТАРТ))
    st, r = await зови(fr.handle_day_set, "POST", {"day": "2026-10-13", "field": "aside", "value": 5, "as": "Ст"})
    eq("поле дня 13 окт — 409", (st, r.get("error")), (409, "before_start"))
    st, r = await зови(fr.handle_day_set, "POST", {"day": "2026-10-13", "field": "note", "value": "х", "as": "Ст"})
    eq("заметка дня до учёта — можно", st, 200)
    st, r = await зови(fr.handle_pay_b, "POST", {"day": "2026-10-13", "pay": 100, "src": "np", "as": "Ст"})
    eq("оплата Барракуде 13 окт — 409", (st, r.get("error")), (409, "before_start"))
    st, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-13", "book": "in", "amount": 100, "comment": "х", "as": "Ст"})
    eq("приход в фонд 13 окт — 409", (st, r.get("error")), (409, "before_start"))
    st, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-13", "book": "rp", "amount": 700, "kind": "salary",
                                                     "who": "Али", "comment": "Зарплата", "as": "Ст"})
    eq("расход статьи 13 окт — можно (факт бюджета)", st, 200)
    b = r["book"]
    d13 = next(x for x in b["days"] if x["day"] == "2026-10-13")
    eq("запись видна в дне", [e["amount"] for e in d13["expenses"]], [700])
    eq("…но стопка РП не тронута", (d13["stack_rp"], d13["expenses_sum"]), (2000, 0))
    eq("…а в факт зарплат вошла", b["budget"]["salary"]["fact"], 700)
    st, r = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-14", "aside": 1000, "collected": 100, "as": "Ст"})
    eq("подтвердить 14 окт — можно", st, 200)
    d14 = next(x for x in r["book"]["days"] if x["day"] == "2026-10-14")
    eq("14 окт в стопках: Б 1000+1000, РП 2000+100, ЧП 2950−1100", (d14["stack_b"], d14["stack_rp"], d14["stack_np"]), (2000, 2100, 1850))

    print("— ЧП: вписанное главнее переноса")
    eq("carry_np = вписанный ЧП 0, не −500", (r["book"]["np"]["carry"], r["book"]["opening"]["carry_np"]), (0, 0))

    print("— пересчёт сейфа")
    st, r = await зови(fr.handle_month_set, "POST", {"month": m, "field": "safe_b_fact", "value": 1900, "as": "Ст"})
    f = r["book"]["safe_fact"]
    eq("пересчёт Б: 1900 против 2000 на сегодня", (f["b"], f["book_b"], f["diff_b"], f["day"]), (1900, 2000, -100, СЕГОДНЯ))
    eq("РП не считали", (f["rp"], f["diff_rp"]), (None, None))

    print("— крипта с дня старта")
    now_ms = int(time.time() * 1000)
    await db.setting_set("crypto_book", {"start_ts": cb._since_ms("2026-10-01"), "read_from": 0, "open_usdt": 100, "open_aed": 350,
                                         "rate": 3.5, "at": "t"})
    await db.crypto_move_add({"_id": "m1", "txid": "t1", "dir": "in", "usdt": 100, "aed": 350, "rate": 3.5,
                              "ts": cb._since_ms("2026-10-10") + 1000, "wallet": "W", "at": "t"})
    await db.crypto_move_add({"_id": "m2", "txid": "t2", "dir": "in", "usdt": 200, "aed": 700, "rate": 3.5,
                              "ts": cb._since_ms("2026-10-14") + 1000, "wallet": "W", "at": "t"})
    stt = await cb.state()
    eq("start_day книги крипты", stt["start_day"], СТАРТ)
    eq("свободная: только пришедшее с 14 окт, без открытия", (stt["open"], stt["inflow"], stt["free"]), (0.0, 700.0, 700.0))
    await fin_start.set_day(None, "т")
    stt = await cb.state()
    eq("снят старт — как раньше: открытие + всё", (stt["open"], stt["inflow"], stt["free"], stt["start_day"]), (350.0, 1050.0, 1400.0, "2026-10-01"))
    await fin_start.set_day(СТАРТ, "т")

    print("— крипта на счету РП на утро старта (остаток держателя)")
    st, r = await зови(fr.handle_month_set, "POST", {"month": m, "field": "rp_cr_open", "value": 9610.79, "as": "Ст"})
    eq("поле открытия принято", st, 200)
    stt = await cb.state()
    eq("счёт РП = открытие, свободная без него", (stt["open_rp"], stt["rp"], stt["free"]), (9610.79, 9610.79, 700.0))
    eq("по_дням держит открытие в день старта", stt["by_day"].get(СТАРТ), 9610.79)
    b = r["book"]
    eq("книга: РП криптой на начало = открытие", b["opening"]["rp_cr_open"], 9610.79)
    d15 = next(x for x in b["days"] if x["day"] == СЕГОДНЯ)
    eq("счёт РП криптой в дне — от открытия", d15["stack_rp_cr"], 9610.79)
    eq("свободная всё ещё предлагается в РП+ дня (14-е подтверждено с 0 криптой)", d15["collected_cr"], 700)
    st, r = await зови(fr.handle_month_set, "POST", {"month": m, "field": "rp_cr_open", "value": "", "as": "Ст"})
    eq("снято — счёт РП 0", (await cb.state())["rp"], 0.0)

    print("— сбор выручки")
    eq("перенос с дня старта", await own._cash_from(), СТАРТ)
    eq("дни назад от 16-го", own._cash_back_days("2026-10-16", СТАРТ), ["2026-10-15", "2026-10-14"])
    eq("дни назад от 14-го — пусто", own._cash_back_days(СТАРТ, СТАРТ), [])

    print()
    print("FAIL:", FAIL) if FAIL else print("ALL OK")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
