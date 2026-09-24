"""Премия за стаж: тысяча дирхам за каждые полгода работы.

Владелец, 23 сен 2026: «кто полгода работает — получает премию 1000 дирхам,
год — 2 тысячи»; «премию получают только операторы и водители»; «в
большинстве случаев они премию берут, когда уезжать собираются, но иногда
могут и просто забирать полугодовые каждый раз — то есть если наработал 6
месяцев, забрал премию на 8-м, он на 12-м всё равно сможет ещё 1000 забрать.
Но если он всё это время премию не брал — она копится».

Как считается
-------------
Ступень — полгода работы, ступень стоит 1000 AED. Отработал 6 месяцев —
заработал 1000, 12 месяцев — 2000, и так дальше. Выплаченное вычитается:
взял на восьмом месяце тысячу — на двенадцатом снова доступна тысяча, а не
две. Не брал вовсе — лежит и копится.

Отъезд обнуляет счёт (владелец выбрал это правило 23 сен 2026: «сброс —
отсчёт заново»): считаем от начала ПОСЛЕДНЕГО периода работы. Уехал на два
месяца и вернулся — стаж считается с дня возвращения, наработанное до отъезда
сгорает. Поэтому и выплаты берём только те, что были внутри текущего периода:
прошлая жизнь человека в этом расчёте не участвует.

Даты приезда программа знает из «Зарплат» (периоды «вышел — уехал»,
finance_pay.work_clean). Даты нет — премию не считаем и говорим об этом
словами: не молча ноль, а «дата приезда не указана».

Выплата — обычная разовая премия в зарплатах (fin_pay_items, kind=bonus) с
пометкой tenure: по ней лист премий и узнаёт свои выплаты. Премия, вписанная
владельцем руками, стаж не закрывает: это другие деньги.
"""
from datetime import date, datetime, timezone

STEP_MONTHS = 6            # ступень стажа
STEP_AED = 1000            # сколько стоит ступень
ROLES = ("driver", "operator", "senior")   # кому положена (владелец: операторы и водители)


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _months_between(a: date, b: date) -> int:
    """Полных месяцев от a до b. 10 декабря → 9 сентября это 8 месяцев,
    10 сентября — уже 9: месяц считается полным в то же число."""
    if not a or not b or b < a:
        return 0
    n = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        n -= 1
    return max(0, n)


def _plus_months(a: date, n: int) -> date:
    """a плюс n месяцев, то же число (31-го в коротком месяце — последний день)."""
    y, m = a.year + (a.month - 1 + n) // 12, (a.month - 1 + n) % 12 + 1
    d = a.day
    while d > 28:
        try:
            return date(y, m, d)
        except ValueError:
            d -= 1
    return date(y, m, d)


def period(work: list) -> dict:
    """Текущий период работы: {from, to}. Пусто — периодов нет вовсе."""
    import finance_pay as pay
    spans = pay.work_clean(work)
    return dict(spans[-1]) if spans else {}


def paid_in(items: list, since: str) -> float:
    """Сколько премии за стаж уже выплачено в текущем периоде."""
    total = 0.0
    for it in items or ():
        if (it.get("kind") or "") != "bonus" or not it.get("tenure"):
            continue
        if it.get("cancelled_at"):
            continue
        if since and str(it.get("day") or "")[:10] < since:
            continue                    # это из прошлой жизни, до отъезда
        try:
            total += float(it.get("amount") or 0)
        except (TypeError, ValueError):
            pass
    return total


def state(person: dict, items: list, today: str = "") -> dict:
    """Что с премией у человека — одной картиной для экрана и для выплаты."""
    t = _d(today) or datetime.now(timezone.utc).date()
    p = period(person.get("work"))
    start, left = _d(p.get("from")), _d(p.get("to"))
    out = {"name": person.get("name") or "", "title": person.get("title") or person.get("name") or "",
           "role": person.get("role") or "", "start": p.get("from") or "", "left": p.get("to") or "",
           "months": 0, "steps": 0, "earned": 0, "paid": 0, "due": 0,
           "next_at": "", "next_step": 1, "step_aed": STEP_AED, "why": ""}
    if not start:
        # Ни разу не отмечали, когда человек вышел. Считать не от чего, и
        # выдумывать дату нельзя — деньги.
        out["why"] = "Дата приезда не указана"
        return out
    # Уехал — стаж замер: дни в отъезде не идут в счёт. Дата отъезда — день,
    # когда человек уже не работает (владелец, 24 сен 2026), поэтому считаем
    # по день до неё.
    from datetime import timedelta as _td
    ушёл = (left - _td(days=1)) if left else None
    к = ушёл if (ушёл and ушёл < t) else t
    months = _months_between(start, к)
    steps = months // STEP_MONTHS
    paid = paid_in(items, p.get("from") or "")
    earned = steps * STEP_AED
    out.update(months=months, steps=steps, earned=earned, paid=int(paid),
               due=max(0, earned - int(paid)),
               next_step=steps + 1,
               next_at=_plus_months(start, (steps + 1) * STEP_MONTHS).isoformat())
    if left and left <= t:
        out["why"] = "Уехал — стаж не идёт"
    return out


def list_for(people: list, items: list, today: str = "") -> list:
    """Лист премий одним списком, в том же порядке, что люди стоят везде:
    сначала операторы, потом водители по районам (владелец, 23 сен 2026: «не
    надо всех с премиями наверх; единый список по порядку, билдингам тоже —
    сверху операторы, потом водители по районам»)."""
    from config_offices import OFFICE_CODES, OFFICE_IDS, OFFICE_NAMES
    by_name: dict = {}
    for it in items or ():
        by_name.setdefault(str(it.get("name") or ""), []).append(it)
    порядок = {"senior": 0, "operator": 1, "driver": 2}
    out = []
    for p in people:
        role = p.get("role") or ""
        if role not in ROLES:
            continue
        st = state(p, by_name.get(p.get("name") or "") or [], today)
        oid = next((d for d in (p.get("districts") or []) if d in OFFICE_IDS), "")
        st["district"] = oid
        st["district_code"] = OFFICE_CODES.get(oid, "")
        st["district_name"] = OFFICE_NAMES.get(oid, "")
        st["_o"] = (порядок.get(role, 3),
                    OFFICE_IDS.index(oid) if oid in OFFICE_IDS else len(OFFICE_IDS),
                    st["title"])
        out.append(st)
    out.sort(key=lambda r: r.pop("_o"))
    return out


def pay_item(st: dict, day: str, who: str) -> dict:
    """Запись премии в зарплаты — обычная разовая премия с пометкой стажа."""
    шаг = int(st.get("steps") or 0)
    return {"name": st["name"], "kind": "bonus", "amount": int(st["due"]),
            "per_month": 0, "from": day[:7], "day": day,
            "note": f"Премия за стаж · {шаг * STEP_MONTHS} месяцев",
            "tenure": шаг, "by": who}


def driver_view(st: dict) -> dict:
    """То же самое для приложения водителя: без чужих имён и без ролей."""
    return {"months": st["months"], "earned": st["earned"], "paid": st["paid"],
            "due": st["due"], "next_at": st["next_at"], "next_step": st["next_step"],
            "step_aed": STEP_AED, "step_months": STEP_MONTHS,
            "start": st["start"], "why": st["why"]}
