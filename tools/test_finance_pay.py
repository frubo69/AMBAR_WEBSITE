"""Зарплаты (finance_pay) — графики удержаний, ставки по месяцам, доллары.
Запуск: python3 tools/test_finance_pay.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import finance_pay as fp

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<60} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

print("— график удержания: 4040 по 1000 с августа")
it = dict(_id="x", name="А", kind="fine", amount=4040, per_month=1000, **{"from": "2026-08"})
eq("июль — ещё не действует", fp.schedule(it, "2026-07")["due"], 0)
eq("август 1000, останется 3040", (fp.schedule(it, "2026-08")["due"], fp.schedule(it, "2026-08")["after"]), (1000, 3040))
eq("декабрь — хвост 40", fp.schedule(it, "2026-12")["due"], 40)
eq("январь — закрыт", (fp.schedule(it, "2027-01")["due"], fp.schedule(it, "2027-01")["done"]), (0, True))
print("— аванс за следующий месяц наперёд: снимается весь сразу")
adv = dict(kind="advance", amount=3000, per_month=0, **{"from": "2026-10"}, day="2026-09-11")
eq("сентябрь 0 / октябрь 3000 / ноябрь 0", tuple(fp.schedule(adv, m)["due"] for m in ("2026-09", "2026-10", "2026-11")), (0, 3000, 0))
eq("без from — с месяца выдачи", fp.schedule(dict(kind="loan", amount=100, day="2026-09-11"), "2026-09")["due"], 100)
print("— ставка действует с месяца, в котором вписана")
docs = [dict(month="2026-07", name="А", rate=3000, unit="month", cur="AED"),
        dict(month="2026-09", name="А", rate=1000, unit="month", cur="USD", days=21, note="н")]
eff8 = fp.effective(dict(name="А", _month="2026-08"), [d for d in docs if d["month"] <= "2026-08"])
eff9 = fp.effective(dict(name="А", _month="2026-09"), docs)
eq("август: 3000 AED в месяц, дни не вписаны", (eff8["rate"], eff8["cur"], eff8["days"], eff8["rate_month"]), (3000, "AED", None, "2026-07"))
eq("сентябрь: 1000 $, дни 21, заметка", (eff9["rate"], eff9["cur"], eff9["days"], eff9["note"]), (1000, "USD", 21, "н"))
print("— месяц человека")
p = dict(name="Макар", role="senior")
r = fp.person_month(p, "2026-09", dict(rate=1750, unit="month", cur="USD", days=None), 21,
                    [dict(_id="1", kind="fine", amount=500, per_month=0, **{"from": "2026-09"}),
                     dict(_id="2", kind="bonus", amount=100, per_month=0, **{"from": "2026-09"})],
                    [dict(amount=5950)], 3.686)
eq("1750 $ × 3.686 = 6450.5; +100 −500 = 6050.5; выплачено 5950 → остаток 100.5",
   (r["rate_aed"], r["accrued"], r["plus"], r["minus"], r["to_pay"], r["paid"], r["left"]), (6450.5, 6450.5, 100, 500, 6050.5, 5950, 100.5))
eq("дней по приложению, не вписаны", (r["days"], r["days_set"]), (21, False))
r2 = fp.person_month(dict(name="В", role="driver"), "2026-09", dict(rate=150, unit="day", cur="AED", days=10), 25, [], [], 3.67)
eq("ставка в день × вписанные дни: 150 × 10", (r2["accrued"], r2["days"], r2["days_set"], r2["days_auto"]), (1500, 10, True, 25))
r3 = fp.person_month(dict(name="С", role="operator"), "2026-09", dict(rate=None), 0, [], [], 3.67)
eq("ставки нет — нули, rate None", (r3["rate"], r3["accrued"], r3["to_pay"]), (None, 0, 0))
print("— сводка: порядок по ролям и итоги")
res = fp.payroll([dict(name="В", role="driver"), dict(name="Макар", role="senior"), dict(name="У", role="operator")], "2026-09",
                 {"Макар": [dict(month="2026-09", name="Макар", rate=6000, unit="month", cur="AED")]},
                 {"В": 3}, [dict(_id="1", name="Макар", kind="advance", amount=1000, per_month=0, **{"from": "2026-09"})],
                 {"Макар": [dict(amount=2000)]}, 3.67)
eq("порядок: старший, оператор, водитель", [x["name"] for x in res["people"]], ["Макар", "У", "В"])
eq("итоги: начислено 6000, минус 1000, к выплате 5000, выплачено 2000, остаток 3000",
   tuple(res["totals"][k] for k in ("accrued", "minus", "to_pay", "paid", "left")), (6000, 1000, 5000, 2000, 3000))
print()
print("FAILED:", fails) if fails else print("ALL OK — зарплаты считаются")
sys.exit(1 if fails else 0)
