#!/usr/bin/env python3
"""
AMBAR — бот устройств (DEVICE LOCATOR): планшеты и всё, что не человек.

Принимает живую трансляцию геопозиции с устройства и кладёт точку под ключ
устройства — в локаторе это отдельная группа «Устройства», не путается ни с
водителями, ни со старшим. Какое устройство за каким аккаунтом, решает .env:
AMBAR_DEVICE_IDS="iPad Star:telegram_id,…". Чужие координаты не хранятся,
сторожу смены устройства не интересны.

Отдельно от бота людей, потому что у старшего телефон и планшет живут под
одним аккаунтом, и по id их не отличить: что приходит СЮДА — устройство,
что в бот геопозиции — человек.

Токен — AMBAR_DEVICE_BOT_TOKEN; юнит — deploy/ambar-device-bot.service.
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          ContextTypes, filters)

import config_staff as staff
import db
import geo_watch

load_dotenv()
DEVICE_BOT_TOKEN = os.getenv("AMBAR_DEVICE_BOT_TOKEN", "")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # адрес запроса содержит токен — в журнал ему нельзя
log = logging.getLogger("device-bot")

HOW = ("1. Скрепка слева от поля ввода\n"
       "2. «Геопозиция»\n"
       "3. «Транслировать геопозицию» → «Пока не отключу»")

_INSTR: dict = {}


async def post_init(app):
    try:
        await db.connect()
    except Exception as e:                   # noqa: BLE001
        log.warning(f"база недоступна: {e}")


async def _say(update: Update, ctx, text: str):
    chat = update.effective_chat.id
    mid = _INSTR.get(chat)
    if mid:
        try:
            await ctx.bot.edit_message_text(chat_id=chat, message_id=mid, text=text)
            return
        except Exception as e:               # noqa: BLE001
            log.debug(f"инструкция не правится: {e}")
            _INSTR.pop(chat, None)
    sent = await update.effective_message.reply_text(text)
    _INSTR[chat] = sent.message_id


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    label = staff.device_by_tg(update.effective_user.id)
    if not label:
        await update.message.reply_text("Нет доступа")
        return
    sent = await update.message.reply_text(
        f"{label}\n\n"
        "С этого устройства нужно сделать одно — включить трансляцию геопозиции:\n\n"
        f"{HOW}\n\n"
        "Один раз. Дальше устройство присылает точку само, даже когда телеграм "
        "свёрнут, а этот чат можно убрать в архив.")
    _INSTR[update.effective_chat.id] = sent.message_id


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    loc = getattr(msg, "location", None) if msg else None
    if not loc or not update.effective_user:
        return
    label = staff.device_by_tg(update.effective_user.id)
    if not label:
        return                               # чужие координаты не храним и не отвечаем
    now = datetime.now(timezone.utc)
    period = getattr(loc, "live_period", None)
    until = now + timedelta(seconds=int(period)) if period else None
    stop = bool(update.edited_message and not period)
    try:
        await db.driver_pos_set(geo_watch.DEVICE_PREFIX + label, geo_watch._biz_day(),
                                loc.latitude, loc.longitude, now, until=until,
                                stop_live=stop, acc=getattr(loc, "horizontal_accuracy", None))
    except Exception as e:                   # noqa: BLE001
        log.warning(f"точка {label} не записана: {e}")
        return
    started = bool(update.message and period)
    if started:
        log.info(f"трансляция включена: {label} · "
                 + ("бессрочно" if period > 86400 else f"{period // 3600} ч"))
        await _say(update, ctx, f"{label} · трансляция идёт. Больше здесь ничего делать "
                                "не нужно — чат можно убрать в архив.")
    elif stop:
        log.info(f"трансляция выключена: {label}")
        await _say(update, ctx, f"{label} · трансляция выключена. Чтобы устройство снова "
                                f"было видно, включите её заново:\n\n{HOW}")
    elif update.message:
        await _say(update, ctx, f"{label} · точка принята, но это разовая точка, она "
                                f"погаснет. Нужна трансляция:\n\n{HOW}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    await cmd_start(update, ctx)


def main():
    if not DEVICE_BOT_TOKEN:
        print("❌ AMBAR_DEVICE_BOT_TOKEN missing — add it to /opt/ambar/.env")
        return
    app = Application.builder().token(DEVICE_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info(f"бот устройств запущен · устройств {len(staff.DEVICE_IDS)}: "
             + ", ".join(staff.DEVICE_IDS) if staff.DEVICE_IDS else
             "бот устройств запущен · устройств нет (AMBAR_DEVICE_IDS пуст)")
    app.run_polling(allowed_updates=["message", "edited_message"])


if __name__ == "__main__":
    main()
