"""Клиент-призрак с telegram_id 0 (владелец, 24 сен 2026: «это что такое» —
про карточку «e2e» с 755 заказами и 8 645 потраченными).

Под нулевым id в заказах лежат ВСЕ телефонные заказы: у них клиента в боте
нет. Запись пользователя с тем же нулём однажды оставил сквозной тест
(tools/e2e.py), и карточка собрала под себя чужую жизнь — 755 заказов,
которых у этого «человека» никогда не было.

Клиент — это человек с телеграм-аккаунтом. Запись без него в список и в
счётчик не попадает, даже если кто-то снова её заведёт.

    python3 tools/test_customer_ghost.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone                        # noqa: E402
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db                                                       # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def main():
    db._db = AsyncMongoMockClient()["ambar_ghost"]
    at = datetime.now(timezone.utc)
    await db._db.users.insert_many([
        {"telegram_id": 111, "full_name": "Живой клиент", "first_seen": at, "total_spent": 500},
        {"telegram_id": 222, "full_name": "Ещё живой", "first_seen": at, "total_spent": 300},
        {"telegram_id": 0, "full_name": "e2e", "first_seen": at, "total_spent": 8645},
        {"full_name": "без поля вовсе", "first_seen": at},
        {"telegram_id": None, "full_name": "с пустым полем", "first_seen": at}])

    сп = await db.get_all_customers()
    eq("в списке только люди с телеграмом", sorted(u["full_name"] for u in сп),
       ["Ещё живой", "Живой клиент"])
    eq("счётчик базы считает их же", await db.customers_count(), 2)
    eq("призрак из базы не удалён — просто не показывается",
       await db._db.users.count_documents({}), 5)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
