"""Остаток приёмки без кодов и ревизия района: код остатка — приход, когда бы
район ни пересчитали (владелец, 16 ноя 2026, про коробку Heineken на Тикоме:
«Да, чини»).

Что было на проде. B5, приёмка 11 ноя: район закрыт с остатком — коробка
Heineken без кодов. С 12 ноя такой товар на складе не числится, значит и в
снимок ревизии не идёт. 15 ноя ревизия района кончилась в 13:59, в 14:06
коробку внесли двумя наклейками — и task_scan записал их cover («пересчёт
его уже видел»): приёмка закрылась 120 из 120, а склад остался 6,5 при 7,5
на полке. Тем же правилом nocode_qty выбрасывал пересчитанный район из «QR
код не внесён»: остатка не было ни на складе, ни в чек-листе, ни в заявке.

  A. Тиком как на проде: район закрыт с остатком → ревизия камерой настоящими
     audit_start / audit_scan / audit_finish → остаток вносят «Внести новый
     товар». Код — приход, склад растёт, заявка и чек-лист видят остаток и
     после ревизии.
  B. Принято без сканирования → ревизия → водитель сканирует всё: каждый код
     — приход (было: весь район cover, на склад не вставало ничего).
  C. Старое правило живо там, где оно верно: район пересчитали, пока товар
     без кодов ещё числился (до деплоя 12 ноя), — бутылка уже в снимке, код к
     ней cover, в «QR код не внесён» с приёмки она не идёт.
  D. Границы count_holds_nocode.

mongomock: позиционный items.$ бьёт в первый элемент — supply_take/untake
заменены теми же условиями по индексу (как в test_nocode_paths.py).

    python3 tools/test_nocode_after_count.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_routes as SR, qr_routes as QR   # noqa: E402
import owner_routes, pay_notify, backdate                          # noqa: E402

U = timezone.utc
DAY = "2026-11-15"                       # учётный день ревизии
T0 = datetime.now(U) - timedelta(hours=6)
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p31": "Heineken 0.33 can",
         "p59": "Tanqueray 1 ltr"}
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


async def add_supply(sid, plan, day="2026-11-11"):
    now = datetime.now(U) - timedelta(hours=3)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": day,
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
    for c in ("qr_codes", "supplies", "supply_events", "qr_found", "qr_locks", "stock_counts",
              "stock_audits", "audit_scans"):
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


async def count(o, day, at, lines: dict):
    """Снимок склада района: {позиция: единиц}."""
    cat = SR._catalog()
    await db.save_stock_count(o, day, {
        "district": o, "day": day, "counted_at": at.isoformat(), "first_time": False,
        "counted_by": 0, "lines": [{"id": pid, "name": NAMES[pid], "price": 100,
                                     "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                                    for pid, q in lines.items()]})
    SR.base_drop()


async def stock(o, *pids):
    SR.base_drop()
    have = ((await SR._district_base(DAY)).get(o) or {}).get("have_exact") or {}
    return tuple(float(have.get(p, 0)) for p in pids)


async def nocode(o):
    return {p: n for (d, p), n in (await sr.nocode_qty()).items() if d == o}


async def debt(o):
    """Долг реестра района: числится, а кода нет (qr_routes.unscanned_by_district)."""
    SR.base_drop()
    d = {}
    await QR.unscanned_by_district(None, d)
    return d.get(o) or {}


async def task(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])


def unwrap(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def call(handler, body, match=None):
    req = make_mocked_request("POST", "/x", match_info=match or {})
    req["owner_id"] = 1
    async def _json(): return body
    req.json = _json
    r = await unwrap(handler)(req)
    return json.loads(r.body)


async def new_item(o, pid, c):
    """«Внести новый товар» настоящей ручкой STAR."""
    await db._db.qr_locks.delete_many({})
    await db._db.supplies.update_many({}, {"$unset": {f"tasks.{o}.hold": ""}})
    return await call(QR.handle_scan, {"code": c, "product_id": pid, "district": o, "mode": "new", "as": "STAR"})


async def close_with_rest(sid, o, note=""):
    r = await sr.fin_request(sid, o, DRV[o], note)
    assert r.get("ok"), r
    r = await sr.fin_decide(sid, o, True, "Старший")
    assert r.get("ok") and r.get("finished"), r


async def audit(o, by="Тест-водитель"):
    """Ревизия района камерой: начать, навести на каждый живой код района,
    завершить. Возвращает (ревизия, строки снимка по позициям)."""
    st, _ = await SR.audit_start(o, DAY, 1, by)
    assert st == 200, st
    codes = [d["_id"] async for d in db._db.qr_codes.find({"district": o, "status": "active"})]
    for c in codes:
        await SR.audit_scan(o, DAY, c, 1, by)
    st, _ = await SR.audit_finish(o, DAY, 1, by)
    assert st == 200, st
    SR.base_drop()
    a = await db.audit_get(o, DAY)
    snap = {l["id"]: l["actual"] for l in (await db.get_stock_count(o, DAY) or {}).get("lines") or []}
    return a, snap, len(codes)


def line(a, pid):
    l = next((l for l in a.get("lines") or [] if l["id"] == pid), None)
    return (l["expected"], l["actual"], l["diff"], l["noqr"]) if l else None


async def order_cell(o, pid):
    """Клетка заявки района: сколько вычтено как «принято, QR код не внесён»."""
    SR.base_drop()
    d = await SR.order_rows(DAY)
    сп = list(d.get("rows") or []) + list(d.get("all_rows") or [])
    r = next((x for x in сп if x["id"] == pid), None)
    return ((r or {}).get("cells", {}).get(o) or {}).get("nocode")


async def main():
    db._db = AsyncMongoMockClient()["ambar_nocode_after_count"]
    db.supply_take, db.supply_untake = _take, _untake
    SR._biz_day = lambda *a, **k: DAY
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    pay_notify.tell_safe = _tell
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    backdate.notify = _say
    eq("день ревизии и сами сканы — после деплоя 12 ноя", datetime.now(U) > SR.NOCODE_OFF_AT, True)

    print("\nA. Тиком 15 ноя: район закрыт с остатком, потом ревизия, потом остаток вносят")
    await wipe()
    o = "tecom"
    await count(o, "2026-11-04", T0, {"p1": 0, "p31": 0})
    sid = await add_supply("S261111", {o: {"p1": 20, "p31": 4}})
    await scan_n(sid, o, "p1", 20)
    await scan_n(sid, o, "p31", 6)                    # три коробки из четырёх: код — полкоробки
    await close_with_rest(sid, o, "Не можно сканировать")
    eq("район закрыт с остатком: коробка Heineken без кодов", (await nocode(o), (await task(sid, o))["left"]), ({"p31": 1.0}, 1))
    eq("склад — только отсканированное: Heineken 3", await stock(o, "p1", "p31"), (20.0, 3.0))
    a, snap, сканов = await audit(o)
    eq("ревизия камерой: 26 сканов, Heineken числится 3 · камера 3 · разница 0 · без кодов 0",
       (сканов, line(a, "p31"), a.get("short"), a.get("over")), (26, (3, 3, 0, 0), None, None))
    eq("в снимке ревизии Heineken 3 — коробки без кодов в нём нет", (snap.get("p31"), snap.get("p1")), (3, 20))
    eq("после ревизии склад тот же", await stock(o, "p1", "p31"), (20.0, 3.0))
    eq("остаток по-прежнему «QR код не внесён» (было: выпадал из счёта)",
       (await nocode(o), (await sr.nocode_by_district()).get(o), [d for _s, _p, d, _t, _v in await sr._nocode_tasks()]),
       ({"p31": 1.0}, 1, [o]))
    eq("заявка его по-прежнему вычитает (было: заказала бы второй раз)", await order_cell(o, "p31"), 1)
    eq("долга реестра у района нет: всё, что числится, с кодами", await debt(o), {})
    eq("пересчёт этот остаток в снимке не держит", await SR.count_holds_nocode(o, (await db.supply_get(sid))["tasks"][o]["unscanned_at"]), False)
    r1 = await new_item(o, "p31", "D44025")
    r2 = await new_item(o, "p31", "D44026")
    eq("обе наклейки ушли в приёмку", [(r.get("ok"), r.get("new"), r.get("intake")) for r in (r1, r2)], [(True, True, sid)] * 2)
    q = [await db.qr_get(c) for c in ("D44025", "D44026")]
    eq("коды — приход поставки (было cover)", [(x.get("src"), x.get("supply_id"), x.get("qty"), x.get("status")) for x in q],
       [("intake", sid, 0.5, "active")] * 2)
    s = await db.supply_get(sid)
    eq("приёмка: Heineken 4 из 4, район вышел из незавершённого приёма",
       (next(i["got"][o] for i in s["items"] if i["id"] == "p31"), s.get("unscanned_open"), await nocode(o)), (4.0, [], {}))
    eq("КОРОБКА НА СКЛАДЕ: Heineken 4 (было: оставалось 3)", await stock(o, "p1", "p31"), (20.0, 4.0))
    eq("заявка больше ничего не вычитает, долга реестра нет", (await order_cell(o, "p31"), await debt(o)), (0, {}))
    r = await call(QR.handle_undo, {"code": "D44026", "as": "STAR"})
    eq("крестик у наклейки: полкоробки снова без кода, со склада ушли",
       (r.get("ok"), await db.qr_get("D44026"), await nocode(o), await stock(o, "p31")), (True, None, {"p31": 0.5}, (3.5,)))
    r = await new_item(o, "p31", "D44026")
    eq("внесли снова — снова приход, склад 4", (r.get("intake"), (await db.qr_get("D44026")).get("src"), await stock(o, "p31")),
       (sid, "intake", (4.0,)))

    print("\nB. Принято без сканирования, потом ревизия, потом водитель сканирует")
    await wipe()
    o = "silicon"
    old = datetime.now(U) - timedelta(days=9)
    for c in ("old1", "old2"):                        # две бутылки с кодами лежат с прошлого пересчёта
        await db._db.qr_codes.insert_one({"_id": c, "status": "active", "product_id": "p1", "product_name": NAMES["p1"],
                                          "district": o, "origin": o, "at": old, "src": "intake", "qty": 1, "label": c})
    await count(o, "2026-11-04", T0, {"p1": 2, "p2": 0})
    sid = await add_supply("S261113", {o: {"p1": 6, "p2": 4}})
    r = await sr.task_noscan(sid, o, DRV[o], photo=b"\xff\xd8" + b"x" * 40)
    eq("принято без сканирования: на складе ничего не прибавилось, 10 единиц ждут кода",
       (r.get("ok"), await stock(o, "p1", "p2"), await nocode(o)), (True, (2.0, 0.0), {"p1": 6.0, "p2": 4.0}))
    a, snap, сканов = await audit(o)
    eq("ревизия: камера видит две бутылки с кодами, расхождений нет",
       (сканов, line(a, "p1"), line(a, "p2"), a.get("short")), (2, (2, 2, 0, 0), (0, 0, 0, 0), None))
    eq("снимок: Absolut 2, Stolichnaya 0 — принятого без кодов в нём нет", (snap.get("p1"), snap.get("p2")), (2, 0))
    eq("10 единиц по-прежнему «QR код не внесён» (было: выпадали)", (await nocode(o), (await sr.nocode_by_district()).get(o)),
       ({"p1": 6.0, "p2": 4.0}, 10))
    eq("заявка их вычитает", (await order_cell(o, "p1"), await order_cell(o, "p2")), (6, 4))
    v = await scan_n(sid, o, "p1", 6) + await scan_n(sid, o, "p2", 4)
    eq("водитель отсканировал все 10", v, ["taken"] * 10)
    srcs = sorted({d.get("src") async for d in db._db.qr_codes.find({"supply_id": sid})})
    eq("каждый код — приход (было: все cover)", srcs, ["intake"])
    s = await db.supply_get(sid)
    eq("район закрылся сам последним сканом", (bool(s["tasks"][o].get("done_at")), await nocode(o)), (True, {}))
    eq("ВСЁ НА СКЛАДЕ: Absolut 8, Stolichnaya 4 (было: 2 и 0)", await stock(o, "p1", "p2"), (8.0, 4.0))
    eq("долга реестра нет", await debt(o), {})

    print("\nC. Район пересчитали, пока товар без кодов ещё числился (до 12 ноя)")
    await wipe()
    o = "bbay"
    cut = SR.NOCODE_OFF_AT
    await count(o, "2026-11-01", datetime(2026, 11, 1, 6, tzinfo=U), {"p1": 0, "p59": 2})
    sid = await add_supply("S261105", {o: {"p1": 20, "p59": 1}}, day="2026-11-05")
    await scan_n(sid, o, "p1", 20)
    await close_with_rest(sid, o, "Куар код не проходит")
    закрыт = datetime(2026, 11, 5, 8, tzinfo=U)
    await db._db.supplies.update_one({"_id": sid}, {"$set": {f"tasks.{o}.unscanned_at": закрыт, f"tasks.{o}.done_at": закрыт}})
    await db._db.qr_codes.update_many({"supply_id": sid}, {"$set": {"at": закрыт - timedelta(hours=1)}})
    # Ревизия 8 ноя по тогдашнему правилу унесла бутылку без кода в снимок: Tanqueray 2 + 1.
    await count(o, "2026-11-08", datetime(2026, 11, 8, 9, tzinfo=U), {"p1": 20, "p59": 3})
    eq("пересчёт 8 ноя держит бутылку в снимке", await SR.count_holds_nocode(o, закрыт), True)
    eq("она на складе и в долге реестра, а не в «QR код не внесён» с приёмки",
       (await stock(o, "p59"), (await debt(o)).get("p59"), await nocode(o)), ((3.0,), 3, {}))
    # Следующая ревизия, уже после 12 ноя, несёт бутылку без кода дальше.
    await count(o, "2026-11-14", datetime(2026, 11, 14, 9, tzinfo=U), {"p1": 20, "p59": 3})
    eq("и после ревизии 14 ноя — так же", (await SR.count_holds_nocode(o, закрыт), await stock(o, "p59"), await nocode(o)),
       (True, (3.0,), {}))
    await unhold(sid, o)
    r = await call(sr.handle_own_scan, {"district": o, "product_id": "p59", "code": "OLD59", "as": "Старший"}, match={"sid": sid})
    eq("код к ней — cover: остаток не растёт, долг реестра меньше на одну",
       (r.get("verdict"), (await db.qr_get("OLD59")).get("src"), await stock(o, "p59"), (await debt(o)).get("p59")),
       ("taken", "cover", (3.0,), 2))
    # Тот же остаток, но район до 12 ноя не пересчитывали — как Тиком: в снимке бутылки нет.
    await db._db.qr_codes.delete_one({"_id": "OLD59"})
    await _untake(sid, o, "p59", 1)
    await db.supply_unscanned_reopen(sid, o)
    await db._db.stock_counts.delete_many({"district": o, "day": "2026-11-08"})
    await count(o, "2026-11-14", datetime(2026, 11, 14, 9, tzinfo=U), {"p1": 20, "p59": 2})
    eq("район до 12 ноя после приёмки не пересчитывали: в снимке её нет, она ждёт кода",
       (await SR.count_holds_nocode(o, закрыт), await stock(o, "p59"), await nocode(o)), (False, (2.0,), {"p59": 1.0}))
    await unhold(sid, o)
    r = await call(sr.handle_own_scan, {"district": o, "product_id": "p59", "code": "NEW59", "as": "Старший"}, match={"sid": sid})
    eq("код — приход, склад +1", (r.get("verdict"), (await db.qr_get("NEW59")).get("src"), await stock(o, "p59")),
       ("taken", "intake", (3.0,)))

    print("\nD. Границы")
    await wipe()
    o = "bbay"
    eq("момента нет", await SR.count_holds_nocode(o, None), False)
    await count(o, "2026-11-12", cut - timedelta(seconds=1), {"p1": 1})
    eq("приняли за минуту до пересчёта, пересчёт за секунду до деплоя — держит", await SR.count_holds_nocode(o, cut - timedelta(minutes=1)), True)
    eq("момент строкой и без пояса — так же", (await SR.count_holds_nocode(o, (cut - timedelta(minutes=1)).isoformat()),
       await SR.count_holds_nocode(o, (cut - timedelta(minutes=1)).replace(tzinfo=None))), (True, True))
    eq("приняли после этого пересчёта — не держит", await SR.count_holds_nocode(o, cut - timedelta(microseconds=1)), False)
    eq("приняли в минуту деплоя и позже — не держит", (await SR.count_holds_nocode(o, cut), await SR.count_holds_nocode(o, datetime.now(U))), (False, False))
    await wipe()
    await count(o, "2026-11-12", cut, {"p1": 1})
    eq("пересчёт ровно в минуту деплоя — уже по новому правилу", await SR.count_holds_nocode(o, cut - timedelta(minutes=1)), False)
    eq("другой район без пересчётов", await SR.count_holds_nocode("tecom", cut - timedelta(days=3)), False)

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
