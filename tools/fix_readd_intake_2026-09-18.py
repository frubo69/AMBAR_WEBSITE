"""Вернуть приход четырём бутылкам поставки S260918-055424 (владелец, 19 сен
2026: «эти бутылки перепутали на приёмке, поэтому их сразу после приёмки
удалили и завели через „внести“, но уже под правильными наименованиями»;
«да, исправь и сделай, чтобы не повторялось»).

Что случилось: B3 Bottega Rose / Bottega Prosecco (Азиз) и B1 Efe Raki /
Chateau Des Laurets (Худоба) на приёмке записали наоборот; STAR убрал коды
и внёс заново строкой «QR код не внесён». Возврат тогда перезаписывал
отметку прихода (src intake → cover), время и того, кто принял, — и склад
перестал считать эти бутылки пришедшими: по каждой позиции на одну меньше
(склад 2 при 3 кодах на полке, 1 при 2). С этого дня возврат так не делает
(db.qr_readd); здесь — починка четырёх записей, сделанных до этого.

Возвращаем то, что возврат бы сохранил:
  • src = intake — приход с поставки;
  • at = время скана на приёмке (at_dev: телефон водителя, у остальных кодов
    тех же задач расходится с сервером на 0,02–1,4 с), проверяем, что оно
    внутри окна приёмки района;
  • by = водитель задачи (driver_id), как у остальных кодов приёмки;
  • re_at / re_by / re_src — сам возврат старшим (время, кто, какой кнопкой).
Название и метка — те, что поставил старший (верные).

Только эти четыре: ищем по признаку (код с поставки, а отметка не intake) и
сверяем, что их ровно четыре и они те самые. Копия записей до правки — в
/opt/ambar/backups/. Без --apply только показывает.

    venv/bin/python tools/fix_readd_intake_2026-09-18.py            # посмотреть
    venv/bin/python tools/fix_readd_intake_2026-09-18.py --apply    # исправить
"""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

SID = "S260918-055424"
WANT = {("silicon", "p92"), ("silicon", "p91"), ("jvc", "p84"), ("jvc", "p111")}
APPLY = "--apply" in sys.argv


def utc(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


async def main():
    await db.connect()
    d = db._db
    rows = await d.qr_codes.find({"supply_id": {"$exists": True, "$ne": None},
                                  "src": {"$ne": "intake"}}).to_list(length=100)
    got = {(r.get("origin"), r.get("product_id")) for r in rows}
    print(f"кодов с поставкой без отметки прихода: {len(rows)}")
    if len(rows) != 4 or got != WANT or any(r.get("supply_id") != SID for r in rows):
        print("не те записи, что ожидались — ничего не трогаю:", sorted(got))
        return 1
    sup = await d.supplies.find_one({"_id": SID})
    plan = []
    for r in rows:
        t = (sup.get("tasks") or {}).get(r["origin"]) or {}
        if r.get("status") != "active" or r.get("src") != "cover" or r.get("re_at"):
            print("запись не в том виде:", r.get("label"), r.get("status"), r.get("src")); return 1
        arrived = utc(r["at_dev"])
        lo, hi = utc(t["started_at"]) - timedelta(minutes=1), utc(t["done_at"]) + timedelta(minutes=1)
        if not (lo <= arrived <= hi):
            print("время скана вне окна приёмки:", r.get("label"), arrived); return 1
        if not t.get("driver_id"):
            print("нет водителя задачи:", r.get("origin")); return 1
        s = {"src": "intake", "at": arrived.replace(tzinfo=None), "by": int(t["driver_id"]),
             "re_at": utc(r["at"]).isoformat(), "re_by": r.get("by"), "re_src": r.get("src") or ""}
        plan.append((r, s))
        print(f"  {r['origin']:8} {r.get('product_name'):26} {r.get('label'):18} "
              f"src {r.get('src')} → intake · время {str(r['at'])[11:19]} → {str(s['at'])[11:19]} (приёмка) "
              f"· принял {t.get('driver')} · возврат старшим {s['re_at'][11:19]}")
    if not APPLY:
        print("это просмотр; исправить — с --apply")
        return 0
    from bson import json_util
    os.makedirs("/opt/ambar/backups", exist_ok=True)
    path = f"/opt/ambar/backups/qr_readd_fix_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps([r for r, _ in plan], ensure_ascii=False, indent=1))
    print("копия до правки:", path)
    n = 0
    for r, s in plan:
        res = await d.qr_codes.update_one({"_id": r["_id"], "status": "active", "src": "cover", "supply_id": SID},
                                          {"$set": s})
        n += res.modified_count
    print(f"исправлено: {n} из {len(plan)}")
    return 0 if n == len(plan) else 1


sys.exit(asyncio.run(main()))
