"""Заявку правят и операторы (владелец, 21 сен 2026: «могут ли операторы
увидеть эту заявку и отредактировать её, а старший утром откроет STAR и
увидит уже отредактированную. Им нужен только этот функционал — сугубо
редактирование»).

mongomock + настоящие operator_routes и stock_routes. Проверяем главное: что
это ОДНА заявка, а не копия, и что оператор не получил лишнего.

  • оператор читает ту же заявку, что и старший, — теми же числами;
  • правка оператора ложится в ту же клетку «день × позиция × район», и
    старший видит её у себя;
  • пустое значение снимает правку — расчёт возвращается;
  • чужой день и чужая позиция отбиваются так же, как у старшего;
  • ручки операторов закрыты охраной, а обработчик — один на оба приложения:
    разойтись правилам правки нельзя.
"""
import asyncio, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ.setdefault("OPERATOR_IDS", "501")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, operator_routes as OP, stock_routes as SR

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

ДЕНЬ = "2026-09-20"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def голый(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def оператор(path, body=None, method="GET", query=""):
    r = make_mocked_request(method, path + query)
    r["operator_id"] = 501
    if body is not None:
        r._read_bytes = json.dumps(body).encode()
    h = голый(OP.handle_op_order_edit if method == "POST" else OP.handle_op_order)
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def старший(path, query=""):
    r = make_mocked_request("GET", path + query)
    r["owner_id"] = 1
    resp = await голый(SR.handle_order)(r)
    return json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_op_order"]
    SR._biz_day = lambda *a, **k: ДЕНЬ
    SR.base_drop()
    pid = next(iter(SR._catalog()))

    print("── одна заявка на двоих ───────────────────────────────────────")
    st, у_оп = await оператор("/api/operator/stock/order")
    у_ст = await старший("/api/owner/stock/order")
    eq("оператор читает заявку", st, 200)
    eq("та же самая: день и состав строк",
       (у_оп["day"], [r["id"] for r in у_оп["rows"]]),
       (у_ст["day"], [r["id"] for r in у_ст["rows"]]))
    eq("и те же районы", [x["id"] for x in у_оп["districts"]], [x["id"] for x in у_ст["districts"]])

    print("── правка оператора видна старшему ────────────────────────────")
    st, res = await оператор("/api/operator/stock/order/edit", method="POST",
                             body={"day": ДЕНЬ, "id": pid, "district": "jvc", "qty": "7"})
    eq("сервер принял правку", (st, res.get("ok")), (200, True))
    eq("в базе стоит наша клетка", (await db.zayavka_edits(ДЕНЬ)).get(pid, {}).get("jvc"), 7)
    у_ст = await старший("/api/owner/stock/order")
    клетка = next((r["cells"]["jvc"] for r in у_ст["all_rows"] if r["id"] == pid), {})
    eq("старший видит её у себя", (клетка.get("need"), клетка.get("edited")), (7, True))
    eq("и счётчик правок у него не ноль", у_ст["edited_count"] >= 1, True)

    print("── пустое значение возвращает расчёт ──────────────────────────")
    st, res = await оператор("/api/operator/stock/order/edit", method="POST",
                             body={"day": ДЕНЬ, "id": pid, "district": "jvc", "qty": ""})
    у_ст = await старший("/api/owner/stock/order")
    клетка = next((r["cells"]["jvc"] for r in у_ст["all_rows"] if r["id"] == pid), {})
    eq("правка снята", ((await db.zayavka_edits(ДЕНЬ)).get(pid, {}).get("jvc"), клетка.get("edited")),
       (None, False))

    print("── чужое не принимаем ─────────────────────────────────────────")
    st, res = await оператор("/api/operator/stock/order/edit", method="POST",
                             body={"day": ДЕНЬ, "id": "нет-такой-позиции", "district": "jvc", "qty": "3"})
    eq("несуществующая позиция", (st, res.get("error")), (400, "unknown_product"))
    st, res = await оператор("/api/operator/stock/order/edit", method="POST",
                             body={"day": ДЕНЬ, "id": pid, "district": "марс", "qty": "3"})
    eq("несуществующий район", (st, res.get("error")), (400, "unknown_district"))
    st, res = await оператор("/api/operator/stock/order/edit", method="POST",
                             body={"day": ДЕНЬ, "id": pid, "district": "jvc", "qty": "много"})
    eq("нечисло", (st, res.get("error")), (400, "bad_qty"))

    print("── правило правки одно на оба приложения ──────────────────────")
    src = open(os.path.join(ROOT, "operator_routes.py"), encoding="utf-8").read()
    i = src.index("async def handle_op_order_edit")
    eq("оператор ходит в обработчик склада, а не в свою копию",
       "stock_routes.handle_order_edit" in src[i:i + 500], True)
    eq("все три ручки под охраной оператора",
       len(re.findall(r"@require_operator\s+async def handle_op_order", src)), 3)
    eq("ручки зарегистрированы",
       ('r.add_get("/api/operator/stock/order", handle_op_order)' in src
        and 'r.add_post("/api/operator/stock/order/edit", handle_op_order_edit)' in src
        and 'r.add_post("/api/operator/stock/order/reset", handle_op_order_reset)' in src), True)

    print("── у операторов только правка, без остального ─────────────────")
    js = open(os.path.join(ROOT, "operator", "order.js"), encoding="utf-8").read()
    eq("ходит только в свои три ручки",
       sorted(set(re.findall(r"/api/operator/[a-z/]+", js))),
       ["/api/operator/stock/order", "/api/operator/stock/order/edit",
        "/api/operator/stock/order/reset"])
    for чужое in ("supply", "status", "answer", "/api/owner"):
        eq(f"ничего про «{чужое}»", чужое in js, False)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
