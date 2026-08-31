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
import asyncio, logging, re, time as _t
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
    _t0 = _t.monotonic()
    # Шесть выборок по одному реестру — разом. По очереди они складывались в
    # шесть задержек до базы подряд, а зависимости между ними нет.
    st, by_product, last, locks, by_district, by_prod_dist = await asyncio.gather(
        db.qr_stats(),
        db.qr_by_product(),
        db.qr_last(limit=20),
        db.qr_locks(datetime.now(timezone.utc)),
        db.qr_by_district(),
        db.qr_by_product_district_all(),
    )
    _t_reg = _t.monotonic() - _t0
    # Позиции — в порядке рабочей таблицы, тем же номером от 1 до 123, что на
    # бумажном листе и в отчётах. Реестр заполняют, стоя у полки, и «по убыванию
    # количества» здесь означает прыгать по залу: ряд идёт как идёт, а глазами
    # человек сверяется со строкой листа.
    from config_stock_order import order_key
    for r in by_product:
        r["no"] = order_key(r.get("product_id") or "") + 1
    by_product.sort(key=lambda r: r["no"])
    # Реестр знает, что бутылку внесли, и не знает, что её увезли: на доставке
    # коды никто не сканирует. Поэтому «продано» и «списано» берём оттуда, где
    # это правда, — из доставленных заказов и журнала списаний, с того дня,
    # когда на точке начали вести реестр. Иначе экран вечно показывает число,
    # которое было верным ровно один день.
    sold = written = 0
    left = {}
    _t1 = _t.monotonic()
    try:
        since = await db.qr_since_by_district()
        used = await db.qr_consumed(since)
        for oid, n in by_district.items():
            u = used.get(oid) or {}
            sold += int(u.get("sold") or 0)
            written += int(u.get("written") or 0)
            left[oid] = max(0, n - int(u.get("sold") or 0) - int(u.get("written") or 0))
    except Exception as e:
        log.warning(f"[qr] расход по реестру не посчитан: {e}")
        left = dict(by_district)
    # Сколько бутылок лежит на полке мимо реестра. Это и есть работа, которая
    # осталась: пока позиция не заведена кодами, камера её не видит, ревизия
    # ищет её глазами, а проверка бутылки на неё ответить не может.
    #
    # Ожидаемое берём из количественного учёта (последний пересчёт + приход −
    # продажи), кодовое — из реестра, и переводим в бутылки: пиво в пересчёте
    # ходит ящиками, а коды — поштучно. Где пересчёта не было вовсе, сравнивать
    # не с чем: такую точку в число не считаем и говорим о ней отдельно.
    unscanned, no_count = {}, []
    _t2 = _t.monotonic()
    try:
        import stock_routes as SR
        base = await SR._district_base(SR._biz_day())
        cat = SR._catalog()
        by_pd = by_prod_dist
        for oid in SR.OFFICE_IDS:
            have = (base.get(oid) or {}).get("have") or {}
            bottles = sum(round(float(q) * SR._unit(cat.get(pid) or {}))
                          for pid, q in have.items() if pid in cat and q)
            coded = sum((by_pd.get(oid) or {}).values())
            if not bottles and not coded:
                no_count.append(oid)
                continue
            unscanned[oid] = max(0, bottles - coded)
    except Exception as e:
        log.warning(f"[qr] не посчитано, сколько без кодов: {e}")
        unscanned, no_count = {}, []

    # Экран открывают у полки, с телефона, и ждут его молча. Если сборка
    # заняла больше секунды — пусть в журнале останется, на чём именно.
    _all = _t.monotonic() - _t0
    if _all > 1.0:
        log.warning(f"[qr] сводка собиралась {_all:.1f} с: реестр {_t_reg:.1f}, "
                    f"расход {_t2 - _t1:.1f}, без кодов {_t.monotonic() - _t2:.1f}")

    return web.json_response({
        "locks": locks,
        "unscanned": unscanned,
        "unscanned_total": sum(unscanned.values()),
        "no_count": no_count,
        "by_district": by_district,
        "by_product_district": by_prod_dist,
        "left_by_district": left,
        "slugs": slug_map(),
        "totals": {
            "total":   sum(st.values()),
            "active":  st.get("active", 0),
            "sold":    sold,
            "written": written,
            "left":    max(0, st.get("active", 0) - sold - written),
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


async def _who(uid) -> str:
    """Имя человека по его записи в базе. Номера телеграма в ответе не бывает."""
    try:
        u = await db.get_user(int(uid or 0)) or {}
    except Exception:
        u = {}
    имя = (u.get("full_name") or u.get("name")
           or f"{u.get('first_name','')} {u.get('last_name','')}".strip())
    # Рабочее имя старше того, что стоит в телеграме и в карточке клиента.
    import config_staff as staff
    return staff.display_name(uid, имя)


async def _story(doc: dict) -> list:
    """Что было с этой бутылкой — от первой записи до последнего касания.

    Собираем из четырёх мест сразу: сама запись реестра (когда завели, куда
    переезжала, списывали ли), проверки у водителей и ревизия проходом. Порознь
    ни одно из них не отвечает на вопрос «что с ней вообще происходило», а
    спрашивают именно его — перед тем, как убрать бутылку из реестра."""
    code = doc.get("code") or ""
    out = []
    if doc.get("at"):
        out.append({"at": str(doc.get("at")), "what": "заведена",
                    "who": await _who(doc.get("by")),
                    "where": doc.get("district") or ""})
    for m in (doc.get("moves") or []):
        out.append({"at": str(m.get("at") or ""), "what": "переезд",
                    "who": await _who(m.get("by")),
                    "where": f"{m.get('from') or ''} → {m.get('to') or ''}"})
    if doc.get("written_at"):
        w = {}
        try:
            w = await db.writeoff_get(doc.get("writeoff") or "") or {}
        except Exception:
            w = {}
        out.append({"at": str(doc.get("written_at")), "what": "списана",
                    "who": w.get("by") or "",
                    "where": " · ".join(x for x in (w.get("kind") or "",
                                                    w.get("note") or "") if x)})
    try:
        for c in await db.qr_seen_in_checks(code):
            out.append({"at": str(c.get("at") or ""), "what": "проверка у водителя",
                        "who": c.get("driver") or "", "where": c.get("district") or ""})
        for a in await db.qr_seen_in_audits(code):
            out.append({"at": str(a.get("at") or a.get("day") or ""), "what": "ревизия",
                        "who": "", "where": a.get("district") or ""})
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[qr] история кода собрана не вся: {e}")
    if doc.get("del_at"):
        out.append({"at": str(doc.get("del_at")), "what": "убрана из реестра",
                    "who": await _who(doc.get("del_by")), "where": ""})
    out.sort(key=lambda r: r["at"])
    return out


# ── сообщение владельцу об убранных бутылках ────────────────────────────────
# Бутылка, вычеркнутая из реестра, — это минус в остатке, сделанный руками и
# без бумаги. Владелец должен об этом узнать, как и о правке закрытого дня.
#
# Но не по сообщению на каждый скан: полку чистят подряд, и десяток бутылок
# превратился бы в десяток сообщений — а первое из них ушло бы раньше, чем
# человек успел нажать крестик и вернуть бутылку назад. Поэтому копим и шлём
# одним письмом через полминуты тишины; возврат за это время просто вынимает
# строку из письма.
DROP_QUIET = 25                       # секунд тишины до отправки
_DROPS: dict = {}                     # кто убирал → накопленное


def _plural(n, one, few, many) -> str:
    n = abs(int(n or 0)) % 100
    d = n % 10
    if 10 < n < 20: return many
    if 1 < d < 5:   return few
    if d == 1:      return one
    return many


async def _drop_send(me: int) -> None:
    try:
        await asyncio.sleep(DROP_QUIET)
    except asyncio.CancelledError:
        return
    st = _DROPS.pop(me, None)
    if not st or not st["items"]:
        return
    try:
        from owner_routes import notify_owners_force, _md
    except Exception as e:                       # noqa: BLE001
        log.error(f"[qr] сообщение об удалении не отправлено: {e}")
        return
    from config_offices import OFFICE_CODES, OFFICE_NAMES
    n = len(st["items"])
    строки = [
        "✂️ *Убрано из реестра*",
        f"{_md(st['who'])} убрал {n} "
        + _plural(n, "бутылку", "бутылки", "бутылок"),
        "",
    ]
    for it in st["items"][:30]:
        d = it.get("district") or ""
        где = f"{OFFICE_CODES.get(d, '')} {OFFICE_NAMES.get(d, '')}".strip() or d
        часть = [it.get("product_name") or "позиция не указана"]
        if it.get("label"): часть.append(it["label"])
        if где: часть.append(где)
        if it.get("was") == "written": часть.append("была списана")
        строки.append("• " + _md(" · ".join(часть)))
    if n > 30:
        строки.append(f"…и ещё {n - 30}")
    try:
        await notify_owners_force("qr.dropped", "\n".join(строки))
    except Exception as e:                       # noqa: BLE001
        log.error(f"[qr] сообщение об удалении не ушло: {e}")


def _drop_note(me: int, who: str, item: dict) -> None:
    st = _DROPS.setdefault(me, {"who": "—", "items": [], "task": None})
    if who:
        st["who"] = who
    st["items"].append(item)
    if st["task"]:
        st["task"].cancel()
    st["task"] = asyncio.create_task(_drop_send(me))


def _drop_forget(me: int, code: str) -> None:
    """Бутылку вернули — вычёркиваем её из ещё не ушедшего письма."""
    st = _DROPS.get(me)
    if not st:
        return
    st["items"] = [x for x in st["items"] if x.get("code") != code]
    if not st["items"]:
        if st["task"]:
            st["task"].cancel()
        _DROPS.pop(me, None)


@require_owner
async def handle_drop(request):
    """Убрать бутылку из реестра по её коду.

    Не стираем: запись остаётся со статусом «удалена», и потому решение
    отменяемо, а история бутылки не пропадает вместе с ней."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = _clean(body.get("code"))
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)
    doc = await db.qr_drop(code, request.get("owner_id") or 0,
                           datetime.now(timezone.utc))
    if not doc:
        old = await db.qr_get(code)
        return web.json_response(
            {"error": "already_deleted" if old else "unknown_code", "code": code},
            status=404, headers=CORS_HEADERS)
    log.info(f"[qr] убрана из реестра {doc.get('label') or code}")
    # Имя приходит от приложения; если его нет — берём из базы. «—» вместо
    # человека в таком сообщении обесценивает его целиком.
    кто = str(body.get("as") or "").strip()[:40]
    if not кто:
        кто = await _who(request.get("owner_id") or 0)
    _drop_note(request.get("owner_id") or 0, кто,
               {"code": code, "label": doc.get("label") or "",
                "product_name": doc.get("product_name") or "",
                "district": doc.get("district") or "",
                "was": doc.get("status") or ""})
    return web.json_response({
        "ok": True, "code": code,
        "label": doc.get("label") or "",
        "product_name": doc.get("product_name") or "",
        "district": doc.get("district") or "",
        "was": doc.get("status") or "active",
        "at": str(doc.get("at") or ""),
        "story": await _story(doc),
    }, headers=CORS_HEADERS,
       dumps=lambda o: __import__("json").dumps(o, default=str))


@require_owner
async def handle_drop_undo(request):
    """Вернуть бутылку в реестр — тем же статусом, с каким её убирали."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = _clean(body.get("code"))
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)
    ok = await db.qr_drop_undo(code)
    if ok:
        log.info(f"[qr] удаление отменено {code}")
        _drop_forget(request.get("owner_id") or 0, code)
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
    doc["story"] = await _story(doc)
    return web.json_response(doc, headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


# ── проверка бутылок ────────────────────────────────────────────────────────
# Подошли к машине водителя и просканировали, что он в ней возит. Это не
# ревизия склада, а проверка человека: главный ответ — «наша бутылка или нет».
#
# Вердикт считает сервер, а не приложение: правило одно на всех, и подменить
# его с телефона нельзя.
#
#   ok             наша, в остатке — зелёная
#   sold           наша, ушла с заказом: водитель её и везёт — зелёная
#   written        наша, но списана — жёлтая: списанное ездить не должно
#   other_district наша, но заведена на другом районе — жёлтая: так бывает,
#                  когда перекидывают товар, но знать об этом надо
#   alien          в реестре нет — красная, это и есть повод для флажка
VERDICTS = ("ok", "sold", "written", "other_district", "alien")


def _verdict(doc: dict, driver_district: str) -> str:
    if not doc:
        return "alien"
    st = (doc.get("status") or "active").strip()
    # Убранная из реестра бутылка — для проверки та же чужая: в остатке её нет.
    if st == "deleted":
        return "alien"
    if st == "written":
        return "written"
    d = (doc.get("district") or "").strip()
    if driver_district and d and d != driver_district:
        return "other_district"
    return "sold" if st == "sold" else "ok"


def _driver_district(name: str) -> str:
    if not name:
        return ""
    import config_staff as staff
    for d, drivers in staff.DISTRICT_DRIVERS.items():
        if name in drivers:
            return d
    return ""


@require_owner
async def handle_check_start(request):
    """Начать проверку. Водитель необязателен: бывает и свободная проверка."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    driver = str(body.get("driver") or "").strip()
    if driver:
        import config_staff as staff
        await _staff_fresh()
        if driver not in staff.driver_names():
            return web.json_response({"error": "unknown_driver"}, status=400,
                                     headers=CORS_HEADERS)
    me = request.get("owner_id") or 0
    who = str(body.get("as") or "").strip()
    cid = await db.qr_check_start(driver, _driver_district(driver), me, who)
    log.info(f"[qr] проверка начата · {driver or 'свободная'} · {who or me}")
    return web.json_response({"ok": True, "check_id": cid, "driver": driver},
                             headers=CORS_HEADERS)


@require_owner
async def handle_check_scan(request):
    """Проверить одну бутылку и записать её в проверку."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = _clean(body.get("code"))
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)
    cid = str(body.get("check_id") or "").strip()
    chk = await db.qr_check_get(cid) if cid else None
    if not chk:
        return web.json_response({"error": "no_check"}, status=400, headers=CORS_HEADERS)

    doc = await db.qr_get(code)
    verdict = _verdict(doc, chk.get("district") or "")
    item = {
        "code": code,
        "at": datetime.now(timezone.utc),
        "verdict": verdict,
        "label": (doc or {}).get("label") or "",
        "product_id": (doc or {}).get("product_id") or "",
        "product_name": (doc or {}).get("product_name") or "",
        "district": (doc or {}).get("district") or "",
        "status": (doc or {}).get("status") or "",
        "added_at": str((doc or {}).get("at") or ""),
    }
    fresh = await db.qr_check_add(cid, item)
    if verdict == "alien":
        log.warning(f"[qr] чужая бутылка при проверке {chk.get('driver') or 'свободной'}: {code[:40]}")
    return web.json_response({"ok": True, "new": fresh, **item,
                              "at": item["at"].isoformat()}, headers=CORS_HEADERS)


@require_owner
async def handle_check_end(request):
    """Закрыть проверку. Про чужие бутылки владелец узнаёт разом, по итогу:
    сообщение на каждую превратило бы проверку в очередь уведомлений."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    cid = str(body.get("check_id") or "").strip()
    chk = await db.qr_check_end(cid)
    if not chk:
        return web.json_response({"error": "no_check"}, status=404, headers=CORS_HEADERS)
    bad, warn = chk.get("bad", 0), chk.get("warn", 0)
    who = chk.get("driver") or "свободная проверка"
    log.info(f"[qr] проверка закрыта · {who} · {chk.get('total',0)} шт · чужих {bad}")
    if bad:
        try:
            from owner_routes import notify_owners_force
            alien = [i for i in chk.get("items", []) if i.get("verdict") == "alien"]
            await notify_owners_force(
                "qr.alien",
                f"🚩 *Проверка бутылок — {_md(who)}*\n"
                f"Проверено: {chk.get('total', 0)}\n"
                f"*Не наших: {bad}*" + (f" · вопросов: {warn}" if warn else "") + "\n"
                + "\n".join(f"`{str(i.get('code',''))[:28]}`" for i in alien[:5]))
        except Exception as e:
            log.error(f"[qr] уведомление о чужих: {e}")
    return web.json_response({"ok": True, **_check_view(chk)}, headers=CORS_HEADERS)


def _md(s: str) -> str:
    return str(s or "").replace("*", "").replace("_", "").replace("`", "")


def _check_view(c: dict) -> dict:
    return {
        "id": c.get("id") or "",
        "driver": c.get("driver") or "",
        "district": c.get("district") or "",
        "by_name": c.get("by_name") or "",
        "at": str(c.get("at") or ""),
        "open": bool(c.get("open")),
        "total": c.get("total", 0),
        "bad": c.get("bad", 0),
        "warn": c.get("warn", 0),
        "items": [{**i, "at": str(i.get("at") or "")} for i in (c.get("items") or [])],
    }


@require_owner
async def handle_checks(request):
    """История проверок и флажки по водителям.

    Флажок — не наказание, а повод спросить: у этого водителя за период
    находили бутылки не из реестра.
    """
    try:
        days = max(1, min(180, int(request.query.get("days", "30") or 30)))
    except ValueError:
        days = 30
    rows = await db.qr_checks(days)
    by_driver = {}
    for c in rows:
        n = c.get("driver") or ""
        if not n:
            continue
        d = by_driver.setdefault(n, {"driver": n, "checks": 0, "bottles": 0,
                                     "bad": 0, "warn": 0, "last": ""})
        d["checks"] += 1
        d["bottles"] += c.get("total", 0)
        d["bad"] += c.get("bad", 0)
        d["warn"] += c.get("warn", 0)
        d["last"] = d["last"] or str(c.get("at") or "")
    return web.json_response({
        "days": days,
        "checks": [_check_view(c) for c in rows],
        "drivers": sorted(by_driver.values(), key=lambda x: (-x["bad"], -x["checks"])),
        "totals": {"checks": len(rows),
                   "bottles": sum(c.get("total", 0) for c in rows),
                   "bad": sum(c.get("bad", 0) for c in rows)},
    }, headers=CORS_HEADERS)


async def _staff_fresh():
    import config_staff as staff
    try:
        staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
    except Exception as e:
        log.warning(f"[qr] перестановка не прочитана: {e}")


@require_owner
async def handle_drivers(request):
    """Кого можно проверить: водители по районам, как их видит владелец."""
    await _staff_fresh()
    import config_staff as staff
    from config_offices import OFFICE_IDS, OFFICE_CODES, OFFICE_NAMES
    return web.json_response({
        "districts": [{"id": d, "code": OFFICE_CODES.get(d, ""),
                       "name": OFFICE_NAMES.get(d, d),
                       "drivers": list(staff.DISTRICT_DRIVERS.get(d, []))}
                      for d in OFFICE_IDS],
    }, headers=CORS_HEADERS)


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
        ("/api/owner/qr/drop",        handle_drop,   "POST"),
        ("/api/owner/qr/drop/undo",   handle_drop_undo, "POST"),
        ("/api/owner/qr/code/{code}", handle_lookup, "GET"),
        ("/api/owner/qr/drivers",     handle_drivers, "GET"),
        ("/api/owner/qr/checks",      handle_checks,  "GET"),
        ("/api/owner/qr/check/start", handle_check_start, "POST"),
        ("/api/owner/qr/check/scan",  handle_check_scan,  "POST"),
        ("/api/owner/qr/check/end",   handle_check_end,   "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info("[qr] routes mounted")
