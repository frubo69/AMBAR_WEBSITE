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
import os, json, hmac, hashlib, html as _html_mod, urllib.parse, mimetypes, logging, time, uuid
from datetime import datetime, timezone, timedelta
DUBAI_TZ = timezone(timedelta(hours=4))
from pathlib import Path
import aiohttp as _aiohttp
from aiohttp import web
from dotenv import load_dotenv
import db
from customer_card import render_customer_card

load_dotenv()
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
SUPPORT_BOT_TOKEN  = os.getenv("SUPPORT_BOT_TOKEN", "")
# Owner bot token — separate Telegram bot (@ambar_manage_bot) whose initData
# signs requests to /api/owner/*. Kept in .env, never in code.
OWNER_BOT_TOKEN    = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
OPERATOR_IDS       = [int(x.strip()) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]
PORT               = int(os.getenv("WEBAPP_PORT", "8080"))
STATIC_DIR         = Path(__file__).parent
UPLOAD_DIR         = STATIC_DIR / "uploads" / "support"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


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
        try:
            resp = await tg_send(BOT_TOKEN, uid, welcome_text, parse_mode="Markdown")
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
    db.close()


# ── Telegram initData validation ──────────────────────────────────────────────
# initData is HMAC-SHA256 signed with the bot's token. Every Telegram bot
# has its own token, so a miniapp launched from @ambar_bot and one launched
# from @ambar_manage_bot produce initData signed differently — we validate
# each against the right secret and never mix them.
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
    async with _aiohttp.ClientSession() as session:
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

    uid       = user.get("id")
    original_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    user_name = original_name
    username  = user.get("username", "—")
    lang      = data.get("lang", "ru")

    # Append operator nickname if set (original name stays, nickname in parentheses)
    try:
        user_doc = await db.get_user(uid)
        if user_doc and user_doc.get("custom_name"):
            user_name = f"{original_name} ({user_doc['custom_name']})"
    except Exception:
        pass

    # Ban check — reject order if user is banned
    try:
        if await db.is_banned(uid):
            log.warning(f"[order] banned user {uid} attempted to place order")
            return web.json_response({"error": "banned"}, status=403, headers=CORS_HEADERS)
    except Exception as e:
        log.warning(f"ban check failed: {e}")
    oid       = data.get("order_id", f"AMB{int(time.time()) % 100000:05d}")
    items     = data.get("items", [])
    phone     = data.get("phone", "—")
    address   = data.get("address", "—")
    gmap_link = data.get("gmap_link", "")
    is_gps    = data.get("is_gps", False)
    tip       = data.get("tip", 0)
    total     = data.get("total", 0)
    loc       = data.get("location", {})
    office_id = data.get("office_id", "office_central")
    office_nm = data.get("office_name", "Ambar")
    comment   = data.get("comment", "")

    item_lines = "\n".join(
        f"  • {i['name']} ×{i['qty']} = {i.get('line_total', i['price'] * i['qty'])} AED"
        for i in items
    )

    # Check if this is the user's very first order (before incrementing)
    referred_by = None
    referrer_username = None
    try:
        user_doc = await db.get_user(uid)
        _TEST_ALWAYS_FIRST = {8251195567}  # DEBUG: always treat as first order
        is_first_order = (uid in _TEST_ALWAYS_FIRST) or (user_doc is None or user_doc.get("orders_total", 0) == 0)
        # Reset verification for test accounts so each order triggers full flow
        if uid in _TEST_ALWAYS_FIRST:
            await db.set_user_field(uid, verified=False, verify_requested=False)
        if is_first_order and user_doc and user_doc.get("referred_by"):
            referred_by = user_doc["referred_by"]
    except Exception:
        is_first_order = False

    # Save order + upsert user profile in parallel
    order_doc = {
        "order_id": oid,        "customer_id": uid,
        "customer_name": user_name, "username": username,
        "phone": phone,         "address": address,   "location": loc,
        "gmap_link": gmap_link, "is_gps": is_gps,
        "items": items,         "item_lines": item_lines,
        "tip": tip,             "total": total,        "lang": lang,
        "office_id": office_id, "office_name": office_nm, "comment": comment,
        "status": "pending",    "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db.save_order(oid, order_doc)
    # Upsert user and increment order counter
    user_fields = dict(name=original_name, full_name=original_name, first_name=user.get("first_name",""),
                       last_name=user.get("last_name",""), username=username,
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
        if conf_msg_id:
            await db.update_order(oid, customer_msg_id=conf_msg_id)
    except Exception as e:
        log.error(f"Customer confirm: {e}")
    # First-order customers must verify before their order is visible to operators
    if is_first_order:
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
    # Address line: text name first, then link
    if address and address != "GPS" and address != "—":
        addr_line = f"🏠 Адрес: {address}"
        if gmap_link:
            addr_line += f"\nGoogle Maps: {gmap_link}"
    elif gmap_link:
        addr_line = f"📍 GPS: {gmap_link}"
    else:
        addr_line = f"🏠 Адрес: {address}"

    # Build first order banner — with referral info if applicable
    if is_first_order and referred_by:
        try:
            referrer_doc = await db.get_user(referred_by)
            referrer_username = referrer_doc.get("username", "—") if referrer_doc else "—"
        except Exception:
            referrer_username = "—"
        first_order_banner = f"<blockquote>🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ — РЕФЕРАЛ</b> 🔴🔴🔴\n👥 Пригласил — @{referrer_username}</blockquote>\n\n"
    elif is_first_order:
        first_order_banner = "<blockquote>🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ!</b> 🔴🔴🔴</blockquote>\n\n"
    else:
        first_order_banner = ""
    tip_line = f"\n🎁 Чаевые: {tip} AED" if tip else ""
    _comment_esc = _html_mod.escape(comment) if comment else ""
    op_text = (
        f"{first_order_banner}"
        f"🏢 Офис: <b>{_html_mod.escape(office_nm)}</b>\n\n"
        f"🆕 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
        f"{addr_line}\n\n"
        f"🛒 <b>Позиции:</b>\n{item_lines}\n"
        f"{tip_line}"
        f"\n💰 <b>Итого: {total} AED</b>"
        + (f"\n\n💬 <b>Комментарий:</b> {_comment_esc}" if comment else "")
    )
    # For every first-order user (including referrals) delay operator
    # notification until verification data is submitted. Referral users still
    # need to go through the flow — the referrer info just shows up as a hint
    # when the operator receives the combined notification.
    _needs_verification = is_first_order
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
            except Exception as e:
                log.error(f"Operator notify {op_id}: {e}")
        if op_msg_ids:
            await db.update_order(oid, op_msg_ids=op_msg_ids)

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
    return web.json_response({"ok": True, "order_id": oid, "needs_verification": _needs_verification}, headers=CORS_HEADERS)


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
    323390062,   # card #001
    7236406959,  # card #002
]
assert len(_WORLDWIDE_IDS) <= 100, "Worldwide PREMIUM is capped at 100 cards"

async def handle_me(request: web.Request) -> web.Response:
    """Returns ban status, referral points, and card type (founder/premium/standard)."""
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    uid_str = request.query.get("uid", "")
    if not uid_str.lstrip("-").isdigit():
        return web.json_response({"banned": False}, headers=CORS_HEADERS)
    uid = int(uid_str)
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
    verified = user_doc.get("verified", False) if user_doc else False
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
        "verify_requested": verify_requested,
        "verify_declined": verify_declined,
        "verify_pending": verify_pending,
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

    await db.submit_verify_request(uid, recommender_name, recommender_phone,
                                    source=source, source_detail=source_detail)

    # Find the pending-verification order and send combined notification
    user_orders = await db.get_user_orders(uid)
    pending = [o for o in user_orders if o.get("pending_verification")]
    user_doc = await db.get_user(uid) or {}
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
        oid = order["order_id"]
        saved_op_text = order.get("op_text", "")
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

        # Banner title — most specific wins
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

        if saved_op_text:
            # Strip old banners (both Markdown and HTML variants)
            import re as _re
            saved_op_text = _re.sub(r'<blockquote>.*?</blockquote>\s*', '', saved_op_text, flags=_re.DOTALL)
            for prefix in ["🔴 *⚠️ ПЕРВЫЙ ЗАКАЗ — новый клиент!*\n\n",
                           "🔴 *⚠️ НОВЫЙ КЛИЕНТ РЕФЕРАЛ*\n",
                           "🔴🔴🔴 *НОВЫЙ КЛИЕНТ!* 🔴🔴🔴\n\n",
                           "🔴🔴🔴 *НОВЫЙ КЛИЕНТ — РЕФЕРАЛ* 🔴🔴🔴\n"]:
                saved_op_text = saved_op_text.replace(prefix, "")
            combined = bq_alert + "\n\n" + saved_op_text.strip()
        else:
            combined = bq_alert + f"\n\n🆕 <b>ЗАКАЗ #{oid}</b>"

        op_buttons = [
            [
                {"text": "✅ Верифицировать", "callback_data": f"verify_{uid}"},
                {"text": "❌ Не верифицировать", "callback_data": f"decverify_{oid}_{uid}"},
            ],
            [{"text": "👤 Клиент", "callback_data": f"client_{oid}_{uid}"}],
        ]
        op_kb = {"inline_keyboard": op_buttons}
        op_msg_ids = {}
        for op_id in OPERATOR_IDS:
            try:
                resp = await tg_send(OPERATOR_BOT_TOKEN, op_id, combined, parse_mode="HTML", reply_markup=op_kb)
                if resp and resp.get("ok") and resp.get("result"):
                    op_msg_ids[str(op_id)] = resp["result"]["message_id"]
            except Exception as e:
                log.error(f"Verify+order notify {op_id}: {e}")
        if op_msg_ids:
            await db.update_order(oid, op_msg_ids=op_msg_ids)
        # Clear the pending flag
        await db.update_order(oid, pending_verification=False)

    log.info(f"[verify-request] uid={uid} source={source} detail={source_detail or recommender_name}")
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
    try:
        user_doc = await db.get_user(uid)
        if user_doc:
            if user_doc.get("is_banned"):
                status_tags.append("🚫 Забанен")
            if user_doc.get("verify_declined"):
                status_tags.append("❌ Верификация отклонена")
            elif not user_doc.get("verified"):
                status_tags.append("🔴 Не верифицирован")
    except Exception:
        pass
    status_line = (" | ".join(status_tags) + "\n") if status_tags else ""
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
    try:
        uid = int(request.rel_url.query.get("uid", 0))
    except (ValueError, TypeError):
        return web.json_response({"active": False}, headers=CORS_HEADERS)
    if not uid:
        return web.json_response({"active": False}, headers=CORS_HEADERS)
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
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    username  = user.get("username", "—")

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
    header_caption = (
        f"📸 Фото\n"
        f"{ctx_line}\n"
        f"👤 {user_name} (@{username}, ID: {uid})"
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
    if not conv_key.startswith(str(uid)):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    conversation = await db.get_support_conv(conv_key)
    after = request.query.get("after", "")
    if after:
        conversation = [m for m in conversation if m.get("ts", "") > after]

    return web.json_response({"messages": conversation}, headers=CORS_HEADERS)


# ── Static file handler ───────────────────────────────────────────────────────
async def handle_static(request: web.Request) -> web.Response:
    path = request.match_info.get("path", "") or "index-6.html"
    if path in ("", "/"):
        path = "index-6.html"
    filepath = (STATIC_DIR / path).resolve()
    try:
        filepath.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return web.Response(status=403, text="Forbidden")
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
    app.router.add_route("OPTIONS", "/api/cancel-order",       handle_cancel_order)
    app.router.add_post(           "/api/cancel-order",        handle_cancel_order)
    app.router.add_route("OPTIONS", "/api/support/send",       handle_support_send)
    app.router.add_post(           "/api/support/send",        handle_support_send)
    app.router.add_route("OPTIONS", "/api/support/send-image", handle_support_send_image)
    app.router.add_post(           "/api/support/send-image",  handle_support_send_image)
    app.router.add_route("OPTIONS", "/api/support/messages",   handle_support_messages)
    app.router.add_get(            "/api/support/messages",    handle_support_messages)
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

    app.router.add_get("/",          handle_static)
    app.router.add_get("/{path:.+}", handle_static)

    log.info(f"🍾 AMBAR API+Static → http://localhost:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
