"""Хронология заявки (владелец, 3 ноя 2026: «отслеживать пошаговый статус
заявки… зайти, нажать и прям хронологически посмотреть, что во сколько
происходило»): журнал supply_log и ручка GET /api/owner/supply/{sid}/log на
mongomock.

Что проверяется:
  • шаги заявки дня — файл выслан (журнал), правка клетки, район снят,
    правки сняты (ручки stock_routes с подписью as) — лежат под днём заявки
    и попадают в ленту поставки, которая на неё отвечает; «файл собран»
    рядом с «выслан» не дублируется;
  • шаги поставки пишут сами ручки: взял район, приёмка начата, убрано,
    недовоз заявлен и решён, принят без сканирования, закрыт с недобором,
    назначен, отдан (старший снял), район отменён; первый и последний скан —
    из реестра кодов; у каждого шага заголовок, район кодом, кто именем;
  • лента по времени; шаг документа и та же запись журнала рядом — одна
    строка (взял, загрузка ответа);
  • поставка без единой записи журнала (до 3 ноя) собирается из документов:
    ответ загружен и заново, взял, сканы, район принят, поставка принята;
  • в ответе нет телеграм-id; неизвестная поставка — 404; журнал без базы
    молчит и действие не ломает.

    python3 tools/test_supply_log.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, supply_routes as sr, stock_routes as SR, supply_log as sl   # noqa: E402
from config_offices import OFFICE_CODES                           # noqa: E402

TODAY, ORDER_DAY = "2026-10-03", "2026-10-02"
NOW = datetime.now(timezone.utc)
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr",
         "p31": "Heineken 0.33 can"}
FAIL = []
N = [0]


def eq(имя, дали, ждём):
    if дали != ждём:
        FAIL.append(имя); print(f"  FAIL {имя}: {дали!r} != {ждём!r}")
    else:
        print(f"  ok   {имя}")


def ok(имя, cond, info=""):
    eq(имя, bool(cond), True) if not info or cond else eq(имя, info, True)


# mongomock: позиционный items.$ бьёт в первый элемент — те же условия по индексу
# (как в tools/test_intake_history.py).
async def _take(sid, district, product_id, room, now, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") != "open":
        return None
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) > room:
        return None
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": qty, f"items.{i}.scanned": 1,
                 f"tasks.{district}.scanned": 1, f"tasks.{district}.rev": 1},
        "$set": {f"tasks.{district}.last_at": now}})
    return await db.supply_get(sid)


async def _untake(sid, district, product_id, qty=1):
    s = await db.supply_get(sid)
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) < qty:
        return False
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": -qty, f"items.{i}.scanned": -1,
                 f"tasks.{district}.scanned": -1, f"tasks.{district}.undo": 1,
                 f"tasks.{district}.rev": 1}})
    return True


def code():
    N[0] += 1
    return f"h{N[0]:06d}"


async def add_supply(sid, day, at, plan, drivers, kind="main", base="", status="open", **extra):
    pids = sorted({p for o in plan for p in plan[o]})
    await db._db.supplies.insert_one({
        "_id": sid, "status": status, "at": at, "day": day, "kind": kind, "base": base, "buys": {},
        "items": [{"id": pid, "name": NAMES[pid], "qty": sum(plan[o].get(pid, 0) for o in plan),
                   "by_district": {o: plan[o][pid] for o in plan if pid in plan[o]},
                   "got": {o: 0 for o in plan if pid in plan[o]}} for pid in pids],
        "total_qty": sum(sum(plan[o].values()) for o in plan),
        "asked_qty": sum(sum(plan[o].values()) for o in plan), "gap_qty": 0,
        "tasks": {o: {"qty": sum(plan[o].values()), "driver": drivers.get(o, ""),
                      "driver_id": 777 if drivers.get(o) else 0,
                      "claimed_at": at if drivers.get(o) else None, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0, "undo": 0, "flags": []} for o in plan}, **extra})
    SR.base_drop()


# Монга хранит время до миллисекунды: шаги теста разносим, чтобы лента
# сортировалась по времени, а не по тому, что нашлось первым.
async def tick():
    await asyncio.sleep(0.003)


async def unhold(sid, o):
    await tick()
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who, c=None):
    await unhold(sid, o)
    r = await sr.task_scan(sid, o, pid, c or code(), who, 777, "", False)
    assert r.get("ok"), (sid, o, pid, r)
    return r


def raw(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def post(h, body, sid=""):
    await tick()
    req = make_mocked_request("POST", "/x", match_info={"sid": sid} if sid else {})
    req._read_bytes = json.dumps(body).encode()
    req["owner_id"] = 1
    r = await raw(h)(req)
    return r.status, json.loads(r.text)


async def log_of(sid):
    req = make_mocked_request("GET", "/x", match_info={"sid": sid})
    req["owner_id"] = 1
    r = await raw(sl.handle_log)(req)
    return r.status, json.loads(r.text)


def kinds(ev, district=None):
    return [e["kind"] for e in ev if district is None or e["district"] == district]


def one(ev, kind, district=None):
    rows = [e for e in ev if e["kind"] == kind and (district is None or e["district"] == district)]
    return rows[0] if len(rows) == 1 else {"n": len(rows)}


async def main():
    db._db = AsyncMongoMockClient()["ambar_sl"]
    db.supply_take, db.supply_untake = _take, _untake
    SR._biz_day = lambda *a, **k: TODAY
    import owner_routes, pay_notify, operator_routes, config_staff as _staff
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    owner_routes.notify_owners_photo = _say
    pay_notify.tell_safe = _tell
    operator_routes.tell_driver = _tell
    sr._notify_done = _say
    sr._cancel_tell = _say
    _staff.driver_names = lambda all=False: ["Сунат"]
    _staff.DRIVER_IDS = {"Сунат": 778}
    _noscan = sr.task_noscan
    sr.task_noscan = lambda *a, **k: _noscan(*a, **{"photo": b"\xff\xd8" + b"x" * 40, **k})
    # Правки заявки: расчёт заявки тут ни при чём — заглушка строк.
    async def _rows(day):
        return {"day": day, "buy_day": TODAY, "all_rows": [], "rows": [], "total_qty": 0,
                "total_aed": 0, "total_cost": 0, "edited_count": 0, "off": [], "districts": []}
    SR.order_rows = _rows
    pid0 = next(iter(SR._catalog()))
    name0 = SR._catalog()[pid0].get("name", pid0)

    # ── заявка дня: сформирована при закрытии смены, файл собран и выслан ──
    print("Заявка дня")
    await db._db.zayavki.insert_one({
        "_id": ORDER_DAY, "asked": {"p1": 10, "p2": 5}, "by": {"p1": {"jvc": 10}, "p2": {"bbay": 5}},
        "at": NOW - timedelta(hours=5, minutes=50), "frozen_at": NOW - timedelta(hours=6),
        "buy_day": TODAY, "base": {"jvc": {}}, "dist": {}})
    await sl.add("sent", day=ORDER_DAY, who="Фарход", note="AMBAR-zayavka-2026-10-03.xlsx",
                 at=NOW - timedelta(hours=5, minutes=49))
    st, r = await post(SR.handle_order_edit, {"day": ORDER_DAY, "id": pid0, "district": "jvc",
                                              "qty": 12, "as": "Фарход"})
    eq("правка клетки прошла", st, 200)
    st, r = await post(SR.handle_order_district, {"day": ORDER_DAY, "district": "tecom", "off": True,
                                                  "as": "Фарход"})
    eq("район снят", st, 200)
    st, r = await post(SR.handle_order_reset, {"day": ORDER_DAY, "id": pid0, "as": "Фарход"})
    eq("правки сняты", st, 200)
    ev_day = await db.supply_events_day(ORDER_DAY)
    eq("записи дня", [e["kind"] for e in ev_day], ["sent", "order_edit", "order_off", "order_reset"])
    eq("правка: кто и что", (ev_day[1]["who"], ev_day[1]["note"], ev_day[1]["district"]),
       ("Фарход", f"{name0} · 12", "jvc"))
    eq("сброс по позиции — названием", ev_day[3]["note"], name0)

    # ── поставка: ответ загружен, районы в работе ──
    print("Поставка")
    t_imp = NOW - timedelta(hours=2)
    await add_supply("S1", TODAY, t_imp,
                     {"jvc": {"p1": 3, "p31": 2}, "bbay": {"p2": 2}, "tecom": {"p5": 1}}, {})
    sup = await db.supply_get("S1")
    # так пишет handle_import: запись о загрузке — с днём заявки
    await sl.add("answer", "S1", day=TODAY, who="Фарход", note=sl.answer_note(sup),
                 data={"order_day": ORDER_DAY}, at=t_imp)
    eq("подпись ответа", sl.answer_note(sup), "4 позиции · 8 бутылок")
    await tick()
    okc, _ = await sr.task_claim("S1", "jvc", "Худоба", 777, datetime.now(timezone.utc))
    eq("взял JVC", okc, True)
    await tick()
    r = await sr.task_hold("S1", "jvc", "Худоба", 777, True)
    eq("камера открыта", r.get("ok"), True)
    первый = code()
    await scan("S1", "jvc", "p1", "Худоба", первый)
    await scan("S1", "jvc", "p1", "Худоба")
    await unhold("S1", "jvc")
    await tick()
    r = await sr.task_undo("S1", "jvc", первый, "Худоба")
    eq("убрать бутылку", r.get("ok"), True)
    await scan("S1", "jvc", "p1", "Худоба")
    await unhold("S1", "jvc")
    r = await sr.task_finish("S1", "jvc", "Худоба")
    eq("JVC закрыт", r.get("ok"), True)
    await tick()
    okc, _ = await sr.task_claim("S1", "bbay", "Авазбек", 777, datetime.now(timezone.utc))
    await unhold("S1", "bbay")
    r = await sr.task_noscan("S1", "bbay", "Авазбек",
                             short={"lines": [{"id": "p2", "qty": 1}], "note": "дали одну"})
    eq("Бизнес Бей без сканирования", r.get("ok"), True)
    await tick()
    r = await sr.short_decide("S1", "bbay", True, "Фарход")
    eq("недовоз подтверждён", r.get("ok"), True)
    st, r = await post(sr.handle_own_assign, {"district": "tecom", "driver": "Сунат", "as": "Фарход"}, "S1")
    eq("Тиком назначен", st, 200)
    st, d = await log_of("S1")
    eq("назначен — без второй строки «взят»", kinds(d["events"], "tecom"), ["order_off", "assigned"])
    st, r = await post(sr.handle_release, {"district": "tecom", "as": "Фарход"}, "S1")
    eq("Тиком снят с водителя", (st, r.get("ok")), (200, True))
    st, r = await post(sr.handle_cancel, {"districts": ["tecom"], "as": "Фарход"}, "S1")
    eq("Тиком отменён", (st, r.get("cancelled")), (200, ["tecom"]))

    st, d = await log_of("S1")
    eq("ручка отвечает", st, 200)
    ev = d["events"]
    eq("день заявки найден", d["order_day"], ORDER_DAY)
    eq("лента по времени", [e["at"] for e in ev], sorted(e["at"] for e in ev))
    print("   ", [(e["kind"], e["at"][11:23]) for e in ev])
    eq("шаги заявки дня в ленте", kinds(ev)[:6],
       ["order_frozen", "sent", "answer", "order_edit", "order_off", "order_reset"])
    eq("«файл собран» рядом с «выслан» не дублируется", "book" in kinds(ev), False)
    eq("ответ — один, из журнала, с именем", (one(ev, "answer")["who"], one(ev, "answer")["note"]),
       ("Фарход", "4 позиции · 8 бутылок"))
    eq("JVC по шагам", kinds(ev, "jvc"),
       ["order_edit", "claimed", "started", "scan_first", "undo", "scan_last", "done_short"])
    eq("взял — одна строка, не две", (one(ev, "claimed", "jvc")["who"], one(ev, "claimed", "jvc")["code"]),
       ("Худоба", OFFICE_CODES["jvc"]))
    eq("убрано: что", one(ev, "undo", "jvc")["note"].startswith("Absolut 1 ltr · "), True)
    eq("последний скан: сколько", one(ev, "scan_last", "jvc")["note"], "2 кода · 2 ед")
    eq("закрыт с недобором: принято", one(ev, "done_short", "jvc")["note"], "принято 2 из 5")
    eq("Бизнес Бей по шагам", kinds(ev, "bbay"), ["claimed", "short", "noscan", "short_ok"])
    eq("недовоз: кто и сколько", (one(ev, "short", "bbay")["who"], one(ev, "short", "bbay")["note"]),
       ("Авазбек", "1 ед · дали одну"))
    eq("решение: кто", one(ev, "short_ok", "bbay")["who"], "Фарход")
    eq("без сканирования: сколько осталось", one(ev, "noscan", "bbay")["note"], "не отсканировано 2")
    eq("Тиком по шагам", kinds(ev, "tecom"), ["order_off", "assigned", "released", "cancelled"])
    eq("назначен: кому и кем", (one(ev, "assigned", "tecom")["who"], one(ev, "assigned", "tecom")["note"]),
       ("Сунат", "назначил Фарход"))
    eq("снят: с кого и кем", (one(ev, "released", "tecom")["who"], one(ev, "released", "tecom")["note"]),
       ("Сунат", "снял Фарход"))
    eq("заголовки есть у всех", all(e["title"] for e in ev), True)
    eq("цвета точек из словаря", {e["cls"] for e in ev} <= {"", "ok", "warn", "dn", "wait"}, True)
    txt = json.dumps(d, ensure_ascii=False)
    eq("телеграм-id в ответе нет", ("777" in txt, "778" in txt), (False, False))

    # ── поставка до журнала: только документы ──
    print("Поставка без журнала")
    t0 = NOW - timedelta(days=1, hours=3)
    await add_supply("S0", "2026-09-30", t0, {"silicon": {"p5": 2}}, {"silicon": "Сунат"},
                     status="done", done_at=t0 + timedelta(hours=2), replaced_at=t0 + timedelta(minutes=20),
                     replaced_n=1, first_at=t0)
    await db._db.supplies.update_one({"_id": "S0"}, {"$set": {
        "tasks.silicon.started_at": t0 + timedelta(hours=1), "tasks.silicon.done_at": t0 + timedelta(hours=2),
        "tasks.silicon.scanned": 2, "items.0.got.silicon": 2}})
    for i in range(2):
        await db.qr_add(code(), "p5", NAMES["p5"], "silicon", 777, t0 + timedelta(hours=1, minutes=i * 7),
                        f"l{i}", extra={"src": "intake", "supply_id": "S0", "driver": "Сунат",
                                        "origin": "silicon", "qty": 1})
    st, d = await log_of("S0")
    ev = d["events"]
    eq("старая поставка собрана из документов", kinds(ev),
       ["answer", "claimed", "answer_replaced", "scan_first", "scan_last", "done", "supply_done"])
    eq("начало = первый скан — одной строкой", "started" in kinds(ev), False)
    eq("сканы: кто", (one(ev, "scan_first")["who"], one(ev, "scan_last")["note"]), ("Сунат", "2 кода · 2 ед"))
    eq("район принят: сколько", one(ev, "done")["note"], "2 ед")
    eq("у старой поставки нет дня заявки без заморозки", d["order_day"], "2026-09-30")

    # ── ручка и журнал в пустоте ──
    print("Края")
    st, _ = await log_of("nope")
    eq("неизвестная поставка — 404", st, 404)
    было = db._db
    db._db = None
    await sl.add("claimed", "S1", who="x", district="jvc")       # не бросает
    db._db = было
    eq("журнал без базы молчит", len(await db.supply_events("S1")) > 0, True)
    await sl.add("nonsense", "S1")
    eq("неизвестный шаг не пишется", any(e["kind"] == "nonsense" for e in await db.supply_events("S1")), False)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
