"""Геопозиция старшего (19 сен 2026, владелец: «присылай только сообщения о
том, что включили/выключили геопозицию»). Про старшего — только сигналы
телеграма из чата STAR-бота: «выключил геопозицию» и «включил геопозицию»
(после выключения — сколько её не было); самим старшим не шлём. Проход
сторожа про старшего не пишет ничего: ни «два часа без точки», ни «не видна
с начала смены», ни «точки снова идут». Без базы."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import geo_watch as gw, driver_routes as dr, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

G, ST, SENT, EXCL = {}, {}, [], []
async def geo_state(key, since=None): return dict(G)
async def watch_get(key): return dict(ST)
async def watch_set(key, fields, unset=None):
    ST.update(fields)
    for k in (unset or []): ST.pop(k, None)
async def owners(text, event, reply_markup=None, exclude=None, meta=None):
    SENT.append(text); EXCL.append(set(exclude or ())); return 1
async def no_days(day): return []
async def nosync(*a, **k): return None
dr._geo_state = geo_state
db.geo_watch_get = watch_get; db.geo_watch_set = watch_set; db.get_driver_days = no_days
gw._owners = owners
gw.staff.sync = nosync
gw._working_hours = lambda now: True
gw.staff.SENIOR_STAR_IDS = {"Старший": 7}
gw._STARTED = None
UTC = datetime(2026, 9, 15, 11, 50, tzinfo=timezone.utc)


async def main():
    print("── выключил / включил ─────────────────────────────────────────")
    r = await gw.on_senior_stream("Старший", False, UTC)
    eq("выключил — одно сообщение", (r, SENT), (True, ["📍 *Старший*: выключил геопозицию"]))
    eq("самим старшим не шлём", EXCL[-1], {7})
    r = await gw.on_senior_stream("Старший", False, UTC + timedelta(minutes=2))
    eq("повторный сигнал выключения — без повтора", (r, len(SENT)), (False, 1))
    r = await gw.on_senior_stream("Старший", True, UTC + timedelta(minutes=47))
    eq("включил — «Не было 47 мин»", (r, SENT[-1]), (True, "📍 *Старший*: включил геопозицию\nНе было 47 мин."))
    eq("отметка выключения снята", "off_since" in ST, False)
    r = await gw.on_senior_stream("Старший", True, UTC + timedelta(minutes=50))
    eq("включил без выключения — просто «включил»", SENT[-1], "📍 *Старший*: включил геопозицию")

    print("── проход про старшего молчит ─────────────────────────────────")
    SENT.clear(); ST.clear()
    for age, stream in ((2 * 3600 + 60, True), (40 * 60, False), (None, False)):
        G.clear(); G.update({"stream": stream, "age_sec": age, "fresh": False, "until": "x" if stream else ""})
        await gw.tick(UTC.astimezone(gw.DUBAI_TZ))
    eq("два часа без точки, остывшая точка, ни одной точки — ни слова", SENT, [])
    eq("прохода по старшему больше нет", hasattr(gw, "_seniors_tick"), False)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
