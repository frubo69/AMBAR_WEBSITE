"""Смену района не закрыть посреди дня без подтверждения (29 сен 2026).

28 сентября Алгусес закрыли в 14:13 — через четыре часа после начала суток.
Работа шла ещё шестнадцать часов, и каждый заказ после этого система подписала
СЛЕДУЮЩИМ днём (db.order_day_now): за 28-е у района осталось ноль заказов, три
заказа на 3400 AED уехали в 29-е, и никто об этом не узнал.

Меряем возраст дня, а не «есть ли водители на смене»: на нормальном закрытии
они тоже на смене — оператор закрывает район первым, водитель свою смену после
него. Проверка по 214 закрытиям: 196 между 05:00 и 07:00, все до 10:44, то
есть через 19–25 часов. Порог 12 часов отделяет их с запасом.

  • день молодой — отказ, и в отказе названы те, кто на смене;
  • подтвердил (force) — закрывает;
  • день отработан — закрывает молча, как раньше;
  • заказ в пути по-прежнему запрет, а не вопрос.

    python3 tools/test_shift_close_early.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta                          # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, operator_routes as opr                                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-28"
СОСТОЯНИЕ = {"district": "alguses", "operator": "Фарух", "orders": 0, "revenue": 0,
             "open": 0, "open_ids": [], "in_route": 0}
ЗАКРЫТО = []

async def _state(day, districts, ids): return {"districts": [dict(СОСТОЯНИЕ)]}
async def _close(day, oid, fields): ЗАКРЫТО.append((day, oid)); return True
async def _dd(day, name):
    return {"shift_open_at": "2026-09-28T09:44:00+00:00"} if name in ("Джавид", "Сунат") else {}

opr._fresh_districts = lambda: asyncio.sleep(0, [{"id": "alguses", "code": "B4"}])
opr._people_for = lambda r, d: {}
opr._scope = lambda p, w, d: {"alguses"}
opr._shift_state = _state
opr._biz_date = lambda dt: datetime.fromisoformat(ДЕНЬ).date()
opr._staff_mod.DISTRICT_DRIVERS = {"alguses": ["Джавид", "Сунат", "Даврон"]}
db.shift_close = _close
db.get_driver_day = _dd
db.driver_track_clear = lambda names: asyncio.sleep(0, 0)


async def закрыть(**body):
    r = make_mocked_request("POST", "/x")
    r["op_id"] = 1
    async def js(): return {"district": "alguses", "as": "Фарух", **body}
    r.json = js
    h = opr.handle_shift_close
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    print("── сама арифметика возраста дня ─────────────────────────────")
    from datetime import date
    сег = opr._biz_date(datetime.now(opr.DUBAI_TZ))
    вчера = сег - timedelta(days=1)
    eq("вчерашний день ровно на сутки старше",
       round(opr._hours_in(вчера) - opr._hours_in(сег), 6), 24.0)
    свои = (datetime.now(opr.DUBAI_TZ)
            - datetime(сег.year, сег.month, сег.day, opr.SHIFT_START_HOUR,
                       tzinfo=opr.DUBAI_TZ)).total_seconds() / 3600
    eq("совпадает со счётом вручную", round(opr._hours_in(сег) - свои, 6), 0.0)

    print("── день ещё идёт: четыре часа, как у Алгусеса ───────────────")
    opr._hours_in = lambda day: 4.2
    код, тело = await закрыть()
    eq("ПОСРЕДИ ДНЯ НЕ ЗАКРЫВАЕМ", (код, тело.get("error")), (409, "too_early"))
    eq("сказали, сколько прошло", тело.get("hours"), 4.2)
    eq("и кого это оставит без дня", тело.get("drivers"), ["Джавид", "Сунат"])
    eq("смена не закрыта", ЗАКРЫТО, [])

    print("── оператор подтвердил ──────────────────────────────────────")
    код, _ = await закрыть(force=1)
    eq("закрыли", код, 200)
    eq("запись легла", ЗАКРЫТО, [(ДЕНЬ, "alguses")])

    print("── день отработан: как было ─────────────────────────────────")
    ЗАКРЫТО.clear()
    opr._hours_in = lambda day: 19.8
    код, _ = await закрыть()
    eq("закрывается без вопросов", (код, ЗАКРЫТО), (200, [(ДЕНЬ, "alguses")]))

    print("── заказ в пути — по-прежнему запрет, а не вопрос ───────────")
    ЗАКРЫТО.clear()
    СОСТОЯНИЕ["in_route"] = 2; СОСТОЯНИЕ["in_route_ids"] = ["AMB1", "AMB2"]
    opr._hours_in = lambda day: 4.2
    код, тело = await закрыть(force=1)
    eq("даже с подтверждением не пускаем", (код, тело.get("error")), (409, "orders_in_route"))
    eq("и ничего не закрыли", ЗАКРЫТО, [])

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
