"""Адреса для рамки Divoom: ключ из HMAC секрета, голый текст, чужой ключ — 401,
кэш 10 с (16 сен 2026). Без базы."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BOT_TOKEN"] = "123:test"; os.environ.pop("AMBAR_PIXOO_KEY", None); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
from aiohttp.test_utils import make_mocked_request
import pixoo_routes as px, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
N = {"v": 998}
async def count(): return N["v"]
db.customers_count = count
async def call(key, what=None):
    r = make_mocked_request("GET", "/pixoo/x", match_info={"key": key, **({"what": what} if what else {})})
    resp = await px.handle_pixoo(r); return resp.status, resp.text
async def main():
    k = px.pixoo_key()
    eq("ключ выведен из секрета, 16 знаков", (len(k), k.isalnum()), (16, True))
    eq("верный ключ → число текстом", await call(k), (200, "998"))
    eq("чужой ключ → 401", (await call("nope"))[0], 401)
    N["v"] = 1000
    eq("в пределах 10 с — из кэша", await call(k), (200, "998"))
    px._cache.clear()
    eq("после кэша — свежее", await call(k), (200, "1000"))
    eq("неизвестный what → 404", (await call(k, "foo"))[0], 404)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
