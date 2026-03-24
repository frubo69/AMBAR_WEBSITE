#!/usr/bin/env python3
"""AMBAR Customer Bot — opens mini app, receives orders, ban check"""
import os, json, logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import db

load_dotenv()
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN   = os.getenv("OPERATOR_BOT_TOKEN", "")
OPERATOR_IDS         = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]
WEBAPP_URL           = os.getenv("WEBAPP_URL", "")
CATALOG_FILE         = "catalog.json"
STOCK_FILE           = "stock.json"
SUPPORT_BOT_USERNAME = "ambar_support_bot"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


# ── Stock helpers (kept in JSON — catalog management, not user data) ──────────
def load_json(f):
    try: return json.loads(Path(f).read_text())
    except: return {}

def save_json(f, d):
    Path(f).write_text(json.dumps(d, ensure_ascii=False, indent=2))

def load_stock():
    stock = load_json(STOCK_FILE)
    if not stock:
        try:
            catalog = json.loads(Path(CATALOG_FILE).read_text())
            stock = {p["id"]: p.get("stockQty", 0) for p in catalog}
            save_json(STOCK_FILE, stock)
        except: pass
    return stock

def deduct_stock(items):
    stock = load_stock()
    for item in items:
        if stock.get(item["id"], 0) < item["qty"]:
            return False
    for item in items:
        stock[item["id"]] = max(0, stock.get(item["id"], 0) - item["qty"])
    save_json(STOCK_FILE, stock)
    try:
        catalog = json.loads(Path(CATALOG_FILE).read_text())
        for p in catalog:
            qty = stock.get(p["id"], 0)
            p["stockQty"] = qty
            p["stock"]    = qty > 0
        Path(CATALOG_FILE).write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    except: pass
    return True


# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_review(cid, lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(str(i), callback_data=f"rev_{i}_{cid}_{lang}") for i in range(1, 6)
    ]])


# ── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = ctx.user_data.get("lang", "ru")
    name = update.effective_user.first_name

    # Ban check — silently skip if DB is unavailable
    try:
        if await db.is_banned(uid):
            await update.message.reply_text(
                "🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку.", parse_mode="Markdown")
            return
    except Exception as e:
        log.warning(f"ban check failed: {e}")

    if WEBAPP_URL:
        try:
            await ctx.bot.set_chat_menu_button(
                chat_id=uid,
                menu_button=MenuButtonWebApp(
                    text="🍾 Заказать" if lang == "ru" else "🍾 Order",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            )
        except Exception as e:
            log.debug(f"set_chat_menu_button: {e}")

    text = (
        f"👋 Привет, {name}!\n\n"
        f"Добро пожаловать в *AMBAR* — премиальная доставка spirits прямо к вашей двери.\n\n"
        f"✨ *Почему выбирают нас:*\n"
        f"⚡️ Быстрая доставка — привезём в кратчайшие сроки\n"
        f"🥃 Тщательно подобранный ассортимент — только проверенные бренды и редкие позиции\n"
        f"💎 Честные цены — premium качество без лишних наценок\n\n"
        f"Нажмите *🍾 Заказать* слева от поля ввода 👇"
        if lang == "ru" else
        f"👋 Hey, {name}!\n\n"
        f"Welcome to *AMBAR* — premium spirits delivery, right to your door.\n\n"
        f"✨ *Why choose us:*\n"
        f"⚡️ Fast delivery — we'll be there in no time\n"
        f"🥃 Curated selection — trusted brands and rare finds\n"
        f"💎 Fair pricing — premium quality, no unnecessary markups\n\n"
        f"Tap *🍾 Order* to the left of the input field 👇"
    )
    # Send welcome message first — DB write is best-effort
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    # Upsert user profile in background (doesn't affect UX if it fails)
    try:
        tg_user   = update.effective_user
        full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
        await db.upsert_user(
            uid,
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "",
            full_name=full_name,
            name=full_name,
            username=tg_user.username or "—",
        )
    except Exception as e:
        log.warning(f"upsert_user failed: {e}")


async def cb_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    score = parts[1]
    lang  = parts[3] if len(parts) > 3 else "ru"
    uid   = q.from_user.id

    try: await q.delete_message()
    except: pass

    if lang == "ru":
        text = (f"🙏 *Спасибо за оценку {score}/5!*\n\n"
                f"💬 _Хотите оставить комментарий? Просто напишите его — бот его сохранит._")
    else:
        text = (f"🙏 *Thank you for rating {score}/5!*\n\n"
                f"💬 _Want to leave a comment? Just send it here — the bot will save it._")

    thanks_msg = await ctx.bot.send_message(uid, text, parse_mode="Markdown")
    await db.upd_ustate(uid,
        awaiting_comment=True, rating=score, lang=lang,
        thanks_msg_id=thanks_msg.message_id,
        to_delete_on_order=[thanks_msg.message_id],
    )


async def fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if await db.is_banned(uid):
        await update.message.reply_text("🚫 Ваш аккаунт заблокирован.")
        return

    text   = update.message.text or ""
    ustate = await db.get_ustate(uid)

    # ── Handle free-text review comment ───────────────────────────────────────
    if ustate.get("awaiting_comment") and text and not text.startswith("/"):
        comment    = text.strip()
        thanks_mid = ustate.get("thanks_msg_id")
        score      = ustate.get("rating", "?")
        lang       = ustate.get("lang", "ru")

        try: await update.message.delete()
        except: pass

        if thanks_mid:
            safe = comment.replace("_","\\_").replace("*","\\*").replace("`","\\`").replace("[","\\[")
            if lang == "ru":
                edited = f"🙏 *Спасибо за оценку {score}/5!*\n\n💬 *Ваш отзыв:* _{safe}_"
            else:
                edited = f"🙏 *Thank you for rating {score}/5!*\n\n💬 *Your review:* _{safe}_"
            try:
                await ctx.bot.edit_message_text(
                    edited, chat_id=uid, message_id=thanks_mid, parse_mode="Markdown")
            except: pass

        await db.upd_ustate(uid, awaiting_comment=False)
        return

    if SUPPORT_BOT_USERNAME and text in ("🆘 Поддержка", "🆘 Support"):
        lang  = ctx.user_data.get("lang", "ru")
        label = "🆘 Открыть поддержку" if lang == "ru" else "🆘 Open Support"
        kb    = InlineKeyboardMarkup([[
            InlineKeyboardButton(label, url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
        ]])
        msg = "💬 Напишите нам в поддержку:" if lang == "ru" else "💬 Contact our support:"
        await update.message.reply_text(msg, reply_markup=kb)
        return

    await cmd_start(update, ctx)


async def post_init(app: Application):
    await db.connect()


def main():
    if not BOT_TOKEN:  print("❌ BOT_TOKEN missing");  return
    if not WEBAPP_URL: print("❌ WEBAPP_URL missing"); return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_review, pattern=r"^rev_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    log.info("🍾 AMBAR Customer Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
