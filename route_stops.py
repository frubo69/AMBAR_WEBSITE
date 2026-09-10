"""Маршрут за день из точек трека: остановки, стоянки, разрывы, отрезки пути,
расстояние, подписи, состояние «сейчас».

Чистая арифметика без базы — её гоняют стендом на синтетических треках.
Точки приходят как есть: раз в несколько секунд в движении и редко на
стоянке (телеграм шлёт точку, когда она сменилась), с выбросами GPS и
дырами, когда телефон молчал. Правила (владелец, 10 сен 2026):

  остановка — три минуты подряд точки не выходят из круга ста метров;
              стоянка с двумя точками за сорок минут — тоже остановка, а не
              разрыв: телефон молчал, потому что не двигался;
  стоянка   — та же остановка, но от десяти минут: «прибыл куда-то»;
  разрыв    — точек нет дольше пятнадцати минут, а следующая далеко: ехал без
              сигнала; на карте это пунктир, на полоске — красный отрезок;
  выброс    — отрезок быстрее 150 км/ч в расстояние не идёт;
  подпись   — остановка у адреса заказа этого дня — «Заказ N», у первой или
              последней точки дня — «База»;
  сейчас    — последняя точка не финиш: сегодня это «в пути», «стоит здесь
              с …» или «сигнал пропал в …»; за прошлый день — последняя точка.
"""
import math
from datetime import datetime, timezone

STOP_RADIUS_M = 100         # в этом круге — стоит
STOP_MIN_S = 180            # три минуты — остановка
VISIT_MIN_S = 600           # десять минут — уже стоянка, прибыл
GAP_MIN_S = 900             # точек нет дольше четверти часа — разрыв (в пути)
LOST_S = 900                # последней точке больше четверти часа — сигнал пропал
GLITCH_KMH = 150            # быстрее — сбой GPS, отрезок не считаем
NEAR_ORDER_M = 150          # остановка у адреса заказа
NEAR_BASE_M = 150           # остановка у старта или финиша дня — база
ORDER_WINDOW_S = 3600       # заказ считается доставленным с этой остановки в пределах часа
DRAW_MAX = 400              # точек в одном отрезке линии для карты


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


def _dist(pts: list, i0: int, i1: int) -> float:
    """Путь по точкам i0..i1 без выбросов."""
    total = 0.0
    for i in range(i0 + 1, i1 + 1):
        if _glitch(pts[i - 1], pts[i]):
            continue
        total += haversine_m(pts[i - 1]["lat"], pts[i - 1]["lon"], pts[i]["lat"], pts[i]["lon"])
    return total


def label_stops(stops: list, pts: list, orders: list) -> None:
    """Подписи на месте: заказ — адрес заказа этого дня рядом и по времени;
    база — у старта или финиша дня; иначе по длительности — остановка или
    стоянка («прибыл куда-то»)."""
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
        long = s["sec"] >= VISIT_MIN_S
        s["kind"], s["label"] = ("visit", "Стоянка") if long else ("stop", "Остановка")
        s["order_id"], s["address"] = "", ""
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
        if any(p and haversine_m(s["lat"], s["lon"], p["lat"], p["lon"]) <= NEAR_BASE_M
               for p in (first, last)):
            s["kind"], s["label"] = "base", "База"


def _thin(pts: list) -> list:
    if len(pts) <= DRAW_MAX:
        return pts
    step = math.ceil(len(pts) / DRAW_MAX)
    out = pts[::step]
    if out[-1] is not pts[-1]:
        out.append(pts[-1])
    return out


def _empty():
    return {"points": 0, "timeline": [], "stops": [], "start": None, "end": None,
            "summary": {"dist_km": 0, "moving_min": 0, "stop_min": 0, "stops": 0, "visits": 0,
                        "gaps": 0, "from": "", "to": ""}}


def build(raw_points, orders=None, now=None, today: bool = True) -> dict:
    """Всё, что нужно экрану маршрута, одним словарём.

    timeline — отрезки подряд без дыр, от первой точки до последней: в пути
    (move, с куском линии и километрами), остановка / стоянка / заказ / база
    (с точкой и подписью), разрыв (gap, две точки для пунктира). По нему
    рисуется полоска внизу экрана и линия на карте; stops — те же остановки
    отдельно, для меток. end.state — что сейчас: moving / stopped / lost, а за
    прошлый день — last."""
    pts = norm_points(raw_points)
    if not pts:
        return _empty()
    now = _dt(now) or datetime.now(timezone.utc)
    stops = find_stops(pts)
    gaps = find_gaps(pts)
    label_stops(stops, pts, orders or [])
    iso = lambda d: d.astimezone(timezone.utc).isoformat()
    n = len(pts)

    # Отрезки подряд: события по положению в треке, между ними — путь.
    marks = [("stop", s["i0"], s) for s in stops] + [("gap", g["i"] - 1, g) for g in gaps]
    marks.sort(key=lambda m: m[1])
    timeline, cursor, move_m, no = [], 0, 0.0, 0
    def _move(i0, i1):
        nonlocal move_m
        if i1 <= i0:
            return
        d = _dist(pts, i0, i1)
        move_m += d
        seg = _thin(pts[i0:i1 + 1])
        timeline.append({"kind": "move", "from": iso(pts[i0]["at"]), "to": iso(pts[i1]["at"]),
                         "min": round((pts[i1]["at"] - pts[i0]["at"]).total_seconds() / 60),
                         "dist_km": round(d / 1000, 1),
                         "pts": [[round(p["lat"], 6), round(p["lon"], 6)] for p in seg]})
    for kind, at_i, ev in marks:
        if kind == "stop":
            _move(cursor, ev["i0"])
            no += 1
            ev["no"] = no
            timeline.append({"kind": ev["kind"], "no": no, "label": ev["label"],
                             "order_id": ev["order_id"], "address": ev["address"],
                             "lat": round(ev["lat"], 6), "lon": round(ev["lon"], 6),
                             "from": iso(ev["from"]), "to": iso(ev["to"]),
                             "min": round(ev["sec"] / 60), "ongoing": False})
            cursor = ev["i1"]
        else:
            _move(cursor, ev["i"] - 1)
            timeline.append({"kind": "gap", "from": iso(ev["from"]), "to": iso(ev["to"]),
                             "min": round(ev["sec"] / 60),
                             "a": [round(ev["a"][0], 6), round(ev["a"][1], 6)],
                             "b": [round(ev["b"][0], 6), round(ev["b"][1], 6)]})
            cursor = ev["i"]
    _move(cursor, n - 1)

    # Сейчас. Последняя точка — не финиш: сегодня она говорит, что человек
    # либо едет, либо стоит здесь с такого-то часа, либо пропал из эфира.
    last = pts[-1]
    age = (now - last["at"]).total_seconds()
    in_stop = bool(stops) and stops[-1]["i1"] == n - 1
    if not today:
        state = "last"
    elif age > LOST_S:
        state = "lost"
    elif in_stop:
        state = "stopped"
    else:
        state = "moving"
    if state == "stopped" and timeline and timeline[-1].get("kind") != "move":
        # Стоит и сейчас: длительность считаем до «сейчас», а не до последней точки.
        timeline[-1]["ongoing"] = True
        timeline[-1]["min"] = round((now - stops[-1]["from"]).total_seconds() / 60)
        stops[-1]["sec"] = (now - stops[-1]["from"]).total_seconds()

    stop_sec = sum(s["sec"] for s in stops)
    gap_sec = sum(g["sec"] for g in gaps)
    moving_sec = sum(t["min"] * 60 for t in timeline if t["kind"] == "move")
    return {
        "points": n,
        "timeline": timeline,
        "stops": [{"no": s["no"], "kind": s["kind"], "label": s["label"], "order_id": s["order_id"],
                   "address": s["address"], "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
                   "from": iso(s["from"]), "to": iso(s["to"]), "min": round(s["sec"] / 60),
                   "ongoing": bool(state == "stopped" and s is stops[-1])} for s in stops],
        "start": {"lat": round(pts[0]["lat"], 6), "lon": round(pts[0]["lon"], 6), "at": iso(pts[0]["at"])},
        "end": {"lat": round(last["lat"], 6), "lon": round(last["lon"], 6), "at": iso(last["at"]),
                "state": state, "age_min": round(age / 60)},
        "summary": {"dist_km": round(move_m / 1000, 1), "moving_min": round(moving_sec / 60),
                    "stop_min": round(stop_sec / 60), "stops": len(stops),
                    "visits": sum(1 for s in stops if s["kind"] in ("visit", "order", "base") and s["sec"] >= VISIT_MIN_S),
                    "gaps": len(gaps), "from": iso(pts[0]["at"]), "to": iso(last["at"])},
    }
