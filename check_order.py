#!/usr/bin/env python3
"""Check ETA and timing for a specific order."""
import asyncio, sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
import db

async def main():
    await db.connect()
    _db = db._db_or_none()
    if _db is None:
        print("ERROR: DB not connected"); return

    orders = await _db.orders.find(
        {"status": "delivered"}, {"_id": 0}
    ).to_list(length=5000)

    for o in orders:
        oid = o.get("order_id", "?")
        ts = o.get("timestamp", "")
        eta = o.get("eta")
        confirmed = o.get("confirmed_at", "")
        delivered = o.get("updated_at", "") or o.get("delivered_at", "")

        dur = None
        if ts and delivered:
            try:
                t0 = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                t1 = datetime.fromisoformat(delivered).replace(tzinfo=timezone.utc)
                dur = round((t1 - t0).total_seconds() / 60, 1)
            except:
                pass

        eta_val = int(eta or 0) or 25
        is_late = dur is not None and int(dur) > eta_val + 5

        print(f"{oid}: eta={eta!r} (using {eta_val}), placed={ts}, confirmed={confirmed}, delivered={delivered}, duration={dur} min, late={is_late}")

asyncio.run(main())
