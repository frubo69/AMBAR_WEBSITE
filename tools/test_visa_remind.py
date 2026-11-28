"""Визы: отложить напоминание по человеку (владелец, 17 ноя 2026: «чтобы можно
было настраивать отдельно по каждому человеку, как в будильнике в эпл, snooze,
типа отложить… чтобы из-за этого человека виза в чек-лист всплывала не за 10, а
за 9 или 8 или 7 или 5, как ему угодно… но не меньше 3 дней можно поставить»).

У человека свой срок — за сколько дней до конца визы он встаёт в строку «Визы»
чек-листа: от 3 до общего (14, как и было). Своего нет — общий.

  A. По умолчанию ничего не изменилось: у всех общий срок, в чек-листе те же.
  B. Отложили — человек встаёт в чек-лист за свои дни, не раньше; общий срок
     остальных прежний.
  C. Границы: меньше 3 и больше общего нельзя, не число — нельзя; общий срок
     снимает своё значение.
  D. Срок визы, «за пределами ОАЭ» и заметка от напоминания не зависят и друг
     друга не стирают.
  E. Настоящий чек-лист (owner_routes.handle_checklist): строка «Визы», её
     подпись, счёт и цвет — до и после.
  F. «Отложить до завтра» на карточке (владелец, 28 ноя 2026): отметка
     visas_later общей ручкой чек-листа убирает строку за эти рабочие сутки,
     остальное и сами визы не трогает, за другие сутки не действует.

    python3 tools/test_visa_remind.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, config_staff as staff, owner_routes as orr              # noqa: E402
FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def call(h, method, body=None, path="/x"):
    req = make_mocked_request(method, path)
    req["owner_id"] = 1; req["owner_user"] = {"id": 1}
    async def js(): return body or {}
    req.json = js
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    res = await h(req)
    return res.status, json.loads(res.body)


async def due(day):
    return [(v["name"], v["days"]) for v in await orr._visas_due(day)]


async def chk():
    st, r = await call(orr.handle_checklist, "GET", path="/api/owner/checklist")
    return next((x for x in r["rows"] if x["id"] == "visas"), None)


def plus(day, n):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


async def main():
    db._db = AsyncMongoMockClient()["visa_remind"]
    staff.AWAY.clear()
    for n, d in (("Тест-водитель 1", "jvc"), ("Тест-водитель 2", "jvc"), ("Тест-водитель 3", "tecom"),
                 ("Тест-водитель 4", "bbay"), ("Тест-водитель 5", "silicon")):
        await db.driver_add(n, d, 1)
    await staff.sync(force=True)
    DAY = "2026-11-17"
    A, B, C, D, E = (f"Тест-водитель {i}" for i in range(1, 6))
    for name, n in ((A, 2), (B, 8), (C, 13), (D, 20)):
        await call(orr.handle_visas_set, "POST", {"name": name, "until": plus(DAY, n), "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": E, "until": plus(DAY, 5), "abroad": True, "as": "Т"})

    print("A. По умолчанию — как было")
    people = {p["name"]: p for p in await orr._visas_view(DAY)}
    eq("у всех общий срок 14", sorted({p["remind"] for p in people.values()}), [14])
    eq("в чек-листе: 2, 8 и 13 дней; 20 дней далеко; за пределами ОАЭ не считается", await due(DAY), [(A, 2), (B, 8), (C, 13)])
    st, r = await call(orr.handle_visas, "GET")
    eq("ответ: общий срок 14, меньше 3 поставить нельзя", (st, r["horizon"], r["remind_min"]), (200, 14, 3))

    print("\nB. Отложили")
    st, r = await call(orr.handle_visas_remind, "POST", {"name": B, "days": 7, "as": "Т"})
    eq("B: напомнить за 7 дней — записано", (st, r.get("ok"), {p["name"]: p["remind"] for p in r["people"]}[B]), (200, True, 7))
    eq("сегодня у B 8 дней — из чек-листа ушёл, остальные на месте", await due(DAY), [(A, 2), (C, 13)])
    eq("завтра у B 7 дней — встал в чек-лист", await due(plus(DAY, 1)), [(A, 1), (B, 7), (C, 12)])
    st, r = await call(orr.handle_visas_remind, "POST", {"name": C, "days": 3, "as": "Т"})
    eq("C: напомнить за 3 дня", (st, {p["name"]: p["remind"] for p in r["people"]}[C]), (200, 3))
    eq("у C 13 дней — нет; за 4 дня — нет; за 3 — встал; вышла — стоит",
       ([n for n, _ in await due(DAY)], C in [n for n, _ in await due(plus(DAY, 9))],
        C in [n for n, _ in await due(plus(DAY, 10))], C in [n for n, _ in await due(plus(DAY, 15))]),
       ([A], False, True, True))
    eq("A (2 дня) стоит при любом своём сроке — меньше трёх не поставить", (await due(DAY))[0], (A, 2))
    eq("общий срок остальных прежний: D за 14 дней встаёт", D in [n for n, _ in await due(plus(DAY, 6))], True)

    print("\nC. Границы")
    for days, код in ((2, "too_small"), (0, "too_small"), (-5, "too_small"), (15, "too_big"), (100, "too_big"),
                      ("много", "bad_days"), (None, "bad_days"), (7.5, "bad_days"), (True, "bad_days"), ("", "bad_days")):
        st, r = await call(orr.handle_visas_remind, "POST", {"name": B, "days": days, "as": "Т"})
        eq(f"  {days!r} → 400 {код}", (st, r.get("error")), (400, код))
    eq("после отказов у B по-прежнему 7", {p["name"]: p["remind"] for p in await orr._visas_view(DAY)}[B], 7)
    st, r = await call(orr.handle_visas_remind, "POST", {"name": "", "days": 7})
    eq("без имени → 400", (st, r.get("error")), (400, "no_name"))
    st, r = await call(orr.handle_visas_remind, "POST", {"name": B, "days": "5", "as": "Т"})
    eq("число строкой берётся: «5» → 5", (st, {p["name"]: p["remind"] for p in r["people"]}[B]), (200, 5))
    st, r = await call(orr.handle_visas_remind, "POST", {"name": B, "days": 3, "as": "Т"})
    eq("ровно 3 — можно", (st, {p["name"]: p["remind"] for p in r["people"]}[B]), (200, 3))
    st, r = await call(orr.handle_visas_remind, "POST", {"name": B, "days": 14, "as": "Т"})
    doc = await db._db.staff_visas.find_one({"_id": B})
    eq("общий срок 14 — своего значения больше нет", (st, {p["name"]: p["remind"] for p in r["people"]}[B], doc.get("remind")), (200, 14, None))
    eq("и B снова в чек-листе за 8 дней", B in [n for n, _ in await due(DAY)], True)
    await db._db.staff_visas.update_one({"_id": D}, {"$set": {"remind": 99}})
    await db._db.staff_visas.update_one({"_id": A}, {"$set": {"remind": 1}})
    people = {p["name"]: p["remind"] for p in await orr._visas_view(DAY)}
    eq("мусор в базе (99, 1) читается как общий срок", (people[D], people[A]), (14, 14))

    print("\nD. Срок визы и напоминание друг друга не стирают")
    await call(orr.handle_visas_remind, "POST", {"name": B, "days": 6, "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": B, "until": plus(DAY, 40), "note": "продлили", "as": "Т"})
    p = {x["name"]: x for x in await orr._visas_view(DAY)}[B]
    eq("продлили визу — напоминание за 6 осталось", (p["until"], p["note"], p["remind"]), (plus(DAY, 40), "продлили", 6))
    await call(orr.handle_visas_remind, "POST", {"name": B, "days": 9, "as": "Т"})
    p = {x["name"]: x for x in await orr._visas_view(DAY)}[B]
    eq("поменяли напоминание — срок и заметка на месте", (p["until"], p["note"], p["remind"], p["abroad"]), (plus(DAY, 40), "продлили", 9, False))
    await call(orr.handle_visas_remind, "POST", {"name": E, "days": 4, "as": "Т"})
    eq("за пределами ОАЭ со своим сроком — в чек-лист всё равно не идёт", E in [n for n, _ in await due(plus(DAY, 4))], False)
    st, r = await call(orr.handle_visas_remind, "POST", {"name": "Новый человек", "days": 8, "as": "Т"})
    p = {x["name"]: x for x in r["people"]}["Новый человек"]
    eq("человек без срока визы: напоминание записано, в чек-лист не идёт", (st, p["remind"], p["until"], p["days"]), (200, 8, "", None))

    print("\nE. Настоящий чек-лист")
    await db._db.staff_visas.delete_many({})
    today = orr._biz_day_start(datetime.now(orr.DUBAI_TZ)).date().isoformat()
    await call(orr.handle_visas_set, "POST", {"name": A, "until": plus(today, 10), "as": "Т"})
    await call(orr.handle_visas_set, "POST", {"name": B, "until": plus(today, 6), "as": "Т"})
    r = await chk()
    eq("строка «Визы»: двое, не красная, ведёт на страницу виз",
       (r and r["t"], r and r["n"], r and r["state"], r and r["go"]), ("Визы", 2, "now", "visas"))
    eq("подпись — оба человека", (A in r["s"], B in r["s"]), (True, True))
    await call(orr.handle_visas_remind, "POST", {"name": A, "days": 7, "as": "Т"})
    r = await chk()
    eq("A отложили до 7 дней (у него 10): в строке один B", (r["n"], A in r["s"], B in r["s"]), (1, False, True))
    await call(orr.handle_visas_remind, "POST", {"name": B, "days": 5, "as": "Т"})
    eq("B отложили до 5 дней (у него 6): строки «Визы» нет", await chk(), None)
    await call(orr.handle_visas_set, "POST", {"name": B, "until": plus(today, 3), "as": "Т"})
    r = await chk()
    eq("у B осталось 3 дня — строка вернулась красной", (r["n"], r["state"], B in r["s"]), (1, "late", True))
    await call(orr.handle_visas_remind, "POST", {"name": A, "days": 14, "as": "Т"})
    r = await chk()
    eq("A вернули общий срок — снова в строке", (r["n"], A in r["s"]), (2, True))

    print("\nF. «Отложить до завтра» на карточке виз (владелец, 28 ноя 2026)")
    st, full = await call(orr.handle_checklist, "GET", path="/api/owner/checklist")
    было = (len(full["rows"]), full["counts"]["late"])
    eq("перед этим строка «Визы» есть: двое, красная", (r["n"], r["state"]), (2, "late"))
    st, r2 = await call(orr.handle_checklist_mark, "POST", {"day": today, "item": "visas_later", "done": True})
    eq("отметка visas_later принята общей ручкой чек-листа", (st, r2.get("ok")), (200, True))
    eq("строки «Визы» за сегодня нет", await chk(), None)
    st, full = await call(orr.handle_checklist, "GET", path="/api/owner/checklist")
    eq("остальные строки на месте, счёт без виз: строк на одну меньше, красных на одну меньше",
       (len(full["rows"]), full["counts"]["late"]), (было[0] - 1, было[1] - 1))
    doc = await db._db.checklist.find_one({"day": today})
    eq("отметка лежит на рабочих сутках чек-листа, как остальные",
       bool((((doc or {}).get("items") or {}).get("visas_later") or {}).get("done")), True)
    p = {x["name"]: x for x in await orr._visas_view(today)}
    eq("сами визы не тронуты: сроки и напоминания прежние",
       (p[A]["until"], p[B]["until"], p[A]["remind"], p[B]["remind"]), (plus(today, 10), plus(today, 3), 14, 5))
    eq("за завтра отметки нет, а срок у обоих всё ещё в зоне — строка вернётся сама",
       ((await db.checklist_get(plus(today, 1))).get("visas_later"),
        [v["name"] for v in await orr._visas_due(plus(today, 1))]), (None, [B, A]))
    await call(orr.handle_checklist_mark, "POST", {"day": plus(today, -1), "item": "visas_later", "done": True})
    st, r3 = await call(orr.handle_checklist_mark, "POST", {"day": today, "item": "visas_later", "done": False})
    eq("сняли отметку за сегодня — строка снова есть", (st, bool(await chk())), (200, True))
    st, yday = await call(orr.handle_checklist, "GET", path=f"/api/owner/checklist?day={plus(today, -1)}")
    eq("отложено за вчера: во вчерашнем чек-листе строки нет, в сегодняшнем — есть",
       (any(x["id"] == "visas" for x in yday["rows"]), bool(await chk())), (False, True))
    st, r4 = await call(orr.handle_checklist_mark, "POST", {"day": today, "item": "visas_tomorrow", "done": True})
    eq("чужое имя отметки ручка не берёт", st, 400)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
