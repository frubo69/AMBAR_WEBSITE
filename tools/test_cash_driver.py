"""Водитель за смену в «Сборе выручки» (cash_driver.py, владелец, 30 окт 2026:
«раскрыть и посмотреть, какой водитель сколько сдал по факту; на него можно
нажать и посмотреть на отдельной странице, сколько за смену он заработал,
сколько у него расходов, какими они были, есть ли те, что не согласовали или
ещё не согласованные, сколько он чая наработал»).

  • строки района: из реестра (кроме уехавших), возил заказы района, делал
    сверку; кто не работал — строки нет; без сверки — по приложению, cash None;
  • страница: пачки той же арифметикой, что итоги смены (cash_math.piles) —
    ждущие расходы вычтены, отклонённые нет; расходы все, с решениями;
    мимо наличных в «ждёт» не идёт; чай по людям; заказы строками с «взял»;
  • /api/owner/cash-round: у района recon — строки водителей;
    /api/owner/cash-round/driver — страница, без имени 400.

    python3 tools/test_cash_driver.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp import web                                             # noqa: E402
from aiohttp.test_utils import TestClient, TestServer                # noqa: E402
from mongomock_motor import AsyncMongoMockClient                     # noqa: E402
import db, owner_auth, owner_routes as own, config_staff as staff    # noqa: E402
import cash_driver as cd, cash_math as cm                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = "2026-10-29"
OID = list(own.OFFICE_IDS)[1]
OTHER = list(own.OFFICE_IDS)[0]
TEA = next(pid for pid, r in cm.tea_rates().items() if r == 50)     # бутылка с чаем 50
ROSTER = [{"name": "Али", "district": OID, "operator": "Умар"},
          {"name": "Фарух", "district": OID, "operator": "Умар"},
          {"name": "Даврон", "district": OID, "operator": "Умар"},
          {"name": "Улетел", "district": OID, "operator": "Умар"},
          {"name": "Чужой", "district": OTHER, "operator": "Оператор"}]
owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)


async def _all_orders(*a, **k):
    return {o["order_id"]: o async for o in db._db.orders.find({}, {"_id": 0})}


def setup():
    db._db = AsyncMongoMockClient()["cash_driver"]
    db.orders_from = _all_orders
    staff.drivers = lambda all=False: list(ROSTER)
    staff.all_drivers = lambda: list(ROSTER)
    staff.is_away = lambda n: n == "Улетел"
    staff.base_district = lambda n: next((d["district"] for d in ROSTER if d["name"] == n), "")
    staff.base_operator = lambda d: "Умар" if d == OID else "Оператор"
    staff.is_test_driver = lambda n: False
    staff.test_driver_names = lambda: set()
    async def _sync(): return None
    staff.sync = _sync


def заказ(oid, driver, items, total, hm="16:00", pay="cash", office=OID, **kw):
    return {"order_id": oid, "status": "delivered", "driver": driver, "office_id": office, "payment_method": pay,
            "items": items, "total": total, "day": D, "source": "manual", "created_by_name": "Умар",
            "timestamp": f"{D}T{hm}:00", "confirmed_at": f"{D}T{hm}:00", "delivered_at": f"{D}T{hm}:40",
            "address": f"Marina · {oid}", **kw}


async def main():
    setup()
    # Али: 300 с чаем + 100, расходы всех видов, сверка с разницей.
    await db._db.orders.insert_many([
        заказ("a1", "Али", [{"id": TEA, "qty": 1, "price": 300, "line_total": 300},
                            {"id": "p1", "qty": 1, "price": 100, "line_total": 100}], 400, hm="15:00"),
        заказ("a2", "Али", [{"id": "p1", "qty": 1, "price": 100, "line_total": 100}], 100, hm="17:00"),
        # Фарух: один заказ, рассчитались не ровно; дома (питание 40).
        заказ("f1", "Фарух", [{"id": "p1", "qty": 2, "price": 100, "line_total": 200}], 200, settle={"taken": 190}),
        # Гость — не из реестра, но возил заказ района.
        заказ("g1", "Гость", [{"id": "p1", "qty": 1, "price": 100, "line_total": 100}], 150),
    ])
    await db._db.driver_days.insert_many([
        {"day": D, "driver": "Али", "working": True, "shift_open_at": f"{D}T14:02:00+00:00", "extras": [
            {"id": "e1", "amount": 120, "comment": "ENOC", "kind": "fuel", "kind_t": "Заправка", "status": "approved", "pay": "cash", "photo": True, "at": f"{D}T15:05:00"},
            {"id": "e2", "amount": 60, "comment": "", "kind": "wash", "kind_t": "Мойка", "status": "pending", "pay": "cash", "photo": True, "at": f"{D}T17:40:00"},
            {"id": "e3", "amount": 20, "comment": "у Dusit", "kind": "parking", "kind_t": "Парковка", "status": "rejected", "pay": "cash",
             "decided_note": "без чека", "decided_at": f"{D}T18:00:00", "at": f"{D}T16:10:00"},
            {"id": "e4", "amount": 100, "comment": "ADNOC", "kind": "fuel", "kind_t": "Заправка", "status": "approved", "pay": "card", "photo": True, "at": f"{D}T18:30:00"},
            {"id": "e5", "amount": 15, "comment": "#a2 · 5%", "kind": "upsell", "kind_t": "Бонус за допродажу", "status": "approved", "auto": True, "at": f"{D}T17:05:00"},
            {"id": "e6", "amount": 100, "comment": "#x · заказ в долг", "kind": "owed_us", "kind_t": "Нам должны", "status": "pending", "auto": True, "nocash": True, "at": f"{D}T19:00:00"},
        ]},
        {"day": D, "driver": "Фарух", "working": False, "extras": []},
    ])
    await db.recon_put(D, "Али", {"district": OID, "cash": 100, "ok": [1], "fixes": [
        {"pid": "p1", "name": "Absolut 1 ltr", "delta": 1, "at": f"{D}T20:00:00", "ok": None, "by": ""}], "fixes_status": "sent"})

    print("── строки района ───────────────────────────────────────────")
    orders = list((await _all_orders()).values())
    rows = await cd.rows(D, orders)
    r = {x["name"]: x for x in rows[OID]}
    eq("в районе: Али, Гость (возил заказ), Фарух; Даврон не работал, Улетел уехал", sorted(r), ["Али", "Гость", "Фарух"])
    eq("у другого района пусто", rows[OTHER], [])
    # Али: взято 400 + 100 = 500; чай 50; расход 120 + 60 (ждёт — вычтен, как у водителя);
    # питание 80; бонус 15; отклонённое и безнал — нет → выручка 175, к сдаче 225.
    eq("Али: по приложению 225, насчитал 100, −125, ждёт оператора, 1 правка, 1 расход ждёт",
       (r["Али"]["cash_app"], r["Али"]["cash"], r["Али"]["diff"], r["Али"]["mismatch"], r["Али"]["fix_open"],
        r["Али"]["exp_pending"], r["Али"]["orders"], r["Али"]["shift"]),
       (225, 100, -125, True, 1, 1, 2, "open"))
    eq("Фарух без сверки: взял 190, дома 40 → по приложению 150; cash None; не начинал",
       (r["Фарух"]["cash_app"], r["Фарух"]["cash"], r["Фарух"]["started"], r["Фарух"]["orders"], r["Фарух"]["working"]),
       (150, None, False, 1, False))
    eq("Гость: 150 по приложению, расходов нет", (r["Гость"]["cash_app"], r["Гость"]["exp_pending"]), (150, 0))

    print("── страница водителя ────────────────────────────────────────")
    p = await cd.driver_day(D, "Али")
    eq("район и оператор", (p["code"], p["operator"], p["opened_at"][:10]), (own.OFFICE_CODES[OID], "Умар", D))
    eq("пачки: выручка 175, чай 50, к сдаче 225, наличные 500, 2 заказа",
       (p["hand"]["revenue_aed"], p["tea"], p["cash_app"], p["hand"]["taken"], p["hand"]["orders_cash"]), (175, 50, 225, 500, 2))
    eq("расходы: 7 строк (питание + 6), из наличных 180, из них ждёт 60 (1), отклонено 20 (1), безналом 100",
       (p["exp"]["n"], p["exp"]["cash"], p["exp"]["pending"], p["exp"]["pending_n"], p["exp"]["rejected"], p["exp"]["rejected_n"], p["exp"]["card"]),
       (7, 180, 60, 1, 20, 1, 100))
    eq("питание первой строкой, дальше по времени; у отклонённой — причина; «нам должны» мимо наличных",
       [(x["kind"], x["status"], x["nocash"], x["decided_note"]) for x in p["items"]],
       [("meal", "approved", False, ""), ("fuel", "approved", False, ""), ("parking", "rejected", False, "без чека"),
        ("upsell", "approved", False, ""), ("wash", "pending", False, ""), ("fuel", "approved", False, ""),
        ("owed_us", "pending", True, "")])
    eq("сверка: насчитал 100, разница −125, ждёт оператора, правка без ответа",
       (p["recon"]["cash"], p["recon"]["diff"], p["recon"]["mismatch"], p["recon"]["fix_open"]), (100, -125, True, 1))
    eq("чай по людям: Умар 50", p["tea_by"], [{"who": "Умар", "aed": 50}])
    eq("своё: питание 80, бонус 15", p["keep"], {"meal": 80, "bonus": 15})
    eq("заказы по времени, чай у первого", [(o["id"], o["aed"], o["cash"], o["tea"], o["pay"]) for o in p["orders"]],
       [("a1", 400, 400, 50, ""), ("a2", 100, 100, 0, "")])
    f = await cd.driver_day(D, "Фарух")
    eq("Фарух: взял 190 при сумме 200 — видно в строке заказа", [(o["aed"], o["cash"]) for o in f["orders"]], [(200, 190)])
    eq("Фарух: сверку не делал", (f["recon"]["started"], f["recon"]["cash"]), (False, None))
    n = await cd.driver_day(D, "Никто")
    eq("незнакомый водитель — пусто", n["empty"], True)

    print("── ручки ────────────────────────────────────────────────────")
    app = web.Application(); own.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(path, **params):
            r = await cl.get(path, params=params, headers={"Authorization": "tma 1"})
            return r.status, await r.json()
        st, cr = await get("/api/owner/cash-round", day=D)
        x = next(d for d in cr["districts"] if d["id"] == OID)
        eq("cash-round: у района строки водителей", (st, [r["name"] for r in x["recon"]]), (200, ["Али", "Гость", "Фарух"]))
        eq("карточка района считает только согласованное: расход 255 (питание 80 + 40, заправка 120, бонус 15), ждёт 60",
           (x["spend"], x["spend_pending"]), (255, 60))
        st, p = await get("/api/owner/cash-round/driver", day=D, name="Али")
        eq("страница по ручке", (st, p["name"], p["cash_app"]), (200, "Али", 225))
        st, e = await get("/api/owner/cash-round/driver", day=D)
        eq("без имени — 400", (st, e["error"]), (400, "bad_name"))
        st, e = await get("/api/owner/cash-round/driver", day="вчера", name="Али")
        eq("кривой день — 400", (st, e["error"]), (400, "bad_day"))
        r = await cl.get("/api/owner/cash-round/driver", params={"day": D, "name": "Али"})
        eq("без подписи — отказ", r.status in (401, 403), True)

    print("\nИТОГ:", "провалено " + str(len(FAIL)) if FAIL else "всё прошло")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
