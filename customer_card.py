"""Customer-facing order status card.

One live message per order, edited in place through the lifecycle
(pending → approved → delivered / declined / cancelled) and on any
operator edit (items added / removed / qty changed). Items + total
are always shown so the customer sees the current state; detailed
tracking lives in the mini app.
"""
from __future__ import annotations


def _items_block(order: dict) -> str:
    items = order.get("items", [])
    if not items:
        return ""
    return "\n".join(f"  • {i.get('name','?')} ×{i.get('qty',0)}" for i in items)


def render_customer_card(order: dict, lang: str = "ru") -> str:
    oid    = order.get("order_id", "?")
    status = order.get("status", "pending")
    total  = order.get("total", 0)
    eta    = order.get("eta", 0)
    items  = _items_block(order)

    if lang == "ru":
        if status == "pending":
            head   = f"⏳ *Заказ #{oid} оформлен*\n_Ожидает подтверждения оператора_"
            footer = "🍾 Вы можете отслеживать статус Вашего заказа в приложении"
        elif status == "approved":
            eta_line = f"доставка через ~{eta} мин" if eta else "скоро выедем"
            head   = f"✅ *Заказ #{oid} подтверждён*\n_{eta_line}_"
            footer = "🍾 Вы можете отслеживать статус Вашего заказа в приложении"
        elif status == "delivered":
            head   = f"✅ *Заказ #{oid} доставлен!*"
            footer = "_Спасибо! Оцените нас в приложении_ 🥂"
        elif status == "declined":
            head   = f"❌ *Заказ #{oid} отменён*"
            footer = ""
        elif status == "cancelled":
            head   = f"🚫 *Заказ #{oid} отменён*"
            footer = ""
        else:
            head, footer = f"*Заказ #{oid}*", ""
        items_label = "🛒 *Позиции:*"
        total_label = "💰 *Итого:*"
    else:
        if status == "pending":
            head   = f"⏳ *Order #{oid} placed*\n_Awaiting operator confirmation_"
            footer = "🍾 You can track your order status in the app"
        elif status == "approved":
            eta_line = f"delivery in ~{eta} min" if eta else "on the way soon"
            head   = f"✅ *Order #{oid} confirmed*\n_{eta_line}_"
            footer = "🍾 You can track your order status in the app"
        elif status == "delivered":
            head   = f"✅ *Order #{oid} delivered!*"
            footer = "_Thank you! Rate us in the app_ 🥂"
        elif status == "declined":
            head   = f"❌ *Order #{oid} cancelled*"
            footer = ""
        elif status == "cancelled":
            head   = f"🚫 *Order #{oid} cancelled*"
            footer = ""
        else:
            head, footer = f"*Order #{oid}*", ""
        items_label = "🛒 *Items:*"
        total_label = "💰 *Total:*"

    parts = [head]
    if items:
        parts.append(f"{items_label}\n{items}")
    parts.append(f"{total_label} {total} AED")
    if footer:
        parts.append(footer)
    return "\n\n".join(parts)
