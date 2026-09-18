"""Машины водителей (владелец, 18 сен 2026: «в „Кто на каком районе“ за каждым
водителем закрепить его автомобиль», и следом: «надо же уметь и между
водителями перезакреплять машины»). mongomock + настоящие ручки:
  • машины, заведённые полем в записи водителя, переезжают в общий список
    один раз; удалённая потом не воскресает;
  • /api/owner/cars/assign: свободную — за водителем (его прежняя — в
    свободные), чужую — обменом (тот садится на прежнюю) или «забрать» (тот
    без машины); driver пустой — снять, машина остаётся в списке; экран
    видел другого водителя — 409 changed и ничего не меняется;
  • /api/owner/cars/save: новая, новая сразу за водителем, правка данных;
    номер уже в списке — 409 dup_plate; /api/owner/cars/delete;
  • старая ручка /api/owner/drivers/car (STAR, открытый до обновления)
    работает поверх общего списка;
  • случайные перезакрепления тысячами: машина не пропадает и не двоится,
    у водителя не больше одной."""
import asyncio, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, owner_auth
import config_staff as staff
import owner_routes as OR

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
H = {"Authorization": "tma 1"}


def car_of(st, name):
    c = next(x for x in st["drivers"] if x["name"] == name)["car"]
    return c and (c["model"], c["plate"])


def holders(st):
    return {c["plate"]: c["driver"] for c in st["cars"]}


async def main():
    db._db = AsyncMongoMockClient()["ambar_car"]; d = db._db
    for n, o in (("Худоба", "jvc"), ("Фарух", "jvc"), ("Алишер", "tecom"), ("Даврон", "alguses")):
        await db.driver_add(n, o, 1)
    # Как заводили первые часы: полем car в записи водителя.
    for n, car in (("Худоба", {"model": "Hyundai Elantra", "color": "серый", "plate": "97448"}),
                   ("Фарух", {"model": "Hyundai Kona", "color": "серый", "plate": "65149"}),
                   ("Алишер", {"model": "Toyota RAV4", "color": "белый", "plate": "96315"})):
        await d.drivers.update_one({"name": n}, {"$set": {"car": car}})
    await staff.sync(force=True)
    app = web.Application()
    app.router.add_get("/api/owner/staff", OR.handle_staff)
    for p, h in (("/api/owner/drivers/car", OR.handle_drivers_car), ("/api/owner/cars/assign", OR.handle_cars_assign),
                 ("/api/owner/cars/save", OR.handle_cars_save), ("/api/owner/cars/delete", OR.handle_cars_delete)):
        app.router.add_post(p, h)
    async with TestClient(TestServer(app)) as cl:
        async def post(path, body, code=200):
            r = await cl.post(path, headers=H, json=body)
            j = await r.json()
            if r.status != code:
                eq(f"{path} {body} — ответ", (r.status, j.get("error")), (code, None))
            return j
        async def staff_now():
            return await (await cl.get("/api/owner/staff", headers=H)).json()
        def cid(st, plate):
            return next(c["id"] for c in st["cars"] if c["plate"] == plate)

        print("── перенос из записей водителей ───────────────────────────────")
        st = await staff_now()
        eq("три машины в общем списке", sorted(holders(st).items()),
           [("65149", "Фарух"), ("96315", "Алишер"), ("97448", "Худоба")])
        eq("у водителя района — его машина", car_of(st, "Худоба"), ("Hyundai Elantra", "97448"))
        eq("и в телефонах водителей", next(x for x in st["links"] if x["name"] == "Худоба")["car"]["plate"], "97448")
        eq("у Даврона машины нет", car_of(st, "Даврон"), None)
        eq("из записей водителей поле убрано", await d.drivers.count_documents({"car": {"$exists": True}}), 0)
        await staff_now()
        eq("повторное чтение ничего не удвоило", await d.cars.count_documents({}), 3)

        print("── обмен машинами ─────────────────────────────────────────────")
        st = await post("/api/owner/cars/assign", {"car": cid(st, "65149"), "driver": "Худоба", "mode": "swap",
                                                   "expect": "Фарух"})
        eq("Худоба сел на Kona, Фарух — на Elantra", (car_of(st, "Худоба"), car_of(st, "Фарух")),
           (("Hyundai Kona", "65149"), ("Hyundai Elantra", "97448")))

        print("── забрать у другого ──────────────────────────────────────────")
        st = await post("/api/owner/cars/assign", {"car": cid(st, "96315"), "driver": "Худоба", "mode": "take",
                                                   "expect": "Алишер"})
        eq("Худоба на RAV4, Алишер без машины, Kona свободна",
           (car_of(st, "Худоба"), car_of(st, "Алишер"), holders(st)["65149"]),
           (("Toyota RAV4", "96315"), None, ""))

        print("── экран видел другого водителя ───────────────────────────────")
        j = await post("/api/owner/cars/assign", {"car": cid(st, "97448"), "driver": "Даврон", "mode": "take",
                                                  "expect": "Худоба"}, 409)
        eq("409 changed и свежий штат, ничего не сдвинулось",
           (j["error"], holders(j["staff"])["97448"], car_of(j["staff"], "Даврон")), ("changed", "Фарух", None))

        print("── свободную — за водителем ───────────────────────────────────")
        st = await post("/api/owner/cars/assign", {"car": cid(st, "65149"), "driver": "Даврон", "expect": ""})
        eq("Даврон на свободной Kona", car_of(st, "Даврон"), ("Hyundai Kona", "65149"))

        print("── снять машину ───────────────────────────────────────────────")
        st = await post("/api/owner/cars/assign", {"car": cid(st, "65149"), "driver": "", "expect": "Даврон"})
        eq("Даврон без машины, Kona в списке свободной", (car_of(st, "Даврон"), holders(st)["65149"]), (None, ""))

        print("── новая машина, правка, номер-двойник, удаление ──────────────")
        st = await post("/api/owner/cars/save", {"model": "Toyota FJ", "color": "белый", "plate": "65 135",
                                                 "driver": "Даврон"})
        eq("новая — сразу за Давроном", car_of(st, "Даврон"), ("Toyota FJ", "65 135"))
        j = await post("/api/owner/cars/save", {"model": "Что-то", "plate": "65135"}, 409)
        eq("тот же номер без пробела — «уже в списке» и чья", (j["error"], j["car"]["driver"]), ("dup_plate", "Даврон"))
        j = await post("/api/owner/cars/save", {"model": "", "color": "синий", "plate": ""}, 400)
        eq("без модели и номера — нельзя", j["error"], "empty")
        st = await post("/api/owner/cars/save", {"id": cid(st, "65 135"), "model": "Toyota FJ Cruiser",
                                                 "color": "белый", "plate": "65135"})
        eq("правка данных — водитель тот же", car_of(st, "Даврон"), ("Toyota FJ Cruiser", "65135"))
        st = await post("/api/owner/cars/save", {"model": "Nissan Patrol", "color": "чёрный", "plate": "11111",
                                                 "driver": "Даврон"})
        eq("вторая новая за Давроном — FJ в свободные",
           (car_of(st, "Даврон"), holders(st)["65135"]), (("Nissan Patrol", "11111"), ""))
        st = await post("/api/owner/cars/delete", {"id": cid(st, "11111")})
        eq("удалили из списка — Даврон без машины", (car_of(st, "Даврон"), "11111" in holders(st)), (None, False))
        eq("удалённая не воскресает", "11111" in holders(await staff_now()), False)

        print("── старая ручка STAR поверх общего списка ─────────────────────")
        st = await post("/api/owner/drivers/car", {"name": "Алишер", "model": "  Mazda   6 ", "color": "серый",
                                                   "plate": "95293"})
        eq("новая машина за Алишером, пробелы подчищены", car_of(st, "Алишер"), ("Mazda 6", "95293"))
        st = await post("/api/owner/drivers/car", {"name": "Алишер", "model": "Mazda 6", "color": "серый",
                                                   "plate": "95293"})
        eq("то же ещё раз — не двоится", sum(1 for c in st["cars"] if c["plate"] == "95293"), 1)
        st = await post("/api/owner/drivers/car", {"name": "Алишер", "model": "", "color": "", "plate": ""})
        eq("пустые поля — снята, но осталась в списке", (car_of(st, "Алишер"), holders(st)["95293"]), (None, ""))

        print("── отказы ─────────────────────────────────────────────────────")
        eq("без входа не пускает", (await cl.post("/api/owner/cars/assign", json={})).status, 401)
        j = await post("/api/owner/cars/assign", {"car": "car_нет", "driver": "Худоба"}, 404)
        eq("нет такой машины — 404", j["error"], "unknown_car")
        j = await post("/api/owner/cars/assign", {"car": cid(st, "95293"), "driver": "Никто"}, 404)
        eq("нет такого водителя — 404", j["error"], "unknown_driver")
        j = await post("/api/owner/drivers/car", {"name": "Никто", "model": "x"}, 404)
        eq("старая ручка: чужое имя — 404", j["error"], "unknown_driver")

        print("── случайные перезакрепления ──────────────────────────────────")
        rnd = random.Random(7)
        names = ["Худоба", "Фарух", "Алишер", "Даврон"]
        steps = checks = 0
        for step in range(3000):
            st = await staff_now()
            cars = st["cars"]
            roll = rnd.random()
            if roll < 0.55 and cars:
                c = rnd.choice(cars)
                drv = rnd.choice(names + [""])
                exp = c["driver"] if rnd.random() < 0.9 else rnd.choice(names + [""])
                before = {x["id"]: x["driver"] for x in cars}
                mine_before = next((x["id"] for x in cars if x["driver"] == drv), None) if drv else None
                mode = rnd.choice(["swap", "take"])
                r = await cl.post("/api/owner/cars/assign", headers=H,
                                  json={"car": c["id"], "driver": drv, "mode": mode, "expect": exp})
                after = {x["id"]: x["driver"] for x in (await staff_now())["cars"]}
                if exp != c["driver"]:
                    checks += 1
                    if not (r.status == 409 and after == before):
                        eq(f"шаг {step}: чужое «видел» — ничего не меняет", (r.status, after == before), (409, True))
                        break
                elif drv != c["driver"]:
                    checks += 3
                    ok = r.status == 200 and after[c["id"]] == drv
                    if drv and mine_before and mine_before != c["id"]:
                        ok = ok and after[mine_before] == (c["driver"] if mode == "swap" else "")
                    ok = ok and all(after[k] == v for k, v in before.items() if k not in (c["id"], mine_before))
                    if not ok:
                        eq(f"шаг {step}: {c['plate']} → «{drv}» ({mode})", after, before)
                        break
            elif roll < 0.8:
                plate = str(rnd.randint(10000, 10030))
                r = await cl.post("/api/owner/cars/save", headers=H,
                                  json={"model": "Car", "plate": plate, "driver": rnd.choice(names + [""])})
                checks += 1
                if r.status not in (200, 409):
                    eq(f"шаг {step}: новая машина", r.status, 200); break
            elif cars:
                await cl.post("/api/owner/cars/delete", headers=H, json={"id": rnd.choice(cars)["id"]})
            raw = await d.cars.find({}).to_list(length=500)
            per = {}
            for x in raw:
                if x.get("driver"):
                    per[x["driver"]] = per.get(x["driver"], 0) + 1
            plates = [db.car_plate_key(x.get("plate")) for x in raw]
            checks += 2
            if any(v > 1 for v in per.values()) or len(plates) != len(set(plates)):
                eq(f"шаг {step}: у водителя одна машина, номера не двоятся", (per, len(plates) - len(set(plates))), "—")
                break
            steps += 1
        eq(f"шагов без нарушений ({checks} проверок)", steps, 3000)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
