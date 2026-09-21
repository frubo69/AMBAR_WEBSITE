"""Смена: правило 15:00 и открытие без отметки (владелец, 22 сен 2026).

  • водитель открыл смену позже 15:00 учётных суток (ночью — тоже позже) —
    оповещение старшему (событие driver.late_shift) и операторам района;
    до 15:00 — тишина; тест-водитель — тишина;
  • оператор открывает смену района, отметив не всех или никого: неотмеченных
    не трогаем — ни «на смене», ни «дома», ни питания, ни сообщения;
  • неотмеченный водитель открывает смену сам, если смена района открыта: он
    на смене (питание рабочего дня) и записан в бригаду района; отмеченный
    «дома» — отказ marked_off; район не открыт — отказ not_marked, как раньше.
Без базы: всё, что пишет и шлёт наружу, подменено."""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from datetime import datetime, timezone, timedelta
from aiohttp.test_utils import make_mocked_request
import driver_routes as dr
import operator_routes as opr
import geo_watch

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

DUBAI = timezone(timedelta(hours=4))
DAY = "2026-09-22"
at = lambda h, m, d=22: datetime(2026, 9, d, h, m, tzinfo=DUBAI).astimezone(timezone.utc)

# ── подмены: день водителя, смена района, оповещения ────────────────────────
DAYS, OPENS, CREW_ADD, SENT = {}, {}, [], {"owners": [], "ops": []}
async def gdd(day, name): return dict(DAYS.get(name) or {})
async def sdd(day, name, fields): DAYS.setdefault(name, {}).update(fields)
async def opens(day): return dict(OPENS)
async def crew_add(day, district, name): CREW_ADD.append((day, district, name))
async def geo(me, since=None): return {"ok": True, "left_min": 60}
async def none(*a, **k): return None
dr.db.get_driver_day = gdd; dr.db.save_driver_day = sdd; dr.db.shift_opens_for_day = opens
dr.db.shift_crew_add = crew_add
dr._geo_for = geo; dr._after_close = none
dr._biz_day = lambda *a, **k: DAY
async def view(me): return {"ok": True}
dr._shift_view = view                         # экран смены проверяют другие тесты
fake_owner = types.ModuleType("owner_routes")
async def notify_owners(key, text, **k): SENT["owners"].append((key, text))
fake_owner.notify_owners = notify_owners
fake_op = types.ModuleType("op_route")
async def op_send(text, district="", **k): SENT["ops"].append((district, text))
fake_op.send = op_send
sys.modules["owner_routes"] = fake_owner; sys.modules["op_route"] = fake_op
NOW = {"t": at(11, 0)}
_real_dt = dr.datetime
class _DT(_real_dt):
    @classmethod
    def now(cls, tz=None): return NOW["t"] if tz else NOW["t"].replace(tzinfo=None)
dr.datetime = _DT


def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h


async def open_(me):
    r = make_mocked_request("POST", "/x"); r["driver"] = dict(me)
    resp = await unwrap(dr.handle_shift_open)(r)
    return resp.status, json.loads(resp.text).get("error") or "ок"


async def main():
    print("── 15:00 ───────────────────────────────────────────────────────")
    eq("10:30 — вовремя", dr._shift_late(DAY, at(10, 30)), False)
    eq("14:59 — вовремя", dr._shift_late(DAY, at(14, 59)), False)
    eq("15:00 — уже поздно", dr._shift_late(DAY, at(15, 0)), True)
    eq("02:00 ночью (те же учётные сутки) — поздно", dr._shift_late(DAY, at(2, 0, d=23)), True)

    print("── водитель открывает смену ────────────────────────────────────")
    ME = {"name": "Худоба", "district": "jvc"}
    DAYS.clear(); OPENS.clear()
    eq("район не открыт, отметки нет — not_marked, как раньше", await open_(ME), (409, "not_marked"))
    OPENS["jvc"] = {"drivers": {}}
    DAYS["Худоба"] = {"working": False}
    eq("отмечен «дома» — marked_off", await open_(ME), (409, "marked_off"))
    DAYS.clear(); NOW["t"] = at(11, 5)
    eq("район открыт, отметки нет — открыл сам", await open_(ME), (200, "ок"))
    eq("он на смене: питание рабочего дня, пометка «сам»",
       (DAYS["Худоба"].get("working"), DAYS["Худоба"].get("self_marked"), bool(DAYS["Худоба"].get("shift_open_at"))),
       (True, True, True))
    eq("записан в бригаду района", CREW_ADD, [(DAY, "jvc", "Худоба")])
    eq("до 15:00 — никаких оповещений", (SENT["owners"], SENT["ops"]), ([], []))

    DAYS.clear(); CREW_ADD.clear(); NOW["t"] = at(16, 20)
    DAYS["Али"] = {"working": True}
    eq("отмеченный открывает в 16:20", await open_({"name": "Али", "district": "jvc"}), (200, "ок"))
    eq("отмеченного в бригаду второй раз не пишем", CREW_ADD, [])
    eq("старшему — событие driver.late_shift, с именем, районом и временем",
       (len(SENT["owners"]), SENT["owners"][0][0], "Али" in SENT["owners"][0][1], "16:20" in SENT["owners"][0][1],
        "B1" in SENT["owners"][0][1], "15:00" in SENT["owners"][0][1]),
       (1, "driver.late_shift", True, True, True, True))
    eq("операторам района — то же", (len(SENT["ops"]), SENT["ops"][0][0], "Али" in SENT["ops"][0][1], "16:20" in SENT["ops"][0][1]),
       (1, "jvc", True, True))
    SENT["owners"].clear(); SENT["ops"].clear()
    NOW["t"] = at(1, 10, d=23)
    eq("ночью неотмеченный открыл сам", await open_({"name": "Файзуло", "district": "jvc"}), (200, "ок"))
    eq("ночью — тоже поздно: оповещения ушли", (len(SENT["owners"]), len(SENT["ops"])), (1, 1))
    SENT["owners"].clear(); SENT["ops"].clear()
    eq("тест-водитель ночью — открыл", await open_({"name": "Тест-водитель", "district": "", "test": True}), (200, "ок"))
    eq("тест-водитель — без оповещений", (SENT["owners"], SENT["ops"]), ([], []))

    print("── оператор открывает смену района ─────────────────────────────")
    SAVED, TOLD, SHIFT = {}, [], {}
    async def districts(): return [{"id": "jvc", "code": "B1", "name": "JVC", "operator": "Умар"}]
    async def shift_open(day, oid, doc): SHIFT.update(doc); return True
    async def save(day, name, fields): SAVED[name] = fields
    async def tell(oid, names, crew, who): TOLD.extend(names)
    opr._fresh_districts = districts
    opr._people_for = lambda req, ds: {}
    opr._scope = lambda people, who, ds: {"jvc"}
    opr._staff_mod.DISTRICT_DRIVERS = {"jvc": ["Худоба", "Али", "Файзуло"]}
    opr.db.shift_open = shift_open; opr.db.save_driver_day = save
    opr._opens_drop = lambda: None; opr._tell_crew = tell
    r = make_mocked_request("POST", "/api/operator/shift/open")
    r._read_bytes = json.dumps({"district": "jvc", "as": "Умар", "drivers": {"Али": True, "Файзуло": False}}).encode()
    resp = await unwrap(opr.handle_shift_open)(r)
    body = json.loads(resp.text)
    eq("отметил не всех — смена открыта", (resp.status, body.get("ok")), (200, True))
    eq("в смене района только отмеченные", SHIFT.get("drivers"), {"Али": True, "Файзуло": False})
    eq("день: отмечены двое, Худобу (в отъезде) не трогаем", SAVED, {"Али": {"working": True}, "Файзуло": {"working": False}})
    eq("сообщение — только отмеченным", sorted(TOLD), ["Али", "Файзуло"])
    eq("в ответе — кто не отмечен", body.get("unmarked"), ["Худоба"])
    SAVED.clear(); TOLD.clear(); SHIFT.clear()
    r = make_mocked_request("POST", "/api/operator/shift/open")
    r._read_bytes = json.dumps({"district": "jvc", "as": "Умар", "drivers": {}}).encode()
    resp = await unwrap(opr.handle_shift_open)(r)
    eq("никого не отметил — тоже открыта, никого не трогали",
       (resp.status, SHIFT.get("drivers"), SAVED, TOLD), (200, {}, {}, []))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
