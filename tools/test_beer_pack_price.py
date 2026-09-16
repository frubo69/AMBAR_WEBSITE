"""Цена пачки пива: 12 штук — цена позиции, 24 — ровно вдвое (владелец,
16 сен 2026: «не 100/195, а 100/200»). Сервер (api_server._catalog_unit_price,
по нему проверяется итог заказа) и цена по источнику у оператора/водителя.
Без базы."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from api_server import _catalog_unit_price as price
from operator_routes import _unit_price_for
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
beer = {"id": "p31", "price": 100, "price_12": 100, "price_24": 200, "price_full": 100, "price_12_full": 100, "price_24_full": 200}
eq("12 шт = 100", price(beer, 12), 100.0)
eq("24 шт = 200, не 195", price(beer, 24), 200.0)
eq("без pcs — цена позиции", price(beer, None), 100.0)
old = {"id": "x", "cat": "Пиво", "price": 150}               # старая запись пива без полей пачек
eq("нет price_24 → ровно вдвое, без «минус пять»", price(old, 24), 300.0)
eq("нет price_12 → цена позиции", price(old, 12), 150.0)
own = {"id": "y", "cat": "Пиво", "price": 100, "price_12": 100, "price_24": 210}   # владелец задал 24 сам
eq("price_24 из каталога главнее формулы", price(own, 24), 210.0)
spirit = {"id": "p1", "price": 100, "price_full": 100}
eq("крепкое: pcs не трогает цену", (price(spirit, 24), price(spirit, None)), (100.0, 100.0))
eq("источник app → цена приложения", _unit_price_for("app", beer, 24), 200.0)
eq("источник manual → прайс (те же 200)", _unit_price_for("manual", beer, 24), 200.0)
print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
