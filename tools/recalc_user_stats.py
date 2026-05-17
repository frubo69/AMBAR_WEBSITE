#!/usr/bin/env python3
"""Full cleanup: delete test/demo orders, recalculate all user stats from real orders.
Uses the same db.py connection as the bots (reads .env for MONGO_URI)."""

import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

TEST_ACCOUNTS = {8251195567, 6731325660}

def _to_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0

async def main():
    await db.connect()
    mdb = db._db

    # ── Step 1: Dump raw DB state ──
    print("=" * 60)
    print("STEP 1: Raw database state")
    print("=" * 60)

    all_orders = await mdb.orders.find(
        {}, {"order_id": 1, "customer_id": 1, "status": 1, "total": 1,
             "customer_name": 1, "username": 1, "timestamp": 1}
    ).to_list(None)
    print(f"Total orders in DB: {len(all_orders)}")

    for o in all_orders:
        cid = o.get("customer_id", "?")
        oid = o.get("order_id", "?")
        status = o.get("status", "?")
        total = o.get("total", 0)
        name = o.get("customer_name", "?")
        uname = o.get("username", "")
        ts = str(o.get("timestamp", ""))[:19]
        is_test = " **TEST**" if _to_int(cid) in TEST_ACCOUNTS else ""
        print(f"  {oid} | cid={cid} | {name} @{uname} | {status} | {total} AED | {ts}{is_test}")

    print(f"\nUsers with total_spent > 0:")
    users_with_spent = await mdb.users.find(
        {"total_spent": {"$gt": 0}},
        {"telegram_id": 1, "full_name": 1, "username": 1,
         "total_spent": 1, "orders_total": 1, "orders_done": 1}
    ).sort("total_spent", -1).to_list(None)
    for u in users_with_spent:
        name = u.get("full_name") or "?"
        print(f"  {name} @{u.get('username','')} | tid={u.get('telegram_id')} | "
              f"spent={u.get('total_spent')} | orders={u.get('orders_total')} | done={u.get('orders_done')}")

    # ── Step 2: Delete test account orders ──
    print("\n" + "=" * 60)
    print("STEP 2: Deleting test account orders...")
    print("=" * 60)

    test_orders = [o for o in all_orders if _to_int(o.get("customer_id")) in TEST_ACCOUNTS]
    if test_orders:
        test_oids = [o["order_id"] for o in test_orders if "order_id" in o]
        result = await mdb.orders.delete_many({"order_id": {"$in": test_oids}})
        print(f"Deleted {result.deleted_count} test orders")
    else:
        print("No test orders found")

    # ── Step 3: Recalculate ALL user stats ──
    print("\n" + "=" * 60)
    print("STEP 3: Recalculating all user stats...")
    print("=" * 60)

    real_orders = await mdb.orders.find({}, {"customer_id": 1, "status": 1, "total": 1}).to_list(None)
    print(f"Real orders remaining: {len(real_orders)}")

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

    all_users = await mdb.users.find(
        {}, {"telegram_id": 1, "full_name": 1, "username": 1,
             "total_spent": 1, "orders_total": 1, "orders_done": 1, "orders_declined": 1}
    ).to_list(None)
    print(f"Total users: {len(all_users)}")

    updated = 0
    for u in all_users:
        uid = u.get("telegram_id")
        if not uid:
            continue
        real = stats.get(_to_int(uid), {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0})
        old = {k: int(u.get(k, 0) or 0) for k in ("total_spent", "orders_total", "orders_done", "orders_declined")}

        if any(old[k] != real[k] for k in real):
            name = u.get("full_name") or u.get("username") or str(uid)
            print(f"  FIX {name}: spent {old['total_spent']}->{real['total_spent']}, "
                  f"orders {old['orders_total']}->{real['orders_total']}, "
                  f"done {old['orders_done']}->{real['orders_done']}")
            await mdb.users.update_one({"telegram_id": uid}, {"$set": real})
            updated += 1

    # ── Step 4: Verify ──
    print("\n" + "=" * 60)
    print(f"DONE. Fixed {updated} users. New top-10:")
    print("=" * 60)

    top = await mdb.users.find(
        {"total_spent": {"$gt": 0}},
        {"full_name": 1, "username": 1, "total_spent": 1, "orders_done": 1, "orders_total": 1}
    ).sort("total_spent", -1).limit(10).to_list(None)

    if not top:
        print("  (no users with delivered orders)")
    for i, u in enumerate(top, 1):
        name = u.get("full_name") or u.get("username") or "?"
        print(f"  #{i} {name}: {u['total_spent']} AED | "
              f"{u.get('orders_done',0)} delivered / {u.get('orders_total',0)} total")

asyncio.run(main())
