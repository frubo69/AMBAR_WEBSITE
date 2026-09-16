"""Правило удержания (владелец, 14 сен 2026): бой, брак, просрочка — по
закупке; утеря и недостача — полная цена по прайсу. Без базы: каталог и
цены закупки подменены."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import stock_routes as sr

CAT = {"abs": {"id": "abs", "name": "Absolut 1 ltr", "price": 95},
       "beer": {"id": "beer", "name": "Heineken", "price_24_full": 240}}
sr._catalog = lambda: CAT
sr._LOSS_CACHE = {"abs": 62, "beer": 120}       # закупка за учётную единицу (ящик у пива)
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

for kind in ("бой", "брак", "просрочка"):
    eq(f"{kind}: 2 бутылки по закупке", sr._comp_of("abs", 2, kind), 124)
    eq(f"{kind}: основание", sr._comp_basis(kind), "закупка")
for kind in ("потеря", "недостача"):
    eq(f"{kind}: 2 бутылки по прайсу", sr._comp_of("abs", 2, kind), 190)
    eq(f"{kind}: основание", sr._comp_basis(kind), "прайс")
# Пиво считается коробками: qty списания — коробки, цена — за коробку (владелец, 16 сен 2026).
eq("пиво, бой: закупка коробки × 3 коробки", sr._comp_of("beer", 3, "бой"), 360)
eq("пиво, утеря: прайс коробки × 3 коробки", sr._comp_of("beer", 3, "потеря"), 720)
eq("полкоробки пива, бой: половина закупки коробки", sr._comp_of("beer", 0.5, "бой"), 60)
eq("цены в прайсе нет → 0", sr._comp_of("nope", 1, "потеря"), 0)
eq("закупки нет → 0", sr._comp_of("nope", 1, "бой"), 0)
row = sr._wo_row({"_id": "w1", "item": "abs", "qty": 1, "kind": "потеря", "at": None}, CAT)
eq("строка утери: due по прайсу", (row["due"], row["due_by"]), (95, "прайс"))
eq("строка утери: loss по закупке остаётся", row["loss"], 62)
row = sr._wo_row({"_id": "w2", "item": "abs", "qty": 1, "kind": "бой", "at": None}, CAT)
eq("строка боя: due по закупке", (row["due"], row["due_by"]), (62, "закупка"))
print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
