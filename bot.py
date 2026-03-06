#!/usr/bin/env python3
"""AMBAR Customer Bot — opens mini app, receives orders, ban check"""
import os, json, time, logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

load_dotenv()
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
OPERATOR_IDS       = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]
WEBAPP_URL         = os.getenv("WEBAPP_URL", "")
ORDERS_FILE        = os.getenv("ORDERS_FILE", "orders.json")
BANS_FILE          = "bans.json"
ADDRESSES_FILE     = "addresses.json"
CATALOG_FILE       = "catalog.json"
STOCK_FILE         = "stock.json"
USER_STATE_FILE    = "user_state.json"
SUPPORT_BOT_USERNAME = "ambar_support_bot"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── File helpers ──────────────────────────────────────────────────────────────
def load_json(f):
    try: return json.loads(Path(f).read_text())
    except: return {}

def save_json(f, d):
    Path(f).write_text(json.dumps(d, ensure_ascii=False, indent=2))

# ── User state helpers (comment / review flow) ────────────────────────────────
def get_ustate(uid: int) -> dict:
    return load_json(USER_STATE_FILE).get(str(uid), {})

def set_ustate(uid: int, data: dict):
    s = load_json(USER_STATE_FILE); s[str(uid)] = data; save_json(USER_STATE_FILE, s)

def upd_ustate(uid: int, **kw):
    s = load_json(USER_STATE_FILE); s.setdefault(str(uid), {}).update(kw); save_json(USER_STATE_FILE, s)

def is_banned(uid):
    return str(uid) in load_json(BANS_FILE)

def load_orders(): return load_json(ORDERS_FILE)
def save_order(oid, data):
    o = load_orders(); o[oid] = data; save_json(ORDERS_FILE, o)
def update_order(oid, **kw):
    o = load_orders()
    if oid in o: o[oid].update(kw); save_json(ORDERS_FILE, o)

# ── Saved addresses ───────────────────────────────────────────────────────────
def save_user_address(uid, addr_entry):
    all_addr = load_json(ADDRESSES_FILE)
    uid_str  = str(uid)
    lst      = all_addr.get(uid_str, [])
    exists   = [a for a in lst if a.get("address","").strip().lower() == addr_entry.get("address","").strip().lower()]
    if not exists:
        lst.insert(0, addr_entry)
        lst = lst[:5]
    all_addr[uid_str] = lst
    save_json(ADDRESSES_FILE, all_addr)

# ── Stock helpers ─────────────────────────────────────────────────────────────
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
            p["stock"] = qty > 0
        Path(CATALOG_FILE).write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    except: pass
    return True

# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_review(cid, lang):
    return InlineKeyboardMarkup([[InlineKeyboardButton(str(i), callback_data=f"rev_{i}_{cid}_{lang}") for i in range(1, 6)]])

# ── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку.", parse_mode="Markdown")
        return
    lang = ctx.user_data.get("lang", "ru")
    name = update.effective_user.first_name

    # Attach mini app to the persistent menu button (left of input field).
    # sendData() works from this button exactly the same as a keyboard button.
    if WEBAPP_URL:
        try:
            await ctx.bot.set_chat_menu_button(
                chat_id=uid,
                menu_button=MenuButtonWebApp(
                    text="🍾 Заказать" if lang == "ru" else "🍾 Order",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
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

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

async def cb_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    score = parts[1]
    lang  = parts[3] if len(parts) > 3 else "ru"
    uid   = q.from_user.id

    # Delete the "оцените сервис" message with the buttons
    try: await q.delete_message()
    except: pass

    # Send "спасибо за оценку" + invite to leave a comment
    if lang == "ru":
        text = (f"🙏 *Спасибо за оценку {score}/5!*\n\n"
                f"💬 _Хотите оставить комментарий? Просто напишите его — бот его сохранит._")
    else:
        text = (f"🙏 *Thank you for rating {score}/5!*\n\n"
                f"💬 _Want to leave a comment? Just send it here — the bot will save it._")

    thanks_msg = await ctx.bot.send_message(uid, text, parse_mode="Markdown")

    # Save state so fallback() can catch the comment
    upd_ustate(uid,
        awaiting_comment=True,
        rating=score,
        lang=lang,
        thanks_msg_id=thanks_msg.message_id,
        to_delete_on_order=[thanks_msg.message_id],
    )

async def fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("🚫 Ваш аккаунт заблокирован.")
        return

    text = update.message.text or ""

    # ── Handle free-text review comment ───────────────────────────────────────
    ustate = get_ustate(uid)
    if ustate.get("awaiting_comment") and text and not text.startswith("/"):
        comment      = text.strip()
        thanks_mid   = ustate.get("thanks_msg_id")
        score        = ustate.get("rating", "?")
        lang         = ustate.get("lang", "ru")

        # Delete the user's comment message immediately
        try: await update.message.delete()
        except: pass

        # Edit the "спасибо за оценку" message to show the comment
        if thanks_mid:
            # Escape special chars that break Markdown
            safe = comment.replace("_","\_").replace("*","\*").replace("`","\`").replace("[","\[")
            if lang == "ru":
                edited = (f"🙏 *Спасибо за оценку {score}/5!*\n\n"
                          f"💬 *Ваш отзыв:* _{safe}_")
            else:
                edited = (f"🙏 *Thank you for rating {score}/5!*\n\n"
                          f"💬 *Your review:* _{safe}_")
            try:
                await ctx.bot.edit_message_text(
                    edited, chat_id=uid, message_id=thanks_mid, parse_mode="Markdown")
            except: pass

        # Mark comment as received — but keep the message in to_delete_on_order
        upd_ustate(uid, awaiting_comment=False)
        return

    if SUPPORT_BOT_USERNAME and text in ("🆘 Поддержка", "🆘 Support"):
        lang = ctx.user_data.get("lang", "ru")
        label = "🆘 Открыть поддержку" if lang == "ru" else "🆘 Open Support"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(label, url=f"https://t.me/{SUPPORT_BOT_USERNAME}")]])
        msg = "💬 Напишите нам в поддержку:" if lang == "ru" else "💬 Contact our support:"
        await update.message.reply_text(msg, reply_markup=kb)
        return

    await cmd_start(update, ctx)

def main():
    if not BOT_TOKEN: print("❌ BOT_TOKEN missing"); return
    if not WEBAPP_URL: print("❌ WEBAPP_URL missing"); return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_review, pattern=r"^rev_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    log.info("🍾 AMBAR Customer Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
