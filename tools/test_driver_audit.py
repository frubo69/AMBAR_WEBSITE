"""Ревизия у водителя через настоящие ручки (владелец, 19 сен 2026: «можем
водителям тоже сделать кнопку ревизия? там, где у них раньше была кнопка
„переместить“, во вкладке „Товар“; но ревизия должна проходить исключительно
по их билдингу; по итогам ревизии отчёт старшему должен приходить и
операторам»).

aiohttp-сервер с driver_routes и stock_routes, initData водителя подписан как
у телеграма, вход STAR. Проверяется:
  • район — только свой: из подписи, запрос его не выбирает; у водителя
    другого района — своя ревизия; без района — «no_district»;
  • тест-водителю и без подписи — закрыто;
  • денег в ответах водителю нет (закупка, прайс, суммы недостачи);
  • проход общий со STAR: начатую водителем старший видит идущей;
  • вердикты сервера: наша, повтор, числится на другом районе, списана, не из
    реестра; пиво — полкоробки на код; отмена скана;
  • завершение: пересчёт записан, ревизия ждёт решения старшего, в ней
    «кем завершена» и комментарий; отчёт — старшему (с деньгами) и операторам
    района (без денег), по одному разу; повтор и скан после — «finished»;
  • два «Завершить» в одну секунду — один отчёт;
  • «Возобновить» у старшего снимает отметку водителя;
  • ручки STAR после переноса ядра — как были."""
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
import driver_routes as DR, owner_auth, driver_audit as DA
import owner_routes, op_route

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-19"; T0 = datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D
TOKEN = "123456:TEST"
DR.DRIVER_BOT_TOKEN = TOKEN
ROSTER = {101: {"name": "Худоба", "district": "jvc"}, 105: {"name": "Фарух", "district": "jvc"},
          102: {"name": "Парвиз", "district": "bbay"}, 104: {"name": "Тест", "district": "jvc", "test": True},
          106: {"name": "Без района", "district": ""}}
async def _nosync(*a, **k): return None
staff.sync = _nosync
staff.driver_by_tg = lambda uid: dict(ROSTER[uid]) if uid in ROSTER else None
staff.test_driver = lambda uid, force=False: None
owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)

SENT = {"owner": [], "op": []}
async def _owners(kind, text, **k): SENT["owner"].append((kind, text))
async def _ops(text, district="", **k): SENT["op"].append((district, text, k.get("parse_mode"), k.get("own_only")))
owner_routes.notify_owners = _owners
op_route.send = _ops


def init_data(uid):
    pairs = {"auth_date": str(int(time.time())), "query_id": "q",
             "user": json.dumps({"id": uid, "first_name": "x"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def money_keys(o, path=""):
    """Где в ответе водителю лежат деньги."""
    bad = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("loss", "due", "price", "aed", "cost", "short_aed", "amount"):
                bad.append(path + "." + k)
            bad += money_keys(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            bad += money_keys(v, f"{path}[{i}]")
    return bad


async def settle():
    for _ in range(20):
        if not DA._TELLS:
            return
        await asyncio.sleep(0.02)


async def main():
    db._db = AsyncMongoMockClient()["ambar_drv_audit"]; d = db._db
    # JVC: Absolut 5 бутылок (3 с кодами), Heineken 2 коробки (1 коробка кодами —
    # два кода по полкоробки); Бизнес Бей: Absolut 2.
    for oid, per in {"jvc": {"p1": 5, "p31": 2}, "bbay": {"p1": 2}, "silicon": {"p1": 1}}.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]), "actual": q,
                       "counted": True} for p, q in per.items()]})
    def code(cid, pid, name, oid, qty=1, status="active"):
        return d.qr_codes.insert_one({"_id": cid, "status": status, "product_id": pid, "product_name": name,
            "district": oid, "origin": oid, "src": "cover", "qty": qty, "at": T0})
    for i in range(3):
        await code(f"a{i}", "p1", "Absolut 1 ltr", "jvc")
    await code("h0", "p31", "Heineken 0.33 can", "jvc", 0.5)
    await code("h1", "p31", "Heineken 0.33 can", "jvc", 0.5)
    await code("bb0", "p1", "Absolut 1 ltr", "bbay")
    await code("bb1", "p1", "Absolut 1 ltr", "bbay")
    await code("wr0", "p1", "Absolut 1 ltr", "jvc", status="written")
    await code("si0", "p1", "Absolut 1 ltr", "silicon")
    SR.base_drop()

    app = web.Application()
    DR.setup(app); SR.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def drv(uid, method, path, body=None):
            h = {"Authorization": "tma " + init_data(uid)} if uid else {}
            r = await cl.request(method, path, json=body, headers=h)
            return r.status, (await r.json() if r.content_type == "application/json" else await r.text())
        async def own(method, path, body=None, params=None):
            r = await cl.request(method, path, json=body, params=params, headers={"Authorization": "tma 1"})
            return r.status, await r.json()

        print("── доступ и район ────────────────────────────────────────────────")
        eq("без подписи — 401", (await drv(0, "GET", "/api/driver/audit"))[0], 401)
        st, b = await drv(104, "GET", "/api/driver/audit")
        eq("тест-водителю ревизии нет", (st, b.get("error")), (403, "test_account"))
        st, b = await drv(106, "GET", "/api/driver/audit")
        eq("без района — no_district", (st, b.get("error")), (409, "no_district"))
        eq("без района — кнопки нет (brief none)", (await drv(106, "GET", "/api/driver/audit/brief"))[1].get("state"), "none")

        st, b = await drv(101, "GET", "/api/driver/audit?district=bbay")
        eq("район из подписи, не из запроса", (st, b["district"], b["district_code"]), (200, "jvc", "B1"))
        rows = {r["id"]: r for r in b["rows"]}
        eq("Absolut: с кодом 3, без кода 2, нашли 0", (rows["p1"]["coded"], rows["p1"]["noqr"], rows["p1"]["actual"]), (3, 2, 0))
        eq("Heineken: с кодом 1 коробка, без кода 1, коробками", (rows["p31"]["coded"], rows["p31"]["noqr"], rows["p31"]["unit"]), (1, 1, 24))
        eq("денег в листе водителя нет", money_keys(b), [])
        eq("не начата", b["audit"]["state"], "idle")
        eq("кнопка: idle", (await drv(101, "GET", "/api/driver/audit/brief"))[1].get("state"), "idle")

        print("── проход ───────────────────────────────────────────────────────")
        st, b = await drv(101, "POST", "/api/driver/audit/start", {"district": "bbay"})
        eq("начал Худоба — идёт, подписано именем", (b["ok"], b["audit"]["state"], b["audit"]["started_by"]), (True, "running", "Худоба"))
        st, sh = await own("GET", "/api/owner/stock/audit/sheet", params={"district": "jvc"})
        eq("старший в STAR видит ту же ревизию идущей", (sh["audit"]["state"], sh["audit"]["started_by"]), ("running", "Худоба"))
        eq("у Парвиза (B2) своя ревизия — не начата", (await drv(102, "GET", "/api/driver/audit"))[1]["audit"]["state"], "idle")

        scan = lambda uid, c: drv(uid, "POST", "/api/driver/audit/scan", {"code": c, "district": "bbay"})
        st, r = await scan(101, "a0")
        eq("наша", (r["ok"], r["new"], r["verdict"], r["count"], r["total"]), (True, True, "ok", 1, 1))
        eq("в ответе скана денег нет", money_keys(r), [])
        st, r = await scan(101, "a0")
        eq("повтор — не второй раз", (r["ok"], r["new"], r["total"]), (True, False, 1))
        st, r = await scan(105, "a1")
        eq("второй водитель района сканирует в тот же проход", (r["verdict"], r["count"], r["total"]), ("ok", 2, 2))
        st, r = await scan(101, "bb0")
        eq("бутылка Бизнес Бея на полке JVC — числится на B2", (r["verdict"], r["home_code"]), ("other", "B2"))
        st, r = await scan(101, "wr0")
        eq("списанная на полке", r["verdict"], "written")
        st, r = await scan(101, "ZZZ-alien")
        eq("код не из реестра", (r["verdict"], r["product_id"]), ("alien", ""))
        st, r = await scan(101, "h0")
        eq("пиво — полкоробки на код", (r["verdict"], r["count"], r["unit"]), ("ok", 0.5, 24))
        st, r = await scan(101, "a2")
        eq("третья своя Absolut", (r["verdict"], r["count"], r["total"]), ("ok", 5, 5.5))
        st, r = await scan(102, "bb1")
        eq("скан Парвиза уходит в ревизию B2, не в JVC", (r["verdict"], r["total"]), ("ok", 1))
        eq("кнопка у Худобы: идёт, сканов 7 (повтор не пишется)",
           (await drv(101, "GET", "/api/driver/audit/brief"))[1], {"state": "running", "district_code": "B1", "district_name": "JVC", "scans": 7})

        st, r = await drv(101, "POST", "/api/driver/audit/undo", {"code": "a2"})
        eq("отмена скана", (r["ok"], r["total"]), (True, 4.5))
        st, b = await drv(101, "GET", "/api/driver/audit")
        rows = {x["id"]: x for x in b["rows"]}
        eq("после отмены Absolut: нашли 4 (две свои + чужая + списанная)", (rows["p1"]["actual"], rows["p1"]["coded"]), (4, 3))
        st, r = await scan(101, "a2")
        eq("отменённую можно отсканировать снова", (r["new"], r["count"]), (True, 5))

        print("── завершение ───────────────────────────────────────────────────")
        SENT["owner"].clear(); SENT["op"].clear()
        st, b = await drv(101, "POST", "/api/driver/audit/finish",
                          {"note": "Две бутылки <b>разбились</b>", "district": "bbay"})
        await settle()
        eq("завершено", (st, b["ok"]), (200, True))
        a = b["audit"]
        eq("ждёт решения старшего", a["state"], "pending")
        eq("кем завершена — водителем", (a["finished_kind"], a["finished_by"]), ("driver", "Худоба"))
        eq("недостача — по строкам в единицах, без денег",
           sorted((l["id"], l["qty"], l["unit"]) for l in a["short"]), [("p31", 0.5, 24)])
        eq("лишние: Absolut +2 — чужая с B2 и списанная", [(l["id"], l["qty"], l["homes"], l["written"]) for l in a["over"]],
           [("p1", 2, ["B2"], 1)])
        eq("коды не из реестра", a["alien"], 1)
        eq("денег в итоге водителю нет", money_keys(b), [])
        cnt = await d.stock_counts.find_one({"district": "jvc", "day": D, "audit_finished_at": {"$exists": True}})
        eq("пересчёт района записан", bool(cnt), True)
        doc = await db.audit_get("jvc", D)
        eq("в ревизии — комментарий водителя", doc.get("finished_note"), "Две бутылки <b>разбились</b>")
        eq("отчёт старшему — один, событие stock.audit", [k for k, _ in SENT["owner"]], ["stock.audit"])
        ot = SENT["owner"][0][1] if SENT["owner"] else ""
        for want in ("Ревизия водителя — B1 JVC", "Провёл Худоба", "Не хватает", "Heineken 0.33 can — 0,5 кор",
                     "Лишние", "числится на B2", "списана", "Не из реестра: 1 код",
                     "Где решать: STAR → Учёт → Чек-лист смены → «Решения по ревизиям»"):
            eq(f"старшему: «{want}»", want in ot, True)
        eq("старшему — сумма недостачи в AED", "AED" in ot, True)
        eq("оператору района — один раз, JVC, HTML, только свой оператор",
           [(x[0], x[2], x[3]) for x in SENT["op"]], [("jvc", "HTML", True)])
        opt = SENT["op"][0][1] if SENT["op"] else ""
        eq("операторам без денег", "AED" in opt, False)
        eq("операторам комментарий экранирован", "&lt;b&gt;разбились&lt;/b&gt;" in opt, True)
        eq("операторам: разбирает старший", "разбирает старший" in opt, True)

        st, b = await drv(101, "POST", "/api/driver/audit/finish", {})
        await settle()
        eq("второе завершение — finished, отчёта второго нет", (b["ok"], b["error"], len(SENT["owner"])), (False, "finished", 1))
        st, r = await scan(101, "a2")
        eq("скан после завершения — finished", (r["ok"], r["error"]), (False, "finished"))
        st, r = await drv(101, "POST", "/api/driver/audit/undo", {"code": "a0"})
        eq("отмена после завершения — finished", (r["ok"], r.get("error")), (False, "finished"))
        st, b = await drv(101, "POST", "/api/driver/audit/start", {})
        eq("начать заново сегодня — нельзя, итог на экране", (b["ok"], b["error"], b["audit"]["state"]), (False, "finished", "pending"))
        eq("кнопка: итог", (await drv(101, "GET", "/api/driver/audit/brief"))[1]["state"], "pending")

        print("── STAR после водителя ──────────────────────────────────────────")
        st, rep = await own("GET", "/api/owner/stock/audit/report", params={"district": "jvc"})
        eq("в отчёте STAR — водитель и комментарий",
           (rep["audit"]["finished_kind"], rep["audit"]["finished_by"], rep["audit"]["note"]),
           ("driver", "Худоба", "Две бутылки <b>разбились</b>"))
        st, r = await own("POST", "/api/owner/stock/audit/reopen", {"district": "jvc", "as": "STAR"})
        doc = await db.audit_get("jvc", D)
        eq("«Возобновить» снимает отметку водителя", (r["ok"], doc.get("finished_kind"), doc.get("finished_note")), (True, None, None))
        eq("у водителя снова идёт", (await drv(101, "GET", "/api/driver/audit"))[1]["audit"]["state"], "running")

        print("── два «Завершить» разом ────────────────────────────────────────")
        SENT["owner"].clear(); SENT["op"].clear()
        (s1, b1), (s2, b2) = await asyncio.gather(drv(101, "POST", "/api/driver/audit/finish", {}),
                                                   drv(105, "POST", "/api/driver/audit/finish", {}))
        await settle()
        eq("одно прошло, второе — finished", sorted([b1["ok"], b2["ok"]]), [False, True])
        eq("отчёт один — старшему и операторам", (len(SENT["owner"]), len(SENT["op"])), (1, 1))

        print("── ручки STAR как были ──────────────────────────────────────────")
        st, r = await own("POST", "/api/owner/stock/audit/start", {"district": "silicon", "as": "STAR"})
        eq("STAR: начать", (st, r["audit"]["state"], r["audit"]["started_by"]), (200, "running", "STAR"))
        st, r = await own("POST", "/api/owner/stock/audit/scan", {"district": "silicon", "code": "si0"})
        eq("STAR: скан", (st, r["verdict"], r["count"]), (200, "ok", 1))
        st, r = await own("GET", "/api/owner/stock/audit/scan", params={"district": "silicon"})
        eq("STAR: состояние прохода", (st, r["counts"]), (200, {"p1": 1}))
        st, r = await own("POST", "/api/owner/stock/audit/scan/undo", {"district": "silicon", "code": "si0"})
        eq("STAR: отмена", (st, r["ok"], r["total"]), (200, True, 0))
        await own("POST", "/api/owner/stock/audit/scan", {"district": "silicon", "code": "si0"})
        SENT["owner"].clear(); SENT["op"].clear()
        st, r = await own("POST", "/api/owner/stock/audit/finish", {"district": "silicon", "as": "STAR"})
        await settle()
        eq("STAR: завершить — сошлось и закрыто", (st, r["audit"]["state"], r["audit"]["finished_by"], r["audit"]["finished_kind"]),
           (200, "closed", "STAR", ""))
        eq("STAR сам себе отчёт не шлёт", (len(SENT["owner"]), len(SENT["op"])), (0, 0))
        st, r = await own("POST", "/api/owner/stock/audit/finish", {"district": "silicon"})
        eq("STAR: повтор — 409 finished", (st, r["error"]), (409, "finished"))
        st, r = await own("POST", "/api/owner/stock/audit/finish", {"district": "tecom"})
        eq("STAR: не начата — 409 not_started", (st, r["error"]), (409, "not_started"))

    print("── маршрут: только оператор района ──────────────────────────────")
    import importlib
    OR = importlib.reload(op_route)            # настоящий send/own_chats, без подмены из начала
    ops = {501: "Умар", 502: "Умар", 503: "Фарух", 504: "Джанабиль"}
    staff.operator_chats = lambda name: [t for t, n in ops.items() if n == name]
    hid = set()
    async def _hid(name): return name in hid
    OR.hidden = _hid
    eq("JVC — оба устройства Умара, и только они", sorted(c["chat_id"] for c in await OR.own_chats("jvc")), [501, 502])
    eq("без приставки «подмена»", {c["prefix"] for c in await OR.own_chats("jvc")}, {""})
    hid.add("Умар")
    eq("оператор в скрытом режиме — никому (и не соседу)", await OR.own_chats("jvc"), [])
    hid.clear()
    was = dict(staff.DISTRICT_OPERATOR)
    staff.DISTRICT_OPERATOR["jvc"] = "Джанабиль"           # перестановка на сегодня
    eq("перестановка: сегодняшний оператор района", [c["chat_id"] for c in await OR.own_chats("jvc")], [504])
    staff.DISTRICT_OPERATOR.clear(); staff.DISTRICT_OPERATOR.update(was)
    eq("неизвестный район — никому", await OR.own_chats("nowhere"), [])
    import api_server
    got = []
    async def _tg(token, chat, text, **k):
        got.append(chat); return {"ok": True, "result": {"message_id": 7}}
    api_server.tg_send = _tg; api_server.OPERATOR_BOT_TOKEN = "x"
    async def _reg(*a, **k): return None
    db.drv_msg_add = _reg
    r = await OR.send("текст", district="jvc", own_only=True)
    eq("send(own_only) — только устройства оператора района", sorted(got), [501, 502])

    print("\nвсё прошло" if not FAIL else f"\nНЕ ПРОШЛИ: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
