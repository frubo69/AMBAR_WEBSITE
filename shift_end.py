"""Конец смены: что происходит, когда закрылся последний район.

Смысл момента
-------------
Операторы работают до утра. Когда последний из них говорит «на сегодня всё»,
про день становится известно главное: продажи окончательны. До этой секунды
любая цифра — предварительная, и заявку по ней собирать нельзя: приедет ещё
один заказ, и на полке останется на три бутылки меньше, чем мы просили.

Поэтому здесь и стоит сборка заявки. Старший просыпается примерно в это время
и должен получить не «доброе утро», а готовый файл: смена закрыта, продано
столько-то, вот чего не хватает до нормы — отправляй в магазин.

Ровно один раз
--------------
Закрыться последним может любой район, а собрать заявку нужно однажды. Кто
первым поставил пометку дня, тот и собирает; остальным пометка не даётся, и
они молча ничего не делают. Проверять «а не собирали ли уже» перед сборкой
нельзя: два района закрываются в одну секунду чаще, чем кажется.

Чего здесь нет
--------------
Отправки в магазин. Файл кладётся старшему в переписку, а дальше он сам
решает, кому и когда его переслать: у магазина бывает выходной, бывает другой
номер, бывает разговор перед заявкой. Автоматическая отправка сэкономила бы
одно движение и отняла бы этот выбор.
"""
import logging

import db
from config_offices import OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger("shift")


def _md(s: str) -> str:
    return str(s or "").replace("*", "").replace("_", "").replace("`", "")


def _fmt(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


async def on_all_closed(day: str, state: dict) -> bool:
    """Последний район закрылся. True — мы и собрали заявку."""
    first = await db.shift_day_mark(day, "order_built")
    if not first:
        log.info(f"[shift] {day}: заявку уже собрал кто-то другой")
        return False

    dd = state.get("districts") or []
    orders = sum(d["orders"] for d in dd)
    revenue = sum(d["revenue"] for d in dd)
    hanging = sum(d["open"] for d in dd)

    rows = "\n".join(
        f"• {_md(d['code'])} {_md(d['name'])} — {d['orders']} зак. · {_fmt(d['revenue'])} AED"
        + (f" · висит {d['open']}" if d["open"] else "")
        for d in dd)

    head = (f"*Смена закрыта — {day}*\n"
            f"Закрылись все районы. Продажи дня окончательны.\n\n"
            f"{rows}\n\n*Итого: {orders} заказов · {_fmt(revenue)} AED*")
    if hanging:
        head += (f"\n\n_Незакрытых заказов: {hanging}. Они не попали в этот итог "
                 f"и не учтены в заявке._")

    # ── заявка ──────────────────────────────────────────────────────────────
    # Собирается по свежести пересчёта: если район считали позавчера, заявка по
    # нему — фантазия, и старший должен знать это раньше, чем отправит файл.
    file_note = ""
    raw = name = None
    try:
        import supply_routes
        raw, name = await supply_routes._build_book(day)
        data = await supply_routes._order_rows(day)
        stale = [d for d in data.get("districts", []) if d.get("counted") != day]
        if stale:
            file_note = "\n\n⚠️ " + ", ".join(
                f"{_md(d['code'])}: пересчёт {_md(d.get('counted') or 'не делали')}"
                for d in stale) + "\nПо этим районам заявка посчитана по старым остаткам."
        head += (f"\n\n*Заявка собрана:* {data.get('total_qty', 0)} бутылок · "
                 f"{_fmt(data.get('total_aed', 0))} AED{file_note}")
    except Exception as e:
        log.error(f"[shift] заявка не собралась: {e}")
        head += "\n\n⚠️ Заявку собрать не удалось — соберите вручную в «Учёте»."

    from owner_routes import notify_owners
    await notify_owners("shift.closed", head)

    if raw:
        ids = []
        try:
            ids = await db.get_all_manager_ids()
        except Exception as e:
            log.error(f"[shift] получатели файла: {e}")
        for uid in ids:
            try:
                ok, err = await supply_routes._send_doc(
                    uid, raw, name, f"Заявка в магазин · {day}")
                if not ok:
                    log.warning(f"[shift] файл {uid}: {err}")
            except Exception as e:
                log.error(f"[shift] файл {uid}: {e}")
    log.info(f"[shift] {day}: смена закрыта у всех · {orders} заказов · {revenue} AED")
    return True
