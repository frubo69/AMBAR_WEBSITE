"""Заявка собирается один раз в день и дальше не гуляет (владелец, 25 сен 2026:
«заявка формируется автоматически только ОДИН РАЗ В ДЕНЬ — после закрытия
смены, и она НЕ динамическая»).

До этого экран пересчитывал её при каждом открытии: продали бутылку — число
выросло, приняли товар — упало. 25 сен заявка на глазах у владельца съехала
с 521 на 441, потому что между двумя взглядами приняли район.

  • пока снимка нет — расчёт живой и помечен как предварительный;
  • заморозили — числа перестают зависеть от склада: продажи и приёмки их
    не двигают;
  • второй раз заморозить нельзя: снимок дня пишется единожды;
  • ручные правки и «не везём сюда» продолжают работать поверх снимка —
    их замораживать нельзя, их правят весь день;
  • день закупки хранится отдельно: смену закрывают утром, и собранная
    заявка — на сегодня.

    python3 tools/test_order_frozen.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as sr                                      # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-24"
СКЛАД = {}          # что «лежит на полке» — подменяем, чтобы двигать расчёт


async def подменить(have):
    """Склад района B1 по позициям: {pid: сколько}."""
    def пусто():
        return {"have": {}, "have_exact": {}, "sug": {}, "came": {}, "gone": {},
                "counted": True}
    async def _base(day):
        out = {o: пусто() for o in sr.OFFICE_IDS}
        out["jvc"] = dict(пусто(), have=dict(have), have_exact=dict(have),
                          sug={p: 10 for p in have})
        return out
    sr._district_base = _base


async def main():
    db._db = AsyncMongoMockClient()["ambar_order_frozen"]
    sr._biz_day = lambda *a, **k: ДЕНЬ
    первый = next(iter(sr._catalog()))
    await подменить({первый: 2})            # норма 10, на полке 2 → просим 8

    print("── пока не заморозили — расчёт живой ──────────────────────────")
    d = await sr.order_rows(ДЕНЬ)
    eq("помечен предварительным", d["frozen"], False)
    было = d["total_qty"]
    eq("просит недостающее", было, 8)
    await подменить({первый: 6})            # приняли товар — на полке стало 6
    d = await sr.order_rows(ДЕНЬ)
    eq("и живой расчёт тут же поехал", d["total_qty"], 4)

    print("── замораживаем ───────────────────────────────────────────────")
    r = await sr.freeze_order(ДЕНЬ)
    eq("заморожена", (r["ok"], r["day"]), (True, ДЕНЬ))
    eq("день закупки записан", bool(r.get("buy_day")), True)
    d = await sr.order_rows(ДЕНЬ)
    eq("теперь заявка помечена собранной", d["frozen"], True)
    зам = d["total_qty"]
    eq("число — то, что было в момент заморозки", зам, 4)

    print("── склад двигается, заявка стоит ──────────────────────────────")
    await подменить({первый: 0})            # всё продали
    d = await sr.order_rows(ДЕНЬ)
    eq("после продаж не выросла", d["total_qty"], зам)
    await подменить({первый: 99})           # приняли вагон
    d = await sr.order_rows(ДЕНЬ)
    eq("после приёмки не упала", d["total_qty"], зам)

    print("── второй раз не замораживается ───────────────────────────────")
    r2 = await sr.freeze_order(ДЕНЬ)
    eq("сказал, что уже", r2.get("already"), True)
    eq("и число прежнее", (await sr.order_rows(ДЕНЬ))["total_qty"], зам)
    # прямая запись поверх замороженного дня — тихий отказ, а не ошибка ключа
    eq("запись поверх снимка отклонена без поломки",
       await db.zayavka_freeze_set(ДЕНЬ, {"xxx": {"jvc": {"calc": 99}}}, "2026-09-26"), False)
    eq("и снимок не переписан", (await sr.order_rows(ДЕНЬ))["total_qty"], зам)

    print("── правки и «не везём» живут поверх снимка ────────────────────")
    await db.zayavka_edit_set(ДЕНЬ, первый, "jvc", 3)
    d = await sr.order_rows(ДЕНЬ)
    eq("правка поменяла заявку", d["total_qty"], 3)
    eq("и видно, что правлено руками", d["edited_count"], 1)
    await db.zayavka_edit_set(ДЕНЬ, первый, "jvc", None)
    eq("сняли правку — вернулось", (await sr.order_rows(ДЕНЬ))["total_qty"], зам)
    await db.zayavka_off_set(ДЕНЬ, "jvc", True)
    d = await sr.order_rows(ДЕНЬ)
    eq("выключенный район не везём", d["total_qty"], 0)
    await db.zayavka_off_set(ДЕНЬ, "jvc", False)
    eq("вернули район — вернулось", (await sr.order_rows(ДЕНЬ))["total_qty"], зам)

    print("── без дня экран получает ПОСЛЕДНЮЮ собранную ────────────────")
    d = await sr.order_rows()            # как зовёт экран: без дня
    eq("показана собранная, а не прикид на текущий день", (d["day"], d["frozen"]), (ДЕНЬ, True))
    eq("и число её же", d["total_qty"], зам)

    print("── спросили ДЕНЬ ЗАКУПКИ — получили тот же документ ──────────")
    # экран просит «заявку на 25-е», а документ лежит под 24-м
    куплен = (await db.zayavka_freeze_get(ДЕНЬ))["buy_day"]
    d = await sr.order_rows(куплен)
    eq("нашли по дню закупки", (d["day"], d["frozen"]), (ДЕНЬ, True))
    eq("и число документа, а не пересчёт", d["total_qty"], зам)

    print("── новый день — своя заявка ───────────────────────────────────")
    # день, который не совпадает ни с днём документа, ни с его днём закупки
    eq("чужой день не заморожен", (await sr.order_rows("2026-10-10"))["frozen"], False)

    await фазы()
    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


async def фазы():
    """Заявка собралась — но окончательный вид принимает после ответа магазина."""
    print("\n── фазы заявки ────────────────────────────────────────────────")
    eq("пока не собрана — предварительный расчёт",
       (await sr.order_rows("2026-09-30"))["phase"], "draft")
    eq("собрана, магазин не ответил", (await sr.order_rows(ДЕНЬ))["phase"], "asked")

    async def поставка(tasks, status="open"):
        await db._db.supplies.delete_many({"day": ДЕНЬ})
        await db.supply_save({"_id": "S" + str(len(tasks)) + status, "day": ДЕНЬ,
                              "kind": "main", "status": status, "at": "now",
                              "items": [], "tasks": tasks,
                              "asked_qty": 691, "total_qty": 511, "gap_qty": 180})
        return await sr.order_rows(ДЕНЬ)

    d = await поставка({"jvc": {}, "bbay": {}})
    eq("магазин ответил — за товаром не ездили", d["phase"], "answered")
    eq("и числа ответа на месте",
       (d["supply"]["asked_qty"], d["supply"]["give_qty"], d["supply"]["gap_qty"]),
       (691, 511, 180))
    d = await поставка({"jvc": {"claimed_at": "t"}, "bbay": {}})
    eq("водитель взял район — идёт приёмка", d["phase"], "taking")
    d = await поставка({"jvc": {"done_at": "t"}, "bbay": {"done_at": "t"}})
    eq("приняли всё — завершена", d["phase"], "done")
    d = await поставка({"jvc": {"done_at": "t"}, "bbay": {"cancelled_at": "t"}})
    eq("отменённый район не мешает завершению", d["phase"], "done")
    d = await поставка({"jvc": {}}, status="cancelled")
    eq("заявку отменили целиком", d["phase"], "cancelled")

    # Владелец, 25 сен 2026: «в верхней табличке цифры у нас только по
    # основной заявке фиксируются». Докупка на другой базе живёт рядом и в
    # эти числа не подмешивается — ни в «просили», ни в «дают», ни в фазу.
    await db._db.supplies.delete_many({"day": ДЕНЬ})
    d = await поставка({"jvc": {"done_at": "t"}})
    await db.supply_save({"_id": "X1", "day": ДЕНЬ, "kind": "extra", "status": "open",
                          "at": "now", "items": [], "tasks": {"bbay": {}},
                          "asked_qty": 180, "total_qty": 180, "gap_qty": 0})
    d2 = await sr.order_rows(ДЕНЬ)
    eq("докупка не тронула числа основной",
       (d2["supply"]["asked_qty"], d2["supply"]["give_qty"], d2["supply"]["gap_qty"]),
       (691, 511, 180))
    eq("и не сдвинула фазу", d2["phase"], "done")
    eq("это по-прежнему основная заявка", d2["supply"]["supply_id"], d["supply"]["supply_id"])
    await db._db.supplies.delete_many({"day": ДЕНЬ})


sys.exit(asyncio.run(main()))
