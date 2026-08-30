#!/usr/bin/env python3
"""
AMBAR Owner/Manager Bot — @ambar_manage_bot

Minimal Telegram bot whose entire job is to launch the owner dashboard
miniapp at https://owner.ambar-delivery.com/. Kept intentionally thin:
no business logic, no DB, no state. Anything beyond "open the panel"
lives in the miniapp itself.

Что здесь всё-таки есть, кроме кнопки
-------------------------------------
Одно решение — согласование списания. Списание приходит владельцу снимком с
двумя кнопками, и до его ответа товар со склада не вычитается. Отвечать на
вопрос надо там, где он задан: гонять человека в приложение ради двух кнопок
под фотографией, которую он и так видит, — лишний шаг в единственном месте,
где решение занимает секунду. Кнопку обрабатывает тот процесс, который держит
этот токен, то есть этот; поэтому здесь появились `db` и одна запись в неё.
Тексты у бота и у приложения общие — `writeoff_msg`.

Access model
------------
- OWNER_IDS: hardcoded in config.py, always allowed.
- MANAGER_IDS: comma-separated env var AMBAR_MANAGER_IDS, same access.
- Anyone else: polite "access restricted" reply; no data leaked.

The same OWNER_IDS ∪ MANAGER_IDS gate is enforced server-side on every
/api/owner/* endpoint via require_owner (owner_auth.py). This bot's gate
is just UX — the API would reject unauthorized callers anyway.
"""
import os
import logging

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
    MenuButtonDefault,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import db
import writeoff_msg as wm

load_dotenv()

OWNER_BOT_TOKEN = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
OWNER_WEBAPP_URL = os.getenv(
    "OWNER_WEBAPP_URL", "https://owner.ambar-delivery.com/"
)
# OWNER_IDS hardcoded here as a small set for defense-in-depth; the real
# enforcement is server-side in require_owner. Keep in sync with config.py.
OWNER_IDS = {686932322, 982022772}
MANAGER_IDS = {
    int(x.strip())
    for x in os.getenv("AMBAR_MANAGER_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("owner-bot")


def is_allowed(uid: int) -> bool:
    return uid in OWNER_IDS or uid in MANAGER_IDS


def launcher_keyboard() -> InlineKeyboardMarkup:
    """Inline button that opens the miniapp inside Telegram's WebView."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "📊 Открыть панель",
            web_app=WebAppInfo(url=OWNER_WEBAPP_URL),
        )]]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопку получает только тот, кто в списке.

    Раньше её отдавали всем: доступ всё равно проверяется и в приложении, и на
    каждом запросе к серверу, так что нажать было безопасно. Но безопасно ≠
    правильно. Посторонний, нажав кнопку, грузил приложение, получал семь
    отказов подряд и поднимал тревогу — а главное, узнавал, что за этим ботом
    что-то есть.

    Теперь чужому не отдаётся ничего, кроме двух слов. Ни названия панели, ни
    кнопки, ни намёка, что список доступа существует и в него можно попроситься.
    """
    uid = update.effective_user.id
    allowed = is_allowed(uid)
    log.info(
        "/start uid=%s @%s allowed=%s",
        uid, update.effective_user.username, allowed,
    )
    if not allowed:
        await _track(await update.message.reply_text("Нет доступа"))
        return
    await _track(await update.message.reply_text(
        "Панель владельца AMBAR",
        reply_markup=launcher_keyboard(),
    ))


async def _track(msg):
    """Записать сообщение в реестр чата владельца.

    По этому реестру переписка стирается на 47-м часу и целиком — по тревоге
    (owner_sweep). Пишем и свои ответы, и входящие: в приватном чате телеграм
    разрешает боту удалять и те, и другие, но только если знаешь их номера."""
    try:
        if msg is not None:
            await db.owner_msg_add(msg.chat_id, msg.message_id, "bot")
    except Exception as e:
        log.debug("реестр: %s", e)


async def on_any_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первым делом — запомнить входящее, что бы это ни было."""
    await _track(update.effective_message)


async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое сообщение — снова кнопка. Чужому — те же два слова."""
    if not update.effective_user:
        return
    if not is_allowed(update.effective_user.id):
        await _track(await update.message.reply_text("Нет доступа"))
        return
    await _track(await update.message.reply_text(
        "Панель владельца AMBAR",
        reply_markup=launcher_keyboard(),
    ))


async def on_writeoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Решение по списанию — прямо под фотографией.

    Ответить телеграму надо в любом случае и первым делом: без answer у
    нажавшего крутится часик на кнопке, и он жмёт второй раз.

    Переход разрешён только из «ждёт» — это проверяет сама база. Поэтому два
    владельца, нажавшие одновременно, получат один результат, а второй увидит,
    кто решил до него, вместо молчаливой перезаписи чужого решения."""
    q = update.callback_query
    hit = wm.parse_cb(q.data if q else "")
    if not hit:
        return
    wid, ok = hit
    uid = update.effective_user.id if update.effective_user else 0
    if not is_allowed(uid):
        await q.answer("Нет доступа", show_alert=True)
        return
    # Отвечаем сразу и молча: иначе на кнопке крутится часик и по ней жмут
    # второй раз. Результат человек увидит там, где смотрит, — в самой подписи
    # под фотографией; всплывающее «Согласовано» до записи в базу было бы
    # обещанием, которое ещё не факт что сбудется.
    await q.answer()
    name = (update.effective_user.full_name or "").strip()[:60]
    try:
        doc = await db.writeoff_decide(wid, ok, uid, name)
    except Exception as e:
        log.error("списание %s: база недоступна: %s", wid, e)
        await q.answer("База недоступна — решите в панели", show_alert=True)
        return
    if not doc:
        # Уже решено — вторым нажатием ничего не меняем, но кнопки в этом чате
        # снимаем: висят они именно потому, что решал кто-то другой.
        cur = await db.writeoff_get(wid) or {}
        by = cur.get("decided_by_name") or "другой владелец"
        try:
            await q.edit_message_caption(
                caption=wm.decided_caption(q.message.caption_html or "",
                                           (cur.get("state") == "ok"), by),
                parse_mode="HTML")
        except Exception:
            pass
        return
    await _writeoff_after(context, doc, ok, name, q)


async def _writeoff_after(context, doc: dict, ok: bool, by_name: str, q=None):
    """Снять кнопки у всех, кому вопрос задавали, и ответить водителю.

    Кнопка, которая больше ничего не делает, хуже отсутствующей: по ней жмут и
    получают отказ, не понимая, что вопрос давно закрыт. А водитель ждёт ответа
    — от него зависит, зачтён ему бой или эти бутылки спросят с него в
    пересчёте."""
    # Обычно сообщений столько, скольким владельцам вопрос задавали. Но если
    # их не записали (упала база в момент отправки), под руками всё равно есть
    # одно — то, по кнопке которого только что нажали. Оставить на нём живую
    # кнопку нельзя ни в каком случае.
    msgs = doc.get("msgs") or []
    if not msgs and q is not None and q.message:
        msgs = [{"chat_id": q.message.chat_id, "message_id": q.message.message_id}]
    cap = None
    for m in msgs:
        try:
            if cap is None:
                cur = await context.bot.edit_message_reply_markup(
                    chat_id=m["chat_id"], message_id=m["message_id"], reply_markup=None)
                cap = wm.decided_caption(cur.caption_html or "", ok, by_name)
                await context.bot.edit_message_caption(
                    chat_id=m["chat_id"], message_id=m["message_id"],
                    caption=cap, parse_mode="HTML")
            else:
                await context.bot.edit_message_caption(
                    chat_id=m["chat_id"], message_id=m["message_id"],
                    caption=cap, parse_mode="HTML")
        except Exception as e:
            log.info("кнопки у %s не сняты: %s", m.get("chat_id"), str(e)[:80])
    tid = None
    try:
        import config_staff as staff
        tid = staff.DRIVER_IDS.get((doc.get("by") or "").strip())
    except Exception as e:
        log.warning("список водителей не прочитан: %s", e)
    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if tid and token:
        try:
            from telegram import Bot
            from datetime import datetime, timezone
            sent = await Bot(token).send_message(
                tid, wm.driver_text(doc.get("name", ""), int(doc.get("qty") or 0),
                                    ok, doc.get("decided_note", "")))
            # В реестр чата водителя: это сообщение шлёт STAR-бот, но приходит
            # оно в водительский чат — и скрытый режим водителя должен его
            # стирать. Без записи решение по списанию оставалось бы в чате.
            if sent:
                await db.drv_msg_add(int(tid), int(sent.message_id),
                                     datetime.now(timezone.utc))
        except Exception as e:
            log.warning("водителю не ушло: %s", e)


async def post_init(application: Application):
    """Кнопка приложения — только у тех, кто в списке.

    Общая кнопка меню ставится сразу всем, кто откроет бота: телеграм показывает
    её ещё до первого сообщения. Поэтому общую снимаем, а каждому из своих
    ставим личную — телеграм умеет и так."""
    try:
        await db.connect()
    except Exception as e:
        log.warning("база недоступна — согласование списаний работать не будет: %s", e)
    try:
        await application.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        log.info("общая кнопка меню снята — она видна посторонним")
    except Exception as e:
        log.warning("не удалось снять общую кнопку: %s", e)
    for uid in sorted(OWNER_IDS | MANAGER_IDS):
        try:
            await application.bot.set_chat_menu_button(
                chat_id=uid,
                menu_button=MenuButtonWebApp(
                    text="Панель",
                    web_app=WebAppInfo(url=OWNER_WEBAPP_URL),
                ),
            )
        except Exception as e:
            # Не открывал бота — телеграму некуда ставить. Появится при /start.
            log.info("кнопка для %s не поставлена: %s", uid, str(e)[:80])
    log.info("кнопка приложения выдана %s своим", len(OWNER_IDS | MANAGER_IDS))


def main():
    if not OWNER_BOT_TOKEN:
        raise RuntimeError(
            "AMBAR_OWNER_BOT_TOKEN is not set — add it to /opt/ambar/.env"
        )

    app = (
        Application.builder()
        .token(OWNER_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(MessageHandler(filters.ALL, on_any_update), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_writeoff, pattern=r"^wo:(ok|no):"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_any_message))

    log.info("🔐 AMBAR Owner Bot starting — miniapp: %s", OWNER_WEBAPP_URL)
    log.info("   allowed owners: %s", sorted(OWNER_IDS))
    log.info("   allowed managers: %s", sorted(MANAGER_IDS) or "(none)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
