"""Штрафы, требующие решения (владелец, 22 сен 2026): программа формирует,
старший решает, любое решение — в историю с исходом. Два вида:
  • геолокация выключилась на открытой смене — штраф 200 AED (правится при
    назначении), один за день: новые выключения дописываются, пока ждёт;
    «Назначить» — обычный штраф (водителю сообщение), «Не назначать» — исход;
  • смену открыли позже 15:00 — «урезать питание» 80 → 40 за тот день
    (meal_cut сильнее отпуска раньше конца смены) или «не урезать».
mongomock + настоящие db, fines_auto, geo_watch.on_stream, driver_routes._late_alert,
finance_routes (ручка решения, история). Решают один раз (409), штраф без
суммы не назначить (400), тест-водитель — мимо."""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["OWNER_WEBAPP_URL"] = "https://star.example/"
from datetime import datetime, timezone, timedelta
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, fines_auto, geo_watch, driver_routes as dr, finance_routes as fr
import config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

DUBAI = timezone(timedelta(hours=4))
OWN, GEO, OPS, TOLD = [], [], [], []
fake_owner = types.ModuleType("owner_routes")
async def notify_owners(key, text, **k): OWN.append((key, text, k.get("reply_markup")))
fake_owner.notify_owners = notify_owners
fake_op = types.ModuleType("op_route")
async def op_send(text, district="", **k): OPS.append((district, text))
fake_op.send = op_send
sys.modules["owner_routes"] = fake_owner; sys.modules["op_route"] = fake_op
async def geo_owners(text, event, reply_markup=None, exclude=None, meta=None): GEO.append((text, reply_markup)); return 1
geo_watch._owners = geo_owners
async def _sync(*a, **k): pass
staff.sync = _sync
async def tell_safe(name, text, parse_mode="HTML"): TOLD.append((name, text)); return 1
fr._pn.tell_safe = tell_safe
async def _touch(*a, **k): pass
fr._touch = _touch
async def build(month, *a, **k): return {"month": month}
fr.build = build
fr._biz_day = lambda *a, **k: "2026-09-22"
DAY = "2026-09-22"
at = lambda h, m, d=22: datetime(2026, 9, d, h, m, tzinfo=DUBAI)


async def decide(body):
    r = make_mocked_request("POST", "/api/owner/finance/fines/decide")
    r._read_bytes = json.dumps(body).encode(); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = fr.handle_fine_decide
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_fines"]; d = db._db
    for n in ("Худоба", "Али", "Баха"):
        await db.save_driver_day(DAY, n, {"working": True, "shift_open_at": at(12, 0).astimezone(timezone.utc)})

    print("── геолокация выключилась на смене → штраф 200 на решение ──────")
    await geo_watch.on_stream("Худоба", False, now=at(19, 40).astimezone(timezone.utc))
    p = await d.fine_pending.find_one({"kind": "geo_off"})
    eq("записан: кто, день, за что, во сколько, 200, ждёт",
       (p["_id"], p["name"], p["day"], p["reason"], p["note"], p["amount"], p["status"]),
       ("geo_off:2026-09-22:Худоба", "Худоба", DAY, "Отключил геолокацию", "выключил в 19:40", 200, "pending"))
    eq("старшему — «выключил геопозицию», строка о штрафе и кнопка в STAR",
       ("выключил геопозицию" in GEO[0][0], "Штраф 200 AED ждёт решения" in GEO[0][0],
        GEO[0][1]["inline_keyboard"][0][0]["text"], GEO[0][1]["inline_keyboard"][0][0]["web_app"]["url"]),
       (True, True, "Решить по штрафу", "https://star.example/?go=fines"))
    await geo_watch.on_stream("Худоба", True, now=at(19, 50).astimezone(timezone.utc))
    GEO.clear()
    await geo_watch.on_stream("Худоба", False, now=at(21, 5).astimezone(timezone.utc))
    p = await d.fine_pending.find_one({"kind": "geo_off"})
    eq("второй раз за день — та же запись, время дописано", (await d.fine_pending.count_documents({}), p["note"]),
       (1, "выключал 2 раза: 19:40, 21:05"))
    eq("и в сообщении про повтор — без строки о штрафе и без кнопки", ("Штраф" in GEO[0][0], GEO[0][1]), (False, None))
    await db.save_driver_day(DAY, "Тест-водитель", {"working": True, "shift_open_at": at(12, 0).astimezone(timezone.utc)})
    await geo_watch.on_stream("Тест-водитель", False, now=at(20, 0).astimezone(timezone.utc))
    eq("тест-водителю — штрафа нет", await d.fine_pending.count_documents({"name": "Тест-водитель"}), 0)

    print("── поздняя смена → решение по питанию ──────────────────────────")
    await dr._late_alert({"name": "Али", "district": "bbay"}, at(16, 20), DAY)
    p = await d.fine_pending.find_one({"kind": "late_shift"})
    eq("записано: за что, подробности, без суммы", (p["reason"], p["note"], p.get("amount")),
       ("Поздно открыл смену", "открыл в 16:20, правило — до 15:00", None))
    eq("старшему — вопрос про питание и кнопка", ("урезать питание с 80 до 40?" in OWN[0][1],
       OWN[0][2]["inline_keyboard"][0][0]["text"]), (True, "Решить по питанию"))
    await dr._late_alert({"name": "Баха", "district": "bbay"}, at(17, 57), DAY)
    v = {x["name"]: x for x in await fines_auto.pending()}
    eq("в окошке трое: у штрафа 200, у питания 80 → 40",
       (sorted(v), v["Худоба"]["action"], v["Худоба"]["amount"], v["Али"]["action"], v["Али"]["meal_from"], v["Али"]["meal_to"]),
       (["Али", "Баха", "Худоба"], "fine", 200, "meal", 80, 40))

    print("── решения ─────────────────────────────────────────────────────")
    st, b = await decide({"id": "geo_off:2026-09-22:Худоба", "decision": "assign", "amount": 0, "as": "Макар"})
    eq("штраф без суммы — нельзя", (st, b.get("error")), (400, "bad_amount"))
    st, b = await decide({"id": "geo_off:2026-09-22:Худоба", "decision": "assign", "amount": 150, "month": "2026-09", "as": "Макар"})
    eq("назначил 150 (правка 200)", (st, b.get("ok")), (200, True))
    it = await d.fin_pay_items.find_one({})
    eq("в зарплатах обычный штраф: 150, день нарушения, этот месяц, разом, за что",
       (it["kind"], it["amount"], it["day"], it["from"], it["per_month"], it["reason"], it["note"], it["auto"]),
       ("fine", 150, DAY, "2026-09", 0, "Отключил геолокацию", "выключал 2 раза: 19:40, 21:05", "geo_off"))
    eq("водителю — сообщение о штрафе", (TOLD[0][0], "Штраф 150 AED" in TOLD[0][1]), ("Худоба", True))
    TOLD.clear()
    st, b = await decide({"id": "late_shift:2026-09-22:Али", "decision": "assign", "month": "2026-09", "as": "Макар"})
    eq("урезал питание — без суммы можно", (st, b.get("ok")), (200, True))
    day = await db.get_driver_day(DAY, "Али")
    eq("питание за день — 40, а не 80", (day.get("meal_rate"), staff.meal_of(day)), (40, 40))
    eq("отпуск раньше конца смены с 80 — урезанное не перебивает", staff.meal_of({**day, "meal_rate": 80}), 40)
    eq("новой записи в зарплатах нет", await d.fin_pay_items.count_documents({}), 1)
    eq("водителю — сообщение: день, 40 вместо 80, за что", (TOLD[0][0], "40 AED вместо 80" in TOLD[0][1],
       "22 сентября" in TOLD[0][1], "Поздно открыл смену" in TOLD[0][1]), ("Али", True, True, True))
    TOLD.clear()
    st, b = await decide({"id": "late_shift:2026-09-22:Баха", "decision": "skip", "as": "Слон"})
    eq("не урезал — питание не тронуто, водителю ничего",
       (st, staff.meal_of(await db.get_driver_day(DAY, "Баха")), TOLD), (200, 80, []))
    st, b = await decide({"id": "late_shift:2026-09-22:Баха", "decision": "assign", "as": "Макар"})
    eq("второй раз — 409 и свежая книга", (st, b.get("error"), b.get("book")), (409, "decided", {"month": "2026-09"}))
    eq("ждущих не осталось", await fines_auto.pending(), [])

    print("── история ─────────────────────────────────────────────────────")
    items = [dict(x) for x in await d.fin_pay_items.find({}).to_list(10)]
    h = fr._penalty_history(items, "2026-09", decided=await fines_auto.decided())
    eq("все три решения в истории с исходом",
       sorted((x["name"], x["auto"], x["declined"], x["meal"], x["amount"]) for x in h),
       [("Али", "late_shift", False, True, 40), ("Баха", "late_shift", True, True, 40),
        ("Худоба", "geo_off", False, False, 150)])
    await db.save_driver_day(DAY, "Файзуло", {"working": True, "shift_open_at": at(12, 0).astimezone(timezone.utc)})
    await geo_watch.on_stream("Файзуло", False, now=at(22, 0).astimezone(timezone.utc))
    await decide({"id": "geo_off:2026-09-22:Файзуло", "decision": "skip", "as": "Слон"})
    h = fr._penalty_history(items, "2026-09", decided=await fines_auto.decided())
    f = next(x for x in h if x["name"] == "Файзуло")
    eq("«Не назначен» у штрафа — сумма, за что, кто решил", (f["declined"], f["meal"], f["amount"], f["reason"], f["by"]),
       (True, False, 200, "Отключил геолокацию", "Слон"))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
