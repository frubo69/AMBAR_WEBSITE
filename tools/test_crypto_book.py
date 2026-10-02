"""Крипта в распределении РП+ — примеры владельца слово в слово (2 окт 2026).

  • «на крипте 1000 дирхам, 8700 добавили наличкой — РП+ у нас теперь такой»;
  • «на следующий день пришло ещё 2к на кошелёк — предлагать уже не 3, а эти 2к»;
  • «выделено 1000, мы 500 вписали в РП− [криптой] — на балансе РП 500;
     на следующий день добавили 2000 из крипты — теперь в РП 2500»;
  • сколько крипты класть в РП+ — можно править, предлагается вся;
  • вывод крипты в наличные (Доп. РП+ «из крипты»): сперва из свободной,
    остальное — с крипта-счёта РП в наличные РП; свободной нет — всё с РП.

    python3 tools/test_crypto_book.py
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

МИР = {"today": "2026-10-02", "bal": {"MAIN": 0.0, "OLD": 0.0}, "tr": {"MAIN": [], "OLD": []}, "net": True, "n": 0}


async def готовь():
    db, fr = await FF._сервер_готовь()
    import tron, wallet_routes as wr, crypto_book as cb, pay_notify
    fr._biz_day = lambda *a, **k: МИР["today"]
    wr.TRON_RECEIVE_ADDRESS = "MAIN"
    async def _old(): return ["OLD"]
    wr.old_addresses = _old
    wr._rate = lambda: 3.5
    async def _bal(a): return {"usdt": МИР["bal"][a], "trx": 9.0} if МИР["net"] else None
    async def _tr(a, limit=200, pages=6, min_ts=0):
        return [t for t in МИР["tr"][a] if t["ts"] >= min_ts] if МИР["net"] else None
    tron.get_balance, tron.get_transfers = _bal, _tr
    async def _тихо(*a, **k): return None
    pay_notify.tell_safe = _тихо
    return db, fr, cb, wr


def пришло(aed, куда="MAIN", от="TClient"):
    """Клиент заплатил: aed наших дирхам = aed / 3.5 USDT."""
    МИР["n"] += 1
    usdt = round(aed / 3.5, 6)
    МИР["bal"][куда] = round(МИР["bal"][куда] + usdt, 6)
    МИР["tr"][куда].append({"txid": f"tx{МИР['n']:04d}", "amount": usdt, "in": True, "peer": от,
                           "ts": int(time.time() * 1000) + МИР["n"]})


async def main():
    db, fr, cb, wr = await готовь()
    зови = FF._зови
    async def книга(m="2026-10"): return await fr.build(m, light=True)
    def день(b, d): return next(x for x in b["days"] if x["day"] == d)
    async def выручка(d, сумма):
        МИР["n"] += 1
        await db._db.orders.insert_one({"order_id": f"o{МИР['n']}", "timestamp": f"{d}T12:00:00",
                                        "status": "delivered", "total": сумма, "tip": 0, "office_id": "jvc"})
    await зови(fr.handle_month_set, "POST", {"month": "2026-10", "field": "norm", "value": 9700, "as": "Т"})

    print("Книга заводится сама: открытие — что лежит на кошельке")
    МИР["bal"]["MAIN"] = 0.43203
    eq("до первого чтения сети книги нет — крипту не предлагаем", (await книга())["crypto"]["ready"], False)
    МИР["net"] = False
    eq("сеть молчит — не заводим наугад", (await cb.sync())["ok"], False)
    МИР["net"] = True
    await cb.sync()
    st = await cb.state()
    eq("открытие 0.43203 USDT = 1.51 AED, всё свободно", (st["ready"], st["open"], st["free"], st["rp"]), (True, 1.51, 1.51, 0.0))

    print("\nПример 1: на крипте 1000, наличкой 8700")
    await db.setting_set(cb.KEY, {**(await cb.cfg()), "open_usdt": 0.0, "open_aed": 0.0})   # для круглых чисел
    await выручка("2026-10-01", 40000)
    пришло(1000)
    await cb.sync()
    d = день(await книга(), "2026-10-01")
    eq("предложение: криптой 1000, наличкой 8700 (норма 9700)", (d["collected_cr"], d["collected"], d["cr_src"]), (1000.0, 8700.0, "free"))
    eq("Барракуде половина, ЧП+ — остаток наличных", (d["aside"], d["np_plus"]), (20000.0, 40000 - 20000 - 8700))
    код, _ = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-01", "aside": d["aside"], "collected": d["collected"],
                                                  "collected_cr": d["collected_cr"], "as": "Т"})
    b = await книга()
    eq("подтверждено: в РП наличными 8700, криптой 1000", (код, b["safe"]["rp"], b["safe"]["rp_cr"], b["safe"]["rp_all"]), (200, 8700, 1000, 9700))
    eq("сейф (наличные) крипту не включает", b["safe"]["total"], b["safe"]["b"] + b["safe"]["rp"] + b["safe"]["np"])
    eq("свободной крипты не осталось", (b["crypto"]["free"], b["crypto"]["rp"]), (0.0, 1000.0))

    print("\nНа следующий день пришло ещё 2000 — предлагать 2000, а не 3000")
    МИР["today"] = "2026-10-03"
    await выручка("2026-10-02", 30000)
    пришло(2000)
    await cb.sync()
    d = день(await книга(), "2026-10-02")
    eq("криптой 2000, наличкой 7700", (d["collected_cr"], d["collected"]), (2000.0, 7700.0))

    print("\nПример 2: из выделенной 1000 потратили 500 криптой")
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-02", "book": "rp", "amount": 500, "comment": "сим",
                                                     "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    b = await книга()
    eq("РП− криптой 500: крипта РП 500, наличные РП не тронуты", (код, b["safe"]["rp_cr"], b["safe"]["rp"]), (200, 500, 8700))
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-02", "book": "rp", "amount": 600, "comment": "ещё",
                                                     "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    eq("больше, чем есть в крипте РП, — отказ с остатком", (код, r.get("error"), r.get("have")), (409, "no_crypto", 500.0))
    код, _ = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-02", "book": "rp", "amount": 300, "comment": "нал",
                                                     "photo": FF.КАДР, "thumb": "", "as": "Т"})
    b = await книга()
    eq("расход наличными — из наличных РП, крипта на месте", (код, b["safe"]["rp"], b["safe"]["rp_cr"]), (200, 8400, 500))
    d = день(b, "2026-10-02")
    await зови(fr.handle_day_ok, "POST", {"day": "2026-10-02", "aside": d["aside"], "collected": d["collected"],
                                           "collected_cr": d["collected_cr"], "as": "Т"})
    b = await книга()
    eq("добавили 2000 из крипты — в РП криптой 2500", (b["safe"]["rp_cr"], b["crypto"]["rp"], b["crypto"]["free"]), (2500, 2500.0, 0.0))
    eq("наличные РП: 8700 − 300 + 7700", b["safe"]["rp"], 16100)

    print("\nСколько крипты класть — можно править; остаток ждёт следующего дня")
    МИР["today"] = "2026-10-04"
    await выручка("2026-10-03", 30000)
    пришло(2000)
    await cb.sync()
    код, _ = await зови(fr.handle_day_set, "POST", {"day": "2026-10-03", "field": "collected_cr", "value": 1500, "as": "Т"})
    d = день(await книга(), "2026-10-03")
    eq("вписали 1500: наличкой до нормы 8200", (код, d["collected_cr"], d["collected"], d["cr_src"]), (200, 1500.0, 8200.0, "manual"))
    код, r = await зови(fr.handle_day_set, "POST", {"day": "2026-10-03", "field": "collected_cr", "value": 2500, "as": "Т"})
    eq("больше свободной вписать нельзя", (код, r.get("error"), r.get("free")), (409, "no_free", 2000.0))
    код, r = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-03", "aside": d["aside"], "collected": d["collected"],
                                                  "collected_cr": 1500, "as": "Т"})
    b = await книга()
    eq("подтверждено 1500: в РП 4000, свободных 500", (код, b["crypto"]["rp"], b["crypto"]["free"]), (200, 4000.0, 500.0))
    МИР["today"] = "2026-10-05"
    await выручка("2026-10-04", 30000)
    пришло(1000)
    await cb.sync()
    d = день(await книга(), "2026-10-04")
    eq("наутро предлагается 500 оставшихся + 1000 новых", d["collected_cr"], 1500.0)

    print("\nВывод крипты в наличные: сперва свободная, остальное — с крипта-счёта РП")
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-04", "book": "in", "amount": 2000, "comment": "Из крипты",
                                                     "src": "crypto", "as": "Т"})
    b = await книга()
    eq("вывели 2000: из свободной 1500, с РП 500", (код, r.get("cr_free"), r.get("cr_rp")), (200, 1500.0, 500.0))
    eq("крипта РП 3500, свободной 0, наличные РП +2000", (b["crypto"]["rp"], b["crypto"]["free"], b["safe"]["rp"]), (3500.0, 0.0, 16100 + 8200 + 2000))
    eq("РП целиком вырос только на свободную часть", b["safe"]["rp_all"], 16100 + 8200 + 4000 + 1500)
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-04", "book": "in", "amount": 1000, "comment": "Из крипты",
                                                     "src": "crypto", "as": "Т"})
    eq("свободной нет — вся сумма с крипта-счёта РП в наличные", (код, r.get("cr_free"), r.get("cr_rp")), (200, 0.0, 1000.0))
    b = await книга()
    eq("РП целиком не изменился, крипта → наличные", (b["crypto"]["rp"], b["safe"]["rp_all"]), (2500.0, 16100 + 8200 + 4000 + 1500))
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-04", "book": "in", "amount": 2600, "comment": "Из крипты",
                                                     "src": "crypto", "as": "Т"})
    eq("больше, чем есть крипты вообще, — отказ", (код, r.get("error"), r.get("have")), (409, "no_crypto", 2500.0))

    print("\nЗадним числом счёт РП в минус не уходит ни в один день")
    код, _ = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-04", "book": "rp", "amount": 2400, "comment": "крупный",
                                                     "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    eq("4 окт потратили 2400 криптой — на счету РП осталось 100", (код, (await cb.state())["rp"]), (200, 100.0))
    МИР["today"] = "2026-10-06"
    await выручка("2026-10-05", 30000)
    пришло(3000)
    await cb.sync()
    d = день(await книга(), "2026-10-05")
    код, _ = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-05", "aside": d["aside"], "collected": d["collected"],
                                                  "collected_cr": 3000, "as": "Т"})
    eq("5 окт положили ещё 3000 — сейчас в РП криптой 3100", (код, (await cb.state())["rp"]), (200, 3100.0))
    код, r = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-04", "book": "rp", "amount": 600, "comment": "задним числом",
                                                     "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    eq("расход 600 датой 4 окт: сейчас хватает, а на конец 4 окт было 100 — отказ",
       (код, r.get("error"), r.get("have")), (409, "no_crypto", 100.0))
    код, r = await зови(fr.handle_day_set, "POST", {"day": "2026-10-01", "field": "collected_cr", "value": 100, "as": "Т"})
    eq("уменьшить уже потраченную раскладку нельзя", (код, r.get("error")), (409, "cr_spent"))
    b = await книга()
    eq("ни в один день крипта РП не в минусе", min(x["stack_rp_cr"] for x in b["days"]) >= 0, True)

    print("\nМежду своими кошельками — не приход; сверка дважды — не удвоение")
    было = (await cb.state())["free"]
    МИР["tr"]["OLD"].append({"txid": "txint", "amount": 100.0, "in": True, "peer": "MAIN", "ts": int(time.time() * 1000) + 999})
    await cb.sync(); await cb.sync()
    eq("свободная не изменилась", (await cb.state())["free"], было)

    print("\nКомиссия вывода: наличных пришло N, из крипты ушло N + наша половина")
    пришло(700); await cb.sync()
    st = await cb.state(); b0 = await книга()
    eq("перед выводом: свободных 700, на счёте РП 3100", (st["free"], st["rp"]), (700.0, 3100.0))
    код, r1 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-05", "book": "in", "amount": 500, "comment": "Из крипты",
                                                      "src": "crypto", "fee_usdt": 4, "as": "Т"})
    eq("вывели 500, комиссия 4 USDT = 14 AED, наша половина 7 — вся из свободной",
       (код, r1.get("cr_free"), r1.get("cr_rp"), r1.get("fee"), r1.get("fee_ours"), r1.get("fee_free")), (200, 500.0, 0.0, 14.0, 7.0, 7.0))
    st = await cb.state(); b = await книга()
    eq("из свободной ушло 507, счёт РП не тронут", (st["free"], st["rp"]), (193.0, 3100.0))
    eq("наличных в РП пришло ровно 500", b["safe"]["rp"] - b0["safe"]["rp"], 500)
    d5 = день(b, "2026-10-05")
    ком = [e for e in d5["expenses"] if e.get("fee_of")]
    eq("комиссия — своей строкой в РП−, «криптой», с пометкой вывода",
       [(e["amount"], e["pay"], e["comment"], e["fee_of"] == r1["id"]) for e in ком], [(7.0, "crypto", "Комиссия за вывод крипты", True)])
    # Комиссия оплачена свободной криптой, а не деньгами, уже лежавшими в РП:
    # в РП пришло 507 (500 наличными и 7 криптой), 7 тут же ушло расходом.
    eq("РП+ вырос на 507, РП− на 7, РП целиком — на 500",
       (round(b["safe"]["rp_in"] - b0["safe"]["rp_in"], 2), round(b["safe"]["rp_out"] - b0["safe"]["rp_out"], 2),
        round(b["safe"]["rp_all"] - b0["safe"]["rp_all"], 2)), (507, 7, 500))
    eq("сейф (наличные) комиссию не видит: +500", b["safe"]["total"] - b0["safe"]["total"], 500)
    eq("сохранение: открытие + пришло = свободно + РП + потрачено + выведено",
       round(st["open"] + st["inflow"], 2), round(st["free"] + st["rp"] + st["exp"] + st["wd_free"] + st["wd_rp"], 2))
    код, r2 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-05", "book": "in", "amount": 190, "comment": "Из крипты",
                                                      "src": "crypto", "fee_usdt": 4, "as": "Т"})
    eq("свободной на комиссию не хватило: 3 из свободной, 4 со счёта РП",
       (код, r2.get("cr_free"), r2.get("cr_rp"), r2.get("fee_ours"), r2.get("fee_free")), (200, 190.0, 0.0, 7.0, 3.0))
    st = await cb.state()
    eq("свободной 0, на счёте РП 3096", (st["free"], st["rp"]), (0.0, 3096.0))
    код, r3 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-05", "book": "in", "amount": 3095, "comment": "Из крипты",
                                                      "src": "crypto", "fee_usdt": 4, "as": "Т"})
    eq("с комиссией не хватает — отказ, хотя самих наличных хватило бы", (код, r3.get("error"), r3.get("have")), (409, "no_crypto", 3096.0))
    код, _ = await зови(fr.handle_entry_del, "DELETE", {"id": r2["fee_id"], "as": "Т"})
    st = await cb.state()
    d5 = день(await книга(), "2026-10-05")
    вывод2 = next(e for e in d5["ins"] if e["id"] == r2["id"])
    eq("убрали одну комиссию: вывод остался без неё, 3 вернулись в свободную, 4 — на счёт РП",
       (код, st["free"], st["rp"], вывод2["fee_ours"], вывод2["fee_free"]), (200, 3.0, 3100.0, 0, 0))
    код, _ = await зови(fr.handle_entry_del, "DELETE", {"id": r1["id"], "as": "Т"})
    st = await cb.state()
    d5 = день(await книга(), "2026-10-05")
    eq("убрали вывод — его комиссия ушла вместе с ним", (код, st["free"], st["rp"], [e for e in d5["expenses"] if e.get("fee_of")]),
       (200, 510.0, 3100.0, []))
    код, r4 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-05", "book": "in", "amount": 100, "comment": "Из крипты",
                                                      "src": "crypto", "as": "Т"})
    eq("без комиссии — как раньше", (код, r4.get("cr_free"), r4.get("fee_ours"), r4.get("fee_id")), (200, 100.0, None, ""))

    print("\nПеренос на следующий месяц")
    МИР["today"] = "2026-11-02"
    n = await книга("2026-11")
    o = await книга("2026-10")
    eq("крипта РП и наличные РП открывают ноябрь остатком октября",
       (n["safe"]["open_rp_cr"], n["safe"]["open_rp"]), (o["safe"]["rp_cr"], o["safe"]["rp"]))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
