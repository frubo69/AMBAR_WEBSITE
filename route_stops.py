"""Маршрут за день из точек трека: остановки, разрывы, расстояние, подписи.

Чистая арифметика без базы — её гоняют стендом на синтетических треках.
Точки приходят как есть: раз в несколько секунд в движении и редко на
стоянке (телеграм шлёт точку, когда она сменилась), с выбросами GPS и
дырами, когда телефон молчал. Отсюда четыре правила:

  остановка — точки не выходят из круга STOP_RADIUS_M дольше STOP_MIN_S;
             стоянка с двумя точками за сорок минут — тоже остановка,
             а не разрыв: телефон молчал, потому что не двигался;
  разрыв    — точек нет дольше GAP_MIN_S, а следующая далеко: ехал без
             сигнала; на карте это пунктир, в списке — «без сигнала»;
  выброс    — отрезок быстрее GLITCH_KMH в расстояние не идёт;
  подпись   — остановка у адреса заказа этого дня — «Заказ N», у первой или
             последней точки дня — «База», остальное — «Остановка».
"""
import math
from datetime import datetime, timezone

STOP_RADIUS_M = 60          # в этом круге — стоит
STOP_MIN_S = 240            # стоит не меньше четырёх минут — остановка
GAP_MIN_S = 900             # точек нет дольше четверти часа — разрыв
GLITCH_KMH = 150            # быстрее — сбой GPS, отрезок не считаем
NEAR_ORDER_M = 150          # остановка у адреса заказа
NEAR_BASE_M = 150           # остановка у старта или финиша дня — база
ORDER_WINDOW_S = 3600       # заказ считается доставленным с этой остановки в пределах часа
DRAW_MAX = 1500             # точек в линии для карты: больше телефону не надо


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def norm_points(raw) -> list:
    """[{lat, lon, at}] по времени; без времени или координат — мимо."""
    out = []
    for p in raw or []:
        try:
            lat, lon = float(p.get("lat")), float(p.get("lon"))
        except (TypeError, ValueError, AttributeError):
            continue
        at = _dt(p.get("at"))
        if at is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        out.append({"lat": lat, "lon": lon, "at": at})
    out.sort(key=lambda x: x["at"])
    return out


def find_stops(pts: list) -> list:
    """Остановки: [{i0, i1, lat, lon, from, to, sec}] — i0..i1 включительно."""
    stops, n, i = [], len(pts), 0
    while i < n:
        clat, clon, k, j = pts[i]["lat"], pts[i]["lon"], 1, i + 1
        while j < n:
            if haversine_m(clat, clon, pts[j]["lat"], pts[j]["lon"]) > STOP_RADIUS_M:
                break
            k += 1
            clat += (pts[j]["lat"] - clat) / k
            clon += (pts[j]["lon"] - clon) / k
            j += 1
        sec = (pts[j - 1]["at"] - pts[i]["at"]).total_seconds()
        if j - i >= 2 and sec >= STOP_MIN_S:
            stops.append({"i0": i, "i1": j - 1, "lat": clat, "lon": clon,
                          "from": pts[i]["at"], "to": pts[j - 1]["at"], "sec": sec})
            i = j
        else:
            i += 1
    return stops


def find_gaps(pts: list) -> list:
    """Разрывы: молчал дольше GAP_MIN_S и за это время уехал."""
    gaps = []
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        sec = (b["at"] - a["at"]).total_seconds()
        if sec > GAP_MIN_S and haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > STOP_RADIUS_M:
            gaps.append({"i": i, "from": a["at"], "to": b["at"], "sec": sec,
                         "a": (a["lat"], a["lon"]), "b": (b["lat"], b["lon"])})
    return gaps


def _glitch(a, b) -> bool:
    sec = (b["at"] - a["at"]).total_seconds()
    d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
    return sec >= 0 and d / max(sec, 1.0) * 3.6 > GLITCH_KMH


def distance_m(pts: list, gaps: list, stops: list = None) -> float:
    """Путь: без разрывов, без выбросов и без дрожания на стоянках — стоящий
    телефон рисует точки вокруг себя, и за час такого «пути» набегал километр."""
    skip = {g["i"] for g in gaps}
    for st in stops or []:
        skip.update(range(st["i0"] + 1, st["i1"] + 1))
    total = 0.0
    for i in range(1, len(pts)):
        if i in skip or _glitch(pts[i - 1], pts[i]):
            continue
        total += haversine_m(pts[i - 1]["lat"], pts[i - 1]["lon"], pts[i]["lat"], pts[i]["lon"])
    return total


def label_stops(stops: list, pts: list, orders: list) -> None:
    """Подписи на месте: база — у старта или финиша дня; заказ — адрес заказа
    этого дня рядом и по времени; остальное — остановка."""
    first, last = (pts[0] if pts else None), (pts[-1] if pts else None)
    ords = []
    for o in orders or []:
        loc = o.get("location") or {}
        try:
            olat, olon = float(loc.get("lat")), float(loc.get("lon"))
        except (TypeError, ValueError):
            continue
        ords.append({"lat": olat, "lon": olon, "at": _dt(o.get("delivered_at") or o.get("timestamp")),
                     "id": str(o.get("order_id") or ""), "address": str(o.get("address") or "")[:60]})
    for s in stops:
        s["kind"], s["label"], s["order_id"], s["address"] = "stop", "Остановка", "", ""
        best, best_d = None, None
        for o in ords:
            d = haversine_m(s["lat"], s["lon"], o["lat"], o["lon"])
            if d > NEAR_ORDER_M:
                continue
            if o["at"] and not (s["from"].timestamp() - ORDER_WINDOW_S <= o["at"].timestamp()
                                <= s["to"].timestamp() + ORDER_WINDOW_S):
                continue
            if best is None or d < best_d:
                best, best_d = o, d
        if best:
            s["kind"], s["label"] = "order", f"Заказ {best['id']}".strip()
            s["order_id"], s["address"] = best["id"], best["address"]
            continue
        near_base = any(p and haversine_m(s["lat"], s["lon"], p["lat"], p["lon"]) <= NEAR_BASE_M
                        for p in (first, last))
        if near_base:
            s["kind"], s["label"] = "base", "База"


def _thin(pts: list) -> list:
    if len(pts) <= DRAW_MAX:
        return pts
    step = math.ceil(len(pts) / DRAW_MAX)
    out = pts[::step]
    if out[-1] is not pts[-1]:
        out.append(pts[-1])
    return out


def build(raw_points, orders=None) -> dict:
    """Всё, что нужно карточке маршрута, одним словарём."""
    pts = norm_points(raw_points)
    if not pts:
        return {"points": 0, "segments": [], "gaps": [], "stops": [], "start": None, "end": None,
                "summary": {"dist_km": 0, "moving_min": 0, "stop_min": 0, "stops": 0, "gaps": 0,
                            "from": "", "to": ""}}
    stops = find_stops(pts)
    gaps = find_gaps(pts)
    label_stops(stops, pts, orders or [])
    dist = distance_m(pts, gaps, stops)
    span = (pts[-1]["at"] - pts[0]["at"]).total_seconds()
    stop_sec = sum(s["sec"] for s in stops)
    gap_sec = sum(g["sec"] for g in gaps)
    # Линия — кусками между разрывами; сам разрыв карта рисует пунктиром.
    segments, cut = [], 0
    for g in gaps:
        segments.append(pts[cut:g["i"]])
        cut = g["i"]
    segments.append(pts[cut:])
    segs = [[[round(p["lat"], 6), round(p["lon"], 6)] for p in _thin(seg)] for seg in segments if len(seg) >= 1]
    iso = lambda d: d.astimezone(timezone.utc).isoformat()
    return {
        "points": len(pts),
        "segments": segs,
        "gaps": [{"from": iso(g["from"]), "to": iso(g["to"]), "min": round(g["sec"] / 60),
                  "a": [round(g["a"][0], 6), round(g["a"][1], 6)],
                  "b": [round(g["b"][0], 6), round(g["b"][1], 6)]} for g in gaps],
        "stops": [{"no": n + 1, "kind": s["kind"], "label": s["label"], "order_id": s["order_id"],
                   "address": s["address"], "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
                   "from": iso(s["from"]), "to": iso(s["to"]), "min": round(s["sec"] / 60)}
                  for n, s in enumerate(stops)],
        "start": {"lat": round(pts[0]["lat"], 6), "lon": round(pts[0]["lon"], 6), "at": iso(pts[0]["at"])},
        "end": {"lat": round(pts[-1]["lat"], 6), "lon": round(pts[-1]["lon"], 6), "at": iso(pts[-1]["at"])},
        "summary": {"dist_km": round(dist / 1000, 1), "moving_min": max(0, round((span - stop_sec - gap_sec) / 60)),
                    "stop_min": round(stop_sec / 60), "stops": len(stops), "gaps": len(gaps),
                    "from": iso(pts[0]["at"]), "to": iso(pts[-1]["at"])},
    }
