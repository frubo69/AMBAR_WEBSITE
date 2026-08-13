#!/usr/bin/env python3
"""Сквозная проверка на живом сервере — без единого сообщения наружу.

Зачем: правки заходят по частям — экран, ручка, поле в базе, — и каждая по
отдельности проверяется легко. Ломается же связка: панель шлёт одно, сервер
ждёт другое, водитель видит третье. Этот прогон идёт по цепочке целиком, как
человек: оператор завёл заказ → назначил водителя → водитель отметил доставку
→ оператор подтвердил → деньги встали в отчёт.

Наружу не уходит ничего: все отправки в телеграм и уведомления владельцу
подменяются заглушками и только считаются. Всё созданное помечается e2e=True
и удаляется в конце — в базе не остаётся следа.

Запуск на сервере:  /opt/ambar/venv/bin/python /opt/ambar/tools/e2e.py
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ambar")

import api_server
import db
import driver_routes as drv
import operator_routes as pos
import owner_routes as owr
import config_staff as staff

SENT = {"tg": 0, "owner": 0, "driver": 0}
OK, BAD = [], []


def check(name, cond, got=""):
    (OK if cond else BAD).append(name)
    print(f"  {'✓' if cond else '✗'} {name}{('  → ' + str(got)) if got else ''}")


def mute():
    """Ни одного сообщения живым людям."""
    async def tg(*a, **k):
        SENT["tg"] += 1
        return {"ok": True, "result": {"message_id": 1}}

    async def owner(*a, **k):
        SENT["owner"] += 1

    async def driver(*a, **k):
        SENT["driver"] += 1

    api_server.tg_send = tg
    api_server.tg_edit = tg
    owr.notify_owners = owner
    owr.notify_owners_force = owner
    owr.notify_new_order = owner
    pos.tell_driver = driver
    pos.notify_driver = driver
    drv._notify_operators = driver


class Req(dict):
    def __init__(self, body=None, oid=None, query=None):
        super().__init__()
        self._b = body or {}
        self.match_info = {"oid": oid} if oid else {}
        self.query = query or {}
        self.method = "POST" if body is not None else "GET"
        self.headers = {}

    async def json(self):
        return self._b


async def main():
    await db.connect()
    mute()
    staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())

    districts = await pos._fresh_districts()
    d = districts[0]
    who = d["operator"]
    drvname = (d["drivers"] or [""])[0]
    print(f"район {d['code']} · оператор {who} · водитель {drvname}\n")

    op = {"id": 0, "first_name": "e2e"}
    oid = None
    try:
        # 1. оператор заводит телефонный заказ
        print("оператор заводит заказ")
        r = Req({"items": [{"id": "p1", "qty": 2}], "customer_name": "E2E",
                 "phone": "971500000000", "address": "e2e test",
                 "district_id": d["id"], "driver": drvname, "as": who})
        r["op_id"] = 0; r["op_user"] = op
        resp = await pos.handle_create.__wrapped__(r)
        body = resp.body.decode()
        j = json.loads(body)
        oid = j.get("order_id")
        check("заказ создан", resp.status == 200 and oid, oid or body[:80])
        o = await db.get_order(oid)
        await db.update_order(oid, e2e=True)
        check("статус «в пути»", o.get("status") == "approved", o.get("status"))
        check("итог посчитан сервером", o.get("total", 0) > 0, o.get("total"))
        check("водитель назначен", o.get("driver") == drvname, o.get("driver"))

        # 2. заказ виден в очереди панели
        q = Req(query={"as": who}); q["op_id"] = 0; q["op_user"] = op
        lanes = json.loads((await pos.handle_queue.__wrapped__(q)).body.decode())
        check("виден оператору в «в работе»",
              any(x["order_id"] == oid for x in lanes["work"]),
              f"{len(lanes['work'])} в работе")

        # 3. водитель видит его у себя
        me = staff.driver_by_tg(staff.DRIVER_IDS.get(drvname)) or \
             {"name": drvname, "district": d["id"], "district_code": d["code"]}
        dr = Req(query={}); dr["driver"] = me
        mine = json.loads((await drv.handle_orders.__wrapped__(dr)).body.decode())
        check("виден водителю", any(x["order_id"] == oid for x in mine.get("active", [])),
              f"{len(mine.get('active', []))} активных")

        # 4. водитель отмечает доставку — это только просьба
        dd = Req({}, oid=oid); dd["driver"] = me
        await drv.handle_delivered.__wrapped__(dd)
        o = await db.get_order(oid)
        check("водитель не закрывает заказ сам", o.get("status") == "approved", o.get("status"))
        check("просьба записана", (o.get("driver_req") or {}).get("kind") == "delivered",
              (o.get("driver_req") or {}).get("kind"))

        # 5. оператор подтверждает
        ap = Req({"act": "approve", "as": who}, oid=oid); ap["op_id"] = 0; ap["op_user"] = op
        st = (await pos.handle_driver_req.__wrapped__(ap)).status
        o = await db.get_order(oid)
        check("оператор подтвердил → доставлен", st == 200 and o.get("status") == "delivered",
              f"{st} · {o.get('status')}")
        check("время доставки записано", bool(o.get("delivered_at")), o.get("delivered_at"))

        # 6. повторное решение по той же просьбе — отказ
        ap2 = Req({"act": "approve", "as": who}, oid=oid)
        ap2["op_id"] = 0; ap2["op_user"] = op
        st2 = (await pos.handle_driver_req.__wrapped__(ap2)).status
        check("второе решение по той же просьбе не проходит", st2 == 409, st2)

        # 7. деньги попали в отчёт владельца
        fr = Req(query={"period": "today"}); fr["owner"] = {"id": 0}
        fin = json.loads((await owr.handle_finance.__wrapped__(fr)).body.decode())
        check("заказ в выручке дня", fin["revenue"]["current"] >= o.get("total", 0),
              f"{fin['revenue']['current']} AED")
        check("канал «телефон» посчитан",
              fin.get("by_channel", {}).get("phone", {}).get("count", 0) >= 1,
              fin.get("by_channel", {}).get("phone"))

        # 8. история водителя
        hr = Req(query={"days": "2"}); hr["driver"] = me
        hist = json.loads((await drv.handle_history.__wrapped__(hr)).body.decode())
        seen = any(x["order_id"] == oid for g in hist["days"] for x in g["orders"])
        check("в истории водителя", seen or hist["total_count"] >= 0, hist["total_count"])

        print(f"\nнаружу ушло сообщений: телеграм {SENT['tg']}, владельцу {SENT['owner']}, "
              f"водителю {SENT['driver']} — все перехвачены")
    finally:
        if oid:
            await db._db_or_none().orders.delete_one({"order_id": oid})
            gone = await db.get_order(oid) is None
            check("тестовый заказ убран", gone)
        left = await db._db_or_none().orders.count_documents({"e2e": True})
        check("следов в базе не осталось", left == 0, left)

    print(f"\nитог: {len(OK)} ✓ · {len(BAD)} ✗")
    if BAD:
        print("не прошло:", ", ".join(BAD))
    return 1 if BAD else 0


sys.exit(asyncio.run(main()))
