"""
AMBAR — shared MongoDB helpers.
All three processes (api_server, bot, operator_bot) import this module.
Each process maintains its own Motor client pointing at the same Atlas cluster.
"""
from __future__ import annotations
import os, logging, re, json
from datetime import datetime, timezone, timedelta
import motor.motor_asyncio
import certifi
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv
from config import OWNER_IDS, MANAGER_IDS

load_dotenv()
log        = logging.getLogger(__name__)
MONGO_URI  = os.getenv("MONGO_URI", "")

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_db = None


async def connect():
    """Connect to MongoDB Atlas and create indexes. Idempotent."""
    global _client, _db
    if _db is not None:
        return _db
    if not MONGO_URI:
        log.warning("⚠️  MONGO_URI not set — DB unavailable")
        return None
    # tlsCAFile передаём ТОЛЬКО облачным адресам: сам факт этой настройки
    # включает в драйвере TLS, и локальная база, которая по TLS не слушает,
    # отвечает обрывом рукопожатия. Для Atlas свежий набор корневых
    # сертификатов по-прежнему нужен — на старых системах иначе не соединиться.
    _opts = {"serverSelectionTimeoutMS": 8000}
    if MONGO_URI.startswith("mongodb+srv://") or "tls=true" in MONGO_URI \
            or "ssl=true" in MONGO_URI:
        _opts["tlsCAFile"] = certifi.where()
    _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI, **_opts)
    _db = _client.ambar
    try:
        await _db.orders.create_index("order_id", unique=True)
        await _db.orders.create_index("customer_id")
        # Очередь панели: что нового, что в работе, что тронули после отметки.
        await _db.orders.create_index([("status", 1), ("timestamp", -1)])
        await _db.orders.create_index("updated_at")
        # Окно по времени — самый частый запрос всех экранов денег.
        await _db.orders.create_index("timestamp")
        await _db.users.create_index("telegram_id", unique=True)
        await _db.support_messages.create_index("conv_key", unique=True)
        await _db.support_map.create_index("fwd_msg_id", unique=True)
        await _db.shifts.create_index([("operator_id", 1), ("status", 1)])
        # Приход после пересчёта спрашивается на каждую заявку — по району,
        # источнику и времени.
        await _db.qr_codes.create_index([("district", 1), ("src", 1), ("at", 1)])
        # Проход камерой по полке: счёт по позициям спрашивается после каждого
        # скана, и без индекса это перебор всего прохода на каждую бутылку.
        await _db.audit_scans.create_index([("district", 1), ("day", 1)])
        await _db.shift_days.create_index([("day", 1)])
        # Страховка на случай незакрытой смены: полтора суток без обновлений —
        # и запись о положении водителя уходит сама. Срок считается от «at», а
        # он переписывается каждой точкой, так что живого водителя это не
        # трогает.
        await _db.driver_pos.create_index("at", expireAfterSeconds=36 * 3600)
        await _db.owner_managers.create_index("telegram_id", unique=True)
        await _db.drivers.create_index("name", unique=True)
        # Один телеграм-аккаунт не может быть двумя водителями. Partial, потому
        # что непривязанных записей с telegram_id=None может быть сколько угодно.
        await _db.drivers.create_index(
            "telegram_id", unique=True,
            partialFilterExpression={"telegram_id": {"$type": "long"}},
        )
        await _db.driver_days.create_index([("day", 1), ("driver", 1)], unique=True)
        await _db.owner_access_log.create_index("telegram_id", unique=True)
        await _db.owner_access_log.create_index([("status", 1), ("last_attempt_at", -1)])
        await _db.owner_notifications.create_index([("created_at", -1)])
        await _db.owner_notifications.create_index("event_key")
        # Реестр переписки owner-бота: свипер ходит по дате, тревога — по чату.
        await _db.owner_msgs.create_index("at")
        await _db.owner_msgs.create_index("chat_id")
        # Crypto invoices: one per order; a txid binds to exactly one invoice so
        # a transfer can never credit two orders. Partial (string-only) index so
        # the many invoices with txid=None/unset don't collide on the null value.
        await _db.crypto_invoices.create_index("order_id", unique=True)
        await _db.crypto_invoices.create_index(
            "txid", unique=True,
            partialFilterExpression={"txid": {"$type": "string"}},
        )
        # No two WAITING invoices may share a USDT amount — the amount is how an
        # incoming transfer is matched to exactly one order. Partial so confirmed/
        # expired invoices (which keep their amount) don't collide.
        await _db.crypto_invoices.create_index(
            "amount_usdt", unique=True,
            partialFilterExpression={"status": "waiting"},
        )
        await _db.crypto_invoices.create_index([("status", 1), ("expires_at_ms", 1)])
        await _db.crypto_invoices.create_index("customer_id")
        # Clean up duplicate owner_prefs docs (keep newest by updated_at).
        try:
            pipeline = [{"$group": {"_id": "$owner_id", "count": {"$sum": 1}}},
                        {"$match": {"count": {"$gt": 1}}}]
            async for group in _db.owner_prefs.aggregate(pipeline):
                oid = group["_id"]
                docs = await _db.owner_prefs.find(
                    {"owner_id": oid}, {"_id": 1, "updated_at": 1}
                ).sort("updated_at", -1).to_list(length=50)
                if len(docs) > 1:
                    dup_ids = [d["_id"] for d in docs[1:]]
                    await _db.owner_prefs.delete_many({"_id": {"$in": dup_ids}})
                    log.info(f"[owner-prefs] cleaned {len(dup_ids)} dupes for owner_id={oid}")
        except Exception as e:
            log.warning(f"[owner-prefs] dupe cleanup: {e}")
        try:
            await _db.owner_prefs.create_index("owner_id", unique=True)
        except Exception:
            pass
        # Migrate old prefs dict → prefs_json for any docs still in old format.
        try:
            cursor = _db.owner_prefs.find({"prefs_json": {"$exists": False}})
            async for doc in cursor:
                prefs = doc.get("prefs")
                if isinstance(prefs, dict):
                    # Handle both flat {"orders.new": true} and nested
                    # {orders: {new: true}} (MongoDB dot-key corruption).
                    flat = {}
                    for k, v in prefs.items():
                        if isinstance(v, dict):
                            for k2, v2 in v.items():
                                flat[f"{k}.{k2}"] = v2
                        else:
                            flat[k] = v
                    await _db.owner_prefs.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"prefs_json": json.dumps(flat)},
                         "$unset": {"prefs": ""}},
                    )
                    log.info(f"[owner-prefs] migrated prefs→prefs_json for owner_id={doc.get('owner_id')}")
                else:
                    # No prefs at all — set defaults.
                    await _db.owner_prefs.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"prefs_json": json.dumps(_DEFAULT_PREFS)}},
                    )
                    log.info(f"[owner-prefs] set default prefs_json for owner_id={doc.get('owner_id')}")
        except Exception as e:
            log.warning(f"[owner-prefs] migration: {e}")
        # Seed the debt-program (В ДОЛГ) test account — idempotent.
        try:
            await _db.users.update_one(
                {"telegram_id": DEBT_TEST_ACCOUNT},
                {"$set": {"debt_allowed": True},
                 "$setOnInsert": {
                     "telegram_id": DEBT_TEST_ACCOUNT,
                     "debt": 0.0,
                     "is_banned": False,
                     "first_seen": datetime.now(timezone.utc),
                     "orders_total": 0, "orders_done": 0, "orders_declined": 0,
                     "total_spent": 0, "support_tickets": 0,
                     "notes": "", "verified": False, "verify_requested": False,
                 }},
                upsert=True,
            )
        except Exception as e:
            log.warning(f"[debt] seed test account: {e}")
        log.info("✅ MongoDB connected — db: ambar")
    except Exception as e:
        log.error(f"MongoDB index error: {e}")
    return _db


def close():
    if _client:
        _client.close()


def _db_or_none():
    return _db


# ── Orders ────────────────────────────────────────────────────────────────────

# Список заказов целиком читают почти все экраны: выручка, люди, районы,
# очередь панели. Открытие AMBAR STAR — это десяток запросов подряд, и каждый
# тянул все четыреста заказов из Atlas по сети заново. Держим их две секунды:
# столько живёт одна пачка запросов от одной страницы. Любая запись сбрасывает
# кэш сразу, поэтому «свежесть» здесь ничем не жертвует — новый заказ виден в
# тот же миг, что и раньше.
_ORDERS_CACHE = {"at": 0.0, "docs": None}
_ORDERS_WIN: dict = {}          # окно по времени → (когда прочитали, заказы)
_ORDERS_TTL = 2.0


def _orders_dirty():
    _ORDERS_CACHE["docs"] = None
    _ORDERS_WIN.clear()


async def save_order(oid: str, data: dict):
    db = _db_or_none()
    if db is None: return
    await db.orders.update_one({"order_id": oid}, {"$set": data}, upsert=True)
    _orders_dirty()


async def update_order(oid: str, **kw):
    db = _db_or_none()
    if db is None: return
    await db.orders.update_one({"order_id": oid}, {"$set": kw})
    _orders_dirty()


# ── Экстренная ситуация ─────────────────────────────────────────────────────
# Водитель может оказаться там, где в его телефон смотрит кто-то ещё. Тогда он
# переводит приложение в скрытый режим, и оно перестаёт быть приложением
# доставки. Состояние держим на сервере, а не только в телефоне: снаружи должно
# быть видно, что человек в беде, а само приложение может быть закрыто,
# переустановлено или разряжено.
async def panic_set(driver: str, on: bool, at, meta: dict = None) -> None:
    db = _db_or_none()
    if db is None: return
    if on:
        await db.panic.replace_one(
            {"_id": driver}, {"_id": driver, "on": True, "at": at, **(meta or {})},
            upsert=True)
    else:
        await db.panic.update_one({"_id": driver},
                                  {"$set": {"on": False, "off_at": at}})


async def panic_get(driver: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    doc = await db.panic.find_one({"_id": driver})
    return doc if (doc or {}).get("on") else None


async def panic_all() -> list:
    """Кто сейчас в скрытом режиме — для панели и для владельца."""
    db = _db_or_none()
    if db is None: return []
    return await db.panic.find({"on": True}).to_list(length=50)


# ── Переписка по заказу ─────────────────────────────────────────────────────
# Водитель и оператор разговаривают о конкретном заказе — и разговор живёт на
# самом заказе, а не отдельной перепиской. Причина простая: обсуждают всегда
# «этот адрес», «эти бутылки», и через неделю в споре нужна не переписка, а
# заказ вместе с ней.
#
# Кто где остановился, помним двумя метками времени, а не флагом «прочитано» на
# каждом сообщении: сторон всего две, и вопрос у обеих один — есть ли что-то
# новее того, что я видел.
async def order_chat_add(oid: str, msg: dict) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    doc = await db.orders.find_one_and_update(
        {"order_id": oid},
        {"$push": {"chat": msg},
         "$set": {"chat_at": msg.get("at"),
                  # Своё сообщение писавший уже видел — иначе счётчик
                  # непрочитанного загорелся бы у самого автора.
                  f"chat_seen_{msg.get('by')}": msg.get("at")}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if doc: _orders_dirty()
    return doc


async def order_chat_seen(oid: str, side: str, at: str) -> None:
    db = _db_or_none()
    if db is None: return
    await db.orders.update_one({"order_id": oid}, {"$set": {f"chat_seen_{side}": at}})
    _orders_dirty()


async def get_order(oid: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.orders.find_one({"order_id": oid}, {"_id": 0})


async def get_active_orders(telegram_id: int) -> list:
    """Return all pending/approved/delivered orders for a user, newest first.
    Delivered orders are included so the in-app review UI can be shown,
    but excluded once the user has already submitted a review."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.orders.find(
        {"customer_id": telegram_id, "status": {"$in": ["pending", "approved", "delivered"]},
         "$or": [
             {"status": {"$in": ["pending", "approved"]}},
             {"status": "delivered", "review_score": {"$exists": False}, "review_skipped": {"$ne": True}},
         ]},
        {"_id": 0},
        sort=[("timestamp", -1)]
    )
    return await cursor.to_list(length=10)


async def get_all_orders(office_id=None) -> dict:
    """Returns {order_id: order_doc, ...}.

    office_id can be:
      • None  → no office filter (returns all orders)
      • str   → orders from that single office
      • list  → orders from any office in the list (used by operators who
                handle multiple offices — central+north+south, etc.)
    Empty list is treated as 'no orders for this operator' to avoid
    accidentally returning everyone's orders to a non-operator."""
    import time as _t
    db = _db_or_none()
    if db is None: return {}
    if office_id is None:
        if _ORDERS_CACHE["docs"] is not None and \
           _t.monotonic() - _ORDERS_CACHE["at"] < _ORDERS_TTL:
            return _ORDERS_CACHE["docs"]
        filt = {}
    elif isinstance(office_id, (list, tuple, set)):
        if not office_id:
            return {}
        filt = {"office_id": {"$in": list(office_id)}}
    else:
        filt = {"office_id": office_id}
    cursor = db.orders.find(filt, {"_id": 0})
    docs = await cursor.to_list(length=2000)
    out = {o["order_id"]: o for o in docs}
    if office_id is None:
        _ORDERS_CACHE["docs"] = out
        _ORDERS_CACHE["at"] = _t.monotonic()
    return out


async def orders_from(since_iso: str) -> dict:
    """Заказы с указанного момента плюс все незакрытые — их возраст не важен.

    Экраны считают деньги за окно, а не за всю историю. Читать ради «сегодня»
    четыреста заказов — это мегабайт по сети, и на нашем тарифе Atlas он идёт
    семь секунд: скорость там режется по объёму, а не по числу запросов.
    Незакрытые берём любого возраста: заказ, висящий с ночи, обязан попасть в
    «в работе» независимо от выбранного окна.
    """
    import time as _t
    db = _db_or_none()
    if db is None: return {}
    hit = _ORDERS_WIN.get(since_iso)
    if hit and _t.monotonic() - hit[0] < _ORDERS_TTL:
        return hit[1]
    # Не тащим то, чем экран денег не пользуется: готовые строки чека и номера
    # сообщений в телеграме занимают треть каждого заказа и нужны только боту.
    cur = db.orders.find({"$or": [
        {"timestamp": {"$gte": since_iso}},
        {"status": {"$in": ["pending", "approved"]}},
    ]}, {"_id": 0, "item_lines": 0, "op_msg_ids": 0, "customer_msg_ids": 0,
         "_delivered_notif_msgs": 0})
    docs = await cur.to_list(length=2000)
    out = {o["order_id"]: o for o in docs}
    # Панель открывает несколько экранов сразу, и все спрашивают одно окно.
    # Держим его те же две секунды, что и полный список.
    if len(_ORDERS_WIN) > 8: _ORDERS_WIN.clear()
    _ORDERS_WIN[since_iso] = (_t.monotonic(), out)
    return out


async def get_user_orders(tg_id: int) -> list:
    db = _db_or_none()
    if db is None: return []
    # Match both int and string customer_id (handles DB migration type mismatches)
    cursor = db.orders.find(
        {"customer_id": {"$in": [tg_id, str(tg_id)]}},
        {"_id": 0}
    ).sort("timestamp", -1)
    return await cursor.to_list(length=200)


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(telegram_id: int, **fields):
    """Upsert user doc. Matches existing schema field names."""
    db = _db_or_none()
    if db is None: return
    now = datetime.now(timezone.utc)
    fields["last_seen"] = now
    # Move phone → add to phones array if provided
    phone = fields.pop("phone", None)
    set_fields = fields
    update = {
        "$set": set_fields,
        "$setOnInsert": {
            "telegram_id": telegram_id,
            "is_banned": False,
            "first_seen": now,
            "orders_total": 0,
            "orders_done": 0,
            "orders_declined": 0,
            "total_spent": 0,
            "support_tickets": 0,
            "notes": "",
            "verified": False,
            "verify_requested": False,
        },
    }
    if phone:
        # Normalize to digits only so "+971 50 123 4567" and "971501234567"
        # don't both get stored as separate entries in the phones array.
        digits = re.sub(r"\D", "", str(phone))
        if digits:
            update["$addToSet"] = {"phones": digits}
    await db.users.update_one({"telegram_id": telegram_id}, update, upsert=True)


async def get_user(telegram_id: int) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.users.find_one({"telegram_id": telegram_id}, {"_id": 0})


async def is_banned(telegram_id: int) -> bool:
    u = await get_user(telegram_id)
    if not u:
        return False
    # New schema: explicit is_banned field
    if u.get("is_banned") is True:
        return True
    # Old schema fallback: documents migrated from JSON had ban_reason but no is_banned field.
    # If is_banned is not explicitly False (i.e. was never set) and ban_reason exists → banned.
    if u.get("is_banned") is None and u.get("ban_reason"):
        # Backfill the field so future checks are fast
        db = _db_or_none()
        if db is not None:
            try:
                await db.users.update_one(
                    {"telegram_id": telegram_id},
                    {"$set": {"is_banned": True}}
                )
            except Exception:
                pass
        return True
    return False


async def ban_user(telegram_id: int, reason: str, by: int):
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"is_banned": True, "ban_reason": reason, "banned_by": by,
                  "banned_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


async def unban_user(telegram_id: int):
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"is_banned": False, "ban_reason": None, "banned_by": None, "banned_at": None}},
    )


async def _increment_user(telegram_id: int, **counters):
    """Atomically increment numeric fields on a user doc."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one({"telegram_id": telegram_id}, {"$inc": counters})


async def get_all_banned() -> list:
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find({"is_banned": True}, {"_id": 0})
    return await cursor.to_list(length=500)


# ── Addresses ─────────────────────────────────────────────────────────────────

async def save_address(telegram_id: int, addr_entry: dict):
    """Адрес клиента — в его профиль, наверх списка, максимум восемь.

    Приложение держит адреса в памяти телефона, и до сих пор этим всё и
    заканчивалось: в заказ уезжала копия строки, а в базе у человека адресов
    не было вовсе. Отсюда «нет сохранённых адресов» у клиента, который только
    что сделал заказ, и невозможность подсказать адрес оператору по телефону.
    Теперь каждый заказ пополняет книгу адресов: повтор не плодим, считаем,
    сколько раз ездили, и помним, когда были в последний раз."""
    db = _db_or_none()
    if db is None: return
    addr = (addr_entry.get("address") or "").strip()
    if not addr:
        return
    u = await get_user(telegram_id) or {}
    lst = list(u.get("addresses", []) or [])
    norm = addr.lower()
    prev = next((a for a in lst if (a.get("address") or "").strip().lower() == norm), None)
    lst = [a for a in lst if (a.get("address") or "").strip().lower() != norm]
    entry = {**(prev or {}), **{k: v for k, v in addr_entry.items() if v not in (None, "", {})}}
    entry["address"] = addr
    entry["orders"] = int((prev or {}).get("orders") or 0) + 1
    entry["used_at"] = addr_entry.get("used_at") or datetime.now(timezone.utc).isoformat()
    if not prev:
        entry["first_at"] = entry["used_at"]
    lst.insert(0, entry)
    await upsert_user(telegram_id, addresses=lst[:8])


# ── User state ────────────────────────────────────────────────────────────────

async def get_ustate(telegram_id: int) -> dict:
    u = await get_user(telegram_id)
    return (u or {}).get("state", {})


async def set_ustate(telegram_id: int, data: dict):
    await upsert_user(telegram_id, state=data)


async def upd_ustate(telegram_id: int, **kw):
    u     = await get_user(telegram_id) or {}
    state = {**u.get("state", {}), **kw}
    await upsert_user(telegram_id, state=state)


# ── Support messages ──────────────────────────────────────────────────────────

async def get_support_conv(conv_key: str) -> list:
    db = _db_or_none()
    if db is None: return []
    doc = await db.support_messages.find_one({"conv_key": conv_key})
    return doc.get("messages", []) if doc else []


async def support_threads_brief(limit: int = 300) -> list:
    """Все переписки, но только с последним сообщением каждой.

    Список тикетов в панели оператора обновляется постоянно, а переписки растут;
    тянуть их целиком ради одной строки предпросмотра — лишний мегабайт на
    каждый опрос."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.support_messages.find(
        {}, {"_id": 0, "conv_key": 1, "channel": 1, "seen_operator": 1,
             "messages": {"$slice": -1}})
    return await cursor.to_list(length=limit)


async def support_mark_seen(conv_key: str, ts: str):
    """Оператор открыл переписку — значит прочитал её по это сообщение.

    Хранится метка времени последнего увиденного сообщения, а не флаг: придёт
    новое — переписка снова станет непрочитанной сама, без отдельного сброса."""
    db = _db_or_none()
    if db is None or not ts:
        return
    await db.support_messages.update_one(
        {"conv_key": conv_key}, {"$set": {"seen_operator": str(ts)}}, upsert=True)


async def users_by_ids(ids: list) -> dict:
    """Пачкой: {telegram_id: user}. По одному — это N запросов на список."""
    db = _db_or_none()
    if db is None: return {}
    try:
        want = [int(i) for i in ids]
    except (TypeError, ValueError):
        return {}
    if not want: return {}
    docs = await db.users.find({"telegram_id": {"$in": want}}, {"_id": 0}).to_list(length=len(want) + 10)
    return {int(d.get("telegram_id") or 0): d for d in docs}


async def get_recent_support_convs(limit: int = 50) -> list:
    """Recent support conversations, newest-last-message first (owner app list)."""
    db = _db_or_none()
    if db is None: return []
    docs = await db.support_messages.find({}, {"_id": 0}).to_list(length=500)
    docs.sort(key=lambda d: (d.get("messages") or [{}])[-1].get("ts", ""), reverse=True)
    return docs[:limit]


async def append_support_msg(conv_key: str, msg: dict):
    db = _db_or_none()
    if db is None: return
    await db.support_messages.update_one(
        {"conv_key": conv_key},
        {"$push": {"messages": msg}},
        upsert=True,
    )


async def save_support_map_entry(fwd_id: str, entry: dict):
    db = _db_or_none()
    if db is None: return
    await db.support_map.update_one(
        {"fwd_msg_id": fwd_id},
        {"$set": {**entry, "fwd_msg_id": fwd_id}},
        upsert=True,
    )


async def save_support_fwd_id(conv_key: str, ts: str, op_id: int, fwd_msg_id: int):
    """Map a message timestamp to its forwarded Telegram message ID per operator."""
    db = _db_or_none()
    if db is None: return
    await db.support_fwd_ids.update_one(
        {"conv_key": conv_key, "ts": ts, "op_id": op_id},
        {"$set": {"fwd_msg_id": fwd_msg_id}},
        upsert=True,
    )


async def get_support_fwd_id(conv_key: str, ts: str, op_id: int) -> int | None:
    """Look up the forwarded Telegram message ID for a given message."""
    db = _db_or_none()
    if db is None: return None
    doc = await db.support_fwd_ids.find_one(
        {"conv_key": conv_key, "ts": ts, "op_id": op_id}
    )
    return doc.get("fwd_msg_id") if doc else None


async def get_support_map_entry(fwd_id: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.support_map.find_one({"fwd_msg_id": fwd_id}, {"_id": 0})


async def set_user_field(telegram_id: int, **fields):
    """Set arbitrary fields on a user document."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": fields})


async def verify_user(telegram_id: int):
    """Mark user as verified (operator-approved)."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"verified": True, "verified_at": datetime.now(timezone.utc).isoformat()}},
    )


async def decline_verification(telegram_id: int):
    """Mark user's verification as declined."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"verify_declined": True, "verify_declined_at": datetime.now(timezone.utc).isoformat()}},
    )


async def undecline_verification(telegram_id: int):
    """Remove verification decline flag (for re-verification)."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"verify_declined": False, "verify_declined_at": None}},
    )


async def get_declined_verification_users() -> list:
    """Return users whose verification was declined."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find(
        {"verify_declined": True, "$or": [{"verified": False}, {"verified": {"$exists": False}}]},
        {"_id": 0}
    )
    return await cursor.to_list(length=200)


async def get_pending_orders_for_user(customer_id: int) -> list:
    """Return pending orders for a given customer."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.orders.find(
        {"customer_id": customer_id, "status": "pending"},
        {"_id": 0}
    )
    return await cursor.to_list(length=50)


async def submit_verify_request(telegram_id: int, recommender_name: str, recommender_phone: str,
                                 source: str = "", source_detail: str = ""):
    """Store verification request info and mark as pending."""
    db = _db_or_none()
    if db is None: return
    fields = {
        "verify_requested": True,
        "verify_source": source,
        "verify_source_detail": source_detail,
        "verify_recommender_name": recommender_name,
        "verify_recommender_phone": recommender_phone,
        "verify_requested_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": fields},
    )


# ── Shifts ────────────────────────────────────────────────────────────────────

async def open_shift(operator_id: int, office_id: str = None):
    """Open a new shift for an operator. Close any existing open shift first."""
    db = _db_or_none()
    if db is None: return
    # Close any existing open shift
    await db.shifts.update_many(
        {"operator_id": operator_id, "status": "open"},
        {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}},
    )
    await db.shifts.insert_one({
        "operator_id": operator_id,
        "office_id": office_id,
        "status": "open",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
    })


async def close_shift(operator_id: int) -> dict | None:
    """Close the active shift and return the shift doc."""
    db = _db_or_none()
    if db is None: return None
    now = datetime.now(timezone.utc).isoformat()
    shift = await db.shifts.find_one(
        {"operator_id": operator_id, "status": "open"}, {"_id": 0}
    )
    if not shift:
        return None
    await db.shifts.update_one(
        {"operator_id": operator_id, "status": "open"},
        {"$set": {"status": "closed", "closed_at": now}},
    )
    shift["closed_at"] = now
    shift["status"] = "closed"
    return shift


async def get_active_shift(operator_id: int) -> dict | None:
    """Return the currently open shift for an operator, or None."""
    db = _db_or_none()
    if db is None: return None
    return await db.shifts.find_one(
        {"operator_id": operator_id, "status": "open"}, {"_id": 0}
    )


async def get_orders_in_range(start_iso: str, end_iso: str, office_id: str = None,
                              limit: int | None = 500, fields: list = None) -> list:
    """Заказы за период.

    limit обрезает по убыванию времени, то есть отрезает всегда самое старое.
    Для суток пятисот хватает с запасом, а на длинных окнах такая обрезка
    выглядит как «в начале месяца ничего не продавали» — и портит ровно те
    числа, ради которых длинное окно и берут. Кто просит месяц и больше,
    передаёт limit=None: пусть лучше запрос будет тяжелее, чем итог неверным.

    fields — проекция: на длинных окнах из заказа нужны четыре поля, а не
    адреса с перепиской. Меньше байтов по сети — быстрее и дешевле."""
    db = _db_or_none()
    if db is None: return []
    filt = {"timestamp": {"$gte": start_iso, "$lte": end_iso}}
    if office_id:
        filt["office_id"] = office_id
    proj = {"_id": 0}
    if fields:
        proj.update({f: 1 for f in fields})
    cursor = db.orders.find(filt, proj).sort("timestamp", -1)
    return await cursor.to_list(length=limit)


async def get_unverified_users_with_orders() -> list:
    """Return users who are not verified and have placed at least one order."""
    db = _db_or_none()
    if db is None: return []
    # Get distinct customer_ids from orders
    order_cids = await db.orders.distinct("customer_id")
    # Normalize to int (handles possible string ids)
    int_cids = set()
    for c in order_cids:
        try:
            int_cids.add(int(c))
        except (ValueError, TypeError):
            pass
    if not int_cids:
        return []
    cursor = db.users.find(
        {"telegram_id": {"$in": list(int_cids)}, "$or": [{"verified": False}, {"verified": {"$exists": False}}]},
        {"_id": 0}
    )
    return await cursor.to_list(length=200)


async def get_customers_invited_by(operator_id: int) -> list:
    """Return customers who joined via this operator's /start op_<id> link."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find(
        {"invited_by_operator": operator_id},
        {"_id": 0}
    ).sort("invited_at", -1)
    return await cursor.to_list(length=500)


async def get_common_invite_customers() -> list:
    """Return customers who joined via the common /start op link (invited_by_operator=0)."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find(
        {"invited_by_operator": 0},
        {"_id": 0}
    ).sort("invited_at", -1)
    return await cursor.to_list(length=500)


async def get_owner_prefs(owner_id: int) -> dict:
    """Notification preferences for one owner — what events they want pushed
    via @ambar_manage_bot.  Returns sensible defaults if nothing saved yet."""
    db = _db_or_none()
    if db is None: return {}
    doc = await db.owner_prefs.find_one({"owner_id": owner_id}, {"_id": 0})
    if not doc:
        return {
            "owner_id": owner_id,
            "master": True,
            "preset": "important",
            "quiet": {"enabled": False, "from": "22:00", "to": "08:00"},
            "prefs": dict(_DEFAULT_PREFS),
        }
    if "prefs_json" in doc:
        try:
            doc["prefs"] = json.loads(doc.pop("prefs_json"))
        except Exception:
            doc["prefs"] = {}
    return doc


async def set_owner_prefs(owner_id: int, body: dict) -> None:
    """Upsert the full notif-pref doc. The prefs dict (which has dotted keys
    like 'orders.new') is stored as a JSON string to avoid MongoDB dot-notation
    issues."""
    db = _db_or_none()
    if db is None: return
    doc = {
        "owner_id": owner_id,
        "master": body.get("master", True),
        "preset": body.get("preset", "custom"),
        "quiet": body.get("quiet", {}),
        "prefs_json": json.dumps(body.get("prefs", {})),
        "revThreshold": body.get("revThreshold", 10000),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.owner_prefs.update_one(
        {"owner_id": owner_id},
        {"$set": doc},
        upsert=True,
    )
    log.info(f"[owner-prefs] saved for {owner_id}: master={doc['master']}")


async def set_quiet_msg_id(owner_id: int, msg_id: int | None) -> None:
    """Store the Telegram message_id of the quiet-mode indicator message."""
    db = _db_or_none()
    if db is None: return
    if msg_id is None:
        await db.owner_prefs.update_one(
            {"owner_id": owner_id}, {"$unset": {"_quiet_msg_id": ""}})
    else:
        await db.owner_prefs.update_one(
            {"owner_id": owner_id}, {"$set": {"_quiet_msg_id": msg_id}})


async def get_quiet_msg_id(owner_id: int) -> int | None:
    """Get the stored quiet-mode Telegram message_id."""
    db = _db_or_none()
    if db is None: return None
    doc = await db.owner_prefs.find_one(
        {"owner_id": owner_id}, {"_id": 0, "_quiet_msg_id": 1})
    return (doc or {}).get("_quiet_msg_id")


_DEFAULT_PREFS = {
    "orders.new": True, "orders.new500": False, "orders.new1000": True,
    "orders.delivered": False, "orders.cancelled": True, "orders.declined": True,
    "orders.driver_done": True, "orders.reverted": True, "orders.edited": True,
    "orders.backfilled": True,
    "timing.late45": True, "timing.notAccepted5": True, "timing.enroute30": False,
    "reviews.bad3": True, "reviews.good5": False, "reviews.comment": True, "reviews.any": False,
    "digest.morning": True, "digest.evening": True, "digest.weekly": False, "digest.monthly": False,
    "stock.low": True, "stock.out": True,
    # Списание — это убыток, и узнавать о нём из отчёта в конце месяца поздно.
    "stock.writeoff": True,
    # Смена закрыта у всех — с этим сообщением приходит и заявка в магазин:
    # выключать его незачем, но право такое есть.
    "shift.closed": True,
    # Приёмка: итог по району и отдельно — то, на что стоит посмотреть глазами.
    "supply.done": True, "supply.flag": True,
    "customers.new": False, "customers.verify": True, "customers.verified": True, "customers.vip": False,
    "customers.vipReturn": False, "customers.vipChurn": False,
    "ops.officeEmpty": True,
    # Чужая бутылка у водителя — не паника, но владелец должен узнать в тот же
    # день: по умолчанию включено.
    "qr.alien": True,
    # Экстренная ситуация у водителя. Выключить нельзя по смыслу — здесь стоит
    # ради того, чтобы событие было в общем списке уведомлений.
    "driver.panic": True,
    "finance.revenueLow": True, "finance.avgDrop": True,
    "finance.cancelSpike": True, "finance.record": False, "finance.tipHigh": False,
    "support.new": False, "support.noreply": True, "support.complaint": True, "support.escalation": False,
    "support.replied": True,
    "system.botDown": True, "system.apiErrors": True, "system.dbDown": True, "system.deploy": False,
    "delivery.browser": True,
}


async def ensure_owner_prefs(owner_id: int) -> None:
    """Atomic: create a default prefs doc only if none exists.
    Also cleans up duplicate docs and migrates old prefs→prefs_json."""
    db = _db_or_none()
    if db is None or not owner_id: return

    # 1. Delete ALL duplicate docs for this owner, keeping only the newest.
    cursor = db.owner_prefs.find({"owner_id": owner_id}, {"_id": 1, "updated_at": 1})
    docs = await cursor.to_list(length=50)
    if len(docs) > 1:
        docs.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        dup_ids = [d["_id"] for d in docs[1:]]
        await db.owner_prefs.delete_many({"_id": {"$in": dup_ids}})
        log.info(f"[owner-prefs] cleaned {len(dup_ids)} duplicate docs for {owner_id}")

    # 2. Atomic upsert: insert defaults only if no doc exists.
    await db.owner_prefs.update_one(
        {"owner_id": owner_id},
        {"$setOnInsert": {
            "owner_id": owner_id,
            "master": True,
            "preset": "important",
            "quiet": {"enabled": False, "from": "22:00", "to": "08:00"},
            "prefs_json": json.dumps(_DEFAULT_PREFS),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )

    # 3. Migrate old format (prefs dict → prefs_json string).
    doc = await db.owner_prefs.find_one({"owner_id": owner_id})
    if doc and "prefs_json" not in doc and isinstance(doc.get("prefs"), dict):
        await db.owner_prefs.update_one(
            {"_id": doc["_id"]},
            {"$set": {"prefs_json": json.dumps(doc["prefs"])}, "$unset": {"prefs": ""}},
        )


async def get_owners_subscribed_to(event_key: str) -> list:
    """Return owner_ids whose prefs have event_key enabled, master is on,
    and we're not in their quiet hours.

    Managers who have never opened the dashboard (no prefs doc) get
    _DEFAULT_PREFS applied — so they receive important notifications
    out of the box.  Once they save any prefs, only those are used."""
    db = _db_or_none()
    if db is None: return []

    # 1. Fetch ALL prefs docs (including master=False) so we know who has
    #    explicitly configured their prefs.
    cursor = db.owner_prefs.find(
        {},
        {"_id": 0, "owner_id": 1, "master": 1, "quiet": 1,
         "prefs_json": 1, "prefs": 1, "updated_at": 1},
    )
    rows = await cursor.to_list(length=200)

    # Deduplicate: if multiple docs exist for one owner_id, use the newest.
    best: dict[int, dict] = {}
    for r in rows:
        oid = r.get("owner_id")
        if oid is None:
            continue
        prev = best.get(oid)
        if prev is None or (r.get("updated_at", "") > prev.get("updated_at", "")):
            best[oid] = r

    configured_ids = set(best.keys())

    # 2. Collect every manager ID (env owners + env managers + DB managers).
    all_manager_ids = set(OWNER_IDS) | set(MANAGER_IDS)
    try:
        db_managers = await get_managers()
        for m in db_managers:
            if not m.get("blocked"):
                all_manager_ids.add(m["telegram_id"])
    except Exception:
        pass

    # 3. Managers without a prefs doc get _DEFAULT_PREFS.
    for mid in all_manager_ids:
        if mid not in configured_ids:
            best[mid] = {
                "owner_id": mid,
                "master": True,
                "prefs_json": json.dumps(_DEFAULT_PREFS),
            }

    # 4. Filter by master, event_key, and quiet hours.
    out = []
    now_h = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=4))).hour
    for r in best.values():
        if r.get("master") is False:
            continue
        if "prefs_json" in r:
            try:
                p = json.loads(r["prefs_json"])
            except Exception:
                p = {}
        else:
            p = r.get("prefs") or {}
        # Ключа может не быть вовсе: событие завели позже, чем человек сохранил
        # настройки. Молчать в этом случае — худший вариант: он не отказывался,
        # он просто не знал. Берём значение по умолчанию.
        if not p.get(event_key, _DEFAULT_PREFS.get(event_key, False)):
            continue
        q = r.get("quiet") or {}
        if q.get("enabled"):
            try:
                from_h = int(str(q.get("from","22:00")).split(":")[0])
                to_h   = int(str(q.get("to","08:00")).split(":")[0])
                in_quiet = (now_h >= from_h or now_h < to_h) if from_h >= to_h else (from_h <= now_h < to_h)
                if in_quiet:
                    continue
            except Exception:
                pass
        out.append(r["owner_id"])
    return out


async def get_all_manager_ids() -> list:
    """Every owner/manager id (env owners + env managers + active DB managers),
    ignoring prefs/master/quiet — for force-delivering critical notifications
    (e.g. crypto-paid orders) that must reach owners regardless of filters."""
    ids = set(OWNER_IDS) | set(MANAGER_IDS)
    try:
        for m in await get_managers():
            if not m.get("blocked") and m.get("telegram_id"):
                ids.add(m["telegram_id"])
    except Exception:
        pass
    return list(ids)


async def get_configured_owner_ids() -> list:
    """Return owner_ids that have ANY doc in owner_prefs (regardless of master)."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.owner_prefs.find({}, {"_id": 0, "owner_id": 1})
    docs = await cursor.to_list(length=200)
    return [d["owner_id"] for d in docs if "owner_id" in d]


async def get_managers() -> list:
    """Return all DB-stored managers as plain dicts. Sorted newest-first."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.owner_managers.find({}, {"_id": 0}).sort("added_at", -1)
    return await cursor.to_list(length=200)


async def add_manager(telegram_id: int, name: str = "", username: str = "", added_by: int = 0) -> dict:
    """Upsert a manager. Returns the saved document. No-op if telegram_id is 0."""
    db = _db_or_none()
    if db is None or not telegram_id:
        return {}
    doc = {
        "telegram_id": int(telegram_id),
        "name": (name or "").strip(),
        "username": (username or "").lstrip("@").strip(),
        "added_by": int(added_by) if added_by else 0,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.owner_managers.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def remove_manager(telegram_id: int) -> bool:
    """Delete a manager by telegram_id. Returns True if a row was removed."""
    db = _db_or_none()
    if db is None: return False
    res = await db.owner_managers.delete_one({"telegram_id": int(telegram_id)})
    return res.deleted_count > 0


async def is_manager(telegram_id: int) -> bool:
    """Check if a Telegram user is a DB-stored manager AND not blocked."""
    db = _db_or_none()
    if db is None: return False
    doc = await db.owner_managers.find_one({"telegram_id": int(telegram_id)}, {"_id": 1, "blocked": 1})
    return bool(doc) and not doc.get("blocked", False)


async def is_manager_blocked(telegram_id: int) -> bool:
    """True if user has a blocked record in owner_managers."""
    db = _db_or_none()
    if db is None: return False
    doc = await db.owner_managers.find_one({"telegram_id": int(telegram_id)}, {"_id": 1, "blocked": 1})
    return bool(doc) and bool(doc.get("blocked", False))


async def set_manager_blocked(telegram_id: int, blocked: bool, by: int = 0) -> bool:
    """Toggle the blocked flag on a manager. Returns True if a row was updated."""
    db = _db_or_none()
    if db is None: return False
    upd = {"blocked": bool(blocked)}
    if blocked:
        upd["blocked_at"] = datetime.now(timezone.utc).isoformat()
        upd["blocked_by"] = int(by) if by else 0
    else:
        upd["blocked_at"] = ""
        upd["blocked_by"] = 0
    res = await db.owner_managers.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": upd},
    )
    return res.matched_count > 0


# ── Owner access log: track unauthorized attempts + explicit blocks ──────────
# Rows are keyed by telegram_id and have a status:
#   'pending'  — unauthorized user tried to open the bot, awaiting decision
#   'blocked'  — explicitly denied by owner (sees "blocked" deny screen)
#   'approved' — a record exists but the user is allowed (rare; usually means
#                they were promoted to manager and we kept the log row for history)
async def log_access_attempt(user: dict, request_path: str = "", ip: str = "", ua: str = "") -> dict:
    """Upsert an access-log entry from a Telegram user dict. Returns
    {is_first_in_window: bool, doc: dict} so the caller can decide whether
    to fire a Telegram alert (we throttle to once per 24h per user)."""
    db = _db_or_none()
    if db is None or not user:
        return {"is_first_in_window": False, "doc": {}}
    tg_id = int(user.get("id") or 0)
    if not tg_id:
        return {"is_first_in_window": False, "doc": {}}
    now = datetime.now(timezone.utc)
    existing = await db.owner_access_log.find_one({"telegram_id": tg_id}, {"_id": 0})
    is_first = True
    if existing:
        try:
            last = datetime.fromisoformat(existing.get("last_attempt_at", ""))
            if (now - last).total_seconds() < 24 * 3600:
                is_first = False
        except Exception:
            pass
    update = {
        "telegram_id":      tg_id,
        "username":         user.get("username", ""),
        "first_name":       user.get("first_name", ""),
        "last_name":        user.get("last_name", ""),
        "language_code":    user.get("language_code", ""),
        "is_premium":       bool(user.get("is_premium", False)),
        "allows_pm":        bool(user.get("allows_write_to_pm", False)),
        "photo_url":        user.get("photo_url", ""),
        "last_attempt_at":  now.isoformat(),
        "last_path":        request_path,
        "last_ip":          ip,
        "last_user_agent":  ua,
    }
    inc = {"attempts": 1}
    set_on_insert = {
        "first_attempt_at": now.isoformat(),
        "status":           "pending",
        "blocked_at":       "",
        "blocked_by":       0,
    }
    await db.owner_access_log.update_one(
        {"telegram_id": tg_id},
        {"$set": update, "$inc": inc, "$setOnInsert": set_on_insert},
        upsert=True,
    )
    doc = await db.owner_access_log.find_one({"telegram_id": tg_id}, {"_id": 0})
    return {"is_first_in_window": is_first, "doc": doc or {}}


async def get_access_log(status: str = "") -> list:
    """Return access-log entries, newest first. Filter by status if given."""
    db = _db_or_none()
    if db is None: return []
    q = {"status": status} if status else {}
    cursor = db.owner_access_log.find(q, {"_id": 0}).sort("last_attempt_at", -1)
    return await cursor.to_list(length=500)


async def get_access_entry(telegram_id: int) -> dict:
    db = _db_or_none()
    if db is None: return {}
    doc = await db.owner_access_log.find_one({"telegram_id": int(telegram_id)}, {"_id": 0})
    return doc or {}


async def set_access_status(telegram_id: int, status: str, by: int = 0) -> bool:
    """Update status of an access-log entry. Status: pending|blocked|approved."""
    db = _db_or_none()
    if db is None: return False
    if status not in ("pending", "blocked", "approved"):
        return False
    upd = {"status": status}
    if status == "blocked":
        upd["blocked_at"] = datetime.now(timezone.utc).isoformat()
        upd["blocked_by"] = int(by) if by else 0
    else:
        upd["blocked_at"] = ""
        upd["blocked_by"] = 0
    res = await db.owner_access_log.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": upd},
    )
    return res.matched_count > 0


async def get_access_attempt(telegram_id: int) -> dict | None:
    """Строка журнала попыток доступа. Нужна, чтобы подставить имя и @username
    для id, которые есть только в .env и больше нигде."""
    db = _db_or_none()
    if db is None: return None
    return await db.owner_access_log.find_one(
        {"telegram_id": int(telegram_id)}, {"_id": 0})


async def is_access_blocked(telegram_id: int) -> bool:
    """True if user has an access-log row with status='blocked'."""
    db = _db_or_none()
    if db is None: return False
    doc = await db.owner_access_log.find_one(
        {"telegram_id": int(telegram_id), "status": "blocked"},
        {"_id": 1},
    )
    return doc is not None


async def get_blocked_access_ids() -> set:
    """Return the set of telegram_ids with status='blocked' in access_log.
    Used to mark env-managers as blocked in the managers list UI without
    requiring them to have a DB row in owner_managers."""
    db = _db_or_none()
    if db is None: return set()
    cursor = db.owner_access_log.find({"status": "blocked"}, {"_id": 0, "telegram_id": 1})
    rows = await cursor.to_list(length=500)
    return {int(r["telegram_id"]) for r in rows if r.get("telegram_id")}


async def upsert_access_block(telegram_id: int, by: int = 0, hint: dict = None) -> dict:
    """Upsert an access-log row in 'blocked' status — used to revoke
    env-managers who don't have an owner_managers row. The optional `hint`
    can pre-fill name/username so the UI shows something nicer than just
    the bare ID."""
    db = _db_or_none()
    if db is None or not telegram_id:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    set_doc = {
        "telegram_id": int(telegram_id),
        "status":      "blocked",
        "blocked_at":  now,
        "blocked_by":  int(by) if by else 0,
        "last_attempt_at": now,
    }
    if hint:
        if hint.get("name"):     set_doc["first_name"] = hint["name"]
        if hint.get("username"): set_doc["username"]   = hint["username"].lstrip("@")
    set_on_insert = {
        "first_attempt_at": now,
        "attempts":         0,
        "first_name":       hint.get("name", "")     if hint else "",
        "last_name":        "",
        "username":         (hint.get("username", "") if hint else "").lstrip("@"),
        "language_code":    "",
        "is_premium":       False,
        "allows_pm":        False,
        "photo_url":        "",
        "last_path":        "",
        "last_ip":          "",
        "last_user_agent":  "",
    }
    await db.owner_access_log.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": set_doc, "$setOnInsert": set_on_insert},
        upsert=True,
    )
    doc = await db.owner_access_log.find_one({"telegram_id": int(telegram_id)}, {"_id": 0})
    return doc or {}


async def get_all_customers() -> list:
    """Return all user documents, newest-first."""
    db = _db_or_none()
    if db is None: return []
    # Потолок высокий не для красоты: по этому списку идёт рассылка, и клиент,
    # не попавший в выборку, молча её не получит. База растёт сотнями в месяц,
    # и прежние 2000 однажды отрезали бы хвост без единого следа в счётчиках.
    cursor = db.users.find({}, {"_id": 0}).sort("first_seen", -1)
    return await cursor.to_list(length=100000)


async def award_referral_points(referrer_id: int, referred_id: int, points: int):
    """Award referral points to the referrer and log the referral."""
    db = _db_or_none()
    if db is None: return
    await db.users.update_one(
        {"telegram_id": referrer_id},
        {
            "$inc": {"referral_points": points},
            "$push": {"referrals": {"user_id": referred_id, "points": points,
                                     "at": datetime.now(timezone.utc)}},
        },
    )


# ── Owner notifications ──────────────────────────────────────────────────────

async def insert_notification(event_key: str, text: str, owner_id: int = 0, meta: dict | None = None) -> None:
    """Persist a notification event. owner_id=0 means broadcast. `meta` carries
    routing hints for the owner app (e.g. the support conv_key) — the
    notifications API returns it verbatim."""
    db = _db_or_none()
    if db is None: return
    doc = {
        "event_key": event_key,
        "text": text,
        "owner_id": owner_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if meta:
        doc["meta"] = meta
    await db.owner_notifications.insert_one(doc)


async def get_notifications_since(since_iso: str, owner_id: int = 0, limit: int = 50) -> list:
    """Return notifications created after `since_iso`."""
    db = _db_or_none()
    if db is None: return []
    filt = {
        "created_at": {"$gt": since_iso},
        "$or": [{"owner_id": 0}, {"owner_id": owner_id}],
    }
    cursor = db.owner_notifications.find(filt, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_recent_notifications(owner_id: int = 0, limit: int = 30) -> list:
    """Return the most recent N notifications."""
    db = _db_or_none()
    if db is None: return []
    filt = {"$or": [{"owner_id": 0}, {"owner_id": owner_id}]}
    cursor = db.owner_notifications.find(filt, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


# ── Broadcasts (owner self-serve promo sender) ───────────────────────────────
async def mark_user_unreachable(telegram_id: int, reason: str) -> None:
    """Телеграм отказался доставлять этому человеку — запомнить почему.

    Пишем только при смене состояния: рассылка идёт по всей базе, и слепой
    апдейт на каждого — это лишняя тысяча записей за проход."""
    db = _db_or_none()
    if db is None or not telegram_id: return
    await db.users.update_one(
        {"telegram_id": int(telegram_id), "unreachable": {"$ne": reason}},
        {"$set": {"unreachable": reason,
                  "unreachable_at": datetime.now(timezone.utc).isoformat()}})


async def clear_user_unreachable(telegram_id: int) -> None:
    """Дошло — значит человек снова доступен, и метка обязана уйти.

    Заблокировавший бота может его разблокировать, и узнать об этом можно
    только удавшейся отправкой. Метка, пережившая своё основание, вечно
    занижала бы аудиторию."""
    db = _db_or_none()
    if db is None or not telegram_id: return
    await db.users.update_one(
        {"telegram_id": int(telegram_id), "unreachable": {"$exists": True}},
        {"$unset": {"unreachable": "", "unreachable_at": ""}})


async def save_broadcast(doc: dict) -> None:
    """Upsert a broadcast job by job_id — live counters + final stats/history."""
    db = _db_or_none()
    if db is None or not doc.get("job_id"): return
    await db.broadcasts.update_one({"job_id": doc["job_id"]}, {"$set": doc}, upsert=True)


async def get_broadcast(job_id: str) -> dict | None:
    db = _db_or_none()
    if db is None or not job_id: return None
    return await db.broadcasts.find_one({"job_id": job_id}, {"_id": 0})


async def get_recent_broadcasts(limit: int = 10) -> list:
    db = _db_or_none()
    if db is None: return []
    cursor = db.broadcasts.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


# ── Crypto invoices (USDT TRC-20, watch-only) ────────────────────────────────
# An invoice lifecycle: waiting → confirmed (terminal) or expired. We credit only
# on irreversible (solidified) transfers, so there is no intermediate "detected"
# state server-side — "open" means strictly "waiting". The order payload is stored
# on the invoice so the confirmed payment can be promoted into a real order
# without ever trusting a client "paid" claim.
_CRYPTO_OPEN = ["waiting"]


def _dup_key_field(e: DuplicateKeyError) -> str:
    """Which unique index a DuplicateKeyError tripped: 'amount_usdt', 'order_id',
    'txid', or '' if undeterminable. Reads keyPattern (4.2+), falls back to text."""
    try:
        kp = (e.details or {}).get("keyPattern") or {}
        for f in ("amount_usdt", "order_id", "txid"):
            if f in kp:
                return f
    except Exception:
        pass
    msg = str(e)
    for f in ("amount_usdt", "order_id", "txid"):
        if f in msg:
            return f
    return ""


async def create_crypto_invoice(doc: dict) -> str:
    """Insert a new invoice. Returns 'ok', or which unique constraint blocked it:
    'dup_amount' (another waiting invoice holds this amount — caller should pick a
    new one and retry), 'dup_order' (order_id exists), or 'error'."""
    db = _db_or_none()
    if db is None: return "error"
    try:
        await db.crypto_invoices.insert_one(doc)
        return "ok"
    except DuplicateKeyError as e:
        field = _dup_key_field(e)
        if field == "amount_usdt": return "dup_amount"
        if field == "order_id":    return "dup_order"
        log.warning(f"[crypto] create invoice dup (oid={doc.get('order_id')}): {e}")
        return "error"
    except Exception as e:
        log.warning(f"[crypto] create invoice failed (oid={doc.get('order_id')}): {e}")
        return "error"


async def reissue_crypto_invoice(oid: str, fields: dict) -> str:
    """Re-quote an existing (expired) invoice in place. Returns 'ok', 'dup_amount'
    (the new amount collides with another waiting invoice — pick a new one and
    retry), or 'error'. Used instead of update_crypto_invoice when `fields` sets a
    new amount_usdt that must stay unique among waiting invoices."""
    db = _db_or_none()
    if db is None: return "error"
    try:
        await db.crypto_invoices.update_one({"order_id": oid}, {"$set": fields})
        return "ok"
    except DuplicateKeyError as e:
        if _dup_key_field(e) == "amount_usdt": return "dup_amount"
        log.warning(f"[crypto] reissue dup (oid={oid}): {e}")
        return "error"
    except Exception as e:
        log.warning(f"[crypto] reissue failed (oid={oid}): {e}")
        return "error"


async def get_crypto_invoice(oid: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.crypto_invoices.find_one({"order_id": oid}, {"_id": 0})


async def crypto_invoices_by_txids(txids: list) -> dict:
    """Счета, оплаченные этими переводами: {txid: счёт}.

    Нужно экрану кошелька: приход, за которым стоит наш счёт, — это оплата
    заказа, а приход без счёта — деньги, пришедшие мимо. Разделить их можно
    только так: txid у счёта уникален."""
    db = _db_or_none()
    txids = [t for t in (txids or []) if t]
    if db is None or not txids: return {}
    cur = db.crypto_invoices.find(
        {"txid": {"$in": txids}},
        {"_id": 0, "txid": 1, "order_id": 1, "amount_usdt": 1, "status": 1})
    return {d["txid"]: d for d in await cur.to_list(length=len(txids)) if d.get("txid")}


async def update_crypto_invoice(oid: str, **kw):
    db = _db_or_none()
    if db is None: return
    await db.crypto_invoices.update_one({"order_id": oid}, {"$set": kw})


async def list_open_crypto_invoices(limit: int = 200) -> list:
    """Invoices still awaiting payment / confirmation (non-terminal)."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.crypto_invoices.find({"status": {"$in": _CRYPTO_OPEN}}, {"_id": 0}).limit(limit)
    return await cursor.to_list(length=limit)


async def reserved_crypto_amounts() -> set:
    """USDT amounts currently reserved by open invoices. The invoice endpoint
    picks an amount NOT in this set so an incoming transfer maps to exactly one
    order (amount is the matching key alongside the unique-txid guard)."""
    db = _db_or_none()
    if db is None: return set()
    cursor = db.crypto_invoices.find({"status": {"$in": _CRYPTO_OPEN}},
                                     {"_id": 0, "amount_usdt": 1})
    docs = await cursor.to_list(length=1000)
    return {round(float(d.get("amount_usdt", 0)), 6) for d in docs}


async def claim_crypto_txid(oid: str, txid: str) -> bool:
    """Atomically bind `txid` to invoice `oid` exactly once (idempotency).

    Returns True only if THIS call performed the binding, or the invoice already
    carries this exact txid (idempotent re-call). Returns False if the invoice
    already has a different txid, or the txid is bound to another invoice (the
    unique-sparse index raises, which we catch) — the caller must NOT credit."""
    db = _db_or_none()
    if db is None: return False
    try:
        res = await db.crypto_invoices.update_one(
            {"order_id": oid, "$or": [{"txid": None}, {"txid": {"$exists": False}}]},
            {"$set": {"txid": txid}},
        )
        if res.modified_count == 1:
            return True
        cur = await db.crypto_invoices.find_one({"order_id": oid}, {"_id": 0, "txid": 1})
        return bool(cur and cur.get("txid") == txid)
    except Exception as e:
        log.warning(f"[crypto] claim_txid conflict (oid={oid} txid={txid}): {e}")
        return False


async def mark_crypto_confirmed(oid: str, confirmations: int) -> bool:
    """Atomically flip a not-yet-confirmed invoice to 'confirmed'. Returns True
    only if THIS call performed the flip — so confirmation is logged once even if
    the watcher sees the same transfer on several polls."""
    db = _db_or_none()
    if db is None: return False
    now = datetime.now(timezone.utc)
    res = await db.crypto_invoices.update_one(
        {"order_id": oid, "status": {"$ne": "confirmed"}},
        {"$set": {"status": "confirmed", "confirmations": confirmations,
                  "confirmed_at_ms": int(now.timestamp() * 1000),
                  "confirmed_at": now.isoformat()}},
    )
    return res.modified_count == 1


async def claim_crypto_promotion(oid: str) -> bool:
    """Exactly-once gate for promoting a confirmed invoice into a real order.

    Atomically stamps `promoted_at_ms` only if it is absent, returning True only
    to the caller that won. Promotion is fail-closed: we stamp BEFORE building the
    order, so a crash mid-promotion never double-creates / double-notifies — at
    worst an order isn't auto-created and is recoverable from the stored payload+txid."""
    db = _db_or_none()
    if db is None: return False
    res = await db.crypto_invoices.update_one(
        {"order_id": oid, "promoted_at_ms": {"$exists": False}},
        {"$set": {"promoted_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000)}},
    )
    return res.modified_count == 1


async def list_confirmed_unpromoted_crypto_invoices(limit: int = 50) -> list:
    """Confirmed invoices whose promotion hasn't completed (e.g. a restart landed
    between confirm and promote). The watcher retries these each tick."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.crypto_invoices.find(
        {"status": "confirmed", "promoted_at_ms": {"$exists": False}}, {"_id": 0}
    ).limit(limit)
    return await cursor.to_list(length=limit)


# ── Debt (В ДОЛГ) ─────────────────────────────────────────────────────────────
# Selected customers may take orders on credit. Fields on the user doc:
#   debt_allowed  — gates the В ДОЛГ payment option (whitelist, admin-managed)
#   debt          — current balance in AED (grows on delivery, admin edits down)
#   debt_history  — audit log: order deliveries and manual admin edits

DEBT_TEST_ACCOUNT = 686932322


def _round_aed(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


async def is_debt_allowed(telegram_id: int) -> bool:
    u = await get_user(int(telegram_id))
    return bool(u and u.get("debt_allowed") and not u.get("is_banned"))


async def get_debt(telegram_id: int) -> float:
    u = await get_user(int(telegram_id))
    return _round_aed((u or {}).get("debt"))


async def set_debt_allowed(telegram_id: int, allowed: bool, by: int = 0):
    """Enable/disable the В ДОЛГ payment option for a customer."""
    db = _db_or_none()
    if db is None: return
    now = datetime.now(timezone.utc)
    await db.users.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": {"debt_allowed": bool(allowed),
                  "debt_allowed_changed_at": now.isoformat(),
                  "debt_allowed_changed_by": int(by) if by else 0},
         "$setOnInsert": {
             "telegram_id": int(telegram_id), "debt": 0.0,
             "is_banned": False, "first_seen": now,
             "orders_total": 0, "orders_done": 0, "orders_declined": 0,
             "total_spent": 0, "support_tickets": 0,
             "notes": "", "verified": False, "verify_requested": False,
         }},
        upsert=True,
    )


async def add_debt(telegram_id: int, amount: float, order_id: str = "", note: str = ""):
    """Atomically shift a customer's debt by `amount` (negative to reduce).
    Used when a В ДОЛГ order is delivered (+total) or delivery is undone (−total)."""
    db = _db_or_none()
    if db is None: return
    delta = _round_aed(amount)
    entry = {
        "delta": delta,
        "order_id": order_id,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.update_one(
        {"telegram_id": int(telegram_id)},
        {"$inc": {"debt": delta}, "$push": {"debt_history": entry}},
    )


async def debts_of(ids: list) -> dict:
    """{telegram_id: долг} пачкой. Водителю на экран нужен долг сразу по всем
    клиентам смены, а спрашивать по одному — это запрос на каждый заказ."""
    db = _db_or_none()
    if db is None or not ids: return {}
    # $ne: 0 в монго ловит и тех, у кого поля нет вовсе, — а это почти все.
    cur = db.users.find({"telegram_id": {"$in": [int(i) for i in ids]},
                         "debt": {"$exists": True, "$ne": 0}},
                        {"_id": 0, "telegram_id": 1, "debt": 1})
    return {int(u["telegram_id"]): _round_aed(u.get("debt"))
            for u in await cur.to_list(length=200)}


async def set_debt(telegram_id: int, new_amount: float, by: int = 0, note: str = "") -> dict:
    """Admin edit: set debt to an absolute value (e.g. after a cash repayment).
    Returns {"old": …, "new": …}."""
    db = _db_or_none()
    if db is None: return {}
    old = await get_debt(telegram_id)
    new = _round_aed(new_amount)
    entry = {
        "set": new, "old": old,
        "by": int(by) if by else 0,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": {"debt": new}, "$push": {"debt_history": entry}},
    )
    return {"old": old, "new": new}


async def claim_debt_delivery(oid: str) -> bool:
    """Exactly-once gate: True only the first time a В ДОЛГ order is counted
    into the debt balance (protects against double-taps on «Доставлено»)."""
    db = _db_or_none()
    if db is None: return False
    res = await db.orders.update_one(
        {"order_id": oid, "debt_counted": {"$ne": True}},
        {"$set": {"debt_counted": True}},
    )
    if res.modified_count: _orders_dirty()
    return res.modified_count == 1


async def unclaim_debt_delivery(oid: str) -> bool:
    """Reverse of claim_debt_delivery — used by undo-delivered."""
    db = _db_or_none()
    if db is None: return False
    res = await db.orders.update_one(
        {"order_id": oid, "debt_counted": True},
        {"$set": {"debt_counted": False}},
    )
    if res.modified_count: _orders_dirty()
    return res.modified_count == 1


async def get_debtors() -> list:
    """Debt-program customers: whitelisted OR still carrying a balance
    (so revoking debt_allowed never hides an unpaid debt). Biggest debt first."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find(
        {"$or": [{"debt_allowed": True}, {"debt": {"$gt": 0}}]},
        {"_id": 0},
    ).sort("debt", -1)
    return await cursor.to_list(length=500)


# ─── склад: пересчёты, перемещения, нормы ──────────────────────────────────
# Пересчёт хранится целиком за день и район: так видно и что ввели, и с чем
# сравнивали, а вчерашний документ служит опорой для завтрашнего ожидания.

async def save_stock_count(district: str, day: str, doc: dict):
    db = _db_or_none()
    if db is None: return
    await db.stock_counts.update_one(
        {"district": district, "day": day}, {"$set": doc}, upsert=True)


async def get_stock_count(district: str, day: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.stock_counts.find_one({"district": district, "day": day}, {"_id": 0})


async def get_last_stock_count(district: str, before_day: str | None = None) -> dict | None:
    """Последний пересчёт района. before_day — строго раньше этой даты: именно
    он служит отправной точкой для ожидаемого остатка."""
    db = _db_or_none()
    if db is None: return None
    q = {"district": district}
    if before_day:
        q["day"] = {"$lt": before_day}
    cur = db.stock_counts.find(q, {"_id": 0}).sort("day", -1).limit(1)
    rows = await cur.to_list(length=1)
    return rows[0] if rows else None


async def get_stock_counts_recent(district: str, before_day: str | None = None,
                                  limit: int = 40) -> list:
    """Последние пересчёты района, свежие первыми — с позициями.

    Нужны, чтобы знать, когда каждую позицию последний раз реально считали
    глазами: то, что не продавалось, всё равно может уйти боем или в карман,
    и такие позиции надо возвращать на проверку по кругу."""
    db = _db_or_none()
    if db is None: return []
    q = {"district": district}
    if before_day:
        q["day"] = {"$lte": before_day}
    cur = db.stock_counts.find(q, {"_id": 0}).sort("day", -1).limit(int(limit))
    return await cur.to_list(length=int(limit))


# ── привязка водителей ───────────────────────────────────────────────────────
# Водитель до сих пор существовал в системе только как имя в заказе. Чтобы он
# мог открыть своё приложение, имя нужно связать с телеграм-аккаунтом, а связать
# может только владелец: одноразовый код, ссылка, первый вход — и привязка
# закрывается. Без этого любой, кто узнает имя, стал бы водителем.

async def get_driver_link(telegram_id: int) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.drivers.find_one({"telegram_id": int(telegram_id)}, {"_id": 0})


async def get_driver_by_name(name: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.drivers.find_one({"name": name}, {"_id": 0})


async def get_driver_by_code(code: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.drivers.find_one({"code": code, "telegram_id": None}, {"_id": 0})


async def get_driver_links() -> list:
    db = _db_or_none()
    if db is None: return []
    return await db.drivers.find({}, {"_id": 0}).to_list(length=200)


async def set_driver_code(name: str, code: str, by: int):
    """Выдать (или перевыпустить) код привязки. Старый код с этого момента мёртв."""
    db = _db_or_none()
    if db is None: return
    await db.drivers.update_one(
        {"name": name},
        {"$set": {"name": name, "code": code, "telegram_id": None,
                  "code_by": by, "code_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def link_driver(code: str, telegram_id: int, tg: dict) -> dict | None:
    """Привязать аккаунт к коду. Возвращает запись водителя или None.

    Условие telegram_id: None в фильтре — защита от гонки: если код уже
    сработал, второй запрос ничего не найдёт и никого не привяжет."""
    db = _db_or_none()
    if db is None: return None
    r = await db.drivers.find_one_and_update(
        {"code": code, "telegram_id": None},
        {"$set": {"telegram_id": int(telegram_id),
                  "tg_name": tg.get("first_name", ""),
                  "tg_username": tg.get("username", ""),
                  "linked_at": datetime.now(timezone.utc)}},
        return_document=True,
    )
    if r:
        r.pop("_id", None)
    return r


async def unlink_driver(name: str) -> bool:
    """Отвязать аккаунт — водитель уволился или потерял телефон."""
    db = _db_or_none()
    if db is None: return False
    r = await db.drivers.update_one(
        {"name": name},
        {"$set": {"telegram_id": None, "code": None, "unlinked_at": datetime.now(timezone.utc)}},
    )
    return bool(r.modified_count)


# ── расходы по водителям ─────────────────────────────────────────────────────
# Один документ на (день, водитель): отметка о выходе, ставка питания и список
# разовых трат. Ключ составной, потому что и то и другое правят в течение дня по
# одному человеку, а не всей сменой разом.

# ── реклама: откуда к нам приходят ──────────────────────────────────────────
async def promo_chat_seen(chat_id: int, title: str):
    """Реестр чатов, где рекламный бот отвечал.

    Нужен ради названий: в ссылке едет id, а в отчёте владелец должен видеть
    «Dubai Expats», а не «1002345678». Заодно считаем ответы — без них выйдет,
    что чат не приводит клиентов, хотя бот там ни разу и не сработал."""
    db = _db_or_none()
    if db is None: return
    now = datetime.now(timezone.utc)
    await db.promo_chats.update_one(
        {"_id": int(chat_id)},
        {"$set": {"title": title, "last": now},
         "$setOnInsert": {"first": now},
         "$inc": {"replies": 1}},
        upsert=True)


# ── Журнал рекламы ──────────────────────────────────────────────────────────
# Счётчик «11 ответов» говорит, что бот сработал, но не говорит ни когда, ни на
# чей вопрос, ни где это сообщение лежит. А спрашивают всегда именно это:
# человек пришёл — откуда именно, из какого поста, из какого разговора.
# Поэтому каждое срабатывание пишется отдельной строкой, со ссылкой на само
# сообщение там, где телеграм её позволяет собрать.
async def promo_log_add(doc: dict):
    db = _db_or_none()
    if db is None: return
    doc.setdefault("at", datetime.now(timezone.utc))
    try:
        await db.promo_log.insert_one(doc)
    except Exception as e:
        log.warning(f"promo_log_add: {e}")


async def promo_log(kind: str = "", limit: int = 300) -> list:
    """Журнал от свежего к старому. kind: 'reply' | 'post' | пусто — всё."""
    db = _db_or_none()
    if db is None: return []
    q = {"kind": kind} if kind else {}
    rows = await db.promo_log.find(q, {"_id": 0}).sort("at", -1).to_list(length=limit)
    for r in rows:
        r["at"] = str(r.get("at") or "")
    return rows


async def spend_by_customer() -> dict:
    """{telegram_id: {n, aed}} по доставленным заказам.

    Считает база, а не сервер: раньше ради этой сводки вычитывались все заказы
    целиком — мегабайт по сети и восемь секунд на тарифе, где скорость режется
    объёмом. Здесь же наружу выходит по три числа на клиента."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.orders.aggregate([
        {"$match": {"status": "delivered", "test": {"$ne": True},
                    "customer_id": {"$nin": [None, 0]}}},
        {"$group": {"_id": "$customer_id", "n": {"$sum": 1},
                    "aed": {"$sum": {"$ifNull": ["$total", 0]}}}},
    ])
    out = {}
    for d in await cur.to_list(length=5000):
        try:
            out[int(d["_id"])] = {"n": int(d["n"]), "aed": int(d["aed"] or 0)}
        except (TypeError, ValueError):
            continue
    return out


async def get_promo_chats() -> list:
    db = _db_or_none()
    if db is None: return []
    return await db.promo_chats.find({}).to_list(length=500)


async def get_users_by_via() -> list:
    """Все, у кого записан канал прихода, — сырьё для отчёта по рекламе."""
    db = _db_or_none()
    if db is None: return []
    cur = db.users.find(
        {"invited_via": {"$exists": True, "$nin": [None, ""]}},
        {"_id": 0, "telegram_id": 1, "invited_via": 1, "invited_at": 1,
         "name": 1, "username": 1, "verified": 1})
    return await cur.to_list(length=20000)


# ── Заявки: что именно мы просили у магазина ────────────────────────────────
# Снимок нужен ради одной вещи — понять при возврате файла, что магазин
# отказал. В файле все позиции каталога, и ноль в строке сам по себе ничего не
# говорит: то ли не давали, то ли и не просили.
async def zayavka_save(day: str, asked: dict, by: dict = None):
    """Снимок заявки. `asked` — итоги по позициям, `by` — те же числа по точкам.

    Разбивка нужна разнице: магазин урезал Absolut с 20 до 12, и вопрос не
    «сколько не хватает», а «в какой район не доедет»."""
    db = _db_or_none()
    if db is None: return
    await db.zayavki.replace_one(
        {"_id": day}, {"_id": day, "asked": asked, "by": by or {},
                       "at": datetime.now(timezone.utc)},
        upsert=True)


async def zayavka_last() -> dict:
    db = _db_or_none()
    if db is None: return {}
    doc = await db.zayavki.find_one(sort=[("at", -1)])
    return (doc or {}).get("asked") or {}


async def zayavka_last_full() -> dict:
    db = _db_or_none()
    if db is None: return {}
    return await db.zayavki.find_one(sort=[("at", -1)]) or {}


# ── Приём заказа: захват, а не запись ───────────────────────────────────────
# Заказ достаётся одному. Два оператора с двух устройств нажимают «Принять»
# одновременно чаще, чем кажется: карточка висит у всех, и когда она наконец
# появляется, тянутся к ней сразу двое. Обычный update здесь молча пропустил бы
# обоих — клиент получил бы два подтверждения с разным временем.
#
# Поэтому статус меняем условием: сработало — заказ твой, вернулось ничего —
# его взяли раньше, и это не ошибка, а нормальный ответ, который надо показать.
async def claim_order(oid: str, fields: dict) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    doc = await db.orders.find_one_and_update(
        {"order_id": oid, "status": "pending"},
        {"$set": fields},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER)
    if doc: _orders_dirty()
    return doc


async def orders_changed_since(iso: str, limit: int = 400) -> list:
    """Заказы, тронутые после указанного момента — для опроса из панели.

    Панель спрашивает «что нового» каждые пару секунд; выкачивать всю смену
    ради одного изменившегося заказа незачем."""
    db = _db_or_none()
    if db is None: return []
    cur = db.orders.find({"updated_at": {"$gt": iso}}, {"_id": 0}) \
                   .sort("updated_at", 1).limit(limit)
    return await cur.to_list(length=limit)


# ── Кто на каком районе ─────────────────────────────────────────────────────
# Расписание живёт в config_staff, но люди меняются местами чаще, чем выходит
# релиз: ушёл в отпуск, взяли нового, поменялись сменами. Перестановка лежит
# здесь и накладывается поверх расписания, поэтому её всегда видно и всегда
# можно снять — в коде остаётся то, как задумано.
async def staff_map_get() -> dict:
    db = _db_or_none()
    if db is None: return {}
    cur = db.staff_map.find({})
    return {d["_id"]: d["operator"] async for d in cur if d.get("operator")}


async def staff_map_set(district: str, operator: str):
    db = _db_or_none()
    if db is None: return
    if operator:
        await db.staff_map.replace_one(
            {"_id": district}, {"_id": district, "operator": operator,
                                "at": datetime.now(timezone.utc)}, upsert=True)
    else:
        await db.staff_map.delete_one({"_id": district})


async def staff_map_clear():
    db = _db_or_none()
    if db is None: return
    await db.staff_map.delete_many({})


async def driver_map_clear():
    db = _db_or_none()
    if db is None: return
    await db.driver_map.delete_many({})


async def driver_map_get() -> dict:
    """Кто из водителей стоит не там, где в расписании: имя → район."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.driver_map.find({})
    return {d["_id"]: d["district"] async for d in cur if d.get("district")}


async def driver_map_set(driver: str, district: str):
    db = _db_or_none()
    if db is None: return
    if district:
        await db.driver_map.replace_one(
            {"_id": driver}, {"_id": driver, "district": district,
                              "at": datetime.now(timezone.utc)}, upsert=True)
    else:
        await db.driver_map.delete_one({"_id": driver})


# ── Картинки, уже загруженные в телеграм ────────────────────────────────────
# Раз отданный телеграму файл получает file_id и живёт у них вечно. Дальше
# карточку можно собирать по этому id, и телеграму не нужно ходить к нам за
# картинкой в момент, когда человек набирает имя бота: именно это хождение и
# показывает пустую карточку — оно медленное и молча срывается.
async def tg_file_get(name: str):
    db = _db_or_none()
    if db is None: return None
    doc = await db.tg_files.find_one({"_id": name})
    return (doc or {}).get("file_id")


async def tg_file_set(name: str, file_id: str):
    db = _db_or_none()
    if db is None: return
    await db.tg_files.replace_one(
        {"_id": name}, {"_id": name, "file_id": file_id,
                        "at": datetime.now(timezone.utc)}, upsert=True)


# ── Ручные правки заявки ────────────────────────────────────────────────────
# Расчёт по норме — предложение, а не приговор: владелец видит полку своими
# глазами и знает, что через два дня свадьба, а по этой позиции магазин тянет.
# Правка живёт отдельно от расчёта, поэтому её всегда можно снять и вернуться
# к тому, что посчитала программа.
async def zayavka_edit_set(day: str, pid: str, district: str, qty):
    db = _db_or_none()
    if db is None: return
    key = f"by.{pid}.{district}"
    if qty is None:
        await db.zayavka_edits.update_one({"_id": day}, {"$unset": {key: ""}}, upsert=True)
    else:
        await db.zayavka_edits.update_one(
            {"_id": day}, {"$set": {key: int(qty), "at": datetime.now(timezone.utc)}},
            upsert=True)


async def zayavka_edit_clear(day: str, pid: str = None):
    db = _db_or_none()
    if db is None: return
    if pid:
        await db.zayavka_edits.update_one({"_id": day}, {"$unset": {f"by.{pid}": ""}})
    else:
        await db.zayavka_edits.delete_one({"_id": day})


async def zayavka_edits(day: str) -> dict:
    db = _db_or_none()
    if db is None: return {}
    doc = await db.zayavka_edits.find_one({"_id": day})
    return (doc or {}).get("by") or {}


# ── Поставки: заявка ушла в магазин, вернулась и стала списком на забор ─────
async def supply_save(doc: dict):
    db = _db_or_none()
    if db is None: return
    await db.supplies.replace_one({"_id": doc["_id"]}, doc, upsert=True)


async def supply_get(sid: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.supplies.find_one({"_id": sid})


async def supply_list(limit: int = 30, status: str = None) -> list:
    db = _db_or_none()
    if db is None: return []
    q = {"status": status} if status else {}
    cur = db.supplies.find(q).sort("at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    for r in rows:
        r["supply_id"] = r.pop("_id")
        r["at"] = str(r.get("at") or "")
    return rows


async def supply_buy_set(sid: str, product_id: str, doc: dict | None) -> bool:
    """Записать закупку позиции на доп. складе. doc=None — стереть запись.

    Живёт в самой заявке, а не отдельной книгой: докупали именно по этому
    недобору, и в отрыве от него цена ни о чём не говорит."""
    db = _db_or_none()
    if db is None or not sid or not product_id: return False
    upd = ({"$unset": {f"buys.{product_id}": ""}} if doc is None
           else {"$set": {f"buys.{product_id}": doc}})
    r = await db.supplies.update_one({"_id": sid}, upd)
    return bool(r.matched_count)


async def supply_bump(sid: str, product_id: str, by: int) -> dict | None:
    """Отметить, что одна бутылка позиции забрана.

    Считаем прямо в документе поставки: у водителя на экране должно быть
    «7 из 12», а собирать это из реестра кодов на каждый скан — лишний проход
    по всей коллекции ради одного числа."""
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    return await db.supplies.find_one_and_update(
        {"_id": sid, "items.id": product_id},
        {"$inc": {"items.$.scanned": 1}, "$set": {"last_by": by}},
        return_document=ReturnDocument.AFTER)


# ── Приёмка: задача района и принятые по ней бутылки ────────────────────────
# Поставка приходит одна на все районы, а забирают её по частям: у каждого
# района свой водитель и своя машина. Поэтому единица работы — не поставка, а
# пара «поставка × район»: её берут, по ней сканируют и её закрывают.
#
# Захват атомарный по той же причине, что и заказ: карточка висит у всех
# водителей района, и тянутся к ней сразу двое. Обычный update молча пропустил
# бы обоих, и в магазин поехали бы две машины за одним товаром.
async def supply_task_claim(sid: str, district: str, driver: str,
                            driver_id: int, now) -> tuple:
    db = _db_or_none()
    if db is None: return False, None
    from pymongo import ReturnDocument
    doc = await db.supplies.find_one_and_update(
        {"_id": sid, "status": "open",
         f"tasks.{district}.driver": "", f"tasks.{district}.done_at": None},
        {"$set": {f"tasks.{district}.driver": driver,
                  f"tasks.{district}.driver_id": driver_id,
                  f"tasks.{district}.claimed_at": now}},
        return_document=ReturnDocument.AFTER)
    if doc:
        return True, (doc.get("tasks") or {}).get(district)
    cur = await db.supplies.find_one({"_id": sid}, {"tasks": 1})
    return False, ((cur or {}).get("tasks") or {}).get(district)


async def supply_task_release(sid: str, district: str, driver: str = None) -> bool:
    """Отпустить задачу. driver задан — отпускает сам водитель и только свою."""
    db = _db_or_none()
    if db is None: return False
    q = {"_id": sid, f"tasks.{district}.done_at": None}
    if driver is not None:
        q[f"tasks.{district}.driver"] = driver
    r = await db.supplies.update_one(q, {"$set": {
        f"tasks.{district}.driver": "", f"tasks.{district}.driver_id": 0,
        f"tasks.{district}.claimed_at": None}})
    return r.modified_count > 0


async def supply_take(sid: str, district: str, product_id: str, limit: int, now):
    """Принять одну бутылку по строке задачи. None — строка уже добрана.

    Условие «принято меньше подтверждённого» стоит в самом запросе, а не в
    коде над ним: между «посмотрел» и «прибавил» помещается ещё один скан, и
    тогда по строке из 23 бутылок приедет 24-я. Лишняя бутылка в остатке
    страшнее отказа: её потом не отличить от настоящей."""
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    return await db.supplies.find_one_and_update(
        {"_id": sid, "status": "open",
         "items": {"$elemMatch": {"id": product_id, f"got.{district}": {"$lt": limit}}}},
        {"$inc": {f"items.$.got.{district}": 1, "items.$.scanned": 1,
                  f"tasks.{district}.scanned": 1},
         "$set": {f"tasks.{district}.last_at": now}},
        return_document=ReturnDocument.AFTER)


async def supply_untake(sid: str, district: str, product_id: str) -> bool:
    """Снять одну бутылку — водитель отменил последний скан."""
    db = _db_or_none()
    if db is None: return False
    r = await db.supplies.update_one(
        {"_id": sid, "items": {"$elemMatch": {"id": product_id,
                                              f"got.{district}": {"$gt": 0}}}},
        {"$inc": {f"items.$.got.{district}": -1, "items.$.scanned": -1,
                  f"tasks.{district}.scanned": -1, f"tasks.{district}.undo": 1}})
    return r.modified_count > 0


async def supply_task_start(sid: str, district: str, now) -> None:
    """Отметить начало приёмки — только первый раз.

    Время начала и время конца дают окно, в которое всё происходило. По нему
    видно и честную разгрузку на сорок минут, и «приём» за полторы минуты."""
    db = _db_or_none()
    if db is None: return
    await db.supplies.update_one(
        {"_id": sid, f"tasks.{district}.started_at": None},
        {"$set": {f"tasks.{district}.started_at": now}})


async def supply_task_finish(sid: str, district: str, gaps: list,
                             note: str, now) -> dict | None:
    """Закрыть задачу района. Возвращает поставку целиком."""
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    doc = await db.supplies.find_one_and_update(
        {"_id": sid, f"tasks.{district}.done_at": None},
        {"$set": {f"tasks.{district}.done_at": now,
                  f"tasks.{district}.gaps": gaps,
                  f"tasks.{district}.note": note}},
        return_document=ReturnDocument.AFTER)
    if not doc:
        return None
    # Поставка закрыта, когда закрыт последний район: пока хоть один в работе,
    # она остаётся открытой и видна водителям.
    if all((t or {}).get("done_at") for t in (doc.get("tasks") or {}).values()):
        await db.supplies.update_one({"_id": sid},
                                     {"$set": {"status": "done", "done_at": now}})
        doc["status"] = "done"
    return doc


async def supply_task_flag(sid: str, district: str, flag: dict) -> None:
    """Пометка о странном на приёмке. Не блокирует — показывает старшему."""
    db = _db_or_none()
    if db is None: return
    await db.supplies.update_one({"_id": sid},
                                 {"$push": {f"tasks.{district}.flags": flag}})


async def supplies_with_open_tasks(limit: int = 10) -> list:
    """Поставки, в которых есть незакрытые задачи — то, что видит водитель."""
    db = _db_or_none()
    if db is None: return []
    cur = db.supplies.find({"status": "open"}).sort("at", -1).limit(limit)
    return await cur.to_list(length=limit)


async def intake_since(district: str, since) -> dict:
    """Сколько бутылок принято на район после указанного момента: позиция → шт.

    Нужно заявке: пересчёт был вчера, ночью пришла поставка, и без этого
    программа завтра закажет то, что уже стоит на полке."""
    db = _db_or_none()
    if db is None or not since: return {}
    cur = db.qr_codes.aggregate([
        {"$match": {"district": district, "src": "intake", "at": {"$gt": since}}},
        {"$group": {"_id": "$product_id", "n": {"$sum": 1}}},
    ])
    return {d["_id"]: d["n"] for d in await cur.to_list(length=500) if d["_id"]}


# ── Списания: бой, брак, просрочка, потеря ─────────────────────────────────
# Списание — это признание, что товар исчез не через кассу. Пока такого места
# не было, исчезнувшее превращалось в «пересчёт наврал», и заявка каждый раз
# заказывала то, чего уже нет. Поэтому у списания три обязательных свойства:
# кто, что именно и фотография. Без фотографии запись не принимается — не из
# недоверия, а потому что иначе списание становится способом закрыть недостачу.
WRITEOFF_KINDS = ("бой", "брак", "просрочка", "потеря")


async def writeoff_add(doc: dict, photo: bytes = b"") -> str:
    """Записать списание. Фото кладём отдельно: список читают часто, а
    картинки в тех же документах тянули бы по мегабайту на каждое открытие."""
    db = _db_or_none()
    if db is None: return ""
    import uuid
    wid = uuid.uuid4().hex[:12]
    # Списание не вычитается со склада, пока владелец его не согласовал: до
    # решения это заявление водителя, а не факт. Записи, сделанные до того,
    # как согласование появилось, поля не имеют вовсе — они были учтены сразу,
    # и отменять их задним числом нечестно.
    doc = {**doc, "_id": wid, "state": doc.get("state") or "pending"}
    await db.writeoffs.insert_one(doc)
    if photo:
        from bson.binary import Binary
        await db.writeoff_photos.insert_one(
            {"_id": wid, "img": Binary(photo), "at": doc.get("at")})
    return wid


# Что считается вычтенным со склада: согласованное и всё старое, у которого
# поля state нет вовсе. Отклонённое и ждущее решения на остаток не влияют —
# бутылка либо ещё стоит на полке, либо это недостача, а не бой.
WRITEOFF_COUNTED = {"state": {"$nin": ["pending", "no"]}}


# «Списывать нечего» — тоже запись, а не пустой экран.
#
# Пустой список отвечает сразу на два вопроса и ни на один честно: то ли за
# день ничего не разбили, то ли забыли записать. Отметка отвечает на первый,
# и у неё есть автор и время: сказать «ничего не было» — это утверждение,
# и оно должно быть чьим-то.
async def writeoff_none_set(day: str, by: int, by_name: str) -> None:
    db = _db_or_none()
    if db is None or not day: return
    await db.writeoff_none.update_one(
        {"_id": day},
        {"$set": {"by": int(by or 0), "by_name": str(by_name or "")[:60],
                  "at": datetime.now(timezone.utc).isoformat()}}, upsert=True)


async def writeoff_none_clear(day: str) -> None:
    db = _db_or_none()
    if db is None or not day: return
    await db.writeoff_none.delete_one({"_id": day})


async def writeoff_none_get(day: str) -> dict | None:
    db = _db_or_none()
    if db is None or not day: return None
    d = await db.writeoff_none.find_one({"_id": day})
    if d: d["day"] = d.pop("_id")
    return d


async def writeoff_del(wid: str) -> bool:
    """Убрать запись списания вместе с её снимком.

    Нужна ровно в одном месте: когда бутылку успели списать между чтением кода
    и записью. Оставлять такую запись нельзя — она вычтет со склада вторую
    такую же бутылку, которой нет."""
    db = _db_or_none()
    if db is None or not wid: return False
    await db.writeoff_photos.delete_one({"_id": wid})
    r = await db.writeoffs.delete_one({"_id": wid})
    return r.deleted_count > 0


async def writeoff_get(wid: str) -> dict | None:
    db = _db_or_none()
    if db is None or not wid: return None
    return await db.writeoffs.find_one({"_id": wid}, {"img": 0})


async def writeoff_decide(wid: str, ok: bool, by: int, by_name: str = "",
                          note: str = "") -> dict | None:
    """Согласовать списание или отклонить — ровно один раз.

    Решение принимают в двух местах сразу: кнопкой в мини-аппе и кнопкой в
    боте под фотографией. Плюс телеграм переспрашивает нажатие при плохой
    связи. Поэтому переход разрешён только из «ждёт»: второе нажатие ничего не
    меняет и возвращает пусто, а не переписывает уже принятое решение."""
    db = _db_or_none()
    if db is None or not wid: return None
    from pymongo import ReturnDocument
    return await db.writeoffs.find_one_and_update(
        {"_id": wid, "state": "pending"},
        {"$set": {"state": "ok" if ok else "no",
                  "decided_at": datetime.now(timezone.utc),
                  "decided_by": int(by or 0),
                  "decided_by_name": str(by_name or "")[:60],
                  "decided_note": str(note or "")[:200]}},
        projection={"img": 0}, return_document=ReturnDocument.AFTER)


async def writeoff_compensate(wid: str, who: str, amount: int, note: str,
                              by: int, by_name: str = "") -> dict | None:
    """Назначить удержание по списанию — или снять его.

    Удержание живёт на самом списании, а не отдельной записью: иначе одно и то
    же событие пришлось бы держать в двух местах и следить, чтобы они не
    разошлись. Отсюда же берётся и сумма удержаний по человеку — складыванием.

    Удерживать можно только по согласованному: у ждущего решения ещё нет факта,
    а отклонённое и так остаётся недостачей на том, у кого пропало — списывать
    с него второй раз значит взять дважды за одно.

    Пустой человек или ноль — это снятие удержания: передумать владелец имеет
    право, и отдельная кнопка для этого не нужна."""
    db = _db_or_none()
    if db is None or not wid: return None
    from pymongo import ReturnDocument
    amount = max(0, int(amount or 0))
    who = str(who or "").strip()[:60]
    if not who or not amount:
        upd = {"$unset": {"comp": ""}}
    else:
        upd = {"$set": {"comp": {
            "who": who, "amount": amount, "note": str(note or "")[:200],
            "at": datetime.now(timezone.utc),
            "by": int(by or 0), "by_name": str(by_name or "")[:60]}}}
    return await db.writeoffs.find_one_and_update(
        {"_id": wid, "state": "ok"}, upd,
        projection={"img": 0}, return_document=ReturnDocument.AFTER)


async def writeoff_pending(limit: int = 100) -> list:
    """Списания, ждущие решения — старые сверху: первым разбирают то, что
    висит дольше всех."""
    db = _db_or_none()
    if db is None: return []
    cur = db.writeoffs.find({"state": "pending"}, {"img": 0}).sort("at", 1).limit(int(limit))
    return await cur.to_list(length=int(limit))


async def writeoff_note_msg(wid: str, sent: list) -> None:
    """Запомнить сообщения в чатах владельцев — чтобы после решения убрать у
    них кнопки. Иначе второй владелец жмёт по уже решённому и получает отказ."""
    db = _db_or_none()
    if db is None or not wid or not sent: return
    await db.writeoffs.update_one({"_id": wid}, {"$set": {"msgs": sent}})


async def writeoff_photo(wid: str) -> bytes:
    db = _db_or_none()
    if db is None or not wid: return b""
    d = await db.writeoff_photos.find_one({"_id": wid}, {"_id": 0, "img": 1})
    return bytes((d or {}).get("img") or b"")


async def writeoff_list(since=None, district: str = "", by: str = "",
                        state: str = "", day: str = "", limit: int = 300) -> list:
    """История списаний, новые сверху. Без фотографий — их берут по одной."""
    db = _db_or_none()
    if db is None: return []
    q = {}
    if day:     q["day"] = day
    if since:   q["at"] = {"$gte": since}
    if district: q["district"] = district
    if by:       q["by"] = by
    if state:    q["state"] = state
    cur = db.writeoffs.find(q, {"img": 0}).sort("at", -1).limit(int(limit))
    return await cur.to_list(length=int(limit))


async def writeoff_since(since: dict, skip_coded: bool = False) -> dict:
    """Сколько бутылок списано после пересчёта: {район: {позиция: шт}}.

    Тому же расчёту, что учитывает приход и продажи: разбитая бутылка ушла со
    склада так же честно, как проданная, и заявка должна знать об этом раньше,
    чем следующий пересчёт."""
    db = _db_or_none()
    out = {}
    if db is None or not since: return out
    for district, dt in (since or {}).items():
        if not dt: continue
        cur = db.writeoffs.aggregate([
            {"$match": {"district": district, "at": {"$gt": dt}, **WRITEOFF_COUNTED,
                        # Списанное сканом уже вышло из реестра: у той бутылки
                        # статус сменился, и она не считается активной. Тому,
                        # кто считает ОТ РЕЕСТРА, вычитать её второй раз нельзя;
                        # тому, кто считает от ручного пересчёта, — обязательно.
                        **({"code": {"$in": [None, ""]}} if skip_coded else {})}},
            {"$group": {"_id": "$item", "n": {"$sum": "$qty"}}},
        ])
        got = {d["_id"]: int(d["n"] or 0) for d in await cur.to_list(length=500) if d["_id"]}
        if got: out[district] = got
    return out


async def sold_since(since_iso: str) -> list:
    """Доставленные заказы с указанного момента — для отката остатка вперёд.

    Пересчёт склада — это снимок на момент времени. Пока его не повторили,
    честный остаток = снимок + приход − продажи. Без последнего слагаемого
    заявка считает, что всё проданное с того дня по-прежнему стоит на полке."""
    db = _db_or_none()
    if db is None: return []
    cur = db.orders.find({"timestamp": {"$gte": since_iso}, "status": "delivered"},
                         {"_id": 0, "timestamp": 1, "office_id": 1, "items": 1})
    return await cur.to_list(length=None)


# ── Сообщения боту водителя ─────────────────────────────────────────────────
# Что мы отправили водителю в телеграм, помним по номерам сообщений: иначе в
# скрытом режиме их нечем убрать. Только водительский бот и только его чат —
# ничего из переписки старших и операторов сюда не попадает и попасть не может.
async def drv_msg_add(chat_id: int, mid: int, at):
    """Запомнить отправленное. Держим двое суток: дольше телеграм удалять не
    даёт, и хранить номера, которыми уже нельзя воспользоваться, незачем."""
    db = _db_or_none()
    if db is None or not (chat_id and mid): return
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    edge = (at if hasattr(at, "isoformat") else _dt.now(_tz.utc)) - _td(hours=48)
    try:
        await db.driver_msgs.update_one(
            {"_id": int(chat_id)},
            {"$push": {"msgs": {"$each": [{"m": int(mid), "at": at}], "$slice": -300}}},
            upsert=True)
        await db.driver_msgs.update_one(
            {"_id": int(chat_id)}, {"$pull": {"msgs": {"at": {"$lt": edge}}}})
    except Exception as e:
        log.debug(f"drv_msg_add {chat_id}/{mid}: {e}")


async def drv_msgs_take(chat_id: int) -> list:
    """Забрать номера и сразу забыть их: удаление — дело одноразовое, а
    повторная попытка по тем же номерам только шумит в логах."""
    db = _db_or_none()
    if db is None or not chat_id: return []
    from pymongo import ReturnDocument
    doc = await db.driver_msgs.find_one_and_update(
        {"_id": int(chat_id)}, {"$set": {"msgs": []}},
        return_document=ReturnDocument.BEFORE)
    return [int(m["m"]) for m in ((doc or {}).get("msgs") or []) if m.get("m")]


# ── Где водители ────────────────────────────────────────────────────────────
# Координаты приходят живой трансляцией телеграма: водитель сам включает её на
# смену и сам видит её у себя в чате с таймером и кнопкой «остановить».
#
# Что храним и почему именно так. Точка «сейчас» — одна на водителя, с
# перезаписью: диспетчеру нужна не история, а текущее положение. Трек — только
# за идущую смену, и он стирается, когда район закрывают. Сверху страховка
# сроком жизни: если смену забыли закрыть, документ уйдёт сам через полтора
# суток после последнего обновления.
#
# Чего здесь нет: вечного архива передвижений. Он не нужен для работы, а
# украсть его можно только тогда, когда он есть.
TRACK_MAX = 2000              # точек на смену: восемь часов раз в пятнадцать секунд

async def driver_pos_set(name: str, day: str, lat: float, lon: float, at,
                         until=None, acc=None, stop_live: bool = False) -> None:
    """Точка водителя. until — до какого времени идёт трансляция.

    stop_live гасит признак трансляции. Раньше его не было, потому что срок
    кончался сам: восемь часов истекали, и «идёт ли трансляция» отвечало время.
    С бессрочной трансляцией отвечать стало нечему — телеграм присылает срок на
    шестьдесят восемь лет вперёд, и выключенная трансляция навсегда осталась бы
    для нас включённой. Единственный честный признак её конца — сообщение от
    самого телеграма, что она кончилась."""
    db = _db_or_none()
    if db is None or not name: return
    pt = {"lat": round(float(lat), 6), "lon": round(float(lon), 6), "at": at}
    doc = {"$set": {"lat": pt["lat"], "lon": pt["lon"], "at": at, "day": day},
           "$push": {"track": {"$each": [pt], "$slice": -TRACK_MAX}}}
    if stop_live:
        doc["$unset"] = {"until": ""}
    elif until is not None:
        doc["$set"]["until"] = until
    if acc is not None:
        doc["$set"]["acc"] = round(float(acc), 1)
    await db.driver_pos.update_one({"_id": name}, doc, upsert=True)


async def driver_pos_all(names: list = None) -> list:
    """Последние точки. Трек не отдаём: он тяжёлый и нужен по одному водителю."""
    db = _db_or_none()
    if db is None: return []
    q = {"_id": {"$in": list(names)}} if names else {}
    rows = await db.driver_pos.find(q, {"track": 0}).to_list(length=100)
    for r in rows:
        r["driver"] = r.pop("_id")
        r["at"] = str(r.get("at") or "")
        r["until"] = str(r.get("until") or "")
    return rows


async def driver_track(name: str, day: str = "") -> list:
    db = _db_or_none()
    if db is None or not name: return []
    d = await db.driver_pos.find_one({"_id": name}, {"track": 1, "day": 1})
    if not d or (day and d.get("day") != day):
        return []
    return [{"lat": p["lat"], "lon": p["lon"], "at": str(p.get("at") or "")}
            for p in (d.get("track") or [])]


async def driver_track_clear(names: list) -> int:
    """Стереть треки: смену закрыли, маршрут больше никому не нужен.

    Точка «сейчас» остаётся — по ней видно, что водитель ещё в сети, — а вот
    маршрут за смену уходит совсем."""
    db = _db_or_none()
    if db is None or not names: return 0
    res = await db.driver_pos.update_many({"_id": {"$in": list(names)}},
                                          {"$set": {"track": []}})
    return res.modified_count


# ── Смена: день закрыт, продажи посчитаны ───────────────────────────────────
# Смена закрывается по району: сутки считает не программа по часам, а человек,
# который знает, что заказов больше не будет. Пока район не закрыт, продажи
# этого дня не окончательны и заявку по ним собирать нельзя.
async def shift_close(day: str, district: str, doc: dict) -> bool:
    """Закрыть смену района. False — её уже закрывали."""
    db = _db_or_none()
    if db is None: return False
    from pymongo.errors import DuplicateKeyError
    try:
        await db.shift_days.insert_one({"_id": f"{day}:{district}", "day": day,
                                        "district": district, **doc})
        return True
    except DuplicateKeyError:
        return False


async def shift_open(day: str, district: str, doc: dict) -> bool:
    """Открыть смену района. False — её уже открывали сегодня.

    Отдельным документом, а не флагом в закрытии: открытие и закрытие — два
    разных события с разным временем и разными людьми, и в истории они должны
    стоять порознь."""
    db = _db_or_none()
    if db is None: return False
    from pymongo.errors import DuplicateKeyError
    try:
        await db.shift_opens.insert_one({"_id": f"{day}:{district}", "day": day,
                                         "district": district, **doc})
        return True
    except DuplicateKeyError:
        return False


async def shift_gate_since(day: str) -> str:
    """С каких суток запрет «работать только после открытия смены» действует.

    Записывается один раз — в первый же проход после появления запрета. День
    выката не считается: люди уже работают, и запирать их посреди смены за то,
    что утром такой кнопки не существовало, нельзя."""
    db = _db_or_none()
    if db is None: return ""
    doc = await db.shift_opens.find_one({"_id": "*:gate"})
    if doc:
        return doc.get("since") or ""
    await db.shift_opens.update_one(
        {"_id": "*:gate"}, {"$setOnInsert": {"since": day, "day": day, "district": "*"}},
        upsert=True)
    return day


async def driver_gate_since(day: str) -> str:
    """С каких суток водителю нельзя работать без открытой смены.

    Тот же приём, что и у операторов, и по той же причине: водитель, который
    сейчас на адресе, не должен упереться в новый экран из-за того, что мы
    выкатили обновление посреди его смены."""
    db = _db_or_none()
    if db is None: return ""
    doc = await db.shift_opens.find_one({"_id": "*:dgate"})
    if doc:
        return doc.get("since") or ""
    await db.shift_opens.update_one(
        {"_id": "*:dgate"}, {"$setOnInsert": {"since": day, "day": day, "district": "*"}},
        upsert=True)
    return day


async def shift_opens_for_day(day: str) -> dict:
    db = _db_or_none()
    if db is None: return {}
    cur = db.shift_opens.find({"day": day, "district": {"$ne": "*"}})
    return {d["district"]: d async for d in cur}


async def shift_open_drop(day: str, district: str) -> bool:
    """Убрать отметку об открытии — на случай ошибки старшего."""
    db = _db_or_none()
    if db is None: return False
    r = await db.shift_opens.delete_one({"_id": f"{day}:{district}"})
    return r.deleted_count > 0


async def shift_journal(day_from: str, day_to: str) -> list:
    """История открытий и закрытий за период, одним списком.

    Открытие и закрытие лежат в разных коллекциях, потому что это разные
    события, но читают их вместе: вопрос всегда один — что было со сменой."""
    db = _db_or_none()
    if db is None: return []
    # Служебные пометки («с какого дня напоминаем», «с какого дня запрет»)
    # лежат в тех же коллекциях под районом «*». Это не события смены.
    q = {"day": {"$gte": day_from, "$lte": day_to}, "district": {"$ne": "*"}}
    out = []
    for d in await db.shift_opens.find(q).to_list(length=1000):
        out.append({**d, "kind": "open", "at": d.get("opened_at")})
    for d in await db.shift_days.find(q).to_list(length=1000):
        out.append({**d, "kind": "close", "at": d.get("closed_at")})
    out.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return out


async def shift_reopen(day: str, district: str) -> bool:
    db = _db_or_none()
    if db is None: return False
    r = await db.shift_days.delete_one({"_id": f"{day}:{district}"})
    return r.deleted_count > 0


async def shifts_for_day(day: str) -> dict:
    db = _db_or_none()
    if db is None: return {}
    cur = db.shift_days.find({"day": day})
    return {d["district"]: d async for d in cur}


async def shift_nag_since(day: str) -> str:
    """С каких суток напоминания вообще работают.

    Записывается один раз — в первый же проход после появления функции. Всё,
    что раньше, не напоминаем: закрыть те смены было нечем."""
    db = _db_or_none()
    if db is None: return ""
    doc = await db.shift_days.find_one({"_id": "*:nag"})
    if doc:
        return doc.get("since") or ""
    await db.shift_days.update_one(
        {"_id": "*:nag"}, {"$setOnInsert": {"since": day, "day": day, "district": "*"}},
        upsert=True)
    return day


async def shift_day_mark(day: str, field: str) -> bool:
    """Пометить день: «заявку по нему уже собрали». True — пометили мы.

    Отдельным документом на день, а не флагом на последнем районе: закрыться
    последним может любой район, а собрать заявку нужно ровно один раз. Кто
    первым поставил пометку, тот и собирает — остальным вернётся False, и они
    ничего не сделают."""
    db = _db_or_none()
    if db is None: return False
    from pymongo.errors import DuplicateKeyError
    try:
        r = await db.shift_days.update_one(
            {"_id": f"{day}:*", field: {"$exists": False}},
            {"$set": {field: datetime.now(timezone.utc), "day": day, "district": "*"}},
            upsert=True)
        return bool(r.modified_count or r.upserted_id)
    except DuplicateKeyError:
        # Документ дня уже есть и пометка в нём стоит — собрал кто-то другой.
        return False


# ── AMBAR STOCK: реестр бутылок по их кодам ─────────────────────────────────
async def qr_next_seq(product_id: str) -> int:
    """Номер следующей бутылки этой позиции.

    Счётчик отдельным документом, а не «сколько сейчас в реестре»: после
    удаления бутылки номер не должен переиспользоваться, иначе два разных
    физических экземпляра однажды получат одну метку."""
    db = _db_or_none()
    if db is None: return 0
    from pymongo import ReturnDocument
    d = await db.counters.find_one_and_update(
        {"_id": f"qr:{product_id}"}, {"$inc": {"n": 1}},
        upsert=True, return_document=ReturnDocument.AFTER)
    return int((d or {}).get("n") or 1)


async def qr_add(code: str, product_id: str, product_name: str, district,
                 by: int, at, label: str = "", extra: dict = None) -> bool:
    """Записать бутылку. True — записали, False — этот код уже есть.

    Код лежит в _id, поэтому вторая запись с тем же номером невозможна на
    уровне базы, а не «проверяется кодом»: между проверкой и вставкой два
    человека успевают отсканировать одну полку."""
    db = _db_or_none()
    if db is None: return False
    from pymongo.errors import DuplicateKeyError
    try:
        await db.qr_codes.insert_one({
            "_id": code, "status": "active", "product_id": product_id,
            "product_name": product_name, "district": district,
            "label": label, "by": by, "at": at, **(extra or {})})
        return True
    except DuplicateKeyError:
        # Убранная из реестра бутылка вернулась на полку: запись цела, но
        # реестр её не считает. Заводим заново поверх старой — иначе камера
        # отвечает «уже есть» на бутылку, которой в остатке нет.
        r = await db.qr_codes.update_one(
            {"_id": code, "status": "deleted"},
            {"$set": {"status": "active", "product_id": product_id,
                      "product_name": product_name, "district": district,
                      "label": label, "by": by, "at": at, **(extra or {})},
             "$unset": {"was": "", "del_at": "", "del_by": ""}})
        return bool(r.modified_count)


async def qr_write_off(code: str, wid: str) -> bool:
    """Пометить конкретную бутылку списанной. False — её уже списали или продали.

    Условие по статусу стоит в самом запросе, а не проверкой перед ним: два
    человека могут поднести к камере одну и ту же разбитую бутылку, и вторая
    попытка должна не пройти, а не списать её дважды."""
    db = _db_or_none()
    if db is None: return False
    r = await db.qr_codes.update_one(
        {"_id": code, "status": "active"},
        {"$set": {"status": "written", "writeoff": wid,
                  "written_at": datetime.now(timezone.utc).isoformat()}})
    return r.matched_count > 0


async def qr_write_off_undo(code: str) -> bool:
    """Вернуть бутылку в остаток — когда списание по коду отменяют."""
    db = _db_or_none()
    if db is None: return False
    r = await db.qr_codes.update_one(
        {"_id": code, "status": "written"},
        {"$set": {"status": "active"}, "$unset": {"writeoff": "", "written_at": ""}})
    return r.matched_count > 0


async def qr_marked_since(since, districts: list = None) -> dict:
    """Сколько бутылок внесли в базу руками после указанного момента.

    Руками — то есть не приёмкой: приход от магазина считается отдельно и уже
    попал в «приняли». Здесь остаётся ровно то, что довозили с доп. складов и
    заводили через «Внести товар»."""
    db = _db_or_none()
    if db is None or not since: return {}
    q = {"at": {"$gt": since}, "src": {"$ne": "intake"}}
    if districts:
        q["district"] = {"$in": list(districts)}
    cur = db.qr_codes.aggregate([{"$match": q},
                                 {"$group": {"_id": "$district", "n": {"$sum": 1}}}])
    return {d["_id"]: int(d["n"] or 0) for d in await cur.to_list(length=50) if d["_id"]}


async def qr_remove(code: str) -> bool:
    db = _db_or_none()
    if db is None: return False
    r = await db.qr_codes.delete_one({"_id": code})
    return r.deleted_count > 0


async def qr_drop(code: str, by: int, at) -> dict | None:
    """Убрать бутылку из реестра, не стирая её историю.

    Стереть запись насовсем нельзя: тогда исчезает и то, что с этой бутылкой
    было, — где стояла, кто её вносил, списывали ли её. Поэтому «удалить» —
    это статус: в остатке её больше нет, а история цела и удаление отменяемо.
    Прежний статус запоминаем в `was`, иначе возврат сделает активной бутылку,
    которая была списана.

    Возвращает запись ДО удаления или None, если такой в реестре нет."""
    db = _db_or_none()
    if db is None: return None
    from pymongo import ReturnDocument
    d = await db.qr_codes.find_one_and_update(
        {"_id": code, "status": {"$ne": "deleted"}},
        [{"$set": {"was": {"$ifNull": ["$was", "$status"]}, "status": "deleted",
                   "del_at": at, "del_by": by}}],
        return_document=ReturnDocument.BEFORE)
    if d:
        d["code"] = d.pop("_id")
    return d


async def qr_drop_undo(code: str) -> bool:
    """Вернуть бутылку в реестр — тем статусом, с каким её убирали."""
    db = _db_or_none()
    if db is None: return False
    r = await db.qr_codes.update_one(
        {"_id": code, "status": "deleted"},
        [{"$set": {"status": {"$ifNull": ["$was", "active"]}}},
         {"$unset": ["was", "del_at", "del_by"]}])
    return bool(r.modified_count)


async def qr_seen_in_checks(code: str, limit: int = 20) -> list:
    """Где эта бутылка попадалась в проверках у водителей."""
    db = _db_or_none()
    if db is None: return []
    out = []
    cur = db.qr_checks.find({"items.code": code}, {"_id": 0, "at": 1, "driver": 1,
                                                  "district": 1, "items": 1}
                            ).sort("at", -1).limit(limit)
    async for d in cur:
        it = next((x for x in (d.get("items") or []) if x.get("code") == code), {})
        out.append({"at": d.get("at"), "driver": d.get("driver") or "",
                    "district": d.get("district") or "",
                    "verdict": it.get("verdict") or ""})
    return out


async def qr_seen_in_audits(code: str, limit: int = 20) -> list:
    """Где эта бутылка попадалась в ревизии проходом."""
    db = _db_or_none()
    if db is None: return []
    cur = db.audit_scans.find({"code": code}, {"_id": 0, "day": 1, "district": 1,
                                               "at": 1}).sort("day", -1).limit(limit)
    return await cur.to_list(length=limit)


async def qr_stats() -> dict:
    db = _db_or_none()
    if db is None: return {}
    cur = db.qr_codes.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])
    return {d["_id"]: d["n"] for d in await cur.to_list(length=20)}


async def qr_by_product() -> list:
    """Сколько бутылок записано по каждой позиции — это и есть остаток в штуках."""
    db = _db_or_none()
    if db is None: return []
    cur = db.qr_codes.aggregate([
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$product_id",
                    "name": {"$first": "$product_name"},
                    "n": {"$sum": 1},
                    "last": {"$max": "$at"}}},
        {"$sort": {"n": -1}},
    ])
    rows = await cur.to_list(length=500)
    return [{"product_id": r["_id"], "name": r.get("name") or r["_id"],
             "count": r["n"], "last": str(r.get("last") or "")} for r in rows]


async def qr_since_by_district() -> dict:
    """С какого момента на каждой точке ведётся реестр — первый её код."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.qr_codes.aggregate([{"$group": {"_id": "$district", "at": {"$min": "$at"}}}])
    return {d["_id"]: d["at"] for d in await cur.to_list(length=50) if d["_id"] and d["at"]}


async def qr_consumed(since: dict) -> dict:
    """Сколько с тех пор продано и списано на каждой точке.

    Реестр знает, что бутылку внесли, и не знает, что её увезли: на доставке
    коды никто не сканирует. Поэтому «продано» считаем не по кодам, а по
    доставленным заказам — тем же способом, что и остаток на складе. Иначе в
    реестре вечно стоит число, которое было верным один день."""
    db = _db_or_none()
    out = {k: {"sold": 0, "written": 0} for k in (since or {})}
    if db is None or not since:
        return out
    lo = min(since.values())
    lo_iso = lo.isoformat() if hasattr(lo, "isoformat") else str(lo)
    cur = db.orders.find({"timestamp": {"$gte": lo_iso}, "status": "delivered"},
                         {"_id": 0, "timestamp": 1, "office_id": 1, "items": 1})
    for o in await cur.to_list(length=None):
        oid = o.get("office_id") or ""
        if oid not in out:
            continue
        ts = str(o.get("timestamp") or "")
        s = since[oid]
        s_iso = s.isoformat() if hasattr(s, "isoformat") else str(s)
        if ts < s_iso:
            continue
        out[oid]["sold"] += sum(int(i.get("qty") or 0) for i in (o.get("items") or []))
    for district, dt in since.items():
        cur = db.writeoffs.aggregate([
            # Списанное сканом здесь не считаем: та бутылка уже вышла из
            # реестра сменой статуса, и второй раз её вычитать нельзя.
            {"$match": {"district": district, "at": {"$gte": dt}, **WRITEOFF_COUNTED,
                        "code": {"$in": [None, ""]}}},
            {"$group": {"_id": None, "n": {"$sum": "$qty"}}}])
        rows = await cur.to_list(length=1)
        out[district]["written"] = int((rows[0]["n"] if rows else 0) or 0)
    return out


# ── ревизия сканированием ───────────────────────────────────────────────────
# Проход по полке камерой: каждая бутылка отмечается своим кодом. Отсюда
# берётся факт — сколько штук позиции на точке лежит на самом деле, — а всё,
# чего камера не увидела, остаётся недостачей.
#
# Ключ записи — район, день и сам код. Двойной скан одной бутылки физически не
# может дать двойку в остатке: вторая запись с тем же ключом не вставится.
# Проверять это отдельно нельзя — посчитанная дважды бутылка в базе выглядит
# точно так же, как настоящая, и найти её потом не по чему.

def _audit_key(district: str, day: str, code: str) -> str:
    return f"{district}:{day}:{code}"


async def audit_scan_add(district: str, day: str, code: str, doc: dict) -> bool:
    """Записать бутылку в проход. False — эту уже сканировали сегодня."""
    db = _db_or_none()
    if db is None: return False
    try:
        await db.audit_scans.insert_one({
            "_id": _audit_key(district, day, code), "district": district,
            "day": day, "code": code, **doc})
        return True
    except DuplicateKeyError:
        return False


async def audit_scan_del(district: str, day: str, code: str) -> bool:
    """Убрать последний скан — тот, что человек только что сделал зря."""
    db = _db_or_none()
    if db is None: return False
    r = await db.audit_scans.delete_one({"_id": _audit_key(district, day, code)})
    return bool(r.deleted_count)


async def audit_scan_counts(district: str, day: str) -> dict:
    """{позиция: сколько бутылок увидела камера}. Чужие коды сюда не попадают:
    у них нет позиции, и приписать их некуда."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.audit_scans.aggregate([
        {"$match": {"district": district, "day": day, "product_id": {"$ne": ""}}},
        {"$group": {"_id": "$product_id", "n": {"$sum": 1}}}])
    return {d["_id"]: int(d["n"] or 0) for d in await cur.to_list(length=800) if d["_id"]}


async def audit_scan_odd(district: str, day: str, limit: int = 60) -> list:
    """Всё, на что стоит посмотреть глазами: коды не из реестра, бутылки с
    чужой точки и уже списанные. Каждая такая — вопрос, а не ошибка скана."""
    db = _db_or_none()
    if db is None: return []
    cur = db.audit_scans.find({"district": district, "day": day,
                               "verdict": {"$ne": "ok"}}).sort("at", -1).limit(limit)
    return await cur.to_list(length=limit)


async def audit_scan_stats(district: str, day: str) -> dict:
    db = _db_or_none()
    if db is None: return {"total": 0, "odd": 0, "at": ""}
    total = await db.audit_scans.count_documents({"district": district, "day": day})
    odd = await db.audit_scans.count_documents(
        {"district": district, "day": day, "verdict": {"$ne": "ok"}})
    last = await db.audit_scans.find({"district": district, "day": day}) \
                               .sort("at", -1).limit(1).to_list(length=1)
    return {"total": int(total), "odd": int(odd),
            "at": str((last[0].get("at") if last else "") or "")}


async def audit_scan_clear(district: str, day: str) -> int:
    """Начать проход заново. Нужно, когда посреди ревизии стало ясно, что
    считали не ту полку: дочищать по одной бутылке — не вариант."""
    db = _db_or_none()
    if db is None: return 0
    r = await db.audit_scans.delete_many({"district": district, "day": day})
    return int(r.deleted_count)


async def qr_by_product_district(district: str) -> dict:
    """{позиция: сколько бутылок с кодами лежит на этой точке}."""
    db = _db_or_none()
    if db is None or not district: return {}
    cur = db.qr_codes.aggregate([
        {"$match": {"status": "active", "district": district}},
        {"$group": {"_id": "$product_id", "n": {"$sum": 1}}},
    ])
    return {d["_id"]: int(d["n"] or 0) for d in await cur.to_list(length=500) if d["_id"]}


async def qr_by_product_district_all() -> dict:
    """{точка: {позиция: сколько бутылок}} — одним запросом по всем точкам.

    Список позиций во «Внести товар» показывает число рядом с каждой строкой, и
    это число про ТУ точку, на которой стоит человек. Общий счёт по всем точкам
    в этом месте — не сводка, а ошибка: на B3 показывалось то, что лежит на B4."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.qr_codes.aggregate([
        {"$match": {"status": "active"}},
        {"$group": {"_id": {"d": "$district", "p": "$product_id"}, "n": {"$sum": 1}}},
    ])
    out = {}
    for r in await cur.to_list(length=5000):
        d, pid = (r["_id"] or {}).get("d"), (r["_id"] or {}).get("p")
        if not d or not pid: continue
        out.setdefault(d, {})[pid] = int(r["n"] or 0)
    return out


async def qr_by_district() -> dict:
    """Сколько бутылок записано на каждой точке — для выбора точки."""
    db = _db_or_none()
    if db is None: return {}
    cur = db.qr_codes.aggregate([
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$district", "n": {"$sum": 1}}},
    ])
    return {d["_id"]: d["n"] for d in await cur.to_list(length=50) if d["_id"]}


async def qr_count_product(product_id: str) -> int:
    db = _db_or_none()
    if db is None: return 0
    return await db.qr_codes.count_documents({"product_id": product_id,
                                              "status": "active"})


async def qr_last(limit: int = 20) -> list:
    db = _db_or_none()
    if db is None: return []
    cur = db.qr_codes.find({}).sort("at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    for r in rows:
        r["code"] = r.pop("_id")
        r["at"] = str(r.get("at") or "")
    return rows


async def qr_lock_take(key: str, by: int, name: str, now, until):
    """Занять позицию на точке под пересчёт.

    Возвращает (заняли ли, кем занято). Атомарно: два человека жмут «считать
    Absolut на B2» в одну секунду, и без этого оба получили бы «да» — а потом
    посчитали бы одну полку дважды, каждый со своим числом."""
    db = _db_or_none()
    if db is None: return True, None
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    try:
        doc = await db.qr_locks.find_one_and_update(
            {"_id": key, "$or": [{"by": by}, {"until": {"$lt": now}}]},
            {"$set": {"by": by, "name": name, "until": until, "at": now}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return True, doc
    except DuplicateKeyError:
        # Запись есть и она не наша и не протухла — значит занято.
        return False, await db.qr_locks.find_one({"_id": key})


async def qr_lock_free(key: str, by: int) -> bool:
    db = _db_or_none()
    if db is None: return True
    r = await db.qr_locks.delete_one({"_id": key, "by": by})
    return r.deleted_count > 0


async def qr_locks(now) -> list:
    """Кто что сейчас считает. Протухшие не показываем — человек просто ушёл."""
    db = _db_or_none()
    if db is None: return []
    cur = db.qr_locks.find({"until": {"$gte": now}})
    rows = await cur.to_list(length=500)
    for r in rows:
        r["key"] = r.pop("_id")
        r["until"] = str(r.get("until") or "")
        r["at"] = str(r.get("at") or "")
    return rows


async def qr_list(product_id: str, district: str, limit: int = 500) -> list:
    """Бутылки этой позиции на этой точке — новые сверху."""
    db = _db_or_none()
    if db is None: return []
    q = {"product_id": product_id, "status": "active"}
    if district: q["district"] = district
    cur = db.qr_codes.find(q).sort("at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    for r in rows:
        r["code"] = r.pop("_id")
        r["at"] = str(r.get("at") or "")
    return rows


# ── проверки бутылок ────────────────────────────────────────────────────────
# Проверка — это сессия: подошли к машине водителя и просканировали, что в ней
# лежит. Хранится одним документом, потому что смысл имеет именно сессия
# целиком: «двенадцать бутылок, одна чужая» читается, а двенадцать отдельных
# записей — нет.

async def qr_check_start(driver: str, district: str, by: int, by_name: str) -> str:
    db = _db_or_none()
    if db is None: return ""
    r = await db.qr_checks.insert_one({
        "driver": driver, "district": district, "by": by, "by_name": by_name,
        "at": datetime.now(timezone.utc), "open": True,
        "items": [], "total": 0, "bad": 0, "warn": 0})
    return str(r.inserted_id)


async def qr_check_add(check_id: str, item: dict) -> bool:
    """Дописать бутылку в проверку. Повтор в той же сессии не считается."""
    db = _db_or_none()
    if db is None: return False
    from bson import ObjectId
    try: oid = ObjectId(check_id)
    except Exception: return False
    inc = {"total": 1}
    if item.get("verdict") == "alien": inc["bad"] = 1
    elif item.get("verdict") in ("written", "other_district"): inc["warn"] = 1
    r = await db.qr_checks.update_one(
        {"_id": oid, "items.code": {"$ne": item.get("code")}},
        {"$push": {"items": item}, "$inc": inc})
    return bool(r.modified_count)


async def qr_check_end(check_id: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    from bson import ObjectId
    try: oid = ObjectId(check_id)
    except Exception: return None
    await db.qr_checks.update_one({"_id": oid}, {"$set": {"open": False,
                                  "closed_at": datetime.now(timezone.utc)}})
    d = await db.qr_checks.find_one({"_id": oid})
    if d: d["id"] = str(d.pop("_id"))
    return d


async def qr_check_get(check_id: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    from bson import ObjectId
    try: oid = ObjectId(check_id)
    except Exception: return None
    d = await db.qr_checks.find_one({"_id": oid})
    if d: d["id"] = str(d.pop("_id"))
    return d


async def qr_checks(days: int = 30) -> list:
    """История проверок за период — новые сверху."""
    db = _db_or_none()
    if db is None: return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    async for d in db.qr_checks.find({"at": {"$gte": since}}).sort("at", -1).limit(200):
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


async def qr_get(code: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    d = await db.qr_codes.find_one({"_id": code})
    if d:
        d["code"] = d.pop("_id")
    return d


async def qr_move(code: str, frm: str, to: str, tid: str, by: int, at) -> bool:
    """Переставить бутылку на другой офис. False — её там уже нет.

    Реестр знает каждую бутылку поштучно, и «переехала» для него — это смена
    офиса у той же записи, а не новая: иначе одна бутылка считалась бы дважды,
    на старом месте и на новом.

    Офис отправителя стоит в условии, а не только в записи: два человека могут
    сканировать одну полку одновременно, и вторая попытка перевезти ту же
    бутылку должна не пройти, а не увезти её со следующего места."""
    db = _db_or_none()
    if db is None: return False
    r = await db.qr_codes.update_one(
        {"_id": code, "district": frm},
        {"$set": {"district": to},
         "$push": {"moves": {"from": frm, "to": to, "at": at, "by": by,
                             "transfer": tid}}})
    return r.matched_count > 0


async def qr_move_undo(code: str, tid: str = "") -> dict | None:
    """Вернуть бутылку туда, откуда её только что перевезли.

    Отменяем только последний переезд и только если бутылка всё ещё там, куда
    он её поставил: иначе отмена перечеркнула бы чужой, более поздний. tid —
    когда отменяют не «последнее», а конкретную запись перемещения: если
    последний переезд бутылки уже другой, отменять нечего."""
    db = _db_or_none()
    if db is None: return None
    d = await db.qr_codes.find_one({"_id": code}, {"moves": 1, "district": 1})
    last = ((d or {}).get("moves") or [])[-1:]
    if not last: return None
    last = last[0]
    if (d.get("district") or "") != (last.get("to") or ""): return None
    if tid and str(last.get("transfer") or "") != str(tid): return None
    await db.qr_codes.update_one(
        {"_id": code}, {"$set": {"district": last.get("from") or ""},
                        "$pop": {"moves": 1}})
    return last


async def biz_greeted(telegram_id: int) -> bool:
    """Здоровались ли уже с этим человеком от имени бизнес-аккаунта.

    Без базы возвращаем True: поздороваться дважды хуже, чем не поздороваться —
    второе приветствие подряд выглядит как сбой."""
    db = _db_or_none()
    if db is None: return True
    return await db.biz_greetings.find_one({"_id": telegram_id}) is not None


async def mark_biz_greeted(telegram_id: int, **fields):
    db = _db_or_none()
    if db is None: return
    await db.biz_greetings.update_one(
        {"_id": telegram_id},
        {"$setOnInsert": {"at": datetime.now(timezone.utc), **fields}}, upsert=True)


async def get_driver_day(day: str, driver: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.driver_days.find_one({"day": day, "driver": driver}, {"_id": 0})


async def get_driver_days(day: str) -> list:
    db = _db_or_none()
    if db is None: return []
    return await db.driver_days.find({"day": day}, {"_id": 0}).to_list(length=200)


async def get_driver_days_range(day_from: str, day_to: str) -> list:
    """Расходы за отрезок — для сводки по неделе или месяцу."""
    db = _db_or_none()
    if db is None: return []
    cur = db.driver_days.find({"day": {"$gte": day_from, "$lte": day_to}}, {"_id": 0})
    return await cur.to_list(length=5000)


async def save_driver_day(day: str, driver: str, fields: dict):
    db = _db_or_none()
    if db is None: return
    await db.driver_days.update_one(
        {"day": day, "driver": driver},
        {"$set": {**fields, "day": day, "driver": driver}},
        upsert=True,
    )


async def set_driver_no_expense(day: str, driver: str, kind: str, none: bool):
    """«Сегодня не заправлялся». Это ответ, а не расход.

    Держим отдельным полем, а не записью на ноль дирхамов: ноль в списке трат
    выглядит как ошибка ввода и занимает строку в учёте у старшего. А ответить
    водитель обязан — иначе непонятно, то ли расхода не было, то ли он забыл.
    Разница между «нет» и «молчит» и есть весь смысл поля."""
    db = _db_or_none()
    if db is None: return
    key = f"no_expense.{kind}"
    if none:
        await db.driver_days.update_one(
            {"day": day, "driver": driver},
            {"$set": {key: datetime.now(timezone.utc).isoformat()},
             "$setOnInsert": {"day": day, "driver": driver}}, upsert=True)
    else:
        await db.driver_days.update_one({"day": day, "driver": driver},
                                        {"$unset": {key: ""}})


async def add_driver_expense(day: str, driver: str, item: dict):
    """Разовый расход. Пишется через $push, чтобы две записи подряд с разных
    устройств не затирали друг друга."""
    db = _db_or_none()
    if db is None: return
    await db.driver_days.update_one(
        {"day": day, "driver": driver},
        {"$push": {"extras": item}, "$setOnInsert": {"day": day, "driver": driver}},
        upsert=True,
    )


async def update_driver_expense(day: str, driver: str, item_id: str,
                                amount: int, comment: str) -> bool:
    """Водитель поправил свою же трату. Решение менеджера при этом сбрасывается:
    утверждали одну сумму, а стала другая — значит, смотреть надо заново."""
    db = _db_or_none()
    if db is None: return False
    now = datetime.now(timezone.utc).isoformat()
    r = await db.driver_days.update_one(
        {"day": day, "driver": driver, "extras.id": item_id},
        {"$set": {"extras.$.amount": amount,
                  "extras.$.comment": comment,
                  "extras.$.status": "pending",
                  "extras.$.edited_at": now},
         "$unset": {"extras.$.decided_by": "", "extras.$.decided_at": ""}},
    )
    return bool(r.matched_count)


async def set_driver_expense_status(day: str, driver: str, item_id: str,
                                    status: str, by: int) -> bool:
    """Решение менеджера по трате. Позиционный $ обновляет ровно тот элемент
    массива, что попал в фильтр, — соседние записи не трогаются."""
    db = _db_or_none()
    if db is None: return False
    r = await db.driver_days.update_one(
        {"day": day, "driver": driver, "extras.id": item_id},
        {"$set": {"extras.$.status": status,
                  "extras.$.decided_by": by,
                  "extras.$.decided_at": datetime.now(timezone.utc).isoformat()}},
    )
    return bool(r.matched_count)


async def del_driver_expense(day: str, driver: str, item_id: str) -> bool:
    db = _db_or_none()
    if db is None: return False
    r = await db.driver_days.update_one(
        {"day": day, "driver": driver},
        {"$pull": {"extras": {"id": item_id}}},
    )
    return bool(r.modified_count)


async def get_finished_audits(limit: int = 40) -> list:
    """Завершённые ревизии, свежие первыми — без позиций.

    Позиции тянутся отдельно, когда открывают конкретный отчёт: список из сорока
    ревизий по сто двадцать две строки в каждой — это мегабайты ради заголовков."""
    db = _db_or_none()
    if db is None: return []
    cur = db.stock_counts.find(
        {"audit_finished_at": {"$exists": True}},
        {"_id": 0, "lines": 0},
    ).sort("audit_finished_at", -1).limit(int(limit))
    return await cur.to_list(length=int(limit))


# ── Чек-лист смены ─────────────────────────────────────────────────────────
# Галочка ставится сама везде, где её можно вывести из данных. Здесь хранится
# только то, чего система не видит: деньги на руках, разговор с человеком,
# решение по норме. Ключ — рабочие сутки, а не календарный день.

async def checklist_get(day: str) -> dict:
    db = _db_or_none()
    if db is None:
        return {}
    doc = await db.checklist.find_one({"day": day}, {"_id": 0, "items": 1})
    return (doc or {}).get("items") or {}


async def checklist_set(day: str, item: str, done: bool, by: str = "") -> dict:
    db = _db_or_none()
    if db is None:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    if done:
        val = {"done": True, "by": by, "at": now}
        await db.checklist.update_one({"day": day},
                                      {"$set": {f"items.{item}": val, "day": day}},
                                      upsert=True)
    else:
        val = {}
        await db.checklist.update_one({"day": day},
                                      {"$unset": {f"items.{item}": ""},
                                       "$set": {"day": day}}, upsert=True)
    return val


async def get_stock_counts_for_day(day: str) -> list:
    db = _db_or_none()
    if db is None: return []
    return await db.stock_counts.find({"day": day}, {"_id": 0, "lines": 0}).to_list(length=50)


async def add_stock_transfer(doc: dict) -> str:
    """Записать перемещение. Возвращает id — по нему его потом отменяют."""
    db = _db_or_none()
    if db is None: return ""
    r = await db.stock_transfers.insert_one(dict(doc))
    return str(r.inserted_id)


async def delete_stock_transfer(tid: str) -> bool:
    """Убрать ошибочное перемещение. Физически удаляем: перемещение — не событие
    истории, а поправка к остатку, и «отменённая» строка только путала бы счёт."""
    from bson import ObjectId
    db = _db_or_none()
    if db is None: return False
    try:
        r = await db.stock_transfers.delete_one({"_id": ObjectId(tid)})
    except Exception:
        return False
    return r.deleted_count > 0


async def get_stock_transfers(day: str) -> list:
    # Потолок высокий не для красоты: перемещение сканом — это строка на каждую
    # бутылку, и день большого переезда легко даёт их сотни. Недобранные строки
    # тихо испортили бы пересчёт обоим районам.
    db = _db_or_none()
    if db is None: return []
    return await db.stock_transfers.find({"day": day}).to_list(length=5000)


async def get_stock_transfer(tid: str) -> dict | None:
    from bson import ObjectId
    db = _db_or_none()
    if db is None: return None
    try:
        return await db.stock_transfers.find_one({"_id": ObjectId(tid)})
    except Exception:
        return None


async def get_stock_norms() -> dict:
    """{"district:product_id": норма} — только заданные вручную."""
    db = _db_or_none()
    if db is None: return {}
    rows = await db.stock_norms.find({}, {"_id": 0}).to_list(length=2000)
    return {f'{r["district"]}:{r["product_id"]}': int(r.get("norm") or 0) for r in rows}


async def del_stock_norm(district: str, product_id: str):
    """Убрать ручную норму — позиция снова считается по продажам."""
    db = _db_or_none()
    if db is None: return
    await db.stock_norms.delete_one({"district": district, "product_id": product_id})


async def set_stock_norm(district: str, product_id: str, norm: int, by: int = 0):
    db = _db_or_none()
    if db is None: return
    await db.stock_norms.update_one(
        {"district": district, "product_id": product_id},
        {"$set": {"district": district, "product_id": product_id, "norm": int(norm),
                  "by": by, "at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)


async def support_set_channel(conv_key: str, channel: str):
    """Откуда пришёл клиент: 'bot' — писал прямо в бот поддержки, 'app' — из
    приложения. От этого зависит, куда доставлять ответ оператора."""
    db = _db_or_none()
    if db is None: return
    await db.support_messages.update_one(
        {"conv_key": conv_key}, {"$set": {"channel": channel}}, upsert=True)


async def support_channel(conv_key: str) -> str:
    db = _db_or_none()
    if db is None: return ""
    doc = await db.support_messages.find_one({"conv_key": conv_key},
                                             {"_id": 0, "channel": 1})
    return (doc or {}).get("channel", "")


# ── Переписка owner-бота: реестр, чистка и архив ──────────────────────────
# Телеграм разрешает боту удалять только свои сообщения не старше 48 часов.
# Поэтому переписку с владельцем ведём по реестру: каждое отправленное и
# принятое сообщение записываем, чуть раньше срока стираем (свипер на 47-м
# часу), а по тревоге сносим всё, что в реестре осталось. Содержимое при этом
# не теряется: текст события лежит в owner_notifications и в бэкапах.

async def owner_msg_add(chat_id: int, message_id: int, event_key: str = "",
                        at: str = "") -> None:
    db = _db_or_none()
    if db is None or not (chat_id and message_id): return
    await db.owner_msgs.update_one(
        {"_id": f"{chat_id}:{message_id}"},
        {"$setOnInsert": {
            "chat_id": int(chat_id), "message_id": int(message_id),
            "event_key": event_key,
            "at": at or datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True)


async def owner_msgs_due(before_iso: str, limit: int = 300) -> list:
    """Что пора стереть: всё, что старше порога."""
    db = _db_or_none()
    if db is None: return []
    cursor = db.owner_msgs.find({"at": {"$lt": before_iso}}, {"_id": 0}).limit(limit)
    return await cursor.to_list(length=limit)


async def owner_msgs_of(chat_id: int, limit: int = 2000) -> list:
    db = _db_or_none()
    if db is None: return []
    cursor = db.owner_msgs.find({"chat_id": int(chat_id)}, {"_id": 0}).limit(limit)
    return await cursor.to_list(length=limit)


async def owner_msg_drop(chat_id: int, message_id: int) -> None:
    db = _db_or_none()
    if db is None: return
    await db.owner_msgs.delete_one({"_id": f"{chat_id}:{message_id}"})


async def notifications_search(keys: list | None = None, q: str = "",
                               frm: str = "", to: str = "",
                               limit: int = 50, offset: int = 0,
                               skip_test: bool = True) -> tuple:
    """Архив уведомлений: поиск по тексту, тип и период. Возвращает (строки, всего).

    Тестовые аккаунты владельца отсекаем прямо в запросе, а не после выборки:
    иначе «всего» и постраничная подгрузка врали бы на количество выброшенных
    строк. Условие по тексту, потому что структурного поля с автором у старых
    записей нет — а вычищать их надо и задним числом."""
    db = _db_or_none()
    if db is None: return [], 0
    filt: dict = {}
    if skip_test:
        try:
            import config_test
            pats = config_test.skip_patterns()
        except Exception:
            pats = []
        if pats:
            filt["$nor"] = [{"text": {"$regex": re.escape(p), "$options": "i"}}
                            for p in pats]
    if keys:
        filt["event_key"] = {"$in": list(keys)}
    if frm or to:
        rng = {}
        if frm: rng["$gte"] = frm
        if to:  rng["$lte"] = to
        filt["created_at"] = rng
    if q:
        filt["text"] = {"$regex": re.escape(q), "$options": "i"}
    total = await db.owner_notifications.count_documents(filt)
    cursor = (db.owner_notifications.find(filt, {"_id": 0})
              .sort("created_at", -1).skip(max(0, offset)).limit(limit))
    return await cursor.to_list(length=limit), total


async def export_token_put(token: str, doc: dict) -> None:
    db = _db_or_none()
    if db is None: return
    await db.export_tokens.update_one({"_id": token}, {"$set": doc}, upsert=True)


async def export_token_get(token: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.export_tokens.find_one({"_id": token}, {"_id": 0})


async def set_cover_msg_id(owner_id: int, msg_id: int | None) -> None:
    """Номер сообщения-прикрытия («игра») в чате владельца.

    Его нужно снять ровно тогда, когда владелец выходит из скрытого режима:
    иначе приглашение в тетрис останется висеть в рабочем чате и будет
    выглядеть ровно тем, чем является."""
    db = _db_or_none()
    if db is None: return
    if msg_id is None:
        await db.owner_prefs.update_one({"owner_id": owner_id},
                                        {"$unset": {"_cover_msg_id": ""}})
    else:
        await db.owner_prefs.update_one({"owner_id": owner_id},
                                        {"$set": {"_cover_msg_id": msg_id}}, upsert=True)


async def get_cover_msg_id(owner_id: int) -> int | None:
    db = _db_or_none()
    if db is None: return None
    doc = await db.owner_prefs.find_one({"owner_id": owner_id},
                                        {"_id": 0, "_cover_msg_id": 1})
    return (doc or {}).get("_cover_msg_id")


async def shift_day_snapshot(day: str, snap: dict = None):
    """Слепок дня на момент сборки заявки: сколько было заказов и денег.

    Заявка собирается один раз — по первому моменту, когда закрылись все
    районы. Но район могут открыть заново и добить в него заказы, и тогда
    отправленный файл уже неполный. Слепок позволяет это заметить: если при
    следующем закрытии цифры дня разошлись со слепком, заявку надо уточнить.
    """
    db = _db_or_none()
    if db is None: return {}
    if snap is None:
        doc = await db.shift_days.find_one({"_id": f"{day}:*"}, {"_id": 0, "snap": 1})
        return (doc or {}).get("snap") or {}
    await db.shift_days.update_one({"_id": f"{day}:*"},
                                   {"$set": {"snap": snap, "day": day, "district": "*"}},
                                   upsert=True)
    return snap
