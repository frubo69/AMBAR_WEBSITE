"""
AMBAR — люди: кто на каком районе, кто старший и кто возит.

Одна таблица на всё приложение. Из неё живут:
  • POS (operator_routes.DISTRICTS) — выбор района и водителя в ручном заказе;
  • страница «Операторы» в ambar star — статистика по каждому.

Как устроена работа
-------------------
Заказ всегда привязан к району (office_id), а район — к своему оператору. Своего
входа в телеграм у районных операторов нет: входящие заказы принимает старший
оператор со своего устройства. Отсюда правило разнесения:

  • время реакции (успел ли принять заказ)  → тому, чьё устройство приняло,
    то есть старшему. Только у него есть telegram_id, и только по нему такое
    измерение вообще имеет смысл;
  • всё остальное — заказы, выручка, время доставки, оценки, чаевые → району
    и, значит, его оператору и водителям.

Поэтому у старшего в карточке нет чаевых: бутылки возят не он.
"""

# Старшие операторы: свой вход в бот и в POS, работают по всем районам.
SENIOR_OPERATORS = [
    {"id": "parviz", "name": "Парвиз", "telegram_id": 1567466073},
]

# Общие устройства — не люди. С планшета операторы заводят телефонные заказы,
# поэтому все они приходят под одним telegram_id. Кто именно оформил, видно по
# району заказа, а не по устройству, и время реакции здесь бессмысленно: заказ
# создаётся уже принятым. Список нужен, чтобы такой id не выглядел чужим.
DEVICES = [
    {"id": "tablet", "name": "Планшет операторов",
     "telegram_id": 8854333070, "username": "AMBAR_PIANSHET"},
]
DEVICE_BY_TG = {d["telegram_id"]: d for d in DEVICES}

# Районы и их люди. Оператор может вести несколько районов.
DISTRICT_STAFF = [
    {"district": "jvc",     "operator": "Умар",      "drivers": ["Худоба", "Фарух"]},
    {"district": "tecom",   "operator": "Умар",      "drivers": ["Файзуло", "Алишер"]},
    {"district": "bbay",    "operator": "Джанабиль", "drivers": ["Парвиз", "Авазбек", "Бахадыр"]},
    {"district": "silicon", "operator": "Фарух",     "drivers": ["Фаредун", "Азиз"]},
    {"district": "alguses", "operator": "Фарух",     "drivers": ["Сунат", "Даврон"]},
]

# ── расходы на питание ───────────────────────────────────────────────────────
# Платят каждый день и всем, но по-разному: вышел на смену — одна ставка,
# не вышел — другая. Поэтому «кто сегодня работает» приходится отмечать
# руками, и пока не отмечено, начислять нечего: 80 и 40 — разные деньги.
MEAL_WORKING = 80
MEAL_OFF = 40


def drivers() -> list:
    """Все водители с их районом и оператором, в порядке районов B1…B5."""
    from config_offices import OFFICE_CODES, OFFICE_NAMES
    out, seen = [], set()
    for st in DISTRICT_STAFF:
        for name in st["drivers"]:
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "id": _slug(name), "name": name,
                "district": st["district"],
                "district_code": OFFICE_CODES.get(st["district"], ""),
                "district_name": OFFICE_NAMES.get(st["district"], st["district"]),
                "operator": st["operator"],
            })
    return out


# ── производное ──────────────────────────────────────────────────────────────
DISTRICT_OPERATOR = {s["district"]: s["operator"] for s in DISTRICT_STAFF}
DISTRICT_DRIVERS = {s["district"]: list(s["drivers"]) for s in DISTRICT_STAFF}

SENIOR_BY_TG = {s["telegram_id"]: s for s in SENIOR_OPERATORS}
SENIOR_IDS = tuple(s["telegram_id"] for s in SENIOR_OPERATORS)


def _slug(name: str) -> str:
    """Стабильный id оператора из имени: он попадает в ссылки и data-атрибуты."""
    tbl = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
           "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
           "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
           "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
           "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    return "".join(tbl.get(ch, ch) for ch in (name or "").lower()) or "op"


def operators() -> list:
    """Все операторы: сперва старшие, потом районные, каждый один раз.

    scope — районы, за которые человек отвечает. У старшего это все районы:
    он принимает заказ откуда угодно."""
    from config_offices import OFFICE_IDS

    out = []
    for s in SENIOR_OPERATORS:
        out.append({"id": s["id"], "name": s["name"], "senior": True,
                    "telegram_id": s["telegram_id"],
                    "districts": list(OFFICE_IDS), "drivers": []})
    seen = {}
    for s in DISTRICT_STAFF:
        name = s["operator"]
        if name not in seen:
            seen[name] = {"id": _slug(name), "name": name, "senior": False,
                          "telegram_id": None, "districts": [], "drivers": []}
            out.append(seen[name])
        seen[name]["districts"].append(s["district"])
        for d in s["drivers"]:
            if d not in seen[name]["drivers"]:
                seen[name]["drivers"].append(d)
    return out
