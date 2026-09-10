#!/usr/bin/env python3
"""Пересчитать клиентам «доставлено» и «потрачено» по самим заказам.

Счётчики orders_done / total_spent на клиенте прибавлял только бот оператора;
заказы, закрытые из приложения оператора, в них не попадали. Теперь плюс
ставится из любой двери, а старые заказы надо досчитать один раз.

Считает по заказам со статусом delivered (тестовые не в счёт), сравнивает с
тем, что записано у клиента, и печатает расхождения. Пишет в базу ТОЛЬКО с
--apply: выставляет orders_done, total_spent и ставит на доставленных заказах
отметку stats_counted, чтобы возврат из доставленных вычитал ровно то, что
прибавлено.

Запуск на сервере:
  /opt/ambar/venv/bin/python /opt/ambar/tools/recount_customers.py          # только показать
  /opt/ambar/venv/bin/python /opt/ambar/tools/recount_customers.py --apply  # записать
"""
import asyncio, os, sys
sys.path.insert(0, "/opt/ambar")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:                                        # noqa: BLE001
    pass
import db


async def compute() -> dict:
    """{telegram_id: {"orders_done": n, "total_spent": aed, "oids": [...]}}"""
    d = db._db_or_none()
    out = {}
    cur = d.orders.find({"status": "delivered", "test": {"$ne": True}},
                        {"_id": 0, "order_id": 1, "customer_id": 1, "total": 1})
    async for o in cur:
        try:
            cid = int(o.get("customer_id") or 0)
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        r = out.setdefault(cid, {"orders_done": 0, "total_spent": 0, "oids": []})
        r["orders_done"] += 1
        r["total_spent"] += int(o.get("total") or 0)
        if o.get("order_id"):
            r["oids"].append(o["order_id"])
    return out


async def main(apply: bool):
    await db.connect()
    d = db._db_or_none()
    if d is None:
        print("нет базы"); return 1
    want = await compute()
    users = {u["telegram_id"]: u async for u in d.users.find(
        {}, {"_id": 0, "telegram_id": 1, "orders_done": 1, "total_spent": 1, "first_name": 1})}
    diffs = []
    for cid, w in want.items():
        u = users.get(cid)
        if not u:
            continue
        have = (int(u.get("orders_done") or 0), int(u.get("total_spent") or 0))
        if have != (w["orders_done"], w["total_spent"]):
            diffs.append((cid, u.get("first_name", ""), have, (w["orders_done"], w["total_spent"]), w["oids"]))
    # у кого доставок нет, а счётчики не нули — тоже расхождение
    for cid, u in users.items():
        if cid in want:
            continue
        have = (int(u.get("orders_done") or 0), int(u.get("total_spent") or 0))
        if have != (0, 0):
            diffs.append((cid, u.get("first_name", ""), have, (0, 0), []))
    print(f"клиентов с доставками: {len(want)} · расхождений: {len(diffs)}")
    for cid, name, have, w, _ in diffs:
        print(f"  {name or '—'}: было {have[0]} зак / {have[1]} AED → станет {w[0]} зак / {w[1]} AED")
    if not apply:
        print("\nбез --apply ничего не записано")
        return 0
    n = 0
    for cid, _, _, w, oids in diffs:
        await d.users.update_one({"telegram_id": cid},
                                 {"$set": {"orders_done": w[0], "total_spent": w[1]}})
        n += 1
    m = await d.orders.update_many({"status": "delivered", "test": {"$ne": True},
                                    "stats_counted": {"$ne": True}},
                                   {"$set": {"stats_counted": True}})
    print(f"записано: клиентов {n}, отметок на заказах {m.modified_count}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
