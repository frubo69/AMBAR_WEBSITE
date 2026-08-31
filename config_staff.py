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

# ── доступы водителей ────────────────────────────────────────────────────────
# Водитель заходит в своё приложение под собственным телеграмом, поэтому имени
# мало — нужен id. Живут они в окружении, а не здесь: список меняется с людьми,
# а не с кодом, и держать его в git значит выкатывать релиз ради нового водителя.
#
#   AMBAR_DRIVER_IDS="Худоба:123456789,Фарух:987654321"
#
# Пока водителя нет в списке, приложение его не пустит — это и есть выдача
# доступа: вписали id, человек вошёл.
import os as _os


# ── как называть человека в учёте ──────────────────────────────────────────
# Имя в телеграме — личное дело человека, и меняться оно может по любому
# поводу. В подписях правок, в письмах бота и в истории нужно другое: как его
# зовут в работе. Отсюда эта таблица — id из тех, что и так лежат в коде, и
# рабочее имя.
DISPLAY_NAMES = {
    686932322: "fixxxik",
}


def display_name(telegram_id, fallback: str = "") -> str:
    """Рабочее имя человека; нет в таблице — то, что дали, иначе прочерк."""
    try:
        имя = DISPLAY_NAMES.get(int(telegram_id or 0))
    except (TypeError, ValueError):
        имя = None
    return имя or (fallback or "").strip() or "—"


def _parse_driver_ids(raw: str) -> dict:
    out = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, _, tid = part.rpartition(":")
        name, tid = name.strip(), tid.strip()
        if name and tid.isdigit():
            out[name] = int(tid)
    return out


DRIVER_IDS = _parse_driver_ids(_os.getenv("AMBAR_DRIVER_IDS", ""))
DRIVER_BY_TG = {v: k for k, v in DRIVER_IDS.items()}


# ── доступы операторов ──────────────────────────────────────────────────────
# У районного оператора своего входа в телеграм долго не было: заказы принимал
# старший со своего устройства, и «оператор района» существовал только как
# подпись. Как только у него появляется id, он становится адресатом: заказ
# своего района приходит ему, а не всем сразу.
#
#   AMBAR_OPERATOR_IDS="Умар:123456789,Джанабиль:987654321"
#
# Пусто — работает как раньше: всё уходит старшему и на планшет.
OPERATOR_IDS_BY_NAME = _parse_driver_ids(_os.getenv("AMBAR_OPERATOR_IDS", ""))


def operator_tg(name: str) -> int:
    """Телеграм районного оператора; 0 — своего входа у него нет."""
    return int(OPERATOR_IDS_BY_NAME.get((name or "").strip()) or 0)


def operator_by_tg(telegram_id) -> str:
    """Кто это по id: районный оператор или пусто."""
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return ""
    for имя, i in OPERATOR_IDS_BY_NAME.items():
        if int(i) == tid:
            return имя
    for s in SENIOR_OPERATORS:
        if int(s.get("telegram_id") or 0) == tid:
            return s["name"]
    return ""


def driver_by_tg(telegram_id) -> dict | None:
    """Кто это, если он вообще водитель. Возвращает запись из drivers()."""
    name = DRIVER_BY_TG.get(int(telegram_id or 0))
    if not name:
        return None
    return next((d for d in drivers() if d["name"] == name), None)


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
                "telegram_id": DRIVER_IDS.get(name),
            })
    return out


# ── перестановки ─────────────────────────────────────────────────────────────
# Кто на каком районе меняется чаще, чем выходит релиз: отпуск, новый человек,
# поменялись сменами. Поэтому список выше — то, как задумано, а поверх него
# ложится перестановка из базы. Её всегда видно и всегда можно снять.
#
# Переставляются и операторы, и водители: водитель уходит в отпуск или его
# перебрасывают на соседний район так же часто. Разница в том, что оператор у
# района один, а водителей несколько, поэтому их перестановка хранится в другую
# сторону — «этот водитель теперь здесь».
_BASE_OPERATOR = {s["district"]: s["operator"] for s in DISTRICT_STAFF}
_BASE_DRIVERS = {s["district"]: list(s["drivers"]) for s in DISTRICT_STAFF}
_BASE_DRIVER_AT = {n: s["district"] for s in DISTRICT_STAFF for n in s["drivers"]}


def apply_moves(moves: dict, driver_moves: dict = None):
    """Наложить перестановку. Пустые словари возвращают всё как в коде."""
    moves = moves or {}
    for s in DISTRICT_STAFF:
        s["operator"] = moves.get(s["district"]) or _BASE_OPERATOR[s["district"]]
    DISTRICT_OPERATOR.clear()
    DISTRICT_OPERATOR.update({s["district"]: s["operator"] for s in DISTRICT_STAFF})

    dm = driver_moves or {}
    at = {n: (dm.get(n) or d) for n, d in _BASE_DRIVER_AT.items()}
    for s in DISTRICT_STAFF:
        # Порядок держим по расписанию: сначала свои, потом пришедшие. Список
        # водителей читают глазами, и прыгающий порядок мешает.
        own = [n for n in _BASE_DRIVERS[s["district"]] if at.get(n) == s["district"]]
        came = [n for n in _BASE_DRIVER_AT if at.get(n) == s["district"] and n not in own]
        s["drivers"] = own + came
    DISTRICT_DRIVERS.clear()
    DISTRICT_DRIVERS.update({s["district"]: list(s["drivers"]) for s in DISTRICT_STAFF})


def base_operator(district: str) -> str:
    """Кто стоит на районе в расписании — чтобы показать, от чего отступили."""
    return _BASE_OPERATOR.get(district, "")


def base_district(driver: str) -> str:
    """Где водитель стоит в расписании."""
    return _BASE_DRIVER_AT.get(driver, "")


def driver_names() -> list:
    """Все водители в порядке районов расписания."""
    return list(_BASE_DRIVER_AT)


def operator_names() -> list:
    """Все, кого можно поставить на район: районные и старшие."""
    seen, out = set(), []
    for n in list(_BASE_OPERATOR.values()) + [s["name"] for s in SENIOR_OPERATORS]:
        if n not in seen:
            seen.add(n); out.append(n)
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
