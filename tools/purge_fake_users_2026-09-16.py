"""Снести накрученные аккаунты 16 сен 2026 (владелец купил ~300 «посещений»
бота): 298 записей users за один час, у всех язык не выбран, заказов нет,
телефона нет, верификации нет. Живые 998 не трогаем. DRY=1 — показать;
DRY=0 — копия затронутого в /root и удаление.
Запуск на VPS: cd /opt/ambar && DRY=0 venv/bin/python tools/purge_fake_users_2026-09-16.py"""
import datetime, json, os
DRY = os.environ.get("DRY", "1") == "1"
uri = ""
for line in open("/opt/ambar/.env", encoding="utf-8"):
    if line.startswith("MONGO_URI="): uri = line.split("=", 1)[1].strip().strip('"').strip("'")
import pymongo
db = pymongo.MongoClient(uri, serverSelectionTimeoutMS=8000).get_default_database()
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
D4 = datetime.timedelta(hours=4)
win_from = datetime.datetime(2026, 9, 16, 12, 0)     # 16:00 Дубай = 12:00 UTC, начало накрутки
q = {"first_seen": {"$gte": win_from, "$lte": now},
     "$or": [{"lang": None}, {"lang": {"$exists": False}}, {"lang": ""}],
     "$and": [{"$or": [{"orders_total": {"$in": [0, None]}}, {"orders_total": {"$exists": False}}]},
              {"$or": [{"phones": {"$in": [[], None]}}, {"phones": {"$exists": False}}]},
              {"$or": [{"phone_verified": {"$in": [False, None]}}, {"phone_verified": {"$exists": False}}]}]}
rows = list(db.users.find(q))
fs = sorted(u["first_seen"] for u in rows)
print(f"под удаление: {len(rows)}; first_seen с {(fs[0]+D4).strftime('%H:%M:%S') if fs else '-'} по {(fs[-1]+D4).strftime('%H:%M:%S') if fs else '-'} Дубай")
ids = [int(u["telegram_id"]) for u in rows if u.get("telegram_id")]
print("их заказов:", db.orders.count_documents({"customer_id": {"$in": ids}}))
print("всего users до:", db.users.count_documents({}))
if not DRY and rows:
    path = f"/root/fake_users_{now.strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump(rows, f, default=str, ensure_ascii=False)
    r = db.users.delete_many({"_id": {"$in": [u["_id"] for u in rows]}})
    print("удалено:", r.deleted_count, "копия:", path)
    print("всего users после:", db.users.count_documents({}))
else:
    print("DRY: ничего не удалено")
