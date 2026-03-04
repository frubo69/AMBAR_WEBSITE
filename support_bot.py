from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS


# map: forwarded_message_id -> user_id
MESSAGE_MAP = {}

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

    if not user_id:
        return

    # Send admin reply as bot
    await msg.copy(chat_id=user_id)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

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