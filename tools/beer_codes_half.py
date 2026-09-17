"""Коды пива — по полкоробки (владелец, 17 сен 2026: «на каждой коробке по 2
кода, каждое сканирование пива это шаг +0.5»).

До этого код пива нёс коробку (qty 1): переключатель «Полкоробки» во «Внести
товар» не показывался, а приёмка закрывалась, когда кодов набиралось столько
же, сколько коробок в плане. Скрипт переводит такие коды на 0.5 и не трогает
склад:

  • код «по пересчёту» (src=cover) и удалённый — просто qty 0.5: в остаток они
    не входят, вырастет только долг «QR код не внесён» — это вторые коды,
    которые ещё не отсканированы;
  • код приёмки (src=intake) — qty 0.5 и intake_extra 0.5: приёмка уже
    засчитала коробку, и коробка на складе остаётся, пока район не
    пересчитают (db.intake_since складывает qty + intake_extra).

Всё остальное, что могло унаследовать qty от кода пива, — переезд или
списание сканом, скан ревизии, «Внести новый товар» пивом, незакрытая приёмка
со сканами — скрипт не угадывает: находит — останавливается, ничего не меняя.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/beer_codes_half.py            # что будет
    PYTHONPATH=/opt/ambar venv/bin/python tools/beer_codes_half.py --apply    # сделать
    PYTHONPATH=/opt/ambar venv/bin/python tools/beer_codes_half.py --rollback <копия.json>
--apply пишет копию прежних полей в /root/beer_codes_half_<время>.json, после
записи сверяет приход и склад по районам с тем, что было, и при расхождении
откатывается сам."""
import asyncio, json, sys, time
from datetime import datetime, timezone

HALF = 0.5


def beer_ids(sr) -> set:
    return {pid for pid, p in sr._catalog().items() if sr.code_qty(p) == HALF}


async def blockers(d, beer: set) -> list:
    """То, что скрипт не переводит сам: там qty кода уже разошёлся по другим записям."""
    ids = sorted(beer)
    out = []
    n = await d.stock_transfers.count_documents({"product_id": {"$in": ids}, "code": {"$exists": True},
                                                "qty": {"$ne": HALF}})
    if n: out.append(f"переезды пива сканом: {n}")
    n = await d.writeoffs.count_documents({"item": {"$in": ids}, "code": {"$nin": [None, ""]},
                                           "qty": {"$ne": HALF}})
    if n: out.append(f"списания пива сканом: {n}")
    n = await d.audit_scans.count_documents({"product_id": {"$in": ids}, "qty": {"$ne": HALF}})
    if n: out.append(f"сканы ревизий по пиву: {n}")
    n = await d.qr_codes.count_documents({"product_id": {"$in": ids}, "src": "new", "qty": {"$ne": HALF}})
    if n: out.append(f"пиво, внесённое новым товаром: {n}")
    n = await d.qr_codes.count_documents({"product_id": {"$in": ids},
                                          "src": {"$nin": ["cover", "intake", "new"]}, "qty": {"$ne": HALF}})
    if n: out.append(f"коды пива с непонятным источником: {n}")
    # Незакрытая приёмка, по которой уже сканировали пиво: там got задачи и
    # коды пойдут в разном счёте, если перевести одни коды.
    async for c in d.qr_codes.find({"product_id": {"$in": ids}, "src": "intake", "qty": {"$ne": HALF}},
                                   {"supply_id": 1, "origin": 1, "district": 1}):
        s = await d.supplies.find_one({"_id": c.get("supply_id")}, {"status": 1, "tasks": 1})
        t = ((s or {}).get("tasks") or {}).get(c.get("origin") or c.get("district")) or {}
        if not s or (s.get("status") == "open" and not t.get("done_at") and not t.get("cancelled_at")):
            out.append(f"незакрытая приёмка {c.get('supply_id')} со сканами пива")
            break
    return out


async def plan(d, beer: set) -> list:
    """[(код, прежние поля, новые поля)] — только то, что меняется."""
    rows = []
    async for c in d.qr_codes.find({"product_id": {"$in": sorted(beer)}, "qty": {"$ne": HALF}}):
        old_qty = float(c["qty"]) if c.get("qty") is not None else 1.0
        new = {"qty": HALF}
        if c.get("src") == "intake":
            new["intake_extra"] = round(old_qty - HALF + float(c.get("intake_extra") or 0), 4)
        rows.append((c["_id"], {"qty": c.get("qty"), "intake_extra": c.get("intake_extra")}, new))
    return rows


async def snapshot(db, sr, qr) -> dict:
    """Что должно остаться прежним (приход и склад) и что вырастет (долг без QR)."""
    since = {}
    for oid in sr.OFFICE_IDS:
        cnt = await db.get_last_stock_count(oid, before_day=None)
        since[oid] = sr._dt_of((cnt or {}).get("counted_at") or "")
    came = {oid: (await db.intake_since(oid, since[oid]) if since[oid] else {}) for oid in sr.OFFICE_IDS}
    manual = {oid: {pid: sum(q for _, q in ev) for pid, ev in (await db.qr_manual_events(oid, since[oid])).items()}
              for oid in sr.OFFICE_IDS}
    sr.base_drop()
    base = await sr._district_base(sr._biz_day())
    have = {oid: dict(base[oid]["have_exact"]) for oid in sr.OFFICE_IDS}
    debt, _ = await qr.unscanned_by_district(None, {})
    return {"came": came, "manual": manual, "have": have, "debt": dict(debt),
            "stamp": await _stamp(db)}


async def _stamp(db):
    try:
        from datetime import timedelta
        return await db.delivered_stamp(
            (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", ""))
    except Exception:                                        # noqa: BLE001
        return None


def diff(a: dict, b: dict) -> list:
    out = []
    for oid in sorted(set(a) | set(b)):
        x, y = a.get(oid) or {}, b.get(oid) or {}
        for pid in sorted(set(x) | set(y)):
            if round(float(x.get(pid) or 0), 4) != round(float(y.get(pid) or 0), 4):
                out.append((oid, pid, x.get(pid), y.get(pid)))
    return out


async def run(db, sr, qr, apply: bool, backup_dir: str = "/root", say=print) -> dict:
    d = db._db_or_none()
    beer = beer_ids(sr)
    stop = await blockers(d, beer)
    rows = await plan(d, beer)
    by = {}
    for code, old, new in rows:
        doc = await d.qr_codes.find_one({"_id": code}, {"district": 1, "src": 1, "status": 1})
        k = (doc.get("district"), doc.get("src") or "-", doc.get("status"), "intake_extra" in new)
        by[k] = by.get(k, 0) + 1
    say(f"позиций пива: {len(beer)}; кодов к переводу на 0.5: {len(rows)}")
    for k, n in sorted(by.items(), key=str):
        say(f"   {k[0]} src={k[1]} {k[2]}{' + intake_extra' if k[3] else ''}: {n}")
    if stop:
        say("СТОП — это скрипт сам не переводит: " + "; ".join(stop))
        return {"ok": False, "stop": stop, "rows": len(rows)}
    if not rows:
        say("переводить нечего")
        return {"ok": True, "rows": 0}
    before = await snapshot(db, sr, qr)
    if not apply:
        say("пробный прогон: ничего не записано (--apply — сделать)")
        return {"ok": True, "rows": len(rows), "dry": True, "before": before}

    path = f"{backup_dir}/beer_codes_half_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump([{"_id": c, **old} for c, old, _ in rows], f, ensure_ascii=False, default=str)
    say(f"копия прежних полей: {path}")
    done = 0
    for code, old, new in rows:
        q = {"_id": code}
        q["qty"] = old["qty"] if old["qty"] is not None else {"$exists": False}
        r = await d.qr_codes.update_one(q, {"$set": new})
        done += r.modified_count
    after = await snapshot(db, sr, qr)
    bad = diff(before["came"], after["came"]) + diff(before["manual"], after["manual"])
    moved = diff(before["have"], after["have"])
    if moved and before["stamp"] == after["stamp"]:
        bad += moved                         # продаж между замерами не было — склад обязан совпасть
    say(f"записано: {done} из {len(rows)}")
    if bad or done != len(rows):
        say(f"РАСХОЖДЕНИЕ — откатываю: {bad[:10]}")
        await rollback(db, path, say)
        return {"ok": False, "bad": bad, "backup": path}
    for oid in sr.OFFICE_IDS:
        b, a = before["debt"].get(oid, 0), after["debt"].get(oid, 0)
        if b != a:
            say(f"   долг «QR код не внесён» {oid}: {b} → {a}")
    if moved:
        say(f"   склад менялся между замерами продажей (не перевод): {moved[:5]}")
    say("склад и приход по районам — как были")
    return {"ok": True, "rows": len(rows), "done": done, "backup": path, "before": before, "after": after}


async def rollback(db, path: str, say=print) -> int:
    d = db._db_or_none()
    rows = json.load(open(path))
    n = 0
    for r in rows:
        set_, unset = {}, {}
        for k in ("qty", "intake_extra"):
            if r.get(k) is None: unset[k] = ""
            else: set_[k] = r[k]
        upd = {}
        if set_: upd["$set"] = set_
        if unset: upd["$unset"] = unset
        res = await d.qr_codes.update_one({"_id": r["_id"]}, upd)
        n += res.modified_count
    try:
        import stock_routes
        stock_routes.base_drop()
    except Exception:                                        # noqa: BLE001
        pass
    say(f"откат: восстановлено {n} из {len(rows)}")
    return n


async def main():
    import db, stock_routes as sr, qr_routes as qr
    await db.connect()
    if "--rollback" in sys.argv:
        await rollback(db, sys.argv[sys.argv.index("--rollback") + 1])
        return
    res = await run(db, sr, qr, apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
