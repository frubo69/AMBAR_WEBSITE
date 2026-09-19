"""Наш курс приёма валюты у клиентов (владелец, 19 сен 2026: «для клиентов у
нас другой курс, наш внутренний, он есть у водителей — у раздела курсы добавь
ещё наш внутренний курс, чтобы мы могли его посмотреть и менять»).

По нему водитель называет клиенту сумму в валюте (меню «Валютой» на карточке
заказа), и в момент выбора он замораживается на заказе (driver_routes.handle_fx):
поменяли курс — меняются новые выборы, уже выбранные на заказах остаются как
были. Это не курс обменника (rates.py) — это наша цена, и решает её владелец.

Живёт в базе (fx_take), по умолчанию — числа, что стояли в коде с 15 сен 2026;
каждое изменение — в журнал (fx_take_log): кто, когда, с какого на какое.
Список валют — прежние четыре: ими и платят.
"""
from __future__ import annotations

import logging
import time as _t

import db

log = logging.getLogger(__name__)

DEFAULT = [
    {"code": "USD", "name": "Доллар США", "rate": 3.5},
    {"code": "EUR", "name": "Евро", "rate": 4.0},
    {"code": "GBP", "name": "Фунт стерлингов", "rate": 4.5},
    {"code": "SAR", "name": "Риал", "rate": 1.0},
]
CODES = [r["code"] for r in DEFAULT]
# Дирхамов за единицу: риял — около одного, фунт — около пяти. Выше пятидесяти
# не бывает ни у одной из четырёх — это опечатка (35 вместо 3.5).
RATE_MAX = 50.0
TTL = 30.0                  # список заказов водителя опрашивается раз в 5 с —
                            # база не должна отвечать на каждый опрос

_CACHE: dict = {"at": 0.0, "rows": None}


def drop() -> None:
    _CACHE["at"] = 0.0


async def rates() -> list:
    """[{code, name, rate, by_name, at}] — вписанное в STAR поверх умолчаний."""
    now = _t.monotonic()
    if _CACHE["rows"] is not None and now - _CACHE["at"] < TTL:
        return [dict(r) for r in _CACHE["rows"]]
    try:
        saved = await db.fx_take_all()
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fx_take] курс для клиентов не прочитан, беру по умолчанию: {e}")
        saved = {}
    rows = []
    for r in DEFAULT:
        s = saved.get(r["code"]) or {}
        try:
            v = float(s.get("rate") or 0)
        except (TypeError, ValueError):
            v = 0.0
        rows.append({**r, "rate": v if 0 < v <= RATE_MAX else r["rate"],
                     "by_name": s.get("by_name") or "", "at": s.get("at") or ""})
    _CACHE.update(at=now, rows=rows)
    return [dict(r) for r in rows]


async def set_rate(code: str, rate, who: str = "") -> tuple:
    """(ok, строка или ошибка). Меняет курс одной валюты и пишет в журнал."""
    code = str(code or "").strip().upper()
    if code not in CODES:
        return False, "unknown_code"
    try:
        v = round(float(str(rate).replace(",", ".")), 4)
    except (TypeError, ValueError):
        return False, "bad_rate"
    if not (0 < v <= RATE_MAX):
        return False, "bad_rate"
    was = next((r["rate"] for r in await rates() if r["code"] == code), None)
    await db.fx_take_put(code, v, who)
    try:
        await db.fx_take_log_add({"code": code, "was": was, "rate": v, "by_name": who})
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[fx_take] журнал не записан: {e}")
    drop()
    log.info(f"[fx_take] курс для клиентов {code}: {was} → {v} · {who or '—'}")
    row = next(r for r in await rates() if r["code"] == code)
    return True, {**row, "was": was}
