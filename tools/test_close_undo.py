"""Вернуть отпущенного водителя в смену (владелец, 24 сен 2026: «водитель
уезжает 24-го, но он же ещё сейчас работает, смена не закончилась, а оператор
его не может выбрать, чтобы заказ ему выдать»).

Отпустили раньше — заказы ему не назначаются, и это правильно. Но решение
бывает преждевременным: билет оказался позже, машина ещё у него. Возврата не
было вовсе — только руками в базе.

mongomock + настоящие close_req.decide / undo.

    python3 tools/test_close_undo.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone                        # noqa: E402
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db, close_req, config_staff as staff                     # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-23"
ВОДИТЕЛЬ = "Азиз"
СКОУП = {"silicon"}
СКАЗАНО = []


async def подготовить():
    db._db = AsyncMongoMockClient()["ambar_close"]
    close_req.driver_card = lambda n: {"name": n, "district": "silicon"} if n == ВОДИТЕЛЬ else {}
    import operator_routes
    async def tell(name, text): СКАЗАНО.append((name, text))
    operator_routes.tell_driver = tell
    await db.save_driver_day(ДЕНЬ, ВОДИТЕЛЬ, {"working": True, "shift_open_at": datetime.now(timezone.utc)})
    doc = await db.get_driver_day(ДЕНЬ, ВОДИТЕЛЬ)
    code, _ = await close_req.ask(ДЕНЬ, {"name": ВОДИТЕЛЬ, "district": "silicon"}, doc,
                                  "other", "У меня билет", [], False)
    return code


async def main():
    код = await подготовить()
    eq("водитель попросил закрыть смену раньше", код, 200)
    d = await db.get_driver_day(ДЕНЬ, ВОДИТЕЛЬ)
    rid = (d.get("close_req") or {}).get("id")

    код, _ = await close_req.decide(ДЕНЬ, ВОДИТЕЛЬ, rid, True, staff.MEAL_WORKING, "", "", "Фарух", СКОУП)
    eq("оператор отпустил", код, 200)
    rows = await close_req.day_rows(ДЕНЬ)
    eq("отпущенному заказы не назначают", ВОДИТЕЛЬ in close_req.released_names(rows), True)

    СКАЗАНО.clear()
    код, r = await close_req.undo(ДЕНЬ, ВОДИТЕЛЬ, "Фарух", СКОУП)
    eq("вернули в смену", (код, r.get("status")), (200, "undone"))
    rows = await close_req.day_rows(ДЕНЬ)
    eq("и заказы снова можно назначать", ВОДИТЕЛЬ in close_req.released_names(rows), False)
    d = await db.get_driver_day(ДЕНЬ, ВОДИТЕЛЬ)
    eq("питание вернулось рабочим", d.get("meal_rate"), staff.MEAL_WORKING)
    eq("кто вернул — записано", (d.get("close_req") or {}).get("undone_by"), "Фарух")
    eq("просьба снята, а не открыта заново: оператору не звонит",
       [x["driver"] for x in close_req.panel_rows(rows)] if hasattr(close_req, "panel_rows") else
       [x.get("driver") for x in rows if (x.get("close_req") or {}).get("status") == "open"], [])
    eq("и старшему её уже не передать", (d.get("close_req") or {}).get("status"), "undone")

    eq("водителю сказали", ("вернул вас в смену" in (СКАЗАНО[0][1] if СКАЗАНО else "")), True)

    код, r = await close_req.undo(ДЕНЬ, ВОДИТЕЛЬ, "Фарух", СКОУП)
    eq("второй раз возвращать нечего", (код, r.get("error")), (409, "not_released"))
    код, r = await close_req.undo(ДЕНЬ, ВОДИТЕЛЬ, "Фарух", {"jvc"})
    eq("чужого водителя не вернуть", (код, r.get("error")), (403, "not_yours"))

    # Водитель всё-таки уезжает — может попросить снова, без ожидания.
    код, _ = await close_req.ask(ДЕНЬ, {"name": ВОДИТЕЛЬ, "district": "silicon"},
                                 await db.get_driver_day(ДЕНЬ, ВОДИТЕЛЬ),
                                 "other", "Всё-таки пора", [], False)
    eq("попросить снова можно сразу", код, 200)
    d = await db.get_driver_day(ДЕНЬ, ВОДИТЕЛЬ)
    rid = (d.get("close_req") or {}).get("id")
    eq("снятая просьба ушла в историю", len(d.get("close_hist") or []), 1)
    код, _ = await close_req.decide(ДЕНЬ, ВОДИТЕЛЬ, rid, True, staff.MEAL_WORKING, "", "", "Фарух", СКОУП)
    eq("и отпустить по ней можно", код, 200)

    # Смену уже закрыли — возвращать поздно: день посчитан.
    await db.save_driver_day(ДЕНЬ, ВОДИТЕЛЬ, {"shift_close_at": datetime.now(timezone.utc)})
    код, r = await close_req.undo(ДЕНЬ, ВОДИТЕЛЬ, "Фарух", СКОУП)
    eq("смена закрыта — возврата нет", (код, r.get("error")), (409, "already_closed"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
