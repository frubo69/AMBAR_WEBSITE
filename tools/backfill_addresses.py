#!/usr/bin/env python3
"""Собрать книги адресов клиентов из уже сделанных заказов.

Адреса никогда не сохранялись в профиль: приложение держало их в памяти
телефона, а в заказ уезжала копия строки. Поэтому в панели у любого клиента
было «нет сохранённых адресов» — даже у того, кто вчера сделал заказ.

Данные, впрочем, никуда не делись: каждый заказ несёт адрес, название, ссылку
на карту и координаты. Этот скрипт проходит по заказам от старых к новым и
складывает их в профили — так же, как это теперь делает сам заказ.

    python3 tools/backfill_addresses.py --dry     # посмотреть, ничего не меняя
    python3 tools/backfill_addresses.py           # заполнить
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/opt/ambar")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db                                                    # noqa: E402


async def run(dry: bool) -> None:
    await db.connect()
    d = db._db_or_none()
    orders = await d.orders.find(
        {"address": {"$nin": ["", "—", None]}},
        {"_id": 0, "customer_id": 1, "address": 1, "address_label": 1,
         "gmap_link": 1, "is_gps": 1, "location": 1, "office_id": 1,
         "office_name": 1, "timestamp": 1}).sort("timestamp", 1).to_list(20000)
    print(f"заказов с адресом: {len(orders)}")

    seen: dict = {}
    for o in orders:
        uid = o.get("customer_id")
        if not uid:
            continue
        addr = (o.get("address") or "").strip()
        if not addr or addr == "—":
            continue
        seen.setdefault(int(uid), []).append(o)

    print(f"клиентов с адресами: {len(seen)}")
    filled = skipped = 0
    for uid, rows in seen.items():
        u = await db.get_user(uid) or {}
        if u.get("addresses"):
            skipped += 1
            continue                      # книга уже есть — не трогаем
        if dry:
            filled += 1
            continue
        for o in rows:                    # от старых к новым: счётчик сойдётся
            loc = o.get("location") or {}
            await db.save_address(uid, {
                "address": (o.get("address") or "").strip(),
                "label": (o.get("address_label") or "").strip()[:80],
                "gmap_link": o.get("gmap_link") or "",
                "is_gps": bool(o.get("is_gps")),
                "lat": loc.get("lat", 0), "lon": loc.get("lon", 0),
                "office_id": o.get("office_id") or "",
                "office_name": o.get("office_name") or "",
                "used_at": str(o.get("timestamp") or ""),
                "from_orders": True,
            })
        filled += 1

    print(f"{'нашлось бы' if dry else 'заполнено'} профилей: {filled} · "
          f"уже были адреса: {skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.dry))


if __name__ == "__main__":
    main()
