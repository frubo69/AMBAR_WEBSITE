"""Остаток приёмки «QR код не внесён»: какой бы кнопкой ни вносили — гаснет,
а чего магазин не дал — записывается недовозом (владелец, 14 ноя 2026: «горят
бутылки во внести qr-код с приёмок, которые водители не смогли принять… зашёл
он в чек-лист, внёс эти бутылки, но они почему-то остались гореть… почему
пишет нашли на складе… в свободной проверке Tanqueray 1 ltr не наша»).

Что было на проде 14 ноя: остатки B2 (Tanqueray 1) и B5 (Heineken, коробка)
внесли кнопкой «Внести новый товар» — код лёг приходом мимо приёмки, остаток
остался гореть; коды удалили — приёмка на них отвечала «уже в реестре»,
проверка — «НЕ НАША»; две находки «Нашли на складе» висели, хотя коды через
минуту приняла приёмка; Hennessy VSOP на B3, которой магазин не дал, записать
недовозом после закрытия района было нечем.

  A. «Внести новый товар» по позиции с остатком приёмки — в приёмку: остаток
     гаснет, код — приход этой поставки, склад растёт один раз; крестик
     возвращает как было; второй код той же позиции — обычный новый товар.
  B. Код с убранной записью приёмка берёт (и с полки старшим, и «новым
     товаром», и водителем на открытой приёмке); отмена возвращает запись
     убранной. Убранную списанную и ездившую между районами — не берёт.
  C. Пиво: остаток полкоробки гасит один код.
  D. Находка закрывается, когда код принят приёмкой; список находок сам
     снимает те, чей код уже в реестре.
  E. Недовоз по остатку закрытого района: в miss, в недобор магазину и в
     блок «Недовоз подтверждён», из «QR код не внесён» уходит, склад не растёт.
  F. По пересчёту (mode cover) и чужая занятость — как раньше / отказ.

mongomock: позиционный items.$ бьёт в первый элемент — supply_take/untake
заменены теми же условиями по индексу (как в test_fin_request.py).

    python3 tools/test_nocode_paths.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_routes as SR, supply_log, qr_routes as QR   # noqa: E402

DAY = "2026-11-14"
T0 = datetime.now(timezone.utc) - timedelta(hours=6)
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p31": "Heineken 0.33 can",
         "p59": "Tanqueray 1 ltr", "p75": "Hennessy VSOP 1 ltr"}
COUNT = {"bbay": {"p1": 4, "p59": 2}, "tecom": {"p1": 3, "p31": 5}, "silicon": {"p1": 2, "p2": 1, "p75": 1}}
DRV = {"bbay": "Тест-водитель 1", "tecom": "Тест-водитель 2", "silicon": "Тест-водитель 3"}
FAIL = []
N = [0]


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


async def add_supply(sid, plan, at=None):
    now = at or datetime.now(timezone.utc) - timedelta(hours=3)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": DAY,
        "items": [{"id": pid, "name": NAMES[pid], "asked": sum(plan[o].get(pid, 0) for o in plan),
                   "qty": sum(plan[o].get(pid, 0) for o in plan),
                   "by_district": {o: plan[o][pid] for o in plan if pid in plan[o]},
                   "got": {o: 0 for o in plan if pid in plan[o]}}
                  for pid in sorted({p for o in plan for p in plan[o]})],
        "tasks": {o: {"driver": DRV[o], "driver_id": 1, "claimed_at": now, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0} for o in plan}})
    SR.base_drop()
    return sid


async def wipe():
    for c in ("qr_codes", "supplies", "supply_events", "qr_found", "qr_locks"):
        await db._db[c].delete_many({})
    SR.base_drop()


def code(prefix="c"):
    N[0] += 1
    return f"{prefix}{N[0]:06d}"


async def unhold(sid, o):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who=None, owner=False, c=None):
    await unhold(sid, o)
    return await sr.task_scan(sid, o, pid, c or code(), who or DRV[o], 1, "", owner)


async def scan_n(sid, o, pid, n):
    return [(await scan(sid, o, pid)).get("verdict") for _ in range(n)]


async def stock(o):
    SR.base_drop()
    base = await SR._district_base(DAY)
    have = (base.get(o) or {}).get("have_exact") or {}
    return {p: float(have.get(p, 0)) for p in sorted(COUNT[o])}


def base(o, **plus):
    return {p: float(COUNT[o].get(p, 0) + plus.get(p, 0)) for p in sorted(COUNT[o])}


async def task(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])


async def nocode(o):
    return {p: n for (d, p), n in (await sr.nocode_qty()).items() if d == o}


def unwrap(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def call(handler, body, path="/x", match=None):
    req = make_mocked_request("POST", path, match_info=match or {})
    req["owner_id"] = 1
    async def _json(): return body
    req.json = _json
    r = await unwrap(handler)(req)
    return json.loads(r.body)


async def new_item(o, pid, c, mode="new", hold=False):
    """«Внести новый товар» настоящей ручкой STAR. Замок последнего скана
    водителя живёт секунды — в жизни остаток вносят через часы, поэтому перед
    сканом его снимаем (hold=True — оставить: проверка «вносит другой»)."""
    await db._db.qr_locks.delete_many({})
    if not hold:
        await db._db.supplies.update_many({}, {"$unset": {f"tasks.{o}.hold": ""}})
    return await call(QR.handle_scan, {"code": c, "product_id": pid, "district": o, "mode": mode, "as": "Старший"})


async def undo(c):
    """Крестик у строки в «Внести новый товар»."""
    return await call(QR.handle_undo, {"code": c, "as": "Старший"})


async def close_with_rest(sid, o, note=""):
    r = await sr.fin_request(sid, o, DRV[o], note)
    assert r.get("ok"), r
    r = await sr.fin_decide(sid, o, True, "Старший")
    assert r.get("ok") and r.get("finished"), r


async def kinds(sid):
    return [e["kind"] for e in (await supply_log.timeline(sid))["events"]]


async def main():
    db._db = AsyncMongoMockClient()["ambar_nocode_paths"]
    db.supply_take, db.supply_untake = _take, _untake
    cat = SR._catalog()
    SR._biz_day = lambda *a, **k: DAY
    import owner_routes, pay_notify
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    pay_notify.tell_safe = _tell
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    for o in COUNT:
        await db.save_stock_count(o, "2026-11-13", {
            "district": o, "day": "2026-11-13", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": pid, "name": NAMES[pid], "price": 100,
                                         "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                                        for pid, q in COUNT[o].items()]})

    print("A. «Внести новый товар» по позиции с остатком приёмки (B2, Tanqueray)")
    await wipe()
    sid = await add_supply("S1", {"bbay": {"p1": 20, "p59": 1}})
    await scan_n(sid, "bbay", "p1", 20)
    await close_with_rest(sid, "bbay", "Куар код не проходит")
    eq("район закрыт с остатком: горит Tanqueray 1", (await nocode("bbay"), (await task(sid, "bbay"))["left"]), ({"p59": 1.0}, 1))
    eq("склад — только отсканированное", await stock("bbay"), base("bbay", p1=20))
    r = await new_item("bbay", "p59", "D44024")
    eq("ответ: внесена, ушла в приёмку", (r.get("ok"), r.get("new"), r.get("intake"), bool(r.get("label"))), (True, True, "S1", True))
    q = await db.qr_get("D44024")
    eq("код — приход этой поставки, а не «новый товар»", (q.get("src"), q.get("supply_id"), q.get("status"), q.get("district"), q.get("driver")),
       ("intake", "S1", "active", "bbay", "Старший"))
    eq("остаток погас", (await nocode("bbay"), (await task(sid, "bbay"))["left"], (await db.supply_get(sid)).get("unscanned_open")), ({}, 0, []))
    eq("склад вырос на одну — один раз", await stock("bbay"), base("bbay", p1=20, p59=1))
    eq("в хронологии — остаток внесён", (await kinds(sid))[-1], "unscanned_done")
    r = await new_item("bbay", "p59", "D44099")
    eq("остатка больше нет — следующая бутылка обычным новым товаром", (r.get("new"), r.get("intake"), (await db.qr_get("D44099")).get("src")), (True, None, "new"))
    eq("склад +1 руками", await stock("bbay"), base("bbay", p1=20, p59=2))
    r = await undo("D44024")
    eq("крестик у внесённой в приёмку: убрана", (r.get("ok"), await db.qr_get("D44024")), (True, None))
    eq("остаток снова горит, район снова в незавершённом приёме",
       (await nocode("bbay"), (await task(sid, "bbay"))["left"], (await db.supply_get(sid)).get("unscanned_open")), ({"p59": 1.0}, 1, ["bbay"]))
    eq("склад вернулся", await stock("bbay"), base("bbay", p1=20, p59=1))

    print("\nB. Код с убранной записью (как D44024 на проде: внесён «новым товаром» и удалён)")
    await db._db.qr_codes.delete_one({"_id": "D44099"})
    await db._db.qr_codes.insert_one({"_id": "D44024", "status": "deleted", "was": "active", "product_id": "p59",
        "product_name": NAMES["p59"], "district": "bbay", "label": "tan#000060", "by": 1,
        "at": datetime.now(timezone.utc) - timedelta(hours=5), "src": "new", "qty": 1, "origin": "bbay",
        "del_at": datetime.now(timezone.utc) - timedelta(hours=5), "del_by": 1})
    eq("проверка бутылок: «не наша» — как и раньше", QR._verdict(await db.qr_get("D44024"), ""), "alien")
    await unhold(sid, "bbay")
    r = await call(sr.handle_own_scan, {"district": "bbay", "product_id": "p59", "code": "D44024", "as": "Старший"},
                   match={"sid": sid})
    eq("приёмка с полки берёт убранный код (было «уже в реестре»)", (r.get("ok"), r.get("verdict"), r.get("finished")), (True, "taken", True))
    q = await db.qr_get("D44024")
    eq("запись — приход поставки, прежняя сохранена", (q.get("status"), q.get("src"), q.get("supply_id"), (q.get("reused") or {}).get("prev", {}).get("status"),
       (q.get("reused") or {}).get("prev", {}).get("src")), ("active", "intake", "S1", "deleted", "new"))
    eq("проверка бутылок теперь: наша", QR._verdict(q, ""), "ok")
    eq("остаток погас", (await nocode("bbay"), (await task(sid, "bbay"))["left"]), ({}, 0))
    eq("склад +1", await stock("bbay"), base("bbay", p1=20, p59=1))
    r = await undo("D44024")
    q = await db.qr_get("D44024")
    eq("отмена: запись снова убранная, как была", (r.get("ok"), q.get("status"), q.get("src"), q.get("supply_id"), "reused" in q), (True, "deleted", "new", None, False))
    eq("остаток снова горит", (await nocode("bbay"), (await db.supply_get(sid)).get("unscanned_open")), ({"p59": 1.0}, ["bbay"]))
    r = await new_item("bbay", "p59", "D44024")
    eq("тот же убранный код «новым товаром» — тоже в приёмку", (r.get("new"), r.get("intake"), (await db.qr_get("D44024")).get("src")), (True, "S1", "intake"))
    eq("остаток погас, склад +1", (await nocode("bbay"), await stock("bbay")), ({}, base("bbay", p1=20, p59=1)))
    # Чужая история: убранная списанная и ездившая между районами — не берём.
    await undo("D44024")
    await db._db.qr_codes.update_one({"_id": "D44024"}, {"$set": {"was": "written"}})
    await unhold(sid, "bbay")
    eq("убранную списанную приёмка не берёт", (await scan(sid, "bbay", "p59", who="Старший", owner=True, c="D44024")).get("verdict"), "known")
    await db._db.qr_codes.update_one({"_id": "D44024"}, {"$set": {"was": "active", "moves": [{"from": "jvc", "to": "bbay"}]}})
    eq("убранную с переездами — тоже", (await scan(sid, "bbay", "p59", who="Старший", owner=True, c="D44024")).get("verdict"), "known")
    eq("остаток на месте", await nocode("bbay"), {"p59": 1.0})
    # Водитель на открытой приёмке.
    sid2 = await add_supply("S2", {"bbay": {"p1": 2}})
    await db._db.qr_codes.insert_one({"_id": "OLD1", "status": "deleted", "was": "active", "product_id": "p2",
        "product_name": NAMES["p2"], "district": "bbay", "label": "sto#000001", "by": 1, "at": T0, "src": "new",
        "qty": 1, "del_at": T0, "del_by": 1})
    r = await scan(sid2, "bbay", "p1", c="OLD1")
    q = await db.qr_get("OLD1")
    eq("водитель: убранный код принят, под позицией приёмки", (r.get("ok"), q.get("product_id"), q.get("src"), q.get("supply_id"),
       (q.get("reused") or {}).get("prev", {}).get("product_id")), (True, "p1", "intake", "S2", "p2"))
    eq("живой код по-прежнему «уже в реестре»", (await scan(sid2, "bbay", "p1", c="OLD1")).get("verdict"), "known")

    print("\nC. Пиво: остаток полкоробки (B5, Heineken)")
    await wipe()
    sid = await add_supply("S3", {"tecom": {"p1": 30, "p31": 2}})
    await scan_n(sid, "tecom", "p1", 30); await scan_n(sid, "tecom", "p31", 3)
    await close_with_rest(sid, "tecom", "Не можно сканировать")
    eq("горит полкоробки", await nocode("tecom"), {"p31": 0.5})
    r = await new_item("tecom", "p31", "D44008")
    eq("один код пива гасит полкоробки", (r.get("new"), r.get("intake"), r.get("qty"), await nocode("tecom")), (True, "S3", 0.5, {}))
    eq("склад: две коробки пива", await stock("tecom"), base("tecom", p1=30, p31=2))

    print("\nD. «Нашли на складе»: находка закрывается, когда код принят")
    await wipe()
    sid = await add_supply("S4", {"bbay": {"p1": 3}})
    await db.qr_found_add("F1", "bbay", DRV["bbay"], datetime.now(timezone.utc))
    await scan(sid, "bbay", "p1", c="F1")
    f = await db._db.qr_found.find_one({"_id": "F1"})
    eq("код принят приёмкой — находка решена", (f.get("status"), f.get("decided_by")), ("added", DRV["bbay"]))
    # Старые находки, повисшие до правки: код уже в реестре.
    await db.qr_found_add("F2", "jvc", "Эскадер", datetime.now(timezone.utc))
    await db._db.qr_codes.insert_one({"_id": "F2", "status": "active", "product_id": "p1", "product_name": NAMES["p1"],
                                      "district": "jvc", "src": "intake", "supply_id": "S0", "at": datetime.now(timezone.utc)})
    await db.qr_found_add("F3", "jvc", "Эскадер", datetime.now(timezone.utc))          # кода в реестре нет
    await db.qr_found_add("F4", "jvc", "Эскадер", datetime.now(timezone.utc))          # запись убрана
    await db._db.qr_codes.insert_one({"_id": "F4", "status": "deleted", "was": "active", "product_id": "p1",
                                      "product_name": NAMES["p1"], "district": "jvc", "src": "new", "at": T0, "del_at": T0})
    req = make_mocked_request("GET", "/api/owner/qr/found"); req["owner_id"] = 1
    rows = json.loads((await unwrap(QR.handle_found_list)(req)).body)["rows"]
    eq("в списке — только те, чьего кода в реестре нет", sorted(x["code"] for x in rows), ["F3", "F4"])
    eq("несвежая находка закрыта сама", (await db._db.qr_found.find_one({"_id": "F2"})).get("status"), "added")

    print("\nE. Недовоз по остатку закрытого района (B3, Hennessy VSOP — «нету на баракуда»)")
    await wipe()
    sid = await add_supply("S5", {"silicon": {"p1": 20, "p2": 1, "p75": 1}})
    await scan_n(sid, "silicon", "p1", 20)
    await close_with_rest(sid, "silicon", "нету на баракуда")
    eq("горят две позиции", await nocode("silicon"), {"p2": 1.0, "p75": 1.0})
    было = await stock("silicon")
    await unhold(sid, "silicon")
    r = await call(sr.handle_own_unscanned_short, {"district": "silicon", "product_id": "p75", "as": "Старший"},
                   match={"sid": sid})
    eq("недовоз по одной позиции: записан, район ещё с остатком", (r.get("ok"), r.get("cleared"), r.get("qty"), r["task"]["left"]), (True, False, 1, 1))
    t = (await db.supply_get(sid))["tasks"]["silicon"]
    eq("в miss, в недоборе, остаток без кодов — только вторая позиция",
       (t.get("miss"), [(g["id"], g["gap"]) for g in t.get("gaps") or []], t.get("unscanned")), ({"p75": 1}, [("p75", 1)], {"p2": 1}))
    eq("блок района: недовоз подтверждён старшим", (t["short"]["status"], t["short"]["decided_by"], [(l["id"], l["qty"]) for l in t["short"]["lines"]]),
       ("ok", "Старший", [("p75", 1)]))
    eq("в недобор магазину", [(x["id"], x["gap"], x["by_district"]) for x in sr._shortfall(await db.supply_get(sid))["rows"]], [("p75", 1, {"silicon": 1})])
    eq("горит только то, что на полке", await nocode("silicon"), {"p2": 1.0})
    eq("склад не изменился", await stock("silicon"), было)
    eq("в хронологии — недовоз подтверждён", (await kinds(sid))[-1], "short_ok")
    v = await task(sid, "silicon")
    eq("строка Hennessy: сканировать больше не просят", [(l["id"], l["need"], l["left"], l["miss"]) for l in v["lines"] if l["id"] == "p75"], [("p75", 0, 0, 1)])
    await unhold(sid, "silicon")
    r = await call(sr.handle_own_unscanned_short, {"district": "silicon", "product_id": "p75", "as": "Старший"}, match={"sid": sid})
    eq("второй раз по той же позиции — нечего", (r.get("ok"), r.get("verdict")), (False, "empty"))
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.silicon.hold": {
        "who": DRV["silicon"], "kind": "driver", "tg": 2, "at": datetime.now(timezone.utc),
        "until": datetime.now(timezone.utc) + timedelta(minutes=1)}}})
    r = await call(sr.handle_own_unscanned_short, {"district": "silicon", "product_id": "p2", "as": "Старший"}, match={"sid": sid})
    eq("остаток сейчас вносит водитель — не трогаем", (r.get("ok"), r.get("verdict"), r.get("by")), (False, "busy", DRV["silicon"]))
    r = await new_item("silicon", "p2", "S5X1", hold=True)
    eq("и «новым товаром» мимо него не пройти", (r.get("ok"), r.get("error"), r.get("by"), await db.qr_get("S5X1")), (False, "busy", DRV["silicon"], None))
    await unhold(sid, "silicon")
    r = await call(sr.handle_own_unscanned_short, {"district": "silicon", "product_id": "", "as": "Старший"}, match={"sid": sid})
    s = await db.supply_get(sid)
    eq("недовоз по всему остатку: район ушёл из незавершённого приёма",
       (r.get("ok"), r.get("cleared"), s.get("unscanned_open"), bool(s["tasks"]["silicon"].get("unscanned_done_at")), await nocode("silicon")),
       (True, True, [], True, {}))
    eq("недовоз района — обе позиции одним блоком", [(l["id"], l["qty"]) for l in s["tasks"]["silicon"]["short"]["lines"]], [("p75", 1), ("p2", 1)])
    r = await call(sr.handle_own_unscanned_short, {"district": "silicon", "product_id": "", "as": "Старший"}, match={"sid": sid})
    eq("остатка нет — ручка отказывает", (r.get("ok"), r.get("verdict")), (False, "no_remainder"))
    r = await call(sr.handle_own_unscanned_short, {"district": "нет такого", "as": "Старший"}, match={"sid": sid})
    eq("чужой район — 400", r.get("error"), "bad_district")

    print("\nF. По пересчёту и принятое без сканирования")
    await wipe()
    sid = await add_supply("S6", {"bbay": {"p1": 20, "p59": 1}})
    await scan_n(sid, "bbay", "p1", 20)
    await close_with_rest(sid, "bbay", "")
    r = await new_item("bbay", "p59", "CV1", mode="cover")
    eq("код по пересчёту (cover) в приёмку не идёт", (r.get("new"), r.get("intake"), (await db.qr_get("CV1")).get("src"), await nocode("bbay")),
       (True, None, "cover", {"p59": 1.0}))
    r = await new_item("bbay", "p1", "NW1")
    eq("позиция без остатка — обычный новый товар", (r.get("new"), r.get("intake"), (await db.qr_get("NW1")).get("src")), (True, None, "new"))
    await db._db.qr_codes.insert_one({"_id": "LIVE1", "status": "active", "product_id": "p1", "product_name": NAMES["p1"],
                                      "district": "bbay", "src": "new", "at": datetime.now(timezone.utc)})
    r = await new_item("bbay", "p59", "LIVE1")
    eq("живой код под другой позицией — «уже в реестре», остаток на месте", (r.get("new"), r.get("product_name"), await nocode("bbay")),
       (False, NAMES["p1"], {"p59": 1.0}))
    sid7 = await add_supply("S7", {"tecom": {"p1": 2}})
    r = await sr.task_noscan(sid7, "tecom", DRV["tecom"], photo=b"\xff\xd8" + b"x" * 40)
    eq("принято без сканирования", r.get("ok"), True)
    r = await new_item("tecom", "p1", "NS1")
    eq("«новый товар» по принятому без сканирования — тоже в приёмку", (r.get("new"), r.get("intake"), (await db.qr_get("NS1")).get("src"), (await task(sid7, "tecom"))["left"]),
       (True, "S7", "intake", 1))
    r = await undo("NS1")
    eq("крестик: убрана из открытой приёмки", (r.get("ok"), await db.qr_get("NS1"), (await task(sid7, "tecom"))["left"]), (True, None, 2))

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
