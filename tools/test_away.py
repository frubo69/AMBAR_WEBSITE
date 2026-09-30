"""Уехавший исчезает из рабочих списков, но остаётся в команде (владелец,
24 сен 2026: «они улетели — они должны отовсюду исчезать, пока снова не
прилетели… оставляй их в команде, просто пиши серым, что уехал; но в расходы
смены его даже включать не надо»).

Один ответ на всю систему — `config_staff.AWAY`, он же у панели оператора,
у локатора, у проверки бутылок и у напоминаний.

С 29 сен 2026 (владелец: «у всех водителей, которые уехали, автоматом
отзывать доступ к водительскому боту и переставать требовать от них
верификацию — на время, пока они не работают») уехавший ещё и:
  • не входит в приложение — 403 away с днём отъезда, а не голое «нет доступа»;
  • не штрафуется за выключенную геопозицию и не будит старшего;
  • получает всё обратно сам, как только снова вышел на работу.

    python3 tools/test_away.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, config_staff as staff                                  # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-25"


async def завести():
    db._db = AsyncMongoMockClient()["ambar_away"]
    await db._db.fin_people.insert_many([
        {"_id": "Работает", "work": [{"from": "2026-01-01", "to": ""}]},
        {"_id": "Уехал", "work": [{"from": "2026-01-01", "to": "2026-09-24"}]},
        {"_id": "Сегодня", "work": [{"from": "2026-01-01", "to": ДЕНЬ}]},
        {"_id": "Вернулся", "work": [{"from": "2026-01-01", "to": "2026-08-01"},
                                     {"from": "2026-09-20", "to": ""}]},
        # Его никто не возвращает по ходу теста — на нём проверяем доступ.
        {"_id": "Улетел", "work": [{"from": "2026-01-01", "to": "2026-09-24"}]},
    ])
    await staff.sync_away(ДЕНЬ)


async def main():
    await завести()
    eq("уехавшие найдены", sorted(staff.AWAY), ["Сегодня", "Уехал", "Улетел"])
    eq("день отъезда записан", staff.away_since("Уехал"), "2026-09-24")
    eq("работающий на месте", staff.is_away("Работает"), False)
    eq("вернувшийся на месте", staff.is_away("Вернулся"), False)
    eq("кого нет в «Зарплатах» — считаем на месте", staff.is_away("Неизвестный"), False)
    eq("список без уехавших",
       staff.here(["Работает", "Уехал", "Вернулся", "Сегодня"]), ["Работает", "Вернулся"])

    # Расходы смены: строка уехавшего не должна появиться вовсе.
    # Состав района — через расписание, как в бою: списки собирает apply_moves.
    _d0 = staff.DISTRICT_STAFF[0]["district"]
    staff._BASE_DRIVERS[_d0] = ["Работает", "Уехал"]
    staff._BASE_DRIVER_AT.clear(); staff._BASE_DRIVER_AT.update({"Работает": _d0, "Уехал": _d0})
    # С 30 сен 2026 уехавших нет в САМОМ ИСТОЧНИКЕ (владелец: «НИГДЕ, кроме
    # того места, где я указываю, уехал он или приехал»): рабочие списки их не
    # содержат, полный список отдаётся только по просьбе all=True.
    staff.apply_moves(*staff._LAST_MOVES)
    eq("в рабочем списке водителей его нет",
       [d["name"] for d in staff.drivers() if d["name"] in ("Работает", "Уехал")], ["Работает"])
    eq("и в составе района", staff.DISTRICT_DRIVERS[staff.DISTRICT_STAFF[0]["district"]], ["Работает"])
    eq("и среди имён", "Уехал" in staff.driver_names(), False)
    eq("а по просьбе (зарплаты, деньги прошлых дней, вход) — есть, с пометкой",
       [(d["name"], d["away"], d["away_since"]) for d in staff.drivers(all=True) if d["name"] == "Уехал"],
       [("Уехал", True, "2026-09-24")])
    eq("район за ним остаётся — для денег прошедших дней",
       staff.district_map().get("Уехал"), staff.DISTRICT_STAFF[0]["district"])
    eq("имя в полном списке", "Уехал" in staff.driver_names(all=True), True)

    # С 29 сен 2026 его нет и в самой «Команде», и у оператора (владелец:
    # «почему указаны даже те водители, которые сейчас не работают? это
    # путает»). Проверяем то, что уходит на экран, а не внутренний список.
    import operator_routes as opr
    eq("у оператора в районе только те, кто здесь",
       opr._districts()[0]["drivers"], ["Работает"])
    eq("заказ уехавшему не отдать",
       "Уехал" in opr._drivers_of(False, districts=opr._districts()), False)
    import owner_routes as ownr
    async def _ничего(): return None
    ownr._staff_fresh = _ничего
    staff.DISTRICT_DRIVERS[staff.DISTRICT_STAFF[0]["district"]] = ["Работает", "Уехал"]
    штат = await ownr._staff_payload()
    eq("в команде на районе его нет", штат["districts"][0]["drivers"], ["Работает"])
    eq("и в списке водителей нет",
       [v["name"] for v in штат["drivers"] if v["name"] == "Уехал"], [])
    eq("и среди телефонов нет",
       [l["name"] for l in штат["links"] if l["name"] == "Уехал"], [])

    # Вернулся — и снова везде.
    await db._db.fin_people.update_one({"_id": "Уехал"},
                                       {"$push": {"work": {"from": ДЕНЬ, "to": ""}}})
    await staff.sync_away(ДЕНЬ)
    eq("прилетел — снова в списках", staff.is_away("Уехал"), False)
    eq("и в рабочем списке", staff.here(["Работает", "Уехал"]), ["Работает", "Уехал"])

    # ── доступ и слежение (29 сен 2026) ─────────────────────────────────
    # Повод: Азиз уехал 24 сентября, 28-го выключил геопозицию у себя дома —
    # и получил штраф 200 AED на решение.
    import driver_routes as dr, geo_watch as gw, fines_auto as fa
    from aiohttp.test_utils import make_mocked_request

    УЕХАЛ = {"name": "Улетел", "district": "jvc", "district_code": "B1"}
    ЗДЕСЬ = {"name": "Работает", "district": "jvc", "district_code": "B1"}
    кто = {"кто": УЕХАЛ}
    dr._valid_init_data = lambda init, token: {"id": 777}
    dr.staff.sync = lambda *a, **k: asyncio.sleep(0)       # реестр уже в памяти
    dr.staff.driver_by_tg = lambda uid: кто["кто"]
    gw.staff.sync = dr.staff.sync

    @dr.require_driver
    async def ручка(request):
        from aiohttp import web
        return web.json_response({"ok": True})

    async def войти():
        r = make_mocked_request("GET", "/x", headers={"Authorization": "tma x"})
        resp = await ручка(r)
        return resp.status, json.loads(resp.text)

    eq("он уехавший", staff.is_away("Улетел"), True)
    код, тело = await войти()
    eq("УЕХАВШЕГО В ПРИЛОЖЕНИЕ НЕ ПУСКАЮТ", (код, тело.get("error")), (403, "away"))
    eq("и говорят, с какого дня", тело.get("since"), "2026-09-24")

    кто["кто"] = ЗДЕСЬ
    код, тело = await войти()
    eq("а работающего пускают", (код, тело.get("ok")), (200, True))

    # Выключил геопозицию — ни штрафа, ни вести старшему.
    письма = []
    async def _тихо(*a, **k): письма.append(a[0] if a else "")
    gw._owners = _тихо
    eq("сторож уехавшего не разбирает", await gw.on_stream("Улетел", on=False), False)
    eq("и старшему не писал", письма, [])
    eq("штраф уехавшему не заводится",
       await fa.geo_off("Улетел", "jvc", "2026-09-26", "13:32"), False)
    # Коллекция называется fine_pending (без «s») — на «fines_pending» проверка
    # проходит всегда и ничего не значит.
    eq("в базе его и нет", await db._db.fine_pending.count_documents({}), 0)
    eq("а работающему — заводится",
       await fa.geo_off("Работает", "jvc", "2026-09-26", "13:32"), True)
    eq("и он в базе", await db._db.fine_pending.count_documents({}), 1)

    # Вернулся — доступ и слежение возвращаются сами, руками ничего не трогаем.
    await db._db.fin_people.update_one({"_id": "Улетел"},
                                       {"$push": {"work": {"from": "2026-09-26", "to": ""}}})
    await staff.sync_away("2026-09-26")
    кто["кто"] = УЕХАЛ
    код, _ = await войти()
    eq("ВЕРНУЛСЯ — ПУСКАЮТ, ничего не нажимая", код, 200)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
