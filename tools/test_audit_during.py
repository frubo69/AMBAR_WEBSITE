"""Ревизия идёт часами, а снимок у неё один: то, что случилось с позицией
после её скана и до последнего скана ревизии, раньше не попадало никуда
(владелец, 16 ноя 2026, на «Начать чинить уже сработавшие ошибки по списку?»:
«Да, чини»).

Что было. Снимок = счёт камеры, момент снимка = последний скан ВСЕЙ ревизии;
склад после неё = снимок + события позже этого момента. Позицию посчитали в
три ночи, ревизию закончили в девять вечера — продажа в три часа дня «до
снимка», и бутылка числится дальше (JVC, 31 окт 2026: Campo Viejo Gran Reserva
2 — недостача 2 на ревизии 11 ноя). Приёмка и «внести новый товар» в том же
окне на склад не вставали. Наклейка коробки с приёмки, внесённая в реестр
после последнего скана, считалась дважды: в счёте камеры и приходом.

  A. Расход после скана позиции и до снимка: продажа, списание, переезд по
     коду — снимок уменьшается; переезд бутылки, которую камера не видела, —
     нет.
  B. Приход в том же окне: приёмка, «внести новый товар», переезд на район.
  C. Пока позицию ещё считают: бутылку отсканировали и продали — снимок
     меньше; продали неотсканированную — зачёт, снимок как у камеры.
  D. Заказ после скана позиции, довезённый после снимка, — склад вычтет сам,
     один раз.
  E. Наклейка коробки с приёмки, которую камера видела «не в реестре»: внесли
     после последнего скана — один раз на складе, излишка в отчёте нет.
  F. Позицию не сканировали вовсе, а товар приехал по ходу ревизии — не
     недостача, на склад встаёт.
  G. Списание водителя, согласованное после ревизии (из STAR и кнопкой в боте
     владельца): бутылку камера посчитала, разбили позже — снимок доводится
     при согласовании; отклонили — бутылка числится и выйдет недостачей;
     разбита до скана — ничего.
  H. Бутылка охране: отсканирована и отдана до снимка, согласована после —
     снимок доводится; отдана до скана (случай 9–10 ноя) — нет.
  I. Поздняя наклейка (audit_found) доводку не теряет; возобновлённая и
     завершённая заново ревизия не применяет её дважды.

    python3 tools/test_audit_during.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_routes as SR, qr_routes as QR   # noqa: E402
import expense_routes as EX                                        # noqa: E402
import owner_routes, pay_notify, backdate                          # noqa: E402

U = timezone.utc
DAY = "2026-11-15"
O, O2 = "tecom", "jvc"
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p31": "Heineken 0.33 can"}
FAIL = []
N = [0]


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


def now():
    return datetime.now(U)


async def tick():
    await asyncio.sleep(0.02)


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


async def reg(code, pid, at=None, district=O, src="intake", qty=1):
    await db._db.qr_codes.insert_one({
        "_id": code, "status": "active", "product_id": pid, "product_name": NAMES[pid],
        "district": district, "origin": district, "at": at or now(), "src": src, "qty": qty,
        "label": code})
    SR.base_drop()


async def count(o, day, at, lines: dict):
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
    v = tuple(float(have.get(p, 0)) for p in pids)
    return v[0] if len(v) == 1 else v


async def wipe():
    for c in ("qr_codes", "supplies", "supply_events", "qr_found", "qr_locks", "stock_counts",
              "stock_audits", "audit_scans", "audit_scans_dropped", "orders", "writeoffs",
              "stock_transfers", "driver_days"):
        await db._db[c].delete_many({})
    SR.base_drop()


async def shelf(a=5, s=3, other=0):
    """Полка Тикома: a Absolut (A0…), s Stolichnaya (S0…), прошлый снимок 9
    дней назад; на JVC — other Absolut (J0…) и свой снимок."""
    await wipe()
    old = now() - timedelta(days=9)
    for i in range(a): await reg(f"A{i}", "p1", old)
    for i in range(s): await reg(f"S{i}", "p2", old)
    for i in range(other): await reg(f"J{i}", "p1", old, district=O2)
    await count(O, "2026-11-04", old + timedelta(hours=1), {"p1": a, "p2": s})
    await count(O2, "2026-11-04", old + timedelta(hours=1), {"p1": other, "p2": 0})


async def start():
    st, _ = await SR.audit_start(O, DAY, 1, "Водитель"); assert st == 200, st
    await tick()             # база хранит время до миллисекунды — события идут не в ту же


async def see(*codes):
    for c in codes:
        await SR.audit_scan(O, DAY, c, 1, "Водитель")
    await tick()


async def finish():
    st, rep = await SR.audit_finish(O, DAY, 1, "Водитель"); assert st == 200, (st, rep)
    return rep


async def last_then_finish():
    """Досчитать Stolichnaya (это последний скан ревизии) и завершить."""
    await tick()
    await see("S0", "S1", "S2")
    return await finish()


async def snap(pid="p1"):
    return next((l["actual"] for l in (await db.get_stock_count(O, DAY) or {}).get("lines") or []
                 if l["id"] == pid), None)


async def line(pid="p1"):
    a = await db.audit_get(O, DAY) or {}
    l = next((l for l in a.get("lines") or [] if l["id"] == pid), None)
    return (l["expected"], l["actual"], l["diff"]) if l else None


async def audit_res():
    a = await db.audit_get(O, DAY) or {}
    return ((a.get("short") or {}).get("qty"), (a.get("over") or {}).get("qty"))


async def sale(pid="p1", qty=1, at=None, delivered=None, office=O):
    N[0] += 1
    at = at or now()
    doc = {"order_id": f"O{N[0]}", "status": "delivered", "office_id": office, "stock_office": office,
           "driver": "Водитель", "timestamp": at.isoformat().replace("+00:00", ""),
           "items": [{"id": pid, "qty": qty, "name": NAMES[pid]}]}
    doc["delivered_at"] = (delivered or at).isoformat().replace("+00:00", "")
    await db._db.orders.insert_one(doc)
    SR.base_drop()
    await tick()


async def writeoff(pid="p1", qty=1, state="ok", code="", at=None, own=True):
    wid = await db.writeoff_add({"at": at or now(), "day": DAY, "item": pid, "name": NAMES[pid],
                                 "qty": qty, "kind": "бой", "district": O, "by": "Водитель",
                                 "state": state, "code": code, "own": own, "src": ""})
    SR.base_drop()
    await tick()
    return wid


async def decide(wid, ok=True):
    return await call(SR.handle_writeoff_decide, {"ok": ok, "as": "STAR"}, {"wid": wid})


async def add_supply(sid, plan):
    t = now() - timedelta(hours=3)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": t, "day": "2026-11-11",
        "items": [{"id": pid, "name": NAMES[pid], "asked": q, "qty": q,
                   "by_district": {O: q}, "got": {O: 0}} for pid, q in plan.items()],
        "tasks": {O: {"driver": "Водитель", "driver_id": 1, "claimed_at": t, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0}}})
    SR.base_drop()
    return sid


async def _take(sid, district, product_id, room, at, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") not in ("open", "done"):
        return None
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) > room:
        return None
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": qty, f"items.{i}.scanned": 1,
                 f"tasks.{district}.scanned": 1, f"tasks.{district}.rev": 1},
        "$set": {f"tasks.{district}.last_at": at}})
    return await db.supply_get(sid)


async def _untake(sid, district, product_id, qty=1):
    return True


async def intake(sid, pid, code, owner=False):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{O}.hold": ""}})
    r = await sr.task_scan(sid, O, pid, code, "Водитель", 1, "", owner)
    await tick()
    return r.get("verdict")


async def new_item(pid, code):
    await db._db.qr_locks.delete_many({})
    r = await call(QR.handle_scan, {"code": code, "product_id": pid, "district": O, "mode": "new", "as": "STAR"})
    await tick()
    return r


async def main():
    db._db = AsyncMongoMockClient()["ambar_audit_during"]
    db.supply_take, db.supply_untake = _take, _untake
    SR._biz_day = lambda *a, **k: DAY
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    pay_notify.tell_safe = _tell
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    backdate.notify = _say
    SR._writeoff_after = _say

    print("A. Расход после скана позиции и до последнего скана ревизии")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    await sale()
    await last_then_finish()
    eq("продали 1: в отчёте 5 из 5 без расхождений, снимок 4, склад 4 (было 5)",
       (await line(), await audit_res(), await snap(), await stock(O, "p1")), ((5, 5, 0), (None, None), 4, 4.0))
    eq("Stolichnaya никто не трогал — 3", (await snap("p2"), await stock(O, "p2")), (3, 3.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    await writeoff()
    await last_then_finish()
    eq("списали 1 (сразу согласовано): снимок 4, склад 4 (было 5)", (await line(), await snap(), await stock(O, "p1")),
       ((5, 5, 0), 4, 4.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    r = await SR.move_by_code("A4", O2, 1, "STAR"); assert r.get("ok"), r
    await tick()
    await last_then_finish()
    eq("увезли по коду бутылку, которую камера видела: снимок 4, склад Тикома 4, JVC 1 (было 5 и 1)",
       (await snap(), await stock(O, "p1"), await stock(O2, "p1")), (4, 4.0, 1.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3")          # A4 камера не видела
    r = await SR.move_by_code("A4", O2, 1, "STAR"); assert r.get("ok"), r
    await tick()
    await last_then_finish()
    eq("увезли бутылку, которую камера НЕ видела: в счёте её нет — снимок 4 как у камеры, склад 4",
       (await snap(), await stock(O, "p1"), await stock(O2, "p1")), (4, 4.0, 1.0))

    print("\nB. Приход после скана позиции и до последнего скана ревизии")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    sid = await add_supply("S1", {"p1": 2})
    eq("приёмка двух кодов", [await intake(sid, "p1", "N1"), await intake(sid, "p1", "N2")], ["taken", "taken"])
    await last_then_finish()
    eq("приняли 2: в отчёте 5 из 5, снимок 7, склад 7 (было 5)", (await line(), await audit_res(), await snap(), await stock(O, "p1")),
       ((5, 5, 0), (None, None), 7, 7.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    r = await new_item("p1", "NEW1"); assert r.get("ok") and r.get("new"), r
    await last_then_finish()
    eq("внесли новым товаром 1: снимок 6, склад 6 (было 5)", (await line(), await snap(), await stock(O, "p1")), ((5, 5, 0), 6, 6.0))

    await shelf(other=1); await start(); await see("A0", "A1", "A2", "A3", "A4")
    r = await SR.move_by_code("J0", O, 1, "STAR"); assert r.get("ok"), r
    await tick()
    await last_then_finish()
    eq("привезли по коду с JVC 1: снимок 6, склад Тикома 6, JVC 0 (было 5)",
       (await snap(), await stock(O, "p1"), await stock(O2, "p1")), (6, 6.0, 0.0))

    print("\nC. Пока позицию ещё считают")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    await sale()                                       # продали уже посчитанную
    await see("A0")                                    # к полке вернулись: повторный скан позиции не добавит
    await db._db.audit_scans.update_one({"code": "A4"}, {"$set": {"at": now()}})   # последний скан позиции — после продажи
    await last_then_finish()
    eq("отсканировали все 5, одну продали, пока позицию считали: зачёта нет, снимок 4, склад 4",
       (await line(), await audit_res(), await snap(), await stock(O, "p1")), ((5, 5, 0), (None, None), 4, 4.0))

    await shelf(); await start(); await see("A0", "A1")
    await sale()                                       # продали неотсканированную
    await see("A2", "A3")                              # A4 уехала с заказом
    await last_then_finish()
    eq("продали неотсканированную, пока считали: зачёт 1, недостачи нет, снимок 4, склад 4",
       (await line(), await audit_res(), await snap(), await stock(O, "p1")), ((5, 4, 0), (None, None), 4, 4.0))

    print("\nD. Заказ после скана позиции, довезли после последнего скана ревизии")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    t_order = now()
    await tick()
    await see("S0", "S1", "S2")                        # последний скан ревизии
    await sale(at=t_order, delivered=now())            # довезли уже после него
    await finish()
    eq("снимок 5 (бутылка ещё числилась), склад 4 — вычлось один раз", (await snap(), await stock(O, "p1")), (5, 4.0))

    print("\nE. Наклейка коробки с приёмки: камера видела её «не в реестре»")
    for когда in ("после последнего скана ревизии", "до последнего скана ревизии"):
        await shelf(a=0, s=3); await start()
        await db._db.stock_counts.update_one({"district": O, "day": "2026-11-04"},
                                             {"$set": {"lines.0.actual": 0}})
        for i in range(6): await reg(f"H{i}", "p31", now() - timedelta(days=2), qty=0.5)   # три коробки с кодами
        sid = await add_supply("S2", {"p31": 4})
        await db._db.supplies.update_one({"_id": sid}, {"$set": {"items.0.got." + O: 3}})
        await see("H0", "H1", "H2", "H3", "H4", "H5", "X1", "X2")       # X1, X2 — наклейки четвёртой коробки, в реестре их нет
        if когда.startswith("до"):
            eq("  наклейки внесли приёмкой", [await intake(sid, "p31", "X1", True), await intake(sid, "p31", "X2", True)], ["taken", "taken"])
            await see("S0", "S1", "S2")
        else:
            await see("S0", "S1", "S2")
            eq("  наклейки внесли приёмкой", [await intake(sid, "p31", "X1", True), await intake(sid, "p31", "X2", True)], ["taken", "taken"])
        await finish()
        eq(f"внесли {когда}: в отчёте 4 из 4, излишка нет, на складе 4 коробки (было 5)",
           (await line("p31"), await audit_res(), await stock(O, "p31")), ((4, 4, 0), (None, None), 4.0))

    print("\nF. Позицию не сканировали вовсе, а товар приехал по ходу ревизии")
    for когда in ("до последнего скана", "после последнего скана, до «Завершить»"):
        await shelf(a=0, s=3)
        await db._db.stock_counts.update_one({"district": O, "day": "2026-11-04"}, {"$set": {"lines.0.actual": 0}})
        await start()
        sid = await add_supply("S3", {"p1": 2})
        if когда.startswith("до"):
            await intake(sid, "p1", "N1"); await intake(sid, "p1", "N2")
            await see("S0", "S1", "S2")
        else:
            await see("S0", "S1", "S2")
            await intake(sid, "p1", "N1"); await intake(sid, "p1", "N2")
        sheet = await SR.audit_sheet(O, DAY)
        row = next(r for r in sheet["rows"] if r["id"] == "p1")
        eq(f"  лист до завершения ({когда}): приехавшее не числится недостачей", (row["expected"], row["actual"], row["diff"]), (0, 0, 0))
        await finish()
        eq(f"приняли 2 {когда}: недостачи нет (было 2), на складе 2 (было {'0' if когда.startswith('до') else '2'})",
           (await audit_res(), await stock(O, "p1")), ((None, None), 2.0))

    print("\nG. Списание водителя, согласованное после ревизии")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    wid = await writeoff(state="pending", own=False)
    await last_then_finish()
    eq("ждёт решения: ревизия бутылку посчитала, склад 5", (await snap(), await stock(O, "p1")), (5, 5.0))
    r = await decide(wid, True)
    eq("согласовали после ревизии: снимок 4, склад 4 (было 5 навсегда)", (r.get("ok"), await snap(), await stock(O, "p1")), (True, 4, 4.0))
    eq("в строке ревизии запись о доводке", (next(l for l in (await db.audit_get(O, DAY))["lines"] if l["id"] == "p1").get("late")), -1)

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    wid = await writeoff(state="pending", own=False)
    await last_then_finish()
    await decide(wid, False)
    eq("отклонили: бутылка числится (выйдет недостачей на следующей ревизии), склад 5", (await snap(), await stock(O, "p1")), (5, 5.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    wid = await writeoff(state="pending", own=False, code="A2")
    await last_then_finish()
    await decide(wid, True)
    eq("сканом, код камера видела до боя: снимок 4, склад 4, код списан",
       (await snap(), await stock(O, "p1"), (await db.qr_get("A2")).get("status")), (4, 4.0, "written"))

    await shelf(); await start()
    wid = await writeoff(state="pending", own=False, code="A4")      # разбили ДО скана полки
    await see("A0", "A1", "A2", "A3")
    await last_then_finish()
    eq("разбили до скана, решения нет: камера не видела — недостача 1, снимок 4", (await audit_res(), await snap()), ((1, None), 4))
    await decide(wid, True)
    eq("согласовали: в снимке её и не было — снимок 4, склад 4 (второй раз не вычлась)", (await snap(), await stock(O, "p1")), (4, 4.0))

    # То же решение кнопкой под фотографией в боте владельца — другой процесс,
    # своя дверь (owner_bot.on_writeoff → db.writeoff_decide).
    import owner_bot as OB, writeoff_msg as WM
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    wid = await writeoff(state="pending", own=False)
    await last_then_finish()
    OB.is_allowed = lambda uid: True
    OB._writeoff_after = _say
    class _Q:
        data = f"{WM.CB_PREFIX}:ok:{wid}"
        message = None
        async def answer(self, *a, **k): return None
    upd = type("Upd", (), {"callback_query": _Q(), "effective_user": type("U", (), {"id": 1, "full_name": "Старший"})()})()
    await OB.on_writeoff(upd, None)
    eq("согласовали кнопкой в боте владельца: снимок 4, склад 4",
       ((await db.writeoff_get(wid)).get("state"), await snap(), await stock(O, "p1")), ("ok", 4, 4.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    await last_then_finish()
    wid = await writeoff(state="pending", own=False)                   # разбили после ревизии
    await decide(wid, True)
    eq("разбили после ревизии: снимок 5, склад 4 — вычитает сам склад, один раз", (await snap(), await stock(O, "p1")), (5, 4.0))

    print("\nH. Бутылка охране")
    async def guard(code, at):
        item = {"id": "g1", "kind": "guard", "status": "pending", "code": code, "bottle_id": "p1",
                "bottle": NAMES["p1"], "district": O, "at": at.isoformat(), "label": code}
        await db._db.driver_days.insert_one({"day": DAY, "driver": "Водитель", "extras": [item]})
        return item

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    item = await guard("A3", now())
    await last_then_finish()
    eq("отсканирована и отдана до снимка, решения нет: склад 5", (await audit_res(), await snap(), await stock(O, "p1")), ((None, None), 5, 5.0))
    await EX._guard_bottle_gone(item, DAY, "Водитель", 1)
    eq("согласовали после ревизии: снимок 4, склад 4 (было 5)", (await snap(), await stock(O, "p1")), (4, 4.0))

    await shelf(); await start()
    item = await guard("A4", now())                                    # отдана до скана полки
    await tick()
    await see("A0", "A1", "A2", "A3")
    await last_then_finish()
    eq("отдана до скана: зачёт, недостачи нет, снимок 4", (await audit_res(), await snap()), ((None, None), 4))
    await EX._guard_bottle_gone(item, DAY, "Водитель", 1)
    eq("согласовали: снимок 4, склад 4 — второй раз не вычлась (9–10 ноя)", (await snap(), await stock(O, "p1")), (4, 4.0))

    print("\nI. Поздняя наклейка и возобновление")
    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "ZZ1")   # A4 под камерой с наклейкой ZZ1, которой нет в реестре
    await sale()
    await last_then_finish()
    eq("недостача 1 (A4 «не видели»), продана 1 после скана: снимок 3", (await audit_res(), await snap()), ((1, None), 3))
    r = await new_item("p1", "ZZ1")
    eq("наклейку внесли — засчитана в ревизию: недостачи нет, снимок 4 (5 под камерой − 1 продана), склад 4",
       (r.get("ok"), await audit_res(), await snap(), await stock(O, "p1")), (True, (None, None), 4, 4.0))

    await shelf(); await start(); await see("A0", "A1", "A2", "A3", "A4")
    await sale()
    await last_then_finish()
    r = await call(SR.handle_audit_reopen, {"district": O, "day": DAY, "as": "STAR"})
    eq("возобновили: пересчёт стёрт, склад от прошлого снимка 4", (r.get("ok"), await stock(O, "p1")), (True, 4.0))
    await finish()
    eq("завершили заново: снимок 4, склад 4 — доводка один раз", (await snap(), await stock(O, "p1")), (4, 4.0))

    print("\n" + ("ПРОВАЛЕНО: " + "; ".join(FAIL) if FAIL else "Все проверки прошли."))
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
