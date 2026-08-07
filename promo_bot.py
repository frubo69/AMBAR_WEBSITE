#!/usr/bin/env python3
"""AMBAR Promo Bot — отвечает в чатах сообществ и ведёт в основного бота.

Отдельный бот, а не основной, намеренно. Автоответы в чужих чатах живут ровно
до первой жалобы, а @AmBarDelivery_bot — это не рекламный канал: его токеном
подписывается initData приложения, через него уходят подтверждения заказов.
Потерять его из-за рекламы нельзя, потерять этого — всего лишь потерять рекламу.

Отвечаем только там, где бот администратор: это и есть согласие владельца чата.
В обычном чате Telegram отдаёт боту не все сообщения, админу — все, так что
права администратора здесь ещё и техническое условие.
"""
import os, logging, time
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          ContextTypes, filters)
import config_promo as promo

load_dotenv()
PROMO_BOT_TOKEN = os.getenv("PROMO_BOT_TOKEN", "")
MAIN_BOT        = os.getenv("MAIN_BOT_USERNAME", "AmBarDelivery_bot")
PUBLIC_ORIGIN   = os.getenv("AMBAR_PUBLIC_ORIGIN", "https://ambar-delivery.com")
OWNER_IDS       = [int(x.strip()) for x in os.getenv("AMBAR_OWNER_IDS", "").split(",")
                   if x.strip().isdigit()]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("promo")

_admin_in  = {}      # chat_id → (админ ли, до какого времени верим)
_last_chat = {}      # chat_id → когда отвечали
_last_user = {}      # user_id → когда отвечали
_stats     = {}      # chat_id → (название, сколько ответов)


def _lang(text: str) -> str:
    """Русский, если в сообщении есть кириллица."""
    return "ru" if any("а" <= c.lower() <= "я" or c == "ё" for c in text) else "en"


async def _is_admin(ctx, chat_id: int) -> bool:
    """Мы админ в этом чате? Ответ держим 10 минут, чтобы не дёргать API на
    каждое сообщение — в живом чате это сотни запросов в час."""
    hit = _admin_in.get(chat_id)
    if hit and hit[1] > time.time():
        return hit[0]
    try:
        m = await ctx.bot.get_chat_member(chat_id, ctx.bot.id)
        ok = m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        log.warning(f"[promo] статус в {chat_id}: {e}")
        ok = False
    _admin_in[chat_id] = (ok, time.time() + 600)
    return ok


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or not msg.from_user or msg.from_user.is_bot:
        return
    if msg.chat.type not in ("group", "supergroup"):
        return
    if not promo.matches(msg.text):
        return

    now = time.time()
    cid, uid = msg.chat.id, msg.from_user.id
    # Ограничители до проверки прав: незачем ходить в API ради сообщения,
    # на которое всё равно промолчим.
    if now - _last_chat.get(cid, 0) < promo.CHAT_COOLDOWN_MIN * 60:
        return
    if now - _last_user.get(uid, 0) < promo.USER_COOLDOWN_H * 3600:
        return
    if not await _is_admin(ctx, cid):
        return

    lang = _lang(msg.text)
    try:
        await msg.reply_photo(
            photo=f"{PUBLIC_ORIGIN}/{promo.IMG[lang]}",
            caption=promo.TEXT[lang],
            parse_mode="HTML",
            # Ответом на конкретное сообщение, а не вбросом в чат: так это
            # читается как подсказка человеку, а не как реклама всем.
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                promo.BTN[lang], url=f"https://t.me/{MAIN_BOT}?start=chat_{abs(cid)}")]]),
        )
    except Exception as e:
        log.warning(f"[promo] ответ в {cid}: {e}")
        return

    _last_chat[cid], _last_user[uid] = now, now
    title, cnt = _stats.get(cid, (msg.chat.title or str(cid), 0))
    _stats[cid] = (title, cnt + 1)
    log.info(f"[promo] {msg.chat.title} · {uid} · «{msg.text[:60]}»")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Кто-то открыл самого рекламного бота — уводим в основной."""
    lang = _lang(update.effective_user.first_name or "")
    await update.message.reply_photo(
        photo=f"{PUBLIC_ORIGIN}/{promo.IMG[lang]}",
        caption=promo.TEXT[lang], parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            promo.BTN[lang], url=f"https://t.me/{MAIN_BOT}?start=chat_direct")]]))


async def cmd_chats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Где бот стоит и сколько раз ответил — владельцу."""
    if update.effective_user.id not in OWNER_IDS:
        return
    if not _stats:
        await update.message.reply_text("Пока ни одного ответа."); return
    rows = "\n".join(f"• {t} — {n}" for t, n in
                     sorted(_stats.values(), key=lambda x: -x[1]))
    await update.message.reply_text(f"Ответов с перезапуска:\n{rows}")


def main():
    if not PROMO_BOT_TOKEN:
        print("❌ PROMO_BOT_TOKEN missing"); return
    app = Application.builder().token(PROMO_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("chats", cmd_chats, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info(f"📣 AMBAR Promo Bot started · ведёт в @{MAIN_BOT}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
