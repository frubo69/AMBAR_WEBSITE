"""Закрыть перемещения, которые уже развезли по билдингам (владелец, 21 сен
2026: «весь этот товар уже развезли по билдингам; просто внеси его на те
склады, куда он должен был уехать, а эти перемещения все в историю»).

Вносить на склад НЕЧЕГО: бутылку переносит скан отдающего, и все коды этих
передач уже числятся на районе-получателе (проверено перед запуском — B4 и
B5, все active). Не хватает только отметки получателя, из-за которой заявки
висят открытыми и держат смену обеим сторонам.

Поэтому скрипт делает ровно одно: ставит передачам отметку «принято» от имени
старшего, с заметкой, что приёмку не сканировали, — и закрывает задачи и
заявки, у которых после этого не осталось незакрытых пар. Склад не трогает
вовсе.

Копии заявок — в /opt/ambar/backups/. Без --apply только показывает.
    venv/bin/python tools/move_close_2026-09-21.py [--apply]
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import move_routes as MV
from config_offices import OFFICE_CODES

APPLY = "--apply" in sys.argv
C = lambda o: OFFICE_CODES.get(o, o)
ЗАМЕТКА = "Товар развезён по билдингам, приёмку не сканировали — закрыто старшим 21.09"


async def main():
    await db.connect()
    now = datetime.now(timezone.utc)
    docs = await db.move_orders_open()
    if not docs:
        print("открытых заявок нет"); return 0

    план = []                       # (mid, to, src, got, кто, что)
    for doc in docs:
        for to, t in sorted((doc.get("tasks") or {}).items()):
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            give = t.get("give") or {}
            for src in sorted({l.get("from") for l in (t.get("lines") or [])} - {None}):
                g = give.get(src) or {}
                ls = [l for l in (t.get("lines") or []) if l.get("from") == src]
                got = sum(float(l.get("got") or 0) for l in ls)
                if g.get("accepted_at") or got <= 1e-9:
                    continue        # уже принято или не начинали — не наше дело
                что = ", ".join(f"{l.get('name','')} {float(l.get('got') or 0):g}"
                                for l in ls if float(l.get("got") or 0) > 1e-9)
                план.append((doc["_id"], to, src, got, g.get("driver") or "—", что))

    for mid, to, src, got, кто, что in план:
        print(f"{C(src)}→{C(to)} · {got:g} ед · отдал {кто} · {что[:90]}")
    print(f"\nитого закрыть: {len(план)} "
          f"{'передача' if len(план) == 1 else 'передач'}")
    if not APPLY:
        print("это просмотр; сделать — с --apply"); return 0

    from bson import json_util
    os.makedirs("/opt/ambar/backups", exist_ok=True)
    for doc in docs:
        path = f"/opt/ambar/backups/move_{doc['_id']}_{now:%Y%m%d-%H%M%S}.json"
        open(path, "w", encoding="utf-8").write(json_util.dumps(doc, ensure_ascii=False, indent=1))
        print("копия:", path)

    закрыто = 0
    for mid, to, src, got, кто, что in план:
        ок = await db.move_give_accept(mid, to, src, {
            "accepted_at": now, "accepted_by": "STAR", "accepted_by_id": 0,
            "accept_ok": True, "accept_lines": [], "accept_note": ЗАМЕТКА})
        закрыто += bool(ок)
        print(f"  {C(src)}→{C(to)}: принято — {bool(ок)}")
        doc = await db.move_order_get(mid)
        task = (doc.get("tasks") or {}).get(to) or {}
        srcs = {l.get("from") for l in (task.get("lines") or [])} - {None}
        if srcs and all(((task.get("give") or {}).get(x) or {}).get("accepted_at") for x in srcs):
            await db.move_task_done(mid, to, now)
            print(f"  задача {C(to)} закрыта: принято от всех")
    for doc in docs:
        закр = await db.move_order_close_if_done(doc["_id"], now)
        print(f"заявка {doc['_id']}: закрыта — {bool(закр)}")

    осталось = await db.move_orders_open()
    print(f"\nзакрыто передач: {закрыто} · открытых заявок осталось: {len(осталось)}")
    return 0


sys.exit(asyncio.run(main()))
