#!/usr/bin/env python3
"""
AMBAR — бот водителя.

Делает ровно одно: открывает приложение. Заказы, доставка, расходы — всё внутри
мини-аппа, здесь только вход и понятный ответ тому, кого в списке нет.

Список водителей живёт в AMBAR_DRIVER_IDS (config_staff). Бот не решает, кого
пускать — он только объясняет; пускает сервер, проверяя подпись initData этим же
токеном. Значит вход в приложение водителя невозможен из операторского бота.
"""
import asyncio
import logging
import os
import re
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
logging.getLogger("httpx").setLevel(logging.WARNING)   # адрес запроса содержит токен — в журнал ему нельзя
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
    # Реестр водителей — из базы, сразу и дальше по кругу: привязал телефон
    # в другой службе — здесь узнают за полминуты.
    try:
        await staff.sync(force=True)
    except Exception as e:
        log.warning(f"реестр при старте: {e}")
    asyncio.get_event_loop().create_task(staff.roster_loop(30))
    try:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Панель", web_app=WebAppInfo(url=DRIVER_WEBAPP_URL)))
        log.info("кнопка приложения установлена")
    except Exception as e:
        log.warning(f"set_chat_menu_button: {e}")


# ── привязка телефона по одноразовой ссылке (15 сен 2026) ───────────────────
# Водителя заводит владелец в STAR: имя, район, и STAR выдаёт ссылку вида
# t.me/бот?start=drv_<код>. Человек открывает её — и его аккаунт становится
# этим водителем. Код одноразовый, живёт CODE_TTL и гасится первым входом;
# второй по той же ссылке не войдёт. Перебор кодов текстом отбивается счётчиком
# попыток. Кто привязался — владельцу приходит сообщение.
CODE_TTL = timedelta(days=7)
CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
_ATTEMPTS: dict = {}                       # uid → времена неудачных попыток
ATTEMPTS_MAX, ATTEMPTS_WIN = 5, timedelta(hours=1)


def _norm_code(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _too_many(uid: int) -> bool:
    now = datetime.now(timezone.utc)
    hist = [t for t in _ATTEMPTS.get(uid, []) if now - t < ATTEMPTS_WIN]
    _ATTEMPTS[uid] = hist
    return len(hist) >= ATTEMPTS_MAX


def _failed(uid: int):
    _ATTEMPTS.setdefault(uid, []).append(datetime.now(timezone.utc))


async def bind_code(uid: int, code: str, tg: dict) -> tuple:
    """('ok', запись) — привязано; ('bad', None) — кода нет, он использован
    или просрочен; ('rate', None) — слишком много попыток."""
    code = _norm_code(code)
    if _too_many(uid):
        return "rate", None
    if not CODE_RE.match(code):
        _failed(uid); return "bad", None
    row = await db.get_driver_by_code(code)
    at = (row or {}).get("code_at")
    if at is not None and getattr(at, "tzinfo", None) is None:
        at = at.replace(tzinfo=timezone.utc)
    if not row or not at or datetime.now(timezone.utc) - at > CODE_TTL:
        _failed(uid); return "bad", None
    linked = await db.link_driver(code, uid, tg)
    if not linked:
        _failed(uid); return "bad", None
    _ATTEMPTS.pop(uid, None)
    return "ok", linked


async def _tell_owners(text: str):
    """Владельцам в их бот: кто привязал телефон. Без id."""
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    try:
        from config import OWNER_IDS
    except Exception:                          # noqa: BLE001
        OWNER_IDS = set()
    if not token or not OWNER_IDS:
        return
    from telegram import Bot
    try:
        async with Bot(token) as b:
            for cid in sorted(OWNER_IDS):
                try:
                    await b.send_message(cid, text)
                except Exception as e:         # noqa: BLE001
                    log.warning(f"владельцу не ушло: {e}")
    except Exception as e:                     # noqa: BLE001
        log.warning(f"бот владельца: {e}")


async def _bind(update: Update, code: str):
    u = update.effective_user
    status, row = await bind_code(u.id, code, {"first_name": u.first_name or "",
                                               "username": u.username or ""})
    if status == "rate":
        await update.message.reply_text("Слишком много попыток. Попробуйте через час.")
        return
    if status != "ok":
        await update.message.reply_text(
            "Ссылка недействительна или уже использована.\nПопросите у менеджера новую.")
        log.info(f"привязка отклонена: {u.id} (@{u.username})")
        return
    try:
        await staff.sync(force=True)
    except Exception as e:                     # noqa: BLE001
        log.warning(f"реестр после привязки: {e}")
    me = staff.driver_or_test(u.id) or {}
    name = row.get("name") or me.get("name") or "водитель"
    where = "" if me.get("test") else (me.get("district_name") or "")
    sent = await update.message.reply_text(
        f"Готово: вы подключены как {name}" + (f" · {where}" if where else "") + ".\n\n"
        "Откройте панель — там смена, заказы и расходы.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Открыть панель", web_app=WebAppInfo(url=DRIVER_WEBAPP_URL))]]))
    await _remember(sent)
    log.info(f"привязан: {name} ← {u.id} (@{u.username})")
    await _tell_owners(f"🔗 {name}: телефон привязан — {u.first_name or ''}"
                       + (f" (@{u.username})" if u.username else ""))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Код, присланный текстом, — тот же вход, что и ссылка. Всё остальное
    молчит: в скрытом режиме чат прикидывается игрой."""
    if not update.message or not update.effective_user:
        return
    code = _norm_code(update.message.text)
    if len(code) != 8:
        return
    if staff.driver_by_tg(update.effective_user.id):
        return
    await _bind(update, code)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Ссылка-приглашение: /start drv_<код>.
    args = ctx.args or []
    if args and str(args[0]).lower().startswith("drv_"):
        await _bind(update, str(args[0])[4:])
        return
    try:
        await staff.sync(force=True)
    except Exception as e:                     # noqa: BLE001
        log.warning(f"реестр: {e}")
    me = staff.driver_or_test(uid)
    if not me:
        # Без id в ответе: номер аккаунта чужому ничего не должен говорить, а
        # доступ теперь выдают ссылкой, а не по id (владелец, 15 сен 2026).
        await update.message.reply_text(
            "Этот аккаунт не подключён.\n\n"
            "Откройте ссылку-приглашение от менеджера или пришлите сюда код из неё.")
        log.info(f"вход без доступа: {uid} (@{update.effective_user.username})")
        return

    await _remember(update.message)
    # Ответ нарочно ни о чём: ни имени, ни района, ни слова о заказах. В
    # скрытом режиме чат прикидывается игрой, и /start, набранный чужой рукой,
    # не должен выдать, чей это телефон и чем он занят (владелец, 10 сен 2026).
    sent = await update.message.reply_text(
        "Для начала откройте панель.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Открыть панель", web_app=WebAppInfo(url=DRIVER_WEBAPP_URL))]]))
    await _remember(sent)
    # Про трансляцию здесь больше не говорим: её включают в отдельном боте
    # геопозиции (владелец, 10 сен 2026: «оно уже неактуально»), а этот чат
    # стирается скрытым режимом, и трансляции в нём не место.
    log.info(f"вход: {me['name']} ({uid})")


# ── где водитель ───────────────────────────────────────────────────────────
# Живую трансляцию водитель включает сам: скрепка → Геопозиция → Транслировать.
# Дальше телефон шлёт точки сюда даже при свёрнутом телеграме, а у водителя всё
# это время висит его собственная трансляция с таймером и кнопкой «остановить».
#
# Почему не геолокация из мини-аппа: браузер отдаёт координаты, только пока
# приложение открыто на экране. За рулём оно свёрнуто — то есть работало бы
# ровно тогда, когда не нужно.
from bizday import SHIFT_START_HOUR      # граница суток одна на всю систему (bizday)
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
    me = staff.driver_or_test(update.effective_user.id)
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
    chat, mid = update.effective_chat.id, getattr(msg, "message_id", 0)
    if stop:
        # Конец прежнего сообщения после «включил заново» — не выключение.
        import geo_watch
        if await geo_watch.old_stream_end(me["name"], chat, mid):
            log.info(f"прежняя трансляция кончилась, новая идёт: {me['name']}")
            return
    try:
        await db.driver_pos_set(me["name"], _biz_day(), loc.latitude, loc.longitude,
                                now, until=until, stop_live=stop,
                                acc=getattr(loc, "horizontal_accuracy", None),
                                live=(chat, mid) if period else None)
    except Exception as e:
        log.warning(f"точка {me['name']} не записана: {e}")
        return
    # Включил или выключил трансляцию — старший узнаёт в ту же секунду. Не на
    # каждую точку: включение — это новое сообщение со сроком, выключение —
    # правка без срока; всё остальное — просто координаты.
    started = bool(update.message and period)
    if started or stop:
        try:
            import geo_watch
            await geo_watch.on_stream(me["name"], on=started, now=now)
        except Exception as e:
            log.warning(f"старшему о трансляции {me['name']} не ушло: {e}")
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
        # Само сообщение с трансляцией НЕ запоминаем: всё из реестра чистильщик
        # стирает через восемь часов, а стёртая трансляция — выключенная
        # трансляция. Водитель её не трогал, а посреди смены пропадал с карты.
        # Наш ответ на неё — обычная переписка, он в реестр идёт.
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
    if not staff.driver_or_test(update.effective_user.id):
        return
    await _remember(update.message)
    import geo_watch
    link = await geo_watch.geo_bot_link()
    sent = await update.message.reply_text(
        "Трансляцию геопозиции включают в отдельном боте"
        + (f": {link}" if link else " геопозиции") + "\n\n"
        "1. Откройте его и нажмите /start\n"
        "2. 📎 слева от поля ввода → «Геопозиция»\n"
        "3. «Транслировать» → «Пока не выключу»\n\n"
        "Один раз: телефон будет сам присылать точку, даже когда телеграм "
        "свёрнут, а тот чат можно убрать в архив.",
        reply_markup=drop_keyboard())
    await _remember(sent)


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if staff.driver_or_test(update.effective_user.id):
        await _remember(update.message)
    sent = await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")
    if staff.driver_or_test(update.effective_user.id):
        await _remember(sent)


# ── временно: проверка микрофона ───────────────────────────────────────────
# Открывать пробник ссылкой бесполезно: ссылка в телеграме открывается во
# встроенном браузере, а это другое вебвью с другими правами. Проверять надо
# ровно ту среду, в которой живёт приложение водителя, — то есть кнопкой
# web_app. Команда и сама страница уходят вместе с ответом на вопрос.
MIC_TEST_URL = DRIVER_WEBAPP_URL.rstrip("/") + "/mic.html"


async def cmd_mic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Проверка микрофона. Откройте и скажите что-нибудь вслух.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Открыть проверку", web_app=WebAppInfo(url=MIC_TEST_URL))]]))
    log.info(f"[mic] пробник открыт: {update.effective_user.id}")


def main():
    if not DRIVER_BOT_TOKEN:
        print("❌ DRIVER_BOT_TOKEN missing")
        return
    app = Application.builder().token(DRIVER_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("mic", cmd_mic))
    # И первое сообщение с точкой, и каждая правка живой трансляции.
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.LOCATION, on_location))
    # Код привязки текстом — запасной путь к ссылке.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info(f"бот водителя запущен · доступ у {len(staff.DRIVER_IDS)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
