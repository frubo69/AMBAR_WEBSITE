"""Стенд «книжка клиентов»: настоящие clients_routes, operator_routes и
owner-ручки поверх базы в памяти; страницы оператора и STAR — настоящие.
Имена, телефоны, адреса и суммы ВЫДУМАНЫ (стенд, не бой).

    python3 tools/book_stand_server.py [port]        # по умолчанию 8797

Оператор:  http://127.0.0.1:8797/op/      STAR: http://127.0.0.1:8797/
"""
import asyncio, os, sys, logging
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
logging.basicConfig(level=logging.WARNING)
from aiohttp import web                                            # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import owner_auth                                                  # noqa: E402
owner_auth.require_owner = lambda h: h
import operator_routes as opr                                      # noqa: E402
opr.require_operator = lambda h: h
import db, clients as C, clients_routes as CR, stock_value         # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8797
ЛЮДИ = [("Иван С.", "+971 50 123 4567", ["Dubai Marina, Marina Gate 2, apt 1804", "Business Bay, Bay Square 7, office 402"], True),
        ("Алина К.", "+971 55 777 0303", ["Marina, Villa 12"], False),
        ("Дмитрий М.", "+971 52 198 0000", ["JVC, Diamond Views 3, apt 507"], False),
        ("Robert B.", "+971 56 052 2000", ["Business Bay, Executive Towers J, 2101"], False),
        ("Наталья П.", "+971 58 643 1000", ["Silicon Oasis, Tower B, 1205", "Al Barsha, Villa 9", "Tecom, Grosvenor 1407"], False),
        ("Олег Л.", "+971 54 889 0000", ["Silicon Oasis, Binghatti 12"], False)]


async def seed():
    db._db = AsyncMongoMockClient()["book_stand"]
    now = datetime.now(timezone.utc)
    cat = opr._catalog_by_id()
    for k, (name, phone, addrs, w) in enumerate(ЛЮДИ):
        for j, a in enumerate(addrs):
            for t in range(2 if j == 0 else 1):
                oid = f"S{k}{j}{t}"
                items = [{"id": "p70", "name": cat["p70"]["name"], "qty": 2, "price": 450, "line_total": 900},
                         {"id": "p67", "name": cat["p67"]["name"], "qty": 1, "price": 350, "line_total": 350}]
                o = {"order_id": oid, "phone": phone, "customer_name": name, "address": a, "total": 1250, "status": "delivered",
                     "items": items, "district": "JVC", "office_id": "jvc", "driver": "Стенд А", "source": "manual",
                     "timestamp": (now - timedelta(days=20 - k * 3 - j - t)).isoformat(), "day": (now - timedelta(days=20 - k * 3 - j - t)).strftime("%Y-%m-%d")}
                await db._db.orders.insert_one(o)
                await C.remember(o, by="стенд")
    ivan = await C.by_phone("0501234567")
    await db.client_set(ivan["_id"], {"wholesale": {"active": True, "prices": {"p71": 515, "p70": 420, "p69": 370, "p72": 1450, "p68": 360, "p73": 1530}, "rule": None, "by": "стенд", "at": now}})
    addrs = ivan["addresses"]; addrs[0]["label"] = "Дом"; addrs[0]["note"] = "вход со двора, звонить за 10 минут"; addrs[1]["label"] = "Офис"
    await db.client_set(ivan["_id"], {"addresses": addrs})
    o = {"order_id": "W1", "phone": "+971 50 123 4567", "customer_name": "Иван С.", "address": addrs[0]["text"], "total": 44525, "status": "delivered",
         "items": [{"id": "p71", "name": cat["p71"]["name"], "qty": 15, "price": 515, "reg": 550, "own": True, "line_total": 7725},
                   {"id": "p72", "name": cat["p72"]["name"], "qty": 10, "price": 1450, "reg": 1600, "own": True, "line_total": 14500},
                   {"id": "p67", "name": cat["p67"]["name"], "qty": 20, "price": 350, "line_total": 7000},
                   {"id": "p73", "name": cat["p73"]["name"], "qty": 10, "price": 1530, "reg": 1800, "own": True, "line_total": 15300}],
         "own_prices": {"p71": 515, "p72": 1450, "p73": 1530}, "wholesale": True, "district": "JVC", "office_id": "jvc", "driver": "Стенд А", "source": "manual",
         "timestamp": (now - timedelta(days=1)).isoformat(), "day": (now - timedelta(days=1)).strftime("%Y-%m-%d")}
    await db._db.orders.insert_one(o)
    await C.remember(o, by="стенд")


async def h_any(request):
    return web.json_response({"error": "stand"}, status=404)


async def h_ping(request):
    # панели оператора нужен /ping: кто за планшетом, районы, водители
    return web.json_response({"ok": True, "operator": "Стенд", "pinned": "Стенд",
        "people": [{"name": "Стенд", "senior": True, "districts": ["jvc", "bbay"]}],
        "districts": [
        {"id": "jvc", "code": "B1", "name": "JVC", "operator": "Стенд", "drivers": ["Стенд А", "Стенд Б"]},
        {"id": "bbay", "code": "B2", "name": "Бизнес Бей", "operator": "Стенд", "drivers": ["Стенд В"]}],
        "offices": [{"id": "jvc", "name": "JVC"}], "test": False, "test_allowed": False})


async def h_queue(request):
    return web.json_response({"new": [], "work": [], "done": [], "counts": {"app": 0, "manual": 0}, "districts": []})


async def h_empty(request):
    return web.json_response({"ok": True, "tickets": [], "items": [], "events": [], "list": [], "open": None, "n": 0})


def main():
    app = web.Application(client_max_size=20 * 1024 * 1024)
    async def _start(app): await seed()
    app.on_startup.append(_start)
    CR.setup(app)
    cat_h = opr.handle_catalog
    while hasattr(cat_h, "__wrapped__"):
        cat_h = cat_h.__wrapped__                    # без подписи телеграма на стенде
    app.router.add_get("/api/operator/catalog", cat_h)
    app.router.add_get("/api/operator/ping", h_ping)
    app.router.add_get("/api/operator/queue", h_queue)
    for p in ("/api/operator/support", "/api/operator/feed", "/api/operator/shift", "/api/operator/tea",
              "/api/operator/where", "/api/operator/day", "/api/operator/short", "/api/operator/extra"):
        app.router.add_get(p, h_empty)
    app.router.add_route("*", "/api/{tail:.*}", h_any)
    app.router.add_get("/op/", lambda r: web.FileResponse(os.path.join(ROOT, "operator", "index.html")))
    app.router.add_static("/op/", os.path.join(ROOT, "operator"))
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(ROOT, "owner", "index.html")))
    app.router.add_static("/products/", os.path.join(ROOT, "products")) if os.path.isdir(os.path.join(ROOT, "products")) else None
    app.router.add_static("/", os.path.join(ROOT, "owner"))
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
