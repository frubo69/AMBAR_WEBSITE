"""Завершить приёмку без сканирования остатка: просьба водителя, решение
старшего, остаток без кодов на складе, в чек-листе и в «Внести / удалить
товар», досканирование с полки после закрытия (владелец, 4 ноя 2026: «надо
давать водителям возможность запросить разрешение закончить приёмку, даже
если какое-то количество позиций они не отсканировали; разрешать или
запрещать будет старший; … в чек-лист должно сваливаться предупреждение …
должен светиться во „внести товар“ как неотсканированная бутылка; должно
быть понятно, что это за бутылка и откуда она взялась»).

mongomock: позиционный items.$ бьёт в первый элемент — supply_take/untake
заменены теми же условиями по индексу (как в test_intake_life.py).

    python3 tools/test_fin_request.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_routes as SR, supply_log    # noqa: E402

DAY = "2026-11-04"
T0 = datetime.now(timezone.utc) - timedelta(hours=2)
PLAN = {"jvc": {"p1": 30, "p2": 2, "p31": 2}, "bbay": {"p1": 10, "p5": 2}}
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr",
         "p31": "Heineken 0.33 can"}
COUNT = {"jvc": {"p1": 5, "p2": 1, "p31": 3}, "bbay": {"p1": 4, "p5": 2}}
DRV = {"jvc": "Тест-водитель 1", "bbay": "Тест-водитель 2"}
QTY = {}
FAIL = []
N = [0]
СКАЗАНО = []          # (ключ, текст, кнопка) — что ушло старшему
ПИСЬМА = []           # (кому, текст) — что ушло водителям


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


async def _take(sid, district, product_id, room, now, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") not in ("open", "done"):
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


async def add_supply(sid, at=None, day=DAY):
    now = at or datetime.now(timezone.utc)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": day,
        "items": [{"id": pid, "name": NAMES[pid], "qty": sum(PLAN[o].get(pid, 0) for o in PLAN),
                   "by_district": {o: PLAN[o][pid] for o in PLAN if pid in PLAN[o]},
                   "got": {o: 0 for o in PLAN if pid in PLAN[o]}}
                  for pid in sorted({p for o in PLAN for p in PLAN[o]})],
        "tasks": {o: {"driver": DRV[o], "driver_id": 1, "claimed_at": now, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0} for o in PLAN}})
    SR.base_drop()
    return sid


async def fresh(sid="S1", **kw):
    await db._db.qr_codes.delete_many({})
    await db._db.supplies.delete_many({})
    await db._db.supply_events.delete_many({})
    СКАЗАНО.clear(); ПИСЬМА.clear()
    return await add_supply(sid, **kw)


def code():
    N[0] += 1
    return f"c{N[0]:06d}"


async def unhold(sid, o):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who=None, owner=False):
    await unhold(sid, o)
    return await sr.task_scan(sid, o, pid, code(), who or DRV[o], 1, "", owner)


async def scan_n(sid, o, pid, n, **kw):
    return [(await scan(sid, o, pid, **kw)).get("verdict") for _ in range(n)]


async def stock(o, until=None):
    SR.base_drop()
    base = await SR._district_base(DAY) if until is None else await SR._base_calc(DAY, until=until)
    have = (base.get(o) or {}).get("have_exact") or {}
    return {p: float(have.get(p, 0)) for p in sorted(PLAN[o])}


async def task(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])


def base(o, **plus):
    return {p: float(COUNT[o].get(p, 0) + plus.get(p, 0)) for p in sorted(PLAN[o])}


def unwrap(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def drv_req(sid, o, note, who=None):
    """Просьба настоящей ручкой водителя."""
    import driver_routes as dr
    from aiohttp.test_utils import make_mocked_request
    req = make_mocked_request("POST", f"/api/driver/supply/{sid}/finish-request", match_info={"sid": sid})
    req["driver"] = {"name": who or DRV[o], "district": o}; req["tg"] = {"id": 1}
    async def _json(): return {"district": o, "note": note}
    req.json = _json
    r = await unwrap(dr.handle_supply_finreq)(req)
    return r.status, json.loads(r.body)


async def own_decide(sid, o, ok):
    """Решение настоящей ручкой STAR."""
    from aiohttp.test_utils import make_mocked_request
    req = make_mocked_request("POST", f"/api/owner/supply/{sid}/task/finreq", match_info={"sid": sid})
    req["owner_id"] = 1
    async def _json(): return {"district": o, "ok": ok, "as": "Старший"}
    req.json = _json
    r = await unwrap(sr.handle_own_finreq)(req)
    return r.status, json.loads(r.body)


async def checklist_rows():
    import owner_routes as OR
    from aiohttp.test_utils import make_mocked_request
    req = make_mocked_request("GET", "/api/owner/checklist")
    req["owner_id"] = 1
    r = await unwrap(OR.handle_checklist)(req)
    return {x["id"]: x for x in json.loads(r.body)["rows"]}


async def main():
    global QTY
    db._db = AsyncMongoMockClient()["ambar_finreq"]
    db.supply_take, db.supply_untake = _take, _untake
    cat = SR._catalog()
    QTY = {pid: float(SR.code_qty(cat[pid])) for pid in NAMES}
    SR._biz_day = lambda *a, **k: DAY
    import owner_routes
    async def _say(key, text, parse_mode="Markdown", test=False, reply_markup=None, **kw):
        СКАЗАНО.append((key, text, reply_markup)); return []
    import pay_notify
    async def _tell(name, text, parse_mode=None):
        ПИСЬМА.append((name, text)); return 1
    pay_notify.tell_safe = _tell
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    os.environ["OWNER_WEBAPP_URL"] = "https://star.test/app?x=1"
    for o in COUNT:
        await db.save_stock_count(o, "2026-11-03", {
            "district": o, "day": "2026-11-03", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": pid, "name": NAMES[pid], "price": 100,
                                         "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                                        for pid, q in COUNT[o].items()]})

    print("Просьба: когда можно и когда нельзя")
    sid = await fresh()
    eq("ничего не отсканировано — просить нечего",
       (await sr.fin_request(sid, "jvc", DRV["jvc"], "устал")).get("verdict"), "nothing_scanned")
    await scan_n(sid, "jvc", "p1", 2)
    r = await sr.fin_request(sid, "jvc", DRV["jvc"], "устал")
    eq("отсканировано 2 из 34 — рано, порог 90 %", (r.get("verdict"), r.get("got"), r.get("need")), ("too_few", 2, 34))
    await scan_n(sid, "jvc", "p1", 27); await scan_n(sid, "jvc", "p2", 1)
    eq("30 из 34 — 88 %, ещё рано", (await sr.fin_request(sid, "jvc", DRV["jvc"], "")).get("verdict"), "too_few")
    await scan_n(sid, "jvc", "p31", 2)       # 31 из 34 — 91 %, можно
    eq("чужую задачу не просят", (await sr.fin_request(sid, "jvc", "Кто-то", "")).get("verdict"), "not_mine")
    st, r = await drv_req(sid, "jvc", "  полка закрыта, ключа нет  ")
    eq("ручка водителя: просьба принята", (st, r.get("ok"), (r["task"]["fin_req"] or {}).get("status")),
       (200, True, "pending"))
    v = await task(sid, "jvc")
    eq("в просьбе остаток по строкам", {l["id"]: l["qty"] for l in v["fin_req"]["lines"]},
       {"p1": 1, "p2": 1, "p31": 1})
    eq("причина обрезана по краям", v["fin_req"]["note"], "полка закрыта, ключа нет")
    eq("вторую просьбу не принять, пока ждёт", (await sr.fin_request(sid, "jvc", DRV["jvc"], "")).get("verdict"),
       "pending")
    eq("старшему ушло с кнопкой в приёмку", (len(СКАЗАНО), СКАЗАНО[0][0],
       "Просьба завершить приёмку без сканирования" in СКАЗАНО[0][1],
       "без кодов 3 бутылки" in СКАЗАНО[0][1], "полка закрыта" in СКАЗАНО[0][1],
       СКАЗАНО[0][2]["inline_keyboard"][0][0]["web_app"]["url"]),
       (1, "supply.finreq", True, True, True, "https://star.test/app?x=1&go=supply%3AS1%3Ajvc"))
    eq("задача не тронута — сканировать можно дальше", (await scan(sid, "jvc", "p1")).get("ok"), True)
    eq("по ревизии в журнале — просьба", [e["kind"] for e in (await supply_log.timeline(sid))["events"]
                                           if e["kind"] in ("fin_req", "fin_ok", "fin_no")], ["fin_req"])
    eq("ручка водителя без района — 400", (await drv_req(sid, "", "", who=DRV["jvc"]))[0], 400)

    print("\nОтказ старшего")
    st, r = await own_decide(sid, "jvc", False)
    eq("отказано", (st, r.get("ok"), r.get("finished"), r["task"]["fin_req"]["status"], r["task"]["fin_req"]["decided_by"]),
       (200, True, False, "no", "Старший"))
    eq("водителю сказано", (len(ПИСЬМА), ПИСЬМА[-1][0], "не разрешил" in ПИСЬМА[-1][1]), (1, DRV["jvc"], True))
    eq("задача открыта, остаток тот же", ((await task(sid, "jvc"))["done_at"], (await task(sid, "jvc"))["left"]), ("", 2))
    eq("второй раз не решить", (await sr.fin_decide(sid, "jvc", True, "Старший")).get("verdict"), "not_pending")
    eq("после отказа можно попросить снова", (await sr.fin_request(sid, "jvc", DRV["jvc"], "всё равно не могу")).get("ok"), True)
    ПИСЬМА.clear()

    print("\nНедовоз ждёт — разрешать нельзя")
    await sr.short_report(sid, "jvc", DRV["jvc"], "driver", [{"id": "p2", "qty": 1}], "не было")
    eq("сперва недовоз", (await sr.fin_decide(sid, "jvc", True, "Старший")).get("verdict"), "short_pending")
    await sr.short_decide(sid, "jvc", True, "Старший")
    eq("после недовоза сканировать осталось меньше", (await task(sid, "jvc"))["left"], 1)

    print("\nРазрешение: район закрыт, остаток на полке без кодов")
    st, r = await own_decide(sid, "jvc", True)
    eq("разрешено и закрыто", (st, r.get("ok"), r.get("finished"), r["task"]["fin_req"]["status"]),
       (200, True, True, "ok"))
    s = await db.supply_get(sid)
    t = s["tasks"]["jvc"]
    ua = db._dt_aware(t.get("unscanned_at"))
    eq("закрыта в момент решения", bool(t.get("done_at")) and bool(t.get("unscanned_at")), True)
    eq("остаток записан по строкам на момент решения", t.get("unscanned"), {"p31": 1})
    eq("кто просил и кто разрешил", (t.get("unscanned_by"), t.get("unscanned_ok_by")), (DRV["jvc"], "Старший"))
    eq("поставка помнит район с остатком", s.get("unscanned_open"), ["jvc"])
    eq("недобор магазину — только подтверждённый недовоз, не остаток",
       {g["id"]: g["gap"] for g in t["gaps"]}, {"p2": 1})
    eq("водителю сказано", (len(ПИСЬМА), "разрешил" in ПИСЬМА[-1][1], "1 бутылка" in ПИСЬМА[-1][1]), (1, True, True))
    eq("старшим — итог приёмки с остатком", ("Завершена без сканирования: 1 бутылка" in СКАЗАНО[-1][1],
                                           "разрешил Старший" in СКАЗАНО[-1][1]), (True, True))
    eq("СКЛАД: отсканированное + остаток на полке; недовоза нет", await stock("jvc"),
       base("jvc", p1=30, p2=1, p31=2))
    st = await stock("jvc", until=ua - timedelta(seconds=1))
    # Сканы и решение легли в одну секунду — смотрим только то, что без кодов.
    eq("склад на момент ДО решения — остатка без кодов ещё нет", st["p31"], 3.0)
    eq("склад на момент ПОСЛЕ решения — с остатком",
       await stock("jvc", until=ua + timedelta(seconds=1)), base("jvc", p1=30, p2=1, p31=2))
    eq("в журнале: просьба, отказ, просьба, разрешение, закрытие без сканирования",
       [e["kind"] for e in (await supply_log.timeline(sid))["events"]
        if e["kind"] in ("fin_req", "fin_ok", "fin_no", "done_unscanned", "done")],
       ["fin_req", "fin_no", "fin_req", "fin_ok", "done_unscanned"])
    eq("«заказано и не забрано» остаток не держит", (await sr.pending_qty()).get("jvc", {}).get("p31", 0), 0)

    print("\nОткуда бутылка: чек-лист и «Внести / удалить товар»")
    rows = await checklist_rows()
    eq("строка чек-листа есть и красная", (rows.get("unscanned_left") or {}).get("state"), "late")
    eq("в строке: район, сколько, что, приёмка, водитель, кто разрешил",
       all(w in rows["unscanned_left"]["s"] for w in ("B1 JVC", "1 бутылка", "Heineken 0.33 can 1",
                                                     "приёмка 4 ноября", DRV["jvc"], "разрешил Старший")), True)
    eq("строка ведёт в район", (rows["unscanned_left"]["go"], rows["unscanned_left"]["tasks"][0]["district"]),
       ("unscanned_left", "jvc"))
    eq("строки просьбы нет — решена", "fin_req" in rows, False)
    inb = (await sr.intake_by_district(DRV["jvc"]))["districts"]
    jvc = next(x for x in inb if x["id"] == "jvc")
    eq("район в «незавершённом приёме» с остатком", (jvc["left"], len(jvc["tasks"])), (1, 1))
    tt = jvc["tasks"][0]
    eq("задача помечена как завершённая, с тем, откуда остаток",
       (tt["finished"], tt["unscanned_by"], tt["unscanned_ok_by"], tt["day"], tt["driver"], bool(tt["unscanned_at"])),
       (True, DRV["jvc"], "Старший", DAY, DRV["jvc"], True))
    eq("строки — только то, что без кодов", {l["id"]: l["left"] for l in tt["lines"]}, {"p31": 1})
    ut = await sr.unscanned_tasks()
    eq("список остатков после приёмки", [(x["district"], x["left"], x["ok_by"]) for x in ut], [("jvc", 1, "Старший")])
    # На «Обзор» STAR (intake_live) закрытый с остатком район не идёт (владелец,
    # 12 ноя 2026: «не выноси приёмки вот сюда в обзор») — его остаток в
    # чек-листе и во «Внести QR коды», что проверено выше.
    live = await sr.intake_live()
    eq("на «Обзоре» закрытого с остатком района нет", [x["district"] for x in live], [])
    row = sr._intake_row(sid, s, "jvc", t, {})
    eq("история приёмок: статус unscanned с полями и остатком",
       (row["status"], row["unscanned_by"], row["unscanned_ok_by"], row["unscanned_qty"], row["left"]),
       ("unscanned", DRV["jvc"], "Старший", 1, 1))

    print("\nДосканировать с полки после закрытия — поставка уже done")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"status": "done"}})
    eq("чужой водитель не может", (await scan(sid, "jvc", "p2", who="Кто-то")).get("verdict"), "not_mine")
    r = await scan(sid, "jvc", "p31", who="Старший", owner=True)
    eq("старший с полки вносит код", (r.get("ok"), r.get("finished")), (True, False))
    eq("код лёг как cover — склад не вырос", await stock("jvc"), base("jvc", p1=30, p2=1, p31=2))
    eq("остаток уменьшился — код банок Heineken это полкоробки", (await task(sid, "jvc"))["left"], 0.5)
    # Сверх подтверждённого недовоза, но в пределах заявки — нашедшаяся
    # бутылка, как в открытой приёмке: принимается и растит склад.
    eq("нашедшаяся сверх недовоза принимается", (await scan(sid, "jvc", "p2", who="Старший", owner=True)).get("verdict"), "taken")
    eq("и она — приход, склад вырос", await stock("jvc"), base("jvc", p1=30, p2=2, p31=2))
    r = await scan(sid, "jvc", "p31")
    eq("последняя бутылка — остаток внесён", (r.get("ok"), r.get("finished")), (True, True))
    s = await db.supply_get(sid)
    eq("поставка больше не держит район", (s.get("unscanned_open"), bool(s["tasks"]["jvc"].get("unscanned_done_at"))),
       ([], True))
    eq("склад тот же", await stock("jvc"), base("jvc", p1=30, p2=2, p31=2))
    eq("в «незавершённом приёме» района нет", next(x for x in (await sr.intake_by_district(DRV["jvc"]))["districts"] if x["id"] == "jvc")["tasks"], [])
    eq("строки чек-листа нет", "unscanned_left" in await checklist_rows(), False)
    eq("в закрытую больше не сканируют — поставка done", (await scan(sid, "jvc", "p1")).get("verdict"), "no_supply")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"status": "open"}})
    eq("и при открытой поставке — закрыта", (await scan(sid, "jvc", "p1")).get("verdict"), "closed")
    eq("журнал: остаток внесён", [e["kind"] for e in (await supply_log.timeline(sid))["events"] if e["kind"] == "unscanned_done"],
       ["unscanned_done"])
    eq("история: остатка нет, остался подтверждённый недовоз", sr._intake_row(sid, s, "jvc", s["tasks"]["jvc"], {})["status"], "short")

    print("\nПросьба ждёт, а район закрыли обычным путём — просьба снята")
    sid = await fresh("S2")
    await scan_n(sid, "bbay", "p1", 10); await scan_n(sid, "bbay", "p5", 1)
    eq("11 из 12 — больше 90 %, просьба принята", (await sr.fin_request(sid, "bbay", DRV["bbay"], "")).get("ok"), True)
    rows = await checklist_rows()
    eq("строка просьбы в чек-листе, красная, с районом и причиной", ((rows.get("fin_req") or {}).get("state"),
       "B2" in rows["fin_req"]["s"], DRV["bbay"] in rows["fin_req"]["s"], rows["fin_req"]["tasks"][0]["supply_id"]),
       ("late", True, True, "S2"))
    await scan_n(sid, "bbay", "p5", 1)
    r = await sr.task_finish(sid, "bbay", DRV["bbay"])
    eq("закрыта сканом", r.get("ok"), True)
    eq("просьба снята", ((await db.supply_get(sid))["tasks"]["bbay"]["fin_req"] or {}).get("status"), "void")
    eq("решать нечего", (await sr.fin_decide(sid, "bbay", True, "Старший")).get("verdict"), "not_pending")
    eq("строки просьбы нет", "fin_req" in await checklist_rows(), False)
    eq("остатка нет — склад по принятому", await stock("bbay"), base("bbay", **PLAN["bbay"]))

    print("\nПринято без сканирования, потом просьба закрыть остаток")
    sid = await fresh("S3")
    _task_noscan = sr.task_noscan
    r = await _task_noscan(sid, "jvc", DRV["jvc"], photo=b"\xff\xd8" + b"x" * 40)
    eq("принято без сканирования", r.get("ok"), True)
    await scan_n(sid, "jvc", "p1", 3)
    eq("принято без сканирования, но порог тот же: 3 из 34 — рано",
       (await sr.fin_request(sid, "jvc", DRV["jvc"], "")).get("verdict"), "too_few")
    await scan_n(sid, "jvc", "p1", 27); await scan_n(sid, "jvc", "p2", 1)
    eq("31 из 34 — можно", (await sr.fin_request(sid, "jvc", DRV["jvc"], "")).get("ok"), True)
    # Хаб «Закупка» у принятого без сканирования пишет «сканируют · 31 из 34»,
    # а не «принимают без сканирования» (владелец, 4 ноя 2026).
    from aiohttp.test_utils import make_mocked_request as _mk
    rq = _mk("GET", "/api/owner/supply/list"); rq["owner_id"] = 1
    lst = json.loads((await unwrap(sr.handle_list)(rq)).body)
    def _find(o):
        if isinstance(o, dict):
            if o.get("supply_id") == sid and "tasks" in o: return o
            for v in o.values():
                r = _find(v)
                if r: return r
        if isinstance(o, list):
            for v in o:
                r = _find(v)
                if r: return r
    tk = _find(lst)["tasks"]["jvc"]
    eq("список поставок для хаба: принято без сканирования, отсканировано 31 из 34",
       (bool(tk["noscan_at"]), tk["got_units"], tk["need_units"]), (True, 31, 34))
    eq("склад до решения — по заявке", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    r = await sr.fin_decide(sid, "jvc", True, "Старший")
    eq("разрешено", (r.get("ok"), r.get("finished")), (True, True))
    eq("склад после — тот же, товар и так был на полке", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    eq("остаток — то, что без кодов", (await db.supply_get(sid))["tasks"]["jvc"].get("unscanned"), {"p2": 1, "p31": 2})

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
