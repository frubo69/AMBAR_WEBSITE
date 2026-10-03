"""Деньги на случайных месяцах — короткий прогон в общем наборе тестов.

Полный прогон — `python3 tools/fuzz_finance.py` (см. там же, что проверяется);
здесь тот же код на небольшом числе месяцев и с постоянным зерном, чтобы
поломка в расчёте сейфа, в экранах ДДС или в ручках финансов всплывала при
каждом запуске тестов, а не раз в неделю.

Плюс проверка самого фаззера: испорченное ядро он обязан ловить.

    python3 tools/test_finance_fuzz.py
"""
import asyncio, importlib.util, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_finance as F                                          # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

r = random.Random(20261001)
books = []
for i in range(2500):
    days, op = F.месяц(r)
    b = F.проверить_ядро(days, op, f"#{i}"); F.свойства(r, days, op, f"#{i}")
    if b and len(books) < 400:
        books.append(F.для_экрана(b, days, op))
eq("ядро сейфа: 2 500 случайных месяцев сошлись со второй моделью", F.БЕДЫ[:2], [])
плохо, всего = F.экран(books)
eq("экраны ДДС дня, ДДС месяца и Барракуды сходятся с сейфом", (всего, плохо[:2]), (0, []))

снимки = []
for seed in (11, 12, 13, 14):
    снимки += asyncio.run(F.сервер(seed, 25)) or []
eq("настоящая книга и ручки: 4 прогона по 25 ходов", F.БЕДЫ[:2], [])
плохо, всего = F.экран(снимки)
eq("экраны на книгах сервера", (всего, len(снимки) > 10), (0, True))

print("── фаззер обязан ловить испорченный расчёт ────────────────────")
src = open(os.path.join(ROOT, "finance_calc.py"), encoding="utf-8").read()
настоящий = F.calc
for имя, a, b in (
    ("минус выручки снова уменьшает сейф", "np_plus = max(0.0, base) - aside - collected", "np_plus = base - aside - collected"),
    ("неподтверждённый день попал в сейф", "(0.0, 0.0, 0.0) if pending else (aside, collected, np_plus)", "(aside, collected, np_plus)"),
    ("оплата не делится по стопке", "pay_b = min(pay_total, max(0.0, safe_b + aside_c + mv_b))", "pay_b = pay_total"),
):
    assert src.count(a) == 1, имя
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("calc_mut", loader=None))
    exec(compile(src.replace(a, b), "calc_mut", "exec"), mod.__dict__)
    F.calc = mod; F.БЕДЫ.clear()
    rr = random.Random(5)
    for _ in range(300):
        days, op = F.месяц(rr)
        F.проверить_ядро(days, op, "m"); F.свойства(rr, days, op, "m")
        if F.БЕДЫ:
            break
    eq(f"пойман: {имя}", bool(F.БЕДЫ), True)
F.calc = настоящий; F.БЕДЫ.clear()

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
