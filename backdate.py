"""
AMBAR — правки задним числом: кто и что менял в закрытом дне.

Приложение умеет показывать любой прошедший день и править его: вчерашний
пересчёт, забытый расход, отметку водителя. Это нужная возможность — смену
закрывают ночью, ошибки находят утром, — но она же и опасная: цифра, на
которую вчера смотрели и по которой считали недостачу, сегодня может стать
другой, и никто об этом не узнает.

Поэтому каждая правка не сегодняшнего дня уходит владельцу в бот. Не «можно
настроить, чтобы уходила» — уходит всегда, мимо настроек уведомлений: это не
сводка, а след в учёте. Сегодняшний день так не сторожим, иначе бот будет
звенеть на обычную работу.

Telegram id в тексте не бывает — только имя или «—»: это правило всего
приложения, и сообщения бота из него не исключение.
"""
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("backdate")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12       # рабочие сутки 12:00 → 12:00, как во всей системе


def biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _human_day(day: str) -> str:
    """«2026-08-28» → «28 августа, четверг». Дата в отчёте должна читаться
    глазами: по «2026-08-28» человек не понимает, какая это была смена."""
    MES = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря")
    DNI = ("понедельник", "вторник", "среда", "четверг", "пятница",
           "суббота", "воскресенье")
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(day or "")
    return f"{d.day} {MES[d.month - 1]}, {DNI[d.weekday()]}"


def _ago(day: str, today: str) -> str:
    try:
        n = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return ""
    if n == 1: return "вчера"
    if n == 2: return "позавчера"
    return f"{n} дней назад" if n > 2 else ""


async def notify(day: str, who: str, what: str, detail: str = "") -> None:
    """Сказать владельцам, что закрытый день кто-то поправил.

    day    — какой день правили (рабочие сутки, YYYY-MM-DD)
    who    — имя человека из телеграма; пусто — «—», но не id
    what   — что за правка одной строкой: «расход водителя», «пересчёт склада»
    detail — подробность, если она есть: сумма, район, позиция

    Сегодняшний день и будущее пропускаем молча: правка текущей смены — это
    просто работа."""
    today = biz_day()
    if not day or str(day) >= today:
        return
    try:
        from owner_routes import notify_owners_force, _md
    except Exception as e:                       # noqa: BLE001
        log.error(f"[backdate] уведомление не отправлено: {e}")
        return
    когда = _ago(day, today)
    строки = [
        "*Правка задним числом*",
        f"{_md(who or '—')} изменил данные за *{_md(_human_day(day))}*"
        + (f" ({когда})" if когда else ""),
        "",
        f"• {_md(what)}" + (f" — {_md(detail)}" if detail else ""),
    ]
    log.info(f"[backdate] {day}: {what} — {who or '—'}")
    try:
        await notify_owners_force("data.backdated", "\n".join(строки))
    except Exception as e:                       # noqa: BLE001
        log.error(f"[backdate] отправка не удалась: {e}")
