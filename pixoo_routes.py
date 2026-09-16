"""Цифры для рамки Divoom Pixoo-64 (владелец, 16 сен 2026).

Рамка умеет «текст по ссылке»: сама раз в N секунд ходит на адрес и рисует
строку, как встроенный счётчик подписчиков ютуба. Здесь такие адреса:

    /pixoo/<ключ>          — клиентов в базе бота, например «998»
    /pixoo/<ключ>/online   — водителей на связи прямо сейчас

Ответ — голый текст, без JSON. Ключ выводится из секрета бота (HMAC), в
базе и в .env ничего не хранится; узнать его — tools/pixoo_key.py. Рамка
говорит по http, поэтому путь /pixoo/ в nginx отдаётся и без https."""
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone

from aiohttp import web

import db

log = logging.getLogger("ambar.pixoo")

CACHE_SEC = 10                   # рамка спрашивает каждые полминуты; чаще базу не трогаем
_cache: dict = {}


def pixoo_key() -> str:
    """Ключ из .env, а нет — выведенный из секрета бота: без записи куда-либо."""
    k = (os.getenv("AMBAR_PIXOO_KEY") or "").strip()
    if k:
        return k
    secret = (os.getenv("BOT_TOKEN") or "").strip()
    if not secret:
        return ""
    return hmac.new(secret.encode(), b"pixoo", hashlib.sha256).hexdigest()[:16]


async def _customers() -> int:
    return int(await db.customers_count())


async def _online() -> int:
    """Водители, у кого трансляция идёт и точка не старше десяти минут."""
    import config_staff as staff
    from driver_routes import _geo_state
    n = 0
    for name in staff.driver_names():
        try:
            g = await _geo_state(name)
        except Exception:                          # noqa: BLE001
            continue
        if g.get("stream") and g.get("age_sec") is not None and g["age_sec"] < 600:
            n += 1
    return n


async def _value(what: str) -> str:
    now = time.time()
    hit = _cache.get(what)
    if hit and now - hit[0] < CACHE_SEC:
        return hit[1]
    try:
        v = str(await (_online() if what == "online" else _customers()))
    except Exception as e:                         # noqa: BLE001
        log.warning(f"[pixoo] {what}: {e}")
        v = hit[1] if hit else "--"
    _cache[what] = (now, v)
    return v


async def handle_pixoo(request):
    key = pixoo_key()
    if not key or not hmac.compare_digest(request.match_info.get("key", ""), key):
        return web.Response(status=401, text="no")
    what = request.match_info.get("what") or "customers"
    if what not in ("customers", "online"):
        return web.Response(status=404, text="no")
    # Формат — как у эталона Divoom (appin.divoom-gz.com/Device/ReturnCurrentDate):
    # JSON с полем DispData. Голый текст рамка молча не рисует — проверено на
    # Pixoo-64 владельца 17 сен 2026. ?plain=1 — голый текст для глаз.
    value = await _value(what)
    if request.query.get("plain"):
        return web.Response(text=value, content_type="text/plain", charset="utf-8",
                            headers={"Cache-Control": "no-store"})
    return web.json_response({"ReturnCode": 0, "ReturnMessage": "", "DispData": value},
                             headers={"Cache-Control": "no-store"})


def setup(app):
    r = app.router
    r.add_get("/pixoo/{key}", handle_pixoo)
    r.add_get("/pixoo/{key}/{what}", handle_pixoo)
    log.info("[pixoo] routes mounted" if pixoo_key() else "[pixoo] ключа нет — адреса выключены")
