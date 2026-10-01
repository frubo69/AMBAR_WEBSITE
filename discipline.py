"""Карточка дисциплины: что на человеке за период — штрафы, прощённое, ждущее.

Владелец, 1 окт 2026 («1, 2, 7»): по каждому человеку — сколько и каких
нарушений, сумма, динамика. Считается из тех же двух мест, что и «Штрафы»:
записи в зарплатах (fin_pay_items: fine / hold) и решения по тому, что
сформировала программа (fine_pending). Ничего своего не хранит.

Что есть что:
  fines     — назначенные штрафы и удержания (отменённые — отдельно, amnesty);
  forgiven  — программа предложила, старший решил не назначать;
  meal      — урезанное питание (80 → 40);
  pending   — ждёт решения;
  clean     — сколько дней без нарушений (от последнего события до сегодня).
"""
import logging
from datetime import datetime, timedelta

import bizday
import db
import finance_pay as pay
import fines_auto

log = logging.getLogger(__name__)


def _day_of(it: dict) -> str:
    return str(it.get("day") or "")[:10] or (bizday.day_of(it.get("at")) or "")


def _what(reason: str) -> str:
    """Вид нарушения для сводки: первая часть формулировки, без подробностей."""
    r = str(reason or "").replace(" ", " ")
    for sep in (" — ", " · "):
        r = r.split(sep)[0]
    return r.strip() or "Без причины"


async def build(days: int = 90, today: str = "") -> dict:
    today = today or bizday.biz_day()
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
    month0 = today[:7] + "-01"
    people: dict = {}

    def P(name: str) -> dict:
        return people.setdefault(name, {"name": name, "fines": 0, "sum": 0, "month_fines": 0, "month_sum": 0,
                                        "amnesty": 0, "forgiven": 0, "meal": 0, "pending": 0,
                                        "last": "", "kinds": {}, "rows": []})

    def add(p, day, what, amount, state):
        k = p["kinds"].setdefault(what, {"t": what, "n": 0, "sum": 0})
        k["n"] += 1
        if state == "fine":
            k["sum"] += amount
        p["rows"].append({"day": day, "t": what, "amount": amount, "state": state})
        if state != "pending" and day > p["last"]:
            p["last"] = day

    for it in await db.fin_pay_items_get():
        if it.get("kind") not in pay.PENALTY_KINDS:
            continue
        day = _day_of(it)
        if not day or day < since or day > today:
            continue
        p, amount = P(it.get("name") or ""), pay._i(pay._n(it.get("amount")))
        what = _what(it.get("reason")) if it.get("kind") == "fine" else "Удержание"
        if it.get("cancelled_at"):
            p["amnesty"] += 1
            add(p, day, what, amount, "amnesty")
            continue
        p["fines"] += 1; p["sum"] += amount
        if day >= month0:
            p["month_fines"] += 1; p["month_sum"] += amount
        add(p, day, what, amount, "fine")

    for status in ("pending", "declined", "assigned"):
        for d in await db.fine_pending_list(status=status, limit=2000):
            day = str(d.get("day") or "")[:10]
            if not day or day < since or day > today:
                continue
            meal = fines_auto.action_of(d.get("kind") or "") == "meal"
            if status == "assigned" and not meal:
                continue                                 # назначенный штраф уже посчитан записью
            p = P(d.get("name") or "")
            what = _what(fines_auto.full_text(d))
            amount = (fines_auto.MEAL_FROM - fines_auto.MEAL_TO) if meal else int(float(d.get("amount") or 0))
            if status == "pending":
                p["pending"] += 1; add(p, day, what, amount, "pending")
            elif status == "declined":
                p["forgiven"] += 1; add(p, day, what, amount, "forgiven")
            else:
                p["meal"] += 1; add(p, day, what, amount, "meal")

    out = []
    for p in people.values():
        if not p["name"]:
            continue
        p["kinds"] = sorted(p["kinds"].values(), key=lambda k: (-k["n"], k["t"]))
        p["rows"].sort(key=lambda r: r["day"], reverse=True)
        p["events"] = len(p["rows"])
        p["clean"] = ((datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(p["last"], "%Y-%m-%d")).days
                      if p["last"] else None)
        out.append(p)
    # Сверху — у кого больше всего назначено и ждёт; при равенстве — по сумме.
    out.sort(key=lambda p: (-(p["fines"] + p["meal"] + p["pending"]), -p["sum"], p["name"]))
    return {"days": days, "since": since, "today": today, "people": out,
            "totals": {"fines": sum(p["fines"] for p in out), "sum": sum(p["sum"] for p in out),
                       "pending": sum(p["pending"] for p in out), "forgiven": sum(p["forgiven"] for p in out),
                       "meal": sum(p["meal"] for p in out)}}
