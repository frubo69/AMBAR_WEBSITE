#!/usr/bin/env python3
"""Debug: compare delivered orders in DB against catalog product IDs."""
import asyncio, json, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
import db

async def main():
    await db.connect()
    _db = db._db_or_none()
    if _db is None:
        print("ERROR: DB not connected"); return

    # 1. Get ALL delivered orders
    orders = await _db.orders.find(
        {"status": "delivered"}, {"_id": 0}
    ).to_list(length=5000)
    print(f"Total delivered orders: {len(orders)}")

    # 2. Aggregate product sales from orders
    sales = {}
    for o in orders:
        for it in (o.get("items") or []):
            pid = it.get("id")
            if not pid:
                continue
            if pid not in sales:
                sales[pid] = {"sold": 0, "rev": 0, "name": it.get("name", "?"), "orders": []}
            qty = int(it.get("qty") or 0)
            line = it.get("line_total") or (it.get("price", 0) * qty)
            sales[pid]["sold"] += qty
            sales[pid]["rev"] += int(line or 0)
            sales[pid]["orders"].append(o.get("order_id", "?"))

    print(f"\nUnique product IDs in delivered orders: {len(sales)}")
    print("\n--- Products sold (from orders) ---")
    for pid, s in sorted(sales.items(), key=lambda x: -x[1]["rev"]):
        print(f"  {pid}: {s['name']} — sold={s['sold']}, rev={s['rev']} AED (orders: {s['orders'][:3]}...)")

    # 3. Load catalog
    catalog = json.loads((Path(__file__).parent / "catalog.json").read_text())
    cat_ids = {p["id"] for p in catalog}
    cat_map = {p["id"]: p["name"] for p in catalog}

    # 4. Check for mismatches
    order_ids = set(sales.keys())
    missing = order_ids - cat_ids
    if missing:
        print(f"\n!!! IDs in orders but NOT in catalog: {missing}")
        for pid in missing:
            print(f"  {pid}: {sales[pid]['name']} — sold={sales[pid]['sold']}, rev={sales[pid]['rev']}")
    else:
        print("\n✓ All order product IDs exist in catalog")

    extra = cat_ids - order_ids
    if extra:
        print(f"\nCatalog products with 0 sales: {len(extra)}")

    # 5. Check what _aggregate_sales would return for "month" period
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_orders = [o for o in orders if o.get("timestamp", "") >= month_start.isoformat().replace("+00:00", "")]
    print(f"\nDelivered orders this month (since {month_start.strftime('%Y-%m-%d')}): {len(month_orders)}")

    month_sales = {}
    for o in month_orders:
        for it in (o.get("items") or []):
            pid = it.get("id")
            if not pid: continue
            if pid not in month_sales:
                month_sales[pid] = {"sold": 0, "rev": 0, "name": it.get("name", "?")}
            qty = int(it.get("qty") or 0)
            line = it.get("line_total") or (it.get("price", 0) * qty)
            month_sales[pid]["sold"] += qty
            month_sales[pid]["rev"] += int(line or 0)

    print("--- This month's sales ---")
    for pid, s in sorted(month_sales.items(), key=lambda x: -x[1]["rev"]):
        in_cat = "✓" if pid in cat_ids else "✗ MISSING"
        print(f"  {pid} [{in_cat}]: {s['name']} — sold={s['sold']}, rev={s['rev']}")

asyncio.run(main())
