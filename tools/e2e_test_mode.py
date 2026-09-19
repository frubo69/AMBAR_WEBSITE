#!/usr/bin/env python3
"""Тест-режим на живом сервере — без единого сообщения наружу.

Создаёт тест-заказ (test: True) через тот же код, что и приложение, и
проверяет, что его не видит никто, кроме тест-аккаунтов: очередь настоящего
оператора, приложение настоящего водителя, финансы и склад считают без него;
тест-оператор и тест-водитель его видят. В конце заказ удаляется.

Запуск на сервере:  /opt/ambar/venv/bin/python /opt/ambar/tools/e2e_test_mode.py
"""
import asyncio, json, sys
sys.path.insert(0, "/opt/ambar")
import config, config_staff as staff, db, api_server, op_route
import driver_routes as drv, operator_routes as pos, owner_routes as owr, finance_routes as fin, bizday

OK, BAD, SENT = [], [], {"n": 0}
def check(name, cond, got=""):
    (OK if cond else BAD).append(name)
    print(f"  {'✓' if cond else '✗'} {name}{('  → ' + str(got)) if got else ''}")

def mute():
    async def tg(*a, **k):
        SENT["n"] += 1; return {"ok": True, "result": {"message_id": 1}}
    async def none(*a, **k): return None
    api_server.tg_send = tg; api_server.tg_edit = tg
    owr.tg_send = tg
    owr.notify_owners = none; owr.notify_owners_force = none; owr.notify_new_order = none
    pos.tell_driver = none; pos.notify_driver = none; pos._customer_card = none; pos._refresh_cards = none
    drv._notify_operators = none

class Req(dict):
    def __init__(self, body=None, oid=None, query=None):
        super().__init__(); self._b = body or {}
        self.match_info = {"oid": oid} if oid else {}; self.query = query or {}
        self.method = "POST" if body is not None else "GET"; self.headers = {}
    async def json(self): return self._b

async def main():
    await db.connect(); mute()
    staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
    TD = config.TEST_DRIVER_NAME
    print(f"тест-оператор(ы): {len(config.TEST_OPERATOR_IDS)} · тест-водитель(и): {len(config.TEST_DRIVER_IDS)} "
          f"· флажок у {len(config.TEST_ORDER_IDS)} акк.")
    check("тест-роли заданы (пусто = владельцы)", bool(config.TEST_OPERATOR_IDS and config.TEST_DRIVER_IDS))
    both = config.TEST_OPERATOR_IDS & set(pos.OPERATOR_IDS)
    print(f"  · тестер и настоящий оператор в одном лице: {len(both)} акк. — тест-режим включается переключателем в панели")
    bothd = config.TEST_DRIVER_IDS & set(staff.DRIVER_BY_TG)
    print(f"  · тестер и настоящий водитель в одном лице: {len(bothd)} акк. — тест-режим включается в профиле приложения")
    tid = next(iter(config.TEST_DRIVER_IDS)); me_t = staff.test_driver(tid, force=True)
    check("test_driver() узнаёт тест-аккаунт", bool(me_t and me_t.get("test")), me_t and me_t["name"])
    day = bizday.biz_day()
    districts = await pos._fresh_districts(); d = districts[0]
    real_me = staff.driver_by_tg(staff.DRIVER_IDS.get((d["drivers"] or [""])[0])) or \
              {"name": (d["drivers"] or ["—"])[0], "district": d["id"], "district_code": d["code"]}
    before_fin = (await fin._sales([day]))[day]["gross"] if hasattr(fin, "_sales") else None
    oid = None
    try:
        # 1. тест-заказ через тот же код, что у приложения
        oid = api_server._new_order_id()
        src = {"items": [{"id": "p1", "name": "E2E", "qty": 1, "price": 10, "line_total": 10}],
               "phone": "+971500000000", "address": "e2e test", "office_id": d["id"], "lang": "ru"}
        await api_server._finalize_accepted_order(src, {"id": 0, "first_name": "e2e"}, oid, test=True)
        o = await db.get_order(oid)
        check("заказ создан с меткой test", bool(o) and o.get("test") is True, o and o.get("status"))
        await db.update_order(oid, e2e=True)
        # 2. фильтры базы
        check("get_all_orders() не видит", oid not in await db.get_all_orders())
        check("get_all_orders(test=True) видит", oid in await db.get_all_orders(test=True))
        since, until = bizday.window_utc(day, day)
        check("get_orders_in_range() не видит", not any(x["order_id"] == oid for x in await db.get_orders_in_range(since, until)))
        check("get_orders_in_range(test=True) видит", any(x["order_id"] == oid for x in await db.get_orders_in_range(since, until, test=True)))
        # 3. панель: настоящий оператор и тест-оператор
        who = next(p["name"] for p in pos._people(districts) if p["senior"])
        q = Req(query={"as": who}); q["op_id"] = 0; q["op_user"] = {"id": 0}; q["op_test"] = False
        lanes = json.loads((await pos.handle_queue.__wrapped__(q)).body.decode())
        rows = lambda L: [x for k in ("new", "work", "done") for x in L.get(k, [])]
        check("настоящий оператор: в очереди нет", not any(x.get("order_id") == oid for x in rows(lanes)))
        q = Req(query={"as": config.TEST_PERSON}); q["op_id"] = 0; q["op_user"] = {"id": 0}; q["op_test"] = True
        lanes = json.loads((await pos.handle_queue.__wrapped__(q)).body.decode())
        check("тест-оператор: в очереди «новые»", any(x["order_id"] == oid for x in lanes["new"]))
        pg = Req(query={}); pg["op_id"] = 0; pg["op_user"] = {"id": 0}; pg["op_test"] = True
        ping = json.loads((await pos.handle_ping.__wrapped__(pg)).body.decode())
        # Тест-водителей с 18 сен 2026 несколько (реестр, config_staff.test_driver_names):
        # важно не «ровно этот один», а что боевых имён в районах нет вовсе.
        тест_имена = staff.test_driver_names()
        чужие = sorted({d for x in ping["districts"] for d in x["drivers"] if d not in тест_имена})
        check("тест-оператору в районах только тест-водители",
              not чужие and all(x["drivers"] for x in ping["districts"]), чужие or "районы пусты")
        # 4. назначение тест-водителю и приложение водителя
        await db.update_order(oid, status="approved", driver=TD, day=day, confirmed_at=o["timestamp"])
        r = Req(query={}); r["driver"] = me_t
        mine = json.loads((await drv.handle_orders.__wrapped__(r)).body.decode())
        check("тест-водитель видит заказ", any(x["order_id"] == oid for x in mine.get("active", [])))
        await db.update_order(oid, driver=real_me["name"])
        r = Req(query={}); r["driver"] = real_me
        mine = json.loads((await drv.handle_orders.__wrapped__(r)).body.decode())
        check("настоящий водитель не видит даже со своим именем", not any(x["order_id"] == oid for x in mine.get("active", [])))
        await db.update_order(oid, driver=TD)
        # 5. доставка: деньги и склад без него
        await pos._close_delivered(oid, await db.get_order(oid), "e2e", TD)
        o = await db.get_order(oid)
        check("доставлен", o.get("status") == "delivered")
        check("счётчик клиента не тронут (stats_counted нет)", not o.get("stats_counted"))
        if before_fin is not None:
            after_fin = (await fin._sales([day]))[day]["gross"]
            check("выручка дня не изменилась", after_fin == before_fin, f"{before_fin} → {after_fin}")
        between = await db.orders_between(since, until)
        check("orders_between (финансы) не видит", not any(x["order_id"] == oid for x in between))
        sold = await db.sold_since(since)
        check("sold_since (склад) не видит", not any(x.get("items") and x["items"][0].get("name") == "E2E" for x in sold))
        print(f"\nнаружу ушло сообщений: {SENT['n']} — все перехвачены")
    finally:
        if oid:
            await db._db_or_none().orders.delete_one({"order_id": oid})
            check("тест-заказ убран", await db.get_order(oid) is None)
        left = await db._db_or_none().orders.count_documents({"e2e": True})
        check("следов в базе не осталось", left == 0, left)
    print(f"\nитог: {len(OK)} ✓ · {len(BAD)} ✗")
    if BAD: print("не прошло:", ", ".join(BAD))
    return 1 if BAD else 0

sys.exit(asyncio.run(main()))
