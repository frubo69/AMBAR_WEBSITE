"""Журнал кошелька по отчёту его владельца (владелец, 12 окт 2026: «всё, что он
загрузил отчётом, считай достоверным и окончательным; веди параллельно учёт
свой по транзакциям, но когда он загружает отчёт — оттуда бери ВСЕ
недостающие данные»).

mongomock + настоящие wallet_routes / wallet_ledger / crypto_book / finance_routes:
  • настоящий формат: листы месяцев с «ломаной» шапкой (над «№ · Дата · Вид»
    стоят итоги) и сводные «Заказы» / «Расходы» — дата, вид и USDT угадываются
    по строкам, остаток после операции берётся с листа месяца;
  • каждая строка — в журнал с дирхамами, курсом, остатком и видом; повторная
    загрузка ничего не задваивает, поправленное описание обновляется;
  • расходы с первого дня учёта — РП− «криптой» со статьёй по словам (билет →
    «Билеты», ENOC → «Бензин», SIM → «Пополнение»); непонятное — «Что-то ещё»
    с пометкой «неразобрано»; снятие наличных — Доп. РП+ «из крипты»;
    до начала учёта — только журнал;
  • уже вписанный рукой расход (тот же день, та же сумма, криптой) второй
    раз не пишется;
  • книга финансов: счёт РП не меняется, статья получает факт, наличные РП
    не тронуты; свободная крипта — от остатка по отчёту;
  • владелец разносит неразобранное фишкой статьи и может убрать из книги;
  • сеть молчит — отчёт всё равно ложится в журнал.

    python3 tools/test_wallet_ledger.py
"""
import asyncio, base64, io, json, os, sys
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
import fuzz_finance as FF                                          # noqa: E402

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

DUBAI = timezone(timedelta(hours=4))
МС = lambda d: int(d.timestamp() * 1000)
from wallet_report_fixture import ОПЕРАЦИИ, отчёт_xlsx                # noqa: E402


async def main():
    db, fr = await FF._сервер_готовь()
    import tron, wallet_routes as W, wallet_ledger as L, crypto_book as cb, pay_notify
    fr._biz_day = lambda *a, **k: "2026-10-05"
    W.TRON_RECEIVE_ADDRESS = "MAIN"
    async def _old(): return []
    W.old_addresses = _old
    W._rate = lambda: 3.5
    СЕТЬ = {"on": True, "bal": 3000.0}        # на адресе лежит много: до отчёта книга верит сети
    # переводы в сети: приходы клиентов (четыре из отчёта) и один, которого в отчёте ещё нет
    ПЕРЕВОДЫ = [
        {"txid": "a" * 64, "amount": 100.0, "in": True, "ts": МС(datetime(2026, 9, 28, 13, 0, 20, tzinfo=DUBAI)), "peer": "TXa"},
        {"txid": "b" * 64, "amount": 80.0, "in": True, "ts": МС(datetime(2026, 10, 2, 0, 56, 10, tzinfo=DUBAI)), "peer": "TXb"},
        {"txid": "c" * 64, "amount": 1000.0, "in": True, "ts": МС(datetime(2026, 10, 4, 6, 57, 5, tzinfo=DUBAI)), "peer": "TXc"},
        {"txid": "d" * 64, "amount": 2000.0, "in": True, "ts": МС(datetime(2026, 10, 4, 7, 0, 30, tzinfo=DUBAI)), "peer": "TXd"},
        {"txid": "e" * 64, "amount": 50.0, "in": True, "ts": МС(datetime(2026, 10, 5, 9, 0, 0, tzinfo=DUBAI)), "peer": "TXe"},
    ]
    async def transfers(addr, limit=200, pages=6, min_ts=0):
        return [t for t in ПЕРЕВОДЫ if t["ts"] >= min_ts] if СЕТЬ["on"] else None
    async def balance(addr): return {"usdt": СЕТЬ["bal"], "trx": 100} if СЕТЬ["on"] else None
    tron.get_transfers, tron.get_balance = transfers, balance
    async def _тихо(*a, **k): return None
    pay_notify.tell_safe = _тихо
    # книга крипты заведена 2 окт 01:01 (как в бою)
    await db.setting_set(cb.KEY, {"start_ts": МС(datetime(2026, 10, 2, 1, 1, tzinfo=DUBAI)),
                                  "read_from": МС(datetime(2026, 10, 2, 1, 1, tzinfo=DUBAI)),
                                  "open_usdt": 80.43203, "open_aed": 281.51, "rate": 3.5})
    await cb.sync()
    await db.setting_set(L.BOOK_KEY, {"on": True})      # запись в книгу включена владельцем
    # бюджет октября: статьи как в бою («Что-то ещё» книга заводит сама)
    зови = FF._зови
    for имя, kind, group in (("Билеты", "pool", ""), ("Бензин", "pool", ""), ("Визы", "pool", ""),
                             ("Хоз. нужды", "pool", "home"), ("Покупка", "pool", "sim"), ("Пополнение", "pool", "sim")):
        await зови(fr.handle_budget_set, "POST", {"month": "2026-10", "name": имя, "plan": 0, "due": 0, "kind": kind,
                                                 "group": group, "as": "Т"})
    статьи = {l["name"]: l["_id"] for l in await db.fin_budget_get("2026-10")}
    eq("статьи октября заведены", sorted(статьи), sorted(["Билеты", "Бензин", "Визы", "Хоз. нужды", "Покупка", "Пополнение", "Что-то ещё"]))
    # В РП 1 октября положили 1000 криптой (как в жизни: распределение дня), и
    # старший вписал рукой расход криптой — тот же билет 2 окт на 785.
    await зови(fr.handle_month_set, "POST", {"month": "2026-10", "field": "norm", "value": 1000, "as": "Т"})
    await db._db.orders.insert_one({"order_id": "o1", "timestamp": "2026-10-01T12:00:00", "status": "delivered",
                                    "total": 5000, "tip": 0, "office_id": "jvc"})
    b0 = await fr.build("2026-10", light=True)
    д = next(x for x in b0["days"] if x["day"] == "2026-10-01")
    код, _ = await зови(fr.handle_day_ok, "POST", {"day": "2026-10-01", "aside": д["aside"], "collected": 0,
                                                  "collected_cr": 1000, "as": "Т"})
    eq("1 окт подтверждён: в РП 1000 криптой", (код, (await cb.state())["rp"]), (200, 1000.0))
    код, r0 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-02", "book": "rp", "amount": 785, "comment": "Билет Диловар",
                                                      "line": статьи["Билеты"], "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    eq("расход рукой криптой записан", код, 200)
    hand1 = r0["id"]

    async def загрузить(raw, имя="crypto_wallet_report.xlsx"):
        r = FF.make_mocked_request("POST", "/api/owner/wallet/report") if hasattr(FF, "make_mocked_request") else None
        from aiohttp.test_utils import make_mocked_request
        r = make_mocked_request("POST", "/api/owner/wallet/report"); r["owner_id"] = 1
        r._read_bytes = json.dumps({"name": имя, "data": base64.b64encode(raw).decode(), "as": "Владелец"}).encode()
        h = W.handle_report
        while hasattr(h, "__wrapped__"): h = h.__wrapped__
        resp = await h(r)
        return resp.status, json.loads(resp.text)

    print("── выключатель: без него отчёт только в журнал ────────────────")
    await db.setting_set(L.BOOK_KEY, {"on": False})
    st, r = await загрузить(отчёт_xlsx(ОПЕРАЦИИ[:3]))
    eq("выключено: строки в журнале, в книгу ничего, остаток в книгу не идёт", (st, r["ledger"]["new"], r["ledger"]["booked"], r["ledger"].get("off"), (await cb.state())["src"]), (200, 3, 0, True, "chain"))
    await db._db.wallet_ledger.delete_many({}); await db._db.wallet_reports.delete_many({}); await db._db.wallet_purposes.delete_many({})
    await db.setting_set(L.BOOK_KEY, {"on": True})

    print("── разбор настоящего формата ──────────────────────────────────")
    rows = W._report_rows("r.xlsx", отчёт_xlsx(ОПЕРАЦИИ))
    eq("строк столько, сколько операций — листы схлопнулись", len(rows), len(ОПЕРАЦИИ))
    eq("у всех дата, дирхамы, курс и остаток", all(r["when"] and r["aed"] is not None and r["rate"] and r["balance"] is not None for r in rows), True)
    r3 = next(r for r in rows if r["desc"].startswith("Авиабилет"))
    eq("расход: USDT, −AED, остаток, вид", (r3["amount"], r3["aed"], r3["balance"], r3["op"], r3["way"]), (197.71, -692.0, -742.0, "Вывод USDT", True))
    eq("виды", sorted(L.kind(r["op"], r["desc"], r["aed"], r["way"]) for r in rows),
       sorted(["in", "out", "out", "in", "out", "sim", "cash", "out", "out", "in", "in"]))
    eq("день книги: 1 окт 00:39 — смена 30 сентября (граница 10:00), а не первый день учёта", L.book_day(r3["when"]), "2026-09-30")
    eq("день книги: 2 окт 00:56 — смена 1 октября", L.book_day(next(r for r in rows if r["aed"] == 280)["when"]), "2026-10-01")

    print("── загрузка: журнал, книга, статьи ────────────────────────────")
    st, r = await загрузить(отчёт_xlsx(ОПЕРАЦИИ))
    j = r.get("ledger") or {}
    eq("ответ: строк и назначено переводам", (st, r["rows"], r["matched"]), (200, 11, 4))
    eq("журнал: новых 11, в книгу 4, дубль 1, до учёта 2 (ENOC сентября и авиабилет в ночь на 1 окт)", (j["new"], j["booked"], j["dup"], j["history"]), (11, 4, 1, 2))
    eq("неразобранных 2 (подписка, такси)", j["unassigned"], 2)
    eq("остаток по отчёту — последняя строка", (j["balance"], j["balance_ts"] > 0), (6553.0, True))
    лента = await db.wallet_ledger_list()
    eq("в журнале все строки", len(лента), 11)
    sh = await L.shifts("2026-10")
    дни = {x["day"]: x for x in sh["days"]}
    eq("по сменам: месяц, приход клиентов = сумма строк «приход» октября",
       (sh["month"], sh["totals"]["in"], all(set(x) >= {"in", "orders", "diff", "out", "cash"} for x in sh["days"])),
       ("2026-10", round(sum(float(x["aed"]) for x in лента if x["kind"] == "in" and x["month"] == "2026-10"), 2), True))
    eq("по сменам: расход и выдача за день — положительными числами", all(x["out"] >= 0 and x["cash"] >= 0 for x in sh["days"]), True)
    по = {x["desc"]: x for x in лента}
    eq("ENOC сентября — история, в книгу не пошёл", по["VIP ENOC: баланс 400"]["book"], "history")
    eq("приход — не в книгу (его знает сеть), перевод найден", (по["Заказ"]["book"], по["Заказ"]["matched"], по["Заказ"]["txid"][:3]), ("", True, "aaa"))
    eq("билет 2 окт уже вписан рукой — дубль", по["Билет: Диловар. Душанбе - Дубай"]["book"], "dup:" + hand1)
    записи = {e["comment"]: e for e in await db.fin_entries_where({"wal": {"$exists": True}})}
    eq("авиабилет 1 окт 00:39 — смена 30 сентября, до учёта: в книгу не пошёл, в журнале сентябрь",
       (по["Авиабилет - Даврон. Ташкент - Абу Даби"]["book"], по["Авиабилет - Даврон. Ташкент - Абу Даби"]["day"],
        по["Авиабилет - Даврон. Ташкент - Абу Даби"]["month"], "Авиабилет - Даврон. Ташкент - Абу Даби" in записи), ("history", "2026-09-30", "2026-09", False))
    eq("SIM → «Пополнение»", записи["Пополнение номеров"]["line"], статьи["Пополнение"])
    eq("снятие наличных → Доп. РП+ из крипты, из свободной", (записи["Снятие наличных с банкомата"]["book"],
        записи["Снятие наличных с банкомата"]["src"], записи["Снятие наличных с банкомата"]["cr_free"]), ("in", "crypto", 2100.0))
    eq("подписка → «Что-то ещё», неразобрано", (записи["Подписка ChatGPT PLUS"]["line"], записи["Подписка ChatGPT PLUS"].get("wal_guess")),
       (статьи["Что-то ещё"], False))

    print("── книга финансов ─────────────────────────────────────────────")
    b = await fr.build("2026-10", light=True)
    д1 = next(x for x in b["days"] if x["day"] == "2026-10-01")
    д3 = next(x for x in b["days"] if x["day"] == "2026-10-03")
    д2 = next(x for x in b["days"] if x["day"] == "2026-10-02")
    eq("1 окт: ночной авиабилет — смена 30 сентября, расходов дня нет; крипта РП 1000, наличных РП 0",
       (д1["expenses_sum"], д1["expenses_cr_sum"], д1["stack_rp_cr"], д1["stack_rp"]), (0, 0, 1000, 0))
    eq("2 окт: рукой 785 с крипта-счёта РП и SIM 200 из свободной — крипта РП 215",
       (д2["expenses_cr_sum"], д2["stack_rp_cr"]), (985, 215))
    eq("3 окт: наличные из крипты 2100 в РП, расходы 400 криптой, крипта РП не тронута",
       (д3["extra_rp"], д3["expenses_cr_sum"], д3["stack_rp"], д3["stack_rp_cr"]), (2100, 400, 2100, 215))
    факт = {l["name"]: l["fact"] for l in b["budget"]["lines"]}
    eq("факт статей: Билеты 785 рукой, Пополнение 200, Что-то ещё 400", (факт["Билеты"], факт["Пополнение"], факт["Что-то ещё"]), (785, 200, 400))
    st_ = await cb.state()
    # остаток по отчёту 6553 + пришло после отчёта 50 USDT × 3.5 = 175
    eq("книга крипты: остаток от отчёта + приход после него", (st_["src"], st_["report"]["balance"], st_["report"]["inflow_after"], st_["wallet"]), ("report", 6553.0, 175.0, 6728.0))
    eq("свободная = на счёте − РП", (st_["free"], st_["free_fact"], st_["rp"]), (6513.0, 6513.0, 215.0))
    eq("книга месяца видит ту же свободную", b["crypto"]["free"], 6513.0)
    код, r1 = await зови(fr.handle_entry_add, "POST", {"day": "2026-10-03", "book": "rp", "amount": 300, "comment": "ещё",
                                                      "photo": FF.КАДР, "thumb": "", "pay": "crypto", "as": "Т"})
    eq("рукой криптой больше, чем на счёте РП, — отказ как раньше", (код, r1.get("error"), r1.get("have")), (409, "no_crypto", 215.0))

    print("── депозит из расхода отчёта (владелец, 20 окт) ───────────────")
    sim = записи["Пополнение номеров"]
    код, r = await зови(fr.handle_entry_split, "POST", {"id": sim["_id"], "amount": 50, "as": "Т"})
    eq("разделили: 200 → 150 и депозит 50", (код, r.get("ok")), (200, True))
    sim2 = await db.fin_entry_get(sim["_id"]); dep = await db.fin_entry_get(r["id"])
    eq("расход стал 150, крипта из свободной 150", (sim2["amount"], sim2["cr_free"]), (150, 150))
    eq("депозит: 50 тем же днём, статьёй, криптой из свободной, со связью с журналом",
       (dep["deposit"], dep["day"], dep["line"], dep["pay"], dep["cr_free"], dep["wal"], dep["split_of"], dep["at"]),
       (True, "2026-10-02", статьи["Пополнение"], "crypto", 50, sim["wal"], sim["_id"], sim["at"]))
    b = await fr.build("2026-10", light=True)
    д2 = next(x for x in b["days"] if x["day"] == "2026-10-02")
    eq("2 окт: расходы криптой те же 985, крипта РП те же 215, в депозитах 50",
       (д2["expenses_cr_sum"], д2["stack_rp_cr"], b["safe"]["dep"]), (985, 215, 50))
    факт = {l["name"]: l["fact"] for l in b["budget"]["lines"]}
    eq("факт «Пополнение» — 150: депозит не расход", факт["Пополнение"], 150)
    st_ = await cb.state()
    eq("книга крипты не шелохнулась", (st_["free"], st_["rp"], st_["wallet"]), (6513.0, 215.0, 6728.0))
    код, r = await зови(fr.handle_entry_split, "POST", {"id": sim["_id"], "amount": 150, "as": "Т"})
    eq("вся сумма депозитом — отказ", (код, r.get("error")), (400, "too_much"))
    код, r = await зови(fr.handle_entry_split, "POST", {"id": dep["_id"], "amount": 10, "as": "Т"})
    eq("депозит не делится", (код, r.get("error")), (400, "no_split"))
    код, r = await зови(fr.handle_entry_split, "POST", {"id": "нет", "amount": 10, "as": "Т"})
    eq("нет записи — 404", код, 404)
    код, r = await зови(fr.handle_entry_del, "DELETE", {"id": dep["_id"], "as": "Т"})
    sim3 = await db.fin_entry_get(sim["_id"])
    eq("убрали депозит — сумма вернулась в расход", (код, sim3["amount"], sim3["cr_free"]), (200, 200, 200))
    b = await fr.build("2026-10", light=True)
    eq("в депозитах снова 0, факт «Пополнение» 200", (b["safe"]["dep"], {l["name"]: l["fact"] for l in b["budget"]["lines"]}["Пополнение"]), (0, 200))

    print("── повторная загрузка — ничего не задваивается ─────────────────")
    st, r = await загрузить(отчёт_xlsx(ОПЕРАЦИИ, {8: "Подписка ChatGPT PLUS для оператора"}))
    j = r["ledger"]
    eq("новых 0, обновлено 11, в книгу 0", (j["new"], j["updated"], j["booked"]), (0, 11, 0))
    eq("записей из отчёта всё так же 4", len(await db.fin_entries_where({"wal": {"$exists": True}})), 4)
    лента = await db.wallet_ledger_list()
    eq("описание обновилось, связь с книгой осталась", [x for x in лента if "оператора" in x["desc"]][0]["book"].startswith("entry:"), True)

    print("── разбор неразобранного ──────────────────────────────────────")
    from aiohttp.test_utils import make_mocked_request
    async def ручка(h, body):
        rq = make_mocked_request("POST", "/x"); rq["owner_id"] = 1
        rq._read_bytes = json.dumps({**body, "as": "Владелец"}).encode()
        while hasattr(h, "__wrapped__"): h = h.__wrapped__
        resp = await h(rq)
        return resp.status, json.loads(resp.text)
    rq = make_mocked_request("GET", "/api/owner/wallet/ledger?month=2026-10"); rq["owner_id"] = 1
    h = W.handle_ledger
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    стр = json.loads((await h(rq)).text)
    eq("страница журнала: месяц по сменам (ночь на 1 окт — сентябрь), строки, неразобранные, статьи", (стр["month"], len(стр["rows"]), len(стр["unassigned"]), len(стр["lines"]) > 0), ("2026-10", 8, 2, True))
    eq("итоги месяца: приход 10780, расход 3485", (стр["totals"]["in"], стр["totals"]["out"]), (10780.0, 3485.0))
    ключ = next(x["key"] for x in стр["unassigned"] if x["desc"].startswith("Подписка"))
    st, r = await ручка(W.handle_ledger_assign, {"key": ключ, "line": статьи["Хоз. нужды"]})
    e = await db.fin_entry_get(записи["Подписка ChatGPT PLUS"]["_id"])
    eq("статья записана, пометка снята", (st, e["line"], e.get("wal_guess")), (200, статьи["Хоз. нужды"], True))
    st, r = await ручка(W.handle_ledger_assign, {"key": ключ, "line": "нет-такой"})
    eq("чужая статья — отказ", (st, r.get("error")), (400, "bad_line"))
    ключ2 = next(x["key"] for x in стр["unassigned"] if x["desc"].startswith("Такси"))
    st, r = await ручка(W.handle_ledger_assign, {"key": ключ2, "skip": True})
    eq("«не в книгу»: запись убрана, строка осталась", (st, await db.fin_entry_get(записи["Такси новым водителям"]["_id"]),
        (await db.wallet_ledger_get([ключ2]))[ключ2]["book"]), (200, None, "skip"))
    st, r = await загрузить(отчёт_xlsx(ОПЕРАЦИИ))
    eq("после повторной загрузки убранное не возвращается", (r["ledger"]["booked"], await db.fin_entry_get(записи["Такси новым водителям"]["_id"])), (0, None))
    eq("неразобранных не осталось", len(await L.unassigned()), 0)
    # запись убрали рукой через книгу — отчёт её не заводит снова
    await db.fin_entry_del(записи["Пополнение номеров"]["_id"])
    st, r = await загрузить(отчёт_xlsx(ОПЕРАЦИИ))
    eq("убранная рукой запись не возвращается", (r["ledger"]["booked"], (await db.wallet_ledger_list())[0] is not None), (0, True))
    eq("строка помечена «removed»", next(x["book"] for x in await db.wallet_ledger_list() if x["desc"] == "Пополнение номеров"), "removed")

    print("── сеть молчит — отчёт всё равно в журнале ───────────────────")
    СЕТЬ["on"] = False
    новые = ОПЕРАЦИИ + [[12, "05.10.2026 12:00", "Вывод USDT", "USDT", 142.86, 3.5, -500, 6053, "ENOC заправка"]]
    st, r = await загрузить(отчёт_xlsx(новые))
    eq("200, offline, новая строка в книге как «Бензин»", (st, r["offline"], r["ledger"]["new"], r["ledger"]["booked"]), (200, True, 1, 1))
    e = [x for x in await db.fin_entries_where({"wal": {"$exists": True}}) if x["comment"] == "ENOC заправка"][0]
    eq("статья «Бензин», день 5 окт", (e["line"], e["day"]), (статьи["Бензин"], "2026-10-05"))
    СЕТЬ["on"] = True
    st_ = await cb.state()
    # перевод 5 окт 09:00 в отчёте не значится, а остаток отчёта — на 12:00: отчёт главный, перевод в остаток не добавляем
    eq("остаток по отчёту сдвинулся на новую строку, свободная = остаток − РП", (st_["report"]["balance"], st_["wallet"], st_["free"]), (6053.0, 6053.0, 6053.0 - 215.0))

    print("── сводка на экране кошелька ──────────────────────────────────")
    W._drop_cache()
    d = await W._build()
    eq("сводка: остаток, месяц, строк", (d["ledger"]["balance"], d["ledger"]["month"], d["ledger"]["rows_n"]), (6053.0, "2026-10", 12))
    eq("книга в ответе кошелька: источник — отчёт, на счёте — по отчёту, адрес в сети не мера",
       (d["book"]["src"], d["book"]["wallet"], d["book"]["diff"], d["book"]["report"]["balance"]), ("report", 6053.0, 0.0, 6053.0))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
