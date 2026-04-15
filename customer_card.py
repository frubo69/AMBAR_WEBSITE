"""Customer-facing order status card.

One live message per order, edited in place through the lifecycle
(pending → approved → delivered / declined / cancelled). All detail
lives in the mini app; this message is intentionally minimalistic.
"""
from __future__ import annotations


def render_customer_card(order: dict, lang: str = "ru") -> str:
    oid    = order.get("order_id", "?")
    status = order.get("status", "pending")
    eta    = order.get("eta", 0)

    if lang == "ru":
        if status == "pending":
            return (f"⏳ *Заказ #{oid} оформлен*\n"
                    f"_Ожидает подтверждения оператора_\n\n"
                    f"🍾 Подробности — в приложении")
        if status == "approved":
            eta_line = f"доставка через ~{eta} мин" if eta else "скоро выедем"
            return (f"✅ *Заказ #{oid} подтверждён*\n"
                    f"_{eta_line}_\n\n"
                    f"🍾 Подробности — в приложении")
        if status == "delivered":
            return (f"✅ *Заказ #{oid} доставлен!*\n"
                    f"_Спасибо! Оцените нас в приложении_ 🥂")
        if status == "declined":
            return f"❌ *Заказ #{oid} отменён.*"
        if status == "cancelled":
            return f"🚫 *Заказ #{oid} отменён.*"
        return f"*Заказ #{oid}*"

    # EN
    if status == "pending":
        return (f"⏳ *Order #{oid} placed*\n"
                f"_Awaiting operator confirmation_\n\n"
                f"🍾 Details — in the app")
    if status == "approved":
        eta_line = f"delivery in ~{eta} min" if eta else "on the way soon"
        return (f"✅ *Order #{oid} confirmed*\n"
                f"_{eta_line}_\n\n"
                f"🍾 Details — in the app")
    if status == "delivered":
        return (f"✅ *Order #{oid} delivered!*\n"
                f"_Thank you! Rate us in the app_ 🥂")
    if status == "declined":
        return f"❌ *Order #{oid} cancelled.*"
    if status == "cancelled":
        return f"🚫 *Order #{oid} cancelled.*"
    return f"*Order #{oid}*"
