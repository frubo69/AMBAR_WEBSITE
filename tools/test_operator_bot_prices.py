"""Бот операторов берёт цены из catalog.json, а не из встроенного списка
(владелец, 16 сен 2026: «во всех ботах тоже подтяни цены»). Пиво 24 = p24 из
каталога, без него ровно вдвое. Без базы и без телеграма."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import operator_bot as ob
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
cat = {c["id"]: c for c in json.load(open(os.path.join(os.path.dirname(ob.__file__), "catalog.json")))}
items = ob._products(); by = {p["id"]: p for p in items}
eq("позиций как в каталоге без сигарет", len(items), sum(1 for c in cat.values() if c.get("cat") != "Сигареты"))
eq("сигарет в боте нет", any(p["cat"] == "Сигареты" for p in items), False)
eq("Absolut — цена прайса, не 95", by["p1"]["price"], cat["p1"]["price_full"])
eq("Heineken банка: p12/p24 из каталога", (by["p31"]["p12"], by["p31"]["p24"]), (cat["p31"]["price_12_full"], cat["p31"]["price_24_full"]))
eq("пачка 24 = ровно вдвое, не «минус 5»", ob.beer_pack_price(by["p31"], "24"), 2 * ob.beer_pack_price(by["p31"], "12"))
eq("у пива нет ключа price — recalc_order не трогает строки пачек", "price" in by["p31"], False)
eq("без p24 — вдвое", ob.beer_pack_price({"p12": 150}, "24"), 300)
eq("категории кнопок все есть в каталоге", [c for c, _ in ob.CATEGORY_ORDER if not any(p["cat"] == c for p in items)], [])
kb = ob.kb_beer_pack("o1", "p31"); texts = [b.text for row in kb.inline_keyboard for b in row]
eq("кнопки пачек: 100 и 200", texts[:2], [f"📦 ×12  —  {by['p31']['p12']} AED", f"📦 ×24  —  {by['p31']['p24']} AED"])
ob._products_cache["items"] = []; ob._products_cache["mtime"] = 0.0
eq("перечитывается по mtime", len(ob._products()), len(items))
print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
