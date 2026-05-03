"""
Routes for the owner dashboard (mini-app at /owner/).

Endpoints:
    GET /api/owner/ping     — auth-guarded smoke test
    GET /api/owner/finance  — revenue/profit/tips/by-office/trend (Money tab)

Register with:
    from owner_routes import setup as setup_owner_routes
    setup_owner_routes(app)
"""
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web

from owner_auth import require_owner, CORS_HEADERS
import db
# Premium card lists live in api_server (single source of truth).
from api_server import _FOUNDER_ID, _PREMIUM_IDS, _WORLDWIDE_IDS, tg_send, BOT_TOKEN


# ─── constants ──────────────────────────────────────────────────────────
DUBAI_TZ = timezone(timedelta(hours=4))

# Estimated profit margin until per-item cost-of-goods is tracked.
# Mock data assumed ~35% (6480/18420). Revisit once catalog has cost field.
MARGIN_PCT = 35

# Statuses that count as realized revenue.
REVENUE_STATUSES = ("delivered",)

# Office IDs (display order matches dashboard).
OFFICE_IDS = ("office_central", "office_north", "office_south")

VALID_PERIODS = ("today", "week", "month", "year")


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


def _period_window(period: str, ref: datetime = None):
    """Return (start, end, prev_start, prev_end) for the given period,
    all in Dubai TZ. `end` is exclusive (start of tomorrow for daily-aligned)."""
    ref = ref or _now_dubai()
    today_start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    if period == "today":
        start, end = today_start, tomorrow_start
        prev_start, prev_end = start - timedelta(days=1), start
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


def _by_office(orders) -> dict:
    by = {oid: 0 for oid in OFFICE_IDS}
    for _, o in orders:
        oid = o.get("office_id")
        if oid in by:
            by[oid] += int(o.get("total", 0) or 0)
    return by


def _bucket_trend(orders, start_dt: datetime, period: str) -> list:
    """Bucket revenue into time slots appropriate for the period:
    today→24 hours, week→7 days, month→30 days, year→12 months."""
    if period == "today":
        buckets = [0] * 24
        for dt, o in orders:
            buckets[dt.hour] += int(o.get("total", 0) or 0)
        # Trim to current hour so the sparkline doesn't show empty future hours.
        cutoff = _now_dubai().hour + 1
        return buckets[:cutoff] or [0]
    if period == "week":
        buckets = [0] * 7
        for dt, o in orders:
            idx = (dt.date() - start_dt.date()).days
            if 0 <= idx < 7:
                buckets[idx] += int(o.get("total", 0) or 0)
        return buckets
    if period == "month":
        buckets = [0] * 30
        for dt, o in orders:
            idx = (dt.date() - start_dt.date()).days
            if 0 <= idx < 30:
                buckets[idx] += int(o.get("total", 0) or 0)
        return buckets
    if period == "year":
        buckets = [0] * 12
        for dt, o in orders:
            months_diff = (dt.year - start_dt.year) * 12 + (dt.month - start_dt.month)
            if 0 <= months_diff < 12:
                buckets[months_diff] += int(o.get("total", 0) or 0)
        return buckets
    return []


def _last_7_days(all_orders: dict) -> list:
    """Last-7-days revenue bars (Money tab). Index 0 = 7 days ago, 6 = today."""
    today_start = _now_dubai().replace(hour=0, minute=0, second=0, microsecond=0)
    end = today_start + timedelta(days=1)
    start = end - timedelta(days=7)
    orders = _orders_in_window(all_orders, start, end)
    buckets = [0] * 7
    for dt, o in orders:
        idx = (dt.date() - start.date()).days
        if 0 <= idx < 7:
            buckets[idx] += int(o.get("total", 0) or 0)
    return buckets


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
    """Cheap health + identity check. Returns the authenticated owner's id."""
    return web.json_response(
        {
            "ok": True,
            "owner_id": request["owner_id"],
            "server_time": int(time.time()),
        },
        headers=CORS_HEADERS,
    )


@require_owner
async def handle_finance(request):
    """Finance summary powering the hero revenue card and Money tab.

    Query:
        period = today | week | month | year   (default: today)

    Response:
        revenue {current, previous, delta_pct, delta_label}
        profit  {current, margin_pct, estimated}
        tips
        by_office {office_central, office_north, office_south}
        trend []        — period-aware sparkline data
        bars_7d {values[7], average, total}  — always last 7 days for Money tab
    """
    period = request.query.get("period", "today")
    if period not in VALID_PERIODS:
        return web.json_response(
            {"error": f"invalid period (use one of: {', '.join(VALID_PERIODS)})"},
            status=400, headers=CORS_HEADERS,
        )

    all_orders = await db.get_all_orders()

    start, end, prev_start, prev_end = _period_window(period)
    curr_orders = _orders_in_window(all_orders, start, end)        # delivered only
    prev_orders = _orders_in_window(all_orders, prev_start, prev_end)
    curr_all    = _all_orders_in_window(all_orders, start, end)    # any status
    prev_all    = _all_orders_in_window(all_orders, prev_start, prev_end)

    rev_curr = _sum_field(curr_orders, "total")
    rev_prev = _sum_field(prev_orders, "total")
    pct = _delta_pct(rev_curr, rev_prev)

    bars = _last_7_days(all_orders)
    bars_total = sum(bars)

    # ── KPI section ───────────────────────────────────────────────────
    orders_count_curr = len(curr_all)
    orders_count_prev = len(prev_all)
    orders_delta_count = orders_count_curr - orders_count_prev
    delivered_count   = len(curr_orders)
    declined_count    = sum(1 for _, o in curr_all if o.get("status") in ("declined", "cancelled"))
    in_route_count    = sum(1 for o in all_orders.values() if o.get("status") == "approved")
    avg_check         = (rev_curr // delivered_count) if delivered_count else 0
    done_pct          = round(delivered_count / orders_count_curr * 100, 1) if orders_count_curr else 0
    # Reviews: collect non-null review_score from delivered orders in window
    reviews = [int(o.get("review_score")) for _, o in curr_orders if o.get("review_score")]
    rating_avg   = round(sum(reviews) / len(reviews), 2) if reviews else 0
    rating_count = len(reviews)

    period_lbl_for_rating = {"today":"сегодня","week":"неделя","month":"месяц","year":"год"}[period]

    return web.json_response({
        "period": period,
        "currency": "AED",
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
        "by_office": _by_office(curr_orders),
        "trend":     _bucket_trend(curr_orders, start, period),
        "bars_7d": {
            "values":  bars,
            "total":   bars_total,
            "average": bars_total // 7 if bars_total else 0,
        },
        "kpi": {
            "orders":         orders_count_curr,
            "orders_delta":   orders_delta_count,
            "avg_check":      avg_check,
            "done":           delivered_count,
            "done_total":     orders_count_curr,
            "done_pct":       done_pct,
            "in_route":       in_route_count,
            "declined":       declined_count,
            "rating":         rating_avg,
            "rating_count":   rating_count,
            "rating_period":  period_lbl_for_rating,
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


def setup(app):
    """Wire owner routes into the aiohttp app. Called from api_server.main()."""
    app.router.add_route("OPTIONS", "/api/owner/ping",    handle_ping)
    app.router.add_get(             "/api/owner/ping",    handle_ping)
    app.router.add_route("OPTIONS", "/api/owner/finance", handle_finance)
    app.router.add_get(             "/api/owner/finance", handle_finance)
    app.router.add_route("OPTIONS", "/api/owner/customers",              handle_customers)
    app.router.add_get(             "/api/owner/customers",              handle_customers)
    app.router.add_route("OPTIONS", "/api/owner/customers/{telegram_id}", handle_customer_detail)
    app.router.add_get(             "/api/owner/customers/{telegram_id}", handle_customer_detail)
    app.router.add_route("OPTIONS", "/api/owner/customers/{telegram_id}/{action:ban|unban}", handle_customer_ban)
    app.router.add_post(            "/api/owner/customers/{telegram_id}/{action:ban|unban}", handle_customer_ban)
