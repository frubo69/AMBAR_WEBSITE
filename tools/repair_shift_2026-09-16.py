"""Починка 16 сен 2026: операторы в 11:58 «переоткрыли» вчерашний день вместо
открытия сегодняшнего. Переоткрытие стёрло закрытия 15.09 (shift_days), а
открытий 16.09 и отметок водителей на 16.09 нет. DRY=1 — только показать."""
import datetime, json, os, sys
DRY = os.environ.get("DRY", "1") == "1"
uri = ""
for line in open("/opt/ambar/.env", encoding="utf-8"):
    if line.startswith("MONGO_URI="): uri = line.split("=", 1)[1].strip().strip('"').strip("'")
import pymongo
db = pymongo.MongoClient(uri, serverSelectionTimeoutMS=8000).get_default_database()
UTC = datetime.timezone.utc
def u(h, m, s): return datetime.datetime(2026, 9, 16, h, m, s, tzinfo=UTC)
# из журнала ambar-api (UTC): закрытия 15.09 Парвизом и переоткрытия в 11:58 Дубая
CLOSES = {"jvc": (u(1, 53, 39), 2, 2800), "tecom": (u(1, 53, 40), 5, 2640), "bbay": (u(1, 53, 41), 3, 6750),
          "silicon": (u(1, 53, 42), 2, 2750), "alguses": (u(1, 53, 43), 4, 2485)}
REOPENS = {"bbay": u(7, 58, 6), "tecom": u(7, 58, 59), "jvc": u(7, 59, 2), "silicon": u(7, 59, 7), "alguses": u(7, 59, 8)}
sys.path.insert(0, "/opt/ambar")
for line in open("/opt/ambar/.env", encoding="utf-8"):
    if "=" in line and not line.startswith("#"):
        k, v = line.rstrip("\n").split("=", 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))
import config_staff as staff
parviz = next((int(x.get("telegram_id") or 0) for x in staff.SENIOR_OPERATORS if x.get("name") == "Парвиз"), 0)
opens15 = {d["district"]: d for d in db.shift_opens.find({"day": "2026-09-15", "district": {"$ne": "*"}})}
backup = {"shift_opens_15": [], "shift_days_15": list(db.shift_days.find({"day": "2026-09-15"})),
          "shift_opens_16": list(db.shift_opens.find({"day": "2026-09-16"})),
          "driver_days_16": list(db.driver_days.find({"day": "2026-09-16"}))}
for d in opens15.values(): backup["shift_opens_15"].append(d)
if not DRY:
    with open(f"/root/repair_shift_{datetime.datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
        json.dump(backup, f, default=str, ensure_ascii=False)
plan = []
for oid, (at, n, rev) in CLOSES.items():
    src = opens15.get(oid) or {}
    doc = {"_id": f"2026-09-15:{oid}", "day": "2026-09-15", "district": oid, "closed_at": at, "by": parviz,
           "by_name": "Парвиз", "operator": src.get("operator", ""), "orders": n, "revenue": rev, "open": 0, "open_ids": [],
           "restored_at": datetime.datetime.now(UTC), "restored_why": "переоткрытие 16.09 11:58 стёрло закрытие; восстановлено из журнала"}
    plan.append(("shift_days.insert", doc["_id"], f"закрыта {at.isoformat()} · {n} заказов · {rev} AED · оператор {doc['operator']}"))
    if not DRY:
        db.shift_days.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
for oid, at in REOPENS.items():
    src = opens15.get(oid)
    if not src: plan.append(("SKIP", oid, "нет открытия 15.09")); continue
    doc = {"_id": f"2026-09-16:{oid}", "day": "2026-09-16", "district": oid, "opened_at": at, "by": src.get("by", 0),
           "by_name": src.get("by_name", ""), "operator": src.get("operator", ""), "drivers": dict(src.get("drivers") or {}),
           "moved_from": "2026-09-15", "moved_why": "открыли в 11:58 как переоткрытие вчерашнего дня; перенесено на сегодня"}
    plan.append(("shift_opens.insert", doc["_id"], f"открыта {at.isoformat()} · {doc['by_name']} · водители {doc['drivers']}"))
    if not DRY:
        db.shift_opens.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
    for n, w in (src.get("drivers") or {}).items():
        plan.append(("driver_days.working", f"2026-09-16 {n}", str(bool(w))))
        if not DRY:
            db.driver_days.update_one({"day": "2026-09-16", "driver": n},
                                      {"$set": {"working": bool(w)}, "$setOnInsert": {"day": "2026-09-16", "driver": n}}, upsert=True)
print("DRY" if DRY else "APPLIED")
for p in plan: print("  ", *p)
if not DRY:
    print("проверка: закрытий 15.09:", db.shift_days.count_documents({"day": "2026-09-15", "district": {"$ne": "*"}}),
          "открытий 16.09:", db.shift_opens.count_documents({"day": "2026-09-16"}),
          "отметок 16.09:", db.driver_days.count_documents({"day": "2026-09-16", "working": True}))
