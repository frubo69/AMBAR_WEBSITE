#!/usr/bin/env python3
"""
AMBAR — бот геопозиции (LOCATOR): люди.

Делает ровно одно: принимает живую трансляцию геопозиции водителя или старшего
и кладёт точку туда, откуда её читают карта и локатор. Водитель — под своим
именем (локатор оператора, сторож смены), старший — под ключом старшего
(карта владельца). Кто есть кто, решает .env: AMBAR_DRIVER_IDS и
AMBAR_SENIOR_STAR_IDS. Чужие координаты не хранятся.

Устройства (планшеты) сюда не транслируют: у них свой бот (device_bot.py) и
своя группа в локаторе — чтобы телефон старшего и его планшет не сбивали
след друг другу.

Зачем отдельный бот. Трансляция «пока не отключу» — это одно сообщение
человека, которое телеграм правит; стёртое сообщение — выключенная
трансляция. В чатах рабочих ботов сообщения стираются (скрытый режим,
чистильщик), и трансляции там не место. Здесь ничего не стирается и в
реестры стирания не пишется.

Токен — AMBAR_GEO_BOT_TOKEN в /opt/ambar/.env; юнит — deploy/ambar-geo-bot.service.
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          ChatMemberHandler, ContextTypes, filters)
import asyncio

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
    # Реестр водителей из базы: кто есть кто — по нему.
    try:
        await staff.sync(force=True)
    except Exception as e:                   # noqa: BLE001
        log.warning(f"реестр при старте: {e}")
    import asyncio as _aio
    _aio.get_event_loop().create_task(staff.roster_loop(30))
    _aio.get_event_loop().create_task(_probe_loop(app.bot))


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
    if kind == "driver":
        # Водителю бот ничего не объясняет и имени не называет (владелец,
        # 15 сен 2026): что делать в этом чате, показывает приложение, а в
        # самом чате должны остаться только точка и история LEGO с
        # предупреждением. Чужой глаз не должен увидеть здесь «Худоба · водитель».
        return
    who = "водитель" if kind == "driver" else "старший"
    sent = await update.message.reply_text(
        f"{name} · {who}\n\n"
        "Здесь нужно сделать одно — включить трансляцию геопозиции с телефона:\n\n"
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
    chat = update.effective_chat.id
    # Трансляция, которую раньше пометили «iPad»: устройства теперь живут в
    # своём боте. Точки не берём, а один раз говорим, куда её перенести.
    if kind == "senior":
        try:
            st = await db.geo_stream_doc(chat, msg.message_id)
        except Exception:                    # noqa: BLE001
            st = {}
        if st.get("device") == "tablet":
            if not st.get("hinted"):
                link = await geo_watch.device_bot_link()
                await _say(update, ctx, "Планшет транслирует в отдельный бот устройств"
                           + (f": {link}" if link else "") + ". Выключите трансляцию "
                           "здесь и включите там; в этом боте — только телефон.")
                try:
                    await db.geo_stream_set(chat, msg.message_id, "tablet", name,
                                            extra={"hinted": True})
                except Exception:            # noqa: BLE001
                    pass
            return
    now = datetime.now(timezone.utc)
    period = getattr(loc, "live_period", None)
    until = now + timedelta(seconds=int(period)) if period else None
    # Выключили: телеграм правит то же сообщение, а срока у точки больше нет.
    stop = bool(update.edited_message and not period)
    key = name if kind == "driver" else geo_watch.SENIOR_PREFIX + name
    try:
        await db.driver_pos_set(key, geo_watch._biz_day(), loc.latitude, loc.longitude,
                                now, until=until, stop_live=stop,
                                acc=getattr(loc, "horizontal_accuracy", None))
    except Exception as e:                   # noqa: BLE001
        log.warning(f"точка {name} не записана: {e}")
        return
    started = bool(update.message and period)
    if started or stop:
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
        if kind == "driver":
            # Владелец, 15 сен 2026: водителю в ответ — не короткое «идёт», а
            # очень длинная история LEGO по-английски, частями: точка с картой
            # уезжает далеко вверх, и листать до неё приходится долго.
            await _lego_wall(ctx, chat, name)
        else:
            await _say(update, ctx, f"{name} · трансляция идёт. Больше здесь ничего делать "
                                    "не нужно — чат можно убрать в архив.")
    elif stop:
        log.info(f"трансляция выключена: {name}")
        if kind != "driver":
            await _say(update, ctx, f"{name} · трансляция выключена. Чтобы вас снова видели, "
                                    f"включите её заново:\n\n{HOW}")
    elif update.message:
        # Разовая точка: дошла, но погаснет через минуты. Нужна трансляция.
        # Водителю об этом скажет приложение, здесь молчим.
        if kind != "driver":
            await _say(update, ctx, f"{name} · точка принята, но это разовая точка, она "
                                    f"погаснет. Нужна трансляция:\n\n{HOW}")


async def _lego_wall(ctx, chat: int, name: str):
    """История LEGO частями по одному сообщению (предел телеграма — 4096
    знаков). Шлём подряд с паузой; на «слишком часто» ждём, сколько просят,
    и повторяем один раз. Сбой одной части не роняет остальные."""
    import asyncio as _aio
    from lego_history import PARTS
    try:
        from telegram.error import RetryAfter
    except Exception:                        # noqa: BLE001
        RetryAfter = ()                      # type: ignore[assignment]
    sent, last = 0, None
    for i, part in enumerate(PARTS, 1):
        for attempt in (1, 2):
            try:
                last = await ctx.bot.send_message(chat, part, disable_web_page_preview=True)
                sent += 1
                break
            except RetryAfter as e:          # type: ignore[misc]
                await _aio.sleep(float(getattr(e, "retry_after", 3) or 3) + 0.5)
            except Exception as e:           # noqa: BLE001
                log.warning(f"история LEGO {name}, часть {i}: {e}")
                break
        await _aio.sleep(0.5)
    log.info(f"{name}: история LEGO отправлена, частей {sent} из {len(PARTS)}")
    # Последнее сообщение — проба: раз в минуту бот трогает его, и если его
    # больше нет, значит чат удалён вместе с трансляцией (см. _probe_once).
    if last is not None:
        try:
            await db.geo_probe_set(chat, name, last.message_id)
        except Exception as e:                   # noqa: BLE001
            log.warning(f"проба {name} не записана: {e}")


# ── удалённый чат и заблокированный бот (владелец, 15 сен 2026) ─────────────
# Телеграм не сообщает боту, что человек удалил переписку: точки просто
# перестают приходить, а сервер считает трансляцию живой, пока не остынет
# якорь стояния. «Удалил переписку — бот больше не спрашивает». Поэтому два
# сторожа: блокировку бота телеграм присылает событием my_chat_member — гасим
# трансляцию в ту же секунду; удаление чата ловим пробой — раз в минуту бот
# трогает своё последнее сообщение в чате, и если его больше нет, чата нет.
PROBE_EVERY = 60


async def _stream_gone(name: str, why: str):
    now = datetime.now(timezone.utc)
    try:
        await db.driver_pos_stop(name, now)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"остановка трансляции {name}: {e}")
    try:
        await geo_watch.on_stream(name, on=False, now=now)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"о потере трансляции {name} не сообщили: {e}")
    log.info(f"трансляция потеряна: {name} · {why}")


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Заблокировал бота (или удалил чат с остановкой) — трансляции нет."""
    cm = update.my_chat_member
    if not cm or not cm.new_chat_member or not cm.from_user:
        return
    status = getattr(cm.new_chat_member, "status", "")
    kind, name = _role(cm.from_user.id)
    if kind != "driver":
        return
    if status in ("kicked", "left"):
        try:
            await db.geo_probe_clear(cm.chat.id)
        except Exception:                        # noqa: BLE001
            pass
        await _stream_gone(name, "бот заблокирован")


async def _probe_once(bot) -> int:
    """Один круг проб. Возвращает, сколько трансляций признано потерянными."""
    from telegram.error import BadRequest, Forbidden
    now = datetime.now(timezone.utc)
    gone_n = 0
    for p in await db.geo_probe_all():
        name, chat, mid = p.get("name"), p.get("_id"), p.get("mid")
        if not name or not chat or not mid:
            continue
        rows = await db.driver_pos_all([name])
        r = (rows or [{}])[0] if rows else {}
        until = r.get("until")
        if until is not None and getattr(until, "tzinfo", None) is None:
            until = until.replace(tzinfo=timezone.utc)
        if not until or until <= now:
            continue                             # трансляции и так нет — трогать нечего
        gone = False
        try:
            await bot.edit_message_reply_markup(chat_id=chat, message_id=mid, reply_markup=None)
        except BadRequest as e:
            m = str(e).lower()
            if "not modified" in m:
                pass                             # сообщение на месте
            elif "not found" in m or "message_id_invalid" in m or "chat not found" in m:
                gone = True
            else:
                log.debug(f"проба {name}: {e}")
        except Forbidden:
            gone = True                          # бот заблокирован
        except Exception as e:                   # noqa: BLE001
            log.debug(f"проба {name}: {e}")
        if gone:
            gone_n += 1
            try:
                await db.geo_probe_clear(chat)
            except Exception:                    # noqa: BLE001
                pass
            await _stream_gone(name, "чат удалён")
    return gone_n


async def _probe_loop(bot):
    while True:
        await asyncio.sleep(PROBE_EVERY)
        try:
            await _probe_once(bot)
        except Exception as e:                   # noqa: BLE001
            log.warning(f"круг проб: {e}")


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
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    log.info(f"бот геопозиции запущен · водителей {len(staff.DRIVER_IDS)} · "
             f"старших {len(staff.SENIOR_STAR_IDS)}")
    app.run_polling(allowed_updates=["message", "edited_message", "my_chat_member"])


if __name__ == "__main__":
    main()
