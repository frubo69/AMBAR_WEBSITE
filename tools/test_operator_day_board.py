"""Итоги смены у оператора: по районам и по водителям, штуки и деньги
(владелец, 30 сен 2026: «не видят, сколько сегодня на районах было заказов и
сколько это в деньгах… и сколько какой водитель наработал»).

Считается тем же правилом, что смена: день заказа — смена, где его приняли;
доставленное — выручка; «без оплаты» — в штуках есть, в деньгах нет;
тест-заказы и чужие дни не в счёт; незакрытое — «в работе», но не на вчера.

    python3 tools/test_operator_day_board.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, operator_routes as opr, bizday                         # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

N = [0]
async def order(day, office, driver, total, status="delivered", **kw):
    N[0] += 1
    ts = (bizday.day_start(day) + timedelta(hours=3, minutes=N[0])).astimezone(timezone.utc)
    iso = ts.isoformat().replace("+00:00", "")
    await db._db.orders.insert_one({"order_id": f"D{N[0]:03d}", "status": status, "office_id": office,
                                    "driver": driver, "total": total, "timestamp": iso,
                                    "confirmed_at": iso, "day": day, "items": [], **kw})

class Req:
    def __init__(self, **q): self.query = q
    def get(self, k, d=None): return d

async def board(**q):
    h = opr.handle_day_board
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    db._ORDERS_WIN.clear()
    return json.loads((await h(Req(**q))).body)

async def main():
    db._db = AsyncMongoMockClient()["ambar_dayboard"]
    async def scope(request, who): return set(), ["jvc"]
    opr._op_scope = scope
    async def fresh(): return opr._districts()
    opr._fresh_districts = fresh
    today = bizday.biz_day()
    вчера = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    d0 = opr._districts()[0]; d1 = opr._districts()[1]
    A, B = (d0["drivers"] + ["А", "Б"])[:2]
    await order(today, d0["id"], A, 1000)
    await order(today, d0["id"], A, 500)
    await order(today, d0["id"], B, 300)
    await order(today, d0["id"], B, 1250, payment_method="free")
    await order(today, d0["id"], A, 700, status="approved")
    await order(today, d0["id"], "", 400, status="pending")
    await order(today, d0["id"], A, 900, status="cancelled")
    await order(today, d0["id"], A, 5000, test=True)
    await order(today, d1["id"], "Чужой", 200)
    await order(вчера, d0["id"], A, 7000)
    await order(вчера, d0["id"], A, 100, status="approved")     # висит со вчера

    r = await board(**{"as": "x"})
    x = next(z for z in r["districts"] if z["id"] == d0["id"])
    eq("сегодня: день", (r["day"], r["today"]), (today, True))
    eq("район: доставлено и выручка — «без оплаты» в штуках, не в деньгах", (x["done"], x["aed"]), (4, 1800))
    eq("район: в работе — и сегодняшний, и висящий со вчера", (x["work"], x["work_aed"]), (2, 800))
    eq("район: новые и отменённые", (x["new"], x["cancelled"]), (1, 1))
    drv = {v["name"]: (v["done"], v["aed"], v["work"]) for v in x["drivers"]}
    eq("водитель А", drv[A], (2, 1500, 2))
    eq("водитель Б", drv[B], (2, 300, 0))
    eq("водители — по выручке", [v["name"] for v in x["drivers"]][:2], [A, B])
    y = next(z for z in r["districts"] if z["id"] == d1["id"])
    eq("чужой водитель стоит там, куда вёз", ("Чужой", 1, 200) in [(v["name"], v["done"], v["aed"]) for v in y["drivers"]], True)
    eq("водитель района без заказов — строкой с нулём",
       all(any(v["name"] == n for v in y["drivers"]) for n in d1["drivers"]), True)
    eq("итог по всем районам", (r["total"]["done"], r["total"]["aed"], r["total"]["work"]), (5, 2000, 2))
    eq("районы по порядку кодов", [z["code"] for z in r["districts"]], sorted(z["code"] for z in r["districts"]))
    eq("мои районы", r["mine"], ["jvc"])
    eq("в ответе только числа и имена", sorted(x.keys()),
       sorted(["id", "code", "name", "operator", "done", "aed", "work", "work_aed", "new", "cancelled", "drivers", "recon_alert"]))

    r = await board(**{"as": "x", "day": "yesterday"})
    x = next(z for z in r["districts"] if z["id"] == d0["id"])
    eq("вчера: день", (r["day"], r["today"]), (вчера, False))
    eq("вчера: только доставленное того дня", (x["done"], x["aed"], x["work"], x["new"]), (1, 7000, 0, 0))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
