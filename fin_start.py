"""«Учёт с» — день, с которого ведётся книга финансов.

Владелец, 14 окт 2026: октябрь никто не вёл (ни одной отметки «Получил», ни
одного подтверждённого дня, ни одной оплаты Барракуде), восстанавливать
двенадцать дней по памяти нечем — учёт запускается чистым стартом с утра
выбранного дня. Всё, что раньше, — «до учёта»: дни не ждут подтверждения и в
стопки сейфа не входят, сбор выручки считается с этого дня, крипта в РП+ идёт
с этого дня. Открытие месяца («На начало») при этом значит «на утро дня
старта» — его вписывают по пересчёту сейфа.

Одна настройка на всё приложение (app_settings['fin_start']), читается с
кэшем; синхронным местам (книга кошелька) отдаётся последнее прочитанное.
"""
import logging
import time
from datetime import datetime, timezone

import db

log = logging.getLogger("fin_start")

KEY = "fin_start"
_TTL = 30.0
_cache: dict = {"day": None, "at": 0.0, "loaded": False}


def _valid(day) -> str | None:
    s = str(day or "").strip()
    if len(s) != 10:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


async def get() -> str | None:
    """День старта или None — учёт идёт с начала времён."""
    if _cache["loaded"] and time.time() - _cache["at"] < _TTL:
        return _cache["day"]
    try:
        doc = await db.setting_get(KEY)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fin_start] не прочитан: {e}")
        return _cache["day"]
    _cache.update(day=_valid((doc or {}).get("day")), at=time.time(), loaded=True)
    return _cache["day"]


def cached() -> str | None:
    """Последнее прочитанное — для синхронного кода."""
    return _cache["day"]


async def load() -> str | None:
    _cache["loaded"] = False
    return await get()


async def set_day(day: str | None, who: str = "") -> str | None:
    """Назначить день старта (None — снять). Возвращает записанное."""
    d = _valid(day) if day else None
    if day and not d:
        raise ValueError("bad_day")
    if d:
        await db.setting_set(KEY, {"day": d, "by": who, "at": datetime.now(timezone.utc)})
    else:
        await db.setting_set(KEY, {"day": None, "by": who, "at": datetime.now(timezone.utc)})
    _cache.update(day=d, at=time.time(), loaded=True)
    log.info(f"[fin_start] учёт с {d or '—'} · {who or '—'}")
    return d


def before(day: str, start: str | None) -> bool:
    """День раньше начала учёта."""
    return bool(start) and str(day or "") < start
