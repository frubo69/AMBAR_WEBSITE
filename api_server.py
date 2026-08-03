#!/usr/bin/env python3
"""
AMBAR API + Static file server — MongoDB edition
- Serves the mini app HTML/assets on /
- GET  /api/orders  → order history for authenticated Telegram user
- POST /api/order   → create order, notify customer + operators
- POST /api/support/send        → support chat message
- POST /api/support/send-image  → support chat photo
- GET  /api/support/messages    → fetch conversation
All user/order data is stored in MongoDB Atlas (db: ambar).
"""
from __future__ import annotations
import re
import os, json, hmac, hashlib, html as _html_mod, urllib.parse, mimetypes, logging, time, uuid, math, asyncio
from datetime import datetime, timezone, timedelta
DUBAI_TZ = timezone(timedelta(hours=4))
from pathlib import Path
import aiohttp as _aiohttp
from aiohttp import web
from dotenv import load_dotenv
import db
from customer_card import render_customer_card
from config import (
    CRYPTO_REAL_MODE, CRYPTO_USDT_PER_AED, CRYPTO_REQUIRED_CONF,
    CRYPTO_TTL_MIN, CRYPTO_AMOUNT_STEP, TRON_RECEIVE_ADDRESS,
    CRYPTO_WATCH_INTERVAL_SEC, CRYPTO_WATCH_DRYRUN, CRYPTO_FEE_PCT, CRYPTO_TEST_USDT,
    CRYPTO_AED_PER_USDT,
)
from tron import get_incoming_usdt

load_dotenv()
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
SUPPORT_BOT_TOKEN  = os.getenv("SUPPORT_BOT_TOKEN", "")
# Owner bot token — separate Telegram bot (@ambar_manage_bot) whose initData
# signs requests to /api/owner/*. Kept in .env, never in code.
OWNER_BOT_TOKEN    = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
WEBAPP_URL         = os.getenv("WEBAPP_URL", "https://ambar-delivery.com/")
OPERATOR_IDS       = [int(x.strip()) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]
PORT               = int(os.getenv("WEBAPP_PORT", "8080"))
HOST               = os.getenv("WEBAPP_HOST", "127.0.0.1")
STATIC_DIR         = Path(__file__).parent
UPLOAD_DIR         = STATIC_DIR / "uploads" / "support"
_TEST_ACCOUNTS     = {8251195567, 6731325660}

# ── Crypto payments: staged rollout gate ──────────────────────────────────────
# While CRYPTO_PAYMENTS_FOR_ALL is off, only "admin" accounts see a working
# crypto-pay flow; everyone else sees the button greyed out with a "Soon" label.
# This is a DISPLAY gate (it rides on /api/me, which trusts the uid query param).
# The money-handling endpoints we add later MUST re-derive identity from the
# signed Telegram initData — never from this flag.
def _parse_id_set(env_name: str) -> set[int]:
    return {int(x.strip()) for x in os.getenv(env_name, "").split(",")
            if x.strip().lstrip("-").isdigit()}

CRYPTO_PAYMENTS_FOR_ALL = os.getenv("CRYPTO_PAYMENTS_FOR_ALL", "").strip().lower() in ("1", "true", "yes", "on")
# Admins who get the live flow during rollout: explicit allowlist + every staff
# role we already know about (operators/managers/admins/owners) + the founder.
_CRYPTO_TEST_IDS = _parse_id_set("AMBAR_CRYPTO_TEST_IDS")  # exact ids for the test-price override
_CRYPTO_ALLOWLIST = (
    _CRYPTO_TEST_IDS
    | _parse_id_set("AMBAR_ADMIN_IDS")
    | _parse_id_set("AMBAR_OWNER_IDS")
    | _parse_id_set("AMBAR_MANAGER_IDS")
    | set(OPERATOR_IDS)
)

def _crypto_enabled_for(uid: int) -> bool:
    if CRYPTO_PAYMENTS_FOR_ALL:
        return True
    return uid in _CRYPTO_ALLOWLIST or uid == _FOUNDER_ID

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


def _fmt_aed(v) -> str:
    """340.0 → '340', 340.5 → '340.50' — money without float noise."""
    try:
        f = round(float(v or 0), 2)
    except (TypeError, ValueError):
        return "0"
    return f"{f:.2f}".rstrip("0").rstrip(".") if f != int(f) else str(int(f))


# ── DB lifecycle ──────────────────────────────────────────────────────────────
async def on_startup(app):
    await db.connect()
    # Send welcome messages to any newly-issued premium card holders.
    # Runs once at startup (i.e. right after `git pull` + restart).
    try:
        await _announce_new_elite_cards()
    except Exception as e:
        log.warning(f"[elite] startup announce failed: {e}")
    try:
        await _announce_new_worldwide_cards()
    except Exception as e:
        log.warning(f"[worldwide] startup announce failed: {e}")
    # Read-only crypto watcher: polls TronGrid for confirmed USDT transfers and
    # promotes matching invoices to real orders. Inert unless CRYPTO_REAL_MODE.
    app["crypto_watcher"] = asyncio.create_task(_crypto_watch_loop(app))


async def _send_card_welcome(ids, flag_field, total, pad, title_ru, tag):
    """Send welcome to any ids[] holder whose flag_field isn't set on user doc.

    ids:         ordered list — index+1 is the card number
    flag_field:  user doc boolean marking "already announced"
    total:       total cards in the tier (10, 100, ...)
    pad:         zero-pad width for card number string (2, 3, ...)
    title_ru:    the display word shown in bold in the message
    tag:         short key for log lines
    """
    if not ids:
        return
    for idx, uid in enumerate(ids):
        try:
            user_doc = await db.get_user(uid)
        except Exception:
            user_doc = None
        if (user_doc or {}).get(flag_field):
            continue
        card_number = f"{idx + 1:0{pad}d}"
        welcome_text = (
            f"🎉 *Добро пожаловать в клуб {title_ru}*\n\n"
            f"Вы стали обладателем эксклюзивной карты *{title_ru}* №{card_number} — одной из {total} по всему миру.\n\n"
            "✨ *Ваши привилегии:*\n\n"
            "• ⚡️ *Premium Express* — приоритетная обработка и ускоренная доставка\n"
            "• 👑 Персональный оператор на связи\n"
            "• 🥃 Эксклюзивные позиции и пробники редких напитков\n"
            f"• 💎 Закрытые предложения только для держателей карты {title_ru}\n\n"
            f"Откройте приложение, чтобы увидеть ваш {title_ru}-статус.\n\n"
            "_Добро пожаловать._ 🥂"
        )
        kb = {"inline_keyboard": [[{
            "text": "👑 Открыть PREMIUM-статус",
            "web_app": {"url": f"{WEBAPP_URL.rstrip('/')}#profile"}
        }]]}
        try:
            resp = await tg_send(BOT_TOKEN, uid, welcome_text, parse_mode="Markdown", reply_markup=kb)
            if resp and resp.get("ok"):
                await db.set_user_field(uid, **{flag_field: True})
                log.info(f"[{tag}] welcome sent to {uid} (card #{card_number})")
            else:
                log.warning(f"[{tag}] welcome failed for {uid}: {resp}")
        except Exception as e:
            log.warning(f"[{tag}] welcome send failed for {uid}: {e}")


async def _announce_new_worldwide_cards():
    await _send_card_welcome(_WORLDWIDE_IDS, "worldwide_card_announced", 100, 3, "PREMIUM", "worldwide")


async def _announce_new_elite_cards():
    await _send_card_welcome(_PREMIUM_IDS, "elite_card_announced", 10, 2, "ÉLITE", "elite")

async def on_cleanup(app):
    watcher = app.get("crypto_watcher")
    if watcher:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
    db.close()


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Nothing here was throttled: order creation, support messages and 5 MB image
# uploads could all be fired in a loop. A single process serves everything, so an
# in-memory sliding window is enough and costs nothing — this is a brake against
# floods and disk-fill, not a defence against a distributed attacker.
_rl_hits: dict[tuple[str, str], list[float]] = {}
_RL_LAST_SWEEP = 0.0


def _rate_limited(bucket: str, key, limit: int, per: float) -> bool:
    """True when this key has already used up `limit` calls in the last `per` sec."""
    global _RL_LAST_SWEEP
    now = time.time()
    k = (bucket, str(key))
    hits = [t for t in _rl_hits.get(k, ()) if now - t < per]
    # Periodic sweep so abandoned keys cannot grow the dict without bound.
    if now - _RL_LAST_SWEEP > 300:
        _RL_LAST_SWEEP = now
        for dead in [kk for kk, ts in _rl_hits.items() if not ts or now - ts[-1] > 3600]:
            _rl_hits.pop(dead, None)
    if len(hits) >= limit:
        _rl_hits[k] = hits
        return True
    hits.append(now)
    _rl_hits[k] = hits
    return False


def _too_many(retry_after: int = 60) -> web.Response:
    return web.json_response(
        {"error": "rate_limited"}, status=429,
        headers={**CORS_HEADERS, "Retry-After": str(retry_after)},
    )


def _client_ip(request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else (request.remote or "?"))


# ── Telegram initData validation ──────────────────────────────────────────────
# initData is HMAC-SHA256 signed with the bot's token. Every Telegram bot
# has its own token, so a miniapp launched from @ambar_bot and one launched
# from @ambar_manage_bot produce initData signed differently — we validate
# each against the right secret and never mix them.
# A signature alone never expires: initData captured once (a proxy log, a shared
# device, a screenshot of devtools) would stay a valid credential forever, and we
# have no way to revoke it. Telegram signs an auth_date for exactly this reason —
# treat initData older than this as expired. 24h is generous enough that a
# miniapp left open all day keeps working; Telegram re-issues it on next launch.
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))


def _validate_init_data_with_token(init_data: str, token: str) -> dict | None:
    if not token:
        return None
    try:
        params    = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val  = params.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc_hash  = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, hash_val):
            return None
        if INIT_DATA_MAX_AGE > 0:
            try:
                age = time.time() - int(params.get("auth_date", "0"))
            except (TypeError, ValueError):
                return None
            # Missing/zero auth_date lands far in the past and is refused too.
            if age > INIT_DATA_MAX_AGE or age < -300:
                log.warning(f"[auth] expired initData rejected (age {int(age)}s)")
                return None
        return json.loads(params.get("user", "{}"))
    except Exception as e:
        log.debug(f"initData parse error: {e}")
        return None


def validate_init_data(init_data: str) -> dict | None:
    """Validate initData from the customer bot (@ambar_bot)."""
    return _validate_init_data_with_token(init_data, BOT_TOKEN)


def validate_owner_init_data(init_data: str) -> dict | None:
    """Validate initData from the owner bot (@ambar_manage_bot). Used by
    /api/owner/* endpoints — a customer-bot initData will not pass here."""
    return _validate_init_data_with_token(init_data, OWNER_BOT_TOKEN)


# ── Telegram Bot API helpers ───────────────────────────────────────────────────
_op_bot_username = None
async def _resolve_op_bot_username():
    """Fetch and cache @username of OPERATOR_BOT for t.me/<bot>?start=... deep links."""
    global _op_bot_username
    if _op_bot_username or not OPERATOR_BOT_TOKEN:
        return _op_bot_username
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{OPERATOR_BOT_TOKEN}/getMe") as resp:
                d = await resp.json()
                _op_bot_username = d.get("result", {}).get("username")
    except Exception as e:
        log.warning(f"getMe(OPERATOR_BOT) failed: {e}")
    return _op_bot_username

async def tg_send(token, chat_id, text, parse_mode="Markdown", reply_markup=None, reply_to_message_id=None):
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if reply_to_message_id:
        payload["reply_parameters"] = json.dumps({
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True
        })
    # Hard timeout: without it a stalled Telegram call hangs the awaiting request
    # forever (try/except can't catch an infinite await) — that can silently freeze
    # notification flows like handle_verify_request mid-send.
    _to = _aiohttp.ClientTimeout(total=20)
    async with _aiohttp.ClientSession(timeout=_to) as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()

async def tg_edit(token, chat_id, message_id, text, parse_mode="HTML", reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with _aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()

async def tg_delete(token, chat_id, message_id):
    """Silently delete a message. Telegram only allows this within 48h; errors are swallowed."""
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "message_id": message_id}) as resp:
                return await resp.json()
    except Exception as e:
        log.debug(f"tg_delete {chat_id}/{message_id}: {e}")
        return None

async def tg_send_photo(token, chat_id, photo_path, caption=""):
    url  = f"https://api.telegram.org/bot{token}/sendPhoto"
    data = _aiohttp.FormData()
    data.add_field("chat_id", str(chat_id))
    if caption:
        data.add_field("caption", caption[:1024])
    with open(photo_path, "rb") as f:
        data.add_field("photo", f, filename=Path(photo_path).name, content_type="image/jpeg")
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as resp:
                return await resp.json()


# ── POST /api/order ───────────────────────────────────────────────────────────
def _new_order_id() -> str:
    """Server-issued order id. Random tail so ids are not guessable or replayable."""
    return f"AMB{int(time.time()) % 100000:05d}{uuid.uuid4().hex[:3].upper()}"


async def handle_create_order(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid = user.get("id")
    if _rate_limited("order", uid, 10, 60):
        log.warning(f"[rl] order flood from uid={uid}")
        return _too_many()
    # Ban check — reject order if user is banned
    try:
        if await db.is_banned(uid):
            log.warning(f"[order] banned user {uid} attempted to place order")
            return web.json_response({"error": "banned"}, status=403, headers=CORS_HEADERS)
    except Exception as e:
        log.warning(f"ban check failed: {e}")

    # The order id is ours, not the client's. db.save_order upserts on order_id,
    # so honouring a client-supplied id let anyone $set over an existing order —
    # someone else's, or their own already-delivered one — by replaying the id.
    # The client still gets the id back in the response, so nothing downstream
    # needs it up front.
    oid = _new_order_id()
    for _ in range(5):
        if not await db.get_order(oid):
            break
        oid = _new_order_id()
    else:
        log.error(f"[order] could not allocate a free order_id for uid={uid}")
        return web.json_response({"error": "try_again"}, status=503, headers=CORS_HEADERS)

    # Crypto orders. The client may submit payment_method="crypto" / paid=true.
    #   • DEMO (CRYPTO_REAL_MODE off — today's state, no real wallet/watcher yet):
    #     accept it as a *test* prepaid order so the operator gets a loud
    #     "TEST CRYPTO ORDER" banner (see _finalize_accepted_order).
    #   • REAL mode: NEVER trust a client-side "paid" claim. Genuine crypto orders
    #     are promoted only by the on-chain watcher (_promote_invoice_to_order),
    #     so reject the client-submitted paid order outright.
    prepaid = None
    if data.get("payment_method") == "crypto" and data.get("paid"):
        if CRYPTO_REAL_MODE:
            log.warning(f"[order] rejected client crypto 'paid' claim for {oid} "
                        f"(uid={uid}) — real mode credits via on-chain watcher only")
            return web.json_response({"error": "crypto_paid_unverified"},
                                     status=403, headers=CORS_HEADERS)
        _c = data.get("crypto") or {}
        prepaid = {"method": _c.get("asset", "USDT"), "txid": _c.get("txid"),
                   "amount_usdt": _c.get("amount"), "test": True}

    # Номер должен быть подтверждён Telegram: клиент делится контактом, бот его
    # ловит и кладёт в phone_verified. Без этого заказ не принимаем — именно
    # ради отсечения выдуманных номеров всё и затевалось. Проверяем на сервере,
    # блокировка кнопки во фронте — только удобство.
    try:
        _u = await db.get_user(uid)
    except Exception:
        _u = None
    if not (_u or {}).get("phone_verified") and uid not in _TEST_ACCOUNTS:
        log.warning(f"[order] uid={uid} без подтверждённого номера — отказ")
        return web.json_response({"error": "phone_not_verified"},
                                 status=403, headers=CORS_HEADERS)

    # В ДОЛГ (pay-later): only for whitelisted customers — the server re-checks,
    # the client-side gate is cosmetic.
    debt = False
    if data.get("payment_method") == "debt":
        try:
            debt = await db.is_debt_allowed(uid)
        except Exception as e:
            log.warning(f"[debt] allow check failed for uid={uid}: {e}")
        if not debt:
            log.warning(f"[order] rejected В ДОЛГ order {oid} from non-whitelisted uid={uid}")
            return web.json_response({"error": "debt_not_allowed"},
                                     status=403, headers=CORS_HEADERS)

    result = await _finalize_accepted_order(data, user, oid, prepaid=prepaid, debt=debt)
    return web.json_response(
        {"ok": True, "order_id": oid, "needs_verification": result["needs_verification"]},
        headers=CORS_HEADERS,
    )


def _is_vetted(user_doc: dict | None) -> bool:
    """A customer counts as vetted when formally verified OR with at least one
    DELIVERED order — the courier has already met them face to face. Keeps the
    verification wall for genuinely new customers, but never again holds a
    regular's order from the operator (legacy customers predate the verify flow
    and have no `verified` flag). Used by the operator hold, /api/me (the app's
    wall) and the operator status label — one rule, so they can't disagree."""
    if not user_doc:
        return False
    if user_doc.get("verified"):
        return True
    try:
        return int(user_doc.get("orders_done", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


async def _finalize_accepted_order(src: dict, user: dict, oid: str, *,
                                   prepaid: dict | None = None,
                                   debt: bool = False) -> dict:
    """Persist an accepted order and run the full notification fan-out: customer
    card, first-order verification gate, operator + owner notifications, and
    referral points. Shared by the live POST /api/order path and the crypto
    watcher's promotion of a confirmed prepaid invoice.

    `src` carries the order fields (the request body, or an invoice's stored
    order_payload). `user` is the Telegram identity (from signed initData, or
    reconstructed from the invoice). `prepaid`, when set, is a dict
    {"method","txid","amount_usdt"} marking the order as already paid online.
    Returns {"needs_verification": bool, "is_first_order": bool}.
    """
    uid       = user.get("id")
    original_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    user_name = original_name
    username  = user.get("username", "—")
    lang      = src.get("lang", "ru")

    # Append operator nickname if set (original name stays, nickname in parentheses)
    try:
        user_doc = await db.get_user(uid)
        if user_doc and user_doc.get("custom_name"):
            user_name = f"{original_name} ({user_doc['custom_name']})"
    except Exception:
        pass

    items     = src.get("items", [])
    phone     = src.get("phone", "—")
    # Три номера живут отдельно и намеренно: подтверждённый нельзя потерять,
    # даже если клиент вписал в доставку другой. Оператор видит всё.
    try:
        phone_shared = (await db.get_user(uid) or {}).get("phone_verified") or ""
    except Exception:
        phone_shared = ""
    phone_extra  = re.sub(r"\D", "", str(src.get("phone_extra") or ""))
    address   = src.get("address", "—")
    gmap_link = src.get("gmap_link", "")
    is_gps    = src.get("is_gps", False)
    tip       = src.get("tip", 0)
    # The client's `total` is a display value, never the price. Re-derive it from
    # item ids against catalog.json — the crypto path already did this (F4), the
    # cash/card path did not, so a hand-crafted POST could book a 2000 AED basket
    # as 1 AED and the operator card would print the lie.
    total     = src.get("total", 0)
    try:
        authoritative = await _recompute_order_total_aed(items, tip)
        if authoritative > 0:
            if abs(float(total or 0) - authoritative) > 0.5:
                log.warning(f"[order] #{oid} total mismatch: client said {total}, "
                            f"catalog says {authoritative} — using catalog")
            total = authoritative
    except Exception as e:
        log.error(f"[order] total recompute failed for #{oid}: {e}")
    loc       = src.get("location", {})
    # Офис определяет СЕРВЕР по координатам доставки: во фронтенде нет ни
    # координат офисов, ни логики выбора (и не должно быть). Опорные точки
    # приходят из .env — см. config_offices. Если координат нет (адрес введён
    # руками) или точки не настроены — остаётся район, выбранный в форме.
    office_id = src.get("office_id") or ""
    office_nm = src.get("office_name") or ""
    try:
        from config_offices import resolve_office
        office_id, office_nm, _rule = resolve_office(src)
        # «default» значит, что у заказа не было ни координат, ни района, ни
        # узнаваемого адреса — район подставлен, но это повод присмотреться.
        log.info(f"[office] #{oid} → {office_id} (по признаку: {_rule})"
                 if _rule != "default" else
                 f"[office] #{oid} → {office_id} ПО УМОЛЧАНИЮ: определить район было нечем")
    except Exception as e:
        log.error(f"[office] resolve failed: {e}")
    comment   = src.get("comment", "")

    item_lines = "\n".join(
        f"  • {i['name']} ×{i['qty']} = {i.get('line_total', i['price'] * i['qty'])} AED"
        for i in items
    )

    # Check if this is the user's very first order (before incrementing)
    referred_by = None
    referrer_username = None
    _user_verified = False
    try:
        user_doc = await db.get_user(uid)
        _TEST_ALWAYS_FIRST = {8251195567, 6731325660}  # DEBUG: always treat as first order
        is_first_order = (uid in _TEST_ALWAYS_FIRST) or (user_doc is None or user_doc.get("orders_total", 0) == 0)
        # Whether the customer is already vetted. The operator hold below keys off
        # THIS, not "first order", so it stays in lock-step with the app's wall — an
        # unverified customer is held until they submit the form no matter how many
        # orders they've started (closes the place-a-second-order bypass).
        _user_verified = _is_vetted(user_doc)
        # Reset verification for test accounts so each order triggers full flow
        if uid in _TEST_ALWAYS_FIRST:
            await db.set_user_field(uid, verified=False, verify_requested=False)
            _user_verified = False
        if is_first_order and user_doc and user_doc.get("referred_by"):
            referred_by = user_doc["referred_by"]
    except Exception:
        is_first_order = False

    # A confirmed crypto payment IS the verification — paying real USDT on-chain is a
    # stronger trust signal than the self-reported source form, so the payer is
    # auto-verified here (no operator approval) and the prepaid order is never held
    # below. Cash orders are untouched and still go through the manual gate.
    if prepaid and uid not in _TEST_ACCOUNTS:
        try:
            await db.verify_user(uid)
        except Exception as e:
            log.warning(f"[crypto] auto-verify on prepaid failed for uid={uid}: {e}")

    # Hold this order back from the operator until an UNVERIFIED customer submits their
    # verification. Keyed off verified status (not "first order") so it stays in
    # lock-step with the app's wall — it can never ship an order while the wall is still
    # up, and it closes the place-a-second-order bypass. Prepaid crypto is auto-verified
    # just above, so it passes straight through. One condition, reused for the customer
    # warning and the operator hold so the two never disagree.
    _needs_verification = (not _user_verified) and uid not in _TEST_ACCOUNTS and not prepaid

    # Save order + upsert user profile in parallel
    order_doc = {
        "order_id": oid,        "customer_id": uid,
        "customer_name": user_name, "username": username,
        "phone": phone,         "address": address,   "location": loc,
        # Номер из Telegram сохраняем всегда, даже если доставка на другой.
        "phone_shared": phone_shared, "phone_extra": phone_extra,
        "gmap_link": gmap_link, "is_gps": is_gps,
        "items": items,         "item_lines": item_lines,
        "tip": tip,             "total": total,        "lang": lang,
        "office_id": office_id, "office_name": office_nm, "comment": comment,
        "status": "pending",    "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if prepaid:
        order_doc["payment_method"]     = "crypto"
        order_doc["paid"]               = True
        order_doc["crypto_txid"]        = prepaid.get("txid")
        order_doc["crypto_amount_usdt"] = prepaid.get("amount_usdt")
        if prepaid.get("test"):
            order_doc["crypto_test"]    = True   # demo order — not a real payment
    elif debt:
        order_doc["payment_method"] = "debt"
    if uid not in _TEST_ACCOUNTS:
        await db.save_order(oid, order_doc)
        user_fields = dict(name=original_name, full_name=original_name, first_name=user.get("first_name",""),
                           last_name=user.get("last_name",""), username=username,
                           language_code=user.get("language_code", ""),
                           last_order_at=datetime.now(timezone.utc))
        if phone != "—":
            user_fields["phone"] = phone
        await db.upsert_user(uid, **user_fields)
        await db._increment_user(uid, orders_total=1)

    # ── Customer confirmation (single live status msg, edited through lifecycle) ─
    # Purge the live msgs from previous *finished* orders so the chat stays clean.
    # Active orders (pending/confirmed/approved) keep their cards — the customer
    # still needs visibility into in-flight work while placing a new order.
    _TERMINAL_STATUSES = {"delivered", "cancelled", "declined", "rejected"}
    try:
        prev_orders = await db.get_user_orders(uid)
        for po in prev_orders:
            if po.get("order_id") == oid:
                continue  # skip the order we just created
            if po.get("status") not in _TERMINAL_STATUSES:
                continue  # preserve live cards for still-active orders
            prev_mid = po.get("customer_msg_id") or (po.get("customer_msg_ids") or [None])[0]
            if prev_mid:
                await tg_delete(BOT_TOKEN, uid, prev_mid)
                await db.update_order(po["order_id"], customer_msg_id=None)
    except Exception as e:
        log.debug(f"prev msg cleanup: {e}")
    try:
        confirm = render_customer_card(order_doc, lang)
        conf_result = await tg_send(BOT_TOKEN, uid, confirm)
        conf_msg_id = conf_result.get("result", {}).get("message_id")
        if conf_msg_id and uid not in _TEST_ACCOUNTS:
            await db.update_order(oid, customer_msg_id=conf_msg_id)
    except Exception as e:
        log.error(f"Customer confirm: {e}")
    # Warn the customer their order is held — same condition as the operator hold, so a
    # verified customer placing a first order never gets a false "held" warning.
    if _needs_verification:
        if lang == "ru":
            warn_text = (
                "🚨 <b>ВЕРИФИКАЦИЯ ОБЯЗАТЕЛЬНА</b>\n\n"
                "Ваш заказ <b>не передан оператору</b>, пока вы не пройдёте верификацию в приложении.\n\n"
                "Откройте AMBAR и заполните короткую форму — займёт минуту."
            )
        else:
            warn_text = (
                "🚨 <b>VERIFICATION REQUIRED</b>\n\n"
                "Your order <b>has not been sent to an operator</b> until you complete verification in the app.\n\n"
                "Open AMBAR and fill out a short form — it takes a minute."
            )
        try:
            resp = await tg_send(BOT_TOKEN, uid, warn_text, parse_mode="HTML")
            warn_mid = (resp or {}).get("result", {}).get("message_id")
            if warn_mid:
                await db.set_user_field(uid, verify_warn_msg_id=warn_mid)
        except Exception as e:
            log.warning(f"Verification-warning send failed: {e}")

    # ── Operator notification ─────────────────────────────────────────────────
    lat, lon = loc.get("lat", 0), loc.get("lon", 0)
    # Build google maps link if not provided
    if not gmap_link and lat and lon:
        try: gmap_link = f"https://maps.google.com/maps?q={float(lat):.6f},{float(lon):.6f}"
        except (ValueError, TypeError): pass
    # Address line: text name first, then link. User-typed address MUST be escaped —
    # a stray "<" in it makes Telegram reject the whole HTML message (silent drop).
    _addr_esc = _html_mod.escape(str(address or "—"))
    if address and address != "GPS" and address != "—":
        addr_line = f"🏠 Адрес: {_addr_esc}"
        if gmap_link:
            addr_line += f"\nGoogle Maps: {gmap_link}"
    elif gmap_link:
        addr_line = f"📍 GPS: {gmap_link}"
    else:
        addr_line = f"🏠 Адрес: {_addr_esc}"
    # HTML-safe item lines for the operator message (stored item_lines stays raw —
    # other consumers render it as plain text).
    _item_lines_html = "\n".join(
        f"  • {_html_mod.escape(str(i.get('name', '')))} ×{i.get('qty', 0)} = "
        f"{i.get('line_total', (i.get('price', 0) or 0) * (i.get('qty', 0) or 0))} AED"
        for i in items
    )

    # Source info collected pre-payment (crypto auto-verify path). Shown on the paid
    # order's banner so the operator still sees where the customer came from — the same
    # data the cash flow surfaces via the verify-request message.
    _src_info = ""
    if prepaid and is_first_order:
        _vs = (src.get("verify_source") or "").strip()
        if _vs:
            _src_labels = {"friend": "👥 Знакомый", "operator": "📞 Оператор", "other": "💬 Другое"}
            _src_info = f"\n📋 Источник: <b>{_src_labels.get(_vs, _html_mod.escape(_vs))}</b>"
            _rn = (src.get("verify_recommender_name") or "").strip()
            _rp = (src.get("verify_recommender_phone") or "").strip()
            _sd = (src.get("verify_source_detail") or "").strip()
            if _vs == "friend" and _rn:
                _src_info += f"\n👤 {_html_mod.escape(_rn)}" + (f" — {_html_mod.escape(_rp)}" if _rp else "")
            elif _sd:
                _src_info += f"\n💬 {_html_mod.escape(_sd)}"

    # Build first order banner — with referral info if applicable
    if is_first_order and uid in _TEST_ACCOUNTS:
        first_order_banner = "<blockquote>🟢🟢🟢 <b>ТЕСТ (НЕ НАСТОЯЩИЙ ЗАКАЗ)</b> 🟢🟢🟢</blockquote>\n\n"
    elif is_first_order and referred_by:
        try:
            referrer_doc = await db.get_user(referred_by)
            referrer_username = referrer_doc.get("username", "—") if referrer_doc else "—"
        except Exception:
            referrer_username = "—"
        first_order_banner = f"<blockquote>🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ — РЕФЕРАЛ</b> 🔴🔴🔴\n👥 Пригласил — @{referrer_username}{_src_info}</blockquote>\n\n"
    elif is_first_order:
        first_order_banner = f"<blockquote>🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ!</b> 🔴🔴🔴{_src_info}</blockquote>\n\n"
    else:
        first_order_banner = ""
    # Prepaid (crypto) orders are already settled — flag it so the operator does
    # NOT collect cash on delivery.
    if prepaid and prepaid.get("test"):
        # DEMO crypto order (real wallet/watcher not connected). Loud warning so the
        # operator never mistakes it for a settled order. Inert once CRYPTO_REAL_MODE.
        paid_banner = (
            "\n\n<blockquote>🧪🧪🧪 <b>TEST CRYPTO ORDER</b> 🧪🧪🧪\n"
            "<b>НЕ НАСТОЯЩАЯ ОПЛАТА</b> — демо крипто-оплаты, реальный кошелёк ещё "
            "не подключён. Не выдавайте заказ как оплаченный.\n"
            f"Заявленная сумма: {_html_mod.escape(str(prepaid.get('amount_usdt')))} USDT</blockquote>"
        )
    elif prepaid:
        # Settled online (crypto). Sits right after the total + highlighted, so the
        # operator clearly sees it's already paid and must NOT collect cash.
        paid_banner = (
            "\n\n<blockquote>✅💎 <b>ОПЛАЧЕНО ОНЛАЙН — КРИПТА</b>\n"
            f"💵 USDT · TRC-20 · <b>{prepaid.get('amount_usdt')} USDT</b>\n"
            "☑️ Уже зачислено — <b>наличные НЕ брать</b></blockquote>"
        )
    elif debt:
        # В ДОЛГ: goods go out now, money comes later. Show the running balance so
        # the operator decides accept/decline with the full picture in front of them.
        _cur_debt = 0.0
        try:
            _cur_debt = await db.get_debt(uid)
        except Exception as e:
            log.warning(f"[debt] balance fetch failed for uid={uid}: {e}")
        try:
            _after = round(_cur_debt + float(total or 0), 2)
        except (TypeError, ValueError):
            _after = _cur_debt
        paid_banner = (
            "\n\n<blockquote>📒 <b>ОПЛАТА: В ДОЛГ</b>\n"
            f"💰 Текущий долг: <b>{_fmt_aed(_cur_debt)} AED</b>\n"
            f"➕ Этот заказ: {_fmt_aed(total)} AED → долг станет <b>{_fmt_aed(_after)} AED</b>\n"
            "☑️ Наличные НЕ брать — сумма записывается в долг</blockquote>"
        )
    else:
        paid_banner = ""
    tip_line = f"\n🎁 Чаевые: {tip} AED" if tip else ""
    # Номера в карточку заказа НЕ попадают: она пересылается водителям и висит
    # в общем чате операторов. Телефон живёт только за кнопкой «Клиент»
    # (operator_bot.customer_card), где его видит тот, кому он нужен.
    _comment_esc = _html_mod.escape(comment) if comment else ""
    op_text = (
        f"{first_order_banner}"
        f"🏢 Офис: <b>{_html_mod.escape(office_nm)}</b>\n\n"
        f"🆕 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
        f"{addr_line}\n\n"
        f"🛒 <b>Позиции:</b>\n{_item_lines_html}\n"
        f"{tip_line}"
        f"\n💰 <b>Итого: {total} AED</b>"
        f"{paid_banner}"
        + (f"\n\n💬 <b>Комментарий:</b> {_comment_esc}" if comment else "")
    )
    # Held back here (condition computed above) until the customer verifies — keeps the
    # order invisible to the operator while the app still shows the wall.
    if _needs_verification:
        await db.update_order(oid, pending_verification=True, op_text=op_text,
                              referred_by=referred_by, referrer_username=referrer_username)
        log.info(f"[order] #{oid} held for verification — operator notification delayed (ref={referrer_username})")
    else:
        op_buttons = [
            [
                {"text": "✅ Принять",   "callback_data": f"acc_{oid}_{uid}"},
                {"text": "❌ Отклонить", "callback_data": f"dec_{oid}_{uid}"},
            ],
            [
                {"text": "✏️ Редактировать", "callback_data": f"edit_{oid}"},
                {"text": "📍 Геолокация",    "callback_data": f"loc_{oid}"},
            ],
            [{"text": "👤 Клиент", "callback_data": f"client_{oid}_{uid}"}],
        ]
        op_kb = {"inline_keyboard": op_buttons}
        op_msg_ids = {}
        for op_id in OPERATOR_IDS:
            try:
                resp = await tg_send(OPERATOR_BOT_TOKEN, op_id, op_text, parse_mode="HTML", reply_markup=op_kb)
                if resp and resp.get("ok") and resp.get("result"):
                    op_msg_ids[str(op_id)] = resp["result"]["message_id"]
                else:
                    # ok:false (parse error / blocked / bad chat) was silently
                    # swallowed before — the #1 way orders vanished for operators.
                    log.error(f"Operator notify {op_id} REJECTED for #{oid}: {resp}")
            except Exception as e:
                log.error(f"Operator notify {op_id}: {e}")
        if op_msg_ids:
            await db.update_order(oid, op_msg_ids=op_msg_ids)
        elif OPERATOR_IDS:
            # Not a single operator got the order — that's an outage, not a log line.
            log.error(f"[order] #{oid} reached NO operator — check OPERATOR_BOT_TOKEN / OPERATOR_IDS")
            try:
                from owner_routes import notify_owners_force
                await notify_owners_force(
                    "orders.opFail",
                    f"🛑 *Заказ #{oid} НЕ доставлен ни одному оператору!*\n"
                    f"Он висит в списке «Новые заказы», но пуш не дошёл.\n"
                    f"💰 {total} AED · {user_name}\n"
                    f"Проверьте операторский бот.")
            except Exception as e:
                log.error(f"[owner-notif] opFail alert failed: {e}")

    # Award referral points (+5) to the referrer on first order
    if is_first_order and referred_by:
        try:
            await db.award_referral_points(referred_by, uid, 5)
            # Notify referrer about the bonus
            ref_msg = (
                f"🎉 *Реферальный бонус!*\n\n"
                f"Ваш друг сделал первый заказ.\n"
                f"Вам начислено *+5 очков* лояльности!"
            )
            await tg_send(BOT_TOKEN, referred_by, ref_msg)
            log.info(f"[referral] awarded 5 pts to {referred_by} for {uid}'s first order")
        except Exception as e:
            log.error(f"Referral award failed: {e}")

    log.info(f"[order] #{oid} user={uid} items={len(items)} total={total} AED")

    # Owner notification — fires for EVERY order at placement, INCLUDING held ones, so the
    # owner is never blind to an order even if the customer abandons the verification form.
    # Held orders are flagged "⏳ ОЖИДАЕТ ВЕРИФИКАЦИИ". Only the OPERATOR stays gated on
    # verification (handle_verify_request); the owner always gets this heads-up. (Two real
    # orders were silently stranded when this was deferred — #AMB1713977 and one before it.)
    try:
        from owner_routes import notify_new_order
        await notify_new_order(oid, total, user_name, phone, address, office_nm or office_id,
                               uid, _FOUNDER_ID, _PREMIUM_IDS, _WORLDWIDE_IDS,
                               items=items, prepaid=prepaid, held=_needs_verification)
    except Exception as e:
        log.error(f"[owner-notif] orders.new failed: {e}")

    if is_first_order:
        try:
            from owner_routes import notify_owners
            await notify_owners(
                "customers.new",
                f"👤 *Новый клиент · первый заказ*\n"
                f"Имя: {user_name}\n"
                f"@{username or '—'}\n"
                f"Заказ #{oid} · {total} AED"
            )
        except Exception as e:
            log.error(f"[owner-notif] customers.new failed: {e}")

    return {"needs_verification": _needs_verification, "is_first_order": is_first_order}


# ── Crypto payments: invoice create + status (USDT TRC-20, watch-only) ─────────
# POST /api/crypto/invoice      → reserve a unique amount, persist a WAITING
#                                 invoice. NO operator notification until the
#                                 on-chain watcher confirms payment (CP3).
# GET  /api/crypto/invoice/{oid} → poll status for the owning user.
# Identity is ALWAYS re-derived from signed initData here — never the uid display
# gate. The amount is computed server-side; the client's price is never trusted.

def _pick_unique_crypto_amount(base_usdt: float, reserved: set) -> float:
    """Round the base amount UP to the nearest step (never undercharge), then
    bump by one step until it isn't already reserved by another open invoice —
    so an incoming transfer amount maps to exactly one order."""
    step = CRYPTO_AMOUNT_STEP if CRYPTO_AMOUNT_STEP > 0 else 0.01
    amt = round(math.ceil(base_usdt / step) * step, 6)
    guard = 0
    while round(amt, 6) in reserved and guard < 10000:
        amt = round(amt + step, 6)
        guard += 1
    return round(amt, 6)


def _crypto_order_payload(data: dict, uid: int, user: dict, oid: str, total_aed: float) -> dict:
    """Server-trusted snapshot of the order, stored on the invoice so a confirmed
    payment can be promoted to a real order (CP3) without trusting a client
    "paid" claim. Identity comes from initData; the rest mirrors /api/order."""
    return {
        "order_id": oid,
        "items": data.get("items", []),
        "phone": data.get("phone", "—"),
        "address": data.get("address", "—"),
        "address_label": data.get("address_label", ""),
        "gmap_link": data.get("gmap_link", ""),
        "is_gps": data.get("is_gps", False),
        "location": data.get("location", {}),
        "tip": data.get("tip", 0),
        "total": total_aed,
        # офис доопределит сервер по координатам при промоушене инвойса
        "office_id": data.get("office_id", ""),
        "office_name": data.get("office_name", ""),
        "comment": data.get("comment", ""),
        "lang": data.get("lang", "ru"),
        "customer_id": uid,
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("username", "—"),
        "language_code": user.get("language_code", ""),
        # Referral/source info collected pre-payment (crypto auto-verify path) — rides
        # along on the invoice snapshot so the operator still sees where the customer
        # came from on the confirmed, auto-verified order.
        "verify_source": data.get("verify_source", ""),
        "verify_source_detail": data.get("verify_source_detail", ""),
        "verify_recommender_name": data.get("verify_recommender_name", ""),
        "verify_recommender_phone": data.get("verify_recommender_phone", ""),
    }


def _crypto_invoice_response(doc: dict) -> web.Response:
    return web.json_response({
        "ok": True,
        "order_id": doc.get("order_id"),
        "address": doc.get("address"),
        "asset": doc.get("asset", "USDT"),
        "network": doc.get("network", "TRC-20"),
        "amount_usdt": doc.get("amount_usdt"),
        "amount_aed": doc.get("amount_aed"),
        "fee_pct": doc.get("fee_pct", CRYPTO_FEE_PCT),
        "required_confirmations": doc.get("required_confirmations", CRYPTO_REQUIRED_CONF),
        "expires_at": doc.get("expires_at_ms"),
        "status": doc.get("status", "waiting"),
    }, headers=CORS_HEADERS)


# ── Server-authoritative catalog pricing (F4) ─────────────────────────────────
# Prices live in catalog.json (owner-editable at runtime). We recompute each
# crypto order's goods total here from item id + qty so the customer pays exactly
# what the order is worth at current prices — the client's claimed prices/total
# are never trusted for the amount we credit on-chain.
_CATALOG_FILE = STATIC_DIR / "catalog.json"
_catalog_cache: dict = {"mtime": 0.0, "by_id": {}}

def _load_catalog_by_id() -> dict:
    """{id: product} from catalog.json, re-read only when the file mtime changes
    (so owner price edits take effect without a restart)."""
    try:
        mtime = _CATALOG_FILE.stat().st_mtime
    except OSError:
        return _catalog_cache["by_id"]
    if mtime != _catalog_cache["mtime"]:
        try:
            data = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
            _catalog_cache["by_id"] = {
                p["id"]: p for p in data if isinstance(p, dict) and "id" in p
            }
            _catalog_cache["mtime"] = mtime
        except Exception as e:
            log.warning(f"[crypto] catalog load failed: {e}")
    return _catalog_cache["by_id"]

def _catalog_unit_price(p: dict, pcs) -> float:
    """Unit price mirroring the frontend (index-6.html beerPrice): a 24-pack = double the
    12-pack minus a flat 5, snapped up to a clean 0/5 in our favour (95→185, 140→275); a
    12-pack = price. Derived from price — the stale price_12/price_24 fields are ignored."""
    base = float(p.get("price", 0) or 0)
    try:
        pcs = int(pcs)
    except (TypeError, ValueError):
        pcs = 0
    if pcs == 24 and base:
        return float(math.ceil((base * 2 - 5) / 5) * 5)
    return base

async def _recompute_order_total_aed(items: list, tip: float) -> float:
    """Authoritative order total (AED) = Σ catalog_unit_price×qty + tip. Unknown
    ids fall back to the client's line_total so a valid order is never rejected;
    known ids are always priced from the catalog (tamper-proof)."""
    catalog = await asyncio.to_thread(_load_catalog_by_id)
    subtotal = 0.0
    for it in (items or []):
        try:
            qty = int(it.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        p = catalog.get(it.get("id"))
        if p:
            subtotal += _catalog_unit_price(p, it.get("pcs")) * qty
        else:
            try:
                subtotal += float(it.get("line_total", 0) or 0)
            except (TypeError, ValueError):
                pass
    try:
        tip = float(tip or 0)
    except (TypeError, ValueError):
        tip = 0.0
    return round(subtotal + tip, 2)


async def handle_crypto_invoice_create(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    if not CRYPTO_REAL_MODE:
        return web.json_response({"ok": False, "error": "crypto_unavailable"}, status=503, headers=CORS_HEADERS)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    uid = user.get("id")

    # Re-enforce the rollout gate server-side (the /api/me flag is display-only).
    if not _crypto_enabled_for(uid):
        return web.json_response({"error": "crypto_not_enabled"}, status=403, headers=CORS_HEADERS)
    try:
        if await db.is_banned(uid):
            return web.json_response({"error": "banned"}, status=403, headers=CORS_HEADERS)
    except Exception as e:
        log.warning(f"[crypto] ban check failed: {e}")

    oid = (data.get("order_id") or "").strip() or _new_order_id()
    # F4: never trust the client's `total`. Recompute the goods total from each
    # item's id + qty against the server-authoritative catalog (catalog.json).
    total_aed = await _recompute_order_total_aed(data.get("items", []), data.get("tip", 0))
    if total_aed <= 0:
        return web.json_response({"error": "bad_total"}, status=400, headers=CORS_HEADERS)

    now_ms = int(time.time() * 1000)
    existing = await db.get_crypto_invoice(oid)
    # The client is allowed to re-send its own order id so retries stay idempotent
    # (handled just below), but it must never name an id that already belongs to a
    # placed order — promoting that invoice would upsert straight over it.
    if not existing and await db.get_order(oid):
        log.warning(f"[crypto] uid={uid} asked for an invoice on existing order {oid}")
        return web.json_response({"error": "order_conflict"}, status=409, headers=CORS_HEADERS)
    if existing:
        # Never reveal or mutate another customer's invoice for this order id.
        if existing.get("customer_id") != uid:
            return web.json_response({"error": "order_conflict"}, status=409, headers=CORS_HEADERS)
        est = existing.get("status", "waiting")
        # Once confirmed/paid the invoice is locked: return it unchanged so the
        # amount keeps matching the transfer that paid it.
        if est in ("confirmed", "paid"):
            return _crypto_invoice_response(existing)
        # Still genuinely open within its window → idempotent: same invoice.
        if est == "waiting" and existing.get("expires_at_ms", 0) > now_ms:
            return _crypto_invoice_response(existing)
        # Otherwise expired / past TTL → fall through and re-issue in place.

    expires_ms = now_ms + CRYPTO_TTL_MIN * 60 * 1000
    # Customer pays the goods total converted to USDT at a fixed merchant rate
    # (CRYPTO_AED_PER_USDT AED per 1 USDT). That rate already bakes in the
    # network/conversion margin — there is NO separate % fee. amount_aed stays the
    # goods value; the margin lives only in the USDT amount we credit on-chain.
    base_usdt = total_aed / CRYPTO_AED_PER_USDT if CRYPTO_AED_PER_USDT > 0 else 0.0
    # TEST override: a tiny fixed USDT so the whole on-chain flow can be tested for
    # cents. Restricted to AMBAR_CRYPTO_TEST_IDS — applies ONLY to those exact
    # accounts; everyone else (admins included) pays the real amount.
    if CRYPTO_TEST_USDT > 0 and uid in _CRYPTO_TEST_IDS:
        base_usdt = CRYPTO_TEST_USDT
        log.info(f"[crypto] TEST amount uid={uid}: {base_usdt} USDT (order {oid}; real total {total_aed} AED)")
    payload = _crypto_order_payload(data, uid, user, oid, total_aed)
    reserved = await db.reserved_crypto_amounts()

    # The USDT amount must be unique across open invoices — that is how an
    # incoming transfer maps to exactly one order. A partial-unique index on
    # amount_usdt enforces it; if two requests race onto the same amount the
    # loser gets "dup_amount", so we reserve it locally and bump to the next.
    for _attempt in range(50):
        amount = _pick_unique_crypto_amount(base_usdt, reserved)
        if existing:
            fields = {
                "status": "waiting",
                "amount_usdt": amount,
                "amount_aed": total_aed,
                "fee_pct": 0,
                "required_confirmations": CRYPTO_REQUIRED_CONF,
                "confirmations": 0,
                "txid": None,
                "created_at_ms": now_ms,
                "expires_at_ms": expires_ms,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "order_payload": payload,
            }
            res = await db.reissue_crypto_invoice(oid, fields)
            if res == "ok":
                log.info(f"[crypto] invoice #{oid} re-issued uid={uid} amount={amount} USDT (≈{total_aed} AED @ {CRYPTO_AED_PER_USDT:g} AED/USDT)")
                return _crypto_invoice_response({**existing, **fields})
        else:
            doc = {
                "order_id": oid,
                "customer_id": uid,
                "status": "waiting",
                "asset": "USDT", "chain": "TRON", "network": "TRC-20",
                "address": TRON_RECEIVE_ADDRESS,
                "amount_usdt": amount,
                "amount_aed": total_aed,
                "fee_pct": 0,
                "required_confirmations": CRYPTO_REQUIRED_CONF,
                "confirmations": 0,
                "txid": None,
                "created_at_ms": now_ms,
                "expires_at_ms": expires_ms,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "order_payload": payload,
            }
            res = await db.create_crypto_invoice(doc)
            if res == "ok":
                log.info(f"[crypto] invoice #{oid} uid={uid} amount={amount} USDT (≈{total_aed} AED @ {CRYPTO_AED_PER_USDT:g} AED/USDT)")
                return _crypto_invoice_response(doc)
            if res == "dup_order":
                # Another request created this order's invoice first — return it
                # if it's ours, else it's a genuine cross-customer id clash.
                other = await db.get_crypto_invoice(oid)
                if other and other.get("customer_id") == uid:
                    return _crypto_invoice_response(other)
                return web.json_response({"error": "order_conflict"}, status=409, headers=CORS_HEADERS)
        if res == "dup_amount":
            reserved.add(round(amount, 6))
            continue
        return web.json_response({"error": "create_failed"}, status=500, headers=CORS_HEADERS)

    # Exhausted the retry budget without finding a free amount (extremely rare).
    return web.json_response({"error": "amount_unavailable"}, status=503, headers=CORS_HEADERS)


async def handle_crypto_invoice_get(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    auth = request.headers.get("Authorization", "")
    init = auth[4:] if auth.startswith("tma ") else ""
    user = validate_init_data(init)
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    uid = user.get("id")
    oid = request.match_info.get("oid", "")
    inv = await db.get_crypto_invoice(oid)
    if not inv:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if inv.get("customer_id") != uid:
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    status = inv.get("status", "waiting")
    # Lazy expiry so the client sees `expired` even before the watcher sweeps it.
    if status == "waiting" and inv.get("expires_at_ms") and int(time.time() * 1000) > inv["expires_at_ms"]:
        await db.update_crypto_invoice(oid, status="expired")
        status = "expired"
    return web.json_response({
        "status": status,
        "txid": inv.get("txid"),
        "confirmations": inv.get("confirmations", 0),
        "required_confirmations": inv.get("required_confirmations", CRYPTO_REQUIRED_CONF),
    }, headers=CORS_HEADERS)


# ── Crypto receipt: branded PDF built from our own verified on-chain record ─────
# POST /api/crypto/receipt        → owner-authed; returns a short-lived signed
#                                   {exp, sig} the client turns into a GET URL.
# GET  /api/crypto/receipt/{oid}  → validates the signature → renders the PDF.
# No private data in the URL — the sig is a capability token (HMAC, 10-min TTL).
_RECEIPT_SECRET = (os.getenv("AMBAR_RECEIPT_SECRET") or BOT_TOKEN or "ambar-receipt").encode()


def _receipt_sig(oid: str, exp: int) -> str:
    return hmac.new(_RECEIPT_SECRET, f"{oid}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


async def handle_crypto_receipt_create(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    auth = request.headers.get("Authorization", "")
    user = validate_init_data(auth[4:] if auth.startswith("tma ") else "")
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    uid = user.get("id")
    try:
        data = await request.json()
    except Exception:
        data = {}
    oid = (data.get("order_id") or "").strip()
    order = await db.get_order(oid)
    if not order or order.get("customer_id") not in (uid, str(uid)):
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if order.get("payment_method") != "crypto" or not order.get("paid"):
        return web.json_response({"error": "not_crypto"}, status=400, headers=CORS_HEADERS)
    exp = int(time.time()) + 600  # 10-minute capability token
    return web.json_response({"order_id": oid, "exp": exp, "sig": _receipt_sig(oid, exp)},
                             headers=CORS_HEADERS)


async def handle_crypto_receipt_pdf(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    oid = request.match_info.get("oid", "")
    try:
        exp = int(request.query.get("exp", "0"))
    except ValueError:
        exp = 0
    sig = request.query.get("sig", "")
    if not oid or exp < int(time.time()) or not hmac.compare_digest(sig, _receipt_sig(oid, exp)):
        return web.Response(status=403, text="link expired", headers=CORS_HEADERS)
    order = await db.get_order(oid)
    if not order:
        return web.Response(status=404, text="not found", headers=CORS_HEADERS)
    try:
        from crypto_receipt import build_receipt
        pdf = build_receipt(order, to_address=TRON_RECEIVE_ADDRESS,
                            from_address=order.get("crypto_from") or "")
    except ImportError:
        log.warning("[receipt] fpdf2 not installed on this host — run: pip install fpdf2")
        return web.json_response({"error": "pdf_unavailable"}, status=503, headers=CORS_HEADERS)
    except Exception as e:
        log.error(f"[receipt] build failed for {oid}: {e}")
        return web.json_response({"error": "build_failed"}, status=500, headers=CORS_HEADERS)
    headers = dict(CORS_HEADERS)
    headers["Content-Type"] = "application/pdf"
    headers["Content-Disposition"] = f'inline; filename="AMBAR-{oid}.pdf"'
    return web.Response(body=pdf, headers=headers)


# ── Crypto watcher: confirm on-chain payments + promote to real orders ─────────
# Background task (started in on_startup). Read-only against the chain via
# tron.py — it never holds keys. Crediting happens ONLY on confirmed
# (irreversible) transfers, matched to an open invoice by its unique USDT amount,
# and is idempotent: a txid binds to exactly one invoice and each invoice is
# promoted to a real order at most once.

async def _crypto_promote_invoice(inv: dict, txid: str, amount) -> None:
    """Promote a confirmed invoice to a real order via _finalize_accepted_order,
    using the identity + order snapshot captured when the invoice was created."""
    p = inv.get("order_payload") or {}
    uid = inv.get("customer_id") or p.get("customer_id")
    user = {
        "id": uid,
        "first_name": p.get("first_name", ""),
        "last_name": p.get("last_name", ""),
        "username": p.get("username", "—"),
        "language_code": p.get("language_code", ""),
    }
    await _finalize_accepted_order(
        p, user, inv["order_id"],
        prepaid={"method": "USDT · TRC-20", "txid": txid,
                 "amount_usdt": amount if amount is not None else inv.get("amount_usdt")},
    )


async def _crypto_try_confirm(inv: dict, transfer: dict) -> None:
    """Credit one confirmed transfer to its matching invoice, exactly once."""
    oid  = inv["order_id"]
    txid = (transfer.get("txid") or "").strip()
    amt  = transfer.get("amount")
    if not txid:
        return
    if CRYPTO_WATCH_DRYRUN:
        log.info(f"[crypto-watch][DRYRUN] would credit #{oid} ← {amt} USDT txid={txid} "
                 f"(no status change, no order, no messages)")
        return
    # 1) Bind txid → this invoice, exactly once. A txid already bound elsewhere,
    #    or a different txid already on this invoice, means do NOT credit.
    if not await db.claim_crypto_txid(oid, txid):
        log.warning(f"[crypto-watch] txid {txid} will not bind to #{oid} — skip "
                    f"(possible double-spend / mismatch)")
        return
    # 2) Flip to confirmed (logged once).
    conf = inv.get("required_confirmations", CRYPTO_REQUIRED_CONF)
    if await db.mark_crypto_confirmed(oid, conf):
        log.info(f"[crypto-watch] #{oid} CONFIRMED ← {amt} USDT txid={txid}")
    # 3) Promote to a real order, exactly once. Fail-closed: the promotion gate is
    #    stamped BEFORE building the order, so a crash never double-creates.
    if await db.claim_crypto_promotion(oid):
        try:
            await _crypto_promote_invoice(inv, txid, amt)
            log.info(f"[crypto-watch] #{oid} promoted to order")
        except Exception as e:
            log.error(f"[crypto-watch] #{oid} promotion FAILED after confirm "
                      f"(manual recovery; payload+txid stored on invoice): {e}")


async def _crypto_watch_tick() -> None:
    """One polling cycle: retry stuck promotions, expire stale invoices, then
    match newly confirmed transfers to open invoices by exact amount."""
    now_ms = int(time.time() * 1000)

    # (a) Retry any invoice confirmed but not yet promoted (e.g. a restart landed
    #     in the confirm→promote window). Self-healing.
    for inv in await db.list_confirmed_unpromoted_crypto_invoices():
        if CRYPTO_WATCH_DRYRUN:
            log.info(f"[crypto-watch][DRYRUN] would re-promote confirmed #{inv['order_id']}")
            continue
        if await db.claim_crypto_promotion(inv["order_id"]):
            try:
                await _crypto_promote_invoice(inv, inv.get("txid") or "", inv.get("amount_usdt"))
                log.info(f"[crypto-watch] #{inv['order_id']} promoted (recovery)")
            except Exception as e:
                log.error(f"[crypto-watch] #{inv['order_id']} recovery promotion failed: {e}")

    # (b) Expire stale WAITING invoices so their reserved amount frees up. A
    #     'detected' invoice is locked to an on-chain tx and left to confirm.
    live = []
    for inv in await db.list_open_crypto_invoices():
        exp = inv.get("expires_at_ms", 0)
        if inv.get("status") == "waiting" and exp and now_ms > exp:
            await db.update_crypto_invoice(inv["order_id"], status="expired")
            log.info(f"[crypto-watch] #{inv['order_id']} expired (unpaid)")
        else:
            live.append(inv)
    if not live:
        return

    # (c) Fetch confirmed incoming USDT since the oldest live invoice, then match
    #     by exact amount. only_confirmed=True ⇒ irreversible transfers only.
    since_ms = min(int(i.get("created_at_ms", now_ms)) for i in live)
    transfers = await get_incoming_usdt(TRON_RECEIVE_ADDRESS, since_ms, only_confirmed=True)
    if not transfers:
        return
    by_amount = {round(float(i.get("amount_usdt", 0)), 6): i for i in live}
    for tr in transfers:
        try:
            amt = round(float(tr.get("amount", 0)), 6)
        except (TypeError, ValueError):
            continue
        inv = by_amount.get(amt)
        if inv:
            await _crypto_try_confirm(inv, tr)


async def _crypto_watch_loop(app) -> None:
    """Long-running poller; cancelled on shutdown."""
    if not CRYPTO_REAL_MODE:
        log.info("[crypto-watch] disabled (CRYPTO_REAL_MODE off — no receive address / API key)")
        return
    log.info(f"[crypto-watch] started · interval={CRYPTO_WATCH_INTERVAL_SEC}s · "
             f"dryrun={CRYPTO_WATCH_DRYRUN} · addr={TRON_RECEIVE_ADDRESS[:6]}…")
    await asyncio.sleep(3)  # let DB + indexes settle after startup
    while True:
        try:
            await _crypto_watch_tick()
        except asyncio.CancelledError:
            log.info("[crypto-watch] stopped")
            raise
        except Exception as e:
            log.warning(f"[crypto-watch] tick error: {e}")
        await asyncio.sleep(CRYPTO_WATCH_INTERVAL_SEC)


# ── POST /api/cancel-order ────────────────────────────────────────────────────
async def handle_cancel_order(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid      = user.get("id")
    order_id = data.get("order_id", "").strip()
    reason   = data.get("reason", "").strip()
    comment  = data.get("comment", "").strip()

    if not order_id:
        return web.json_response({"error": "missing order_id"}, status=400, headers=CORS_HEADERS)

    order = await db.get_order(order_id)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)

    # Ownership check (handle int vs str customer_id)
    if str(order.get("customer_id")) != str(uid):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    status = order.get("status", "")
    if status not in ("pending", "confirmed", "approved"):
        return web.json_response({"error": "not_cancellable", "status": status}, status=409, headers=CORS_HEADERS)

    await db.update_order(order_id,
        status="cancelled",
        cancel_reason=reason,
        cancel_comment=comment,
        cancelled_at=datetime.now(timezone.utc).isoformat(),
    )

    # Update original order messages with cancelled status
    op_msg_ids = order.get("op_msg_ids", {})
    user_name = order.get("customer_name", "—")
    username  = order.get("username", "—")
    dismiss_kb = {"inline_keyboard": [[{"text": "✅ Просмотрено", "callback_data": "delmsg"}]]}
    if op_msg_ids:
        # Build cancelled order card
        def _esc(t):
            return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        cancel_lines = [
            f"🚫 <b>ЗАКАЗ #{order_id} — ОТМЕНЁН КЛИЕНТОМ</b>",
            "",
            f"🏢 Офис: <b>{_esc(order.get('office_name', '—'))}</b>",
            "",
        ]
        gmap = order.get("gmap_link", "")
        addr = order.get("address", "—")
        if gmap:
            cancel_lines.append(f"🏠 Адрес: {_esc(addr)}" if addr and addr != "GPS" and addr != "—" else "🏠 Адрес: GPS")
            cancel_lines.append(f"Google Maps: {gmap}")
        else:
            cancel_lines.append(f"🏠 Адрес: {_esc(addr)}")
        cancel_lines.append("")
        cancel_lines.append("🛒 <b>Позиции:</b>")
        for item in order.get("items", []):
            lt = item.get("line_total", item["price"] * item["qty"])
            cancel_lines.append(f"  • {_esc(item['name'])} ×{item['qty']} = {lt} AED")
        cancel_lines.append("")
        cancel_lines.append(f"💰 <b>Итого: {order.get('total', 0)} AED</b>")
        cancel_lines.append("")
        cancel_lines.append(f"👤 {_esc(user_name)} (@{_esc(username)}, ID: <code>{uid}</code>)")
        cancel_lines.append(f"📋 Причина: {_esc(reason or '—')}")
        if comment:
            cancel_lines.append(f"💬 Комментарий: {_esc(comment)}")
        cancel_card = "\n".join(cancel_lines)
        for op_id_str, msg_id in op_msg_ids.items():
            try:
                await tg_edit(OPERATOR_BOT_TOKEN, int(op_id_str), msg_id, cancel_card, reply_markup=dismiss_kb)
            except Exception as e:
                log.error(f"Edit cancel card {op_id_str}: {e}")
    else:
        # Fallback: send separate cancel notification if no stored message IDs
        op_text = (
            f"🚫 *ЗАКАЗ #{order_id} ОТМЕНЁН КЛИЕНТОМ*\n\n"
            f"👤 *{user_name}* (@{username}, ID: `{uid}`)\n\n"
            f"📋 *Причина:* {reason or '—'}"
            + (f"\n💬 *Комментарий:* {comment}" if comment else "")
        )
        for op_id in OPERATOR_IDS:
            try:
                await tg_send(OPERATOR_BOT_TOKEN, op_id, op_text, reply_markup=dismiss_kb)
            except Exception as e:
                log.error(f"Operator cancel notify {op_id}: {e}")

    # Update the customer's live status msg → "cancelled"
    cust_msg_id = order.get("customer_msg_id") or (order.get("customer_msg_ids") or [None])[0]
    if cust_msg_id:
        try:
            updated = await db.get_order(order_id)
            await tg_edit(BOT_TOKEN, uid, cust_msg_id,
                          render_customer_card(updated, updated.get("lang", "ru")),
                          parse_mode="Markdown")
        except Exception as e:
            log.error(f"Customer cancel edit: {e}")

    log.info(f"[cancel-order] #{order_id} user={uid} reason={reason!r}")

    # Owner notification — orders.cancelled
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "orders.cancelled",
            f"🚫 *Клиент отменил заказ #{order_id}*\n"
            f"Клиент: {user_name}\n"
            f"Причина: {reason or '—'}"
            + (f"\nКомментарий: {comment}" if comment else "")
        )
    except Exception as e:
        log.error(f"[owner-notif] orders.cancelled failed: {e}")

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


# ── GET /api/me ───────────────────────────────────────────────────────────────
# Privileged IDs stored server-side only — never sent to the client
_FOUNDER_ID = 7865205960
# ÉLITE premium tier: up to 10 cards, serial "N° XX / 10".
# Order matters — list index + 1 is the card number.
_PREMIUM_IDS = [686932322, 1459370603]
assert len(_PREMIUM_IDS) <= 10, "ÉLITE premium is capped at 10 cards"
# Worldwide Premium tier: up to 100 cards, serial "N° XXX / 100".
# Order matters — list index + 1 is the card number. Append new holders to
# the end so their cards get the next sequential number (003, 004, ...).
_WORLDWIDE_IDS = [
    323390062,    # card #001
    7236406959,   # card #002
    1154453658,   # card #003
]
assert len(_WORLDWIDE_IDS) <= 100, "Worldwide PREMIUM is capped at 100 cards"

async def handle_me(request: web.Request) -> web.Response:
    """Returns ban status, referral points, and card type (founder/premium/standard)."""
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    # Identity comes from signed initData, never from a ?uid= the caller picked.
    # This used to trust the query param, which made the whole profile — ban
    # state, points, card tier, verification — readable for any Telegram id by
    # anyone with curl, and let ?lc= write to any user's document.
    auth = request.headers.get("Authorization", "")
    user = validate_init_data(auth[4:]) if auth.startswith("tma ") else None
    if not user or not user.get("id"):
        return web.json_response({"error": "unauthorized"}, status=401, headers=CORS_HEADERS)
    uid = int(user["id"])
    lc = (request.query.get("lc", "") or user.get("language_code", "")).strip()
    if lc:
        try:
            await db.set_user_field(uid, language_code=lc)
        except Exception:
            pass
    try:
        user_doc = await db.get_user(uid)
        banned = user_doc.get("is_banned", False) if user_doc else False
        ref_points = user_doc.get("referral_points", 0) if user_doc else 0
    except Exception as e:
        log.warning(f"ban check /api/me failed: {e}")
        banned = False
        ref_points = 0

    # Determine card type server-side (IDs never leave the server)
    if uid == _FOUNDER_ID:
        card_type = "founder"
        premium_index = -1
    elif uid in _PREMIUM_IDS:
        card_type = "premium"
        premium_index = _PREMIUM_IDS.index(uid)
    elif uid in _WORLDWIDE_IDS:
        card_type = "worldwide"
        premium_index = _WORLDWIDE_IDS.index(uid)
    else:
        card_type = "standard"
        premium_index = -1


    demo = user_doc.get("demo", False) if user_doc else False

    # Verification status — referrals no longer skip the flow; they must
    # submit the form just like any other first-time user. The referrer info
    # is passed along to the operator as a hint when the form is submitted.
    verified = _is_vetted(user_doc)
    verify_requested = user_doc.get("verify_requested", False) if user_doc else False
    verify_declined = user_doc.get("verify_declined", False) if user_doc else False

    # Server-authoritative "wall must be shown" flag: any order still marked
    # pending_verification means the user placed an order but hasn't submitted
    # the verification form yet. Survives across app restarts regardless of
    # whether the client's localStorage was cleared (Telegram desktop sometimes
    # wipes it between sessions).
    verify_pending = False
    if not verified and not verify_requested and not verify_declined:
        try:
            _user_orders = await db.get_user_orders(uid)
            verify_pending = any(o.get("pending_verification") for o in _user_orders)
        except Exception as e:
            log.warning(f"verify_pending check failed for uid={uid}: {e}")

    log.info(f"[me] uid={uid} banned={banned} card={card_type} demo={demo} verified={verified} pending={verify_pending}")
    return web.json_response({
        "banned": banned,
        "referral_points": ref_points,
        "card_type": card_type,
        "premium_index": premium_index,
        "demo": demo,
        "verified": verified,
        # Подтверждённый номер: пришёл контактом в бота от самого Telegram.
        # Фронт по нему решает, разблокировать ли ручной ввод и кнопку заказа.
        "phone_verified": (user_doc or {}).get("phone_verified") or "",
        "verify_requested": verify_requested,
        "verify_declined": verify_declined,
        "verify_pending": verify_pending,
        # Staged-rollout display gate for crypto payments (see _crypto_enabled_for).
        "crypto_enabled": _crypto_enabled_for(uid),
        # Whether the server has a real receive wallet + watcher (address+key set).
        # The frontend uses this to switch off its client-side demo automatically,
        # so there is no way to half-activate (demo stays on until real mode is on).
        "crypto_real_mode": CRYPTO_REAL_MODE,
        # В ДОЛГ (pay-later) programme: display gate + current balance. The order
        # endpoint re-checks the whitelist server-side, so this is cosmetic.
        "debt_allowed": bool(user_doc.get("debt_allowed")) if user_doc else False,
        "debt": round(float(user_doc.get("debt") or 0), 2) if user_doc else 0,
    }, headers=CORS_HEADERS)


# ── POST /api/verify-request ──────────────────────────────────────────────────
async def handle_verify_request(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid = user.get("id")
    source = (data.get("source", "") or "").strip()
    source_detail = (data.get("source_detail", "") or "").strip()
    recommender_name = (data.get("recommender_name", "") or "").strip()
    recommender_phone = (data.get("recommender_phone", "") or "").strip()

    if not source:
        return web.json_response({"error": "missing source"}, status=400, headers=CORS_HEADERS)

    try:
        await db.submit_verify_request(uid, recommender_name, recommender_phone,
                                        source=source, source_detail=source_detail)
    except Exception as e:
        log.error(f"[verify-request] submit_verify_request failed uid={uid}: {e}")

    # Find the pending-verification order and send combined notification. Every DB read
    # below is wrapped: a held order is INVISIBLE to the operator until this handler
    # fires, so nothing here may abort the function before the sends.
    try:
        user_orders = await db.get_user_orders(uid)
    except Exception as e:
        log.error(f"[verify-request] get_user_orders failed uid={uid}: {e}")
        user_orders = []
    pending = [o for o in user_orders if o.get("pending_verification")]
    if not pending:
        log.warning(f"[verify-request] uid={uid} submitted verification but NO held order found "
                    f"(total orders={len(user_orders)}) — operator gets no order message this call")
    try:
        user_doc = await db.get_user(uid) or {}
    except Exception as e:
        log.error(f"[verify-request] get_user failed uid={uid}: {e}")
        user_doc = {}
    inv_op = user_doc.get("invited_by_operator")
    inv_at = user_doc.get("invited_at")
    def _fmt_inv_at(t):
        if not t: return ""
        try:
            dt = datetime.fromisoformat(str(t).replace("Z","+00:00")) if isinstance(t, str) else t
            return dt.astimezone(DUBAI_TZ).strftime('%d.%m.%Y %H:%M')
        except Exception:
            return ""
    joined_str = _fmt_inv_at(inv_at)
    for order in pending:
        oid = order.get("order_id", "?")
        saved_op_text = order.get("op_text", "")
        # Bulletproof defaults — a held order is INVISIBLE to the operator until this
        # send fires, so even if the rich build below throws, a usable notification
        # (order id, source, verify buttons) still goes out.
        op_kb = {"inline_keyboard": [
            [
                {"text": "✅ Верифицировать", "callback_data": f"verify_{uid}"},
                {"text": "❌ Не верифицировать", "callback_data": f"decverify_{oid}_{uid}"},
            ],
            [{"text": "👤 Клиент", "callback_data": f"client_{oid}_{uid}"}],
        ]}
        _src_fb = {"friend": "Знакомый", "operator": "Оператор", "other": "Другое"}.get(source, source or "—")
        combined = (f"🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ!</b> 🔴🔴🔴\n📋 Источник: <b>{_src_fb}</b>\n\n"
                    f"🆕 <b>ЗАКАЗ #{oid}</b>")
        try:
            # Build source info line
            source_labels = {
                "friend": "👥 Знакомый",
                "operator": "📞 Оператор",
                "other": "💬 Другое",
            }
            src_line = source_labels.get(source, source)
            src_extra = ""
            if source == "friend" and recommender_name:
                src_extra = f"\n👤 {recommender_name}" + (f" — {recommender_phone}" if recommender_phone else "")
            elif source_detail:
                src_extra = f"\n💬 {source_detail}"

            # Attribution hints (multiple can apply — show whichever are set)
            hints = []
            order_ref_username = order.get("referrer_username")
            if order.get("referred_by") and order_ref_username:
                hints.append(f"👥 Пригласил клиент — @{order_ref_username}")
            if inv_op is not None and inv_op > 0:
                h = f"🔗 По ссылке оператора <code>{inv_op}</code>"
                if joined_str: h += f" · вступил {joined_str}"
                hints.append(h)
            elif inv_op == 0:
                h = "🔗 По общей ссылке операторов"
                if joined_str: h += f" · вступил {joined_str}"
                hints.append(h)

            # Banner title — test accounts get green TEST banner
            if uid in _TEST_ACCOUNTS:
                bq_alert = "<blockquote>🟢🟢🟢 <b>ТЕСТ (НЕ НАСТОЯЩИЙ ЗАКАЗ)</b> 🟢🟢🟢</blockquote>"
            else:
                if order.get("referred_by"):
                    banner_title = "<b>НОВЫЙ КЛИЕНТ — РЕФЕРАЛ</b>"
                elif inv_op is not None and inv_op > 0:
                    banner_title = "<b>НОВЫЙ КЛИЕНТ — ССЫЛКА ОПЕРАТОРА</b>"
                elif inv_op == 0:
                    banner_title = "<b>НОВЫЙ КЛИЕНТ — ОБЩАЯ ССЫЛКА</b>"
                else:
                    banner_title = "<b>НОВЫЙ КЛИЕНТ!</b>"
                hints_str = ("\n" + "\n".join(hints)) if hints else ""
                bq_alert = f"<blockquote>🔴🔴🔴 {banner_title} 🔴🔴🔴{hints_str}\n📋 Источник: <b>{src_line}</b>{src_extra}</blockquote>"

            # Prepaid (crypto) orders carry a "ОПЛАЧЕНО ОНЛАЙН" blockquote in op_text;
            # the strip below removes ALL blockquotes, so re-inject it — the operator
            # must still see the order is settled and NOT collect cash on delivery.
            paid_banner = ""
            if order.get("payment_method") == "crypto" and order.get("paid"):
                paid_banner = (
                    f"<blockquote>💳 <b>ОПЛАЧЕНО ОНЛАЙН · USDT · TRC-20</b>\n"
                    f"Сумма: {order.get('crypto_amount_usdt')} USDT</blockquote>\n\n"
                )
            elif order.get("payment_method") == "debt":
                # Same for В ДОЛГ — the strip would erase the debt banner.
                _cur_debt = 0.0
                try:
                    _cur_debt = await db.get_debt(uid)
                except Exception:
                    pass
                try:
                    _after = round(_cur_debt + float(order.get("total") or 0), 2)
                except (TypeError, ValueError):
                    _after = _cur_debt
                paid_banner = (
                    "<blockquote>📒 <b>ОПЛАТА: В ДОЛГ</b>\n"
                    f"💰 Текущий долг: <b>{_fmt_aed(_cur_debt)} AED</b>\n"
                    f"➕ Этот заказ: {_fmt_aed(order.get('total'))} AED → долг станет <b>{_fmt_aed(_after)} AED</b>\n"
                    "☑️ Наличные НЕ брать — сумма записывается в долг</blockquote>\n\n"
                )

            if saved_op_text:
                # Strip old banners (both Markdown and HTML variants)
                import re as _re
                saved_op_text = _re.sub(r'<blockquote>.*?</blockquote>\s*', '', saved_op_text, flags=_re.DOTALL)
                for prefix in ["🔴 *⚠️ ПЕРВЫЙ ЗАКАЗ — новый клиент!*\n\n",
                               "🔴 *⚠️ НОВЫЙ КЛИЕНТ РЕФЕРАЛ*\n",
                               "🔴🔴🔴 *НОВЫЙ КЛИЕНТ!* 🔴🔴🔴\n\n",
                               "🔴🔴🔴 *НОВЫЙ КЛИЕНТ — РЕФЕРАЛ* 🔴🔴🔴\n"]:
                    saved_op_text = saved_op_text.replace(prefix, "")
                combined = bq_alert + "\n\n" + paid_banner + saved_op_text.strip()
            else:
                combined = bq_alert + "\n\n" + paid_banner + f"🆕 <b>ЗАКАЗ #{oid}</b>"
        except Exception as e:
            log.error(f"[verify-request] message build failed for #{oid} — sending fallback: {e}")
        op_msg_ids = {}
        for op_id in OPERATOR_IDS:
            try:
                resp = await tg_send(OPERATOR_BOT_TOKEN, op_id, combined, parse_mode="HTML", reply_markup=op_kb)
                if resp and resp.get("ok") and resp.get("result"):
                    op_msg_ids[str(op_id)] = resp["result"]["message_id"]
                else:
                    log.error(f"Verify+order notify {op_id} REJECTED for #{oid}: {resp}")
            except Exception as e:
                log.error(f"Verify+order notify {op_id}: {e}")
        if op_msg_ids:
            await db.update_order(oid, op_msg_ids=op_msg_ids)
        elif OPERATOR_IDS:
            log.error(f"[verify-request] #{oid} reached NO operator — check OPERATOR_BOT_TOKEN / OPERATOR_IDS")
            try:
                from owner_routes import notify_owners_force
                await notify_owners_force(
                    "orders.opFail",
                    f"🛑 *Заказ #{oid} (после верификации) НЕ доставлен ни одному оператору!*\n"
                    f"Он висит в списке «Новые заказы», но пуш не дошёл.\n"
                    f"Проверьте операторский бот.")
            except Exception as e:
                log.error(f"[owner-notif] opFail alert failed: {e}")
        # Clear the pending flag
        await db.update_order(oid, pending_verification=False)
        # (The owner "new order" ping already fired at placement — see
        #  _finalize_accepted_order. No deferred re-send here, or the owner gets it twice.)

    log.info(f"[verify-request] uid={uid} source={source} detail={source_detail or recommender_name}")

    # Owner notification — customers.verify (will be replaced on approve/decline)
    try:
        from owner_routes import notify_owners
        src_lbl = {"friend":"от друга","operator":"от оператора"}.get(source, source or "—")
        owner_msg_ids = await notify_owners(
            "customers.verify",
            f"📝 *Запрос верификации*\n"
            f"Клиент: {user.get('first_name','')} {user.get('last_name','')}\n"
            f"@{user.get('username','—')}\n"
            f"ID: `{uid}`\n"
            f"Источник: {src_lbl}"
            + (f"\nРекомендатель: {recommender_name} ({recommender_phone})" if recommender_name else "")
        )
        if owner_msg_ids:
            await db.set_user_field(uid, verify_owner_msg_ids=owner_msg_ids)
    except Exception as e:
        log.error(f"[owner-notif] customers.verify failed: {e}")

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


# ── GET /api/orders ───────────────────────────────────────────────────────────
async def handle_orders(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)

    user = validate_init_data(auth[4:])
    if not user:
        log.warning("initData invalid — returning [] (dev mode?)")
        return web.json_response({"orders": []}, headers=CORS_HEADERS)

    uid        = user.get("id")
    user_orders = await db.get_user_orders(uid)
    # Sanitise any raw datetime objects that would break JSON serialisation
    for o in user_orders:
        for k, v in o.items():
            if isinstance(v, datetime):
                o[k] = v.isoformat()
    log.info(f"[orders] user={uid} count={len(user_orders)}")
    return web.json_response({"orders": user_orders}, headers=CORS_HEADERS)


# Complaint keywords — if a customer message contains any of these, fire
# support.complaint notification.  Stems are used so "верните"/"возврат" both match.
_COMPLAINT_KEYWORDS = [
    # ═══ RUSSIAN ═══
    # General dissatisfaction
    "плохо", "плохой", "плохая", "плохое",
    "ужас", "кошмар", "отвратительн",
    "разочарован", "недовол", "безобраз",
    "жалоба", "жалуюсь",
    "некачествен", "брак",
    # Returns / refunds
    "верните", "возврат", "вернуть", "деньги назад", "компенсац",
    # Delivery issues
    "долго ждал", "долго жд", "слишком долго",
    "опоздал", "опоздани", "задержк",
    "не привез", "не доставил", "забыли", "потерял",
    "не то привез", "перепутал", "не тот заказ", "чужой заказ",
    "не та бутылка", "не тот напиток",
    # Damaged / broken
    "разбит", "разбили", "разбитая", "битая",
    "треснул", "трещин",
    "течёт", "течет", "протекает", "протекло", "пролил", "разлил",
    "повреждён", "повреждена", "помят", "помятая",
    "испорчен",
    # Packaging / tampering
    "открыт", "вскрыт", "распечатан",
    "упаковк", "этикетк",
    "недолив", "не долили", "не полная",
    # Counterfeit / quality
    "подделка", "палёнка", "палёный", "палёнк", "фейк", "контрафакт",
    "мутн", "осадок",
    "выдохлось", "выдохш",
    "кислое", "скисло", "прокисло",
    "просрочен", "просрочк", "истёк", "истек", "срок годност",
    # Temperature
    "тёплый", "тёплая", "тёплое", "теплый", "теплая",
    "не охлажд", "не холодн", "нагрел",
    # Rudeness / service
    "хам", "грубо", "грубый", "нагл", "хамство",
    "обман", "мошен", "развод",
    "игнор", "не отвечает", "не перезвон",
    # ═══ ENGLISH ═══
    # General dissatisfaction
    "terrible", "horrible", "awful", "disgusting",
    "worst", "unacceptable", "disappointed", "disappointing",
    "ridiculous", "pathetic", "outrageous",
    "complaint", "complain",
    # Returns / refunds
    "refund", "money back", "compensat", "reimburse",
    "return it", "give me back", "want my money",
    # Delivery issues
    "too long", "waited too long", "still waiting",
    "late", "delayed", "never arrived", "not delivered",
    "forgot", "lost my order", "missing",
    "wrong order", "wrong bottle", "wrong item", "not what i ordered",
    "mixed up", "someone else",
    # Damaged / broken
    "broken", "smashed", "shattered", "cracked",
    "leaking", "leaked", "spilled",
    "damaged", "dented", "crushed",
    # Packaging / tampering
    "opened", "tampered", "unsealed",
    "packaging", "label",
    "half empty", "not full", "short pour",
    # Counterfeit / quality
    "fake", "counterfeit", "knockoff",
    "cloudy", "sediment",
    "flat", "gone off", "stale",
    "sour", "vinegar",
    "expired", "expiry", "out of date",
    # Temperature
    "warm", "not cold", "not chilled", "room temperature",
    # Rudeness / service
    "rude", "disrespectful", "unprofessional", "attitude",
    "scam", "fraud", "liar", "lying",
    "ignored", "no response", "no reply", "never called back",
    # ═══ SWEAR WORDS / AGGRESSIVE LANGUAGE ═══
    # Russian
    "блять", "блядь", "бляд", "сука", "сучк", "пизд", "пиздец",
    "хуй", "хуё", "хуе", "нахуй", "нахуя", "охуе",
    "ебан", "ебат", "ёбан", "заеб", "уёб", "уеб", "ёб твою",
    "мудак", "мудач", "дебил", "идиот", "кретин", "долбоёб", "долбоеб",
    "пошёл нах", "пошел нах", "иди нах",
    "говно", "дерьмо", "срань", "засранц",
    "урод", "тварь", "скотин", "падл",
    "наебалово", "наебал", "наёб", "наебк", "наебщик",
    "пиздёж", "пиздеж", "пиздабол", "пиздоболы",
    "кидалово", "кидал", "кинул", "развели", "лохотрон",
    "впарил", "втюхал", "наварил",
    # English
    "fuck", "fucked", "fucking", "wtf",
    "shit", "shitty", "bullshit",
    "ass", "asshole",
    "bitch", "bastard",
    "crap", "crappy",
    "damn", "dammit",
    "piss", "pissed",
    "idiot", "moron", "stupid",
]

# ── POST /api/support/send ────────────────────────────────────────────────────
async def handle_support_send(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid       = user.get("id")
    # Each message is relayed into the operators' Telegram — unthrottled it is a
    # spam cannon pointed at the team's chat.
    if _rate_limited("support", uid, 20, 60):
        log.warning(f"[rl] support flood from uid={uid}")
        return _too_many()
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    username  = user.get("username", "—")
    order_id  = data.get("order_id", "")
    text      = (data.get("text", "") or "").strip()
    reply_to_text = (data.get("reply_to_text", "") or "").strip()
    reply_to_role = data.get("reply_to_role", "")
    reply_to_ts   = data.get("reply_to_ts", "")

    try:
        if await db.is_banned(uid):
            return web.json_response({"error": "banned"}, status=403, headers=CORS_HEADERS)
    except Exception as e:
        log.warning(f"ban check failed: {e}")

    if not text:
        return web.json_response({"error": "empty message"}, status=400, headers=CORS_HEADERS)

    conv_key  = f"{uid}_{order_id}" if order_id else str(uid)
    server_ts = datetime.now(timezone.utc).isoformat()

    msg_doc = {"role": "user", "text": text, "ts": server_ts}
    if reply_to_text:
        msg_doc["reply_to_text"] = reply_to_text
        msg_doc["reply_to_role"] = reply_to_role
    await db.append_support_msg(conv_key, msg_doc)

    # Build context line
    if order_id and order_id != "general":
        context_line = f"📦 Заказ: `#{order_id}`"
    else:
        context_line = "📋 Общий вопрос"
    # Check user status
    status_tags = []
    highlight = False
    try:
        user_doc = await db.get_user(uid)
        if user_doc:
            if user_doc.get("is_banned"):
                status_tags.append("🚫 Забанен")
            if user_doc.get("verify_declined"):
                status_tags.append("❌ Верификация отклонена")
                highlight = True
            elif user_doc.get("verify_requested") and not _is_vetted(user_doc):
                status_tags.append("⏳ Ожидает верификации")
                highlight = True
            elif not _is_vetted(user_doc):
                status_tags.append("🔴 Не верифицирован")
    except Exception:
        pass
    if status_tags:
        joined = " | ".join(status_tags)
        status_line = (f"⚠️ *{joined}*\n" if highlight else f"{joined}\n")
    else:
        status_line = ""
    header = (
        f"{context_line}\n"
        f"👤 {user_name} (@{username}, ID: `{uid}`)\n"
        f"{status_line}\n"
        f"💬 {text}"
    )
    token = SUPPORT_BOT_TOKEN or BOT_TOKEN
    # If the support message references an order, add a URL button that
    # deep-links the operator bot straight into that order's card.
    op_kb = None
    if order_id and order_id != "general":
        op_bot_name = await _resolve_op_bot_username()
        if op_bot_name:
            op_kb = {"inline_keyboard": [[{
                "text": f"👁 Открыть заказ #{order_id}",
                "url": f"https://t.me/{op_bot_name}?start=order_{order_id}",
            }]]}
    for op_id in OPERATOR_IDS:
        try:
            # Look up fwd_msg_id of the message being replied to (for native Telegram reply)
            reply_msg_id = None
            if reply_to_ts:
                reply_msg_id = await db.get_support_fwd_id(conv_key, reply_to_ts, op_id)

            result = await tg_send(token, op_id, header, reply_markup=op_kb, reply_to_message_id=reply_msg_id)
            fwd_id = result.get("result", {}).get("message_id")
            if fwd_id:
                await db.save_support_map_entry(str(fwd_id), {
                    "user_id": uid, "conv_key": conv_key, "order_id": order_id
                })
                # Save mapping: this message's ts → fwd_id for this operator
                await db.save_support_fwd_id(conv_key, server_ts, op_id, fwd_id)
        except Exception as e:
            log.error(f"Support forward {op_id}: {e}")

    log.info(f"[support] user={uid} order={order_id}")

    try:
        from owner_routes import notify_owners, OWNER_BOT_TOKEN
        import re as _re
        ctx_lbl = f"заказ #{order_id}" if order_id and order_id != "general" else "общий вопрос"
        # Routing hint for the owner app: tapping the alert opens THIS conversation.
        _meta = {"conv_key": conv_key,
                 "order_id": order_id if order_id and order_id != "general" else ""}

        # Check for complaint keywords first
        text_lower = text.lower()
        matched = [kw for kw in _COMPLAINT_KEYWORDS
                   if _re.search(r'\b' + _re.escape(kw), text_lower)]

        text_new = (f"💬 *Новое обращение в поддержку*\n"
                    f"Клиент: {user_name} (@{username})\n"
                    f"Контекст: {ctx_lbl}\n"
                    f"_{text[:100]}_")

        if matched:
            # Build complaint message with highlighted keywords
            highlighted = text[:200]
            for kw in matched:
                highlighted = _re.sub(
                    r'\b(' + _re.escape(kw) + r'\w*)',
                    r"⟨ *\1* ⟩",
                    highlighted,
                    flags=_re.IGNORECASE,
                )
            text_complaint = (f"⚠️ *Жалоба — ключевые слова*\n"
                              f"Клиент: {user_name} (@{username})\n"
                              f"Контекст: {ctx_lbl}\n\n"
                              f"\"{highlighted}\"")

            # Dedup: users with both support.new + support.complaint ON
            # get only the complaint version (higher priority)
            complaint_subs = await db.get_owners_subscribed_to("support.complaint")
            new_subs = await db.get_owners_subscribed_to("support.new")
            complaint_set = set(complaint_subs)

            # Send complaint to complaint subscribers
            await db.insert_notification("support.complaint", text_complaint, meta=_meta)
            for oid in complaint_subs:
                try:
                    result = await tg_send(OWNER_BOT_TOKEN, oid, text_complaint, parse_mode="Markdown")
                    if not result or not result.get("ok"):
                        log.error(f"[owner-notif] support.complaint → {oid} TG error: {result}")
                except Exception as e:
                    log.error(f"[owner-notif] support.complaint → {oid} failed: {e}")

            # Send regular support.new only to those NOT getting complaint
            await db.insert_notification("support.new", text_new, meta=_meta)
            for oid in new_subs:
                if oid in complaint_set:
                    continue  # already got the complaint version
                try:
                    result = await tg_send(OWNER_BOT_TOKEN, oid, text_new, parse_mode="Markdown")
                    if not result or not result.get("ok"):
                        log.error(f"[owner-notif] support.new → {oid} TG error: {result}")
                except Exception as e:
                    log.error(f"[owner-notif] support.new → {oid} failed: {e}")
        else:
            # No trigger words — just send support.new normally
            await notify_owners("support.new", text_new, meta=_meta)
    except Exception as e:
        log.error(f"[owner-notif] support notification failed: {e}")

    return web.json_response({"ok": True, "ts": server_ts}, headers=CORS_HEADERS)


# ── POST /api/review ──────────────────────────────────────────────────────────
async def handle_review(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    data = await request.json()
    init_data = data.get("initData", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    uid      = user.get("id")
    order_id = data.get("order_id", "")
    score    = data.get("score", 0)
    comment  = data.get("comment", "").strip()
    if not order_id or not score:
        return web.json_response({"error": "missing fields"}, status=400, headers=CORS_HEADERS)
    order = await db.get_order(order_id)
    if not order or order.get("customer_id") != uid:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("review_score"):
        return web.json_response({"error": "already reviewed"}, status=409, headers=CORS_HEADERS)
    tags = data.get("tags", [])
    await db.update_order(order_id, review_score=int(score), review_comment=comment,
                          review_tags=tags, reviewed_at=datetime.now(timezone.utc).isoformat())
    # Notify operators about the review
    user_name = order.get("customer_name", "—")
    tag_labels = {"speed":"Быстрая доставка","courier":"Вежливый курьер","packaging":"Аккуратная упаковка","quality":"Качество товара"}
    stars = "⭐" * int(score)
    tags_line = ", ".join(tag_labels.get(t, t) for t in tags) if tags else ""
    op_text = (f"📝 *Отзыв на заказ #{order_id}*\n\n"
               f"👤 {user_name}\n"
               f"🏅 {stars} ({score}/5)"
               + (f"\n👍 {tags_line}" if tags_line else "")
               + (f"\n💬 _{comment}_" if comment else ""))
    dismiss_kb = {"inline_keyboard": [[{"text": "✅ Просмотрено", "callback_data": "delmsg"}]]}
    for op_id in OPERATOR_IDS:
        try:
            await tg_send(OPERATOR_BOT_TOKEN, op_id, op_text, reply_markup=dismiss_kb)
        except Exception as e:
            log.error(f"Review notify {op_id}: {e}")
    log.info(f"[review] #{order_id} uid={uid} score={score}")

    # Owner notifications — review event(s) based on score
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "reviews.any",
            f"📝 *Отзыв на заказ #{order_id}*\n"
            f"Клиент: {user_name}\n"
            f"Оценка: {stars} ({score}/5)"
            + (f"\nКомментарий: _{comment}_" if comment else "")
        )
        if int(score) <= 3:
            await notify_owners(
                "reviews.bad3",
                f"⚠️ *Плохой отзыв ({score}★)*\n"
                f"Заказ #{order_id} · {user_name}"
                + (f"\n_{comment}_" if comment else "")
            )
        if int(score) == 5:
            await notify_owners(
                "reviews.good5",
                f"⭐ *Отличный отзыв (5★)*\n"
                f"Заказ #{order_id} · {user_name}"
                + (f"\n_{comment}_" if comment else "")
            )
        if comment:
            await notify_owners(
                "reviews.comment",
                f"💬 *Отзыв с комментарием*\n"
                f"Заказ #{order_id} · {user_name} · {stars}\n"
                f"_{comment}_"
            )
    except Exception as e:
        log.error(f"[owner-notif] reviews.* failed: {e}")

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


# ── POST /api/review-skip ───────────────────────────────────────────────────
async def handle_review_skip(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    data = await request.json()
    user = validate_init_data(data.get("initData", ""))
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    uid = user.get("id")
    order_id = data.get("order_id", "")
    if not order_id:
        return web.json_response({"error": "missing order_id"}, status=400, headers=CORS_HEADERS)
    order = await db.get_order(order_id)
    if not order or order.get("customer_id") != uid:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    await db.update_order(order_id, review_skipped=True)
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


# ── GET /api/active-order ─────────────────────────────────────────────────────
async def handle_active_order(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    # Was ?uid= with no auth at all — anyone could read any customer's live
    # orders, including their delivery address and basket, by guessing ids.
    auth = request.headers.get("Authorization", "")
    user = validate_init_data(auth[4:]) if auth.startswith("tma ") else None
    if not user or not user.get("id"):
        return web.json_response({"error": "unauthorized"}, status=401, headers=CORS_HEADERS)
    uid = int(user["id"])
    orders = await db.get_active_orders(uid)
    if not orders:
        return web.json_response({"active": False, "orders": []}, headers=CORS_HEADERS)
    return web.json_response({
        "active": True,
        "orders": [{
            "order_id":     o.get("order_id"),
            "status":       o.get("status"),
            "confirmed_at": o.get("confirmed_at"),
            "eta":          o.get("eta"),
            "items":        o.get("items", []),
            "total":        o.get("total", 0),
            "address":      o.get("address", ""),
            "review_score": o.get("review_score"),
        } for o in orders],
    }, headers=CORS_HEADERS)


# Magic-byte sniffing for uploads. Only real raster images get a filename, and
# the extension is ours — SVG is deliberately absent, it is a script container.
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff",                    ".jpg"),
    (b"\x89PNG\r\n\x1a\n",               ".png"),
    (b"GIF87a",                          ".gif"),
    (b"GIF89a",                          ".gif"),
)


def _sniff_image_ext(blob: bytes) -> str | None:
    for magic, ext in _IMAGE_MAGIC:
        if blob.startswith(magic):
            return ext
    # WEBP and HEIC are container formats: "RIFF....WEBP" / "....ftypheic".
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    if blob[4:8] == b"ftyp" and blob[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return ".heic"
    return None


# ── POST /api/support/send-image ──────────────────────────────────────────────
async def handle_support_send_image(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    reader    = await request.multipart()
    init_data = order_id = caption = ""
    image_data = None
    image_ext  = ".jpg"

    async for part in reader:
        if   part.name == "initData":  init_data  = (await part.read()).decode()
        elif part.name == "order_id":  order_id   = (await part.read()).decode()
        elif part.name == "caption":   caption    = (await part.read()).decode()
        elif part.name == "image":
            image_data = await part.read()
            image_ext  = Path(part.filename or "photo.jpg").suffix or ".jpg"

    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)
    if not image_data:
        return web.json_response({"error": "no image"}, status=400, headers=CORS_HEADERS)
    if len(image_data) > 5 * 1024 * 1024:
        return web.json_response({"error": "file too large"}, status=400, headers=CORS_HEADERS)

    uid       = user.get("id")
    if _rate_limited("upload", uid, 12, 300):
        log.warning(f"[rl] upload flood from uid={uid}")
        return _too_many(300)
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    username  = user.get("username", "—")

    # The extension used to come from the client's filename, and uploads/ is
    # publicly served — so a support "photo" called evil.html or evil.svg became
    # attacker-controlled markup hosted on our own origin. Ignore the claimed
    # name entirely and derive the type from the actual bytes.
    image_ext = _sniff_image_ext(image_data)
    if not image_ext:
        return web.json_response({"error": "not_an_image"}, status=400, headers=CORS_HEADERS)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname    = f"{uuid.uuid4().hex[:12]}{image_ext}"
    fpath    = UPLOAD_DIR / fname
    fpath.write_bytes(image_data)
    url_path = f"/uploads/support/{fname}"

    conv_key  = f"{uid}_{order_id}" if order_id else str(uid)
    server_ts = datetime.now(timezone.utc).isoformat()

    await db.append_support_msg(conv_key, {
        "role": "user", "type": "photo", "url": url_path,
        "caption": caption, "ts": server_ts
    })

    if order_id and order_id != "general":
        ctx_line = f"📦 Заказ: #{order_id}"
    else:
        ctx_line = "📋 Общий вопрос"
    # Check user status
    status_tags = []
    highlight = False
    try:
        user_doc = await db.get_user(uid)
        if user_doc:
            if user_doc.get("is_banned"):
                status_tags.append("🚫 Забанен")
            if user_doc.get("verify_declined"):
                status_tags.append("❌ Верификация отклонена")
                highlight = True
            elif user_doc.get("verify_requested") and not _is_vetted(user_doc):
                status_tags.append("⏳ Ожидает верификации")
                highlight = True
            elif not _is_vetted(user_doc):
                status_tags.append("🔴 Не верифицирован")
    except Exception:
        pass
    if status_tags:
        joined = " | ".join(status_tags)
        status_line = ("\n⚠️ " + joined) if highlight else ("\n" + joined)
    else:
        status_line = ""
    header_caption = (
        f"📸 Фото\n"
        f"{ctx_line}\n"
        f"👤 {user_name} (@{username}, ID: {uid})"
        + status_line
        + (f"\n💬 {caption}" if caption else "")
    )
    token = SUPPORT_BOT_TOKEN or BOT_TOKEN
    for op_id in OPERATOR_IDS:
        try:
            result = await tg_send_photo(token, op_id, str(fpath), header_caption)
            fwd_id = result.get("result", {}).get("message_id")
            if fwd_id:
                await db.save_support_map_entry(str(fwd_id), {
                    "user_id": uid, "conv_key": conv_key, "order_id": order_id
                })
        except Exception as e:
            log.error(f"Support photo forward {op_id}: {e}")

    log.info(f"[support-img] user={uid} file={fname}")
    return web.json_response({"ok": True, "ts": server_ts, "url": url_path}, headers=CORS_HEADERS)


# ── GET /api/support/messages ─────────────────────────────────────────────────
async def handle_support_messages(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)

    user = validate_init_data(auth[4:])
    if not user:
        return web.json_response({"messages": []}, headers=CORS_HEADERS)

    uid      = user.get("id")
    conv_key = request.query.get("conv_key", "")
    # Keys are "<uid>" or "<uid>_<order_id>". A bare startswith() also matched a
    # longer id with the same prefix — uid 12345 could read 123456's thread.
    if conv_key != str(uid) and not conv_key.startswith(f"{uid}_"):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    conversation = await db.get_support_conv(conv_key)
    after = request.query.get("after", "")
    if after:
        conversation = [m for m in conversation if m.get("ts", "") > after]

    return web.json_response({"messages": conversation}, headers=CORS_HEADERS)


# ── GET /api/points-history ───────────────────────────────────────────────────
async def handle_points_history(request: web.Request) -> web.Response:
    """Returns the user's combined points history: +1 per delivered order
    and +N per referral, sorted newest-first. Names of referred friends are
    resolved server-side so the client never sees other users' Telegram IDs.
    """
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)
    user = validate_init_data(auth[4:])
    if not user:
        return web.json_response({"items": []}, headers=CORS_HEADERS)
    uid = user.get("id")

    items = []
    try:
        user_doc = await db.get_user(uid) or {}
        # Referral entries — each {user_id, points, at}
        for r in user_doc.get("referrals", []) or []:
            ref_uid = r.get("user_id")
            ref_name = ""
            ref_username = ""
            if ref_uid:
                try:
                    ref_doc = await db.get_user(ref_uid) or {}
                    ref_name = (ref_doc.get("first_name") or ref_doc.get("name") or "").strip()
                    ref_username = (ref_doc.get("username") or "").strip()
                except Exception:
                    pass
            at = r.get("at")
            ts = at.isoformat() if hasattr(at, "isoformat") else str(at or "")
            items.append({
                "type": "referral",
                "amount": int(r.get("points") or 0),
                "name": ref_name,
                "username": ref_username,
                "ts": ts,
            })
        # Delivered orders — implicit +1 each
        try:
            orders = await db.get_user_orders(uid)
        except Exception:
            orders = []
        for o in orders:
            if o.get("status") != "delivered":
                continue
            ts_raw = o.get("delivered_at") or o.get("timestamp") or ""
            ts = ts_raw.isoformat() if hasattr(ts_raw, "isoformat") else str(ts_raw)
            items.append({
                "type": "order",
                "amount": 1,
                "order_id": o.get("order_id", ""),
                "ts": ts,
            })
    except Exception as e:
        log.warning(f"points-history failed for uid={uid}: {e}")

    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return web.json_response({"items": items}, headers=CORS_HEADERS)


# ── Static file handler ───────────────────────────────────────────────────────
# STATIC_DIR is the repo root — the same directory that holds .env, .git and
# every .py module. A traversal check alone is NOT enough here: it only stops
# requests from escaping the directory, it happily serves the secrets sitting
# inside it. So this is an allow-list: a request is served only if it names one
# of the public root files, or lives under a public asset directory with a
# harmless extension. Everything else is a 404 — including dotfiles, sources,
# backups and dumps. Adding a new public asset means adding it here on purpose.
PUBLIC_ROOT_FILES = {
    "index-6.html", "manifest.json", "catalog.json", "qrcode.min.js",
    "icon.png", "HOME_SCREEN_BG.webp", "BACKGROUND_NEW_ADD.png",
    "CRYPTO_PROMO_AMBAR_RU.png", "CRYPTO_PROMO_AMBAR_EN.png",
    "promo_banner_ru.png",  "promo_banner_en.png",
    "promo_hero_ru.png",    "promo_hero_en.png",
    "promo_modal_ru.png",   "promo_modal_en.png",
    "promo_addhome_ru.png", "promo_addhome_en.png",
}
PUBLIC_DIRS = ("owner/", "operator/", "TEXTURES/", "fonts/", "LOGOS/", "uploads/")
PUBLIC_EXTS = {
    ".html", ".js", ".css", ".json", ".map",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".ico",
    ".ttf", ".otf", ".woff", ".woff2", ".mp3", ".wav", ".ogg",
}


def _is_public_asset(rel: str) -> bool:
    """rel is a STATIC_DIR-relative POSIX path with no traversal left in it."""
    if not rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts):      # .env, .git/…, .broadcast_sent_*
        return False
    if rel in PUBLIC_ROOT_FILES:
        return True
    if not rel.startswith(PUBLIC_DIRS):
        return False
    return ("." + rel.rsplit(".", 1)[-1].lower()) in PUBLIC_EXTS if "." in parts[-1] else False


async def handle_static(request: web.Request) -> web.Response:
    path = request.match_info.get("path", "") or "index-6.html"
    if path in ("", "/"):
        path = "index-6.html"
    filepath = (STATIC_DIR / path).resolve()
    try:
        rel = filepath.relative_to(STATIC_DIR.resolve()).as_posix()
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    # Directory URLs (/owner/) resolve to their index.html below — allow the
    # bare directory through the check by testing the file it maps to.
    probe = rel + "/index.html" if filepath.is_dir() else rel
    if not _is_public_asset(probe):
        log.warning(f"[static] blocked non-public path: {rel}")
        return web.Response(status=404, text="Not found")
    # Directory requests (e.g. /owner/) → serve index.html inside them, same
    # as any normal web server. Needed so the owner miniapp at /owner/ works
    # without requiring /owner/index.html in the URL.
    if filepath.is_dir():
        filepath = filepath / "index.html"
    if not filepath.exists() or not filepath.is_file():
        return web.Response(status=404, text="Not found")
    mime, _ = mimetypes.guess_type(str(filepath))
    # For HTML files use no-store to prevent Telegram WebView from caching stale versions
    is_html = str(filepath).endswith(".html")
    cache_header = "no-store, no-cache, must-revalidate, max-age=0" if is_html else "public, max-age=86400"
    headers = {
        "Content-Type": mime or "application/octet-stream",
        "Cache-Control": cache_header,
    }
    if is_html:
        headers["Pragma"] = "no-cache"
        headers["Expires"] = "0"
    return web.FileResponse(filepath, headers=headers)


# ── App setup ─────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        log.warning("⚠️  BOT_TOKEN not set — initData validation will always fail!")

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_route("OPTIONS", "/api/me",                 handle_me)
    app.router.add_get(            "/api/me",                  handle_me)
    app.router.add_route("OPTIONS", "/api/review",              handle_review)
    app.router.add_post(           "/api/review",              handle_review)
    app.router.add_route("OPTIONS", "/api/review-skip",        handle_review_skip)
    app.router.add_post(           "/api/review-skip",         handle_review_skip)
    app.router.add_route("OPTIONS", "/api/active-order",       handle_active_order)
    app.router.add_get(            "/api/active-order",        handle_active_order)
    app.router.add_route("OPTIONS", "/api/verify-request",     handle_verify_request)
    app.router.add_post(           "/api/verify-request",     handle_verify_request)
    app.router.add_route("OPTIONS", "/api/orders",             handle_orders)
    app.router.add_get(            "/api/orders",              handle_orders)
    app.router.add_route("OPTIONS", "/api/order",              handle_create_order)
    app.router.add_post(           "/api/order",               handle_create_order)
    app.router.add_route("OPTIONS", "/api/crypto/invoice",       handle_crypto_invoice_create)
    app.router.add_post(           "/api/crypto/invoice",        handle_crypto_invoice_create)
    app.router.add_route("OPTIONS", "/api/crypto/invoice/{oid}", handle_crypto_invoice_get)
    app.router.add_get(            "/api/crypto/invoice/{oid}",  handle_crypto_invoice_get)
    app.router.add_route("OPTIONS", "/api/crypto/receipt",       handle_crypto_receipt_create)
    app.router.add_post(           "/api/crypto/receipt",        handle_crypto_receipt_create)
    app.router.add_route("OPTIONS", "/api/crypto/receipt/{oid}", handle_crypto_receipt_pdf)
    app.router.add_get(            "/api/crypto/receipt/{oid}",  handle_crypto_receipt_pdf)
    app.router.add_route("OPTIONS", "/api/cancel-order",       handle_cancel_order)
    app.router.add_post(           "/api/cancel-order",        handle_cancel_order)
    app.router.add_route("OPTIONS", "/api/support/send",       handle_support_send)
    app.router.add_post(           "/api/support/send",        handle_support_send)
    app.router.add_route("OPTIONS", "/api/support/send-image", handle_support_send_image)
    app.router.add_post(           "/api/support/send-image",  handle_support_send_image)
    app.router.add_route("OPTIONS", "/api/support/messages",   handle_support_messages)
    app.router.add_get(            "/api/support/messages",    handle_support_messages)
    app.router.add_route("OPTIONS", "/api/points-history",     handle_points_history)
    app.router.add_get(            "/api/points-history",      handle_points_history)
    # Owner dashboard routes (/api/owner/*). Kept in a separate module so the
    # surface is easy to find and extend without touching customer routes.
    from owner_auth import install_validator
    from owner_routes import setup as setup_owner_routes
    # Owner miniapp runs under @ambar_manage_bot → validate initData with its
    # token, NOT the customer BOT_TOKEN. A miniapp launched from the customer
    # bot cannot reach /api/owner/* even if the user id is in OWNER_IDS.
    if not OWNER_BOT_TOKEN:
        log.warning("⚠️  AMBAR_OWNER_BOT_TOKEN not set — /api/owner/* will 401 on every request!")
    install_validator(validate_owner_init_data)
    setup_owner_routes(app)
    # Self-serve broadcast (owner sends promos from ambar star) — same auth.
    try:
        import broadcast_routes
        broadcast_routes.setup(app)
    except Exception as e:
        log.error(f"broadcast routes setup failed: {e}")
    # Склад: пересчёт, перемещения, заявка, норма — тоже owner-only.
    try:
        import stock_routes
        stock_routes.setup(app)
    except Exception as e:
        log.error(f"stock routes setup failed: {e}")
    # Operator iPad POS (manual phone-in orders) — own auth vs OPERATOR_BOT_TOKEN.
    try:
        import operator_routes
        operator_routes.setup(app)
    except Exception as e:
        log.error(f"operator routes setup failed: {e}")

    app.router.add_get("/",          handle_static)
    app.router.add_get("/{path:.+}", handle_static)

    log.info(f"🍾 AMBAR API+Static → http://{HOST}:{PORT}")
    # Bind loopback by default: nginx proxies to 127.0.0.1:8080, so listening on
    # 0.0.0.0 only added a second door on the public IP that skips TLS, the
    # Cloudflare edge and every nginx rule. Override with WEBAPP_HOST if the
    # server ever needs to answer directly.
    web.run_app(app, host=HOST, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
