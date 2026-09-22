"""Штрафы на решение: формирует программа, решает старший.

Владелец, 22 сен 2026: «между „Штрафы и удержания“ и „История штрафов“ —
окошко „Штрафы, требующие решения“: туда автоматически уходит сформированный
штраф с двумя кнопками — назначить или не назначать; всё уходит в историю
вне зависимости от решения, с исходом». И следом — какие именно:
  • «за позднее открытие смены не ценовой штраф, а мы урезаем ему питание с
    80 до 40 принудительно; просто надо принять решение — урезаем или нет»;
  • «по отключению геолокации — фиксированный штраф 200 дирхам, но его можно
    будет редактировать»; «не во время смены — у нас в принципе запрещено
    отключать геолокацию».

Запись (коллекция fine_pending):
  _id     — событие: «geo_off:2026-09-22:Худоба» — одно на человека, вид и
            день; повтор того же события второй записи не даёт;
  kind    — вид (KINDS), name, district, day — день нарушения, at — когда
            записали, reason — за что, note — подробности; у geo_off ещё
            times — во сколько выключал (пока ждёт решения, дописываются);
  amount  — у штрафа: предложенная сумма (200, правится при назначении);
  status  — pending → assigned | declined; decided_by, decided_at; у штрафа
            item — id записи в зарплатах.
Решения (finance_routes.handle_fine_decide):
  • штраф: «Назначить» — обычный штраф в зарплатах, водителю то же сообщение,
    что о любом штрафе; «Не назначать» — только исход в истории;
  • питание: «Урезать» — питание за тот день 40 вместо 80 (meal_rate дня
    водителя — его и так читает config_staff.meal_of везде: итоги смены,
    сбор выручки, «Финансы»), водителю — сообщение; «Не урезать» — ничего.
"""
import html
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import db

log = logging.getLogger(__name__)

MEAL_FROM, MEAL_TO = 80, 40
GEO_OFF_FINE = 200

KINDS = {
    "late_shift": {"reason": "Поздно открыл смену", "action": "meal"},
    "geo_off": {"reason": "Отключил геолокацию", "action": "fine", "amount": GEO_OFF_FINE},
}


def action_of(kind: str) -> str:
    return (KINDS.get(kind) or {}).get("action") or "fine"


def _times_word(n: int) -> str:
    return "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"


def full_text(d: dict) -> str:
    """За что — одной фразой (владелец, 22 сен 2026: «не надо разделять и
    раскидывать по странице — сразу полноценную формулировку штрафа; чтобы
    было предельно понятно каждому, за что, и тем не менее выглядело
    официально»). Её видят старший в «Штрафах», водитель в истории списаний и
    в сообщении бота."""
    kind = d.get("kind") or ""
    nb = "\u00a0"                 # неразрывный: «2 раза», «в 19:40» и тире не рвутся по строкам
    if kind == "geo_off":
        t = [x for x in (d.get("times") or []) if x]
        # Сам выключил (сигнал телеграма с телефона) или связь пропала и это
        # заметил сторож — разные вещи, и называются по-разному: у выключенного
        # или разряженного телефона сигнала нет вовсе (владелец, 22 сен 2026).
        if not d.get("self", True):
            return "Геолокация не передаётся" + (f"{nb}— с {t[0]}" if t else "")
        if len(t) > 1:
            at = ", ".join(f"в{nb}{x}" if i == 0 else x for i, x in enumerate(t[:-1]))
            return f"Отключение геолокации{nb}— {len(t)}{nb}{_times_word(len(t))}: {at} и{nb}{t[-1]}"
        # Не «во время смены»: у нас в принципе запрещено отключать
        # геолокацию (владелец, 22 сен 2026) — и на смене, и вне её.
        return "Отключение геолокации" + (f"{nb}— в{nb}{t[0]}" if t else "")
    if kind == "late_shift":
        note = d.get("note") or ""
        m = re.search(r"\b(\d{1,2}:\d{2})\b", note)
        hm = d.get("hm") or (m.group(1) if m else "")
        r = re.search(r"до (\d{1,2}):00", note)
        rule = d.get("rule_hour") or (int(r.group(1)) if r else 18)
        return f"Открытие смены позже {rule}:00" + (f"{nb}— смена открыта в{nb}{hm}" if hm else "")
    return d.get("reason") or ""


def _geo_note(times: list) -> str:
    t = [x for x in times if x]
    if not t:
        return ""
    return f"выключил в {t[0]}" if len(t) == 1 else f"выключал {len(t)} раза: {', '.join(t)}" \
        if len(t) < 5 else f"выключал {len(t)} раз: {', '.join(t)}"


async def late_shift(name: str, district: str, day: str, at_hm: str, rule_hour: int) -> bool:
    """Смену открыли позже правила — на решение: урезать питание или нет.
    True — новая запись."""
    doc = {"_id": f"late_shift:{day}:{name}", "kind": "late_shift", "name": name,
           "district": district, "day": day, "reason": KINDS["late_shift"]["reason"],
           "note": f"открыл в {at_hm}, правило — до {rule_hour}:00", "hm": at_hm, "rule_hour": rule_hour,
           "status": "pending", "at": datetime.now(timezone.utc)}
    try:
        ok = await db.fine_pending_add(doc)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] {name}: решение по питанию не записано: {e}")
        return False
    if ok:
        log.info(f"[fines] на решение: {name} — поздно открыл смену {day} в {at_hm}, питание")
    return ok


async def geo_off(name: str, district: str, day: str, at_hm: str, by_signal: bool = True) -> bool:
    """Выключилась геолокация — штраф 200 на решение, один за день (на смене
    и вне её: отключать её запрещено вообще, владелец, 22 сен 2026):
    пока по нему не решили, новые выключения того же дня дописываются в
    подробности. True — новая запись (о ней и стоит сказать старшему)."""
    pid = f"geo_off:{day}:{name}"
    doc = {"_id": pid, "kind": "geo_off", "name": name, "district": district, "day": day,
           "reason": KINDS["geo_off"]["reason"], "times": [at_hm], "note": _geo_note([at_hm]),
           "self": bool(by_signal), "amount": GEO_OFF_FINE, "status": "pending",
           "at": datetime.now(timezone.utc)}
    try:
        if await db.fine_pending_add(doc):
            log.info(f"[fines] на решение: {name} — выключил геолокацию {day} в {at_hm}")
            return True
        cur = await db.fine_pending_get(pid)
        if cur and cur.get("status") == "pending" and at_hm not in (cur.get("times") or []):
            times = list(cur.get("times") or []) + [at_hm]
            # Сначала заметили пропажу, потом пришёл сигнал с телефона — это уже
            # выключение: запись называется по самому сильному из того, что было.
            fields = {"times": times, "note": _geo_note(times)}
            if by_signal and not cur.get("self", True):
                fields["self"] = True
            await db.fine_pending_update(pid, fields)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] {name}: штраф за геолокацию не записан: {e}")
    return False


def view(d: dict) -> dict:
    kind = d.get("kind") or ""
    return {"id": str(d.get("_id") or ""), "kind": kind, "action": action_of(kind),
            "name": d.get("name") or "", "district": d.get("district") or "", "day": d.get("day") or "",
            "reason": d.get("reason") or "", "note": d.get("note") or "", "text": full_text(d),
            "amount": int(float(d.get("amount") or 0)) or None,
            **({"meal_from": MEAL_FROM, "meal_to": MEAL_TO} if action_of(kind) == "meal" else {}),
            "status": d.get("status") or "pending", "by": d.get("decided_by") or ""}


async def pending() -> list:
    """Ждут решения — для окошка «Штрафы, требующие решения»."""
    try:
        return [view(d) for d in await db.fine_pending_list(status="pending")]
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] ждущие не прочитаны: {e}")
        return []


async def decided(limit: int = 60) -> list:
    """Решения, у которых нет своей записи в зарплатах, — в историю с исходом:
    любое «не назначать / не урезать» и «урезать питание» (штраф, назначенный
    по решению, виден в истории сам — записью в зарплатах)."""
    try:
        rows = await db.fine_pending_list(status="declined", limit=limit)
        rows += [d for d in await db.fine_pending_list(status="assigned", limit=limit)
                 if action_of(d.get("kind") or "") == "meal"]
        return rows
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] решения не прочитаны: {e}")
        return []


async def for_person(name: str, limit: int = 60) -> list:
    """Решения по одному человеку, у которых нет записи в зарплатах, — в его
    историю списаний в приложении водителя (владелец, 22 сен 2026: «если
    принимается решение водителя не штрафовать, ему в истории штрафов надо
    показывать — как тот, который ему решили простить»): прощённый штраф,
    прощённое и урезанное питание. Назначенный штраф он и так видит записью."""
    try:
        rows = await db.fine_pending_list(status="declined", limit=limit, name=name)
        rows += [d for d in await db.fine_pending_list(status="assigned", limit=limit, name=name)
                 if action_of(d.get("kind") or "") == "meal"]
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] решения по {name} не прочитаны: {e}")
        return []
    out = []
    for d in rows:
        meal = action_of(d.get("kind") or "") == "meal"
        out.append({"id": "auto:" + str(d.get("_id") or ""), "kind": "fine", "t": "Штраф",
                    "amount": (MEAL_FROM - MEAL_TO) if meal else int(float(d.get("amount") or 0)),
                    "per_month": 0, "day": d.get("day") or "",
                    # часа не даём: у решения он свой, а день — день нарушения;
                    # во сколько было нарушение — в подробностях
                    "at": "",
                    "start": str(d.get("day") or "")[:7], "reason": d.get("reason") or "",
                    "note": d.get("note") or "", "text": full_text(d), "due": 0, "left": 0, "done": False,
                    "cancelled": False, "src": "", "wid": "", "auto": d.get("kind") or "",
                    "forgiven": d.get("status") == "declined", "meal": meal,
                    **({"meal_from": MEAL_FROM, "meal_to": MEAL_TO} if meal else {})})
    return out


def meal_cut_text(doc: dict, who: str) -> str:
    """Водителю — его ботом: за что и насколько урезано питание."""
    import pay_notify as _pn
    lines = [f"🍽 <b>Питание за {html.escape(_pn.day_t(doc.get('day') or ''))} — "
             f"{MEAL_TO} AED вместо {MEAL_FROM}</b>", html.escape(full_text(doc))]
    if who:
        lines.append(f"Решил: {html.escape(who)}")
    return "\n".join(x for x in lines if x)


def open_button(text: str = "Решить по штрафу") -> dict | None:
    """Кнопка под сообщением старшему — STAR сразу на «Штрафах». Адрес — от
    OWNER_WEBAPP_URL: наш домен людям не даём; адреса нет — нет и кнопки."""
    url = os.getenv("OWNER_WEBAPP_URL", "").strip()
    if not url:
        return None
    url += ("&" if "?" in url else "?") + urlencode({"go": "fines"})
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": url}}]]}
