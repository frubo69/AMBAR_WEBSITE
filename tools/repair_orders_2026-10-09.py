"""Два ночных заказа B5 (Castel Barreyres, крипта, по 200), вписанных Умаром
9 окт 2026 в 06:13 и 06:15 — после закрытия смены в 05:57 — программа
подписала 9 октября (правило «после закрытия — следующий день»). Владелец:
«это вчерашние заказы, перенеси их на 8 октября». В 15:14 Умар их отменил.

Скрипт возвращает оба заказа доставленными и переносит на 8 октября, как
заказ задним числом (backfilled). Копия до правки — /root/repair_orders_*.json.

    cd /opt/ambar && ./venv/bin/python tools/repair_orders_2026-10-09.py        # показать
    cd /opt/ambar && DRY=0 ./venv/bin/python tools/repair_orders_2026-10-09.py  # применить
"""
import asyncio, json, os, sys, logging
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)
from dotenv import load_dotenv; load_dotenv(".env")   # noqa: E402
import db                                             # noqa: E402

IDS = ["AMB2104640", "AMB2025638"]
DAY, FROM = "2026-10-08", "2026-10-09"
DRY = os.getenv("DRY", "1") != "0"


async def main():
    await db.connect()
    rows = [await db._db.orders.find_one({"order_id": i}) for i in IDS]
    if any(r is None for r in rows):
        print("не все заказы найдены"); return 1
    for r in rows:
        print(f"{r['order_id']}: status={r.get('status')} day={r.get('day')} delivered_at={r.get('delivered_at')} "
              f"cancelled_at={r.get('cancelled_at')} total={r.get('total')} {r.get('payment_method')}")
    if DRY:
        print("\nDRY=1 — ничего не меняю. Применить: DRY=0"); return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = f"/root/repair_orders_{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, default=str, indent=1)
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        if r.get("day") == DAY and r.get("status") == "delivered":
            print(r["order_id"], "уже на месте"); continue
        delivered_at = r.get("delivered_at") or r.get("confirmed_at") or r.get("timestamp")
        res = await db._db.orders.update_one(
            {"order_id": r["order_id"]},
            {"$set": {"status": "delivered", "delivered_at": delivered_at, "delivered_by": r.get("delivered_by") or "Умар",
                      "stock_office": r.get("stock_office") or "tecom", "day": DAY,
                      "backfilled": True, "backfill_day": DAY, "backfill_at": now,
                      "day_moved_from": FROM, "day_moved_by": "владелец, 9 окт 2026: заказы закрытой смены",
                      "updated_at": now},
             "$unset": {"cancelled_at": "", "cancelled_by": "", "cancelled_by_name": "", "cancel_reason": ""}})
        print(r["order_id"], "восстановлен и перенесён" if res.modified_count else "НЕ изменён")
    for i in IDS:
        o = await db._db.orders.find_one({"order_id": i}, {"_id": 0, "order_id": 1, "status": 1, "day": 1, "delivered_at": 1, "backfilled": 1})
        print("  ", o)
    print("копия до правки:", path)
    return 0


sys.exit(asyncio.run(main()))
