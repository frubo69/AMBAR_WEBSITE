"""Доп. заявка: на базе нет позиции (владелец, 30 сен 2026).

  • «нет в наличии» снимает позицию с района: цены не требует, приёмку
    остального не держит, со склада и из «ждёт на базе» уходит;
  • район закрыли с недобором — недостающее собирается в следующую доп.
    заявку (черновиком), где написано, на каких базах уже искали;
  • цена другой базы живёт при этой базе и в закупочную цену позиции не идёт;
  • в сумму закупки то, чего не оказалось, не входит.

    python3 tools/test_extra_na.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_value                       # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr"}
PLAN = {"jvc": {"p1": 3, "p2": 2}, "bbay": {"p1": 2, "p5": 4}}
DRV = {"jvc": "Худоба", "bbay": "Авазбек"}
СКАЗАНО = []

async def make(sid="X1", base="База Один", **kw):
    now = datetime.now(timezone.utc)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": "2026-09-30", "kind": "extra", "base": base,
        "items": [{"id": p, "name": NAMES[p], "qty": sum(PLAN[o].get(p, 0) for o in PLAN),
                   "asked": sum(PLAN[o].get(p, 0) for o in PLAN),
                   "by_district": {o: PLAN[o][p] for o in PLAN if p in PLAN[o]},
                   "got": {o: 0 for o in PLAN if p in PLAN[o]}}
                  for p in sorted({p for o in PLAN for p in PLAN[o]})],
        "tasks": {o: {"driver": DRV[o], "driver_id": 1, "claimed_at": now, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "scanned": 0}
                  for o in PLAN}, **kw})
    return sid

async def view(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])

async def main():
    db._db = AsyncMongoMockClient()["ambar_extra_na"]
    import owner_routes
    async def _say(key, text, **kw): СКАЗАНО.append(text); return []
    owner_routes.notify_owners = _say; owner_routes.notify_owners_force = _say
    sr._buy_note = lambda *a, **k: None

    print("Нет в наличии")
    sid = await make()
    eq("без цен приёмка не начинается", (await sr.task_noscan(sid, "jvc", "Худоба")).get("verdict"), "prices_needed")
    sup = await db.supply_get(sid)
    await sr.buy_set(sup, "p1", 40, 0, "Худоба", 1)
    v = await view(sid, "jvc")
    eq("одна цена есть, второй нет", (v["prices_ok"], v["left"]), (False, 5))
    r = await sr.na_set(sid, "jvc", "p2", True, "Худоба")
    eq("отметил «нет в наличии»", r.get("ok"), True)
    v = r["task"]
    eq("цены больше не держат", v["prices_ok"], True)
    eq("сканировать осталось только то, что есть", (v["need"], v["left"]), (3, 3))
    eq("строка помечена", [(l["id"], l["na"], l["miss"]) for l in v["lines"] if l["id"] == "p2"], [("p2", True, 2)])
    eq("«ждёт на базе» без неё", (await sr.pending_qty()).get(("jvc", "p2")), None)
    eq("а остальное ждёт", (await sr.pending_qty()).get(("jvc", "p1")), 3.0)
    eq("чужой район отметить нельзя", (await sr.na_set(sid, "bbay", "p1", True, "Худоба")).get("verdict"), "not_mine")
    eq("позицию не из района — нельзя", (await sr.na_set(sid, "jvc", "p5", True, "Худоба")).get("verdict"), "not_in_supply")
    r = await sr.na_set(sid, "jvc", "p2", False, "Худоба")
    eq("нашлась — отметку сняли, цена снова нужна", (r["task"]["prices_ok"], r["task"]["left"]), (False, 5))
    await db._db.supplies.update_one({"_id": sid, "items.id": "p1"}, {"$set": {"items.0.got.jvc": 1}})
    eq("по позиции уже приняли — только через «Не всё привезли»",
       (await sr.na_set(sid, "jvc", "p1", True, "Худоба")).get("verdict"), "taken")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"items.0.got.jvc": 0}})
    await make("S9", kind="main", base="")
    eq("у основной заявки такой отметки нет", (await sr.na_set("S9", "jvc", "p2", True, "Худоба")).get("verdict"), "not_extra")
    await db._db.supplies.delete_one({"_id": "S9"})

    print("\nРайон закрыли — следующая доп. заявка")
    await sr.na_set(sid, "jvc", "p2", True, "Худоба")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"items.0.got.jvc": 3, "tasks.jvc.scanned": 3}})
    СКАЗАНО.clear()
    r = await sr.task_finish(sid, "jvc", "Худоба", "")
    eq("район закрыт, недобор — то, чего не было", (r["ok"], [(g["id"], g["gap"]) for g in r["gaps"]]), (True, [("p2", 2)]))
    x2 = r["next_draft"]
    eq("собрана следующая доп. заявка", bool(x2), True)
    d = await db.supply_get(x2)
    eq("черновик, без базы, водителям не видна", (d["status"], d["base"], d["kind"]), ("draft", "", "extra"))
    eq("состав — недостающее по району", {i["id"]: i["by_district"] for i in d["items"]}, {"p2": {"jvc": 2}})
    eq("написано, где уже искали", (d["tried_bases"], d["from_supply"]), (["База Один"], sid))
    eq("старшему сказано", any("новой доп. заявке" in t for t in СКАЗАНО), True)
    t = await sr.tasks_for_driver("Худоба", "jvc")
    eq("у водителей её нет", [x["supply_id"] for k in t for x in t[k] if x["supply_id"] == x2], [])

    print("\nВторой район той же заявки — в тот же черновик")
    await sr.na_set(sid, "bbay", "p5", True, "Авазбек")
    sup = await db.supply_get(sid)
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"items.0.got.bbay": 1, "tasks.bbay.scanned": 1}})
    await sr.short_report(sid, "bbay", "Авазбек", "driver", [{"id": "p1", "qty": 1}], "")
    r = await sr.task_finish(sid, "bbay", "Авазбек", "")
    eq("черновик тот же", r["next_draft"], x2)
    d = await db.supply_get(x2)
    eq("в нём оба района", {i["id"]: i["by_district"] for i in d["items"]},
       {"p2": {"jvc": 2}, "p1": {"bbay": 1}, "p5": {"bbay": 4}})
    eq("итог и задачи пересчитаны", (d["total_qty"], {o: t["qty"] for o, t in d["tasks"].items()}), (7, {"jvc": 2, "bbay": 5}))
    eq("родитель закрыт", (await db.supply_get(sid))["status"], "done")

    print("\nНа второй базе тоже нет — цепочка идёт дальше")
    await db.supply_set(x2, {"status": "open", "base": "База Два"}, only_open=False)
    await db._db.supplies.update_one({"_id": x2}, {"$set": {"tasks.jvc.driver": "Худоба"}})
    await sr.na_set(x2, "jvc", "p2", True, "Худоба")
    await asyncio.sleep(1.1)
    r = await sr.task_finish(x2, "jvc", "Худоба", "")
    d3 = await db.supply_get(r["next_draft"])
    eq("третья заявка помнит обе базы", (d3["tried_bases"], d3["from_supply"], r["next_draft"] != x2),
       (["База Один", "База Два"], x2, True))

    print("\nЦены других баз")
    eq("цена записана при своей базе", [(p["base"], p["item"], p["price"]) for p in await db.base_prices("база один")],
       [("База Один", "p1", 40.0)])
    stock_value._COST["at"] = 0; stock_value._COST["map"] = {}
    было = dict(await stock_value.cost_map())
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"buys.p1.price": 9999}})
    stock_value._COST["at"] = 0; stock_value._COST["map"] = {}
    eq("в закупочную цену позиции она не идёт", (await stock_value.cost_map()).get("p1"), было.get("p1"))
    await db._db.supplies.insert_one({"_id": "S8", "status": "done", "at": datetime.now(timezone.utc),
                                      "kind": "main", "buys": {"p1": {"price": 77}}, "items": [], "tasks": {}})
    stock_value._COST["at"] = 0; stock_value._COST["map"] = {}
    try:
        import config_cost
        в_прайсе = "p1" in getattr(config_cost, "COST", {})
    except Exception:
        в_прайсе = False
    eq("а цена докупки основной заявки — идёт, как раньше (если её не перебивает прайс)",
       в_прайсе or (await stock_value.cost_map()).get("p1") == 77, True)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
