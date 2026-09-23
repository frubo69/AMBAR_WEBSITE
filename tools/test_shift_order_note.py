"""Подпись к заявке, которая уходит старшему ночью (владелец, 23 сен 2026:
«проверь, почему такая заявка странная… почему они разные»).

В ту ночь на одни и те же 424 бутылки файл говорил 36 957 AED, а подпись под
ним — 86 450: в файле закупка, в подписи — сумма по нашему прайсу. Теперь в
подписи то же число, что в файле. Вторая правка — про пересчёт: строка «заявка
посчитана по старым остаткам» осталась от прежнего склада, где остаток жил
только до следующего пересчёта; сейчас он ведётся событиями, и врать так
нельзя.

Без базы: настоящий shift_end, заявка и отправка — заглушки.

    python3 tools/test_shift_order_note.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
import shift_end, db, supply_routes, owner_routes                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

DAY = "2026-09-22"
ЗАЯВКА = {"total_qty": 424, "total_aed": 86450, "total_cost": 36957,
          "rows": [{"pid": f"p{i}"} for i in range(60)],
          "districts": [{"code": "B1", "counted": "2026-09-14"},
                        {"code": "B2", "counted": "2026-09-14"},
                        {"code": "B3", "counted": "2026-09-14"}]}
СМЕНА = {"districts": [{"code": "B1", "name": "JVC", "orders": 6, "revenue": 12000, "open": 0},
                       {"code": "B2", "name": "Бизнес Бей", "orders": 4, "revenue": 8200, "open": 0}]}


async def сказали(заявка) -> str:
    """Собрать смену и вернуть текст, ушедший старшему."""
    ушло = []
    async def mark(day, what): return True
    async def snap(day, data=None): return {}
    async def book(day): return None, None            # файла нет — проверяем текст
    async def rows(day): return заявка
    async def notify(kind, text, **kw): ушло.append(text)
    async def ids(): return []
    db.shift_day_mark, db.shift_day_snapshot, db.get_all_manager_ids = mark, snap, ids
    supply_routes._build_book, supply_routes._order_rows = book, rows
    owner_routes.notify_owners = notify
    await shift_end.on_all_closed(DAY, СМЕНА)
    return ушло[0] if ушло else ""


async def main():
    t = await сказали(ЗАЯВКА)
    eq("в подписи — закупка, как в файле", "закупка 36 957 AED" in t, True)
    eq("суммы по нашему прайсу в подписи нет", "86 450" in t, False)
    eq("сказано, сколько бутылок и позиций", "424 бутылки · 60 позиций" in t, True)
    eq("про «старые остатки» больше не пишем", "старым остаткам" in t, False)
    eq("когда считали — словами и одной строкой",
       "Последний пересчёт складов — 14 сентября" in t, True)
    eq("и честно: остаток с тех пор ведётся сам", "продажи, перемещения" in t, True)

    # Считали сегодня — про пересчёт молчим: напоминать не о чем.
    свежая = {**ЗАЯВКА, "districts": [{"code": "B1", "counted": DAY}, {"code": "B2", "counted": DAY}]}
    t2 = await сказали(свежая)
    eq("посчитали сегодня — строки о пересчёте нет", "пересчёт" in t2.lower(), False)

    # Разные даты — перечисляем по районам, «не делали» тоже словом.
    пёстрая = {**ЗАЯВКА, "districts": [{"code": "B1", "counted": "2026-09-14"},
                                       {"code": "B2", "counted": "2026-09-20"},
                                       {"code": "B3", "counted": ""}]}
    t3 = await сказали(пёстрая)
    eq("даты разные — списком по районам",
       "B1 — 14 сентября" in t3 and "B2 — 20 сентября" in t3 and "B3 — не делали" in t3, True)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
