"""Район можно выключить из заявки (владелец, 24 сен 2026: «закупка нужна
честно прям именно на один билдинг, дай все остальные вручную отменить и
отправить на Барракуду»).

Выключенный район не идёт ни в итоги, ни в файл магазину; расчёт по нему
остаётся виден, чтобы было понятно, от чего отказались. Решение живёт один
день. Последний район выключить нельзя: пустая заявка — не заявка.

    python3 tools/test_order_district_off.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, stock_routes as sr, supply_routes as sup               # noqa: E402
from config_offices import OFFICE_IDS                             # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-24"
A, B = OFFICE_IDS[0], OFFICE_IDS[1]


async def завести():
    db._db = AsyncMongoMockClient()["ambar_ord_off"]
    sr.base_drop()
    sr._biz_day = lambda *a, **k: ДЕНЬ
    cat = sr._catalog()
    pid = next(iter(cat))
    t0 = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    # Пересчёт с нулём на двух районах и нормой 10 — заявка попросит по 10.
    for oid in OFFICE_IDS:
        await db.save_stock_count(oid, "2026-09-14", {
            "district": oid, "day": "2026-09-14", "counted_at": t0, "first_time": True,
            "counted_by": 0,
            "lines": [{"id": pid, "name": cat[pid].get("name", ""), "price": 100, "unit": 1,
                       "actual": 0, "counted": True}]})
        await db.set_stock_norm(oid, pid, 10)
    return pid


async def зови(h, body):
    r = make_mocked_request("POST", "/x")
    r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    async def js(): return body
    r.json = js
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    pid = await завести()
    d = await sr.order_rows(ДЕНЬ)
    всего = d["total_qty"]
    eq("заявка просит по всем районам", всего, 10 * len(OFFICE_IDS))
    eq("выключенных нет", d["off"], [])

    st, r = await зови(sr.handle_order_district, {"day": ДЕНЬ, "district": A, "off": True})
    eq("район выключили", (st, r["off"]), (200, [A]))
    d = await sr.order_rows(ДЕНЬ)
    eq("итог уменьшился ровно на него", d["total_qty"], всего - 10)
    клетка = next(r_ for r_ in d["all_rows"] if r_["id"] == pid)["cells"][A]
    eq("в клетке ноль, но расчёт видно", (клетка["need"], клетка["calc"], клетка["off"]), (0, 10, True))
    eq("в списке районов он помечен",
       next(x for x in d["districts"] if x["id"] == A)["off"], True)
    eq("соседа не тронули",
       next(r_ for r_ in d["all_rows"] if r_["id"] == pid)["cells"][B]["need"], 10)

    # Файл магазину: колонки выключенного района быть не должно.
    raw, name = await sup._build_book(ДЕНЬ)
    import io as _io
    from openpyxl import load_workbook
    from config_offices import OFFICE_CODES
    ws = load_workbook(_io.BytesIO(raw)).active
    # Шапка таблицы — строка, где стоит «Code»; колонки районов идут после цены.
    шапка = next([c.value for c in row] for row in ws.iter_rows(min_row=1, max_row=8)
                 if "Code" in [c.value for c in row])
    колонки = [str(v) for v in шапка if v]
    eq("в файле нет колонки выключенного района",
       any(str(v).startswith(OFFICE_CODES[A] + " ") for v in колонки), False)
    eq("а работающие районы в файле есть",
       sum(1 for v in колонки if any(str(v).startswith(OFFICE_CODES[o] + " ")
                                     for o in OFFICE_IDS)), len(OFFICE_IDS) - 1)

    st, r = await зови(sr.handle_order_district, {"day": ДЕНЬ, "district": A, "off": False})
    eq("вернули — снова в заявке", (st, r["off"]), (200, []))
    eq("и итог вернулся", (await sr.order_rows(ДЕНЬ))["total_qty"], всего)

    for oid in OFFICE_IDS[:-1]:
        await зови(sr.handle_order_district, {"day": ДЕНЬ, "district": oid, "off": True})
    st, r = await зови(sr.handle_order_district,
                       {"day": ДЕНЬ, "district": OFFICE_IDS[-1], "off": True})
    eq("последний район выключить нельзя", (st, r.get("error")), (409, "last_district"))
    d = await sr.order_rows(ДЕНЬ)
    eq("один район всё-таки везём", d["total_qty"], 10)

    st, r = await зови(sr.handle_order_district, {"day": ДЕНЬ, "district": "нет-такого", "off": True})
    eq("чужой район — отказ", (st, r.get("error")), (400, "unknown_district"))

    # «Снять правки» количеств выключение не отменяет.
    await db.zayavka_edit_set(ДЕНЬ, pid, OFFICE_IDS[-1], 3)
    eq("правка встала", (await sr.order_rows(ДЕНЬ))["total_qty"], 3)
    await db.zayavka_edit_clear(ДЕНЬ)
    d = await sr.order_rows(ДЕНЬ)
    eq("правки сняты, выключенные районы остались", (d["total_qty"], len(d["off"])),
       (10, len(OFFICE_IDS) - 1))

    # Завтрашняя заявка — чистая.
    d2 = await sr.order_rows("2026-09-25")
    eq("назавтра заявка просит всё", d2["off"], [])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
