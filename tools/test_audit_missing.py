"""Ревизия говорит, КАКИХ бутылок не нашли (владелец, 24 сен 2026: «что теперь
с недостачей?»).

«Не хватает 1» у полки бесполезно: искать нечего, если не сказать какую. Лист
ревизии отдаёт по строкам с недостачей коды, которых камера не видела, — с
этикеткой и тем, откуда бутылка приехала. Их бывает больше, чем недостача: у
проданной бутылки код из реестра не исчезает, — поэтому это кандидаты на
поиск, а не приговор.

    python3 tools/test_audit_missing.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as sr                                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

РАЙОН, ДЕНЬ = "tecom", "2026-09-23"
ПОЗ = "p1"


async def завести(коды, сканы):
    db._db = AsyncMongoMockClient()["ambar_audit_missing"]
    sr.base_drop()
    t0 = datetime.now(timezone.utc) - timedelta(days=3)
    for i, (c, откуда) in enumerate(коды):
        doc = {"_id": c, "status": "active", "product_id": ПОЗ, "district": РАЙОН,
               "qty": 1, "label": f"abs#{i:06d}", "at": t0 + timedelta(minutes=i),
               "origin": откуда or РАЙОН}
        if откуда and откуда != РАЙОН:
            doc["moves"] = [{"from": откуда, "to": РАЙОН, "at": (t0 + timedelta(hours=1)).isoformat()}]
        await db._db.qr_codes.insert_one(doc)
    for c in сканы:
        await db._db.audit_scans.insert_one(
            {"district": РАЙОН, "day": ДЕНЬ, "code": c, "product_id": ПОЗ,
             "qty": 1, "at": datetime.now(timezone.utc)})


async def main():
    # Три бутылки на районе, камера видела одну.
    await завести([("101", ""), ("102", "jvc"), ("103", "")], ["101"])
    нет = await db.audit_unscanned(РАЙОН, ДЕНЬ, [ПОЗ])
    eq("вернулись те, кого камера не видела", sorted(x["code"] for x in нет[ПОЗ]), ["102", "103"])
    eq("у приехавшей видно, откуда", next(x["from"] for x in нет[ПОЗ] if x["code"] == "102"), "jvc")
    eq("этикетка на месте", all(x["label"] for x in нет[ПОЗ]), True)
    eq("отсканированной в списке нет", "101" in [x["code"] for x in нет[ПОЗ]], False)

    await завести([("101", ""), ("102", "")], ["101", "102"])
    eq("всё отсканировано — искать нечего", await db.audit_unscanned(РАЙОН, ДЕНЬ, [ПОЗ]), {})

    await завести([(str(200 + i), "") for i in range(20)], [])
    eq("список ограничен", len((await db.audit_unscanned(РАЙОН, ДЕНЬ, [ПОЗ], limit=12))[ПОЗ]), 12)

    # Списанную бутылку не ищем: она и не должна лежать.
    await завести([("101", ""), ("102", "")], [])
    await db._db.qr_codes.update_one({"_id": "102"}, {"$set": {"status": "written"}})
    eq("списанная в поиск не идёт",
       [x["code"] for x in (await db.audit_unscanned(РАЙОН, ДЕНЬ, [ПОЗ]))[ПОЗ]], ["101"])

    # Лист ревизии кладёт список только там, где недостача.
    await завести([("101", ""), ("102", ""), ("103", "")], ["101"])
    await db._db.stock_counts.insert_one({
        "district": РАЙОН, "day": "2026-09-14", "counted_at": (datetime.now(timezone.utc)
                                                               - timedelta(days=5)).isoformat(),
        "lines": [{"id": ПОЗ, "actual": 3}]})
    строки, _ = await sr._audit_lines(РАЙОН, ДЕНЬ)
    строка = next(r for r in строки if r["id"] == ПОЗ)
    eq("недостача посчитана", (строка["expected"], строка["actual"], строка["diff"]), (3, 1, 2))
    eq("и рядом — кого искать", sorted(m["code"] for m in строка.get("missing") or []), ["102", "103"])
    другие = [r for r in строки if r["id"] != ПОЗ and r.get("missing")]
    eq("у строк без недостачи списка нет", другие, [])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
