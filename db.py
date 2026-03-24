"""
AMBAR — shared MongoDB helpers.
All three processes (api_server, bot, operator_bot) import this module.
Each process maintains its own Motor client pointing at the same Atlas cluster.
"""
from __future__ import annotations
import os, logging
from datetime import datetime, timezone
import motor.motor_asyncio
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
        MONGO_URI, serverSelectionTimeoutMS=8000
    )
    _db = _client.ambar
    try:
        await _db.orders.create_index("order_id", unique=True)
        await _db.orders.create_index("customer_id")
        await _db.users.create_index("tg_id", unique=True)
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

async def upsert_user(tg_id: int, **fields):
    db = _db_or_none()
    if db is None: return
    now = datetime.now(timezone.utc)
    fields["updated_at"] = now
    await db.users.update_one(
        {"tg_id": tg_id},
        {
            "$set": fields,
            "$setOnInsert": {"tg_id": tg_id, "is_banned": False, "created_at": now},
        },
        upsert=True,
    )


async def get_user(tg_id: int) -> dict | None:
    db = _db_or_none()
    if db is None: return None
    return await db.users.find_one({"tg_id": tg_id}, {"_id": 0})


async def is_banned(tg_id: int) -> bool:
    u = await get_user(tg_id)
    return bool(u and u.get("is_banned"))


async def ban_user(tg_id: int, reason: str, by: int):
    await upsert_user(
        tg_id,
        is_banned=True,
        ban_reason=reason,
        banned_by=by,
        banned_at=datetime.now(timezone.utc).isoformat(),
    )


async def unban_user(tg_id: int):
    await upsert_user(tg_id, is_banned=False, ban_reason=None, banned_by=None, banned_at=None)


async def get_all_banned() -> list:
    db = _db_or_none()
    if db is None: return []
    cursor = db.users.find({"is_banned": True}, {"_id": 0})
    return await cursor.to_list(length=500)


# ── Addresses ─────────────────────────────────────────────────────────────────

async def save_address(tg_id: int, addr_entry: dict):
    """Push address to front of list (max 5, no duplicate streets)."""
    u     = await get_user(tg_id) or {}
    lst   = u.get("addresses", [])
    norm  = addr_entry.get("address", "").strip().lower()
    lst   = [a for a in lst if a.get("address", "").strip().lower() != norm]
    lst.insert(0, addr_entry)
    await upsert_user(tg_id, addresses=lst[:5])


# ── User state ────────────────────────────────────────────────────────────────

async def get_ustate(tg_id: int) -> dict:
    u = await get_user(tg_id)
    return (u or {}).get("state", {})


async def set_ustate(tg_id: int, data: dict):
    await upsert_user(tg_id, state=data)


async def upd_ustate(tg_id: int, **kw):
    u     = await get_user(tg_id) or {}
    state = {**u.get("state", {}), **kw}
    await upsert_user(tg_id, state=state)


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
