"""Недобор с отменёнными районами и вычет уже заказанного на других базах
(supply_routes._shortfall + _cover). Запуск: python3 tools/test_supply_reorder.py"""
import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
import supply_routes as sr

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<60} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

# доп. заявка на базу ABC: район a закрыт с недобором 4, район b отменён (ничего не приняли), c принят
X1 = {"_id": "X1", "kind": "extra", "base": "ABC", "status": "done",
      "items": [{"id": "gin", "name": "Джин", "asked": 30, "qty": 30,
                 "by_district": {"a": 10, "b": 10, "c": 10}, "got": {"a": 6, "b": 0, "c": 10}},
                {"id": "rum", "name": "Ром", "asked": 5, "qty": 5,
                 "by_district": {"b": 5}, "got": {"b": 0}}],
      "tasks": {"a": {"done_at": "d", "gaps": [{"id": "gin", "name": "Джин", "need": 10, "got": 6, "gap": 4}]},
                "b": {"cancelled_at": "c"},
                "c": {"done_at": "d", "gaps": []}}}
short = sr._shortfall(X1)
rows = {r["id"]: r for r in short["rows"]}
print("— недобор доп. заявки: не выдали + отменённый район")
eq("gin: 4 не выдали (a) + 10 отменили (b) = 14", (rows["gin"]["gap"], rows["gin"]["by_district"]), (14, {"a": 4, "b": 10}))
eq("rum: 5 отменили (b), причина cancelled", (rows["rum"]["gap"], rows["rum"]["why"]), (5, "cancelled"))
eq("всего 19", short["qty"], 19)

print("— вычет заказанного на DEF: gin b:10 уже там, rum нет")
X2 = {"supply_id": "X2", "kind": "extra", "base": "DEF", "status": "open", "at": "t2",
      "items": [{"id": "gin", "by_district": {"b": 10}}], "tasks": {"b": {}}}
sr._cover(short, [X2])
g = rows["gin"]
eq("gin covered 10 (b), left 4 (a)", (g["covered"], g["covered_by"], g["left_by"], g["left"]), (10, {"b": 10}, {"a": 4}, 4))
eq("gin bases = [DEF]", g["bases"], ["DEF"])
eq("rum left 5", rows["rum"]["left"], 5)
eq("qty_left = 9, children = [DEF 10]", (short["qty_left"], [(k["base"], k["qty"]) for k in short["children"]]), (9, [("DEF", 10)]))

print("— отменённая дочерняя заявка и отменённый район в ней не покрывают")
short = sr._shortfall(X1)
X3 = {"supply_id": "X3", "base": "GHI", "status": "cancelled", "at": "t3",
      "items": [{"id": "gin", "by_district": {"a": 4}}], "tasks": {"a": {}}}
X4 = {"supply_id": "X4", "base": "JKL", "status": "open", "at": "t4",
      "items": [{"id": "rum", "by_district": {"b": 5}}], "tasks": {"b": {"cancelled_at": "x"}}}
sr._cover(short, [X3, X4])
rows = {r["id"]: r for r in short["rows"]}
eq("ничего не покрыто", (rows["gin"]["left"], rows["rum"]["left"], short["qty_left"]), (14, 5, 19))
eq("детей в списке: JKL с 0 (X3 отменена — мимо)", [(k["base"], k["qty"]) for k in short["children"]], [("JKL", 0)])

print("— основная: отменённый район без принятого + урезанное магазином")
M = {"_id": "S1", "kind": "main", "status": "open",
     "items": [{"id": "gin", "name": "Джин", "asked": 20, "qty": 20, "by_district": {"a": 10, "b": 10}, "got": {"a": 10, "b": 3}}],
     "dropped": [], "short": [{"id": "vod", "name": "Водка", "asked": 12, "qty": 6, "gap": 6, "by_district": {"a": 6}}],
     "tasks": {"a": {"done_at": "d", "gaps": []}, "b": {"cancelled_at": "c"}}}
short = sr._shortfall(M); rows = {r["id"]: r for r in short["rows"]}
eq("gin b: 10 − 3 принятых = 7, причина cancelled", (rows["gin"]["gap"], rows["gin"]["why"]), (7, "cancelled"))
eq("vod 6 урезал", (rows["vod"]["gap"], rows["vod"]["why"]), (6, "short"))
print()
print("FAILED:", fails) if fails else print("ALL OK — недобор и цепочка баз")
sys.exit(1 if fails else 0)
