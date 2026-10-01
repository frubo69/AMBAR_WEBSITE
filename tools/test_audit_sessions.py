"""Подходы ревизии: сколько отсканировали за каждый заход в сканер.

Владелец, 1 окт 2026: старший сканирует подходами — 10–15 бутылок, вернулся к
списку — и хочет видеть, сколько было в последнем и в предыдущих. «Если зашёл,
ничего не отсканировал и вернулся — не надо писать 0, от 1+ пиши».

  • сканы одного захода (одна метка ses) — один подход;
  • пустой заход подхода не даёт: подход делают сканы;
  • повтор бутылки в другом подходе его не раздувает (ключ район:день:код);
  • сканы без метки (старые ревизии) делятся по паузам больше трёх минут;
  • убрали скан — подход похудел, убрали последний — подход исчез;
  • новые сверху, номера по порядку работы.

    python3 tools/test_audit_sessions.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as sr                                      # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

Р, Д = "jvc", "2026-10-01"
T0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


async def main():
    db._db = AsyncMongoMockClient()["ambar_ses"]
    cat = sr._catalog(); pid = next(iter(cat)); pid2 = list(cat)[1]
    for i in range(6):
        p_ = pid if i < 4 else pid2
        await db.qr_add(f"q{i}", p_, cat[p_]["name"], Р, 1, T0)
    async def скан(code, ses, at):
        r = await sr.audit_scan(Р, Д, code, 1, "Старший", ses)
        await db._db.audit_scans.update_one({"_id": db._audit_key(Р, Д, code)}, {"$set": {"at": at}})
        return r

    print("── два захода и пустой между ними ─────────────────────────")
    await скан("q0", "aaa", T0); await скан("q1", "aaa", T0 + timedelta(seconds=20))
    await скан("q2", "aaa", T0 + timedelta(seconds=45))
    # заход «bbb» — зашёл и вышел, ни одного скана
    await скан("q4", "ccc", T0 + timedelta(minutes=1)); await скан("q5", "ccc", T0 + timedelta(minutes=1, seconds=30))
    s = await db.audit_sessions(Р, Д)
    eq("подходов два, пустого нет", [(x["no"], x["id"], x["n"]) for x in s], [(2, "ccc", 2), (1, "aaa", 3)])
    eq("кто сканировал", s[0]["by_name"], "Старший")
    eq("что в подходе", [(i["id"], i["qty"]) for i in s[1]["items"]], [(pid, 3.0)])

    print("── повтор той же бутылки в новом подходе ──────────────────")
    r = await sr.audit_scan(Р, Д, "q0", 1, "Старший", "ddd")
    eq("повтор не записан", r["new"], False)
    eq("и подхода из него не вышло", [x["id"] for x in await db.audit_sessions(Р, Д)], ["ccc", "aaa"])

    print("── убрали скан ────────────────────────────────────────────")
    await db.audit_scan_del(Р, Д, "q5")
    eq("подход похудел", [(x["id"], x["n"]) for x in await db.audit_sessions(Р, Д)], [("ccc", 1), ("aaa", 3)])
    await db.audit_scan_del(Р, Д, "q4")
    eq("убрали последний — подход исчез", [x["id"] for x in await db.audit_sessions(Р, Д)], ["aaa"])

    print("── старые сканы без метки — по паузам ─────────────────────")
    await db._db.audit_scans.delete_many({})
    for i, сек in enumerate((0, 30, 60, 400, 430, 2000)):
        await db._db.audit_scans.insert_one({"_id": db._audit_key(Р, Д, f"z{i}"), "district": Р, "day": Д, "code": f"z{i}",
                                             "at": T0 + timedelta(seconds=сек), "by": 1, "product_id": pid,
                                             "product_name": "x", "verdict": "ok", "qty": 1})
    eq("три подхода: 3, 2 и 1", [x["n"] for x in await db.audit_sessions(Р, Д)], [1, 2, 3])

    print("── лист ревизии отдаёт подходы ────────────────────────────")
    await db.audit_set(Р, Д, {"started_at": T0.isoformat(), "started_by": 1})
    sh = await sr.audit_sheet(Р, Д)
    eq("в листе они есть, время строкой", (len(sh["sessions"]), isinstance(sh["sessions"][0]["from"], str)), (3, True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
