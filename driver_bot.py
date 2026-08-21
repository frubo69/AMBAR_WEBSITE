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
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
                      MenuButtonWebApp, ReplyKeyboardRemove)
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          ContextTypes, filters)

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
    # Заодно снимаем старую клавиатуру, если она осталась с прошлых версий.
    kb = await update.message.reply_text(
        "Чтобы оператор видел вас всю смену, включите трансляцию: "
        "скрепка → «Геопозиция» → «Транслировать» → 8 часов.",
        reply_markup=drop_keyboard())
    await _remember(kb)
    log.info(f"вход: {me['name']} ({uid})")


# ── где водитель ───────────────────────────────────────────────────────────
# Живую трансляцию водитель включает сам: скрепка → Геопозиция → Транслировать.
# Дальше телефон шлёт точки сюда даже при свёрнутом телеграме, а у водителя всё
# это время висит его собственная трансляция с таймером и кнопкой «остановить».
#
# Почему не геолокация из мини-аппа: браузер отдаёт координаты, только пока
# приложение открыто на экране. За рулём оно свёрнуто — то есть работало бы
# ровно тогда, когда не нужно.
SHIFT_START_HOUR = int(os.getenv("AMBAR_SHIFT_START_HOUR", "12"))
DUBAI = timezone(timedelta(hours=4))


def _biz_day(ref=None) -> str:
    ref = ref or datetime.now(DUBAI)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


# Кнопки «я здесь» у поля ввода больше нет.
#
# Она была нужна, пока приложение не умело брать точку само. Теперь умеет —
# и точка уходит без единого нажатия, просто когда водитель открывает
# приложение. А кнопка осталась бы висеть серой полосой под полем ввода всю
# смену, занимая место и напоминая о себе без повода. Одноразовая польза,
# постоянная цена.
#
# Убираем и у тех, у кого она уже стоит: клавиатура живёт в чате, пока её
# явно не снимут.
def drop_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Точка от водителя: и первая, и каждая следующая из трансляции.

    Живые обновления приходят правкой того же сообщения, поэтому здесь оба
    случая — и message, и edited_message."""
    msg = update.effective_message
    loc = getattr(msg, "location", None) if msg else None
    if not loc or not update.effective_user:
        return
    me = staff.driver_by_tg(update.effective_user.id)
    if not me:
        return                       # чужие координаты нам не нужны и не хранятся
    now = datetime.now(timezone.utc)
    until = None
    period = getattr(loc, "live_period", None)
    if period:
        until = now + timedelta(seconds=int(period))
    # Трансляцию выключили. Телеграм сообщает об этом правкой того же сообщения:
    # точка приходит, а срока у неё больше нет. Разовая точка отдельным
    # сообщением — не то же самое, её шлют и поверх идущей трансляции.
    stop = bool(update.edited_message and not period)
    try:
        await db.driver_pos_set(me["name"], _biz_day(), loc.latitude, loc.longitude,
                                now, until=until, stop_live=stop,
                                acc=getattr(loc, "horizontal_accuracy", None))
    except Exception as e:
        log.warning(f"точка {me['name']} не записана: {e}")
        return
    # Разовая точка — одно короткое подтверждение: без него человек не знает,
    # дошло ли, и жмёт ещё раз.
    if update.message and not period:
        ok = await update.message.reply_text("Точка принята — оператор вас видит.")
        await _remember(update.message)
        await _remember(ok)
        return
    # На включение трансляции отвечаем один раз. На каждую её точку писать
    # нельзя: телефон шлёт их десятками, и чат превратится в ленту.
    if update.message and period:
        await _remember(update.message)
        sent = await update.message.reply_text(
            "Трансляция включена — спасибо. Оператор видит, где вы.\n"
            "Выключить можно в любой момент кнопкой «Остановить» в этом сообщении.")
        await _remember(sent)
        # Бессрочная трансляция приходит служебным сроком 0x7FFFFFFF — это
        # шестьдесят восемь лет, и печатать их часами незачем.
        log.info(f"трансляция включена: {me['name']} · "
                 + ("бессрочно" if period > 86400 else f"{period // 3600} ч"))


async def cmd_where(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Напоминание, как включить трансляцию: словами и один раз."""
    if not staff.driver_by_tg(update.effective_user.id):
        return
    await _remember(update.message)
    sent = await update.message.reply_text(
        "Чтобы оператор видел вас всю смену:\n\n"
        "1. Скрепка в этом чате\n"
        "2. «Геопозиция»\n"
        "3. «Транслировать» → «Пока не выключу»\n\n"
        "Включить хватит один раз: телефон будет сам присылать точку, даже "
        "когда телеграм свёрнут. Маршрут стирается, когда закрывают смену, "
        "а в отпуске трансляцию можно выключить тем же меню.\n\n"
        "Пока приложение открыто, точка уходит и без трансляции — сама.",
        reply_markup=drop_keyboard())
    await _remember(sent)


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
    app.add_handler(CommandHandler("where", cmd_where))
    # И первое сообщение с точкой, и каждая правка живой трансляции.
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.LOCATION, on_location))
    log.info(f"бот водителя запущен · доступ у {len(staff.DRIVER_IDS)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
