"""
AMBAR — офисы (районы) и определение ближайшего офиса к клиенту.

╔══════════════════════════════════════════════════════════════════════════╗
║  ПРИВАТНОСТЬ — ЧИТАТЬ ПЕРЕД ПРАВКОЙ                                      ║
║                                                                          ║
║  Реальных адресов и координат офисов в этом файле НЕТ и быть не должно.  ║
║  Опорные точки живут ТОЛЬКО в переменной окружения AMBAR_OFFICE_ANCHORS  ║
║  (/opt/ambar/.env — вне git), и владелец кладёт туда уже огрублённые     ║
║  до сетки 0.02° (~2 км) значения, полученные tools/office_anchor.py.     ║
║                                                                          ║
║  Округление необратимо: из клетки 2×2 км исходную точку восстановить     ║
║  нельзя — информации для этого физически не осталось. Для выбора         ║
║  ближайшего офиса такой точности хватает с запасом: офисы разнесены      ║
║  на километры, и сдвиг опорной точки на километр решение не меняет.      ║
║                                                                          ║
║  НИКОГДА не хардкодить координаты сюда и тем более во фронтенд.          ║
╚══════════════════════════════════════════════════════════════════════════╝

Формат AMBAR_OFFICE_ANCHORS (одна строка):
    jvc:LAT,LON|tecom:LAT,LON|bbay:LAT,LON|silicon:LAT,LON|alguses:LAT,LON

Если переменная не задана — гео-выбор офиса просто отключается, и офис
берётся из того, что прислал клиент (район из формы). Ничего не падает.
"""
import os
import re
import math

# id офиса == id района в POS (operator_routes.DISTRICTS): офис ≡ район.
# Порядок здесь — рабочий порядок владельца, B1…B5. От него отталкивается всё:
# разбивка выручки, плитки районов, счётчики заказов. Меняется он тут и больше
# нигде.
# ── Телефоны точек ────────────────────────────────────────────────────────
# Рабочие линии районов: у каждой точки своя сим-карта, у Бизнес Бей их две.
# Номер нужен в карточке района — чаще всего владелец смотрит на неё как раз
# затем, чтобы позвонить на точку, а не чтобы искать номер в записной книжке.
#
# Значения по умолчанию пустые: настоящие номера кладём в /opt/ambar/.env,
# рядом с остальными живыми данными, а не в репозиторий. Формат одной строкой:
#
#   AMBAR_OFFICE_PHONES=jvc:001=+9715xxxxxxx|bbay:002_1=+9715xxxxxxx,002_2=+9715xxxxxxx
#
# Пока переменной нет, строка с телефоном просто не рисуется — ничего не
# ломается и пустых плашек не появляется.
OFFICE_PHONES = {
    "jvc":     [("001", "")],
    "bbay":    [("002_1", ""), ("002_2", "")],
    "silicon": [("003", "")],
    "alguses": [("004", "")],
    "tecom":   [("005", "")],
}


def _load_phones() -> dict:
    raw = os.getenv("AMBAR_OFFICE_PHONES", "").strip()
    if not raw:
        return {k: [(lbl, num) for lbl, num in v if num] for k, v in OFFICE_PHONES.items()}
    out = {}
    for part in raw.split("|"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        oid, lines = part.split(":", 1)
        items = []
        for chunk in lines.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            label, _, num = chunk.partition("=")
            num = (num or label).strip()
            items.append(((label.strip() if num != label.strip() else ""), num))
        if items:
            out[oid.strip()] = items
    return out


def phones_for(office_id: str) -> list:
    """[{"label": "002_1", "number": "+9715..."}] — пусто, если номер не задан."""
    return [{"label": lbl, "number": num}
            for lbl, num in _load_phones().get(office_id, []) if num]


OFFICES = [
    {"id": "jvc",     "code": "B1", "name": "JVC"},
    {"id": "bbay",    "code": "B2", "name": "Бизнес Бей"},
    {"id": "silicon", "code": "B3", "name": "Силикон"},
    {"id": "alguses", "code": "B4", "name": "Алгусес"},
    {"id": "tecom",   "code": "B5", "name": "Тиком"},
]
OFFICE_CODES = {o["id"]: o["code"] for o in OFFICES}
OFFICE_IDS = tuple(o["id"] for o in OFFICES)
OFFICE_NAMES = {o["id"]: o["name"] for o in OFFICES}

# Старые id из прошлой схемы. История уже разложена по районам
# (district_id / координаты / название района в адресе); сюда попадают лишь
# единичные заказы, у которых нет вообще никаких признаков местоположения.
LEGACY_OFFICE_IDS = ("office_central", "office_north", "office_south")
LEGACY_OFFICE_NAMES = {oid: "Без района" for oid in LEGACY_OFFICE_IDS}

def office_name(office_id: str) -> str:
    return OFFICE_NAMES.get(office_id) or LEGACY_OFFICE_NAMES.get(office_id) or (office_id or "—")


DEFAULT_OPERATORS = [int(x.strip()) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]

# Привязки «офис → оператор» пока НЕТ: каждый оператор видит все офисы, ровно
# как до появления районов. Когда решим разделять — раскидать здесь.
OFFICE_OPERATORS = {oid: DEFAULT_OPERATORS for oid in OFFICE_IDS}
for _lid in LEGACY_OFFICE_IDS:
    OFFICE_OPERATORS[_lid] = DEFAULT_OPERATORS


# ── опорные точки из окружения ──────────────────────────────────────────────
def _load_anchors() -> dict:
    """{office_id: (lat, lon)} из AMBAR_OFFICE_ANCHORS. Кривые записи молча
    пропускаются — лучше офис без гео, чем упавший сервер."""
    out = {}
    raw = (os.getenv("AMBAR_OFFICE_ANCHORS", "") or "").strip()
    for part in raw.split("|"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        oid, _, coords = part.partition(":")
        oid = oid.strip()
        lat_s, _, lon_s = coords.partition(",")
        try:
            lat, lon = float(lat_s.strip()), float(lon_s.strip())
        except ValueError:
            continue
        if oid in OFFICE_NAMES and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out[oid] = (lat, lon)
    return out


OFFICE_ANCHORS = _load_anchors()
GEO_READY = bool(OFFICE_ANCHORS)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по большому кругу (гаверсинус), км."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Куда падает заказ, если определить район не удалось ничем. Настраивается
# через AMBAR_DEFAULT_OFFICE. Существует, чтобы заказов «без района» не было
# в принципе — такой заказ ломает финансовую разбивку.
DEFAULT_OFFICE_ID = os.getenv("AMBAR_DEFAULT_OFFICE", "bbay")
if DEFAULT_OFFICE_ID not in OFFICE_NAMES:
    DEFAULT_OFFICE_ID = OFFICE_IDS[0]

# Явные названия районов в свободном тексте адреса. Только однозначные слова.
NAME_HINTS = {
    "jvc":     ("jvc", "jvt", "jumeirah village"),
    "tecom":   ("тиком", "tecom", "barsha", "барша"),
    "bbay":    ("бизнес бей", "бизнес бэй", "business bay"),
    "silicon": ("силикон", "silicon"),
    "alguses": ("алгусес", "алгусайс", "qusais", "кусаис"),
}
_COORD_RE = re.compile(r"(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)")


def nearest_offices(office_id: str) -> list:
    """Районы по близости к заданному — от ближнего к дальнему, без него самого.

    Нужно подмене: оператор скрылся, и его заказы должен подхватить тот, кому
    ехать ближе всех. Без опорных точек (AMBAR_OFFICE_ANCHORS) расстояния взять
    неоткуда — тогда отдаём порядок из настроек: он хотя бы постоянный, а не
    случайный."""
    здесь = OFFICE_ANCHORS.get(office_id)
    прочие = [o for o in OFFICE_IDS if o != office_id]
    if not здесь:
        return прочие
    с_гео = [o for o in прочие if o in OFFICE_ANCHORS]
    без_гео = [o for o in прочие if o not in OFFICE_ANCHORS]
    с_гео.sort(key=lambda o: _km(здесь[0], здесь[1],
                                 OFFICE_ANCHORS[o][0], OFFICE_ANCHORS[o][1]))
    return с_гео + без_гео


def resolve_office(src: dict):
    """Определить район заказа. Возвращает (office_id, office_name, rule).

    Заказ БЕЗ района невозможен: последним шагом отрабатывает дефолт, иначе
    выручка повисает в «Без района» и портит разбивку по офисам.
    Порядок — от самого достоверного признака к самому слабому."""
    # 1. Оператор явно выбрал район в POS
    d = src.get("district_id")
    if d in OFFICE_NAMES:
        return d, OFFICE_NAMES[d], "district"

    # 2. Координаты точки доставки
    loc = src.get("location") or {}
    near = nearest_office(loc.get("lat"), loc.get("lon"))
    if near:
        return near[0], near[1], "geo"

    # 3. Координаты внутри ссылки на карту
    m = _COORD_RE.search(str(src.get("gmap_link") or ""))
    if m:
        near = nearest_office(m.group(1), m.group(2))
        if near:
            return near[0], near[1], "gmap"

    # 4. Район, выбранный клиентом в форме
    cid = src.get("office_id")
    if cid in OFFICE_NAMES:
        return cid, OFFICE_NAMES[cid], "form"

    # 5. Название района прямо в тексте адреса/комментария
    txt = (str(src.get("address") or "") + " " + str(src.get("comment") or "")).lower()
    if txt.strip():
        for oid, words in NAME_HINTS.items():
            if any(w in txt for w in words):
                return oid, OFFICE_NAMES[oid], "address"

    # 6. Ничего не сработало — дефолтный район, чтобы заказ не потерялся
    return DEFAULT_OFFICE_ID, OFFICE_NAMES[DEFAULT_OFFICE_ID], "default"


def nearest_office(lat, lon):
    """(office_id, office_name) ближайшего офиса к точке доставки.

    None — если координат нет (0,0 / мусор) или опорные точки не настроены."""
    if not OFFICE_ANCHORS:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not lat or not lon:          # 0,0 — координат по факту нет
        return None
    best, best_km = None, None
    for oid, (a_lat, a_lon) in OFFICE_ANCHORS.items():
        d = _km(lat, lon, a_lat, a_lon)
        if best_km is None or d < best_km:
            best, best_km = oid, d
    return (best, OFFICE_NAMES[best]) if best else None
