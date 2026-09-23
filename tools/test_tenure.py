"""Премия за стаж (владелец, 23 сен 2026: «кто полгода работает — получает
премию 1000 дирхам, год — 2 тысячи»).

Правила, которые он назвал:
  • ступень — полгода работы, ступень стоит 1000; не брал — копится;
  • взял премию на восьмом месяце — на двенадцатом снова доступна тысяча;
  • уехал — отсчёт начинается заново с дня возвращения (его выбор из двух:
    «сброс», а не «пауза»);
  • премия только водителям и операторам; дата приезда не указана — не
    считаем и говорим об этом словами;
  • выплата — обычная премия в зарплатах, водителю поздравление в бот.

Настоящие tenure.state / list_for и настоящая ручка выплаты на mongomock.

    python3 tools/test_tenure.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
from aiohttp.test_utils import make_mocked_request              # noqa: E402
import db, tenure, finance_routes as fr                         # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

СЕГОДНЯ = "2026-09-23"
def чел(имя, периоды, роль="driver"):
    return {"name": имя, "title": имя, "role": роль, "work": периоды}
def премия(имя, сумма, день, ступень=1, отменена=False):
    d = {"name": имя, "kind": "bonus", "amount": сумма, "day": день, "tenure": ступень}
    if отменена: d["cancelled_at"] = день
    return d


def правила():
    print("── сколько наработал ──────────────────────────────────────────")
    s = tenure.state(чел("Авазбек", [{"from": "2025-12-10", "to": ""}]), [], СЕГОДНЯ)
    eq("9 месяцев — одна ступень, тысяча ждёт", (s["months"], s["earned"], s["due"]), (9, 1000, 1000))
    eq("и видно, когда будет следующая", s["next_at"], "2026-12-10")
    s = tenure.state(чел("Новенький", [{"from": "2026-09-01", "to": ""}]), [], СЕГОДНЯ)
    eq("меньше полугода — премии нет, кнопка ждёт даты", (s["due"], s["next_at"]), (0, "2027-03-01"))
    s = tenure.state(чел("Старожил", [{"from": "2025-09-23", "to": ""}]), [], СЕГОДНЯ)
    eq("ровно год — две ступени, копится 2000", (s["months"], s["earned"], s["due"]), (12, 2000, 2000))

    print("── брал или не брал ───────────────────────────────────────────")
    ч = чел("Худоба", [{"from": "2025-09-23", "to": ""}])
    s = tenure.state(ч, [премия("Худоба", 1000, "2026-05-23")], СЕГОДНЯ)
    eq("взял тысячу на восьмом месяце — на двенадцатом доступна ещё тысяча",
       (s["earned"], s["paid"], s["due"]), (2000, 1000, 1000))
    s = tenure.state(ч, [премия("Худоба", 1000, "2026-05-23"), премия("Худоба", 1000, СЕГОДНЯ, 2)], СЕГОДНЯ)
    eq("взял обе — больше нечего", s["due"], 0)
    s = tenure.state(ч, [премия("Худоба", 1000, "2026-05-23", отменена=True)], СЕГОДНЯ)
    eq("отменённая премия не считается выплаченной", s["due"], 2000)
    s = tenure.state(ч, [{"name": "Худоба", "kind": "bonus", "amount": 500, "day": "2026-06-01"}], СЕГОДНЯ)
    eq("премия, вписанная руками (без пометки стажа), стаж не закрывает", s["due"], 2000)

    print("── уехал и вернулся ───────────────────────────────────────────")
    s = tenure.state(чел("Вернувшийся", [{"from": "2025-01-01", "to": "2026-06-30"},
                                         {"from": "2026-08-01", "to": ""}]), [], СЕГОДНЯ)
    eq("считаем с возвращения, прошлое сгорело", (s["start"], s["months"], s["due"]),
       ("2026-08-01", 1, 0))
    s = tenure.state(чел("Уехал", [{"from": "2025-01-01", "to": "2026-08-01"}]), [], СЕГОДНЯ)
    eq("уехал и не вернулся — стаж замер на дне отъезда, не растёт",
       (s["months"], s["earned"], s["why"]), (19, 3000, "Уехал — стаж не идёт"))
    s = tenure.state(чел("Вернувшийся", [{"from": "2025-01-01", "to": "2026-06-30"},
                                         {"from": "2026-08-01", "to": ""}]),
                     [премия("Вернувшийся", 1000, "2025-08-01")], СЕГОДНЯ)
    eq("старые выплаты в новый счёт не лезут", s["paid"], 0)

    print("── кому положена ──────────────────────────────────────────────")
    люди = [чел("Водитель", [{"from": "2025-12-10", "to": ""}]),
            чел("Оператор", [{"from": "2025-12-10", "to": ""}], "operator"),
            чел("Старший", [{"from": "2025-12-10", "to": ""}], "senior"),
            чел("Офис", [{"from": "2025-12-10", "to": ""}], "other")]
    л = tenure.list_for(люди, [], СЕГОДНЯ)
    eq("водители, операторы и старший — да, офис — нет", [r["title"] for r in л],
       ["Водитель", "Оператор", "Старший"])
    s = tenure.state(чел("Безымянный", []), [], СЕГОДНЯ)
    eq("даты приезда нет — не считаем и говорим словами",
       (s["due"], s["next_at"], s["why"]), (0, "", "Дата приезда не указана"))


async def ручка():
    print("── выплата ────────────────────────────────────────────────────")
    db._db = AsyncMongoMockClient()["ambar_tenure"]
    await db._db.fin_people.insert_one({"_id": "Азиз", "role": "driver",
                                        "work": [{"from": "2026-03-21", "to": ""}]})
    import config_staff as staff
    staff.SENIOR_OPERATORS = []
    staff.operators = lambda: []
    staff.drivers = lambda: [{"name": "Азиз", "district": "silicon"}]
    fr._biz_day = lambda *a, **k: СЕГОДНЯ
    сказали = []
    async def tell(name, text): сказали.append((name, text))
    fr._pn.tell_safe = tell

    async def зови(h, body=None):
        r = make_mocked_request("POST" if body else "GET", "/x")
        r["owner_id"] = 1; r["owner_user"] = {"id": 1}
        if body is not None:
            async def js(): return body
            r.json = js
        while hasattr(h, "__wrapped__"): h = h.__wrapped__
        resp = await h(r)
        return resp.status, json.loads(resp.text)

    st, лист = await зови(fr.handle_tenure)
    a = next(r for r in лист["people"] if r["name"] == "Азиз")
    eq("в листе: 6 месяцев, тысяча к выплате", (a["months"], a["due"], лист["ready"]), (6, 1000, 1))

    st, r = await зови(fr.handle_tenure_pay, {"name": "Азиз", "as": "fixxxik"})
    eq("выплатили", st, 200)
    eq("после выплаты премии больше не ждут",
       next(x for x in r["bonus"]["people"] if x["name"] == "Азиз")["due"], 0)
    eq("запись легла в зарплаты премией за стаж",
       [(i.get("kind"), i.get("amount"), i.get("tenure")) async for i in db._db.fin_pay_items.find({})],
       [("bonus", 1000, 1)])
    eq("водителю ушло поздравление", ("Поздравляем" in сказали[0][1], сказали[0][0]), (True, "Азиз"))

    st, r = await зови(fr.handle_tenure_pay, {"name": "Азиз", "as": "fixxxik"})
    eq("второе нажатие второй тысячи не даёт", (st, r.get("error")), (409, "not_yet"))
    st, r = await зови(fr.handle_tenure_pay, {"name": "Нет такого", "as": "fixxxik"})
    eq("незнакомому — отказ", st, 404)


async def main():
    правила()
    await ручка()
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
