"""Недовоз: отчёт водителя или оператора, решение — за старшим (владелец,
21 сен 2026: «магазин ответил, что предоставит весь товар, а приехали —
половины нет, а заявка требует отсканировать всё, даже то, что нам не дали»;
«конечное решение в обоих случаях, что с водителями, что с операторами,
принимаю я через AMBAR STAR»).

mongomock + настоящие supply_routes (task_scan, short_report, short_decide).

  • отчёт задачу не трогает: остаток тот же, водители сканируют дальше;
  • второй отчёт, пока первый ждёт решения, не ложится;
  • недовоз больше, чем осталось принять, срезается до остатка;
  • «Подтвердить»: недовоз уходит в miss, отсканировать его больше не просят;
    если остальное уже принято — район закрывается сам, с недобором для
    магазина ровно на недовоз;
  • «Отклонить»: ничего не меняется, можно прислать новый отчёт;
  • отчёт оператора до всякого скана: после подтверждения водителям остаётся
    отсканировать ровно то, что привезли, и район закроется на последней
    бутылке «Завершить приёмку» — с недобором на недовоз.
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, supply_routes as sr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
СКАЗАНО = []


async def tell(*a, **k):
    СКАЗАНО.append(a)


def задача(driver=""):
    return {"driver": driver, "driver_id": 1 if driver else 0, "claimed_at": NOW if driver else None,
            "started_at": None, "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0}


async def поставка(sid):
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": NOW, "day": "2026-09-21",
        "items": [{"id": "p1", "name": "Absolut 1 ltr", "qty": 12, "by_district": {"jvc": 12}, "got": {"jvc": 0}},
                  {"id": "p2", "name": "Gordon's 0.7", "qty": 6, "by_district": {"jvc": 6}, "got": {"jvc": 0}}],
        "tasks": {"jvc": задача("Худоба")}})


async def скан(sid, pid, n, start=0):
    """Принятое по позиции — прямо в поставке. Настоящий task_scan на mongomock
    с несколькими позициями кладёт всё в первую (позиционный $ там работает
    не так, как в монге), поэтому здесь число ставим сами: проверяем не скан,
    а то, как недовоз меняет остаток и закрытие."""
    sup = await db.supply_get(sid)
    i = next(k for k, it in enumerate(sup["items"]) if it["id"] == pid)
    await db._db.supplies.update_one({"_id": sid}, {"$inc": {f"items.{i}.got.jvc": n,
                                                             "tasks.jvc.scanned": n}})


async def вид(sid):
    sup = await db.supply_get(sid)
    return sr._task_view(sid, sup, "jvc", sup["tasks"]["jvc"], "Худоба")


async def main():
    db._db = AsyncMongoMockClient()["ambar_short"]
    sr._short_tell = lambda *a, **k: tell("short", *a)
    sr._notify_done = lambda *a, **k: tell("done", *a)

    print("── водитель: привезли 6 Absolut из 12, Gordon's не дали ────────")
    await поставка("S1")
    await скан("S1", "p1", 6)
    v = await вид("S1")
    eq("до отчёта: осталось 6 + 6", (v["got"], v["left"]), (6, 12))
    r = await sr.short_report("S1", "jvc", "Худоба", "driver",
                              [{"id": "p1", "qty": 6}, {"id": "p2", "qty": 6}], "магазин не дал")
    eq("отчёт принят", r.get("ok"), True)
    v = await вид("S1")
    eq("отчёт ждёт решения и задачу не трогает",
       (v["short"]["status"], v["left"], v["done_at"]), ("pending", 12, ""))
    eq("старшему сказали", len([x for x in СКАЗАНО if x[0] == "short"]), 1)
    r = await sr.short_report("S1", "jvc", "Худоба", "driver", [{"id": "p1", "qty": 1}], "")
    eq("второй отчёт, пока первый ждёт, не ложится", r.get("verdict"), "pending")

    print("── старший подтверждает ───────────────────────────────────────")
    r = await sr.short_decide("S1", "jvc", True, "STAR")
    eq("подтверждено, и район закрылся сам: остальное уже принято", (r.get("ok"), r.get("finished")), (True, True))
    sup = await db.supply_get("S1")
    t = sup["tasks"]["jvc"]
    eq("недовоз записан", t.get("miss"), {"p1": 6, "p2": 6})
    eq("недобор для магазина — ровно недовоз",
       sorted((g["id"], g["gap"]) for g in t.get("gaps") or []), [("p1", 6), ("p2", 6)])
    eq("поставка закрыта целиком", sup["status"], "done")
    r = await sr.short_decide("S1", "jvc", False, "STAR")
    eq("решать второй раз нечего", r.get("verdict"), "not_pending")

    print("── отклонение ─────────────────────────────────────────────────")
    await поставка("S2")
    r = await sr.short_report("S2", "jvc", "Худоба", "driver", [{"id": "p1", "qty": 40}], "не было")
    v = await вид("S2")
    eq("недовоз больше остатка срезан до остатка", v["short"]["lines"][0]["qty"], 12)
    r = await sr.short_decide("S2", "jvc", False, "STAR")
    v = await вид("S2")
    eq("отклонён: остаток прежний, недовоза нет",
       (v["short"]["status"], v["left"], (await db.supply_get("S2"))["tasks"]["jvc"].get("miss")),
       ("no", 18, None))
    r = await sr.short_report("S2", "jvc", "Худоба", "driver", [{"id": "p2", "qty": 2}], "")
    eq("после отклонения можно прислать новый", r.get("ok"), True)

    print("── оператор пишет до всякого скана ────────────────────────────")
    await поставка("S3")
    r = await sr.short_report("S3", "jvc", "Мадина", "operator", [{"id": "p1", "qty": 5}], "дали 7 из 12")
    eq("отчёт оператора принят", (r.get("ok"), (await вид("S3"))["short"]["by_kind"]), (True, "operator"))
    r = await sr.short_decide("S3", "jvc", True, "STAR")
    v = await вид("S3")
    eq("после подтверждения: сканировать 7 Absolut и 6 Gordon's, район открыт",
       (next(l["need"] for l in v["lines"] if l["id"] == "p1"), v["left"], v["done_at"], r.get("finished")),
       (7, 13, "", False))
    await скан("S3", "p1", 7)
    await скан("S3", "p2", 6)
    v = await вид("S3")
    eq("всё привезённое принято — осталось ноль", v["left"], 0)
    r = await sr.task_finish("S3", "jvc", "Худоба", "")
    t = (await db.supply_get("S3"))["tasks"]["jvc"]
    eq("«Завершить приёмку» — недобор ровно на недовоз",
       (r.get("ok"), [(g["id"], g["gap"]) for g in t.get("gaps") or []]), (True, [("p1", 5)]))

    print("── закрытому и чужому отчёт не нужен ──────────────────────────")
    r = await sr.short_report("S1", "jvc", "Худоба", "driver", [{"id": "p1", "qty": 1}], "")
    eq("закрытый район", r.get("verdict"), "closed")
    r = await sr.short_report("S3", "jvc", "Худоба", "driver", [{"id": "нет", "qty": 1}], "")
    eq("пустой отчёт", r.get("verdict") in ("empty", "closed"), True)

    print("── найденная бутылка сканируется и выравнивает число ──────────")
    # Владелец, 29 сен 2026: «чтобы учесть человеческий фактор, давай им
    # сканировать столько, сколько по факту есть, просто потом число именно
    # сканированием и выравнивается».
    #
    # Подтверждённый недовоз снимает ОБЯЗАННОСТЬ сканировать недостающее, но
    # не должен запрещать скан, если бутылки нашлись. Предел скана берётся из
    # плана заявки (by_district), а не из _need_eff. Настоящий task_scan на
    # mongomock с несколькими позициями не гоняется (см. помощник «скан»),
    # поэтому сторожим само выражение в исходнике: здесь цена ошибки —
    # отказ сканировать привезённый товар.
    import inspect, re as _re
    src = inspect.getsource(sr.task_scan)
    need_строки = [l.strip() for l in src.splitlines() if _re.match(r"\s*need\s*=", l)]
    eq("предел скана считается по плану заявки",
       need_строки, ['need = int((item.get("by_district") or {}).get(oid) or 0)'])
    eq("и это НЕ заниженное недовозом число",
       any("_need_eff" in l for l in need_строки), False)
    eq("отказ «уже всё» сравнивает с ним же",
       'if got + qty > need:' in src, True)

    # И то же самое числами на виде задачи: после подтверждения недовоза
    # сканировать НАДО меньше, а план района прежний.
    sup = await db.supply_get("S3")
    it = next(i for i in sup["items"] if i["id"] == "p1")
    task = sup["tasks"]["jvc"]
    eq("план района не тронут", int(it["by_district"]["jvc"]), 12)
    eq("а ожидаем меньше ровно на недовоз",
       (sr._need_eff(it, "jvc", task), sr._miss(task, "p1")), (7.0, 5.0))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
