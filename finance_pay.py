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
"""
from __future__ import annotations

from finance_calc import _n, _i

KINDS = {'fine': 'Штраф', 'advance': 'Аванс', 'loan': 'Долг', 'bonus': 'Премия'}
CASH_KINDS = ('advance', 'loan')            # деньги выданы на руки — расход фонда
MINUS_KINDS = ('fine', 'advance', 'loan')   # снимаются с зарплаты
PAY_KINDS = ('salary', 'advance', 'loan')  # записи фонда, которые считаются зарплатами
ROLES = ('senior', 'operator', 'driver', 'other')
ROLE_T = {'senior': 'Старшие', 'operator': 'Операторы', 'driver': 'Водители', 'other': 'Другие'}
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
    eff = dict(rate=None, unit='month', cur='AED', days=None, note='', rate_month='')
    for d in month_docs:                       # отсортированы по месяцу
        for k in ('rate', 'unit', 'cur'):
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
    rows, plus, minus, debt = [], 0.0, 0.0, 0.0
    for it in items:
        s = schedule(it, month)
        if it.get('kind') == 'bonus':
            plus += s['due']
        else:
            minus += s['due']
            debt += s['after']
        rows.append(dict(id=it.get('_id'), kind=it.get('kind'), t=KINDS.get(it.get('kind'), ''),
                         amount=_i(_n(it.get('amount'))), per_month=_i(_n(it.get('per_month'))),
                         start=str(it.get('from') or '')[:7], day=it.get('day') or '',
                         note=it.get('note') or '', entry=it.get('entry') or '', **s))
    to_pay = accrued + plus - minus
    paid = sum(_n(e.get('amount')) for e in payouts)
    return dict(
        name=p.get('name'), role=p.get('role') or 'other', manual=bool(p.get('manual')),
        rate=None if eff.get('rate') is None else _i(rate), unit=unit, cur=cur,
        rate_month=eff.get('rate_month') or '', rate_aed=_i(rate_aed),
        days=_i(_n(days)), days_auto=_i(_n(days_auto)), days_set=eff.get('days') is not None,
        note=eff.get('note') or '', accrued=_i(accrued), plus=_i(plus), minus=_i(minus),
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
