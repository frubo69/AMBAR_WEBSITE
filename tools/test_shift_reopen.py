"""Закрытая смена не открывается заново: пока оператор не откроет следующую,
водителю только итоги (владелец, 15 сен 2026). Без базы."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from datetime import datetime, timezone
from aiohttp.test_utils import make_mocked_request
import driver_routes as dr
import geo_watch
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

T = datetime(2026, 9, 15, 18, 40, tzinfo=timezone.utc)
D, LAST, OPENED, ASKED = {}, {}, {"v": False}, {}
async def gdd(day, name): ASKED["day"] = day; return dict(D)
async def sdd(day, name, fields): D.update(fields)
async def sfd(day): return {"jvc": {"closed_at": "x"}}
async def last_closed(name, before, days=14): ASKED["last"] = (name, before); return dict(LAST) if LAST else None
async def opened_after(at, district=""): ASKED["after"] = (at, district); return OPENED["v"]
async def geo(me, since=None): return {"ok": True, "fresh": True, "stream": True, "watch_ok": True, "left_min": 60, "endless": False}
async def none(*a, **k): return []
async def link(): return ""
dr.db.get_driver_day = gdd; dr.db.save_driver_day = sdd; dr.db.shifts_for_day = sfd
dr.db.driver_last_closed = last_closed; dr.db.shift_opened_after = opened_after
dr.db.get_orders_in_range = none; dr.db.writeoff_list = none
dr._geo_for = geo; dr._in_route = none; geo_watch.geo_bot_link = link
dr._biz_day = lambda *a, **k: "2026-09-15"
ME = {"name": "Худоба", "district": "jvc"}
def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h
def req(method="POST", path="/x", me=None):
    r = make_mocked_request(method, path); r["driver"] = dict(me or ME); return r
async def open_(me=None):
    resp = await unwrap(dr.handle_shift_open)(req(me=me)); return resp.status, json.loads(resp.text).get("error") or "ок"

async def main():
    # 1. Сегодня закрыл, новой смены нет
    D.clear(); D.update({"working": True, "shift_open_at": T, "shift_close_at": T}); LAST.clear(); OPENED["v"] = False
    v = await dr._shift_view(ME)
    eq("закрыта сегодня → after_close, итоги за сегодня, открыть нельзя",
       (v["after_close"], v["report_day"], v["can_open"], v["closed"], bool(v["report_closed_at"])),
       (True, "2026-09-15", False, True, True))
    eq("спросили: открывал ли оператор район после момента закрытия", ASKED["after"], (T, "jvc"))
    eq("открыть снова → 409 after_close", await open_(), (409, "after_close"))
    # 2. Сегодня закрыл, но оператор уже открыл новую (после закрытия)
    OPENED["v"] = True
    v = await dr._shift_view(ME)
    eq("оператор открыл новую → запрета нет, открыть можно", (v["after_close"], v["report_day"], v["can_open"]), (False, "", True))
    eq("открыть → 200", await open_(), (200, "ок"))
    eq("открытие сняло закрытие", (bool(D["shift_open_at"]), D["shift_close_at"]), (True, None))
    # 3. Сутки сменились: сегодня пусто, вчера закрыл, новой нет
    D.clear(); LAST.update({"day": "2026-09-14", "closed_at": T}); OPENED["v"] = False
    v = await dr._shift_view(ME)
    eq("вчера закрыл, новой нет → итоги за вчера, экран закрытой смены",
       (v["after_close"], v["report_day"], v["opened"], v["closed"], v["can_open"]),
       (True, "2026-09-14", False, False, False))
    eq("последнюю смену искали до сегодняшнего дня", ASKED["last"], ("Худоба", "2026-09-15"))
    eq("открыть → 409 after_close (раньше отметки оператора)", await open_(), (409, "after_close"))
    eq("тест-водитель тоже не откроет", await open_({**ME, "test": True}), (409, "after_close"))
    eq("у тест-водителя спросили про смену в любом районе: в тест-районе её не открывают",
       ASKED["after"][1], "")
    # 4. Сутки сменились, оператор открыл новую смену
    OPENED["v"] = True
    v = await dr._shift_view(ME)
    eq("оператор открыл → обычный экран «не открыта»", (v["after_close"], v["report_day"]), (False, ""))
    eq("без отметки оператора → not_marked, как раньше", await open_(), (409, "not_marked"))
    # 5. Смена идёт
    D.clear(); D.update({"working": True, "shift_open_at": T, "extras": [], "no_expense": {"fuel": True, "wash": True, "parking": True}}); OPENED["v"] = False
    v = await dr._shift_view(ME)
    eq("смена идёт → запрета нет", (v["after_close"], v["can_close"], v["can_open"]), (False, True, False))
    # 6. Закрытие: в ответе сразу after_close
    resp = await unwrap(dr.handle_shift_close)(req()); v = json.loads(resp.text)
    eq("закрыл → в ответе after_close и итоги за сегодня", (resp.status, v["after_close"], v["report_day"], v["can_open"]), (200, True, "2026-09-15", False))
    # 7. Старая запись со строкой вместо даты — не запираем (сравнение дат в базе не сработало бы)
    D.clear(); D.update({"shift_open_at": "2026-09-15T10:00:00+00:00", "shift_close_at": "2026-09-15T18:40:00+00:00"})
    v = await dr._shift_view(ME)
    eq("строка ISO в closed_at → сравнили как дату", (v["after_close"], ASKED["after"][0]), (True, datetime(2026, 9, 15, 18, 40, tzinfo=timezone.utc)))
    D.clear(); D.update({"shift_open_at": "x", "shift_close_at": "не дата"})
    v = await dr._shift_view(ME)
    eq("нечитаемый момент закрытия → не запираем", v["after_close"], False)
    # 8. Итоги за другой день и ручка
    D.clear()
    m = await dr._shift_summary(ME, "2026-09-14")
    eq("итоги за указанный день", (m["day"], ASKED["day"], "closed_at" in m), ("2026-09-14", "2026-09-14", True))
    resp = await unwrap(dr.handle_shift_summary)(req("GET", "/x?day=2026-09-14"))
    eq("ручка: ?day=вчера", json.loads(resp.text)["day"], "2026-09-14")
    resp = await unwrap(dr.handle_shift_summary)(req("GET", "/x?day=2027-01-01"))
    eq("ручка: день из будущего → сегодня", json.loads(resp.text)["day"], "2026-09-15")
    resp = await unwrap(dr.handle_shift_summary)(req("GET", "/x?day=abc"))
    eq("ручка: мусор → сегодня", json.loads(resp.text)["day"], "2026-09-15")
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
