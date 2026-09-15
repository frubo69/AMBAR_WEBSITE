"""Точки от трекер-приложений (15 сен 2026).

Телеграм в фоне точек не шлёт: айфон без движения молчит часами, в движении
шлёт пачками, только пока телеграм открыт. Владелец: «мне надо безотказно
видеть их геопозицию». Поэтому вторая, независимая дорога: на телефоне
стоит трекер (Traccar Client, OwnTracks — свободные и открытые), он шлёт
точку сам, каждые полминуты, и кладёт её в ту же запись driver_pos, что и
трансляция телеграма. Локатор, сторож и приложение ничего нового не узнают.

Протоколы:
  • OsmAnd (Traccar Client): GET или POST-форма
      /api/track?id=КЛЮЧ&lat=..&lon=..&timestamp=..&accuracy=..&batt=..
  • OwnTracks (HTTP-режим): POST JSON {"_type":"location","lat":..,"lon":..,
      "tst":..,"acc":..,"batt":..} на /api/track?id=КЛЮЧ

Ключ — единственный секрет: по нему находим, чья точка. Ни имени, ни id
телеграма в запросе нет. Ключи выдаёт STAR (owner_routes)."""
import json
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import bizday
from owner_auth import CORS_HEADERS

log = logging.getLogger("ambar.track")

KEEPALIVE_SEC = 15 * 60        # без точек четверть часа — трекер «замолчал», трансляции нет
MAX_AGE_SEC = 6 * 3600         # точка старше шести часов из буфера трекера — не наша


def _ts(v, now: datetime) -> datetime:
    """Момент точки: секунды или миллисекунды с эпохи, ISO 8601, «yyyy-MM-dd HH:mm:ss»;
    нет или мусор — сейчас. Из будущего — сейчас (часы телефона врут)."""
    if v is None or v == "":
        return now
    s = str(v).strip()
    try:
        n = float(s)
        if n > 1e11:                       # миллисекунды
            n /= 1000.0
        dt = datetime.fromtimestamp(n, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return now
    return now if dt > now + timedelta(minutes=1) else dt


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _read(request) -> dict:
    """Параметры из строки запроса, формы или JSON — что прислали."""
    p = dict(request.query)
    if request.method == "POST":
        ctype = (request.headers.get("Content-Type") or "").lower()
        try:
            if "json" in ctype:
                body = await request.json()
                if isinstance(body, dict):
                    if body.get("_type") and body.get("_type") != "location":
                        p["_skip"] = "1"           # OwnTracks шлёт и другое: transition, lwt…
                    for k in ("lat", "lon", "acc", "batt", "tst", "vel", "tid"):
                        if body.get(k) is not None:
                            p.setdefault({"acc": "accuracy", "tst": "timestamp"}.get(k, k), body[k])
            else:
                form = await request.post()
                for k, v in form.items():
                    p.setdefault(k, v)
        except Exception:                          # noqa: BLE001
            pass
    return p


async def handle_track(request):
    now = datetime.now(timezone.utc)
    p = await _read(request)
    if p.get("_skip"):
        return web.json_response([], headers=CORS_HEADERS)
    token = str(p.get("id") or p.get("deviceid") or p.get("u") or "").strip()
    trk = await db.tracker_by_token(token) if token else None
    if not trk:
        # 401, а не 404: сканер-джейл считает 404-е, а телефоны сидят за общим адресом оператора.
        return web.json_response({"error": "unknown_tracker"}, status=401, headers=CORS_HEADERS)
    lat, lon = _num(p.get("lat")), _num(p.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return web.json_response({"error": "bad_coords"}, status=422, headers=CORS_HEADERS)
    at = _ts(p.get("timestamp"), now)
    if (now - at).total_seconds() > MAX_AGE_SEC:
        return web.json_response({"ok": True, "ignored": "too_old"}, headers=CORS_HEADERS)
    acc = _num(p.get("accuracy"))
    if acc is None:
        acc = _num(p.get("hdop"))
    batt = _num(p.get("batt"))
    key = trk["key"]
    # Трекер шлёт и накопленное без связи: точку старее уже записанной не
    # кладём поверх свежей, иначе человек «отъезжал» назад.
    try:
        cur = await db.driver_pos_snapshot(key)
        cur_at = (cur or {}).get("at")
        if cur_at is not None and getattr(cur_at, "tzinfo", None) is None:
            cur_at = cur_at.replace(tzinfo=timezone.utc)
        if cur_at and at < cur_at:
            return web.json_response({"ok": True, "ignored": "older"}, headers=CORS_HEADERS)
    except Exception as e:                         # noqa: BLE001
        log.warning(f"[track] текущая точка {key} не прочиталась: {e}")
    try:
        await db.driver_pos_set(key, bizday.biz_day(at), lat, lon, at,
                                acc=acc if acc is not None and acc >= 0 else None,
                                keepalive=KEEPALIVE_SEC)
        await db.tracker_seen(token, at, batt if batt is not None else None)
    except Exception as e:                         # noqa: BLE001
        log.error(f"[track] точка {key} не записана: {e}")
        return web.json_response({"error": "db"}, status=503, headers=CORS_HEADERS)
    # OwnTracks ждёт JSON-массив, Traccar Client — любой 2xx.
    return web.json_response([], headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=204, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    for path in ("/api/track", "/track"):
        r.add_route("OPTIONS", path, _opt)
        r.add_route("GET", path, handle_track)
        r.add_route("POST", path, handle_track)
    log.info("[track] routes mounted")
