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
import math

# id офиса == id района в POS (operator_routes.DISTRICTS): офис ≡ район.
OFFICES = [
    {"id": "jvc",     "name": "JVC"},
    {"id": "tecom",   "name": "Тиком"},
    {"id": "bbay",    "name": "Бизнес Бей"},
    {"id": "silicon", "name": "Силикон"},
    {"id": "alguses", "name": "Алгусес"},
]
OFFICE_IDS = tuple(o["id"] for o in OFFICES)
OFFICE_NAMES = {o["id"]: o["name"] for o in OFFICES}

# Офисы из прошлой схемы. Живых заказов туда больше не пишем, но старые
# заказы на них ссылаются — чтобы история выручки и списки не ломались.
LEGACY_OFFICE_IDS = ("office_central", "office_north", "office_south")
LEGACY_OFFICE_NAMES = {
    "office_central": "Архив · Central",
    "office_north":   "Архив · North",
    "office_south":   "Архив · South",
}

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


def nearest_office(lat, lon):
    """(office_id, office_name) ближайшего офиса к точке доставки.

    None — если координат нет (0,0 / мусор) или опорные точки не настроены;
    вызывающий код тогда оставляет офис, выбранный по району в форме."""
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
