"""AMBAR STOCK — реестр QR-кодов. Одна бутылка = один код = одна учётная единица.

Первый слой системы: выпуск кодов и их состояние. Сканирование и привязка к
точке лягут сюда же, когда решим, где живёт сканер, — но реестр от этого не
зависит и нужен раньше всего: печатать наклейки не из чего, пока кодов нет.

Жизненный цикл кода:
    free      выпущен, напечатан, ещё ни на чём не наклеен — на остаток не влияет
    active    отсканирован на точке, стал бутылкой в остатке
    sold      ушёл с заказом
    written   списан: бой, пересорт, недостача

Ключевое правило спецификации, которое здесь и держится: наклейка сама по себе
остатка не меняет. Пока код free, его как бы нет. Поэтому массово «активировать
диапазон» нельзя — только физический скан переводит код в active.
"""
import logging, secrets, string
from datetime import datetime, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("qr")

# Алфавит без похожих знаков. На крышке 25 мм QR читают под углом и в тени, а
# код иногда придётся вбить руками — 0/O и 1/I/L там неразличимы.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 8

MAX_BATCH = 5000          # больше одной партии за раз печатать всё равно нечем


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


async def _unique_codes(n: int) -> list:
    """n свежих кодов, которых ещё нет в базе.

    31^8 — это 850 миллиардов комбинаций, при десятках тысяч кодов совпадение
    почти невозможно. Но «почти» здесь мало: совпавший код — это две бутылки с
    одним номером, то есть тихая ошибка в остатке, которую никто не найдёт."""
    out, tries = set(), 0
    while len(out) < n and tries < 20:
        need = n - len(out)
        batch = {_new_code() for _ in range(need)}
        taken = await db.qr_existing(list(batch))
        out |= (batch - set(taken))
        tries += 1
    if len(out) < n:
        raise RuntimeError(f"не удалось выпустить {n} кодов, получилось {len(out)}")
    return sorted(out)


@require_owner
async def handle_stats(request):
    """Сводка реестра: сколько выпущено и в каком они состоянии."""
    st = await db.qr_stats()
    batches = await db.qr_batches(limit=30)
    return web.json_response({
        "totals": {
            "total":   sum(st.values()),
            "free":    st.get("free", 0),
            "active":  st.get("active", 0),
            "sold":    st.get("sold", 0),
            "written": st.get("written", 0),
        },
        "batches": batches,
        "code_len": CODE_LEN,
        "max_batch": MAX_BATCH,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_batch_create(request):
    """Выпустить партию кодов.

    Позиция необязательна: наклейки можно печатать впрок и решать у полки, на
    что их клеить. Если позиция задана, код с ней и родится — тогда при скане
    товар подставится сам, без выбора из ста двадцати позиций."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    try:
        count = int(body.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count < 1 or count > MAX_BATCH:
        return web.json_response({"error": "bad_count", "max": MAX_BATCH},
                                 status=400, headers=CORS_HEADERS)

    product_id = (body.get("product_id") or "").strip() or None
    product_name = ""
    if product_id:
        from operator_routes import _catalog_by_id
        p = _catalog_by_id().get(product_id)
        if not p:
            return web.json_response({"error": "unknown_product"}, status=400,
                                     headers=CORS_HEADERS)
        product_name = p.get("name", "")

    note = str(body.get("note") or "").strip()[:80]
    codes = await _unique_codes(count)
    now = datetime.now(timezone.utc)
    batch_id = "B" + now.strftime("%y%m%d") + "-" + secrets.token_hex(2).upper()

    await db.qr_insert(batch_id, codes, product_id, product_name, note,
                       request.get("owner_id") or 0, now)
    log.info(f"[qr] партия {batch_id}: {count} кодов"
             + (f" · {product_name}" if product_name else " · без позиции"))
    return web.json_response({"ok": True, "batch_id": batch_id, "count": count,
                              "product_id": product_id, "product_name": product_name},
                             headers=CORS_HEADERS)


@require_owner
async def handle_batch_codes(request):
    """Коды партии — для печати наклеек."""
    bid = request.match_info.get("bid", "")
    codes = await db.qr_batch_codes(bid)
    if not codes:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    return web.json_response({"batch_id": bid, "count": len(codes), "codes": codes},
                             headers=CORS_HEADERS)


@require_owner
async def handle_lookup(request):
    """Что это за код. Дальше сюда ляжет история операций бутылки."""
    code = (request.match_info.get("code") or "").strip().upper()
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
        ("/api/owner/qr",                 handle_stats,        "GET"),
        ("/api/owner/qr/batch",           handle_batch_create, "POST"),
        ("/api/owner/qr/batch/{bid}",     handle_batch_codes,  "GET"),
        ("/api/owner/qr/code/{code}",     handle_lookup,       "GET"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info("[qr] routes mounted")
