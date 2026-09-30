"""Выручка в минусе в сейф не ложится.

Владелец, 30 сен 2026: «пока там минус — пиши 0… деньги на расходы мы берём не
из сейфа». В начале дня водители уже потратили (мойка, бензин), а наличных ещё
не привезли: окошко выручки показывает минус. Сейф от этого не уменьшается.

    python3 tools/test_finance_negative_day.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import finance_calc as calc

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

opening = {"safe_b_open": 0, "rp_open": 500, "np_open": 1000}
book = calc.compute([
    dict(day="2026-09-29", cash=0, spend=760, handed=-760),                       # минус
    dict(day="2026-09-30", cash=5000, spend=200, handed=4800, aside=2000, collected=1500),
], opening)
d1, d2 = book["days"]
eq("выручка дня показана как есть", d1["base"], -760)
eq("в ЧП+ минус не идёт", d1["np_plus"], 0)
eq("сейф не уменьшился", (d1["stack_rp"], d1["stack_np"], d1["stack_total"]), (500, 1000, 1500))
eq("обычный день считается как раньше", (d2["np_plus"], d2["stack_total"]), (1300, 1500 + 4800))
eq("сейф месяца", book["safe"]["total"], 6300)
print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
