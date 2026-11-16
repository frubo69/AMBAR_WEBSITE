"""Четыре ошибки ревизии, которые уже срабатывали на проде (владелец, 16 ноя
2026, на «Начать чинить уже сработавшие ошибки по списку?»: «Да, чини»).

  J. Код другого района под камерой не считается своим. Было: такой скан шёл
     в общий счёт позиции и закрывал дыру от своей пропавшей бутылки — здесь
     «сошлось», недостача вылезала у района, где бутылка числится (Don Julio
     1942 и Clase Azul: стоят на B3, списаны недостачей с B4, 3400 AED).
     Теперь: своя недостача и чужой излишек — раздельно; «Внести на район»
     перевозит чужую сюда, и у её района недостачи нет.
  K. Початая коробка пива. На коробке две наклейки по полкоробки, продажа
     банками ни одну не гасит: числится с половиной, камера видит на
     полкоробки больше. Было: излишек +0,5 сегодня, −0,5 на следующей ревизии.
     Теперь вторая наклейка початой коробки в счёт не идёт.
  L. Сначала коды не из реестра. Было: недостачу можно было списать, пока
     бутылка с неизвестной наклейкой стояла в «QR код не внесён» (B3, 8 ноя
     2026). Теперь сервер решение по недостаче не принимает, пока такие коды
     не внесены или не убраны.
  M. Не та полка. Было: 12 ноя полку JVC 25 минут сканировали в ревизии B2 —
     382 скана. Теперь сервер считает, сколько бутылок одного чужого района
     идут подряд, сканер на пятой останавливается, а хвост можно убрать.

    python3 tools/test_audit_shelf.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as SR, qr_routes as QR, driver_audit as DA   # noqa: E402
import owner_routes, pay_notify, backdate                          # noqa: E402
from config_offices import OFFICE_CODES                            # noqa: E402

U = timezone.utc
DAY, DAY2 = "2026-11-15", "2026-11-16"
O, O2 = "silicon", "alguses"
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


async def call(handler, body, match=None, status=False):
    req = make_mocked_request("POST", "/x", match_info=match or {})
    req["owner_id"] = 1
    async def _json(): return body
    req.json = _json
    r = await unwrap(handler)(req)
    out = json.loads(r.body)
    return (r.status, out) if status else out


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


async def stock(o, pid, day=DAY):
    SR.base_drop()
    have = ((await SR._district_base(day)).get(o) or {}).get("have_exact") or {}
    return float(have.get(pid, 0))


async def wipe():
    for c in ("qr_codes", "qr_found", "qr_locks", "stock_counts", "stock_audits", "audit_scans",
              "audit_scans_dropped", "orders", "writeoffs", "stock_transfers", "supplies"):
        await db._db[c].delete_many({})
    SR.base_drop()


OLD = None


async def base(own: dict, other: dict = None):
    """Прошлый снимок 9 дней назад: own — {позиция: единиц} у района O, other —
    у O2. Коды заводятся отдельно."""
    global OLD
    await wipe()
    OLD = now() - timedelta(days=9)
    await count(O, "2026-11-04", OLD + timedelta(hours=1), own)
    await count(O2, "2026-11-04", OLD + timedelta(hours=1), other or {p: 0 for p in own})


async def start(day=DAY):
    st, _ = await SR.audit_start(O, day, 1, "Старший"); assert st == 200, st
    await tick()


async def see(*codes, day=DAY, ses=""):
    out = []
    for c in codes:
        out.append(await SR.audit_scan(O, day, c, 1, "Старший", ses))
        await tick()         # база хранит время до миллисекунды; сканы идут по одному
    return out


async def finish(day=DAY):
    st, rep = await SR.audit_finish(O, day, 1, "Старший"); assert st == 200, (st, rep)
    return rep


async def snap(pid, day=DAY, o=O):
    return next((l["actual"] for l in (await db.get_stock_count(o, day) or {}).get("lines") or []
                 if l["id"] == pid), None)


def ln(rep_or_sheet, pid):
    r = next((r for r in rep_or_sheet.get("rows") or [] if r["id"] == pid), None)
    return r and (r["expected"], r["actual"], r["diff"], r.get("other") or 0)


async def sale(pid, bottles, office=O):
    N[0] += 1
    t = now().isoformat().replace("+00:00", "")
    await db._db.orders.insert_one({
        "order_id": f"O{N[0]}", "status": "delivered", "office_id": office, "stock_office": office,
        "driver": "Водитель", "timestamp": t, "delivered_at": t,
        "items": [{"id": pid, "qty": bottles, "name": NAMES[pid]}]})
    SR.base_drop()
    await tick()


async def main():
    db._db = AsyncMongoMockClient()["ambar_audit_shelf"]
    async def _say(*a, **k): return []
    async def _tell(*a, **k): return 1
    pay_notify.tell_safe = _tell
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    backdate.notify = _say
    SR._biz_day = lambda *a, **k: DAY
    c2 = OFFICE_CODES[O2]

    print("J. Код другого района под камерой")
    await base({"p1": 1, "p2": 3}, {"p1": 1, "p2": 0})
    await reg("OWN1", "p1", OLD)                        # своя бутылка района — её на полке нет
    await reg("FOR1", "p1", OLD, district=O2)           # бутылка соседнего района — стоит здесь
    for i in range(3): await reg(f"S{i}", "p2", OLD)
    await start()
    await see("FOR1", "S0", "S1", "S2")
    sheet = await SR.audit_sheet(O, DAY)
    eq("лист: числится 1, лежит 1, своих не хватает 1, чужая 1",
       (ln(sheet, "p1"), next(r for r in sheet["rows"] if r["id"] == "p1")["other_from"]), ((1, 1, 1, 1), c2))
    eq("итоги листа: недостача 1 и излишек 1 (было: сошлось)",
       (sheet["totals"]["short_qty"], sheet["totals"]["over_qty"], sheet["totals"]["mismatched"]), (1, 1, 1))
    eq("своя бутылка — в списке «не поднесли к камере»",
       [m["code"] for m in next(r for r in sheet["rows"] if r["id"] == "p1").get("missing") or []], ["OWN1"])
    d = DA._sheet(sheet, await db.audit_get(O, DAY) or {})
    eq("лист водителя: нашли 0 из 1 своих; недостача 1, лишних 1",
       ([(r["actual"], r["coded"]) for r in d["rows"] if r["id"] == "p1"], d["totals"]["short"], d["totals"]["over"]),
       ([(0, 1)], 1, 1))
    rep = await finish()
    a = rep["audit"]
    eq("отчёт: недостача 1 своей", [(l["id"], l["qty"]) for l in (a["short"] or {}).get("lines") or []], [("p1", 1)])
    eq("отчёт: излишек 1 — числится на соседнем районе, код назван",
       [(l["id"], l["qty"], l["other"], [c["code"] for c in l["codes"]]) for l in (a["over"] or {}).get("lines") or []],
       [("p1", 1, 1, ["FOR1"])])
    eq("снимок района — что лежит: 1; у соседа числится 1", (await snap("p1"), await stock(O, "p1"), await stock(O2, "p1")), (1, 1.0, 1.0))
    r = await call(SR.handle_audit_over, {"district": O, "day": DAY, "as": "Старший"})
    eq("«Внести на район»: перевезена 1", ((r["audit"]["over"] or {}).get("moved"), (await db.qr_get("FOR1"))["district"]), (1, O))
    eq("здесь 1 (не дважды), у соседа 0 — недостачи там не будет (было: 1 → списание с него)",
       (await stock(O, "p1"), await stock(O2, "p1")), (1.0, 0.0))
    r = await call(SR.handle_audit_short, {"district": O, "day": DAY, "blame": False, "as": "Старший"})
    eq("недостача своей бутылки решена здесь, ревизия закрыта", (bool(r["audit"]["short"]["resolved_at"]), r["audit"]["state"]), (True, "closed"))

    await base({"p1": 2, "p2": 0}, {"p1": 1, "p2": 0})
    await reg("OWN1", "p1", OLD); await reg("OWN2", "p1", OLD); await reg("FOR1", "p1", OLD, district=O2)
    await start(); await see("OWN1", "OWN2", "FOR1")
    rep = await finish()
    eq("свои на месте и одна чужая: недостачи нет, излишек 1 — как раньше",
       (rep["audit"]["short"], (rep["audit"]["over"] or {}).get("qty"), ln(rep, "p1")), (None, 1, (2, 3, 0, 1)))

    print("\nK. Початая коробка пива")
    await base({"p31": 3, "p2": 0})
    for i in range(6): await reg(f"H{i}", "p31", OLD, qty=0.5)       # три коробки, шесть наклеек
    await sale("p31", 12)                                              # продали 12 банок
    eq("числится 2,5", await stock(O, "p31"), 2.5)
    await start(); await see(*[f"H{i}" for i in range(6)])             # на початой коробке обе наклейки
    sheet = await SR.audit_sheet(O, DAY)
    row = next(r for r in sheet["rows"] if r["id"] == "p31")
    eq("лист: числится 2,5, лежит 2,5, расхождения нет; вторая наклейка початой коробки — 0,5",
       ((row["expected"], row["actual"], row["diff"]), row["open"]), ((2.5, 2.5, 0), 0.5))
    rep = await finish()
    eq("отчёт: излишка нет (было +0,5), ревизия закрыта сразу", (rep["audit"]["over"], rep["audit"]["short"], rep["audit"]["state"]),
       (None, None, "closed"))
    eq("снимок 2,5 (было 3), склад 2,5", (await snap("p31"), await stock(O, "p31")), (2.5, 2.5))
    await sale("p31", 12)                                              # допродали початую коробку
    SR._biz_day = lambda *a, **k: DAY2
    eq("числится 2", await stock(O, "p31", DAY2), 2.0)
    await start(DAY2); await see("H0", "H1", "H2", "H3", day=DAY2)     # коробку выбросили с обеими наклейками
    rep = await finish(DAY2)
    eq("следующая ревизия: 2 из 2, недостачи нет (было −0,5)", (ln(rep, "p31"), rep["audit"]["short"], rep["audit"]["over"]),
       (None, None, None))
    eq("снимок следующей ревизии 2", await snap("p31", DAY2), 2)
    SR._biz_day = lambda *a, **k: DAY

    await base({"p31": 3, "p2": 0})
    for i in range(7): await reg(f"H{i}", "p31", OLD, qty=0.5)
    await start(); await see(*[f"H{i}" for i in range(7)])
    rep = await finish()
    eq("числится ровно 3 (початой коробки нет), камера 3,5 — излишек 0,5 остаётся излишком",
       ((rep["audit"]["over"] or {}).get("qty"), await snap("p31")), (0.5, 3.5))

    await base({"p31": 3, "p2": 0})
    for i in range(6): await reg(f"H{i}", "p31", OLD, qty=0.5)
    await sale("p31", 12)
    await start(); await see(*[f"H{i}" for i in range(4)])             # одной коробки нет вовсе
    rep = await finish()
    eq("числится 2,5, камера 2 — недостача 0,5, как и было", ((rep["audit"]["short"] or {}).get("qty"), await snap("p31")), (0.5, 2))

    print("\nL. Сначала коды не из реестра, потом недостача")
    await base({"p1": 3, "p2": 0})
    for i in range(3): await reg(f"A{i}", "p1", OLD)
    await start(); await see("A0", "A1", "RELABEL")                    # на третьей бутылке наклейка не из реестра
    rep = await finish()
    eq("итог: недостача 1, кодов не из реестра 1", ((rep["audit"]["short"] or {}).get("qty"), rep["audit"]["alien"], rep["alien"]["left"]), (1, 1, 1))
    st, r = await call(SR.handle_audit_short, {"district": O, "day": DAY, "blame": False, "as": "Старший"}, status=True)
    eq("решить недостачу нельзя: сначала код (было: списывалась)", (st, r.get("error"), r.get("left")), (409, "alien_first", 1))
    eq("списаний нет", await db._db.writeoffs.count_documents({}), 0)
    await db._db.qr_locks.delete_many({})
    r = await call(QR.handle_scan, {"code": "RELABEL", "product_id": "p1", "district": O, "mode": "new", "as": "Старший"})
    a = await db.audit_get(O, DAY)
    eq("код внесли — бутылка засчитана в ревизию: недостачи нет, ревизия закрыта, списывать нечего",
       (r.get("ok"), a.get("short"), bool(a.get("closed_at")), await stock(O, "p1")), (True, None, True, 3.0))

    await base({"p1": 3, "p2": 0})
    for i in range(3): await reg(f"A{i}", "p1", OLD)
    await start(); await see("A0", "A1", "GARBAGE")
    await finish()
    st, r = await call(SR.handle_audit_short, {"district": O, "day": DAY, "blame": False, "as": "Старший"}, status=True)
    eq("ошибочный код: недостача тоже ждёт", (st, r.get("error")), (409, "alien_first"))
    r = await call(SR.handle_audit_alien_drop, {"district": O, "day": DAY, "code": "GARBAGE", "as": "Старший"})
    eq("код убрали из итога", (r.get("ok"), r.get("alien_left")), (True, 0))
    st, r = await call(SR.handle_audit_short, {"district": O, "day": DAY, "blame": False, "as": "Старший"}, status=True)
    eq("теперь недостача решается и ревизия закрыта", (st, bool(r["audit"]["short"]["resolved_at"]), r["audit"]["state"]), (200, True, "closed"))

    print("\nM. Не та полка")
    await base({"p1": 2, "p2": 0}, {"p1": 8, "p2": 0})
    await reg("OWN1", "p1", OLD); await reg("OWN2", "p1", OLD)
    for i in range(8): await reg(f"F{i}", "p1", OLD, district=O2)
    await start()
    r = await see("OWN1", "F0", "F1", "F2", "F3")
    eq("чужие подряд: 0, 1, 2, 3, 4", [x["foreign_run"] for x in r], [0, 1, 2, 3, 4])
    r = await see("OWN2", "F4")
    eq("своя бутылка обрывает хвост", [x["foreign_run"] for x in r], [0, 1])
    r = await see("F5", "F6", "F7", "F0")
    eq("пятая подряд — порог; повтор того же кода хвост не растит",
       ([x["foreign_run"] for x in r], r[0]["foreign_stop"], r[2]["home_code"], r[2]["new"], r[3]["new"]),
       ([2, 3, 4, 4], 5, c2, True, False))
    await db._db.audit_scans.delete_many({"code": {"$in": ["F0", "F1", "F2", "F3"]}})   # первые четыре убрали крестиком
    r = await see("F0")
    eq("пятая чужая подряд", (r[0]["foreign_run"], r[0]["foreign_run"] >= r[0]["foreign_stop"]), (5, True))
    eq("в счёте прохода 7 бутылок Absolut", (await db.audit_scan_counts(O, DAY)).get("p1"), 7)
    st, r = await call(SR.handle_audit_scan_foreign_drop, {"district": O, "day": DAY, "home": O2, "as": "Старший"}, status=True)
    eq("«Убрать и выйти»: хвост снят — 5 сканов", (st, r.get("removed"), sorted(r.get("codes") or []), r.get("total")),
       (200, 5, ["F0", "F4", "F5", "F6", "F7"], 2))
    eq("сканы не стёрты — в архиве с пометкой",
       (await db._db.audit_scans_dropped.count_documents({"dropped_why": "foreign"}),
        await db._db.audit_scans.count_documents({"district": O, "day": DAY})), (5, 2))
    rep = await finish()
    eq("ревизия своей полки: 2 из 2, излишка нет (было бы: 5 чужих → «Внести на район»)",
       (ln(rep, "p1"), rep["audit"]["over"], await stock(O2, "p1")), (None, None, 8.0))
    st, r = await call(SR.handle_audit_scan_foreign_drop, {"district": O, "day": DAY, "home": O2}, status=True)
    eq("у завершённой ревизии хвост не снимают", (st, r.get("error")), (409, "finished"))
    st, r = await call(SR.handle_audit_scan_foreign_drop, {"district": O, "day": DAY, "home": O}, status=True)
    eq("свой район — не чужая полка", st, 400)

    print("\n" + ("ПРОВАЛЕНО: " + "; ".join(FAIL) if FAIL else "Все проверки прошли."))
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
