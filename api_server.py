#!/usr/bin/env python3
"""
AMBAR API + Static file server
- Serves the mini app HTML/assets on /
- Serves GET  /api/orders → returns orders for the authenticated Telegram user
- Serves POST /api/order  → receives order from mini app, notifies user + operators
- Validates Telegram WebApp initData via HMAC-SHA256
"""
from __future__ import annotations
import os, json, hmac, hashlib, urllib.parse, mimetypes, logging, time
from datetime import datetime
from pathlib import Path
import aiohttp as _aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
SUPPORT_BOT_TOKEN  = os.getenv("SUPPORT_BOT_TOKEN", "")
OPERATOR_IDS       = [int(x.strip()) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]
ORDERS_FILE        = os.getenv("ORDERS_FILE", "orders.json")
SUPPORT_MSGS_FILE  = Path(__file__).parent / "support_messages.json"
SUPPORT_MAP_FILE   = Path(__file__).parent / "support_map.json"
PORT               = int(os.getenv("WEBAPP_PORT", "8080"))
STATIC_DIR         = Path(__file__).parent

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}

# ── Telegram initData validation ──────────────────────────────────────────────
def validate_init_data(init_data: str) -> dict | None:
    """
    Validate Telegram WebApp initData string.
    Returns the user dict on success, None on failure.
    Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val = params.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash  = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, hash_val):
            return None
        return json.loads(params.get("user", "{}"))
    except Exception as e:
        log.debug(f"initData parse error: {e}")
        return None

# ── File helpers ──────────────────────────────────────────────────────────────
def load_orders() -> dict:
    try:
        return json.loads(Path(ORDERS_FILE).read_text())
    except:
        return {}

def save_order(oid, data):
    orders = load_orders()
    orders[oid] = data
    Path(ORDERS_FILE).write_text(json.dumps(orders, ensure_ascii=False, indent=2))

def update_order(oid, **kw):
    orders = load_orders()
    if oid in orders:
        orders[oid].update(kw)
        Path(ORDERS_FILE).write_text(json.dumps(orders, ensure_ascii=False, indent=2))

# ── Support file helpers ──────────────────────────────────────────────────────
def load_support_msgs() -> dict:
    try:
        return json.loads(SUPPORT_MSGS_FILE.read_text())
    except:
        return {}

def save_support_msgs(data: dict):
    SUPPORT_MSGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

def load_support_map() -> dict:
    try:
        return json.loads(SUPPORT_MAP_FILE.read_text())
    except:
        return {}

def save_support_map(data: dict):
    SUPPORT_MAP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ── Telegram Bot API helper ──────────────────────────────────────────────────
async def tg_send(token, chat_id, text, parse_mode="Markdown", reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with _aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()

# ── API: POST /api/order — receive order from mini app ───────────────────────
async def handle_create_order(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    init_data = data.get("initData", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid       = user.get("id")
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    username  = user.get("username", "—")

    lang      = data.get("lang", "ru")
    oid       = data.get("order_id", f"AMB{int(time.time()) % 100000:05d}")
    items     = data.get("items", [])
    phone     = data.get("phone", "—")
    address   = data.get("address", "—")
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

    save_order(oid, {
        "order_id": oid, "customer_id": uid,
        "customer_name": user_name, "username": username,
        "phone": phone, "address": address, "location": loc,
        "items": items, "item_lines": item_lines,
        "tip": tip, "total": total, "lang": lang,
        "office_id": office_id, "office_name": office_nm,
        "comment": comment,
        "status": "pending", "timestamp": datetime.now().isoformat(),
    })

    # ── Send confirmation to customer ────────────────────────────────────────
    if lang == "ru":
        confirm = (
            f"✅ *Заказ #{oid} оформлен!*\n\n"
            f"🏠 {address}\n📞 {phone}\n\n"
            f"🛒 *Позиции:*\n{item_lines}\n\n"
            f"🎁 Чаевые: {tip} AED\n"
            f"💰 *Итого: {total} AED*\n\n"
            f"⏳ Оператор позвонит вам для подтверждения."
        )
    else:
        confirm = (
            f"✅ *Order #{oid} placed!*\n\n"
            f"🏠 {address}\n📞 {phone}\n\n"
            f"🛒 *Items:*\n{item_lines}\n\n"
            f"🎁 Tip: {tip} AED\n"
            f"💰 *Total: {total} AED*\n\n"
            f"⏳ Our operator will call you to confirm."
        )

    try:
        conf_result = await tg_send(BOT_TOKEN, uid, confirm)
        conf_msg_id = conf_result.get("result", {}).get("message_id")
        if conf_msg_id:
            update_order(oid, customer_msg_ids=[conf_msg_id])
    except Exception as e:
        log.error(f"Customer confirm message: {e}")

    # ── Notify operators ─────────────────────────────────────────────────────
    lat = loc.get("lat", 0)
    lon = loc.get("lon", 0)
    loc_str = ""
    if lat and lon:
        try:
            loc_str = f"\n📍 {float(lat):.5f}, {float(lon):.5f}"
        except (ValueError, TypeError):
            pass

    op_text = (
        f"🆕 *НОВЫЙ ЗАКАЗ #{oid}*\n\n"
        f"🏢 Офис: *{office_nm}*\n\n"
        f"👤 *{user_name}*\n"
        f"📞 `{phone}`\n"
        f"🔗 @{username} | ID: `{uid}`\n"
        f"🏠 Адрес: {address}{loc_str}\n\n"
        f"🛒 *Позиции:*\n{item_lines}\n\n"
        f"🎁 Чаевые: {tip} AED\n"
        f"💰 *Итого: {total} AED*"
        + (f"\n\n💬 *Комментарий:* {comment}" if comment else "")
    )
    op_kb = {"inline_keyboard": [
        [
            {"text": "✅ Принять",   "callback_data": f"acc_{oid}_{uid}"},
            {"text": "❌ Отклонить", "callback_data": f"dec_{oid}_{uid}"},
        ],
        [
            {"text": "✏️ Редактировать", "callback_data": f"edit_{oid}"},
            {"text": "📍 Геолокация",    "callback_data": f"loc_{oid}"},
        ],
        [{"text": "🚫 Забанить клиента", "callback_data": f"ban_{oid}_{uid}"}],
    ]}

    for op_id in OPERATOR_IDS:
        try:
            await tg_send(OPERATOR_BOT_TOKEN, op_id, op_text, reply_markup=op_kb)
            log.info(f"Notified operator {op_id} for order {oid}")
        except Exception as e:
            log.error(f"Operator notify {op_id}: {e}")

    log.info(f"[order] #{oid} from user {uid} — {len(items)} items, total {total} AED")
    return web.json_response({"ok": True, "order_id": oid}, headers=CORS_HEADERS)

# ── API: GET /api/orders ──────────────────────────────────────────────────────
async def handle_orders(request: web.Request) -> web.Response:
    # Handle CORS preflight
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)

    init_data = auth[4:]
    user = validate_init_data(init_data)

    if not user:
        # Dev/test mode: initData is empty or invalid — return empty list gracefully
        log.warning("initData invalid or empty — returning [] (dev mode?)")
        return web.json_response({"orders": []}, headers=CORS_HEADERS)

    uid = user.get("id")
    all_orders = load_orders()

    # Filter orders belonging to this customer
    user_orders = [o for o in all_orders.values() if o.get("customer_id") == uid]

    # Sort newest first
    user_orders.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    log.info(f"[orders] user={uid} count={len(user_orders)}")
    return web.json_response({"orders": user_orders}, headers=CORS_HEADERS)

# ── API: POST /api/support/send — user sends support message from mini app ────
async def handle_support_send(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    init_data = data.get("initData", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"error": "auth failed"}, status=401, headers=CORS_HEADERS)

    uid       = user.get("id")
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    username  = user.get("username", "—")
    order_id  = data.get("order_id", "")
    text      = (data.get("text", "") or "").strip()

    if not text:
        return web.json_response({"error": "empty message"}, status=400, headers=CORS_HEADERS)

    conv_key = f"{uid}_{order_id}" if order_id else str(uid)

    # 1. Save to support_messages.json
    msgs = load_support_msgs()
    if conv_key not in msgs:
        msgs[conv_key] = []
    msgs[conv_key].append({
        "role": "user",
        "text": text,
        "ts": datetime.now().isoformat(),
    })
    save_support_msgs(msgs)

    # 2. Forward to operators via SUPPORT_BOT_TOKEN
    header = (
        f"💬 *Чат поддержки (Mini App)*\n\n"
        f"📦 Заказ: `#{order_id}`\n"
        f"👤 {user_name} (@{username}, ID: `{uid}`)\n\n"
        f"💬 {text}"
    )

    smap = load_support_map()
    token = SUPPORT_BOT_TOKEN or BOT_TOKEN  # fallback to main bot if no support token
    for op_id in OPERATOR_IDS:
        try:
            result = await tg_send(token, op_id, header)
            fwd_id = result.get("result", {}).get("message_id")
            if fwd_id:
                smap[str(fwd_id)] = {
                    "user_id": uid,
                    "conv_key": conv_key,
                    "order_id": order_id,
                }
        except Exception as e:
            log.error(f"Support forward to {op_id}: {e}")

    save_support_map(smap)
    log.info(f"[support] user={uid} order={order_id} msg='{text[:50]}'")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)

# ── API: GET /api/support/messages — fetch conversation for a conv_key ────────
async def handle_support_messages(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)

    user = validate_init_data(auth[4:])
    if not user:
        return web.json_response({"messages": []}, headers=CORS_HEADERS)

    uid = user.get("id")
    conv_key = request.query.get("conv_key", "")

    # Security: conv_key must belong to this user
    if not conv_key.startswith(str(uid)):
        return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)

    msgs = load_support_msgs()
    conversation = msgs.get(conv_key, [])

    # Optional: filter by "after" timestamp for incremental polling
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

    # Prevent path traversal
    try:
        filepath.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return web.Response(status=403, text="Forbidden")

    if not filepath.exists() or not filepath.is_file():
        return web.Response(status=404, text="Not found")

    mime, _ = mimetypes.guess_type(str(filepath))
    return web.FileResponse(filepath, headers={
        "Content-Type": mime or "application/octet-stream",
        "Cache-Control": "no-cache",
    })

# ── App setup ─────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        log.warning("⚠️  BOT_TOKEN not set — initData validation will always fail!")

    app = web.Application()
    app.router.add_route("OPTIONS", "/api/orders", handle_orders)
    app.router.add_get("/api/orders", handle_orders)
    app.router.add_route("OPTIONS", "/api/order", handle_create_order)
    app.router.add_post("/api/order", handle_create_order)
    app.router.add_route("OPTIONS", "/api/support/send", handle_support_send)
    app.router.add_post("/api/support/send", handle_support_send)
    app.router.add_route("OPTIONS", "/api/support/messages", handle_support_messages)
    app.router.add_get("/api/support/messages", handle_support_messages)
    app.router.add_get("/", handle_static)
    app.router.add_get("/{path:.+}", handle_static)

    log.info(f"🍾 AMBAR API+Static server → http://localhost:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
