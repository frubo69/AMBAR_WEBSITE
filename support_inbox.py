"""Единая точка приёма обращений клиента.

Написать нам можно тремя способами: чат внутри приложения, бот поддержки и
основной бот. В базу попадал только первый — остальные два жили пересылками в
чате админов или не жили вообще, и панель оператора их не видела. Здесь всё
сводится в одну переписку на клиента: где бы человек ни написал, обращение
одно, история одна, и ответ уходит туда, где он писал последним.
"""

import os
from datetime import datetime, timezone

import aiohttp

import db

CHANNEL_APP     = "app"      # чат внутри мини-аппа
CHANNEL_SUPPORT = "bot"      # @ambar_support_bot
CHANNEL_MAIN    = "mainbot"  # основной бот AMBAR

# Читаем окружение на вызове, а не на импорте: модуль подключают и до
# load_dotenv(), и тогда токены оказались бы пустыми навсегда.
def _support_token() -> str:
    return os.getenv("SUPPORT_BOT_TOKEN", "")


def _main_token() -> str:
    return os.getenv("BOT_TOKEN", "")


def _operator_ids() -> list:
    return [int(x) for x in os.getenv("OPERATOR_IDS", "").split(",")
            if x.strip().isdigit()]

_CHANNEL_LABEL = {
    CHANNEL_SUPPORT: "🤖 Бот поддержки",
    CHANNEL_MAIN:    "🤖 Основной бот",
}


def conv_key(uid: int) -> str:
    """Тот же ключ, что у «Общего вопроса» в приложении: переписка одна."""
    return f"{uid}_general"


def status_line(u: dict | None) -> str:
    """Кто пишет. Чаще всего это человек, застрявший на верификации, — без
    этой строки оператор видит голое «Hello» и не понимает, чего от него ждут."""
    u = u or {}
    tags = []
    if u.get("is_banned") or u.get("banned"):
        tags.append("🚫 Забанен")
    if u.get("verify_declined"):
        tags.append("❌ Верификация отклонена")
    elif u.get("verify_requested") and not u.get("verified"):
        tags.append("⏳ Ждёт верификации")
    elif not u.get("verified") and not int(u.get("orders_done", 0) or 0):
        tags.append("🔴 Не верифицирован")
    return " | ".join(tags)


async def capture(uid: int, *, channel: str, text: str = "",
                  photo_url: str = "", caption: str = "") -> tuple[str, dict]:
    """Сохранить входящее клиента. Возвращает (ключ переписки, сообщение)."""
    key = conv_key(uid)
    ts = datetime.now(timezone.utc).isoformat()
    if photo_url:
        msg = {"role": "user", "type": "photo", "url": photo_url,
               "caption": caption or "", "ts": ts, "via": channel}
    else:
        msg = {"role": "user", "type": "text",
               "text": text or caption or "(файл)", "ts": ts, "via": channel}
    await db.append_support_msg(key, msg)
    await db.support_set_channel(key, channel)
    return key, msg


async def _tg(token: str, method: str, payload: dict) -> dict:
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload) as r:
                return await r.json()
    except Exception as e:
        print(f"⚠️ tg {method} failed: {e}")
        return {}


async def send_as_support(uid: int, text: str) -> dict:
    return await _tg(_support_token(), "sendMessage", {"chat_id": uid, "text": text})


async def send_as_main(uid: int, text: str) -> dict:
    return await _tg(_main_token(), "sendMessage", {"chat_id": uid, "text": text})


async def notify_operators(uid: int, user_doc: dict | None, text: str,
                           channel: str, key: str):
    """Разослать обращение операторам так же, как это делает приложение, и
    запомнить связку сообщение→клиент: ответ реплаем в телеграме должен
    работать и после перезапуска ботов."""
    u = user_doc or {}
    name = (u.get("first_name") or u.get("name") or str(uid)).strip()
    uname = u.get("username") or "—"
    st = status_line(u)
    header = (f"{_CHANNEL_LABEL.get(channel, '🤖 Бот')}\n"
              f"👤 {name} (@{uname}, ID: {uid})\n"
              + (f"{st}\n" if st else "")
              + f"\n💬 {text}")
    for op_id in _operator_ids():
        r = await _tg(_support_token(), "sendMessage",
                      {"chat_id": op_id, "text": header})
        mid = (r.get("result") or {}).get("message_id")
        if mid:
            try:
                await db.save_support_map_entry(str(mid), {
                    "user_id": uid, "conv_key": key,
                    "order_id": "", "channel": channel,
                })
            except Exception as e:
                print(f"⚠️ DB map save failed: {e}")
