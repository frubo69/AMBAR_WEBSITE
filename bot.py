#!/usr/bin/env python3
"""AMBAR Customer Bot — opens mini app, receives orders, ban check"""
import os, json, logging
import re
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove,
                      WebAppInfo, MenuButtonWebApp, InlineQueryResultPhoto,
                      InlineQueryResultsButton)
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          InlineQueryHandler, ContextTypes, filters)
import db
from config_offices import OFFICE_NAMES, OFFICE_CODES

load_dotenv()
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
OPERATOR_BOT_TOKEN   = os.getenv("OPERATOR_BOT_TOKEN", "")
OPERATOR_IDS         = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]
WEBAPP_URL           = os.getenv("WEBAPP_URL", "")
CATALOG_FILE         = "catalog.json"
STOCK_FILE           = "stock.json"
SUPPORT_BOT_USERNAME = "ambar_support_bot"
from config import OWNER_IDS, MANAGER_IDS          # свои — те же, что и везде
# Домен нужен для картинки в inline-карточке: Telegram забирает её по ссылке.
PUBLIC_ORIGIN        = os.getenv("AMBAR_PUBLIC_ORIGIN", "https://ambar-delivery.com")

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
def _parse_start_arg(arg: str, uid: int):
    """Разбор deep link. → (кто пригласил из клиентов, id оператора, район).

    Приглашать самого себя нельзя, иначе первый же переход по своей ссылке
    записал бы человека приглашённым самим собой."""
    referrer_id = None
    invited_by_operator = None
    invited_district = None
    arg = (arg or "").strip()
    if arg.startswith("ref_"):
        try:
            referrer_id = int(arg[4:])
        except ValueError:
            referrer_id = None
        if referrer_id == uid:
            referrer_id = None
    elif arg in ("op", "biz"):
        # biz — приветствие бизнес-аккаунта: конкретного оператора за ним нет,
        # но канал видно по invited_via.
        invited_by_operator = 0
    elif arg.startswith("op_"):
        who, _, dist = arg[3:].partition("_")
        try:
            invited_by_operator = int(who)
        except ValueError:
            invited_by_operator = None
        if invited_by_operator == uid:
            invited_by_operator = None
        if dist in OFFICE_NAMES:
            invited_district = dist
    return referrer_id, invited_by_operator, invited_district



async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = ctx.user_data.get("lang", "ru")
    name = update.effective_user.first_name

    # Deep links:
    #   /start ref_<id>   — customer-to-customer referral
    #   /start op_<id>    — specific-operator invite
    #   /start op_<id>_<district> — то же, но известно откуда: планшет один на
    #                     всех, поэтому район в ссылке — единственный способ
    #                     понять, чьё это приглашение
    #   /start op         — common (shared) operator invite; stored as invited_by_operator=0
    referrer_id, invited_by_operator, invited_district = _parse_start_arg(
        ctx.args[0] if ctx.args else "", uid)

    # Ban check — silently skip if DB is unavailable
    try:
        if await db.is_banned(uid):
            ban_msg = await update.message.reply_text(
                "🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку — нажмите кнопку ниже.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💬 Написать в поддержку", url="https://t.me/ambar_support_bot")
                ]])
            )
            try:
                await db.set_user_field(uid, last_ban_msg_id=ban_msg.message_id)
            except: pass
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
            log.warning(f"set_chat_menu_button FAILED: {e}")

    text = (
        f"👋 Привет, {name}!\n\n"
        f"Добро пожаловать в *AMBAR* — премиальная доставка алкогольных напитков.\n\n"
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
    # Send welcome message
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    # Upsert user profile in background (doesn't affect UX if it fails)
    try:
        tg_user   = update.effective_user
        full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
        user_fields = dict(
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "",
            full_name=full_name,
            name=full_name,
            username=tg_user.username or "—",
            language_code=tg_user.language_code or "",
        )
        # First-touch attribution for referral and operator invite (don't overwrite).
        # 0 is a valid invited_by_operator value (common link), so compare to None.
        existing = None
        if referrer_id or invited_by_operator is not None:
            existing = await db.get_user(uid)
        if referrer_id and (existing is None or not existing.get("referred_by")):
            user_fields["referred_by"] = referrer_id
            log.info(f"[referral] user {uid} referred by {referrer_id}")
        if invited_by_operator is not None and (existing is None or existing.get("invited_by_operator") is None):
            user_fields["invited_by_operator"] = invited_by_operator
            user_fields["invited_at"] = datetime.now(timezone.utc)
            if (ctx.args[0] if ctx.args else "") == "biz":
                user_fields["invited_via"] = "biz"       # приветствие бизнес-аккаунта
            if invited_district:
                user_fields["invited_district"] = invited_district
            log.info(f"[op-invite] user {uid} invited_by_operator={invited_by_operator}"
                     f"{' district=' + invited_district if invited_district else ''}")
        await db.upsert_user(uid, **user_fields)
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
    try:
        if await db.is_banned(uid):
            await update.message.reply_text(
                "🚫 Ваш аккаунт заблокирован.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💬 Написать в поддержку", url="https://t.me/ambar_support_bot")
                ]])
            )
            return
    except Exception as e:
        log.warning(f"ban check failed: {e}")

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


async def on_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Клиент поделился номером из мини-аппа (WebApp.requestContact).

    Единственный достоверный источник номера: Telegram присылает контакт сюда,
    в бота, а не в браузер — подделать его на стороне клиента нельзя.
    Обязательна сверка contact.user_id с отправителем: иначе можно переслать
    чужую визитку и «подтвердить» чужой номер."""
    msg = update.effective_message
    c = getattr(msg, "contact", None)
    if not c:
        return
    uid = update.effective_user.id
    if c.user_id != uid:
        log.warning(f"[phone] uid={uid} прислал чужой контакт (user_id={c.user_id}) — игнорируем")
        return
    digits = re.sub(r"\D", "", c.phone_number or "")
    if len(digits) < 8:
        return
    try:
        await db.set_user_field(
            uid,
            phone_verified=digits,
            phone_verified_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.upsert_user(uid, phone=digits)      # заодно в общий список номеров
        log.info(f"[phone] uid={uid} подтвердил номер ···{digits[-4:]}")
    except Exception as e:
        log.error(f"[phone] сохранение номера uid={uid} не удалось: {e}")


# ── inline: приглашение кнопкой, а не ссылкой ────────────────────────────────
# Клиенты, которые заказывают по телефону, боятся ссылок от незнакомых номеров —
# и правильно делают. Поэтому оператор не копирует URL, а набирает в чате имя
# бота и выбирает готовую карточку: уходит нормальное сообщение с фото и
# кнопкой, от его же имени. Ссылка живёт внутри кнопки, вместе с кодом
# приглашения, так что переход по-прежнему засчитывается оператору.
#
# Отвечаем только своим: inline-запрос может прислать кто угодно, а карточка
# выглядит официально — чужим такое в руки давать нельзя.
INLINE_TEXT = {
    "ru": ("<b>AMBAR — премиальная доставка по Дубаю</b>\n\n"
           "Заказ по телефону работает как работал. В приложении — быстрее "
           "и на 5% дешевле: скидка действует только здесь.\n\n"
           "Каталог, адрес и история заказов в одном месте."),
    "en": ("<b>AMBAR — premium delivery across Dubai</b>\n\n"
           "Ordering by phone works exactly as before. In the app it is faster "
           "and 5% cheaper — the discount is in-app only.\n\n"
           "Catalogue, address and order history in one place."),
}
INLINE_BTN = {"ru": "Открыть AMBAR", "en": "Open AMBAR"}


def _inline_district(q: str):
    """Район из того, что оператор набрал после имени бота. Планшет общий, и
    без этого непонятно, чьё приглашение сработало."""
    q = (q or "").strip().lower()
    if not q:
        return None
    for oid, code in OFFICE_CODES.items():
        if q == oid or q == code.lower() or q in OFFICE_NAMES[oid].lower():
            return oid
    return None


async def on_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    if iq is None:
        return
    uid = iq.from_user.id
    if uid not in set(OPERATOR_IDS) | set(MANAGER_IDS) | set(OWNER_IDS):
        # Посторонним карточку не отдаём, но и в тупик не отправляем: кнопка
        # уводит в самого бота по общей ссылке.
        log.info(f"[inline] посторонний {uid} набрал имя бота — отдали только кнопку")
        await iq.answer([], cache_time=300, is_personal=True,
                        button=InlineQueryResultsButton(text="Открыть AMBAR",
                                                        start_parameter="op"))
        return

    dist = _inline_district(iq.query)
    me = (await ctx.bot.get_me()).username
    payload = f"op_{uid}" + (f"_{dist}" if dist else "")
    url = f"https://t.me/{me}?start={payload}"
    where = f" · {OFFICE_CODES[dist]} {OFFICE_NAMES[dist]}" if dist else ""

    results = []
    for lang in ("ru", "en"):
        results.append(InlineQueryResultPhoto(
            id=f"invite_{lang}_{dist or 'all'}",
            # Именно JPEG и именно лёгкий: Telegram забирает картинку по ссылке
            # сам, и PNG на два мегабайта он либо не примет, либо будет тянуть
            # на глазах у оператора.
            photo_url=f"{PUBLIC_ORIGIN}/promo_invite_{lang}.jpg",
            thumbnail_url=f"{PUBLIC_ORIGIN}/promo_invite_{lang}.jpg",
            photo_width=1280, photo_height=640,
            title=("Приглашение по-русски" if lang == "ru" else "Invite in English") + where,
            description=("Фото, текст и кнопка — без ссылки в тексте"
                         if lang == "ru" else "Photo, text and a button — no raw link"),
            caption=INLINE_TEXT[lang],
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(INLINE_BTN[lang], url=url)]]),
        ))
    # is_personal + cache_time=0: в ссылке код конкретного оператора, чужому
    # её показывать нельзя ни секунды.
    await iq.answer(results, cache_time=0, is_personal=True)
    log.info(f"[inline] оператор {uid} взял карточку"
             f"{' по району ' + dist if dist else ' без района'}")


# Кто владеет соединением: в business_message падают и сообщения самого
# владельца, а здороваться с ним не надо.
_BIZ_OWNERS = {}


async def on_business_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Приветствие тому, кто впервые написал в бизнес-аккаунт.

    Штатное приветствие Telegram Business — обычное сообщение человека, кнопку
    под него повесить нельзя, остаётся ссылка в тексте. А ссылок клиенты и
    боятся. Кнопку умеет только бот, поэтому здороваемся отсюда — той же
    карточкой, что оператор отправляет вручную.

    Пока бот не подключён в «Telegram Business → Чат-боты», обработчик просто
    молчит: таких апдейтов не приходит."""
    msg = update.business_message
    if msg is None or msg.from_user is None or msg.from_user.is_bot:
        return
    cid = msg.business_connection_id
    owner = _BIZ_OWNERS.get(cid)
    if owner is None:
        try:
            owner = (await ctx.bot.get_business_connection(cid)).user.id
            _BIZ_OWNERS[cid] = owner
        except Exception as e:
            log.warning(f"[biz] не узнали владельца соединения: {e}")
            return
    uid = msg.from_user.id
    if uid == owner:                       # это владелец пишет клиенту
        return
    if await db.biz_greeted(uid):          # второй раз не здороваемся
        return

    lang = "ru" if (msg.from_user.language_code or "ru").startswith("ru") else "en"
    try:
        me = (await ctx.bot.get_me()).username
        await ctx.bot.send_photo(
            chat_id=msg.chat.id,
            business_connection_id=cid,
            photo=f"{PUBLIC_ORIGIN}/promo_invite_{lang}.jpg",
            caption=INLINE_TEXT[lang],
            parse_mode="HTML",
            # Именно url, а не web_app: в сообщениях от имени бизнес-аккаунта
            # Telegram разрешает только url, login_url и callback-кнопки.
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(INLINE_BTN[lang], url=f"https://t.me/{me}?start=biz")]]),
        )
        await db.mark_biz_greeted(uid, lang=lang,
                                  username=msg.from_user.username or "—")
        log.info(f"[biz] поздоровались с {uid} ({lang})")
    except Exception as e:
        log.warning(f"[biz] приветствие {uid}: {e}")


def main():
    if not BOT_TOKEN:  print("❌ BOT_TOKEN missing");  return
    if not WEBAPP_URL: print("❌ WEBAPP_URL missing"); return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_review, pattern=r"^rev_"))
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))
    app.add_handler(InlineQueryHandler(on_inline))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, on_business_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    log.info("🍾 AMBAR Customer Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
