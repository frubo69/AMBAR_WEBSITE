"""Подарок за сумму заказа.

Отдельным файлом, потому что это живая настройка маркетинга: порог и позиция
будут меняться, а трогать ради этого логику заказа не нужно.

Подарок добавляет СЕРВЕР — и в заказ из приложения, и в телефонный. Клиент
присылает только состав корзины: если бы подарок приходил с фронтенда, любой
POST мог бы положить в заказ бесплатную бутылку без всякой тысячи.
"""

# Порог считается по товарам, без чаевых: чаевые — это водителю, а не покупка.
MIN_AED = 1000

# Что дарим. id из catalog.json — по нему берётся название и списывается склад.
ITEM_ID = "p105"                       # Jacob Creek Shiraz 0.75

NAME = {"ru": "Вино в подарок", "en": "Wine on the house"}
# Сколько не хватает до подарка — это и есть вся механика: без этой строки
# акция не работает, человек просто не знает, что до неё осталось чуть-чуть.
HINT = {"ru": "до вина в подарок", "en": "to a free bottle of wine"}


def qualifies(subtotal_aed) -> bool:
    try:
        return float(subtotal_aed or 0) >= MIN_AED
    except (TypeError, ValueError):
        return False


def gift_line(catalog_by_id: dict) -> dict | None:
    """Строка подарка для состава заказа.

    Возвращает None, если позиции нет в каталоге или её нет в наличии: обещать
    подарок, которого нет на полке, хуже, чем не обещать вовсе."""
    p = (catalog_by_id or {}).get(ITEM_ID)
    if not p or not p.get("stock"):
        return None
    return {"id": ITEM_ID, "name": p.get("name", ""), "qty": 1,
            "price": 0, "line_total": 0, "gift": True}


def apply(items: list, subtotal_aed, catalog_by_id: dict) -> list:
    """Добавить подарок в состав, если заказ дотянул до порога.

    Если такая позиция уже куплена, подарок всё равно отдельной строкой: иначе
    в заказе будет две бутылки по цене двух, а подарка человек не увидит."""
    items = list(items or [])
    if not qualifies(subtotal_aed):
        return items
    if any(i.get("gift") for i in items):
        return items
    line = gift_line(catalog_by_id)
    return items + [line] if line else items
