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

DAY_MANUAL = ('handed_fact', 'aside', 'collected', 'extra_rp', 'pay', 'pay_b', 'pay_b_extra',
              'pay_src', 'pay_rp')
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
         handed         сдали всего по приложению (cash − spend − kept, если не задано)
         kept           зарплата, которую водитель оставил себе из наличных смены
                        (владелец, 3 окт 2026): в сейф не доехала, в выручке дня
                        её нет; сама зарплата — записью расхода с pay='hands'
         handed_fact    сдали всего по факту пересчёта (None — как в приложении);
                        раскладка дня идёт от факта, разница с приложением — gap
         ordered        заказали у Баракуды (закупочные цены)
         ordered_extra  закупили на других базах (для справки)
         aside          отложили в сейф Б
         collected      собрал в фонд РП — НАЛИЧНЫМИ из выручки
         collected_cr   в фонд РП криптой с кошелька (со 2 окт 2026): в сейф
                        не ложится — лежит на счету РП крипта-частью
         extra_rp       доп. приход в фонд РП наличными (не из выручки); вывод
                        крипты в наличные тоже здесь, всей суммой
         cr_cash        сколько из вывода крипты взято с крипта-счёта РП: это
                        не новый приход, а перекладка внутри РП — крипта-часть
                        уменьшается на столько же, на сколько выросли наличные
         extra_cr       свободная крипта, пришедшая на крипта-счёт РП мимо
                        ежедневной раскладки: часть комиссии вывода, взятая из
                        свободной (тут же тратится расходом «криптой»)
         pay            оплатили Баракуде всего за день: сначала из стопки
                        Баракуды, недостающее — из ЧП и/или РП (делится само
                        по pay_src); если задано, pay_b / pay_b_extra ниже не
                        читаются
         pay_src        откуда взято недостающее сверх стопки Баракуды
                        (владелец, 3 окт 2026): '' или 'np' — из ЧП (как было),
                        'rp' — всё из РП, 'mix' — из РП pay_rp, остальное из ЧП
         pay_rp         при pay_src='mix' — сколько недостающего взято из РП;
                        при явных pay_b / pay_b_extra — оплата из РП своей суммой
         dep_back       вернулось депозитов (владелец, 4 окт 2026): возврат
                        идёт через Доп. РП+ и уже сидит в extra_rp; здесь —
                        сколько из него закрывает открытые депозиты
         dep_lost       удержано при возврате депозита: деньги не вернулись и
                        не вернутся — с этого момента это расход
                        (у записей расхода с отметкой deposit=True сама сумма
                        расходом не считается: она лежит у контрагента)
         norm           норма РП+ дня (из бюджета): по ней видно, сколько в
                        РП+ собрано СВЕРХ нормы — это возврат того, что фонд
                        отдал Баракуде (см. rp_owed)
         pay_b          оплатили Баракуде из сейфа Б
         pay_b_extra    добавили оплату Баракуде из ЧП
         expenses       [{amount, comment, pay, deposit, dep_use}] — расходы
                        предприятия из фонда; pay='crypto' — платили криптой:
                        вычитается из крипта-части РП, наличные сейфа не
                        трогает; pay='hands' — зарплата из наличных на руках у
                        водителя: фонд её получил (приход «с рук») и тут же
                        выдал, сейф не трогается, расход фонда — считается;
                        deposit=True — внесённый депозит/аванс: из фонда ушло,
                        но лежит у контрагента и расходом не считается;
                        dep_use — часть суммы, покрытая депозитом/авансом,
                        который уже лежал у контрагента (владелец, 5 окт
                        2026: «счёт на 3000, платим 2000 — из аванса 1000 он
                        себе забрал»): из фонда уходит только amount − dep_use,
                        расходом для прибыли считается вся сумма, депозит
                        уменьшается на dep_use
         payouts        [{amount, who, comment}] — выплаты из чистой прибыли
         moves          [{amount, from, to}] — переводы между стопками сейфа
                        (владелец, 3 окт 2026: «перевести со счёта РП на счёт
                        ЧП»): from/to ∈ b | rp | np; сейф целиком не меняют,
                        деньги переходят из стопки в стопку; идут до оплаты
                        Баракуде — переложенное в её стопку можно тут же платить
         pending        раскладка дня ещё не подтверждена старшим: aside /
                        collected / прибыль дня показываются как предложение,
                        но в стопки, итоги и остатки не входят
       opening — переносы и факты месяца:
         safe_b_open, debt_b_open   — сейф Б и долг Б на начало месяца
         carry_np                   — остаток (перенос) прошлого месяца по ЧП
         storage                    — на хранении
         rp_open, np_open           — стопки РП и ЧП в сейфе на начало месяца
                                      (три стопки сейфа: Баракуда, РП, ЧП)
         rp_cr_open                 — крипта на счету РП на начало месяца
         rp_owed_open               — сколько фонд РП отдал Баракуде и ещё не
                                      вернул на начало месяца
         safe_np_fact, safe_b_fact  — пересчитанные сейфы (None — не считали)
    """
    safe_b = _n(opening.get('safe_b_open'))
    debt_b = _n(opening.get('debt_b_open'))
    rp = 0.0          # фонд РП, накопительно с начала месяца
    np_acc = 0.0      # чистая прибыль, накопительно
    # три стопки сейфа: Баракуда (safe_b), РП+ − РП−, ЧП+ − ЧП−; с переносом
    rp_st = _n(opening.get('rp_open'))
    np_st = _n(opening.get('np_open'))
    # Крипта-часть РП: лежит на кошельке, а не в сейфе, но числится за фондом.
    rp_cr = _n(opening.get('rp_cr_open'))
    # Что фонд отдал Баракуде и ещё не вернул (владелец, 3 окт 2026: «в
    # последующем предлагать собрать больше на следующий день, пока не вернёмся
    # к тому РП, который по состоянию на этот день должен быть»). Возвращается
    # тем, что в РП+ собрано сверх нормы дня.
    owed = _n(opening.get('rp_owed_open'))
    out = []
    t = dict(gross=0.0, cash=0.0, spend=0.0, handed=0.0, ordered=0.0,
             ordered_extra=0.0, aside=0.0, collected=0.0, extra_rp=0.0,
             pay_b=0.0, pay_b_extra=0.0, expenses=0.0, np_plus=0.0,
             payouts=0.0, card=0.0, crypto=0.0, tips=0.0, base=0.0, gap=0.0, pending=0,
             collected_cr=0.0, expenses_cr=0.0, cr_cash=0.0, extra_cr=0.0, expenses_hands=0.0, kept=0.0,
             pay_rp=0.0, rp_back=0.0,
             mv_b_in=0.0, mv_b_out=0.0, mv_rp_in=0.0, mv_rp_out=0.0, mv_np_in=0.0, mv_np_out=0.0,
             dep_paid=0.0, dep_back=0.0, dep_lost=0.0, dep_used=0.0)
    # Депозиты (владелец, 4 окт 2026): внесённое из РП лежит у контрагента
    # (ренткар) и вернётся — это наши деньги, не расход. Открытые на начало
    # месяца переезжают из прошлого (dep_open).
    dep = _n(opening.get('dep_open'))
    for d in days:
        cash = _n(d.get('cash'))
        spend = _n(d.get('spend'))
        kept = _n(d.get('kept'))
        handed = _n(d['handed']) if d.get('handed') is not None else cash - spend - kept
        fact = d.get('handed_fact')
        base = handed if fact is None else _n(fact)
        gap = handed - base
        aside = _n(d.get('aside'))
        collected = _n(d.get('collected'))
        extra_rp = _n(d.get('extra_rp'))
        pay_b = _n(d.get('pay_b'))
        pay_b_extra = _n(d.get('pay_b_extra'))
        pay_rp = _n(d.get('pay_rp'))
        norm = _n(d.get('norm'))
        ordered = _n(d.get('ordered'))
        ordered_extra = _n(d.get('ordered_extra'))
        exps = d.get('expenses') or []
        # Списано из депозита/аванса (владелец, 5 окт 2026): часть суммы расхода,
        # покрытая деньгами, которые уже лежали у контрагента. Из фонда она
        # сегодня не уходит — ушла, когда вносили; расходом для прибыли
        # считается сейчас. У самого взноса (deposit) списания быть не может.
        def _use(e):
            return 0.0 if e.get('deposit') else max(0.0, min(_n(e.get('dep_use')), _n(e.get('amount'))))

        def _cash(e):
            return _n(e.get('amount')) - _use(e)
        dep_use = sum(_use(e) for e in exps)
        exp_full = sum(_n(e.get('amount')) for e in exps)       # расходы целиком, с покрытыми депозитом
        exp_sum = exp_full - dep_use                             # что фонд отдал сегодня
        exp_cr = sum(_cash(e) for e in exps if e.get('pay') == 'crypto')
        exp_hands = sum(_cash(e) for e in exps if e.get('pay') == 'hands')
        dep_paid = sum(_n(e.get('amount')) for e in exps if e.get('deposit'))
        dep_back = _n(d.get('dep_back'))
        dep_lost = _n(d.get('dep_lost'))
        dep = dep + dep_paid - dep_back - dep_lost - dep_use
        collected_cr = _n(d.get('collected_cr'))
        cr_cash = _n(d.get('cr_cash'))
        extra_cr = _n(d.get('extra_cr'))
        pay_sum = sum(_n(e.get('amount')) for e in (d.get('payouts') or []))
        # переводы между стопками: из какой ушло, в какую пришло
        mv_in = {'b': 0.0, 'rp': 0.0, 'np': 0.0}
        mv_out = {'b': 0.0, 'rp': 0.0, 'np': 0.0}
        for mv in (d.get('moves') or []):
            a = _n(mv.get('amount'))
            if mv.get('from') in mv_out:
                mv_out[mv['from']] += a
            if mv.get('to') in mv_in:
                mv_in[mv['to']] += a
        mv_b, mv_rp, mv_np = (mv_in['b'] - mv_out['b'], mv_in['rp'] - mv_out['rp'], mv_in['np'] - mv_out['np'])
        # Выручка в минусе в сейф не ложится (владелец, 30 сен 2026: «пока там
        # минус — пиши 0… деньги на расходы мы берём не из сейфа»). Минус в
        # окошке выручки значит, что водители потратили больше, чем пока
        # привезли наличными, — это их деньги на руках, а не дыра в сейфе.
        np_plus = max(0.0, base) - aside - collected
        pending = bool(d.get('pending'))
        # неподтверждённый день — только предложение: в деньгах его ещё нет
        aside_c, collected_c, np_c = (0.0, 0.0, 0.0) if pending else (aside, collected, np_plus)
        collected_cr_c = 0.0 if pending else collected_cr
        if d.get('pay') is not None:
            # одна сумма оплаты: из стопки Баракуды, сколько в ней есть (с учётом
            # подтверждённой раскладки этого дня); недостающее — из ЧП, из РП
            # или понемногу из обоих, как выбрал старший (pay_src)
            pay_total = _n(d.get('pay'))
            pay_b = min(pay_total, max(0.0, safe_b + aside_c + mv_b))
            short = pay_total - pay_b
            src = d.get('pay_src') or ''
            if src == 'rp':
                pay_rp = short
            elif src == 'mix':
                pay_rp = min(short, max(0.0, pay_rp))
            else:
                pay_rp = 0.0
            pay_b_extra = short - pay_rp
        # книга «Баракуда»: сейф и долг — бегущие остатки
        safe_b = safe_b + aside_c + mv_b - pay_b
        debt_b = debt_b + ordered - pay_b - pay_b_extra - pay_rp
        # Долг фонда: отданное Баракуде сегодня прибавляется, а собранное в РП+
        # сверх нормы (подтверждённого дня) его возвращает — в тот же день или
        # в следующие. Без нормы возврат не распознать: тогда он не считается.
        owed_before = owed + pay_rp
        above = max(0.0, collected_c + collected_cr_c - norm) if norm > 0 else 0.0
        rp_back = min(owed_before, above)
        owed = owed_before - rp_back
        # Фонд целиком (наличные + крипта): перекладка крипты в наличные его
        # не меняет, поэтому cr_cash вычитается из прихода. Отданное Баракуде
        # из фонда уходит; возврат уже сидит в collected.
        rp = rp + collected_c + collected_cr_c + extra_rp + extra_cr - cr_cash + exp_hands - exp_sum - pay_rp + mv_rp
        np_acc = np_acc + np_c - pay_sum
        # Наличная стопка РП — только то, что в сейфе; оплаченное криптой её
        # не трогает. Крипта-часть — рядом, своим счётом.
        rp_st = rp_st + collected_c + extra_rp - (exp_sum - exp_cr - exp_hands) - pay_rp + mv_rp
        rp_cr = rp_cr + collected_cr_c + extra_cr - exp_cr - cr_cash
        np_st = np_st + np_c - pay_sum - pay_b_extra + mv_np
        touched = any(d.get(k) is not None for k in DAY_MANUAL) \
            or bool(d.get('expenses')) or bool(d.get('payouts')) or bool(d.get('moves'))
        if d.get('pending'):
            t['pending'] += 1
        out.append(dict(
            day=d['day'], gross=_i(_n(d.get('gross'))), cash=_i(cash),
            card=_i(_n(d.get('card'))), crypto=_i(_n(d.get('crypto'))),
            tips=_i(_n(d.get('tips'))), spend=_i(spend), handed=_i(handed),
            handed_fact=None if fact is None else _i(_n(fact)), handed_src=d.get('handed_src') or '',
            base=_i(base), gap=_i(gap),
            ordered=_i(ordered), ordered_extra=_i(ordered_extra),
            aside=_i(aside), collected=_i(collected), extra_rp=_i(extra_rp),
            collected_cr=_i(collected_cr), cr_cash=_i(cr_cash), extra_cr=_i(extra_cr),
            expenses_cr_sum=_i(exp_cr), stack_rp_cr=_i(rp_cr),
            expenses_hands_sum=_i(exp_hands), kept=_i(kept),
            dep_paid=_i(dep_paid), dep_back=_i(dep_back), dep_lost=_i(dep_lost), dep=_i(dep),
            dep_used=_i(dep_use), expenses_full=_i(exp_full),
            pay_b=_i(pay_b), pay_b_extra=_i(pay_b_extra), pay_rp=_i(pay_rp),
            pay_src=d.get('pay_src') or '', norm=_i(norm),
            rp_owed_before=_i(owed_before), rp_back=_i(rp_back), rp_owed=_i(owed),
            expenses=d.get('expenses') or [], expenses_sum=_i(exp_sum),
            payouts=d.get('payouts') or [], payouts_sum=_i(pay_sum),
            moves=d.get('moves') or [],
            mv_b_in=_i(mv_in['b']), mv_b_out=_i(mv_out['b']), mv_rp_in=_i(mv_in['rp']), mv_rp_out=_i(mv_out['rp']),
            mv_np_in=_i(mv_in['np']), mv_np_out=_i(mv_out['np']),
            np_plus=_i(np_plus), safe_b=_i(safe_b), debt_b=_i(debt_b),
            rp=_i(rp), np_acc=_i(np_acc), touched=touched,
            pay=_i(pay_b + pay_b_extra + pay_rp), ok=bool(d.get('ok')), counted=not pending,
            stack_b=_i(safe_b), stack_rp=_i(rp_st), stack_np=_i(np_st), stack_total=_i(safe_b + rp_st + np_st),
            manual={k: d.get(k) for k in DAY_MANUAL},
        ))
        t['gross'] += _n(d.get('gross')); t['cash'] += cash; t['spend'] += spend
        t['card'] += _n(d.get('card')); t['crypto'] += _n(d.get('crypto'))
        t['tips'] += _n(d.get('tips'))
        t['handed'] += handed; t['base'] += base; t['gap'] += gap; t['ordered'] += ordered
        t['ordered_extra'] += ordered_extra; t['aside'] += aside_c
        t['collected'] += collected_c; t['extra_rp'] += extra_rp
        t['collected_cr'] += collected_cr_c; t['expenses_cr'] += exp_cr; t['cr_cash'] += cr_cash
        t['expenses_hands'] += exp_hands; t['kept'] += kept
        t['extra_cr'] += extra_cr
        t['dep_paid'] += dep_paid; t['dep_back'] += dep_back; t['dep_lost'] += dep_lost; t['dep_used'] += dep_use
        t['pay_b'] += pay_b; t['pay_b_extra'] += pay_b_extra; t['pay_rp'] += pay_rp; t['rp_back'] += rp_back
        t['expenses'] += exp_sum; t['np_plus'] += np_c; t['payouts'] += pay_sum
        for k in ('b', 'rp', 'np'):
            t[f'mv_{k}_in'] += mv_in[k]; t[f'mv_{k}_out'] += mv_out[k]

    # «РП + и −»
    # всего собрал за месяц — наличными и криптой; перекладка крипты в
    # наличные приходом не считается
    # зарплата с рук водителя — приход в фонд мимо сейфа (и тут же расход)
    rp_in = t['collected'] + t['collected_cr'] + t['extra_rp'] + t['extra_cr'] - t['cr_cash'] + t['expenses_hands']
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
    paid = t['pay_b'] + t['pay_b_extra'] + t['pay_rp']
    safe_b_fact = opening.get('safe_b_fact')
    safe_b_diff = None if safe_b_fact is None else _n(safe_b_fact) - safe_b
    # Прибыль по расчёту, не по кассе: продали − купили у базы − расходы фонда
    # − расходы водителей − чай (владелец, 20 сен 2026: «расходы — вообще все,
    # включая чай, расходы водителей, весь РП−, всё, что оттуда вычитается»).
    # Чай сидит внутри цены и уходит операторам и водителям — значит, расход.
    # Приход в фонд сюда не входит: перевод с крипты уже сидит в «продали», а
    # возвращённый депозит — не доход. Закупки на других базах тоже мимо: их
    # оплату старший записывает расходом из фонда.
    # Депозит — не расход: деньги наши и вернутся (владелец, 4 окт 2026);
    # расходом становится удержанное при возврате и списанное из депозита в
    # оплату (expenses — только то, что фонд отдал; покрытое депозитом — рядом).
    econ = (t['gross'] - t['ordered'] - (t['expenses'] - t['dep_paid'] + t['dep_lost'] + t['dep_used'])
            - t['spend'] - t['tips'])

    totals = {k: _i(v) for k, v in t.items()}
    safe = dict(open_b=_i(_n(opening.get('safe_b_open'))), open_rp=_i(_n(opening.get('rp_open'))),
                open_np=_i(_n(opening.get('np_open'))),
                b=_i(safe_b), rp=_i(rp_st), np=_i(np_st), total=_i(safe_b + rp_st + np_st),
                b_in=_i(t['aside']), b_out=_i(t['pay_b']),
                rp_in=_i(rp_in), rp_out=_i(t['expenses']),
                # крипта-часть РП: не в сейфе (total — только наличные), рядом
                open_rp_cr=_i(_n(opening.get('rp_cr_open'))), rp_cr=_i(rp_cr),
                rp_all=_i(rp_st + rp_cr),
                # отдано Баракуде из фонда за месяц, что из этого уже вернулось
                # собранным сверх нормы, и что фонду ещё должны (на конец)
                rp_b=_i(t['pay_rp']), rp_back=_i(t['rp_back']), rp_owed=_i(owed),
                open_rp_owed=_i(_n(opening.get('rp_owed_open'))),
                np_in=_i(t['np_plus']), np_out=_i(t['payouts'] + t['pay_b_extra']),
                # переводы между стопками за месяц: что в каждую пришло и ушло
                **{k: _i(t[k]) for k in ('mv_b_in', 'mv_b_out', 'mv_rp_in', 'mv_rp_out', 'mv_np_in', 'mv_np_out')},
                # депозиты: лежит у контрагентов на конец месяца, на начало,
                # и за месяц — внесли / вернулось / удержано
                dep=_i(dep), open_dep=_i(_n(opening.get('dep_open'))),
                dep_paid=_i(t['dep_paid']), dep_back=_i(t['dep_back']), dep_lost=_i(t['dep_lost']),
                dep_used=_i(t['dep_used']),
                pending=int(t['pending']))
    return dict(
        days=out,
        totals=totals,
        rp=dict(collected=_i(t['collected']), collected_cr=_i(t['collected_cr']),
                extra=_i(t['extra_rp']), cr_cash=_i(t['cr_cash']),
                rp_in=_i(rp_in), expenses=_i(t['expenses']), result=_i(rp_result),
                to_b=_i(t['pay_rp']), back=_i(t['rp_back']), owed=_i(owed), hands=_i(t['expenses_hands']),
                dep_paid=_i(t['dep_paid']), dep_back=_i(t['dep_back']), dep_lost=_i(t['dep_lost']),
                dep_used=_i(t['dep_used']), dep=_i(dep)),
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
               pay_rp=_i(t['pay_rp']),
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
        rp_owed_open=safe.get('rp_owed'),
        dep_open=safe.get('dep'),
    )
