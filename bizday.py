"""Учётные сутки и день заказа — одно правило на всё приложение.

Сутки идут с полудня до полудня (Дубай): заказ, доставленный в 02:00, — это
ещё вчерашняя смена. Но день заказа считается не по моменту, когда клиент его
создал, а по моменту, когда его ВЗЯЛИ В РАБОТУ (confirmed_at): заказ,
пришедший в 10:30 и принятый после открытия смены в 12:08, принадлежит этой
смене, а не вчерашней, которую уже закрыли и посчитали (владелец, 11 сен 2026:
«улетел во вчера, и это путает»).

Если смену района за учётные сутки уже закрыли, а заказ взяли в работу после
этого (утро до полудня), он подписывается следующим днём — поле `day` на
заказе ставит db.order_day_now при принятии. Оно главнее любого расчёта.

Окна выборок по timestamp расширяются назад на сутки (PAD): заказ, созданный
до границы суток, но принятый после неё, иначе не попал бы в выборку дня.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = int(os.getenv("AMBAR_SHIFT_START_HOUR", "12"))
PAD = timedelta(hours=24)


def biz_day(ref: datetime | None = None) -> str:
    """Учётные сутки момента (по умолчанию — сейчас), 'YYYY-MM-DD'."""
    ref = ref or datetime.now(DUBAI_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ref = ref.astimezone(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def day_start(day: str) -> datetime:
    """Начало учётных суток (Дубай, aware)."""
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)


def next_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def parse_ts(ts) -> datetime | None:
    """Момент из заказа: ISO-строка (UTC без Z, с Z или со смещением) или
    datetime. Наивное время считается UTC."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def day_of(ts) -> str | None:
    dt = parse_ts(ts)
    return biz_day(dt) if dt else None


def order_at(o: dict):
    """Момент, которым заказ относится к смене: принят — или создан, если
    принять ещё не успели."""
    return o.get("confirmed_at") or o.get("timestamp")


def order_day(o: dict) -> str | None:
    """День заказа: подписанный при принятии, иначе по моменту принятия."""
    d = o.get("day")
    if isinstance(d, str) and len(d) == 10:
        return d
    return day_of(order_at(o))


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "")


def since_utc(day: str) -> str:
    """Нижняя граница выборки по timestamp для дня (и всего после него):
    начало суток минус запас."""
    return _utc_iso(day_start(day) - PAD)


def window_utc(day_from: str, day_to: str) -> tuple[str, str]:
    """(с, до) по timestamp для отрезка учётных дней включительно."""
    return _utc_iso(day_start(day_from) - PAD), _utc_iso(day_start(day_to) + timedelta(days=1))


def in_days(o: dict, day_from: str, day_to: str) -> bool:
    d = order_day(o)
    return bool(d) and day_from <= d <= day_to
