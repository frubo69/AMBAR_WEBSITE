import os, uuid
from pathlib import Path
from datetime import datetime, timezone
import aiohttp as _aiohttp

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS
import db
import support_inbox

# Main customer bot token (for sending notifications to users)
MAIN_BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# map: forwarded_message_id -> user_id (in-memory, for direct bot users)
MESSAGE_MAP = {}
# dedup: set of already-processed update_ids
_SEEN_UPDATES: set = set()

async def _notify_user(user_id: int, text: str):
    """Send notification to user via main AMBAR bot."""
    if not MAIN_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                r = await resp.json()
                if not r.get("ok"):
                    print(f"⚠️ Notification failed: {r}")
    except Exception as e:
        print(f"⚠️ Notification error: {e}")

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

    # Без Markdown намеренно: имя клиента приходит от него самого, и одна
    # звёздочка или подчёркивание в нём роняли отправку целиком — вместе с
    # пересылкой сообщения и связкой «ответ → переписка».
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

    # Dedup — skip if we've already processed this update
    if update.update_id in _SEEN_UPDATES:
        return
    _SEEN_UPDATES.add(update.update_id)
    if len(_SEEN_UPDATES) > 10000:
        _SEEN_UPDATES.clear()

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

    # Пишем в ту же переписку, что и приложение: панель оператора читает
    # support_messages, и без этой записи письмо в бот для неё не существует —
    # оно жило только пересылкой в чате админов.
    conv_key = support_inbox.conv_key(user.id)
    try:
        if msg.photo:
            f = await msg.photo[-1].get_file()
            upload_dir = Path(__file__).parent / "uploads" / "support"
            upload_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex[:12]}.jpg"
            await f.download_to_drive(str(upload_dir / fname))
            await support_inbox.capture(
                user.id, channel=support_inbox.CHANNEL_SUPPORT,
                photo_url=f"/uploads/support/{fname}", caption=msg.caption or "")
        else:
            await support_inbox.capture(
                user.id, channel=support_inbox.CHANNEL_SUPPORT,
                text=msg.text or msg.caption or "(файл)")
    except Exception as e:
        print(f"⚠️ Failed to save user message to DB: {e}")

    # Check ban status for this user
    ban_notice = ""
    try:
        user_doc = await db.get_user(user.id)
        if user_doc and user_doc.get("is_banned"):
            banned_at = (user_doc.get("banned_at") or "")[:10]
            ban_reason = user_doc.get("ban_reason") or "—"
            ban_notice = (
                f"\n\n🔴 ПОЛЬЗОВАТЕЛЬ ЗАБЛОКИРОВАН"
                f"\nДата: {banned_at}"
                f"\nПричина: {ban_reason}"
            )
    except Exception as e:
        print(f"⚠️ Ban check failed: {e}")

    # Forward to admins with user info
    for admin_id in ADMIN_IDS:
        try:
            # Send user info first (with ban notice if applicable)
            info_msg = await context.bot.send_message(
                chat_id=admin_id, text=format_user_info(user) + ban_notice)

            # Forward actual user message
            try:
                forwarded = await msg.forward(chat_id=admin_id)
            except Exception as e:
                # Пересылку может запрещать приватность клиента. Копия доходит
                # всегда, а без неё оператор видел карточку без самого вопроса.
                print(f"⚠️ Forward blocked, copying instead: {e}")
                forwarded = await msg.copy(chat_id=admin_id)

            # Map forwarded message to user
            MESSAGE_MAP[forwarded.message_id] = user.id
            MESSAGE_MAP[info_msg.message_id] = user.id
            # Дублируем связку в базу: MESSAGE_MAP умирает вместе с процессом,
            # и после рестарта ответы оператора уходили в никуда.
            # Карточку клиента привязываем наравне с сообщением: оператор
            # отвечает на ту из двух, что попалась под палец, — а ответ на
            # карточку раньше не находил переписку и пропадал молча.
            for mid in (forwarded.message_id, info_msg.message_id):
                try:
                    await db.save_support_map_entry(str(mid), {
                        "user_id": user.id, "conv_key": conv_key,
                        "order_id": "", "channel": "bot",
                    })
                except Exception as e:
                    print(f"⚠️ DB map save failed: {e}")

        except Exception as e:
            print(f"⚠️ Could not forward to admin {admin_id}: {e}")

    # Complaint keyword detection
    if msg.text:
        try:
            import re as _re
            from api_server import _COMPLAINT_KEYWORDS
            from owner_routes import notify_owners
            text_lower = msg.text.lower()
            matched = [kw for kw in _COMPLAINT_KEYWORDS
                       if _re.search(r'\b' + _re.escape(kw), text_lower)]
            if matched:
                uname = user.username or "—"
                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                highlighted = msg.text[:200]
                for kw in matched:
                    highlighted = _re.sub(
                        r'\b(' + _re.escape(kw) + r'\w*)',
                        r"⟨ *\1* ⟩",
                        highlighted,
                        flags=_re.IGNORECASE,
                    )
                await notify_owners("support.complaint",
                    f"⚠️ *Жалоба — ключевые слова*\n"
                    f"Клиент: {full_name} (@{uname})\n"
                    f"Канал: Telegram бот\n\n"
                    f"\"{highlighted}\"",
                    parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Complaint detection failed: {e}")


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

    # База — первая: в ней лежат и переписки из приложения, и прямые письма в
    # бот. Память оставляем запасным вариантом для старых пересылок.
    conv_info = None
    try:
        conv_info = await db.get_support_map_entry(str(replied_id))
    except Exception as e:
        print(f"⚠️ DB lookup failed: {e}")
    user_id = (conv_info or {}).get("user_id") or MESSAGE_MAP.get(replied_id)

    if not user_id:
        # Ответ на сообщение, которое ни с кем не связано. Если отвечали боту,
        # значит целились в клиента и промахнулись — молчать нельзя: оператор
        # уверен, что ответил.
        try:
            to = msg.reply_to_message.from_user
            if to and to.id == context.bot.id:
                await msg.reply_text(
                    "⚠️ Не понял, кому этот ответ: переписка старше перезапуска "
                    "бота или сообщение уже не связано с клиентом.\n"
                    "Ответьте на свежую карточку клиента — или напишите ему из "
                    "панели оператора.")
        except Exception as e:
            print(f"⚠️ orphan reply notice failed: {e}")
        return

    # Известная переписка — сохраняем ответ в базу, чтобы он был в истории
    if conv_info and conv_info.get("conv_key"):
        conv_key = conv_info["conv_key"]
        ts = datetime.now(timezone.utc).isoformat()

        try:
            if msg.photo:
                photo = msg.photo[-1]
                file = await photo.get_file()
                upload_dir = Path(__file__).parent / "uploads" / "support"
                upload_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{uuid.uuid4().hex[:12]}.jpg"
                fpath = upload_dir / fname
                await file.download_to_drive(str(fpath))
                await db.append_support_msg(conv_key, {
                    "role": "operator", "type": "photo",
                    "url": f"/uploads/support/{fname}",
                    "caption": msg.caption or "",
                    "ts": ts,
                })
            else:
                await db.append_support_msg(conv_key, {
                    "role": "operator", "type": "text",
                    "text": msg.text or msg.caption or "(media)",
                    "ts": ts,
                })
            # Save fwd_id mapping so users can reply to operator messages
            op_id = msg.from_user.id if msg.from_user else 0
            if op_id:
                await db.save_support_fwd_id(conv_key, ts, op_id, msg.message_id)
        except Exception as e:
            print(f"⚠️ Failed to save operator reply to DB: {e}")

        order_id = conv_info.get("order_id", "")
        ch = conv_info.get("channel") or ""
        if ch == support_inbox.CHANNEL_SUPPORT:
            # Человек писал прямо сюда — ответ должен прийти в этот же чат,
            # а не «откройте приложение».
            try:
                await msg.copy(chat_id=user_id)
            except Exception as e:
                print(f"⚠️ Could not send reply to user {user_id}: {e}")
        elif ch == support_inbox.CHANNEL_MAIN:
            # Писал в основной бот — переслать копию оттуда нельзя, отправляем
            # текст его же ботом, чтобы ответ пришёл в тот самый чат.
            body = msg.text or msg.caption or ""
            await support_inbox.send_as_main(
                user_id, f"💬 {body}" if body else
                "💬 Поддержка прислала файл — откройте чат @ambar_support_bot")
        else:
            notif = (
                f"💬 *Новое сообщение от поддержки*"
                + (f" по заказу #{order_id}" if order_id else "")
                + f"\n\nОткройте приложение, чтобы прочитать ответ."
            )
            await _notify_user(user_id, notif)
        await _notify_owners_replied(msg, user_id, order_id, conv_key=conv_key)
        return

    # Direct bot conversation — send reply to user DM
    try:
        await msg.copy(chat_id=user_id)
    except Exception as e:
        print(f"⚠️ Could not send reply to user {user_id}: {e}")
        return
    await _notify_owners_replied(msg, user_id, "", conv_key=None)


async def _notify_owners_replied(msg, user_id: int, order_id: str, conv_key: str | None):
    """Owner alert (support.replied toggle): an operator answered a client —
    include WHO replied and the reply text so ambar star sees what was said."""
    try:
        from owner_routes import notify_owners
        u = (await db.get_user(user_id)) or {}
        cname = u.get("first_name") or u.get("name") or str(user_id)
        cuser = u.get("username") or "—"
        op = msg.from_user
        opn = ("@" + op.username) if (op and op.username) else ((op.first_name if op else "") or "оператор")
        reply_txt = (msg.text or msg.caption or ("📷 фото" if msg.photo else "(медиа)"))[:150]
        ctx = f"заказ #{order_id}" if order_id and order_id != "general" else (
            "общий вопрос" if conv_key else "Telegram бот")
        meta = {"conv_key": conv_key,
                "order_id": order_id if order_id and order_id != "general" else ""} if conv_key else None
        await notify_owners(
            "support.replied",
            f"🎧 *Оператор ответил в поддержке*\n"
            f"Клиент: {cname} (@{cuser})\n"
            f"Контекст: {ctx}\n"
            f"Оператор: {opn}\n"
            f"_«{reply_txt}»_",
            meta=meta)
    except Exception as e:
        print(f"⚠️ support.replied notify failed: {e}")


async def post_init(app):
    await db.connect()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))

    # Ответы админов — в отдельной группе, и это важно. Телеграм отдаёт
    # сообщение только первому подошедшему хендлеру внутри группы. Пока оба
    # стояли рядом, любое сообщение клиента, отправленное реплаем — а так
    # отвечают почти все, — попадало в ветку админа, там отсеивалось по
    # is_admin и исчезало совсем: без подтверждения клиенту, без записи в
    # переписку и без пересылки оператору. Разные группы обрабатываются
    # независимо, поэтому теперь каждое сообщение доходит до своей ветки.
    app.add_handler(
        MessageHandler(filters.REPLY & filters.ALL, handle_admin_reply), group=-1
    )

    # user messages
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_user_message)
    )

    print("🤖 Support bot is running...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()