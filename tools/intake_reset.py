"""Обнулить приёмку районов в поставке — как будто её не начинали (владелец,
21 сен 2026: «на B1 и B5 сделали приёмку — всё, что они внесли, обнулить,
чтобы смогли всё заново отсканировать, как будто ничего и не было»).

Для каждого района:
  • стирает коды, принятые этой поставкой в этот район (qr_codes: supply_id,
    origin, src intake/cover) — бутылки снова свободны для скана;
  • в строках поставки: принятое районом → 0, счётчик сканов строки − его коды;
  • задача района: scanned/undo → 0, started_at/noscan_at/done_at/last_at →
    пусто, недобор, отметки и заметка — пустые, замок снят; версия rev +1 —
    открытые приложения увидят новую версию и сбросят числа сами. Водитель,
    за которым задача, остаётся за ней.

Ничего не трогает и говорит почему, если: район сканируют прямо сейчас
(живой замок), район закрыт или отменён, хоть одну бутылку уже продали,
перевезли, списали или прошли ревизией — стереть такой код значит потерять
то, что с бутылкой было дальше.

Копии поставки и стираемых кодов — в /opt/ambar/backups/. Без --apply только
показывает.
    venv/bin/python tools/intake_reset.py S260921-050452 B1 B5 [--apply]
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from config_offices import OFFICE_CODES

SRC = ("intake", "cover")


def _aware(v):
    if v is None or not hasattr(v, "tzinfo"):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


async def plan(sid: str, oids: list) -> tuple:
    """(что стереть по районам, почему нельзя) — ничего не меняет."""
    d = db._db
    sup = await d.supplies.find_one({"_id": sid})
    if not sup:
        return None, [f"поставки {sid} нет"]
    now = datetime.now(timezone.utc)
    нельзя, что = [], {}
    for oid in oids:
        код = OFFICE_CODES.get(oid, oid)
        t = (sup.get("tasks") or {}).get(oid)
        if not t:
            нельзя.append(f"{код}: в поставке нет задачи района"); continue
        if t.get("done_at") or t.get("cancelled_at"):
            нельзя.append(f"{код}: район уже закрыт или отменён"); continue
        h = t.get("hold") or {}
        until = _aware(h.get("until"))
        if h and until and until >= now:
            нельзя.append(f"{код}: сейчас сканирует {h.get('who') or 'кто-то'} — подождите, пока закончит")
        codes = await d.qr_codes.find({"supply_id": sid, "origin": oid, "src": {"$in": list(SRC)}}).to_list(None)
        ids = [c["_id"] for c in codes]
        ушли = [c["_id"] for c in codes if (c.get("status") or "active") != "active" or c.get("district") != oid]
        if ушли:
            нельзя.append(f"{код}: {len(ушли)} бутылок уже продали, перевезли или списали")
        if ids:
            if await d.stock_transfers.count_documents({"code": {"$in": ids}}):
                нельзя.append(f"{код}: по этим бутылкам есть перемещения")
            if await d.writeoffs.count_documents({"code": {"$in": ids}}):
                нельзя.append(f"{код}: по этим бутылкам есть списания")
            if await d.audit_scans.count_documents({"code": {"$in": ids}}):
                нельзя.append(f"{код}: эти бутылки уже прошли ревизию")
        по_строкам = {}
        for c in codes:
            p = c.get("product_id") or ""
            по_строкам.setdefault(p, {"codes": 0, "qty": 0.0})
            по_строкам[p]["codes"] += 1
            по_строкам[p]["qty"] += float(c.get("qty") or 1)
        got = {it["id"]: float((it.get("got") or {}).get(oid) or 0) for it in sup.get("items") or []}
        # Реестр и строки должны сходиться: иначе стираем не то, что приняли.
        for p, g in got.items():
            if abs(g - (по_строкам.get(p) or {}).get("qty", 0.0)) > 1e-9:
                нельзя.append(f"{код}: строка {p} — принято {g:g}, а кодов на {(по_строкам.get(p) or {}).get('qty', 0):g}")
        что[oid] = {"codes": codes, "по_строкам": по_строкам, "got": got, "task": t}
    return (sup, что), нельзя


async def apply(sid: str, sup: dict, что: dict) -> None:
    d = db._db
    idx = {it["id"]: i for i, it in enumerate(sup.get("items") or [])}
    for oid, x in что.items():
        ids = [c["_id"] for c in x["codes"]]
        if ids:
            r = await d.qr_codes.delete_many({"_id": {"$in": ids}, "supply_id": sid, "origin": oid})
            assert r.deleted_count == len(ids), (r.deleted_count, len(ids))
        inc = {f"tasks.{oid}.rev": 1}
        setv = {f"tasks.{oid}.scanned": 0, f"tasks.{oid}.undo": 0,
                f"tasks.{oid}.started_at": None, f"tasks.{oid}.noscan_at": None,
                f"tasks.{oid}.done_at": None, f"tasks.{oid}.last_at": None,
                f"tasks.{oid}.gaps": [], f"tasks.{oid}.flags": [], f"tasks.{oid}.note": ""}
        for p, i in idx.items():
            setv[f"items.{i}.got.{oid}"] = 0
            n = (x["по_строкам"].get(p) or {}).get("codes", 0)
            if n:
                inc[f"items.{i}.scanned"] = -n
        await d.supplies.update_one({"_id": sid}, {"$set": setv, "$inc": inc,
                                                   "$unset": {f"tasks.{oid}.hold": ""}})
    try:
        import stock_routes
        stock_routes.base_drop()
    except Exception:
        pass


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    APPLY = "--apply" in sys.argv
    if len(args) < 2:
        print(__doc__); return 2
    sid = args[0]
    by_code = {c: o for o, c in OFFICE_CODES.items()}
    oids = [by_code.get(a.upper(), a) for a in args[1:]]
    await db.connect()
    res, нельзя = await plan(sid, oids)
    if res is None:
        print("\n".join(нельзя)); return 1
    sup, что = res
    for oid, x in что.items():
        t = x["task"]
        print(f"{OFFICE_CODES.get(oid, oid)}: водитель {t.get('driver') or '— (свободна)'} · "
              f"принято {sum(x['got'].values()):g} · кодов к стиранию {len(x['codes'])} · "
              f"позиций {len([1 for g in x['got'].values() if g])}")
    if нельзя:
        print("\nНЕЛЬЗЯ, ничего не тронуто:\n  " + "\n  ".join(нельзя)); return 1
    if not APPLY:
        print("\nэто просмотр; сделать — с --apply"); return 0
    from bson import json_util
    os.makedirs("/opt/ambar/backups", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"/opt/ambar/backups/intake_reset_{sid}_{stamp}.json"
    open(path, "w", encoding="utf-8").write(json_util.dumps(
        {"supply": sup, "codes": {o: x["codes"] for o, x in что.items()}}, ensure_ascii=False, indent=1))
    print("копия:", path)
    await apply(sid, sup, что)
    print("обнулено:", ", ".join(OFFICE_CODES.get(o, o) for o in что))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
