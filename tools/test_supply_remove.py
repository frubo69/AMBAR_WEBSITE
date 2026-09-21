"""Убрать бутылку из приёмки (владелец, 21 сен 2026: «во время приёмки камера
иногда цепляет бутылки, которые не надо было вносить, а удалить её и
сканировать правильную во время сканирования возможности нет; а эта бутылка
может ещё и потеряться»).

mongomock + настоящие supply_routes.task_scan / task_undo / task_finish /
_notify_done. Позиционный items.$ у mongomock бьёт в первый элемент массива,
поэтому supply_take / supply_untake здесь заменены теми же условиями по
индексу строки: проверяем правила приёмки, а не монгу.

  • камера поймала Red Label, пока сканировали Absolut: он записан как
    Absolut, строка полна, настоящий Absolut уже «по этой всё»;
  • тот же Red Label под своей позицией: «уже в этой приёмке — как Absolut»,
    а не безликое «уже в реестре»;
  • убрали — место в строке Absolut свободно, код свободен: Red Label ложится
    под свою позицию, настоящий Absolut — под свою;
  • убрать можно любую бутылку приёмки и когда угодно, пока район открыт:
    не только последнюю, не только в первые полторы минуты, не три раза;
  • нельзя: чужой водитель, пока сканирует другой, чужая приёмка, чужой
    район, проданная или перевезённая бутылка, закрытый район;
  • пиво: код — полкоробки, и строка возвращается на 0,5;
  • старший видит в итоге приёмки, что именно убрали; отдельный сигнал —
    только когда убирали много.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
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

NOW = datetime.now(timezone.utc)
ABS, RED, BEER = "p1", "p2", "p31"


async def take(sid, district, product_id, room, now, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") != "open":
        return None
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) > room:
        return None
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": qty, f"items.{i}.scanned": 1,
                 f"tasks.{district}.scanned": 1, f"tasks.{district}.rev": 1},
        "$set": {f"tasks.{district}.last_at": now}})
    return await db.supply_get(sid)


async def untake(sid, district, product_id, qty=1):
    s = await db.supply_get(sid)
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) < qty:
        return False
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": -qty, f"items.{i}.scanned": -1,
                 f"tasks.{district}.scanned": -1, f"tasks.{district}.undo": 1,
                 f"tasks.{district}.rev": 1}})
    return True


def поставка(sid, lines, district="jvc", driver="Худоба"):
    return {"_id": sid, "status": "open", "at": NOW, "day": "2026-09-21",
            "items": [{"id": pid, "name": name, "qty": n, "by_district": {district: n},
                       "got": {district: 0}} for pid, name, n in lines],
            "tasks": {district: {"driver": driver, "driver_id": 1, "claimed_at": NOW,
                                 "started_at": None, "noscan_at": None, "done_at": None,
                                 "cancelled_at": None, "erev": 0, "scanned": 0}}}


async def скан(sid, pid, code, who="Худоба", district="jvc"):
    return await sr.task_scan(sid, district, pid, code, who, 1, "", False)


async def строка(sid, pid, district="jvc"):
    s = await db.supply_get(sid)
    it = next(i for i in s["items"] if i["id"] == pid)
    return it["got"][district], it["by_district"][district]


СКАЗАНО = []


async def main():
    db._db = AsyncMongoMockClient()["ambar_remove"]
    db.supply_take, db.supply_untake = take, untake
    import owner_routes
    async def _say(key, text, **kw):
        СКАЗАНО.append((key, text)); return []
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say

    await db._db.supplies.insert_one(поставка("S1", [(ABS, "Absolut 1 ltr", 2), (RED, "Red Label 1 ltr", 1)]))

    print("── камера поймала не ту бутылку ───────────────────────────────")
    eq("первый Absolut принят", (await скан("S1", ABS, "a1"))["ok"], True)
    r = await скан("S1", ABS, "r1")                      # на самом деле Red Label
    eq("соседний Red Label записан как Absolut, строка полна", (r["ok"], r["left"]), (True, 0))
    r = await скан("S1", ABS, "a2")
    eq("настоящий второй Absolut уже не взять", r.get("verdict"), "full")
    r = await скан("S1", RED, "r1")
    eq("тот же Red Label под своей позицией: «уже в этой приёмке — как Absolut»",
       (r.get("verdict"), r.get("ours"), r.get("as_id"), r.get("as_name")),
       ("known", True, ABS, "Absolut 1 ltr"))
    r = await скан("S1", ABS, "a1")
    eq("и на полной строке про свою бутылку говорит то же, а не «по этой всё»",
       (r.get("verdict"), r.get("ours")), ("known", True))

    print("── убрали и внесли правильно ──────────────────────────────────")
    u = await sr.task_undo("S1", "jvc", "r1", "Худоба")
    eq("убрано, и сервер отдаёт строку, из которой убрали",
       (u.get("ok"), u.get("product_id"), u.get("got"), u.get("left"), u.get("need")),
       (True, ABS, 1, 1, 2))
    eq("код свободен — его больше нет в реестре", await db.qr_get("r1"), None)
    eq("Red Label ложится под свою позицию", (await скан("S1", RED, "r1"))["ok"], True)
    eq("настоящий Absolut — под свою", (await скан("S1", ABS, "a2"))["ok"], True)
    eq("строки сошлись: Absolut 2/2, Red Label 1/1",
       (await строка("S1", ABS), await строка("S1", RED)), ((2, 2), (1, 1)))

    print("── любую и когда угодно, пока район открыт ────────────────────")
    await db._db.qr_codes.update_one({"_id": "a1"}, {"$set": {"at": NOW - timedelta(hours=3)}})
    u = await sr.task_undo("S1", "jvc", "a1", "Худоба")
    eq("не последнюю и через три часа", u.get("ok"), True)
    for k in range(4):
        await скан("S1", ABS, f"x{k}")
        u = await sr.task_undo("S1", "jvc", f"x{k}", "Худоба")
    eq("и пятый, шестой раз за приёмку — без потолка", u.get("ok"), True)

    print("── когда нельзя ───────────────────────────────────────────────")
    await скан("S1", ABS, "a3")
    eq("чужой водитель", (await sr.task_undo("S1", "jvc", "a3", "Авазбек")).get("verdict"), "not_mine")
    await db._db.supplies.update_one({"_id": "S1"}, {"$set": {"tasks.jvc.hold": {
        "who": "fixxxik", "kind": "senior", "at": NOW, "until": NOW + timedelta(minutes=5)}}})
    eq("пока сканирует другой", (await sr.task_undo("S1", "jvc", "a3", "Худоба")).get("verdict"), "busy")
    await db._db.supplies.update_one({"_id": "S1"}, {"$unset": {"tasks.jvc.hold": ""}})
    await db._db.qr_codes.insert_one({"_id": "z1", "status": "active", "product_id": ABS, "district": "jvc",
                                      "origin": "jvc", "src": "intake", "supply_id": "S0", "qty": 1, "at": NOW})
    eq("бутылка чужой приёмки", (await sr.task_undo("S1", "jvc", "z1", "Худоба")).get("verdict"), "not_ours")
    await db._db.qr_codes.update_one({"_id": "a3"}, {"$set": {"status": "sold"}})
    eq("уже проданную — нет: у неё своя история", (await sr.task_undo("S1", "jvc", "a3", "Худоба")).get("verdict"), "moved")
    await db._db.qr_codes.update_one({"_id": "a3"}, {"$set": {"status": "active", "district": "bbay"}})
    eq("перевезённую в другой район — тоже", (await sr.task_undo("S1", "jvc", "a3", "Худоба")).get("verdict"), "moved")
    await db._db.qr_codes.update_one({"_id": "a3"}, {"$set": {"district": "jvc"}})
    eq("строка от отказов не пострадала", await строка("S1", ABS), (2, 2))
    s = await db.supply_get("S1")
    await db._db.supplies.update_one({"_id": "S1"}, {"$set": {"tasks.jvc.done_at": NOW}})
    eq("закрытый район", (await sr.task_undo("S1", "jvc", "a3", "Худоба")).get("verdict"), "closed")
    await db._db.supplies.update_one({"_id": "S1"}, {"$set": {"tasks.jvc.done_at": None}})

    print("── пиво: код — полкоробки ─────────────────────────────────────")
    await db._db.supplies.insert_one(поставка("S2", [(BEER, "Heineken 0.33 can", 1)]))
    await скан("S2", BEER, "h1"); await скан("S2", BEER, "h2")
    u = await sr.task_undo("S2", "jvc", "h1", "Худоба")
    eq("убрали код пива — строка вернулась на 0,5", (u.get("got"), u.get("left")), (0.5, 0.5))

    print("── старший видит, что убирали ─────────────────────────────────")
    СКАЗАНО.clear()
    fin = await sr.task_finish("S1", "jvc", "Худоба", "")
    eq("район закрыт", fin.get("ok"), True)
    await asyncio.sleep(0.05)
    итог = next((t for k, t in СКАЗАНО if k == "supply.done"), "")
    eq("в итоге приёмки — что именно убрали", ("Убрано из приёмки" in итог, "Absolut 1 ltr ×6" in итог), (True, True))
    сигнал = next((t for k, t in СКАЗАНО if k == "supply.flag"), "")
    eq("убирали много — отдельный сигнал с числом", "убрано из приёмки: 6 бутылок" in сигнал, True)
    СКАЗАНО.clear()
    await db._db.supplies.insert_one(поставка("S3", [(ABS, "Absolut 1 ltr", 1)]))
    await скан("S3", ABS, "q1"); await sr.task_undo("S3", "jvc", "q1", "Худоба"); await скан("S3", ABS, "q2")
    await sr.task_finish("S3", "jvc", "Худоба", "")
    await asyncio.sleep(0.05)
    eq("одна правка — только строка в итоге, без тревоги",
       ([k for k, t in СКАЗАНО], "Убрано из приёмки: Absolut 1 ltr" in (СКАЗАНО[0][1] if СКАЗАНО else "")),
       (["supply.done"], True))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
