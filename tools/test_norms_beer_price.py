"""Страница «Нормы»: пиво стоит коробками, а не банками (владелец, 11 ноя 2026:
«чини оба» — на слова «страница «Нормы», где пиво считается по цене банки при
норме в коробках»).

Что было. handle_norms отдавал цену строки как прайс учётной единицы ÷ число
бутылок в ней: у пива — прайс коробки ÷ 24, цена банки. А норма пива — в
коробках (шаг 0,5). Стоимость полки по норме (norm_aed, её же считает
приложение после «+»/«−») и «заморожено» (frozen_aed: сверх расчёта × цена)
у пива выходили в 24 раза меньше. Теперь цена строки — прайс учётной единицы:
у бутылки как была, у пива — за коробку.

Настоящая ручка handle_norms на mongomock; расчёт нормы и основа склада
подставлены (норма от продаж здесь ни при чём).

    python3 tools/test_norms_beer_price.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, stock_routes as SR                                       # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = "2026-11-11"; РАЙОН = SR.OFFICE_IDS[0]
ПИВО, БУТЫЛКА = "p43", "p1"

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h


async def main():
    db._db = AsyncMongoMockClient()["ambar_norms_beer"]
    cat = SR._catalog()
    коробка, бутылка = SR._price(cat[ПИВО]), SR._price(cat[БУТЫЛКА])
    eq("прайс: у пива за коробку из 24, у бутылки за штуку", (SR._unit(cat[ПИВО]), SR._unit(cat[БУТЫЛКА])), (24, 1))
    # Расчёт предлагает пива 1 коробку, бутылок 3; владелец поставил 2,5 коробки
    # и 3 бутылки. На полке 4,5 и 2.
    async def _sug(district, day, detail=False):
        return {ПИВО: {"norm": 1, "expect": 1, "safety": 0, "base": 0.1, "cls": ""},
                БУТЫЛКА: {"norm": 3, "expect": 3, "safety": 0, "base": 0.4, "cls": ""}}
    async def _base(day):
        return {РАЙОН: {"have": {ПИВО: 4.5, БУТЫЛКА: 2}}}
    SR._suggested_norms = _sug; SR._district_base = _base
    await db.set_stock_norm(РАЙОН, ПИВО, 2.5, 1)
    await db.set_stock_norm(РАЙОН, БУТЫЛКА, 3, 1)

    r = make_mocked_request("GET", f"/api/owner/stock/norms?district={РАЙОН}&day={D}"); r["owner_id"] = 1
    resp = await unwrap(SR.handle_norms)(r)
    eq("ручка ответила", resp.status, 200)
    w = json.loads(resp.text)
    rows = {x["id"]: x for x in w["rows"]}
    eq("ЦЕНА СТРОКИ ПИВА — ПРАЙС КОРОБКИ", rows[ПИВО]["price"], коробка)
    eq("цена строки бутылки — как была", rows[БУТЫЛКА]["price"], бутылка)
    eq("норма и остаток пива — коробками", (rows[ПИВО]["norm"], rows[ПИВО]["have"], rows[ПИВО]["unit"]), (2.5, 4.5, 24))
    eq("ПОЛКА ПО НОРМЕ: 2,5 коробки × прайс коробки + 3 бутылки × прайс",
       w["norm_aed"], round(2.5 * коробка + 3 * бутылка, 2))
    eq("ЗАМОРОЖЕНО: сверх расчёта 1,5 коробки × прайс коробки", w["frozen_aed"], round(1.5 * коробка, 2))
    eq("количество по норме", w["norm_qty"], 5.5)
    eq("вручную — одна строка (бутылки совпали с расчётом)", w["manual"], 1)
    # То же, что считает приложение после «+»/«−»: Σ norm × price по строкам.
    eq("приложение сложит то же", round(sum(x["norm"] * x["price"] for x in w["rows"])), round(w["norm_aed"]))

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
