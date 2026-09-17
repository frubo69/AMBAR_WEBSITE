"""Пересканировать пиво закрытой приёмки заново (владелец, 17 сен 2026: на
приёмке S260917-104601 в Бизнес Бей пиво внесли вразнобой — 19 кодов из 38;
«удалить внесённое пиво с этой заявки… и на этот раз он забьет его правильно»).

Новую заявку не заводим: финансы сложили бы закупку второй раз, карточка
«Заявка» в STAR взяла бы её за основную, а заявка «на другую базу» просит у
водителя цены. Вместо этого в той же заявке заново открываются строки пива:

  • коды пива этой приёмки удаляются из реестра (копия — в файл);
  • принятое по строкам пива обнуляется, задача района снова открыта и
    отмечена «принято без сканирования» — товар остаётся на складе
    (need − got), а по мере сканирования перетекает в коды;
  • водитель тот же — задача на нём; на последнем коде закрывается сама.

После записи сверяется остаток района по этим позициям; разошёлся — откат.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/rescan_beer_supply.py S260917-104601 bbay            # что будет
    PYTHONPATH=/opt/ambar venv/bin/python tools/rescan_beer_supply.py S260917-104601 bbay --apply    # сделать
    PYTHONPATH=/opt/ambar venv/bin/python tools/rescan_beer_supply.py --rollback <копия.json>"""
import asyncio, json, sys, time
from datetime import datetime, timezone

NOSCAN_BY = "STAR"


async def plan(db, sr, sid: str, oid: str) -> dict:
    d = db._db_or_none()
    cat = sr._catalog()
    sup = await d.supplies.find_one({"_id": sid})
    stop = []
    if not sup:
        return {"stop": ["нет такой заявки"]}
    task = (sup.get("tasks") or {}).get(oid)
    if not task:
        return {"stop": [f"в заявке нет района {oid}"]}
    if not task.get("done_at") or task.get("cancelled_at"):
        stop.append("задача района не закрыта (или отменена) — открывать нечего")
    lines = [it for it in sup.get("items") or []
             if sr._unit(cat.get(it.get("id")) or {}) > 1
             and float((it.get("by_district") or {}).get(oid) or 0) > 0]
    pids = [it["id"] for it in lines]
    codes = await d.qr_codes.find({"supply_id": sid, "product_id": {"$in": pids}}).to_list(1000)
    for c in codes:
        if (c.get("origin") or c.get("district")) != oid or c.get("district") != oid:
            stop.append(f"код {c['_id']} уже не в районе приёмки")
        if (c.get("status") or "active") not in ("active", "deleted"):
            stop.append(f"код {c['_id']} {c.get('status')}")
    ids = [c["_id"] for c in codes]
    if ids:
        if await d.stock_transfers.count_documents({"code": {"$in": ids}}):
            stop.append("по кодам были переезды")
        if await d.writeoffs.count_documents({"code": {"$in": ids}}):
            stop.append("по кодам были списания")
        if await d.audit_scans.count_documents({"code": {"$in": ids}}):
            stop.append("коды видела ревизия")
    return {"sup": sup, "task": task, "lines": lines, "codes": codes, "stop": stop}


async def snapshot(db, sr, qr, oid: str, pids: list) -> dict:
    sr.base_drop()
    base = await sr._district_base(sr._biz_day())
    have = {pid: float((base[oid].get("have_exact") or {}).get(pid) or 0) for pid in pids}
    reg = await db.qr_by_product_district_all()
    coded = {pid: float((reg.get(oid) or {}).get(pid) or 0) for pid in pids}
    u, _ = await qr.unscanned_by_district(None, {})
    return {"have": have, "coded": coded, "debt": u.get(oid, 0)}


def reopened(sup: dict, oid: str, pids: set, codes: list, now) -> dict:
    """Та же заявка: пиво района не принято, задача открыта «без сканирования»."""
    per = {}
    for c in codes:
        per[c["product_id"]] = per.get(c["product_id"], 0) + 1
    items = []
    for it in sup.get("items") or []:
        it = dict(it)
        if it.get("id") in pids:
            it["got"] = {**(it.get("got") or {}), oid: 0}
            it["scanned"] = max(0, int(it.get("scanned") or 0) - per.get(it["id"], 0))
        items.append(it)
    task = dict((sup.get("tasks") or {}).get(oid) or {})
    task.update(done_at=None, gaps=[], note="", noscan_at=now, noscan_by=NOSCAN_BY,
                scanned=max(0, int(task.get("scanned") or 0) - len(codes)))
    task.pop("hold", None)
    return {**sup, "status": "open", "done_at": None, "items": items,
            "tasks": {**(sup.get("tasks") or {}), oid: task}}


async def run(db, sr, qr, sid: str, oid: str, apply: bool, backup_dir: str = "/root", say=print) -> dict:
    d = db._db_or_none()
    p = await plan(db, sr, sid, oid)
    if p.get("stop") and not p.get("sup"):
        say("СТОП: " + "; ".join(p["stop"]))
        return {"ok": False, "stop": p["stop"]}
    cat = sr._catalog()
    pids = [it["id"] for it in p["lines"]]
    for it in p["lines"]:
        n = sum(1 for c in p["codes"] if c["product_id"] == it["id"])
        say(f"   {it.get('name')}: план {(it.get('by_district') or {}).get(oid)} кор, "
            f"принято {(it.get('got') or {}).get(oid)}, кодов этой приёмки {n}")
    say(f"строк пива: {len(pids)}; кодов к удалению: {len(p['codes'])}; водитель задачи: {p['task'].get('driver')}")
    if p["stop"]:
        say("СТОП: " + "; ".join(p["stop"]))
        return {"ok": False, "stop": p["stop"]}
    before = await snapshot(db, sr, qr, oid, pids)
    say(f"сейчас: склад {before['have']}, в реестре {before['coded']}, долг района {before['debt']}")
    if not apply:
        say("пробный прогон: ничего не записано (--apply — сделать)")
        return {"ok": True, "dry": True, "codes": len(p["codes"]), "before": before}

    path = f"{backup_dir}/rescan_{sid}_{oid}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump({"supply": p["sup"], "codes": p["codes"]}, f, ensure_ascii=False,
                  default=lambda v: {"$dt": v.isoformat()} if isinstance(v, datetime) else str(v))
    say(f"копия: {path}")
    now = datetime.now(timezone.utc)
    new = reopened(p["sup"], oid, set(pids), p["codes"], now)
    r = await d.supplies.replace_one({"_id": sid, "status": p["sup"].get("status"),
                                      f"tasks.{oid}.done_at": p["task"].get("done_at")}, new)
    if not r.modified_count:
        say("заявку успели поменять — ничего не записано")
        return {"ok": False, "stop": ["race"], "backup": path}
    await d.qr_codes.delete_many({"_id": {"$in": [c["_id"] for c in p["codes"]]}})
    after = await snapshot(db, sr, qr, oid, pids)
    bad = {pid: (before["have"][pid], after["have"][pid]) for pid in pids
           if round(before["have"][pid], 3) != round(after["have"][pid], 3)}
    if bad:
        say(f"СКЛАД РАЗОШЁЛСЯ {bad} — откатываю")
        await rollback(db, sr, path, say)
        return {"ok": False, "bad": bad, "backup": path}
    say(f"после: склад {after['have']} (как был), в реестре {after['coded']}, долг района {after['debt']}")
    return {"ok": True, "backup": path, "before": before, "after": after, "codes": len(p["codes"])}


async def rollback(db, sr, path: str, say=print) -> None:
    d = db._db_or_none()
    data = json.load(open(path))
    sup = data["supply"]

    def revive(v):
        # Время в копии помечено {"$dt": …}: возвращаем ровно то, что было.
        if isinstance(v, dict):
            if set(v) == {"$dt"}:
                return datetime.fromisoformat(v["$dt"])
            return {k: revive(x) for k, x in v.items()}
        if isinstance(v, list):
            return [revive(x) for x in v]
        return v
    await d.supplies.replace_one({"_id": sup["_id"]}, revive(sup), upsert=True)
    n = 0
    for c in data["codes"]:
        c = revive(c)
        if not await d.qr_codes.find_one({"_id": c["_id"]}):
            await d.qr_codes.insert_one(c); n += 1
    sr.base_drop()
    say(f"откат: заявка восстановлена, кодов возвращено {n} из {len(data['codes'])}")


async def main():
    import db, stock_routes as sr, qr_routes as qr
    await db.connect()
    if "--rollback" in sys.argv:
        await rollback(db, sr, sys.argv[sys.argv.index("--rollback") + 1])
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    res = await run(db, sr, qr, args[0], args[1], apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
