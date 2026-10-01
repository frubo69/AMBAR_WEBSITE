"""Кошелёк: сколько в нём НАШИХ денег и сверка переводов с заказами, которые
оператор отметил криптой (владелец, 1 окт 2026).

  • наших = остаток USDT × 3.5 — курс, по которому платил клиент; разница до
    рынка (3.65–3.67) уходит посреднику, в наши деньги она не входит;
  • заказы криптой месяца: сколько их, под какими перевод найден (счёт или
    привязка), под какими нет;
  • сверка: заказ с отметкой «крипта» идёт первым — наличный заказ на ту же
    сумму ему не соперник; два крипто-заказа на одну сумму — спорно.

    python3 tools/test_wallet_ours.py
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, bizday, tron, wallet_routes as wr                      # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

СЕЙЧАС = datetime.now(timezone.utc)
ДЕНЬ = bizday.biz_day()
БАЛАНС = {"MAIN": 415.0, "OLD": 10.5}
ЛЕНТА = {"MAIN": [], "OLD": []}

async def _bal(a): return {"usdt": БАЛАНС[a], "trx": 9.0}
async def _tr(a): return list(ЛЕНТА[a])

def перевод(tx, usdt, часов_назад=1.0, пришло=True):
    return {"txid": tx, "amount": usdt, "in": пришло, "peer": "TPeer000000000000",
            "ts": int((СЕЙЧАС - timedelta(hours=часов_назад)).timestamp() * 1000)}

async def заказ(oid, total, способ="", часов_назад=1.0, **kw):
    t = СЕЙЧАС - timedelta(hours=часов_назад)
    await db._db.orders.insert_one({
        "order_id": oid, "total": total, "status": "delivered", "day": ДЕНЬ,
        "timestamp": t.isoformat().replace("+00:00", ""), "confirmed_at": t.isoformat(),
        "customer_name": "Клиент", "office_id": "jvc",
        **({"payment_method": способ, "paid": True} if способ else {}), **kw})

async def main():
    db._db = AsyncMongoMockClient()["ambar_wallet_ours"]
    tron.get_balance, tron.get_transfers = _bal, _tr
    wr.TRON_RECEIVE_ADDRESS = "MAIN"
    async def _old(): return ["OLD"]
    wr.old_addresses = _old
    wr._rate = lambda: 3.5

    print("Наших денег")
    d = await wr._build()
    eq("текущий кошелёк: 415 USDT × 3.5", (d["balance"]["usdt"], d["ours_aed"]), (415.0, 1452.5))
    eq("старый кошелёк — свой счёт", d["old"][0]["ours_aed"], 36.75)
    eq("оба вместе и курс в ответе", (d["usdt_all"], d["ours_all_aed"], d["rate"]), (425.5, 1489.25, 3.5))
    eq("по рынку было бы больше — это доля посредника", round(425.5 * 3.6725 - d["ours_all_aed"], 2), 73.4)
    БАЛАНС["MAIN"] = 0.43203
    eq("мелочь не теряется", (await wr._build())["ours_aed"], 1.51)

    print("\nЗаказы криптой против переводов")
    await заказ("A1", 350, "crypto")                      # оператор отметил, перевод привяжем
    await заказ("A2", 700, "crypto", 3)                   # отметил, перевода нет
    await заказ("A3", 1050, "crypto", crypto_paid=True)   # счёт из приложения
    await заказ("N1", 350)                                # наличные на ту же сумму, что A1
    await заказ("T1", 430, "transfer")                    # перевод на карту — не крипта
    await db._db.crypto_invoices.insert_one({"_id": "i1", "order_id": "A3", "txid": "tx-a3",
                                             "status": "confirmed", "amount_usdt": 300.0})
    ЛЕНТА["MAIN"] = [перевод("tx-a3", 300.0), перевод("tx-a1", 100.0, 0.5)]
    z = (await wr._build())["orders"]
    eq("крипто-заказов три, перевод на карту не в счёт", (z["n"], z["aed"], z["usdt"]), (3, 2100, 600.0))
    eq("перевод найден только по счёту", (z["found_n"], z["found_aed"]), (1, 1050))
    eq("без перевода — два, свежий первым", [(o["order_id"], o["usdt"]) for o in z["free"]],
       [("A1", 100.0), ("A2", 200.0)])

    print("\nСверка: отметка оператора главнее совпадения суммы")
    m = await wr._match_orders(False)
    eq("100 USDT: наличный N1 на те же 350 не мешает — берём A1, уверенно",
       [(p["order_id"], p["sure"], p["crypto"]) for p in m["pairs"]], [("A1", True, True)])
    m = await wr._match_orders(True, "STAR")
    eq("привязано", m["taken"], 1)
    z = (await wr._build())["orders"]
    eq("после привязки: найдено два, без перевода один", (z["found_n"], z["free_n"], z["free_aed"]), (2, 1, 700))

    print("\nДва крипто-заказа на одну сумму — решает человек")
    await заказ("A4", 700, "crypto", 2)
    ЛЕНТА["MAIN"].append(перевод("tx-x", 200.0, 2.5))
    m = await wr._match_orders(False)
    eq("спорно, само не привязывает", [(p["sure"], p["crypto"], p["others"]) for p in m["pairs"]], [(False, True, 1)])

    print("\nБез отметок — как раньше, по сумме и дню")
    await db._db.orders.delete_many({"payment_method": "crypto"})
    await заказ("N2", 700, "", 2)
    m = await wr._match_orders(False)
    eq("наличный заказ на ту же сумму находится, но без пометки",
       [(p["order_id"], p["sure"], p["crypto"]) for p in m["pairs"]], [("N2", True, False)])

    print("\nСама ручка сверки отвечает (с 20 сен по 1 окт её подменяла сверка отчёта)")
    import json
    class Зап(dict):
        method = "GET"; query = {}
        async def json(self): return {}
    h = wr.handle_match
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    r = await h(Зап(owner_id=1))
    eq("GET /api/owner/wallet/match", (r.status, sorted(json.loads(r.text))), (200, ["ambiguous", "orphans", "pairs", "taken"]))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
