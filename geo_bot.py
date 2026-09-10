#!/usr/bin/env python3
"""
AMBAR — бот геопозиции.

Делает ровно одно: принимает живую трансляцию геопозиции и кладёт точку туда,
откуда её читают карта и локатор. Водитель — под своим именем (локатор
оператора, сторож смены), старший — под ключом старшего (карта владельца).
Кто есть кто, решает .env: AMBAR_DRIVER_IDS и AMBAR_SENIOR_STAR_IDS, как у
остальных ботов. Чужие координаты не хранятся.

Зачем отдельный бот. Трансляция «пока не отключу» — это одно сообщение
человека, которое телеграм правит; стёртое сообщение — выключенная
трансляция. В чатах рабочих ботов сообщения стираются (скрытый режим,
чистильщик), и трансляции там не место. Здесь ничего не стирается и в
реестры стирания не пишется: человек один раз включает трансляцию, убирает
чат в архив, и она работает, пока он сам её не выключит.

Токен — AMBAR_GEO_BOT_TOKEN в /opt/ambar/.env; юнит — deploy/ambar-geo-bot.service.
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

import config_staff as staff
import db
import geo_watch

load_dotenv()
GEO_BOT_TOKEN = os.getenv("AMBAR_GEO_BOT_TOKEN", "")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # адрес запроса содержит токен — в журнал ему нельзя
log = logging.getLogger("geo-bot")

HOW = ("1. Скрепка слева от поля ввода\n"
       "2. «Геопозиция»\n"
       "3. «Транслировать геопозицию» → «Пока не отключу»")

# Единственное сообщение бота в чате — инструкция после /start. Когда
# трансляция включилась, её же правим в подтверждение, а не пишем второе:
# в этом чате должно остаться одно сообщение человека и одно наше.
_INSTR: dict = {}


def _role(uid) -> tuple:
    """(вид, имя): driver / senior / owner / '' — по спискам из .env."""
    me = staff.driver_by_tg(uid)
    if me:
        return "driver", me["name"]
    name = staff.senior_star_by_tg(uid)
    if name:
        return "senior", name
    try:
        import config
        if uid in config.OWNER_IDS or uid in config.MANAGER_IDS:
            return "owner", ""
    except Exception:                        # noqa: BLE001
        pass
    return "", ""


async def post_init(app):
    try:
        await db.connect()
    except Exception as e:                   # noqa: BLE001
        log.warning(f"база недоступна: {e}")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    kind, name = _role(update.effective_user.id)
    if kind == "owner":
        await update.message.reply_text(
            "Это бот геопозиции для водителей и старшего. Владельца он не отслеживает.")
        return
    if not kind:
        await update.message.reply_text("Нет доступа")
        return
    who = "водитель" if kind == "driver" else "старший"
    sent = await update.message.reply_text(
        f"{name} · {who}\n\n"
        "Здесь нужно сделать одно — включить трансляцию геопозиции:\n\n"
        f"{HOW}\n\n"
        "Один раз. Дальше телефон присылает точку сам, даже когда телеграм "
        "свёрнут, а этот чат можно убрать в архив.")
    _INSTR[update.effective_chat.id] = sent.message_id


async def _say(update: Update, ctx, text: str, markup=None):
    """Ответить, правя инструкцию, если она ещё на месте, иначе новым сообщением."""
    chat = update.effective_chat.id
    mid = _INSTR.get(chat)
    if mid:
        try:
            await ctx.bot.edit_message_text(chat_id=chat, message_id=mid, text=text,
                                            reply_markup=markup)
            return
        except Exception as e:               # noqa: BLE001
            log.debug(f"инструкция не правится: {e}")
            _INSTR.pop(chat, None)
    sent = await update.effective_message.reply_text(text, reply_markup=markup)
    _INSTR[chat] = sent.message_id


def _device_kb(mid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Телефон", callback_data=f"dev:phone:{mid}"),
        InlineKeyboardButton("iPad", callback_data=f"dev:tablet:{mid}")]])


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Точка: первая из трансляции, каждая следующая (правкой того же
    сообщения) и разовая. Водитель и старший пишутся в ту же запись, что и из
    своих ботов и из панели — карта, локатор и сторож ничего нового не узнают."""
    msg = update.effective_message
    loc = getattr(msg, "location", None) if msg else None
    if not loc or not update.effective_user:
        return
    kind, name = _role(update.effective_user.id)
    if kind not in ("driver", "senior"):
        return                               # чужие и владельцы: не храним, не отвечаем
    now = datetime.now(timezone.utc)
    period = getattr(loc, "live_period", None)
    until = now + timedelta(seconds=int(period)) if period else None
    # Выключили: телеграм правит то же сообщение, а срока у точки больше нет.
    stop = bool(update.edited_message and not period)
    device = ""
    if kind == "driver":
        key = name
    else:
        # У старшего два устройства под одним аккаунтом, и по id их не
        # отличить. Какое это — он говорит кнопкой при включении, а помним
        # мы это по номеру сообщения трансляции: правки приходят им же.
        # Пока не ответил — телефон.
        device = await db.geo_stream_get(update.effective_chat.id, msg.message_id)
        phone, ipad = geo_watch.senior_keys(name)
        key = ipad if device == "tablet" else phone
    try:
        await db.driver_pos_set(key, geo_watch._biz_day(), loc.latitude, loc.longitude,
                                now, until=until, stop_live=stop,
                                acc=getattr(loc, "horizontal_accuracy", None))
    except Exception as e:                   # noqa: BLE001
        log.warning(f"точка {name} не записана: {e}")
        return
    started = bool(update.message and period)
    # Сторожу — про телефон: планшет лежит на точке, и его выключенная
    # трансляция не значит, что старший пропал.
    if (started or stop) and device != "tablet":
        try:
            if kind == "driver":
                await geo_watch.on_stream(name, on=started, now=now)
            else:
                await geo_watch.on_senior_stream(name, on=started, now=now)
        except Exception as e:               # noqa: BLE001
            log.warning(f"о трансляции {name} не сообщили: {e}")
    if started:
        log.info(f"трансляция включена: {name} · "
                 + ("бессрочно" if period > 86400 else f"{period // 3600} ч"))
        if kind == "senior":
            await _say(update, ctx, f"{name} · трансляция идёт. С какого это устройства?",
                       _device_kb(msg.message_id))
        else:
            await _say(update, ctx, f"{name} · трансляция идёт. Больше здесь ничего делать "
                                    "не нужно — чат можно убрать в архив.")
    elif stop:
        log.info(f"трансляция выключена: {name}")
        await _say(update, ctx, f"{name} · трансляция выключена. Чтобы вас снова видели, "
                                f"включите её заново:\n\n{HOW}")
    elif update.message:
        # Разовая точка: дошла, но погаснет через минуты. Нужна трансляция.
        await _say(update, ctx, f"{name} · точка принята, но это разовая точка, она "
                                f"погаснет. Нужна трансляция:\n\n{HOW}")


async def on_device(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Старший ответил, с какого устройства трансляция. Помним по номеру её
    сообщения; с этой секунды правки идут под ключ того устройства."""
    q = update.callback_query
    if not q or not update.effective_user:
        return
    try:
        await q.answer()
    except Exception:                        # noqa: BLE001
        pass
    kind, name = _role(update.effective_user.id)
    if kind != "senior":
        return
    try:
        _, dev, mid = str(q.data or "").split(":")
        mid = int(mid)
    except ValueError:
        return
    if dev not in ("phone", "tablet"):
        return
    await db.geo_stream_set(update.effective_chat.id, mid, dev, name)
    label = "iPad" if dev == "tablet" else "телефон"
    try:
        await q.edit_message_text(f"{name} · трансляция идёт · {label}. Больше здесь "
                                  "ничего делать не нужно — чат можно убрать в архив.")
    except Exception as e:                   # noqa: BLE001
        log.debug(f"ответ не правится: {e}")
    log.info(f"устройство трансляции: {name} · {label}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Любое другое сообщение — та же инструкция, чужому — два слова."""
    if not update.effective_user or not update.message:
        return
    await cmd_start(update, ctx)


def main():
    if not GEO_BOT_TOKEN:
        print("❌ AMBAR_GEO_BOT_TOKEN missing — add it to /opt/ambar/.env")
        return
    app = Application.builder().token(GEO_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_device, pattern=r"^dev:"))
    log.info(f"бот геопозиции запущен · водителей {len(staff.DRIVER_IDS)} · "
             f"старших {len(staff.SENIOR_STAR_IDS)}")
    app.run_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()
