"""Переименование водителя во всей базе (tools/rename_driver.py): на копиях
записей того же вида, что на бою 19 сен 2026 (Бахадыр → Баха)."""
import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import rename_driver as RD
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
O, N = "Бахадыр", "Баха"
async def main():
    d = AsyncMongoMockClient()["ren"]
    await d.drivers.insert_one({"_id": "x1", "name": O, "district": "bbay", "telegram_id": 5})
    await d.drivers.insert_one({"_id": "x2", "name": "Бахадыров", "district": "jvc"})     # чужое имя с тем же началом
    await d.driver_days.insert_one({"_id": "dd1", "day": "2026-09-18", "driver": O,
                                    "extras": [{"id": "a", "by_driver": "Парвиз"}, {"id": "b", "by_driver": O}]})
    await d.orders.insert_one({"_id": "o1", "driver": O, "driver_msg_to": O, "address": "Бахадыр стрит 5"})
    await d.shift_opens.insert_one({"_id": "s1", "drivers": {O: True, "Парвиз": False}})
    await d.shift_opens.insert_one({"_id": "s2", "crew_shift": {O: {"open": True, "by": O}}})
    await d.driver_pos.insert_one({"_id": O, "lat": 1})
    await d.driver_tracks.insert_one({"_id": f"2026-09-18:{O}", "name": O, "pts": [1, 2]})
    await d.fin_pay_months.insert_one({"_id": f"2026-09|{O}", "name": O, "rate": 3000})
    await d.fin_people.insert_one({"_id": O, "role": "driver"})
    await d.owner_notifications.insert_one({"_id": "n1", "text": f"📍 {O}: выключил геопозицию", "meta": {"driver": O}})
    await d.move_orders.insert_one({"_id": "m1", "tasks": {"bbay": {"give": {"jvc": {"accepted_by": O}}, "driver": ""}}})
    await d.supplies.insert_one({"_id": "S1", "tasks": {"bbay": {"driver": O, "noscan_by": ""}}})
    await d.calls.insert_one({"_id": "c1", "to": O, "from": "STAR"})
    await d.cars.insert_one({"_id": "car1", "driver": O})

    dry = await RD.rename(d, O, N, apply=False)
    eq("просмотр ничего не меняет", (await d.drivers.find_one({"_id": "x1"}))["name"], O)
    eq("просмотр: сколько записей тронем («Бахадыров» — не наш)", dry["docs"], 14)
    eq("похожее имя и адрес — в отчёте, не трогаем", sorted(dry["left"]),
       ["drivers.name — имя внутри текста, не трогаю", "orders.address — имя внутри текста, не трогаю"])

    tmp = tempfile.mkdtemp()
    res = await RD.rename(d, O, N, apply=True, backup_dir=tmp)
    eq("копия записана до правок", bool(res["backup"]) and os.path.getsize(res["backup"]) > 0, True)
    eq("реестр", (await d.drivers.find_one({"_id": "x1"}))["name"], N)
    eq("чужое похожее имя не тронуто", (await d.drivers.find_one({"_id": "x2"}))["name"], "Бахадыров")
    dd = await d.driver_days.find_one({"_id": "dd1"})
    eq("день смены и расход в массиве", (dd["driver"], [x["by_driver"] for x in dd["extras"]]), (N, ["Парвиз", N]))
    o = await d.orders.find_one({"_id": "o1"})
    eq("заказ: водитель и адресат, адрес цел", (o["driver"], o["driver_msg_to"], o["address"]), (N, N, "Бахадыр стрит 5"))
    eq("экипаж — ключ словаря", (await d.shift_opens.find_one({"_id": "s1"}))["drivers"], {N: True, "Парвиз": False})
    eq("ключ с именем и имя внутри значения", (await d.shift_opens.find_one({"_id": "s2"}))["crew_shift"], {N: {"open": True, "by": N}})
    eq("точка: запись под новым ключом", (await d.driver_pos.find_one({"_id": N}) or {}).get("lat"), 1)
    eq("и под старым её нет", await d.driver_pos.find_one({"_id": O}), None)
    t = await d.driver_tracks.find_one({"_id": f"2026-09-18:{N}"}) or {}
    eq("трек: «день:имя» и имя внутри", (t.get("name"), t.get("pts")), (N, [1, 2]))
    f = await d.fin_pay_months.find_one({"_id": f"2026-09|{N}"}) or {}
    eq("зарплата месяца: «месяц|имя»", (f.get("name"), f.get("rate")), (N, 3000))
    eq("человек в «Финансах»", (await d.fin_people.find_one({"_id": N}) or {}).get("role"), "driver")
    n = await d.owner_notifications.find_one({"_id": "n1"})
    eq("уведомление: текст и meta", (n["text"], n["meta"]["driver"]), (f"📍 {N}: выключил геопозицию", N))
    eq("перемещение: кто принял", (await d.move_orders.find_one({"_id": "m1"}))["tasks"]["bbay"]["give"]["jvc"]["accepted_by"], N)
    eq("приёмка", (await d.supplies.find_one({"_id": "S1"}))["tasks"]["bbay"]["driver"], N)
    eq("звонок и машина", ((await d.calls.find_one({"_id": "c1"}))["to"], (await d.cars.find_one({"_id": "car1"}))["driver"]), (N, N))
    # живой процесс успел записать под новым именем — старую удаляем, новую не трогаем
    await d.driver_pos.insert_one({"_id": O, "lat": 7})
    await d.driver_pos.replace_one({"_id": N}, {"_id": N, "lat": 9})
    again = await RD.rename(d, O, N, apply=True, backup_dir=tmp)
    eq("повторный прогон: осталась новая точка", ((await d.driver_pos.find_one({"_id": N}))["lat"], await d.driver_pos.find_one({"_id": O})), (9, None))
    eq("после второго прогона старого имени нет", (await RD.rename(d, O, N, apply=False))["docs"], 0)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
