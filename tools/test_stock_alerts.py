"""Критические остатки (владелец, 30 сен 2026): «хватит меньше чем на день, и
когда ноль — для редких»; сообщение раз в сутки; в заявке и на складе красным.

    python3 tools/test_stock_alerts.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as SR, stock_alerts as sa                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

DAY = SR._biz_day()
NOW = datetime.now(timezone.utc)
N = [0]

async def sale(office, pid, qty, days_ago):
    N[0] += 1
    ts = (NOW - timedelta(days=days_ago, hours=1)).isoformat().replace("+00:00", "")
    await db._db.orders.insert_one({"order_id": f"A{N[0]:04d}", "status": "delivered", "office_id": office,
                                    "timestamp": ts, "delivered_at": ts, "items": [{"id": pid, "qty": qty}]})

async def main():
    db._db = AsyncMongoMockClient()["ambar_alerts"]
    cat = SR._catalog()
    # Пересчёт — только что: продажи ниже лежат ДО него и остаток не трогают.
    await db.save_stock_count("jvc", DAY, {
        "district": "jvc", "day": DAY, "counted_at": NOW.isoformat(), "first_time": False, "counted_by": 0,
        "lines": [{"id": p, "name": cat[p]["name"], "price": 100, "unit": SR._unit(cat[p]), "actual": q, "counted": True}
                  for p, q in {"p1": 2, "p2": 0, "p5": 5, "p3": 0, "p10": 1, "p31": 0.5}.items()]})
    for d in range(1, 15):
        await sale("jvc", "p1", 5, d)          # ходовая: 5 в день, на полке 2
        await sale("jvc", "p5", 1, d)          # одна в день, на полке 5
        await sale("bbay", "p1", 9, d)         # район без пересчёта
    await sale("jvc", "p2", 1, 10)             # редкая, на полке пусто
    await sale("jvc", "p10", 1, 6)             # редкая, на полке одна
    await sale("jvc", "p31", 12, 3)            # пиво: полкоробки раз в две недели, на полке полкоробки
    SR.base_drop(); SR._DEMAND["key"] = None

    c = await sa.critical()
    eq("районы", sorted(c), ["jvc"])
    got = {r["id"]: (r["have"], r["zero"]) for r in c["jvc"]}
    eq("ходовой меньше чем на день — критично", got.get("p1"), (2, False))
    eq("редкая и пусто — критично", got.get("p2"), (0, True))
    eq("хватает на день — нет", "p5" in got, False)
    eq("редкая, одна на полке — нет", "p10" in got, False)
    eq("пиво: полкоробки при редких продажах — нет", "p31" in got, False)
    eq("не продавали вовсе — пустая полка не новость", "p3" in got, False)
    eq("район без пересчёта молчит", "bbay" in c, False)
    eq("пустое — первым", [r["id"] for r in c["jvc"]], ["p2", "p1"])
    eq("для экранов — только номера позиций", await sa.ids(), {"jvc": ["p2", "p1"]})

    print("\nСообщение")
    t = sa.text_for(c)
    eq("шапка и район", ("Критические остатки — 2" in t, "B1" in t), (True, True))
    eq("пустое названо пустым, ходовое — числом", ("— пусто" in t, "— 2 · в день 5" in t), (True, True))
    eq("оператору чужого района — ничего", sa.text_for(c, ["bbay"]), "")
    eq("своего — его район", "B1" in sa.text_for(c, ["jvc"]), True)
    eq("критического нет — сообщения нет", sa.text_for({}), "")
    много = {"jvc": [dict(c["jvc"][0], name=f"Позиция {i}") for i in range(20)]}
    eq("длинный список режется", "…и ещё 8" in sa.text_for(много), True)

    print("\nЗаявка и склад")
    data = {"rows": [{"id": "p1", "cells": {"jvc": {"need": 3}, "bbay": {"need": 1}}},
                     {"id": "p5", "cells": {"jvc": {"need": 0}}}], "all_rows": []}
    await sa.mark_order(data)
    eq("в заявке помечены клетка и строка", (data["rows"][0].get("crit"), data["rows"][0]["cells"]["jvc"].get("crit"),
                                             data["rows"][0]["cells"]["bbay"].get("crit")), (True, True, None))
    eq("некритическая — без пометки", data["rows"][1].get("crit"), None)
    import stock_value
    v = await stock_value.build("")
    eq("склад отдаёт, что красить", v.get("critical"), {"jvc": ["p2", "p1"]})

    print("\nРаз в сутки")
    eq("первая отметка дня", await db.once_mark("stock_alert", "2026-09-30"), True)
    eq("вторая — уже стоит", await db.once_mark("stock_alert", "2026-09-30"), False)
    eq("назавтра — снова можно", await db.once_mark("stock_alert", "2026-10-01"), True)
    ушло = []
    import owner_routes
    async def _say(key, text, **kw): ушло.append(key)
    owner_routes.notify_owners_force = _say
    async def _ops(crit): return 3
    sa._tell_operators = _ops
    r = await sa.send()
    eq("рассылка: владельцам и операторам", (r, ушло), ({"positions": 2, "owners": 1, "operators": 3}, ["stock.critical"]))
    await db._db.stock_counts.delete_many({}); SR.base_drop(); ушло.clear()
    r = await sa.send()
    eq("нечего слать — не шлём", (r["positions"], ушло), (0, []))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
