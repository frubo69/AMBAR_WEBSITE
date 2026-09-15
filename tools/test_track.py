"""Точки от трекер-приложений: OsmAnd (Traccar Client) и OwnTracks на одной
ручке /api/track; ключ — единственный секрет; keepalive вместо срока
трансляции; старые точки из буфера не затирают свежую (15 сен 2026). Без базы."""
import asyncio, json, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from aiohttp.test_utils import make_mocked_request
import track_routes as tr, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
TRK = {"K7QW2XNP4A": {"_id": "K7QW2XNP4A", "key": "Худоба"}, "SENIOR1234": {"_id": "SENIOR1234", "key": "op:Старший"}}
POS, SEEN, SNAP = [], [], {}
async def by_token(t): return TRK.get(t)
async def pos_set(name, day, lat, lon, at, until=None, acc=None, stop_live=False, live=None, keepalive=None):
    POS.append(dict(name=name, day=day, lat=lat, lon=lon, at=at, acc=acc, keepalive=keepalive, until=until))
async def seen(token, at, batt=None): SEEN.append((token, batt))
async def snapshot(name): return SNAP.get(name)
db.tracker_by_token = by_token; db.driver_pos_set = pos_set; db.tracker_seen = seen; db.driver_pos_snapshot = snapshot
def req(method, qs="", body=None, ctype="application/json"):
    if body is not None:
        raw = json.dumps(body).encode() if ctype == "application/json" else body.encode()
        r = make_mocked_request(method, "/api/track" + qs, headers={"Content-Type": ctype}, payload=None)
        r._read_bytes = raw            # aiohttp: тело мокнутого запроса
        return r
    return make_mocked_request(method, "/api/track" + qs)
async def call(*a, **k):
    resp = await tr.handle_track(req(*a, **k)); return resp.status, json.loads(resp.text)
async def main():
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    st, body = await call("GET", f"?id=K7QW2XNP4A&lat=25.1&lon=55.2&timestamp={ts}&accuracy=12&batt=80&speed=3")
    eq("OsmAnd GET → 200 и точка водителя с keepalive", (st, POS[-1]["name"], POS[-1]["lat"], POS[-1]["acc"], POS[-1]["keepalive"], POS[-1]["until"]),
       (200, "Худоба", 25.1, 12.0, tr.KEEPALIVE_SEC, None))
    eq("батарея запомнена по ключу", SEEN[-1], ("K7QW2XNP4A", 80.0))
    eq("день точки — учётные сутки", POS[-1]["day"], __import__("bizday").biz_day(now))
    st, body = await call("GET", f"?id=K7QW2XNP4A&lat=25.1&lon=55.2&timestamp={ts * 1000}")
    eq("миллисекунды тоже понимаем", (st, abs((POS[-1]["at"] - now).total_seconds()) < 2), (200, True))
    st, body = await call("POST", "?id=SENIOR1234", body={"_type": "location", "lat": 25.2, "lon": 55.3, "tst": ts, "acc": 5, "batt": 41, "tid": "ST"})
    eq("OwnTracks JSON → точка старшего, ответ []", (st, body, POS[-1]["name"], POS[-1]["acc"]), (200, [], "op:Старший", 5.0))
    st, body = await call("POST", "?id=SENIOR1234", body={"_type": "transition", "event": "enter"})
    eq("OwnTracks не-location → пропускаем, 200", (st, body, len(POS)), (200, [], 3))
    st, body = await call("POST", "", body="id=K7QW2XNP4A&lat=25.3&lon=55.4", ctype="application/x-www-form-urlencoded")
    eq("OsmAnd POST-форма → точка", (st, POS[-1]["lat"], POS[-1]["lon"]), (200, 25.3, 55.4))
    st, body = await call("GET", "?id=NOPE&lat=25&lon=55")
    eq("чужой ключ → 401 (не 404: джейл сканеров)", st, 401)
    st, body = await call("GET", "?id=K7QW2XNP4A&lat=95&lon=55")
    eq("координаты мимо → 422", st, 422)
    n = len(POS)
    st, body = await call("GET", f"?id=K7QW2XNP4A&lat=25&lon=55&timestamp={ts - 7 * 3600}")
    eq("точка семичасовой давности из буфера — не берём", (st, body.get("ignored"), len(POS)), (200, "too_old", n))
    SNAP["Худоба"] = {"at": now}
    st, body = await call("GET", f"?id=K7QW2XNP4A&lat=25&lon=55&timestamp={ts - 600}")
    eq("точка старее уже записанной — не затираем свежую", (st, body.get("ignored"), len(POS)), (200, "older", n))
    st, body = await call("GET", f"?id=K7QW2XNP4A&lat=25&lon=55&timestamp={ts + 3600}")
    eq("время из будущего → сейчас", (st, abs((POS[-1]["at"] - now).total_seconds()) < 2), (200, True))
    SNAP.clear()
    st, body = await call("GET", "?id=K7QW2XNP4A&lat=25&lon=55&timestamp=2026-09-15T12:00:00Z")
    eq("ISO-время понимаем", (st, POS[-1]["at"].isoformat()), (200, "2026-09-15T12:00:00+00:00"))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
