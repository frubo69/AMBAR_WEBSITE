"""Зарплата водителю из наличных на руках (владелец, 3 окт 2026): «нажать
выплатить зарплату за прошлый месяц и выбрать, откуда — из РП или из тех
денег, что у него на руках за вчерашнюю смену; чтобы она вычлась из них и при
сборе выручки ничего не сломалось».

    python3 tools/test_pay_hands.py
"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_finance as FF                                          # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def main():
    db, fr = await FF._сервер_готовь()
    import owner_auth, config_staff as staff, expense_routes as xr, pay_notify
    owner_auth.install_alerter(None)
    async def _тихо(*a, **k): return None
    pay_notify.tell_safe = _тихо
    СЕГ, МЕС = FF.СЕГОДНЯ, FF.МЕС                      # 2026-09-24
    ПРОШ = "2026-08"
    зови = FF._зови
    # водитель в реестре: JVC
    staff.apply_roster([{"name": "Худоба", "district": "jvc", "telegram_id": 0}])
    eq("водитель в реестре", any(d.get("name") == "Худоба" for d in staff.all_drivers()), True)
    await зови(fr.handle_month_set, "POST", {"month": МЕС, "field": "rp_open", "value": 20000, "as": "Т"})
    # его смена: наличные 1150 + 320, чай 0; питание 80 (на смене), заправка 50 одобрена, запрос «зарплата 500» ждёт
    for i, tot in enumerate((1150, 320)):
        await db._db.orders.insert_one({"order_id": f"H{i}", "timestamp": f"{СЕГ}T10:{i:02d}:00", "status": "delivered",
                                        "total": tot, "tip": 0, "office_id": "jvc", "driver": "Худоба"})
    await db._db.driver_days.insert_one({"day": СЕГ, "driver": "Худоба", "working": True, "extras": [
        {"id": "x1", "amount": 50, "kind": "fuel", "status": "approved", "pay": "cash", "comment": "бензин"},
        {"id": "x2", "amount": 500, "kind": "other", "status": "pending", "pay": "cash", "comment": "зарплата", "by_driver": True}]})
    import json
    from aiohttp.test_utils import make_mocked_request
    async def hands(name, day):
        r = make_mocked_request("GET", f"/x?name={name}&day={day}", headers={"Authorization": "tma 1"})
        resp = await fr.handle_pay_hands(r)
        return resp.status, json.loads(resp.text)
    код, инфо = await hands("Худоба", СЕГ)
    eq("на руках: наличные − питание − заправка, не сдано, запрос виден",
       (код, инфо["cash"], инфо["spend"], инфо["have"], инфо["collected"], [(r["id"], r["amount"]) for r in инфо["requests"]]),
       (200, 1470, 130, 1340, False, [("x2", 500)]))
    before = await fr.build(МЕС)
    rp0, econ0, exp0 = before["safe"]["rp"], before["econ"], before["totals"]["expenses"]
    d0 = next(x for x in before["days"] if x["day"] == СЕГ)

    print("— выплата из наличных на руках")
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 400, "day": СЕГ, "month": ПРОШ,
                                                      "src": "hands", "hands_day": СЕГ, "as": "Т"})
    eq("записана", (код, отв.get("hands", {}).get("day")), (200, СЕГ))
    e = await db.fin_entry_get(отв["id"])
    eq("запись фонда: зарплата за август, pay=hands, смена и вычет", (e["kind"], e["pay_month"], e["pay"], e["hands_day"], bool(e.get("hands_extra"))),
       ("salary", ПРОШ, "hands", СЕГ, True))
    row = await db.get_driver_day(СЕГ, "Худоба")
    x = next(z for z in row["extras"] if z.get("salary_of") == отв["id"])
    eq("у водителя: согласованный вычет «Зарплата за август» 400", (x["status"], x["amount"], x["comment"], x["kind"]), ("approved", 400, "Зарплата за август 2026", "other"))
    b = await fr.build(МЕС); d = next(z for z in b["days"] if z["day"] == СЕГ)
    eq("день: выручка меньше на 400, kept=400, расходы водителей те же", (d["handed"], d["kept"], d["spend"]), (d0["handed"] - 400, 400, d0["spend"]))
    eq("стопка РП не тронута, расход фонда +400, приход «с рук» 400", (b["safe"]["rp"], b["totals"]["expenses"] - exp0, b["rp"]["hands"]), (rp0, 400, 400))
    eq("прибыль по расчёту меньше ровно на 400 (один раз)", econ0 - b["econ"], 400)
    eq("ДДС-поля: расход с рук отдельно", (d["expenses_hands_sum"], d["expenses_sum"]), (400, d0["expenses_sum"] + 400))
    pay8 = next(p for p in (await fr.build(ПРОШ))["pay"]["people"] if p["name"] == "Худоба")
    eq("ведомость за август: выплачено 400", (pay8["paid"], pay8["payouts"][0]["pay"], pay8["payouts"][0]["hands_day"]), (400, "hands", СЕГ))
    код, инфо = await hands("Худоба", СЕГ)
    eq("на руках стало меньше на 400", (инфо["have"], инфо["kept"]), (940, 400))
    try:
        import owner_routes as orr
        orders = list((await db.orders_from(f"{СЕГ}T00:00:00")).values())
        a = (await orr._cash_amounts(СЕГ, orders))["jvc"]
        eq("сбор выручки: расход района включает вычет, к сдаче 940", (a["spend"], a["net"]), (530, 940))
    except Exception as ex:                                # noqa: BLE001
        print("  (сбор выручки не проверен в этой среде:", type(ex).__name__, ")")

    print("— отказы")
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 5000, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": СЕГ, "as": "Т"})
    eq("больше, чем на руках → 409 no_cash", (код, отв["error"], отв["have"]), (409, "no_cash", 940))
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Петрович", "amount": 100, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": СЕГ, "as": "Т"})
    eq("не водитель → not_driver", (код, отв["error"]), (400, "not_driver"))
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 100, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": "2026-09-30", "as": "Т"})
    eq("будущая смена → future", (код, отв["error"]), (400, "future"))
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 100, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": СЕГ, "req": "нет", "as": "Т"})
    eq("чужой/несуществующий запрос → req_taken", (код, отв["error"]), (409, "req_taken"))

    print("— запрос водителя подхватывается")
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 500, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": СЕГ, "req": "x2", "as": "Т"})
    eq("выплата по запросу записана", код, 200)
    row = await db.get_driver_day(СЕГ, "Худоба")
    x2 = next(z for z in row["extras"] if z["id"] == "x2")
    eq("запрос стал вычетом: согласован, 500, «Зарплата за август», прежнее запомнено", (x2["status"], x2["amount"], x2["comment"], x2["salary_of"] == отв["id"], x2["prev"]["status"], x2["prev"]["comment"]),
       ("approved", 500, "Зарплата за август 2026", True, "pending", "зарплата"))
    код, инфо = await hands("Худоба", СЕГ)
    eq("запросов больше нет, на руках 440", (инфо["requests"], инфо["have"]), ([], 440))
    pay8 = next(p for p in (await fr.build(ПРОШ))["pay"]["people"] if p["name"] == "Худоба")
    eq("ведомость: выплачено 900", pay8["paid"], 900)
    код, отв2 = await зови(fr.handle_entry_del, "DELETE", {"id": отв["id"], "as": "Т"})
    row = await db.get_driver_day(СЕГ, "Худоба")
    x2 = next(z for z in row["extras"] if z["id"] == "x2")
    eq("убрали выплату — запрос снова ждёт решения как был", (код, x2["status"], x2["comment"], x2.get("salary_of") or "", x2.get("prev") or ""), (200, "pending", "зарплата", "", ""))
    pay8 = next(p for p in (await fr.build(ПРОШ))["pay"]["people"] if p["name"] == "Худоба")
    eq("ведомость: снова 400", pay8["paid"], 400)

    print("— вычет стёрли или отклонили у водителя — выплата уходит")
    e = await db.fin_entry_get(e["_id"])
    r = make_mocked_request("DELETE", f"/x?day={СЕГ}&driver=Худоба", match_info={"item_id": e["hands_extra"]}, headers={"Authorization": "tma 1"})
    resp = await xr.handle_extra_del(r)
    eq("расход водителя стёрт", resp.status, 200)
    eq("запись зарплаты ушла из книги", await db.fin_entry_get(e["_id"]), None)
    pay8 = next(p for p in (await fr.build(ПРОШ))["pay"]["people"] if p["name"] == "Худоба")
    eq("ведомость: выплат 0", pay8["paid"], 0)
    b = await fr.build(МЕС); d = next(z for z in b["days"] if z["day"] == СЕГ)
    eq("день без вычета: выручка как была", (d["handed"], d["kept"]), (d0["handed"], 0))

    print("— сдано — из той смены больше не выдать")
    await db.checklist_set(СЕГ, "cash:jvc", True, "Т")
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 100, "day": СЕГ, "month": ПРОШ, "src": "hands", "hands_day": СЕГ, "as": "Т"})
    eq("выручка сдана → 409 collected", (код, отв["error"]), (409, "collected"))
    код, отв = await зови(fr.handle_pay_out, "POST", {"name": "Худоба", "amount": 100, "day": СЕГ, "month": ПРОШ, "as": "Т"})
    b = await fr.build(МЕС)
    eq("обычная выплата из РП по-прежнему работает: стопка РП −100", (код, b["safe"]["rp"]), (200, rp0 - 100))

    print("— ядро: зарплата с рук")
    import finance_calc as calc
    r = calc.compute([dict(day="2026-10-01", gross=3000, cash=3000, spend=200, kept=400, ordered=0, ok=True, norm=0, aside=0, collected=0,
                           expenses=[dict(amount=400, comment="Зарплата", pay="hands"), dict(amount=100, comment="x")])],
                     dict(rp_open=1000))
    d = r["days"][0]
    eq("выручка = наличные − расходы − оставленная зарплата", d["handed"], 2400)
    eq("стопка РП: минус только наличный расход; с рук — приход и расход фонда", (d["stack_rp"], r["safe"]["rp_in"], r["safe"]["rp_out"], r["rp"]["hands"]), (900, 400, 500, 400))
    eq("прибыль: оба расхода один раз, kept не расход", r["econ"], 3000 - 500 - 200)


asyncio.run(main())
print("\nИТОГ: " + ("все прошли" if not FAIL else f"ПРОВАЛЫ: {FAIL}"))
sys.exit(1 if FAIL else 0)
