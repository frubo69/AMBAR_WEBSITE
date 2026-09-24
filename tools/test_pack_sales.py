"""Пачка пива из бота оператора списывается со склада (ревизия 24 сен 2026).

Бот оператора, добавляя ×12 или ×24, пишет в заказ СВОЙ id — «p31_12»: у
пачки своя цена, и в каталоге такой позиции нет. Склад читал этот id как есть
— и продажа не списывала ничего: «p31_12» на полке нет, а Heineken оставался
нетронутым. Десять таких продаж нашлось с апреля по сентябрь.

Мини-аппа для того же шлёт `pcs` — этот путь работал и должен продолжать.

    python3 tools/test_pack_sales.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
import stock_routes as sr                                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def main():
    cat = sr._catalog()
    пиво = next((pid for pid, p in cat.items() if sr._unit(p) > 1), None)
    бутылка = next((pid for pid, p in cat.items() if sr._unit(p) == 1), None)
    eq("в каталоге есть пиво (коробка) и обычная бутылка", bool(пиво and бутылка), True)
    ед = sr._unit(cat[пиво])
    eq("в коробке пива бутылок", ед, 24)

    eq("пачка ×12 из бота: списывается полкоробки базовой позиции",
       sr.sale_item({"id": f"{пиво}_12", "qty": 1}, cat), (пиво, 12 / ед))
    eq("две пачки ×12 — коробка", sr.sale_item({"id": f"{пиво}_12", "qty": 2}, cat), (пиво, 24 / ед))
    eq("пачка ×24 — целая коробка", sr.sale_item({"id": f"{пиво}_24", "qty": 1}, cat), (пиво, 1.0))
    eq("мини-аппа со своим pcs считается как раньше",
       sr.sale_item({"id": пиво, "qty": 1, "pcs": 12}, cat), (пиво, 12 / ед))
    eq("обычная банка — одна банка", sr.sale_item({"id": пиво, "qty": 1}, cat), (пиво, 1 / ед))
    eq("обычная бутылка — штука", sr.sale_item({"id": бутылка, "qty": 3}, cat), (бутылка, 3.0))

    eq("выдуманный id не превращается в товар",
       sr.sale_item({"id": "p999_12", "qty": 1}, cat), ("p999_12", 1.0))
    eq("id без числа в хвосте не трогаем",
       sr.sale_item({"id": f"{пиво}_x", "qty": 1}, cat), (f"{пиво}_x", 1.0))
    eq("строка без id и без количества ничего не списывает",
       (sr.sale_item({"qty": 1}, cat), sr.sale_item({"id": пиво, "qty": 0}, cat)), (("", 0.0), ("", 0.0)))
    # Если у строки уже есть pcs, хвост id — не второй множитель.
    eq("pcs и хвост одновременно не умножаются дважды",
       sr.sale_item({"id": f"{пиво}_12", "qty": 1, "pcs": 12}, cat), (f"{пиво}_12", 12.0))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(main())
