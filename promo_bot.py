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
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      InlineQueryResultPhoto, InlineQueryResultCachedPhoto,
                      InlineQueryResultsButton)
from telegram.constants import ChatMemberStatus
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          InlineQueryHandler, ChosenInlineResultHandler,
                          ContextTypes, filters)
import config_promo as promo
import db

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
        sent = await msg.reply_photo(
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
    title = msg.chat.title or str(cid)
    _stats[cid] = (title, _stats.get(cid, (title, 0))[1] + 1)
    # Строка в журнал: когда, где, на чей вопрос и — главное — ссылка на само
    # сообщение. Владелец спрашивает не «сколько раз сработало», а «покажи, из
    # какого разговора пришёл этот человек».
    try:
        await db.promo_log_add({
            "kind": "reply", "chat_id": cid, "chat_key": abs(cid),
            "title": title, "username": msg.chat.username or "",
            "msg_id": sent.message_id, "ask_id": msg.message_id,
            "ask_text": (msg.text or "")[:200],
            "ask_by": uid, "ask_name": (msg.from_user.full_name or "").strip(),
            "ask_user": msg.from_user.username or "",
            "lang": lang, "link": _msg_link(msg.chat, sent.message_id)})
    except Exception as e:
        log.warning(f"[promo] журнал ответа: {e}")
    # В ссылке едет abs(id) — под тем же ключом чат ложится в реестр, иначе в
    # отчёте вместо названия будет голое число.
    try:
        await db.promo_chat_seen(abs(cid), title)
    except Exception as e:
        log.warning(f"[promo] реестр чатов: {e}")
    log.info(f"[promo] {title} · {uid} · «{msg.text[:60]}»")


def _msg_link(chat, mid: int) -> str:
    """Ссылка на сообщение в чате.

    У публичного чата она открывается у кого угодно, у закрытой супергруппы —
    только у того, кто в ней состоит. Владелец в них состоит: он их сам и
    находил. Где ссылку собрать нельзя (обычная группа без id вида -100),
    отдаём пусто — лучше без ссылки, чем битая."""
    if getattr(chat, "username", ""):
        return f"https://t.me/{chat.username}/{mid}"
    cid = str(getattr(chat, "id", ""))
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{mid}"
    return ""


async def _photo_id(ctx, path: str):
    """file_id картинки в телеграме, загрузив её туда при первой надобности.

    Карточку с фотографией по ссылке телеграм собирает, сходив за файлом к нам
    прямо в момент набора. Полмегабайта через океан он ждать не будет: карточка
    появляется пустой или не появляется вовсе. Отданный однажды файл получает
    вечный id, и дальше карточка собирается мгновенно и из ничего.

    Загружаем себе же в избранное и сразу убираем сообщение — id от удаления
    не портится."""
    fid = None
    try:
        fid = await db.tg_file_get(path)
    except Exception as e:
        log.warning(f"[promo] file_id из базы: {e}")
    if fid:
        return fid
    who = OWNER_IDS + [i for i in promo.POSTER_IDS if i not in OWNER_IDS]
    if not os.path.exists(path) or not who:
        return None
    # Первым в списке может стоять тот, кто этого бота ни разу не открывал, —
    # телеграм на такого отвечает «chat not found», и картинка не грузилась
    # вовсе. Пробуем всех, кому вообще можно писать.
    for chat in who:
        try:
            with open(path, "rb") as f:
                m = await ctx.bot.send_photo(chat, photo=f,
                                             caption=f"служебная загрузка · {path}")
            fid = m.photo[-1].file_id
            try:
                await m.delete()
            except Exception:
                pass
            await db.tg_file_set(path, fid)
            log.info(f"[promo] картинка {path} загружена в телеграм")
            return fid
        except Exception as e:
            log.warning(f"[promo] {path} через {chat}: {e}")
    log.error(f"[promo] не удалось загрузить {path} ни одному из своих")
    return None


async def on_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Рекламный пост: набрал имя бота в чате — выбрал карточку — она ушла.

    Пост и автоответ — разные интеграции и разные деньги, поэтому у поста своя
    метка. Площадку задаёт то, что набрано после имени бота: «@ambarpr_bot
    marina» пометит переходы как marina. Без этого все посты слились бы в один
    канал, и вопрос «какой чат окупается» остался бы без ответа."""
    iq = update.inline_query
    if iq is None:
        return
    uid = iq.from_user.id
    if uid not in set(OWNER_IDS) | set(promo.POSTER_IDS):
        # Посторонним карточку не отдаём: она выглядит официально, и любой
        # желающий мог бы рассылать её от нашего имени.
        log.info(f"[promo] посторонний {uid} набрал имя бота")
        await iq.answer([], cache_time=300, is_personal=True,
                        button=InlineQueryResultsButton(text="Открыть AMBAR",
                                                        start_parameter="post_all"))
        return

    tag = promo.slug(iq.query)
    url = f"https://t.me/{MAIN_BOT}?start=post_{tag}"
    results = []
    for lang in ("ru", "en"):
        # Карточку языка показываем, только если её картинка правда лежит:
        # без файла результат окажется дырой без объяснений.
        path = promo.POST_IMG[lang]
        if not os.path.exists(path):
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(promo.BTN[lang], url=url)]])
        fid = await _photo_id(ctx, path)
        if fid:
            results.append(InlineQueryResultCachedPhoto(
                id=f"post_{lang}_{tag}", photo_file_id=fid,
                title=f"Рекламный пост · {lang.upper()}",
                description=f"метка «{tag}»",
                caption=promo.POST_TEXT[lang], parse_mode="HTML",
                reply_markup=kb))
        else:
            # Запасной путь на случай, когда загрузить файл не вышло: пусть
            # телеграм сходит по ссылке сам. Превью отдельным файлом — за
            # тяжёлым он идёт неохотно и часто показывает пустоту.
            thumb = path.replace(".jpg", "_thumb.jpg")
            results.append(InlineQueryResultPhoto(
                id=f"post_{lang}_{tag}",
                photo_url=f"{PUBLIC_ORIGIN}/{path}",
                thumbnail_url=f"{PUBLIC_ORIGIN}/"
                              f"{thumb if os.path.exists(thumb) else path}",
                photo_width=1536, photo_height=1024,
                title=f"Рекламный пост · {lang.upper()}",
                description=f"метка «{tag}»",
                caption=promo.POST_TEXT[lang], parse_mode="HTML",
                reply_markup=kb))
    # cache_time=0: метка меняется от запроса к запросу, закэшированный ответ
    # отдал бы чужую.
    if not results:
        log.error("[promo] нет ни одной картинки поста — карточку не собрать")
    await iq.answer(results, cache_time=0, is_personal=True)
    log.info(f"[promo] пост {uid} · метка «{tag}» · карточек {len(results)}")


async def on_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Карточку выбрали — значит пост опубликован. Пишем это в журнал.

    Что телеграм даёт и чего не даёт. Даёт: момент, метку, язык и того, кто
    опубликовал. Не даёт ссылки: у инлайн-сообщения нет ни чата, ни номера —
    только внутренний inline_message_id, из которого ссылку не собрать. Поэтому
    у постов есть время и метка, но нет перехода к самому посту, и метка —
    единственный способ отличить одну площадку от другой.

    Приходит это только если в BotFather включена инлайн-обратная связь
    (/setinlinefeedback → 100%). Пока она выключена, журнал постов пуст, и об
    этом честно сказано на экране рекламы."""
    r = update.chosen_inline_result
    if r is None:
        return
    parts = (r.result_id or "").split("_", 2)      # post_<lang>_<tag>
    lang = parts[1] if len(parts) > 2 else ""
    tag = parts[2] if len(parts) > 2 else promo.slug(r.query)
    try:
        await db.promo_log_add({
            "kind": "post", "tag": tag, "lang": lang,
            "by": r.from_user.id, "by_name": (r.from_user.full_name or "").strip(),
            "by_user": r.from_user.username or "",
            "inline_id": r.inline_message_id or ""})
    except Exception as e:
        log.warning(f"[promo] журнал поста: {e}")
    log.info(f"[promo] пост опубликован · метка «{tag}» · {lang}")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Кто-то открыл самого рекламного бота — уводим в основной.

    Отдаём ту же карточку, что уходит инлайном: человек, попавший сюда по
    ссылке из поста, должен увидеть пост, а не короткую подсказку для чата.
    Заодно, если это владелец, картинка поста заодно загружается в телеграм:
    первым бот написать не может, и до «Start» ему просто некуда её отдать."""
    if update.effective_user.id in set(OWNER_IDS) | set(promo.POSTER_IDS):
        for lang_ in ("ru", "en"):
            await _photo_id(ctx, promo.POST_IMG[lang_])
    # Язык — по имени, но карточка существует только та, у которой есть
    # картинка: английской пока нет, и инлайн её тоже не показывает. Отдать
    # вместо поста короткую подсказку для чатов — не то же сообщение.
    lang = _lang(update.effective_user.first_name or "")
    if not os.path.exists(promo.POST_IMG.get(lang, "")):
        lang = next((l for l in ("ru", "en") if os.path.exists(promo.POST_IMG[l])), "")
    # Метка post_direct: переход считается постовым, но отличим от чатовых.
    url = f"https://t.me/{MAIN_BOT}?start=post_direct"
    if not lang:
        log.error("[promo] нет ни одной картинки поста — на /start ответить нечем")
        await update.message.reply_text(promo.TEXT["ru"], parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(promo.BTN["ru"], url=url)]]))
        return
    path = promo.POST_IMG[lang]
    fid = await _photo_id(ctx, path)
    await update.message.reply_photo(
        photo=fid or f"{PUBLIC_ORIGIN}/{path}",
        caption=promo.POST_TEXT[lang], parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(promo.BTN[lang], url=url)]]))


async def cmd_chats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Где бот стоит и сколько раз ответил — владельцу."""
    if update.effective_user.id not in OWNER_IDS:
        return
    if not _stats:
        await update.message.reply_text("Пока ни одного ответа."); return
    rows = "\n".join(f"• {t} — {n}" for t, n in
                     sorted(_stats.values(), key=lambda x: -x[1]))
    await update.message.reply_text(f"Ответов с перезапуска:\n{rows}")


async def post_init(app):
    await db.connect()


def main():
    if not PROMO_BOT_TOKEN:
        print("❌ PROMO_BOT_TOKEN missing"); return
    app = Application.builder().token(PROMO_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("chats", cmd_chats, filters=filters.ChatType.PRIVATE))
    app.add_handler(InlineQueryHandler(on_inline))
    app.add_handler(ChosenInlineResultHandler(on_chosen))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info(f"📣 AMBAR Promo Bot started · ведёт в @{MAIN_BOT}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
