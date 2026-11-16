"""Бот оператора, кнопка «Доставлен»: заказ закрывается с полкой водителя и
моментом доставки (владелец, 16 ноя 2026, на «Начать чинить уже сработавшие
ошибки по списку?»: «Да, чини»).

Что было. Обработчик «Доставлен» звал `_staff_mod.sync()` и
`_staff_mod.stock_office(...)`, а сам модуль в файл подключён не был: имя не
существовало, NameError глотался тем же except («реестр не прочитан»), и заказ
закрывался без stock_office. Не ставился и delivered_at. Склад тогда списывает
с района АДРЕСА заказа, а не с полки водителя, и берёт моментом время заказа:
6 ноя 2026, AMB75414C0C — три вина ушли с полки B3, списались с B4 (недостача
B3, излишек B4); заказ, принятый до пересчёта района и закрытый после него,
не списывался вовсе.

Гоняем настоящий обработчик кнопки `operator_bot.cb` на копии базы; телеграм
и карточки заглушены.

    python3 tools/test_bot_done_shelf.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as SR, config_staff as staff               # noqa: E402
import operator_bot as ob                                          # noqa: E402
import owner_routes                                                # noqa: E402

U = timezone.utc
DAY = "2026-11-16"
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


class Q:
    def __init__(self, data): self.data, self.shown = data, []
    async def answer(self, *a, **k): return None
    async def edit_message_text(self, text, **k): self.shown.append(text)


class Upd:
    def __init__(self, data):
        self.callback_query = Q(data)
        self.effective_user = type("U", (), {"id": 777001})()


class Ctx:
    user_data = {}
    bot = None


async def press(data):
    u = Upd(data)
    await ob.cb(u, Ctx())
    return u.callback_query.shown


async def stock(o, pid):
    SR.base_drop()
    return float((((await SR._district_base(DAY)).get(o) or {}).get("have_exact") or {}).get(pid, 0))


async def main():
    db._db = AsyncMongoMockClient()["ambar_bot_done"]
    SR._biz_day = lambda *a, **k: DAY
    async def _none(*a, **k): return None
    async def _list(*a, **k): return []
    async def _card(*a, **k): return "карточка"
    ob.update_customer_card = _none
    ob.order_card = _card
    ob.kb_order_actions = _none
    owner_routes.notify_owners = _list
    eq("модуль реестра подключён в боте (было: имени не существовало)", ob._staff_mod is staff, True)

    # Водитель стоит на B3 Silicon (реестр в базе), заказ записан на адрес в B4.
    await db._db.drivers.insert_one({"name": "Тест-водитель 3", "district": "silicon", "telegram_id": 0})
    час_назад = datetime.now(U) - timedelta(hours=1)
    for o in ("silicon", "alguses"):
        await db.save_stock_count(o, "2026-11-10", {
            "district": o, "day": "2026-11-10", "counted_by": 0,
            "counted_at": (datetime.now(U) - timedelta(days=5)).isoformat(),
            "lines": [{"id": "p1", "name": "Absolut 1 ltr", "price": 100, "unit": 1, "actual": 5, "counted": True}]})
    await db._db.orders.insert_one({
        "order_id": "AMBTEST1", "status": "approved", "office_id": "alguses", "driver": "Тест-водитель 3",
        "customer_id": 555001, "customer_name": "Клиент", "total": 300, "payment_method": "cash",
        "timestamp": час_назад.isoformat().replace("+00:00", ""),
        "items": [{"id": "p1", "qty": 3, "name": "Absolut 1 ltr", "price": 100}]})
    eq("до доставки: на обоих районах по 5", (await stock("silicon", "p1"), await stock("alguses", "p1")), (5.0, 5.0))

    до = datetime.now(U)
    показано = await press("done_AMBTEST1_555001")
    o = await db.get_order("AMBTEST1")
    eq("заказ доставлен, оператору показано", (o["status"], bool(показано) and "Доставлен" in показано[-1]), ("delivered", True))
    eq("полка — район водителя, а не адреса (было: поля не было)", o.get("stock_office"), "silicon")
    dl = SR._dt_of(o.get("delivered_at") or "")
    eq("момент доставки записан (было: не ставился)", bool(dl) and до - timedelta(seconds=2) <= dl <= datetime.now(U), True)
    eq("склад: списалось с полки водителя B3, B4 не тронут (было: 5 и 2)",
       (await stock("silicon", "p1"), await stock("alguses", "p1")), (2.0, 5.0))

    # Заказ принят ДО пересчёта района, закрыт кнопкой ПОСЛЕ него.
    await db._db.orders.insert_one({
        "order_id": "AMBTEST2", "status": "approved", "office_id": "silicon", "driver": "Тест-водитель 3",
        "customer_id": 555002, "customer_name": "Клиент", "total": 100, "payment_method": "cash",
        "timestamp": (datetime.now(U) - timedelta(hours=2)).isoformat().replace("+00:00", ""),
        "items": [{"id": "p1", "qty": 1, "name": "Absolut 1 ltr", "price": 100}]})
    await db.save_stock_count("silicon", "2026-11-16", {
        "district": "silicon", "day": "2026-11-16", "counted_by": 0,
        "counted_at": datetime.now(U).isoformat(),
        "lines": [{"id": "p1", "name": "Absolut 1 ltr", "price": 100, "unit": 1, "actual": 2, "counted": True}]})
    eq("пересчёт района: 2 (бутылка заказа ещё на полке)", await stock("silicon", "p1"), 2.0)
    await asyncio.sleep(0.02)
    await press("done_AMBTEST2_555002")
    eq("закрыли кнопкой после пересчёта: списалась (было: оставалась 2 — момент продажи был раньше снимка)",
       await stock("silicon", "p1"), 1.0)

    # Водителя нет в реестре — полка остаётся районом заказа, как и было.
    await db._db.orders.insert_one({
        "order_id": "AMBTEST3", "status": "approved", "office_id": "alguses", "driver": "Кто-то",
        "customer_id": 555003, "customer_name": "Клиент", "total": 100, "payment_method": "cash",
        "timestamp": datetime.now(U).isoformat().replace("+00:00", ""),
        "items": [{"id": "p1", "qty": 1, "name": "Absolut 1 ltr", "price": 100}]})
    await press("done_AMBTEST3_555003")
    eq("водитель не из реестра — район заказа", (await db.get_order("AMBTEST3")).get("stock_office"), "alguses")

    print("\n" + ("ПРОВАЛЕНО: " + "; ".join(FAIL) if FAIL else "Все проверки прошли."))
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
