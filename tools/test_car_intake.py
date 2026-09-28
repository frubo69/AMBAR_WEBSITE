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

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

РУБЕЖ = "2026-09-29"
ci.FROM_DAY = РУБЕЖ
ДЕНЬ = "2026-09-30"
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


async def зови(h, method="GET", body=None):
    r = make_mocked_request(method, "/x", headers={"Authorization": "tma x"})
    if body is not None:
        async def js(): return body
        r.json = js
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
    rec = await db.car_intake_get("Новичок", РУБЕЖ)
    eq("запись на месте", (rec["km"], rec["plate"]), (84210, "97448"))
    eq("снимок лёг отдельно",
       len(await db.car_intake_photo(db.car_intake_id("Новичок", РУБЕЖ))) > 2000, True)
    код, тело = await зови(dr.handle_car_intake_post, "POST", {"km": 99999, "photo": КАДР})
    eq("второй раз тот же период не принимаем", (код, тело.get("error")), (409, "already"))

    print("── уехал и вернулся — приём заново ────────────────────────────")
    await db._db.fin_people.update_one(
        {"_id": "Новичок"},
        {"$set": {"work": [{"from": РУБЕЖ, "to": "2026-10-05"},
                           {"from": "2026-10-20", "to": ""}]}})
    await staff.sync_away("2026-10-21")
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
    eq("сначала требуем", (await ci.state(РЕМ))["need"], True)
    await db.car_intake_skip("Ремонтный", "2026-10-20", "владелец", "машина в ремонте")
    st = await ci.state(РЕМ)
    eq("пропустили — не требуем", st["need"], False)
    eq("но запись о пропуске есть",
       (st["done"] or {}).get("skipped"), True)

    print("── тест-водитель живёт вне «Зарплат» ──────────────────────────")
    eq("его не трогаем", (await ci.state({"name": "Тест-водитель", "test": True}))["need"], False)

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
