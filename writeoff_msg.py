"""Согласование списаний — тексты и кнопки, общие для API и для бота владельца.

Решение по списанию принимается в двух местах: кнопкой в мини-аппе (через
`stock_routes`) и кнопкой прямо под фотографией в чате с ботом (`owner_bot`).
Это разные процессы, и если бы каждый писал свой текст, они бы разъехались
через месяц: в чате «принято», в приложении «согласовано», а в истории третье
слово. Поэтому весь текст живёт здесь, а импорт этого модуля ничего тяжёлого
за собой не тянет — ни базы, ни aiohttp.
"""
import html

# Решение прилетает кнопкой, а кнопка возвращает строку. Формат один на всех:
# wo:<решение>:<id списания>. Идентификатор — двенадцать шестнадцатеричных
# знаков, в шестьдесят четыре байта телеграма это влезает с запасом.
CB_PREFIX = "wo"


def cb(wid: str, ok: bool) -> str:
    return f"{CB_PREFIX}:{'ok' if ok else 'no'}:{wid}"


def parse_cb(data: str):
    """('id', True) — согласовать, ('id', False) — отклонить, None — не наше."""
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != CB_PREFIX or parts[1] not in ("ok", "no"):
        return None
    return parts[2], parts[1] == "ok"


def keyboard(wid: str) -> dict:
    """Две кнопки под фотографией. Больше здесь быть не должно: решение
    двоичное, а «разберусь позже» — это просто не нажимать."""
    return {"inline_keyboard": [[
        {"text": "✅ Согласовать", "callback_data": cb(wid, True)},
        {"text": "🚫 Отклонить",  "callback_data": cb(wid, False)},
    ]]}


def caption(name: str, qty: int, kind: str, who: str, code: str,
            note: str = "", aed: float = 0) -> str:
    """Подпись к фотографии. HTML, а не Markdown: в названиях товара и в
    комментарии водителя живут любые символы, а незакрытая звёздочка роняет
    сообщение целиком — телеграм не покажет его вовсе."""
    e = html.escape
    lines = [f"🗑 <b>Списание · {e(kind)}</b>",
             f"{e(name)} × {qty}" + (f" · {int(aed)} AED" if aed else ""),
             f"{e(who)}" + (f" ({e(code)})" if code else "")]
    if note:
        lines.append(f"<i>{e(note)}</i>")
    lines.append("")
    lines.append("Пока не согласовано — со склада не вычитается.")
    return "\n".join(lines)


def decided_caption(base: str, ok: bool, by_name: str = "") -> str:
    """Та же подпись после решения. Кнопки убираем, а строку оставляем: чат
    владельцев — это ещё и история, и «кто разрешил» в ней должно быть видно
    без захода в приложение."""
    head = base.split("\nПока не согласовано")[0].rstrip()
    who = f" · {html.escape(by_name)}" if by_name else ""
    return head + ("\n\n✅ <b>Согласовано</b>" if ok
                   else "\n\n🚫 <b>Отклонено</b> — остаётся недостачей") + who


def driver_text(name: str, qty: int, ok: bool, note: str = "") -> str:
    """Ответ водителю. Он ждёт решения, а не молчания: пока списание висит, он
    не знает, зачтён ему бой или его спросят за него в конце смены."""
    if ok:
        return f"Списание принято: {name} × {qty}."
    return (f"Списание отклонено: {name} × {qty}."
            + (f"\n{note}" if note else "")
            + "\nЭти бутылки остаются на вас как недостача — подойдите к старшему.")
