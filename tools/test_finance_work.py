"""Кто когда на работе — и зарплата за дни на работе (владелец, 21 сен 2026:
«кто-то уезжает, кто-то приезжает, а зарплата у всех первого числа; человек
не должен приехать 15-го и получить как за целый месяц — он должен получить
пол зарплаты»; старшие Макар и Стас сменяют друг друга).

  • дни на работе в месяце: вышел 15 сентября — 16 из 30; уехал 20-го — 19
    (день отъезда не рабочий, владелец 24 сен 2026: «24 сентября — значит, он
    сегодня не выходит на смену, он уезжает»); два периода складываются; без
    периодов — весь месяц, как было;
  • на работе ли сегодня, с какого числа, какой по счёту день; уехал — когда
    и когда вернётся;
  • правка периодов: вышел, уехал, поправить, убрать; нельзя: выйти, не
    уехав, уехать раньше, чем вышел, наложить периоды друг на друга;
  • оклад в месяц — за дни на работе (и в долларах по курсу), премия в месяц
    — той же долей; ставка в день — по сменам, периоды её не трогают;
  • настоящий сервер (mongomock): ручка pay/work, ведомость, бюджет «Зарплаты»
    и карточка водителя считают одинаково; Макар и Стас по очереди получают
    каждый за свои дни, а весь фонд — ровно один оклад.
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, finance_pay as pay, finance_routes as fr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

M = "2026-09"


def голый(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def ручка(body):
    r = make_mocked_request("POST", "/api/owner/finance/book/pay/work")
    r["owner_id"] = 1
    r._read_bytes = json.dumps({**body, "month": M, "as": "Тест"}).encode()
    resp = await голый(fr.handle_pay_work)(r)
    return resp.status, json.loads(resp.text)


async def main():
    print("── дни на работе в месяце ─────────────────────────────────────")
    eq("без периодов — весь месяц", pay.work_in([], M), dict(days=30, of=30, share=1.0, spans=[], set=False))
    w = pay.work_in([{"from": "2026-09-15", "to": ""}], M)
    eq("вышел 15 сентября — 16 из 30", (w["days"], w["of"], w["spans"]), (16, 30, [["2026-09-15", "2026-09-30"]]))
    eq("в октябре — весь месяц", pay.work_in([{"from": "2026-09-15", "to": ""}], "2026-10")["days"], 31)
    eq("уехал 20-го (начало не отмечали) — 19 дней: 20-е он уже не работает",
       pay.work_in([{"from": "", "to": "2026-09-20"}], M)["days"], 19)
    eq("и в октябре уже ноль", pay.work_in([{"from": "", "to": "2026-09-20"}], "2026-10")["days"], 0)
    eq("два периода в месяце складываются",
       pay.work_in([{"from": "2026-09-01", "to": "2026-09-10"}, {"from": "2026-09-21", "to": ""}], M)["days"], 19)
    eq("пересменка день в день: уехал 16-го, второй вышел 16-го — месяц целиком",
       (pay.work_in([{"from": "2026-09-01", "to": "2026-09-16"}], M)["days"]
        + pay.work_in([{"from": "2026-09-16", "to": ""}], M)["days"]), 30)
    eq("и это не наложение периодов",
       pay.work_error(pay.work_clean([{"from": "2026-09-01", "to": "2026-09-16"},
                                      {"from": "2026-09-16", "to": ""}])), "")
    eq("а вот заход на день раньше — наложение",
       pay.work_error(pay.work_clean([{"from": "2026-09-01", "to": "2026-09-16"},
                                      {"from": "2026-09-15", "to": ""}])), "overlap")
    eq("февраль високосного года — 29 дней", pay.work_in([], "2028-02")["of"], 29)

    print("── на работе ли сегодня ───────────────────────────────────────")
    W = [{"from": "2026-08-01", "to": "2026-08-31"}, {"from": "2026-09-15", "to": ""}]
    eq("работает: с какого числа и какой по счёту день",
       {k: pay.work_now(W, "2026-09-21")[k] for k in ("on", "since", "day_n")},
       {"on": True, "since": "2026-09-15", "day_n": 7})
    eq("первый день — первый", pay.work_now(W, "2026-09-15")["day_n"], 1)
    x = pay.work_now(W, "2026-09-05")
    eq("между периодами: когда уехал и когда вернётся", (x["on"], x["left"], x["back"]),
       (False, "2026-08-31", "2026-09-15"))
    eq("без периодов — считается на работе", pay.work_now([], "2026-09-21")["on"], True)

    print("── правка периодов ────────────────────────────────────────────")
    w, e = pay.work_apply([], "start", "2026-09-15")
    eq("вышел на работу", (w, e), ([{"from": "2026-09-15", "to": ""}], ""))
    eq("выйти второй раз, не уехав, нельзя", pay.work_apply(w, "start", "2026-09-20")[1], "already_on")
    eq("уехать раньше, чем вышел, нельзя", pay.work_apply(w, "end", "2026-09-10")[1], "end_before_start")
    w, e = pay.work_apply(w, "end", "2026-10-20")
    eq("уехал", (w, e), ([{"from": "2026-09-15", "to": "2026-10-20"}], ""))
    eq("уехать, уже уехав, нельзя", pay.work_apply(w, "end", "2026-10-25")[1], "not_on")
    eq("выйти снова внутри прошлого периода нельзя", pay.work_apply(w, "start", "2026-10-01")[1], "overlap")
    w2, e = pay.work_apply(w, "start", "2026-12-01")
    eq("вернулся в декабре — второй период", (len(w2), e), (2, ""))
    eq("поправить так, чтобы налезло, нельзя",
       pay.work_apply(w2, "set", i=1, a="2026-10-10", b="")[1], "overlap")
    eq("поправить дату выхода", pay.work_apply(w2, "set", i=0, a="2026-09-16", b="2026-10-20")[0][0]["from"], "2026-09-16")
    eq("убрать период", pay.work_apply(w2, "del", i=1)[0], w)
    eq("уехал, когда даты ещё не отмечали", pay.work_apply([], "end", "2026-09-20")[0], [{"from": "", "to": "2026-09-20"}])

    print("── начислено за дни на работе ─────────────────────────────────")
    def месяц(work, rate, unit="month", cur="AED", bonus=None, days_auto=0, usd=3.677):
        eff = dict(rate=rate, unit=unit, cur=cur, days=None, note="", rate_month=M, bonus=bonus)
        return pay.person_month({"name": "X", "role": "driver", "work": work}, M, eff, days_auto, [], [], usd)
    r = месяц([{"from": "2026-09-15", "to": ""}], 3000)
    eq("3 000 AED, вышел 15-го → 1 600", (r["accrued"], r["work_days"], r["month_days"]), (1600, 16, 30))
    eq("1 000 $, вышел 15-го → 1 961,07 AED по курсу 3,677 (книга хранит до копеек)",
       месяц([{"from": "2026-09-15", "to": ""}], 1000, cur="USD")["accrued"], 1961.07)
    r = месяц([{"from": "2026-09-15", "to": ""}], 3000, bonus=300)
    eq("премия в месяц той же долей, настройка прежняя", (r["plus"], r["bonus_due"], r["bonus_month"]), (160, 160, 300))
    eq("ставка в день — по сменам, периоды не трогают",
       месяц([{"from": "2026-09-15", "to": ""}], 100, unit="day", days_auto=10)["accrued"], 1000)
    eq("без периодов — полный оклад, как было", месяц([], 3000)["accrued"], 3000)
    eq("уехал до месяца — ноль", месяц([{"from": "", "to": "2026-08-31"}], 3000)["accrued"], 0)

    print("── настоящий сервер ───────────────────────────────────────────")
    db._db = AsyncMongoMockClient()["ambar_work"]
    fr._biz_day = lambda *a, **k: "2026-09-21"
    for n in ("Макар", "Стас"):
        await db.fin_person_set(n, {"role": "other", "manual": True, "created": f"2026-09-0{1 if n == 'Макар' else 2}"})
        await db.fin_pay_month_set(M, n, {"rate": 2500, "cur": "USD"})
    st, r = await ручка({"name": "Макар", "action": "end", "day": "2026-09-16"})
    eq("Макар уехал 16-го — последний его день 15-е", (st, r.get("ok")), (200, True))
    st, r = await ручка({"name": "Стас", "action": "start", "day": "2026-09-16"})
    eq("Стас вышел 16-го", (st, r.get("ok")), (200, True))
    st, bad = await ручка({"name": "Стас", "action": "start", "day": "2026-09-20"})
    eq("второй раз «вышел» — отказ", (st, bad.get("error")), (400, "already_on"))
    ppl = {p["name"]: p for p in r["book"]["pay"]["people"]}
    полный = round(2500 * r["book"]["pay"]["usd"], 2)     # курс месяца, как в книге
    eq("Макар — 15 дней, Стас — 15 дней",
       (ppl["Макар"]["work_days"], ppl["Стас"]["work_days"]), (15, 15))
    eq("каждому половина, вместе — ровно один оклад",
       (ppl["Макар"]["accrued"], ppl["Стас"]["accrued"], ppl["Макар"]["accrued"] + ppl["Стас"]["accrued"]),
       (полный / 2, полный / 2, полный))
    sal = {p["name"]: p for p in r["book"]["budget"]["salary"]["people"]}
    eq("бюджет «Зарплаты» — те же суммы, что ведомость",
       (sal["Макар"]["plan"], sal["Стас"]["plan"]), (ppl["Макар"]["accrued"], ppl["Стас"]["accrued"]))
    eq("и дни там же", (sal["Стас"]["work_days"], sal["Стас"]["month_days"], sal["Стас"]["work_set"]), (15, 30, True))
    card = await fr.person_card("Стас", M)
    eq("карточка человека: на работе с 16-го, шестой день",
       {k: card["work_now"][k] for k in ("on", "since", "day_n")}, {"on": True, "since": "2026-09-16", "day_n": 6})
    card = await fr.person_card("Макар", M)
    eq("Макар уехал — не на работе, в «уехал» стоит день отъезда",
       (card["work_now"]["on"], card["work_now"]["left"]), (False, "2026-09-16"))
    oct_ = await fr.pay_month("2026-10")
    op = {p["name"]: p for p in oct_["people"]}
    eq("в октябре: Стас весь месяц, Макар ноль", (op["Стас"]["accrued"], op["Макар"]["accrued"]), (полный, 0))
    st, r = await ручка({"name": "Стас", "action": "set", "i": 0, "from": "2026-09-16", "to": "2026-09-10"})
    eq("конец раньше начала — отказ", (st, r.get("error")), (400, "end_before_start"))
    st, r = await ручка({"name": "Стас", "action": "del", "i": 0})
    eq("убрали период — снова весь месяц", {p["name"]: p for p in r["book"]["pay"]["people"]}["Стас"]["accrued"], полный)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
