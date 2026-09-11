"""Учёт денег по книге старшего — чистая математика, без базы и без сети.

Источник логики — месячный отчёт старшего в Excel (четыре листа):
  «ЧП +»        — чистая прибыль по дням, всего, минус результат фонда РП,
                   итог месяца, итог / 2;
  «РП + и −»    — фонд расходов предприятия: собрал, доп. приход, расходы;
  «ЧП − и ИТОГ» — выплаты из прибыли и сверка сейфа в конце месяца;
  «Баракуда»    — поставщик: заказали, отложили, оплатили, сейф Б, долг Б.

Деньги за день раскладываются на три части:
  сдали  =  отложили (сейф Б → Баракуде)  +  собрал (фонд РП)  +  прибыль дня.
Прибыль дня здесь не вводится руками, а выводится из двух введённых частей —
так день всегда сходится, «хвост» невозможен по построению.

Все функции чистые: на вход дни и переносы, на выход те же числа, что
считала таблица. Тест на июльских данных — tools/test_finance_calc.py.
"""

from __future__ import annotations

DAY_MANUAL = ('handed_fact', 'aside', 'collected', 'extra_rp', 'pay', 'pay_b', 'pay_b_extra')
MONTH_MANUAL = ('safe_b_open', 'debt_b_open', 'carry_np', 'storage',
                'safe_np_fact', 'safe_b_fact')


def _n(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(v: float) -> float:
    """Целое там, где оно целое, иначе как есть — чтобы 1482.5 не стало 1482."""
    r = round(v, 2)
    return int(r) if r == int(r) else r


def compute(days: list[dict], opening: dict) -> dict:
    """days — по одному словарю на календарный день месяца (в порядке дат):
         day            'YYYY-MM-DD'
         gross          продали всего (все способы оплаты)
         cash           наличными
         spend          расходы водителей, вычтенные из наличных до сдачи
         handed         сдали всего по приложению (cash − spend, если не задано)
         handed_fact    сдали всего по факту пересчёта (None — как в приложении);
                        раскладка дня идёт от факта, разница с приложением — gap
         ordered        заказали у Баракуды (закупочные цены)
         ordered_extra  закупили на других базах (для справки)
         aside          отложили в сейф Б
         collected      собрал в фонд РП
         extra_rp       доп. приход в фонд РП (не из выручки)
         pay            оплатили Баракуде всего за день: сначала из стопки
                        Баракуды, остальное — из ЧП (делится само); если задано,
                        pay_b / pay_b_extra ниже не читаются
         pay_b          оплатили Баракуде из сейфа Б
         pay_b_extra    добавили оплату Баракуде из ЧП / РП
         expenses       [{amount, comment}] — расходы предприятия из фонда
         payouts        [{amount, who, comment}] — выплаты из чистой прибыли
       opening — переносы и факты месяца:
         safe_b_open, debt_b_open   — сейф Б и долг Б на начало месяца
         carry_np                   — остаток (перенос) прошлого месяца по ЧП
         storage                    — на хранении
         rp_open, np_open           — стопки РП и ЧП в сейфе на начало месяца
                                      (три стопки сейфа: Баракуда, РП, ЧП)
         safe_np_fact, safe_b_fact  — пересчитанные сейфы (None — не считали)
    """
    safe_b = _n(opening.get('safe_b_open'))
    debt_b = _n(opening.get('debt_b_open'))
    rp = 0.0          # фонд РП, накопительно с начала месяца
    np_acc = 0.0      # чистая прибыль, накопительно
    # три стопки сейфа: Баракуда (safe_b), РП+ − РП−, ЧП+ − ЧП−; с переносом
    rp_st = _n(opening.get('rp_open'))
    np_st = _n(opening.get('np_open'))
    out = []
    t = dict(gross=0.0, cash=0.0, spend=0.0, handed=0.0, ordered=0.0,
             ordered_extra=0.0, aside=0.0, collected=0.0, extra_rp=0.0,
             pay_b=0.0, pay_b_extra=0.0, expenses=0.0, np_plus=0.0,
             payouts=0.0, card=0.0, crypto=0.0, tips=0.0, base=0.0, gap=0.0, pending=0)
    for d in days:
        cash = _n(d.get('cash'))
        spend = _n(d.get('spend'))
        handed = _n(d['handed']) if d.get('handed') is not None else cash - spend
        fact = d.get('handed_fact')
        base = handed if fact is None else _n(fact)
        gap = handed - base
        aside = _n(d.get('aside'))
        collected = _n(d.get('collected'))
        extra_rp = _n(d.get('extra_rp'))
        pay_b = _n(d.get('pay_b'))
        pay_b_extra = _n(d.get('pay_b_extra'))
        if d.get('pay') is not None:
            # одна сумма оплаты: из стопки Баракуды, сколько в ней есть, остальное из ЧП
            pay_total = _n(d.get('pay'))
            pay_b = min(pay_total, max(0.0, safe_b + aside))
            pay_b_extra = pay_total - pay_b
        ordered = _n(d.get('ordered'))
        ordered_extra = _n(d.get('ordered_extra'))
        exp_sum = sum(_n(e.get('amount')) for e in (d.get('expenses') or []))
        pay_sum = sum(_n(e.get('amount')) for e in (d.get('payouts') or []))
        np_plus = base - aside - collected
        # книга «Баракуда»: сейф и долг — бегущие остатки
        safe_b = safe_b + aside - pay_b
        debt_b = debt_b + ordered - pay_b - pay_b_extra
        rp = rp + collected + extra_rp - exp_sum
        np_acc = np_acc + np_plus - pay_sum
        rp_st = rp_st + collected + extra_rp - exp_sum
        np_st = np_st + np_plus - pay_sum - pay_b_extra
        touched = any(d.get(k) is not None for k in DAY_MANUAL) \
            or bool(d.get('expenses')) or bool(d.get('payouts'))
        if d.get('pending'):
            t['pending'] += 1
        out.append(dict(
            day=d['day'], gross=_i(_n(d.get('gross'))), cash=_i(cash),
            card=_i(_n(d.get('card'))), crypto=_i(_n(d.get('crypto'))),
            tips=_i(_n(d.get('tips'))), spend=_i(spend), handed=_i(handed),
            handed_fact=None if fact is None else _i(_n(fact)), base=_i(base), gap=_i(gap),
            ordered=_i(ordered), ordered_extra=_i(ordered_extra),
            aside=_i(aside), collected=_i(collected), extra_rp=_i(extra_rp),
            pay_b=_i(pay_b), pay_b_extra=_i(pay_b_extra),
            expenses=d.get('expenses') or [], expenses_sum=_i(exp_sum),
            payouts=d.get('payouts') or [], payouts_sum=_i(pay_sum),
            np_plus=_i(np_plus), safe_b=_i(safe_b), debt_b=_i(debt_b),
            rp=_i(rp), np_acc=_i(np_acc), touched=touched,
            pay=_i(pay_b + pay_b_extra), ok=bool(d.get('ok')),
            stack_b=_i(safe_b), stack_rp=_i(rp_st), stack_np=_i(np_st), stack_total=_i(safe_b + rp_st + np_st),
            manual={k: d.get(k) for k in DAY_MANUAL},
        ))
        t['gross'] += _n(d.get('gross')); t['cash'] += cash; t['spend'] += spend
        t['card'] += _n(d.get('card')); t['crypto'] += _n(d.get('crypto'))
        t['tips'] += _n(d.get('tips'))
        t['handed'] += handed; t['base'] += base; t['gap'] += gap; t['ordered'] += ordered
        t['ordered_extra'] += ordered_extra; t['aside'] += aside
        t['collected'] += collected; t['extra_rp'] += extra_rp
        t['pay_b'] += pay_b; t['pay_b_extra'] += pay_b_extra
        t['expenses'] += exp_sum; t['np_plus'] += np_plus; t['payouts'] += pay_sum

    # «РП + и −»
    rp_in = t['collected'] + t['extra_rp']          # всего собрал за месяц
    rp_result = rp_in - t['expenses']               # ИТОГО месяц (фонд)
    # «ЧП +»
    profit = t['np_plus'] + rp_result               # ИТОГО месяц
    half = profit / 2                               # Итого / 2
    # «ЧП − и ИТОГ»
    np_left = profit - t['payouts']                 # Итого ЧП в остатке
    storage = _n(opening.get('storage'))
    carry_np = _n(opening.get('carry_np'))
    should_be = np_left + storage + carry_np        # Всего должно быть
    safe_np_fact = opening.get('safe_np_fact')
    safe_np_diff = None if safe_np_fact is None else _n(safe_np_fact) - should_be
    # «Баракуда» — итог
    sold = t['gross'] if t['gross'] else t['handed']
    bought = t['ordered']
    ratio = (bought / (sold / 100.0)) if sold else None
    paid = t['pay_b'] + t['pay_b_extra']
    safe_b_fact = opening.get('safe_b_fact')
    safe_b_diff = None if safe_b_fact is None else _n(safe_b_fact) - safe_b
    # Прибыль по расчёту, не по кассе: продали − купили у базы − расходы фонда
    # − расходы водителей. Приход в фонд сюда не входит: перевод с крипты уже
    # сидит в «продали», а возвращённый депозит — не доход. Закупки на других
    # базах тоже мимо: их оплату старший записывает расходом из фонда.
    econ = t['gross'] - t['ordered'] - t['expenses'] - t['spend']

    totals = {k: _i(v) for k, v in t.items()}
    safe = dict(open_b=_i(_n(opening.get('safe_b_open'))), open_rp=_i(_n(opening.get('rp_open'))),
                open_np=_i(_n(opening.get('np_open'))),
                b=_i(safe_b), rp=_i(rp_st), np=_i(np_st), total=_i(safe_b + rp_st + np_st),
                b_in=_i(t['aside']), b_out=_i(t['pay_b']),
                rp_in=_i(t['collected'] + t['extra_rp']), rp_out=_i(t['expenses']),
                np_in=_i(t['np_plus']), np_out=_i(t['payouts'] + t['pay_b_extra']),
                pending=int(t['pending']))
    return dict(
        days=out,
        totals=totals,
        rp=dict(collected=_i(t['collected']), extra=_i(t['extra_rp']),
                rp_in=_i(rp_in), expenses=_i(t['expenses']), result=_i(rp_result)),
        np=dict(days=_i(t['np_plus']), rp_result=_i(rp_result), profit=_i(profit),
                half=_i(half), payouts=_i(t['payouts']), left=_i(np_left),
                storage=_i(storage), carry=_i(carry_np), should_be=_i(should_be),
                safe_fact=None if safe_np_fact is None else _i(_n(safe_np_fact)),
                diff=None if safe_np_diff is None else _i(safe_np_diff)),
        b=dict(safe_open=_i(_n(opening.get('safe_b_open'))),
               debt_open=_i(_n(opening.get('debt_b_open'))),
               sold=_i(sold), bought=_i(bought),
               ratio=None if ratio is None else round(ratio, 1),
               paid=_i(paid), pay_safe=_i(t['pay_b']), pay_extra=_i(t['pay_b_extra']),
               aside=_i(t['aside']), debt_end=_i(debt_b), safe_end=_i(safe_b),
               balance=_i(safe_b - debt_b),
               safe_fact=None if safe_b_fact is None else _i(_n(safe_b_fact)),
               diff=None if safe_b_diff is None else _i(safe_b_diff)),
        econ=_i(econ),
        safe=safe,
    )


def carry_from(prev: dict | None) -> dict:
    """Что переезжает в следующий месяц из закрытого: по факту пересчёта,
    а если сейф не считали — по расчёту."""
    if not prev:
        return {}
    np_ = prev.get('np') or {}
    b = prev.get('b') or {}
    safe = prev.get('safe') or {}
    return dict(
        carry_np=np_['safe_fact'] if np_.get('safe_fact') is not None else np_.get('should_be'),
        safe_b_open=b['safe_fact'] if b.get('safe_fact') is not None else b.get('safe_end'),
        debt_b_open=b.get('debt_end'),
        rp_open=safe.get('rp'), np_open=safe.get('np'),
    )
