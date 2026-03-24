"""
AMBAR — shared MongoDB helpers.
All three processes (api_server, bot, operator_bot) import this module.
Each process maintains its own Motor client pointing at the same Atlas cluster.
"""
from __future__ import annotations
import os, logging
from datetime import datetime, timezone
import motor.motor_asyncio
import certifi
from dotenv import load_dotenv

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
    _client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=8000,
        tlsCAFile=certifi.where(),   # use up-to-date CA bundle, fixes Atlas TLS on old servers
    )
    _db = _client.ambar
    try:
        await _db.orders.create_index("order_id", unique=True)
        await _db.orders.create_index("customer_id")
        await _db.users.create_index("telegram_id", unique=True)
        await _db.support_messages.create_index("conv_key", unique=True)
        await _db.support_map.create_index("fwd_msg_id", unique=True)
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

async def save_order(oid: str, data: dict):
    db = _db_or_none()
    if db is None: return
    await db.orders.update_one({"order_id": oid}, {"$set": data}, upsert=True)


async def update_order(oid: str, **kw):
    db = _db_or_none()
    if db is None: return
    await db.orders.update_one({"order_id": oid}, {"$set": kw})


async def get_order(oid: str) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.orders.find_one({"order_id": oid}, {"_id": 0})


async def get_all_orders(office_id: str = None) -> dict:
    """Returns {order_id: order_doc, ...}"""
    db = _db_or_none()
    if db is None: return {}
    filt = {"office_id": office_id} if office_id else {}
    cursor = db.orders.find(filt, {"_id": 0})
    docs = await cursor.to_list(length=2000)
    return {o["order_id"]: o for o in docs}


async def get_user_orders(tg_id: int) -> list:
    db = _db_or_none()
    if db is None: return []
    cursor = db.orders.find({"customer_id": tg_id}, {"_id": 0}).sort("timestamp", -1)
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
        },
    }
    if phone:
        update["$addToSet"] = {"phones": phone}
    await db.users.update_one({"telegram_id": telegram_id}, update, upsert=True)


async def get_user(telegram_id: int) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.users.find_one({"telegram_id": telegram_id}, {"_id": 0})


async def is_banned(telegram_id: int) -> bool:
    u = await get_user(telegram_id)
    return bool(u and u.get("is_banned"))


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
    """Push address to front of list (max 5, no duplicate streets)."""
    u    = await get_user(telegram_id) or {}
    lst  = u.get("addresses", [])
    norm = addr_entry.get("address", "").strip().lower()
    lst  = [a for a in lst if a.get("address", "").strip().lower() != norm]
    lst.insert(0, addr_entry)
    await upsert_user(telegram_id, addresses=lst[:5])


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
