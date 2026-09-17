"""Норма правилом (владелец, 18 сен 2026). mongomock + настоящие stock_routes:
  • прочерк операторов — ноль, даже если позиция продаётся;
  • спрос решает: покрытие COVER дней плюс подушка, но не выше цифры листа ×1,5;
  • дорогое в район, который его не покупал, не ставим; дешёвое — пробой в 1 шт;
  • позиция, не проданная нигде, — ноль;
  • у пива норма шагает полукоробками."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import norm_rule as M

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

async def main():
    db._db = AsyncMongoMockClient()["ambar_test"]; d = db._db
    cat = SR._catalog()
    fast, slow, dear, cheap, never, beer = "p1", "p14", "p30", "p115", "p66", "p31"
    print("позиции:", {k: cat[k]["name"] for k in (fast, slow, dear, cheap, never, beer)},
          "\nцены:", {k: SR._price(cat[k]) for k in (dear, cheap)})
    # лист операторов: у fast потолок мал нарочно, у slow прочерк
    M.SHEET = {"jvc": {fast: 10, slow: 0, dear: 2, cheap: 4, never: 3, beer: 4},
               "bbay": {fast: 10, slow: 5, dear: 2, cheap: 4, never: 3, beer: 4}}
    # продажи: 20 дней, в jvc быстрая позиция и пиво, в bbay — дорогая и cheap
    for i in range(20):
        t = (T0 + timedelta(days=i)).isoformat()
        await d.orders.insert_one({"_id": f"a{i}", "status": "delivered", "timestamp": t,
            "office_id": "jvc", "items": [{"id": fast, "qty": 4}, {"id": beer, "qty": 24},
                                          {"id": slow, "qty": 1}]})
        await d.orders.insert_one({"_id": f"b{i}", "status": "delivered", "timestamp": t,
            "office_id": "bbay", "items": [{"id": dear, "qty": 1}, {"id": cheap, "qty": 1}]})
    rate, n = await M.demand(db, SR, "2026-08-01")
    eq("окно: 20 дней", n, 20)
    eq("спрос jvc по быстрой позиции — 4 в день", round(rate[("jvc", fast)], 2), 4.0)
    eq("пиво считается коробками: 24 банки = 1 коробка", round(rate[("jvc", beer)], 2), 1.0)
    t = M.build(SR, rate)
    eq("прочерк операторов — ноль, хотя позиция продаётся", t[("jvc", slow)], 0)
    eq("быстрая позиция упёрлась в потолок листа ×1,5 (10 → 15)", t[("jvc", fast)], 15)
    eq("дорогая позиция в район без продаж не ставится", t[("jvc", dear)], 0)
    eq("дешёвая позиция без продаж здесь — проба в 1 шт", t[("jvc", cheap)], 1)
    eq("позиция, не проданная нигде, — ноль", (t[("jvc", never)], t[("bbay", never)]), (0, 0))
    eq("пиво: норма кратна полукоробке", t[("jvc", beer)] * 2 % 1, 0.0)
    eq("где позиция продаётся — не меньше двух", t[("bbay", dear)] >= 2, True)
    eq("дешёвое пиво без продаж здесь — проба в полкоробки", t[("bbay", beer)], 0.5)
    M.SHEET["bbay"][beer] = 0
    eq("но прочерк операторов отменяет и пробу", M.build(SR, rate)[("bbay", beer)], 0)
    M.SHEET["bbay"][beer] = 4

    # запись и откат
    await db.set_stock_norm("jvc", fast, 99, 0)
    res = await M.run(db, SR, apply=False, say=lambda s: None)
    eq("пробный прогон ничего не пишет", (res["ok"], (await db.get_stock_norms())["jvc:" + fast]), (True, 99))
    import tempfile
    res = await M.run(db, SR, apply=True, backup_dir=tempfile.mkdtemp(), say=lambda s: None)
    norms = await db.get_stock_norms()
    rule = await db.stock_norm_rule_get()
    eq("записалось", (res["ok"], norms["jvc:" + fast]), (True, 15))
    eq("правило записано с параметрами", (rule.get("kind"), rule.get("cover"), rule.get("test_max")),
       ("rule", M.COVER, M.TEST_MAX))
    await M.rollback(db, SR, res["backup"], say=lambda s: None)
    eq("откат вернул прежнюю норму", (await db.get_stock_norms())["jvc:" + fast], 99)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
