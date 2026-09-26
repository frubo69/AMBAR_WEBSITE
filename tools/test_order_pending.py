"""Заказанное и не забранное заявка второй раз не просит (владелец, 26 сен 2026).

25 сентября водители не успели забрать Силикон: магазин собрал 86 единиц,
пробил слип и отложил их до завтра. Заявка следующего дня считала полку по
складу — товара на ней нет — и собиралась попросить те же 86 заново. Это тот
же случай, что с перемещениями: будущий приход надо вычитать, иначе купим
дважды.

  • открытая задача приёмки вычитается из заявки, как вычитается перемещение;
  • принятое БЕЗ СКАНИРОВАНИЯ не вычитается: те бутылки уже на полке
    (их прибавляет _noscan_after), вычесть вторым концом значит потерять их;
  • закрытая и отменённая задачи не вычитаются — товар уже привезли или
    не привезут;
  • принято наполовину — вычитается только остаток;
  • число видно в клетке отдельным полем pending: не «заявка сломалась»,
    а «столько ждёт на базе».

    python3 tools/test_order_pending.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone                            # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as sr, supply_routes as sup                # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ, РАЙОН = "2026-09-26", "silicon"


async def подменить(have):
    def пусто():
        return {"have": {}, "have_exact": {}, "sug": {}, "came": {}, "gone": {}, "counted": True}
    async def _base(day):
        out = {o: пусто() for o in sr.OFFICE_IDS}
        out[РАЙОН] = dict(пусто(), have=dict(have), have_exact=dict(have),
                          sug={p: 10 for p in have})
        return out
    sr._district_base = _base


async def поставка(pid, need, got=0, *, done=False, cancelled=False, noscan=False,
                   sid="S1", status="open"):
    now = datetime.now(timezone.utc)
    await db._db.supplies.delete_many({})
    await db._db.supplies.insert_one({
        "_id": sid, "status": status, "at": now, "day": ДЕНЬ,
        "items": [{"id": pid, "name": pid, "qty": need,
                   "by_district": {РАЙОН: need}, "got": {РАЙОН: got}}],
        "tasks": {РАЙОН: {"qty": need, "positions": 1, "scanned": got, "driver": "Алишер",
                          "done_at": now if done else None,
                          "cancelled_at": now if cancelled else None,
                          "noscan_at": now if noscan else None}}})


async def клетка(pid):
    sr.base_drop()
    d = await sr.order_rows(ДЕНЬ)
    # Строка с нулевым заказом в rows не попадает — смотрим и в all_rows:
    # клетка существует и тогда, когда просить нечего.
    сп = list(d.get("rows") or []) + list(d.get("all_rows") or [])
    r = next((x for x in сп if x["id"] == pid), None)
    c = (r or {}).get("cells", {}).get(РАЙОН) or {}
    return d["total_qty"], c


async def main():
    db._db = AsyncMongoMockClient()["ambar_order_pending"]
    sr._biz_day = lambda *a, **k: ДЕНЬ
    pid = next(iter(sr._catalog()))
    await подменить({pid: 2})                       # норма 10, на полке 2

    print("── без поставки ───────────────────────────────────────────────")
    await db._db.supplies.delete_many({})
    всего, c = await клетка(pid)
    eq("просим недостающее", (всего, c["need"]), (8, 8))
    eq("ничего не ждёт", c["pending"], 0)

    print("── магазин собрал 6 и ждёт водителя ───────────────────────────")
    await поставка(pid, 6)
    всего, c = await клетка(pid)
    eq("ЗАЯВКА ВЫЧЛА ОЖИДАЮЩЕЕ", (всего, c["need"]), (2, 2))
    eq("и показывает, сколько ждёт", c["pending"], 6)

    print("── половину уже забрали ───────────────────────────────────────")
    await поставка(pid, 6, got=4)
    всего, c = await клетка(pid)
    eq("вычитается только остаток", (c["pending"], c["need"]), (2, 6))

    print("── принято без сканирования: уже на полке ─────────────────────")
    await поставка(pid, 6, noscan=True)
    всего, c = await клетка(pid)
    eq("НЕ вычитаем — иначе потеряем дважды", (c["pending"], c["need"]), (0, 8))

    print("── задача закрыта или отменена ────────────────────────────────")
    await поставка(pid, 6, done=True)
    всего, c = await клетка(pid)
    eq("закрытую не вычитаем", (c["pending"], c["need"]), (0, 8))
    await поставка(pid, 6, cancelled=True)
    всего, c = await клетка(pid)
    eq("отменённую не вычитаем", (c["pending"], c["need"]), (0, 8))
    await поставка(pid, 6, status="done")
    всего, c = await клетка(pid)
    eq("закрытую поставку не вычитаем", (c["pending"], c["need"]), (0, 8))

    print("── ждёт больше, чем нужно ─────────────────────────────────────")
    await поставка(pid, 30)
    всего, c = await клетка(pid)
    eq("в минус не уходим", (c["pending"], c["need"]), (30, 0))

    print("── снимок заявки помнит ожидающее ─────────────────────────────")
    await поставка(pid, 6)
    r = await sr.freeze_order(ДЕНЬ)
    eq("заморожена", r["ok"], True)
    await db._db.supplies.delete_many({})           # товар забрали
    sr.base_drop()
    d = await sr.order_rows(ДЕНЬ)
    _, c = await клетка(pid)
    eq("собранная заявка не пересчитывается", (d["total_qty"], c["need"]), (2, 2))
    eq("и помнит, сколько тогда ждало", c["pending"], 6)

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
