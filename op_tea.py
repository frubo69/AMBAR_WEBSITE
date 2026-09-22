"""Чай операторов — сколько заработал каждый: за день и за месяц.

Владелец, 22 сен 2026: «мы можем что-то придумать, чтобы операторы у себя в
приложении видели свой чай, который они заработали?»

Что считается. Чай — ставка «чайной» позиции каталога (поле tip, 50 AED за
бутылку) плюс чай клиента, со всех доставленных заказов района за учётный
день (cash_math.order_tea). Ровно эту сумму старший забирает у района второй
пачкой в «Сборе выручки» и видит в «Команде» чаевыми района.

Кому. Оператору района в тот день — тому, кто записан в открытии смены
района (shift_opens.operator: раскладка на момент открытия, с перестановками).
Смену не открывали — оператору, на которого отправлен сам заказ
(dispatch_operator), и только потом нынешней раскладке. Так вчерашний чай не
переезжает к другому человеку, если район сегодня отдали другому.

Заказы в работе (ещё не доставлены) — отдельной строкой «в работе»: это чай,
который будет, а не который есть.
"""
import calendar
import time

import bizday
import cash_math
import config_staff as staff
import db
from config_offices import OFFICE_CODES

# Из заказа нужно только это: месяц заказов целиком — полмегабайта адресов и
# переписки, которые экрану чая ни к чему.
FIELDS = ["order_id", "timestamp", "confirmed_at", "delivered_at", "day", "status",
          "office_id", "items.id", "items.name", "items.qty", "tip", "dispatch_operator", "driver"]

# Панель спрашивает чай раз в минуту с каждого устройства; месяц заказов на
# всех один. Прошлые месяцы не меняются — держим дольше.
TTL_NOW, TTL_PAST = 20.0, 600.0
_CACHE: dict = {}


def _last_day(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _who(o: dict, day: str, opens: dict) -> str:
    oid = o.get("office_id") or ""
    op = (opens.get((day, oid)) or {}).get("operator")
    return op or str(o.get("dispatch_operator") or "").strip() or staff.DISTRICT_OPERATOR.get(oid, "")


def _order_view(o: dict, rates: dict) -> dict:
    """Заказ строкой: за какие бутылки чай и сколько."""
    lines, bottles = [], 0
    for it in o.get("items") or []:
        r = rates.get(it.get("id")) or 0
        q = int(cash_math._n(it.get("qty")))
        if r > 0 and q > 0:
            lines.append({"name": str(it.get("name") or it.get("id") or ""), "qty": q, "aed": r * q})
            bottles += q
    tip = int(cash_math._n(o.get("tip")))
    if tip > 0:
        lines.append({"name": "Чаевые клиента", "qty": 0, "aed": tip})
    # Узнают заказ по району и водителю. Часа не показываем: телефонные заказы
    # вносят пачкой под утро (замер 22 сен: 81 из 89 чайных заказов принят в
    # 05:xx), и час внесения ничего не говорит; по нему только сортируем.
    at = o.get("confirmed_at") or o.get("timestamp") or o.get("delivered_at") or ""
    return {"id": str(o.get("order_id") or ""), "code": OFFICE_CODES.get(o.get("office_id") or "", ""),
            "driver": str(o.get("driver") or "").strip(), "at": str(at),
            "lines": lines, "bottles": bottles, "aed": sum(x["aed"] for x in lines)}


async def month(month: str, test: bool = False) -> dict:
    """Чайные заказы учётных дней месяца (по сегодня) с оператором каждого."""
    today = bizday.biz_day()
    key = (month, bool(test))
    hit = _CACHE.get(key)
    ttl = TTL_NOW if month >= today[:7] else TTL_PAST
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    first, last = f"{month}-01", min(_last_day(month), today)
    rows, live = [], []
    if first <= today:
        since, until = bizday.window_utc(first, last)
        orders = await db.get_orders_in_range(since, until, None, limit=None, fields=FIELDS, test=test)
        opens = await db.shift_opens_between(first, last)
        rates = cash_math.tea_rates()
        for o in orders:
            if o.get("status") != "delivered":
                continue
            day = bizday.order_day(o)
            if not day or not first <= day <= last:
                continue
            v = _order_view(o, rates)
            if v["aed"] <= 0:
                continue
            rows.append({"day": day, "who": _who(o, day, opens), "district": o.get("office_id") or "", **v})
        if month == today[:7]:
            now = await db.orders_from(bizday.since_utc(today), test=test)
            for o in now.values():
                if o.get("status") not in ("pending", "approved"):
                    continue
                aed = cash_math.order_tea(o, rates)
                if aed > 0:
                    live.append({"who": _who(o, today, opens), "aed": aed})
    data = {"month": month, "today": today, "rows": rows, "live": live}
    _CACHE[key] = (time.monotonic(), data)
    if len(_CACHE) > 24:
        _CACHE.clear()
        _CACHE[key] = (time.monotonic(), data)
    return data


def person(data: dict, name: str | None) -> dict:
    """Чай одного человека (None — всех вместе, для тест-режима): сегодня, по
    дням месяца, по заказам."""
    mine = [r for r in data["rows"] if name is None or r["who"] == name]
    days: dict = {}
    for r in mine:
        d = days.setdefault(r["day"], {"day": r["day"], "aed": 0, "bottles": 0, "orders": []})
        d["aed"] += r["aed"]
        d["bottles"] += r["bottles"]
        d["orders"].append({k: r[k] for k in ("id", "code", "driver", "at", "lines", "bottles", "aed")})
    for d in days.values():
        d["orders"].sort(key=lambda x: x["at"], reverse=True)
    today = days.get(data["today"]) or {"aed": 0, "bottles": 0, "orders": []}
    by: dict = {}
    for o in today["orders"]:
        by[o["code"]] = by.get(o["code"], 0) + o["aed"]
    live = [x for x in data["live"] if name is None or x["who"] == name]
    return {
        "month": data["month"], "today": data["today"],
        "day": {"aed": today["aed"], "bottles": today["bottles"],
                "by": [{"code": c, "aed": a} for c, a in sorted(by.items())],
                "live": {"aed": sum(x["aed"] for x in live), "n": len(live)}},
        "total": {"aed": sum(d["aed"] for d in days.values()),
                  "bottles": sum(d["bottles"] for d in days.values()), "days": len(days)},
        "days": sorted(days.values(), key=lambda d: d["day"], reverse=True),
    }


def summary(data: dict, people: list) -> dict:
    """Для старшего: каждый районный оператор строкой — сегодня и за месяц."""
    ops = []
    for p in people:
        v = person(data, p["name"])
        ops.append({"name": p["name"], "codes": sorted(OFFICE_CODES.get(d, "") for d in p.get("districts") or []),
                    "today": v["day"]["aed"], "live": v["day"]["live"]["aed"], "month": v["total"]["aed"]})
    everyone = person(data, None)
    return {"month": data["month"], "today": data["today"], "ops": ops,
            "day": everyone["day"], "total": everyone["total"]}
