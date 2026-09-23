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

Кто когда на работе (владелец, 21 сен 2026: «весь персонал не круглый год
работает: кто-то уезжает, кто-то приезжает, а зарплата у всех первого
числа; человек не должен приехать 15-го и получить как за целый месяц — он
должен получить половину»; старшие Макар и Стас сменяют друг друга). У
человека — периоды «вышел на работу → уехал»: с какого дня и по какой
включительно; конец пуст — работает сейчас. Оклад в месяц (и премия в
месяц) — за дни на работе: оклад × дни на работе / дни месяца. Вышел 15
сентября — 16 из 30 дней. Ставка в день и так считается по сменам, её
периоды не трогают. Периодов нет — человек на работе весь месяц, как было
до них: оклады, которые уже платят, не меняются.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

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


# ── периоды работы ──────────────────────────────────────────────────────────
def month_len(month: str) -> int:
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def work_clean(work) -> list:
    """Периоды как они лежат у человека: [{from, to}], по порядку. Пустое
    «с» — работал ещё до того, как начали отмечать; пустое «по» — работает."""
    out = []
    for p in work or []:
        a = str((p or {}).get("from") or "")[:10]
        b = str((p or {}).get("to") or "")[:10]
        if (a and not _day(a)) or (b and not _day(b)) or not (a or b):
            continue
        out.append({"from": a, "to": b})
    out.sort(key=lambda p: p["from"])
    return out


def work_error(work: list) -> str:
    """Что не так с периодами: конец раньше начала, периоды налезают друг на
    друга, незакрытый период не последний. Пустая строка — всё в порядке."""
    prev_to = None
    for i, p in enumerate(work):
        a, b = p["from"], p["to"]
        if a and b and b < a:
            return "end_before_start"
        if i and (prev_to == "" or (a and prev_to and a <= prev_to) or not a):
            return "overlap"
        prev_to = b
    return ""


def work_in(work, month: str) -> dict:
    """Сколько дней человек на работе в месяце. Периодов нет — весь месяц."""
    n = month_len(month)
    work = work_clean(work)
    if not work:
        return dict(days=n, of=n, share=1.0, spans=[], set=False)
    first, last = f"{month}-01", f"{month}-{n:02d}"
    spans, days = [], 0
    for p in work:
        a, b = max(p["from"] or first, first), min(p["to"] or last, last)
        if a <= b:
            spans.append([a, b])
            days += (_day(b) - _day(a)).days + 1
    return dict(days=days, of=n, share=days / n, spans=spans, set=True)


def work_now(work, day: str) -> dict:
    """На работе ли человек в этот день и с какого числа — для приложения:
    «Работает с …» в профиле водителя, а дальше — приёмка машины в первый
    день и всё, что зависит от того, когда человек вышел. day_n — какой по
    счёту это день на работе (1 — первый); 0 — начало не отмечено."""
    work = work_clean(work)
    if not work:
        return dict(set=False, on=True, since="", until="", day_n=0, left="", back="")
    for p in work:
        a, b = p["from"], p["to"]
        if (not a or a <= day) and (not b or day <= b):
            n = (_day(day) - _day(a)).days + 1 if a else 0
            return dict(set=True, on=True, since=a, until=b, day_n=n, left="", back="")
    left = max((p["to"] for p in work if p["to"] and p["to"] < day), default="")
    back = min((p["from"] for p in work if p["from"] and p["from"] > day), default="")
    return dict(set=True, on=False, since="", until="", day_n=0, left=left, back=back)


def work_apply(work, action: str, day: str = "", i: int = -1, a: str = "", b: str = "") -> tuple:
    """Правка периодов: (новые периоды, ошибка).
    start — вышел на работу с day; end — уехал, day — последний день;
    set — поправить период i (a — с, b — по, пусто — работает); del — убрать i."""
    work = work_clean(work)
    if action == "start":
        if not _day(day):
            return work, "bad_day"
        if work and not work[-1]["to"]:
            return work, "already_on"
        work = work + [{"from": day, "to": ""}]
    elif action == "end":
        if not _day(day):
            return work, "bad_day"
        if not work:                               # работал до того, как начали отмечать
            work = [{"from": "", "to": day}]
        elif work[-1]["to"]:
            return work, "not_on"
        elif work[-1]["from"] and day < work[-1]["from"]:
            return work, "end_before_start"
        else:
            work = work[:-1] + [{"from": work[-1]["from"], "to": day}]
    elif action == "set":
        if not (0 <= i < len(work)) or (a and not _day(a)) or (b and not _day(b)) or not (a or b):
            return work, "bad_period"
        work = work[:i] + [{"from": a, "to": b}] + work[i + 1:]
    elif action == "del":
        if not (0 <= i < len(work)):
            return work, "bad_period"
        work = work[:i] + work[i + 1:]
    else:
        return work, "bad_action"
    work = work_clean(work)
    return work, work_error(work)


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
    # Оклад в месяц — за дни на работе в этом месяце (периоды «вышел —
    # уехал»); ставка в день и так считается по сменам.
    w = work_in(p.get('work'), month)
    accrued = rate_aed * w['share'] if unit == 'month' else rate_aed * _n(days)
    # Своя премия в месяц — как оклад: с месяца, где вписана, и дальше; за
    # неполный месяц — та же доля.
    bonus_month = max(0.0, _n(eff.get('bonus')))
    bonus_due = bonus_month * w['share']
    rows, plus, minus, debt = [], bonus_due, 0.0, 0.0
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
        name=p.get('name'),
        # Как звать на экране. Обычно то же имя; у оператора с тёзкой-водителем
        # ключ уточнён («Парвиз · старший»), а зовут его по-прежнему Парвизом.
        title=p.get('title') or p.get('name'),
        role=p.get('role') or 'other', manual=bool(p.get('manual')),
        rate=None if eff.get('rate') is None else _i(rate), unit=unit, cur=cur,
        rate_month=eff.get('rate_month') or '', rate_aed=_i(rate_aed),
        days=_i(_n(days)), days_auto=_i(_n(days_auto)), days_set=eff.get('days') is not None,
        note=eff.get('note') or '', accrued=_i(accrued), plus=_i(plus), minus=_i(minus),
        bonus_month=_i(bonus_month), bonus_due=_i(bonus_due), bonus_set=eff.get('bonus') is not None,
        work=work_clean(p.get('work')), work_set=w['set'], work_days=w['days'],
        month_days=w['of'], work_spans=w['spans'],
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
