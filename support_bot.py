import os, uuid
from pathlib import Path
from datetime import datetime, timezone
import aiohttp as _aiohttp

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS
import db

# Main customer bot token (for sending notifications to users)
MAIN_BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# map: forwarded_message_id -> user_id (in-memory, for direct bot users)
MESSAGE_MAP = {}

async def _notify_user(user_id: int, text: str):
    """Send notification to user via main AMBAR bot."""
    if not MAIN_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                r = await resp.json()
                if not r.get("ok"):
                    print(f"⚠️ Notification failed: {r}")
    except Exception as e:
        print(f"⚠️ Notification error: {e}")

def t(user, en, ru):
    """Return RU if user language is Russian, else EN"""
    if user.language_code and user.language_code.startswith("ru"):
        return ru
    return en


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def format_user_info(user):
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"

    username = f"@{user.username}" if user.username else "—"
    lang = user.language_code or "—"

    return (
        "👤 New support message\n\n"
        f"Name: {name}\n"
        f"Username: {username}\n"
        f"User ID: {user.id}\n"
        f"Language: {lang}\n\n"
        "👇 Message below 👇"
    )


# -------------------------
# /start command
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_admin(user.id):
        await update.message.reply_text(
            "👋 You are set as support admin.\n"
            "Reply to forwarded messages to answer users."
        )
        return

    await update.message.reply_text(
        t(
            user,
            "👋 Hi!\n\nThis is our support chat.\n"
            "Send any message, photo or file and we'll reply here.",
            "👋 Привет!\n\nЭто чат поддержки.\n"
            "Отправь сообщение, фото или файл — мы ответим здесь."
        )
    )



# -------------------------
# USER → SUPPORT
# -------------------------
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    # Ignore admins here
    if is_admin(user.id):
        return

    # Friendly confirmation
    if msg.text:
        await msg.reply_text(
            t(user,
              "✅ Got it! Support will reply here.",
              "✅ Получили сообщение! Скоро ответим здесь.")
        )
    else:
        await msg.reply_text(
            t(user,
              "📎 Received! Support will reply here.",
              "📎 Получили файл! Скоро ответим здесь.")
        )

    # Forward to admins with user info
    for admin_id in ADMIN_IDS:
        try:
            # Send user info first
            info_msg = await context.bot.send_message(
                chat_id=admin_id,
                text=format_user_info(user)
            )

            # Forward actual user message
            forwarded = await msg.forward(chat_id=admin_id)

            # Map forwarded message to user
            MESSAGE_MAP[forwarded.message_id] = user.id

        except Exception as e:
            print(f"⚠️ Could not forward to admin {admin_id}: {e}")


# -------------------------
# ADMIN → USER (reply)
# -------------------------
async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if not is_admin(msg.from_user.id):
        return

    if not msg.reply_to_message:
        return

    replied_id = msg.reply_to_message.message_id
    user_id = MESSAGE_MAP.get(replied_id)

    # Check MongoDB for mini app conversations
    conv_info = None
    if not user_id:
        try:
            conv_info = await db.get_support_map_entry(str(replied_id))
            if conv_info:
                user_id = conv_info["user_id"]
        except Exception as e:
            print(f"⚠️ DB lookup failed: {e}")

    if not user_id:
        return

    # Mini app conversation — save reply to MongoDB so it appears in the app
    if conv_info:
        conv_key = conv_info["conv_key"]
        ts = datetime.now(timezone.utc).isoformat()

        try:
            if msg.photo:
                photo = msg.photo[-1]
                file = await photo.get_file()
                upload_dir = Path(__file__).parent / "uploads" / "support"
                upload_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{uuid.uuid4().hex[:12]}.jpg"
                fpath = upload_dir / fname
                await file.download_to_drive(str(fpath))
                await db.append_support_msg(conv_key, {
                    "role": "operator", "type": "photo",
                    "url": f"/uploads/support/{fname}",
                    "caption": msg.caption or "",
                    "ts": ts,
                })
            else:
                await db.append_support_msg(conv_key, {
                    "role": "operator", "type": "text",
                    "text": msg.text or msg.caption or "(media)",
                    "ts": ts,
                })
        except Exception as e:
            print(f"⚠️ Failed to save operator reply to DB: {e}")

        # Notify user via main bot
        order_id = conv_info.get("order_id", "")
        notif = (
            f"💬 *Новое сообщение от поддержки*"
            + (f" по заказу #{order_id}" if order_id else "")
            + f"\n\nОткройте приложение, чтобы прочитать ответ."
        )
        await _notify_user(user_id, notif)
        return

    # Direct bot conversation — send reply to user DM
    try:
        await msg.copy(chat_id=user_id)
    except Exception as e:
        print(f"⚠️ Could not send reply to user {user_id}: {e}")


async def post_init(app):
    await db.connect()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))

    # admin replies FIRST
    app.add_handler(
        MessageHandler(filters.REPLY & filters.ALL, handle_admin_reply)
    )

    # user messages
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_user_message)
    )

    print("🤖 Support bot is running...")
    app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()