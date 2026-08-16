#!/usr/bin/env python3
"""
AMBAR — бот водителя.

Делает ровно одно: открывает приложение. Заказы, доставка, расходы — всё внутри
мини-аппа, здесь только вход и понятный ответ тому, кого в списке нет.

Список водителей живёт в AMBAR_DRIVER_IDS (config_staff). Бот не решает, кого
пускать — он только объясняет; пускает сервер, проверяя подпись initData этим же
токеном. Значит вход в приложение водителя невозможен из операторского бота.
"""
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes

import config_staff as staff
import db

load_dotenv()
DRIVER_BOT_TOKEN = os.getenv("DRIVER_BOT_TOKEN", "")
DRIVER_WEBAPP_URL = os.getenv("DRIVER_WEBAPP_URL", "https://ambar-delivery.com/driver/")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("driver-bot")


# Что сказано в этом чате, помним по номерам сообщений: в скрытом режиме
# приложение маскируется, и переписка должна уйти вместе с ним. Здесь это
# только два ответа на команды — всё остальное водителю шлёт сервер и
# запоминает у себя. Ничего, кроме чата водителя с его ботом, сюда не попадает.
async def _remember(msg):
    if not msg:
        return
    try:
        await db.drv_msg_add(msg.chat_id, msg.message_id, datetime.now(timezone.utc))
    except Exception as e:
        log.debug(f"номер сообщения не записан: {e}")


async def post_init(app):
    """Кнопка приложения рядом с полем ввода — чтобы её не искали в меню."""
    try:
        await db.connect()
    except Exception as e:
        log.warning(f"база недоступна, чистка чата работать не будет: {e}")
    try:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Заказы", web_app=WebAppInfo(url=DRIVER_WEBAPP_URL)))
        log.info("кнопка приложения установлена")
    except Exception as e:
        log.warning(f"set_chat_menu_button: {e}")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    me = staff.driver_by_tg(uid)
    if not me:
        # Человеку, которого нет в списке, нужен не отказ, а объяснение и id —
        # менеджеру всё равно придётся его спросить.
        await update.message.reply_text(
            "Этот аккаунт пока не в списке водителей.\n\n"
            f"Ваш ID: {uid}\n"
            "Передайте его менеджеру — он выдаст доступ.")
        log.info(f"вход без доступа: {uid} (@{update.effective_user.username})")
        return

    await _remember(update.message)
    sent = await update.message.reply_text(
        f"{me['name']}, здравствуйте.\n"
        f"Ваш район: {me['district_code']} {me['district_name']}\n\n"
        "В приложении — заказы на смену, отметка доставки и расходы.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Открыть заказы", web_app=WebAppInfo(url=DRIVER_WEBAPP_URL))]]))
    await _remember(sent)
    log.info(f"вход: {me['name']} ({uid})")


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if staff.driver_by_tg(update.effective_user.id):
        await _remember(update.message)
    sent = await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")
    if staff.driver_by_tg(update.effective_user.id):
        await _remember(sent)


def main():
    if not DRIVER_BOT_TOKEN:
        print("❌ DRIVER_BOT_TOKEN missing")
        return
    app = Application.builder().token(DRIVER_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    log.info(f"бот водителя запущен · доступ у {len(staff.DRIVER_IDS)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
