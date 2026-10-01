"""Приём машины: пробег на въезде, без него на смену нельзя.

Владелец, 29 сен 2026: «водители, которые приезжают новенькие или с отпуска,
должны будут фотографировать пробег машины и вписывать его вручную, это будет
процедурой принятия автомобиля; без этого выйти на работу будет нельзя».
И там же: «к Муину и Фахридину не нужно сейчас это применять, это начнём со
следующих приехавших водителей».

  • период начался ДО рубежа — не требуем ничего (те, кто уже работает);
  • период начался с рубежа — требуем, и смену не открыть;
  • принял — открывается;
  • уехал и вернулся — новый период, приём заново;
  • пробег меньше прошлого не берём, нелепый прыжок переспрашиваем;
  • снимок обязателен: число без кадра ничем не подтверждено;
  • своей машины нет — принимает свободную, и она за ним закрепляется;
  • владелец может пропустить (машина в ремонте) — тогда пускает.

С 1 октября 2026 (владелец, 30 сен): «каждое первое число перед открытием
смены водитель точно так же должен фоткать пробег… каждый месяц и каждую смену
водителями авто, если например они поменялись машинами»:

  • до рубежа месячного правила старожила не трогаем;
  • первая смена месяца — показание, без него смену не открыть; в том же
    месяце второй раз не спрашиваем; новый месяц — снова;
  • поменялись машинами — снимают оба; вернули назад — снова;
  • показание меньше прошлого ПО ЭТОЙ МАШИНЕ (чьё бы оно ни было) не берём;
  • владелец гасит показание («переснять») — водитель снимает заново;
  • машины нет и свободных нет — пускаем, в STAR он «без машины»;
  • журнал в STAR: по машинам, с разницей и теми, кого ждём.

    python3 tools/test_car_intake.py
"""
import asyncio, base64, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp import web                                           # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, config_staff as staff, car_intake as ci                # noqa: E402
import driver_routes as dr                                        # noqa: E402
import owner_routes as orr, owner_auth                            # noqa: E402
owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
_sync = staff.sync

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

РУБЕЖ = "2026-09-29"
ci.FROM_DAY = РУБЕЖ
ci.MONTHLY_FROM = "2026-12-01"          # первая половина — старое правило, месячное выключено
ДЕНЬ = "2026-09-30"
день = {"d": ДЕНЬ}
ci.today = lambda: день["d"]
# Кадр: JPEG-заголовок и достаточный вес — сервер проверяет и то и другое.
КАДР = base64.b64encode(b"\xff\xd8" + b"o" * 3000).decode()

СТАРЫЙ = {"name": "Старожил", "district": "jvc", "district_code": "B1"}
НОВЫЙ = {"name": "Новичок", "district": "jvc", "district_code": "B1"}
БЕЗМАШИНЫ = {"name": "Безмашинный", "district": "jvc", "district_code": "B1"}
кто = {"я": НОВЫЙ}

dr._valid_init_data = lambda init, token: {"id": 1}
dr.staff.sync = lambda *a, **k: asyncio.sleep(0)
dr.staff.driver_by_tg = lambda uid: кто["я"]
dr._biz_day = lambda *a, **k: ДЕНЬ


async def зови(h, method="GET", body=None, path="/x", auth="tma x"):
    r = make_mocked_request(method, path, headers={"Authorization": auth})
    if body is not None:
        async def js(): return body
        r.json = js
    await asyncio.sleep(0.003)          # mongomock хранит время до миллисекунды
    resp = await h(r)
    return resp.status, (json.loads(resp.text) if resp.content_type == "application/json" else {})


async def main():
    db._db = AsyncMongoMockClient()["ambar_car"]
    await db._db.fin_people.insert_many([
        {"_id": "Старожил", "work": [{"from": "2026-09-01", "to": ""}]},      # до рубежа
        {"_id": "Новичок", "work": [{"from": РУБЕЖ, "to": ""}]},              # с рубежа
        {"_id": "Безмашинный", "work": [{"from": РУБЕЖ, "to": ""}]},
    ])
    await staff.sync_away(ДЕНЬ)
    моя = await db.car_add("Hyundai Elantra", "серый", "97448")
    свободная = await db.car_add("Kia Rio", "белый", "12345")
    await db.car_set_driver(моя, "Новичок")

    print("── кто уже работает — того не трогаем ─────────────────────────")
    кто["я"] = СТАРЫЙ
    st = await ci.state(СТАРЫЙ)
    eq("период начался до рубежа — приём не нужен", (st["need"], st["since"]), (False, ""))
    код, тело = await зови(dr.handle_car_intake)
    eq("и ручка говорит то же", (код, тело["need"]), (200, False))

    print("── новенький ──────────────────────────────────────────────────")
    кто["я"] = НОВЫЙ
    код, тело = await зови(dr.handle_car_intake)
    eq("приём нужен", (код, тело["need"], тело["since"]), (200, True, РУБЕЖ))
    eq("и машина названа", (тело["car"] or {}).get("plate"), "97448")

    # Смена: оператор отметил, гео в порядке — мешает только машина.
    await db.save_driver_day(ДЕНЬ, "Новичок", {"working": True})
    dr._geo_for = lambda me: asyncio.sleep(0, {"ok": True, "left_min": 60})
    код, тело = await зови(dr.handle_shift_open, "POST", {})
    eq("НА СМЕНУ НЕ ПУСКАЮТ", (код, тело.get("error")), (409, "need_car"))

    print("── число и кадр ───────────────────────────────────────────────")
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 0, "photo": КАДР})
    eq("ноль — не пробег", (код, тело.get("error")), (400, "bad_km"))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 84210})
    eq("БЕЗ СНИМКА НЕ ПРИНИМАЕМ", (код, тело.get("error")), (400, "no_photo"))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": "84 210", "photo": КАДР})
    eq("принял, пробелы в числе не мешают", (код, тело.get("km")), (200, 84210))

    print("── теперь смена открывается ───────────────────────────────────")
    код, _ = await зови(dr.handle_shift_open, "POST", {})
    eq("пустили", код, 200)
    rec = await db.car_reading_mine("Новичок", моя)
    eq("запись на месте", (rec["km"], rec["plate"], rec["why"]), (84210, "97448", "new"))
    eq("снимок лёг отдельно", len(await db.car_intake_photo(rec["_id"])) > 2000, True)
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 99999, "photo": КАДР})
    eq("второй раз тот же период не принимаем", (код, тело.get("error")), (409, "already"))

    print("── уехал и вернулся — приём заново ────────────────────────────")
    await db._db.fin_people.update_one(
        {"_id": "Новичок"},
        {"$set": {"work": [{"from": РУБЕЖ, "to": "2026-10-05"},
                           {"from": "2026-10-20", "to": ""}]}})
    await staff.sync_away("2026-10-21"); день["d"] = "2026-10-21"
    st = await ci.state(НОВЫЙ)
    eq("НОВЫЙ ПЕРИОД — ТРЕБУЕМ СНОВА", (st["need"], st["since"]), (True, "2026-10-20"))
    eq("и прошлый пробег подставлен", int((st["last"] or {}).get("km") or 0), 84210)

    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 80000, "photo": КАДР})
    eq("одометр назад не ходит", (код, тело.get("error"), тело.get("last_km")),
       (400, "less", 84210))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 900000, "photo": КАДР})
    eq("нелепый прыжок — переспрашиваем", (код, тело.get("error")), (400, "jump"))
    код, тело = await зови(dr.handle_car_intake_post, "POST",
                           {"km": 900000, "photo": КАДР, "confirm": 1})
    eq("подтвердил — берём", (код, тело.get("km")), (200, 900000))

    print("── своей машины нет ───────────────────────────────────────────")
    кто["я"] = БЕЗМАШИНЫ
    код, тело = await зови(dr.handle_car_intake)
    eq("предлагают свободные", [c["plate"] for c in тело["free"]], ["12345"])
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 1000, "photo": КАДР})
    eq("без выбора машины не принять", (код, тело.get("error")), (409, "no_car"))
    код, тело = await зови(dr.handle_car_intake_post, "POST",
                           {"car_id": свободная, "km": 1000, "photo": КАДР})
    eq("принял свободную", (код, тело.get("plate")), (200, "12345"))
    машины = {c["_id"]: c.get("driver") for c in await db.cars_all()}
    eq("И ОНА ЗА НИМ ЗАКРЕПИЛАСЬ", машины[свободная], "Безмашинный")

    print("── владелец пропускает ────────────────────────────────────────")
    await db._db.fin_people.insert_one(
        {"_id": "Ремонтный", "work": [{"from": "2026-10-20", "to": ""}]})
    await staff.sync_away("2026-10-21")
    РЕМ = {"name": "Ремонтный", "district": "jvc", "district_code": "B1"}
    st = await ci.state(РЕМ)
    eq("машины нет и свободных нет — снимать нечего, пускаем", (st["need"], st["nocar"]), (False, True))
    ремонт = await db.car_add("Mazda", "серый", "95293")
    await db.car_set_driver(ремонт, "Ремонтный")
    for n in ("Старожил", "Новичок", "Безмашинный", "Ремонтный"):
        await db.driver_add(n, "jvc", 1)
    await _sync(force=True); await staff.sync_away("2026-10-21")
    eq("с машиной — требуем", (await ci.state(РЕМ))["need"], True)
    код, тело = await зови(orr.handle_car_intake_skip, "POST",
                           {"driver": "Ремонтный", "note": "машина в ремонте"}, auth="tma 1")
    eq("владелец пропустил", (код, тело.get("ok")), (200, True))
    st = await ci.state(РЕМ)
    eq("пропустили — не требуем", st["need"], False)
    eq("но запись о пропуске есть",
       (st["done"] or {}).get("skipped"), True)

    print("── тест-водитель живёт вне «Зарплат» ──────────────────────────")
    eq("его не трогаем", (await ci.state({"name": "Тест-водитель", "test": True}))["need"], False)

    print("── каждый месяц ───────────────────────────────────────────────")
    from datetime import datetime, timezone
    старая = await db.car_add("Toyota RAV4", "белый", "96315")
    await db.car_set_driver(старая, "Старожил")
    давно = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await db._db.cars.update_many({}, {"$set": {"at": давно}})       # закреплены до правила
    кто["я"] = СТАРЫЙ
    день["d"] = "2026-11-30"
    eq("до рубежа старожила не трогаем", (await ci.state(СТАРЫЙ))["need"], False)
    ci.MONTHLY_FROM = "2026-09-15"
    день["d"] = "2026-12-01"
    st = await ci.state(СТАРЫЙ)
    eq("ПЕРВОЕ ЧИСЛО — НУЖНО ПОКАЗАНИЕ", (st["need"], st["why"]), (True, "month"))
    await db.save_driver_day(ДЕНЬ, "Старожил", {"working": True})
    код, тело = await зови(dr.handle_shift_open, "POST", {})
    eq("смену не открыть", (код, тело.get("error"), тело.get("why")), (409, "need_car", "month"))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 50000, "photo": КАДР})
    eq("снял", (код, тело.get("km")), (200, 50000))
    eq("больше не спрашиваем", (await ci.state(СТАРЫЙ))["need"], False)
    день["d"] = "2026-12-20"
    eq("в том же месяце — не спрашиваем", (await ci.state(СТАРЫЙ))["need"], False)
    день["d"] = "2027-01-02"
    st = await ci.state(СТАРЫЙ)
    eq("НОВЫЙ МЕСЯЦ (вышел 2-го) — СНОВА", (st["need"], st["why"]), (True, "month"))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 53100, "photo": КАДР})
    eq("снял за январь", код, 200)

    print("── поменялись машинами ────────────────────────────────────────")
    кто["я"] = НОВЫЙ
    eq("новичку тоже пора — месяц", (await ci.state(НОВЫЙ))["why"], "month")
    код, _ = await зови(dr.handle_car_intake_post, "POST", {"km": 900100, "photo": КАДР})
    eq("снял", код, 200)
    await asyncio.sleep(0.01)
    eq("до обмена оба спокойны", ((await ci.state(СТАРЫЙ))["need"], (await ci.state(НОВЫЙ))["need"]),
       (False, False))
    await db.car_set_driver(старая, "Новичок"); await db.car_set_driver(моя, "Старожил")
    a, b = await ci.state(СТАРЫЙ), await ci.state(НОВЫЙ)
    eq("СНИМАЮТ ОБА", (a["need"], a["why"], b["need"], b["why"]), (True, "car", True, "car"))
    eq("и прошлый пробег — чужой, этой машины", int((b["last"] or {}).get("km") or 0), 53100)
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 53000, "photo": КАДР})
    eq("меньше, чем снял прежний водитель, — не берём", (код, тело.get("error"), тело.get("last_km")),
       (400, "less", 53100))
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 53150, "photo": КАДР})
    eq("снял", код, 200)
    eq("его отпустило, второго — нет", ((await ci.state(НОВЫЙ))["need"], (await ci.state(СТАРЫЙ))["need"]),
       (False, True))
    await asyncio.sleep(0.01)
    await db.car_set_driver(старая, "Старожил"); await db.car_set_driver(моя, "Новичок")
    eq("вернули назад — снова оба", ((await ci.state(НОВЫЙ))["need"], (await ci.state(СТАРЫЙ))["need"]),
       (True, True))
    кто["я"] = СТАРЫЙ
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 53200, "photo": КАДР})
    eq("старожил снял", код, 200)
    await asyncio.sleep(0.01)

    print("── владелец: журнал, снимок, переснять ────────────────────────")
    код, тело = await зови(orr.handle_car_intakes, path="/x?month=2027-01", auth="tma 1")
    eq("журнал отдаётся", (код, тело["month"]), (200, "2027-01"))
    m = {c["plate"]: c for c in тело["cars"]}
    eq("по машине — последний пробег и кто за ней", (m["96315"]["km"], m["96315"]["driver"], m["96315"]["need"]),
       (53200, "Старожил", False))
    eq("ждём того, кто не снял", (m["97448"]["driver"], m["97448"]["need"], m["97448"]["why"]),
       ("Новичок", True, "car"))
    rows = m["96315"]["rows"]
    eq("показания месяца, свежее сверху, с разницей",
       [(r["driver"], r["km"], r.get("diff")) for r in rows],
       [("Старожил", 53200, 50), ("Новичок", 53150, 50), ("Старожил", 53100, 3100)])
    eq("числового id и координат в журнале нет", any(k in rows[0] for k in ("geo", "tg", "_id")), False)
    код, _ = await зови(orr.handle_car_intake_photo, path="/x?id=" + rows[0]["id"].replace(":", "%3A"), auth="tma 1")
    eq("снимок открывается", код, 200)
    код, _ = await зови(orr.handle_car_intakes, auth="tma x")
    eq("чужому журнал закрыт", код in (401, 403), True)
    код, тело = await зови(orr.handle_car_intake_void, "POST", {"id": rows[0]["id"]}, auth="tma 1")
    eq("погасили показание", (код, тело.get("ok")), (200, True))
    st = await ci.state(СТАРЫЙ)
    eq("ВОДИТЕЛЬ СНИМАЕТ ЗАНОВО", (st["need"], int((st["last"] or {}).get("km") or 0)), (True, 53150))
    код, тело = await зови(orr.handle_car_intakes, path="/x?month=2027-01", auth="tma 1")
    r0 = {c["plate"]: c for c in тело["cars"]}["96315"]
    eq("в журнале оно осталось зачёркнутым, пробег машины — прежний",
       (r0["rows"][0]["void"], r0["km"], r0["need"]), (True, 53150, True))

    print("── сдача в ремонт ─────────────────────────────────────────────")
    # Владелец, 1 окт 2026: «перед сдачей водитель так же фоткает одометр».
    кто["я"] = НОВЫЙ
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 900200, "photo": КАДР})   # снял своё, чтобы не мешало
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 900300})
    eq("без снимка не сдать", (код, тело.get("error")), (400, "no_photo"))
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 100, "photo": КАДР})
    eq("пробег меньше прошлого не берём", (код, тело.get("error")), (400, "less"))
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 900300, "photo": КАДР, "note": "стучит подвеска"})
    eq("сдал в ремонт", (код, тело.get("km"), bool(тело["shift"]["car_repair"])), (200, 900300, True))
    c = next(x for x in await db.cars_all() if x["_id"] == моя)
    eq("машина осталась за ним, на ней отметка", (c["driver"], c["repair"]["by"], c["repair"]["note"]),
       ("Новичок", "Новичок", "стучит подвеска"))
    rec = await db.car_reading_mine("Новичок", моя)
    eq("показание записано с причиной", (rec["km"], rec["why"], rec["note"]), (900300, "repair", "стучит подвеска"))
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 900310, "photo": КАДР})
    eq("второй раз сдать нельзя", (код, тело.get("error")), (409, "in_repair"))
    день["d"] = "2027-02-01"
    st = await ci.state(НОВЫЙ)
    eq("В РЕМОНТЕ — месячный пробег не спрашиваем, смену не держим", (st["need"], bool(st["repair"])), (False, True))
    код, тело = await зови(orr.handle_car_intakes, path="/x?month=2027-02", auth="tma 1")
    eq("старший видит «в ремонте»", {c["plate"]: bool(c["repair"]) for c in тело["cars"]}["97448"], True)
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 900350, "photo": КАДР, "back": True})
    eq("забрал из ремонта", (код, тело.get("km"), тело["shift"]["car_repair"]), (200, 900350, ""))
    eq("отметка снята", "repair" in next(x for x in await db.cars_all() if x["_id"] == моя), False)
    rec = await db.car_reading_mine("Новичок", моя)
    eq("и это показание закрывает месяц", (rec["why"], (await ci.state(НОВЫЙ))["need"]), ("repair_back", False))
    код, тело = await зови(dr.handle_car_repair, "POST", {"km": 900360, "photo": КАДР, "back": True})
    eq("забрать то, что не в ремонте, нельзя", (код, тело.get("error")), (409, "not_in_repair"))

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
