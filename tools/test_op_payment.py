"""Оператор выбирает способ оплаты: наличные, крипта, перевод (владелец, 27 сен 2026).

До этого крипту вбивали обычным заказом с комментарием «крипта»: в выручке
она была, а система считала её наличными в кармане водителя. За пять дней
20–24.09 так прошло 4 018 AED — отсюда и расхождение «Обзора» со сданным.

  • наличные — как было: заказ считается наличными, идёт водителю на руки и
    в сбор выручки;
  • крипта и перевод — в выручку идут, в наличные водителя и в сбор выручки
    НЕ идут; заказ помечается оплаченным, чтобы водитель не поехал забирать
    деньги второй раз;
  • способ можно поправить у заказа — «оплачено» переставляется вместе с ним;
  • долг и «без оплаты» панель не перетирает: их ставят отдельно;
  • чужой способ — отказ, а не молчаливые наличные.

    python3 tools/test_op_payment.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
import db, cash_math, finance_routes as FR                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ = "2026-09-27"


def заказ(pay, total=500):
    o = {"order_id": "A" + pay, "status": "delivered", "total": total, "tip": 0,
         "office_id": "jvc", "district_id": "jvc", "day": ДЕНЬ, "items": [],
         "timestamp": ДЕНЬ + "T14:00:00+00:00", "confirmed_at": ДЕНЬ + "T14:00:00+00:00"}
    if pay != "cash":
        o["payment_method"] = pay; o["paid"] = True
    else:
        o["payment_method"] = "cash"
    return o


async def main():
    db._db = AsyncMongoMockClient()["ambar_op_pay"]

    print("── что держит водитель ────────────────────────────────────────")
    eq("наличные — его деньги", cash_math.pays_cash(заказ("cash")), True)
    eq("КРИПТА — НЕ ЕГО", cash_math.pays_cash(заказ("crypto")), False)
    eq("ПЕРЕВОД — НЕ ЕГО", cash_math.pays_cash(заказ("transfer")), False)
    eq("и оба помечены оплаченными", [cash_math.is_prepaid(заказ(p))
                                      for p in ("crypto", "transfer")], [True, True])

    print("── две пачки водителя ─────────────────────────────────────────")
    п = cash_math.piles([заказ("cash", 500), заказ("crypto", 300), заказ("transfer", 200)], [], 0)
    eq("взято только по наличному заказу", п["taken"], 500.0)
    eq("наличных заказов один из трёх", п["orders_cash"], 1)
    eq("на руках — он же", п["in_hand"], 500.0)

    print("── выручка дня: все три в ней ─────────────────────────────────")
    for o in (заказ("cash", 500), заказ("crypto", 300), заказ("transfer", 200)):
        await db._db.orders.insert_one(dict(o, _id=o["order_id"]))
    s = (await FR._sales([ДЕНЬ]))[ДЕНЬ]
    eq("валовая — вся сумма", s["gross"], 1000)
    eq("наличными — только наличный", s["cash"], 500)
    eq("криптой — свой", s["crypto"], 300)
    eq("переводом — в безнал", s["card"], 200)
    eq("по районам наличных — только наличный", s["cash_by"].get("jvc"), 500)

    print("── сбор выручки ───────────────────────────────────────────────")
    import owner_routes as own
    import config_staff as staff
    staff.drivers = lambda: [{"name": "Алишер", "district": "jvc"}]
    staff.DISTRICT_DRIVERS = {"jvc": ["Алишер"]}
    a = await own._cash_amounts(ДЕНЬ, [заказ("cash", 500), заказ("crypto", 300),
                                       заказ("transfer", 200)])
    eq("СОБИРАЕМ ТОЛЬКО НАЛИЧНЫЕ", a["jvc"]["cash"], 500)

    print("── ручка создания ─────────────────────────────────────────────")
    import operator_routes as op
    from aiohttp.test_utils import make_mocked_request
    # Проверка способа стоит ПОСЛЕ района и водителя — подставляем их, иначе
    # ручка отвечает «нет водителя» и до способа не доходит.
    op._drivers_of = lambda *a, **k: ["Алишер"]
    async def _свободен(n): return False
    op._released_now = _свободен
    async def создать(pay):
        r = make_mocked_request("POST", "/x")
        r["op_id"] = 1; r["op_user"] = {"id": 1}
        # Проверка способа стоит после состава — кладём настоящую позицию.
        pid = next(iter(op._catalog_by_id()))
        async def js(): return {"items": [{"id": pid, "qty": 1}],
                                "district_id": "jvc", "driver": "Алишер",
                                "payment_method": pay}
        r.json = js
        h = op.handle_create
        while hasattr(h, "__wrapped__"): h = h.__wrapped__
        resp = await h(r)
        return resp.status, json.loads(resp.text)
    st, b = await создать("naличные")
    eq("чужой способ — отказ", (st, b.get("error")), (400, "bad_payment"))

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
