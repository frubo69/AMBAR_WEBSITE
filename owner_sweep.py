"""Чистильщик переписки: AMBAR STAR, водители, операторы.

Телеграм разрешает боту удалить своё сообщение только в первые двое суток.
Значит копить их нельзя: то, что старше, в тревожный момент уже не убрать
никакой кнопкой.

Но двое суток — это про предел телеграма, а не про то, сколько переписке
положено жить. Живёт она восемь часов: смена короче, а сообщение, которому
больше, ничего уже не решает — оно только лежит в телефоне и ждёт, когда его
прочитает не тот человек. Поэтому чистим по восьми часам, а сорок восемь
остаются тем, чем и были: границей, за которой поздно.

Чистим три переписки одним проходом, каждую своим ботом:
    AMBAR STAR  — реестр owner_msgs, токен владельца;
    водители    — реестр driver_msgs, токен водительского бота;
    операторы   — тот же driver_msgs (ключ там чат, а не человек),
                  токен операторского.

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

TTL_HOURS = 8          # сколько живёт переписка
EVERY_SEC = 300


# Сколько номеров ниже известного добираем подряд. Номера в личном чате идут
# по порядку; всё, что в этом окне не наше или уже стёрто, телеграм пропускает.
RANGE = 400


async def _range_delete(token: str, chat_id: int, lo: int, hi: int, keep=()) -> int:
    """Стереть все номера от lo до hi включительно, кроме keep."""
    from api_server import tg_delete_many
    if hi < lo:
        return 0
    ids = [i for i in range(max(1, lo), hi + 1) if i not in keep]
    return await tg_delete_many(token, chat_id, ids) if ids else 0


async def sweep_once() -> int:
    from api_server import tg_delete, tg_deleted
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not token:
        return 0
    edge = (datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS)).isoformat()
    rows = await db.owner_msgs_due(edge, limit=300)
    gone = 0
    top = {}                       # чат → самый старший номер из просроченных
    for r in rows:
        cid, mid = r.get("chat_id"), r.get("message_id")
        res = None
        try:
            res = await tg_delete(token, cid, mid)
        except Exception:
            res = None
        if tg_deleted(res):
            if (res or {}).get("ok"):
                gone += 1
            # Из реестра убираем только когда сообщения точно нет: стёрли, или
            # телеграм говорит, что его не существует. Сбой сети или лимит —
            # строка остаётся, и следующий проход попробует снова. Раньше её
            # выбрасывали в любом случае, и сообщение после этого жило в чате
            # до конца двух суток, а дальше его нельзя было стереть никому.
            await db.owner_msg_drop(cid, mid)
        else:
            log.warning(f"[owner-sweep] {cid}/{mid} не удалилось: {(res or {}).get('description')}")
        top[int(cid)] = max(top.get(int(cid), 0), int(mid or 0))
    # Диапазоном ниже самого старшего просроченного: всё под ним ещё старее,
    # и в реестре его могло не быть — сторожевые скрипты, сбой записи, что
    # угодно. Отметка помнит, докуда дошли, чтобы не гонять одно и то же.
    for cid, hi in top.items():
        try:
            done = await db.owner_sweep_mark_get(cid)
            cover = await db.get_cover_msg_id(cid)
            lo = max(done + 1, hi - RANGE)
            if lo <= hi:
                gone += await _range_delete(token, cid, lo, hi, keep=(cover,) if cover else ())
                await db.owner_sweep_mark_set(cid, hi)
        except Exception as e:
            log.warning(f"[owner-sweep] диапазон {cid}: {e}")
    gone += await _sweep_drv()
    return gone


def _token_for(chat_id: int) -> str:
    """Каким ботом стучаться в этот чат.

    Реестр у водителей и операторов общий — ключом там чат, — а боты разные, и
    чужим токеном сообщение не удалить: телеграм ответит «не найдено», номер
    уйдёт из реестра, и сообщение останется в чате навсегда. Поэтому сначала
    смотрим, чей это чат, и только потом стучимся."""
    import config_staff as staff
    if int(chat_id) in {int(v) for v in staff.DRIVER_IDS.values() if v}:
        return os.getenv("DRIVER_BOT_TOKEN", "")
    оп = {int(x) for x in os.getenv("OPERATOR_IDS", "").replace(" ", "").split(",")
          if x.isdigit()}
    if int(chat_id) in оп:
        return os.getenv("OPERATOR_BOT_TOKEN", "")
    return ""


async def _sweep_drv() -> int:
    """Переписка водителей и операторов. Отдельным проходом, потому что реестр
    другой и ключ в нём — время, а не строка: там даты лежат датами."""
    from api_server import tg_delete
    edge = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS)
    gone = 0
    from api_server import tg_deleted
    for cid, mid in await db.drv_msgs_due(edge, limit=300):
        token = _token_for(cid)
        if token:
            res = None
            try:
                res = await tg_delete(token, cid, mid)
            except Exception:
                res = None
            if not tg_deleted(res):
                continue                 # сбой — оставляем на следующий проход
            if (res or {}).get("ok"):
                gone += 1
        # Чужой чат без токена из реестра тоже убираем: держать номер, которым
        # некому воспользоваться, значит перебирать его каждые пять минут.
        await db.drv_msg_drop(cid, mid)
    return gone


async def wipe_chat(chat_id: int) -> int:
    """Снести всё, что помним по этому чату. Вызывается по тревоге."""
    from api_server import tg_delete, tg_deleted
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not token or not chat_id:
        return 0
    gone = 0
    for r in await db.owner_msgs_of(int(chat_id)):
        mid = r.get("message_id")
        res = None
        try:
            res = await tg_delete(token, chat_id, mid)
        except Exception:
            res = None
        if tg_deleted(res):
            if (res or {}).get("ok"):
                gone += 1
            await db.owner_msg_drop(int(chat_id), mid)
        else:
            log.warning(f"[owner] штора: {chat_id}/{mid} не удалилось: {(res or {}).get('description')}")
    return gone


async def wipe_below(chat_id: int, below: int) -> int:
    """Тревога, второй заход: всё ниже прикрытия диапазоном. Реестр знает
    только то, что через него прошло; сообщения сторожевых скриптов, файлы
    и всё, что ушло мимо, ловим здесь по номерам. Двое суток — предел
    телеграма, старше не сотрёт никто."""
    token = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not token or not chat_id or not below:
        return 0
    hi = int(below) - 1
    lo = max(1, hi - RANGE)
    try:
        n = await _range_delete(token, int(chat_id), lo, hi, keep=(int(below),))
        await db.owner_sweep_mark_set(int(chat_id), hi)
        return n
    except Exception as e:
        log.warning(f"[owner] штора, диапазон {chat_id}: {e}")
        return 0


async def loop(app=None):
    await asyncio.sleep(20)          # даём подняться остальному
    while True:
        try:
            n = await sweep_once()
            if n:
                # Не «из чата владельца»: с тех пор проход накрывает три
                # переписки, и подпись, оставшаяся от одной, врала бы в журнале.
                log.info(f"[owner-sweep] убрано сообщений старше {TTL_HOURS} ч: {n}")
        except Exception as e:
            log.warning(f"[owner-sweep] сбой: {e}")
        await asyncio.sleep(EVERY_SEC)
