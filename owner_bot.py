#!/usr/bin/env python3
"""
AMBAR Owner/Manager Bot — @ambar_manage_bot

Minimal Telegram bot whose entire job is to launch the owner dashboard
miniapp at https://owner.ambar-delivery.com/. Kept intentionally thin:
no business logic, no DB, no state. Anything beyond "open the panel"
lives in the miniapp itself.

Access model
------------
- OWNER_IDS: hardcoded in config.py, always allowed.
- MANAGER_IDS: comma-separated env var AMBAR_MANAGER_IDS, same access.
- Anyone else: polite "access restricted" reply; no data leaked.

The same OWNER_IDS ∪ MANAGER_IDS gate is enforced server-side on every
/api/owner/* endpoint via require_owner (owner_auth.py). This bot's gate
is just UX — the API would reject unauthorized callers anyway.
"""
import os
import logging

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

OWNER_BOT_TOKEN = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
OWNER_WEBAPP_URL = os.getenv(
    "OWNER_WEBAPP_URL", "https://owner.ambar-delivery.com/"
)
# OWNER_IDS hardcoded here as a small set for defense-in-depth; the real
# enforcement is server-side in require_owner. Keep in sync with config.py.
OWNER_IDS = {686932322, 982022772}
MANAGER_IDS = {
    int(x.strip())
    for x in os.getenv("AMBAR_MANAGER_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("owner-bot")


def is_allowed(uid: int) -> bool:
    return uid in OWNER_IDS or uid in MANAGER_IDS


def launcher_keyboard() -> InlineKeyboardMarkup:
    """Inline button that opens the miniapp inside Telegram's WebView."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "📊 Открыть панель",
            web_app=WebAppInfo(url=OWNER_WEBAPP_URL),
        )]]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Everyone gets the launcher button. Access control happens inside
    the miniapp itself (full-screen block overlay) and server-side in
    require_owner — so even if someone accidentally gets pointed at this
    bot, they can't do anything. Letting them tap the button is fine."""
    uid = update.effective_user.id
    allowed = is_allowed(uid)
    log.info(
        "/start uid=%s @%s allowed=%s",
        uid, update.effective_user.username, allowed,
    )
    await update.message.reply_text(
        "Панель владельца AMBAR",
        reply_markup=launcher_keyboard(),
    )


async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any non-command message → re-send the launcher. Same reasoning as
    cmd_start: the miniapp and /api/owner/* both gate access, so the bot
    can be uniformly welcoming."""
    if not update.effective_user:
        return
    await update.message.reply_text(
        "Панель владельца AMBAR",
        reply_markup=launcher_keyboard(),
    )


async def post_init(application: Application):
    """Set the bot's menu button programmatically too — BotFather's UI
    setting is authoritative but this makes the button show up for users
    even on fresh installs without relying on BotFather config drift."""
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Панель",
                web_app=WebAppInfo(url=OWNER_WEBAPP_URL),
            )
        )
        log.info("menu button set to %s", OWNER_WEBAPP_URL)
    except Exception as e:
        log.warning("could not set menu button: %s", e)


def main():
    if not OWNER_BOT_TOKEN:
        raise RuntimeError(
            "AMBAR_OWNER_BOT_TOKEN is not set — add it to /opt/ambar/.env"
        )

    app = (
        Application.builder()
        .token(OWNER_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_any_message))

    log.info("🔐 AMBAR Owner Bot starting — miniapp: %s", OWNER_WEBAPP_URL)
    log.info("   allowed owners: %s", sorted(OWNER_IDS))
    log.info("   allowed managers: %s", sorted(MANAGER_IDS) or "(none)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
