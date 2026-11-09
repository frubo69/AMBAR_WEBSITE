"""Расходы водителей за месяц одной лентой (владелец, 4 ноя 2026).

«Хочу, чтобы в расходах были фильтры по водителям и/или категориям, по типу
охрана, парковка, чтобы можно было конкретно смотреть, кто сколько потратил
за месяц… и посмотреть так же подробно, когда расход был совершён,
согласован он или отклонён».

mongomock + настоящая ручка GET /api/owner/expenses/month:
  • в ленте — питание отмеченных дней и каждая разовая трата месяца, новые
    сверху, с днём, именем, статусом, временем подачи и решения, причиной;
    соседние месяцы в ленту не попадают;
  • total сходится с расходами водителей в книге (finance_routes._spend) по
    тем же дням, extra — без питания, card — безнал; бутылка охране, заказ в
    долг, зарплата из выручки, отклонённое и ждущее — не деньги (money);
  • pending — ждущее решения с суммой и числом, как в расходах дня; бутылка
    охране, пока ждёт, в сумме тоже (владелец, 10 ноя 2026: «почему пишет 0,
    хотя по факту 71») — иначе «1 запись · 0 AED»; принятая бутылка деньгами
    не становится;
  • люди — реестр в порядке районов плюс те, кого в реестре нет, но записи
    были (уехал до реестра);
  • миниатюр в ленте нет — только признак снимка; месяц мимо формы — текущий;
  • без авторизации — 401.

    python3 tools/test_expense_month.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp import web                                               # noqa: E402
from aiohttp.test_utils import TestClient, TestServer                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
import db, expense_routes as xr, finance_routes as fr, config_staff as staff, owner_auth   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
staff.drivers = lambda: [
    {"name": "Водитель А", "district": "jvc", "district_code": "B1", "district_name": "JVC", "operator": "Оператор"},
    {"name": "Водитель Б", "district": "bbay", "district_code": "B2", "district_name": "Business Bay", "operator": "Оператор"}]
M = "2026-11"
D1, D2, D3 = "2026-11-01", "2026-11-02", "2026-11-03"
MEAL_ON, MEAL_OFF = staff.meal_of({"working": True}), staff.meal_of({"working": False})


def x(id, kind, amount, status="approved", **k):
    return {"id": id, "kind": kind, "kind_t": xr.EXTRA_KINDS[kind]["t"], "amount": amount, "status": status,
            "plus": bool(xr.EXTRA_KINDS[kind].get("plus")), **k}


async def main():
    db._db = AsyncMongoMockClient()["ambar_exp_month"]
    await db._db.driver_days.insert_many([
        {"day": D1, "driver": "Водитель А", "working": True, "extras": [
            x("a1", "fuel", 100, at="2026-11-01T10:00:00+00:00", photo=True, thumb="data:thumb", pay="cash"),
            x("a2", "parking", 20, "pending", by_driver="Водитель А", at="2026-11-01T11:00:00+00:00", thumb="data:thumb"),
            x("a3", "wash", 50, "rejected", by_driver="Водитель А", at="2026-11-01T12:00:00+00:00",
              decided_at="2026-11-01T13:00:00+00:00", decided_note="Не видно мойки",
              photo=True, car_photo=True, thumb="t", car_thumb="c", photo_ok="no", car_ok="no")]},
        {"day": D2, "driver": "Водитель А", "working": True, "extras": [
            x("a4", "fuel", 60, pay="card", at="2026-11-02T10:00:00+00:00"),
            x("a5", "guard", 85, code="abs#000001", bottle="Absolut", bottle_id="absolut", at="2026-11-02T11:00:00+00:00"),
            x("a6", "owed_us", 300, "pending", nocash=True, auto=True, order="X1",
              comment="#X1 · заказ в долг", at="2026-11-02T12:00:00+00:00"),
            x("a7", "guard", 71, "pending", by_driver="Водитель А", code="cor#000001", bottle="Corona Extra 0.355 bottle",
              bottle_id="p43", comment="Менеджер", at="2026-11-02T13:00:00+00:00")]},
        {"day": D2, "driver": "Водитель Б", "working": True, "extras": [
            x("b1", "we_got", 40, at="2026-11-02T10:00:00+00:00"),
            x("b2", "other", 500, salary_of="s1", comment="Зарплата за октябрь", at="2026-11-02T11:00:00+00:00"),
            x("b3", "kfc", 30, at="2026-11-02T12:00:00+00:00")]},
        {"day": D1, "driver": "Водитель Б"},                       # не отмечен — питания нет
        {"day": D3, "driver": "Водитель В", "working": False, "extras": [
            x("v1", "parking", 15, at="2026-11-03T09:00:00+00:00", no_receipt=True)]},
        {"day": "2026-10-31", "driver": "Водитель А", "working": True, "extras": [x("o1", "fuel", 999, at="2026-10-31T10:00:00+00:00")]},
        {"day": "2026-12-01", "driver": "Водитель А", "working": True, "extras": [x("o2", "fuel", 999, at="2026-12-01T10:00:00+00:00")]},
    ])
    app = web.Application(); xr.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(p, auth=True, **q):
            r = await cl.get(p, params=q, headers={"Authorization": "tma 1"} if auth else {})
            return r.status, (await r.json())

        print("── лента месяца ────────────────────────────────────────────────")
        st, d = await get("/api/owner/expenses/month", month=M)
        eq("ответ 200, месяц и границы", (st, d["month"], d["from"], d["to"]), (200, M, "2026-11-01", "2026-11-30"))
        it = {i["id"]: i for i in d["items"]}
        eq("записей: 4 питания + 11 трат, соседних месяцев нет",
           (d["n"], len(it), "o1" in it, "o2" in it), (15, 15, False, False))
        eq("новые сверху: первая запись — 3 ноября", d["items"][0]["day"], D3)
        eq("питание: по строке на отмеченный день, не отмечен — нет",
           sorted((i["driver"], i["day"], i["amount"]) for i in d["items"] if i["kind"] == "meal"),
           sorted([("Водитель А", D1, MEAL_ON), ("Водитель А", D2, MEAL_ON), ("Водитель Б", D2, MEAL_ON), ("Водитель В", D3, MEAL_OFF)]))
        meal = 3 * MEAL_ON + MEAL_OFF
        eq("итоги: всего / питание / разовые / безнал", (d["total"], d["meal"], d["extra"], d["card"]), (meal + 165, meal, 165, 60))
        eq("ждут решения: парковка 20, заказ в долг 300 и бутылка охране 71 — число и сумма об одном",
           (d["pending"], d["pending_n"]), (391, 3))

        print("── сходится с книгой месяца (_spend) ────────────────────────────")
        days = [f"2026-11-{n:02d}" for n in range(1, 31)]
        sp = await fr._spend(days)
        eq("total = сумма расходов водителей по дням книги", sum(sp[x]["spend"] for x in days), d["total"])
        eq("card = безнал по книге", sum(sp[x]["card"] for x in days), d["card"])

        print("── что у записи ────────────────────────────────────────────────")
        a1, a3, a5, a6, b1, b2, v1 = it["a1"], it["a3"], it["a5"], it["a6"], it["b1"], it["b2"], it["v1"]
        eq("заправка: день, имя, район из реестра, принята, деньги, снимок есть, миниатюры нет",
           (a1["day"], a1["driver"], a1["district"], a1["status"], a1["money"], a1["photo"], "thumb" in a1),
           (D1, "Водитель А", "jvc", "approved", True, True, False))
        eq("миниатюр нет ни у одной записи", any("thumb" in i or "car_thumb" in i for i in d["items"]), False)
        eq("мойка: отклонена, когда подана и решена, причина, оба снимка, вердикты, не деньги",
           (a3["status"], a3["at"][:16], a3["decided_at"][:16], a3["decided_note"], a3["photo"], a3["car_photo"],
            a3["photo_ok"], a3["car_ok"], a3["by_driver"], a3["money"]),
           ("rejected", "2026-11-01T12:00", "2026-11-01T13:00", "Не видно мойки", True, True, "no", "no", True, False))
        eq("охрана бутылкой: товар, не деньги, название бутылки", (a5["goods"], a5["money"], a5["bottle"], a5["amount"]), (True, False, "Absolut", 85))
        a7 = it["a7"]
        eq("бутылка охране, пока ждёт: товар, не деньги, в ожидающих — да", (a7["status"], a7["goods"], a7["money"], a7["signed"]),
           ("pending", True, False, 71))

        print("── расходы дня: «ждёт решения» с бутылкой ──────────────────────")
        st, dd = await get("/api/owner/expenses", day=D2)
        ra = next(r for r in dd["drivers"] if r["name"] == "Водитель А")
        eq("строка водителя: ждут заказ в долг 300 и бутылка 71, записей 2",
           (st, ra["pending_sum"], ra["pending_count"]), (200, 371, 2))
        eq("итоги дня: сумма и число ожидающих об одном", (dd["totals"]["pending"], dd["totals"]["pending_count"]), (371, 2))
        eq("принятая бутылка 85 в расходы дня не идёт, ждущая — тоже", (ra["extra_sum"], ra["card_sum"], ra["total"]),
           (60, 60, MEAL_ON + 60))
        eq("заказ в долг: ждёт, не деньги, nocash", (a6["status"], a6["money"], a6["nocash"], a6["auto"]), ("pending", False, True, True))
        eq("нам вернули: со знаком минус, деньги", (b1["signed"], b1["plus"], b1["money"]), (-40, True, True))
        eq("зарплата из выручки: не расход", (b2["salary"], b2["money"], b2["comment"]), (True, False, "Зарплата за октябрь"))
        eq("без чека и безнал", (v1["no_receipt"], it["a4"]["card"], it["a4"]["pay"]), (True, True, "card"))

        print("── люди и категории ────────────────────────────────────────────")
        eq("реестр в порядке районов, уехавший до реестра — следом, away",
           [(p["name"], p["district"], p["away"]) for p in d["drivers"]],
           [("Водитель А", "jvc", False), ("Водитель Б", "bbay", False), ("Водитель В", "", True)])
        eq("категории: питание первой, потом виды трат по порядку",
           [k["id"] for k in d["kinds"]][:4], ["meal", "fuel", "wash", "parking"])
        eq("у «нам вернули» плюс", next(k["plus"] for k in d["kinds"] if k["id"] == "we_got"), True)

        print("── месяц мимо формы и права ────────────────────────────────────")
        st, d2 = await get("/api/owner/expenses/month", month="2026-13")
        eq("кривой месяц — текущий", (st, d2["month"]), (200, xr._biz_day()[:7]))
        st, d3 = await get("/api/owner/expenses/month")
        eq("без месяца — текущий", (st, d3["month"]), (200, xr._biz_day()[:7]))
        st, _ = await get("/api/owner/expenses/month", auth=False, month=M)
        eq("без авторизации — 401", st, 401)

    print("\nИТОГ:", "все проверки прошли" if not FAIL else f"ПРОВАЛЕНО {len(FAIL)}: " + "; ".join(FAIL))
    return 0 if not FAIL else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
