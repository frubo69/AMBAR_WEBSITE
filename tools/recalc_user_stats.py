#!/usr/bin/env python3
"""Recalculate total_spent, orders_total, orders_done, orders_declined
for every user from actual orders in the database."""

import asyncio
import motor.motor_asyncio

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "ambar"

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    orders = await db.orders.find({}, {"customer_id": 1, "status": 1, "total": 1}).to_list(None)
    print(f"Found {len(orders)} orders in database")

    stats = {}
    for o in orders:
        cid = o.get("customer_id")
        if not cid:
            continue
        cid = int(cid)
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

    users = await db.users.find({}, {"telegram_id": 1, "full_name": 1, "first_name": 1,
                                      "total_spent": 1, "orders_total": 1, "orders_done": 1}).to_list(None)
    print(f"Found {len(users)} users")

    updated = 0
    for u in users:
        uid = u.get("telegram_id")
        if not uid:
            continue
        real = stats.get(int(uid), {"orders_total": 0, "orders_done": 0, "orders_declined": 0, "total_spent": 0})
        old_spent = int(u.get("total_spent", 0) or 0)
        old_total = int(u.get("orders_total", 0) or 0)

        if old_spent != real["total_spent"] or old_total != real["orders_total"]:
            name = u.get("full_name") or u.get("first_name") or str(uid)
            print(f"  {name}: spent {old_spent} -> {real['total_spent']}, "
                  f"orders {old_total} -> {real['orders_total']}, "
                  f"done {real['orders_done']}, declined {real['orders_declined']}")
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

    print(f"\nDone. Updated {updated} users.")

asyncio.run(main())
