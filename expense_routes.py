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
import backdate
import photos
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


# ── за что именно ──────────────────────────────────────────────────────────
# «Доп. расход» одной строкой не отвечал ни на один вопрос, который задают
# через неделю: заправка это или чей-то долг, вернули мы или нам. Поэтому у
# траты есть вид, и каждый вид знает про себя две вещи.
#
# Чек. Заправку, мойку и парковку без снимка чека записать нельзя: это деньги,
# отданные на стороне, и единственное их доказательство — бумажка в руках.
# Остальные виды доказываются самим фактом: премию назначает владелец, охрану
# видно на въезде.
#
# Знак. «Нам вернули» — деньги, пришедшие обратно, и в расходе дня они стоят
# минусом. Иначе возврат увеличивал бы расход ровно так же, как трата.
EXTRA_KINDS = {
    "fuel":    {"t": "Заправка",     "receipt": True},
    "wash":    {"t": "Мойка",        "receipt": True},
    "parking": {"t": "Парковка",     "receipt": True},
    "kfc":     {"t": "KFC · премия"},
    "guard":   {"t": "Охрана"},
    "other":   {"t": "Доп. расход"},
    # Порядок в конце списка — как просили: мы вернули, нам должны, и два со
    # знаком плюс — нам вернули и мы должны: это деньги, пришедшие к нам.
    "we_gave": {"t": "Мы вернули"},
    "owed_us": {"t": "Нам должны"},
    "we_got":  {"t": "Нам вернули",  "plus": True},
    "we_owe":  {"t": "Мы должны",    "plus": True},
}


def _kind(e: dict) -> dict:
    return EXTRA_KINDS.get(str((e or {}).get("kind") or "other")) or EXTRA_KINDS["other"]


def _signed(e: dict) -> int:
    """Сумма траты со знаком: возврат уменьшает расход дня, а не увеличивает."""
    a = _amount((e or {}).get("amount"))
    return -a if _kind(e).get("plus") else a


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
    extra_sum = sum(_signed(e) for e in extras if _status(e) == "approved")
    pending = sum(_signed(e) for e in extras if _status(e) == "pending")
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


async def _held(day_from: str, day_to: str = "") -> list:
    """Удержания за период: с кого, сколько и за что.

    Удержание — это не расход компании, а его противоположность: деньги
    вернутся из зарплаты. Поэтому в сумму расходов оно не идёт и стоит
    отдельной строкой — иначе один и тот же бой уменьшал бы расход, которого
    он не уменьшал, а просто перекладывал с компании на человека.

    Днём считаем день самого списания, а не день решения: разбили в среду,
    решили в пятницу — это среда, и в отчёте за среду оно и должно стоять."""
    rows = await db.writeoff_list(limit=1000)
    out = {}
    for r in rows:
        c = r.get("comp") or {}
        amount = int(c.get("amount") or 0)
        if not amount or (r.get("state") or "ok") != "ok":
            continue
        d = str(r.get("day") or "")
        if d < day_from or (day_to and d > day_to):
            continue
        who = c.get("who") or ""
        h = out.setdefault(who, {"who": who, "amount": 0, "n": 0, "items": []})
        h["amount"] += amount
        h["n"] += 1
        if len(h["items"]) < 12:
            h["items"].append({
                "id": r.get("_id"), "day": d, "amount": amount,
                "name": r.get("name", "") or r.get("item", ""),
                "qty": int(r.get("qty") or 0), "kind": r.get("kind", ""),
                "note": c.get("note", ""), "by": r.get("by", "")})
    return sorted(out.values(), key=lambda x: -x["amount"])


@require_owner
async def handle_day(request):
    """GET /api/owner/expenses?day= — расходы за день по каждому водителю."""
    day = (request.query.get("day") or "").strip() or _biz_day()
    saved = {r.get("driver"): r for r in await db.get_driver_days(day)}
    rows = [_day_row(d, saved.get(d["name"])) for d in staff.drivers()]
    held = await _held(day, day)
    return web.json_response({
        "day": day,
        "today": _biz_day(),
        "rates": {"working": staff.MEAL_WORKING, "off": staff.MEAL_OFF},
        "drivers": rows,
        "totals": _totals(rows),
        "held": held,
        "held_total": sum(h["amount"] for h in held),
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
    await backdate.notify(day, str(body.get("as") or ""), "отметка водителя",
                          f"{driver} — " + ("вышел" if working is True
                                            else "дома" if working is False else "отметка снята"))
    saved = await db.get_driver_day(day, driver)
    base = next(d for d in staff.drivers() if d["name"] == driver)
    return web.json_response({"ok": True, "driver": _day_row(base, saved)},
                             headers=CORS_HEADERS)


@require_owner
async def handle_extra_add(request):
    """POST /api/owner/expenses/extra — разовая трата.
    body: {day?, driver, amount, comment, kind?, photo?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    driver = str(body.get("driver") or "").strip()
    if driver not in {d["name"] for d in staff.drivers()}:
        return web.json_response({"error": "unknown_driver"}, status=400, headers=CORS_HEADERS)
    amount = _amount(body.get("amount"))
    comment = str(body.get("comment") or "").strip()[:200]
    day = str(body.get("day") or "").strip() or _biz_day()

    kid = str(body.get("kind") or "other").strip()
    if kid not in EXTRA_KINDS:
        kid = "other"
    вид = EXTRA_KINDS[kid]
    # Охрана — единственный расход, где платят не деньгами, а бутылкой. Сумму
    # тут не спрашиваем ни у кого: код с крышки знает, что это за бутылка и
    # сколько за неё отдали при закупке — тот же путь, что у водителя.
    бутылка = None
    if kid == "guard":
        from driver_routes import _guard_bottle
        бутылка, беда = await _guard_bottle(body.get("code"))
        if беда:
            return web.json_response({"error": беда}, status=400, headers=CORS_HEADERS)
        amount = бутылка["cost"]
    # Без суммы записывать нечего, без комментария — незачем: через неделю такой
    # расход не отличить от опечатки.
    if (amount <= 0 and kid != "guard") or not comment:
        return web.json_response({"error": "amount_and_comment_required"},
                                 status=400, headers=CORS_HEADERS)
    # Чек — не формальность: заправку, мойку и парковку оплачивают на стороне,
    # и снимок бумажки — единственное, чем такая трата подтверждается. Без него
    # запись не принимаем вовсе, а не «принимаем и помечаем».
    # Чек — не формальность, но и не повод потерять запись: сначала разбираем
    # кадр, и если он битый или тяжёлый — говорим об этом отдельно от «нет чека
    # вовсе». Две разные беды с одним словом лечат по-разному.
    photo, беда = photos.decode(body.get("photo"))
    if беда:
        return web.json_response({"error": беда, "kind": kid},
                                 status=400, headers=CORS_HEADERS)
    if вид.get("receipt") and not photo:
        return web.json_response({"error": "no_photo", "kind": kid},
                                 status=400, headers=CORS_HEADERS)

    item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
            "kind": kid, "kind_t": вид["t"], "plus": bool(вид.get("plus")),
            "by": request["owner_id"], "status": "approved",
            "at": datetime.now(timezone.utc).isoformat()}
    if бутылка:
        item.update(бутылка["item"])
    if photo:
        # Сам снимок лежит отдельно: строку расхода читают часто, а картинку
        # смотрят раз, и таскать по сети мегабайт ради строки незачем.
        thumb = photos.thumb(body.get("thumb"))
        try:
            await db.expense_photo_set(item["id"], photo, thumb)
            item["photo"] = True
            item["thumb"] = thumb
        except Exception as e:                   # noqa: BLE001
            log.warning(f"[expenses] снимок чека не сохранён: {e}")
    await db.add_driver_expense(day, driver, item)
    # Записал старший — расход уже согласован, и бутылка уходит со склада
    # сразу, тем же списанием, что и при решении по заявке водителя.
    if бутылка:
        await _guard_bottle_gone(item, day, driver, request["owner_id"])
    знак = "−" if вид.get("plus") else "+"
    log.info(f"[expenses] {day} {driver}: {знак}{amount} AED — {вид['t']}: {comment}")
    await backdate.notify(day, str(body.get("as") or ""), "доп. расход добавлен",
                          f"{driver} — {amount} AED, {вид['t']}: {comment}")
    saved = await db.get_driver_day(day, driver)
    base = next(d for d in staff.drivers() if d["name"] == driver)
    return web.json_response({"ok": True, "item": item, "driver": _day_row(base, saved)},
                             headers=CORS_HEADERS)


# ── финансы: долги и удержания по людям ──────────────────────────────────
# Кто кому должен — считается, а не ведётся: из согласованных записей
# «нам должны / нам вернули / мы должны / мы вернули» по водителям и из
# удержаний по списаниям, где виновным может быть и оператор. Роль человека
# — из штатного расписания.
@require_owner
async def handle_debts(request):
    people: dict = {}

    def P(name: str) -> dict:
        return people.setdefault(name, {"name": name, "they": 0, "we": 0, "items": []})

    try:
        days = await db.get_driver_days_range("2000-01-01", "2999-12-31")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[finance] дни водителей не прочитались: {e}")
        days = []
    for d in days:
        drv = str(d.get("driver") or "").strip()
        if not drv:
            continue
        for e in d.get("extras") or []:
            if _status(e) != "approved":
                continue
            k = str(e.get("kind") or "")
            a = _amount(e.get("amount"))
            if not a or k not in ("owed_us", "we_got", "we_owe", "we_gave"):
                continue
            row = P(drv)
            if k == "owed_us":   row["they"] += a
            elif k == "we_got":  row["they"] -= a
            elif k == "we_owe":  row["we"] += a
            else:                row["we"] -= a
            row["items"].append({"day": d.get("day") or "", "kind": k,
                                 "t": EXTRA_KINDS[k]["t"], "amount": a,
                                 "comment": str(e.get("comment") or "")[:120]})
    try:
        wos = await db.writeoff_list(state="ok", limit=3000)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[finance] списания не прочитались: {e}")
        wos = []
    for w in wos:
        c = w.get("comp") or {}
        who = str(c.get("who") or "").strip()
        a = _amount(c.get("amount"))
        if not who or not a:
            continue
        row = P(who)
        row["they"] += a
        row["items"].append({"day": w.get("day") or "", "kind": "comp", "t": "Удержание",
                             "amount": a,
                             "comment": f"{w.get('name') or ''} × {w.get('qty') or 1}"
                                        + (f" · {c.get('note')}" if c.get("note") else "")})

    seniors = {x["name"] for x in (staff.SENIOR_OPERATORS or [])}
    operators = {(d.get("operator") or "").strip() for d in staff.DISTRICT_STAFF} - seniors
    drivers = set(staff.driver_names())
    def role(n: str) -> str:
        if n in seniors: return "senior"
        if n in operators: return "operator"
        if n in drivers: return "driver"
        return "other"
    rows = []
    for r in people.values():
        r["items"].sort(key=lambda x: x["day"], reverse=True)
        r["net"] = r["they"] - r["we"]
        r["role"] = role(r["name"])
        if r["they"] or r["we"]:
            rows.append(r)
    rows.sort(key=lambda r: (-abs(r["net"]), r["name"]))
    return web.json_response({
        "people": rows,
        "they": sum(max(0, r["net"]) for r in rows),
        "we": sum(max(0, -r["net"]) for r in rows),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_bottle_look(request):
    """GET /api/owner/bottle?code=… — что за бутылка под кодом и почём.

    Тот же ответ, что получает водитель у камеры: узнать «эта уже продана»
    надо, пока бутылка в руке, а не после записи расхода."""
    from driver_routes import _guard_bottle, GUARD_SAY
    б, беда = await _guard_bottle(request.query.get("code"))
    if беда:
        return web.json_response({"ok": False, "verdict": беда,
                                  "say": GUARD_SAY.get(беда, "не наша бутылка")},
                                 headers=CORS_HEADERS)
    return web.json_response({"ok": True, "cost": б["cost"], **б["item"]},
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
    await db.expense_photo_del(item_id)
    await backdate.notify(day, (request.query.get("as") or ""),
                          "доп. расход убран", driver)
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
    await backdate.notify(day, str(body.get("as") or ""),
                          "решение по расходу водителя",
                          f"{driver} — " + ("принят" if action == "approve" else "отклонён"))

    saved = await db.get_driver_day(day, driver)
    # Бутылка охране уходит со склада здесь, а не в момент, когда её отдали:
    # до решения владельца это заявление водителя, а не факт — то же правило,
    # что у списаний. Пишем в две книги сразу: реестр кодов помечает бутылку
    # ушедшей, журнал списаний вычитает её там, где счёт идёт от пересчёта.
    # Одна книга без другой ломает остаток.
    if action == "approve":
        item = next((e for e in (saved or {}).get("extras", [])
                     if e.get("id") == item_id), None)
        await _guard_bottle_gone(item, day, driver, request["owner_id"])

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
    held = await _held(start, today)
    return web.json_response({
        "from": start, "to": today, "days": days,
        "by_day": days_list, "by_driver": people,
        "held": held,
        "held_total": sum(h["amount"] for h in held),
        "total_meal": sum(d["meal"] for d in days_list),
        "total_extra": sum(d["extra"] for d in days_list),
        "total": sum(d["meal"] + d["extra"] for d in days_list),
    }, headers=CORS_HEADERS)


async def _guard_bottle_gone(item: dict | None, day: str, driver: str, by: int) -> None:
    """Списать бутылку, отданную охране. Молча ничего не делаем для всего
    остального: код есть только у этого вида расхода."""
    code = str((item or {}).get("code") or "").strip()
    if not code:
        return
    now = datetime.now(timezone.utc)
    wid = await db.writeoff_add({
        # Позиция обязательна: журнал вычитает со склада по ней, и запись без
        # неё честно лежит в истории, ничего при этом не вычитая.
        "at": now, "day": day, "item": item.get("bottle_id") or "", "code": code,
        "name": item.get("bottle") or "", "qty": 1, "kind": "охрана",
        "note": (item.get("comment") or "")[:200],
        "district": item.get("district") or "",
        "by": driver, "by_id": 0, "label": item.get("label") or "",
        "state": "ok", "decided_at": now, "decided_by": by,
        "expense": item.get("id"),
    })
    if not await db.qr_write_off(code, wid):
        # Между заявкой и решением бутылку успели списать или продать. Запись
        # оставлять нельзя: она вычтет со склада вторую такую же.
        await db.writeoff_del(wid)
        log.warning(f"[expenses] бутылка {code} уже ушла — списание не записано")
        return
    log.info(f"[expenses] охране отдана {item.get('bottle','')} · {code} · {day}")


@require_owner
async def handle_extra_photo(request):
    """GET /api/owner/expenses/photo/{item_id} — сам чек.

    Без этого маршрута снимок был письмом в никуда: его требовали при записи,
    сохраняли — и никто уже не мог на него взглянуть, а расход утверждали
    вслепую."""
    img = await db.expense_photo((request.match_info.get("item_id") or "").strip())
    if not img:
        return web.json_response({"error": "no_photo"}, status=404, headers=CORS_HEADERS)
    return web.Response(body=img, content_type="image/jpeg",
                        headers={**CORS_HEADERS, "Cache-Control": "private, max-age=86400"})


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/expenses",            handle_day,       "GET"),
        ("/api/owner/expenses/period",     handle_period,    "GET"),
        ("/api/owner/expenses/working",    handle_working,   "POST"),
        ("/api/owner/expenses/extra",      handle_extra_add, "POST"),
        ("/api/owner/bottle",              handle_bottle_look, "GET"),
        ("/api/owner/finance/debts",       handle_debts,     "GET"),
        ("/api/owner/expenses/photo/{item_id}", handle_extra_photo, "GET"),
        ("/api/owner/expenses/extra/{item_id}", handle_extra_del, "DELETE"),
        ("/api/owner/expenses/extra/{item_id}/{action}", handle_extra_decide, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, handler)
    log.info("[expenses] routes mounted")
