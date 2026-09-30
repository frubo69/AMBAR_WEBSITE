"""Оператор и доп. заявки — смотреть и править (владелец, 30 сен 2026: «чтобы
операторы только редактировали, но отправлять водителям они не могут доп.
заявку — и тем более заявку»): список, заявка целиком, состав по районам (и у
черновика), цена. Собрать новую, отправить черновик и отменить оператор не
может — ручек на это у него нет. Основная заявка не трогается вовсе.

    python3 tools/test_operator_extra.py
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, operator_routes as opr, supply_routes as sr            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

class Req(dict):
    def __init__(self, body=None, sid="", **q):
        super().__init__(op_user={"id": 5, "first_name": "Оператор"})
        self._b = body; self.match_info = {"sid": sid}; self.query = q
    async def json(self): return self._b or {}

async def call(h, *a, **k):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    r = await h(Req(*a, **k))
    return r.status, json.loads(r.body)

async def main():
    db._db = AsyncMongoMockClient()["ambar_opextra"]
    import owner_routes
    async def _say(*a, **k): return []
    owner_routes.notify_owners = _say; owner_routes.notify_owners_force = _say
    async def _tell(*a, **k): return None
    opr.tell_driver = _tell
    async def _costs(): return {}
    sr._costs_refresh = _costs
    sr._buy_note = lambda *a, **k: None
    # внутренняя ручка в _extra_do завёрнута в require_operator — снимаем охрану целиком
    raw = lambda h: h

    print("Отправить и собрать оператор не может")
    eq("ручек «собрать», «отправить», «отменить» у оператора нет",
       [n for n in ("handle_extra_new", "handle_extra_confirm", "handle_extra_cancel") if hasattr(opr, n)], [])
    from aiohttp import web
    app = web.Application(); opr.setup(app)
    пути = sorted({(r.method, r.resource.canonical) for r in app.router.routes()
                   if "/operator/extra" in r.resource.canonical and r.method not in ("OPTIONS", "HEAD")})
    eq("в адресах оператора по доп. заявкам — только смотреть, состав и цена", пути,
       [("GET", "/api/operator/extra"), ("GET", "/api/operator/extra/{sid}"),
        ("POST", "/api/operator/extra/{sid}/buy"), ("POST", "/api/operator/extra/{sid}/lines")])
    eq("и по основной заявке — только смотреть и править",
       sorted({(r.method, r.resource.canonical) for r in app.router.routes()
               if "/operator/stock/order" in r.resource.canonical and r.method not in ("OPTIONS", "HEAD")}),
       [("GET", "/api/operator/stock/order"), ("POST", "/api/operator/stock/order/edit"),
        ("POST", "/api/operator/stock/order/reset")])
    eq("ничего про отправку заявки магазину или водителям",
       [r.resource.canonical for r in app.router.routes() if "/api/operator/" in r.resource.canonical
        and any(w in r.resource.canonical for w in ("/send", "/confirm", "/import", "/export", "/cancel"))
        and "/supply" in r.resource.canonical + "/extra" * ("/extra" in r.resource.canonical)], [])

    # Заявку собирает старший — ручкой STAR.
    import supply_routes as _sr
    st, r = await call(_sr.handle_extra_create, {"base": "База Один", "as": "Старший",
                                                 "items": [{"id": "p1", "by_district": {"jvc": 3, "bbay": 2}},
                                                           {"id": "p2", "by_district": {"jvc": 1}}]})
    sid = r["supply_id"]

    print("\nСписок и одна заявка")
    await db._db.supplies.insert_one({"_id": "S1", "status": "open", "at": datetime.now(timezone.utc),
                                      "day": "2026-09-30", "kind": "main", "items": [], "tasks": {"jvc": {}}})
    st, r = await call(opr.handle_extra_list)
    eq("в списке только доп. заявки", [x["supply_id"] for x in r["supplies"]], [sid])
    eq("районы пришли", len(r["districts"]) >= 5, True)
    st, r = await call(opr.handle_extra_one, sid=sid)
    eq("заявка целиком", (st, r["base"], sorted(t["district"] for t in r["tasks"])), (200, "База Один", ["bbay", "jvc"]))

    print("\nПравить состав")
    st, r = await call(opr.handle_extra_lines, {"district": "jvc", "as": "Оператор",
                                                "lines": [{"product_id": "p1", "qty": 5}, {"product_id": "p5", "qty": 2}]}, sid=sid)
    jvc = next(t for t in r["tasks"] if t["district"] == "jvc")
    eq("число поменяли, позицию добавили", (st, {l["id"]: l["need"] for l in jvc["lines"]}), (200, {"p1": 5, "p2": 1, "p5": 2}))
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.jvc.started_at": datetime.now(timezone.utc)}})
    st, r = await call(opr.handle_extra_lines, {"district": "jvc", "lines": [{"product_id": "p1", "qty": 9}]}, sid=sid)
    eq("район начали принимать — править нельзя", (st, r.get("error")), (409, "district_locked"))

    print("\nЦена")
    st, r = await call(opr.handle_extra_buy, {"product_id": "p1", "price": 41.5, "as": "Оператор"}, sid=sid)
    eq("вписана", (st, r["buys"]["p1"]["price"]), (200, 41.5))
    eq("и легла при базе", [(x["base"], x["price"]) for x in await db.base_prices("база один")], [("База Один", 41.5)])

    print("\nЧерновик: поправить и отправить")
    await db._db.supplies.insert_one({"_id": "X9", "status": "draft", "at": datetime.now(timezone.utc),
        "day": "2026-09-30", "kind": "extra", "base": "", "from_supply": sid, "tried_bases": ["База Один"],
        "items": [{"id": "p2", "name": "Stolichnaya 1 ltr", "qty": 2, "asked": 2, "by_district": {"jvc": 2}, "got": {"jvc": 0}}],
        "tasks": {"jvc": {"driver": "", "scanned": 0, "started_at": None, "noscan_at": None, "done_at": None,
                          "cancelled_at": None, "qty": 2, "positions": 1}}, "total_qty": 2})
    st, r = await call(opr.handle_extra_one, sid="X9")
    eq("видно, где уже искали", r["tried_bases"], ["База Один"])
    st, r = await call(opr.handle_extra_lines, {"district": "jvc", "lines": [{"product_id": "p2", "qty": 4}]}, sid="X9")
    eq("черновик правится", (st, r["total_qty"]), (200, 4))
    eq("а отправить его оператору нечем — черновик остался черновиком",
       (await db.supply_get("X9"))["status"], "draft")

    print("\nОсновную заявку этими ручками не тронуть")
    for имя, h, body in (("посмотреть", opr.handle_extra_one, None),
                         ("править", opr.handle_extra_lines, {"district": "jvc", "lines": [{"product_id": "p1", "qty": 1}]}),
                         ("цена", opr.handle_extra_buy, {"product_id": "p1", "price": 1})):
        st, r = await call(h, body, sid="S1")
        eq(имя, (st, r.get("error")), (403, "not_extra"))
    eq("и она цела", (await db.supply_get("S1"))["status"], "open")
    st, r = await call(opr.handle_extra_one, sid="нет")
    eq("несуществующая — 404", st, 404)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
