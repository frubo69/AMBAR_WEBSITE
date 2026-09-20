"""Снять несделанное в открытых заявках на перемещение и завести новую,
пересчитанную от остатка после продаж 19.09 (владелец, 20 сен 2026: «они
сегодня тоже не смогли все перемещения сделать; учти всё то, что успели, и
сегодняшние продажи, пересчитай заявку и перемещения необходимые заново и
сделай так, чтобы у всех на телефонах уже отображалось всё правильно»).

Та же схема, что 19.09 (tools/move_replace_2026-09-19.py), но пары, которые
остаются, не вписаны руками, а считаются: остаётся всё, где отдана хотя бы
одна бутылка (её уже отсканировали, товар переехал, получателю осталось
принять). Снимается только то, где не начинали, — строки с got = 0 (условие в
самом запросе, отсканированное не тронуть) и отметки передачи.

Дальше move_routes.plan от остатка на эту минуту: снятое уже не считается
«едущим», отданное лежит в остатке, продажи дня в нём же. Новая заявка — тем
же путём, что «Создать» в STAR. Заявка магазину в STAR считается сама: открытые
перемещения она считает сделанными.

Копии старых заявок — в /opt/ambar/backups/. Без --apply только показывает.
    venv/bin/python tools/move_replace_2026-09-20.py [--apply]
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import move_routes as MV
import stock_routes as SR
from config_offices import OFFICE_CODES

APPLY = "--apply" in sys.argv
C = lambda o: OFFICE_CODES.get(o, o)


def _pairs(t: dict) -> dict:
    """Пары «откуда» задачи: сколько единиц в строках и сколько уже отдано."""
    out = {}
    for l in t.get("lines") or []:
        src = l.get("from")
        p = out.setdefault(src, {"qty": 0.0, "got": 0.0, "lines": 0})
        p["qty"] += float(l.get("qty") or 0)
        p["got"] += float(l.get("got") or 0)
        p["lines"] += 1
    return out


async def main():
    await db.connect()
    d = db._db
    now = datetime.now(timezone.utc)
    docs = await db.move_orders_open()
    if not docs:
        print("открытых заявок нет"); return 1

    план = []          # (mid, doc, to, keep[], drop[])
    снять_ед = снять_строк = 0
    for doc in docs:
        print(f"\nзаявка {doc['_id']} · {str(doc.get('at'))[:16]}")
        for to, t in sorted((doc.get("tasks") or {}).items()):
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            pp = _pairs(t)
            keep = sorted(s for s, p in pp.items() if p["got"] > 1e-9)
            drop = sorted(s for s, p in pp.items() if p["got"] <= 1e-9)
            for s in keep:
                g = (t.get("give") or {}).get(s) or {}
                print(f"  оставить {C(s)}→{C(to)}: {pp[s]['got']:g} из {pp[s]['qty']:g} ед отдано"
                      f" · {'принято' if g.get('accepted_at') else 'ждёт «Принял»'}")
            for s in drop:
                снять_ед += pp[s]["qty"]; снять_строк += pp[s]["lines"]
                print(f"  снять    {C(s)}→{C(to)}: {pp[s]['qty']:g} ед, не начинали")
            if drop:
                план.append((doc["_id"], to, keep, drop))
    print(f"\nитого снять: {снять_строк} строк · {снять_ед:g} ед")
    if not APPLY:
        print("это просмотр; сделать — с --apply"); return 0

    from bson import json_util
    os.makedirs("/opt/ambar/backups", exist_ok=True)
    for doc in docs:
        path = f"/opt/ambar/backups/move_{doc['_id']}_{now:%Y%m%d-%H%M%S}.json"
        open(path, "w", encoding="utf-8").write(json_util.dumps(doc, ensure_ascii=False, indent=1))
        print("копия:", path)

    for mid, to, keep, drop in план:
        if not keep:                                  # сделанного в задаче нет — снять целиком
            print(f"  снята задача {C(to)} в {mid}: {await db.move_order_cancel(mid, to, now)}")
            continue
        r = await d.move_orders.update_one(
            {"_id": mid, "status": "open", f"tasks.{to}.done_at": None, f"tasks.{to}.cancelled_at": None},
            {"$pull": {f"tasks.{to}.lines": {"from": {"$in": drop}, "got": 0}},
             "$unset": {f"tasks.{to}.give.{s}": "" for s in drop}})
        print(f"  {mid} · {C(to)}: убраны пары {[C(s) for s in drop]}: {bool(r.modified_count)}")
        t = ((await d.move_orders.find_one({"_id": mid})).get("tasks") or {}).get(to) or {}
        left = [l for l in t.get("lines") or [] if l.get("from") in drop]
        if left:
            print(f"  ВНИМАНИЕ: в {C(to)} остались строки снимаемых пар (успели сканировать): {len(left)}")
        srcs = sorted({l.get("from") for l in t.get("lines") or []} - {None})
        if srcs and all(((t.get("give") or {}).get(s) or {}).get("accepted_at") for s in srcs):
            await db.move_task_done(mid, to, now)
            print(f"  задача {C(to)} закрыта: всё оставшееся принято")
    for doc in docs:
        await db.move_order_close_if_done(doc["_id"], now)

    SR.base_drop()
    day = SR._biz_day()
    plan = await MV.plan(day)
    rows = [{"from": r["from"], "to": r["to"], "id": r["id"], "qty": r["qty"]} for r in plan["rows"]]
    res = await MV.create(rows, by="STAR",
                          note=f"пересчёт 20.09 от остатка после продаж {day}: вместо несделанного")
    print("новая заявка:", {k: v for k, v in res.items() if k != "skipped"},
          "· пропущено строк:", len(res.get("skipped") or []))
    return 0 if res.get("ok") else 1


sys.exit(asyncio.run(main()))
