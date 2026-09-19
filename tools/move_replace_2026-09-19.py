"""Заменить несделанное в заявке на перемещение MV260918-070617 новой заявкой,
пересчитанной от сегодняшнего остатка (владелец, 19 сен 2026: «те два
перемещения, которые они сегодня уже сделали, оставим в покое; остальные они
сделать не успели… тогда формируй новую заявку и перемещения, и сделай так,
чтобы всё у всех в телефонах уже отображалось как надо»).

Сделано и остаётся как есть:
  • B1→B2 — 4 ед., отдано и принято;
  • B1→B4 — 4 ед., отдано, ждёт «Принял» у B4.
Снимается всё остальное — 212 ед., в которых не отсканировано ни одной
бутылки:
  • задачи B1, B3, B5 (у них сделанного нет) — целиком, как «Снять» в STAR;
  • у B2 и B4 — только несделанные пары: их строки (got = 0 — условие в
    самом запросе, отсканированное не тронем) и отметки передачи; задача B2
    после этого закрыта (осталась принятая пара), B4 ждёт «Принял».
Потом — move_routes.plan от остатка на эту минуту (снятое уже не считается
«едущим») и новая заявка тем же путём, что «Создать» в STAR. Заявка магазину
в STAR считается сама: открытые перемещения она считает сделанными.

Копия старой заявки — в /opt/ambar/backups/. Без --apply только показывает.
    venv/bin/python tools/move_replace_2026-09-19.py [--apply]
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import move_routes as MV
import stock_routes as SR
from config_offices import OFFICE_CODES

MID = "MV260918-070617"
KEEP = {("jvc", "bbay"), ("jvc", "alguses")}       # (откуда, куда): B1→B2 принято, B1→B4 отдано
APPLY = "--apply" in sys.argv
C = lambda o: OFFICE_CODES.get(o, o)


async def main():
    await db.connect()
    d = db._db
    doc = await d.move_orders.find_one({"_id": MID})
    if not doc or doc.get("status") != "open":
        print("заявки нет или она не открыта:", (doc or {}).get("status")); return 1
    now = datetime.now(timezone.utc)
    cancel_tasks, trim = [], {}
    for to, t in (doc.get("tasks") or {}).items():
        if t.get("done_at") or t.get("cancelled_at"):
            continue
        srcs = sorted({l.get("from") for l in t.get("lines") or []} - {None})
        keep = [s for s in srcs if (s, to) in KEEP]
        drop = [s for s in srcs if (s, to) not in KEEP]
        got_drop = sum(float(l.get("got") or 0) for l in t.get("lines") or [] if l.get("from") in drop)
        if got_drop > 0:
            print(f"В {C(to)} по снимаемым парам уже отсканировано {got_drop:g} — ничего не трогаю"); return 1
        if not keep:
            cancel_tasks.append(to)
        elif drop:
            trim[to] = drop
        print(f"  задача {C(to)}: оставить {[C(s) + '→' + C(to) for s in keep]} · снять {[C(s) + '→' + C(to) for s in drop]}")
    print("снять задачи целиком:", [C(x) for x in cancel_tasks], "· убрать пары:", {C(k): [C(s) for s in v] for k, v in trim.items()})
    if not APPLY:
        print("это просмотр; сделать — с --apply"); return 0

    from bson import json_util
    os.makedirs("/opt/ambar/backups", exist_ok=True)
    path = f"/opt/ambar/backups/move_{MID}_{now:%Y%m%d-%H%M%S}.json"
    open(path, "w", encoding="utf-8").write(json_util.dumps(doc, ensure_ascii=False, indent=1))
    print("копия старой заявки:", path)

    for to in cancel_tasks:
        ok = await db.move_order_cancel(MID, to, now)
        print(f"  снята задача {C(to)}: {ok}")
    for to, drop in trim.items():
        r = await d.move_orders.update_one(
            {"_id": MID, "status": "open", f"tasks.{to}.done_at": None, f"tasks.{to}.cancelled_at": None},
            {"$pull": {f"tasks.{to}.lines": {"from": {"$in": drop}, "got": 0}},
             "$unset": {f"tasks.{to}.give.{s}": "" for s in drop}})
        print(f"  {C(to)}: убраны пары {[C(s) for s in drop]}: {bool(r.modified_count)}")
        t = ((await d.move_orders.find_one({"_id": MID})).get("tasks") or {}).get(to) or {}
        left = [l for l in t.get("lines") or [] if l.get("from") in drop]
        if left:
            print(f"  ВНИМАНИЕ: в {C(to)} остались строки снимаемых пар (их успели сканировать): {len(left)}")
        srcs = sorted({l.get("from") for l in t.get("lines") or []} - {None})
        if srcs and all(((t.get("give") or {}).get(s) or {}).get("accepted_at") for s in srcs):
            await db.move_task_done(MID, to, now)
            print(f"  задача {C(to)} закрыта: всё оставшееся принято")
    await db.move_order_close_if_done(MID, now)

    SR.base_drop()
    day = SR._biz_day()
    plan = await MV.plan(day)
    rows = [{"from": r["from"], "to": r["to"], "id": r["id"], "qty": r["qty"]} for r in plan["rows"]]
    res = await MV.create(rows, by="STAR", note=f"пересчёт 19.09 от остатка после продаж: вместо несделанного в {MID}")
    print("новая заявка:", {k: v for k, v in res.items() if k != "skipped"}, "· пропущено строк:", len(res.get("skipped") or []))
    return 0 if res.get("ok") else 1


sys.exit(asyncio.run(main()))
