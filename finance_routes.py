"""Книга учёта денег — раздел «Финансы» владельца.

Источник — месячный отчёт старшего (Excel из четырёх листов). Что приложение
знает само, считается здесь на лету и руками не вводится:
  сдали      — наличные доставленных заказов за учётные сутки минус расходы
               водителей (питание и согласованные разовые) — та же цифра, что
               в «Сборе выручки»;
  заказали   — поставки этого дня в закупочных ценах (stock_value.cost_map);
  продали    — вся выручка дня, по способам оплаты.
Что старший решает сам, лежит в fin_days / fin_entries / fin_months (db.py):
отложили в сейф Б, собрал в фонд, оплаты Баракуде, доп. приход, расходы из
фонда, выплаты из прибыли, переносы и пересчёты сейфов.

Математика книги — finance_calc.compute(); здесь только сбор входных чисел и
запись ручных. Один запрос отдаёт весь месяц: экран рисует день, четыре книги
и итог из одного ответа, без второго похода на сервер.
"""
from __future__ import annotations

import calendar
import logging
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import backdate
import finance_calc as calc
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger(__name__)

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе
MAX_AMOUNT = 10_000_000

DAY_FIELDS = ('handed_fact', 'ordered_fact', 'aside', 'collected', 'extra_rp',
              'pay_b', 'pay_b_extra')
MONTH_FIELDS = ('safe_b_open', 'debt_b_open', 'carry_np', 'storage',
                'safe_np_fact', 'safe_b_fact')
BOOKS = ('rp', 'np')


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


def _num(v):
    """Число из тела запроса; None и пустая строка — «снять значение»."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("bad_number")
    if f != f or abs(f) > MAX_AMOUNT:
        raise ValueError("bad_number")
    r = round(f, 2)
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
    first, last = days[0], days[-1]
    since = _utc_iso(_day_start(first))
    until = _utc_iso(_day_start(last) + timedelta(days=1))
    try:
        orders = await db.orders_between(since, until)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fin] заказы не прочитаны: {e}")
        orders = []
    out = {d: dict(gross=0, cash=0, crypto=0, card=0, debt=0, tips=0, orders=0,
                   cash_by=dict()) for d in days}
    for o in orders:
        try:
            ts = datetime.fromisoformat(o.get("timestamp", "")).replace(
                tzinfo=timezone.utc).astimezone(DUBAI_TZ)
        except (ValueError, TypeError):
            continue
        day = _biz_day(ts)
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
    отдельно, в расход не идут."""
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
    for r in rows:
        s = out.get(r.get("day") or "")
        if s is None:
            continue
        w = r.get("working")
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
    return out


async def _purchases(days: list[str]) -> dict:
    """Поставки по дням в закупочных ценах. Основная — «заказали у Баракуды»,
    с других баз — отдельно. Цена за бутылку = цена учётной единицы / бутылок
    в ней (пиво идёт ящиками). Позиция без цены в сумму не попадает — рядом
    доля бутылок, у которых цена известна."""
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


# ── Месяц целиком ────────────────────────────────────────────────────────────
async def _opening(month: str, depth: int = 0) -> dict:
    """Переносы месяца: что вписано руками — главнее; остальное берём из
    закрытия прошлого месяца, если он вообще вёлся."""
    doc = await db.fin_month_get(month)
    explicit = {k: doc.get(k) for k in MONTH_FIELDS if doc.get(k) is not None}
    carried: dict = {}
    if depth < 6:
        prev = _prev_month(month)
        prev_doc = await db.fin_month_get(prev)
        days = _month_days(prev)
        touched = bool(prev_doc) or bool(await db.fin_days_get(days[0], days[-1])) \
            or bool(await db.fin_entries_get(days[0], days[-1]))
        if touched:
            book = await build(prev, depth + 1, light=True)
            carried = calc.carry_from(book)
    opening = {**{k: v for k, v in carried.items() if v is not None}, **explicit}
    return {"opening": opening, "explicit": explicit, "carried": carried,
            "note": doc.get("note") or ""}


async def build(month: str, depth: int = 0, light: bool = False) -> dict:
    days = _month_days(month)
    today = _biz_day()
    sales, spend, purch, manual, entries, opening = (
        await _sales(days), await _spend(days), await _purchases(days),
        await db.fin_days_get(days[0], days[-1]),
        await db.fin_entries_get(days[0], days[-1]),
        await _opening(month, depth))
    by_day_entries: dict = {}
    for e in entries:
        by_day_entries.setdefault(e.get("day"), {"rp": [], "np": []})
        row = {"id": e.get("_id"), "amount": e.get("amount"), "comment": e.get("comment") or "",
               "who": e.get("who") or "", "by": e.get("by") or "",
               "at": str(e.get("at") or "")}
        by_day_entries[e.get("day")][e.get("book") if e.get("book") in BOOKS else "rp"].append(row)
    rows = []
    for d in days:
        s, sp, pu, m = sales[d], spend[d], purch[d], manual.get(d) or {}
        en = by_day_entries.get(d) or {"rp": [], "np": []}
        handed = s["cash"] - sp["spend"]
        ordered_auto = pu["ordered"]
        rows.append(dict(
            day=d, gross=s["gross"], cash=s["cash"], card=s["card"], crypto=s["crypto"],
            debt=s["debt"], tips=s["tips"], spend=sp["spend"], handed=handed,
            handed_fact=m.get("handed_fact"),
            ordered=m["ordered_fact"] if m.get("ordered_fact") is not None else ordered_auto,
            ordered_extra=pu["ordered_extra"],
            aside=m.get("aside"), collected=m.get("collected"), extra_rp=m.get("extra_rp"),
            pay_b=m.get("pay_b"), pay_b_extra=m.get("pay_b_extra"),
            expenses=en["rp"], payouts=en["np"]))
    book = calc.compute(rows, opening["opening"])
    # Сверх математики — то, что нужно экрану: откуда взялось «заказали»,
    # ждущие расходы, отметки сбора, будущее.
    marks = {} if light else await _marks([d for d in days if d <= today])
    for i, d in enumerate(days):
        r, s, sp, pu, m = book["days"][i], sales[d], spend[d], purch[d], manual.get(d) or {}
        r.update(orders=s["orders"], debt=s["debt"], spend_pending=sp["pending"],
                 ordered_auto=pu["ordered"], ordered_fact=m.get("ordered_fact"),
                 ordered_cover=pu["cover"], supplies=pu["supplies"],
                 note=m.get("note") or "", future=d > today, today=d == today)
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
                prev_month=_prev_month(month))
    return book


# ── Ручки ────────────────────────────────────────────────────────────────────
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
    снять. Прошедший день уходит владельцам в бот (backdate)."""
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
    who = str(body.get("as") or "").strip()[:40]
    if value is None or value == "":
        await db.fin_day_set(day, {"by": who}, unset=[field])
    else:
        await db.fin_day_set(day, {field: value, "by": who})
    log.info(f"[fin] {day} {field} → {value!r} · {who or '—'}")
    await backdate.notify(day, who, "книга учёта: " + FIELD_T.get(field, field),
                          "" if value in (None, "") else f"{value} AED")
    return _json({"ok": True, "day": day, "field": field, "value": value,
                  "book": await build(day[:7])})


@require_owner
async def handle_entry_add(request):
    """POST {day, book: rp|np, amount, comment, who, as} — строка расхода из
    фонда (rp) или выплаты из прибыли (np)."""
    try:
        body = await request.json()
        day = _day_arg(body.get("day"))
        book = str(body.get("book") or "rp")
        if book not in BOOKS:
            return _json({"error": "bad_book"}, 400)
        amount = _num(body.get("amount"))
        if amount is None or amount <= 0:
            return _json({"error": "bad_amount"}, 400)
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = str(body.get("as") or "").strip()[:40]
    doc = {"_id": secrets.token_hex(6), "day": day, "book": book, "amount": amount,
           "comment": str(body.get("comment") or "").strip()[:120],
           "who": str(body.get("who") or "").strip()[:60],
           "by": who, "at": datetime.now(timezone.utc)}
    await db.fin_entry_add(doc)
    log.info(f"[fin] {day} {book} +{amount} «{doc['comment']}» · {who or '—'}")
    await backdate.notify(day, who, "книга учёта: " + ("расход из фонда" if book == "rp"
                                                        else "выплата из прибыли"),
                          f"{amount} AED {doc['comment']}".strip())
    return _json({"ok": True, "id": doc["_id"], "book": await build(day[:7])})


@require_owner
async def handle_entry_del(request):
    """DELETE {id, as} — убрать строку."""
    try:
        body = await request.json()
        eid = str(body.get("id") or "")
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    old = await db.fin_entry_get(eid)
    if not old:
        return _json({"error": "not_found"}, 404)
    await db.fin_entry_del(eid)
    who = str(body.get("as") or "").strip()[:40]
    log.info(f"[fin] {old.get('day')} {old.get('book')} −{old.get('amount')} убрано · {who or '—'}")
    await backdate.notify(str(old.get("day") or ""), who, "книга учёта: строка убрана",
                          f"{old.get('amount')} AED {old.get('comment') or ''}".strip())
    return _json({"ok": True, "book": await build(str(old.get("day") or "")[:7])})


@require_owner
async def handle_month_set(request):
    """POST {month, field, value, as} — перенос, хранение или пересчёт сейфа."""
    try:
        body = await request.json()
        month = _month_arg(body.get("month"))
        field = str(body.get("field") or "")
        if field not in MONTH_FIELDS and field != "note":
            return _json({"error": "bad_field"}, 400)
        value = (str(body.get("value") or "").strip()[:200] if field == "note"
                 else _num(body.get("value")))
    except Exception:                             # noqa: BLE001
        return _json({"error": "bad_request"}, 400)
    who = str(body.get("as") or "").strip()[:40]
    if value is None or value == "":
        await db.fin_month_set(month, {"by": who}, unset=[field])
    else:
        await db.fin_month_set(month, {field: value, "by": who})
    log.info(f"[fin] {month} {field} → {value!r} · {who or '—'}")
    return _json({"ok": True, "month": month, "field": field, "value": value,
                  "book": await build(month)})


FIELD_T = {
    "handed_fact": "сдали по факту", "ordered_fact": "заказали по счёту",
    "aside": "отложили в сейф Б", "collected": "собрал в фонд",
    "extra_rp": "доп. приход в фонд", "pay_b": "оплата Баракуде из сейфа Б",
    "pay_b_extra": "оплата Баракуде из ЧП / РП", "note": "заметка дня",
}


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
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt)
            seen.add(path)
        r.add_route(method, path, handler)
    log.info("[fin] routes mounted")
