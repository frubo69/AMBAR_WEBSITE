"""Водитель просит закрыть смену раньше оператора (18 сен 2026).

Владелец: «водитель должен мочь запрашивать закрытие смены до того, как
оператор закрыл смену, а разрешение должно приходить именно оператору этого
водителя в приложение». И правила к этому:
  • просить можно, только когда все заказы доставлены;
  • отпуская, оператор решает, оставить питание за день 80 или сделать 40;
  • молчит оператор десять минут — запрос уходит старшему (Парвизу);
  • после отказа просить снова можно не раньше чем через пятнадцать минут.

Как это ложится на смену. Третий шаг закрытия у водителя — «оператор закрыл
смену района». Теперь его проходят двумя путями: район закрыт целиком ИЛИ
оператор отпустил этого водителя. Остальное не меняется: расходы, приёмка,
перемещения и итоги с наличными на руках водитель проходит как всегда.

Решает оператор района водителя — тот, кто сидит в панели за этот район, — и
старший, он видит все районы. Кто нажал первым, тот и решил: условие «запрос
ещё открыт» стоит в самом обновлении базы, второе нажатие получит «уже решил».

Запрос лежит в записи дня водителя (driver_days.close_req, прошлые за день —
в close_hist). Отпущенного водителя панель не даёт выбрать для нового заказа,
и сервер такое назначение не примет.
"""
import html
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import db
import config_staff as staff

log = logging.getLogger("close_req")

REASONS = {"shift_end": "Моя смена закончилась", "sick": "Плохо себя чувствую",
           "car": "Проблема с машиной", "other": "Другое"}
NO_REASONS = {"queue": "Есть заказы в очереди", "partner": "Дождись напарника",
              "hour": "Через час", "other": "Другое"}
COOLDOWN = timedelta(minutes=15)        # после отказа — не чаще
ESCALATE = timedelta(minutes=10)        # молчит оператор района — старшему


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def _iso(v) -> str:
    v = _aware(v)
    return v.isoformat() if v else ""


def _mins(v, now=None) -> int:
    v = _aware(v)
    return max(0, int(((now or _now()) - v).total_seconds() // 60)) if v else 0


# Записи дня с запросами — на пару секунд: панель спрашивает очередь часто, и
# за один её ответ они нужны дважды. Любая запись (просьба, отзыв, решение)
# кэш сбрасывает — иначе решение доезжало бы до экрана с опозданием.
_ROWS = {"day": "", "at": 0.0, "rows": []}


def _drop_cache():
    _ROWS["at"] = 0.0


async def day_rows(day: str) -> list:
    import time
    if _ROWS["day"] == day and time.monotonic() - _ROWS["at"] < 2.0:
        return _ROWS["rows"]
    rows = await db.close_reqs_of_day(day)
    _ROWS.update(day=day, at=time.monotonic(), rows=rows)
    return rows


def driver_card(name: str) -> dict:
    """Район и оператор водителя — по сегодняшней расстановке, а не по записи
    запроса: водителя могли переставить утром."""
    return next((d for d in staff.drivers() if d["name"] == name), {}) or {}


def reason_text(r: dict) -> str:
    t = REASONS.get(r.get("reason") or "", "")
    if r.get("reason") == "other" and r.get("text"):
        return r["text"]
    return t + (f" · {r['text']}" if r.get("text") else "")


# ── что видит водитель ──────────────────────────────────────────────────────
def view(doc: dict | None, now: datetime | None = None) -> dict | None:
    """Текущий запрос водителя и когда можно просить снова. None — не просил."""
    now = now or _now()
    r = (doc or {}).get("close_req")
    if not r:
        return None
    out = {"id": r.get("id") or "", "status": r.get("status") or "",
           "at": _iso(r.get("at")), "reason": r.get("reason") or "",
           "reason_t": reason_text(r), "to": r.get("to") or "",
           "by": r.get("by") or "", "decided_at": _iso(r.get("decided_at")),
           "meal": r.get("meal"), "note": r.get("note") or "",
           "no_reason_t": NO_REASONS.get(r.get("no_reason") or "", ""),
           "escalated": bool(r.get("escalated_at")), "next_at": ""}
    if r.get("status") == "no":
        nxt = (_aware(r.get("decided_at")) or now) + COOLDOWN
        if nxt > now:
            out["next_at"] = nxt.isoformat()
    return out


async def ask(day: str, me: dict, doc: dict | None, reason: str, text: str,
              in_route: list, district_closed: bool) -> tuple:
    """Водитель просит. (код, ответ)."""
    now = _now()
    text = str(text or "").strip()[:200]
    if reason not in REASONS:
        return 400, {"error": "bad_reason"}
    if reason == "other" and not text:
        return 400, {"error": "text_required"}
    doc = doc or {}
    if not doc.get("shift_open_at"):
        return 409, {"error": "not_open"}
    if doc.get("shift_close_at"):
        return 409, {"error": "already_closed"}
    if district_closed:
        return 409, {"error": "day_closed"}         # смену района уже закрыли — просить нечего
    if in_route:
        return 409, {"error": "orders_in_route", "ids": in_route}
    cur = doc.get("close_req") or {}
    st = cur.get("status")
    if st == "open":
        return 409, {"error": "exists"}
    if st == "ok":
        return 409, {"error": "released"}
    if st == "no":
        nxt = (_aware(cur.get("decided_at")) or now) + COOLDOWN
        if nxt > now:
            return 409, {"error": "too_soon", "next_at": nxt.isoformat()}
    card = driver_card(me["name"])
    req = {"id": secrets.token_hex(5), "at": now, "status": "open",
           "reason": reason, "text": text,
           "district": card.get("district") or me.get("district") or "",
           "to": card.get("operator") or me.get("operator") or ""}
    ok_ = await db.close_req_put(day, me["name"], req, prev=cur or None)
    _drop_cache()
    if not ok_:
        return 409, {"error": "exists"}
    log.info(f"[close] {me['name']} просит закрыть смену · {req['district']} · {reason_text(req)}")
    return 200, {"ok": True, "close_req": view({"close_req": req}, now)}


async def withdraw(day: str, name: str, doc: dict | None) -> tuple:
    cur = (doc or {}).get("close_req") or {}
    if cur.get("status") != "open":
        return 409, {"error": "not_open"}
    got = await db.close_req_set(day, name, cur["id"], {"status": "withdrawn", "decided_at": _now()})
    _drop_cache()
    if not got:
        return 409, {"error": "gone"}
    log.info(f"[close] {name} отозвал запрос")
    return 200, {"ok": True}


async def undo(day: str, driver: str, by: str, scope: set) -> tuple:
    """Вернуть отпущенного в смену. (код, ответ).

    Владелец, 24 сен 2026: «водитель уезжает 24-го, но он же ещё сейчас
    работает, смена не закончилась, а оператор его не может выбрать, чтобы
    заказ ему выдать». Отпустили — и новых заказов ему не назначают; передумал
    (билет позже, машина ещё у него) — вернуть было нечем, кроме базы.

    Возвращаем всё как было до решения: запрос снова открыт, питание — рабочее
    (отпуск раньше времени его резал), и водителю говорим об этом."""
    card = driver_card(driver)
    if not card or card.get("district") not in scope:
        return 403, {"error": "not_yours"}
    doc = await db.get_driver_day(day, driver) or {}
    cur = (doc or {}).get("close_req") or {}
    if cur.get("status") != "ok":
        return 409, {"error": "not_released", "status": cur.get("status") or ""}
    if doc.get("shift_close_at"):
        return 409, {"error": "already_closed"}
    # only_open=False: мы как раз и меняем УЖЕ решённый запрос — иначе условие
    # «менять только открытые» не пустит нас к отпущенному.
    got = await db.close_req_set(day, driver, cur.get("id") or "",
                                 {"status": "open", "by": "", "decided_at": None,
                                  "meal": None, "note": cur.get("note") or "",
                                  "undone_by": by, "undone_at": _now()},
                                 {"meal_rate": staff.MEAL_WORKING}, only_open=False)
    _drop_cache()
    if not got:
        return 409, {"error": "gone"}
    log.info(f"[close] {by}: {driver} возвращён в смену")
    try:
        from operator_routes import tell_driver
        await tell_driver(driver, f"{html.escape(by)} вернул вас в смену — "
                                  "заказы снова могут прийти. Питание за сегодня прежнее.")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[close] сообщение водителю {driver}: {e}")
    return 200, {"ok": True, "status": "open"}


def released_names(rows: list) -> set:
    """Кого сегодня отпустили — им новых заказов не назначают."""
    return {d.get("driver") for d in rows if (d.get("close_req") or {}).get("status") == "ok"}


# ── что видит оператор ──────────────────────────────────────────────────────
async def open_for(day: str, scope: set, *, rows: list | None = None,
                   closed: dict | None = None,
                   route_by_driver: dict | None = None,
                   waiting_by_district: dict | None = None) -> list:
    """Открытые запросы водителей районов оператора — с тем, что нужно, чтобы
    решить за секунду: сколько у водителя в пути, кто ещё на смене в районе,
    сколько заказов ждут водителя, сколько длится его смена."""
    now = _now()
    rows = rows if rows is not None else await day_rows(day)
    open_ = [d for d in rows if (d.get("close_req") or {}).get("status") == "open"
             and not d.get("shift_close_at")]
    if not open_:
        return []
    closed = closed if closed is not None else await db.shifts_for_day(day)
    days = await db.get_driver_days(day)
    rel = released_names(rows)
    cards = {d["name"]: d for d in staff.drivers()}
    out = []
    for doc in open_:
        r = doc["close_req"]
        name = doc.get("driver") or ""
        card = cards.get(name) or {}
        dist = card.get("district") or r.get("district") or ""
        if dist not in scope or closed.get(dist):
            continue
        mates = [x.get("driver") for x in days
                 if x.get("driver") != name and x.get("shift_open_at") and not x.get("shift_close_at")
                 and x.get("driver") not in rel
                 and (cards.get(x.get("driver")) or {}).get("district") == dist]
        out.append({
            "driver": name, "id": r.get("id") or "", "district": dist,
            "code": card.get("district_code") or "", "district_name": card.get("district_name") or "",
            "at": _iso(r.get("at")), "mins": _mins(r.get("at"), now),
            "reason": r.get("reason") or "", "reason_t": reason_text(r),
            "to": r.get("to") or "", "escalated": bool(r.get("escalated_at")),
            "in_route": int((route_by_driver or {}).get(name, 0)),
            "mates": sorted(m for m in mates if m),
            "waiting": int((waiting_by_district or {}).get(dist, 0)),
            "shift_min": _mins(doc.get("shift_open_at"), now),
        })
    out.sort(key=lambda x: x["at"])
    return out


async def decide(day: str, driver: str, rid: str, ok: bool, meal, no_reason: str,
                 note: str, by: str, scope: set) -> tuple:
    """Решение оператора. (код, ответ)."""
    card = driver_card(driver)
    if not card or card.get("district") not in scope:
        return 403, {"error": "not_yours"}
    note = str(note or "").strip()[:200]
    now = _now()
    if ok:
        try:
            meal = int(meal)
        except (TypeError, ValueError):
            meal = 0
        if meal not in (staff.MEAL_WORKING, staff.MEAL_OFF):
            return 400, {"error": "meal_required"}
        fields, extra = {"status": "ok", "by": by, "decided_at": now, "meal": meal, "note": note}, \
                        {"meal_rate": meal}
    else:
        if no_reason not in NO_REASONS:
            return 400, {"error": "reason_required"}
        if no_reason == "other" and not note:
            return 400, {"error": "note_required"}
        fields, extra = {"status": "no", "by": by, "decided_at": now,
                         "no_reason": no_reason, "note": note}, None
    got = await db.close_req_set(day, driver, rid, fields, extra)
    _drop_cache()
    if not got:
        cur = ((await db.get_driver_day(day, driver)) or {}).get("close_req") or {}
        return 409, {"error": "decided", "by": cur.get("by") or "", "status": cur.get("status") or ""}
    log.info(f"[close] {by}: {driver} — {'отпустил, питание ' + str(meal) if ok else 'не сейчас · ' + NO_REASONS[no_reason]}")
    if ok:
        text = (f"{html.escape(by)} отпустил вас. Питание за сегодня — {meal} AED.\n\n"
                "Закройте смену в приложении: итоги и наличные на руках.")
    else:
        why = NO_REASONS[no_reason] if no_reason != "other" else note
        extra_note = f" — {note}" if note and no_reason != "other" else ""
        text = (f"{html.escape(by)} пока не отпускает: {html.escape(why.lower() if why else '')}"
                f"{html.escape(extra_note)}.\n\nПопросить снова можно через 15 минут.")
    try:
        from operator_routes import tell_driver
        await tell_driver(driver, text)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[close] сообщение водителю {driver}: {e}")
    return 200, {"ok": True, "status": fields["status"]}


# ── молчит оператор — старшему ─────────────────────────────────────────────
async def escalate_tick(now: datetime | None = None) -> int:
    """Запросы, на которые десять минут никто не ответил, — старшему в бот.
    Отметку ставим до отправки и тем же условием «ещё открыт»: две службы или
    два прохода подряд одно и то же дважды не пришлют."""
    import bizday
    now = now or _now()
    day = bizday.biz_day()
    token = os.getenv("OPERATOR_BOT_TOKEN", "")
    chats = list(staff.SENIOR_IDS)
    if not token or not chats:
        return 0
    closed = await db.shifts_for_day(day)
    sent = 0
    for doc in await db.close_reqs_of_day(day):
        r = doc.get("close_req") or {}
        if r.get("status") != "open" or r.get("escalated_at") or doc.get("shift_close_at"):
            continue
        at = _aware(r.get("at"))
        if not at or now - at < ESCALATE:
            continue
        name = doc.get("driver") or ""
        card = driver_card(name)
        if closed.get(card.get("district") or r.get("district") or ""):
            continue
        marked = await db.close_req_set(day, name, r["id"], {"escalated_at": now})
        _drop_cache()
        if not marked:
            continue
        text = (f"⏱ {name} ({card.get('district_code') or ''}) просит закрыть смену уже "
                f"{_mins(at, now)} мин — оператор района{(' (' + r['to'] + ')') if r.get('to') else ''} "
                f"не ответил.\nПричина: {reason_text(r)}.\n\nРешить можно в панели, в «Событиях».")
        try:
            from api_server import tg_send
            for cid in chats:
                await tg_send(token, cid, text)
            sent += 1
            log.info(f"[close] запрос {name} ушёл старшему")
        except Exception as e:                               # noqa: BLE001
            log.warning(f"[close] старшему не ушло: {e}")
    return sent


async def loop(app):
    """Раз в минуту — не молчит ли кто-то слишком долго."""
    import asyncio
    await asyncio.sleep(30)
    while True:
        try:
            await escalate_tick()
        except Exception as e:                               # noqa: BLE001
            log.error(f"[close] {e}")
        await asyncio.sleep(60)
