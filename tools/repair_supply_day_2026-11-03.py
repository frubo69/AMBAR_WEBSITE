"""Поставка 3 ноября 2026 легла на 2 ноября. Ответ магазина загрузили 3-го в
09:14 по Дубаю, учётные сутки в этот час — ещё 2 ноября: поставка
S261103-051452 получила day 2026-11-02, а её ответ правками лёг на заявку
1 ноября (закупка 2-го), у которой уже была своя поставка S261102-062248, —
ответ той поставки при этом затёрт. Владелец, 4 ноя 2026: «отработали заявку
3 числа, значит и пиши её 3-м числом». Сама загрузка с этого дня датирует
поставку днём закупки заявки (supply_routes.answer_day).

Скрипт:
  1. поставке S261103-051452 ставит day 2026-11-03;
  2. её записям журнала «ответ загружен» — day 2026-11-03, order_day 2026-11-02;
  3. заявке 2 ноября (закупка 3-го) записывает ответ из S261103-051452 тем же
     путём, что загрузка (_answer_to_order); время правок — время загрузки;
  4. заявке 1 ноября возвращает ответ из S261102-062248 тем же путём; клетки,
     которые тронул только чужой ответ, снимает — список печатается.
Копия документов до правки — /root/repair_supply_day_*.json.

    cd /opt/ambar && ./venv/bin/python tools/repair_supply_day_2026-11-03.py        # показать
    cd /opt/ambar && DRY=0 ./venv/bin/python tools/repair_supply_day_2026-11-03.py  # применить
"""
import asyncio, json, os, sys, logging
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)
from dotenv import load_dotenv; load_dotenv(".env")   # noqa: E402
import db                                             # noqa: E402
import supply_routes as sr                            # noqa: E402

SID3, SID2 = "S261103-051452", "S261102-062248"
DOC3, DOC2 = "2026-11-02", "2026-11-01"     # документы заявок: смена 2-го (закупка 3-го), смена 1-го (закупка 2-го)
DAY3 = "2026-11-03"
DRY = os.getenv("DRY", "1") != "0"
BACKUP_DIR = os.getenv("REPAIR_DIR", "/root")
WHY = "владелец, 4 ноя 2026: отработали заявку 3 числа — пиши её 3-м числом"
KINDS = {"$in": ["answer", "answer_replaced"]}


def _cells(items: list, asked_full: dict) -> dict:
    """Какие клетки (позиция, район) → число записал бы _answer_to_order."""
    дают = {it["id"]: (it.get("by_district") or {}) for it in items}
    районы = set()
    for by in list(дают.values()) + list((asked_full or {}).values()):
        районы |= {str(k) for k in (by or {})}
    out = {}
    for pid in set(дают) | set(asked_full or {}):
        по_районам = дают.get(pid) or {}
        for oid in районы:
            было = float(((asked_full or {}).get(pid) or {}).get(oid) or 0)
            стало = float(по_районам.get(oid) or 0)
            if было == 0 and стало == 0:
                continue
            out[(pid, oid)] = стало
    return out


def _name(cat, pid): return (cat.get(pid) or {}).get("name") or pid


def _n(by: dict) -> int: return sum(len(v or {}) for v in (by or {}).values())


async def main():
    await db.connect()
    cat = sr._catalog_by_id()
    sup3 = await db._db.supplies.find_one({"_id": SID3})
    sup2 = await db._db.supplies.find_one({"_id": SID2})
    z3 = await db._db.zayavki.find_one({"_id": DOC3})
    z2 = await db._db.zayavki.find_one({"_id": DOC2})
    e3 = await db._db.zayavka_edits.find_one({"_id": DOC3}) or {}
    e2 = await db._db.zayavka_edits.find_one({"_id": DOC2}) or {}
    ev = [x async for x in db._db.supply_events.find({"sid": SID3, "kind": KINDS})]
    нет = [n for n, v in ((SID3, sup3), (SID2, sup2), (f"заявка {DOC3}", z3), (f"заявка {DOC2}", z2)) if not v]
    if нет:
        print("не найдено:", ", ".join(нет)); return 1
    for sid, s in ((SID3, sup3), (SID2, sup2)):
        print(f"{sid}: day={s.get('day')} status={s.get('status')} at={s.get('at')} "
              f"replaced_at={s.get('replaced_at')} позиций {len(s.get('items') or [])}")
    for doc, z, e in ((DOC3, z3, e3), (DOC2, z2, e2)):
        print(f"заявка {doc}: buy_day={z.get('buy_day')} frozen_at={z.get('frozen_at')} at={z.get('at')} "
              f"просили {len(z.get('by') or {})} поз.; правок сейчас {_n(e.get('by'))} клеток, at={e.get('at')}")
    print(f"журнал «ответ загружен» у {SID3}: " + ("; ".join(
        f"{x.get('kind')} day={x.get('day')} order_day={(x.get('data') or {}).get('order_day')}" for x in ev) or "нет"))
    if z3.get("buy_day") != DAY3:
        print(f"\nу заявки {DOC3} день закупки {z3.get('buy_day')!r}, ждал {DAY3} — не трогаю"); return 1
    if sup3.get("day") == DAY3 and all(x.get("day") == DAY3 for x in ev):
        print("\nпоставка уже на 3 ноября — делать нечего"); return 0

    cells3 = _cells(sup3.get("items") or [], z3.get("by") or {})     # ответ 3 ноя → документ 2 ноя
    cells2 = _cells(sup2.get("items") or [], z2.get("by") or {})     # ответ 2 ноя → документ 1 ноя
    by3, by2 = e3.get("by") or {}, e2.get("by") or {}
    cur = lambda by, pid, oid: (by.get(pid) or {}).get(oid)
    key = lambda kv: (_name(cat, kv[0][0]), kv[0][1])

    print(f"\n1. {SID3}: day {sup3.get('day')} → {DAY3}")
    print(f"2. журнал {SID3}: {len(ev)} зап. → day {DAY3}, order_day {DOC3}")
    print(f"3. заявка {DOC3} ← ответ {SID3}: {len(cells3)} клеток, из них меняются:")
    for (pid, oid), v in sorted(cells3.items(), key=key):
        было = cur(by3, pid, oid)
        if было != v:
            print(f"     {_name(cat, pid)} · {oid}: {'—' if было is None else f'{было:g}'} → {v:g}")
    лишние = sorted(k for k in cells3 if k not in cells2 and cur(by2, *k) is not None)
    print(f"4. заявка {DOC2} ← ответ {SID2}: {len(cells2)} клеток, из них меняются:")
    for (pid, oid), v in sorted(cells2.items(), key=key):
        было = cur(by2, pid, oid)
        if было != v:
            print(f"     {_name(cat, pid)} · {oid}: {'—' if было is None else f'{было:g}'} → {v:g}")
    print(f"   снять клеток, которые тронул только чужой ответ: {len(лишние)}")
    for pid, oid in лишние:
        print(f"     {_name(cat, pid)} · {oid}: {cur(by2, pid, oid):g} → —")
    if DRY:
        print("\nDRY=1 — ничего не меняю. Применить: DRY=0"); return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"repair_supply_day_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"supply3": sup3, "supply2": {k: sup2.get(k) for k in ("_id", "day", "at", "replaced_at")},
                   "zayavka3": z3, "zayavka2": z2, "edits3": e3, "edits2": e2, "events": ev},
                  f, ensure_ascii=False, default=str, indent=1)
    now = datetime.now(timezone.utc)
    r = await db._db.supplies.update_one(
        {"_id": SID3}, {"$set": {"day": DAY3, "day_moved_from": sup3.get("day"), "day_moved_by": WHY, "day_moved_at": now}})
    print("1.", "поставка перенесена" if r.modified_count else "поставка НЕ изменена")
    n = 0
    for x in ev:
        r = await db._db.supply_events.update_one(
            {"_id": x["_id"]}, {"$set": {"day": DAY3, "data": {**(x.get("data") or {}), "order_day": DOC3}}})
        n += r.modified_count
    print("2. журнал:", n, "зап.")
    await sr._answer_to_order(DOC3, sup3.get("items") or [], z3.get("by") or {})
    await db._db.zayavka_edits.update_one({"_id": DOC3}, {"$set": {"at": sup3.get("replaced_at") or sup3.get("at") or now}})
    print("3. заявка", DOC3, "переписана по ответу", SID3)
    for pid, oid in лишние:
        await db.zayavka_edit_set(DOC2, pid, oid, None)
    await sr._answer_to_order(DOC2, sup2.get("items") or [], z2.get("by") or {})
    await db._db.zayavka_edits.update_one({"_id": DOC2}, {"$set": {"at": sup2.get("replaced_at") or sup2.get("at") or now}})
    print("4. заявка", DOC2, "возвращена к ответу", SID2, f"· снято {len(лишние)}")
    s = await db._db.supplies.find_one({"_id": SID3}, {"day": 1})
    e = [x async for x in db._db.supply_events.find({"sid": SID3, "kind": KINDS})]
    print(f"\nпосле: {SID3} day={s.get('day')} · журнал {[(x.get('day'), (x.get('data') or {}).get('order_day')) for x in e]}"
          f" · правок {DOC3}: {_n((await db._db.zayavka_edits.find_one({'_id': DOC3}) or {}).get('by'))}"
          f" · правок {DOC2}: {_n((await db._db.zayavka_edits.find_one({'_id': DOC2}) or {}).get('by'))}")
    print("копия до правки:", path)
    return 0


sys.exit(asyncio.run(main()))
