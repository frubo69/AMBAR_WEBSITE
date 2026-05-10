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
        await _db.shifts.create_index([("operator_id", 1), ("status", 1)])
        await _db.owner_managers.create_index("telegram_id", unique=True)
        await _db.owner_access_log.create_index("telegram_id", unique=True)
        await _db.owner_access_log.create_index([("status", 1), ("last_attempt_at", -1)])
        await _db.owner_notifications.create_index([("created_at", -1)])
        await _db.owner_notifications.create_index("event_key")
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
    db = _db_or_none()
    if db is None: return {}
    if office_id is None:
        filt = {}
    elif isinstance(office_id, (list, tuple, set)):
        if not office_id:
            return {}
        filt = {"office_id": {"$in": list(office_id)}}
    else:
        filt = {"office_id": office_id}
    cursor = db.orders.find(filt, {"_id": 0})
    docs = await cursor.to_list(length=2000)
    return {o["order_id"]: o for o in docs}


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


async def get_orders_in_range(start_iso: str, end_iso: str, office_id: str = None) -> list:
    """Return all orders with timestamp between start and end."""
    db = _db_or_none()
    if db is None: return []
    filt = {"timestamp": {"$gte": start_iso, "$lte": end_iso}}
    if office_id:
        filt["office_id"] = office_id
    cursor = db.orders.find(filt, {"_id": 0}).sort("timestamp", -1)
    return await cursor.to_list(length=500)


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
    "timing.late45": True, "timing.notAccepted5": True, "timing.enroute30": False,
    "reviews.bad3": True, "reviews.good5": False, "reviews.comment": True, "reviews.any": False,
    "digest.morning": True, "digest.evening": True, "digest.weekly": False, "digest.monthly": False,
    "stock.low": True, "stock.out": True,
    "customers.new": False, "customers.verify": True, "customers.vip": False,
    "customers.vipReturn": False, "customers.vipChurn": False,
    "ops.officeEmpty": True,
    "finance.revenueLow": True, "finance.avgDrop": True,
    "finance.cancelSpike": True, "finance.record": False, "finance.tipHigh": False,
    "support.new": False, "support.noreply": True, "support.complaint": True, "support.escalation": False,
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
        if not p.get(event_key):
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
    cursor = db.users.find({}, {"_id": 0}).sort("first_seen", -1)
    return await cursor.to_list(length=2000)


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

async def insert_notification(event_key: str, text: str, owner_id: int = 0) -> None:
    """Persist a notification event. owner_id=0 means broadcast."""
    db = _db_or_none()
    if db is None: return
    await db.owner_notifications.insert_one({
        "event_key": event_key,
        "text": text,
        "owner_id": owner_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


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
