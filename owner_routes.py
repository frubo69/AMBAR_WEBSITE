"""
Routes for the owner dashboard (mini-app at /owner/).

Endpoints:
    GET /api/owner/ping     — auth-guarded smoke test
    GET /api/owner/finance  — revenue/profit/tips/by-office/trend (Money tab)

Register with:
    from owner_routes import setup as setup_owner_routes
    setup_owner_routes(app)
"""
import time, json, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from owner_auth import require_owner, CORS_HEADERS, install_alerter
from config import OWNER_IDS, MANAGER_IDS
import db
import os, logging
# Premium card lists live in api_server (single source of truth).
from api_server import _FOUNDER_ID, _PREMIUM_IDS, _WORLDWIDE_IDS, tg_send, tg_delete, BOT_TOKEN

log = logging.getLogger(__name__)
# Owner-bot token used to push notifications to the owner via @ambar_manage_bot.
OWNER_BOT_TOKEN = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")


# ─── constants ──────────────────────────────────────────────────────────
DUBAI_TZ = timezone(timedelta(hours=4))

# Who is allowed to see/operate the "Доступ для менеджеров" section.
# Currently single owner (7865205960) plus legacy access for 686932322
# during the transition. Remove 686932322 once new owner is fully onboarded.
LEGACY_MGR_UI_ACCESS = {686932322}


def _can_manage_users(uid: int) -> bool:
    return uid in OWNER_IDS or uid in LEGACY_MGR_UI_ACCESS

# Estimated profit margin until per-item cost-of-goods is tracked.
# Mock data assumed ~35% (6480/18420). Revisit once catalog has cost field.
MARGIN_PCT = 35

# Statuses that count as realized revenue.
REVENUE_STATUSES = ("delivered",)

# Office IDs (display order matches dashboard).
from config_offices import OFFICE_IDS, OFFICE_NAMES   # офисы ≡ районы

VALID_PERIODS = ("today", "yesterday", "week", "month", "year")


# ─── helpers ────────────────────────────────────────────────────────────
def _now_dubai() -> datetime:
    return datetime.now(DUBAI_TZ)


def _parse_ts(ts: str):
    """Order timestamps are stored as UTC ISO strings without 'Z'.
    Returns Dubai-local datetime, or None on parse failure."""
    if not ts:
        return None
    try:
        dt_utc = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(DUBAI_TZ)
    except (ValueError, TypeError):
        return None

# ── Сутки бизнеса ≠ календарные ──────────────────────────────────────────────
# Смена работает с 12:00 до 06:00 следующего дня, поэтому заказ, принятый в
# 02:00, относится к вечеру предыдущего дня, а не к новому. Сутки считаем от
# полудня до полудня: всё, что раньше 12:00, — это ещё вчерашний день.
SHIFT_START_HOUR = int(os.getenv("AMBAR_SHIFT_START_HOUR", "12"))


def _biz_day_start(ref: datetime) -> datetime:
    """Начало рабочих суток, которым принадлежит момент `ref` (Дубай)."""
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return anchor if ref >= anchor else anchor - timedelta(days=1)


def _biz_date(dt: datetime):
    """Дата, которой подписаны рабочие сутки данного момента."""
    return _biz_day_start(dt).date()


def _period_window(period: str, ref: datetime = None):
    """Return (start, end, prev_start, prev_end) for the given period,
    all in Dubai TZ. `end` is exclusive (start of tomorrow for daily-aligned)."""
    ref = ref or _now_dubai()
    today_start = _biz_day_start(ref)
    tomorrow_start = today_start + timedelta(days=1)

    if period == "today":
        start, end = today_start, tomorrow_start
        prev_start, prev_end = start - timedelta(days=1), start
    elif period == "yesterday":
        end = today_start
        start = today_start - timedelta(days=1)
        prev_end, prev_start = start, start - timedelta(days=1)
    elif period == "week":
        end = tomorrow_start
        start = end - timedelta(days=7)
        prev_end = start
        prev_start = prev_end - timedelta(days=7)
    elif period == "month":
        end = tomorrow_start
        start = end - timedelta(days=30)
        prev_end = start
        prev_start = prev_end - timedelta(days=30)
    elif period == "year":
        end = tomorrow_start
        start = end - timedelta(days=365)
        prev_end = start
        prev_start = prev_end - timedelta(days=365)
    else:
        raise ValueError(f"unknown period: {period}")

    return start, end, prev_start, prev_end

def _open_at(all_orders: dict, end_dt: datetime, statuses, office_id=None) -> list:
    """Заказы, до сих пор висящие в одном из `statuses` и созданные ДО конца окна.

    «Ожидают» и «в пути» — это состояние, а не событие: истории статусов у заказа
    нет, поэтому честно ответить «что висело вчера в 23:00» невозможно. Но заказ,
    созданный ПОСЛЕ конца окна, к этому окну точно не относится — раньше такие
    заказы подмешивались в цифры прошлых дней, и открыв вчера, можно было увидеть
    сегодняшний зависший заказ.

    Ограничения сверху достаточно: на живом дне окно кончается в будущем, поэтому
    видно всё зависшее любой давности (это и нужно оператору), а на прошлом дне —
    только то, что к тому моменту уже существовало и до сих пор не закрыто.
    """
    out = []
    for o in all_orders.values():
        if o.get("status") not in statuses:
            continue
        if office_id is not None and (o.get("office_id") or "") != office_id:
            continue
        dt = _parse_ts(o.get("timestamp"))
        if dt is None or dt >= end_dt:
            continue
        out.append(o)
    out.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return out


def _orders_in_window(all_orders: dict, start_dt: datetime, end_dt: datetime):
    """Yield (dubai_dt, order) pairs for delivered orders inside the window."""
    out = []
    for o in all_orders.values():
        if o.get("status") not in REVENUE_STATUSES:
            continue
        dt = _parse_ts(o.get("timestamp"))
        if dt is None:
            continue
        if start_dt <= dt < end_dt:
            out.append((dt, o))
    return out


def _all_orders_in_window(all_orders: dict, start_dt: datetime, end_dt: datetime):
    """Like _orders_in_window but doesn't filter by status — every order
    placed during the window, regardless of outcome."""
    out = []
    for o in all_orders.values():
        dt = _parse_ts(o.get("timestamp"))
        if dt is None:
            continue
        if start_dt <= dt < end_dt:
            out.append((dt, o))
    return out


def _sum_field(orders, field: str) -> int:
    return sum(int(o.get(field, 0) or 0) for _, o in orders)


def _by_office(orders) -> list:
    """Выручка по офисам (районам) — списком, т.к. состав офисов теперь
    настраиваемый. Заказы без определимого района (их единицы) идут отдельной
    строкой «Без района», чтобы сумма сходилась."""
    tot = {}
    for _, o in orders:
        oid = o.get("office_id") or ""
        tot[oid] = tot.get(oid, 0) + int(o.get("total", 0) or 0)
    rows = [{"id": oid, "name": OFFICE_NAMES[oid], "aed": tot.pop(oid, 0)}
            for oid in OFFICE_IDS]
    rest = sum(tot.values())
    if rest:
        rows.append({"id": "legacy", "name": "Без района", "aed": rest})
    return rows


def _bucket_trend(orders, start_dt: datetime, period: str) -> list:
    """Bucket revenue into time slots appropriate for the period:
    today→24 hours, week→7 days, month→30 days, year→12 months."""
    if period in ("today", "yesterday"):
        buckets = [0] * 24
        # Слот 0 — это первый час смены (12:00), а не полночь: считаем смещение от
        # начала суток, тогда график читается в том порядке, в каком шёл день.
        for dt, o in orders:
            idx = int((dt - start_dt).total_seconds() // 3600)
            if 0 <= idx < 24:
                buckets[idx] += int(o.get("total", 0) or 0)
        # Обрезаем по текущему часу только ЖИВЫЕ сутки, чтобы не рисовать пустое
        # будущее; любой завершённый день показывает все 24 часа.
        now = _now_dubai()
        if start_dt == _biz_day_start(now):
            elapsed = int((now - start_dt).total_seconds() // 3600)
            return buckets[:max(0, min(23, elapsed)) + 1] or [0]
        return buckets
    if period == "week":
        buckets = [0] * 7
        for dt, o in orders:
            idx = (_biz_date(dt) - start_dt.date()).days
            if 0 <= idx < 7:
                buckets[idx] += int(o.get("total", 0) or 0)
        return buckets
    if period == "month":
        buckets = [0] * 30
        for dt, o in orders:
            idx = (_biz_date(dt) - start_dt.date()).days
            if 0 <= idx < 30:
                buckets[idx] += int(o.get("total", 0) or 0)
        return buckets
    if period == "year":
        buckets = [0] * 12
        for dt, o in orders:
            bd = _biz_date(dt)
            months_diff = (bd.year - start_dt.year) * 12 + (bd.month - start_dt.month)
            if 0 <= months_diff < 12:
                buckets[months_diff] += int(o.get("total", 0) or 0)
        return buckets
    return []


def _last_7_days(all_orders: dict, ref_day_start: datetime = None) -> list:
    """Столбики выручки за 7 дней. Индекс 6 — просматриваемый день, 0 — за 6 дней
    до него. Раньше всегда упирались в «сегодня», поэтому, отлистав на три дня
    назад, рядом с цифрами того дня продолжали висеть столбики сегодняшней недели."""
    today_start = ref_day_start or _biz_day_start(_now_dubai())
    end = today_start + timedelta(days=1)
    start = end - timedelta(days=7)
    orders = _orders_in_window(all_orders, start, end)
    buckets = [0] * 7
    for dt, o in orders:
        idx = (_biz_date(dt) - start.date()).days
        if 0 <= idx < 7:
            buckets[idx] += int(o.get("total", 0) or 0)
    return buckets


# Fallback when an order has no ETA set by the operator.
LATE_THRESHOLD_FALLBACK = 25
# Grace period: delivery up to this many minutes over ETA is still OK.
LATE_GRACE_MIN = 5


def _delivery_stats(orders) -> dict:
    """Compute average delivery time + late count from delivered orders.

    Late = actual duration > order's ETA + grace (3 min).
    If the order has no ETA, falls back to 25 min."""
    durations = []
    late_count = 0
    for _, o in orders:
        placed_ts = o.get("timestamp")
        delivered_ts = o.get("updated_at") or o.get("delivered_at")
        if not placed_ts or not delivered_ts:
            continue
        try:
            t0 = datetime.fromisoformat(placed_ts).replace(tzinfo=timezone.utc)
            t1 = datetime.fromisoformat(delivered_ts).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        diff_min = (t1 - t0).total_seconds() / 60
        if 0 < diff_min < 24 * 60:
            durations.append(diff_min)
            eta = int(o.get("eta") or 0) or LATE_THRESHOLD_FALLBACK
            if int(diff_min) > eta + LATE_GRACE_MIN:
                late_count += 1
    if not durations:
        return {"avg_min": 0, "late_count": 0, "late_pct": 0, "sample": 0}
    avg_min = round(sum(durations) / len(durations))
    late_pct = round(late_count / len(durations) * 100, 1)
    return {
        "avg_min":    avg_min,
        "late_count": late_count,
        "late_pct":   late_pct,
        "sample":     len(durations),
    }


def _delta_pct(curr: int, prev: int) -> float:
    if prev == 0:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


def _delta_label(pct: float) -> str:
    if pct > 0:
        return f"▲ {pct:.1f}%"
    if pct < 0:
        return f"▼ {abs(pct):.1f}%"
    return "0%"


# ─── handlers ───────────────────────────────────────────────────────────
@require_owner
async def handle_ping(request):
    """Cheap health + identity check. Returns the authenticated owner's id
    plus capability flags so the UI can hide/show owner-only sections."""
    uid = request["owner_id"]
    return web.json_response(
        {
            "ok": True,
            "owner_id":         uid,
            "is_owner":         uid in OWNER_IDS,
            "can_manage_users": _can_manage_users(uid),
            "server_time":      int(time.time()),
        },
        headers=CORS_HEADERS,
    )


def _order_summary(o):
    placed = o.get("timestamp","")
    confirmed = o.get("confirmed_at","")
    delivered = o.get("updated_at","") if o.get("status") == "delivered" else ""
    cancelled_at = o.get("cancelled_at","") or o.get("declined_at","")
    cancelled_by = o.get("cancelled_by","")
    cancel_reason = o.get("cancel_reason","") or o.get("decline_reason","") or o.get("declineReason","")
    actual_min = None
    base = confirmed or placed
    if base and delivered:
        try:
            from datetime import datetime as _dt
            p = _dt.fromisoformat(base.replace("Z","+00:00"))
            d = _dt.fromisoformat(delivered.replace("Z","+00:00"))
            actual_min = max(0, int((d - p).total_seconds() / 60))
        except Exception:
            pass
    return {
        "id": o.get("order_id",""),
        "name": o.get("customer_name","—"),
        "username": o.get("username","—"),
        "customer_id": o.get("customer_id",""),
        "phone": o.get("phone","—"),
        "total": o.get("total", 0),
        "crypto": bool(o.get("payment_method") == "crypto" and o.get("paid")),
        "crypto_usdt": o.get("crypto_amount_usdt") or 0,
        "manual": bool(o.get("source") == "manual"),
        "created_by_name": o.get("created_by_name", ""),
        "district": o.get("district", ""),
        "dispatch_operator": o.get("dispatch_operator", ""),
        "driver": o.get("driver", ""),
        "status": o.get("status",""),
        "ts": placed,
        "eta": o.get("eta",""),
        "confirmed_at": confirmed,
        "delivered_at": delivered,
        "cancelled_at": cancelled_at,
        "cancelled_by": cancelled_by,
        "cancel_reason": cancel_reason,
        "actual_min": actual_min,
        "office": o.get("office_name",""),
        "address": o.get("address","—"),
        "gmap_link": o.get("gmap_link",""),
        "items_short": ", ".join(f"{it.get('name','')} ×{it.get('qty',1)}" for it in (o.get("items") or [])[:3]),
        "items": [
            {"id": it.get("id",""), "name": it.get("name",""), "qty": it.get("qty",1)}
            for it in (o.get("items") or [])
        ],
        "cancel_comment": o.get("cancel_comment","") or o.get("comment",""),
    }

@require_owner
async def handle_finance(request):
    """Finance summary powering the hero revenue card and Money tab.

    Query:
        period = today | week | month | year   (default: today)

    Response:
        revenue {current, previous, delta_pct, delta_label}
        profit  {current, margin_pct, estimated}
        tips
        by_office [{id, name, aed}] — офисы ≡ районы, состав настраиваемый
        trend []        — period-aware sparkline data
        bars_7d {values[7], average, total}  — always last 7 days for Money tab
    """
    period = request.query.get("period", "today")
    if period not in VALID_PERIODS:
        return web.json_response(
            {"error": f"invalid period (use one of: {', '.join(VALID_PERIODS)})"},
            status=400, headers=CORS_HEADERS,
        )

    # day_offset shifts the whole window N days into the past — powers the hero
    # card's day browser (swipe / arrows) without touching the period pills.
    try:
        day_offset = int(request.query.get("day_offset", "0") or 0)
    except ValueError:
        day_offset = 0
    day_offset = max(0, min(365, day_offset))

    all_orders = await db.get_all_orders()

    ref = (_now_dubai() - timedelta(days=day_offset)) if day_offset else None
    start, end, prev_start, prev_end = _period_window(period, ref)
    curr_orders = _orders_in_window(all_orders, start, end)        # delivered only
    prev_orders = _orders_in_window(all_orders, prev_start, prev_end)
    curr_all    = _all_orders_in_window(all_orders, start, end)    # any status
    prev_all    = _all_orders_in_window(all_orders, prev_start, prev_end)

    rev_curr = _sum_field(curr_orders, "total")
    rev_prev = _sum_field(prev_orders, "total")
    pct = _delta_pct(rev_curr, rev_prev)

    # Столбики и 7-дневный тренд идут за просматриваемым днём: для «сегодня» это
    # текущая неделя, для отлистанного дня — неделя, кончающаяся на нём.
    _bars_anchor = _biz_day_start(ref) if day_offset else None
    bars = _last_7_days(all_orders, _bars_anchor)
    bars_total = sum(bars)

    # ── KPI section ───────────────────────────────────────────────────
    orders_count_curr = len(curr_all)
    orders_count_prev = len(prev_all)
    orders_delta_count = orders_count_curr - orders_count_prev
    delivered_count   = len(curr_orders)
    declined_count    = sum(1 for _, o in curr_all if o.get("status") in ("declined", "cancelled"))
    open_pending      = _open_at(all_orders, end, ("pending",))
    open_route        = _open_at(all_orders, end, ("approved",))
    pending_count     = len(open_pending)
    in_route_count    = len(open_route)
    avg_check         = (rev_curr // delivered_count) if delivered_count else 0
    done_pct          = round(delivered_count / orders_count_curr * 100, 1) if orders_count_curr else 0
    # Order counts per office (any status, in window)
    orders_by_office = {oid: 0 for oid in OFFICE_IDS}
    for _, o in curr_all:
        oid = o.get("office_id")
        if oid in orders_by_office:
            orders_by_office[oid] += 1
    # Reviews: collect non-null review_score from delivered orders in window
    reviews = [int(o.get("review_score")) for _, o in curr_orders if o.get("review_score")]
    rating_avg   = round(sum(reviews) / len(reviews), 2) if reviews else 0
    rating_count = len(reviews)
    # Star distribution for rating detail
    rating_dist = {1:0, 2:0, 3:0, 4:0, 5:0}
    for r in reviews:
        if 1 <= r <= 5:
            rating_dist[r] += 1

    # Delivery time + late-rate KPIs (current vs previous for delta).
    deliv_curr = _delivery_stats(curr_orders)
    deliv_prev = _delivery_stats(prev_orders)
    avg_delta_min = deliv_curr["avg_min"] - deliv_prev["avg_min"] if deliv_prev["sample"] else 0

    # Last 7 days order count (any status) — for orders detail trend
    today_start = _bars_anchor or _biz_day_start(_now_dubai())
    week_end = today_start + timedelta(days=1)
    week_start = week_end - timedelta(days=7)
    orders_7d = [0] * 7
    for _, o in _all_orders_in_window(all_orders, week_start, week_end):
        dt = _parse_ts(o.get("timestamp"))
        if dt is None: continue
        idx = (_biz_date(dt) - week_start.date()).days
        if 0 <= idx < 7:
            orders_7d[idx] += 1

    pending_orders = open_pending[:20]
    inroute_orders = open_route[:20]
    delivered_orders_list = sorted(
        [o for _, o in curr_orders],
        key=lambda x: x.get("timestamp",""), reverse=True)[:20]
    declined_orders_list = sorted(
        [o for _, o in curr_all if o.get("status") in ("declined","cancelled")],
        key=lambda x: x.get("timestamp",""), reverse=True)[:20]

    period_lbl_for_rating = {"today":"сегодня","yesterday":"вчера","week":"неделя","month":"месяц","year":"год"}[period]

    # ── Разрез по офисам (страница «Офисы») ──────────────────────────────
    # Только реальные данные: выручка, заказы, среднее время доставки, рейтинг
    # и сколько заказов в работе прямо сейчас. Ничего выдуманного.
    _deliv_by, _all_by, _live_by = {}, {}, {}
    for _dt, _o in curr_orders:
        _deliv_by.setdefault(_o.get("office_id") or "", []).append(_o)
    for _dt, _o in curr_all:
        _all_by.setdefault(_o.get("office_id") or "", []).append(_o)
    for _o in _open_at(all_orders, end, ("pending", "approved")):
        _k = _o.get("office_id") or ""
        _live_by[_k] = _live_by.get(_k, 0) + 1

    offices_block = []
    for _oid in OFFICE_IDS:
        _dl = _deliv_by.get(_oid, [])
        _ds = _delivery_stats([(None, x) for x in _dl])
        _revs = [int(x["review_score"]) for x in _dl if x.get("review_score")]
        offices_block.append({
            "id":           _oid,
            "name":         OFFICE_NAMES[_oid],
            "aed":          sum(int(x.get("total", 0) or 0) for x in _dl),
            "orders":       len(_all_by.get(_oid, [])),
            "delivered":    len(_dl),
            "avg_min":      _ds["avg_min"],
            "avg_sample":   _ds["sample"],
            "late_count":   _ds["late_count"],
            "rating":       round(sum(_revs) / len(_revs), 2) if _revs else 0,
            "rating_count": len(_revs),
            "active":       _live_by.get(_oid, 0),
        })
    # Единичные заказы без признаков местоположения (нет района, координат и
    # узнаваемого адреса) — отдельной строкой, чтобы сумма сходилась.
    _legacy_dl = [x for k, v in _deliv_by.items() if k not in OFFICE_IDS for x in v]
    if _legacy_dl:
        _lds = _delivery_stats([(None, x) for x in _legacy_dl])
        offices_block.append({
            "id": "legacy", "name": "Без района", "legacy": True,
            "aed": sum(int(x.get("total", 0) or 0) for x in _legacy_dl),
            "orders": sum(len(v) for k, v in _all_by.items() if k not in OFFICE_IDS),
            "delivered": len(_legacy_dl),
            "avg_min": _lds["avg_min"], "avg_sample": _lds["sample"],
            "late_count": _lds["late_count"], "rating": 0, "rating_count": 0,
            "active": sum(v for k, v in _live_by.items() if k not in OFFICE_IDS),
        })

    # ── Crypto vs cash split ─────────────────────────────────────────
    # Crypto lands on the wallet at PAYMENT time (order placement), cash arrives
    # with the courier at delivery — the owner needs to instantly see which part
    # of the income is already on the wallet and which is physical cash.
    def _is_crypto(o):
        return o.get("payment_method") == "crypto" and o.get("paid")
    def _usdt(o):
        try:
            return float(o.get("crypto_amount_usdt") or 0)
        except (TypeError, ValueError):
            return 0.0
    crypto_delivered = [o for _, o in curr_orders if _is_crypto(o)]
    crypto_aed  = sum(o.get("total", 0) for o in crypto_delivered)
    crypto_usdt = round(sum(_usdt(o) for o in crypto_delivered), 2)
    # Paid on-chain but still in flight — the money is ALREADY on the wallet.
    inflight = [o for _, o in curr_all
                if _is_crypto(o) and o.get("status") in ("pending", "approved")]

    return web.json_response({
        "period": period,
        "currency": "AED",
        # Authoritative Dubai-local dates for the window — the client labels the
        # hero day from these (its own clock/TZ may differ from the office's).
        "window": {
            "date":       start.strftime("%Y-%m-%d"),
            "today":      _biz_day_start(_now_dubai()).strftime("%Y-%m-%d"),
            "day_offset": day_offset,
        },
        "revenue": {
            "current":     rev_curr,
            "previous":    rev_prev,
            "delta_pct":   pct,
            "delta_label": _delta_label(pct),
        },
        "profit": {
            "current":    round(rev_curr * MARGIN_PCT / 100),
            "margin_pct": MARGIN_PCT,
            "estimated":  True,
        },
        "tips":      _sum_field(curr_orders, "tip"),
        "by_method": {
            "crypto": {"aed": crypto_aed, "usdt": crypto_usdt, "count": len(crypto_delivered)},
            "cash":   {"aed": rev_curr - crypto_aed, "count": delivered_count - len(crypto_delivered)},
            "crypto_inflight": {
                "aed":   sum(o.get("total", 0) for o in inflight),
                "usdt":  round(sum(_usdt(o) for o in inflight), 2),
                "count": len(inflight),
            },
        },
        # App vs phone (operator POS) channel split — delivered orders in the period.
        "by_channel": (lambda m_aed, m_cnt: {
            "app":   {"aed": rev_curr - m_aed, "count": delivered_count - m_cnt},
            "phone": {"aed": m_aed, "count": m_cnt},
        })(sum(o.get("total", 0) for _, o in curr_orders if o.get("source") == "manual"),
           sum(1 for _, o in curr_orders if o.get("source") == "manual")),
        "by_office": _by_office(curr_orders),
        "offices":   offices_block,
        "trend":     _bucket_trend(curr_orders, start, period),
        "bars_7d": {
            "values":  bars,
            "total":   bars_total,
            "average": bars_total // 7 if bars_total else 0,
        },
        "kpi": {
            "orders":           orders_count_curr,
            "orders_delta":     orders_delta_count,
            "avg_check":        avg_check,
            "done":             delivered_count,
            "done_total":       orders_count_curr,
            "done_pct":         done_pct,
            "in_route":         in_route_count,
            "pending":          pending_count,
            "declined":         declined_count,
            "rating":           rating_avg,
            "rating_count":     rating_count,
            "rating_period":    period_lbl_for_rating,
            "rating_dist":      rating_dist,
            "orders_by_office": orders_by_office,
            "orders_7d":        orders_7d,
            # Delivery-time + late KPIs (computed from updated_at − timestamp
            # on delivered orders). avg_min = 0 means we have no samples yet.
            "avg_min":          deliv_curr["avg_min"],
            "avg_min_delta":    avg_delta_min,
            "avg_min_sample":   deliv_curr["sample"],
            "late_count":       deliv_curr["late_count"],
            "late_pct":         deliv_curr["late_pct"],
            "late_threshold":   LATE_THRESHOLD_FALLBACK,
            "pending_orders":   [_order_summary(o) for o in pending_orders],
            "inroute_orders":   [_order_summary(o) for o in inroute_orders],
            "delivered_orders": [_order_summary(o) for o in delivered_orders_list],
            "declined_orders":  [_order_summary(o) for o in declined_orders_list],
        },
    }, headers=CORS_HEADERS)


# ─── customers ─────────────────────────────────────────────────────────

def _card_for_user(user: dict) -> dict:
    """Premium card status. VIP = anyone with a card other than 'standard'.
    Mirrors api_server.handle_me logic so owner sees the same designation
    the customer sees in their loyalty card."""
    uid = int(user.get("telegram_id") or 0)
    if uid == _FOUNDER_ID:
        return {"type": "founder", "label": "FOUNDER", "number": 1, "total": 1}
    if uid in _PREMIUM_IDS:
        return {"type": "premium", "label": "ÉLITE", "number": _PREMIUM_IDS.index(uid) + 1, "total": 10}
    if uid in _WORLDWIDE_IDS:
        return {"type": "worldwide", "label": "WORLDWIDE", "number": _WORLDWIDE_IDS.index(uid) + 1, "total": 100}
    return {"type": "standard", "label": "", "number": 0, "total": 0}

def _initials(user: dict) -> str:
    fn = (user.get("first_name") or "")[:1].upper()
    ln = (user.get("last_name") or "")[:1].upper()
    return (fn + ln) or "?"


def _serialize_user(u: dict, orders: list | None = None) -> dict:
    """Convert a user doc into a JSON-safe dict for the owner dashboard."""
    card = _card_for_user(u)
    total_spent = int(u.get("total_spent", 0) or 0)
    orders_total = int(u.get("orders_total", 0) or 0)
    avg_check = total_spent // orders_total if orders_total else 0
    phones = u.get("phones", [])
    phone = phones[0] if phones else (u.get("phone") or "")

    out = {
        "telegram_id":   u.get("telegram_id"),
        "first_name":    u.get("first_name", ""),
        "last_name":     u.get("last_name", ""),
        "full_name":     u.get("full_name") or f'{u.get("first_name", "")} {u.get("last_name", "")}'.strip(),
        "username":      u.get("username", ""),
        "phone":         phone,
        "initials":      _initials(u),
        "card_type":     card["type"],          # founder | premium | worldwide | standard
        "card_label":    card["label"],         # FOUNDER | ÉLITE | WORLDWIDE | ""
        "card_number":   card["number"],
        "card_total":    card["total"],
        "is_vip":        card["type"] != "standard",
        "verified":      bool(u.get("verified")),
        "is_banned":     bool(u.get("is_banned")),
        # Debt programme (В ДОЛГ): whitelist flag + current balance in AED
        "debt_allowed":  bool(u.get("debt_allowed")),
        "debt":          round(float(u.get("debt") or 0), 2),
        "total_spent":   total_spent,
        "orders_total":  orders_total,
        "orders_done":   int(u.get("orders_done", 0) or 0),
        "orders_declined": int(u.get("orders_declined", 0) or 0),
        "avg_check":     avg_check,
        "referral_points": int(u.get("referral_points", 0) or 0),
        "referrals_count": len(u.get("referrals", [])),
        "first_seen":    u.get("first_seen", ""),
        "last_seen":     u.get("last_seen", ""),
        "addresses":     u.get("addresses", []),
        "verify_source": u.get("verify_source", ""),
        "verify_recommender_name":  u.get("verify_recommender_name", ""),
        "verify_recommender_phone": u.get("verify_recommender_phone", ""),
    }
    if orders is not None:
        out["recent_orders"] = orders[:20]
    return out


def _json_default(obj):
    """Handle datetime serialization for json_response."""
    from datetime import datetime as _dt
    if isinstance(obj, _dt):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


@require_owner
async def handle_customers(request):
    """Return all customers with stats, sorted by total_spent desc."""
    users = await db.get_all_customers()
    result = []
    for u in users:
        result.append(_serialize_user(u))
    result.sort(key=lambda c: c["total_spent"], reverse=True)
    return web.json_response(
        {"customers": result, "total": len(result)},
        headers=CORS_HEADERS,
        dumps=lambda o: __import__("json").dumps(o, default=_json_default),
    )


@require_owner
async def handle_customer_detail(request):
    """Return single customer profile + recent orders."""
    raw = request.match_info["telegram_id"]
    try:
        tg_id = int(raw)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)

    user = await db.get_user(tg_id)
    if not user:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    orders = await db.get_user_orders(tg_id)
    safe_orders = []
    for o in orders[:20]:
        safe_orders.append({
            "order_id":  o.get("order_id", ""),
            "status":    o.get("status", ""),
            "total":     int(o.get("total", 0) or 0),
            "tip":       int(o.get("tip", 0) or 0),
            "items":     o.get("items", []),
            "timestamp": o.get("timestamp", ""),
            "office_id": o.get("office_id", ""),
            "review_score": o.get("review_score"),
            "review_text":  o.get("review_text", ""),
        })

    return web.json_response(
        _serialize_user(user, safe_orders),
        headers=CORS_HEADERS,
        dumps=lambda o: __import__("json").dumps(o, default=_json_default),
    )


# ─── notification preferences ────────────────────────────────────────

@require_owner
async def handle_notif_prefs_get(request):
    """Return the saved notif prefs for the authenticated owner."""
    prefs = await db.get_owner_prefs(request["owner_id"])
    return web.json_response(prefs, headers=CORS_HEADERS)


@require_owner
async def handle_notif_prefs_set(request):
    """Replace notif prefs for the authenticated owner. Body shape:
       {"master": bool, "preset": str, "quiet": {enabled,from,to},
        "prefs": {"orders.new": bool, ...}}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected object"}, status=400, headers=CORS_HEADERS)

    owner_id = request["owner_id"]
    await db.set_owner_prefs(owner_id, body)

    # If user turned OFF quiet mode — delete the quiet message if it exists
    new_quiet = (body.get("quiet") or {}).get("enabled", False)
    if not new_quiet and OWNER_BOT_TOKEN:
        try:
            msg_id = await db.get_quiet_msg_id(owner_id)
            if msg_id:
                await tg_delete(OWNER_BOT_TOKEN, owner_id, msg_id)
                await db.set_quiet_msg_id(owner_id, None)
        except Exception as e:
            log.error(f"[owner-prefs] quiet msg delete failed for {owner_id}: {e}")

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_owner
async def handle_notifications(request):
    """Return recent notification events for the authenticated owner."""
    owner_id = request["owner_id"]
    since = request.query.get("since", "").strip()
    try:
        limit = min(int(request.query.get("limit", "30")), 100)
    except (ValueError, TypeError):
        limit = 30
    if since:
        items = await db.get_notifications_since(since, owner_id=owner_id, limit=limit)
    else:
        items = await db.get_recent_notifications(owner_id=owner_id, limit=limit)
    return web.json_response(
        {"items": items, "count": len(items),
         "server_time": datetime.now(timezone.utc).isoformat()},
        headers=CORS_HEADERS,
    )


@require_owner
async def handle_notif_test(request):
    """Send a single test message to the requesting owner via @ambar_manage_bot."""
    if not OWNER_BOT_TOKEN:
        return web.json_response({"error": "owner bot not configured"}, status=503, headers=CORS_HEADERS)
    try:
        await tg_send(
            OWNER_BOT_TOKEN, request["owner_id"],
            "🔔 *Тест уведомлений*\n\nЕсли вы видите это сообщение — связь с @ambar\\_manage\\_bot работает.",
            parse_mode="Markdown",
        )
        return web.json_response({"ok": True}, headers=CORS_HEADERS)
    except Exception as e:
        log.error(f"[owner-notif] test send failed: {e}")
        return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)


@require_owner
async def handle_office(request):
    """GET /api/owner/office?id=<office_id>&period=&day_offset= — детальный
    разрез одного района: выручка и динамика, каналы, оплата, скорость,
    рейтинг, водители, топ позиций и последние заказы."""
    oid = request.query.get("id", "").strip()
    if oid not in OFFICE_NAMES:
        return web.json_response({"error": "unknown office"}, status=404, headers=CORS_HEADERS)
    period = request.query.get("period", "today")
    if period not in VALID_PERIODS:
        period = "today"
    try:
        day_offset = max(0, min(365, int(request.query.get("day_offset", "0") or 0)))
    except ValueError:
        day_offset = 0

    all_orders = await db.get_all_orders()
    ref = (_now_dubai() - timedelta(days=day_offset)) if day_offset else None
    start, end, prev_start, prev_end = _period_window(period, ref)

    mine = lambda pairs: [(dt, o) for dt, o in pairs if (o.get("office_id") or "") == oid]
    curr      = mine(_orders_in_window(all_orders, start, end))          # доставленные
    prev      = mine(_orders_in_window(all_orders, prev_start, prev_end))
    curr_all  = mine(_all_orders_in_window(all_orders, start, end))      # любой статус

    rev = _sum_field(curr, "total")
    rev_prev = _sum_field(prev, "total")
    pct = _delta_pct(rev, rev_prev)

    delivered = len(curr)
    cancelled = sum(1 for _, o in curr_all if o.get("status") in ("declined", "cancelled"))
    live_pending = len(_open_at(all_orders, end, ("pending",), office_id=oid))
    live_route   = len(_open_at(all_orders, end, ("approved",), office_id=oid))

    # каналы и способ оплаты
    _phone = [(dt, o) for dt, o in curr if o.get("source") == "manual"]
    _crypto = [(dt, o) for dt, o in curr if o.get("payment_method") == "crypto" and o.get("paid")]
    _crypto_aed = _sum_field(_crypto, "total")
    _phone_aed = _sum_field(_phone, "total")

    # рейтинг
    revs = [int(o["review_score"]) for _, o in curr if o.get("review_score")]
    dist = {i: 0 for i in range(1, 6)}
    for r in revs:
        if 1 <= r <= 5:
            dist[r] += 1

    # топ позиций
    items_agg = {}
    for _, o in curr:
        for it in (o.get("items") or []):
            nm = str(it.get("name", "")).strip() or "—"
            e = items_agg.setdefault(nm, {"name": nm, "qty": 0, "aed": 0})
            e["qty"] += int(it.get("qty", 1) or 1)
            e["aed"] += int(it.get("line_total") or (it.get("price", 0) or 0) * (it.get("qty", 1) or 1))
    top_items = sorted(items_agg.values(), key=lambda x: -x["aed"])[:8]

    # водители (по ручным заказам района)
    drv_agg = {}
    for _, o in curr:
        nm = (o.get("driver") or "").strip()
        if not nm:
            continue
        e = drv_agg.setdefault(nm, {"name": nm, "orders": 0, "aed": 0})
        e["orders"] += 1
        e["aed"] += int(o.get("total", 0) or 0)
    drivers = sorted(drv_agg.values(), key=lambda x: -x["aed"])

    ds = _delivery_stats(curr)
    recent = sorted([o for _, o in curr_all], key=lambda x: x.get("timestamp", ""), reverse=True)[:15]

    return web.json_response({
        "id": oid, "name": OFFICE_NAMES[oid], "period": period,
        "window": {"date": start.strftime("%Y-%m-%d"),
                   "today": _biz_day_start(_now_dubai()).strftime("%Y-%m-%d"),
                   "day_offset": day_offset},
        "revenue": {"current": rev, "previous": rev_prev,
                    "delta_pct": pct, "delta_label": _delta_label(pct)},
        "orders": {"total": len(curr_all), "delivered": delivered,
                   "cancelled": cancelled, "pending": live_pending, "in_route": live_route},
        "avg_check": (rev // delivered) if delivered else 0,
        "by_channel": {"app":   {"aed": rev - _phone_aed, "count": delivered - len(_phone)},
                       "phone": {"aed": _phone_aed, "count": len(_phone)}},
        "by_method":  {"crypto": {"aed": _crypto_aed, "count": len(_crypto)},
                       "cash":   {"aed": rev - _crypto_aed, "count": delivered - len(_crypto)}},
        "delivery": {"avg_min": ds["avg_min"], "sample": ds["sample"],
                     "late_count": ds["late_count"], "late_pct": ds["late_pct"],
                     "threshold": LATE_THRESHOLD_FALLBACK},
        "rating": {"avg": round(sum(revs) / len(revs), 2) if revs else 0,
                   "count": len(revs), "dist": dist},
        "trend": _bucket_trend(curr, start, period),
        "top_items": top_items,
        "drivers": drivers,
        "recent": [_order_summary(o) for o in recent],
    }, headers=CORS_HEADERS)


# ── Support conversations (owner app "переписка" window) ────────────────────
def _conv_parts(conv_key: str):
    """conv_key is '{uid}' or '{uid}_{order_id}' (order_id may be 'general')."""
    head, _, tail = (conv_key or "").partition("_")
    uid = int(head) if head.isdigit() else 0
    order_id = "" if tail in ("", "general") else tail
    return uid, order_id


async def _conv_client(uid: int) -> dict:
    u = (await db.get_user(uid)) or {} if uid else {}
    return {
        "id": uid,
        "name": u.get("first_name") or u.get("name") or (str(uid) if uid else "—"),
        "username": u.get("username") or "",
    }


def _msg_preview(m: dict) -> str:
    return (m.get("text") or m.get("caption")
            or ("📷 фото" if m.get("type") == "photo" else ""))[:80]


@require_owner
async def handle_support_threads(request):
    """GET /api/owner/support-threads — recent conversations for the list window."""
    docs = await db.get_recent_support_convs(50)
    out = []
    for d in docs:
        key = d.get("conv_key", "")
        msgs = d.get("messages") or []
        if not key or not msgs:
            continue
        uid, order_id = _conv_parts(key)
        client = await _conv_client(uid)
        last = msgs[-1]
        out.append({
            "key": key,
            "order_id": order_id,
            "name": client["name"],
            "username": client["username"],
            "count": len(msgs),
            "last_ts": last.get("ts", ""),
            "last_role": last.get("role", ""),
            "last_text": _msg_preview(last),
        })
    return web.json_response({"threads": out}, headers=CORS_HEADERS)


@require_owner
async def handle_support_thread(request):
    """GET /api/owner/support-thread?key=<conv_key> | ?order=<AMB…> —
    the FULL conversation (client + operator messages) for the owner app."""
    key = request.query.get("key", "").strip()
    order = request.query.get("order", "").strip().lstrip("#")
    if not key and order:
        docs = await db.get_recent_support_convs(500)
        for d in docs:
            if d.get("conv_key", "").endswith("_" + order):
                key = d["conv_key"]
                break
    if not key:
        return web.json_response({"error": "thread not found"}, status=404, headers=CORS_HEADERS)
    msgs = await db.get_support_conv(key)
    uid, order_id = _conv_parts(key)
    client = await _conv_client(uid)
    return web.json_response({
        "key": key,
        "order_id": order_id,
        "client": client,
        "messages": msgs,
    }, headers=CORS_HEADERS)


# Public helper used by api_server (and operator_bot in future) to push an
# event to all owners subscribed to it. Best-effort; logs failures.
async def notify_owners(event_key: str, text: str, parse_mode: str = "Markdown",
                        meta: dict | None = None) -> list:
    """Send notification to subscribed owners/managers. Returns list of
    {"chat_id": int, "message_id": int} for each successfully sent message
    so callers can delete them later if needed. `meta` is persisted with the
    notification for the owner app (e.g. support conv_key routing)."""
    try:
        await db.insert_notification(event_key, text, meta=meta)
    except Exception as e:
        log.error(f"[owner-notif] persist failed for {event_key}: {e}")

    sent = []
    if not OWNER_BOT_TOKEN:
        return sent
    try:
        owner_ids = await db.get_owners_subscribed_to(event_key)
    except Exception as e:
        log.error(f"[owner-notif] subscriber lookup failed for {event_key}: {e}")
        return sent
    log.info(f"[owner-notif] {event_key} → {owner_ids}")
    for oid in owner_ids:
        try:
            result = await _send_md(OWNER_BOT_TOKEN, oid, text, parse_mode=parse_mode)
            if result and result.get("ok"):
                sent.append({"chat_id": oid, "message_id": result["result"]["message_id"]})
            else:
                log.error(f"[owner-notif] send {event_key} → {oid} TG error: {result}")
        except Exception as e:
            log.error(f"[owner-notif] send {event_key} → {oid} failed: {e}")
    return sent


async def notify_owners_force(event_key: str, text: str, parse_mode: str = "Markdown") -> None:
    """Send to EVERY owner/manager, bypassing prefs + quiet hours — for critical
    alerts (crypto-paid orders, order edits) that must always be seen."""
    try:
        await db.insert_notification(event_key, text)
    except Exception as e:
        log.error(f"[owner-notif] persist {event_key} failed: {e}")
    if not OWNER_BOT_TOKEN:
        return
    try:
        ids = await db.get_all_manager_ids()
    except Exception as e:
        log.error(f"[owner-notif] force lookup failed: {e}")
        return
    log.info(f"[owner-notif] FORCE {event_key} → {len(ids)} owners: {ids}")
    for oid in ids:
        try:
            await _send_md(OWNER_BOT_TOKEN, oid, text, parse_mode=parse_mode)
        except Exception as e:
            log.error(f"[owner-notif] force {event_key} → {oid} failed: {e}")


def _md(s) -> str:
    """Escape Telegram legacy-Markdown specials in USER-TYPED text.

    An unmatched _ * ` or [ in a customer's name, address, comment or a product
    name makes Telegram reject the WHOLE message ("can't parse entities") — the
    notification then vanishes silently. Every interpolated user value must go
    through this."""
    out = str(s if s is not None else "")
    for ch in ("_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


async def _send_md(token, chat_id, text, parse_mode="Markdown"):
    """Send, and if Telegram refuses to parse the entities, resend as PLAIN text.

    Belt-and-braces on top of _md(): a formatting slip must never cost the owner
    an entire notification. Returns the final Telegram response."""
    r = await tg_send(token, chat_id, text, parse_mode=parse_mode)
    if r and not r.get("ok") and "parse" in str(r.get("description", "")).lower():
        log.error(f"[owner-notif] parse error → retrying plain: {r.get('description')}")
        plain = text.replace("*", "").replace("`", "").replace("\\", "")
        r = await tg_send(token, chat_id, plain, parse_mode=None)
    return r


async def notify_new_order(oid, total, user_name, phone, address, office,
                           uid, founder_id, premium_ids, worldwide_ids,
                           items=None, prepaid=None, held=False, manual=None):
    """Send exactly one new-order message per user at their highest matching tier
    (orders.new1000 → orders.new500 → orders.new). VIP notification is independent.
    Crypto-paid orders (prepaid set) ALWAYS reach every owner, bypassing the tier
    filters AND quiet hours. `manual={"operator": name}` marks a phone-in order
    punched in on the operator POS — same tier pipeline, unmistakable 📞 header."""
    # every value below is user-typed → must be escaped or Telegram drops the message
    items_txt = "\n".join(
        f"• {_md(it.get('name',''))} ×{it.get('qty',1)}" for it in (items or [])
    ) or "—"
    _client = _md(user_name or "—") + (f" ({_md(phone)})" if phone and phone != "—" else "")
    base = (f"Сумма: *{total} AED*\n"
            f"Клиент: {_client}\n"
            f"Адрес: {_md(address or '—')}\n"
            f"Офис: {_md(office)}\n"
            f"🛒 Позиции:\n{items_txt}")
    if prepaid:
        base += (f"\n\n✅💎 *ОПЛАЧЕНО КРИПТОЙ*\n"
                 f"{prepaid.get('amount_usdt')} USDT · TRC-20 — наличные НЕ брать")
    if held:
        base = ("⏳ *ОЖИДАЕТ ВЕРИФИКАЦИИ* — клиент ещё не подтверждён.\n"
                "Оператор получит заказ после заполнения формы.\n\n") + base
    if manual:
        head = (f"📞 *РУЧНОЙ ЗАКАЗ — по телефону*\n"
                f"Принял: {_md(manual.get('operator','—'))}\n")
        if manual.get("district"):
            head += (f"📍 Район: *{_md(manual['district'])}*"
                     f" · оператор {_md(manual.get('dispatch_operator','—'))}\n")
        if manual.get("driver"):
            head += f"🚗 Водитель: *{_md(manual['driver'])}*\n"
        base = head + "\n" + base

    tiers = []
    if total >= 1000:
        tiers.append(("orders.new1000", f"💎 *Очень крупный заказ #{oid}!*\n\n{base}"))
        tiers.append(("orders.new500",  f"💰 *Крупный заказ #{oid}*\n\n{base}"))
    elif total >= 500:
        tiers.append(("orders.new500",  f"💰 *Крупный заказ #{oid}*\n\n{base}"))
    tiers.append(("orders.new", f"🆕 *Новый заказ #{oid}*\n{base}"))

    # Persist all matching events for the dashboard alerts.
    for event_key, text in tiers:
        try:
            await db.insert_notification(event_key, text,
                                         meta={"manual": True} if manual else None)
        except Exception:
            pass

    if not OWNER_BOT_TOKEN:
        log.warning("[owner-notif] OWNER_BOT_TOKEN empty — skipping new-order notify")
        return

    if prepaid or manual:
        # CRYPTO (money already on the wallet) and MANUAL phone-in orders reach EVERY
        # owner/manager, ignoring tier filters + quiet hours — a manual order once
        # slipped past tier prefs unseen; the owner wants 100% visibility on them.
        head = ("💎 Очень крупный заказ" if total >= 1000
                else ("💰 Крупный заказ" if total >= 500 else "🆕 Новый заказ"))
        tag = " · 💳 КРИПТА" if prepaid else ""
        force_text = f"*{head} #{oid}*{tag}\n\n{base}"
        try:
            recipients = set(await db.get_all_manager_ids())
        except Exception as e:
            log.error(f"[owner-notif] all-manager lookup failed: {e}")
            recipients = set()
        if founder_id:
            recipients.add(founder_id)
        kind = "CRYPTO" if prepaid else "MANUAL"
        log.info(f"[owner-notif] {kind} order #{oid} → force {len(recipients)} owners: {recipients}")
        for uid_sub in recipients:
            try:
                r = await _send_md(OWNER_BOT_TOKEN, uid_sub, force_text)
                if not r or not r.get("ok"):
                    log.error(f"[owner-notif] {kind} new-order → {uid_sub} REJECTED: {r}")
            except Exception as e:
                log.error(f"[owner-notif] {kind} new-order → {uid_sub} failed: {e}")
    else:
        # Normal: one message per user at their highest subscribed tier.
        all_subs = {}
        for event_key, _ in tiers:
            try:
                ids = await db.get_owners_subscribed_to(event_key)
            except Exception as e:
                log.error(f"[owner-notif] subscriber lookup {event_key} failed: {e}")
                ids = []
            for uid_sub in ids:
                if uid_sub not in all_subs:
                    all_subs[uid_sub] = event_key
        log.info(f"[owner-notif] new-order #{oid} total={total} → {len(all_subs)} recipients: {all_subs}")
        tier_text = {ek: txt for ek, txt in tiers}
        for uid_sub, event_key in all_subs.items():
            try:
                r = await _send_md(OWNER_BOT_TOKEN, uid_sub, tier_text[event_key])
                if not r or not r.get("ok"):
                    log.error(f"[owner-notif] new-order {event_key} → {uid_sub} REJECTED: {r}")
            except Exception as e:
                log.error(f"[owner-notif] new-order {event_key} → {uid_sub} failed: {e}")

    # VIP is independent — not an order-tier, never duplicates with the above.
    if uid == founder_id or uid in premium_ids or uid in worldwide_ids:
        tier = "FOUNDER" if uid == founder_id else ("ÉLITE" if uid in premium_ids else "PREMIUM")
        await notify_owners("customers.vip",
            f"💎 *VIP-клиент сделал заказ*\n"
            f"Карта: {tier}\n{base}")


@require_owner
async def handle_customer_ban(request):
    """Ban or unban a customer. POST body: {"reason": "..."} for ban (optional)."""
    raw = request.match_info["telegram_id"]
    try:
        tg_id = int(raw)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)

    user = await db.get_user(tg_id)
    if not user:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    action = request.match_info.get("action", "ban")
    owner_id = request["owner_id"]

    if action == "ban":
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = (body.get("reason") or "").strip() or "Заблокирован владельцем"
        await db.ban_user(tg_id, reason=reason, by=owner_id)
        # Notify the user via the customer bot. Best-effort — don't fail if Telegram is down.
        if BOT_TOKEN:
            try:
                await tg_send(
                    BOT_TOKEN, tg_id,
                    "🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку для разъяснений.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return web.json_response({"ok": True, "banned": True, "reason": reason}, headers=CORS_HEADERS)

    if action == "unban":
        await db.unban_user(tg_id)
        if BOT_TOKEN:
            try:
                await tg_send(
                    BOT_TOKEN, tg_id,
                    "✅ *Ваш аккаунт разблокирован.*\n\nДобро пожаловать обратно!",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return web.json_response({"ok": True, "banned": False}, headers=CORS_HEADERS)

    return web.json_response({"error": "unknown action"}, status=400, headers=CORS_HEADERS)


@require_owner
async def handle_customer_debt(request):
    """Debt programme admin. POST body, any combination of:
      {"debt": 250}          — set the balance to an absolute value (after a cash
                               repayment, correction, etc.). Logged to debt_history.
      {"debt_allowed": true} — enable/disable the В ДОЛГ payment option.
    Returns the updated customer row."""
    raw = request.match_info["telegram_id"]
    try:
        tg_id = int(raw)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)

    user = await db.get_user(tg_id)
    if not user:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    try:
        body = await request.json()
    except Exception:
        body = {}
    owner_id = request["owner_id"]
    changed = {}

    if "debt_allowed" in body:
        allowed = bool(body.get("debt_allowed"))
        await db.set_debt_allowed(tg_id, allowed, by=owner_id)
        changed["debt_allowed"] = allowed
        log.info(f"[debt] owner {owner_id} set debt_allowed={allowed} for {tg_id}")

    if "debt" in body:
        try:
            amount = float(body.get("debt"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid debt amount"}, status=400, headers=CORS_HEADERS)
        if amount < 0 or amount > 1_000_000:
            return web.json_response({"error": "debt out of range"}, status=400, headers=CORS_HEADERS)
        note = (body.get("note") or "").strip() or "manual edit"
        res = await db.set_debt(tg_id, amount, by=owner_id, note=note)
        changed["debt"] = res
        log.info(f"[debt] owner {owner_id} set debt {res.get('old')}→{res.get('new')} AED for {tg_id}")

    if not changed:
        return web.json_response({"error": "nothing to change"}, status=400, headers=CORS_HEADERS)

    fresh = await db.get_user(tg_id)
    return web.json_response(
        {"ok": True, "changed": changed, "customer": _serialize_user(fresh or {})},
        headers=CORS_HEADERS,
        dumps=lambda o: __import__("json").dumps(o, default=_json_default),
    )


# ─── Catalog (stock toggle + price edit) ────────────────────────────────
# catalog.json holds the canonical product list, including `stock` (bool) and
# `price` (int). It's served statically to the customer mini-app, so any
# update here is visible to customers on their next page load.

CATALOG_FILE = Path(__file__).parent / "catalog.json"
_catalog_lock = asyncio.Lock()


def _read_catalog() -> list:
    try:
        return json.loads(CATALOG_FILE.read_text())
    except Exception as e:
        log.error(f"[catalog] read failed: {e}")
        return []


def _write_catalog(catalog: list) -> None:
    CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))


async def _aggregate_sales(period: str = "month") -> dict:
    """Return {product_id: {sold, rev}} aggregated over the given period."""
    start, end, _, _ = _period_window(period)
    orders = await db.get_orders_in_range(
        start.astimezone(timezone.utc).isoformat().replace("+00:00", ""),
        end.astimezone(timezone.utc).isoformat().replace("+00:00", ""),
    )
    agg = {}
    for o in orders:
        if o.get("status") not in REVENUE_STATUSES:
            continue
        for it in (o.get("items") or []):
            pid = it.get("id")
            if not pid:
                continue
            # Aggregate all custom items under one "СВОБОДНАЯ ПОЗИЦИЯ" row
            if it.get("is_custom") or str(pid).startswith("custom_"):
                pid = "_custom"
            row = agg.setdefault(pid, {"sold": 0, "rev": 0})
            qty = int(it.get("qty") or 0)
            line = it.get("line_total") or (it.get("price", 0) * qty)
            row["sold"] += qty
            row["rev"] += int(line or 0)
    return agg


async def _sales_by_day(days: int = 7) -> dict:
    """{product_id: [шт за день, ...]} за последние `days` рабочих суток.

    Индекс `days-1` — текущая смена, 0 — самая старая. Дни считаются от полудня
    (_biz_date), как и всё остальное, иначе ночные продажи уезжали бы в
    следующий столбик. Раньше этот график в карточке товара рисовался из
    среднего со сдвигом — то есть был выдуман целиком."""
    today = _biz_day_start(_now_dubai())
    start = today - timedelta(days=days - 1)
    end   = today + timedelta(days=1)
    orders = await db.get_orders_in_range(
        start.astimezone(timezone.utc).isoformat().replace("+00:00", ""),
        end.astimezone(timezone.utc).isoformat().replace("+00:00", ""),
    )
    out = {}
    for o in orders:
        if o.get("status") not in REVENUE_STATUSES:
            continue
        dt = _parse_ts(o.get("timestamp"))
        if dt is None:
            continue
        idx = (_biz_date(dt) - start.date()).days
        if not (0 <= idx < days):
            continue
        for it in (o.get("items") or []):
            pid = it.get("id")
            if not pid:
                continue
            if it.get("is_custom") or str(pid).startswith("custom_"):
                pid = "_custom"
            row = out.setdefault(pid, [0] * days)
            row[idx] += int(it.get("qty") or 0)
    return out


@require_owner
async def handle_catalog_list(request):
    """Return full catalog joined with sold/rev aggregated from delivered orders.

    Query: period = today | week | month | year (default: month)
    """
    period = request.query.get("period", "month")
    if period not in VALID_PERIODS:
        period = "month"

    catalog = await asyncio.to_thread(_read_catalog)
    sales = await _aggregate_sales(period)
    # Реальные продажи по дням и за неделю/месяц — карточка товара рисовала их
    # из среднего, теперь берём из заказов.
    trend  = await _sales_by_day(7)
    week   = await _aggregate_sales("week")
    month  = await _aggregate_sales("month")

    items = []
    for p in catalog:
        pid = p.get("id")
        s = sales.get(pid, {"sold": 0, "rev": 0})
        items.append({
            "id":     pid,
            "cat":    p.get("cat") or "—",
            "name":   p.get("name") or "",
            "price":  int(p.get("price") or 0),
            # Цен две: онлайновая (со скидкой 5% за заказ через приложение) и
            # полная — по ней идут телефонные заказы. Отдаём обе, иначе в
            # карточке товара видна только половина правды.
            "price_full": int(p.get("price_full") or p.get("price") or 0),
            "stock":  bool(p.get("stock", True)),
            "img":    p.get("img") or "",
            "desc":   p.get("desc") or "",
            "sold":   s["sold"],
            "rev":    s["rev"],
            "trend7":     trend.get(pid) or [0] * 7,
            "sold_week":  (week.get(pid)  or {}).get("sold", 0),
            "sold_month": (month.get(pid) or {}).get("sold", 0),
        })
    # Append aggregated custom items row if any were sold
    custom_s = sales.get("_custom")
    if custom_s and custom_s["sold"] > 0:
        items.append({
            "id":    "_custom",
            "cat":   "Другое",
            "name":  "СВОБОДНАЯ ПОЗИЦИЯ",
            "price": 0,
            "stock": True,
            "img":   "",
            "desc":  "Товары вне каталога, добавленные оператором",
            "sold":  custom_s["sold"],
            "rev":   custom_s["rev"],
            "trend7":     trend.get("_custom") or [0] * 7,
            "sold_week":  (week.get("_custom")  or {}).get("sold", 0),
            "sold_month": (month.get("_custom") or {}).get("sold", 0),
        })
    return web.json_response({"period": period, "items": items}, headers=CORS_HEADERS)


@require_owner
async def handle_catalog_update(request):
    """Update stock (bool) and/or price (int) for a single product.

    Body: {"stock": bool?, "price": int?}
    Returns the updated product row.
    """
    pid = request.match_info["product_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected object"}, status=400, headers=CORS_HEADERS)

    new_stock = body.get("stock", None)
    new_price = body.get("price", None)
    if new_stock is None and new_price is None:
        return web.json_response({"error": "no fields to update"}, status=400, headers=CORS_HEADERS)
    if new_stock is not None and not isinstance(new_stock, bool):
        return web.json_response({"error": "stock must be bool"}, status=400, headers=CORS_HEADERS)
    if new_price is not None:
        try:
            new_price = int(new_price)
        except (ValueError, TypeError):
            return web.json_response({"error": "price must be integer"}, status=400, headers=CORS_HEADERS)
        if new_price < 0 or new_price > 100000:
            return web.json_response({"error": "price out of range"}, status=400, headers=CORS_HEADERS)

    async with _catalog_lock:
        catalog = await asyncio.to_thread(_read_catalog)
        target = next((p for p in catalog if p.get("id") == pid), None)
        if target is None:
            return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
        if new_stock is not None:
            target["stock"] = new_stock
        if new_price is not None:
            target["price"] = new_price
        await asyncio.to_thread(_write_catalog, catalog)

    log.info(f"[catalog] {request['owner_id']} updated {pid}: stock={new_stock} price={new_price}")
    if new_stock is False:
        try:
            await notify_owners("stock.out",
                f"⚠️ *Товар закончился*\n{target.get('name', pid)}")
        except Exception as e:
            log.error(f"[owner-notif] stock.out failed: {e}")
    return web.json_response({
        "ok": True,
        "id": pid,
        "stock": bool(target.get("stock", True)),
        "price": int(target.get("price") or 0),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_managers_list(request):
    """Return owners (env, read-only) + managers (env + DB, mutable) with metadata.
    Owner-only (plus LEGACY_MGR_UI_ACCESS) — managers can't see this list."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    db_managers = await db.get_managers()
    db_ids = {int(m["telegram_id"]) for m in db_managers}
    # access_log "blocked" rows let us mark env-managers as blocked too,
    # since they don't have an owner_managers row to flip.
    blocked_in_log = await db.get_blocked_access_ids()

    owners = [
        {"telegram_id": int(oid), "name": "", "username": "", "source": "owner", "blocked": False}
        for oid in sorted(OWNER_IDS)
    ]
    managers = []
    # env managers come first. They CAN now be blocked — the block lives in
    # access_log instead of owner_managers since these IDs are sourced from
    # the .env file at startup. Skip IDs that are also owners or already
    # have a DB row, so each user only appears once.
    for mid in sorted(MANAGER_IDS):
        if int(mid) in db_ids or int(mid) in OWNER_IDS:
            continue
        managers.append({
            "telegram_id": int(mid),
            "name": "", "username": "",
            "source": "env",
            "blocked": int(mid) in blocked_in_log,
        })
    # DB managers (mutable) — surface the blocked flag so the UI can split
    # them into "active" vs "blocked" sections.
    for m in db_managers:
        managers.append({
            "telegram_id": int(m["telegram_id"]),
            "name":     m.get("name", ""),
            "username": m.get("username", ""),
            "added_at": m.get("added_at", ""),
            "added_by": m.get("added_by", 0),
            "blocked":  bool(m.get("blocked", False)) or int(m["telegram_id"]) in blocked_in_log,
            "blocked_at": m.get("blocked_at", ""),
            "source":   "db",
        })

    return web.json_response({
        "owners":           owners,
        "managers":         managers,
        "current_user":     request["owner_id"],
        # Capability flag for the UI — true for OWNER_IDS and the legacy
        # access set (currently 686932322). Lets the frontend show block
        # buttons even when the user isn't strictly in OWNER_IDS.
        "can_manage_users": _can_manage_users(request["owner_id"]),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_manager_add(request):
    """Add a DB-stored manager. Owner-only (no manager-promotes-manager).
    Body: {"telegram_id": int, "name": str?, "username": str?}."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)
    try:
        tg_id = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "telegram_id must be a number"}, status=400, headers=CORS_HEADERS)
    if tg_id <= 0:
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)
    if tg_id in OWNER_IDS:
        return web.json_response({"error": "already an owner"}, status=409, headers=CORS_HEADERS)

    doc = await db.add_manager(
        telegram_id = tg_id,
        name        = (body.get("name") or "").strip(),
        username    = (body.get("username") or "").strip(),
        added_by    = request["owner_id"],
    )
    log.info(f"[owner] {request['owner_id']} added manager {tg_id} ({doc.get('name','')})")
    return web.json_response({"ok": True, "manager": doc}, headers=CORS_HEADERS)


def _ru_bool(v) -> str:
    return "да" if v else "нет"


def _md_escape(s: str) -> str:
    """Escape Markdown v1 special chars so usernames/names with _ * ` [ don't break parsing."""
    return (s or "").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _format_unauthorized_alert(user: dict, meta: dict, log_doc: dict) -> str:
    """Russian Telegram alert for owners. Includes everything we can scrape
    out of Telegram's initData user object + the request that hit us."""
    fn = (user.get("first_name") or "").strip()
    ln = (user.get("last_name") or "").strip()
    full_name = (fn + " " + ln).strip() or "—"
    uname = user.get("username") or ""
    uname_link = f"@{_md_escape(uname)}" if uname else "—"
    tg_id = user.get("id") or "—"
    lang = user.get("language_code") or "—"
    is_premium = user.get("is_premium")
    allows_pm = user.get("allows_write_to_pm")
    photo_url = user.get("photo_url") or ""
    attempts = log_doc.get("attempts") or 1
    first_at = log_doc.get("first_attempt_at") or ""
    # Render first_attempt time in Dubai TZ (UTC+4)
    first_at_lbl = "—"
    try:
        if first_at:
            dt = datetime.fromisoformat(first_at).astimezone(DUBAI_TZ)
            first_at_lbl = dt.strftime("%d.%m.%Y · %H:%M")
    except Exception:
        first_at_lbl = first_at or "—"

    lines = [
        "🚨 *ПОПЫТКА НЕСАНКЦИОНИРОВАННОГО ДОСТУПА*",
        "",
        "Кто-то открыл owner-бота, но его нет в списке доступа.",
        "",
        "👤 *Пользователь*",
        f"• Имя: *{_md_escape(full_name)}*",
        f"• Username: {uname_link}",
        f"• Telegram ID: `{tg_id}`",
        f"• Язык: `{lang}`",
        f"• Premium: {_ru_bool(is_premium)}",
        f"• Разрешил писать в ЛС: {_ru_bool(allows_pm)}",
    ]
    if photo_url:
        lines.append(f"• Фото: [открыть]({photo_url})")
    lines += [
        "",
        "🌐 *Запрос*",
        f"• Endpoint: `{_md_escape(meta.get('path',''))}`",
        f"• Метод: `{meta.get('method','')}`",
        f"• IP: `{_md_escape(meta.get('ip','—') or '—')}`",
        f"• User-Agent: `{_md_escape((meta.get('user_agent','') or '—')[:120])}`",
        "",
        "📊 *История*",
        f"• Попыток всего: *{attempts}*",
        f"• Первая попытка: {first_at_lbl}",
        "",
        "_Открой панель → Доступ для менеджеров → Запросы на доступ, чтобы решить судьбу._",
    ]
    return "\n".join(lines)


async def _alert_owners_unauthorized(user: dict, meta: dict, log_doc: dict) -> None:
    """Push a security alert to every OWNER_IDS via @ambar_manage_bot.
    Called from owner_auth.require_owner — best-effort, swallows errors."""
    if not OWNER_BOT_TOKEN:
        log.warning("[owner-auth] OWNER_BOT_TOKEN not set — can't send security alert")
        return
    text = _format_unauthorized_alert(user, meta, log_doc)
    for oid in OWNER_IDS:
        try:
            await _send_md(OWNER_BOT_TOKEN, oid, text)
        except Exception as e:
            log.error(f"[owner-auth] alert send to {oid} failed: {e}")


# Wire the alerter into the auth layer at import time.
install_alerter(_alert_owners_unauthorized)


@require_owner
async def handle_manager_block(request):
    """Block or unblock any manager. Owner-only.
    URL: POST /api/owner/managers/{tg_id}/{action:block|unblock}.

    For DB-managed users the block is stored as `blocked` on their
    owner_managers row. For env-managers (sourced from .env) we can't flip
    a row that doesn't exist, so we upsert a `blocked` entry into access_log
    instead — `require_owner` already checks `is_access_blocked()` before
    the env allow-list, so the next request from that user is denied.

    Owners are never blockable through this endpoint."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
    try:
        tg_id = int(request.match_info["telegram_id"])
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)
    action = request.match_info.get("action", "")
    if action not in ("block", "unblock"):
        return web.json_response({"error": "invalid action"}, status=400, headers=CORS_HEADERS)
    if tg_id in OWNER_IDS:
        return web.json_response({"error": "cannot block an owner"}, status=409, headers=CORS_HEADERS)

    by = request["owner_id"]
    blocked_target = action == "block"
    is_env  = tg_id in MANAGER_IDS
    is_db   = await db.is_manager(tg_id) or await db.is_manager_blocked(tg_id)

    if blocked_target:
        # On block: flip DB row if it exists, AND mirror into access_log so
        # env-managers (no DB row) are also denied. Both checks run in
        # require_owner so either path is sufficient — we set both for
        # consistency in the UI.
        if is_db:
            await db.set_manager_blocked(tg_id, True, by=by)
        await db.upsert_access_block(tg_id, by=by)
    else:
        # On unblock: clear both DB row + access_log status.
        if is_db:
            await db.set_manager_blocked(tg_id, False, by=by)
        # If there's an access_log row, flip it back to 'pending' so it
        # doesn't block but stays in history. set_access_status is a no-op
        # for users who don't have a row, so calling it is safe.
        await db.set_access_status(tg_id, "pending", by=by)

    if not is_env and not is_db:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    log.info(f"[owner] {by} {action}ed user {tg_id} (env={is_env}, db={is_db})")
    return web.json_response({"ok": True, "telegram_id": tg_id, "blocked": blocked_target}, headers=CORS_HEADERS)


@require_owner
async def handle_access_log_list(request):
    """Return the access log. Optional ?status= filter (pending|blocked|approved).
    Owner-only — managers can't see who else tried to get in."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
    status = request.query.get("status", "").strip()
    rows = await db.get_access_log(status=status if status else "")
    # Don't leak photo_url, ip, ua to anyone except owners — but since this
    # is owner-only already, we pass them through as-is.
    return web.json_response({"items": rows, "count": len(rows)}, headers=CORS_HEADERS)


@require_owner
async def handle_access_log_action(request):
    """Block/unblock/approve a logged user. Owner-only.
    URL: POST /api/owner/access-log/{tg_id}/{action:block|unblock|approve}."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
    try:
        tg_id = int(request.match_info["telegram_id"])
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)
    action = request.match_info.get("action", "")
    status_map = {"block": "blocked", "unblock": "pending", "approve": "approved"}
    new_status = status_map.get(action)
    if not new_status:
        return web.json_response({"error": "invalid action"}, status=400, headers=CORS_HEADERS)
    if tg_id in OWNER_IDS:
        return web.json_response({"error": "cannot block an owner"}, status=409, headers=CORS_HEADERS)
    ok = await db.set_access_status(tg_id, new_status, by=request["owner_id"])
    if not ok:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    log.info(f"[owner] {request['owner_id']} set access {tg_id} → {new_status}")
    return web.json_response({"ok": True, "telegram_id": tg_id, "status": new_status}, headers=CORS_HEADERS)


@require_owner
async def handle_manager_remove(request):
    """Remove a DB-stored manager. Owner-only. Env-managers can't be removed
    here — they're sourced from AMBAR_MANAGER_IDS which is loaded at startup."""
    if not _can_manage_users(request["owner_id"]):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
    raw = request.match_info["telegram_id"]
    try:
        tg_id = int(raw)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid telegram_id"}, status=400, headers=CORS_HEADERS)
    if tg_id in MANAGER_IDS:
        return web.json_response(
            {"error": "env-managed", "hint": "remove from AMBAR_MANAGER_IDS env var and restart service"},
            status=409, headers=CORS_HEADERS,
        )
    removed = await db.remove_manager(tg_id)
    if not removed:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    log.info(f"[owner] {request['owner_id']} removed manager {tg_id}")
    return web.json_response({"ok": True, "removed": tg_id}, headers=CORS_HEADERS)


_backfill_done = False

async def _backfill_delivery_times():
    """One-time migration: set updated_at on old delivered orders that lack it.

    Uses confirmed_at + eta as the best estimate; falls back to
    timestamp + 35 min when neither exists.  Runs once per process."""
    global _backfill_done
    if _backfill_done:
        return
    _backfill_done = True
    all_orders = await db.get_all_orders()
    patched = 0
    for oid, o in all_orders.items():
        if o.get("status") != "delivered":
            continue
        if o.get("updated_at"):
            continue
        placed = o.get("timestamp")
        if not placed:
            continue
        confirmed = o.get("confirmed_at")
        eta = int(o.get("eta") or 0)
        if confirmed and eta:
            try:
                t = datetime.fromisoformat(confirmed)
                est = t + timedelta(minutes=eta)
            except (ValueError, TypeError):
                est = None
        else:
            est = None
        if est is None:
            try:
                t0 = datetime.fromisoformat(placed)
                est = t0 + timedelta(minutes=max(eta, 35))
            except (ValueError, TypeError):
                continue
        await db.update_order(oid, updated_at=est.isoformat())
        patched += 1
    if patched:
        log.info(f"[owner] backfilled updated_at on {patched} delivered orders")


async def _monitor_quiet_hours():
    """Background loop: send/delete quiet-mode messages based on actual time.
    Runs every 60s. When a user enters their quiet window → send message.
    When they leave it → delete the message."""
    while True:
        await asyncio.sleep(60)
        if not OWNER_BOT_TOKEN:
            continue
        try:
            _db = db._db_or_none()
            if _db is None:   # Motor DB objects raise on bool() — must compare to None
                continue
            now_h = datetime.now(timezone.utc).astimezone(DUBAI_TZ).hour
            cursor = _db.owner_prefs.find(
                {"quiet.enabled": True},
                {"_id": 0, "owner_id": 1, "quiet": 1, "_quiet_msg_id": 1})
            docs = await cursor.to_list(length=200)
            for doc in docs:
                oid = doc.get("owner_id")
                q = doc.get("quiet", {})
                msg_id = doc.get("_quiet_msg_id")
                try:
                    from_h = int(str(q.get("from", "22:00")).split(":")[0])
                    to_h = int(str(q.get("to", "08:00")).split(":")[0])
                except (ValueError, TypeError):
                    continue
                if from_h >= to_h:
                    in_quiet = (now_h >= from_h or now_h < to_h)
                else:
                    in_quiet = (from_h <= now_h < to_h)

                if in_quiet and not msg_id:
                    # Entering quiet hours — send message
                    try:
                        result = await tg_send(OWNER_BOT_TOKEN, oid,
                            f"🔇 *Тихий режим*\n"
                            f"Уведомления отключены до {q.get('to', '08:00')}",
                            parse_mode="Markdown")
                        if result and result.get("ok"):
                            await db.set_quiet_msg_id(oid, result["result"]["message_id"])
                    except Exception as e:
                        log.error(f"[quiet-monitor] send to {oid} failed: {e}")
                elif not in_quiet and msg_id:
                    # Leaving quiet hours — delete message
                    try:
                        await tg_delete(OWNER_BOT_TOKEN, oid, msg_id)
                        await db.set_quiet_msg_id(oid, None)
                    except Exception as e:
                        log.error(f"[quiet-monitor] delete for {oid} failed: {e}")
        except Exception as e:
            log.error(f"[quiet-monitor] check failed: {e}")


async def _monitor_pending_orders():
    """Background loop: fire timing.notAccepted5 for orders pending > 5 min."""
    _alerted = set()
    while True:
        await asyncio.sleep(60)
        try:
            all_orders = await db.get_all_orders()
            now = datetime.now(timezone.utc)
            for oid, o in all_orders.items():
                if o.get("status") != "pending" or oid in _alerted:
                    continue
                ts = o.get("timestamp")
                if not ts:
                    continue
                try:
                    placed = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                wait_min = (now - placed).total_seconds() / 60
                if wait_min >= 5:
                    _alerted.add(oid)
                    await notify_owners("timing.notAccepted5",
                        f"⏱ *Заказ не принят {int(wait_min)} мин*\n"
                        f"Заказ #{oid}\n"
                        f"Клиент: {o.get('customer_name','—')}\n"
                        f"Сумма: {o.get('total',0)} AED")
            _alerted -= {oid for oid in _alerted if all_orders.get(oid, {}).get("status") != "pending"}
        except Exception as e:
            log.error(f"[monitor] pending check failed: {e}")


def setup(app):
    """Wire owner routes into the aiohttp app. Called from api_server.main()."""
    app.on_startup.append(lambda _: _backfill_delivery_times())
    async def _start_monitors(_):
        asyncio.ensure_future(_monitor_pending_orders())
        asyncio.ensure_future(_monitor_quiet_hours())
    app.on_startup.append(_start_monitors)
    app.router.add_route("OPTIONS", "/api/owner/ping",    handle_ping)
    app.router.add_get(             "/api/owner/ping",    handle_ping)
    app.router.add_route("OPTIONS", "/api/owner/finance", handle_finance)
    app.router.add_get(             "/api/owner/finance", handle_finance)
    app.router.add_route("OPTIONS", "/api/owner/office",  handle_office)
    app.router.add_get(             "/api/owner/office",  handle_office)
    app.router.add_route("OPTIONS", "/api/owner/customers",              handle_customers)
    app.router.add_get(             "/api/owner/customers",              handle_customers)
    app.router.add_route("OPTIONS", "/api/owner/customers/{telegram_id}", handle_customer_detail)
    app.router.add_get(             "/api/owner/customers/{telegram_id}", handle_customer_detail)
    app.router.add_route("OPTIONS", "/api/owner/customers/{telegram_id}/{action:ban|unban}", handle_customer_ban)
    app.router.add_post(            "/api/owner/customers/{telegram_id}/{action:ban|unban}", handle_customer_ban)
    app.router.add_route("OPTIONS", "/api/owner/customers/{telegram_id}/debt", handle_customer_debt)
    app.router.add_post(            "/api/owner/customers/{telegram_id}/debt", handle_customer_debt)
    app.router.add_route("OPTIONS", "/api/owner/notifications", handle_notifications)
    app.router.add_get(             "/api/owner/notifications", handle_notifications)
    app.router.add_route("OPTIONS", "/api/owner/support-threads", handle_support_threads)
    app.router.add_get(             "/api/owner/support-threads", handle_support_threads)
    app.router.add_route("OPTIONS", "/api/owner/support-thread",  handle_support_thread)
    app.router.add_get(             "/api/owner/support-thread",  handle_support_thread)
    app.router.add_route("OPTIONS", "/api/owner/notif-prefs", handle_notif_prefs_get)
    app.router.add_get(             "/api/owner/notif-prefs", handle_notif_prefs_get)
    app.router.add_post(            "/api/owner/notif-prefs", handle_notif_prefs_set)
    app.router.add_route("OPTIONS", "/api/owner/notif-test",  handle_notif_test)
    app.router.add_post(            "/api/owner/notif-test",  handle_notif_test)
    app.router.add_route("OPTIONS", "/api/owner/catalog",                handle_catalog_list)
    app.router.add_get(             "/api/owner/catalog",                handle_catalog_list)
    app.router.add_route("OPTIONS", "/api/owner/catalog/{product_id}",   handle_catalog_update)
    app.router.add_post(            "/api/owner/catalog/{product_id}",   handle_catalog_update)
    app.router.add_route("OPTIONS", "/api/owner/managers",                handle_managers_list)
    app.router.add_get(             "/api/owner/managers",                handle_managers_list)
    app.router.add_post(            "/api/owner/managers",                handle_manager_add)
    app.router.add_route("OPTIONS", "/api/owner/managers/{telegram_id}",  handle_manager_remove)
    app.router.add_delete(          "/api/owner/managers/{telegram_id}",  handle_manager_remove)
    app.router.add_route("OPTIONS", "/api/owner/managers/{telegram_id}/{action:block|unblock}", handle_manager_block)
    app.router.add_post(            "/api/owner/managers/{telegram_id}/{action:block|unblock}", handle_manager_block)
    app.router.add_route("OPTIONS", "/api/owner/access-log",                                    handle_access_log_list)
    app.router.add_get(             "/api/owner/access-log",                                    handle_access_log_list)
    app.router.add_route("OPTIONS", "/api/owner/access-log/{telegram_id}/{action:block|unblock|approve}", handle_access_log_action)
    app.router.add_post(            "/api/owner/access-log/{telegram_id}/{action:block|unblock|approve}", handle_access_log_action)
