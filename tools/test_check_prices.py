"""Проверка бутылки: закупка и продажа в ответе скана (владелец, 19 сен 2026:
«когда проверку бутылки по скану qr кода делаешь, там внизу, где вся
информация, ещё писать закупочную цену и продажную»).

Через настоящие ручки STAR (aiohttp-сервер с qr_routes):
  • бутылка — закупка и продажа за бутылку, те же числа, что на экране
    «Закупочные цены» (/api/owner/stock/prices);
  • пиво — за коробку (unit 24), не за банку и не за полкоробки;
  • позиция без закупки — cost null, продажа есть;
  • ручная правка закупки видна сразу;
  • чужая бутылка и позиция, которой нет в каталоге, — без цен, скан проходит;
  • цены не прочитались — скан всё равно проходит, вердикт на месте;
  • в саму проверку цены не пишутся."""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, qr_routes as QR, stock_value as SV, owner_auth

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
ABS, BEER, NOCOST = "p1", "p31", "p24"
# NOCOST — позиция без закупочной цены. Привязывать проверку к конкретной
# дырке в листе нельзя: 25 сен 2026 пришёл новый прайс магазина, и дырок не
# осталось ни одной (Chivas 25Y, который стоял здесь, получил 975). Поэтому
# цену снимаем сами — проверяем ПОВЕДЕНИЕ «нет закупки → cost null», а не
# наличие дырки в прайсе.


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk_prices"]
    SV._COST.update(at=0.0, map={})
    now = datetime.now(timezone.utc)
    for code, pid in (("C-ABS", ABS), ("C-BEER", BEER), ("C-NOCOST", NOCOST), ("C-GONE", "p99999")):
        await db.qr_register(code, pid, pid, "jvc", 1, now)
    app = web.Application(); QR.setup(app); SV.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def own(method, path, body=None):
            r = await cl.request(method, path, json=body, headers={"Authorization": "tma 1"})
            return r.status, await r.json()
        _, prices = await own("GET", "/api/owner/stock/prices")
        P = {r["id"]: r for r in prices["rows"]}
        _, st = await own("POST", "/api/owner/qr/check/start", {"driver": "", "as": "STAR"})
        cid = st["check_id"]
        scan = lambda code: own("POST", "/api/owner/qr/check/scan", {"check_id": cid, "code": code})

        s, r = await scan("C-ABS")
        eq("бутылка: скан прошёл", (s, r["ok"], r["verdict"]), (200, True, "ok"))
        eq("бутылка: за бутылку", r.get("unit"), 1)
        eq("бутылка: закупка как на экране цен", r.get("cost"), P[ABS]["cost"])
        eq("бутылка: продажа как на экране цен", r.get("price"), P[ABS]["price_app"])
        eq("бутылка: закупка есть и меньше продажи", bool(r.get("cost")) and r["cost"] < r["price"], True)

        s, r = await scan("C-BEER")
        eq("пиво: за коробку", r.get("unit"), 24)
        eq("пиво: закупка за коробку", r.get("cost"), P[BEER]["cost"])
        eq("пиво: продажа за коробку (24)", r.get("price"), P[BEER]["price_app"])

        import config_cost
        _была = config_cost.COST.pop(NOCOST, None)
        SV._COST["at"] = 0.0
        s, r = await scan("C-NOCOST")
        eq("без закупки: cost null, продажа есть", (r.get("cost", "нет"), bool(r.get("price"))), (None, True))
        if _была is not None:
            config_cost.COST[NOCOST] = _была

        await db.cost_override_set(NOCOST, 88.5, "STAR"); SV._COST["at"] = 0.0
        s, r = await scan("C-NOCOST")
        eq("ручная закупка видна сразу", r.get("cost"), 88.5)

        s, r = await scan("C-ALIEN")
        eq("чужая: скан прошёл без цен", (s, r["verdict"], "price" in r, "cost" in r), (200, "alien", False, False))
        s, r = await scan("C-GONE")
        eq("нет в каталоге: скан прошёл без цен", (s, r["ok"], "price" in r), (200, True, False))

        real = SV.unit_money
        async def boom(pid): raise RuntimeError("каталог не прочитался")
        SV.unit_money = boom
        await db.qr_register("C-ABS2", ABS, ABS, "jvc", 1, now)
        s, r = await scan("C-ABS2")
        eq("цены упали — скан прошёл", (s, r["ok"], r["verdict"], "price" in r), (200, True, "ok", False))
        SV.unit_money = real

        chk = await db.qr_check_get(cid)
        eq("в проверку цены не пишутся",
           any(k in i for i in chk.get("items", []) for k in ("price", "cost", "unit")), False)
    print("\nвсё прошло" if not FAIL else f"\nНЕ ПРОШЛИ: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
