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
import os, json, hmac, hashlib, urllib.parse, mimetypes, logging, time, uuid
from datetime import datetime, timezone
from pathlib import Path
import aiohttp as _aiohttp
from aiohttp import web
from dotenv import load_dotenv
import db

load_dotenv()
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
SUPPORT_BOT_TOKEN  = os.getenv("SUPPORT_BOT_TOKEN", "")
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

async def on_cleanup(app):
    db.close()


# ── Telegram initData validation ──────────────────────────────────────────────
def validate_init_data(init_data: str) -> dict | None:
    try:
        params    = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val  = params.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash  = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, hash_val):
            return None
        return json.loads(params.get("user", "{}"))
    except Exception as e:
        log.debug(f"initData parse error: {e}")
        return None


# ── Telegram Bot API helpers ───────────────────────────────────────────────────
async def tg_send(token, chat_id, text, parse_mode="Markdown", reply_markup=None):
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with _aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()

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

    # Save order + upsert user profile in parallel
    order_doc = {
        "order_id": oid,        "customer_id": uid,
        "customer_name": user_name, "username": username,
        "phone": phone,         "address": address,   "location": loc,
        "items": items,         "item_lines": item_lines,
        "tip": tip,             "total": total,        "lang": lang,
        "office_id": office_id, "office_name": office_nm, "comment": comment,
        "status": "pending",    "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db.save_order(oid, order_doc)
    await db.upsert_user(uid, name=user_name, username=username,
                         **({"phone": phone} if phone != "—" else {}))

    # ── Customer confirmation ─────────────────────────────────────────────────
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
            await db.update_order(oid, customer_msg_ids=[conf_msg_id])
    except Exception as e:
        log.error(f"Customer confirm: {e}")

    # ── Operator notification ─────────────────────────────────────────────────
    lat, lon = loc.get("lat", 0), loc.get("lon", 0)
    loc_str  = ""
    if lat and lon:
        try: loc_str = f"\n📍 {float(lat):.5f}, {float(lon):.5f}"
        except (ValueError, TypeError): pass

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
        except Exception as e:
            log.error(f"Operator notify {op_id}: {e}")

    log.info(f"[order] #{oid} user={uid} items={len(items)} total={total} AED")
    return web.json_response({"ok": True, "order_id": oid}, headers=CORS_HEADERS)


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

    if not text:
        return web.json_response({"error": "empty message"}, status=400, headers=CORS_HEADERS)

    conv_key  = f"{uid}_{order_id}" if order_id else str(uid)
    server_ts = datetime.now(timezone.utc).isoformat()

    await db.append_support_msg(conv_key, {"role": "user", "text": text, "ts": server_ts})

    header = (
        f"💬 *Чат поддержки (Mini App)*\n\n"
        f"📦 Заказ: `#{order_id}`\n"
        f"👤 {user_name} (@{username}, ID: `{uid}`)\n\n"
        f"💬 {text}"
    )
    token = SUPPORT_BOT_TOKEN or BOT_TOKEN
    for op_id in OPERATOR_IDS:
        try:
            result = await tg_send(token, op_id, header)
            fwd_id = result.get("result", {}).get("message_id")
            if fwd_id:
                await db.save_support_map_entry(str(fwd_id), {
                    "user_id": uid, "conv_key": conv_key, "order_id": order_id
                })
        except Exception as e:
            log.error(f"Support forward {op_id}: {e}")

    log.info(f"[support] user={uid} order={order_id}")
    return web.json_response({"ok": True, "ts": server_ts}, headers=CORS_HEADERS)


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

    header_caption = (
        f"📸 Mini App Photo\n"
        f"📦 Order: #{order_id}\n"
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
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_route("OPTIONS", "/api/orders",             handle_orders)
    app.router.add_get(            "/api/orders",              handle_orders)
    app.router.add_route("OPTIONS", "/api/order",              handle_create_order)
    app.router.add_post(           "/api/order",               handle_create_order)
    app.router.add_route("OPTIONS", "/api/support/send",       handle_support_send)
    app.router.add_post(           "/api/support/send",        handle_support_send)
    app.router.add_route("OPTIONS", "/api/support/send-image", handle_support_send_image)
    app.router.add_post(           "/api/support/send-image",  handle_support_send_image)
    app.router.add_route("OPTIONS", "/api/support/messages",   handle_support_messages)
    app.router.add_get(            "/api/support/messages",    handle_support_messages)
    app.router.add_get("/",          handle_static)
    app.router.add_get("/{path:.+}", handle_static)

    log.info(f"🍾 AMBAR API+Static → http://localhost:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
