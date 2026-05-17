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

    # ── Step 1: Find and show all suspicious data ──
    print("=" * 60)
    print("STEP 1: Scanning for test/demo orders...")
    print("=" * 60)

    all_orders = await db.orders.find({}, {"order_id": 1, "customer_id": 1, "status": 1,
                                            "total": 1, "customer_name": 1}).to_list(None)
    print(f"Total orders in DB: {len(all_orders)}")

    test_orders = []
    suspicious_orders = []
    for o in all_orders:
        cid = o.get("customer_id")
        try:
            cid_int = int(cid) if cid else 0
        except (ValueError, TypeError):
            cid_int = 0
        total = int(o.get("total", 0) or 0)
        oid = o.get("order_id", "?")
        name = o.get("customer_name", "?")

        if cid_int in TEST_ACCOUNTS:
            test_orders.append(o)
            print(f"  TEST order: {oid} | {name} | {total} AED | status={o.get('status')}")
        elif total > 50000:
            suspicious_orders.append(o)
            print(f"  SUS order:  {oid} | {name} | {total} AED | status={o.get('status')}")

    print(f"\nTest account orders: {len(test_orders)}")
    print(f"Suspicious orders (>50k AED): {len(suspicious_orders)}")

    # ── Step 2: Delete test orders ──
    print("\n" + "=" * 60)
    print("STEP 2: Deleting test account orders...")
    print("=" * 60)

    if test_orders:
        test_oids = [o["order_id"] for o in test_orders if "order_id" in o]
        result = await db.orders.delete_many({"order_id": {"$in": test_oids}})
        print(f"Deleted {result.deleted_count} test orders")
    else:
        print("No test orders to delete")

    # ── Step 3: Recalculate all user stats from remaining real orders ──
    print("\n" + "=" * 60)
    print("STEP 3: Recalculating user stats from real orders...")
    print("=" * 60)

    real_orders = await db.orders.find({}, {"customer_id": 1, "status": 1, "total": 1}).to_list(None)
    print(f"Real orders remaining: {len(real_orders)}")

    stats = {}
    for o in real_orders:
        cid = o.get("customer_id")
        if not cid:
            continue
        try:
            cid = int(cid)
        except (ValueError, TypeError):
            continue
        if cid not in stats:
            stats[cid] = {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0}
        s = stats[cid]
        status = o.get("status", "")
        if status in ("pending", "approved", "delivered", "declined", "cancelled"):
            s["orders_total"] += 1
        if status == "delivered":
            s["orders_done"] += 1
            s["total_spent"] += int(o.get("total", 0) or 0)
        if status == "declined":
            s["orders_declined"] += 1

    users = await db.users.find({}, {"telegram_id": 1, "full_name": 1, "first_name": 1, "username": 1,
                                      "total_spent": 1, "orders_total": 1, "orders_done": 1,
                                      "orders_declined": 1}).to_list(None)
    print(f"Total users: {len(users)}")

    updated = 0
    for u in users:
        uid = u.get("telegram_id")
        if not uid:
            continue
        real = stats.get(int(uid), {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0})
        old_spent = int(u.get("total_spent", 0) or 0)
        old_orders = int(u.get("orders_total", 0) or 0)
        old_done = int(u.get("orders_done", 0) or 0)
        old_declined = int(u.get("orders_declined", 0) or 0)

        changed = (old_spent != real["total_spent"] or old_orders != real["orders_total"]
                    or old_done != real["orders_done"] or old_declined != real["orders_declined"])
        if changed:
            name = u.get("full_name") or u.get("first_name") or u.get("username") or str(uid)
            print(f"  FIX {name}: spent {old_spent}->{real['total_spent']}, "
                  f"orders {old_orders}->{real['orders_total']}, "
                  f"done {old_done}->{real['orders_done']}, "
                  f"declined {old_declined}->{real['orders_declined']}")
            await db.users.update_one(
                {"telegram_id": uid},
                {"$set": {
                    "total_spent": real["total_spent"],
                    "orders_total": real["orders_total"],
                    "orders_done": real["orders_done"],
                    "orders_declined": real["orders_declined"],
                }}
            )
            updated += 1

    # ── Step 4: Show final top-10 ──
    print("\n" + "=" * 60)
    print("STEP 4: New top-10 by real spending")
    print("=" * 60)

    top_users = await db.users.find(
        {"total_spent": {"$gt": 0}},
        {"full_name": 1, "first_name": 1, "username": 1, "total_spent": 1, "orders_total": 1, "orders_done": 1}
    ).sort("total_spent", -1).limit(10).to_list(None)

    for i, u in enumerate(top_users, 1):
        name = u.get("full_name") or u.get("first_name") or u.get("username") or "?"
        print(f"  #{i} {name}: {u.get('total_spent',0)} AED | "
              f"{u.get('orders_done',0)} delivered / {u.get('orders_total',0)} total")

    print(f"\nDone. Fixed {updated} users.")

asyncio.run(main())
