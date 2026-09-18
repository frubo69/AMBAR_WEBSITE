"""Геопозиция: два состояния и только два сообщения (владелец, 19 сен 2026:
«присылай только сообщения о том, что водители включили/выключили геопозицию,
больше не надо „снова в движении“ или „на месте 3 ч“»; «водитель, даже если на
месте, геолокацию не отключал — он в сети»).

Проверяем без Mongo: якорь стояния (db.geo_moved), состояние для панели
(operator_routes.drivers_live: online = идёт трансляция, off_at — когда
выключил) и сторожа (driver_routes._geo_state: watch_ok = идёт трансляция),
сам проход сторожа (geo_watch.tick) и мгновенный путь (on_stream) с
подменённой базой: стоит сколько угодно — ни одного сообщения; поехал — тоже;
выключил — «выключил геопозицию» с водителем и районом для «Событий»
оператора; включил — «включил геопозицию · Не было N»; проход ловит только
выключенную трансляцию, о которой не сказал телеграм; про старшего проход
не пишет вовсе. Запуск: python3 tools/test_geo_still.py"""
import asyncio, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from datetime import datetime, timedelta, timezone
import db, driver_routes, operator_routes, geo_watch, config_staff as staff

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<58} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

# ── якорь ──
print("— якорь стояния (db.geo_moved)")
D = "2026-09-11"
eq("нет якоря → переезд", db.geo_moved(None, 25.1, 55.1, 10, D), True)
prev = {"mv_lat": 25.1, "mv_lon": 55.1, "mv_at": 1, "day": D}
eq("30 м от якоря → стоит", db.geo_moved(prev, 25.10027, 55.1, 10, D), False)
eq("400 м от якоря → поехал", db.geo_moved(prev, 25.1036, 55.1, 10, D), True)
eq("400 м, но точность 900 м → стоит (дрожь)", db.geo_moved(prev, 25.1036, 55.1, 900, D), False)
eq("другой учётный день → переезд", db.geo_moved(prev, 25.1, 55.1, 10, "2026-09-12"), True)
eq("acc None → радиус 150", db.geo_moved(prev, 25.1016, 55.1, None, D), True)
eq("acc 1500 м, ушёл на 600 м → поехал (потолок круга 500)", db.geo_moved(prev, 25.1054, 55.1, 1500, D), True)

# ── состояние по документу ──
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)
FAR = NOW + timedelta(days=365 * 60)
POS = {}
async def driver_pos_all(names=None):
    return [dict(POS[n], driver=n) for n in (names or list(POS)) if n in POS]
db.driver_pos_all = driver_pos_all
class _FakeDT(datetime):
    @classmethod
    def now(cls, tz=None): return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
driver_routes.datetime = _FakeDT
operator_routes.datetime = _FakeDT
async def _orders_by_driver(day): return {}
operator_routes._orders_by_driver = _orders_by_driver
async def panic_all(): return []
db.panic_all = panic_all

def pos(at_min_ago, mv_min_ago, until=FAR):
    return {"lat": 25.1, "lon": 55.1, "at": NOW - timedelta(minutes=at_min_ago),
            "mv_at": NOW - timedelta(minutes=mv_min_ago), "until": until, "day": D}

async def states():
    print("— состояние для панели и сторожа")
    POS.clear()
    POS["A"] = pos(3, 40)          # точка 3 мин назад, стоит 40 мин, трансляция идёт
    POS["B"] = pos(25, 130)        # точка 25 мин назад, стоит 2 ч 10 мин
    POS["C"] = pos(1, 1)           # едет
    POS["E"] = pos(5, 5, until="")  # разовая точка из приложения, 5 мин назад
    POS["F"] = pos(0, 119)         # 1 ч 59 мин на месте — ещё на связи
    rows = {r["driver"]: r for r in (await operator_routes.drivers_live(list(POS), D))["drivers"]}
    eq("A: live=False (точка 3 мин), stream, не lost, still 40 мин",
       (rows["A"]["live"], rows["A"]["stream"], rows["A"]["lost"], rows["A"]["still"] // 60), (False, True, False, 40))
    eq("B: lost", rows["B"]["lost"], True)
    eq("C: едет, still 1 мин", (rows["C"]["lost"], rows["C"]["still"] // 60), (False, 1))
    eq("E: без трансляции lost=False (разовая точка)", (rows["E"]["stream"], rows["E"]["lost"]), (False, False))
    eq("F: 119 мин — не lost", rows["F"]["lost"], False)
    POS["G"] = pos(1, 16 * 60)     # вчерашний якорь: стояние считается с начала суток (10:00 Дубай = 06:00 UTC с 16 сен)
    rows = {r["driver"]: r for r in (await operator_routes.drivers_live(list(POS), D))["drivers"]}
    eq("G: still = 10 ч с начала суток, lost", (rows["G"]["still"] // 3600, rows["G"]["lost"]), (10, True))
    eq("в сети — у всех с трансляцией, сколько бы ни стояли (A, B, C, F, G)",
       sorted(n for n, r in rows.items() if r["online"]), ["A", "B", "C", "F", "G"])
    eq("E: разовая точка из приложения без трансляции — геолокация выключена", rows["E"]["online"], False)
    POS["H"] = dict(pos(30, 30, until=""), stopped_at=NOW - timedelta(minutes=12))
    POS["I"] = dict(pos(30, 30, until=""), stopped_at=NOW - timedelta(days=2))
    rows = {r["driver"]: r for r in (await operator_routes.drivers_live(list(POS), D))["drivers"]}
    eq("H: выключил 12 мин назад — off_at есть", (rows["H"]["online"], bool(rows["H"]["off_at"])), (False, True))
    eq("I: выключал позавчера — off_at пустой (сегодня не включал)", rows["I"]["off_at"], "")
    rows = {r["driver"]: r for r in (await operator_routes.drivers_live(["Никто"], D))["drivers"]}
    eq("нет ни одной точки — не в сети", (rows["Никто"]["has"], rows["Никто"]["online"]), (False, False))
    g = await driver_routes._geo_state("A")
    eq("сторож A: fresh=True (3 мин < 15), watch_ok", (g["fresh"], g["watch_ok"], g["lost"]), (True, True, False))
    g = await driver_routes._geo_state("B")
    eq("сторож B: стоит 2 ч 10 мин — lost по якорю, но в сети: watch_ok", (g["fresh"], g["lost"], g["watch_ok"]), (False, True, True))
    g = await driver_routes._geo_state("F")
    eq("сторож F: 119 мин на месте, точка 0 мин → ok", (g["ok"], g["watch_ok"]), (True, True))

# ── проход сторожа ──
SENT = []
WATCH = {}
DAYS = {}
async def geo_watch_get(key): return dict(WATCH.get(key) or {})
async def geo_watch_set(key, fields, unset=None):
    d = WATCH.setdefault(key, {}); d.update(fields)
    for k in (unset or []): d.pop(k, None)
async def get_driver_days(day): return [dict(v, driver=k) for k, v in DAYS.items()]
async def get_driver_day(day, name): return DAYS.get(name)
async def staff_map_get(): return {}
async def driver_map_get(): return {}
async def geo_lock_set(name, at, why): WATCH.setdefault(name, {})["locked_at"] = at; return "k"
META = []
async def _owners(text, event, reply_markup=None, exclude=None, meta=None):
    SENT.append((event, text)); META.append(meta); return 1
async def _driver(name, text): SENT.append(("driver:" + name, text))
for n, f in dict(geo_watch_get=geo_watch_get, geo_watch_set=geo_watch_set, get_driver_days=get_driver_days,
                 get_driver_day=get_driver_day, staff_map_get=staff_map_get, driver_map_get=driver_map_get,
                 geo_lock_set=geo_lock_set).items():
    setattr(db, n, f)
geo_watch._owners = _owners
geo_watch._driver = _driver
staff.apply_moves = lambda *a, **k: None
async def _nosync(*a, **k): return None      # реестр из базы здесь не нужен: водитель задан руками
staff.sync = _nosync
staff.SENIOR_STAR_IDS = {"Старший": 1}
staff.DRIVER_IDS = {"Али": 2}
geo_watch._STARTED = None

async def watch():
    global NOW
    print("— стоит часами — ни одного сообщения; поехал — тоже")
    POS.clear(); WATCH.clear(); DAYS.clear(); SENT.clear(); META.clear()
    DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    staff.DISTRICT_DRIVERS = {"jvc": ["Али"]}
    for k in range(0, 200, 3):                                   # 3 ч 20 мин стоянки, точка раз в 3 мин
        NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=k)
        POS["op:Старший"] = pos(2, k + 5)
        POS["Али"] = pos(2, k + 5)
        await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("3 ч 20 мин на месте — ни одного сообщения", SENT, [])
    NOW += timedelta(minutes=125); POS["Али"] = pos(125, 330); POS["op:Старший"] = pos(125, 330)
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("айфон на месте 2 ч без единой точки, трансляция идёт — тоже тишина (и про старшего)", SENT, [])
    NOW += timedelta(minutes=5); POS["Али"] = pos(0, 0); POS["op:Старший"] = pos(0, 0)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("поехал — «снова в движении» больше нет", (SENT, out.get("back")), ([], []))

    print("— выключил и включил: мгновенный путь")
    r = await geo_watch.on_stream("Али", False, NOW)
    eq("выключил — «выключил геопозицию»", (r, SENT[-1]), (True, (geo_watch.EVENT_OFF,
       "📍 *Али*: выключил геопозицию\nОператор его не видит.")))
    eq("в записи — водитель и район для «Событий» оператора", META[-1],
       {"driver": "Али", "district": "jvc", "on": False, "self": True})
    n = len(SENT)
    await geo_watch.on_stream("Али", False, NOW + timedelta(minutes=1))
    eq("повторный сигнал выключения — без повтора", len(SENT), n)
    POS["Али"] = pos(30, 30, until="")
    await geo_watch.tick((NOW + timedelta(minutes=2)).astimezone(geo_watch.DUBAI_TZ))
    eq("проход о том же выключении не пишет", len(SENT), n)
    r = await geo_watch.on_stream("Али", True, NOW + timedelta(minutes=17))
    eq("включил — «включил геопозицию · Не было 17 мин»", (r, SENT[-1][1]),
       (True, "📍 *Али*: включил геопозицию\nНе было 17 мин."))
    eq("включение — тоже с водителем и районом", META[-1], {"driver": "Али", "district": "jvc", "on": True, "self": True})
    r = await geo_watch.on_stream("Али", True, NOW + timedelta(minutes=18))
    eq("включил без выключения (перезапуск) — просто «включил»", SENT[-1][1], "📍 *Али*: включил геопозицию")

    print("— проход ловит только выключение, о котором не сказал телеграм")
    SENT.clear(); META.clear(); WATCH.clear()
    POS["Али"] = pos(30, 30, until="")
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("трансляции нет — «геопозиция выключена», один раз", (out.get("off"), [t for _, t in SENT]),
       (["Али"], ["📍 *Али*: геопозиция выключена\nОператор его не видит."]))
    eq("не сам (не знаем, он ли) — self False", META[-1]["self"], False)
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("второй проход — без повтора", len(SENT), 1)
    NOW += timedelta(minutes=9); POS["Али"] = pos(0, 0)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("трансляция вернулась — «включил геопозицию · Не было 9 мин»", (out.get("back"), SENT[-1][1]),
       (["Али"], "📍 *Али*: включил геопозицию\nНе было 9 мин."))

    print("— старая отметка «без движения» (до 19 сен) снимается молча")
    SENT.clear(); WATCH.clear()
    WATCH["Али"] = {"day": D, "off_since": NOW - timedelta(hours=3), "off_why": "still"}
    POS["Али"] = pos(2, 200)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("снята, сообщений нет", ("off_since" in WATCH["Али"], SENT, out.get("back")), (False, [], []))
    WATCH["Али"] = {"day": D, "off_since": NOW - timedelta(hours=3), "off_why": "still"}
    await geo_watch.on_stream("Али", True, NOW)
    eq("перезапуск трансляции при старой отметке — «включил» без «Не было 3 ч»", SENT[-1][1], "📍 *Али*: включил геопозицию")

    print("— конец смены: выключенная и не вернувшаяся — снимается тихо, замка нет")
    SENT.clear(); WATCH.clear(); DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    POS["Али"] = pos(30, 30, until="")
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    DAYS["Али"]["shift_close_at"] = "y"
    n = len(SENT)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("не заперт, метка снята, без сообщений", (out.get("locked"), "off_since" in (WATCH.get("Али") or {}), len(SENT) - n),
       ([], False, 0))

    print("— слепота: у всех на смене ни точки, ни трансляции — молчим, это наша беда")
    SENT.clear(); WATCH.clear()
    staff.DRIVER_IDS = {"А": 1, "Б": 2, "В": 3}
    for n_ in ("А", "Б", "В"):
        DAYS[n_] = {"working": True, "shift_open_at": "x"}; POS[n_] = pos(40, 40, until="")
    DAYS.pop("Али", None)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("blind, сообщений нет", (out.get("blind"), SENT), (True, []))
    POS["А"] = pos(40, 40)                                    # у одного трансляция идёт — не слепые
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("не слепой: выключены у Б и В", (out.get("blind"), sorted(out.get("off") or [])), (None, ["Б", "В"]))

    print("— в текстах нет «без движения», «снова в движении», «молчит», «не присылает»")
    bad = [t for _, t in SENT if any(w in t for w in ("без движения", "снова в движении", "молчит", "не присылает", "Стоял"))]
    eq("ни одного", bad, [])

async def main():
    await states(); await watch()
    print()
    print("FAILED:", fails) if fails else print("ALL OK — два состояния, сообщения только «включил» и «выключил»")
    sys.exit(1 if fails else 0)
asyncio.run(main())
