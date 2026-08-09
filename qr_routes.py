"""AMBAR STOCK — реестр бутылок по их собственным кодам.

Коды мы не печатаем: они уже есть на бутылках. Работа сводится к тому, чтобы
переписать их в базу и привязать к позиции каталога: выбрал Absolut, открыл
камеру, отсканировал — бутылка появилась в реестре.

Жизненный цикл записи:
    active    код отсканирован и привязан к позиции — бутылка в остатке
    sold      ушла с заказом
    written   списана: бой, пересорт, недостача

Главная защита здесь — от повторного скана. Одна и та же бутылка, посчитанная
дважды, даёт лишнюю единицу в остатке, и найти её потом нельзя: в базе она
выглядит точно так же, как настоящая. Поэтому код — это _id: вторая запись с
тем же номером физически невозможна, а не «проверяется».
"""
import logging, re
from datetime import datetime, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("qr")

MAX_CODE = 120          # длиннее не бывает даже у акцизных марок


def _clean(code: str) -> str:
    """Код с этикетки как есть, без пробелов и переносов.

    Регистр не трогаем: у части марок он значащий, и приведение к верхнему
    склеило бы разные бутылки в одну."""
    return re.sub(r"\s+", "", str(code or ""))[:MAX_CODE]


@require_owner
async def handle_stats(request):
    """Сводка реестра: сколько бутылок записано и по каким позициям."""
    st = await db.qr_stats()
    by_product = await db.qr_by_product()
    last = await db.qr_last(limit=20)
    return web.json_response({
        "totals": {
            "total":   sum(st.values()),
            "active":  st.get("active", 0),
            "sold":    st.get("sold", 0),
            "written": st.get("written", 0),
        },
        "by_product": by_product,
        "last": last,
    }, headers=CORS_HEADERS,
       dumps=lambda o: __import__("json").dumps(o, default=str))


@require_owner
async def handle_scan(request):
    """Записать отсканированную бутылку.

    Повтор — не ошибка ввода, а нормальная ситуация: на полке легко навести
    камеру на ту же крышку дважды. Поэтому отвечаем спокойно и говорим, что
    эта бутылка уже посчитана и когда."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)

    code = _clean(body.get("code"))
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)

    product_id = (body.get("product_id") or "").strip()
    from operator_routes import _catalog_by_id
    p = _catalog_by_id().get(product_id)
    if not p:
        return web.json_response({"error": "unknown_product"}, status=400, headers=CORS_HEADERS)

    district = (body.get("district") or "").strip() or None
    now = datetime.now(timezone.utc)
    added = await db.qr_add(code, product_id, p.get("name", ""), district,
                            request.get("owner_id") or 0, now)
    if not added:
        old = await db.qr_get(code)
        return web.json_response({
            "ok": True, "new": False,
            "code": code,
            "product_name": (old or {}).get("product_name", ""),
            "at": str((old or {}).get("at") or ""),
        }, headers=CORS_HEADERS)

    total = await db.qr_count_product(product_id)
    log.info(f"[qr] {p.get('name','')} · {code}"
             + (f" · {district}" if district else ""))
    return web.json_response({"ok": True, "new": True, "code": code,
                              "product_id": product_id,
                              "product_name": p.get("name", ""),
                              "product_total": total},
                             headers=CORS_HEADERS)


@require_owner
async def handle_undo(request):
    """Отменить последний скан — тот, что человек только что сделал зря.

    Убираем не «последний по базе», а конкретный код: пока один считает полку,
    второй может сканировать в другом районе, и «последний» окажется чужим."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = _clean(body.get("code"))
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)
    ok = await db.qr_remove(code)
    if ok:
        log.info(f"[qr] отменён скан {code}")
    return web.json_response({"ok": ok, "code": code}, headers=CORS_HEADERS)


@require_owner
async def handle_lookup(request):
    """Что это за бутылка. Сюда же ляжет её история операций."""
    code = _clean(request.match_info.get("code"))
    doc = await db.qr_get(code)
    if not doc:
        return web.json_response({"error": "unknown_code", "code": code},
                                 status=404, headers=CORS_HEADERS)
    return web.json_response(doc, headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/qr",             handle_stats,  "GET"),
        ("/api/owner/qr/scan",        handle_scan,   "POST"),
        ("/api/owner/qr/undo",        handle_undo,   "POST"),
        ("/api/owner/qr/code/{code}", handle_lookup, "GET"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info("[qr] routes mounted")
