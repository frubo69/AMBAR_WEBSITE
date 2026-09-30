"""Критические остатки: чего на полке района осталось меньше, чем на день.

Владелец, 30 сен 2026: «когда на складе остаются критические остатки — алерт в
бота сообщением, а в самой заявке эти позиции светятся красным, и на складе.
Раз в сутки». Правило выбрал он же: «хватит меньше чем на день, и когда ноль —
для редких».

  критично = позицию в районе продают (за окно продаж был хоть один день),
             и на полке либо пусто, либо меньше, чем уходит за обычный день.

Ходовой позиции двух бутылок мало, если в день уходит пять; редкой (одна в
неделю) хватает и одной — она краснеет только на нуле. Позиция, которую в этом
районе не продавали вовсе, не краснеет никогда: пустая полка там — не новость.
Район, где склад не считали, молчит: остатка там нет не потому, что пусто.

Считаем тем же остатком, что карточка «Склад» и заявка (stock_routes), и теми
же продажами, что норма: среднее за день, свежие дни весят больше.

Сообщение — раз в сутки, в 10:00 по Дубаю: владельцам в STAR целиком,
операторам в их бот — по их районам. Нет критического — нет сообщения.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import db
import stock_routes as SR
from config_offices import OFFICE_CODES, OFFICE_IDS, OFFICE_NAMES

log = logging.getLogger("stock_alerts")

SEND_HOUR = 10                      # по Дубаю
DUBAI = timezone(timedelta(hours=4))
ON = os.getenv("AMBAR_STOCK_ALERT", "1") != "0"
MAX_LINES = 12                      # строк на район в сообщении; остальное — числом


def _rates(d: dict, oid: str) -> dict:
    """{позиция: единиц в обычный день} по продажам района."""
    src = (d.get("per") or {}).get(oid) or {}
    if not src:
        return {}
    lo, n_all = d["from"], d["days"]
    n = n_all - lo
    if n <= 0:
        return {}
    wt = [0.5 ** ((n - 1 - i) / SR.NORM_HALF_LIFE) for i in range(n)]
    wsum = sum(wt) or 1.0
    out = {}
    for pid, ser in src.items():
        ser = ser[lo:]
        if sum(ser) <= 0:
            continue
        out[pid] = sum(wt[i] * ser[i] for i in range(n)) / wsum
    return out


async def critical(day: str = "") -> dict:
    """{район: [{id, name, no, have, per_day, zero}]} — худшее сверху."""
    from config_stock_order import order_key
    day = (day or "").strip() or SR._biz_day()
    base = await SR._district_base(day)
    dem = await SR._demand(day)
    cat = SR._catalog()
    out = {}
    for oid in OFFICE_IDS:
        b = base.get(oid) or {}
        if not b.get("counted_at"):
            continue                                # склад района не считали
        have = b.get("have_exact") or b.get("have") or {}
        rows = []
        for pid, rate in _rates(dem, oid).items():
            p = cat.get(pid)
            if not p or rate <= 0:
                continue
            h = max(0.0, float(have.get(pid) or 0))
            if h > 0 and h >= rate:
                continue
            rows.append({"id": pid, "name": p.get("name", ""), "no": order_key(pid) + 1,
                         "have": round(h * 2) / 2,
                         "per_day": round(rate, 1), "zero": h <= 0})
        # Пустая полка ходовой позиции — первой: это уже потерянные продажи.
        rows.sort(key=lambda r: (not r["zero"], -r["per_day"], r["no"]))
        if rows:
            out[oid] = rows
    return out


async def ids(day: str = "") -> dict:
    """{район: [позиции]} — для экранов: что красить красным."""
    return {o: [r["id"] for r in rows] for o, rows in (await critical(day)).items()}


async def mark_order(data: dict) -> dict:
    """Пометить в заявке критические клетки и строки (crit) — на месте."""
    try:
        crit = {o: set(v) for o, v in (await ids()).items()}
    except Exception as e:                           # noqa: BLE001
        log.warning(f"[alerts] заявка без пометок: {e}")
        return data
    for key in ("rows", "all_rows"):
        for r in data.get(key) or []:
            есть = False
            for oid, c in (r.get("cells") or {}).items():
                if isinstance(c, dict) and r.get("id") in crit.get(oid, ()):
                    c["crit"] = True; есть = True
            if есть:
                r["crit"] = True
    return data


def _q(v) -> str:
    v = round(float(v or 0) * 2) / 2
    return str(int(v)) if v == int(v) else str(v).replace(".", ",")


def text_for(crit: dict, only: list | None = None) -> str:
    """Сообщение. only — районы получателя (оператору — его)."""
    ids = [o for o in OFFICE_IDS if o in crit and (only is None or o in only)]
    if not ids:
        return ""
    всего = sum(len(crit[o]) for o in ids)
    parts = [f"🔴 *Критические остатки — {всего}*",
             "Хватит меньше чем на день или уже пусто."]
    for o in ids:
        rows = crit[o]
        parts.append(f"\n*{OFFICE_CODES.get(o, '')} {OFFICE_NAMES.get(o, o)}* · {len(rows)}")
        for r in rows[:MAX_LINES]:
            name = str(r["name"]).replace("*", "").replace("_", " ")
            parts.append(f"• {name} — " + ("пусто" if r["zero"] else f"{_q(r['have'])}")
                         + f" · в день {_q(r['per_day']) if r['per_day'] >= 0.5 else 'меньше 1'}")
        if len(rows) > MAX_LINES:
            parts.append(f"…и ещё {len(rows) - MAX_LINES}")
    return "\n".join(parts)


async def send(day: str = "") -> dict:
    """Разослать сегодняшнее сообщение. Возвращает, кому сколько ушло."""
    crit = await critical(day)
    res = {"positions": sum(len(v) for v in crit.values()), "owners": 0, "operators": 0}
    if not crit:
        return res
    try:
        from owner_routes import notify_owners_force
        await notify_owners_force("stock.critical", text_for(crit))
        res["owners"] = 1
    except Exception as e:                           # noqa: BLE001
        log.error(f"[alerts] владельцам не ушло: {e}")
    try:
        res["operators"] = await _tell_operators(crit)
    except Exception as e:                           # noqa: BLE001
        log.error(f"[alerts] операторам не ушло: {e}")
    return res


async def _tell_operators(crit: dict) -> int:
    """Операторам — в их бот. Районный оператор получает свои районы; старший и
    общий планшет (за ним садятся разные люди) — все."""
    import config_staff as staff
    from api_server import tg_send
    token = os.getenv("OPERATOR_BOT_TOKEN", "")
    ids = [int(x) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]
    if not token or not ids:
        return 0
    try:
        await staff.sync()
    except Exception:                                # noqa: BLE001
        pass
    n = 0
    for tid in ids:
        имя = staff.OPERATOR_BY_ID.get(tid, "")
        свои = [d for d, op in (staff.DISTRICT_OPERATOR or {}).items() if имя and op == имя]
        t = text_for(crit, свои or None)
        if not t:
            continue
        try:
            await tg_send(token, tid, t, parse_mode="Markdown")
            n += 1
        except Exception as e:                       # noqa: BLE001
            log.warning(f"[alerts] оператору не ушло: {e}")
    return n


async def loop():
    """Раз в сутки, в SEND_HOUR по Дубаю. Отметка о посланном — в базе: рестарт
    службы в 10:05 не должен слать второй раз."""
    if not ON:
        log.info("[alerts] критические остатки: выключено")
        return
    while True:
        try:
            now = datetime.now(DUBAI)
            today = now.strftime("%Y-%m-%d")
            if now.hour >= SEND_HOUR and await db.once_mark("stock_alert", today):
                r = await send()
                log.info(f"[alerts] {today}: критических {r['positions']}, "
                         f"владельцам {r['owners']}, операторам {r['operators']}")
        except Exception as e:                       # noqa: BLE001
            log.error(f"[alerts] круг: {e}")
        await asyncio.sleep(300)
