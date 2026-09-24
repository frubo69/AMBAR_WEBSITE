"""Смена: кто открывает, с кем и когда её можно править.

Правила владельца:
  • 22 сен 2026 — водитель, открывший смену позже 18:00 учётных суток, даёт
    оповещение старшему и операторам района; до 18:00 — тишина;
  • 24 сен 2026 — смену района открывают ТОЛЬКО с бригадой: хотя бы один
    водитель «на смене». До этого район можно было открыть пустым, и тогда
    водители отмечали себя сами — 24-го утром из-за этого вышла каша: новый
    оператор открыл два района без водителей, добавить их в открытую смену не
    смог и закрывал район заново, а четверо тем временем отметились сами;
  • 24 сен 2026 — предлагать можно только тех, кто в Дубае: у кого период
    работы открыт (finance_pay.work_now). Улетевшего нет в списке, и отметить
    его нельзя даже прямым запросом;
  • 24 сен 2026 — бригаду открытой смены оператор правит сколько угодно раз;
    нельзя лишь оставить район без водителей и отправить домой того, кто уже
    открыл свою смену;
  • водитель открывает смену только по отметке оператора (послабление от
    22 сен снято).

Без базы: всё, что пишет и шлёт наружу, подменено.
"""
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
    print("── 18:00 ───────────────────────────────────────────────────────")
    eq("10:30 — вовремя", dr._shift_late(DAY, at(10, 30)), False)
    eq("17:59 — вовремя", dr._shift_late(DAY, at(17, 59)), False)
    eq("18:00 — уже поздно", dr._shift_late(DAY, at(18, 0)), True)
    eq("02:00 ночью (те же учётные сутки) — поздно", dr._shift_late(DAY, at(2, 0, d=23)), True)

    print("── водитель открывает смену ────────────────────────────────────")
    ME = {"name": "Худоба", "district": "jvc"}
    DAYS.clear(); OPENS.clear()
    eq("район не открыт, отметки нет — not_marked", await open_(ME), (409, "not_marked"))
    OPENS["jvc"] = {"drivers": {}}
    eq("район открыт, но его не отметили — всё равно not_marked", await open_(ME), (409, "not_marked"))
    eq("сам себя на смену не записал", DAYS.get("Худоба"), None)
    DAYS["Худоба"] = {"working": False}
    eq("отмечен «дома» — marked_off", await open_(ME), (409, "marked_off"))
    DAYS.clear(); NOW["t"] = at(11, 5)
    DAYS["Худоба"] = {"working": True}
    eq("оператор отметил — открывает", await open_(ME), (200, "ок"))
    eq("смена у него открыта", bool(DAYS["Худоба"].get("shift_open_at")), True)
    eq("пометки «отметился сам» больше нет", DAYS["Худоба"].get("self_marked"), None)
    eq("в бригаду района приложение водителя не пишет", CREW_ADD, [])
    eq("до 18:00 — никаких оповещений", (SENT["owners"], SENT["ops"]), ([], []))

    DAYS.clear(); CREW_ADD.clear(); NOW["t"] = at(19, 20)
    DAYS["Али"] = {"working": True}
    eq("отмеченный открывает в 19:20", await open_({"name": "Али", "district": "jvc"}), (200, "ок"))
    eq("отмеченного в бригаду второй раз не пишем", CREW_ADD, [])
    eq("старшему — событие driver.late_shift, с именем, районом и временем",
       (len(SENT["owners"]), SENT["owners"][0][0], "Али" in SENT["owners"][0][1], "19:20" in SENT["owners"][0][1],
        "B1" in SENT["owners"][0][1], "18:00" in SENT["owners"][0][1]),
       (1, "driver.late_shift", True, True, True, True))
    eq("операторам района — то же", (len(SENT["ops"]), SENT["ops"][0][0], "Али" in SENT["ops"][0][1], "19:20" in SENT["ops"][0][1]),
       (1, "jvc", True, True))
    SENT["owners"].clear(); SENT["ops"].clear()
    NOW["t"] = at(1, 10, d=23)
    DAYS["Файзуло"] = {"working": True}
    eq("ночью отмеченный открыл", await open_({"name": "Файзуло", "district": "jvc"}), (200, "ок"))
    eq("ночью — тоже поздно: оповещения ушли", (len(SENT["owners"]), len(SENT["ops"])), (1, 1))
    SENT["owners"].clear(); SENT["ops"].clear()
    eq("тест-водитель ночью — открыл", await open_({"name": "Тест-водитель", "district": "", "test": True}), (200, "ок"))
    eq("тест-водитель — без оповещений", (SENT["owners"], SENT["ops"]), ([], []))

    print("── кто сегодня в Дубае ─────────────────────────────────────────")
    ЛЮДИ = [
        {"_id": "Али", "work": [{"from": "2026-01-01", "to": ""}]},            # работает
        {"_id": "Файзуло", "work": [{"from": "2026-01-01", "to": DAY}]},       # сегодня последний день
        {"_id": "Улетел", "work": [{"from": "2026-01-01", "to": "2026-09-01"}]},
        {"_id": "Вернулся", "work": [{"from": "2026-01-01", "to": "2026-08-01"},
                                     {"from": "2026-09-20", "to": ""}]},
    ]                                                                          # «Худоба» — без периодов вовсе
    async def people(): return [dict(x) for x in ЛЮДИ]
    opr.db.fin_people_get = people
    opr._staff_mod.DISTRICT_DRIVERS = {"jvc": ["Худоба", "Али", "Файзуло", "Улетел", "Вернулся"],
                                       "bbay": ["Парвиз"]}
    opr._here_drop()
    eq("улетевшего не предлагаем, остальных — да",
       await opr._crew_names("jvc", DAY), ["Худоба", "Али", "Файзуло", "Вернулся"])
    eq("последний день работы — ещё в списке", "Файзуло" in await opr._crew_names("jvc", DAY), True)
    eq("периодов нет — человек на месте", "Худоба" in await opr._crew_names("jvc", DAY), True)
    eq("уже отмеченного не прячем, даже если он улетел",
       await opr._crew_names("jvc", DAY, keep={"Улетел"}),
       ["Худоба", "Али", "Файзуло", "Улетел", "Вернулся"])

    print("── оператор открывает смену района ─────────────────────────────")
    SAVED, TOLD, SHIFT, OPENS_OP, DDAYS = {}, [], {}, {}, []
    async def districts(): return [{"id": "jvc", "code": "B1", "name": "JVC", "operator": "Умар"},
                                   {"id": "bbay", "code": "B2", "name": "Бизнес Бей", "operator": "Умар"}]
    async def shift_open(day, oid, doc):
        if oid in OPENS_OP: return False
        SHIFT.update(doc); OPENS_OP[oid] = {"drivers": dict(doc.get("drivers") or {})}; return True
    async def save(day, name, fields): SAVED.setdefault(name, {}).update(fields)
    async def tell(oid, names, crew, who): TOLD.extend(names)
    async def opens_op(day): return {k: dict(v) for k, v in OPENS_OP.items()}
    async def crew_set(day, oid, drivers):
        if oid not in OPENS_OP: return False
        OPENS_OP[oid]["drivers"] = dict(drivers); return True
    async def ddays(day): return [dict(x) for x in DDAYS]
    opr._fresh_districts = districts
    opr._people_for = lambda req, ds: {}
    opr._scope = lambda people_, who, ds: {"jvc"}          # bbay — чужой район
    opr.db.shift_open = shift_open; opr.db.save_driver_day = save
    opr.db.shift_opens_for_day = opens_op; opr.db.shift_crew_set = crew_set
    opr.db.get_driver_days = ddays
    opr._opens_drop = lambda: None; opr._tell_crew = tell

    async def открыть(drivers, oid="jvc"):
        r = make_mocked_request("POST", "/api/operator/shift/open")
        r._read_bytes = json.dumps({"district": oid, "as": "Умар", "drivers": drivers}).encode()
        resp = await unwrap(opr.handle_shift_open)(r)
        return resp.status, json.loads(resp.text)

    async def бригада(drivers, oid="jvc"):
        r = make_mocked_request("POST", "/api/operator/shift/crew")
        r._read_bytes = json.dumps({"district": oid, "as": "Умар", "drivers": drivers}).encode()
        resp = await unwrap(opr.handle_shift_crew)(r)
        return resp.status, json.loads(resp.text)

    st, b = await открыть({})
    eq("без бригады смену не открыть", (st, b.get("error")), (400, "no_crew"))
    eq("и район остался закрытым", OPENS_OP, {})
    st, b = await открыть({"Али": False, "Файзуло": False})
    eq("все «дома» — это тоже не бригада", (st, b.get("error")), (400, "no_crew"))
    st, b = await открыть({"Улетел": True})
    eq("улетевшим смену не открыть", (st, b.get("error")), (400, "no_crew"))
    st, b = await открыть({"Али": True, "Файзуло": False, "Улетел": True})
    eq("хотя бы один вышел — открыта", (st, b.get("ok")), (200, True))
    eq("в бригаде только те, кто в Дубае", SHIFT.get("drivers"), {"Али": True, "Файзуло": False})
    eq("день проставлен им же", SAVED, {"Али": {"working": True}, "Файзуло": {"working": False}})
    eq("сообщение — им же", sorted(TOLD), ["Али", "Файзуло"])
    eq("в ответе — кого не отметили", b.get("unmarked"), ["Худоба", "Вернулся"])
    st, b = await открыть({"Али": True})
    eq("второй раз не открыть", (st, b.get("error")), (409, "already_open"))

    print("── бригада открытой смены ──────────────────────────────────────")
    SAVED.clear(); TOLD.clear()
    st, b = await бригада({"Худоба": True})
    eq("вышедшего позже добавили", (st, b.get("ok"), b["drivers"].get("Худоба")), (200, True, True))
    eq("у него рабочий день", SAVED.get("Худоба"), {"working": True})
    eq("и ему сказали", TOLD, ["Худоба"])
    eq("остальные на месте", OPENS_OP["jvc"]["drivers"], {"Али": True, "Файзуло": False, "Худоба": True})
    SAVED.clear(); TOLD.clear()
    st, b = await бригада({"Худоба": True})
    eq("то же значение второй раз — ничего не меняем", (st, b.get("changed"), SAVED, TOLD), (200, [], {}, []))
    st, b = await бригада({"Худоба": False})
    eq("и убрать можно", (st, b["drivers"].get("Худоба")), (200, False))
    eq("день стал выходным", SAVED.get("Худоба"), {"working": False})
    st, b = await бригада({"Вернулся": True, "Файзуло": True})
    eq("двоих разом", (st, sorted(b.get("changed"))), (200, ["Вернулся", "Файзуло"]))
    st, b = await бригада({"Улетел": True})
    eq("улетевшего добавить нельзя — правок нет", (st, b.get("changed")), (200, []))
    st, b = await бригада({"Али": False, "Вернулся": False, "Файзуло": False})
    eq("совсем без водителей оставить нельзя", (st, b.get("error")), (409, "last_driver"))
    eq("бригада не тронута", OPENS_OP["jvc"]["drivers"],
       {"Али": True, "Файзуло": True, "Худоба": False, "Вернулся": True})

    DDAYS.append({"driver": "Али", "shift_open_at": "2026-09-22T09:00:00"})
    st, b = await бригада({"Али": False})
    eq("того, кто открыл смену, домой не отправить", (st, b.get("error"), b.get("drivers")),
       (409, "shift_open", ["Али"]))
    DDAYS[0]["shift_close_at"] = "2026-09-22T20:00:00"
    st, b = await бригада({"Али": False})
    eq("закрывшего смену — можно", (st, b["drivers"].get("Али")), (200, False))

    print("── край: в районе никого нет в Дубае ───────────────────────────")
    opr._staff_mod.DISTRICT_DRIVERS["tecom"] = ["Улетел"]
    opr._scope = lambda people_, who, ds: {"jvc", "tecom"}
    opr._here_drop()
    eq("отмечать некого", await opr._crew_names("tecom", DAY), [])
    st, b = await открыть({"Улетел": True}, oid="tecom")
    eq("и смену такого района не открыть — сперва отметить приезд в «Зарплатах»",
       (st, b.get("error"), b.get("drivers")), (400, "no_crew", []))
    opr._scope = lambda people_, who, ds: {"jvc"}

    st, b = await бригада({"Парвиз": True}, oid="bbay")
    eq("чужой район — отказ", (st, b.get("error")), (403, "not_yours"))
    OPENS_OP.pop("jvc")
    st, b = await бригада({"Али": True})
    eq("неоткрытую смену править нечего", (st, b.get("error")), (409, "not_open"))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
