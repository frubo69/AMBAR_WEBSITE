"""Архив сообщений владельца: что считать важным и как это выгрузить.

Переписка с ботом живёт недолго — реестр стирает её раньше, чем телеграм
перестанет это позволять, а по тревоге не остаётся вообще ничего. Ценность при
этом теряться не должна: события лежат в базе, а этот модуль решает, какие из
них — документ, и превращает их в файл, который можно открыть через год.

Что важно: деньги, товар, заявки, доступы и ЧС. Что нет: «новый заказ»,
«доставлен», дайджесты и прочий поток — он полностью восстанавливается из
заказов и в архиве только мешает искать.
"""

import html as _html
import re
from datetime import datetime, timezone, timedelta

DUBAI = timezone(timedelta(hours=4))

# Разделы файла: порядок здесь — порядок в документе.
GROUPS = [
    ("Доступ и безопасность", [
        "security.unauthorized", "delivery.browser",
    ]),
    ("Система", [
        "system.botDown", "system.dbDown", "system.apiErrors", "system.maintenance",
    ]),
    ("Заявки и приёмка", [
        "shift.closed", "supply.done", "supply.flag",
    ]),
    ("Товар", [
        "stock.writeoff", "stock.out", "qr.alien",
    ]),
    ("Деньги", [
        "expenses.request", "finance.revenueLow", "finance.avgDrop",
        "finance.cancelSpike",
    ]),
    ("Правки и отмены заказов", [
        "orders.cancelled", "orders.declined", "orders.reverted",
        "orders.edited", "orders.backfilled", "orders.opFail",
    ]),
    ("Экстренное", [
        "driver.panic", "ops.officeEmpty",
    ]),
    ("Жалобы", [
        "support.complaint", "support.escalation", "reviews.bad3", "reviews.comment",
    ]),
]

TITLES = {
    "security.unauthorized": "Неавторизованный вход в панель",
    "delivery.browser":      "Панель открыта из браузера",
    "system.botDown":        "Бот недоступен",
    "system.dbDown":         "База недоступна",
    "system.apiErrors":      "Ошибки API",
    "system.maintenance":    "Техработы",
    "shift.closed":          "Смена закрыта · заявка",
    "supply.done":           "Приёмка",
    "supply.flag":           "Приёмка — расхождение",
    "stock.writeoff":        "Списание",
    "stock.out":             "Позиция кончилась",
    "qr.alien":              "Чужая бутылка",
    "expenses.request":      "Расход водителя",
    "finance.revenueLow":    "Выручка ниже нормы",
    "finance.avgDrop":       "Средний чек упал",
    "finance.cancelSpike":   "Всплеск отмен",
    "orders.cancelled":      "Заказ отменён",
    "orders.declined":       "Заказ отклонён",
    "orders.reverted":       "Заказ возвращён в работу",
    "orders.edited":         "Заказ изменён",
    "orders.backfilled":     "Заказ внесён задним числом",
    "orders.opFail":         "Сбой у оператора",
    "driver.panic":          "Экстренная ситуация у водителя",
    "ops.officeEmpty":       "Точка без людей",
    "support.complaint":     "Жалоба клиента",
    "support.escalation":    "Эскалация в поддержке",
    "reviews.bad3":          "Низкая оценка",
    "reviews.comment":       "Отзыв с текстом",
}

IMPORTANT = [k for _, keys in GROUPS for k in keys]
_GROUP_OF = {k: g for g, keys in GROUPS for k in keys}


def is_important(event_key: str) -> bool:
    return event_key in _GROUP_OF


def group_of(event_key: str) -> str:
    return _GROUP_OF.get(event_key, "Прочее")


def title_of(event_key: str) -> str:
    return TITLES.get(event_key, event_key or "Событие")


def _local(ts: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(DUBAI)
    except Exception:
        return None


def _fmt_dt(ts: str) -> str:
    d = _local(ts)
    return d.strftime("%d.%m.%Y · %H:%M") if d else str(ts)[:16]


def _body(text: str) -> str:
    """Текст события в html. Пришёл он размеченным для телеграма: *жирное*
    оставляем жирным, остальное — как есть, с сохранением переносов.

    Первую строку выбрасываем, если она целиком жирная: это собственный
    заголовок события, а он уже стоит в шапке карточки."""
    lines = (text or "").split("\n")
    if lines and re.fullmatch(r"\s*[^\w*]*\*[^*]+\*\s*", lines[0] or ""):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    t = _html.escape("\n".join(lines))
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    t = re.sub(r"_([^_\n]+)_", r"<i>\1</i>", t)
    t = t.replace("`", "")
    # Первая строка почти всегда заголовок самого события — она станет шапкой.
    return t.replace("\n", "<br>")


_CSS = """
:root{--bg:#0d0d10;--card:#15151a;--line:#26262e;--txt:#ecebe6;--sub:#8e8d88;
  --gold:#c9a227;--gold-s:#e0bd4a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font:15px/1.55 -apple-system,'SF Pro Text','Segoe UI',Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:40px 20px 80px}
.mast{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:30px}
.mast h1{margin:0;font-size:26px;letter-spacing:5px;font-weight:800;color:var(--gold-s)}
.mast .sub{margin-top:8px;color:var(--sub);font-size:13.5px}
.sum{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0 6px}
.sum a{display:flex;gap:8px;align-items:baseline;text-decoration:none;
  padding:8px 13px;border:1px solid var(--line);border-radius:11px;
  background:var(--card);color:var(--txt);font-size:13px}
.sum a b{color:var(--gold-s);font-variant-numeric:tabular-nums}
h2{margin:38px 0 14px;font-size:13px;letter-spacing:2.4px;text-transform:uppercase;
  color:var(--gold);font-weight:700}
.it{border:1px solid var(--line);background:var(--card);border-radius:14px;
  padding:14px 16px;margin-bottom:10px}
.it-h{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
  margin-bottom:7px}
.it-t{font-size:13px;font-weight:700;letter-spacing:.2px}
.it-d{font-size:12px;color:var(--sub);white-space:nowrap;font-variant-numeric:tabular-nums}
.it-b{font-size:14px;color:#d8d7d2;word-wrap:break-word}
.foot{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--sub);font-size:12px;line-height:1.7}
.empty{color:var(--sub);font-size:13.5px;padding:10px 0}
@media print{
  :root{--bg:#fff;--card:#fff;--line:#ddd;--txt:#111;--sub:#666;--gold:#8a6f16;--gold-s:#8a6f16}
  .it{break-inside:avoid;border-color:#e2e2e2}
  .wrap{padding:0}
}
"""


def render_html(items: list, frm: str = "", to: str = "",
                generated_by: str = "") -> str:
    """Самодостаточный файл: открывается где угодно и печатается в PDF."""
    by_group: dict = {g: [] for g, _ in GROUPS}
    for it in items:
        g = group_of(it.get("event_key", ""))
        by_group.setdefault(g, []).append(it)
    for g in by_group:
        by_group[g].sort(key=lambda x: x.get("created_at", ""), reverse=True)

    now = datetime.now(DUBAI).strftime("%d.%m.%Y · %H:%M")
    period = "за всё время"
    if frm or to:
        period = f"{_fmt_dt(frm)[:10] if frm else '…'} — {_fmt_dt(to)[:10] if to else '…'}"

    sums = "".join(
        f'<a href="#g{i}"><span>{_html.escape(g)}</span><b>{len(by_group.get(g) or [])}</b></a>'
        for i, (g, _) in enumerate(GROUPS) if by_group.get(g))

    parts = []
    for i, (g, _) in enumerate(GROUPS):
        rows = by_group.get(g) or []
        if not rows:
            continue
        parts.append(f'<h2 id="g{i}">{_html.escape(g)} · {len(rows)}</h2>')
        for it in rows:
            parts.append(
                '<div class="it"><div class="it-h">'
                f'<div class="it-t">{_html.escape(title_of(it.get("event_key","")))}</div>'
                f'<div class="it-d">{_fmt_dt(it.get("created_at",""))}</div></div>'
                f'<div class="it-b">{_body(it.get("text",""))}</div></div>')

    if not parts:
        parts.append('<div class="empty">За выбранный период важных событий не было.</div>')

    who = f" · {_html.escape(generated_by)}" if generated_by else ""
    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>AMBAR — архив важных событий</title><style>{_CSS}</style></head><body>"
        '<div class="wrap"><div class="mast"><h1>A M B A R</h1>'
        f'<div class="sub">Архив важных событий · {_html.escape(period)}<br>'
        f'Выгружено {now}{who}</div></div>'
        f'<div class="sum">{sums}</div>'
        + "".join(parts) +
        '<div class="foot">Документ собран из журнала событий AMBAR. Поток вроде '
        '«новый заказ» и «доставлен» сюда намеренно не попадает — он полностью '
        'восстанавливается из заказов.<br>Копия переписки в телеграме живёт не '
        'дольше двух суток: важное хранится здесь и в резервных копиях базы.</div>'
        "</div></body></html>")
