"""Сколько водитель должен сдать — при любом способе оплаты (владелец, 27 сен 2026:
«убедись, что у водителей правильно считаются деньги, которые они должны сдать»).

Проверяется ровно та арифметика, по которой живут три экрана: итоги смены у
водителя, «Сбор выручки» у старшего и «Обзор» у владельца — все зовут
cash_math.

  • наличные — в руках и к сдаче; крипта, перевод, долг и «без оплаты» — нет;
  • чай операторов считается со ВСЕХ доставленных, но с оплаченных мимо
    водителя он достаётся из наличной выручки — отдельной строкой tea_other;
  • расход наличными уменьшает выручку, безналом — не трогает;
  • питание и бонус за допродажу водитель оставляет себе;
  • валюта считается по курсу заказа и сдаётся отдельно;
  • у водителя нет «не сдал»: выручка = взято − чай − расход − питание − бонус;
  • сбор выручки у старшего и итоги водителя дают ОДНО число.

    python3 tools/test_driver_cash.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
import db, cash_math as cm                                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ, РАЙОН, ВОДИТЕЛЬ = "2026-09-27", "jvc", "Алишер"
ЧАЙ = next(iter(cm.tea_rates()))            # позиция, с которой идёт чай оператору
СТАВКА = cm.tea_rates()[ЧАЙ]


def зак(pay, total, *, чайных=0, settle=None, fx=None):
    o = {"order_id": "A" + pay + str(total), "status": "delivered", "total": total,
         "tip": 0, "office_id": РАЙОН, "district_id": РАЙОН, "day": ДЕНЬ, "driver": ВОДИТЕЛЬ,
         "items": [{"id": ЧАЙ, "qty": чайных}] if чайных else [],
         "timestamp": ДЕНЬ + "T14:00:00+00:00", "confirmed_at": ДЕНЬ + "T14:00:00+00:00",
         "payment_method": pay}
    if pay in ("crypto", "transfer"):
        o["paid"] = True
    if settle is not None:
        o["settle"] = settle
    if fx:
        o["pay_fx"] = fx
    return o


def расход(amount, *, pay="cash", kind="fuel", status="approved"):
    return {"kind": kind, "amount": amount, "pay": pay, "status": status, "comment": kind}


async def main():
    db._db = AsyncMongoMockClient()["ambar_drv_cash"]

    print("── чем платили, то и берём ────────────────────────────────────")
    eq("наличные берём", cm.pays_cash(зак("cash", 500)), True)
    eq("крипту НЕ берём", cm.pays_cash(зак("crypto", 300)), False)
    eq("перевод НЕ берём", cm.pays_cash(зак("transfer", 200)), False)
    eq("долг НЕ берём", cm.pays_cash(зак("debt", 400)), False)
    eq("«без оплаты» НЕ берём", cm.pays_cash(зак("free", 100)), False)

    print("── простая смена: 500 наличными и 300 криптой ─────────────────")
    п = cm.piles([зак("cash", 500), зак("crypto", 300)], [], 0)
    eq("взято", п["taken"], 500.0)
    eq("к сдаче", п["revenue"], 500.0)
    eq("на руках", п["in_hand"], 500.0)

    print("── чай операторов ─────────────────────────────────────────────")
    # Две «чайные» бутылки в наличном заказе и одна в криптовом.
    п = cm.piles([зак("cash", 500, чайных=2), зак("crypto", 300, чайных=1)], [], 0)
    eq("чай со всех доставленных", п["tea"], СТАВКА * 3)
    eq("из них с оплаченных мимо водителя", п["tea_other"], СТАВКА * 1)
    eq("К СДАЧЕ = взято − весь чай", п["revenue"], 500.0 - СТАВКА * 3)

    print("── расходы: наличными уменьшают, безналом нет ─────────────────")
    п = cm.piles([зак("cash", 500)], [расход(120), расход(80, pay="card")], 0)
    eq("наличный расход вычтен", п["revenue"], 380.0)
    eq("безнал стоит отдельно", п["card_spent"], 80)
    # Ждущий согласования расход у ВОДИТЕЛЯ вычитается: денег в кармане уже
    # нет, он их потратил. У старшего в «Сборе выручки» — наоборот, не
    # вычитается: владелец может отклонить. Числа за такой день разойдутся
    # ровно на эту сумму, и это единственное место, где они расходятся.
    п = cm.piles([зак("cash", 500)], [расход(120, status="pending")], 0)
    eq("у водителя ждущее вычтено — деньги потрачены", п["revenue"], 380.0)
    eq("и он видит, что решение не принято", п["pending"], 1)

    print("── питание и бонус остаются водителю ──────────────────────────")
    п = cm.piles([зак("cash", 500)], [], 80)
    eq("питание вычтено из сдачи", п["revenue"], 420.0)
    eq("и оно у него в руках", п["meal"], 80)

    print("── валюта: по курсу заказа, сдаётся отдельно ──────────────────")
    п = cm.piles([зак("cash", 400, settle={"taken": 400, "fx": {"code": "USD", "amount": 110}})], [], 0)
    eq("валюта отдельной строкой", [(x["code"], x["amount"]) for x in п["fx"]], [("USD", 110.0)])
    eq("в дирхамах — по курсу заказа", п["taken"], 400.0)

    print("── долг виден, но денег по нему нет ───────────────────────────")
    п = cm.piles([зак("cash", 500), зак("debt", 250)], [], 0)
    eq("к сдаче только наличный", п["revenue"], 500.0)
    eq("долг показан строкой", п["debt"], 250.0)

    print("── старший и водитель считают одно и то же ────────────────────")
    import owner_routes as own, config_staff as staff
    staff.drivers = lambda: [{"name": ВОДИТЕЛЬ, "district": РАЙОН}]
    staff.DISTRICT_DRIVERS = {РАЙОН: [ВОДИТЕЛЬ]}
    заказы = [зак("cash", 500, чайных=2), зак("crypto", 300, чайных=1), зак("transfer", 200)]
    await db._db.driver_days.insert_one(
        {"_id": f"{ДЕНЬ}:{ВОДИТЕЛЬ}", "day": ДЕНЬ, "driver": ВОДИТЕЛЬ,
         "working": True, "extras": [расход(120)]})
    a = (await own._cash_amounts(ДЕНЬ, заказы))[РАЙОН]
    п = cm.piles(заказы, [расход(120)], staff.MEAL_WORKING)
    eq("наличные у старшего = взятое водителем", a["cash"], int(п["taken"]))
    eq("чай тот же", a["tips"], п["tea"])
    eq("ВЫРУЧКА РАЙОНА = ВЫРУЧКА ВОДИТЕЛЯ", a["net"], int(round(п["revenue"])))

    print("── единственное расхождение: ждущий согласования расход ───────")
    await db._db.driver_days.update_one(
        {"_id": f"{ДЕНЬ}:{ВОДИТЕЛЬ}"},
        {"$set": {"extras": [расход(120, status="pending")]}})
    a2 = (await own._cash_amounts(ДЕНЬ, заказы))[РАЙОН]
    п2 = cm.piles(заказы, [расход(120, status="pending")], staff.MEAL_WORKING)
    eq("у старшего ждущее НЕ вычтено", a2["net"] - int(round(п2["revenue"])), 120)
    eq("и он видит его отдельной строкой", a2["spend_pending"], 120)

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
