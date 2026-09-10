"""Монтирует все модули маршрутов в web.Application, как это делает
api_server.main(), и резолвит ключевые пути. Ловит то, чего стенды с прямым
вызовом обработчиков не видят: упавшую петлю регистрации, путь, перекрытый
статикой, метод без маршрута. Запуск: python3 tools/e2e_mount.py"""
import asyncio, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
logging.basicConfig(level=logging.ERROR)
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

MODULES = ["owner_routes", "broadcast_routes", "stock_routes", "expense_routes",
           "qr_routes", "wallet_routes", "stock_value", "finance_routes", "rates",
           "supply_routes", "driver_routes", "operator_routes", "call_routes",
           "room_routes"]
CHECK = [
    ("GET", "/api/owner/finance"), ("GET", "/api/owner/finance/debts"),
    ("GET", "/api/owner/finance/book"), ("POST", "/api/owner/finance/book/day"),
    ("POST", "/api/owner/finance/book/entry"), ("DELETE", "/api/owner/finance/book/entry"),
    ("POST", "/api/owner/finance/book/month"), ("OPTIONS", "/api/owner/finance/book/month"),
    ("GET", "/api/owner/cash-round"), ("GET", "/api/owner/stock/prices"),
    ("GET", "/api/driver/shift"), ("POST", "/api/driver/panic"),
    ("GET", "/api/owner/supply"),
]

async def main():
    app = web.Application()
    failed = []
    for name in MODULES:
        try:
            mod = __import__(name)
            mod.setup(app)
        except Exception as e:                    # noqa: BLE001
            failed.append((name, repr(e)))
    async def handle_static(request): return web.Response(text="static")
    app.router.add_get("/", handle_static)
    app.router.add_get("/{path:.+}", handle_static)
    app.freeze() if hasattr(app, "freeze") else None
    bad = []
    for method, path in CHECK:
        req = make_mocked_request(method, path)
        m = await app.router.resolve(req)
        h = getattr(m, "handler", None)
        name = getattr(h, "__name__", str(h))
        ok = (h is not None and name != "handle_static" and getattr(m, "http_exception", None) is None)
        print(("  ok  " if ok else "FAIL ") + f"{method:<7} {path:<40} → {name}")
        if not ok: bad.append((method, path))
    for n, e in failed: print("SETUP FAILED", n, e)
    print("FAILED:", bad or failed) if (bad or failed) else print("ALL ROUTES MOUNTED")
    sys.exit(1 if (bad or failed) else 0)
asyncio.run(main())
