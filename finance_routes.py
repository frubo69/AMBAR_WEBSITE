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
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import cash_math
import backdate
import finance_calc as calc
import finance_pay as pay
import pay_notify as _pn          # сообщения водителю о его деньгах (штрафы, удержания, зарплата)
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger(__name__)

DUBAI_TZ = timezone(timedelta(hours=4))
from bizday import SHIFT_START_HOUR      # граница суток одна на всю систему (bizday)
MAX_AMOUNT = 10_000_000
MAX_SPAN = 31                  # окно платежа: сколько дней после даты им можно платить
USD_FALLBACK = 3.67

DAY_FIELDS = ('handed_fact', 'ordered_fact', 'aside', 'collected', 'collected_cr', 'extra_rp', 'pay')
PAY_WAYS = ('', 'cash', 'crypto')      # чем платили расход из РП; пусто = наличными
# Крипту РП двигают несколько ручек; проверка «хватает ли» и запись — под одним
# замком, иначе два запроса разом оба увидят остаток и оба его потратят.
_CR_LOCK = asyncio.Lock()
OPEN_FIELDS = ('safe_b_open', 'debt_b_open', 'rp_open', 'np_open')   # стопки сейфа и долг на начало
MONTH_FIELDS = OPEN_FIELDS + ('norm', 'usd')
# 'mv' — перевод между стопками сейфа (владелец, 3 окт 2026: «перевести со
# счёта РП на счёт ЧП»): из from в to, сейф целиком не меняется.
BOOKS = ('rp', 'np', 'in', 'mv')
STACKS = ('b', 'rp', 'np')
STACK_T = {"b": "Барракуда", "rp": "РП", "np": "ЧП"}
ENTRY_KINDS = ('', 'salary', 'advance', 'loan')
# Откуда взято то, чего не хватило в стопке Барракуды на оплату (владелец,
# 3 окт 2026): из ЧП (как было всегда), из РП, или понемногу из обоих.
PAY_SRC = ('', 'np', 'rp', 'mix')
PAY_SRC_T = {"": "из ЧП", "np": "из ЧП", "rp": "из РП", "mix": "из РП и ЧП"}
PAY_FIELDS = ('rate', 'unit', 'cur', 'days', 'note', 'bonus')
# Премию за стаж выплачивают из двух рук сразу — считаем и пишем по очереди.
_TENURE_LOCK = asyncio.Lock()


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
    out = {d: dict(gross=0, cash=0, crypto=0, card=0, debt=0, free=0, tips=0, tips_cash=0, orders=0,
                   cash_by=dict()) for d in days}
    for o in orders:
        day = bizday.order_day(o)
        s = out.get(day)
        if s is None:
            continue
        total = int(o.get("total") or 0)
        m = str(o.get("payment_method") or "").lower()
        # «Без оплаты» — товар уехал, денег нет: в выручку дня такой заказ не
        # входит вовсе, считаем его отдельно (владелец, 12 сен 2026)
        if m == "free":
            s["free"] += total
            continue
        s["gross"] += total
        s["orders"] += 1
        s["tips"] += int(o.get("tip") or 0)
        # Раздельная оплата (владелец, 9 окт 2026): части — по своим столбцам,
        # наличная часть — в наличные района.
        parts = cash_math.pay_parts(o)
        if parts is not None:
            s["cash"] += parts["cash"]
            s["crypto"] += parts["crypto"]
            s["card"] += parts["transfer"]
            if parts["cash"]:
                s["tips_cash"] += int(o.get("tip") or 0)
                oid = o.get("office_id") or ""
                s["cash_by"][oid] = s["cash_by"].get(oid, 0) + parts["cash"]
            continue
        if m == "debt":
            s["debt"] += total
        elif m == "crypto" or o.get("crypto_paid"):
            s["crypto"] += total
        elif _is_prepaid(o):
            s["card"] += total
        else:
            s["cash"] += total
            s["tips_cash"] += int(o.get("tip") or 0)
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
        for d in _staff.all_drivers():
            home[d.get("name")] = d.get("district") or ""
    except Exception:                             # noqa: BLE001
        pass
    # card — согласованное, оплаченное безналом (18 сен 2026): расход дня,
    # но наличных водителя не тронул, и из выручки дня его не вычитаем.
    out = {d: dict(spend=0, pending=0, card=0, kept=0, spend_by=dict()) for d in days}
    work: dict = {}
    for r in rows:
        s = out.get(r.get("day") or "")
        if s is None:
            continue
        w = r.get("working")
        if w is True and r.get("driver"):
            work[r["driver"]] = work.get(r["driver"], 0) + 1
        meal = _staff.meal_of(r)
        amt = meal
        for e in (r.get("extras") or []):
            st = str(e.get("status") or "approved")
            # Заказ в долг уже посчитан в выручке дня, а деньги по нему у
            # клиента: расходом он не становится (владелец, 20 сен 2026).
            if e.get("nocash") or _exp.is_bottle(e):      # бутылка охране — товар, не деньги
                continue
            # Зарплата, оставленная себе из наличных смены (владелец, 3 окт
            # 2026): из выручки дня вычитается, но расход водителя — не она,
            # расход фонда «зарплата» уже записан (pay=hands).
            if st == "approved" and e.get("salary_of"):
                s["kept"] += _exp._signed(e)
                continue
            if st == "approved":
                amt += _exp._signed(e)
                if _exp.is_card(e):
                    s["card"] += _exp._signed(e)
            elif st == "pending" and not _exp.is_card(e):
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
            # Чего на другой базе не оказалось — не покупали, и в сумму этой
            # закупки оно не входит.
            if extra:
                qty -= int(sum(float(((t or {}).get("na") or {}).get(pid) or 0)
                               for t in (sup.get("tasks") or {}).values()))
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
    rows += [dict(name=n, group="car") for n in CAR_NAMES]
    rows += [dict(name=n, group="home", kind="pool") for n in HOME_NAMES]
    rows += [dict(name=n, kind="pool") for n in ("Билеты", "Визы")]
    rows += [dict(name=n, group="sim", kind="pool") for n in SIM_NAMES]
    rows += [dict(name="Бензин", kind="pool")]
    rows += [dict(name=n, group="ads") for n in ADS_NAMES]
    rows += [dict(name=OTHER_NAME, kind="pool")]
    return rows


# «Что-то ещё» — расход, которому не нашлось своей статьи (владелец, 30 сен
# 2026: «где все расходы в РП — зарплаты, сим-карты и т. д. — добавь расход
# „что-то ещё“»). Статья без даты; что это было — говорит комментарий, и без
# него запись не принимается. Есть в каждом заполненном бюджете: см. _budget.
OTHER_NAME = "Что-то ещё"
GROUPS = ("", "rent", "auto", "home", "car", "ads", "sim")
AUTO_NAMES = ("Гараж и ТО", "Парковка", "Страховка/Пассинг")
AUTO_LEGACY = ("Авто", "Аренда")  # так статьи назывались до 11 сен 2026
# Аренда машин («Аренда» внутри «Расходов на автомобили»): у кого арендуем —
# строки группы car, каждая с периодом и датой платежа, как здания.
CAR_NAMES = ("Орион Рент", "Алексей Рент", "Другой Рент")
HOME_NAMES = ("Хоз. нужды", "Продукты", "Коммуналка")   # «Бытовые расходы»
# «Реклама»: виды рекламы, каждая с графиком и суммой в AED или $ (по курсу дня)
ADS_NAMES = ("Посты", "Интеграция бота")
# «Sim»: два подрасхода без даты
SIM_NAMES = ("Покупка", "Пополнение")
# Статья без даты платежа (kind="pool"): просто бюджет на месяц, без периода
# и календаря, правится прямо в списке; старые строки — по названию.
POOL_NAMES = ("Гараж и ТО", "Парковка", "Страховка/Пассинг", "Билеты", "Визы", "Sim", "Бензин",
              OTHER_NAME) + HOME_NAMES
MAX_PERIOD = 24


def _add_months(day: str, n: int) -> str:
    """Та же дата через n месяцев; 31-го → последнее число короткого месяца."""
    d = datetime.strptime(day, "%Y-%m-%d")
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d.day, calendar.monthrange(y, m)[1]):02d}"


def _add_days(day: str, n: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _schedule(ln: dict, month: str, today: str) -> dict:
    """Платёж раз в period месяцев от даты next: когда следующий (первое окно,
    которое ещё не прошло) и попадает ли платёж в этот месяц. Без даты — как
    ежемесячный: план каждый месяц, следующего платежа нет.

    Платёж бывает не днём, а промежутком (рент машин: «со 2 по 5»): span —
    сколько дней он длится после next, окно закрывается по next_to."""
    try:
        period = max(1, min(MAX_PERIOD, int(ln.get("period") or 1)))
    except (TypeError, ValueError):
        period = 1
    try:
        span = max(0, min(MAX_SPAN, int(ln.get("span") or 0)))
    except (TypeError, ValueError):
        span = 0
    nxt = str(ln.get("next") or "")
    try:
        datetime.strptime(nxt, "%Y-%m-%d")
    except ValueError:
        nxt = ""
    if not nxt:
        return dict(period=period, span=0, next="", next_due="", next_to="", due_in=period == 1)
    first, last = month + "-01", _month_days(month)[-1]
    # платёж в этом месяце — любое окно ряда next ± k·period, задевшее месяц
    due_in = False
    for k in range(-40, 41):
        d = _add_months(nxt, k * period)
        if d <= last and _add_days(d, span) >= first:
            due_in = True
            break
        if d > last and k >= 0:
            break
    due = nxt
    while _add_days(due, span) < today:        # окно ещё идёт — дата не убегает
        due = _add_months(due, period)
    return dict(period=period, span=span, next=nxt, next_due=due,
                next_to=_add_days(due, span), due_in=due_in)


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
    # «Что-то ещё» есть в каждом заполненном бюджете: месяц, заполненный до
    # 30 сен 2026 (или из прошлого месяца), статьи не имел — дописываем.
    if lines and not any((ln.get("name") or "") == OTHER_NAME for ln in lines):
        try:
            doc = {"_id": secrets.token_hex(4), "month": month, "name": OTHER_NAME, "plan": 0, "due": 0,
                   "note": "", "kind": "pool", "group": "", "cur": "AED", "period": 1, "next": "",
                   "span": 0, "ord": max([int(ln.get("ord") or 0) for ln in lines] + [len(lines)]) + 1,
                   "by": ""}
            await db.fin_budget_set(doc)
            lines.append(doc)
        except Exception as e:                    # noqa: BLE001
            log.warning(f"[fin] статья «{OTHER_NAME}» не добавлена: {e}")
    fact: dict = {}
    off_plan = 0.0
    sal_fact = 0.0
    for e in entries:
        if e.get("book", "rp") != "rp":
            continue
        if e.get("kind") in pay.PAY_KINDS:
            sal_fact += calc._n(e.get("amount"))
            continue
        # Внесённый депозит/аванс — не факт статьи: аренда этим не оплачена,
        # деньги лежат у контрагента (владелец, 5 окт 2026). Списание из него
        # в оплату — факт целиком: счёт закрыт, пусть и не наличными.
        if e.get("deposit"):
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
    car = dict(plan=0.0, fact=0.0, n=0)       # аренда машин — входит и в «Расходы на автомобили»
    ads = dict(plan=0.0, fact=0.0, n=0)       # «Реклама»
    sim = dict(plan=0.0, fact=0.0, n=0)       # «Sim»
    usd = calc._n(salary.get("usd")) or 3.67  # сумма статьи в $ считается в AED по курсу месяца
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
        cur = "USD" if ln.get("cur") == "USD" else "AED"
        plan_aed = plan * usd if cur == "USD" else plan
        # платёж раз в N месяцев делится поровну на каждый месяц: доля входит в
        # план месяца и в норму дня, и к дате платежа сумма уже отложена
        # (владелец: «не узнавать сюрпризом»); следующий платёж — рядом
        plan_m = plan_aed / sch["period"]
        total += plan_m; fact_sum += f
        if group == "rent":
            rent["plan"] += plan_m; rent["fact"] += f; rent["n"] += 1
        elif group == "auto":
            autog["plan"] += plan_m; autog["fact"] += f; autog["n"] += 1
        elif group == "home":
            home["plan"] += plan_m; home["fact"] += f; home["n"] += 1
        elif group == "car":
            car["plan"] += plan_m; car["fact"] += f; car["n"] += 1
            autog["plan"] += plan_m; autog["fact"] += f
        elif group == "ads":
            ads["plan"] += plan_m; ads["fact"] += f; ads["n"] += 1
        elif group == "sim":
            sim["plan"] += plan_m; sim["fact"] += f; sim["n"] += 1
        rows.append(dict(id=ln.get("_id"), name=name, plan=calc._i(plan), cur=cur, plan_aed=calc._i(plan_aed), plan_m=calc._i(plan_m),
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
    car = dict(plan=calc._i(car["plan"]), fact=calc._i(car["fact"]),
               left=calc._i(car["plan"] - car["fact"]), n=car["n"])
    ads = dict(plan=calc._i(ads["plan"]), fact=calc._i(ads["fact"]),
               left=calc._i(ads["plan"] - ads["fact"]), n=ads["n"])
    sim = dict(plan=calc._i(sim["plan"]), fact=calc._i(sim["fact"]),
               left=calc._i(sim["plan"] - sim["fact"]), n=sim["n"])
    return dict(lines=rows, salary=salary, rent=rent, auto=autog, home=home, car=car, ads=ads, sim=sim, total=calc._i(total), fact=calc._i(fact_all),
                left=calc._i(total - fact_all), off_plan=calc._i(off_plan),
                days=ndays, per_day=calc._i(total / ndays) if ndays and total else 0,
                norm_auto=auto, norm=calc._i(calc._n(norm)) if norm is not None else auto,
                norm_set=norm is not None,
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
            # Зарплата в долларах — по цене, за которую доллар продаёт обменник
            # (Al Ansari, 19 сен 2026: 3.677): столько стоит купить эти доллары.
            # До 19 сен здесь был курс перевода с витрины (3.6805).
            auto = float(row.get("cash_buy_aed") or row.get("cash_aed") or row.get("aed") or 0) or None
            # Владелец, 23 сен 2026: «в зарплатах до сих пор число до тысячных,
            # надо до сотых». Режем сотые без округления — как в «Курсе валют».
            # Курс не только показывается, но и считает: округли мы его на одном
            # экране, сумма в дирхамах перестала бы сходиться с тем, что видно
            # (2 000 $ по 3.67 — это 7 340, а не 7 354). Вписанный руками курс
            # остаётся ровно таким, каким его вписали.
            if auto:
                auto = math.floor(round(auto * 10000) / 100) / 100
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] курс доллара не прочитан: {e}")
    return dict(usd=auto or USD_FALLBACK, usd_auto=auto, usd_set=False)


ROLE_RU = {"senior": "старший оператор", "operator": "оператор",
           "driver": "водитель", "other": "вписан руками"}
# Чем уточняем имя оператора, у которого есть тёзка-водитель.
ROLE_KEY = {"senior": "старший", "operator": "оператор"}


def _people(docs: list, dupes: list | None = None) -> list[dict]:
    """Кто получает зарплату: люди из расписания плюс вписанные руками.

    Человек здесь — это его имя: и оклад, и штрафы, и авансы лежат под именем.
    Поэтому два РАЗНЫХ человека с одним именем в ведомость не помещаются:
    второй молча исчезал (владелец, 23 сен 2026: «где водитель Парвиз? не
    старший оператор, а водитель» — у нас Парвиз и старший, и водитель на
    Бизнес Бей, и Фарух оператор и водитель на JVC, всё это разные люди).
    Молчать об этом нельзя: пропавший человек — это человек без зарплаты.
    Кого не поместили — складываем в dupes, и STAR говорит о нём красным."""
    import config_staff as staff
    from config_offices import OFFICE_IDS
    docs = sorted(docs, key=lambda d: str(d.get("created") or d.get("at") or ""))
    by_name = {str(d.get("_id")): d for d in docs}
    out, seen = [], {}
    try:
        водители = {str(d.get("name")) for d in staff.all_drivers() if d.get("name")}
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] водители не прочитаны: {e}")
        водители = set()

    def ключ(name, role):
        """Под каким именем человек живёт в ведомости.

        Имя отдаём ВОДИТЕЛЮ: по имени водителя ходит вся автоматика — смены,
        штрафы, урезанное питание, чай, удержания за бой. У оператора и
        старшего автоматики нет, только вписанное руками, поэтому тёзка-
        оператор живёт под уточнённым ключом: «Парвиз · старший». На экране
        он всё равно «Парвиз» — роль и так написана над списком.

        Уже заведённый ключ не меняем, даже если тёзка-водитель уволится:
        иначе оклад и авансы повисли бы в пустоте."""
        if role not in ROLE_KEY or not name:
            return name
        q = f"{name} · {ROLE_KEY[role]}"
        if q in by_name:
            return q
        return q if name in водители else name

    def add(name, role, title=None, roster=False, **kw):
        if not name:
            return
        if name in seen:
            # Жалуемся только на двоих ИЗ РАСПИСАНИЯ: это разные живые люди с
            # одним именем, и второму деньги считать негде. Повтор из карточек
            # (человека вписали руками, а потом он появился в расписании) —
            # тот же самый человек, и говорить не о чем.
            было = seen[name]
            if dupes is not None and roster and было["roster"] \
                    and not any(x["name"] == name and x["lost"] == role for x in dupes):
                dupes.append({"name": name, "kept": было["role"], "lost": role,
                              "kept_ru": ROLE_RU.get(было["role"], было["role"]),
                              "lost_ru": ROLE_RU.get(role, role)})
            return
        d = by_name.get(name) or {}
        seen[name] = {"role": d.get("role") or role, "roster": roster}
        if d.get("hidden"):
            return
        out.append(dict(name=name, title=title or name, role=d.get("role") or role,
                        manual=bool(d.get("manual")),
                        pnote=d.get("note") or "", work=pay.work_clean(d.get("work")), **kw))
    # Сначала те, кого вписали руками (руководство, старший), в порядке
    # добавления; потом расписание: старшие операторы, операторы, водители.
    for d in docs:
        if d.get("manual"):
            add(str(d.get("_id")), d.get("role") or "other", districts=[])
    try:
        for s in staff.SENIOR_OPERATORS:
            add(ключ(s.get("name"), "senior"), "senior", title=s.get("name"),
                roster=True, districts=list(OFFICE_IDS))
        for o in staff.operators():
            if not o.get("senior"):
                add(ключ(o.get("name"), "operator"), "operator", title=o.get("name"),
                    roster=True, districts=list(o.get("districts") or []))
        for d in staff.all_drivers():
            add(d.get("name"), "driver", roster=True, districts=[d.get("district") or ""])
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
        # Оклад в месяц — за дни на работе (периоды «вышел — уехал»); ставка
        # в день — на дни на работе, а без периодов на все дни месяца.
        w = pay.work_in(p.get("work"), month)
        plan = rate_aed * w["share"] if unit == "month" else rate_aed * (w["days"] if w["set"] else ndays)
        total += plan
        # водителю — его район: в бюджете водители лежат по районам, как везде
        dist = (p.get("districts") or [""])[0] if p["role"] == "driver" else ""
        people.append(dict(name=p["name"], role=p["role"], role_t=pay.ROLE_T.get(p["role"], ""),
                           manual=bool(p.get("manual")),
                           district=dist, district_code=OFFICE_CODES.get(dist, ""),
                           district_name=OFFICE_NAMES.get(dist, ""),
                           rate=None if eff.get("rate") is None else calc._i(rate),
                           unit=unit, cur=cur, rate_aed=calc._i(rate_aed), plan=calc._i(plan),
                           work_set=w["set"], work_days=w["days"], month_days=w["of"],
                           work_spans=w["spans"]))
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
        # Удержания по списаниям — в ту же ведомость, что штрафы: иначе «к
        # выплате» у старшего и у водителя врало бы на сумму боя.
        items = list(await db.fin_pay_items_get()) + await writeoff_holds()
        shifts = await db.shift_days_worked(days[0], min(days[-1], today))
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] зарплаты не прочитаны: {e}")
        docs, mdocs, items, shifts = [], [], [], []
    dupes: list = []
    people = _people(docs, dupes)
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
    # Штрафы, которые сформировала программа (fines_auto): ждущие — в окошко
    # «Штрафы, требующие решения», не назначенные — в историю с исходом.
    import fines_auto
    # Одно имя на двоих — красной строкой в ведомости: человек, которого в ней
    # нет, зарплату не получит, а штраф однофамильца упадёт на чужую строку.
    res["dupes"] = dupes
    res["pending"] = await fines_auto.pending()
    res["history"] = _penalty_history(items, month, decided=await fines_auto.decided())
    # Правила штрафов — те же, что на экране «Правила»: лист берёт нарушения
    # и суммы отсюда, а не из кода приложения.
    import fine_rules
    res["rules"] = await fine_rules.get()
    return res


async def writeoff_holds(name: str = "") -> list:
    """Удержания по списаниям — теми же записями, что штрафы и удержания из
    «Финансов», но считаются с самих списаний (writeoffs.comp), а не хранятся
    второй раз: одно событие — одно место (владелец, 14 сен 2026: «удержания
    тоже минусуй водителям зарплату»). Снял удержание в списании — запись
    исчезает и отсюда. Всё сразу в месяц списания, без графика."""
    try:
        rows = await db.writeoff_comps(name)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] удержания по списаниям не прочитаны: {e}")
        return []
    out = []
    for r in rows:
        c = r.get("comp") or {}
        if not int(c.get("amount") or 0):
            continue
        day = str(r.get("day") or "")[:10] or str(r.get("at") or "")[:10]
        kind = str(r.get("kind") or "списание").strip()
        what = (f"{kind[:1].upper()}{kind[1:]} · {r.get('name') or r.get('item') or 'товар'}"
                f" × {int(r.get('qty') or 0)}")
        parts = c.get("split") or [{"who": c.get("who") or "", "amount": c.get("amount")}]
        for i, part in enumerate(parts):
            who = str(part.get("who") or "").strip()
            amount = int(part.get("amount") or 0)
            if not who or amount <= 0 or (name and who != name):
                continue
            out.append({"_id": f"wo:{r.get('_id')}:{i}", "name": who, "kind": "hold",
                        "amount": amount, "per_month": 0, "from": day[:7], "day": day,
                        "note": c.get("note") or "", "reason": what,
                        "by": c.get("by_name") or "", "at": c.get("at") or r.get("at"),
                        "src": "writeoff", "wid": r.get("_id")})
    return out


async def pay_month(month: str) -> dict:
    """Ведомость одного месяца без всей книги (выручки, поставок, сейфа): для
    «следующего месяца» в профиле водителя — сколько он получит, если часть
    или всю зарплату уже выдали наперёд."""
    days = _month_days(month)
    try:
        entries = await db.fin_entries_get(days[0], days[-1])
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] записи {month} не прочитаны: {e}")
        entries = []
    try:
        work = (await _spend(days)).get("_work", {})
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] дни работы {month} не прочитаны: {e}")
        work = {}
    fx = await _usd(await db.fin_month_get(month))
    return await _payroll(month, days, _biz_day(), entries, work, fx["usd"])


async def person_card(name: str, month: str) -> dict:
    """Зарплата и списания одного человека за месяц — для профиля в приложении
    водителя. Отдаём только его: чужие суммы туда не попадают."""
    book = await build(month)
    pay_ = book.get("pay") or {}
    p = next((x for x in (pay_.get("people") or []) if x.get("name") == name), None)
    try:
        raw = [i for i in await db.fin_pay_items_get() if str(i.get("name") or "") == name]
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] удержания {name} не прочитаны: {e}")
        raw = []
    raw += await writeoff_holds(name)              # удержания по бою, браку, утере
    items, fines, holds, cnt = [], 0.0, 0.0, 0
    for it in sorted(raw, key=lambda x: str(x.get("at") or x.get("day") or ""), reverse=True):
        if it.get("kind") == "bonus":
            continue
        sch = pay.schedule(it, month)
        gone = bool(it.get("cancelled_at"))
        due = 0.0 if gone else sch["due"]
        if due > 0:
            cnt += 1
            if it.get("kind") == "fine":
                fines += due
            else:
                holds += due
        items.append(dict(id=str(it.get("_id")), kind=it.get("kind"), t=pay.KINDS.get(it.get("kind"), ""),
                          amount=pay._i(pay._n(it.get("amount"))), per_month=pay._i(pay._n(it.get("per_month"))),
                          day=it.get("day") or "", at=str(it.get("at") or ""),  # время — в историю списаний
                          start=str(it.get("from") or "")[:7],
                          reason=it.get("reason") or "", note=it.get("note") or "",
                          due=pay._i(due), left=0 if gone else sch["after"], done=False if gone else sch["done"],
                          cancelled=gone, src=it.get("src") or "", wid=it.get("wid") or "",
                          **({"auto": it["auto"], "text": it.get("reason") or ""} if it.get("auto") else {})))
    # Решения по тому, что сформировала программа: прощённое и урезанное
    # питание — строками в той же истории (из зарплаты они не вычитаются).
    import fines_auto
    items = sorted(items + await fines_auto.for_person(name), key=lambda x: str(x.get("at") or x.get("day") or ""),
                   reverse=True)
    out = dict(month=month, name=name, fines=pay._i(fines), holds=pay._i(holds),
               month_total=pay._i(fines + holds), month_count=cnt, items=items,
               usd=pay_.get("usd"), found=bool(p))
    # Премия за стаж — человеку в его же приложении: сколько он наработал,
    # что уже получил и когда будет следующая (владелец, 23 сен 2026: «это всё
    # ещё надо подвязать к водителям, чтобы они видели, когда у них какая
    # премия»). Чужого здесь нет: только он сам.
    try:
        import tenure
        if p and (p.get("role") or "") in tenure.ROLES:
            out["tenure"] = tenure.driver_view(tenure.state(p, raw, _biz_day()))
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] премия за стаж {name} не посчитана: {e}")
    for k in ("role", "rate", "rate_aed", "unit", "cur", "days", "days_auto", "days_set",
              "accrued", "plus", "minus", "to_pay", "paid", "left", "debt", "payouts",
              "bonus_month", "bonus_due", "bonus_once", "advance", "loan",
              "work_set", "work_days", "month_days", "work_spans"):
        out[k] = (p or {}).get(k)
    # На работе ли сейчас и с какого числа — «Работает с …» в профиле; дальше
    # к этому привяжется всё, что зависит от выхода на работу.
    out["work_now"] = pay.work_now((p or {}).get("work"), _biz_day())
    # Авансы месяца — строками: «зарплата наперёд» или «часть зарплаты», когда
    # выдан и сколько снимается в этом месяце.
    out["advances"] = [dict(id=r.get("id"), t=r.get("t"), mode=r.get("mode") or "", amount=r.get("amount"),
                            due=r.get("due"), day=r.get("day") or "", start=r.get("start") or "",
                            after=r.get("after"))
                       for r in ((p or {}).get("items") or []) if r.get("kind") == "advance" and r.get("due")]
    # Следующий месяц: оклад, премия и что уже выдано наперёд (владелец, 19 сен
    # 2026: «сколько в следующем с учётом того, что часть или всю зарплату
    # выплатили уже в прошлом месяце»).
    nxt = pay.next_month(month)
    try:
        q = next((x for x in ((await pay_month(nxt)).get("people") or []) if x.get("name") == name), None)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] следующий месяц {name}: {e}")
        q = None
    out["next"] = None if not q else dict(
        month=nxt, accrued=q.get("accrued"), plus=q.get("plus"), bonus_month=q.get("bonus_month"),
        advance=q.get("advance"), fines=q.get("fines"), holds=(q.get("holds") or 0) + (q.get("loan") or 0),
        to_pay=q.get("to_pay"), unit=q.get("unit"),
        advances=[dict(t=r.get("t"), mode=r.get("mode") or "", due=r.get("due"), day=r.get("day") or "")
                  for r in (q.get("items") or []) if r.get("kind") == "advance" and r.get("due")])
    out["role_t"] = {"driver": "Водитель", "operator": "Оператор",
                     "senior": "Старший оператор", "other": "Старший"}.get((p or {}).get("role") or "", "")
    return out


def _penalty_history(items: list, month: str, limit: int = 60, decided: list | None = None) -> list:
    """История штрафов и удержаний для «Штрафов/авансов/долгов»: последние
    сверху, отменённые и пересмотренные — с пометкой. От людей не зависит: кого
    убрали из зарплат, того штрафы тоже видно. decided — решения по тому, что
    сформировала программа (fines_auto), у которых нет записи в зарплатах:
    «не назначать / не урезать» (declined) и «урезать питание» (meal) — в
    истории они с исходом."""
    import fines_auto
    out = []
    # решение стоит в истории по времени решения, как штраф — по времени записи
    dec = [dict(d, _dec=True, at=d.get("decided_at") or d.get("at")) for d in (decided or [])]
    for it in sorted(list(items) + dec, key=lambda x: str(x.get("at") or x.get("day") or ""), reverse=True):
        if it.get("_dec"):
            meal = fines_auto.action_of(it.get("kind") or "") == "meal"
            out.append(dict(id="auto:" + str(it.get("_id")), name=it.get("name") or "", kind="fine",
                            t=pay.KINDS["fine"], per_month=0, start="", day=it.get("day") or "",
                            amount=(fines_auto.MEAL_FROM - fines_auto.MEAL_TO) if meal
                            else pay._i(pay._n(it.get("amount"))),
                            note="", reason=fines_auto.full_text(it),
                            by=it.get("decided_by") or "", cancelled=False, cancelled_by="",
                            cancelled_day="", revised=False, revised_by="", src="", wid="", was=None,
                            left=0, done=False, auto=it.get("kind") or "",
                            declined=it.get("status") == "declined", meal=meal))
            if len(out) >= limit:
                break
            continue
        if it.get("kind") not in pay.PENALTY_KINDS:
            continue
        s = pay.schedule(it, month)
        gone = bool(it.get("cancelled_at"))
        out.append(dict(id=str(it.get("_id")), name=it.get("name") or "", kind=it.get("kind"),
                        t=pay.KINDS.get(it.get("kind"), ""), amount=pay._i(pay._n(it.get("amount"))),
                        per_month=pay._i(pay._n(it.get("per_month"))), start=str(it.get("from") or "")[:7],
                        day=it.get("day") or "", note=it.get("note") or "", reason=it.get("reason") or "",
                        by=it.get("by") or "", cancelled=gone, cancelled_by=it.get("cancelled_by") or "",
                        cancelled_day=str(it.get("cancelled_at") or "")[:10],
                        revised=bool(it.get("revised_at")), revised_by=it.get("revised_by") or "",
                        src=it.get("src") or "", wid=it.get("wid") or "",
                        was=None if it.get("was") is None else pay._i(pay._n(it.get("was"))),
                        left=0 if gone else s["after"], done=False if gone else s["done"],
                        auto=it.get("auto") or "", declined=False, meal=False))
        if len(out) >= limit:
            break
    return out


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
        if isinstance(cache, dict) and "np_open" in cache:
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
    if opening.get("np_open") is None and (doc.get("carry_np") is not None or doc.get("storage") is not None):
        opening["np_open"] = calc._n(doc.get("carry_np")) + calc._n(doc.get("storage"))   # старые поля
    return {"opening": opening, "explicit": explicit, "carried": carried,
            "note": doc.get("note") or "", "doc": doc}


def _entry_view(e: dict, line_names: dict) -> dict:
    lid = e.get("line") or ""
    return {"id": e.get("_id"), "amount": e.get("amount"), "comment": e.get("comment") or "",
            "who": e.get("who") or "", "line": lid, "line_name": line_names.get(lid, ""),
            "kind": e.get("kind") or "", "kind_t": {"salary": "Зарплата", "advance": "Аванс",
                                                    "loan": "Долг"}.get(e.get("kind") or "", ""),
            "item": e.get("item") or "", "by": e.get("by") or "", "at": str(e.get("at") or ""),
            "day": e.get("day") or "", "pay_month": e.get("pay_month") or "", "photo": bool(e.get("photo")),
            "route_from": e.get("route_from") or "", "route_to": e.get("route_to") or "",
            "src": e.get("src") or "",
            # перевод между стопками: откуда и куда
            "from": e.get("from") or "", "to": e.get("to") or "",
            # чем платили расход из РП; у вывода крипты — сколько из свободной
            # и сколько с крипта-счёта РП
            "pay": e.get("pay") or "", "cr_free": e.get("cr_free") or 0, "cr_rp": e.get("cr_rp") or 0,
            # зарплата из наличных на руках у водителя: за какую смену и какой
            # записью расхода водителя она вычтена
            "hands_day": e.get("hands_day") or "", "hands_extra": e.get("hands_extra") or "",
            # комиссия вывода: вся (в USDT и дирхамах), наша половина, сколько
            # её взято из свободной крипты; у строки расхода — к какому выводу
            "fee_usdt": e.get("fee_usdt") or 0, "fee": e.get("fee") or 0,
            "fee_ours": e.get("fee_ours") or 0, "fee_free": e.get("fee_free") or 0,
            "fee_entry": e.get("fee_entry") or "", "fee_of": e.get("fee_of") or "",
            # депозит (владелец, 4 окт 2026): у расхода — отметка; у прихода —
            # какой депозит возвращён и сколько при этом удержали
            "deposit": bool(e.get("deposit")), "dep_of": e.get("dep_of") or "", "lost": e.get("lost") or 0,
            # сколько из суммы покрыто депозитом/авансом, который уже лежал у контрагента
            "dep_use": e.get("dep_use") or 0}


async def _deposits() -> list:
    """Все депозиты книги (владелец, 4 окт 2026): внесено расходом РП с отметкой
    «депозит», возвращено записями Доп. РП+ (src=deposit, dep_of). За все
    месяцы: депозит ренткара живёт дольше месяца. Открытые — сверху."""
    try:
        paid = await db.fin_entries_where({"book": "rp", "deposit": True})
        back = await db.fin_entries_where({"book": "in", "src": "deposit"})
        # списано в оплату (владелец, 5 окт 2026): расходы, покрытые депозитом —
        # у каждого записано, из каких депозитов и сколько (dep_uses)
        spent = await db.fin_entries_where({"book": "rp", "dep_uses": {"$exists": True}})
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] депозиты не прочитаны: {e}")
        return []
    by: dict = {}
    for r in back:
        b = by.setdefault(str(r.get("dep_of") or ""), {"back": 0.0, "lost": 0.0, "used": 0.0, "last": ""})
        b["back"] += calc._n(r.get("amount")); b["lost"] += calc._n(r.get("lost"))
        b["last"] = max(b["last"], str(r.get("day") or ""))
    for r in spent:
        for u in (r.get("dep_uses") or []):
            b = by.setdefault(str(u.get("of") or ""), {"back": 0.0, "lost": 0.0, "used": 0.0, "last": ""})
            b["used"] += calc._n(u.get("amount"))
            b["last"] = max(b["last"], str(r.get("day") or ""))
    out = []
    for e in paid:
        b = by.get(str(e.get("_id")), {"back": 0.0, "lost": 0.0, "used": 0.0, "last": ""})
        amount = calc._n(e.get("amount"))
        left = max(0.0, amount - b["back"] - b["lost"] - b["used"])
        ln = await db.fin_budget_line_get(str(e.get("line"))) if e.get("line") else None
        out.append({"id": e.get("_id"), "day": e.get("day") or "", "at": str(e.get("at") or ""),
                    "amount": calc._i(amount),
                    "comment": e.get("comment") or "", "who": e.get("who") or "",
                    "line": e.get("line") or "", "line_name": (ln or {}).get("name") or "",
                    "group": _line_group(ln) if ln else "",
                    "back": calc._i(b["back"]), "lost": calc._i(b["lost"]), "used": calc._i(b["used"]),
                    "left": calc._i(left), "last": b["last"], "open": left > 0.005})
    out.sort(key=lambda x: x["day"], reverse=True)
    out.sort(key=lambda x: not x["open"])
    return out


async def _line_deposits(ln: dict) -> list:
    """Открытые депозиты/авансы статьи — за все месяцы, старые первыми. Статья
    живёт по месяцам своим id, поэтому сшиваем по id и по названию (депозит
    ренткару внесли в сентябре — списывают в октябре)."""
    name = str((ln or {}).get("name") or "")
    lid = str((ln or {}).get("_id") or "")
    rows = [x for x in await _deposits() if x["open"] and (x["line"] == lid or (name and x["line_name"] == name))]
    rows.sort(key=lambda x: (x["day"], x.get("at") or "", x["id"]))      # старые первыми: день, затем время записи
    return rows


def _dep_allocate(amount: float, rows: list) -> list:
    """Разложить списание по открытым депозитам статьи — старые первыми:
    [{of: id депозита, amount}]. Больше остатка не раскладывается."""
    out, rest = [], float(amount)
    for x in rows:
        if rest <= 0.005:
            break
        take = min(rest, calc._n(x.get("left")))
        if take > 0.005:
            out.append({"of": x["id"], "amount": calc._i(take)})
            rest -= take
    return out


async def build(month: str, depth: int = 0, light: bool = False) -> dict:
    days = _month_days(month)
    today = _biz_day()
    if depth == 0:
        try:
            await _budget_carry(month)
        except Exception as e:                        # noqa: BLE001
            log.warning(f"[fin] бюджет {month} не перенесён: {e}")
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
        by_day_entries.setdefault(e.get("day"), {"rp": [], "np": [], "in": [], "mv": []})[bk].append(
            _entry_view(e, line_names))
    # Книга крипты (crypto_book): сколько свободной сейчас и сколько на счету
    # РП было на начало месяца. Свободную предлагаем целиком в РП+ первого дня,
    # который ждёт подтверждения; следующему ждущему — уже ноль.
    import crypto_book
    cb = await crypto_book.state()
    opening["opening"]["rp_cr_open"] = crypto_book.rp_before(cb["by_day"], days[0])
    free_run = cb["free_fact"] if cb["ready"] else 0.0     # по факту на кошельке, не по книге
    # Сверка смены (shift_recon, владелец, 9 окт 2026): разница по факту —
    # подтверждённая оператором у водителя или вписанная старшим в «Сборе
    # выручки» — встаёт в «Собрал по факту», пока число не вписано руками здесь.
    try:
        import shift_recon
        recon_gaps = await shift_recon.book_gaps(days[0], days[-1])
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] сверка {month}: {e}")
        recon_gaps = {}
    rows, meta = [], {}
    for d in days:
        s, sp, pu, m = sales[d], spend[d], purch[d], manual.get(d) or {}
        en = by_day_entries.get(d) or {"rp": [], "np": [], "in": [], "mv": []}
        # выручка дня — то, что старший собирает наличными: наличные заказов
        # минус чай (он водителя) минус расходы водителей наличными; как в
        # обзоре. Оплаченное безналом наличных не тронуло — не вычитаем.
        handed = s["cash"] - s["tips_cash"] - (sp["spend"] - sp["card"]) - sp["kept"]
        past = d <= today
        fact = m.get("handed_fact")
        fact_src = "manual" if fact is not None else ""
        if fact is None and past and recon_gaps.get(d):
            fact, fact_src = handed + recon_gaps[d], "recon"
        base = handed if fact is None else calc._n(fact)
        ordered = m["ordered_fact"] if m.get("ordered_fact") is not None else pu["ordered"]
        # предложение раскладки (владелец, 12 сен 2026): Барракуде — ровно
        # половина выручки, в РП+ — норма дня из бюджета, остаток — ЧП+;
        # старший подтверждает или правит, вписанное руками главнее
        aside, aside_src = m.get("aside"), "manual"
        if aside is None:
            aside_src = ""
            if past and base > 0:
                aside, aside_src = float(int(base // 2)), "half"
        # РП+ криптой (владелец, 2 окт 2026: «сначала считай, сколько криптой
        # пришло на кошелёк, потом — сколько докинуть из налички»). У
        # подтверждённого дня сумма заморожена. У ждущего: вписанное руками
        # (но не больше свободной), иначе — вся свободная крипта.
        cr, cr_src = 0.0, ""
        if m.get("ok"):
            cr, cr_src = calc._n(m.get("collected_cr")), "manual"
        elif past and base > 0 and d >= crypto_book.START_DAY and cb["ready"]:
            if m.get("collected_cr") is not None:
                cr, cr_src = min(calc._n(m.get("collected_cr")), max(0.0, free_run)), "manual"
            else:
                cr, cr_src = max(0.0, free_run), "free"
            cr = round(cr, 2)
            free_run = round(free_run - cr, 2)
        collected, collected_src = m.get("collected"), "manual"
        if collected is None:
            collected_src = ""
            if past and base > 0 and budget["norm"]:
                # наличными — то, чего до нормы не хватило после крипты; до
                # целого дирхама вверх: крипта бывает с копейками (USDT × 3.5),
                # а наличные копейками не докладывают
                collected = float(min(math.ceil(max(0.0, calc._n(budget["norm"]) - cr) - 1e-9),
                                      max(0.0, base - calc._n(aside))))
                collected_src = "norm"
        extra_in = sum(calc._n(x.get("amount")) for x in en["in"])
        cr_cash = sum(calc._n(x.get("cr_rp")) for x in en["in"] if x.get("src") == "crypto")
        # наша половина комиссии вывода, взятая из свободной крипты: на счёт РП
        # она приходит и тут же тратится записью РП− «криптой»
        extra_cr = sum(calc._n(x.get("fee_free")) for x in en["in"] if x.get("src") == "crypto")
        # возврат депозита (владелец, 4 окт 2026): пришёл в фонд через Доп. РП+
        # (он уже в extra_in); удержанное контрагентом — отдельным числом
        dep_back = sum(calc._n(x.get("amount")) for x in en["in"] if x.get("src") == "deposit")
        dep_lost = sum(calc._n(x.get("lost")) for x in en["in"] if x.get("src") == "deposit")
        rows.append(dict(
            day=d, gross=s["gross"], cash=s["cash"], card=s["card"], crypto=s["crypto"],
            debt=s["debt"], tips=s["tips"], spend=sp["spend"], handed=handed,
            handed_fact=fact, handed_src=fact_src, ordered=ordered, ordered_extra=pu["ordered_extra"], kept=sp["kept"],
            aside=aside, collected=collected, collected_cr=cr, cr_cash=cr_cash, extra_cr=extra_cr,
            extra_rp=calc._n(m.get("extra_rp")) + extra_in,
            dep_back=dep_back, dep_lost=dep_lost,
            pay=m.get("pay"), pay_b=m.get("pay_b"), pay_b_extra=m.get("pay_b_extra"),
            pay_src=m.get("pay_src") or "", pay_rp=m.get("pay_rp"), norm=budget["norm"],
            ok=bool(m.get("ok")), pending=past and base > 0 and not m.get("ok"),
            expenses=en["rp"], payouts=en["np"], moves=en["mv"]))
        meta[d] = dict(aside_src=aside_src, collected_src=collected_src, cr_src=cr_src, ins=en["in"],
                       extra_manual=m.get("extra_rp"), ok_by=m.get("ok_by") or "",
                       tips_cash=s["tips_cash"], base=base)
    book = calc.compute(rows, opening["opening"])
    # Долг фонда (владелец, 3 окт 2026): день, который ждёт подтверждения,
    # предлагает собрать в РП+ ещё и то, что фонд отдал Барракуде и не вернул —
    # в пределах выручки дня. Первый расчёт даёт долг на каждый день, второй —
    # раскладку с его учётом; на сам долг она не влияет (ждущие дни в стопки
    # не входят), поэтому двух проходов достаточно.
    changed = False
    for i, d in enumerate(days):
        r, row = book["days"][i], rows[i]
        if row["pending"] and meta[d]["collected_src"] == "norm" and calc._n(r["rp_owed_before"]) > 0:
            cap = max(0.0, meta[d]["base"] - calc._n(row["aside"]))
            want = math.ceil(max(0.0, calc._n(budget["norm"]) - calc._n(row["collected_cr"]))
                             + calc._n(r["rp_owed_before"]) - 1e-9)
            new = float(min(want, cap))
            if new != row["collected"]:
                row["collected"] = new
                changed = True
    if changed:
        book = calc.compute(rows, opening["opening"])
    marks = {} if light else await _marks([d for d in days if d <= today])
    for i, d in enumerate(days):
        r, s, sp, pu, m = book["days"][i], sales[d], spend[d], purch[d], manual.get(d) or {}
        r.update(orders=s["orders"], debt=s["debt"], spend_pending=sp["pending"],
                 spend_cash=calc._i(sp["spend"] - sp["card"]), spend_card=calc._i(sp["card"]),
                 ordered_auto=pu["ordered"], ordered_fact=m.get("ordered_fact"),
                 ordered_cover=pu["cover"], supplies=pu["supplies"],
                 note=m.get("note") or "", future=d > today, today=d == today,
                 aside_src=meta[d]["aside_src"], collected_src=meta[d]["collected_src"],
                 cr_src=meta[d]["cr_src"],
                 ins=meta[d]["ins"], extra_manual=meta[d]["extra_manual"],
                 ok_by=meta[d]["ok_by"], tips_cash=meta[d]["tips_cash"],
                 pay_by=m.get("pay_by") or "",
                 pay_at=_utc_iso(m["pay_at"]) if isinstance(m.get("pay_at"), datetime) else str(m.get("pay_at") or ""),
                 pending=bool(d <= today and r["base"] > 0 and not r["ok"]),
                 salary_sum=calc._i(sum(calc._n(e["amount"]) for e in r["expenses"]
                                        if e.get("kind") in ("salary", "advance", "loan"))))
        r["manual"] = {k: m.get(k) for k in calc.DAY_MANUAL}
        r["manual"]["collected_cr"] = m.get("collected_cr")
        # Сколько из РП+ дня — возврат долга фонда: у подтверждённого дня
        # считает ядро, у ждущего — по предложению (или вписанному руками).
        if r["pending"] and calc._n(budget["norm"]) > 0:
            r["rp_back_plan"] = calc._i(min(calc._n(r["rp_owed_before"]),
                                            max(0.0, calc._n(r["collected"]) + calc._n(r["collected_cr"])
                                                - calc._n(budget["norm"]))))
        else:
            r["rp_back_plan"] = r["rp_back"]
        if not light:
            mk = marks.get(d) or {}
            need = set(s["cash_by"]) | set(k for k, v in sp["spend_by"].items() if v)
            need.discard("")
            legacy = bool((mk.get("cash") or {}).get("done"))
            got = sum(1 for oid in need if legacy or (mk.get(f"cash:{oid}") or {}).get("done"))
            r.update(cash_need=len(need), cash_got=got)
    book.update(month=month, today=today, first=days[0], last=days[-1],
                opening=opening["opening"], opening_explicit=opening["explicit"],
                opening_carried=opening["carried"], month_note=opening["note"],
                prev_month=_prev_month(month), budget=budget,
                # крипта сейчас: свободная и на счету РП — экранам, которые
                # предлагают раскладку и спрашивают, чем платили
                crypto={**{k: cb[k] for k in ("ready", "rate", "rp", "book", "start_day")},
                        "free": cb["free_fact"], "free_book": cb["free"], "wallet": cb.get("wallet")},
                # депозиты за все месяцы: что лежит у контрагентов и что вернулось
                deposits=await _deposits())
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


# ── Крипта РП: проверки перед записью ────────────────────────────────────────
# Все под _CR_LOCK у того, кто зовёт. None — можно, иначе тело отказа (409).
async def _cr_alloc_check(day: str, new: float, was: float) -> dict | None:
    """Сколько крипты кладём в РП+ дня: new — станет, was — уже лежит в книге
    (у неподтверждённого дня — ноль). Больше свободной не положить; меньше
    сделать можно, только если из положенного ещё не потратили."""
    import crypto_book
    if new <= 0 and was <= 0:
        return None
    st = await crypto_book.state()
    if new > 0 and not st["ready"]:
        return {"error": "crypto_unready"}
    if new > 0 and day < crypto_book.START_DAY:
        return {"error": "crypto_early", "from": crypto_book.START_DAY}
    delta = round(new - was, 2)
    if delta > st["free_fact"] + crypto_book.EPS:
        return {"error": "no_free", "free": st["free_fact"], "max": round(st["free_fact"] + was, 2)}
    if delta < -crypto_book.EPS:
        have = crypto_book.rp_min_from(st["by_day"], day)
        if -delta > have + crypto_book.EPS:
            return {"error": "cr_spent", "have": have, "min": round(was - have, 2)}
    return None


async def _cr_spend_check(day: str, amount: float) -> dict | None:
    """Расход из РП криптой: на счету РП должно хватать и в этот день, и в
    каждый следующий — иначе счёт ушёл бы в минус задним числом."""
    import crypto_book
    st = await crypto_book.state()
    if not st["ready"]:
        return {"error": "crypto_unready"}
    have = crypto_book.rp_min_from(st["by_day"], day)
    if amount > have + crypto_book.EPS:
        return {"error": "no_crypto", "have": have}
    return None


async def _cr_withdraw_split(day: str, amount: float, fee_ours: float = 0.0):
    """Вывод крипты в наличные (Доп. РП+ «из крипты»): сначала из свободной,
    остальное — с крипта-счёта РП в наличные РП (владелец, 2 окт 2026).

    fee_ours — наша половина комиссии: наличных пришло amount, а крипты ушло
    amount + fee_ours. Берётся тем же порядком — сперва свободная (сначала на
    сами наличные, остаток её — на комиссию), потом счёт РП.

    Отдаёт (наличные из свободной, наличные с РП, комиссия из свободной, отказ)."""
    import crypto_book
    st = await crypto_book.state()
    if not st["ready"]:
        return 0.0, 0.0, 0.0, {"error": "crypto_unready"}
    free = max(0.0, st["free_fact"])
    rp = max(0.0, crypto_book.rp_min_from(st["by_day"], day))
    надо = round(amount + fee_ours, 2)
    if надо > free + rp + crypto_book.EPS:
        return 0.0, 0.0, 0.0, {"error": "no_crypto", "free": free, "rp": rp, "have": round(free + rp, 2)}
    из_свободной = round(min(надо, free), 2)
    f = round(min(amount, из_свободной), 2)
    return f, round(amount - f, 2), round(из_свободной - f, 2), None


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
    async with _CR_LOCK:
        if field == "collected_cr":
            if value is not None and value < 0:
                return _json({"error": "bad_number"}, 400)
            m = (await db.fin_days_get(day, day)).get(day) or {}
            # у неподтверждённого дня это черновик: в книге его ещё нет
            было = calc._n(m.get("collected_cr")) if m.get("ok") else 0.0
            отказ = await _cr_alloc_check(day, calc._n(value), было)
            if отказ:
                return _json(отказ, 409)
        # Оплата этим путём — без источника: недостающее из ЧП, как было до
        # 3 окт 2026 (STAR теперь пишет оплату через /book/day/pay).
        src_fields = ["pay_src", "pay_rp", "pay_at", "pay_by"] if field == "pay" else []
        if value is None or value == "":
            await db.fin_day_set(day, {"by": who}, unset=[field] + src_fields)
        else:
            await db.fin_day_set(day, {field: value, "by": who}, unset=src_fields or None)
    await _touch(day[:7])
    log.info(f"[fin] {day} {field} → {value!r} · {who or '—'}")
    await backdate.notify(day, who, "финансы: " + FIELD_T.get(field, field),
                          "" if value in (None, "") else (value if field == "note" else f"{value} AED"))
    return _json({"ok": True, "day": day, "field": field, "value": value,
                  "book": await build(day[:7])})


@require_owner
async def handle_pay_b(request):
    """POST {day, pay, src, rp, force, as} — оплата Барракуде за день одной
    суммой и откуда взято то, чего не хватило в её стопке (владелец, 3 окт
    2026: «пусть программа предлагает взять или из РП, или из ЧП, или понемногу
    отовсюду»): src 'np' — из ЧП, 'rp' — из РП, 'mix' — из РП rp, остальное из
    ЧП. pay пустое — снять оплату. Больше, чем лежит в ЧП или в РП по книге,
    не взять — 409 not_enough с остатками; force — записать всё равно (стопка
    уйдёт в минус, старший это видел и подтвердил)."""
    try:
        body = await request.json()
        day = _day_arg(body.get("day"))
        pay = _num(body.get("pay"))
        src = str(body.get("src") or "")
        rp_part = _num(body.get("rp")) or 0
        force = bool(body.get("force"))
        if src not in PAY_SRC or (pay is not None and pay < 0) or rp_part < 0:
            return _json({"error": "bad_request"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    if day > _biz_day():
        return _json({"error": "future"}, 400)
    who = _who(body)
    month = day[:7]
    from_b = from_np = from_rp = 0.0
    if pay is None:
        await db.fin_day_set(day, {"by": who}, unset=["pay", "pay_src", "pay_rp", "pay_at", "pay_by"])
    else:
        book = await build(month, light=True)
        r = next(x for x in book["days"] if x["day"] == day)
        # стопки без сегодняшней оплаты — что лежало в сейфе, когда платили
        b_have = calc._n(r["stack_b"]) + calc._n(r["pay_b"])
        np_have = calc._n(r["stack_np"]) + calc._n(r["pay_b_extra"])
        rp_have = calc._n(r["stack_rp"]) + calc._n(r["pay_rp"])
        from_b = min(pay, max(0.0, b_have))
        short = round(pay - from_b, 2)
        if src == "rp":
            from_rp = short
        elif src == "mix":
            if rp_part > short + 0.005:
                return _json({"error": "bad_split", "short": short}, 400)
            from_rp = round(rp_part, 2)
        from_np = round(short - from_rp, 2)
        if not force and (from_rp > rp_have + 0.005 or from_np > np_have + 0.005):
            return _json({"error": "not_enough", "pay": pay, "short": short, "b": calc._i(b_have),
                          "np": calc._i(np_have), "rp": calc._i(rp_have),
                          "from_np": calc._i(from_np), "from_rp": calc._i(from_rp)}, 409)
        fields = {"pay": pay, "pay_src": src if short > 0 else "", "pay_at": datetime.now(timezone.utc),
                  "pay_by": who, "by": who}
        unset = []
        if src == "mix" and short > 0:
            fields["pay_rp"] = round(from_rp, 2)
        else:
            unset.append("pay_rp")
        await db.fin_day_set(day, fields, unset=unset or None)
    await _touch(month)
    откуда = (f" · из стопки {calc._i(from_b)}" + (f", из ЧП {calc._i(from_np)}" if from_np else "")
              + (f", из РП {calc._i(from_rp)}" if from_rp else "")) if pay is not None else " снята"
    log.info(f"[fin] {day} оплата Барракуде {pay!r}{откуда}" + (" · force" if force else "") + f" · {who or '—'}")
    await backdate.notify(day, who, "финансы: оплата Барракуде",
                          "" if pay is None else f"{pay} AED{откуда}")
    return _json({"ok": True, "day": day, "pay": pay, "from_b": calc._i(from_b), "from_np": calc._i(from_np),
                  "from_rp": calc._i(from_rp), "book": await build(month)})


@require_owner
async def handle_day_ok(request):
    """POST {day, aside, collected, as} — старший подтвердил раскладку дня:
    суммы Барракуде и в РП+ замораживаются, день помечен подтверждённым
    (остаток — ЧП+ — считается сам)."""
    try:
        body = await request.json()
        day = _day_arg(body.get("day"))
        aside = _num(body.get("aside")) or 0
        collected = _num(body.get("collected")) or 0
        cr = _num(body.get("collected_cr")) if "collected_cr" in body else None
        if aside < 0 or collected < 0 or (cr is not None and cr < 0):
            return _json({"error": "bad_number"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    if day > _biz_day():
        return _json({"error": "future"}, 400)
    who = _who(body)
    async with _CR_LOCK:
        m = (await db.fin_days_get(day, day)).get(day) or {}
        # Экран, который про крипту не знает, её не трогает: что лежало в дне —
        # то и остаётся.
        if cr is None:
            cr = calc._n(m.get("collected_cr"))
        было = calc._n(m.get("collected_cr")) if m.get("ok") else 0.0
        отказ = await _cr_alloc_check(day, cr, было)
        if отказ:
            return _json(отказ, 409)
        await db.fin_day_set(day, {"aside": aside, "collected": collected, "collected_cr": cr,
                                   "ok": True, "ok_at": datetime.now(timezone.utc),
                                   "ok_by": who, "by": who})
    await _touch(day[:7])
    log.info(f"[fin] {day} раскладка подтверждена: Барракуде {aside}, РП+ наличными {collected}, "
             f"криптой {cr} · {who or '—'}")
    return _json({"ok": True, "day": day, "book": await build(day[:7])})


async def _line_get(month: str, lid: str):
    """Статья расхода: (документ, годится ли). Пустой id — расход вне плана."""
    if not lid:
        return None, True
    ln = await db.fin_budget_line_get(lid)
    return ln, bool(ln) and ln.get("month") == month


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
        # Возврат депозита, удержанный целиком (владелец, 4 окт 2026): в РП
        # приходит ноль, но запись нужна — она закрывает депозит удержанием.
        нулевой_возврат = (book == "in" and str(body.get("src") or "") == "deposit"
                           and (_num(body.get("lost")) or 0) > 0 and amount is not None and amount == 0)
        if amount is None or amount < 0 or (amount <= 0 and not нулевой_возврат):
            return _json({"error": "bad_amount"}, 400)
        kind = str(body.get("kind") or "")
        if kind not in ENTRY_KINDS or (kind and book != "rp"):
            return _json({"error": "bad_kind"}, 400)
        line = str(body.get("line") or "")[:24] if book == "rp" else ""
        ln, ok = await _line_get(day[:7], line)
        if not ok:
            return _json({"error": "bad_line"}, 400)
        who = str(body.get("who") or "").strip()[:60]
        # билет: откуда и куда летит — для учёта, кому что покупали
        route_from = str(body.get("route_from") or "").strip()[:40] if book == "rp" else ""
        route_to = str(body.get("route_to") or "").strip()[:40] if book == "rp" else ""
        if kind and not who:
            return _json({"error": "who_required"}, 400)
        # Перевод между стопками (владелец, 3 окт 2026): откуда и куда — разные
        # стопки сейфа; комментарий по желанию, дата — день записи.
        mv_from = str(body.get("from") or "") if book == "mv" else ""
        mv_to = str(body.get("to") or "") if book == "mv" else ""
        if book == "mv" and (mv_from not in STACKS or mv_to not in STACKS or mv_from == mv_to):
            return _json({"error": "bad_move"}, 400)
        force = bool(body.get("force"))
        # Доп. РП+ «из крипты» (владелец, 30 сен 2026): вывели USDT наличными в
        # фонд. Отметка нужна, чтобы потом знать, сколько крипты уже забрали.
        src = str(body.get("src") or "") if book == "in" else ""
        if src not in ("", "crypto", "deposit"):
            return _json({"error": "bad_src"}, 400)
        # Депозит (владелец, 4 окт 2026: «там же, где указываем, за что
        # оплатили, доп. графа депозит»): деньги ушли из РП, но они наши и
        # вернутся через Доп. РП+. Только у расхода из фонда без вида.
        deposit = bool(body.get("deposit")) and book == "rp" and not kind
        dep_of = str(body.get("dep_of") or "")[:24] if src == "deposit" else ""
        lost = (_num(body.get("lost")) or 0) if src == "deposit" else 0
        if src == "deposit" and (not dep_of or lost < 0):
            return _json({"error": "bad_deposit"}, 400)
        # Списать из депозита/аванса (владелец, 5 окт 2026: «счёт на 3000, платим
        # 2000 — из аванса 1000 он себе забрал»; у здания при «Оплатил» — «депозит
        # сгорел»): часть суммы покрывается тем, что уже лежит у контрагента.
        # Только у расхода по статье без вида; у взноса депозита списания нет.
        dep_use = (_num(body.get("dep_use")) or 0) if book == "rp" and not kind and not deposit else 0
        if dep_use < 0 or dep_use > amount + 0.005 or (dep_use > 0 and not line):
            return _json({"error": "bad_dep_use"}, 400)
        # Чем платили расход из РП (владелец, 2 окт 2026): наличными — из
        # наличных РП, криптой — с крипта-счёта РП.
        pay_way = str(body.get("pay") or "") if book == "rp" else ""
        if pay_way not in PAY_WAYS:
            return _json({"error": "bad_pay"}, 400)
        # Комиссия вывода крипты — в USDT, как её сняли (владелец, 2 окт 2026).
        fee_usdt = (_num(body.get("fee_usdt"), 6) or 0) if src == "crypto" else 0
        if fee_usdt < 0:
            return _json({"error": "bad_fee"}, 400)
        import photos
        photo, bad = photos.decode(body.get("photo"))
        if bad:
            return _json({"error": bad}, 400)
        thumb = photos.thumb(body.get("thumb")) if photo else ""
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    # фактический расход из РП — только с чеком (владелец: «все обязательно с
    # чеками»); зарплатные записи и выплаты из ЧП идут без снимка. Аренда
    # была исключением: её платят кнопкой «Оплатил» суммой из плана (владелец,
    # 12 сен: «нажал кнопку — оплатил, расход засчитан»).
    #
    # С 29 сен 2026 исключение снято для РЕНТА МАШИН (группа car — Орион,
    # Алексей, «Другой Рент»): «сделай так, чтобы вместе с оплатой нужно было
    # и чеки загружать». Офисы и реклама платятся как раньше — про них
    # владелец не говорил.
    rent = bool(ln) and _line_group(ln) in ("rent", "ads")
    if book == "rp" and not kind and not photo and not rent:
        return _json({"error": "no_photo"}, 400)
    # «Что-то ещё» без слов — просто сумма в никуда: за что, обязательно.
    if ln and (ln.get("name") or "") == OTHER_NAME and not str(body.get("comment") or "").strip():
        return _json({"error": "no_comment"}, 400)
    by = _who(body)
    doc = {"_id": secrets.token_hex(6), "day": day, "book": book, "amount": amount,
           "comment": str(body.get("comment") or "").strip()[:120],
           "who": who, "line": line, "kind": kind, "by": by, "at": datetime.now(timezone.utc),
           "photo": bool(photo)}
    if route_from or route_to:
        doc["route_from"], doc["route_to"] = route_from, route_to
    if src:
        doc["src"] = src
    if deposit:
        doc["deposit"] = True
    if src == "deposit":
        # Возврат — к конкретному депозиту и не больше, чем там ещё лежит
        # (с учётом удержанного); раньше дня внесения вернуться не могло.
        dep = await db.fin_entry_get(dep_of)
        if not dep or dep.get("book") != "rp" or not dep.get("deposit"):
            return _json({"error": "no_deposit"}, 404)
        if day < str(dep.get("day") or ""):
            return _json({"error": "before_paid"}, 400)
        было = await db.fin_entries_where({"book": "in", "src": "deposit", "dep_of": dep_of})
        left = calc._n(dep.get("amount")) - sum(calc._n(x.get("amount")) + calc._n(x.get("lost")) for x in было)
        # списанное в оплату (dep_uses у расходов) тоже уже не лежит
        for r in await db.fin_entries_where({"book": "rp", "dep_uses.of": dep_of}):
            left -= sum(calc._n(u.get("amount")) for u in (r.get("dep_uses") or []) if str(u.get("of")) == dep_of)
        if amount + lost > left + 0.005:
            return _json({"error": "too_much", "have": calc._i(max(0.0, left))}, 409)
        doc["dep_of"] = dep_of
        if lost > 0:
            doc["lost"] = lost
    if dep_use > 0:
        # Покрыть можно только тем, что лежит у контрагента по этой статье
        # (за все месяцы, по id и названию); раскладывается по депозитам —
        # старые первыми, у каждого потом видно, сколько из него списано.
        rows = await _line_deposits(ln)
        have = sum(calc._n(x.get("left")) for x in rows)
        if dep_use > have + 0.005:
            return _json({"error": "no_balance", "have": calc._i(max(0.0, have))}, 409)
        doc["dep_use"] = calc._i(dep_use)
        doc["dep_uses"] = _dep_allocate(dep_use, rows)
    if book == "mv":
        # Больше, чем лежит в стопке (с этого дня и дальше — чтобы ни один
        # день не ушёл в минус), не перевести: 409 с остатком; force — всё равно.
        doc["from"], doc["to"] = mv_from, mv_to
        if not force:
            книга = await build(day[:7], light=True)
            have = min(calc._n(r["stack_" + mv_from]) for r in книга["days"] if r["day"] >= day)
            if amount > have + 0.005:
                return _json({"error": "not_enough", "have": calc._i(have), "from": mv_from,
                              "from_t": STACK_T[mv_from]}, 409)
    async with _CR_LOCK:
        if pay_way == "crypto":
            # криптой уходит только то, что не покрыто депозитом
            отказ = await _cr_spend_check(day, amount - dep_use) if amount - dep_use > 0.005 else None
            if отказ:
                return _json(отказ, 409)
            doc["pay"] = "crypto"
        комиссия = None
        if src == "crypto":
            import crypto_book
            курс = crypto_book.rate()
            fee = round(float(fee_usdt) * курс, 2)                 # вся комиссия, в наших дирхамах
            наша = round(fee * crypto_book.FEE_OURS, 2)            # половина — на нас
            f, r, из_свободной, отказ = await _cr_withdraw_split(day, amount, наша)
            if отказ:
                return _json(отказ, 409)
            doc["cr_free"], doc["cr_rp"] = f, r
            if наша > 0:
                # Наша половина — расход РП «криптой», своей строкой в РП−:
                # по ней видно, куда ушла разница между наличными и USDT.
                комиссия = {"_id": secrets.token_hex(6), "day": day, "book": "rp", "amount": наша,
                            "comment": "Комиссия за вывод крипты", "who": "", "line": "", "kind": "",
                            "by": by, "at": datetime.now(timezone.utc), "photo": False, "pay": "crypto",
                            "fee_of": doc["_id"], "fee_usdt": fee_usdt, "fee": fee}
                doc.update(fee_usdt=fee_usdt, fee=fee, fee_ours=наша, fee_free=из_свободной,
                           fee_entry=комиссия["_id"], fee_rate=курс)
        if photo:
            await db.expense_photo_set("fin:" + doc["_id"], photo, thumb)
        await db.fin_entry_add(doc)
        if комиссия:
            await db.fin_entry_add(комиссия)
    await _touch(day[:7])
    log.info(f"[fin] {day} {book} +{amount} «{doc['comment']}» {who} · {by or '—'}"
             + (f" · {STACK_T[mv_from]} → {STACK_T[mv_to]}" + (" · force" if force else "") if book == "mv" else "")
             + (" · криптой" if pay_way == "crypto" else "")
             + (" · депозит" if deposit else "")
             + (f" · из депозита {doc['dep_use']}" if doc.get("dep_use") else "")
             + (f" · из свободной крипты {doc['cr_free']}, с крипта-счёта РП {doc['cr_rp']}"
                if src == "crypto" else "")
             + (f" · комиссия {doc['fee_usdt']} USDT = {doc['fee']} AED, наша половина {doc['fee_ours']}"
                f" (из свободной {doc['fee_free']})" if doc.get("fee_ours") else ""))
    await backdate.notify(day, by, "финансы: " + (f"перевод {STACK_T[mv_from]} → {STACK_T[mv_to]}" if book == "mv"
                                                 else BOOK_T.get(book, book)),
                          f"{amount} AED {who} {doc['comment']}".strip())
    return _json({"ok": True, "id": doc["_id"], "cr_free": doc.get("cr_free"),
                  "cr_rp": doc.get("cr_rp"), "fee": doc.get("fee"), "fee_ours": doc.get("fee_ours"),
                  "fee_free": doc.get("fee_free"), "fee_id": doc.get("fee_entry") or "",
                  "book": await build(day[:7])})


@require_owner
async def handle_entry_photo(request):
    """GET /api/owner/finance/book/photo/{id} — чек к расходу из РП."""
    eid = (request.match_info.get("id") or "").strip()
    img = await db.expense_photo("fin:" + eid) if eid else b""
    if not img:
        return _json({"error": "no_photo"}, 404)
    return web.Response(body=img, content_type="image/jpeg",
                        headers={**CORS_HEADERS, "Cache-Control": "private, max-age=86400"})


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
    if old.get("deposit"):
        # По депозиту уже записан возврат или списание в оплату — сперва убрать
        # их, иначе возврат повиснет без того, что возвращали.
        used = await db.fin_entries_where({"book": "in", "src": "deposit", "dep_of": eid})
        used += await db.fin_entries_where({"book": "rp", "dep_uses.of": eid})
        if used:
            return _json({"error": "dep_used", "n": len(used)}, 409)
    async with _CR_LOCK:
        await db.fin_entry_del(eid)
        if old.get("hands_extra"):
            await _hands_extra_drop(old)
        # Вывод крипты убрали — его комиссия уходит вместе с ним. Убрали одну
        # комиссию — вывод остаётся, но уже без неё (и без её части из
        # свободной крипты).
        if old.get("fee_entry"):
            await db.fin_entry_del(str(old["fee_entry"]))
        if old.get("fee_of"):
            await db.fin_entry_unset(str(old["fee_of"]),
                                     ["fee_usdt", "fee", "fee_ours", "fee_free", "fee_entry", "fee_rate"])
    if old.get("photo"):
        await db.expense_photo_del("fin:" + eid)
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
        if field in ("norm", "usd") and value is not None and value < 0:
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
        cur = str(body.get("cur") or "").upper()
        if cur not in ("", "AED", "USD"):
            return _json({"error": "bad_cur"}, 400)
        period = int(_num(body.get("period")) or 1)
        if not 1 <= period <= MAX_PERIOD:
            return _json({"error": "bad_period"}, 400)
        nxt = str(body.get("next") or "").strip()
        if nxt:
            nxt = _day_arg(nxt)
        span = int(_num(body.get("span")) or 0)
        if not 0 <= span <= MAX_SPAN:
            return _json({"error": "bad_span"}, 400)
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
        if "span" not in body:
            span = int(old.get("span") or 0)
        if note is None:
            note = str(old.get("note") or "")
        if "cur" not in body:
            cur = "USD" if old.get("cur") == "USD" else "AED"
        if _is_pool(old):
            kind = "pool"
    else:
        lid = secrets.token_hex(4)
        ordv = len(await db.fin_budget_get(month))
    if kind != "office" and name in POOL_NAMES:
        kind = "pool"
    if kind == "pool":
        period, nxt, span = 1, "", 0              # без даты платежа: только бюджет на месяц
    if not nxt:
        span = 0
    doc = {"_id": lid, "month": month, "name": name, "plan": plan, "due": due,
           "note": note or "", "kind": kind, "group": group, "cur": cur or "AED",
           "period": period, "next": nxt, "span": span,
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


def _rows_from_prev(prev: list) -> list:
    """Статьи прошлого месяца — как статьи нового: те же названия, суммы, сроки
    и расписания. Старые строки «Зарплаты» не берём — фонд считается из людей."""
    return [dict(name=ln.get("name"), plan=ln.get("plan") or 0, due=ln.get("due") or 0,
                 note=ln.get("note") or "", kind="pool" if _is_pool(ln) else "office" if ln.get("kind") == "office" else "",
                 group=_line_group(ln), cur="USD" if ln.get("cur") == "USD" else "AED",
                 period=int(ln.get("period") or 1), next=str(ln.get("next") or "")) for ln in prev
            if (ln.get("kind") or "") != "salary"]


_BUD_LOCK = asyncio.Lock()


async def _budget_carry(month: str) -> int:
    """Бюджет нового месяца сам переезжает из прошлого (владелец, 2 окт 2026:
    «у нас каждый месяц бюджет одинаковый»). Раньше новый месяц начинался
    пустым, и заполнять его надо было кнопкой — нажали «По образцу» вместо «Из
    прошлого месяца», и октябрь остался без единой суммы.

    Только текущий месяц и только один раз (отметка budget_carried): бюджет,
    который потом вычистили руками, заново не появляется. Месяц, в котором
    статьи уже есть, не трогаем. Сколько статей перенесли."""
    if month != _biz_day()[:7]:
        return 0
    async with _BUD_LOCK:
        mdoc = await db.fin_month_get(month)
        if mdoc.get("budget_carried") or await db.fin_budget_get(month):
            return 0
        rows = _rows_from_prev(await db.fin_budget_get(_prev_month(month)))
        if not rows:
            return 0
        # Сначала отметка, потом статьи: упади мы посередине, второй перенос
        # не задвоит то, что уже легло.
        await db.fin_month_set(month, {"budget_carried": True})
        for i, r in enumerate(rows):
            await db.fin_budget_set({"_id": secrets.token_hex(4), "month": month, "ord": i,
                                     "by": "перенос из прошлого месяца", **r})
        await _touch(month)
        log.info(f"[fin] бюджет {month} перенесён из {_prev_month(month)}: {len(rows)} статей, "
                 f"план {sum(calc._n(r['plan']) for r in rows):.0f}")
        return len(rows)


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
        rows = _rows_from_prev(prev)
    else:
        rows = [dict(name=r["name"], plan=0, due=0, note="", kind=r.get("kind") or "", group=r.get("group") or "", cur="AED")
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
    known = set(staff.all_driver_names()) | set(staff.operator_names())
    fields["manual"] = name not in known
    if "hidden" not in fields:
        fields["hidden"] = False                  # добавили заново — снова в списке
    await db.fin_person_set(name, fields)
    log.info(f"[fin] зарплаты: {name} {fields.get('role', '')}{' скрыт' if fields['hidden'] else ''} · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_work(request):
    """POST {name, action, day, i, from, to, month, as} — когда человек вышел на
    работу и когда уехал (владелец, 21 сен 2026: «кто-то уезжает, кто-то
    приезжает, а зарплата у всех первого числа; приехал 15-го — получает пол
    зарплаты»). action: start — вышел с day; end — уехал, day — последний
    день; set — поправить период i (from/to, пустое «to» — работает); del —
    убрать период i. Оклад в месяц считается по дням на работе."""
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()[:40]
        action = str(body.get("action") or "")
        if not name:
            return _json({"error": "name_required"}, 400)
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
        day = str(body.get("day") or "")[:10]
        i = int(body.get("i")) if str(body.get("i", "")).lstrip("-").isdigit() else -1
        a, b = str(body.get("from") or "")[:10], str(body.get("to") or "")[:10]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    docs = {str(d.get("_id")): d for d in await db.fin_people_get()}
    work, err = pay.work_apply((docs.get(name) or {}).get("work"), action, day, i, a, b)
    if err:
        return _json({"error": err}, 400)
    who = _who(body)
    await db.fin_person_set(name, {"work": work, "by": who})
    log.info(f"[fin] на работе: {name} {action} {day or f'{a}…{b}'} → "
             f"{', '.join((w['from'] or '…') + '–' + (w['to'] or 'сейчас') for w in work) or 'без периодов'}"
             f" · {who or '—'}")
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
        elif field == "bonus":
            # Премия в месяц — с этого месяца и дальше. Пусто — 0, а не «как
            # раньше»: стёрли — значит с этого месяца премии нет.
            value = _num(raw) or 0
            if value < 0:
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
    if (value is None or value == "") and field != "bonus":
        await db.fin_pay_month_set(month, name, fields, unset=[field])
    else:
        await db.fin_pay_month_set(month, name, {**fields, field: value})
    log.info(f"[fin] зарплаты {month} {name}: {field} → {value!r}{' ' + cur if fields.get('cur') else ''} · {who or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_item_add(request):
    """POST {name, kind, amount, per_month, from: this|next|YYYY-MM, day, note, reason, as}
    — штраф, аванс, долг, премия или удержание. Аванс и долг — деньги выданы из
    фонда: запись расхода в тот же день. reason — за что по штрафному листу
    («Превышение скорости · 20 – 30 км/ч»), note — комментарий."""
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
        mode = str(body.get("mode") or "") if kind == "advance" else ""
        if mode and mode not in pay.ADVANCE_MODES:
            return _json({"error": "bad_mode"}, 400)
        # «Часть зарплаты» — с этого месяца разом; «наперёд» — со следующего
        # разом; «по частям» — как задали (владелец, 19 сен 2026).
        if mode == "part":
            frm, per_month = "this", 0
        elif mode == "ahead":
            frm, per_month = "next", 0
        start = month if frm == "this" else pay.next_month(month) if frm == "next" else _month_arg(frm)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    note = str(body.get("note") or "").strip()[:80]
    reason = str(body.get("reason") or "").strip()[:80]
    iid = secrets.token_hex(5)
    entry_id = ""
    pay_way = str(body.get("pay") or "")
    if pay_way not in PAY_WAYS:
        return _json({"error": "bad_pay"}, 400)
    if kind in pay.CASH_KINDS:
        entry_id = secrets.token_hex(6)
        async with _CR_LOCK:
            if pay_way == "crypto":
                отказ = await _cr_spend_check(day, amount)
                if отказ:
                    return _json(отказ, 409)
            await db.fin_entry_add({"_id": entry_id, "day": day, "book": "rp", "amount": amount,
                                    "comment": note or pay.KINDS[kind], "who": name,
                                    "line": "", "kind": kind, "item": iid,
                                    **({"pay": "crypto"} if pay_way == "crypto" else {}),
                                    "by": who, "at": datetime.now(timezone.utc)})
    item = {"_id": iid, "name": name, "kind": kind, "amount": amount,
            "per_month": per_month, "from": start, "day": day, "note": note,
            **({"mode": mode} if mode else {}),
            **({"reason": reason} if reason else {}),
            "entry": entry_id, "by": who, "at": datetime.now(timezone.utc)}
    await db.fin_pay_item_add(item)
    await _touch(min(month, day[:7]))
    # Водителю — тем же днём, в его бот: что назначили, за что и как удерживается.
    await _pn.tell_safe(name, _pn.added(item, who))
    log.info(f"[fin] зарплаты: {name} {pay.KINDS[kind]}{f' ({reason})' if reason else ''} {amount} с {start}"
             f"{f' по {per_month}/мес' if per_month else ''} · {who or '—'}")
    if kind in pay.CASH_KINDS:
        await backdate.notify(day, who, f"финансы: {pay.KINDS[kind].lower()} {name}", f"{amount} AED")
    return _json({"ok": True, "id": iid, "book": await build(month)})


async def _tenure_list(today: str = "") -> dict:
    """Лист премий за стаж: кто сколько наработал и кому сколько причитается."""
    import tenure
    try:
        docs = await db.fin_people_get()
        items = await db.fin_pay_items_get()
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] лист премий не прочитан: {e}")
        docs, items = [], []
    rows = tenure.list_for(_people(docs), items, today or _biz_day())
    return {"people": rows, "step_aed": tenure.STEP_AED, "step_months": tenure.STEP_MONTHS,
            "due": sum(r["due"] for r in rows),
            "ready": sum(1 for r in rows if r["due"] > 0)}


@require_owner
async def handle_tenure(request):
    """Лист премий — список людей, стаж и что кому причитается."""
    return _json(await _tenure_list())


@require_owner
async def handle_tenure_pay(request):
    """POST {name, as} — выплатить премию за стаж.

    Сумму считаем сами: столько, сколько человек наработал и ещё не получил.
    Нажали дважды (или вдвоём) — второй получит 409: премия уже выплачена, и
    вторая тысяча из воздуха не возьмётся."""
    import tenure
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()[:60]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    if not name:
        return _json({"error": "bad_request"}, 400)
    who = _who(body)
    day = _biz_day()
    async with _TENURE_LOCK:
        лист = await _tenure_list(day)
        st = next((r for r in лист["people"] if r["name"] == name), None)
        if not st:
            return _json({"error": "unknown_person"}, 404)
        if st["due"] <= 0:
            return _json({"error": "not_yet", "bonus": лист}, 409)
        iid = secrets.token_hex(5)
        item = {"_id": iid, **tenure.pay_item(st, day, who), "at": datetime.now(timezone.utc)}
        await db.fin_pay_item_add(item)
    await _touch(day[:7])
    # Водителю — поздравление в его бот тем же днём (владелец, 23 сен 2026:
    # «обязательно чтобы приходило сообщение — поздравляем, вам начислена
    # премия»). В приложении он увидит её в своей истории начислений.
    await _pn.tell_safe(name, _pn.added(item, who))
    log.info(f"[fin] премия за стаж: {name} {item['amount']} AED "
             f"({st['months']} мес) · {who or '—'}")
    return _json({"ok": True, "id": iid, "bonus": await _tenure_list(day),
                  "book": await build(day[:7])})


@require_owner
async def handle_discipline(request):
    """GET ?days=30|90|365 — карточки дисциплины: что на каждом человеке."""
    import discipline
    try:
        days = max(7, min(365, int(request.query.get("days") or 90)))
    except ValueError:
        days = 90
    return _json({"ok": True, **(await discipline.build(days))})


@require_owner
async def handle_fine_rules(request):
    """GET — правила штрафов; POST {cats, as} — сохранить их целиком (экран
    «Правила» в STAR). Ответ — правила в том виде, в каком легли."""
    import fine_rules
    if request.method == "GET":
        return _json({"ok": True, "rules": await fine_rules.get()})
    try:
        body = await request.json()
        cats = body.get("cats")
        if not isinstance(cats, list):
            return _json({"error": "bad_request"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    return _json({"ok": True, "rules": await fine_rules.save(cats, _who(body))})


@require_owner
async def handle_fine_decide(request):
    """POST {id, decision: assign|skip, amount, month, as} — решение по тому,
    что сформировала программа (fines_auto.py). У штрафа «Назначить» — обычный
    штраф в зарплатах: с этого месяца, разом, день — день нарушения; водителю —
    то же сообщение, что о любом штрафе. У питания «Урезать» (тот же assign) —
    питание за тот день 40 вместо 80 и сообщение водителю. «Не назначать / не
    урезать» — только исход в истории. Решают один раз: второе нажатие или
    второй старший получают 409 и свежую книгу, где этого среди ждущих нет."""
    import fines_auto
    try:
        body = await request.json()
        pid = str(body.get("id") or "").strip()
        decision = str(body.get("decision") or "")
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
        if not pid or decision not in ("assign", "skip"):
            return _json({"error": "bad_request"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    cur = await db.fine_pending_get(pid)
    if not cur or cur.get("status") != "pending":
        return _json({"error": "decided", "book": await build(month)}, 409)
    meal = fines_auto.action_of(cur.get("kind") or "") == "meal"
    amount = None
    if decision == "assign" and not meal:
        amount = _num(body.get("amount"))
        if amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
    who = _who(body)
    now = datetime.now(timezone.utc)
    if decision == "assign" and meal:
        doc = await db.fine_pending_decide(pid, {"status": "assigned", "decided_by": who, "decided_at": now})
        if not doc:
            return _json({"error": "decided", "book": await build(month)}, 409)
        day = str(doc.get("day") or "")[:10] or _biz_day()
        try:
            # Та же отметка, что у отпуска раньше конца смены: meal_of её и читает.
            await db.save_driver_day(day, doc.get("name") or "", {"meal_rate": fines_auto.MEAL_TO, "meal_cut": pid})
        except Exception as e:                    # noqa: BLE001
            log.warning(f"[fin] питание {pid}: не урезано, возвращаю в ждущие: {e}")
            await db.fine_pending_undo(pid, {"decided_by": "", "decided_at": ""})
            return _json({"error": "save_failed"}, 500)
        await _touch(day[:7])
        await _pn.tell_safe(doc.get("name") or "", fines_auto.meal_cut_text(doc, who))
        log.info(f"[fin] решение: {doc.get('name')} — {doc.get('reason')} {day}: питание "
                 f"{fines_auto.MEAL_TO} вместо {fines_auto.MEAL_FROM} · {who or '—'}")
        return _json({"ok": True, "book": await build(month)})
    if decision == "skip":
        doc = await db.fine_pending_decide(pid, {"status": "declined", "decided_by": who, "decided_at": now})
        if not doc:
            return _json({"error": "decided", "book": await build(month)}, 409)
        log.info(f"[fin] решение: {doc.get('name')} — {doc.get('reason')} {doc.get('day')}: "
                 f"{'не урезано' if meal else 'не назначен'} · {who or '—'}")
        return _json({"ok": True, "book": await build(month)})
    iid = secrets.token_hex(5)
    doc = await db.fine_pending_decide(pid, {"status": "assigned", "decided_by": who, "decided_at": now,
                                             "amount": amount, "item": iid})
    if not doc:
        return _json({"error": "decided", "book": await build(month)}, 409)
    day = str(doc.get("day") or "")[:10] or _biz_day()
    start = max(_biz_day()[:7], day[:7])              # снимается с зарплаты этого месяца
    # За что — одной официальной фразой: её увидят и старший, и водитель, и бот.
    item = {"_id": iid, "name": doc.get("name") or "", "kind": "fine", "amount": amount,
            "per_month": 0, "from": start, "day": day, "note": "",
            "reason": fines_auto.full_text(doc), "entry": "", "by": who, "at": now,
            "auto": doc.get("kind") or "", "pending": pid}
    try:
        await db.fin_pay_item_add(item)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] штраф на решение {pid}: не записан, возвращаю в ждущие: {e}")
        await db.fine_pending_undo(pid, {"decided_by": "", "decided_at": "", "item": ""})
        return _json({"error": "save_failed"}, 500)
    await _touch(min(start, day[:7]))
    # Водителю — тем же днём, в его бот: как о любом штрафе.
    await _pn.tell_safe(item["name"], _pn.added(item, who))
    log.info(f"[fin] штраф на решение: {item['name']} — {item['reason']} {day} назначен {amount} "
             f"с {start} · {who or '—'}")
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
    if old.get("kind") in pay.PENALTY_KINDS:
        # штраф и удержание не стираются: отменённый остаётся в истории
        if not old.get("cancelled_at"):
            await db.fin_pay_item_set(iid, {"cancelled_at": datetime.now(timezone.utc), "cancelled_by": _who(body)})
            await _pn.tell_safe(old.get("name"), _pn.cancelled(old, _who(body)))
    else:
        await db.fin_pay_item_del(iid)
        if old.get("entry"):
            await db.fin_entry_del(str(old["entry"]))
        await _pn.tell_safe(old.get("name"), _pn.removed(old, _who(body)))
    await _touch(min(month, str(old.get("from") or old.get("day") or month)[:7]))
    log.info(f"[fin] зарплаты: {old.get('name')} {old.get('kind')} {old.get('amount')} "
             f"{'отменено' if old.get('kind') in pay.PENALTY_KINDS else 'убрано'} · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_item_edit(request):
    """POST {id, amount, per_month, note, month, as} — пересмотреть штраф или
    удержание: сумма, график, комментарий. Аванс и долг так не правятся — за ними
    запись расхода фонда. Первая сумма запоминается в was — в истории «было»."""
    try:
        body = await request.json()
        iid = str(body.get("id") or "")
        amount = _num(body.get("amount"))
        if amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
        per_month = _num(body.get("per_month")) or 0
        if per_month < 0:
            return _json({"error": "bad_number"}, 400)
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_pay_item_get(iid)
    if not old:
        return _json({"error": "not_found"}, 404)
    if old.get("kind") not in pay.PENALTY_KINDS:
        return _json({"error": "not_penalty"}, 400)
    if old.get("cancelled_at"):
        return _json({"error": "cancelled"}, 409)
    note = body.get("note")
    fields = {"amount": amount, "per_month": per_month if per_month < amount else 0,
              "note": str(old.get("note") or "" if note is None else note).strip()[:80],
              "revised_at": datetime.now(timezone.utc), "revised_by": _who(body)}
    if _num(old.get("amount")) != amount and old.get("was") is None:
        fields["was"] = old.get("amount")
    await db.fin_pay_item_set(iid, fields)
    await _touch(min(month, str(old.get("from") or old.get("day") or month)[:7]))
    await _pn.tell_safe(old.get("name"), _pn.edited(old, {**old, **fields}, _who(body)))
    parts = f" по {fields['per_month']}/мес" if fields["per_month"] else ""
    log.info(f"[fin] зарплаты: {old.get('name')} {pay.KINDS.get(old.get('kind'), '')} пересмотрен "
             f"{old.get('amount')} → {amount}{parts} · {_who(body) or '—'}")
    return _json({"ok": True, "book": await build(month)})


@require_owner
async def handle_pay_item_restore(request):
    """POST {id, month, as} — вернуть отменённый штраф или удержание: снова
    снимается с зарплаты, пометка об отмене уходит."""
    try:
        body = await request.json()
        iid = str(body.get("id") or "")
        month = _month_arg(body.get("month")) if body.get("month") else _biz_day()[:7]
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_pay_item_get(iid)
    if not old:
        return _json({"error": "not_found"}, 404)
    if old.get("kind") not in pay.PENALTY_KINDS:
        return _json({"error": "not_penalty"}, 400)
    if old.get("cancelled_at"):
        await db.fin_pay_item_set(iid, {"cancelled_at": None, "cancelled_by": "",
                                        "restored_at": datetime.now(timezone.utc), "restored_by": _who(body)})
        await _touch(min(month, str(old.get("from") or old.get("day") or month)[:7]))
        await _pn.tell_safe(old.get("name"), _pn.restored(old, _who(body)))
        log.info(f"[fin] зарплаты: {old.get('name')} {pay.KINDS.get(old.get('kind'), '')} {old.get('amount')} возвращён · {_who(body) or '—'}")
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
    pay_way = str(body.get("pay") or "")
    if pay_way not in PAY_WAYS:
        return _json({"error": "bad_pay"}, 400)
    # Откуда деньги (владелец, 3 окт 2026): из стопки РП — как было; «с рук» —
    # водитель оставил себе из наличных смены hands_day. Тогда сейф не
    # трогается: из выручки той смены сумма вычитается записью расхода
    # водителя, а зарплата записывается расходом фонда с pay=hands.
    src = str(body.get("src") or "")
    if src not in ("", "rp", "hands"):
        return _json({"error": "bad_src"}, 400)
    hands = None
    if src == "hands":
        try:
            hands_day = _day_arg(body.get("hands_day") or day)
        except Exception:                         # noqa: BLE001
            return _json({"error": "bad_request"}, 400)
        hands, отказ = await _hands_check(name, hands_day, amount, str(body.get("req") or ""))
        if отказ:
            return _json(отказ, 409 if отказ.get("error") in ("collected", "no_cash", "req_taken") else 400)
        pay_way = ""
    doc = {"_id": secrets.token_hex(6), "day": day, "book": "rp", "amount": amount,
           "comment": str(body.get("note") or "").strip()[:120] or "Зарплата",
           "who": name, "line": "", "kind": "salary", "pay_month": month,
           **({"pay": "crypto"} if pay_way == "crypto" else {}),
           **({"pay": "hands", "src": "hands", "hands_day": hands["day"]} if hands else {}),
           "by": who, "at": datetime.now(timezone.utc)}
    async with _CR_LOCK:
        if pay_way == "crypto":
            отказ = await _cr_spend_check(day, amount)
            if отказ:
                return _json(отказ, 409)
        if hands:
            doc["hands_extra"] = await _hands_extra_put(hands, doc, who)
        await db.fin_entry_add(doc)
    await _touch(min(month, day[:7], (hands or {}).get("day", day)[:7]))
    await _pn.tell_safe(name, _pn.payout(amount, day, month, doc["comment"], who,
                                        hands_day=(hands or {}).get("day", "")))
    log.info(f"[fin] зарплата {name} {amount} за {month} ({day})"
             + (f" · из наличных на руках за {hands['day']}" if hands else "") + f" · {who or '—'}")
    await backdate.notify(day, who, f"финансы: зарплата {name}",
                          f"{amount} AED" + (f" · из выручки на руках за {hands['day']}" if hands else ""))
    return _json({"ok": True, "id": doc["_id"], "hands": hands and {"day": hands["day"], "extra": doc.get("hands_extra")},
                  "book": await build(month)})


async def _hands_info(name: str, day: str) -> dict:
    """Сколько наличных у водителя на руках за смену day — той же
    арифметикой, что сбор выручки (cash_math): наличные заказов − чай −
    согласованные расходы наличными − уже оставленная зарплата. Плюс: сдана ли
    выручка его района за этот день и что он прислал на согласование."""
    import bizday, cash_math
    import config_staff as _staff
    import expense_routes as _exp
    drv = next((d for d in _staff.all_drivers() if d.get("name") == name), None)
    oid = (drv or {}).get("district") or ""
    try:
        orders = list((await db.orders_from(bizday.since_utc(day))).values())
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] заказы за {day}: {e}")
        orders = []
    mine = [o for o in orders if o.get("status") == "delivered" and bizday.order_day(o) == day
            and (o.get("driver") or "").strip() == name]
    cash_orders = [o for o in mine if cash_math.pays_cash(o)]
    cash = int(round(sum(cash_math.order_money(o)["aed"] for o in cash_orders)))
    tips = sum(cash_math.order_tea(o) for o in mine)
    row = await db.get_driver_day(day, name) or {}
    spend = _staff.meal_of(row) if row.get("working") is not None else 0
    kept, requests = 0, []
    for e in row.get("extras") or []:
        st = str(e.get("status") or "approved")
        if st == "approved" and not _exp.is_card(e) and not _exp.is_bottle(e):
            if e.get("salary_of"):
                kept += _exp._signed(e)
            else:
                spend += _exp._signed(e)
        elif st == "pending":
            requests.append({"id": e.get("id") or "", "kind": e.get("kind") or "other",
                             "kind_t": _exp._kind(e).get("t", ""), "amount": _exp._amount(e.get("amount")),
                             "comment": e.get("comment") or "", "at": str(e.get("at") or "")})
    marks = await db.checklist_get(day) or {}
    collected = bool(((marks.get(f"cash:{oid}") or {}).get("done")) or ((marks.get("cash") or {}).get("done")))
    have = cash - tips - spend - kept
    return {"name": name, "day": day, "district": oid, "driver": bool(drv), "cash": cash, "tips": tips,
            "spend": spend, "kept": kept, "have": have, "collected": collected, "requests": requests}


async def _hands_check(name: str, day: str, amount: float, req: str):
    """Можно ли выдать зарплату из наличных на руках за эту смену."""
    if day > _biz_day():
        return None, {"error": "future"}
    if day < _add_days(_biz_day(), -31):
        return None, {"error": "too_old"}
    info = await _hands_info(name, day)
    if not info["driver"]:
        return None, {"error": "not_driver"}
    if info["collected"]:
        return None, {"error": "collected", **{k: info[k] for k in ("have", "day")}}
    if amount > info["have"] + 0.005:
        return None, {"error": "no_cash", **{k: info[k] for k in ("have", "cash", "tips", "spend", "kept", "day")}}
    if req and not any(r["id"] == req for r in info["requests"]):
        return None, {"error": "req_taken"}
    info["req"] = req
    return info, None


async def _hands_extra_put(hands: dict, doc: dict, who: str) -> str:
    """Вычет из наличных смены — записью расхода водителя (согласованной):
    так его видят и сбор выручки, и итоги смены водителя, и книга. Запрос,
    который водитель прислал сам, — подхватываем: он и становится этим
    вычетом, а не одобряется как обычный расход. Прежние поля запоминаем в
    prev — убрали выплату, запрос вернётся на согласование как был."""
    import config_staff as _staff
    text = f"Зарплата за {pay_month_t(doc['pay_month'])}"
    now = datetime.now(timezone.utc)
    if hands.get("req"):
        row = await db.get_driver_day(hands["day"], hands["name"]) or {}
        e = next((x for x in row.get("extras") or [] if x.get("id") == hands["req"]), None) or {}
        prev = {k: e.get(k) for k in ("kind", "status", "amount", "comment", "pay")}
        await db.set_driver_expense_fields(hands["day"], hands["name"], hands["req"], {
            "kind": "other", "status": "approved", "amount": int(round(float(doc["amount"]))), "comment": text,
            "pay": "cash", "salary_of": doc["_id"], "prev": prev, "decided_by": who,
            "decided_at": now.isoformat(), "decided_note": "", "photo_ok": "ok", "car_ok": "ok"})
        return hands["req"]
    iid = secrets.token_hex(5)
    drv = next((d for d in _staff.all_drivers() if d.get("name") == hands["name"]), None) or {}
    await db.add_driver_expense(hands["day"], hands["name"], {
        "id": iid, "amount": int(round(float(doc["amount"]))), "kind": "other", "comment": text, "pay": "cash",
        "status": "approved", "salary_of": doc["_id"], "by": who, "at": now.isoformat(),
        "district": drv.get("district") or "", "decided_by": who, "decided_at": now.isoformat()})
    return iid


async def _hands_extra_drop(old: dict) -> None:
    """Выплату убрали — вычет из наличных смены уходит: свой — стирается,
    подхваченный запрос водителя — возвращается на согласование как был."""
    day, name, xid = str(old.get("hands_day") or ""), str(old.get("who") or ""), str(old.get("hands_extra") or "")
    if not (day and name and xid):
        return
    row = await db.get_driver_day(day, name) or {}
    e = next((x for x in row.get("extras") or [] if x.get("id") == xid), None)
    if not e:
        return
    prev = e.get("prev")
    if isinstance(prev, dict):
        await db.set_driver_expense_fields(day, name, xid, {**{k: v for k, v in prev.items()}, "salary_of": "",
                                                            "prev": "", "status": prev.get("status") or "pending"})
    else:
        await db.del_driver_expense(day, name, xid)
    try:
        await _touch(day[:7])
    except Exception:                             # noqa: BLE001
        pass


def pay_month_t(month: str) -> str:
    import pay_notify as _p
    try:
        return _p.month_t(month)
    except Exception:                             # noqa: BLE001
        return month


@require_owner
async def handle_pay_hands(request):
    """GET /api/owner/finance/book/pay/hands?name=&day= — наличные на руках у
    водителя за смену: можно ли выдать из них зарплату и что он прислал."""
    name = str(request.query.get("name") or "").strip()[:40]
    try:
        day = _day_arg(request.query.get("day") or _biz_day())
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    if not name:
        return _json({"error": "bad_request"}, 400)
    info = await _hands_info(name, day)
    info["today"] = _biz_day()
    return _json(info)


FIELD_T = {
    "handed_fact": "сдали по факту", "ordered_fact": "заказали по счёту",
    "aside": "отложили Барракуде", "collected": "отложили в РП", "pay": "оплата Барракуде",
    "collected_cr": "в РП криптой",
    "extra_rp": "приход в фонд", "pay_b": "оплата Барракуде из отложенного",
    "pay_b_extra": "оплата Барракуде сверх отложенного", "note": "заметка дня",
}
BOOK_T = {"rp": "расход из фонда", "np": "выплата из прибыли", "in": "приход в фонд", "mv": "перевод между счетами"}


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/finance/book", handle_book, "GET"),
        ("/api/owner/finance/book/day", handle_day_set, "POST"),
        ("/api/owner/finance/book/day/ok", handle_day_ok, "POST"),
        ("/api/owner/finance/book/day/pay", handle_pay_b, "POST"),
        ("/api/owner/finance/book/photo/{id}", handle_entry_photo, "GET"),
        ("/api/owner/finance/book/entry", handle_entry_add, "POST"),
        ("/api/owner/finance/book/entry", handle_entry_del, "DELETE"),
        ("/api/owner/finance/book/month", handle_month_set, "POST"),
        ("/api/owner/finance/book/budget", handle_budget_set, "POST"),
        ("/api/owner/finance/book/budget", handle_budget_del, "DELETE"),
        ("/api/owner/finance/book/budget/fill", handle_budget_fill, "POST"),
        ("/api/owner/finance/book/pay/person", handle_pay_person, "POST"),
        ("/api/owner/finance/book/pay/month", handle_pay_month_set, "POST"),
        ("/api/owner/finance/book/pay/order", handle_pay_order, "POST"),
        ("/api/owner/finance/book/pay/work", handle_pay_work, "POST"),
        ("/api/owner/finance/book/pay/item", handle_pay_item_add, "POST"),
        ("/api/owner/finance/book/pay/item", handle_pay_item_del, "DELETE"),
        ("/api/owner/finance/book/pay/item/edit", handle_pay_item_edit, "POST"),
        ("/api/owner/finance/book/pay/item/restore", handle_pay_item_restore, "POST"),
        ("/api/owner/finance/fines/decide", handle_fine_decide, "POST"),
        ("/api/owner/finance/discipline", handle_discipline, "GET"),
        ("/api/owner/finance/fines/rules", handle_fine_rules, "GET"),
        ("/api/owner/finance/fines/rules", handle_fine_rules, "POST"),
        ("/api/owner/finance/bonus",        handle_tenure,     "GET"),
        ("/api/owner/finance/bonus/pay",    handle_tenure_pay, "POST"),
        ("/api/owner/finance/book/pay/out", handle_pay_out, "POST"),
        ("/api/owner/finance/book/pay/hands", handle_pay_hands, "GET"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt)
            seen.add(path)
        r.add_route(method, path, handler)
    log.info("[fin] routes mounted")
