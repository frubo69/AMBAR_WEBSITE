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
    {"district": "bbay",    "operator": "Джанабиль", "drivers": ["Парвиз", "Авазбек", "Баха"]},
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

# ── старший в AMBAR STAR ─────────────────────────────────────────────────────
# В панель владельца входят трое: владелец, второй владелец и старший —
# менеджер, не оператор из расписания выше. Его геопозицию владельцы смотрят
# из панели, и о её пропаже им пишет сторож. Кто из троих старший — только
# строкой в .env, тем же видом, что у водителей: AMBAR_SENIOR_STAR_IDS=
# "Имя:telegram_id". Строки нет — старшего нет, и панель ни у кого
# геопозицию не спрашивает.
SENIOR_STAR_IDS = _parse_driver_ids(_os.getenv("AMBAR_SENIOR_STAR_IDS", ""))


def senior_star_by_tg(telegram_id) -> str:
    """Имя старшего по его телеграму в панели; не старший — пусто."""
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return ""
    return next((n for n, t in SENIOR_STAR_IDS.items() if t == tid), "")


# ── устройства ───────────────────────────────────────────────────────────────
# Планшеты и прочее, чью геопозицию смотрят отдельно от людей. Метка и
# аккаунт, с которого устройство транслирует в бот устройств:
# AMBAR_DEVICE_IDS="iPad Star:telegram_id,…". Строки нет — устройств нет.
DEVICE_IDS = _parse_driver_ids(_os.getenv("AMBAR_DEVICE_IDS", ""))


def device_by_tg(telegram_id) -> str:
    """Метка устройства по аккаунту; не устройство — пусто."""
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return ""
    return next((n for n, t in DEVICE_IDS.items() if t == tid), "")


def device_code(label: str) -> str:
    """Короткий код на карте — первое слово метки: «iPad Star» → iPad."""
    return ((label or "").split() or [label or ""])[0][:6]


# ── доступы операторов ──────────────────────────────────────────────────────
# У районного оператора своего входа в телеграм долго не было: заказы принимал
# старший со своего устройства, и «оператор района» существовал только как
# подпись. Как только у него появляется id, он становится адресатом: заказ
# своего района приходит ему, а не всем сразу.
#
#   AMBAR_OPERATOR_IDS="123456789:Умар,987654321:Джанабиль,555000111:Умар"
#
# Ключ — id, а не имя, и это не мелочь: один человек садится за разные
# устройства (у B2 их два), а имя у него одно. Обратный порядок молча терял бы
# второе устройство того же оператора.
#
# Пусто — работает как раньше: всё уходит старшему и на планшет.
def _parse_operator_ids(raw: str) -> dict:
    out = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tid, _, name = part.partition(":")
        tid, name = tid.strip(), name.strip()
        if tid.isdigit() and name:
            out[int(tid)] = name
    return out


OPERATOR_BY_ID = _parse_operator_ids(_os.getenv("AMBAR_OPERATOR_IDS", ""))


def operator_chats(name: str) -> list:
    """Все устройства этого оператора. Пусто — своего входа у него нет."""
    имя = (name or "").strip()
    return [tid for tid, n in OPERATOR_BY_ID.items() if n == имя]


def operator_tg(name: str) -> int:
    """Первое устройство оператора — там, где нужен один адресат."""
    ч = operator_chats(name)
    return ч[0] if ч else 0


def operator_by_tg(telegram_id) -> str:
    """Кто это по id: районный оператор, старший или пусто."""
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return ""
    if tid in OPERATOR_BY_ID:
        return OPERATOR_BY_ID[tid]
    for s in SENIOR_OPERATORS:
        if int(s.get("telegram_id") or 0) == tid:
            return s["name"]
    return ""


def driver_by_tg(telegram_id) -> dict | None:
    """Кто это, если он вообще водитель. Возвращает запись из drivers()."""
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return None
    name = DRIVER_BY_TG.get(tid)
    if not name:
        # Тест-водитель, привязанный через базу, — та же персона, что и по
        # списку AMBAR_TEST_DRIVER_IDS.
        return test_driver(tid, force=True) if tid in _ROSTER["test"] else None
    return next((d for d in drivers() if d["name"] == name), None)


# ── тест-водитель ────────────────────────────────────────────────────────────
# Аккаунт из AMBAR_TEST_DRIVER_IDS, которого нет среди настоящих водителей,
# входит в приложение как «Тест-водитель»: видит только тест-заказы, его смены
# и точки не идут ни в деньги, ни сторожу. Район — «Тест-район» (с 17 сен 2026;
# раньше подписывался первым районом расписания); оператор для звонков —
# прежний, первого района.
def test_driver(telegram_id, force: bool = False) -> dict | None:
    """force — аккаунт заодно настоящий водитель, но приложение попросило
    тест-режим переключателем (заголовок X-Ambar-Test); без force боевая
    роль важнее."""
    from config import TEST_DRIVER_IDS, TEST_DRIVER_NAME
    try:
        tid = int(telegram_id or 0)
    except (TypeError, ValueError):
        return None
    if (tid not in TEST_DRIVER_IDS and tid not in _ROSTER["test"]) or (tid in DRIVER_BY_TG and not force):
        return None
    from config_offices import TEST_OFFICE
    st = DISTRICT_STAFF[0]
    # Имя берём из записи реестра: тест-аккаунтов бывает несколько, и у каждого
    # своё имя (18 сен 2026 — второй, старшему оператору). Смены, дни и задачи
    # пишутся по имени, поэтому одно имя на двоих склеило бы их в одного.
    return {"id": "test", "name": _ROSTER["test"].get(tid) or TEST_DRIVER_NAME,
            "district": TEST_OFFICE["id"],
            "district_code": TEST_OFFICE["code"],
            "district_name": TEST_OFFICE["name"],
            "operator": st["operator"], "telegram_id": tid, "test": True}


def driver_or_test(telegram_id) -> dict | None:
    """Настоящий водитель, а если такого нет — тест-водитель (для бота)."""
    return driver_by_tg(telegram_id) or test_driver(telegram_id)


def test_driver_names() -> set:
    """Имена всех тест-водителей: общее из .env и все тест-записи реестра.
    Спрашивать надо набор, а не одно имя: второй тест-аккаунт заведён
    18 сен 2026 старшему оператору, и его смены — тоже тестовые.

    Считаем по записям, а не по привязанным телефонам: заведённый, но ещё не
    привязавшийся тест-водитель — уже тестовое имя, и в боевые списки оно
    попасть не должно ни на минуту."""
    from config import TEST_DRIVER_NAME
    names = {str(r.get("name") or "").strip() for r in _ROSTER["rows"] if r.get("test")}
    return {TEST_DRIVER_NAME} | {n for n in names if n}


def is_test_driver(name: str) -> bool:
    return (name or "").strip() in test_driver_names()


def driver_chats(name: str) -> list:
    """Куда писать водителю по имени: настоящему — его аккаунт, тест-водителю —
    тест-аккаунты (без тех, что заняты настоящими водителями). Пусто — некому."""
    name = (name or "").strip()
    tid = DRIVER_IDS.get(name)
    if tid:
        return [tid]
    if is_test_driver(name):
        # Аккаунты, привязанные именно к этому имени: у каждого тест-водителя
        # своя переписка, чужое тест-сообщение ему не нужно. Общему имени из
        # .env — ещё и AMBAR_TEST_DRIVER_IDS: по этому списку входят без записи
        # в реестре, даже если аккаунт заодно настоящий водитель.
        from config import TEST_DRIVER_IDS, TEST_DRIVER_NAME
        ids = {t for t, n in _ROSTER["test"].items() if n == name}
        if name == TEST_DRIVER_NAME:
            ids |= set(TEST_DRIVER_IDS)
        return sorted(ids)
    return []


# ── расходы на питание ───────────────────────────────────────────────────────
# Платят каждый день и всем, но по-разному: вышел на смену — одна ставка,
# не вышел — другая. Поэтому «кто сегодня работает» приходится отмечать
# руками, и пока не отмечено, начислять нечего: 80 и 40 — разные деньги.
MEAL_WORKING = 80
MEAL_OFF = 40


def meal_of(day_doc: dict | None) -> int:
    """Питание за день по записи дня водителя. Обычно — по отметке оператора:
    вышел (80) или нет (40). Но если водителя отпустили раньше конца смены,
    оператор, отпуская, решает сам — 80 или 40 (владелец, 18 сен 2026), и это
    решение лежит в meal_rate и главнее отметки. Формула одна на всю систему:
    экран водителя, «Финансы», расходы дня и отчёты считают одинаково.
    Урезано старшим за поздний выход (meal_cut, fines_auto) — 40, и отпуск
    раньше конца смены этого не перебивает (владелец, 22 сен 2026)."""
    d = day_doc or {}
    if d.get("meal_cut"):
        return MEAL_OFF
    r = d.get("meal_rate")
    if r in (MEAL_WORKING, MEAL_OFF):
        return int(r)
    w = d.get("working")
    return MEAL_WORKING if w is True else (MEAL_OFF if w is False else 0)


# ── кто сейчас в Дубае ───────────────────────────────────────────────────────
# Владелец, 24 сен 2026: «они улетели — так что они должны отовсюду исчезать,
# пока снова не прилетели… естественно, оставляй их в команде, просто пиши
# серым, что он уехал; но в расходы смены его даже включать не надо».
#
# Кто где — знают периоды работы из «Зарплат» (день отъезда уже не рабочий,
# finance_pay.work_now). Периодов нет — человек на месте: выдумывать отъезд по
# молчанию нельзя. Список живёт рядом с реестром и обновляется тем же sync(),
# поэтому один и тот же ответ у всех служб — панели, ботов и сторожей.
AWAY: dict = {}          # {имя: день отъезда}


def is_away(name: str) -> bool:
    """Улетел и пока не вернулся."""
    return str(name or "") in AWAY


def here(names) -> list:
    """Те же имена без уехавших — для любых рабочих списков."""
    return [n for n in (names or []) if n not in AWAY]


def away_since(name: str) -> str:
    """С какого дня человека нет (день отъезда). Пусто — он здесь."""
    return AWAY.get(str(name or ""), "")


async def sync_away(day: str = ""):
    """Перечитать, кого сейчас нет. Отдельно от реестра: периоды живут в
    «Зарплатах», и читать их каждому потребителю самому — значит развести
    пять разных ответов на один вопрос."""
    import db
    import finance_pay as _pay
    if not day:
        import bizday as _bd
        day = _bd.biz_day()
    out = {}
    for p in await db.fin_people_get():
        имя = str(p.get("_id") or "")
        w = _pay.work_now(p.get("work"), day)
        if имя and w.get("set") and not w.get("on"):
            out[имя] = str(w.get("left") or "")
    AWAY.clear()
    AWAY.update(out)


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
                # Улетел и пока не вернулся: из рабочих списков его убирают, в
                # команде он остаётся — серым, с датой отъезда.
                "away": name in AWAY,
                "away_since": AWAY.get(name, ""),
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

# ── реестр водителей из базы (15 сен 2026) ──────────────────────────────────
# Водитель заводится в базе (коллекция drivers): рабочее имя, район и телефон,
# который появляется после привязки по одноразовой ссылке (driver_bot). Для
# всех, кто есть в базе, база — единственный источник: и район, и телефон
# берутся из неё, строка .env для них не читается. Строка .env остаётся
# запасным путём только для имён, которых в базе нет, — на время перехода.
# Тест-водитель (test: true) в расписание не попадает: он не возит, а
# проверяет приложение, и виден как персона test_driver().
_ENV_DRIVER_IDS = dict(DRIVER_IDS)
_CODE_DRIVERS = {d: list(v) for d, v in _BASE_DRIVERS.items()}
_CODE_DRIVER_AT = dict(_BASE_DRIVER_AT)
_ROSTER = {"rows": [], "test": {}, "at": 0.0}
_LAST_MOVES = ({}, {})


def roster_rows() -> list:
    """Записи водителей из базы, как прочитаны в последний раз."""
    return list(_ROSTER["rows"])


def apply_roster(rows: list):
    """Наложить реестр из базы на расписание и на телефоны."""
    global _BASE_DRIVERS, _BASE_DRIVER_AT
    from config_offices import OFFICE_IDS
    rows = [r for r in (rows or []) if str(r.get("name") or "").strip()]
    _ROSTER["rows"] = rows
    at = dict(_CODE_DRIVER_AT)
    ids = dict(_ENV_DRIVER_IDS)
    test = {}
    for r in rows:
        name = str(r["name"]).strip()
        tid = r.get("telegram_id")
        try:
            tid = int(tid) if tid else 0
        except (TypeError, ValueError):
            tid = 0
        if r.get("test"):
            if tid:
                test[tid] = name
            continue
        if r.get("hidden"):
            at.pop(name, None); ids.pop(name, None)
            continue
        d = str(r.get("district") or "").strip()
        if d in OFFICE_IDS:
            at[name] = d
        elif name not in at:
            continue                                  # без района — не водитель
        # База — источник правды для своих: телефон только из неё.
        if tid:
            ids[name] = tid
        else:
            ids.pop(name, None)
    _BASE_DRIVER_AT = at
    _BASE_DRIVERS = {d: [] for d in _CODE_DRIVERS}
    for n, d in at.items():
        _BASE_DRIVERS.setdefault(d, []).append(n)
    DRIVER_IDS.clear(); DRIVER_IDS.update(ids)
    DRIVER_BY_TG.clear(); DRIVER_BY_TG.update({v: k for k, v in ids.items()})
    _ROSTER["test"] = test
    apply_moves(*_LAST_MOVES)


async def sync(force: bool = False, min_age: float = 3.0):
    """Реестр и перестановки из базы — перед тем, как решать по людям.

    Зовётся на каждом запросе водителя и по кругу в каждой службе; чаще раза
    в несколько секунд ходить в базу незачем, поэтому есть выдержка."""
    import time as _t
    if not force and _t.monotonic() - _ROSTER["at"] < min_age:
        return
    import db
    rows = await db.get_driver_links()
    moves, dm = await db.staff_map_get(), await db.driver_map_get()
    apply_moves(moves, dm)
    apply_roster(rows)
    try:
        await sync_away()
    except Exception as e:                           # noqa: BLE001
        import logging as _lg
        _lg.getLogger("staff").warning(f"[staff] кто уехал — не прочитано: {e}")
    _ROSTER["at"] = _t.monotonic()


async def roster_loop(interval: float = 30.0):
    """Фон для служб, где нет запросов водителя: бот операторов, сторож
    геопозиции, бот владельца. Привязал телефон — узнают за полминуты."""
    import asyncio as _aio, logging as _lg
    log = _lg.getLogger("staff")
    while True:
        try:
            await sync(force=True)
        except Exception as e:                       # noqa: BLE001
            log.warning(f"[staff] реестр не прочитан: {e}")
        await _aio.sleep(interval)


def apply_moves(moves: dict, driver_moves: dict = None):
    """Наложить перестановку. Пустые словари возвращают всё как в коде."""
    global _LAST_MOVES
    _LAST_MOVES = (moves or {}, driver_moves or {})
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
