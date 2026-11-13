"""Карточка заказа клиенту как HTML-виджет Telegram (Bot API 10.4).

Одно живое сообщение на заказ, как и текстовая карточка customer_card.py:
при создании заказа уходит sendWidget, дальше только editMessageWidgetData с
новыми данными, код виджета после отправки не меняется. Кому вместо текста
приходит виджет, решает config.WIDGET_IDS (владелец, 13 ноя 2026: «пока что
примени это только к моему аккаунту»); остальным по-прежнему текст.

Шаблон widget/card.html один на оба языка: словарь текстов подставляется при
сборке, по языку на файл, иначе не влезть в 4 КБ после zlib (лимит
Telegram). Рисунок и состояния по макету Codex 12 ноя 2026. Состояния:
  hi  приветствие (/start)
  nw  заказ оформлен, ждём подтверждения          status pending
  pk  принят, собираем                            status approved
  dr  водитель принял заказ и едет                approved + driver_ack_at
  ar  водитель на месте                           approved + driver_arrived_at
  dl  доставлен, звёзды оценки                    status delivered
  cx  отменён                                     cancelled / declined / rejected
Звёзды: виджет шлёт request("rate:N") → у бота widget_query → review_record
(тот же путь, что отзыв из приложения) → в данные карточки пишется r.
"""
from __future__ import annotations

import asyncio, json, logging, os, zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

import db
from config import WIDGET_IDS, WIDGET_ALL

log = logging.getLogger("widget")

_TOKEN = os.getenv("BOT_TOKEN", "")
_API = f"https://api.telegram.org/bot{_TOKEN}"
_HERE = Path(__file__).parent
_TPL = _HERE / "widget" / "card.html"
_DUBAI = timezone(timedelta(hours=4))

T = {
    "ru": {"wl": "Добро пожаловать!", "wh": "Заказ — только<br>в приложении", "op": "Открыть приложение",
           "mo": "Заказать ещё", "no": "Заказ № ", "st": ["Принят", "Водитель едет", "Доставлен"],
           "nw": ["Заказ оформлен", "ждём подтверждения"], "pk": ["Собираем заказ"],
           "dr": ["Водитель уже едет"], "ar": ["Водитель на месте"], "dl": ["Доставлен", "приятного вечера"],
           "cx": ["Заказ отменён"], "et": "примерно $ мин", "ra": "Оцените заказ", "th": "Спасибо, $ из 5"},
    "en": {"wl": "Welcome!", "wh": "Orders are<br>placed in the app", "op": "Open the app",
           "mo": "Order again", "no": "Order #", "st": ["Accepted", "On the way", "Delivered"],
           "nw": ["Order placed", "awaiting confirmation"], "pk": ["Packing your order"],
           "dr": ["Driver is on the way"], "ar": ["Driver has arrived"], "dl": ["Delivered", "enjoy your evening"],
           "cx": ["Order cancelled"], "et": "about $ min", "ra": "Rate the order", "th": "Thank you, $ of 5"},
}
CAPTION = {
    "ru": ("AMBAR · откройте приложение, чтобы оформить заказ", "AMBAR · Заказ № {oid}"),
    "en": ("AMBAR · open the app to place an order", "AMBAR · Order #{oid}"),
}

_html_cache: dict[str, str] = {}
_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")


def wants(uid) -> bool:
    """Этому клиенту карточка уходит виджетом, а не текстом."""
    try:
        return WIDGET_ALL or int(uid) in WIDGET_IDS
    except (TypeError, ValueError):
        return False


async def _call(method: str, **kw) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.post(f"{_API}/{method}", json=kw) as r:
                res = await r.json()
    except Exception as e:                                       # noqa: BLE001
        log.warning(f"[widget] {method}: {e}")
        return {"ok": False, "description": str(e)}
    if not res.get("ok"):
        log.warning(f"[widget] {method}: {res.get('description')}")
    return res


async def _link() -> str:
    """Ссылка на главное мини-приложение, как в bot._app_link: имя бота из
    .env, иначе спрашиваем у Telegram один раз."""
    global _username
    if not _username:
        me = await _call("getMe")
        _username = ((me.get("result") or {}).get("username") or "").strip()
    return f"https://t.me/{_username}?startapp=home" if _username else "https://t.me/"


async def html(lang: str) -> str:
    """Готовый HTML виджета на языке клиента. Собирается один раз на процесс;
    размер после zlib-9 пишется в журнал, лимит Telegram 4096 байт."""
    lang = "ru" if lang == "ru" else "en"
    if lang not in _html_cache:
        tpl = _TPL.read_text(encoding="utf-8")
        page = (tpl.replace("__T__", json.dumps(T[lang], ensure_ascii=False, separators=(",", ":")))
                   .replace("__LINK__", await _link()))
        packed = len(zlib.compress(page.encode("utf-8"), 9))
        log.info(f"[widget] card {lang}: {len(page.encode('utf-8'))} байт, zlib {packed} байт")
        _html_cache[lang] = page
    return _html_cache[lang]


def _state(order: dict) -> str:
    st = order.get("status") or "pending"
    if st == "delivered":
        return "dl"
    if st in ("cancelled", "declined", "rejected"):
        return "cx"
    if st == "approved":
        if order.get("driver_arrived_at"):
            return "ar"
        if order.get("driver_ack_at"):
            return "dr"
        return "pk"
    return "nw"


def _items(order: dict) -> str:
    parts = [f"{i.get('qty', 0)} × {i.get('name', '?')}" for i in order.get("items") or []]
    s = ", ".join(parts)
    return s if len(s) <= 200 else s[:199] + "…"


def _sum(order: dict) -> str:
    t = order.get("total", 0) or 0
    try:
        t = int(t) if float(t) == int(t) else t
    except (TypeError, ValueError):
        pass
    return f"{t} AED"


def _eta(order: dict, state: str) -> str:
    """Минуты в подписи: пока собираем — срок, названный оператором; водитель
    едет — сколько осталось до обещанного часа (deliver_by, HH:MM по Дубаю)."""
    if state == "dr" and order.get("deliver_by"):
        try:
            now = datetime.now(_DUBAI)
            hh, mm = str(order["deliver_by"]).split(":")
            due = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            left = int((due - now).total_seconds() // 60)
            if 0 < left < 600:
                return str(left)
        except (TypeError, ValueError):
            pass
    eta = order.get("eta")
    return str(int(eta)) if eta else ""


async def _plate(order: dict) -> str:
    """Номер машины водителя (как в фразе «На месте» driver_routes.arrived_phrase)."""
    name = str(order.get("driver") or "").strip()
    if not name:
        return ""
    try:
        mine = [c for c in await db.cars_fleet() if str(c.get("driver") or "") == name]
        car = max(mine, key=lambda c: str(c.get("at") or "")) if mine else None
        return str((car or {}).get("plate") or "").strip()
    except Exception as e:                                       # noqa: BLE001
        log.debug(f"[widget] машина {name}: {e}")
        return ""


async def data_for(order: dict) -> dict:
    """Данные карточки (chat_data, ≤1024 байт) из заказа."""
    s = _state(order)
    d = {"s": s, "n": str(order.get("order_id") or ""), "it": _items(order), "sum": _sum(order),
         "t": datetime.now(_DUBAI).strftime("%H:%M")}
    eta = _eta(order, s)
    if eta and s in ("pk", "dr"):
        d["eta"] = eta
    if s in ("dr", "ar"):
        plate = await _plate(order)
        if plate:
            d["car"] = plate
    if s == "dl" and order.get("review_score"):
        d["r"] = int(order["review_score"])
    return d


def _packed(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


async def send(order: dict) -> int | None:
    """Новое сообщение с карточкой заказа. Возвращает message_id или None."""
    cid = order.get("customer_id")
    lang = "ru" if order.get("lang", "ru") == "ru" else "en"
    if not cid:
        return None
    res = await _call("sendWidget", chat_id=int(cid), html=await html(lang),
                      chat_data=_packed(await data_for(order)),
                      caption=CAPTION[lang][1].format(oid=order.get("order_id", "")),
                      allow_queries=True)
    return ((res.get("result") or {}).get("message_id")) if res.get("ok") else None


async def refresh(order) -> bool:
    """Переписать данные живой карточки по текущему состоянию заказа. Заказ
    без виджета (текстовая карточка или телефонный) — ничего не делает.
    Если сообщение не редактируется (стёрто), шлём новое и запоминаем его."""
    if isinstance(order, str):
        order = await db.get_order(order)
    if not order or not order.get("customer_widget") or not order.get("customer_id"):
        return False
    mid = order.get("customer_msg_id")
    if mid:
        res = await _call("editMessageWidgetData", chat_id=int(order["customer_id"]), message_id=int(mid),
                          chat_data=_packed(await data_for(order)))
        if res.get("ok"):
            return True
    new_mid = await send(order)
    if new_mid:
        await db.update_order(order["order_id"], customer_msg_id=new_mid, customer_widget=True)
    return bool(new_mid)


async def welcome(uid: int, lang: str) -> int | None:
    """Приветственная карточка вместо текста /start."""
    lang = "ru" if lang == "ru" else "en"
    res = await _call("sendWidget", chat_id=int(uid), html=await html(lang),
                      chat_data=_packed({"s": "hi"}), caption=CAPTION[lang][0])
    return ((res.get("result") or {}).get("message_id")) if res.get("ok") else None


async def on_query(uid: int, message_id: int | None, data: str) -> str:
    """Запрос из виджета («rate:N» со звёзд). Оценка пишется тем же путём, что
    отзыв из приложения (api_server.review_record), и карточка получает r.
    Возвращает ответ виджету; Telegram ждёт его 15 секунд, поэтому запись и
    уведомления операторам и владельцам уходят отдельной задачей."""
    if not str(data).startswith("rate:") or not message_id:
        return "ignored"
    try:
        score = int(str(data)[5:])
    except ValueError:
        return "bad"
    if not 1 <= score <= 5:
        return "bad"
    d = db._db_or_none()
    order = await d.orders.find_one({"customer_id": int(uid), "customer_msg_id": int(message_id)},
                                    {"_id": 0}) if d is not None else None
    if not order:
        return "no_order"
    if order.get("status") != "delivered":
        return "not_delivered"
    if order.get("review_score"):
        return "already"
    asyncio.create_task(_rate(order, int(uid), score))
    return "ok"


async def _rate(order: dict, uid: int, score: int) -> None:
    try:
        from api_server import review_record
        await review_record(order, uid, score)
    except Exception as e:                                       # noqa: BLE001
        log.error(f"[widget] оценка #{order.get('order_id')}: {e}")
        return
    await refresh(order.get("order_id"))


async def answer(query_id: str, data: str = "") -> None:
    """Ответить на widget_query (Telegram ждёт ответа 15 секунд)."""
    await _call("answerWidgetQuery", widget_query_id=str(query_id), data=str(data)[:1024])
