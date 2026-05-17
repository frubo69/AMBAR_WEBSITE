#!/usr/bin/env python3
"""Full cleanup: delete test/demo orders, recalculate all user stats from real orders."""

import asyncio
import motor.motor_asyncio

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "ambar"
TEST_ACCOUNTS = {8251195567, 6731325660}

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    # ── Step 1: Dump raw DB state so we can see what's wrong ──
    print("=" * 60)
    print("STEP 1: Raw database state")
    print("=" * 60)

    all_orders = await db.orders.find(
        {}, {"order_id": 1, "customer_id": 1, "status": 1, "total": 1,
             "subtotal": 1, "customer_name": 1, "username": 1, "timestamp": 1}
    ).to_list(None)
    print(f"Total orders in DB: {len(all_orders)}")

    # Show every order with its key fields
    for o in all_orders:
        cid = o.get("customer_id", "?")
        oid = o.get("order_id", "?")
        status = o.get("status", "?")
        total = o.get("total", 0)
        name = o.get("customer_name", "?")
        uname = o.get("username", "")
        ts = str(o.get("timestamp", ""))[:19]
        is_test = "TEST" if _to_int(cid) in TEST_ACCOUNTS else ""
        print(f"  {oid} | cid={cid} | {name} @{uname} | {status} | {total} AED | {ts} {is_test}")

    # Show users with non-zero stats
    print(f"\nUsers with total_spent > 0:")
    users_with_spent = await db.users.find(
        {"total_spent": {"$gt": 0}},
        {"telegram_id": 1, "full_name": 1, "first_name": 1, "username": 1,
         "total_spent": 1, "orders_total": 1, "orders_done": 1, "orders_declined": 1}
    ).sort("total_spent", -1).to_list(None)
    for u in users_with_spent:
        name = u.get("full_name") or u.get("first_name") or "?"
        uname = u.get("username", "")
        print(f"  {name} @{uname} | tid={u.get('telegram_id')} | "
              f"spent={u.get('total_spent')} | orders={u.get('orders_total')} | "
              f"done={u.get('orders_done')} | declined={u.get('orders_declined')}")

    # ── Step 2: Delete test account orders ──
    print("\n" + "=" * 60)
    print("STEP 2: Deleting test account orders...")
    print("=" * 60)

    # Match both int and string customer_id for test accounts
    test_cids = []
    for t in TEST_ACCOUNTS:
        test_cids.extend([t, str(t)])
    test_orders = [o for o in all_orders if _to_int(o.get("customer_id")) in TEST_ACCOUNTS]
    if test_orders:
        test_oids = [o["order_id"] for o in test_orders if "order_id" in o]
        result = await db.orders.delete_many({"order_id": {"$in": test_oids}})
        print(f"Deleted {result.deleted_count} test orders")
    else:
        print("No test orders found")

    # ── Step 3: Recalculate ALL user stats from remaining orders ──
    print("\n" + "=" * 60)
    print("STEP 3: Recalculating all user stats...")
    print("=" * 60)

    real_orders = await db.orders.find({}, {"customer_id": 1, "status": 1, "total": 1}).to_list(None)
    print(f"Real orders remaining: {len(real_orders)}")

    # Build stats per customer from actual orders
    stats = {}
    for o in real_orders:
        cid = _to_int(o.get("customer_id"))
        if not cid:
            continue
        if cid not in stats:
            stats[cid] = {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0}
        s = stats[cid]
        status = o.get("status", "")
        s["orders_total"] += 1
        if status == "delivered":
            s["orders_done"] += 1
            s["total_spent"] += int(o.get("total", 0) or 0)
        elif status == "declined":
            s["orders_declined"] += 1

    # Update EVERY user — reset to 0 if they have no orders
    all_users = await db.users.find(
        {}, {"telegram_id": 1, "full_name": 1, "first_name": 1, "username": 1,
             "total_spent": 1, "orders_total": 1, "orders_done": 1, "orders_declined": 1}
    ).to_list(None)
    print(f"Total users: {len(all_users)}")

    updated = 0
    for u in all_users:
        uid = u.get("telegram_id")
        if not uid:
            continue
        uid_int = _to_int(uid)
        real = stats.get(uid_int, {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0})
        old_spent = int(u.get("total_spent", 0) or 0)
        old_orders = int(u.get("orders_total", 0) or 0)
        old_done = int(u.get("orders_done", 0) or 0)
        old_declined = int(u.get("orders_declined", 0) or 0)

        new_vals = {
            "total_spent": real["total_spent"],
            "orders_total": real["orders_total"],
            "orders_done": real["orders_done"],
            "orders_declined": real["orders_declined"],
        }

        changed = (old_spent != new_vals["total_spent"] or old_orders != new_vals["orders_total"]
                    or old_done != new_vals["orders_done"] or old_declined != new_vals["orders_declined"])
        if changed:
            name = u.get("full_name") or u.get("first_name") or u.get("username") or str(uid)
            print(f"  FIX {name}: spent {old_spent}->{new_vals['total_spent']}, "
                  f"orders {old_orders}->{new_vals['orders_total']}, "
                  f"done {old_done}->{new_vals['orders_done']}, "
                  f"declined {old_declined}->{new_vals['orders_declined']}")
            await db.users.update_one({"telegram_id": uid}, {"$set": new_vals})
            updated += 1

    # ── Step 4: Verify — show new top-10 ──
    print("\n" + "=" * 60)
    print(f"DONE. Fixed {updated} users. New top-10:")
    print("=" * 60)

    top = await db.users.find(
        {"total_spent": {"$gt": 0}},
        {"full_name": 1, "first_name": 1, "username": 1, "total_spent": 1, "orders_done": 1, "orders_total": 1}
    ).sort("total_spent", -1).limit(10).to_list(None)

    if not top:
        print("  (no users with delivered orders)")
    for i, u in enumerate(top, 1):
        name = u.get("full_name") or u.get("first_name") or u.get("username") or "?"
        print(f"  #{i} {name}: {u['total_spent']} AED | "
              f"{u.get('orders_done',0)} delivered / {u.get('orders_total',0)} total")


def _to_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


asyncio.run(main())
