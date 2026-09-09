"""Напоминание о товаре, принятом без сканирования.

Зачем
-----
Водитель или старший забрал район из магазина и не читал коды: ящики в
машине, ехать надо. Это разрешено — но это не приёмка, а долг: бутылки стоят
на полке, а реестр о них не знает. Задача остаётся открытой, поставка — тоже,
и в чек-листе у старшего горит строка.

Чтобы долг не залёживался, старшему раз в час приходит одно сообщение в
AMBAR STAR: какие районы приняты без кодов и сколько там бутылок. Кнопка под
ним открывает приёмку — досканировать можно прямо оттуда, а может и водитель
из своего списка.

Когда молчим
------------
Ночью, с часу до семи по Дубаю: в это время у полки никого нет. Первый час
после приёмки — тоже: о ней уже сказано отдельным сообщением в ту же минуту.
Всё отсканировано — молчим и убираем последнее напоминание из чата: оно
больше не про что.

Одно живое сообщение на адресата: перед новым старое удаляется, и в реестре
владельца (owner_msgs) оно числится, как всё, что бот пишет в AMBAR STAR, —
иначе штора его не уберёт.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import db

log = logging.getLogger("supply-nag")

DUBAI_TZ = timezone(timedelta(hours=4))

# Выключено 9 сен 2026 по просьбе владельца: напоминание приходило раз в час и
# на большом недосканированном районе висело в чате сутками. Код цел и никуда
# не убран — вернуть значит поставить True (или AMBAR_NOSCAN_NAG=1 в .env на
# сервере) и перезапустить ambar-api. Чек-лист смены строку по-прежнему
# показывает: долг виден, просто бот о нём не пишет.
ON = os.getenv("AMBAR_NOSCAN_NAG", "").strip().lower() in ("1", "true", "yes", "on")

EVERY_MIN = 60
QUIET_FROM, QUIET_TO = 1, 7          # часы по Дубаю, когда не пишем

_LAST = {}                           # chat_id → {"at": datetime, "mid": int}


def _dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "").replace(" ", "T"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _plural(n, one, few, many):
    n = abs(int(n or 0)) % 100
    d = n % 10
    if 10 < n < 20: return many
    if 1 < d < 5:   return few
    if d == 1:      return one
    return many


def _text(tasks: list) -> str:
    from owner_routes import _md
    lines = ["⏳ *Приёмка без сканирования*"]
    for t in tasks:
        when = _dt(t.get("at"))
        где = f"{t['code']} {t['name']}".strip()
        if t.get("extra") and t.get("base"):
            где += f" · {t['base']}"
        row = f"{_md(где)} · {t['left']} {_plural(t['left'], 'бутылка', 'бутылки', 'бутылок')}"
        if t.get("by"):
            row += f" · {_md(t['by'])}"
        if when:
            row += f" · с {when.astimezone(DUBAI_TZ).strftime('%H:%M')}"
        lines.append(row)
    return "\n".join(lines)


def _keyboard() -> dict:
    url = os.getenv("OWNER_WEBAPP_URL", "https://owner.ambar-delivery.com/")
    url += ("&" if "?" in url else "?") + "go=supply"
    return {"inline_keyboard": [[{"text": "Открыть приёмку", "web_app": {"url": url}}]]}


async def _drop(token: str, chat_id: int):
    """Убрать прошлое напоминание — из чата и из реестра."""
    from api_server import tg_delete
    prev = _LAST.pop(chat_id, None)
    if prev and prev.get("mid"):
        try:
            await tg_delete(token, chat_id, prev["mid"])
        except Exception:
            pass
        try:
            await db.owner_msg_drop(int(chat_id), int(prev["mid"]))
        except Exception:
            pass


async def _ping(token: str, chat_id: int, text: str, now: datetime) -> bool:
    from api_server import tg_send
    await _drop(token, chat_id)
    res = await tg_send(token, chat_id, text, reply_markup=_keyboard())
    if res and not res.get("ok") and "parse" in str(res.get("description", "")).lower():
        res = await tg_send(token, chat_id, text.replace("*", ""), parse_mode=None,
                            reply_markup=_keyboard())
    mid = ((res or {}).get("result") or {}).get("message_id")
    _LAST[chat_id] = {"at": now, "mid": mid}
    # tg_send сам кладёт сообщение в реестр владельца (_remember_owner_msg).
    return bool(mid)


async def tick(now: datetime = None) -> dict:
    """Один проход. Возвращает, что нашли, — этим же пользуется проверка."""
    now = now or datetime.now(timezone.utc)
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not ON:
        # Выключили при живом сообщении в чате — снимаем его: обновлять его
        # больше некому, а висеть оно будет как настоящее.
        for cid in list(_LAST):
            if token:
                await _drop(token, cid)
            else:
                _LAST.pop(cid, None)
        return {"tasks": 0, "skip": "выключено"}
    import supply_routes
    tasks = await supply_routes.noscan_tasks()
    if not tasks:
        # Долг погашен — последнее напоминание больше не про что.
        for cid in list(_LAST):
            if token:
                await _drop(token, cid)
            else:
                _LAST.pop(cid, None)
        return {"tasks": 0, "sent": 0}
    local = now.astimezone(DUBAI_TZ)
    if QUIET_FROM <= local.hour < QUIET_TO:
        return {"tasks": len(tasks), "skip": "ночь"}
    if not token:
        return {"tasks": len(tasks), "skip": "нет токена"}
    # Первый час после приёмки — тишина: о ней сказано в ту же минуту.
    first = min((_dt(t.get("at")) for t in tasks if _dt(t.get("at"))), default=None)
    if first and (now - first).total_seconds() < EVERY_MIN * 60:
        return {"tasks": len(tasks), "skip": "рано"}
    try:
        ids = await db.get_all_manager_ids()
    except Exception as e:
        log.error(f"[supply-nag] владельцы не прочитаны: {e}")
        return {"tasks": len(tasks), "skip": "нет адресатов"}
    text = _text(tasks)
    sent = 0
    for cid in ids:
        prev = _LAST.get(cid)
        if prev and (now - prev["at"]).total_seconds() < EVERY_MIN * 60:
            continue
        if await _ping(token, int(cid), text, now):
            sent += 1
    if sent:
        log.info(f"[supply-nag] районов без кодов {len(tasks)}, послано {sent}")
    return {"tasks": len(tasks), "sent": sent}


async def loop(app=None):
    """Раз в минуту смотрим, пора ли. Сама частота — час, в tick."""
    await asyncio.sleep(30)            # дать серверу подняться
    if not ON:
        log.info("[supply-nag] выключен (AMBAR_NOSCAN_NAG не задан) — не пишем")
    while True:
        try:
            await tick()
        except Exception as e:
            log.error(f"[supply-nag] {e}")
        await asyncio.sleep(60)
