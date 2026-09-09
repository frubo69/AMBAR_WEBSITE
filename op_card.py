"""
AMBAR — как выглядит карточка нового заказа у оператора.

Видов два, и оба живые.

«buttons» — как было до 9 сен 2026: пять кнопок прямо под заказом, работа шла
в телеграме. Принять / Отклонить, Редактировать / Геолокация, Клиент. Заказ
можно было провести целиком, не открывая панель.

«app» — заказ текстом и одна кнопка «Открыть в приложении». Телеграм остаётся
уведомлением, а работа идёт в панели: там то же самое — принять, отклонить,
править, карточка клиента, — только рядом со списком и остатками.

Переключается одним словом: CARD ниже или переменная AMBAR_OP_CARD в .env на
сервере. Прежний вид никуда не убран и собран здесь же, поэтому вернуть его —
минута, а не восстановление по памяти.

Кнопка мини-аппа живёт только в личке: в группе телеграм такую кнопку не
примет и отобьёт всё сообщение, а заказ, который не дошёл ни до кого, — это
авария. Поэтому для группового чата тот же вход даётся ссылкой на бота.
"""
import os

BUTTONS = "buttons"      # пять кнопок под заказом
APP = "app"              # заказ и одна кнопка «Открыть в приложении»

CARD = (os.getenv("AMBAR_OP_CARD", "") or APP).strip().lower()

OPERATOR_WEBAPP_URL = os.getenv("OPERATOR_WEBAPP_URL",
                                "https://ambar-delivery.com/operator/")
OPERATOR_BOT_NAME = os.getenv("OPERATOR_BOT_NAME", "")


def _kb_buttons(oid, uid) -> dict:
    """Прежний вид: весь заказ проводится кнопками, не выходя из чата."""
    return {"inline_keyboard": [
        [{"text": "✅ Принять",   "callback_data": f"acc_{oid}_{uid}"},
         {"text": "❌ Отклонить", "callback_data": f"dec_{oid}_{uid}"}],
        [{"text": "✏️ Редактировать", "callback_data": f"edit_{oid}"},
         {"text": "📍 Геолокация",    "callback_data": f"loc_{oid}"}],
        [{"text": "👤 Клиент", "callback_data": f"client_{oid}_{uid}"}],
    ]}


def _kb_app(chat_id) -> dict:
    """Новый вид: одна дверь в панель."""
    try:
        личка = int(chat_id or 0) >= 0
    except (TypeError, ValueError):
        личка = True
    if личка:
        кнопка = {"text": "Открыть в приложении",
                  "web_app": {"url": OPERATOR_WEBAPP_URL}}
    elif OPERATOR_BOT_NAME:
        кнопка = {"text": "Открыть в приложении",
                  "url": f"https://t.me/{OPERATOR_BOT_NAME}"}
    else:
        return {"inline_keyboard": []}
    return {"inline_keyboard": [[кнопка]]}


def order_kb(oid, uid=0, chat_id=None) -> dict:
    """Клавиатура под карточкой нового заказа — та, что выбрана в CARD."""
    if CARD == BUTTONS:
        return _kb_buttons(oid, uid)
    return _kb_app(chat_id)


def order_kb_for(oid, uid=0):
    """То же, но адресно: op_route.send зовёт это на каждый чат маршрута —
    кнопка мини-аппа зависит от того, личка это или группа."""
    return lambda chat_id: order_kb(oid, uid, chat_id)
