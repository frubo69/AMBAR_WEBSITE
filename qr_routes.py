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
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("qr")

MAX_CODE = 120          # длиннее не бывает даже у акцизных марок

# Позиция на точке занимается на время пересчёта. Срок нужен обязательно: без
# него человек, закрывший приложение посреди полки, запер бы Absolut на B2
# навсегда, и никто бы не понял почему. Каждый скан продлевает.
LOCK_MIN = 15


def _lock_key(district: str, product_id: str) -> str:
    return f"{district}:{product_id}"


# ── человекочитаемая метка бутылки ──────────────────────────────────────────
# В коде с этикетки может лежать что угодно — акцизный серийник, ссылка, набор
# цифр поставщика. Работать с этим глазами невозможно, поэтому каждой бутылке
# при записи присваивается своя метка: abs#000042, rus_stan#000007. Она и есть
# наш идентификатор, а исходный код остаётся служебным ключом.
_SIZE = re.compile(r"^\d+([.,]\d+)?$|^(ltr|l|ml|cl|btl|can|pcs|шт)$", re.I)
_TRANS = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z",
          "и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
          "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch",
          "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"}


def _words(name: str) -> list:
    """Слова названия: latin/цифры, без пустых. Размеры не выбрасываем здесь —
    они пригодятся, чтобы развести совпавшие метки."""
    out = []
    for w in re.split(r"[^0-9A-Za-zА-Яа-яЁё.,]+", str(name or "")):
        if not w:
            continue
        w = "".join(_TRANS.get(ch, ch) for ch in w.lower())
        w = re.sub(r"[^a-z0-9]", "", w)
        if w:
            out.append(w)
    return out


def _base_slug(name: str) -> tuple:
    """(основа, хвосты) — «Chivas Regal 12Y 1 ltr» → («chi_rega», ['12y'])."""
    ws = _words(name)
    main = [w for w in ws if not _SIZE.match(w)]
    extra = [w for w in ws[len(main[:2]):] if w not in ("ltr", "l", "ml", "cl")
             and not re.fullmatch(r"\d+([.,]\d+)?", w)]
    if not main:
        return "item", extra
    base = main[0][:3] if len(main) == 1 else f"{main[0][:3]}_{main[1][:4]}"
    return base, extra


_SLUGS = {"map": None, "mtime": 0}


def slug_map() -> dict:
    """id позиции → короткая метка, уникальная по всему каталогу.

    Считается разом, а не по одной позиции: «Beluga» и «Belvedere» дают одну и
    ту же основу, три «Chivas Regal» — тоже. Метка, совпавшая у двух позиций,
    делает весь учёт бессмысленным: по abs#000042 нельзя понять, что за бутылка.

    Разводим по возрастанию: сначала удлиняем первое слово, потом добавляем
    отличающий хвост (12y, honey, can), в крайнем случае номер. Порядок обхода
    каталога стабилен, поэтому у уже записанных бутылок метки не разъезжаются —
    к тому же метка хранится в самой записи и после этого не пересчитывается."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    if _SLUGS["map"] is not None and _SLUGS["mtime"] == mt:
        return _SLUGS["map"]

    import json
    with open(path, encoding="utf-8") as f:
        catalog = json.load(f)

    out, taken = {}, set()
    for p in catalog:
        pid, name = p.get("id"), p.get("name", "")
        base, extra = _base_slug(name)
        ws = [w for w in _words(name) if not _SIZE.match(w)]
        # Сначала — то, чем позиции реально отличаются (18y, honey, bottle).
        # Удлинять первое слово нельзя вперёд этого: chi_rega и chiv_rega
        # различаются одной буквой, и перепутать их проще, чем совпавшие.
        cands = [base]
        cands += [f"{base}_{e[:5]}" for e in extra]
        if ws:
            cands.append(ws[0][:4] + (f"_{ws[1][:4]}" if len(ws) > 1 else ""))
        cands += [f"{base}{i}" for i in range(2, 12)]
        for c in cands:
            if c not in taken:
                out[pid] = c; taken.add(c); break
        else:
            out[pid] = f"{base}_{pid}"; taken.add(out[pid])
    _SLUGS["map"], _SLUGS["mtime"] = out, mt
    return out


def product_slug(product_id: str, name: str = "") -> str:
    return slug_map().get(product_id) or _base_slug(name)[0]


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
    locks = await db.qr_locks(datetime.now(timezone.utc))
    return web.json_response({
        "locks": locks,
        "by_district": await db.qr_by_district(),
        "slugs": slug_map(),
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
async def handle_lock(request):
    """Занять позицию на точке под пересчёт.

    Две пары рук на одной полке — это не удвоенная скорость, а два разных
    числа: каждый считает своё, и потом непонятно, какое верное. Поэтому пока
    один считает Absolut на B2, остальным эта пара недоступна."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = (body.get("district") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    if not district or not product_id:
        return web.json_response({"error": "district_and_product_required"},
                                 status=400, headers=CORS_HEADERS)
    me = request.get("owner_id") or 0
    name = str(body.get("who") or "").strip()[:40]
    now = datetime.now(timezone.utc)
    ok, holder = await db.qr_lock_take(_lock_key(district, product_id), me, name,
                                       now, now + timedelta(minutes=LOCK_MIN))
    if not ok:
        return web.json_response({"error": "busy",
                                  "by": (holder or {}).get("name") or "другой сотрудник",
                                  "since": str((holder or {}).get("at") or "")},
                                 status=409, headers=CORS_HEADERS)
    return web.json_response({"ok": True, "until_min": LOCK_MIN}, headers=CORS_HEADERS)


@require_owner
async def handle_unlock(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = (body.get("district") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    ok = await db.qr_lock_free(_lock_key(district, product_id),
                               request.get("owner_id") or 0)
    return web.json_response({"ok": ok}, headers=CORS_HEADERS)


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

    district = (body.get("district") or "").strip()
    if not district:
        return web.json_response({"error": "district_required"}, status=400,
                                 headers=CORS_HEADERS)
    me = request.get("owner_id") or 0
    now = datetime.now(timezone.utc)
    # Скан продлевает бронь: пока человек считает, позиция остаётся за ним, а
    # отойдя на четверть часа он её отпускает сам.
    ok, holder = await db.qr_lock_take(_lock_key(district, product_id), me, "",
                                       now, now + timedelta(minutes=LOCK_MIN))
    if not ok:
        return web.json_response({"error": "busy",
                                  "by": (holder or {}).get("name") or "другой сотрудник"},
                                 status=409, headers=CORS_HEADERS)
    # Номер берём до вставки: если код окажется занят, потерянный номер не
    # страшен, а вот две бутылки с одним номером — уже беда.
    seq = await db.qr_next_seq(product_id)
    label = f"{product_slug(product_id, p.get('name',''))}#{seq:06d}"
    added = await db.qr_add(code, product_id, p.get("name", ""), district, me, now, label)
    if not added:
        old = await db.qr_get(code)
        return web.json_response({
            "ok": True, "new": False,
            "code": code,
            "label": (old or {}).get("label") or "",
            "product_name": (old or {}).get("product_name", ""),
            "district": (old or {}).get("district") or "",
            "at": str((old or {}).get("at") or ""),
        }, headers=CORS_HEADERS)

    total = await db.qr_count_product(product_id)
    log.info(f"[qr] {label} · {p.get('name','')} · {code[:40]}"
             + (f" · {district}" if district else ""))
    return web.json_response({"ok": True, "new": True, "code": code,
                              "label": label, "seq": seq,
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
async def handle_list(request):
    """Что уже записано по позиции на точке.

    Экран сканирования открывается не с нуля: если полку считали вчера, эти
    бутылки надо видеть — иначе человек не понимает, продолжает он счёт или
    начинает заново."""
    pid = (request.query.get("product_id") or "").strip()
    district = (request.query.get("district") or "").strip()
    if not pid:
        return web.json_response({"error": "product_required"}, status=400,
                                 headers=CORS_HEADERS)
    rows = await db.qr_list(pid, district)
    return web.json_response({"items": rows, "total": len(rows)},
                             headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


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
        ("/api/owner/qr/list",        handle_list,   "GET"),
        ("/api/owner/qr/scan",        handle_scan,   "POST"),
        ("/api/owner/qr/lock",        handle_lock,   "POST"),
        ("/api/owner/qr/unlock",      handle_unlock, "POST"),
        ("/api/owner/qr/undo",        handle_undo,   "POST"),
        ("/api/owner/qr/code/{code}", handle_lookup, "GET"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info("[qr] routes mounted")
