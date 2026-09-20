"""Клиент застрял на верификации.

Зачем
-----
Новый клиент собирает заказ, жмёт «Заказать» — и упирается в анкету «от кого
вы о нас узнали». Пока он её не отправил, заказ лежит скрытым: операторам его
не видно вовсе, а владельцу через пять минут приходит «заказ не принят». Он и
правда не принят — только принять его некому, потому что для операторов
заказа ещё не существует (владелец, 21 сен 2026: «клиенты теряются, даже не
понимают, что это за верификация, так её и не проходят… получается, заказ
потерялся, и нет канала связи между оператором и клиентом»).

Что делает
----------
Через две минуты после такого заказа:

  • клиенту — в основной бот, тем же чатом, где он уже есть: остался один шаг,
    вот кнопка анкеты, а если что-то непонятно — напишите прямо сюда;
  • операторам — в бот поддержки, обычным обращением: кто застрял, какой
    заказ, на сколько. Ответ реплаем уходит клиенту в тот же чат — канал связи
    появляется сам, отдельного экрана для этого не нужно;
  • владельцу — вместо «заказ не принят» честное «клиент застрял на
    верификации» (ключ orders.verify_stuck).

Пишем один раз на заказ (verify_nag_at на самом заказе). Прошёл верификацию,
отменил, оформил — сторож молчит: он говорит только о живом заказе, который
ждёт анкеты. Ночью тоже молчим — с часу до семи по Дубаю у операторов никого,
а клиент к утру о заказе и сам вспомнит.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import db
import support_inbox

log = logging.getLogger("verify-nag")

DUBAI_TZ = timezone(timedelta(hours=4))
EVERY_SEC = 30                 # как часто смотрим
AFTER_MIN = 2                  # через сколько минут молчания пишем
QUIET_FROM, QUIET_TO = 1, 7    # часы по Дубаю, когда не пишем


def _dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "").replace(" ", "T"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _quiet(now=None) -> bool:
    h = (now or datetime.now(DUBAI_TZ)).astimezone(DUBAI_TZ).hour
    return QUIET_FROM <= h < QUIET_TO


_BOT_NAME = {"v": None}


async def _bot_name() -> str:
    """Имя основного бота. В окружении его нет — сам бот узнаёт его у телеграма
    на старте, и мы делаем так же, один раз за жизнь процесса."""
    if _BOT_NAME["v"] is not None:
        return _BOT_NAME["v"]
    name = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    if not name:
        r = await support_inbox._tg(os.getenv("BOT_TOKEN", ""), "getMe", {})
        name = str(((r or {}).get("result") or {}).get("username") or "")
    _BOT_NAME["v"] = name
    return name


async def _app_button(label: str) -> dict | None:
    """Кнопка, открывающая приложение. Ссылкой на главное мини-приложение —
    такие запуски телеграм считает; не узнали имя бота — обычной web_app, по
    адресу из окружения (наш домен людям не отдаём)."""
    name = await _bot_name()
    if name:
        return {"text": label, "url": f"https://t.me/{name}?startapp=home"}
    url = os.getenv("WEBAPP_URL", "").strip()
    return {"text": label, "web_app": {"url": url}} if url else None


def _support_link() -> str:
    name = os.getenv("SUPPORT_BOT_USERNAME", "ambar_support_bot").strip().lstrip("@")
    return f"https://t.me/{name}" if name else ""


CLIENT_TEXT = (
    "🍾 <b>Остался один шаг</b>\n\n"
    "Ваш заказ <b>#{oid}</b> на {total} AED уже собран и ждёт — не хватает только "
    "верификации.\n\n"
    "Мы работаем по рекомендациям, поэтому просим коротко ответить, от кого вы о нас "
    "узнали. Это одна минута и делается один раз: дальше заказы уходят оператору сразу, "
    "без лишних вопросов.\n\n"
    "Если что-то непонятно или анкета не открывается — просто напишите нам. "
    "Поддержка на связи и поможет по любому вопросу, а вечер останется вечером 🙂"
)

OP_TEXT = (
    "⏳ Клиент застрял на верификации\n\n"
    "👤 {name}{phone}\n"
    "🆕 Заказ #{oid} · {total} AED{office}\n"
    "Анкету «от кого узнали» открыл {mins} мин назад и не отправил — пока он её не "
    "заполнит, заказ к вам не попадёт.\n\n"
    "Мы уже написали ему в бот. Ответьте на это сообщение — он получит ваш ответ "
    "в том же чате."
)


async def _client_kb() -> dict | None:
    строки = []
    анкета = await _app_button("✅ Пройти верификацию")
    if анкета:
        строки.append([анкета])
    if _support_link():
        строки.append([{"text": "💬 Написать в поддержку", "url": _support_link()}])
    return {"inline_keyboard": строки} if строки else None


async def _tell_client(uid: int, order: dict) -> bool:
    """Клиенту — в основной бот: там он уже есть, и туда же придёт ответ."""
    token = os.getenv("BOT_TOKEN", "")
    if not token or not uid:
        return False
    text = CLIENT_TEXT.format(oid=order.get("order_id", "—"),
                              total=int(float(order.get("total") or 0)))
    payload = {"chat_id": uid, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    kb = await _client_kb()
    if kb:
        payload["reply_markup"] = kb
    r = await support_inbox._tg(token, "sendMessage", payload)
    return bool((r or {}).get("ok"))


async def _tell_operators(uid: int, user: dict, order: dict, mins: int) -> None:
    """Операторам — обращением в бот поддержки, с привязкой к клиенту.

    Не «ещё одно уведомление», а обычная переписка: ответ реплаем поддержка
    доставит клиенту в основной бот, где он и ждёт."""
    key = support_inbox.conv_key(uid)
    # Канал переписки — основной бот: клиент туда уже получил наше сообщение,
    # и ответ оператора должен прийти в тот же чат, а не «откройте приложение».
    try:
        await db.support_set_channel(key, support_inbox.CHANNEL_MAIN)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"канал переписки не выставлен uid={uid}: {e}")
    имя = (user.get("first_name") or user.get("name") or "Клиент").strip()
    тел = str(user.get("phone_verified") or "").strip()
    office = order.get("office_name") or order.get("office") or ""
    text = OP_TEXT.format(
        name=имя, phone=f" · {тел}" if тел else "",
        oid=order.get("order_id", "—"), total=int(float(order.get("total") or 0)),
        office=f" · {office}" if office else "", mins=mins)
    token = os.getenv("SUPPORT_BOT_TOKEN", "")
    kb = {"inline_keyboard": [[{"text": "👤 Клиент",
                                "callback_data": f"client_{order.get('order_id','')}_{uid}"}]]}
    for op_id in support_inbox._operator_ids():
        r = await support_inbox._tg(token, "sendMessage",
                                    {"chat_id": op_id, "text": text, "reply_markup": kb})
        mid = ((r or {}).get("result") or {}).get("message_id")
        if not mid:
            continue
        try:
            await db.save_support_map_entry(str(mid), {
                "user_id": uid, "conv_key": key,
                "order_id": str(order.get("order_id") or ""),
                "channel": support_inbox.CHANNEL_MAIN,
            })
        except Exception as e:                               # noqa: BLE001
            log.warning(f"связка сообщение→клиент не сохранена uid={uid}: {e}")
    # В переписке остаётся след: оператор, открыв её, видит, что клиенту уже
    # написали и что именно.
    try:
        await db.append_support_msg(key, {
            "role": "operator", "type": "text",
            "text": f"🤖 Автоматически: клиенту напомнили про верификацию "
                    f"(заказ #{order.get('order_id','—')})",
            "ts": datetime.now(timezone.utc).isoformat()})
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"след в переписке не записан uid={uid}: {e}")


async def _tell_owner(user: dict, order: dict, mins: int) -> None:
    try:
        from owner_routes import notify_owners
    except Exception as e:                                   # noqa: BLE001
        log.error(f"владельцу не сказали: {e}")
        return
    имя = (user.get("first_name") or user.get("name") or "Клиент").strip()
    await notify_owners(
        "orders.verify_stuck",
        f"⏳ *Клиент застрял на верификации*\n"
        f"Заказ #{order.get('order_id','—')} · {int(float(order.get('total') or 0))} AED\n"
        f"Клиент: {имя}\n"
        f"_Операторам сообщили, они напишут ему в поддержке._",
        test=bool(order.get("test")))


async def once() -> int:
    """Один проход. Возвращает, скольким написали (для тестов)."""
    if _quiet():
        return 0
    try:
        orders = await db.get_all_orders()
    except Exception as e:                                   # noqa: BLE001
        log.error(f"заказы не прочитаны: {e}")
        return 0
    now = datetime.now(timezone.utc)
    сказали = 0
    for oid, o in (orders or {}).items():
        if not o.get("pending_verification") or o.get("verify_nag_at"):
            continue
        if o.get("status") not in ("pending", None, ""):
            continue                                   # отменён или уже поехал
        placed = _dt(o.get("timestamp"))
        if not placed or (now - placed) < timedelta(minutes=AFTER_MIN):
            continue
        uid = int(o.get("customer_id") or o.get("user_id") or 0)
        if not uid:
            continue
        try:
            user = await db.get_user(uid) or {}
        except Exception as e:                               # noqa: BLE001
            log.warning(f"клиент не прочитан uid={uid}: {e}")
            user = {}
        if user.get("verified") or user.get("verify_requested"):
            continue                                   # анкету отправил — не наш случай
        mins = max(AFTER_MIN, int((now - placed).total_seconds() // 60))
        # Отметку ставим ДО отправки: упасть на полпути и написать дважды хуже,
        # чем не написать вовсе.
        try:
            await db.update_order(str(oid), verify_nag_at=now.isoformat())
        except Exception as e:                               # noqa: BLE001
            log.error(f"отметка не поставлена #{oid}: {e}")
            continue
        order = {**o, "order_id": o.get("order_id") or oid}
        try:
            дошло = await _tell_client(uid, order)
            await _tell_operators(uid, user, order, mins)
            await _tell_owner(user, order, mins)
            сказали += 1
            log.info(f"[verify] #{order['order_id']}: клиенту {'да' if дошло else 'нет'}, "
                     f"операторам и владельцу сказали ({mins} мин)")
        except Exception as e:                               # noqa: BLE001
            log.error(f"[verify] #{order['order_id']}: {e}")
    return сказали


async def loop(app=None):
    log.info(f"[verify] сторож верификации: раз в {EVERY_SEC} с, порог {AFTER_MIN} мин")
    while True:
        await asyncio.sleep(EVERY_SEC)
        try:
            await once()
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.error(f"[verify] проход не удался: {e}")
