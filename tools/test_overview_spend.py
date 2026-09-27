"""Расход в «Обзоре» считается так же, как в «Сборе выручки» (владелец, 27 сен 2026).

Владелец сверял тетрадь с приложением и каждый день получал разницу в
200–300 дирхам. Нашлось: карточка «Выручка» вычитала из выручки ВСЕ
согласованные расходы подряд — вместе с оплаченными картой, которые из
наличных водителя не уходили, — и брала сумму по модулю, так что «нам
вернули» не уменьшало расход, а увеличивало. За 24.09.26 «Обзор» показывал
расход 3 003 против 953 в «Сборе выручки», и выручка дня выходила на 1 810
меньше той, что водители реально сдали.

  • оплаченное картой в расход не идёт, но видно отдельным полем card;
  • «нам вернули» (we_got) вычитается, а не прибавляется — знак, а не модуль,
    а «мы вернули» (we_gave) остаётся тратой;
  • расход по заказу в долг (nocash) наличных не трогал — мимо;
  • ждущее согласования по-прежнему мимо, отклонённое мимо;
  • по районам — та же арифметика, что в сумме;
  • ГЛАВНОЕ: «Обзор» и «Сбор выручки» за один день дают один расход.

    python3 tools/test_overview_spend.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
import db, owner_routes as own, config_staff as staff                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ, РАЙОН, ВОДИТЕЛЬ = "2026-09-24", "tecom", "Алишер"


def трата(kind, amount, **kw):
    return dict({"kind": kind, "amount": amount, "status": "approved",
                 "comment": kind, "at": "2026-09-24T12:00:00+00:00"}, **kw)


async def смена(extras):
    await db._db.driver_days.delete_many({})
    await db._db.driver_days.insert_one(
        {"_id": f"{ДЕНЬ}:{ВОДИТЕЛЬ}", "day": ДЕНЬ, "driver": ВОДИТЕЛЬ,
         "working": True, "extras": extras})


async def обзор():
    """Та же ручка, что кормит карточку «Выручка»."""
    начало = own._biz_day_start(datetime(2026, 9, 24, 12, tzinfo=timezone.utc))
    return await own._drivers_spend(начало, начало + timedelta(days=1))


async def сбор():
    """Расход того же дня глазами «Сбора выручки»."""
    a = await own._cash_amounts(ДЕНЬ, [])
    return sum(x["spend"] for x in a.values())


async def main():
    db._db = AsyncMongoMockClient()["ambar_overview_spend"]
    staff.DISTRICT_DRIVERS = {РАЙОН: [ВОДИТЕЛЬ]}
    staff.drivers = lambda: [{"name": ВОДИТЕЛЬ, "district": РАЙОН}]
    питание = staff.MEAL_WORKING

    print("── только наличные ────────────────────────────────────────────")
    await смена([трата("fuel", 200, pay="cash")])
    o = await обзор()
    eq("расход = питание + бензин", o["total"], питание + 200)
    eq("безнала нет", o.get("card", 0), 0)
    eq("и «Сбор выручки» считает так же", await сбор(), o["total"])

    print("── та же мойка, но картой ─────────────────────────────────────")
    await смена([трата("wash", 150, pay="card")])
    o = await обзор()
    eq("БЕЗНАЛ ИЗ ВЫРУЧКИ НЕ ВЫЧИТАЕТСЯ", o["total"], питание)
    eq("но виден отдельно", o.get("card", 0), 150)
    eq("два экрана сходятся", await сбор(), o["total"])

    print("── «нам вернули» уменьшает расход, «мы вернули» увеличивает ───")
    # we_got — «Нам вернули», деньги пришли к нам (plus). we_gave — «Мы
    # вернули», это настоящая трата. Раньше модуль суммы стирал разницу.
    await смена([трата("fuel", 200, pay="cash"), трата("we_got", 120)])
    o = await обзор()
    eq("ЗНАК, А НЕ МОДУЛЬ", o["total"], питание + 200 - 120)
    eq("два экрана сходятся", await сбор(), o["total"])
    await смена([трата("we_gave", 120)])
    o = await обзор()
    eq("«мы вернули» — это трата", o["total"], питание + 120)

    print("── расход по заказу в долг ────────────────────────────────────")
    await смена([трата("other", 300, pay="cash", nocash=True)])
    o = await обзор()
    eq("наличных не тронул — мимо", o["total"], питание)
    eq("два экрана сходятся", await сбор(), o["total"])

    print("── ждущее и отклонённое ───────────────────────────────────────")
    await смена([трата("fuel", 200, pay="cash", status="pending"),
                 трата("wash", 90, pay="cash", status="rejected")])
    o = await обзор()
    eq("в расход не идут", o["total"], питание)

    print("── всё вместе, как в живом дне ────────────────────────────────")
    await смена([трата("fuel", 225, pay="cash"), трата("wash", 38, pay="card"),
                 трата("we_owe", 50, pay="cash"), трата("other", 400, pay="card"),
                 трата("other", 115, pay="cash", status="rejected")])
    o = await обзор()
    eq("расход наличными", o["total"], питание + 225 - 50)
    eq("безналом", o.get("card", 0), 438)
    eq("по району — то же число", o["by_district"].get(РАЙОН), o["total"])
    eq("ДВА ЭКРАНА ДАЮТ ОДНО ЧИСЛО", await сбор(), o["total"])

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
