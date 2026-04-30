"""
One-shot hard-delete an order from production Mongo.

Usage:
    cd /path/to/MINIAPP && python3 tools/delete_order.py AMB6043998

What it does:
    1. Fetches the order from `orders`, prints every field — you confirm it's the right one.
    2. Prompts `type DELETE to confirm`.
    3. Deletes the order document.
    4. Reverts the customer's aggregate counters on the `users` doc:
         - status=delivered  → orders_total -1, orders_done -1, total_spent -<order total>
         - status=declined   → orders_total -1, orders_declined -1
         - status=cancelled  → orders_total -1
         - status=pending    → orders_total -1   (was incremented when order was placed)
         - status=confirmed  → orders_total -1
         - other             → no aggregate revert (warns + asks)

    Telegram messages (op_msg_ids, customer_msg_id) are left alone — they're already
    in chat history; editing/recalling them for an admin cleanup is out of scope.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import certifi
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "")


def _print_doc(doc: dict) -> None:
    """Pretty-print a Mongo doc with non-JSON types stringified."""
    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        try:
            return str(o)
        except Exception:
            return repr(o)
    print(json.dumps(doc, indent=2, ensure_ascii=False, default=_default))


def _aggregate_revert(status: str, total: int) -> dict:
    """Return $inc payload to undo the customer aggregate bumps from this order."""
    revert = {}
    if status == "delivered":
        revert = {"orders_total": -1, "orders_done": -1, "total_spent": -int(total or 0)}
    elif status == "declined":
        revert = {"orders_total": -1, "orders_declined": -1}
    elif status in ("cancelled", "pending", "confirmed", "approved"):
        revert = {"orders_total": -1}
    return revert


async def main(order_id: str) -> int:
    if not MONGO_URI:
        print("ERROR: MONGO_URI is not set (check .env)", file=sys.stderr)
        return 2

    client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=8000,
        tlsCAFile=certifi.where(),
    )
    db = client.ambar

    order = await db.orders.find_one({"order_id": order_id})
    if not order:
        print(f"ERROR: order {order_id} not found in `orders` collection.", file=sys.stderr)
        return 3

    print("=" * 70)
    print(f"FOUND ORDER: {order_id}")
    print("=" * 70)
    _print_doc(order)
    print("=" * 70)

    customer_id = order.get("customer_id")
    status      = order.get("status", "")
    total       = order.get("total", 0)
    revert      = _aggregate_revert(status, total)

    print(f"\nCustomer ID : {customer_id}")
    print(f"Status      : {status}")
    print(f"Total       : {total} AED")
    if revert:
        print(f"Will revert : {revert}  (applied to users.{customer_id})")
    else:
        print(f"Will revert : NONE — unhandled status '{status}', user aggregates untouched.")

    print("\nThis is a HARD DELETE. The order document will be removed.")
    print("Type DELETE (uppercase) and press Enter to proceed. Anything else cancels.")
    confirm = input("> ").strip()
    if confirm != "DELETE":
        print("Cancelled. No changes made.")
        return 1

    # Delete the order
    res = await db.orders.delete_one({"order_id": order_id})
    print(f"\n✓ orders.delete_one matched={res.matched_count if hasattr(res,'matched_count') else 'n/a'} deleted={res.deleted_count}")

    # Revert customer aggregates
    if revert and customer_id is not None:
        # customer_id may be int or str across the collection — try both.
        cid_variants = list({customer_id, str(customer_id)})
        try:
            cid_int = int(customer_id)
            cid_variants = list({cid_int, str(cid_int)})
        except (TypeError, ValueError):
            pass
        upd = await db.users.update_one(
            {"telegram_id": {"$in": cid_variants}},
            {"$inc": revert},
        )
        print(f"✓ users.update_one matched={upd.matched_count} modified={upd.modified_count} (telegram_id in {cid_variants})")
        if upd.matched_count == 0:
            print("  ⚠️  No matching user document — aggregates not reverted (user may have been pruned).")
    elif not revert:
        print("(skipped aggregate revert — see warning above)")

    client.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 tools/delete_order.py <order_id>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
