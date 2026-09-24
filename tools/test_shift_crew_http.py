"""Смена и бригада через настоящие ручки — как их зовёт панель (24 сен 2026).

Слой поверх test_shift_rules: тот проверяет правила прямым вызовом, этот —
весь путь целиком: маршрут, подпись оператора, подпись водителя, JSON. Ловит
то, чего не видно при прямом вызове: не зарегистрированную ручку, чужую
подпись, отсутствие OPTIONS (панель открывается не всегда с адреса API).

    python3 tools/test_shift_crew_http.py
"""
import asyncio, hashlib, hmac, json, os, sys, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ.setdefault("OPERATOR_IDS", "7001,7002")
import logging; logging.disable(logging.CRITICAL)
from aiohttp import web                                          # noqa: E402
from aiohttp.test_utils import TestClient, TestServer            # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402
import db, config_staff as staff                                 # noqa: E402
import driver_routes as DR, operator_routes as OP                # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-24"
TOKEN = "123456:TEST"
OP.OPERATOR_BOT_TOKEN = TOKEN
OP.OPERATOR_IDS = [7001, 7002]
OP.TEST_OPERATOR_IDS = []
DR.DRIVER_BOT_TOKEN = TOKEN
РЕЕСТР = {101: {"name": "Худоба", "district": "jvc"}, 102: {"name": "Фарух", "district": "jvc"},
          103: {"name": "Улетел", "district": "jvc"}}
ЛЮДИ = [{"_id": "Худоба", "work": [{"from": "2026-01-01", "to": ""}]},
        {"_id": "Фарух", "work": [{"from": "2026-01-01", "to": ""}]},
        {"_id": "Улетел", "work": [{"from": "2026-01-01", "to": "2026-09-10"}]}]


def подпись(uid):
    pairs = {"auth_date": str(int(time.time())), "query_id": "q",
             "user": json.dumps({"id": uid, "first_name": "x"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def main():
    db._db = AsyncMongoMockClient()["ambar_shift_http"]
    await db._db.fin_people.insert_many([dict(x) for x in ЛЮДИ])
    async def nosync(*a, **k): return None
    staff.sync = nosync
    staff.driver_by_tg = lambda uid: dict(РЕЕСТР[uid]) if uid in РЕЕСТР else None
    staff.test_driver = lambda uid, force=False: None
    staff.DISTRICT_DRIVERS = {"jvc": ["Худоба", "Фарух", "Улетел"]}
    staff.OPERATOR_DISTRICTS = {"Умар": ["jvc"]}
    OP._staff_mod = staff
    OP._biz_date = lambda *a, **k: datetime.strptime(ДЕНЬ, "%Y-%m-%d").date()
    DR._biz_day = lambda *a, **k: ДЕНЬ
    async def districts(): return [{"id": "jvc", "code": "B1", "name": "JVC", "operator": "Умар"}]
    OP._fresh_districts = districts
    OP._people_for = lambda req, ds: {"Умар": ["jvc"]}
    OP._scope = lambda people, who, ds: {"jvc"} if who == "Умар" else set()
    async def geo(me, since=None): return {"ok": True, "left_min": 60}
    DR._geo_for = geo
    async def none(*a, **k): return None
    DR._after_close = none
    OP._tell_crew = none
    OP._here_drop()

    app = web.Application()
    OP.setup(app); DR.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def оп(method, path, body=None, uid=7001):
            r = await cl.request(method, path, json=body,
                                 headers={"Authorization": "tma " + подпись(uid)})
            return r.status, (await r.json() if r.content_type == "application/json" else await r.text())
        async def вод(uid, method, path, body=None):
            r = await cl.request(method, path, json=body,
                                 headers={"Authorization": "tma " + подпись(uid)})
            return r.status, (await r.json() if r.content_type == "application/json" else await r.text())

        print("── права и маршруты ────────────────────────────────────────────")
        r = await cl.post("/api/operator/shift/crew", json={})
        eq("без подписи — 401", r.status, 401)
        r = await cl.options("/api/operator/shift/crew",
                             headers={"Origin": "https://web.telegram.org",
                                      "Access-Control-Request-Method": "POST"})
        eq("предполётный OPTIONS проходит", r.status, 200)
        st, b = await оп("POST", "/api/operator/shift/crew", {"district": "jvc", "as": "Чужой"})
        eq("чужой оператор — 403", (st, b.get("error")), (403, "not_yours"))

        print("── открытие смены ──────────────────────────────────────────────")
        st, b = await оп("GET", "/api/operator/shift?as=Умар")
        eq("в списке нет улетевшего", b["districts"][0]["drivers"], ["Худоба", "Фарух"])
        st, b = await оп("POST", "/api/operator/shift/open",
                         {"district": "jvc", "as": "Умар", "drivers": {}})
        eq("без бригады — отказ", (st, b.get("error")), (400, "no_crew"))
        st, b = await вод(101, "POST", "/api/driver/shift/open")
        eq("водитель сам себя на смену не ставит", (st, b.get("error")), (409, "not_marked"))
        st, b = await оп("POST", "/api/operator/shift/open",
                         {"district": "jvc", "as": "Умар", "drivers": {"Худоба": True, "Улетел": True}})
        eq("с одним вышедшим — открыта", (st, b.get("ok")), (200, True))
        eq("улетевший в бригаду не попал", b.get("drivers"), {"Худоба": True})

        print("── водитель открывает свою ─────────────────────────────────────")
        st, b = await вод(101, "POST", "/api/driver/shift/open")
        eq("отмеченный открыл", st, 200)
        st, b = await вод(102, "POST", "/api/driver/shift/open")
        eq("неотмеченный — нет", (st, b.get("error")), (409, "not_marked"))

        print("── бригада на ходу ─────────────────────────────────────────────")
        st, b = await оп("POST", "/api/operator/shift/crew",
                         {"district": "jvc", "as": "Умар", "drivers": {"Фарух": True}})
        eq("добавили вышедшего позже", (st, b["drivers"]), (200, {"Худоба": True, "Фарух": True}))
        st, b = await вод(102, "POST", "/api/driver/shift/open")
        eq("и теперь он открывает смену", st, 200)
        st, b = await оп("POST", "/api/operator/shift/crew",
                         {"district": "jvc", "as": "Умар", "drivers": {"Худоба": False}})
        eq("того, кто уже на смене, домой не отправить",
           (st, b.get("error"), b.get("drivers")), (409, "shift_open", ["Худоба"]))
        await db.save_driver_day(ДЕНЬ, "Худоба", {"shift_close_at": datetime.now(timezone.utc)})
        st, b = await оп("POST", "/api/operator/shift/crew",
                         {"district": "jvc", "as": "Умар", "drivers": {"Худоба": False}})
        eq("закрывшего — можно", (st, b["drivers"]), (200, {"Худоба": False, "Фарух": True}))
        st, b = await оп("POST", "/api/operator/shift/crew",
                         {"district": "jvc", "as": "Умар", "drivers": {"Улетел": True}})
        eq("улетевшего не добавить", (st, b.get("changed")), (200, []))
        d = await db.get_driver_day(ДЕНЬ, "Худоба")
        eq("день Худобы стал выходным", d.get("working"), False)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
