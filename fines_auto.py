"""Штрафы на решение: формирует программа, решает старший.

Владелец, 22 сен 2026: «между „Штрафы и удержания“ и „История штрафов“ —
окошко „Штрафы, требующие решения“. Например, кто-то поздно открыл смену —
после 15:00 — туда автоматически уходит сформированный штраф с двумя кнопками:
назначить или не назначать. И всё это уходит в историю штрафов вне
зависимости от того, был он назначен в итоге или нет, — с исходом».

Запись (коллекция fine_pending):
  _id     — событие: «late_shift:2026-09-22:Худоба» — одно на человека и день,
            повтор того же события второй записи не даёт;
  kind    — вид (KINDS), name, district, day — день нарушения, at — когда
            записали, reason — за что, note — подробности («открыл в 16:20»);
  amount  — предложенная сумма: та, с которой этот вид назначили в последний
            раз (поменяли — дальше предлагается новая); ни разу не назначали —
            пусто, сумму вписывают при назначении;
  status  — pending → assigned (item — id штрафа в зарплатах) | declined;
            decided_by, decided_at — кто и когда решил.
Назначен — это обычный штраф (fin_pay_items, с пометкой auto): водителю уходит
то же сообщение, что о любом штрафе, в истории он — штраф со своим статусом.
Не назначен — строкой в истории с исходом «Не назначен»; водителю — ничего.
Решает finance_routes.handle_fine_decide.
"""
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import db

log = logging.getLogger(__name__)

KINDS = {
    "late_shift": {"reason": "Поздно открыл смену"},
}


async def suggest(kind: str) -> int | None:
    """Сумма, с которой этот вид назначили в последний раз."""
    try:
        rows = await db.fine_pending_list(status="assigned", kind=kind, limit=50)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] прошлые решения не прочитаны: {e}")
        return None
    rows.sort(key=lambda r: str(r.get("decided_at") or ""), reverse=True)
    for r in rows:
        a = int(float(r.get("amount") or 0))
        if a > 0:
            return a
    return None


async def late_shift(name: str, district: str, day: str, at_hm: str, rule_hour: int) -> bool:
    """Смену открыли позже правила — штраф на решение. True — новая запись."""
    doc = {"_id": f"late_shift:{day}:{name}", "kind": "late_shift", "name": name,
           "district": district, "day": day, "reason": KINDS["late_shift"]["reason"],
           "note": f"открыл в {at_hm}, правило — до {rule_hour}:00",
           "amount": await suggest("late_shift"), "status": "pending",
           "at": datetime.now(timezone.utc)}
    try:
        ok = await db.fine_pending_add(doc)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] {name}: штраф на решение не записан: {e}")
        return False
    if ok:
        log.info(f"[fines] на решение: {name} — поздно открыл смену {day} в {at_hm}")
    return ok


def view(d: dict) -> dict:
    return {"id": str(d.get("_id") or ""), "kind": d.get("kind") or "", "name": d.get("name") or "",
            "district": d.get("district") or "", "day": d.get("day") or "",
            "reason": d.get("reason") or "", "note": d.get("note") or "",
            "amount": int(float(d.get("amount") or 0)) or None, "status": d.get("status") or "pending",
            "by": d.get("decided_by") or "",
            "decided_at": d["decided_at"].isoformat() if isinstance(d.get("decided_at"), datetime) else ""}


async def pending() -> list:
    """Ждут решения — для окошка «Штрафы, требующие решения»."""
    try:
        return [view(d) for d in await db.fine_pending_list(status="pending")]
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] ждущие не прочитаны: {e}")
        return []


async def declined(limit: int = 60) -> list:
    """Не назначенные — в историю штрафов с исходом."""
    try:
        return await db.fine_pending_list(status="declined", limit=limit)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] не назначенные не прочитаны: {e}")
        return []


def open_button() -> dict | None:
    """Кнопка под сообщением старшему — STAR сразу на «Штрафах». Адрес — от
    OWNER_WEBAPP_URL: наш домен людям не даём; адреса нет — нет и кнопки."""
    url = os.getenv("OWNER_WEBAPP_URL", "").strip()
    if not url:
        return None
    url += ("&" if "?" in url else "?") + urlencode({"go": "fines"})
    return {"inline_keyboard": [[{"text": "Решить по штрафу", "web_app": {"url": url}}]]}
