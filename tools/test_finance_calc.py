"""Ядро учёта против июльского отчёта старшего (Excel, 31 день).

Числа таблицы лежат в finance_july2026.json (сняты с листов «ЧП +»,
«РП + и −», «ЧП − и ИТОГ», «Баракуда»). Проверяем, что каждая формула
таблицы даёт то же, что compute(): всего ЧП, результат фонда, итог месяца,
итог / 2, остаток, «должно быть», разница с сейфом, сейф Б и долг Б по
каждому дню и на конец, продали / купили %, оплатили, баланс долга.
Запуск: python3 tools/test_finance_calc.py
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finance_calc import compute, carry_from

fx = json.load(open(os.path.join(os.path.dirname(__file__), 'finance_july2026.json')))
rows, ex = fx['rows'], fx['expected']

days = []
for r in rows:
    days.append(dict(
        day='2026-07-%02d' % r['day'], cash=r['handed'], spend=0,
        # в таблице прибыль дня писали руками, и «хвост» оседал где-то мимо
        # книги; у нас прибыль выводится, поэтому факт сдачи = раскладка
        handed_fact=r['aside'] + r['collected'] + r['np_plus'],
        ordered=r['ordered'], aside=r['aside'], collected=r['collected'],
        extra_rp=r['extra_rp'], pay_b=r['pay_b'], pay_b_extra=r['pay_b_extra'],
        expenses=[dict(amount=r['expenses'], comment='')] if r['expenses'] else [],
        payouts=[]))
opening = dict(safe_b_open=ex['b_safe_open'], debt_b_open=ex['b_debt_open'],
               carry_np=ex['carry'], storage=0, safe_np_fact=ex['safe_fact'],
               safe_b_fact=ex['b_safe_fact'])
res = compute(days, opening)

fails = []
def eq(name, got, want, tol=0.01):
    ok = (got is not None and want is not None and abs(float(got) - float(want)) <= tol)
    print(('  ok  ' if ok else 'FAIL ') + f'{name:<28} got {got!r:>14}  want {want!r}')
    if not ok: fails.append(name)

print('— «ЧП +»')
eq('Всего (ЧП по дням)', res['np']['days'], ex['np_total'])
eq('Расходы предприятия', res['np']['rp_result'], ex['rp_result'])
eq('ИТОГО месяц', res['np']['profit'], ex['profit'])
eq('Итого / 2', res['np']['half'], ex['half'])
print('— «РП + и −»')
eq('Всего расходы за месяц', res['rp']['expenses'], ex['rp_expenses'])
eq('Всего собрал за месяц', res['rp']['rp_in'], ex['rp_in'])
eq('ИТОГО месяц (фонд)', res['rp']['result'], ex['rp_res'])
print('— «ЧП − и ИТОГ»')
eq('ЧП месяц', res['np']['profit'], ex['npm_profit'])
eq('Расходы из ЧП', res['np']['payouts'], ex['payouts'])
eq('ЧП в остатке', res['np']['left'], ex['left'])
eq('Перенос', res['np']['carry'], ex['carry'])
eq('Всего должно быть', res['np']['should_be'], ex['should_be'])
eq('В сейфе (факт)', res['np']['safe_fact'], ex['safe_fact'])
eq('Разница', res['np']['diff'], ex['diff'])
print('— «Баракуда»')
for i, r in enumerate(rows):
    eq(f'сейф Б день {r["day"]}', res['days'][i]['safe_b'], r['safe_b'])
    eq(f'долг Б день {r["day"]}', res['days'][i]['debt_b'], r['debt_b'])
eq('Всего продали', res['b']['sold'], ex['b_sold'])
eq('Всего купили', res['b']['bought'], ex['b_bought'])
eq('Отложили всего', res['b']['aside'], ex['b_aside'])
eq('Оплаты из Б всего', res['b']['pay_safe'], ex['b_pay'])
eq('Добавил из ЧП/РП всего', res['b']['pay_extra'], ex['b_pay_extra'])
eq('Продали / купили %', res['b']['ratio'], round(ex['b_ratio'], 1), 0.05)
eq('Всего оплатили', res['b']['paid'], ex['b_paid'])
eq('Остаток долга Б', res['b']['debt_end'], ex['b_debt_end'])
eq('Сейф Б должно быть', res['b']['safe_end'], ex['b_safe_end'])
eq('Баланс долга', res['b']['balance'], ex['b_balance'])
eq('Сейф Б факт', res['b']['safe_fact'], ex['b_safe_fact'])
eq('Разница сейф Б', res['b']['diff'], ex['b_diff'])
print('— перенос в август')
c = carry_from(res)
eq('перенос ЧП = сейф факт', c['carry_np'], ex['safe_fact'])
eq('сейф Б открытие', c['safe_b_open'], ex['b_safe_fact'])
eq('долг Б открытие', c['debt_b_open'], ex['b_debt_end'])
print('— хвост (сдали по приложению − раскладка)')
eq('gap за месяц', res['totals']['gap'], 9963)
print()
print('FAILED:', fails) if fails else print('ALL OK — таблица и ядро считают одинаково')
sys.exit(1 if fails else 0)
