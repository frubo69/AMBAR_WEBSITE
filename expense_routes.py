"""
AMBAR — расходы по водителям.

Что считаем
-----------
Питание. Платят каждый день и всем, но по разным ставкам: вышел на смену — 80,
не вышел — 40. Ставка зависит от факта выхода, а факт выхода знает только
старший оператор, поэтому утро начинается с отметки «кто сегодня работает».

Пока водитель не отмечен, расход по нему НЕ начисляется. Это принципиально:
80 и 40 — разные деньги, и подставить любое из них «по умолчанию» значит
показать владельцу сумму, которой никто не подтверждал.

Плюс разовые траты — бензин, мойка, штраф — с обязательным комментарием: расход
без объяснения через неделю не отличить от ошибки ввода.

Чего здесь нет
--------------
Долгов. В бумажной таблице они лежали рядом с питанием, но долг — это не
расход: деньги не потрачены, они у клиента. Смешивать их в одной сумме значило
бы каждый день получать цифру, которая не отвечает ни на один вопрос.

Весь модуль под require_owner: доступ только владельцу и менеджерам.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import config_staff as staff
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("expenses")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12          # рабочие сутки 12:00 → 12:00, как во всей системе


def _biz_day(ref: datetime = None) -> str:
    """Дата рабочих суток. Смена идёт с полудня, поэтому расход, записанный
    ночью, относится к уходящему дню и не расходится с выручкой."""
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _amount(v) -> int:
    """Сумма расхода в дирхамах. Копейки здесь не встречаются, а мусор из тела
    запроса не должен ронять сохранение."""
    try:
        return max(0, int(round(float(v))))
    except (TypeError, ValueError):
        return 0


def _status(e: dict) -> str:
    """Статус траты. Записи без поля — те, что вносил менеджер сам, до появления
    согласования: он их и вносил, значит они утверждены по факту."""
    return e.get("status") or "approved"


def _day_row(d: dict, saved: dict) -> dict:
    """Строка водителя за день: отметка, питание и разовые траты.

    В сумму дня идёт только утверждённое. Трата, которую водитель прислал сам,
    ждёт менеджера: иначе водитель назначал бы себе деньги, а владелец видел бы
    расход, которого никто не одобрял."""
    working = (saved or {}).get("working")
    extras = list((saved or {}).get("extras") or [])
    meal = 0
    if working is True:    meal = staff.MEAL_WORKING
    elif working is False: meal = staff.MEAL_OFF
    extra_sum = sum(_amount(e.get("amount")) for e in extras if _status(e) == "approved")
    pending = sum(_amount(e.get("amount")) for e in extras if _status(e) == "pending")
    return {
        **d,
        "working": working,                 # None — ещё не отмечали
        "meal": meal,
        "extras": extras,
        "extra_sum": extra_sum,
        "pending_sum": pending,
        "pending_count": sum(1 for e in extras if _status(e) == "pending"),
        "total": meal + extra_sum,
    }


def _totals(rows: list) -> dict:
    marked = [r for r in rows if r["working"] is not None]
    return {
        "meal":     sum(r["meal"] for r in rows),
        "extra":    sum(r["extra_sum"] for r in rows),
        "all":      sum(r["total"] for r in rows),
        "pending":  sum(r["pending_sum"] for r in rows),
        "pending_count": sum(r["pending_count"] for r in rows),
        "working":  sum(1 for r in rows if r["working"] is True),
        "off":      sum(1 for r in rows if r["working"] is False),
        "marked":   len(marked),
        "total_drivers": len(rows),
    }


@require_owner
async def handle_day(request):
    """GET /api/owner/expenses?day= — расходы за день по каждому водителю."""
    day = (request.query.get("day") or "").strip() or _biz_day()
    saved = {r.get("driver"): r for r in await db.get_driver_days(day)}
    rows = [_day_row(d, saved.get(d["name"])) for d in staff.drivers()]
    return web.json_response({
        "day": day,
        "today": _biz_day(),
        "rates": {"working": staff.MEAL_WORKING, "off": staff.MEAL_OFF},
        "drivers": rows,
        "totals": _totals(rows),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_working(request):
    """POST /api/owner/expenses/working — отметить выход. body: {day?, driver, working}

    working: true — вышел, false — не вышел, null — снять отметку (ошиблись)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    driver = str(body.get("driver") or "").strip()
    if driver not in {d["name"] for d in staff.drivers()}:
        return web.json_response({"error": "unknown_driver"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()
    w = body.get("working")
    working = None if w is None else bool(w)

    await db.save_driver_day(day, driver, {
        "working": working,
        "marked_by": request["owner_id"],
        "marked_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"[expenses] {day} {driver}: работает={working}")
    saved = await db.get_driver_day(day, driver)
    base = next(d for d in staff.drivers() if d["name"] == driver)
    return web.json_response({"ok": True, "driver": _day_row(base, saved)},
                             headers=CORS_HEADERS)


@require_owner
async def handle_extra_add(request):
    """POST /api/owner/expenses/extra — разовый расход.
    body: {day?, driver, amount, comment}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    driver = str(body.get("driver") or "").strip()
    if driver not in {d["name"] for d in staff.drivers()}:
        return web.json_response({"error": "unknown_driver"}, status=400, headers=CORS_HEADERS)
    amount = _amount(body.get("amount"))
    comment = str(body.get("comment") or "").strip()[:200]
    # Без суммы записывать нечего, без комментария — незачем: через неделю такой
    # расход не отличить от опечатки.
    if amount <= 0 or not comment:
        return web.json_response({"error": "amount_and_comment_required"},
                                 status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()

    item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
            "by": request["owner_id"], "status": "approved",
            "at": datetime.now(timezone.utc).isoformat()}
    await db.add_driver_expense(day, driver, item)
    log.info(f"[expenses] {day} {driver}: +{amount} AED — {comment}")
    saved = await db.get_driver_day(day, driver)
    base = next(d for d in staff.drivers() if d["name"] == driver)
    return web.json_response({"ok": True, "item": item, "driver": _day_row(base, saved)},
                             headers=CORS_HEADERS)


@require_owner
async def handle_extra_del(request):
    """DELETE /api/owner/expenses/extra/{item_id}?day=&driver="""
    item_id = (request.match_info.get("item_id") or "").strip()
    day = (request.query.get("day") or "").strip() or _biz_day()
    driver = (request.query.get("driver") or "").strip()
    ok = await db.del_driver_expense(day, driver, item_id)
    if not ok:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    saved = await db.get_driver_day(day, driver)
    base = next((d for d in staff.drivers() if d["name"] == driver), None)
    return web.json_response(
        {"ok": True, "driver": _day_row(base, saved) if base else None},
        headers=CORS_HEADERS)


@require_owner
async def handle_extra_decide(request):
    """POST /api/owner/expenses/extra/{item_id}/{action} — принять или отклонить.

    Отклонённая трата не удаляется: водитель должен видеть, что его просьбу
    рассмотрели и отказали, иначе он пришлёт её ещё раз."""
    item_id = (request.match_info.get("item_id") or "").strip()
    action = (request.match_info.get("action") or "").strip()
    if action not in ("approve", "reject"):
        return web.json_response({"error": "bad_action"}, status=400, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    day = str(body.get("day") or "").strip() or _biz_day()
    driver = str(body.get("driver") or "").strip()

    ok = await db.set_driver_expense_status(
        day, driver, item_id,
        "approved" if action == "approve" else "rejected",
        request["owner_id"])
    if not ok:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    log.info(f"[expenses] {day} {driver}: расход {item_id} — {action}")

    saved = await db.get_driver_day(day, driver)
    base = next((d for d in staff.drivers() if d["name"] == driver), None)
    # Водителю — короткий ответ в его бот: он ждёт решения, а не молчания.
    try:
        item = next((e for e in (saved or {}).get("extras", []) if e.get("id") == item_id), None)
        tid = staff.DRIVER_IDS.get(driver)
        if item and tid:
            from api_server import tg_send
            import os as _os
            verdict = "принят" if action == "approve" else "отклонён"
            await tg_send(_os.getenv("DRIVER_BOT_TOKEN", ""), tid,
                          f"Расход {item.get('amount')} AED ({item.get('comment','')}) — {verdict}.")
    except Exception as e:
        log.warning(f"[expenses] ответ водителю: {e}")

    return web.json_response(
        {"ok": True, "driver": _day_row(base, saved) if base else None},
        headers=CORS_HEADERS)


@require_owner
async def handle_period(request):
    """GET /api/owner/expenses/period?days=30 — сводка по дням и по людям.

    Один день отвечает «сколько потратили сегодня». Ряд дней отвечает на вопрос
    поинтереснее: у кого разовых трат больше и растут ли они."""
    try:
        days = max(1, min(180, int(request.query.get("days", "30"))))
    except ValueError:
        days = 30
    today = _biz_day()
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = await db.get_driver_days_range(start, today)

    by_day, by_driver = {}, {}
    for r in rows:
        working = r.get("working")
        meal = staff.MEAL_WORKING if working is True else (staff.MEAL_OFF if working is False else 0)
        extra = sum(_amount(e.get("amount")) for e in (r.get("extras") or [])
                    if _status(e) == "approved")
        d = by_day.setdefault(r.get("day", ""), {"day": r.get("day", ""), "meal": 0, "extra": 0, "working": 0})
        d["meal"] += meal; d["extra"] += extra; d["working"] += 1 if working is True else 0
        p = by_driver.setdefault(r.get("driver", ""), {"driver": r.get("driver", ""),
                                                       "meal": 0, "extra": 0, "shifts": 0, "items": 0})
        p["meal"] += meal; p["extra"] += extra
        p["shifts"] += 1 if working is True else 0
        p["items"] += len(r.get("extras") or [])

    days_list = sorted(by_day.values(), key=lambda x: x["day"], reverse=True)
    people = sorted(by_driver.values(), key=lambda x: -(x["meal"] + x["extra"]))
    return web.json_response({
        "from": start, "to": today, "days": days,
        "by_day": days_list, "by_driver": people,
        "total_meal": sum(d["meal"] for d in days_list),
        "total_extra": sum(d["extra"] for d in days_list),
        "total": sum(d["meal"] + d["extra"] for d in days_list),
    }, headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/expenses",            handle_day,       "GET"),
        ("/api/owner/expenses/period",     handle_period,    "GET"),
        ("/api/owner/expenses/working",    handle_working,   "POST"),
        ("/api/owner/expenses/extra",      handle_extra_add, "POST"),
        ("/api/owner/expenses/extra/{item_id}", handle_extra_del, "DELETE"),
        ("/api/owner/expenses/extra/{item_id}/{action}", handle_extra_decide, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, handler)
    log.info("[expenses] routes mounted")
