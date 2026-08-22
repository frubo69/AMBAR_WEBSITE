"""Чистильщик переписки AMBAR STAR.

Телеграм разрешает боту удалить своё сообщение только в первые двое суток.
Значит копить их нельзя: то, что старше, в тревожный момент уже не убрать
никакой кнопкой. Поэтому раз в несколько минут стираем всё, чему больше
сорока семи часов, — с запасом в час на сбой сети и на то, что свипер спал.

Данные при этом никуда не деваются: текст события лежит в owner_notifications,
важное собирается в архив и попадает в резервные копии базы. Уходит только
копия в телеграме — ровно то, что и должно уходить.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import db

log = logging.getLogger("ambar")

TTL_HOURS = 47
EVERY_SEC = 300


async def sweep_once() -> int:
    from api_server import tg_delete
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not token:
        return 0
    edge = (datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS)).isoformat()
    rows = await db.owner_msgs_due(edge, limit=300)
    gone = 0
    for r in rows:
        cid, mid = r.get("chat_id"), r.get("message_id")
        try:
            res = await tg_delete(token, cid, mid)
            if (res or {}).get("ok"):
                gone += 1
        except Exception:
            pass
        # Из реестра убираем в любом случае: если телеграм уже отказал, второй
        # заход ничего не изменит, а очередь копилась бы вечно.
        await db.owner_msg_drop(cid, mid)
    return gone


async def wipe_chat(chat_id: int) -> int:
    """Снести всё, что помним по этому чату. Вызывается по тревоге."""
    from api_server import tg_delete
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not token or not chat_id:
        return 0
    gone = 0
    for r in await db.owner_msgs_of(int(chat_id)):
        mid = r.get("message_id")
        try:
            res = await tg_delete(token, chat_id, mid)
            if (res or {}).get("ok"):
                gone += 1
        except Exception:
            pass
        await db.owner_msg_drop(int(chat_id), mid)
    return gone


async def loop(app=None):
    await asyncio.sleep(20)          # даём подняться остальному
    while True:
        try:
            n = await sweep_once()
            if n:
                log.info(f"[owner-sweep] убрано из чата владельца: {n}")
        except Exception as e:
            log.warning(f"[owner-sweep] сбой: {e}")
        await asyncio.sleep(EVERY_SEC)
