"""Стояние и «пропал» по правилу владельца (11 сен 2026): пока трансляция
идёт, человек на связи; пропал — только без движения два часа подряд.

Проверяем без Mongo: якорь стояния (db.geo_moved), состояние для панели
(operator_routes.drivers_live) и сторожа (driver_routes._geo_state), сам
проход сторожа по старшему и водителю (geo_watch.tick) с подменённой базой:
точка из кармана раз в 3 мин на одном месте → ни одного сообщения; два часа
на месте → одно сообщение «два часа без движения»; поехал → «снова в
движении». Запуск: python3 tools/test_geo_still.py"""
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
    POS["G"] = pos(1, 16 * 60)     # вчерашний якорь: стояние считается с полудня (12:00 Дубай = 08:00 UTC)
    rows = {r["driver"]: r for r in (await operator_routes.drivers_live(list(POS), D))["drivers"]}
    eq("G: still = 8 ч с полудня, lost", (rows["G"]["still"] // 3600, rows["G"]["lost"]), (8, True))
    g = await driver_routes._geo_state("A")
    eq("сторож A: fresh=True (3 мин < 15), watch_ok", (g["fresh"], g["watch_ok"], g["lost"]), (True, True, False))
    g = await driver_routes._geo_state("B")
    eq("сторож B: not fresh, lost, watch_ok=False", (g["fresh"], g["lost"], g["watch_ok"]), (False, True, False))
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
async def _owners(text, event, reply_markup=None, exclude=None): SENT.append((event, text)); return 1
async def _driver(name, text): SENT.append(("driver:" + name, text))
for n, f in dict(geo_watch_get=geo_watch_get, geo_watch_set=geo_watch_set, get_driver_days=get_driver_days,
                 get_driver_day=get_driver_day, staff_map_get=staff_map_get, driver_map_get=driver_map_get,
                 geo_lock_set=geo_lock_set).items():
    setattr(db, n, f)
geo_watch._owners = _owners
geo_watch._driver = _driver
staff.apply_moves = lambda *a, **k: None
staff.SENIOR_STAR_IDS = {"Старший": 1}
staff.DRIVER_IDS = {"Али": 2}
geo_watch._STARTED = None

async def watch():
    global NOW
    print("— проход сторожа: старший в кармане раз в 3 мин на одном месте")
    POS.clear(); WATCH.clear(); DAYS.clear(); SENT.clear()
    DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)   # 20:00 Дубай — рабочее время
    for k in range(0, 100, 3):                                   # 100 минут стоянки, точка раз в 3 мин
        NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=k)
        POS["op:Старший"] = pos(2, k + 5)
        POS["Али"] = pos(2, k + 5)
        await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("за 100 мин стоянки — ни одного сообщения", [s for s in SENT if "без движения" in s[1] or "не видн" in s[1]], [])
    NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=121)
    POS["op:Старший"] = pos(2, 126); POS["Али"] = pos(2, 126)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("старший с точками на базе — виден и через 2 ч", out.get("senior_off"), None)
    eq("водитель off (still)", out.get("off"), ["Али"])
    texts = [t for _, t in SENT]
    eq("о старшем — ни слова", any("Старш" in t for t in texts), False)
    eq("в текстах нет «точек нет» и «потеряно»", any("точек нет" in t or "потерян" in t or "не видна" in t for t in texts), False)
    eq("текст водителя: «На одном месте с 18:0…»", any("На одном месте с" in t and "Али" in t for t in texts), True)
    n = len(SENT)
    NOW += timedelta(minutes=3); POS["op:Старший"] = pos(1, 129); POS["Али"] = pos(1, 129)
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("ещё стоит → повторов нет", len(SENT), n)
    NOW += timedelta(minutes=5); POS["op:Старший"] = pos(0, 0); POS["Али"] = pos(0, 0)   # поехали
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("поехал → водитель back", (out.get("senior_on"), out.get("back")), (None, ["Али"]))
    texts = [t for _, t in SENT[n:]]
    eq("тексты «снова в движении · Стоял …»", all("снова в движении" in t and "Стоял" in t for t in texts), True)
    print("— выключенная трансляция по-прежнему ловится")
    SENT.clear(); WATCH.clear()
    POS["Али"] = pos(30, 30, until="")
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("водитель без трансляции → off (stream)", out.get("off"), ["Али"])
    eq("текст: трансляция выключена", any("трансляция геопозиции выключена" in t for _, t in SENT), True)
    print("— стояние не запирает: закрыл смену стоя — тихо снимается")
    SENT.clear(); WATCH.clear()
    POS["Али"] = pos(2, 150)
    DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("сначала off (still)", (WATCH.get("Али") or {}).get("off_why"), "still")
    DAYS["Али"]["shift_close_at"] = "y"
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("замка нет, метка снята", (out.get("locked"), "off_since" in (WATCH.get("Али") or {})), ([], False))
    print("— выключил трансляцию и не включил — замка нет, пропажа снимается тихо")
    SENT.clear(); WATCH.clear(); DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    POS["Али"] = pos(30, 30, until="")
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    DAYS["Али"]["shift_close_at"] = "y"
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("не заперт, метка снята", (out.get("locked"), "off_since" in (WATCH.get("Али") or {})), ([], False))
    eq("в текстах нет «закроется»", any("закро" in t for _, t in SENT), False)
    print("— «Стоял N» и «Без движения с» — от якоря, не от минуты обнаружения")
    SENT.clear(); WATCH.clear(); DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)
    POS["Али"] = pos(2, 126)                                   # якорь 13:54 UTC
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    since = (WATCH["Али"]["off_since"] - (NOW - timedelta(minutes=126))).total_seconds()
    eq("off_since = якорь (±1 с)", abs(since) < 1.5, True)
    eq("текст: На одном месте с 17:54 (Дубай)", any("На одном месте с 17:54" in t for _, t in SENT), True)
    NOW += timedelta(minutes=8); POS["Али"] = pos(0, 0)
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("снова в движении · Стоял 2 ч 14 мин", any("Стоял 2 ч 14 мин" in t for _, t in SENT), True)
    print("— слепота: трое стоят с редкими точками, один 3 ч — ловится, а не глушит")
    SENT.clear(); WATCH.clear()
    staff.DRIVER_IDS = {"А": 1, "Б": 2, "В": 3}
    for n, (age, mv) in {"А": (16, 30), "Б": (17, 40), "В": (16, 180)}.items():
        DAYS[n] = {"working": True, "shift_open_at": "x"}; POS[n] = pos(age, mv)
    DAYS.pop("Али", None)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("не слепой, off=[В]", (out.get("blind"), out.get("off")), (None, ["В"]))
    print("— база лежит (ни у кого ни точки, ни трансляции) — по-прежнему молчим")
    SENT.clear(); WATCH.clear()
    for n in ("А", "Б", "В"): POS[n] = pos(40, 40, until="")
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("blind", out.get("blind"), True)
    print("— стояние до открытия смены не в счёт")
    SENT.clear(); WATCH.clear(); staff.DRIVER_IDS = {"Али": 2}
    for n in ("А", "Б", "В"): DAYS.pop(n, None); POS.pop(n, None)
    DAYS["Али"] = {"working": True, "shift_open_at": NOW - timedelta(minutes=10)}
    POS["Али"] = pos(1, 180)                                   # дома 3 ч, смену открыл 10 мин назад
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("off нет", out.get("off"), [])
    print("— перезапуск трансляции стоя не даёт «снова идёт»")
    SENT.clear(); WATCH.clear(); DAYS["Али"] = {"working": True, "shift_open_at": "x"}
    POS["Али"] = pos(2, 150)
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    n = len(SENT)
    r = await geo_watch.on_stream("Али", True, NOW)
    eq("on_stream(on) при still — молчит, метка на месте", (r, len(SENT) == n, WATCH["Али"].get("off_why")), (False, True, "still"))
    r = await geo_watch.on_stream("Али", False, NOW + timedelta(minutes=5))
    eq("выключил после стояния — why=stream, off_since = минута выключения",
       (WATCH["Али"].get("off_why"), WATCH["Али"].get("off_since") == NOW + timedelta(minutes=5)), ("stream", True))
    print("— старший: часами на базе с точками — виден; два часа без единой точки — сообщение")
    SENT.clear(); WATCH.clear(); DAYS.clear()
    for k in range(0, 200, 3):
        NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=k)
        POS["op:Старший"] = pos(2, k + 5)
        await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("3 ч 20 мин на базе с точками — ни одного сообщения о старшем", [t for _, t in SENT if "Старш" in t], [])
    NOW += timedelta(minutes=125); POS["op:Старший"] = pos(125, 330)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("2 ч 05 мин без точек → senior_off (silent)", (out.get("senior_off"), WATCH["op:Старший"].get("off_why")), (["Старший"], "silent"))
    eq("текст: «телефон два часа не присылает точку»", any("телефон два часа не присылает точку" in t for _, t in SENT), True)
    eq("off_since = последняя точка", WATCH["op:Старший"]["off_since"] == NOW - timedelta(minutes=125), True)
    NOW += timedelta(minutes=3); POS["op:Старший"] = pos(0, 333)
    out = await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("точка пришла → «точки снова идут · Не было 2 ч 8 мин»", any("точки снова идут" in t and "2 ч 8 мин" in t for _, t in SENT), True)
    print("— старший только из панели: трансляции нет — «геопозиция не видна», без слова о трансляции")
    SENT.clear(); WATCH.clear()
    WATCH["op:Старший"] = {"day": D, "seen": True}
    POS["op:Старший"] = pos(20, 20, until="")
    await geo_watch.tick(NOW.astimezone(geo_watch.DUBAI_TZ))
    eq("текст без «трансляция кончилась»", any("геопозиция не видна" in t and "кончилась" not in t for _, t in SENT), True)

async def main():
    await states(); await watch()
    print()
    print("FAILED:", fails) if fails else print("ALL OK — стоянка не пропажа, пропажа только через два часа без движения")
    sys.exit(1 if fails else 0)
asyncio.run(main())
