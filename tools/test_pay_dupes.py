"""Одно имя на двоих в зарплатной ведомости (владелец, 23 сен 2026: «где
водитель Парвиз? не старший оператор, а водитель»).

У нас Парвиз — и старший оператор, и водитель на Бизнес Бей; Фарух — и
оператор, и водитель на JVC. Это разные люди (разные телеграм-аккаунты), но в
ведомости человек — это его имя: под именем лежат оклад, штрафы и авансы.
Второй с тем же именем в неё не помещался и исчезал молча — то есть оставался
без зарплаты. Теперь он не исчезает молча: попадает в dupes, и STAR говорит о
нём красным.

Без базы: настоящий finance_routes._people, расписание — заглушка.

    python3 tools/test_pay_dupes.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
import config_staff as staff                                   # noqa: E402
import finance_routes as fr                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

staff.SENIOR_OPERATORS = [{"name": "Парвиз"}]
staff.operators = lambda: [{"name": "Парвиз", "senior": True, "districts": ["bbay"]},
                           {"name": "Умар", "districts": ["tecom"]},
                           {"name": "Фарух", "districts": ["jvc"]}]
staff.drivers = lambda: [{"name": "Худоба", "district": "jvc"},
                         {"name": "Фарух", "district": "jvc"},        # тёзка оператора
                         {"name": "Парвиз", "district": "bbay"},      # тёзка старшего
                         {"name": "Авазбек", "district": "bbay"}]


def main():
    dupes: list = []
    люди = fr._people([], dupes)
    имена = [(p["name"], p["role"]) for p in люди]
    eq("кто попал в ведомость", имена,
       [("Парвиз", "senior"), ("Умар", "operator"), ("Фарух", "operator"),
        ("Худоба", "driver"), ("Авазбек", "driver")])
    eq("водителей-тёзок в ведомости нет — их и не было",
       [n for n, r in имена if r == "driver"], ["Худоба", "Авазбек"])
    eq("но теперь они названы поимённо", sorted(x["name"] for x in dupes), ["Парвиз", "Фарух"])
    eq("сказано, кто остался и кто пропал",
       sorted((x["name"], x["kept"], x["lost"]) for x in dupes),
       [("Парвиз", "senior", "driver"), ("Фарух", "operator", "driver")])
    eq("и то же по-русски, для экрана",
       sorted((x["kept_ru"], x["lost_ru"]) for x in dupes),
       [("оператор", "водитель"), ("старший оператор", "водитель")])

    # Имена не совпадают — сообщать не о чем.
    staff.drivers = lambda: [{"name": "Худоба", "district": "jvc"}]
    чисто: list = []
    fr._people([], чисто)
    eq("тёзок нет — и строки нет", чисто, [])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(main())
