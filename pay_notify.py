"""
AMBAR — сообщения водителю о его деньгах: штрафы, удержания, авансы, долги,
премии, зарплата и удержания за бой.

Раньше водитель узнавал о штрафе в день выплаты — из цифры «к выплате», без
объяснений. Спорить в этот момент уже нечем: событие было неделю назад. Теперь
каждое решение старшего уходит в его бот тем же днём: назначили — сообщение,
отменили — сообщение, пересмотрели или вернули — тоже. Что именно, за что,
как удерживается и кто решил.

Правила те же, что у остальных сообщений водителю:
  • скрытый режим (панель «игра») глушит чат — молчим, как tell_driver;
  • каждое отправленное сообщение — в реестр (db.drv_msg_add), иначе штора
    скрытого режима его не сотрёт;
  • адресат — по рабочему имени через config_staff.driver_chats: настоящему
    водителю его аккаунт, тест-водителю тест-аккаунты; операторам и старшим
    (у них нет бота водителя) ничего не уходит.
Ошибка отправки никогда не ломает саму операцию в финансах: сообщение —
следствие решения, а не его часть.
"""
import html
import logging
import os
from datetime import datetime, timezone

import db
import config_staff as staff
import finance_pay as pay

log = logging.getLogger("pay_notify")

MONTHS_N = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль",
            "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
MONTHS_G = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
            "августа", "сентября", "октября", "ноября", "декабря")
# «Штраф отменён», «Удержание отменено»: род слова решает окончание
_NEUTER = {"hold": True}
ICON = {"fine": "⚠️", "hold": "📎", "advance": "💵", "loan": "💳", "bonus": "🎁"}


def month_t(m: str, genitive: bool = False) -> str:
    """'2026-09' → 'сентябрь 2026' / 'сентября 2026'."""
    try:
        y, mm = int(str(m)[:4]), int(str(m)[5:7])
        return f"{(MONTHS_G if genitive else MONTHS_N)[mm - 1]} {y}"
    except (ValueError, IndexError, TypeError):
        return str(m or "")


def day_t(d: str) -> str:
    """'2026-09-14' → '14 сентября'."""
    try:
        return f"{int(str(d)[8:10])} {MONTHS_G[int(str(d)[5:7]) - 1]}"
    except (ValueError, IndexError, TypeError):
        return str(d or "")


def _aed(v) -> str:
    return f"{pay._i(pay._n(v))} AED"


def _e(s) -> str:
    return html.escape(str(s or "").strip())


def _ending(kind: str, past: str) -> str:
    """'отменён' / 'отменено', 'возвращён' / 'возвращено', 'пересмотрен' / 'пересмотрено'."""
    if not _NEUTER.get(kind):
        return past
    return (past[:-2] + "ено") if past.endswith("ён") else past + "о"


def _schedule(item: dict) -> str:
    start = str(item.get("from") or item.get("day") or "")[:7]
    step = pay._i(pay._n(item.get("per_month")))
    if step and step < pay._n(item.get("amount")):
        return f"Удерживается по {step} AED в месяц, начиная с {month_t(start, True)}"
    return f"Удерживается из зарплаты за {month_t(start)}"


def _tail(item: dict, who_line: str, who: str) -> str:
    lines = []
    if item.get("reason"):
        lines.append(_e(item["reason"]))
    if item.get("note"):
        lines.append(f"<i>{_e(item['note'])}</i>")
    if item.get("kind") in pay.MINUS_KINDS:
        lines.append(_schedule(item))
    if who:
        lines.append(f"{who_line}: {_e(who)}")
    return ("\n" + "\n".join(lines)) if lines else ""


def added(item: dict, who: str) -> str:
    kind = item.get("kind") or ""
    t = pay.KINDS.get(kind, "Запись")
    head = f"{t} {_aed(item.get('amount'))}"
    if kind == "advance" and item.get("mode") == "ahead":
        head = (f"Зарплата за {month_t(str(item.get('from') or '')[:7])} наперёд {_aed(item.get('amount'))}"
                f" — выдана {day_t(item.get('day') or '')}")
    elif kind == "advance" and item.get("mode") == "part":
        head = (f"Аванс {_aed(item.get('amount'))} в счёт зарплаты за {month_t(str(item.get('from') or '')[:7])}"
                f" — выдан {day_t(item.get('day') or '')}")
    elif kind in pay.CASH_KINDS:
        head += f" — {'выдан' if kind == 'advance' else 'записан'} {day_t(item.get('day') or '')}"
    elif kind == "bonus" and item.get("tenure"):
        # Премия за стаж — это поздравление, а не строка учёта (владелец,
        # 23 сен 2026: «приходило сообщение обязательно о том, что поздравляем,
        # вам была начислена премия»).
        мес = int(item.get("tenure") or 0) * 6
        ост = мес % 100
        слово = ("месяцев" if 10 < ост < 20 else
                 "месяца" if мес % 10 in (2, 3, 4) else
                 "месяц" if мес % 10 == 1 else "месяцев")
        return (f"🎉 <b>Поздравляем! Премия за стаж {_aed(item.get('amount'))}</b>"
                f"\nВы работаете с нами {мес} {слово} — это ваша премия за стаж."
                # Заметку не повторяем: она о том же самом, что и строка выше.
                + _tail({**item, "note": ""}, "Начислил", who))
    elif kind == "bonus":
        head += f" за {month_t(str(item.get('from') or item.get('day') or '')[:7])}"
    return f"{ICON.get(kind, '•')} <b>{head}</b>" + _tail(item, "Назначил", who)


def cancelled(item: dict, who: str) -> str:
    kind = item.get("kind") or ""
    t = pay.KINDS.get(kind, "Запись")
    if kind == "fine":
        # Штраф не «отменяют», а дают амнистию (владелец, 15 сен 2026).
        return (f"🕊 <b>Амнистия: штраф {_aed(item.get('amount'))} снят</b>"
                + (f"\n{_e(item['reason'])}" if item.get("reason") else "")
                + (f"\nАмнистию дал: {_e(who)}" if who else ""))
    return (f"✅ <b>{t} {_aed(item.get('amount'))} {_ending(kind, 'отменён')}</b>"
            + (f"\n{_e(item['reason'])}" if item.get("reason") else "")
            + (f"\nОтменил: {_e(who)}" if who else ""))


def removed(item: dict, who: str) -> str:
    """Аванс или долг убрали из учёта вместе с выданными деньгами."""
    kind = item.get("kind") or ""
    t = pay.KINDS.get(kind, "Запись")
    return (f"✅ <b>{t} {_aed(item.get('amount'))} убран из учёта</b>"
            + (f"\nУбрал: {_e(who)}" if who else ""))


def edited(old: dict, new: dict, who: str) -> str:
    kind = new.get("kind") or old.get("kind") or ""
    t = pay.KINDS.get(kind, "Запись")
    was, now = pay._i(pay._n(old.get("amount"))), pay._i(pay._n(new.get("amount")))
    if was != now:
        head = f"✏️ <b>{t} {_ending(kind, 'пересмотрен')}: {was} → {now} AED</b>"
    else:
        head = f"✏️ <b>{t} {now} AED: изменены график или комментарий</b>"
    return head + _tail(new, "Пересмотрел", who)


def restored(item: dict, who: str) -> str:
    kind = item.get("kind") or ""
    t = pay.KINDS.get(kind, "Запись")
    return (f"↩️ <b>{t} {_aed(item.get('amount'))} {_ending(kind, 'возвращён')}</b>"
            + (f"\n{_e(item['reason'])}" if item.get("reason") else "")
            + f"\n{_schedule(item)}"
            + (f"\nВернул: {_e(who)}" if who else ""))


def payout(amount, day: str, month: str, note: str, who: str) -> str:
    return (f"💰 <b>Зарплата {_aed(amount)}</b> за {month_t(month)} — выплачена {day_t(day)}"
            + (f"\n<i>{_e(note)}</i>" if note and note != "Зарплата" else "")
            + (f"\nВыдал: {_e(who)}" if who else ""))


async def tell(name: str, text: str, parse_mode: str | None = "HTML") -> int:
    """Отправить водителю по рабочему имени. Возвращает число доставленных."""
    name = (name or "").strip()
    tids = staff.driver_chats(name)
    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if not tids or not token:
        return 0
    try:
        if await db.panic_get(name):
            log.warning(f"[pay] {name} в скрытом режиме — сообщение о деньгах не отправлено")
            return 0
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[pay] проверка скрытого режима {name}: {e}")
    from api_server import tg_send
    n = 0
    for tid in tids:
        try:
            r = await tg_send(token, tid, text, parse_mode=parse_mode)
            mid = ((r or {}).get("result") or {}).get("message_id")
            if not mid:
                log.warning(f"[pay] {name}: телеграм не принял сообщение: {(r or {}).get('description')}")
                continue
            n += 1
            try:
                await db.drv_msg_add(int(tid), int(mid), datetime.now(timezone.utc))
            except Exception as e:                # noqa: BLE001
                log.debug(f"[pay] реестр {tid}: {e}")
        except Exception as e:                    # noqa: BLE001
            log.warning(f"[pay] сообщение {name}: {e}")
    return n


async def tell_safe(name: str, text: str, parse_mode: str | None = "HTML") -> int:
    """То же, но никогда не бросает: решение в финансах важнее сообщения."""
    try:
        return await tell(name, text, parse_mode)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[pay] сообщение {name} не ушло: {e}")
        return 0
