"""Перемещения через настоящие ручки — как их зовут приложения (18 сен 2026).

aiohttp-сервер с теми же маршрутами (driver_routes.setup, supply_routes.setup),
подписанный initData водителя, вход STAR. Ловит то, чего не видно при прямом
вызове функций: права (водитель, тест-водитель, без подписи), район из подписи
а не из запроса, сериализацию дат в JSON, маршруты новых кнопок
(«Начать перемещение», «Принял»), шаг «Перемещение» в смене.
"""
import asyncio, hashlib, hmac, json, os, sys, time
from datetime import datetime, timezone
from urllib.parse import urlencode
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR
import config_staff as staff
import driver_routes as DR, supply_routes as SUP, owner_auth

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"; T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D
TOKEN = "123456:TEST"
DR.DRIVER_BOT_TOKEN = TOKEN
ROSTER = {101: {"name": "Худоба", "district": "jvc"}, 102: {"name": "Парвиз", "district": "bbay"},
          103: {"name": "Бахадыр", "district": "bbay"}, 104: {"name": "Тест", "district": "jvc", "test": True}}
async def _nosync(*a, **k): return None
staff.sync = _nosync
staff.driver_by_tg = lambda uid: dict(ROSTER[uid]) if uid in ROSTER else None
staff.test_driver = lambda uid, force=False: None
owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)


def init_data(uid):
    """initData так, как его подписывает телеграм."""
    pairs = {"auth_date": str(int(time.time())), "query_id": "q",
             "user": json.dumps({"id": uid, "first_name": "x"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def main():
    db._db = AsyncMongoMockClient()["ambar_http"]; d = db._db
    for oid, per in {"jvc": {"p1": 0}, "bbay": {"p1": 3}}.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": 1, "actual": q, "counted": True}
                      for p, q in per.items()]})
    for i in range(3):
        await d.qr_codes.insert_one({"_id": f"v{i}", "status": "active", "product_id": "p1",
            "product_name": "Absolut 1 ltr", "district": "bbay", "origin": "bbay", "src": "cover",
            "qty": 1, "at": T0})
    await d.qr_codes.insert_one({"_id": "sx", "status": "active", "product_id": "p1",
        "product_name": "Absolut 1 ltr", "district": "silicon", "origin": "silicon", "src": "cover",
        "qty": 1, "at": T0})

    app = web.Application()
    DR.setup(app); SUP.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def drv(uid, method, path, body=None):
            h = {"Authorization": "tma " + init_data(uid)}
            r = await cl.request(method, path, json=body, headers=h)
            return r.status, (await r.json() if r.content_type == "application/json" else await r.text())
        async def own(method, path, body=None):
            r = await cl.request(method, path, json=body, headers={"Authorization": "tma 1"})
            return r.status, await r.json()

        print("── STAR: доска и заявка ───────────────────────────────────────────")
        st, b = await own("GET", "/api/owner/move/board")
        eq("доска у STAR — все районы, у позиции остаток по районам",
           (st, len(b["districts"]), next(p for p in b["products"] if p["id"] == "p1")["have"]["bbay"]), (200, 5, 3))
        st, r = await own("POST", "/api/owner/move/create",
                          {"by": "STAR", "rows": [{"from": "bbay", "to": "jvc", "id": "p1", "qty": 2}]})
        eq("заявка создана", (st, r["ok"], r["districts"]), (200, True, 1))
        mid = r["move_id"]
        st, x = await cl.get("/api/owner/move/board"), None
        eq("без входа STAR не пускает", st.status, 401)

        print("── водитель: права ────────────────────────────────────────────────")
        r = await cl.get("/api/driver/move")
        eq("без подписи — 401", r.status, 401)
        st, x = await drv(104, "GET", "/api/driver/move")
        eq("тест-водителю перемещения закрыты", (st, x.get("error")), (403, "test_account"))
        st, x = await drv(999, "GET", "/api/driver/move")
        eq("чужому — 403", st, 403)

        print("── обе стороны видят ──────────────────────────────────────────────")
        st, g = await drv(102, "GET", "/api/driver/move")
        eq("Бизнес Бей: «отдать в JVC»", (st, [(t["to_code"], t["status"], t["need"]) for t in g["give"]]),
           (200, [("B1", "wait", 2)]))
        st, t = await drv(101, "GET", "/api/driver/move")
        eq("JVC: «забрать из Бизнес Бея», серая", [(x_["from_code"], x_["status"]) for x_ in t["take"]], [("B2", "wait")])

        print("── кто что может ──────────────────────────────────────────────────")
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/scan", {"district": "jvc", "code": "v0"})
        eq("получатель сканирует — отказ словами", (st, x["verdict"]), (200, "giver_scans"))
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/start", {"district": "jvc"})
        eq("«Начать» за получателя нельзя", x.get("error"), "not_giver")
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/start", {"district": "jvc"})
        eq("«Начать перемещение»", (st, x["ok"], x["task"]["giver"], x["task"]["status"]), (200, True, "Парвиз", "live"))
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/scan", {"district": "jvc", "code": "sx"})
        eq("бутылку Силикона Бизнес Бей не отдаст — район берём из подписи",
           (x["verdict"], x.get("from_code")), ("other_district", "B3"))
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/accept", {"district": "jvc", "from": "bbay"})
        eq("принять раньше, чем отдали всё, нельзя", x.get("error"), "not_given")

        print("── передача ───────────────────────────────────────────────────────")
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/scan", {"district": "jvc", "code": "v0"})
        eq("скан: отдано 1 из 2", (st, x["ok"], x["task"]["got"], x["finished"]), (200, True, 1, False))
        st, x = await drv(103, "POST", f"/api/driver/move/{mid}/scan", {"district": "jvc", "code": "v1"})
        eq("второй водитель района продолжил и закрыл передачу",
           (x["ok"], x["task"]["giver"], x["task"]["status"], x["finished"]), (True, "Бахадыр", "given", True))
        st, g = await drv(102, "GET", "/api/driver/move")
        eq("у отдающего карточка ушла", g["give"], [])
        st, t = await drv(101, "GET", "/api/driver/move")
        eq("у получателя — активная", [(x_["status"], x_["got"]) for x_ in t["take"]], [("given", 2)])

        print("── смена: перемещение пока не держит (19 сен 2026) ─────────────────")
        async def _geo(me): return {"ok": True}
        DR._geo_for = _geo
        # Обязательные расходы отвечены («не было»): перед замком перемещений
        # сервер проверяет их — иначе отказ был бы про расходы, а не про нас.
        await db.save_driver_day(DR._biz_day(), "Худоба", {"working": True, "shift_open_at": datetime.now(timezone.utc),
                                                           "no_expense": {k: True for k in DR.MUST_ANSWER}})
        eq("замок перемещений выключен", DR.MOVES_HOLD_SHIFT, False)
        st, sh = await drv(101, "GET", "/api/driver/shift")
        eq("в смене JVC шага перемещения нет, закрыть можно",
           (st, sh["moves"], sh["can_close"]), (200, [], True))
        st, x = await drv(101, "POST", "/api/driver/shift/close")
        eq("сервер про перемещение не спрашивает (дальше — ждём оператора)", (st, x.get("error")), (409, "day_open"))
        # Замок цел — включили обратно, и он держит, как 18 сен.
        DR.MOVES_HOLD_SHIFT = True
        st, sh = await drv(101, "GET", "/api/driver/shift")
        eq("включённый замок: шаг «принять из B2», закрыть нельзя",
           (st, [(m["side"], m["code"], m["status"]) for m in sh["moves"]], sh["can_close"]),
           (200, [("take", "B2", "given")], False))
        st, x = await drv(101, "POST", "/api/driver/shift/close")
        eq("и сервер не закроет", (st, x.get("error")), (409, "moves_open"))
        DR.MOVES_HOLD_SHIFT = False

        print("── получатель сканирует (с 19 сен 2026) ───────────────────────────")
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/accept", {"district": "jvc", "from": "bbay"})
        eq("«Принял» без сканов — нельзя", (st, x.get("error")), (200, "scan_all"))
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/receive", {"district": "jvc", "from": "bbay", "code": "v0"})
        eq("сканировать за получателя нельзя — район из подписи", x.get("verdict"), "not_your_district")
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/receive", {"district": "jvc", "from": "bbay", "code": "v0"})
        eq("скан получателя: принято 1 из 2", (st, x["ok"], x["task"]["recv"], x["finished"]), (200, True, 1, False))
        st, t = await drv(101, "GET", "/api/driver/move")
        eq("в карточке получателя — принято 1, осталось 1", [(x_["recv"], x_["recv_left"]) for x_ in t["take"]], [(1, 1)])

        print("── «Принял» ───────────────────────────────────────────────────────")
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/accept", {"district": "jvc", "from": "bbay"})
        eq("за получателя не примешь — район из подписи", x.get("error"), "not_your_district")
        # «Не всё пришло»: вторую бутылку получатель не нашёл. Число руками
        # старого приложения («пришло 1») не берётся — пришло то, что отсканировал.
        st, x = await drv(101, "POST", f"/api/driver/move/{mid}/accept",
                          {"district": "jvc", "from": "bbay", "ok": False, "lines": [{"id": "p1", "got": 2}],
                           "note": "одна разбита"})
        eq("«Принял неровно»", (st, x["ok"], x["task"]["status"], x["task_done"]), (200, True, "diff", True))
        st, sh = await drv(101, "GET", "/api/driver/shift")
        eq("замок снят", sh["moves"], [])
        st, lv = await own("GET", "/api/owner/move/live")
        t0 = lv["tasks"][0]
        eq("STAR видит: принято неровно, кто отдавал, кто принял, что не сошлось",
           (t0["status"], t0["diff"], t0["givers"], t0["sources"][0]["accepted_by"],
            t0["sources"][0]["accept_lines"][0]["got"], t0["sources"][0]["accept_note"]),
           ("done", True, ["Бахадыр"], "Худоба", 1, "одна разбита"))
        st, x = await drv(102, "POST", f"/api/driver/move/{mid}/scan", {"district": "jvc", "code": "v2"})
        eq("в закрытую заявку не отсканировать", x["verdict"], "gone")
        tr = await d.stock_transfers.find({"by_kind": "move"}).to_list(length=10)
        eq("в книге переездов — два, на имена отдающих", sorted(x_["by_name"] for x_ in tr), ["Бахадыр", "Парвиз"])

        print("── STAR: «Взять на себя» район и скан по передаче ─────────────────")
        await d.qr_codes.insert_one({"_id": "v9", "status": "active", "product_id": "p1",
            "product_name": "Absolut 1 ltr", "district": "bbay", "origin": "bbay", "src": "cover",
            "qty": 1, "at": T0})
        st, r2 = await own("POST", "/api/owner/move/create",
                           {"by": "STAR", "rows": [{"from": "bbay", "to": "tecom", "id": "p1", "qty": 1}]})
        mid2 = r2["move_id"]
        st, x = await own("POST", "/api/owner/move/take", {"from": "bbay", "as": "STAR"})
        eq("взял на себя всё с Бизнес Бея", (st, x["ok"], x["took"]), (200, True, 1))
        st, g = await drv(102, "GET", "/api/driver/move")
        eq("у водителя Бизнес Бея карточки больше нет", g["give"], [])
        st, x = await own("POST", f"/api/owner/move/{mid2}/scan", {"district": "tecom", "from": "bbay", "code": "v9", "as": "STAR"})
        eq("старший отсканировал — отдано в Тиком", (st, x["ok"], x["to_code"], x["finished"]), (200, True, "B5", True))
        st, x = await own("POST", "/api/owner/move/drop", {"from": "bbay", "as": "STAR"})
        eq("снять с себя отданное целиком — нечего", (st, x["ok"]), (200, False))

        print("── STAR: «Взять на себя · в B5» — одна передача, остальное водителям ─")
        st, r3 = await own("POST", "/api/owner/move/create",
                           {"by": "STAR", "rows": [{"from": "bbay", "to": "tecom", "id": "p1", "qty": 1},
                                                   {"from": "bbay", "to": "silicon", "id": "p1", "qty": 1}]})
        eq("заявка из Бизнес Бея в два района", (st, r3.get("ok")), (200, True))
        mid3 = r3.get("move_id")
        st, x = await own("POST", "/api/owner/move/take", {"from": "bbay", "to": "tecom", "move_id": mid3, "as": "STAR"})
        eq("взял на себя только Тиком", (st, x["ok"], x["took"]), (200, True, 1))
        st, g = await drv(102, "GET", "/api/driver/move")
        eq("водителю Бизнес Бея осталась передача в Силикон", [y["to_code"] for y in g["give"]], ["B3"])
        st, x = await own("POST", "/api/owner/move/take", {"from": "bbay", "to": "tecom", "move_id": mid3, "as": "STAR-2"})
        eq("второй вход STAR — Тиком уже взят (409)", (st, x["ok"], x["error"], x["senior"]), (409, False, "taken", "STAR"))
        st, x = await own("POST", "/api/owner/move/drop", {"from": "bbay", "to": "tecom", "move_id": mid3, "as": "STAR"})
        eq("вернул Тиком водителям", (st, x["ok"], x["dropped"]), (200, True, 1))
        st, g = await drv(102, "GET", "/api/driver/move")
        eq("у водителя Бизнес Бея снова обе", sorted(y["to_code"] for y in g["give"]), ["B3", "B5"])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
