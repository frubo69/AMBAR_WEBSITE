"""Зарплаты — чистая математика, без базы и без сети.

Как считает старший в тетради («З/П», «Штрафы, авансы, долги»):
  начислено  = оклад за месяц (или ставка × дни) — в дирхамах или в долларах
               по курсу месяца;
  минус      = штрафы, авансы и долги — каждый со своим графиком: сколько
               снимать с зарплаты в месяц; не задано — снимаем сразу всё;
  плюс       = премии этого месяца;
  к выплате  = начислено + плюс − минус;
  выплачено  = записи «зарплата» из фонда за месяц;
  остаток    = к выплате − выплачено.

Аванс — те же деньги, что зарплата, только раньше: деньги выданы из фонда
сегодня (запись расхода), а с зарплаты снимутся с того месяца, который
указали: «за следующий месяц наперёд» — значит с следующего, и следующую
зарплату человек не получает.

Владелец, 19 сен 2026: «водители могут попросить зарплату наперёд авансом —
надо понимать, получил он зарплату наперёд или часть авансом; он должен
видеть, сколько получает в этом месяце с учётом премии и сколько в следующем
с учётом того, что часть или всю зарплату уже выплатили; премии настраиваемые
каждому свои». Поэтому у аванса есть смысл (mode): «часть зарплаты» этого
месяца (part), «зарплата наперёд» за следующий (ahead) или своя схема по
частям; математика та же — с какого месяца и по сколько снимать. А у человека,
как оклад, — своя премия в месяц (bonus, в дирхамах): действует с месяца,
в котором вписана, и дальше, пока её не поменяют; 0 — премии больше нет.
Разовые премии — записями «Премия», как и были.
"""
from __future__ import annotations

from finance_calc import _n, _i

# hold — «Свободное удержание»: сумма вручную, не штраф по листу и не выданные деньги
KINDS = {'fine': 'Штраф', 'advance': 'Аванс', 'loan': 'Долг', 'bonus': 'Премия', 'hold': 'Удержание'}
CASH_KINDS = ('advance', 'loan')            # деньги выданы на руки — расход фонда
MINUS_KINDS = ('fine', 'advance', 'loan', 'hold')   # снимаются с зарплаты
PENALTY_KINDS = ('fine', 'hold')   # «Штрафы и удержания»: пересматриваются и отменяются без стирания
PAY_KINDS = ('salary', 'advance', 'loan')  # записи фонда, которые считаются зарплатами
# Смысл аванса — для слов старшему и водителю; считается он одинаково.
ADVANCE_MODES = {'part': 'Аванс — часть зарплаты', 'ahead': 'Зарплата наперёд', 'custom': 'Аванс по частям'}
ROLES = ('other', 'senior', 'operator', 'driver')      # руководство первым, как в тетради
ROLE_T = {'other': 'Старшие', 'senior': 'Старший оператор', 'operator': 'Операторы', 'driver': 'Водители'}
UNITS = ('month', 'day')
CURS = ('AED', 'USD')


def next_month(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    return f"{y + 1:04d}-01" if mm == 12 else f"{y:04d}-{mm + 1:02d}"


def schedule(item: dict, month: str) -> dict:
    """Сколько удерживаем (или прибавляем) в этом месяце и сколько останется.
    from — месяц, с которого действует; per_month — шаг графика (0 — всё сразу)."""
    amount = _n(item.get('amount'))
    step = _n(item.get('per_month'))
    start = str(item.get('from') or '')[:7] or str(item.get('day') or '')[:7]
    if not start or month < start:
        return dict(due=0, before=_i(amount), after=_i(amount), active=False, done=False)
    left, m = amount, start
    while m < month and left > 0:
        left -= min(step, left) if step > 0 else left
        m = next_month(m)
    due = (min(step, left) if step > 0 else left) if left > 0 else 0.0
    return dict(due=_i(due), before=_i(left), after=_i(left - due),
                active=left > 0, done=left <= 0)


def effective(person: dict, month_docs: list) -> dict:
    """Ставка человека на месяц: последняя вписанная не позже этого месяца.
    Дни и заметка — только из записи самого месяца."""
    eff = dict(rate=None, unit='month', cur='AED', days=None, note='', rate_month='', bonus=None)
    for d in month_docs:                       # отсортированы по месяцу
        for k in ('rate', 'unit', 'cur', 'bonus'):
            if d.get(k) is not None and d.get(k) != '':
                eff[k] = d[k]
                if k == 'rate':
                    eff['rate_month'] = d.get('month') or ''
    last = month_docs[-1] if month_docs else {}
    eff['days'] = last.get('days') if last.get('month') == person.get('_month') else None
    eff['note'] = (last.get('note') or '') if last.get('month') == person.get('_month') else ''
    return eff


def person_month(p: dict, month: str, eff: dict, days_auto, items: list,
                 payouts: list, usd: float) -> dict:
    rate = _n(eff.get('rate'))
    unit = eff.get('unit') if eff.get('unit') in UNITS else 'month'
    cur = eff.get('cur') if eff.get('cur') in CURS else 'AED'
    rate_aed = rate * (usd if cur == 'USD' else 1.0)
    days = eff.get('days') if eff.get('days') is not None else days_auto
    accrued = rate_aed if unit == 'month' else rate_aed * _n(days)
    # Своя премия в месяц — как оклад: с месяца, где вписана, и дальше.
    bonus_month = max(0.0, _n(eff.get('bonus')))
    rows, plus, minus, debt = [], bonus_month, 0.0, 0.0
    parts = dict(bonus_once=0.0, fines=0.0, holds=0.0, advance=0.0, loan=0.0)
    for it in items:
        if it.get('cancelled_at'):             # отменённый штраф — только в истории
            continue
        s = schedule(it, month)
        k = it.get('kind')
        if k == 'bonus':
            plus += s['due']
            parts['bonus_once'] += s['due']
        else:
            minus += s['due']
            debt += s['after']
            parts['fines' if k == 'fine' else 'holds' if k == 'hold' else 'advance' if k == 'advance' else 'loan'] += s['due']
        rows.append(dict(id=it.get('_id'), kind=it.get('kind'),
                         t=(ADVANCE_MODES.get(it.get('mode') or '') if it.get('kind') == 'advance' else '')
                           or KINDS.get(it.get('kind'), ''), mode=it.get('mode') or '',
                         amount=_i(_n(it.get('amount'))), per_month=_i(_n(it.get('per_month'))),
                         start=str(it.get('from') or '')[:7], day=it.get('day') or '',
                         note=it.get('note') or '', reason=it.get('reason') or '',
                         entry=it.get('entry') or '', src=it.get('src') or '', **s))
    to_pay = accrued + plus - minus
    paid = sum(_n(e.get('amount')) for e in payouts)
    return dict(
        name=p.get('name'), role=p.get('role') or 'other', manual=bool(p.get('manual')),
        rate=None if eff.get('rate') is None else _i(rate), unit=unit, cur=cur,
        rate_month=eff.get('rate_month') or '', rate_aed=_i(rate_aed),
        days=_i(_n(days)), days_auto=_i(_n(days_auto)), days_set=eff.get('days') is not None,
        note=eff.get('note') or '', accrued=_i(accrued), plus=_i(plus), minus=_i(minus),
        bonus_month=_i(bonus_month), bonus_set=eff.get('bonus') is not None,
        **{k: _i(v) for k, v in parts.items()},
        minus_other=_i(parts['fines'] + parts['holds'] + parts['loan']),
        to_pay=_i(to_pay), paid=_i(paid), left=_i(to_pay - paid), debt=_i(debt),
        items=rows, payouts=payouts)


def payroll(people: list, month: str, month_docs: dict, days_auto: dict,
            items: list, payouts: dict, usd: float) -> dict:
    """people — [{name, role, manual}], month_docs — имя → записи месяцев (по
    порядку), days_auto — имя → дней по приложению, items — все удержания,
    payouts — имя → выплаты этого месяца."""
    rows, t = [], dict(accrued=0.0, plus=0.0, minus=0.0, to_pay=0.0, paid=0.0, left=0.0, debt=0.0)
    for p in people:
        docs = month_docs.get(p['name']) or []
        eff = effective({**p, '_month': month}, docs)
        its = [it for it in items if it.get('name') == p['name']]
        r = person_month(p, month, eff, days_auto.get(p['name'], 0), its,
                         payouts.get(p['name']) or [], usd)
        rows.append(r)
        for k in t:
            t[k] += _n(r[k])
    order = {r: i for i, r in enumerate(ROLES)}
    rows.sort(key=lambda r: (order.get(r['role'], 9), ))
    return dict(people=rows, totals={k: _i(v) for k, v in t.items()}, usd=usd,
                n=len(rows), roles=[dict(id=r, t=ROLE_T[r]) for r in ROLES])
