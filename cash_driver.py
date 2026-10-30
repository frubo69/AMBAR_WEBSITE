"""Водитель за смену в «Сборе выручки» (владелец, 30 окт 2026: «сделай так,
чтобы можно было раскрыть и посмотреть, какой водитель сколько сдал по
факту; на него можно нажать и посмотреть на отдельной странице, сколько за
смену он заработал, сколько у него расходов, какими они были, есть ли те,
что не согласовали или ещё не согласованные, сколько он чая наработал —
чтобы расхождения было просто искать»).

Две части.

  • rows(day, orders) — строки водителей по районам для складки «Водители»
    в карточке района: все, кто в районе по реестру (кроме уехавших), кто
    возил заказы района и кто делал сверку. У строки — что насчитал к сдаче
    (сверка смены), что по приложению, чем ответил оператор, сколько заказов,
    сколько расходов ждут решения. Кто не работал (ни смены, ни заказов, ни
    расходов, ни сверки) — в строки не идёт: пустая строка не помогает искать.

  • driver_day(day, name) — страница водителя за день: две пачки той же
    арифметикой, что в его «Итогах смены» и в сверке (cash_math.piles),
    сверка, расходы с решениями (принят / ждёт / отклонён, безналом, мимо
    наличных), чай операторам по людям, его собственное (питание, бонус),
    заказы строками.

Второй арифметики здесь нет: всё считается тем же, чем экран водителя, —
иначе старший искал бы расхождение между двумя программами, а не между
программой и деньгами.
"""
import logging

import db
import bizday
import cash_math
import shift_recon
import config_staff as staff
import expense_routes as _exp
from config_offices import OFFICE_IDS, OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger("cash_driver")


def _i(v, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


def _pending_cash(extras: list) -> int:
    """Сколько расходов из наличных ждут решения старшего."""
    return sum(1 for e in extras or []
               if (e.get("status") or "approved") == "pending" and _exp.touches_cash(e))


async def _driver_days(day: str) -> dict:
    try:
        return {r.get("driver"): r for r in await db.get_driver_days(day)}
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[cash_driver] дни водителей за {day}: {e}")
        return {}


async def rows(day: str, orders: list, driver_days: dict | None = None) -> dict:
    """{район: [строка водителя]} за учётный день.

    Строка: name, cash (насчитал к сдаче, None — не считал), cash_app (по
    приложению сейчас), diff, ok (сколько шагов сверки отмечено), confirmed,
    op_fact/op_gap/op_fact_by (ответ оператора), fixes/fix_open, mismatch,
    started, orders (доставленных заказов), exp_pending (расходов из наличных
    ждут решения), shift ('' | open | closed), working."""
    if driver_days is None:
        driver_days = await _driver_days(day)
    views = await shift_recon.day_views(day, orders, driver_days)
    by_o: dict = {oid: [] for oid in OFFICE_IDS}
    seen: set = set()

    def put(oid: str, name: str) -> None:
        if oid in by_o and name and name not in seen:
            seen.add(name)
            by_o[oid].append(name)

    # Реестр: водители района, кроме уехавших (владелец, 27 сен 2026: уехавший
    # денег не возит).
    for d in staff.all_drivers():
        name = str(d.get("name") or "").strip()
        if name and not staff.is_away(name):
            put(d.get("district") or "", name)
    # Возил заказы района — хотя бы один доставленный за этот день.
    for o in orders:
        if o.get("status") != "delivered" or bizday.order_day(o) != day:
            continue
        put(o.get("office_id") or "", str(o.get("driver") or "").strip())
    # Делал сверку.
    for name, v in views.items():
        put(shift_recon._district_of(v, name), name)

    out: dict = {}
    for oid, names in by_o.items():
        rows_ = []
        for name in names:
            v = views.get(name)
            dd = driver_days.get(name) or {}
            extras = dd.get("extras") or []
            mine = shift_recon.mine(orders, name, day)
            started = bool(v and v["started"])
            if not mine and not extras and dd.get("working") is None and not started:
                continue                                     # не работал — строки нет
            cash_app = v["cash_app"] if v else shift_recon.cash_app(orders, dd, name, day)
            rows_.append({
                "name": name,
                "cash": v["cash"] if v else None, "cash_app": cash_app,
                "diff": v["diff"] if v else None,
                "ok": len(v["ok"]) if v else 0,
                "confirmed": bool(v and v["confirmed"]),
                "confirmed_at": (v or {}).get("confirmed_at") or "",
                "op_fact": v["op_fact"] if v else None, "op_gap": v["op_gap"] if v else None,
                "op_fact_by": (v or {}).get("op_fact_by") or "",
                "op_fact_at": (v or {}).get("op_fact_at") or "",
                "fixes": v["fixes"] if v else [], "fix_open": v["fix_open"] if v else 0,
                "mismatch": bool(v and v["mismatch"]), "alert": bool(v and v["alert"]),
                "started": started,
                "orders": len(mine), "exp_pending": _pending_cash(extras),
                "shift": "closed" if dd.get("shift_close_at") else "open" if dd.get("shift_open_at") else "",
                "working": dd.get("working"),
            })
        rows_.sort(key=lambda r: r["name"])
        out[oid] = rows_
    return out


def _tea_by(mine: list) -> list:
    """Чай операторам по людям — с наличных заказов, как в итогах смены
    водителя: кто принял заказ, тому и чай."""
    acc: dict = {}
    for o in mine:
        if not cash_math.pays_cash(o):
            continue
        t = cash_math.order_tea(o)
        if t:
            who = (o.get("created_by_name") if o.get("source") == "manual" else "") or "из приложения"
            acc[who] = acc.get(who, 0) + t
    return [{"who": w, "aed": a} for w, a in sorted(acc.items(), key=lambda x: -x[1])]


def _order_rows(mine: list) -> list:
    """Заказы смены строками: время · адрес · как платили · сумма; что взял
    наличными, если рассчитались не ровно; валюта; чай; создан по сверке."""
    out = []
    for o in sorted(mine, key=lambda o: str(o.get("delivered_at") or o.get("timestamp") or "")):
        m = cash_math.order_money(o) if cash_math.pays_cash(o) else None
        fx = ""
        if m and m["fx"]:
            code, amount = m["fx"]
            fx = f"{cash_math.FX_SYM.get(code, code)}{amount:g}"
        out.append({"id": str(o.get("order_id") or ""),
                    "t": shift_recon._hm(o.get("delivered_at") or o.get("confirmed_at") or o.get("timestamp")),
                    "a": str(o.get("address") or o.get("customer_name") or "").strip() or f"#{o.get('order_id') or ''}",
                    "pay": shift_recon.pay_label(o), "aed": _i(o.get("total")),
                    "cash": _i(m["aed"]) if m else None, "fx": fx,
                    "tea": cash_math.order_tea(o), "recon": bool(o.get("recon"))})
    return out


def _expense_rows(dd: dict, meal: int) -> tuple[list, dict]:
    """Расходы дня строками, все — с решениями; питание первой строкой."""
    items = []
    if dd.get("working") is not None:
        items.append({"id": "", "kind": "meal", "kind_t": "Питание", "amount": meal, "at": "",
                      "status": "approved", "note": "на смене" if dd.get("working") else "дома",
                      "pay": "", "nocash": False, "bottle": False, "no_receipt": False,
                      "decided_note": "", "decided_at": "", "photo": False})
    tot = {"pending": 0, "pending_n": 0, "rejected": 0, "rejected_n": 0}
    for e in dd.get("extras") or []:
        st = str(e.get("status") or "approved")
        amt = _exp._signed(e)
        card = _exp.is_card(e)
        bottle = _exp.is_bottle(e)
        мимо = bool(e.get("nocash")) or bottle
        if st == "pending" and not card and not мимо:
            tot["pending"] += amt
            tot["pending_n"] += 1
        if st == "rejected":
            tot["rejected"] += amt
            tot["rejected_n"] += 1
        items.append({"id": e.get("id") or "", "kind": e.get("kind") or "other",
                      "kind_t": e.get("kind_t") or _exp._kind(e).get("t", "Что-то ещё"),
                      "amount": amt, "at": str(e.get("at") or ""), "status": st,
                      "note": str(e.get("comment") or "")[:80],
                      "pay": "card" if card else ("cash" if e.get("pay") == "cash" else ""),
                      "nocash": мимо, "bottle": bottle, "no_receipt": bool(e.get("no_receipt")),
                      "decided_note": str(e.get("decided_note") or "")[:120] if st == "rejected" else "",
                      "decided_at": _iso(e.get("decided_at")) if st != "pending" else "",
                      "photo": bool(e.get("photo") or e.get("thumb") or e.get("car_photo"))})
    # Питание — первой строкой, без времени: это плата за день; дальше по времени.
    items.sort(key=lambda x: (x["at"] != "", x["at"]))
    return items, tot


async def driver_day(day: str, name: str) -> dict:
    """Страница водителя за учётный день."""
    since, until = bizday.window_utc(day, day)
    try:
        orders = await db.get_orders_in_range(since, until)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[cash_driver] заказы {day}: {e}")
        orders = []
    mine = shift_recon.mine(orders, name, day)
    try:
        dd = await db.get_driver_day(day, name) or {}
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[cash_driver] день {name} {day}: {e}")
        dd = {}
    extras_all = list(dd.get("extras") or [])
    # Отклонённые наличных не тронули — в пачках их нет, в списке есть.
    extras = [e for e in extras_all if (e.get("status") or "approved") != "rejected"]
    meal = staff.meal_of(dd) if dd.get("working") is not None else 0
    hand = cash_math.piles(mine, extras, meal)
    cash_app = _i(hand["revenue_aed"] + hand["tea"])      # к сдаче: выручка + чай, без своего
    try:
        rdoc = await db.recon_get(day, name)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[cash_driver] сверка {name} {day}: {e}")
        rdoc = None
    recon = shift_recon.view(rdoc, cash_app)
    oid = ((rdoc or {}).get("district") or staff.base_district(name)
           or next((o.get("office_id") for o in mine if o.get("office_id")), "") or "")
    items, tot = _expense_rows(dd, meal)
    return {
        "day": day, "name": name,
        "district": oid, "code": OFFICE_CODES.get(oid, ""), "district_name": OFFICE_NAMES.get(oid, oid),
        "operator": staff.base_operator(oid) if oid else "",
        "opened_at": _iso(dd.get("shift_open_at")), "closed_at": _iso(dd.get("shift_close_at")),
        "working": dd.get("working"),
        "hand": hand, "cash_app": cash_app, "recon": recon,
        "orders": _order_rows(mine),
        "items": items,
        "exp": {"n": len(items), "cash": hand["spent_sum"], "card": hand["card_spent"], "got": hand["got"],
                **tot},
        "tea": hand["tea"], "tea_other": hand["tea_other"], "tea_by": _tea_by(mine),
        "keep": {"meal": meal, "bonus": hand["bonus"]},
        "empty": not mine and not extras_all and dd.get("working") is None and not recon["started"],
    }
