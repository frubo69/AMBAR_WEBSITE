"""Машина водителя (владелец, 18 сен 2026: «в „Кто на каком районе“ за
каждым водителем закрепить его автомобиль»). mongomock + настоящие ручки:
  • POST /api/owner/drivers/car кладёт {model, color, plate} в запись водителя;
  • /api/owner/staff отдаёт машину и в водителях района, и в телефонах;
  • пустые поля — машину снять; чужое имя — 404; без входа — 401;
  • перестановки и привязка телефона от машины не страдают."""
import asyncio, os, sys
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


async def main():
    db._db = AsyncMongoMockClient()["ambar_car"]
    await db.driver_add("Худоба", "jvc", 1)
    await db.driver_add("Алишер", "tecom", 1)
    await staff.sync(force=True)
    app = web.Application()
    app.router.add_get("/api/owner/staff", OR.handle_staff)
    app.router.add_post("/api/owner/drivers/car", OR.handle_drivers_car)
    async with TestClient(TestServer(app)) as cl:
        H = {"Authorization": "tma 1"}
        eq("без входа не пускает", (await cl.post("/api/owner/drivers/car", json={"name": "Худоба"})).status, 401)
        r = await cl.post("/api/owner/drivers/car", headers=H,
                          json={"name": "Худоба", "model": "  Hyundai   Elantra ", "color": "серый", "plate": "97448"})
        st = await r.json()
        drv = next(x for x in st["drivers"] if x["name"] == "Худоба")
        lnk = next(x for x in st["links"] if x["name"] == "Худоба")
        eq("машина у водителя района — пробелы подчищены",
           drv["car"], {"model": "Hyundai Elantra", "color": "серый", "plate": "97448"})
        eq("и в телефонах водителей", lnk["car"], drv["car"])
        eq("у водителя без машины — пусто", next(x for x in st["drivers"] if x["name"] == "Алишер")["car"], None)
        eq("район водителя не сдвинулся", drv["district"], "jvc")
        r = await cl.post("/api/owner/drivers/car", headers=H, json={"name": "Никто", "model": "x"})
        eq("чужое имя — 404", r.status, 404)
        r = await cl.post("/api/owner/drivers/car", headers=H, json={"name": "Худоба", "model": "", "color": "", "plate": ""})
        st = await r.json()
        eq("пустые поля — машину сняли",
           next(x for x in st["drivers"] if x["name"] == "Худоба")["car"], None)
        eq("в базе поля машины нет", "car" in (await db.get_driver_by_name("Худоба")), False)
        r = await cl.get("/api/owner/staff", headers=H)
        eq("экран «Кто на каком районе» открывается", r.status, 200)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
