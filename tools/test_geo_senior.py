"""Сторож старшего: два часа без точки любым путём — одно сообщение; в новый
день о той же пропаже второй раз не пишем; о выключенной трансляции тик не
дублирует мгновенное сообщение (15 сен 2026). Без базы."""
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

G, ST, SENT = {}, {}, []
async def geo_state(key, since=None): return dict(G)
async def watch_get(key): return dict(ST)
async def watch_set(key, fields, unset=None):
    ST.update(fields)
    for k in (unset or []): ST.pop(k, None)
async def owners(text, event, reply_markup=None, exclude=None): SENT.append(text.split("\n")[0]); return 1
dr._geo_state = geo_state
db.geo_watch_get = watch_get; db.geo_watch_set = watch_set
gw._owners = owners
gw._working_hours = lambda now: True
gw.staff.SENIOR_STAR_IDS = {"Старший": 1}
gw._senior_ids = lambda: set()
H = 3600
def g(stream, age, fresh=False): return {"stream": stream, "age_sec": age, "fresh": fresh, "until": "x" if stream else ""}
UTC = datetime(2026, 9, 15, 11, 50, tzinfo=timezone.utc)
async def tick(day="2026-09-15", utc=UTC):
    out = {}
    await gw._seniors_tick(utc.astimezone(gw.DUBAI_TZ), utc, day, out)
    return out

async def main():
    # 1. Трансляция идёт, точка час назад — виден, молчим
    ST.clear(); SENT.clear(); G.update(g(True, H))
    await tick(); eq("час без точки при трансляции — виден, сообщений нет", (SENT, ST.get("seen")), ([], True))
    # 2. Два часа без точки — одно сообщение, повтор тика молчит
    G.update(g(True, 2 * H + 60)); await tick(); await tick()
    eq("два часа без точки — одно «не присылает точку»", SENT, ["📍 *Старший*: телефон два часа не присылает точку"])
    eq("молчание считается с последней точки", ST.get("off_since"), UTC - timedelta(seconds=2 * H + 60))
    # 3. Новый день, всё ещё молчит — второго сообщения нет (раньше шло «с начала смены ни одной точки»)
    await tick(day="2026-09-16", utc=UTC + timedelta(hours=20))
    eq("в новый день о той же пропаже не пишем", len(SENT), 1)
    # 4. Точка пришла — «точки снова идут», состояние снято
    G.update(g(True, 30)); await tick(day="2026-09-16", utc=UTC + timedelta(hours=20))
    eq("точка пришла — «снова идут», пропажа снята", (SENT[-1], "off_since" in ST, ST.get("seen")), ("📍 *Старший*: точки снова идут", False, True))
    # 5. Без трансляции — по свежей точке из панели, как и было: остыла — «не видна», один раз
    SENT.clear(); ST.clear(); ST.update({"day": "2026-09-16", "seen": True}); G.update(g(False, 40 * 60))
    await tick(day="2026-09-16"); await tick(day="2026-09-16")
    eq("без трансляции точка остыла — «не видна», один раз", SENT, ["📍 *Старший*: геопозиция не видна"])
    # 6. Выключил трансляцию: мгновенное сообщение уже ушло (stream_off) — тик только запоминает
    SENT.clear(); ST.clear(); ST.update({"day": "2026-09-16", "seen": True, "stream_off": True}); G.update(g(False, 3 * H))
    await tick(day="2026-09-16")
    eq("после «выключил» тик не дублирует сообщение, но помнит с какой минуты", (SENT, ST.get("off_why"), "stream_off" in ST), ([], "stream", False))
    # 7. Новый день без единой точки с его начала — «с начала смены ни одной точки»
    SENT2 = []
    async def owners_full(text, event, reply_markup=None, exclude=None): SENT2.append(text); return 1
    gw._owners = owners_full
    ST.clear(); ST.update({"day": "2026-09-15", "seen": True}); G.update(g(False, 90 * 60))
    await tick(day="2026-09-16", utc=UTC + timedelta(hours=21))       # 12:50 по Дубаю, точка была в 11:20
    eq("последняя точка до начала смены — «ни одной точки с начала смены»",
       SENT2, ["📍 *Старший*: геопозиция не видна\nС начала смены не было ни одной точки."])
    ST.clear(); G.update(g(False, None)); await tick(day="2026-09-16", utc=UTC + timedelta(hours=21))
    eq("точек не было вовсе — то же сообщение", SENT2[-1], "📍 *Старший*: геопозиция не видна\nС начала смены не было ни одной точки.")
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
