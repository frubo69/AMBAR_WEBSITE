"""
AMBAR — склад: пересчёт, перемещения, заявка и норма.

Что это заменяет
----------------
Бумажную таблицу «отчёт-заявка». В ней продажи считаются вычитанием
(«было + привезли ± перемещение − осталось»), поэтому бой, пересорт и
воровство молча растворяются в выручке: недостача неотличима от продажи.

Приложение знает продажи ИЗ ЗАКАЗОВ — по факту. Поэтому остаток можно
предсказать заранее:

    ожидается = прошлый пересчёт + приход ± перемещения − продано по заказам

Менеджер вводит фактический остаток, и разница — это недостача, отдельно от
продаж. Важно: недостача бывает и там, где ничего не продавали — бой, пересорт,
унесённая водителем бутылка. Поэтому позиции без продаж не «застывают»: каждая
возвращается на проверку, если её не считали дольше STALE_DAYS.

Дальше из тех же чисел собирается заявка:

    заявка = норма − остаток на руках

А саму норму больше не нужно выдумывать: она выводится из средних продаж за
последние дни, и приложение показывает, где норма завышена и сколько денег
из-за этого заморожено на полке.

Весь модуль под require_owner: доступ только владельцу и менеджерам.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS
from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES
from config_stock_order import order_key      # порядок обхода полок, как в таблице

log = logging.getLogger("stock")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12       # рабочие сутки 12:00 → 12:00, как во всей системе
NORM_COVER_DAYS = 3         # на сколько дней запаса рассчитана норма по умолчанию
NORM_WINDOW_DAYS = 14       # по какому окну продаж её считать
STALE_DAYS = 7              # через сколько дней позицию пора проверить заново
HISTORY_DEPTH = 45          # сколько пересчётов смотреть назад в поисках проверки


# ── сутки ────────────────────────────────────────────────────────────────────
def _biz_day(ref: datetime = None) -> str:
    """Дата рабочих суток. Смена идёт с полудня, поэтому ночной пересчёт
    относится к уходящему дню и не расходится с выручкой."""
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _days_between(day_a: str, day_b: str):
    """Сколько дней прошло между двумя рабочими сутками. None — если не считали."""
    if not day_a:
        return None
    try:
        a = datetime.strptime(day_a, "%Y-%m-%d")
        b = datetime.strptime(day_b, "%Y-%m-%d")
    except ValueError:
        return None
    return max(0, (b - a).days)


async def _last_checked(district: str, day: str) -> dict:
    """{product_id: дата последней РУЧНОЙ проверки}.

    Строка, перенесённая расчётом (counted=False), проверкой не считается:
    её никто не видел, и бой или недостача по ней остались бы незамеченными.
    У старых пересчётов поля нет — там сохраняли только то, что смотрели."""
    out = {}
    for c in await db.get_stock_counts_recent(district, before_day=day, limit=HISTORY_DEPTH):
        d = c.get("day") or ""
        for l in c.get("lines", []):
            pid = l.get("id")
            if pid and l.get("counted", True) and pid not in out:
                out[pid] = d
    return out


def _day_bounds(day: str, days: int = 1):
    """(начало, конец) в UTC-ISO для выборки заказов за N рабочих суток."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    return f(d), f(d + timedelta(days=days))


# ── единица учёта ────────────────────────────────────────────────────────────
# Крепкое и вино считают бутылками, пиво — ящиками. Ящик двадцать четыре, и
# половина ящика — обычное дело: двенадцать банок продали, двенадцать остались.
# Отсюда и остатки вида 9.5 в рабочей таблице. Так что единица пива — ящик, а
# шаг — половина; целыми бутылками пиво на складе никто не считает.
CASE = 24                   # бутылок в ящике
STEP = 0.5                  # мельче половины ящика не бывает


def _unit(p: dict) -> int:
    """Сколько бутылок в одной учётной единице позиции."""
    return CASE if (p.get("price_24_full") or p.get("price_12_full")) else 1


def _round_step(v) -> float:
    """К ближайшей половине. Считают глазами, дробей мельче не бывает.

    Нечисло — ноль и запись в лог: одна битая строка не должна ронять
    весь пересчёт района."""
    try:
        return round(round(float(v) / STEP) * STEP, 2)
    except (TypeError, ValueError):
        log.warning(f"[stock] в остатке нечисло: {v!r} — считаем нулём")
        return 0.0


def _num(v):
    """9.5 остаётся 9.5, а 9.0 показывается как 9 — лишний ноль только мешает."""
    v = _round_step(v)
    return int(v) if v == int(v) else v


# ── продажи из заказов ───────────────────────────────────────────────────────
def _qty(it: dict) -> int:
    """Бутылок в строке заказа. Пиво идёт пачками: qty=1 при pcs=12 — это
    двенадцать бутылок, и со склада уйдут именно двенадцать."""
    try:
        q = int(it.get("qty") or 0)
    except (TypeError, ValueError):
        return 0
    if q <= 0:
        return 0
    try:
        pcs = int(it.get("pcs") or 0)
    except (TypeError, ValueError):
        pcs = 0
    return q * (pcs if pcs else 1)


async def _sold(day: str, district: str | None = None, days: int = 1) -> dict:
    """{product_id: продано в учётных единицах}.

    Заказы знают бутылки, склад считает ящиками — здесь одно переводится в
    другое, иначе проданная пачка пива выглядела бы как пропавшие двенадцать.
    Только доставленные: отменённый заказ товар со склада не уносит."""
    start, end = _day_bounds(day, days)
    orders = await db.get_orders_in_range(start, end)
    cat = _catalog()
    out = {}
    for o in orders:
        if o.get("status") != "delivered":
            continue
        if district and (o.get("office_id") or "") != district:
            continue
        for it in (o.get("items") or []):
            pid = it.get("id")
            q = _qty(it)
            if pid and q:
                out[pid] = out.get(pid, 0) + q / _unit(cat.get(pid) or {})
    return {k: _round_step(v) for k, v in out.items()}


def _catalog():
    from owner_routes import _read_catalog
    return {p.get("id"): p for p in _read_catalog()}


def _price(p: dict) -> int:
    """Цена одной учётной единицы — полная, без скидки приложения: пропавшее
    стоит столько, сколько за него платят. Для пива это цена ящика, поэтому
    недостача в полящика оценивается в цену двенадцати бутылок сама собой."""
    if p.get("price_24_full"):
        return int(p["price_24_full"])
    return int(p.get("price_full") or p.get("price") or 0)


# ── норма ────────────────────────────────────────────────────────────────────
async def _suggested_norms(district: str, day: str) -> dict:
    """{product_id: норма} по фактическим продажам района.

    Норма = средние продажи в день × запас на NORM_COVER_DAYS, округлённые
    вверх. В таблице норма проставлена руками и годами не менялась — здесь она
    всегда отражает то, как реально берут именно в этом районе.
    """
    start_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=NORM_WINDOW_DAYS - 1)
                 ).strftime("%Y-%m-%d")
    sold = await _sold(start_day, district, days=NORM_WINDOW_DAYS)
    out = {}
    for pid, total in sold.items():
        per_day = total / NORM_WINDOW_DAYS
        out[pid] = max(1, -(-int(per_day * NORM_COVER_DAYS * 100) // 100))  # ceil
    return out


# ── лист пересчёта ───────────────────────────────────────────────────────────
@require_owner
async def handle_sheet(request):
    """Что проверять в районе и сколько ожидается. ?district=jvc[&day=]"""
    district = (request.query.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = (request.query.get("day") or "").strip() or _biz_day()

    prev = await db.get_last_stock_count(district, before_day=day)
    first_time = prev is None
    prev_lines = {l["id"]: l for l in (prev or {}).get("lines", [])}
    sold = await _sold(day, district)
    moves = await db.get_stock_transfers(day)
    existing = await db.get_stock_count(district, day)
    done = {l["id"]: l for l in (existing or {}).get("lines", [])}
    cat = _catalog()

    # Когда каждую позицию последний раз считали руками. Продажи вычитаются
    # сами, но бой и воровство продажами не считаются — позицию, которую давно
    # никто не видел, надо вернуть на проверку, даже если её не заказывали.
    last_checked = await _last_checked(district, day)

    # Перемещения: ушедшее из района вычитаем, пришедшее прибавляем.
    move_by_pid = {}
    for m in moves:
        pid, q = m.get("product_id"), _round_step(m.get("qty") or 0)
        if not pid or not q:
            continue
        if m.get("from") == district:
            move_by_pid[pid] = move_by_pid.get(pid, 0) - q
        if m.get("to") == district:
            move_by_pid[pid] = move_by_pid.get(pid, 0) + q

    rows = []
    for pid, p in cat.items():
        was = _round_step((prev_lines.get(pid) or {}).get("actual") or 0)
        s = _round_step(sold.get(pid) or 0)
        mv = _round_step(move_by_pid.get(pid) or 0)
        ago = _days_between(last_checked.get(pid), day)
        unit = _unit(p)
        rows.append({
            "id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
            "no": order_key(pid) + 1,          # номер строки в рабочей таблице
            "price": _price(p),
            # Пиво считают ящиками по CASE бутылок и половинками ящика — фронт
            # должен знать и шаг, и что вообще стоит за единицей.
            "unit": unit, "step": STEP if unit > 1 else 1,
            "unit_name": "ящик" if unit > 1 else "бутылка",
            "was": _num(was), "sold": _num(s), "moved_qty": _num(mv),
            # На первом пересчёте сравнивать не с чем — вводим как отправную точку.
            "expected": None if first_time else _num(max(0, was + mv - s)),
            "touched": bool(s or mv),
            "checked_day": last_checked.get(pid, ""),
            "days_ago": ago,
            "stale": (not first_time) and (ago is None or ago >= STALE_DAYS),
            "actual": (done.get(pid) or {}).get("actual"),
            # Отметка ревизии: ok — проверено и сошлось, diff — расхождение.
            # Хранится с прошлого захода, иначе ревизию нельзя прервать.
            "mark": (done.get(pid) or {}).get("mark"),
        })
    # Сначала то, где расхождение видно сразу (двигалось), потом то, что давно
    # не проверяли: именно там прячутся бой и недостача без продаж. А внутри
    # каждой группы — порядок таблицы, то есть порядок полок: считать удобнее
    # подряд, чем прыгать по залу за алфавитом.
    rows.sort(key=lambda r: (not r["touched"], not r["stale"], order_key(r["id"])))

    return web.json_response({
        "district": district,
        "district_name": OFFICE_NAMES.get(district, district),
        "district_code": OFFICE_CODES.get(district, ""),
        "day": day, "first_time": first_time,
        "prev_day": (prev or {}).get("day", ""),
        "audit": {
            "started_at":  (existing or {}).get("audit_started_at", ""),
            "finished_at": (existing or {}).get("audit_finished_at", ""),
            "marked": sum(1 for r in rows if r["mark"]),
        },
        "touched_count": sum(1 for r in rows if r["touched"]),
        "stale_count": sum(1 for r in rows if r["stale"] and not r["touched"]),
        "stale_days": STALE_DAYS,
        "total_count": len(rows),
        "saved": bool(existing),
        "rows": rows,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_save(request):
    """Сохранить пересчёт или ревизию.

    body: {district, day?, lines:[{id, actual, income?, auto?, mark?}],
           audit?: bool, finish?: bool}

    Ревизия — это тот же пересчёт, но пройденный целиком и с отметкой на каждой
    позиции: ok — посмотрел, сошлось; diff — не сошлось. Закрыть её можно
    только когда отмечены все, иначе «ревизия проведена» означало бы «часть
    полок посмотрели, а часть посчитали на глаз»."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = str(body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()

    prev = await db.get_last_stock_count(district, before_day=day)
    first_time = prev is None
    prev_lines = {l["id"]: l for l in (prev or {}).get("lines", [])}
    sold = await _sold(day, district)
    moves = await db.get_stock_transfers(day)
    cat = _catalog()

    mv_by = {}
    for m in moves:
        pid, q = m.get("product_id"), _round_step(m.get("qty") or 0)
        if not pid or not q:
            continue
        if m.get("from") == district: mv_by[pid] = mv_by.get(pid, 0) - q
        if m.get("to")   == district: mv_by[pid] = mv_by.get(pid, 0) + q

    is_audit = bool(body.get("audit"))
    finish = bool(body.get("finish"))
    lines, short_qty, short_aed, over_qty, counted_qty = [], 0, 0, 0, 0
    marked = matched = mismatched = 0
    for raw in (body.get("lines") or []):
        pid = raw.get("id"); p = cat.get(pid)
        if not p:
            continue
        try:
            actual = max(0.0, _round_step(raw.get("actual") or 0))
        except (TypeError, ValueError):
            continue
        try:
            income = max(0.0, _round_step(raw.get("income") or 0))
        except (TypeError, ValueError):
            income = 0.0
        was = _round_step((prev_lines.get(pid) or {}).get("actual") or 0)
        s   = _round_step(sold.get(pid) or 0)
        mv  = _round_step(mv_by.get(pid) or 0)
        expected = None if first_time else max(0.0, _round_step(was + income + mv - s))
        # Строка, до которой не дошли руки. Она сохраняется расчётным значением
        # — иначе завтра не с чем будет сравнивать и заявка не узнает остаток, —
        # но проверкой не считается: бой и воровство продажами не пахнут и
        # вылезают именно там, где никто не смотрел.
        auto = bool(raw.get("auto")) and expected is not None
        if auto:
            actual = expected
        diff = None if expected is None else _round_step(expected - actual)  # >0 — не хватает
        price = _price(p)
        if diff:
            if diff > 0: short_qty += diff; short_aed += diff * price
            else:        over_qty  += -diff
        if not auto:
            counted_qty += 1
        mark = raw.get("mark") if raw.get("mark") in ("ok", "diff") else None
        if mark:
            marked += 1
            if mark == "ok": matched += 1
            else:            mismatched += 1
        lines.append({"id": pid, "name": p.get("name", ""), "price": price,
                      "unit": _unit(p),
                      "was": was, "income": income, "moved_qty": mv, "sold": s,
                      "expected": expected, "actual": actual, "diff": diff,
                      "counted": not auto, "mark": mark})

    if finish and marked < len(lines):
        return web.json_response(
            {"error": "audit_incomplete", "marked": marked, "total": len(lines)},
            status=409, headers=CORS_HEADERS)

    now_iso = datetime.now(timezone.utc).isoformat()
    prev_doc = await db.get_stock_count(district, day) or {}
    doc = {"district": district, "district_name": OFFICE_NAMES.get(district, district),
           "day": day, "first_time": first_time,
           "counted_by": request["owner_id"],
           "counted_at": now_iso,
           "lines": lines, "short_qty": _num(short_qty), "short_aed": round(short_aed),
           "over_qty": _num(over_qty),
           "counted_qty": counted_qty, "total_qty": len(lines)}
    if is_audit or marked:
        doc["audit_started_at"] = prev_doc.get("audit_started_at") or now_iso
        doc["marked_qty"] = marked
        doc["matched_qty"] = matched
        doc["mismatch_qty"] = mismatched
        if finish:
            doc["audit_finished_at"] = now_iso
            doc["audit_by"] = request["owner_id"]
    await db.save_stock_count(district, day, doc)
    log.info(f"[stock] {district} {day}: {len(lines)} позиций, "
             f"недостача {short_qty} шт / {short_aed} AED")
    return web.json_response(
        {"ok": True, "day": day, "first_time": first_time,
         "short_qty": _num(short_qty), "short_aed": round(short_aed),
         "over_qty": _num(over_qty),
         "counted_qty": counted_qty, "total_qty": len(lines),
         "audit": bool(is_audit or marked), "finished": bool(finish),
         "marked": marked, "matched": matched, "mismatched": mismatched,
         "lines": [l for l in lines if l["diff"]]}, headers=CORS_HEADERS)


# ── перемещения между районами ───────────────────────────────────────────────
@require_owner
async def handle_transfer(request):
    """Перевезти товар из района в район. body: {from, to, product_id, qty, day?}

    Перемещение не меняет общий остаток — только чей он. Поэтому в пересчёте
    оно вычитается у отдающего и прибавляется принимающему, и недостача из-за
    переезда не появляется."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    src, dst = str(body.get("from") or ""), str(body.get("to") or "")
    pid = str(body.get("product_id") or "")
    try:
        qty = _round_step(body.get("qty") or 0)     # полящика переехать тоже может
    except (TypeError, ValueError):
        qty = 0
    if src not in OFFICE_IDS or dst not in OFFICE_IDS or src == dst:
        return web.json_response({"error": "bad_districts"}, status=400, headers=CORS_HEADERS)
    if pid not in _catalog() or qty <= 0:
        return web.json_response({"error": "bad_item"}, status=400, headers=CORS_HEADERS)

    day = str(body.get("day") or "").strip() or _biz_day()
    doc = {"day": day, "from": src, "to": dst, "product_id": pid,
           "product_name": _catalog()[pid].get("name", ""), "qty": _num(qty),
           "by": request["owner_id"],
           "at": datetime.now(timezone.utc).isoformat()}
    await db.add_stock_transfer(doc)
    log.info(f"[stock] перемещение {qty}×{pid}: {src} → {dst} ({day})")
    return web.json_response({"ok": True, **doc}, headers=CORS_HEADERS)


@require_owner
async def handle_transfer_delete(request):
    """Убрать перемещение, введённое по ошибке."""
    tid = (request.match_info.get("tid") or "").strip()
    ok = await db.delete_stock_transfer(tid)
    return web.json_response({"ok": ok}, status=200 if ok else 404, headers=CORS_HEADERS)


@require_owner
async def handle_transfers(request):
    day = (request.query.get("day") or "").strip() or _biz_day()
    rows = await db.get_stock_transfers(day)
    for r in rows:
        r["id"] = str(r.pop("_id", ""))
        r["from_name"] = OFFICE_NAMES.get(r.get("from"), r.get("from"))
        r["to_name"] = OFFICE_NAMES.get(r.get("to"), r.get("to"))
    return web.json_response({"day": day, "transfers": rows}, headers=CORS_HEADERS)


# ── заявка ───────────────────────────────────────────────────────────────────
@require_owner
async def handle_order(request):
    """Заявка на закупку: сколько довезти в каждый район, чтобы вернуться к норме.

    заявка = норма − остаток на руках. Норма берётся сохранённая, а если её не
    задавали — рассчитанная по продажам. Отдаём и то и другое, чтобы владелец
    видел, где норма расходится с реальным спросом.
    """
    day = (request.query.get("day") or "").strip() or _biz_day()
    cat = _catalog()
    saved_norms = await db.get_stock_norms()
    rows, total_aed, total_qty = [], 0, 0
    frozen_aed = 0          # деньги, стоящие на полке сверх реального спроса

    per_district = {}
    for oid in OFFICE_IDS:
        cnt = await db.get_last_stock_count(oid, before_day=None)
        have = {l["id"]: int(l.get("actual") or 0) for l in (cnt or {}).get("lines", [])}
        sug = await _suggested_norms(oid, day)
        per_district[oid] = {"have": have, "sug": sug,
                             "counted": (cnt or {}).get("day", "")}

    for pid, p in cat.items():
        price = _price(p)
        cells, item_total = {}, 0
        for oid in OFFICE_IDS:
            d = per_district[oid]
            have = int(d["have"].get(pid) or 0)
            sug = int(d["sug"].get(pid) or 0)
            norm = int((saved_norms.get(f"{oid}:{pid}") or sug or 0))
            need = max(0, norm - have)
            cells[oid] = {"have": have, "norm": norm, "suggested": sug, "need": need}
            item_total += need
            if sug and norm > sug:
                frozen_aed += (norm - sug) * price
        if item_total:
            total_qty += item_total
            total_aed += item_total * price
        rows.append({"id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
                     "price": price, "need_total": item_total, "cells": cells})

    rows.sort(key=lambda r: (-r["need_total"], r["name"]))
    return web.json_response({
        "day": day,
        "districts": [{"id": o, "code": OFFICE_CODES.get(o, ""),
                       "name": OFFICE_NAMES.get(o, o),
                       "counted": per_district[o]["counted"]} for o in OFFICE_IDS],
        "total_qty": total_qty, "total_aed": total_aed,
        "frozen_aed": frozen_aed,
        "cover_days": NORM_COVER_DAYS, "window_days": NORM_WINDOW_DAYS,
        "rows": [r for r in rows if r["need_total"] > 0],
    }, headers=CORS_HEADERS)


@require_owner
async def handle_set_norm(request):
    """Задать норму вручную. body: {district, product_id, norm}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    d, pid = str(body.get("district") or ""), str(body.get("product_id") or "")
    try:
        norm = max(0, int(body.get("norm") or 0))
    except (TypeError, ValueError):
        norm = 0
    if d not in OFFICE_IDS or pid not in _catalog():
        return web.json_response({"error": "bad_args"}, status=400, headers=CORS_HEADERS)
    await db.set_stock_norm(d, pid, norm, request["owner_id"])
    return web.json_response({"ok": True, "district": d, "product_id": pid, "norm": norm},
                             headers=CORS_HEADERS)


# ── сводка дня ───────────────────────────────────────────────────────────────
@require_owner
async def handle_status(request):
    day = (request.query.get("day") or "").strip() or _biz_day()
    counts = await db.get_stock_counts_for_day(day)
    by_d = {c["district"]: c for c in counts}
    districts = [{
        "id": oid, "code": OFFICE_CODES.get(oid, ""), "name": OFFICE_NAMES.get(oid, oid),
        "done": oid in by_d,
        "first_time": bool((by_d.get(oid) or {}).get("first_time")),
        "short_qty": int((by_d.get(oid) or {}).get("short_qty") or 0),
        "short_aed": int((by_d.get(oid) or {}).get("short_aed") or 0),
        "counted_at": (by_d.get(oid) or {}).get("counted_at", ""),
    } for oid in OFFICE_IDS]
    return web.json_response({
        "day": day,
        "done": sum(1 for d in districts if d["done"]), "total": len(districts),
        "short_aed": sum(d["short_aed"] for d in districts),
        "short_qty": sum(d["short_qty"] for d in districts),
        "districts": districts,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_audits(request):
    """История ревизий: что и когда закрывали. Только заголовки — сами позиции
    приходят с /stock/result, когда открывают конкретную."""
    rows = await db.get_finished_audits(limit=40)
    out = []
    for c in rows:
        out.append({
            "district": c.get("district", ""),
            "district_code": OFFICE_CODES.get(c.get("district"), ""),
            "district_name": OFFICE_NAMES.get(c.get("district"), c.get("district", "")),
            "day": c.get("day", ""),
            "started_at": c.get("audit_started_at", ""),
            "finished_at": c.get("audit_finished_at", ""),
            "total": int(c.get("total_qty") or 0),
            "matched": int(c.get("matched_qty") or 0),
            "mismatched": int(c.get("mismatch_qty") or 0),
            "short_qty": c.get("short_qty") or 0,
            "short_aed": int(c.get("short_aed") or 0),
            "over_qty": c.get("over_qty") or 0,
        })
    return web.json_response({"audits": out}, headers=CORS_HEADERS)


@require_owner
async def handle_result(request):
    district = (request.query.get("district") or "").strip()
    day = (request.query.get("day") or "").strip() or _biz_day()
    c = await db.get_stock_count(district, day)
    if not c:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    c.pop("_id", None)
    lines = c.get("lines", [])
    # В отчёте нужны и расхождения, и то, что менеджер отметил крестиком: он мог
    # поправить число до совпадения с ожидаемым, и diff обнулился — но факт,
    # что на полке лежало иначе, из отчёта пропадать не должен.
    c["lines"] = sorted(
        [l for l in lines if l.get("diff") or l.get("mark") == "diff"],
        key=lambda l: -abs((l.get("diff") or 0) * (l.get("price") or 0)))
    c["counted_lines"] = sum(1 for l in lines if l.get("counted"))
    c["district_code"] = OFFICE_CODES.get(c.get("district"), "")
    return web.json_response(c, headers=CORS_HEADERS)


def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/stock/sheet",     handle_sheet,     "GET"),
        ("/api/owner/stock/status",    handle_status,    "GET"),
        ("/api/owner/stock/result",    handle_result,    "GET"),
        ("/api/owner/stock/order",     handle_order,     "GET"),
        ("/api/owner/stock/transfers", handle_transfers, "GET"),
        ("/api/owner/stock/audits",    handle_audits,    "GET"),
        ("/api/owner/stock/count",     handle_save,      "POST"),
        ("/api/owner/stock/transfer",  handle_transfer,  "POST"),
        ("/api/owner/stock/norm",      handle_set_norm,  "POST"),
        ("/api/owner/stock/transfer/{tid}", handle_transfer_delete, "DELETE"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, handler)
    log.info("[stock] routes mounted")
