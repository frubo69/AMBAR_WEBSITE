"""Список заказов читается целиком, без потолка (владелец, 13 ноя 2026: «ну
сначала почини потолок»).

db.get_all_orders и db.orders_from читали `to_list(length=2000)` без
сортировки. Число стояло с переезда на Mongo 24 мар 2026, когда заказов были
десятки; 13 ноя 2026 их стало 1927. На 2001-м самые свежие заказы молча выпали
бы из очереди планшета, бота оператора и экранов STAR: выборка отдаёт заказы в
порядке записи, и обрезается хвост — то есть новые.

Проверяем на 2100 заказах: приходят все, самый свежий на месте, фильтр по
району и по тесту считает столько же, сколько лежит в базе; окно orders_from
отдаёт всё окно и все незакрытые заказы старше него.

    python3 tools/test_orders_cap.py
"""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db                                                          # noqa: E402
FAIL = []
N = 2100
T0 = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def ts(i):
    return (T0 + timedelta(hours=i)).isoformat()


def _настоящий_потолок(курсор):
    """mongomock отдаёт to_list(length=N) целиком, сколько бы ни лежало; живой
    драйвер режет ровно на N. Без этой подмены тест проходил бы и на старом
    коде с length=2000 — учим мок резать так же, как режет настоящая база."""
    cls = type(курсор)
    if getattr(cls, "_ambar_capped", False):
        return
    было = cls.to_list

    async def to_list(self, length=None, *a, **k):
        строки = await было(self, length, *a, **k)
        return строки if length is None else строки[:length]

    cls.to_list = to_list
    cls._ambar_capped = True


async def main():
    db._db = AsyncMongoMockClient()["orders_cap"]
    _настоящий_потолок(db._db.orders.find({}))
    docs = []
    for i in range(N):
        docs.append({"order_id": f"AMB{i:05d}", "timestamp": ts(i), "updated_at": ts(i),
                     "office_id": "jvc" if i % 2 else "bbay",
                     # три самых старых так и висят незакрытыми
                     "status": "pending" if i < 3 else "delivered",
                     "customer_id": 1000 + i, "total": 100, "items": []})
    # тест-заказы лежат в той же коллекции и в настоящий список не идут
    docs += [{"order_id": f"AMBT{i:04d}", "timestamp": ts(N + i), "office_id": "jvc",
              "status": "delivered", "test": True, "items": []} for i in range(5)]
    await db._db.orders.insert_many(docs)

    db._orders_dirty()
    все = await db.get_all_orders()
    eq("все настоящие заказы на месте", len(все), N)
    eq("самый свежий заказ не обрезан", f"AMB{N - 1:05d}" in все, True)
    eq("самый старый тоже", "AMB00000" in все, True)
    eq("тест-заказов в настоящем списке нет", any(o.get("test") for o in все.values()), False)

    eq("по одному району — все его заказы", len(await db.get_all_orders("jvc")), N // 2)
    eq("по двум районам — все", len(await db.get_all_orders(["jvc", "bbay"])), N)
    eq("тестовый список — только тест", len(await db.get_all_orders(test=True)), 5)

    db._orders_dirty()
    окно = await db.orders_from(ts(0))
    eq("окно на всю историю — все заказы", len(окно), N)
    db._orders_dirty()
    хвост = await db.orders_from(ts(N - 10))
    eq("короткое окно: десять свежих и три незакрытых любого возраста", len(хвост), 13)
    eq("в коротком окне есть самый свежий", f"AMB{N - 1:05d}" in хвост, True)
    eq("и незакрытый из самых старых", "AMB00000" in хвост, True)

    print("\nALL PASS" if not FAIL else f"\nFAILED: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
