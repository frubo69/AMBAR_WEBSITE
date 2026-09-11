"""Финансы владельца — учёт денег по книге старшего, одним ответом на месяц.

Что приложение знает само и считает на лету (руками не вводится):
  сдали      — наличные доставленных заказов за учётные сутки минус расходы
               водителей (питание и согласованные разовые);
  заказали   — поставки дня в закупочных ценах (stock_value.cost_map);
  продали    — вся выручка дня по способам оплаты;
  дни        — сколько дней человек работал (отметки смен и питания).
Что решает старший (fin_* в db.py):
  день       — сколько отложить базе и в фонд (по умолчанию: базе — по заказу
               дня или по норме, в фонд — по норме бюджета), оплаты базе,
               сдали по факту, заметка;
  записи     — расходы из фонда (со строкой бюджета), приход в фонд,
               выплаты из прибыли, зарплаты и авансы;
  бюджет     — статьи месяца с планом; норма в день = план / дни месяца;
  зарплаты   — ставка, дни, штрафы, авансы, премии — finance_pay.py;
  месяц      — переносы, хранение, пересчёт сейфов, курс доллара.
Математика дня и месяца — finance_calc.compute(), зарплат — finance_pay.
"""
from __future__ import annotations

import calendar
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import backdate
import finance_calc as calc
import finance_pay as pay
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger(__name__)

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе
MAX_AMOUNT = 10_000_000
USD_FALLBACK = 3.67

DAY_FIELDS = ('handed_fact', 'ordered_fact', 'aside', 'collected', 'extra_rp',
              'pay_b', 'pay_b_extra')
OPEN_FIELDS = ('safe_b_open', 'debt_b_open', 'carry_np', 'storage',
               'safe_np_fact', 'safe_b_fact')
MONTH_FIELDS = OPEN_FIELDS + ('norm', 'norm_b', 'usd')
BOOKS = ('rp', 'np', 'in')
ENTRY_KINDS = ('', 'salary', 'advance', 'loan')
PAY_FIELDS = ('rate', 'unit', 'cur', 'days', 'note')


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _day_start(day: str) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "")


def _month_days(month: str) -> list[str]:
    y, m = int(month[:4]), int(month[5:7])
    n = calendar.monthrange(y, m)[1]
    return [f"{y:04d}-{m:02d}-{d:02d}" for d in range(1, n + 1)]


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _num(v, dec: int = 2):
    """Число из тела запроса; None и пустая строка — «снять значение»."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("bad_number")
    if f != f or abs(f) > MAX_AMOUNT:
        raise ValueError("bad_number")
    r = round(f, dec)
    return int(r) if r == int(r) else r


def _is_prepaid(o: dict) -> bool:
    m = str(o.get("payment_method") or "").lower()
    if m in ("crypto", "card", "online", "transfer"):
        return True
    return bool(o.get("paid") or o.get("prepaid") or o.get("crypto_paid"))


# ── Что приложение знает само ────────────────────────────────────────────────
async def _sales(days: list[str]) -> dict:
    """По дням: продали всего и по способам оплаты, чаевые, заказы, наличные
    по районам (для отметок сбора)."""
    import bizday
    # Окно по timestamp с запасом назад: день заказа — смена, в которой его
    # приняли, и созданный до полудня заказ может принадлежать этому дню.
    since, until = bizday.window_utc(days[0], days[-1])
    try:
        orders = await db.orders_between(since, until)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] заказы не прочитаны: {e}")
        orders = []
    out = {d: dict(gross=0, cash=0, crypto=0, card=0, debt=0, tips=0, orders=0,
                   cash_by=dict()) for d in days}
    for o in orders:
        day = bizday.order_day(o)
        s = out.get(day)
        if s is None:
            continue
        total = int(o.get("total") or 0)
        m = str(o.get("payment_method") or "").lower()
        s["gross"] += total
        s["orders"] += 1
        s["tips"] += int(o.get("tip") or 0)
        if m == "debt":
            s["debt"] += total
        elif m == "crypto" or o.get("crypto_paid"):
            s["crypto"] += total
        elif _is_prepaid(o):
            s["card"] += total
        else:
            s["cash"] += total
            oid = o.get("office_id") or ""
            s["cash_by"][oid] = s["cash_by"].get(oid, 0) + total
    return out


async def _spend(days: list[str]) -> dict:
    """Расходы водителей по дням: питание по отметке смены плюс согласованные
    разовые (со знаком — «нам вернули» уменьшает расход). Ждущие решения —
    отдельно, в расход не идут. Заодно — рабочие дни каждого водителя (для
    зарплат): ключ 'work' в out['_work']."""
    import expense_routes as _exp
    import config_staff as _staff
    try:
        rows = await db.get_driver_days_range(days[0], days[-1])
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] расходы водителей не прочитаны: {e}")
        rows = []
    home = {}
    try:
        for d in _staff.drivers():
            home[d.get("name")] = d.get("district") or ""
    except Exception:                             # noqa: BLE001
        pass
    out = {d: dict(spend=0, pending=0, spend_by=dict()) for d in days}
    work: dict = {}
    for r in rows:
        s = out.get(r.get("day") or "")
        if s is None:
            continue
        w = r.get("working")
        if w is True and r.get("driver"):
            work[r["driver"]] = work.get(r["driver"], 0) + 1
        meal = _staff.MEAL_WORKING if w is True else (_staff.MEAL_OFF if w is False else 0)
        amt = meal
        for e in (r.get("extras") or []):
            st = str(e.get("status") or "approved")
            if st == "approved":
                amt += _exp._signed(e)
            elif st == "pending":
                s["pending"] += _exp._signed(e)
        s["spend"] += amt
        oid = home.get(r.get("driver")) or ""
        s["spend_by"][oid] = s["spend_by"].get(oid, 0) + amt
    out["_work"] = work
    return out


async def _purchases(days: list[str]) -> dict:
    """Поставки по дням в закупочных ценах. Основная — «заказали у базы», с
    других баз — отдельно. Цена за бутылку = цена учётной единицы / бутылок в
    ней (пиво идёт ящиками). Позиция без цены в сумму не попадает — рядом доля
    бутылок, у которых цена известна."""
    import stock_routes
    import stock_value
    try:
        sups = await db.supplies_between(days[0], days[-1])
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] поставки не прочитаны: {e}")
        sups = []
    try:
        cost = await stock_value.cost_map()
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] цены закупки не прочитаны: {e}")
        cost = {}
    cat = stock_routes._catalog()
    out = {d: dict(ordered=0.0, ordered_extra=0.0, bottles=0, known=0,
                   supplies=[]) for d in days}
    for sup in sups:
        if sup.get("cancelled_at"):
            continue
        s = out.get(sup.get("day") or "")
        if s is None:
            continue
        extra = (sup.get("kind") or "main") == "extra"
        buys = sup.get("buys") or {}
        total = 0.0
        for it in (sup.get("items") or []):
            pid = it.get("id")
            qty = int(it.get("qty") or 0) or int(it.get("asked") or 0)
            if qty <= 0:
                continue
            b = buys.get(pid) or {}
            if extra and b.get("price"):
                try:
                    total += float(b.get("price") or 0) * float(b.get("qty") or qty)
                    s["bottles"] += qty; s["known"] += qty
                    continue
                except (TypeError, ValueError):
                    pass
            p = cat.get(pid) or {}
            c = float(cost.get(pid) or 0)
            s["bottles"] += qty
            if c > 0:
                total += qty * c / max(1, stock_routes._unit(p))
                s["known"] += qty
        if extra:
            s["ordered_extra"] += total
        else:
            s["ordered"] += total
        s["supplies"].append({"id": sup.get("supply_id"), "kind": "extra" if extra else "main",
                              "base": sup.get("base") or "", "aed": round(total),
                              "status": sup.get("status") or "open"})
    for s in out.values():
        s["ordered"] = round(s["ordered"])
        s["ordered_extra"] = round(s["ordered_extra"])
        s["cover"] = round(s["known"] / s["bottles"] * 100) if s["bottles"] else 100
    return out


async def _marks(days: list[str]) -> dict:
    """Отметки «получил» сбора выручки по районам."""
    out = {}
    for d in days:
        try:
            out[d] = await db.checklist_get(d)
        except Exception:                         # noqa: BLE001
            out[d] = {}
    return out


# ── Бюджет месяца ────────────────────────────────────────────────────────────
def norm_auto(total: float, ndays: int) -> int:
    """План месяца / дни месяца, вверх до сотни: 205 000 / 31 → 6 700."""
    if not total or not ndays:
        return 0
    return int(math.ceil(total / ndays / 100.0 - 1e-9) * 100)


def template_lines() -> list[dict]:
    """Статьи «по образцу» — те, что стоят в бюджете старшего. Зарплат здесь
    нет: зарплатный фонд складывается из людей (см. _pay_plan)."""
    from config_offices import OFFICES
    # «Аренда офисов» — группа: офис отдельно, потом пять зданий (билдинги)
    rows = [dict(name="Аренда офис", group="rent", kind="office")]
    rows += [dict(name=f"Аренда {o['name']}", group="rent") for o in OFFICES]
    # «Расходы на автомобили» и «Бытовые расходы» — группы из подпунктов
    rows += [dict(name=n, group="auto", kind="pool" if n in POOL_NAMES else "") for n in AUTO_NAMES]
    rows += [dict(name=n, group="home") for n in HOME_NAMES]
    rows += [dict(name=n) for n in ("Билеты", "Визы", "Sim", "Бензин", "Реклама")]
    return rows


GROUPS = ("", "rent", "auto", "home")
AUTO_NAMES = ("Аренда", "Гараж и ТО", "Парковка")
AUTO_LEGACY = ("Авто",)  # так статья называлась до 11 сен 2026
# Статья без даты платежа (kind="pool"): просто бюджет на месяц, без периода
# и календаря, правится прямо в списке; старые строки — по названию.
POOL_NAMES = ("Гараж и ТО", "Парковка")
HOME_NAMES = ("Хоз. нужды", "Продукты")
MAX_PERIOD = 24


def _add_months(day: str, n: int) -> str:
    """Та же дата через n месяцев; 31-го → последнее число короткого месяца."""
    d = datetime.strptime(day, "%Y-%m-%d")
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d.day, calendar.monthrange(y, m)[1]):02d}"


def _schedule(ln: dict, month: str, today: str) -> dict:
    """Платёж раз в period месяцев от даты next: когда следующий (первая дата
    не раньше сегодня) и попадает ли платёж в этот месяц. Без даты — как
    ежемесячный: план каждый месяц, следующего платежа нет."""
    try:
        period = max(1, min(MAX_PERIOD, int(ln.get("period") or 1)))
    except (TypeError, ValueError):
        period = 1
    nxt = str(ln.get("next") or "")
    try:
        datetime.strptime(nxt, "%Y-%m-%d")
    except ValueError:
        nxt = ""
    if not nxt:
        return dict(period=period, next="", next_due="", due_in=period == 1)
    first, last = month + "-01", _month_days(month)[-1]
    # платёж в этом месяце — любая дата ряда next ± k·period
    due_in = False
    for k in range(-40, 41):
        d = _add_months(nxt, k * period)
        if first <= d <= last:
            due_in = True
            break
        if d > last and k >= 0:
            break
    due = nxt
    while due < today:
        due = _add_months(due, period)
    return dict(period=period, next=nxt, next_due=due, due_in=due_in)


def _is_pool(ln: dict) -> bool:
    return ln.get("kind") == "pool" or (ln.get("name") or "") in POOL_NAMES


def _line_group(ln: dict) -> str:
    """Группа статьи: «rent» — здание в «Аренде офисов», «auto» — подпункт
    «Расходов на автомобили». Старые строки без поля относим по названию,
    чтобы уже заполненный месяц не рассыпался."""
    g = ln.get("group")
    if g in GROUPS and g:
        return g
    name = str(ln.get("name") or "")
    if name in AUTO_NAMES or name in AUTO_LEGACY:
        return "auto"          # просто «Аренда» — машины, а не «Аренда B1»
    if name.startswith("Аренда "):
        return "rent"
    return "home" if name in HOME_NAMES else ""


async def _budget(month: str, entries: list, mdoc: dict, ndays: int, salary: dict) -> dict:
    """Статьи месяца с планом и фактом плюс зарплатный фонд (salary — из
    _pay_plan: люди и их оклады). Выплаты, авансы и долги людям идут в факт
    зарплат по виду записи, а не по статье."""
    from config_offices import OFFICES, OFFICE_CODES
    try:
        lines = await db.fin_budget_get(month)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] бюджет не прочитан: {e}")
        lines = []
    lines = [ln for ln in lines if (ln.get("kind") or "") != "salary"]   # старые строки «Зарплаты»
    fact: dict = {}
    off_plan = 0.0
    sal_fact = 0.0
    for e in entries:
        if e.get("book", "rp") != "rp":
            continue
        if e.get("kind") in pay.PAY_KINDS:
            sal_fact += calc._n(e.get("amount"))
            continue
        lid = e.get("line") or ""
        if lid:
            fact[lid] = fact.get(lid, 0.0) + calc._n(e.get("amount"))
        else:
            off_plan += calc._n(e.get("amount"))
    rows, total, fact_sum = [], 0.0, 0.0
    rent = dict(plan=0.0, fact=0.0, n=0)
    autog = dict(plan=0.0, fact=0.0, n=0)     # «Расходы на автомобили»; auto ниже — норма
    home = dict(plan=0.0, fact=0.0, n=0)      # «Бытовые расходы»
    today = _biz_day()
    for i, ln in enumerate(lines):
        plan = calc._n(ln.get("plan"))
        f = fact.get(ln.get("_id"), 0.0)
        name = ln.get("name") or ""
        group = _line_group(ln)
        office = group == "rent" and (ln.get("kind") == "office" or name == "Аренда офис")
        # здание в аренде: код района в золотой таблетке и короткое имя — «B1 JVC»
        code, short = "", name
        if group == "rent":
            short = "Офис" if office else (name[7:] if name.startswith("Аренда ") else name)
            oid = next((o["id"] for o in OFFICES if o["name"] == short), "")
            code = OFFICE_CODES.get(oid, "")
        pool = _is_pool(ln)
        sch = dict(period=1, next="", next_due="", due_in=True) if pool else _schedule(ln, month, today)
        # платёж раз в N месяцев делится поровну на каждый месяц: доля входит в
        # план месяца и в норму дня, и к дате платежа сумма уже отложена
        # (владелец: «не узнавать сюрпризом»); следующий платёж — рядом
        plan_m = plan / sch["period"]
        total += plan_m; fact_sum += f
        if group == "rent":
            rent["plan"] += plan_m; rent["fact"] += f; rent["n"] += 1
        elif group == "auto":
            autog["plan"] += plan_m; autog["fact"] += f; autog["n"] += 1
        elif group == "home":
            home["plan"] += plan_m; home["fact"] += f; home["n"] += 1
        rows.append(dict(id=ln.get("_id"), name=name, plan=calc._i(plan), plan_m=calc._i(plan_m),
                         fact=calc._i(f), left=calc._i(plan_m - f), due=int(ln.get("due") or 0),
                         note=ln.get("note") or "", group=group, office=office, code=code, short=short, **sch,
                         kind="pool" if pool else "office" if office else "",
                         ord=int(ln.get("ord") if ln.get("ord") is not None else i)))
    # записи без статьи и записи удалённой статьи — «вне плана», но потрачено
    known = {r["id"] for r in rows}
    stray = sum(v for k, v in fact.items() if k not in known)   # строка удалена, записи остались
    off_plan += stray
    salary = dict(salary, fact=calc._i(sal_fact), left=calc._i(calc._n(salary.get("plan")) - sal_fact))
    total += calc._n(salary.get("plan"))
    fact_all = fact_sum + off_plan + sal_fact
    auto = norm_auto(total, ndays)
    norm = mdoc.get("norm")
    norm_b = mdoc.get("norm_b")
    prev_has = False
    if not lines:
        try:
            prev_has = bool(await db.fin_budget_get(_prev_month(month)))
        except Exception:                         # noqa: BLE001
            prev_has = False
    rent = dict(plan=calc._i(rent["plan"]), fact=calc._i(rent["fact"]),
                left=calc._i(rent["plan"] - rent["fact"]), n=rent["n"])
    autog = dict(plan=calc._i(autog["plan"]), fact=calc._i(autog["fact"]),
                 left=calc._i(autog["plan"] - autog["fact"]), n=autog["n"])
    home = dict(plan=calc._i(home["plan"]), fact=calc._i(home["fact"]),
                left=calc._i(home["plan"] - home["fact"]), n=home["n"])
    return dict(lines=rows, salary=salary, rent=rent, auto=autog, home=home, total=calc._i(total), fact=calc._i(fact_all),
                left=calc._i(total - fact_all), off_plan=calc._i(off_plan),
                days=ndays, per_day=calc._i(total / ndays) if ndays and total else 0,
                norm_auto=auto, norm=calc._i(calc._n(norm)) if norm is not None else auto,
                norm_set=norm is not None,
                norm_b=None if norm_b is None else calc._i(calc._n(norm_b)),
                prev_has=prev_has, empty=not lines)


# ── Зарплаты ─────────────────────────────────────────────────────────────────
async def _usd(mdoc: dict) -> dict:
    if mdoc.get("usd"):
        return dict(usd=float(mdoc["usd"]), usd_auto=None, usd_set=True)
    auto = None
    try:
        import rates
        r = await rates.get_rates()
        row = next((x for x in (r.get("rates") or []) if x.get("code") == "USD"), None)
        if row:
            auto = float(row.get("cash_aed") or row.get("aed") or 0) or None
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] курс доллара не прочитан: {e}")
    return dict(usd=auto or USD_FALLBACK, usd_auto=auto, usd_set=False)


def _people(docs: list) -> list[dict]:
    """Кто получает зарплату: люди из расписания плюс вписанные руками."""
    import config_staff as staff
    from config_offices import OFFICE_IDS
    docs = sorted(docs, key=lambda d: str(d.get("created") or d.get("at") or ""))
    by_name = {str(d.get("_id")): d for d in docs}
    out, seen = [], set()

    def add(name, role, **kw):
        if not name or name in seen:
            return
        seen.add(name)
        d = by_name.get(name) or {}
        if d.get("hidden"):
            return
        out.append(dict(name=name, role=d.get("role") or role, manual=bool(d.get("manual")),
                        pnote=d.get("note") or "", **kw))
    # Сначала те, кого вписали руками (руководство, старший), в порядке
    # добавления; потом расписание: старшие операторы, операторы, водители.
    for d in docs:
        if d.get("manual"):
            add(str(d.get("_id")), d.get("role") or "other", districts=[])
    try:
        for s in staff.SENIOR_OPERATORS:
            add(s.get("name"), "senior", districts=list(OFFICE_IDS))
        for o in staff.operators():
            if not o.get("senior"):
                add(o.get("name"), "operator", districts=list(o.get("districts") or []))
        for d in staff.drivers():
            add(d.get("name"), "driver", districts=[d.get("district") or ""])
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] расписание не прочитано: {e}")
    for d in docs:
        add(str(d.get("_id")), d.get("role") or "other", districts=[])
    # порядок руками (перетягиванием): у кого ord есть — по нему, остальные
    # остаются в прежнем порядке после них
    ords = {str(d.get("_id")): d.get("ord") for d in docs if d.get("ord") is not None}
    out.sort(key=lambda p: (p["name"] not in ords, ords.get(p["name"], 0)))
    return out


async def _pay_plan(month: str, ndays: int, usd: float) -> dict:
    """Зарплатный фонд месяца — из людей и их окладов: оклад в месяц как есть,
    ставка в день × дни месяца, доллары по курсу. Это и есть статья «Зарплаты»
    бюджета: не число, вписанное отдельно, а список людей."""
    try:
        docs = await db.fin_people_get()
        mdocs = await db.fin_pay_months_upto(month)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] оклады не прочитаны: {e}")
        docs, mdocs = [], []
    from config_offices import OFFICE_CODES, OFFICE_NAMES
    by_name: dict = {}
    for d in mdocs:
        by_name.setdefault(str(d.get("name")), []).append(d)
    people, total = [], 0.0
    for p in _people(docs):
        eff = pay.effective({**p, "_month": month}, by_name.get(p["name"]) or [])
        rate = calc._n(eff.get("rate"))
        unit = eff.get("unit") if eff.get("unit") in pay.UNITS else "month"
        cur = eff.get("cur") if eff.get("cur") in pay.CURS else "AED"
        rate_aed = rate * (usd if cur == "USD" else 1.0)
        plan = rate_aed if unit == "month" else rate_aed * ndays
        total += plan
        # водителю — его район: в бюджете водители лежат по районам, как везде
        dist = (p.get("districts") or [""])[0] if p["role"] == "driver" else ""
        people.append(dict(name=p["name"], role=p["role"], role_t=pay.ROLE_T.get(p["role"], ""),
                           manual=bool(p.get("manual")),
                           district=dist, district_code=OFFICE_CODES.get(dist, ""),
                           district_name=OFFICE_NAMES.get(dist, ""),
                           rate=None if eff.get("rate") is None else calc._i(rate),
                           unit=unit, cur=cur, rate_aed=calc._i(rate_aed), plan=calc._i(plan)))
    return dict(plan=calc._i(total), people=people, n=len(people),
                set_n=sum(1 for x in people if x["rate"] is not None), usd=usd)


def _days_auto(people: list, work: dict, shifts: list) -> dict:
    """Дней по приложению: водитель — отметки «работал», оператор — дни, когда
    открывалась смена его района, старший — дни, когда работал хоть один район."""
    by_d: dict = {}
    all_days: set = set()
    for day, district in shifts:
        by_d.setdefault(district, set()).add(day)
        all_days.add(day)
    out = {}
    for p in people:
        if p["role"] == "driver":
            out[p["name"]] = work.get(p["name"], 0)
        elif p["role"] == "operator":
            ds: set = set()
            for d in p.get("districts") or []:
                ds |= by_d.get(d, set())
            out[p["name"]] = len(ds)
        elif p["role"] == "senior":
            out[p["name"]] = len(all_days)
        else:
            out[p["name"]] = 0
    return out


async def _payroll(month: str, days: list[str], today: str, entries: list,
                   work: dict, usd: float) -> dict:
    try:
        docs = await db.fin_people_get()
        mdocs = await db.fin_pay_months_upto(month)
        items = await db.fin_pay_items_get()
        shifts = await db.shift_days_worked(days[0], min(days[-1], today))
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] зарплаты не прочитаны: {e}")
        docs, mdocs, items, shifts = [], [], [], []
    people = _people(docs)
    by_name: dict = {}
    for d in mdocs:
        by_name.setdefault(str(d.get("name")), []).append(d)
    # выплата относится к месяцу, за который платят (pay_month), а не к дню,
    # когда деньги вышли из фонда: зарплату за август платят в сентябре
    try:
        paid_rows = await db.fin_entries_where({"kind": "salary", "pay_month": month})
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] выплаты не прочитаны: {e}")
        paid_rows = []
    seen_ids = {str(e.get("_id")) for e in paid_rows}
    paid_rows += [e for e in entries if e.get("kind") == "salary" and not e.get("pay_month")
                  and str(e.get("_id")) not in seen_ids]
    payouts: dict = {}
    for e in paid_rows:
        if e.get("who"):
            payouts.setdefault(e["who"], []).append(_entry_view(e, {}))
    # люди, которых нет в расписании, но у кого есть выплаты или удержания —
    # тоже в списке, чтобы деньги не пропали из виду
    known = {p["name"] for p in people}
    hidden = {str(d.get("_id")) for d in docs if d.get("hidden")}
    for name in list(payouts) + [it.get("name") for it in items]:
        if name and name not in known and name not in hidden:
            known.add(name)
            people.append(dict(name=name, role="other", manual=True, pnote="", districts=[]))
    res = pay.payroll(people, month, by_name, _days_auto(people, work, shifts), items, payouts, usd)
    for r in res["people"]:
        p = next((x for x in people if x["name"] == r["name"]), {})
        r["pnote"] = p.get("pnote", "")
    return res


# ── Месяц целиком ────────────────────────────────────────────────────────────
async def _opening(month: str, depth: int = 0) -> dict:
    """Переносы месяца: что вписано руками — главнее; остальное берём из
    закрытия прошлого месяца, если он вообще вёлся."""
    doc = await db.fin_month_get(month)
    explicit = {k: doc.get(k) for k in OPEN_FIELDS if doc.get(k) is not None}
    carried: dict = {}
    if depth < 6:
        prev = _prev_month(month)
        prev_doc = await db.fin_month_get(prev)
        cache = prev_doc.get("carry_cache")
        if isinstance(cache, dict):
            # закрытый месяц уже считали: его закрытие лежит в его же документе
            # и стирается любой правкой этого или более раннего месяца (_touch)
            carried = dict(cache)
        else:
            days = _month_days(prev)
            touched = bool(prev_doc) or bool(await db.fin_days_get(days[0], days[-1])) \
                or bool(await db.fin_entries_get(days[0], days[-1]))
            if touched:
                book = await build(prev, depth + 1, light=True)
                carried = calc.carry_from(book)
                if prev < _biz_day()[:7]:
                    try:
                        await db.fin_month_set(prev, {"carry_cache": carried})
                    except Exception as e:            # noqa: BLE001
                        log.warning(f"[fin] кэш переноса {prev} не записан: {e}")
    opening = {**{k: v for k, v in carried.items() if v is not None}, **explicit}
    return {"opening": opening, "explicit": explicit, "carried": carried,
            "note": doc.get("note") or "", "doc": doc}


def _entry_view(e: dict, line_names: dict) -> dict:
    lid = e.get("line") or ""
    return {"id": e.get("_id"), "amount": e.get("amount"), "comment": e.get("comment") or "",
            "who": e.get("who") or "", "line": lid, "line_name": line_names.get(lid, ""),
            "kind": e.get("kind") or "", "kind_t": {"salary": "Зарплата", "advance": "Аванс",
                                                    "loan": "Долг"}.get(e.get("kind") or "", ""),
            "item": e.get("item") or "", "by": e.get("by") or "", "at": str(e.get("at") or ""),
            "day": e.get("day") or "", "pay_month": e.get("pay_month") or ""}


async def build(month: str, depth: int = 0, light: bool = False) -> dict:
    days = _month_days(month)
    today = _biz_day()
    sales, spend, purch, manual, entries, opening = (
        await _sales(days), await _spend(days), await _purchases(days),
        await db.fin_days_get(days[0], days[-1]),
        await db.fin_entries_get(days[0], days[-1]),
        await _opening(month, depth))
    work = spend.pop("_work", {})
    mdoc = opening["doc"]
    fx = await _usd(mdoc)
    budget = await _budget(month, entries, mdoc, len(days), await _pay_plan(month, len(days), fx["usd"]))
    line_names = {ln["id"]: ln["name"] for ln in budget["lines"]}
    by_day_entries: dict = {}
    for e in entries:
        bk = e.get("book") if e.get("book") in BOOKS else "rp"
        by_day_entries.setdefault(e.get("day"), {"rp": [], "np": [], "in": []})[bk].append(
            _entry_view(e, line_names))
    rows, meta = [], {}
    for d in days:
        s, sp, pu, m = sales[d], spend[d], purch[d], manual.get(d) or {}
        en = by_day_entries.get(d) or {"rp": [], "np": [], "in": []}
        handed = s["cash"] - sp["spend"]
        fact = m.get("handed_fact")
        base = handed if fact is None else calc._n(fact)
        ordered = m["ordered_fact"] if m.get("ordered_fact") is not None else pu["ordered"]
        past = d <= today
        # раскладка дня по умолчанию: базе — по норме или по заказу дня,
        # в фонд — по норме бюджета; вписанное руками главнее
        aside, aside_src = m.get("aside"), "manual"
        if aside is None:
            aside_src = ""
            if past and base > 0:
                aside = budget["norm_b"] if budget["norm_b"] is not None else ordered
                aside_src = "norm" if budget["norm_b"] is not None else "order"
        collected, collected_src = m.get("collected"), "manual"
        if collected is None:
            collected_src = ""
            if past and base > 0 and budget["norm"]:
                collected, collected_src = budget["norm"], "norm"
        extra_in = sum(calc._n(x.get("amount")) for x in en["in"])
        rows.append(dict(
            day=d, gross=s["gross"], cash=s["cash"], card=s["card"], crypto=s["crypto"],
            debt=s["debt"], tips=s["tips"], spend=sp["spend"], handed=handed,
            handed_fact=fact, ordered=ordered, ordered_extra=pu["ordered_extra"],
            aside=aside, collected=collected,
            extra_rp=calc._n(m.get("extra_rp")) + extra_in,
            pay_b=m.get("pay_b"), pay_b_extra=m.get("pay_b_extra"),
            expenses=en["rp"], payouts=en["np"]))
        meta[d] = dict(aside_src=aside_src, collected_src=collected_src, ins=en["in"],
                       extra_manual=m.get("extra_rp"))
    book = calc.compute(rows, opening["opening"])
    marks = {} if light else await _marks([d for d in days if d <= today])
    for i, d in enumerate(days):
        r, s, sp, pu, m = book["days"][i], sales[d], spend[d], purch[d], manual.get(d) or {}
        r.update(orders=s["orders"], debt=s["debt"], spend_pending=sp["pending"],
                 ordered_auto=pu["ordered"], ordered_fact=m.get("ordered_fact"),
                 ordered_cover=pu["cover"], supplies=pu["supplies"],
                 note=m.get("note") or "", future=d > today, today=d == today,
                 aside_src=meta[d]["aside_src"], collected_src=meta[d]["collected_src"],
                 ins=meta[d]["ins"], extra_manual=meta[d]["extra_manual"],
                 salary_sum=calc._i(sum(calc._n(e["amount"]) for e in r["expenses"]
                                        if e.get("kind") in ("salary", "advance", "loan"))))
        r["manual"] = {k: m.get(k) for k in calc.DAY_MANUAL}
        if not light:
            mk = marks.get(d) or {}
            need = set(s["cash_by"]) | set(k for k, v in sp["spend_by"].items() if v)
            need.discard("")
            legacy = bool((mk.get("cash") or {}).get("done"))
            got = sum(1 for oid in need if legacy or (mk.get(f"cash:{oid}") or {}).get("done"))
            r.update(cash_need=len(need), cash_got=got)
    b, np_ = book["b"], book["np"]
    safe_total = b["safe_end"] + np_["should_be"]
    fact_b, fact_np = b.get("safe_fact"), np_.get("safe_fact")
    book["safe"] = dict(b=b["safe_end"], b_open=b["safe_open"], np=np_["should_be"],
                        rp=book["rp"]["result"], np_days=np_["days"], payouts=np_["payouts"],
                        carry=np_["carry"], storage=np_["storage"], total=calc._i(safe_total),
                        fact_b=fact_b, fact_np=fact_np, diff_b=b.get("diff"), diff_np=np_.get("diff"),
                        fact=None if fact_b is None and fact_np is None
                        else calc._i(calc._n(fact_b) + calc._n(fact_np)),
                        diff=None if fact_b is None and fact_np is None
                        else calc._i(calc._n(fact_b) + calc._n(fact_np) - safe_total))
    book.update(month=month, today=today, first=days[0], last=days[-1],
                opening=opening["opening"], opening_explicit=opening["explicit"],
                opening_carried=opening["carried"], month_note=opening["note"],
                prev_month=_prev_month(month), budget=budget)
    if not light:
        book["pay"] = await _payroll(month, days, today, entries, work, fx["usd"])
        book["pay"].update(fx)
    return book


# ── Ручки ────────────────────────────────────────────────────────────────────
async def _touch(month: str) -> None:
    """Любая запись в месяц меняет его закрытие и переносы всех следующих."""
    try:
        await db.fin_carry_invalidate(month)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] кэш переносов не сброшен: {e}")


def _json(data, status=200):
    import json
    return web.json_response(data, status=status, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


def _month_arg(s: str) -> str:
    s = (s or "").strip()
    if len(s) == 7 and s[4] == "-":
        int(s[:4]); m = int(s[5:7])
        if 1 <= m <= 12:
            return s
    raise ValueError("bad_month")


def _day_arg(s: str) -> str:
    s = (s or "").strip()
    datetime.strptime(s, "%Y-%m-%d")
    return s


def _who(body: dict) -> str:
    return str(body.get("as") or "").strip()[:40]


@require_owner
async def handle_book(request):
    """GET /api/owner/finance/book?month=YYYY-MM — весь месяц одним ответом."""
    try:
        month = _month_arg(request.query.get("month") or _biz_day()[:7])
    except ValueError:
        return _json({"error": "bad_month"}, 400)
    book = await build(month)
    try:
        book["months"] = await db.fin_months_list()
    except Exception:                             # noqa: BLE001
        book["months"] = []
    return _json(book)


@require_owner
async def handle_day_set(request):
    """POST {day, field, value, as} — одно ручное поле дня. value пустое —
    снять (вернуть значение по умолчанию). Прошедший день уходит владельцам в
    бот (backdate)."""
    try:
        body = await request.json()
        day = _day_arg(body.get("day"))
        field = str(body.get("field") or "")
        if field not in DAY_FIELDS and field != "note":
            return _json({"error": "bad_field"}, 400)
        if field == "note":
            value = str(body.get("value") or "").strip()[:200]
        else:
            value = _num(body.get("value"))
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    if value is None or value == "":
        await db.fin_day_set(day, {"by": who}, unset=[field])
    else:
        await db.fin_day_set(day, {field: value, "by": who})
    await _touch(day[:7])
    log.info(f"[fin] {day} {field} → {value!r} · {who or '—'}")
    await backdate.notify(day, who, "финансы: " + FIELD_T.get(field, field),
                          "" if value in (None, "") else (value if field == "note" else f"{value} AED"))
    return _json({"ok": True, "day": day, "field": field, "value": value,
                  "book": await build(day[:7])})


async def _line_ok(month: str, lid: str) -> bool:
    if not lid:
        return True
    ln = await db.fin_budget_line_get(lid)
    return bool(ln) and ln.get("month") == month


@require_owner
async def handle_entry_add(request):
    """POST {day, book: rp|np|in, amount, comment, who, line, kind, as} —
    строка расхода из фонда (rp, со строкой бюджета), выплаты из прибыли (np)
    или прихода в фонд (in)."""
    try:
        body = await request.json()
        day = _day_arg(body.get("day"))
        book = str(body.get("book") or "rp")
        if book not in BOOKS:
            return _json({"error": "bad_book"}, 400)
        amount = _num(body.get("amount"))
        if amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
        kind = str(body.get("kind") or "")
        if kind not in ENTRY_KINDS or (kind and book != "rp"):
            return _json({"error": "bad_kind"}, 400)
        line = str(body.get("line") or "")[:24] if book == "rp" else ""
        if not await _line_ok(day[:7], line):
            return _json({"error": "bad_line"}, 400)
        who = str(body.get("who") or "").strip()[:60]
        if kind and not who:
            return _json({"error": "who_required"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    by = _who(body)
    doc = {"_id": secrets.token_hex(6), "day": day, "book": book, "amount": amount,
           "comment": str(body.get("comment") or "").strip()[:120],
           "who": who, "line": line, "kind": kind, "by": by, "at": datetime.now(timezone.utc)}
    await db.fin_entry_add(doc)
    await _touch(day[:7])
    log.info(f"[fin] {day} {book} +{amount} «{doc['comment']}» {who} · {by or '—'}")
    await backdate.notify(day, by, "финансы: " + BOOK_T.get(book, book),
                          f"{amount} AED {who} {doc['comment']}".strip())
    return _json({"ok": True, "id": doc["_id"], "book": await build(day[:7])})


@require_owner
async def handle_entry_del(request):
    """DELETE {id, as} — убрать строку; аванс тянет за собой своё удержание."""
    try:
        body = await request.json()
        eid = str(body.get("id") or "")
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_entry_get(eid)
    if not old:
        return _json({"error": "not_found"}, 404)
    await db.fin_entry_del(eid)
    if old.get("item"):
        await db.fin_pay_item_del(str(old["item"]))
    await _touch(str(old.get("day") or "")[:7] or _biz_day()[:7])
    who = _who(body)
    log.info(f"[fin] {old.get('day')} {old.get('book')} −{old.get('amount')} убрано · {who or '—'}")
    await backdate.notify(str(old.get("day") or ""), who, "финансы: строка убрана",
                          f"{old.get('amount')} AED {old.get('comment') or ''}".strip())
    return _json({"ok": True, "book": await build(str(old.get("day") or "")[:7])})


@require_owner
async def handle_month_set(request):
    """POST {month, field, value, as} — перенос, хранение, пересчёт сейфа,
    норма в день (в фонд / базе), курс доллара, заметка."""
    try:
        body = await request.json()
        month = _month_arg(body.get("month"))
        field = str(body.get("field") or "")
        if field not in MONTH_FIELDS and field != "note":
            return _json({"error": "bad_field"}, 400)
        value = (str(body.get("value") or "").strip()[:200] if field == "note"
                 else _num(body.get("value"), 4 if field == "usd" else 2))
        if field in ("norm", "norm_b", "usd") and value is not None and value < 0:
            return _json({"error": "bad_number"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    if value is None or value == "":
        await db.fin_month_set(month, {"by": who}, unset=[field])
    else:
        await db.fin_month_set(month, {field: value, "by": who})
    await _touch(month)
    log.info(f"[fin] {month} {field} → {value!r} · {who or '—'}")
    return _json({"ok": True, "month": month, "field": field, "value": value,
                  "book": await build(month)})


# ── Бюджет ──────────────────────────────────────────────────────────────────
@require_owner
async def handle_budget_set(request):
    """POST {month, id?, name, plan, due, note, kind, as} — статья бюджета:
    новая или правка."""
    try:
        body = await request.json()
        month = _month_arg(body.get("month"))
        name = str(body.get("name") or "").strip()[:40]
        if not name:
            return _json({"error": "name_required"}, 400)
        plan = _num(body.get("plan")) or 0
        if plan < 0:
            return _json({"error": "bad_number"}, 400)
        due = int(_num(body.get("due")) or 0)
        if not 0 <= due <= 31:
            return _json({"error": "bad_due"}, 400)
        lid = str(body.get("id") or "")[:24]
        group = str(body.get("group") or "")
        if group not in GROUPS:
            return _json({"error": "bad_group"}, 400)
        kind = "office" if body.get("office") and group == "rent" else ""
        note = None if body.get("note") is None else str(body.get("note")).strip()[:80]
        period = int(_num(body.get("period")) or 1)
        if not 1 <= period <= MAX_PERIOD:
            return _json({"error": "bad_period"}, 400)
        nxt = str(body.get("next") or "").strip()
        if nxt:
            nxt = _day_arg(nxt)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    if lid:
        old = await db.fin_budget_line_get(lid)
        if not old or old.get("month") != month:
            return _json({"error": "not_found"}, 404)
        ordv = old.get("ord")
        if "group" not in body:
            group = _line_group(old)
        if "office" not in body:
            kind = "office" if old.get("kind") == "office" else ""
        if "period" not in body:
            period = int(old.get("period") or 1)
        if "next" not in body:
            nxt = str(old.get("next") or "")
        if note is None:
            note = str(old.get("note") or "")
        if _is_pool(old):
            kind = "pool"
    else:
        lid = secrets.token_hex(4)
        ordv = len(await db.fin_budget_get(month))
    if kind != "pool" and group == "auto" and name in POOL_NAMES:
        kind = "pool"
    if kind == "pool":
        period, nxt = 1, ""                       # без даты платежа: только бюджет на месяц
    doc = {"_id": lid, "month": month, "name": name, "plan": plan, "due": due,
           "note": note or "", "kind": kind, "group": group,
           "period": period, "next": nxt,
           "ord": ordv if ordv is not None else 0, "by": who}
    await db.fin_budget_set(doc)
    await _touch(month)
    log.info(f"[fin] бюджет {month}: {name} {plan} · {who or '—'}")
    return _json({"ok": True, "id": lid, "book": await build(month)})


@require_owner
async def handle_budget_del(request):
    """DELETE {id, as} — убрать статью; записи с ней остаются «вне плана»."""
    try:
        body = await request.json()
        lid = str(body.get("id") or "")
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_budget_line_get(lid)
    if not old:
        return _json({"error": "not_found"}, 404)
    await db.fin_budget_del(lid)
    await _touch(str(old.get("month")))
    log.info(f"[fin] бюджет {old.get('month')}: убрана «{old.get('name')}» · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(str(old.get("month")))})


@require_owner
async def handle_budget_fill(request):
    """POST {month, from: prev|template, as} — заполнить пустой бюджет: из
    прошлого месяца (с планами) или по образцу (без сумм)."""
    try:
        body = await request.json()
        month = _month_arg(body.get("month"))
        src = str(body.get("from") or "prev")
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    if await db.fin_budget_get(month):
        return _json({"error": "not_empty"}, 409)
    if src == "prev":
        prev = await db.fin_budget_get(_prev_month(month))
        if not prev:
            return _json({"error": "no_prev"}, 404)
        rows = [dict(name=ln.get("name"), plan=ln.get("plan") or 0, due=ln.get("due") or 0,
                     note=ln.get("note") or "", kind="pool" if _is_pool(ln) else "office" if ln.get("kind") == "office" else "",
                     group=_line_group(ln),
                     period=int(ln.get("period") or 1), next=str(ln.get("next") or "")) for ln in prev
                if (ln.get("kind") or "") != "salary"]
    else:
        rows = [dict(name=r["name"], plan=0, due=0, note="", kind=r.get("kind") or "", group=r.get("group") or "")
                for r in template_lines()]
    who = _who(body)
    for i, r in enumerate(rows):
        await db.fin_budget_set({"_id": secrets.token_hex(4), "month": month, "ord": i, "by": who, **r})
    await _touch(month)
    log.info(f"[fin] бюджет {month} заполнен ({src}, {len(rows)}) · {who or '—'}")
    return _json({"ok": True, "n": len(rows), "book": await build(month)})


# ── Зарплаты ─────────────────────────────────────────────────────────────────
@require_owner
async def handle_pay_person(request):
    """POST {name, role, note, hidden, as} — человек в зарплатах: новый (не из
    расписания), роль, заметка, скрыть."""
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()[:40]
        if not name:
            return _json({"error": "name_required"}, 400)
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
        fields = {"by": _who(body)}
        if "role" in body:
            role = str(body.get("role") or "other")
            if role not in pay.ROLES:
                return _json({"error": "bad_role"}, 400)
            fields["role"] = role
        if "note" in body:
            fields["note"] = str(body.get("note") or "").strip()[:80]
        if "hidden" in body:
            fields["hidden"] = bool(body.get("hidden"))
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    import config_staff as staff
    known = set(staff.driver_names()) | set(staff.operator_names())
    fields["manual"] = name not in known
    if "hidden" not in fields:
        fields["hidden"] = False                  # добавили заново — снова в списке
    await db.fin_person_set(name, fields)
    log.info(f"[fin] зарплаты: {name} {fields.get('role', '')}{' скрыт' if fields['hidden'] else ''} · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_order(request):
    """POST {names: [...], month, as} — порядок людей в зарплатах: как
    перетянули, так и лежат (ord по номеру в списке)."""
    try:
        body = await request.json()
        names = [str(n or "").strip()[:40] for n in (body.get("names") or [])]
        names = [n for n in names if n]
        if not names or len(names) > 200:
            return _json({"error": "bad_names"}, 400)
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    for i, n in enumerate(names):
        await db.fin_person_set(n, {"ord": i, "by": who})
    log.info(f"[fin] зарплаты: порядок {', '.join(names)} · {who or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_month_set(request):
    """POST {month, name, field, value, as} — ставка (rate / unit / cur) с этого
    месяца и дальше, дни и заметка — только за месяц."""
    try:
        body = await request.json()
        month = _month_arg(body.get("month"))
        name = str(body.get("name") or "").strip()[:40]
        field = str(body.get("field") or "")
        if not name or field not in PAY_FIELDS:
            return _json({"error": "bad_field"}, 400)
        raw = body.get("value")
        if field in ("rate", "days"):
            value = _num(raw)
            if value is not None and value < 0:
                return _json({"error": "bad_number"}, 400)
        elif field == "unit":
            value = str(raw or "") or None
            if value is not None and value not in pay.UNITS:
                return _json({"error": "bad_unit"}, 400)
        elif field == "cur":
            value = str(raw or "") or None
            if value is not None and value not in pay.CURS:
                return _json({"error": "bad_cur"}, 400)
        else:
            value = str(raw or "").strip()[:80]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    fields: dict = {"by": who}
    # валюта оклада приходит вместе со ставкой — одной записью, чтобы книга
    # не пересчитывалась дважды и не показывала доллары по дирхамовой ставке
    cur = body.get("cur")
    if field == "rate" and cur in pay.CURS:
        fields["cur"] = cur
    if value is None or value == "":
        await db.fin_pay_month_set(month, name, fields, unset=[field])
    else:
        await db.fin_pay_month_set(month, name, {**fields, field: value})
    log.info(f"[fin] зарплаты {month} {name}: {field} → {value!r}{' ' + cur if fields.get('cur') else ''} · {who or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_item_add(request):
    """POST {name, kind, amount, per_month, from: this|next|YYYY-MM, day, note, as}
    — штраф, аванс, долг или премия. Аванс и долг — деньги выданы из фонда:
    запись расхода в тот же день."""
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()[:40]
        kind = str(body.get("kind") or "")
        if not name or kind not in pay.KINDS:
            return _json({"error": "bad_kind"}, 400)
        amount = _num(body.get("amount"))
        if amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
        per_month = _num(body.get("per_month")) or 0
        if per_month < 0:
            return _json({"error": "bad_number"}, 400)
        day = _day_arg(body.get("day") or _biz_day())
        month = _month_arg(body.get("month")) if body.get("month") else day[:7]
        frm = str(body.get("from") or "this")
        start = month if frm == "this" else pay.next_month(month) if frm == "next" else _month_arg(frm)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    note = str(body.get("note") or "").strip()[:80]
    iid = secrets.token_hex(5)
    entry_id = ""
    if kind in pay.CASH_KINDS:
        entry_id = secrets.token_hex(6)
        await db.fin_entry_add({"_id": entry_id, "day": day, "book": "rp", "amount": amount,
                                "comment": note or pay.KINDS[kind], "who": name,
                                "line": "", "kind": kind, "item": iid,
                                "by": who, "at": datetime.now(timezone.utc)})
    await db.fin_pay_item_add({"_id": iid, "name": name, "kind": kind, "amount": amount,
                               "per_month": per_month, "from": start, "day": day, "note": note,
                               "entry": entry_id, "by": who, "at": datetime.now(timezone.utc)})
    await _touch(min(month, day[:7]))
    log.info(f"[fin] зарплаты: {name} {pay.KINDS[kind]} {amount} с {start}"
             f"{f' по {per_month}/мес' if per_month else ''} · {who or '—'}")
    if kind in pay.CASH_KINDS:
        await backdate.notify(day, who, f"финансы: {pay.KINDS[kind].lower()} {name}", f"{amount} AED")
    return _json({"ok": True, "id": iid, "book": await build(month)})


@require_owner
async def handle_pay_item_del(request):
    """DELETE {id, month, as} — убрать удержание; выданные деньги уходят из
    расходов вместе с ним."""
    try:
        body = await request.json()
        iid = str(body.get("id") or "")
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_pay_item_get(iid)
    if not old:
        return _json({"error": "not_found"}, 404)
    await db.fin_pay_item_del(iid)
    if old.get("entry"):
        await db.fin_entry_del(str(old["entry"]))
    await _touch(min(month, str(old.get("day") or month)[:7]))
    log.info(f"[fin] зарплаты: {old.get('name')} {old.get('kind')} {old.get('amount')} убрано · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_out(request):
    """POST {name, amount, day, month, note, as} — выплата зарплаты из фонда:
    деньги выходят днём day, зарплата — за месяц month (по умолчанию месяц дня)."""
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()[:40]
        amount = _num(body.get("amount"))
        if not name or amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
        day = _day_arg(body.get("day") or _biz_day())
        month = _month_arg(body.get("month")) if body.get("month") else day[:7]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    doc = {"_id": secrets.token_hex(6), "day": day, "book": "rp", "amount": amount,
           "comment": str(body.get("note") or "").strip()[:120] or "Зарплата",
           "who": name, "line": "", "kind": "salary", "pay_month": month,
           "by": who, "at": datetime.now(timezone.utc)}
    await db.fin_entry_add(doc)
    await _touch(min(month, day[:7]))
    log.info(f"[fin] зарплата {name} {amount} за {month} ({day}) · {who or '—'}")
    await backdate.notify(day, who, f"финансы: зарплата {name}", f"{amount} AED")
    return _json({"ok": True, "id": doc["_id"], "book": await build(month)})


FIELD_T = {
    "handed_fact": "сдали по факту", "ordered_fact": "заказали по счёту",
    "aside": "отложили базе", "collected": "отложили в фонд",
    "extra_rp": "приход в фонд", "pay_b": "оплата базе из отложенного",
    "pay_b_extra": "оплата базе сверх отложенного", "note": "заметка дня",
}
BOOK_T = {"rp": "расход из фонда", "np": "выплата из прибыли", "in": "приход в фонд"}


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/finance/book", handle_book, "GET"),
        ("/api/owner/finance/book/day", handle_day_set, "POST"),
        ("/api/owner/finance/book/entry", handle_entry_add, "POST"),
        ("/api/owner/finance/book/entry", handle_entry_del, "DELETE"),
        ("/api/owner/finance/book/month", handle_month_set, "POST"),
        ("/api/owner/finance/book/budget", handle_budget_set, "POST"),
        ("/api/owner/finance/book/budget", handle_budget_del, "DELETE"),
        ("/api/owner/finance/book/budget/fill", handle_budget_fill, "POST"),
        ("/api/owner/finance/book/pay/person", handle_pay_person, "POST"),
        ("/api/owner/finance/book/pay/month", handle_pay_month_set, "POST"),
        ("/api/owner/finance/book/pay/order", handle_pay_order, "POST"),
        ("/api/owner/finance/book/pay/item", handle_pay_item_add, "POST"),
        ("/api/owner/finance/book/pay/item", handle_pay_item_del, "DELETE"),
        ("/api/owner/finance/book/pay/out", handle_pay_out, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt)
            seen.add(path)
        r.add_route(method, path, handler)
    log.info("[fin] routes mounted")
