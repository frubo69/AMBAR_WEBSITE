"""Стенд «крипта в РП»: настоящие finance_routes, wallet_routes и crypto_book
поверх базы в памяти и выдуманного кошелька; страница STAR — настоящая, из
каталога owner. Экран проверяется настоящим путём: нажал → запрос → пересчёт.

    python3 tools/crypto_stand_server.py [port]         # по умолчанию 8791

Выдуманный кошелёк: GET /stand/inflow?aed=2000 — «клиент заплатил» (в наших
дирхамах), GET /stand/out?aed=500 — «с кошелька ушло». Суммы и имена выдуманы.
"""
import asyncio, os, sys, time, logging
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
logging.basicConfig(level=logging.WARNING)
from aiohttp import web                                            # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import owner_auth                                                  # noqa: E402
owner_auth.require_owner = lambda h: h            # стенд: без initData (как fb_stand_server)
import db, rates, backdate, tron, pay_notify                       # noqa: E402
import finance_routes as fr, wallet_routes as wr, crypto_book as cb   # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
W = {"bal": 0.0, "tr": [], "n": 0}


def move(aed, вход=True):
    W["n"] += 1
    usdt = round(aed / 3.5, 6)
    W["bal"] = round(W["bal"] + (usdt if вход else -usdt), 6)
    W["tr"].append({"txid": f"stand{W['n']:04d}{'a' * 40}", "amount": usdt, "in": вход,
                    "peer": "TStandClient00000000000000000000001", "ts": int(time.time() * 1000)})


async def seed():
    db._db = AsyncMongoMockClient()["crypto_stand"]

    async def _курс():
        return {"rates": [{"code": "USD", "aed": 3.6725, "cash_aed": 3.67}]}
    rates.get_rates = _курс

    async def _молча(*a, **k):
        return None
    backdate.notify = _молча
    pay_notify.tell_safe = _молча
    wr.TRON_RECEIVE_ADDRESS = "TStandWallet0000000000000000000001"

    async def _old():
        return []
    wr.old_addresses = _old

    async def _bal(a):
        return {"usdt": W["bal"], "trx": 12.5, "unknown": False}

    async def _tr(a, limit=200, pages=6, min_ts=0):
        return [dict(t) for t in sorted(W["tr"], key=lambda t: -t["ts"]) if t["ts"] >= min_ts]
    tron.get_balance, tron.get_transfers = _bal, _tr
    today = fr._biz_day()
    month = today[:7]
    await db.fin_month_set(month, {"norm": 9700})
    await cb.sync()                                   # книга заведена: открытие 0
    await asyncio.sleep(0.01)
    n = 0
    for d in fr._month_days(month):
        if d > today:
            break
        n += 1
        await db._db.orders.insert_one({"order_id": f"S{n}", "timestamp": f"{d}T14:00:00", "status": "delivered",
                                        "total": 38000 + 1500 * n, "tip": 0, "office_id": "jvc"})
    move(1000)
    await cb.sync()


async def h_inflow(request):
    move(float(request.query.get("aed") or 1000))
    await asyncio.sleep(0.01)
    r = await cb.sync()
    wr._drop_cache()
    return web.json_response({"ok": True, **r, "state": {k: v for k, v in (await cb.state()).items() if k != "by_day"}})


async def h_out(request):
    move(float(request.query.get("aed") or 500), False)
    await asyncio.sleep(0.01)
    await cb.sync(); wr._drop_cache()
    return web.json_response({"ok": True})


async def h_any(request):
    return web.json_response({"error": "stand"}, status=404)


def main():
    app = web.Application(client_max_size=20 * 1024 * 1024)

    async def _start(app):
        await seed()
    app.on_startup.append(_start)
    fr.setup(app); wr.setup(app)
    app.router.add_get("/stand/inflow", h_inflow)
    app.router.add_get("/stand/out", h_out)
    app.router.add_route("*", "/api/{tail:.*}", h_any)
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(ROOT, "owner", "index.html")))
    app.router.add_static("/", os.path.join(ROOT, "owner"))
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
