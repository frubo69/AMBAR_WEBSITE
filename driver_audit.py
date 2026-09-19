"""Ревизия у водителя (владелец, 19 сен 2026: «можем водителям тоже сделать
кнопку ревизия? там, где у них раньше была кнопка „переместить“, во вкладке
„Товар“; но ревизия должна проходить исключительно по их билдингу; по итогам
ревизии отчёт старшему должен приходить и операторам»).

Это та же ревизия, что у старшего в STAR: один документ на район и день и
общий проход камерой (stock_routes.audit_*). Начал водитель — старший видит
её идущей, и наоборот; сканировать могут вдвоём. Отличия:

  • район — только свой: берётся из входа водителя (request["driver"]), а не
    из запроса; день — сегодняшний рабочий, задним числом водитель не считает;
  • денег водитель не видит: ни закупки, ни прайса — только штуки;
  • решения — у старшего: кто платит за недостачу, внести излишек, внести
    коды не из реестра. Водитель завершает проход, и ревизия «ждёт решения»
    ровно так же, как после старшего; вернуть её в работу («Возобновить»)
    может только старший;
  • по завершении отчёт уходит старшему — бот AMBAR STAR, событие
    stock.audit, — и операторам района: бот операторов тем же маршрутом, что
    заказы района (свой оператор, старшие операторы и планшет).
"""
from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web

import db
import stock_routes as SR
from config_offices import OFFICE_IDS, OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Ambar-Test",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}
_TELLS: set = set()          # отчёты в пути: держим ссылку, пока задача не кончится


def _json(data, status: int = 200):
    return web.json_response(data, status=status, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


def _where(request) -> tuple:
    """(район, день) водителя. Район — из его входа: ревизию водитель проводит
    только у себя, и выбрать чужой ему нечем."""
    me = request["driver"]
    district = (me.get("district") or "").strip()
    return (district if district in OFFICE_IDS else ""), SR._biz_day()


def _coded(r: dict) -> float:
    c = r.get("coded")
    return c if c is not None else max(0, (r.get("expected") or 0) - (r.get("noqr") or 0))


def _view(a: dict) -> dict:
    """Состояние ревизии без денег: суммы недостачи и закупка — не водителю."""
    v = SR._audit_view(a)
    short, over = v.get("short") or {}, v.get("over") or {}
    return {
        "state": v["state"], "started_at": v["started_at"], "started_by": v["started_by"],
        "finished_at": v["finished_at"], "finished_by": v["finished_by"],
        "finished_kind": v["finished_kind"], "note": v["note"], "closed_at": v["closed_at"],
        "alien": v["alien"],
        "short": [{"id": l.get("id"), "name": l.get("name", ""), "qty": l.get("qty"),
                   "unit": l.get("unit") or 1} for l in short.get("lines") or []],
        "over": [{"id": l.get("id"), "name": l.get("name", ""), "qty": l.get("qty"),
                  "unit": l.get("unit") or 1,
                  "homes": sorted({c.get("home_code") for c in l.get("codes") or []
                                   if c.get("verdict") == "other" and c.get("home_code")}),
                  "written": l.get("written") or 0, "sold": l.get("sold") or 0}
                 for l in over.get("lines") or []],
        # Решено ли старшим — без того, что именно решено: это разговор старшего
        # с виновным, а не строка на экране у всего района.
        "short_done": bool(short.get("resolved_at")), "over_done": bool(over.get("resolved_at")),
    }


def _sheet(sh: dict, a: dict) -> dict:
    """Лист для водителя: позиции, которые на районе есть или должны быть, —
    сколько с кодами числится (это видит камера), сколько увидела, сколько без
    кодов (их камера не видит, и в недостачу они не идут)."""
    rows = []
    for r in sh.get("rows") or []:
        coded, actual, noqr = _coded(r), r.get("actual") or 0, r.get("noqr") or 0
        if not (coded or actual or noqr):
            continue
        rows.append({"id": r.get("id"), "name": r.get("name", ""), "no": r.get("no"),
                     "unit": r.get("unit") or 1, "coded": coded, "actual": actual, "noqr": noqr})
    t = {"coded": 0.0, "actual": 0.0, "noqr": 0.0, "short": 0.0, "over": 0.0}
    for r in rows:
        t["coded"] += r["coded"]; t["actual"] += r["actual"]; t["noqr"] += r["noqr"]
        d = r["coded"] - r["actual"]
        if d > 0:
            t["short"] += d
        elif d < 0:
            t["over"] += -d
    return {
        "district": sh.get("district"), "district_code": sh.get("district_code", ""),
        "district_name": sh.get("district_name", ""), "day": sh.get("day"),
        "coded_codes": sh.get("coded") or 0,
        "rows": rows, "totals": {k: SR._num(round(v, 2)) for k, v in t.items()},
        "scan": {"total": int((sh.get("scan") or {}).get("total") or 0),
                 "odd": int((sh.get("scan") or {}).get("odd") or 0)},
        "audit": _view(a),
    }


async def _full(district: str, day: str) -> dict:
    return _sheet(await SR.audit_sheet(district, day), await db.audit_get(district, day) or {})


# ── ручки (обёрнуты require_driver и _no_test в driver_routes) ──────────────
async def handle_state(request):
    """GET — лист ревизии своего района: состояние, позиции, счёт прохода."""
    district, day = _where(request)
    if not district:
        return _json({"error": "no_district"}, 409)
    return _json(await _full(district, day))


async def handle_brief(request):
    """GET — только состояние, для кнопки на «Товаре»: лист считается тяжело,
    а кнопка перерисовывается при каждом опросе вкладки."""
    district, day = _where(request)
    if not district:
        return _json({"state": "none"})
    a = await db.audit_get(district, day) or {}
    st = SR._audit_view(a)["state"] if a else "idle"
    n = (await db.audit_scan_stats(district, day)).get("total", 0) if st == "running" else 0
    return _json({"state": st, "district_code": OFFICE_CODES.get(district, ""), "scans": int(n or 0)})


async def _body(request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:                               # noqa: BLE001
        return {}


async def handle_start(request):
    """POST — начать (или продолжить начатую) ревизию своего района."""
    district, day = _where(request)
    if not district:
        return _json({"error": "no_district"}, 409)
    me = request["driver"]
    st, res = await SR.audit_start(district, day, int(request["tg"].get("id") or 0), me["name"])
    # Завершена сегодня (старшим или другим водителем) — отдаём лист с итогом:
    # экран покажет, чем кончилось, а не ошибку.
    return _json({"ok": st == 200, **({} if st == 200 else {"error": res.get("error")}),
                  **(await _full(district, day))})


async def handle_scan(request):
    """POST {code} — бутылка в проход. Вердикт — сервера, как у старшего."""
    district, day = _where(request)
    if not district:
        return _json({"ok": False, "error": "no_district"})
    b = await _body(request)
    code = SR.audit_code(b.get("code"))
    if not code:
        return _json({"ok": False, "error": "empty_code"})
    me = request["driver"]
    a = await db.audit_get(district, day) or {}
    if a.get("finished_at"):
        return _json({"ok": False, "error": "finished"})
    if not a.get("started_at"):               # начали с другого телефона и сбросили — начинаем заново
        await SR.audit_start(district, day, int(request["tg"].get("id") or 0), me["name"])
    r = await SR.audit_scan(district, day, code, int(request["tg"].get("id") or 0), me["name"])
    return _json(r)


async def handle_undo(request):
    """POST {code} — убрать бутылку, записанную зря. Только пока ревизия идёт:
    после завершения пересчёт уже записан."""
    district, day = _where(request)
    if not district:
        return _json({"ok": False, "error": "no_district"})
    b = await _body(request)
    code = SR.audit_code(b.get("code"))
    a = await db.audit_get(district, day) or {}
    if a.get("finished_at"):
        return _json({"ok": False, "error": "finished"})
    return _json(await SR.audit_scan_undo(district, day, code))


async def handle_finish(request):
    """POST {note?} — завершить ревизию своего района. Пересчёт записывается
    так же, как у старшего; отчёт — старшему и операторам района."""
    district, day = _where(request)
    if not district:
        return _json({"ok": False, "error": "no_district"})
    me = request["driver"]
    b = await _body(request)
    note = str(b.get("note") or "").strip()[:300]
    st, rep = await SR.audit_finish(district, day, int(request["tg"].get("id") or 0), me["name"],
                                    {"finished_kind": "driver", "finished_note": note})
    if st != 200:
        return _json({"ok": False, "error": rep.get("error"), **(await _full(district, day))})
    a = await db.audit_get(district, day) or {}
    t = asyncio.create_task(tell(district, day, a, me["name"]))
    _TELLS.add(t)
    t.add_done_callback(_TELLS.discard)
    log.info(f"[audit] {district} {day}: завершил водитель {me['name']}")
    return _json({"ok": True, **(await _full(district, day))})


# ── отчёт ────────────────────────────────────────────────────────────────────
def _q(qty, unit) -> str:
    """Количество словами склада: бутылки, у пива коробки (0,5 — двенадцать банок)."""
    v = SR._num(qty or 0)
    s = str(v).replace(".", ",")
    return f"{s} {'кор' if (unit or 1) > 1 else 'бут'}"


def _mins(a: dict):
    return SR._minutes_between(a.get("started_at"), a.get("finished_at"))


def report_lines(a: dict, driver: str, *, money: bool) -> dict:
    """Части отчёта: заголовок, строки недостачи и излишка, хвост. Одни и те
    же для STAR (с деньгами) и операторов (без них); разметку ставит отправка."""
    district = a.get("district") or ""
    where = f"{OFFICE_CODES.get(district, '')} {OFFICE_NAMES.get(district, district)}".strip()
    res = a.get("result") or {}
    short, over = a.get("short") or {}, a.get("over") or {}
    alien = int(a.get("alien") or 0)
    m = _mins(a)
    sub = f"Провёл {driver} · отсканировано {int(res.get('scan_qty') or 0)}" + (f" · {m} мин" if m else "")
    short_rows = [f"{l.get('name', '')} — {_q(l.get('qty'), l.get('unit'))}" for l in short.get("lines") or []]
    over_rows = []
    for l in over.get("lines") or []:
        homes = sorted({c.get("home_code") for c in l.get("codes") or []
                        if c.get("verdict") == "other" and c.get("home_code")})
        why = []
        if homes:
            why.append("числится на " + ", ".join(homes))
        if l.get("written"):
            why.append("списана" if l["written"] == 1 else f"списано {l['written']}")
        if l.get("sold"):
            why.append("ушла с заказом" if l["sold"] == 1 else f"ушли с заказом {l['sold']}")
        over_rows.append(f"{l.get('name', '')} — {_q(l.get('qty'), l.get('unit'))}"
                         + (f" · {', '.join(why)}" if why else ""))
    # Итог одной цифрой не пишем: у пива коробки, у остального бутылки, и
    # сумма «3» из двух бутылок и коробки соврала бы. Счёт — в строках.
    short_head = "Не хватает" + (f" · {int(short['aed']):,} AED".replace(",", " ")
                                 if money and short.get("aed") else "")
    return {"where": where, "sub": sub, "ok": not short_rows and not over_rows and not alien,
            "short_head": short_head, "short": short_rows,
            "over_head": "Лишние", "over": over_rows,
            "alien": alien, "note": a.get("finished_note") or ""}


def owner_text(a: dict, driver: str) -> str:
    """Старшему — Markdown, как остальные сообщения AMBAR STAR."""
    from owner_routes import _md
    p = report_lines(a, driver, money=True)
    out = [f"📋 *Ревизия водителя — {_md(p['where'])}*", _md(p["sub"])]
    if p["ok"]:
        out += ["", "Всё сошлось — ревизия закрыта."]
    for head, rows in ((p["short_head"], p["short"]), (p["over_head"], p["over"])):
        if rows:
            out += ["", f"*{_md(head)}:*"] + [f"• {_md(r)}" for r in rows[:12]]
            if len(rows) > 12:
                out.append(f"…и ещё {len(rows) - 12}")
    if p["alien"]:
        out += ["", f"Кодов не из реестра: {p['alien']} — внести как новый товар"]
    if p["note"]:
        out += ["", f"_{_md(p['note'])}_"]
    if not p["ok"]:
        out += ["", f"Решение — в STAR: Склад → {_md(p['where'])}."]
    return "\n".join(out)


def operator_text(a: dict, driver: str) -> str:
    """Операторам — HTML, как остальные сообщения бота операторов; без денег."""
    import html
    e = lambda s: html.escape(str(s or ""), quote=False)
    p = report_lines(a, driver, money=False)
    out = [f"📋 <b>Ревизия — {e(p['where'])}</b>", e(p["sub"])]
    if p["ok"]:
        out += ["", "Всё сошлось."]
    for head, rows in ((p["short_head"], p["short"]), (p["over_head"], p["over"])):
        if rows:
            out += ["", f"<b>{e(head)}:</b>"] + [f"• {e(r)}" for r in rows[:12]]
            if len(rows) > 12:
                out.append(f"…и ещё {len(rows) - 12}")
    if p["alien"]:
        out += ["", f"Кодов не из реестра: {p['alien']}"]
    if p["note"]:
        out += ["", f"<i>{e(p['note'])}</i>"]
    if not p["ok"]:
        out += ["", "Недостачу и лишнее разбирает старший."]
    return "\n".join(out)


async def tell(district: str, day: str, a: dict, driver: str) -> None:
    """Отчёт старшему (AMBAR STAR) и операторам района. Сбой одной стороны не
    отменяет другую."""
    try:
        from owner_routes import notify_owners
        await notify_owners("stock.audit", owner_text(a, driver))
    except Exception as e:                          # noqa: BLE001
        log.error(f"[audit] отчёт старшему не ушёл ({district}): {e}")
    try:
        import op_route
        await op_route.send(operator_text(a, driver), district=district, parse_mode="HTML")
    except Exception as e:                          # noqa: BLE001
        log.error(f"[audit] отчёт операторам не ушёл ({district}): {e}")
