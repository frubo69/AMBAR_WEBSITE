"""Хронология заявки — что и во сколько с ней происходило, от файла магазину
до последнего скана (владелец, 3 ноя 2026: «отслеживать пошаговый статус
заявки… зайти, нажать и прям хронологически посмотреть, что во сколько
происходило»).

Лента собирается из двух источников.

Журнал db.supply_events: ручки сами кладут запись в момент события — файл
выслан, ответ загружен, заявку поправили, район взяли, принят без
сканирования, недовоз заявлен и решён… Ведётся с 3 ноя 2026. add() не
роняет действие, которое её зовёт: ошибка журнала — предупреждение в лог.

Документы: поставка (supplies), заявка дня (zayavki), её правки
(zayavka_edits) и реестр кодов (qr_codes). По ним восстанавливаются шаги,
которых в журнале нет: всё, что было до журнала, и первый/последний скан
района (каждый скан в журнал не пишется — их сотни). Шаг документа, у
которого в журнале есть такой же (тот же вид, тот же район) в пределах двух
минут, второй раз не показывается.

Ручка GET /api/owner/supply/{sid}/log → {supply_id, day, order_day, kind,
base, events: [{at, kind, title, cls, who, district, code, note}]} по
времени. Телеграм-id в ответ не попадают: кто — только именем.
"""
import logging
from datetime import datetime, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS
from config_offices import OFFICE_CODES

log = logging.getLogger("supply")

# Вид шага → (заголовок, цвет точки). '' — обычный шаг, ok — принято/закрыто,
# warn — требует внимания, dn — отмена или отказ, wait — ждёт решения.
KINDS = {
    "order_frozen":     ("Заявка сформирована", ""),
    "book":             ("Файл заявки собран", ""),
    "sent":             ("Файл заявки выслан", ""),
    "order_edit":       ("Заявка поправлена", "warn"),
    "order_off":        ("Район снят с заявки", "dn"),
    "order_on":         ("Район возвращён в заявку", ""),
    "order_reset":      ("Правки заявки сняты", ""),
    "answer":           ("Ответ магазина загружен", "ok"),
    "answer_replaced":  ("Ответ загружен заново", "warn"),
    "created":          ("Заявка на другую базу создана", ""),
    "draft":            ("Черновик докупки собран", "wait"),
    "confirmed":        ("Докупка подтверждена", "ok"),
    "base":             ("База названа", ""),
    "line_edit":        ("Состав района поправлен", "warn"),
    "assigned":         ("Район назначен", ""),
    "claimed":          ("Район взят", ""),
    "released":         ("Район отдан", "warn"),
    "started":          ("Приёмка начата", ""),
    "scan_first":       ("Первый скан", ""),
    "scan_last":        ("Последний скан", ""),
    "undo":             ("Убрано из приёмки", "warn"),
    "noscan":           ("Принят без сканирования", "warn"),
    "na":               ("Нет в наличии", "warn"),
    "na_off":           ("Снова в наличии", ""),
    "buy":              ("Цена закупки", ""),
    "short":            ("Недовоз заявлен", "wait"),
    "short_ok":         ("Недовоз подтверждён", "ok"),
    "short_no":         ("Недовоз отклонён", "dn"),
    "done":             ("Район принят", "ok"),
    "done_short":       ("Район закрыт с недобором", "warn"),
    "supply_done":      ("Поставка принята", "ok"),
    "cancelled":        ("Район отменён", "dn"),
    "supply_cancelled": ("Заявка отменена", "dn"),
}

# Шаг из документа и та же запись журнала ближе этого — одно событие.
DEDUP_SEC = 120
# Какие записи журнала «закрывают» шаг документа: «назначен» ставит в задаче
# то же claimed_at, что и «взят», — второй строки про то же быть не должно.
_COVERS = {"claimed": ("claimed", "assigned")}


def _dt(v):
    """Время из базы: datetime (бывает без пояса) или строка — isoformat либо
    str(datetime) с пробелом вместо T. Непонятное — None."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v.strip():
        s = v.strip().replace(" ", "T")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def qstr(v) -> str:
    """Количество словами экрана: 12, 0,5 — без хвоста .0."""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f == int(f) else f"{f:g}".replace(".", ",")


def _plural(n, one, few, many) -> str:
    n = abs(int(float(n or 0)))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _ev(kind, at, who="", district="", note="", src="doc", seq="") -> dict:
    # seq — порядок записи в журнале: шаги одной миллисекунды идут как
    # писались, а не как нашлись.
    return {"kind": kind, "at": _dt(at), "who": str(who or "")[:60],
            "district": str(district or ""), "note": str(note or "")[:200],
            "src": src, "seq": seq}


async def add(kind: str, sid: str = "", *, who: str = "", district: str = "",
              note: str = "", day: str = "", data: dict | None = None, at=None) -> None:
    """Запись в журнал. sid пустой — шаг заявки дня (файл, правки), тогда
    day — день заявки (zayavki._id). Никогда не бросает."""
    try:
        if kind not in KINDS:
            log.warning(f"[supply] хронология: неизвестный шаг {kind!r} ({sid or day})")
            return
        await db.supply_event_add({
            "sid": str(sid or ""), "day": str(day or ""), "kind": kind,
            "who": str(who or "").strip()[:60], "district": str(district or ""),
            "note": str(note or "").strip()[:200], "data": dict(data or {}),
            "at": at or datetime.now(timezone.utc)})
    except Exception as e:                           # noqa: BLE001
        log.warning(f"[supply] хронология: {kind} {sid or day} не записан: {e}")


def answer_note(sup: dict) -> str:
    """«N позиций · M бутылок · не дала K» — подпись к ответу магазина."""
    n = len(sup.get("items") or [])
    qty = int(sup.get("total_qty") or 0)
    gap = int(sup.get("gap_qty") or 0)
    s = (f"{n} {_plural(n, 'позиция', 'позиции', 'позиций')} · "
         f"{qty} {_plural(qty, 'бутылка', 'бутылки', 'бутылок')}")
    return s + (f" · не дала {gap}" if gap > 0 else "")


def what_note(sup: dict) -> str:
    """«M бутылок · N позиций» — подпись к заявке на другую базу."""
    n = len(sup.get("items") or [])
    qty = int(sup.get("total_qty") or 0)
    return (f"{qty} {_plural(qty, 'бутылка', 'бутылки', 'бутылок')} · "
            f"{n} {_plural(n, 'позиция', 'позиции', 'позиций')}")


def _task_counts(sup: dict, oid: str) -> tuple:
    """(принято, план) района в единицах склада."""
    got = plan = 0.0
    for it in sup.get("items") or []:
        plan += float((it.get("by_district") or {}).get(oid) or 0)
        got += float((it.get("got") or {}).get(oid) or 0)
    return got, plan


def done_event(sup: dict, oid: str, gaps: list | None = None) -> tuple:
    """(вид, подпись) для закрытия района: «Район принят · 24 ед» или «Район
    закрыт с недобором · принято 20 из 24»."""
    got, plan = _task_counts(sup, oid)
    gap = sum(float((g or {}).get("gap") or 0) for g in (gaps if gaps is not None
                                                           else ((sup.get("tasks") or {}).get(oid) or {}).get("gaps") or []))
    if gap > 0 or got + 1e-9 < plan:
        return "done_short", f"принято {qstr(got)} из {qstr(plan)}"
    return "done", f"{qstr(got)} ед"


def _from_log(e: dict) -> dict:
    return _ev(e.get("kind") or "", e.get("at"), e.get("who") or "", e.get("district") or "",
               e.get("note") or "", src="log", seq=str(e.get("_id") or ""))


async def _order_day_of(sup: dict, logs: list) -> str:
    """День заявки (zayavki._id), на которую отвечает эта поставка: из записи
    журнала о загрузке, иначе — заявка, закупка по которой шла в день
    поставки (та же логика, что при загрузке ответа)."""
    for e in logs:
        od = (e.get("data") or {}).get("order_day") if e.get("kind") in ("answer", "answer_replaced") else ""
        if od:
            return str(od)
    day = sup.get("day") or ""
    try:
        return (await db.zayavka_freeze_for_buy(day)).get("day") or day
    except Exception:                                # noqa: BLE001
        return day


async def timeline(sid: str) -> dict | None:
    sup = await db.supply_get(sid)
    if not sup:
        return None
    extra = (sup.get("kind") or "main") == "extra"
    day = sup.get("day") or ""
    logs = await db.supply_events(sid)
    order_day = "" if extra else await _order_day_of(sup, logs)
    jl = [_from_log(e) for e in logs]
    if order_day:
        jl += [_from_log(e) for e in await db.supply_events_day(order_day)]
    jl = [e for e in jl if e["at"] and e["kind"] in KINDS]

    der = []
    if order_day:
        z = await db.zayavka_doc(order_day)
        if z:
            asked = z.get("asked") or {}
            n = len(asked)
            qty = int(sum(float(v or 0) for v in asked.values()))
            note = (f"{n} {_plural(n, 'позиция', 'позиции', 'позиций')} · "
                    f"{qty} {_plural(qty, 'бутылка', 'бутылки', 'бутылок')}") if asked else ""
            if z.get("frozen_at"):
                der.append(_ev("order_frozen", z["frozen_at"], note=note))
            if z.get("at"):
                der.append(_ev("book", z["at"], note=note))
        # Время правок хранится одно на день и переписывается загрузкой ответа
        # (она пишет ответ правками): показываем его как правку, только если
        # журнал правок не вёл и время не совпадает с загрузкой.
        ze = await db.zayavka_edits_doc(order_day)
        t = _dt((ze or {}).get("at"))
        if t and not any(e["kind"].startswith("order_") for e in jl):
            near = [x for x in (_dt(sup.get("at")), _dt(sup.get("first_at")), _dt(sup.get("replaced_at"))) if x]
            if not any(abs((t - x).total_seconds()) <= DEDUP_SEC for x in near):
                der.append(_ev("order_edit", t))

    first = sup.get("first_at") or sup.get("at")
    base = sup.get("base") or ""
    if extra:
        what = what_note(sup)
        if sup.get("confirmed_at") or sup.get("status") == "draft":
            der.append(_ev("draft", first, note=what + (f" · из недобора {sup['from_supply']}" if sup.get("from_supply") else "")))
            if sup.get("confirmed_at"):
                der.append(_ev("confirmed", sup["confirmed_at"], who=sup.get("by_name") or "", note=base))
        else:
            der.append(_ev("created", first, who=sup.get("by_name") or "",
                           note=(base + " · " if base else "") + what))
    else:
        der.append(_ev("answer", first, note=answer_note(sup)))
        if sup.get("replaced_at"):
            der.append(_ev("answer_replaced", sup["replaced_at"], note=answer_note(sup)
                           + (f" · раз {int(sup.get('replaced_n') or 0)}" if int(sup.get("replaced_n") or 0) > 1 else "")))

    try:
        scans = await db.intake_scanners([sid])
    except Exception as e:                           # noqa: BLE001
        log.warning(f"[supply] хронология {sid}: сканы не прочитаны: {e}")
        scans = {}
    for oid, t in (sup.get("tasks") or {}).items():
        if not isinstance(t, dict):
            continue
        drv = t.get("driver") or ""
        if t.get("claimed_at"):
            der.append(_ev("claimed", t["claimed_at"], who=drv, district=oid,
                           note="сам" if t.get("staff") else ""))
        sc = [w for w in (scans.get((sid, oid)) or []) if _dt(w.get("first"))]
        f_at = min((_dt(w["first"]) for w in sc), default=None)
        l_at = max((_dt(w["last"]) for w in sc if _dt(w.get("last"))), default=None)
        n = sum(int(w.get("n") or 0) for w in sc)
        units = sum(float(w.get("units") or 0) for w in sc)
        # Начало, совпавшее с первым сканом или с «без сканирования», — не
        # отдельный шаг: его поставил тот же скан или та же отметка.
        st = _dt(t.get("started_at"))
        ns = _dt(t.get("noscan_at"))
        if st and not any(x and abs((x - st).total_seconds()) <= DEDUP_SEC for x in (f_at, ns)):
            der.append(_ev("started", st, who=drv, district=oid))
        if f_at:
            who_f = next((w.get("who") or "" for w in sc if _dt(w["first"]) == f_at), "")
            der.append(_ev("scan_first", f_at, who=who_f, district=oid,
                           note="1 код" if n == 1 else ""))
            if n > 1 and l_at and l_at > f_at:
                who_l = next((w.get("who") or "" for w in sc if _dt(w.get("last")) == l_at), "")
                der.append(_ev("scan_last", l_at, who=who_l, district=oid,
                               note=f"{n} {_plural(n, 'код', 'кода', 'кодов')} · {qstr(units)} ед"))
        for f in t.get("flags") or []:
            if isinstance(f, dict) and f.get("kind") == "undo" and f.get("at"):
                der.append(_ev("undo", f["at"], who=f.get("by") or "", district=oid,
                               note=f.get("product") or ""))
        if t.get("noscan_at"):
            der.append(_ev("noscan", t["noscan_at"], who=t.get("noscan_by") or drv, district=oid))
        sh = t.get("short") or {}
        if isinstance(sh, dict) and sh.get("at"):
            q = sum(float((x or {}).get("qty") or 0) for x in sh.get("lines") or [])
            der.append(_ev("short", sh["at"], who=sh.get("by") or "", district=oid,
                           note=f"{qstr(q)} ед" + (f" · {sh['note']}" if sh.get("note") else "")))
            if sh.get("decided_at") and sh.get("status") in ("ok", "no"):
                der.append(_ev("short_ok" if sh["status"] == "ok" else "short_no", sh["decided_at"],
                               who=sh.get("decided_by") or "", district=oid, note=f"{qstr(q)} ед"))
        if t.get("done_at"):
            kind, note = done_event(sup, oid)
            der.append(_ev(kind, t["done_at"], who=drv or t.get("noscan_by") or "", district=oid, note=note))
        if t.get("cancelled_at"):
            der.append(_ev("cancelled", t["cancelled_at"], who=t.get("cancelled_by") or "", district=oid))
    for pid, b in (sup.get("buys") or {}).items():
        if isinstance(b, dict) and b.get("at"):
            try:
                price = float(b.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            der.append(_ev("buy", b["at"], who=b.get("by_name") or "",
                           note=f"{b.get('name') or pid} · {qstr(price)} AED × {qstr(b.get('qty'))}"))
    if sup.get("status") == "done" and sup.get("done_at"):
        der.append(_ev("supply_done", sup["done_at"]))
    if sup.get("status") == "cancelled" and sup.get("cancelled_at"):
        der.append(_ev("supply_cancelled", sup["cancelled_at"], who=sup.get("cancelled_by") or ""))

    def _same(a, b) -> bool:
        """a — запись журнала, b — шаг документа."""
        if a["kind"] not in _COVERS.get(b["kind"], (b["kind"],)) or a["district"] != b["district"]:
            return False
        if abs((a["at"] - b["at"]).total_seconds()) > DEDUP_SEC:
            return False
        # «Убрано»: несколько за минуту — разные бутылки, сверяем позицию.
        return a["kind"] != "undo" or a["note"].startswith(b["note"])

    out = list(jl)
    for d in der:
        if not d["at"]:
            continue
        if any(_same(j, d) for j in jl):
            continue
        # Файл собирается и при отправке из STAR — это одна запись «выслан».
        if d["kind"] == "book" and any(j["kind"] == "sent" and abs((j["at"] - d["at"]).total_seconds()) <= DEDUP_SEC for j in jl):
            continue
        out.append(d)
    out.sort(key=lambda e: (e["at"], e["seq"]))
    return {"supply_id": sid, "day": day, "order_day": order_day,
            "kind": sup.get("kind") or "main", "base": base,
            "events": [{"at": e["at"].isoformat(), "kind": e["kind"],
                        "title": KINDS[e["kind"]][0], "cls": KINDS[e["kind"]][1],
                        "who": e["who"], "district": e["district"],
                        "code": OFFICE_CODES.get(e["district"], "") if e["district"] else "",
                        "note": e["note"]} for e in out]}


@require_owner
async def handle_log(request):
    """GET /api/owner/supply/{sid}/log — хронология одной заявки."""
    sid = request.match_info.get("sid") or ""
    try:
        t = await timeline(sid)
    except Exception as e:                           # noqa: BLE001
        log.error(f"[supply] хронология {sid}: {e}")
        return web.json_response({"error": "failed"}, status=500, headers=CORS_HEADERS)
    if t is None:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    return web.json_response(t, headers=CORS_HEADERS)
