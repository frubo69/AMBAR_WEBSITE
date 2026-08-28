"""
AMBAR — голосовая связь водителя с оператором.

Зачем свой звонок, а не телеграмный
-----------------------------------
Бот звонить не умеет — в Bot API такого метода нет вовсе. Телеграмный звонок
живёт в клиенте, на уровне личного аккаунта, и втягивать в рабочую связь чей-то
личный номер значит поставить смену в зависимость от одного аккаунта. Поэтому
голос идёт своим путём: WebRTC между приложением водителя и панелью оператора,
здесь — только сигналинг, то есть обмен служебными пакетами до того, как звук
пойдёт напрямую.

Кто кому звонит
---------------
Три уровня, строгая иерархия, звонки только сверху вниз:

    AMBAR STAR (владелец, старший)  → любому оператору и любому водителю;
    оператор                        → только своим водителям;
    водитель                        → только своему оператору.

Снизу вверх звонит один водитель своему оператору, и всё. Подмен нет: своего
оператора нет на месте — звонок не проходит, а не уходит кому-то ещё.

Районный оператор в этой системе не аккаунт, а имя: своего входа в телеграм у
него нет, он выбирает себя в панели за общим устройством. Значит и звонок
адресуется не человеку, а тому, кто СЕЙЧАС представлен этим именем в открытой
панели, — на планшете, на компьютере, где угодно. Открыто в двух местах —
звенит в обоих, кто первый снял, у второго погасло.

Обратная сторона (оператор набирает водителя) упирается в то, что мини-апп не
умеет будить телефон: пока приложение закрыто, звонить в него некуда. Поэтому
если живого соединения нет, водителю уходит сообщение из его бота с кнопкой
«взять» — открыв приложение, он попадает прямо на входящий, который всё это
время ждал.

Почему подпись приходит первым сообщением, а не в адресе
-------------------------------------------------------
Браузер не даёт задать заголовки вебсокету, и подпись initData просилась бы в
строку запроса — то есть в логи веб-сервера, каждая на сутки живой пропуск.
Поэтому сокет открывается пустым и обязан назваться первым же сообщением;
не назвался за пять секунд — закрываем.
"""
import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone

from aiohttp import web, WSMsgType

import db
import config_staff as staff

log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}

AUTH_GRACE = 5.0          # сколько ждём, пока сокет назовётся
RING_TTL = 90.0           # сколько звоним, прежде чем считать, что не взяли
RING_STEP = 1.0           # как часто перезванивает бот водителю

# TURN. Пока переменных нет, работаем на одном STUN: по замерам с телефона
# внешний адрес виден, значит прямое соединение чаще всего складывается. TURN
# нужен для сетей, где не складывается, и включается без правок кода.
TURN_HOST = os.getenv("AMBAR_TURN_HOST", "").strip()
TURN_SECRET = os.getenv("AMBAR_TURN_SECRET", "").strip()
STUN_URL = os.getenv("AMBAR_STUN_URL", "stun:stun.l.google.com:19302").strip()


# Верхний уровень — AMBAR STAR. Кто именно звонит, водитель и оператор должны
# видеть словом, а не догадываться по номеру: «Старший» и «AMBAR» — разные
# люди с разным весом просьбы. Имена берём отсюда, id — из настроек сервера.
STAR_NAMES = {
    8927037895: "Старший",
    7865205960: "AMBAR",
}


# ── кто есть кто ────────────────────────────────────────────────────────────
# Кто на каком районе — меняется в течение дня: старший переставляет водителя,
# и сетка связи обязана поменяться вместе с ним, без перезапуска сервиса.
# Перестановка лежит в базе и накладывается на расписание из кода; держим её
# свежей, но не дёргаем базу на каждый чих — секунд десять она не устареет.
_ROSTER_AT = 0.0
_ROSTER_TTL = 10.0


async def refresh_staff(force: bool = False):
    global _ROSTER_AT
    if not force and time.time() - _ROSTER_AT < _ROSTER_TTL:
        return
    try:
        staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
        _ROSTER_AT = time.time()
    except Exception as e:
        log.warning(f"[call] перестановка не прочитана: {e}")


def _senior_name() -> str:
    ppl = staff.SENIOR_OPERATORS or []
    return (ppl[0].get("name") or "").strip() if ppl else ""


def _operator_names() -> set:
    out = {d["operator"].strip() for d in staff.DISTRICT_STAFF if d.get("operator")}
    s = _senior_name()
    if s:
        out.add(s)
    return out


def _all_drivers() -> list:
    return sorted({dr for d in staff.DISTRICT_STAFF for dr in (d.get("drivers") or [])})


def _drivers_of_operator(name: str) -> list:
    """Кого этот оператор ведёт — по районам, которые за ним сейчас."""
    out = []
    for d in staff.DISTRICT_STAFF:
        if (d.get("operator") or "").strip() == name:
            out.extend(d.get("drivers") or [])
    return sorted(set(out))


# ── реестр живых соединений ─────────────────────────────────────────────────
# Всё в памяти процесса: переживать перезапуск здесь нечему — звонок и так
# рвётся вместе с сокетом, а сокеты переподключаются сами.
class Peer:
    __slots__ = ("sid", "ws", "kind", "key", "label", "call", "own_op")

    def __init__(self, sid, ws, kind, key, label, own_op=""):
        self.sid, self.ws, self.kind = sid, ws, kind
        self.key, self.label = key, label
        self.own_op = own_op        # у водителя — его оператор по району
        self.call = None

    async def send(self, **msg):
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass


class Call:
    __slots__ = ("cid", "caller", "callee_key", "callee", "order", "started",
                 "answered", "ring_task", "video")

    def __init__(self, cid, caller, callee_key, order, video=False):
        self.cid, self.caller, self.callee_key = cid, caller, callee_key
        self.callee = None
        self.order = order
        self.started = time.time()
        self.answered = 0.0
        self.ring_task = None
        self.video = bool(video)   # видеозвонок решается в момент вызова


_PEERS: dict = {}        # sid  → Peer
_BY_KEY: dict = {}       # key  → {sid}
_CALLS: dict = {}        # cid  → Call
_PENDING: dict = {}      # key водителя → (cid, до какого времени ждём)


def _bind(p: Peer):
    _PEERS[p.sid] = p
    _BY_KEY.setdefault(p.key, set()).add(p.sid)


def _unbind(p: Peer):
    _PEERS.pop(p.sid, None)
    s = _BY_KEY.get(p.key)
    if s:
        s.discard(p.sid)
        if not s:
            _BY_KEY.pop(p.key, None)


async def _broadcast_roster():
    """Разослать всем свежие списки.

    Зелёная точка «в приложении» должна гаснуть и загораться сама: иначе
    оператор видит водителя доступным через полчаса после того, как тот закрыл
    приложение, и звонит в пустоту, думая, что звонит в трубку."""
    for p in list(_PEERS.values()):
        await p.send(t="roster", roster=_roster(p))


def _sessions(key: str) -> list:
    return [_PEERS[s] for s in _BY_KEY.get(key, set()) if s in _PEERS]


def _free_sessions(key: str) -> list:
    """Свободные — те, кто не в разговоре. Занятому звонить незачем."""
    return [p for p in _sessions(key) if not p.call]


def ice_servers() -> list:
    """Список серверов для браузера. TURN — с логином на десять минут:
    постоянный пароль в открытой странице живёт ровно до первого любопытного."""
    out = [{"urls": STUN_URL}] if STUN_URL else []
    if TURN_HOST and TURN_SECRET:
        import hashlib
        import hmac as _hmac
        import base64
        user = f"{int(time.time()) + 600}:ambar"
        pwd = base64.b64encode(
            _hmac.new(TURN_SECRET.encode(), user.encode(), hashlib.sha1).digest()).decode()
        out.append({"urls": [f"turn:{TURN_HOST}?transport=udp",
                             f"turn:{TURN_HOST}?transport=tcp"],
                    "username": user, "credential": pwd})
    return out


# ── журнал ──────────────────────────────────────────────────────────────────
async def _recent(peer, limit: int = 12) -> list:
    """Последние звонки этого человека — и свои, и чужие к нему.

    Список недавних есть в любом телефоне, и не ради красоты: пропущенный
    звонок иначе исчезает бесследно, а перезвонить надо именно тому, кто
    звонил. Отсюда же и повторный набор одним касанием."""
    out = []
    try:
        x = db._db_or_none()
        if x is None:
            return out
        me_kind, _, me_name = peer.key.partition(":")
        cur = x.calls.find(
            {"$or": [{"from": peer.label, "from_kind": me_kind},
                     {"to": me_name, "to_kind": me_kind}]},
            {"_id": 0}).sort("at", -1).limit(limit)
        async for c in cur:
            mine = c.get("from") == peer.label and c.get("from_kind") == me_kind
            other = c.get("to") if mine else c.get("from")
            other_kind = c.get("to_kind") if mine else c.get("from_kind")
            out.append({
                "who": other, "key": f"{other_kind}:{other}",
                "dir": "out" if mine else "in",
                "at": c.get("at", ""),
                "talk": int(c.get("talk_sec") or 0),
                # Пропущенный — только входящий, на который не ответили. Свой
                # неотвеченный звонок пропущенным называть незачем.
                "missed": (not mine) and c.get("outcome") in ("no_answer", "rejected"),
                "outcome": c.get("outcome", ""),
            })
    except Exception as e:
        log.warning(f"[call] недавние не прочитаны: {e}")
    return out


async def _push_recent(*peers):
    """Разослать обновлённый список тем, кого звонок касался."""
    for p in peers:
        if p and p.sid in _PEERS:
            await p.send(t="recent", recent=await _recent(p))



async def _log_call(call: Call, outcome: str):
    """Звонок в отличие от переписки не оставляет следа сам по себе. Раз он
    заменяет собой сообщение по заказу, след обязан остаться здесь."""
    try:
        x = db._db_or_none()
        if x is None:
            return
        now = time.time()
        await x.calls.insert_one({
            "from": call.caller.label, "from_kind": call.caller.kind,
            "to": call.callee_key.split(":", 1)[-1],
            "to_kind": call.callee_key.split(":", 1)[0],
            "order_id": call.order or "",
            "outcome": outcome,
            "video": bool(getattr(call, "video", False)),
            "at": datetime.now(timezone.utc).isoformat(),
            "wait_sec": round((call.answered or now) - call.started, 1),
            "talk_sec": round(now - call.answered, 1) if call.answered else 0,
        })
    except Exception as e:
        log.warning(f"[call] журнал не записан: {e}")


async def _end(call: Call, outcome: str, quiet_sid: str = "", say: str = ""):
    """Свернуть звонок с обеих сторон. quiet_sid — тот, кто и так знает.

    say — короткий ответ вместо разговора («перезвоню», «занят»). За рулём
    набирать нечего, а звонящему важно услышать не «отклонён», а причину."""
    _CALLS.pop(call.cid, None)
    if call.ring_task:
        call.ring_task.cancel()
    for p in (call.caller, call.callee):
        if p and p.call and p.call.cid == call.cid:
            p.call = None
            if p.sid != quiet_sid:
                await p.send(t="end", call=call.cid, why=outcome, say=say)
    # Пока трубку не сняли, callee ещё никто: звонок звенит сразу на всех
    # устройствах этого имени, и ни одно из них в call не записано. Если их
    # тут не позвать, они будут звенеть в пустоту после того, как звонящий уже
    # передумал, — телефон звонит, а на том конце давно никого.
    ringing = _sessions(call.callee_key) if call.callee is None else []
    for p in ringing:
        if p.sid != quiet_sid:
            await p.send(t="cancel", call=call.cid)
    # Звонок мог ждать водителя, поднятого пинком, — снимаем и это.
    pend = _PENDING.get(call.callee_key)
    if pend and pend[0] == call.cid:
        _PENDING.pop(call.callee_key, None)
    await _log_call(call, outcome)
    await _push_recent(call.caller, call.callee, *ringing)


# ── рингтон в боте водителя ─────────────────────────────────────────────────
# Родного экрана входящего у мини-аппа нет и быть не может: его рисует система
# и пускает туда только установленное приложение. Зато телефон звенит на каждое
# сообщение бота — и из этого собирается звонок.
#
# Одно сообщение — один «дзынь», прослушать легко. Девяносто сообщений подряд —
# свалка в переписке. Поэтому карточка на экране всё время одна: отправили
# новую, стёрли предыдущую. Телефон звенит раз в секунду, а в чате висит один
# живой вызов со счётчиком и кнопкой «взять».
#
# Порядок именно такой — сначала отправить, потом стереть старое. Наоборот
# получилась бы дыра, в которую попадает взгляд: карточки нет ни на экране
# блокировки, ни в чате.
def _drv_token() -> str:
    return os.getenv("DRIVER_BOT_TOKEN", "").strip()


def _drv_app_url() -> str:
    return os.getenv("DRIVER_WEBAPP_URL", "https://ambar-delivery.com/driver/").rstrip("/")


async def _tg_ring(token: str, chat_id: int, text: str, kb: dict):
    """Тик рингтона отправляем напрямую, мимо общей tg_send.

    Та по пути записывает каждое сообщение в реестр переписки водителя — он
    нужен, чтобы скрытый режим мог стереть чат целиком. Тики живут по секунде
    и стираются сами; девяносто записей за звонок засорили бы реестр ради
    сообщений, которых уже нет. А вот пропущенный звонок остаётся в чате — и
    отправляется как раз через tg_send, чтобы в реестр попасть."""
    import aiohttp as _a
    try:
        async with _a.ClientSession(timeout=_a.ClientTimeout(total=10)) as s:
            async with s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat_id, "text": text,
                                    "reply_markup": kb}) as r:
                return await r.json()
    except Exception as e:
        log.warning(f"[call] тик рингтона не ушёл: {e}")
        return {}


async def _tg_delete(token: str, chat_id: int, mid: int):
    if not mid:
        return
    try:
        import aiohttp as _a
        async with _a.ClientSession() as s:
            await s.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                         json={"chat_id": chat_id, "message_id": mid}, timeout=10)
    except Exception:
        pass


async def _ring_driver(call: Call, name: str):
    """Звоним водителю сообщениями, пока не возьмёт или пока не выйдет время."""
    tg_id = staff.DRIVER_IDS.get(name)
    token = _drv_token()
    if not tg_id or not token:
        log.warning(f"[call] некому звонить: {name} не в списке или нет токена")
        return
    from api_server import tg_send

    kb = {"inline_keyboard": [[
        {"text": "Взять звонок",
         "web_app": {"url": _drv_app_url() + "/?call=" + call.cid}}]]}
    prev = 0
    step = RING_STEP
    started = time.time()
    try:
        while time.time() - started < RING_TTL:
            if _CALLS.get(call.cid) is not call or call.answered:
                break
            left = int(RING_TTL - (time.time() - started))
            r = await _tg_ring(token, tg_id,
                               f"Входящий звонок · {call.caller.label}\n"
                               f"Осталось {left} сек", kb)
            if (r or {}).get("ok"):
                mid_new = ((r.get("result") or {}).get("message_id")) or 0
                if prev:
                    await _tg_delete(token, tg_id, prev)
                prev = mid_new
                step = RING_STEP
            elif (r or {}).get("error_code") == 403:
                # Бот заблокирован — звонить некуда, и девяносто попыток этого
                # не изменят. Прекращаем сразу, чтобы звонящий узнал правду.
                log.warning(f"[call] {name} заблокировал бота — рингтон отменён")
                return
            else:
                # Телеграм придержал — не спорим с ним, а замедляемся: иначе
                # он придержит всерьёз и звонок замолчит совсем.
                wait = ((r or {}).get("parameters") or {}).get("retry_after")
                step = float(wait) if wait else min(step * 2, 10.0)
                log.warning(f"[call] рингтон {name} придержан, пауза {step}s: {r}")
            await asyncio.sleep(step)
    except asyncio.CancelledError:
        pass
    finally:
        # Взял — карточка не нужна вовсе. Не взял — вместо неё остаётся след,
        # иначе пропущенный звонок исчезает бесследно.
        if prev:
            await _tg_delete(token, tg_id, prev)
        if _CALLS.get(call.cid) is not call or call.answered:
            return
        try:
            await tg_send(token, tg_id,
                          f"Пропущенный звонок · {call.caller.label}",
                          parse_mode=None)
        except Exception:
            pass


# ── сам звонок ──────────────────────────────────────────────────────────────
async def _start_call(caller: Peer, to_key: str, order: str, video: bool = False):
    if caller.call:
        await caller.send(t="failed", why="busy")
        return

    kind, _, name = to_key.partition(":")
    # Свои же сессии из целей вон: звонок самому себе — петля, а не связь.
    sess = [p for p in _sessions(to_key) if p.key != caller.key]
    # Занят не аппарат, а человек. Оператор может держать панель открытой на
    # планшете и на компьютере разом; если он говорит с одного, звонить во
    # второй нельзя — телефон зазвонит у того, кто уже с трубкой у уха.
    busy = any(p.call for p in sess)
    targets = [p for p in sess if not p.call]

    # «Занят» — не то же, что «недоступен», и человеку разница важна:
    # занятого ждут, недоступного ищут другим путём.
    if busy:
        await caller.send(t="failed", why="busy_them", peer=name)
        return

    cid = secrets.token_hex(8)
    call = Call(cid, caller, to_key, order, video)
    caller.call = call
    _CALLS[cid] = call

    if not targets and kind == "drv":
        # Приложение закрыто: держим звонок и звоним ему в бот.
        if not staff.DRIVER_IDS.get(name) or not _drv_token():
            caller.call = None
            _CALLS.pop(cid, None)
            await caller.send(t="failed", why="offline")
            return
        _PENDING[to_key] = (cid, time.time() + RING_TTL)
        call.ring_task = asyncio.create_task(_ring_both(call, name))
        await caller.send(t="calling", call=cid, to=name,
                          note="приложение закрыто — звоним в телеграм")
        return

    if not targets:
        caller.call = None
        _CALLS.pop(cid, None)
        await caller.send(t="failed", why="offline")
        return

    for p in targets:
        await p.send(t="ring", call=cid, frm=caller.label, kind=caller.kind,
                     order=order, video=call.video)
    await caller.send(t="calling", call=cid, to=to_key.split(":", 1)[-1])
    call.ring_task = asyncio.create_task(_ring_timeout(call, RING_TTL))


async def _ring_both(call: Call, name: str):
    """Рингтон в боте и срок звонка — одной задачей, чтобы отмена снимала оба."""
    try:
        await _ring_driver(call, name)
    finally:
        if _CALLS.get(call.cid) is call and not call.answered:
            await _end(call, "no_answer")


async def _ring_timeout(call: Call, ttl: float):
    try:
        await asyncio.sleep(ttl)
    except asyncio.CancelledError:
        return
    if _CALLS.get(call.cid) is call and not call.answered:
        await _end(call, "no_answer")


async def _accept(p: Peer, cid: str):
    call = _CALLS.get(cid)
    if not call or call.answered or p.call:
        await p.send(t="cancel", call=cid)
        return
    call.callee = p
    call.answered = time.time()
    p.call = call
    if call.ring_task:
        call.ring_task.cancel()
    _PENDING.pop(call.callee_key, None)
    # Остальным устройствам с тем же именем звонок больше не нужен.
    for other in _sessions(call.callee_key):
        if other.sid != p.sid:
            await other.send(t="cancel", call=cid)
    await call.caller.send(t="accepted", call=cid, by=p.label)
    await p.send(t="joined", call=cid, peer=call.caller.label)
    log.info(f"[call] {call.caller.label} → {call.callee_key} принят")


# ── вебсокет ────────────────────────────────────────────────────────────────
async def handle_ws(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    peer = None
    try:
        # Первое сообщение обязано быть представлением.
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=AUTH_GRACE)
        except asyncio.TimeoutError:
            await ws.close(code=4401, message=b"auth timeout")
            return ws
        if first.type != WSMsgType.TEXT:
            await ws.close(code=4401, message=b"auth expected")
            return ws
        try:
            hello = json.loads(first.data)
        except Exception:
            await ws.close(code=4401, message=b"bad auth")
            return ws

        await refresh_staff()
        peer = await _identify(hello)
        if not peer:
            # Отказ обязан быть виден в логах. Молчаливый отказ выглядит с
            # телефона ровно как «связи нет», и искать причину потом не по чему.
            log.warning(f"[call] не пустили: as={hello.get('as')!r}, "
                        f"подпись {'есть' if hello.get('tma') else 'ПУСТАЯ'}")
            await ws.send_json({"t": "denied"})
            await ws.close(code=4403, message=b"forbidden")
            return ws
        peer.ws = ws
        _bind(peer)

        await ws.send_json({
            "t": "ready", "me": peer.label, "kind": peer.kind,
            "peer": _default_target(peer),
            "roster": _roster(peer),
            "recent": await _recent(peer),
            "ice": ice_servers(),
        })
        # Что умеет это устройство — в лог, один раз при входе. Догадки о чужом
        # телефоне по памяти уже дорого обошлись; пусть будет запись.
        env = hello.get("env") or {}
        if isinstance(env, dict) and env:
            log.info(f"[call] на связи: {peer.label} ({peer.kind}), устройство: "
                     f"телеграм={env.get('tg') or '?'} экран={env.get('w') or '?'} "
                     f"пальцем={bool(env.get('touch'))} "
                     f"сессия_звука={bool(env.get('aus'))} трубка={bool(env.get('ear'))} "
                     f"выбор_выхода={bool(env.get('sink'))} замок={bool(env.get('wake'))} "
                     f"версия={env.get('ver') or '?'} поворот={bool(env.get('rot'))}")
        else:
            log.info(f"[call] на связи: {peer.label} ({peer.kind})")
        await _broadcast_roster()

        # Водителя мог поднять пинок — тогда его ждёт входящий.
        pend = _PENDING.get(peer.key)
        if pend and pend[1] > time.time():
            call = _CALLS.get(pend[0])
            if call and not call.answered:
                await peer.send(t="ring", call=call.cid, frm=call.caller.label,
                                kind=call.caller.kind, order=call.order,
                                video=call.video)

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            await _on_message(peer, m)
    except Exception as e:
        log.warning(f"[call] сокет оборвался: {e}")
    finally:
        if peer:
            # Пропавший сокет — это обрыв, а не положенная трубка, и называть
            # его надо своим именем. Пока он писался как «hangup», разговор,
            # убитый уснувшим приложением, выглядел в журнале ровно как
            # законченный по-человечески — и отличить одно от другого было не
            # по чему.
            if peer.call:
                await _end(peer.call, "link", quiet_sid=peer.sid)
            _unbind(peer)
            log.info(f"[call] отключился: {peer.label}, код {ws.close_code}")
            await _broadcast_roster()
    return ws


async def _on_message(peer: Peer, m: dict):
    t = m.get("t")
    if t == "call":
        # Перестановка могла случиться минуту назад — сверяемся с базой, а не
        # с тем, что было при входе в приложение.
        await refresh_staff()
        if peer.kind == "drv":
            fresh = staff.driver_by_tg(staff.DRIVER_IDS.get(peer.label, 0))
            peer.own_op = (fresh or {}).get("operator", peer.own_op) or peer.own_op
        to = (m.get("to") or "").strip() or _default_target(peer)
        if not to:
            await peer.send(t="failed", why="no_target")
            return
        if not _may_call(peer, to):
            await peer.send(t="failed", why="not_allowed")
            return
        await _start_call(peer, to, str(m.get("order") or "")[:32],
                          video=bool(m.get("video")))
        return

    if t == "accept":
        await _accept(peer, str(m.get("call") or ""))
        return

    if t in ("reject", "bye"):
        call = _CALLS.get(str(m.get("call") or "")) or peer.call
        if not call:
            return
        # Отклонить может только тот, кому звонят. «Отклонение» от самого
        # звонящего — это отмена набора, и в журнале она обязана называться
        # своим именем, иначе у собеседника в пропущенных висит несуществующий
        # отказ.
        if t == "reject" and not call.answered and peer.sid != call.caller.sid:
            await _end(call, "rejected", quiet_sid=peer.sid,
                       say=str(m.get("say") or "")[:60])
        else:
            await _end(call, "hangup" if call.answered else "cancelled",
                       quiet_sid=peer.sid)
        return

    # Несколько строк с устройства. Звук ведёт себя по-разному на каждом
    # телефоне, а увидеть его с сервера нельзя — вот пусть телефон и скажет.
    if t == "diag":
        log.info(f"[call] {peer.label}: {str(m.get('text') or '')[:200]}")
        return

    # Состояние камеры — такой же служебный пакет: перекладываем собеседнику.
    # Полагаться на «замолкание» дорожки нельзя, оно приходит не везде.
    if t == "camstate":
        call = peer.call
        if call:
            other = call.callee if call.caller.sid == peer.sid else call.caller
            if other:
                await other.send(t="camstate", on=bool(m.get("on")))
        return

    # Сигнальные пакеты просто перекладываем другой стороне: сервер в
    # содержимое не смотрит и ключей не знает — голос идёт мимо него.
    if t in ("sdp", "ice"):
        call = peer.call
        if not call:
            return
        other = call.callee if call.caller.sid == peer.sid else call.caller
        if other:
            await other.send(t=t, call=call.cid, data=m.get("data"))


# ── кто это и кому ему можно ────────────────────────────────────────────────
async def _identify(hello: dict):
    tma = hello.get("tma") or ""
    sid = secrets.token_hex(8)

    # Водитель — по подписи своего бота.
    try:
        import driver_routes
        u = driver_routes._valid_init_data(tma, driver_routes.DRIVER_BOT_TOKEN)
        if u:
            me = staff.driver_by_tg(u.get("id"))
            if me:
                # Район водителя мог смениться утром — оператора берём из
                # свежего ростера, а не из того, что было при запуске сервиса.
                return Peer(sid, None, "drv", f"drv:{me['name']}", me["name"],
                            own_op=(me.get("operator") or "").strip())
            return None
    except Exception as e:
        log.warning(f"[call] проверка водителя: {e}")

    # Оператор — по подписи операторского бота. Имя, под которым он сидит,
    # приходит от панели: районного оператора как аккаунта не существует, за
    # него встают за общим устройством.
    try:
        import operator_routes
        u = operator_routes._validate_operator_init_data(tma)
        if u and u.get("id") in operator_routes.OPERATOR_IDS:
            asked = (hello.get("as") or "").strip()
            name = asked if asked in _operator_names() else operator_routes._op_name(u)
            return Peer(sid, None, "op", f"op:{name}", name)
    except Exception as e:
        log.warning(f"[call] проверка оператора: {e}")

    # AMBAR STAR — свой бот и своя подпись. Аккаунтов там несколько, и путать
    # их нельзя: звонок «старшему» обязан попасть старшему, а не владельцу.
    # Поэтому каждый входит под своим именем, а не под общим «панель владельца».
    try:
        from api_server import validate_owner_init_data
        from config import OWNER_IDS, MANAGER_IDS
        u = validate_owner_init_data(tma)
        uid = int((u or {}).get("id") or 0)
        if uid and (uid in OWNER_IDS or uid in MANAGER_IDS or await db.is_manager(uid)):
            name = STAR_NAMES.get(uid) or (u.get("first_name") or str(uid))
            return Peer(sid, None, "star", f"star:{uid}", name)
    except Exception as e:
        log.warning(f"[call] проверка AMBAR STAR: {e}")
    return None


def _default_target(peer: Peer) -> str:
    """Кому звонит кнопка без выбора. У водителя — своему оператору, и всё.

    Подмены здесь нет намеренно: нет оператора на районе — звонок не проходит,
    а не уходит куда-нибудь ещё."""
    return f"op:{peer.own_op}" if peer.kind == "drv" and peer.own_op else ""


# Сетка связи. Три уровня и строгая иерархия — звонки идут только сверху вниз,
# и единственное исключение снизу вверх — водитель своему оператору:
#
#   AMBAR STAR (владелец, старший)  → любому оператору и любому водителю;
#   оператор                        → ТОЛЬКО своим водителям;
#   водитель                        → ТОЛЬКО своему оператору.
#
# Оператор не звонит наверх вовсе. Позвонить в AMBAR STAR не может никто: иначе
# кнопка «старший» рано или поздно приведёт к владельцу, а это ровно то
# смешение уровней, ради отсутствия которого иерархия и заведена.
#
# Список строится из ростера, а ростер живёт с перестановками: перевели
# водителя на другой район — он в ту же минуту звонит новому оператору и
# пропадает из списка у прежнего. Отдельного места, где это надо менять
# руками, нет и быть не должно.
def _roster(peer: Peer) -> list:
    """Кому этот человек может позвонить и кто из них сейчас доступен."""
    rows = []

    def add(key, name, role):
        if not any(r["key"] == key for r in rows):
            rows.append({"key": key, "name": name, "role": role,
                         "online": bool(_sessions(key))})

    if peer.kind == "drv":
        if peer.own_op:
            add(f"op:{peer.own_op}", peer.own_op, "свой оператор")
        return rows

    if peer.kind == "star":
        for n in sorted(_operator_names()):
            add(f"op:{n}", n, "оператор")
        for n in _all_drivers():
            add(f"drv:{n}", n, "водитель")
        return rows

    for n in _drivers_of_operator(peer.label):
        add(f"drv:{n}", n, "водитель")
    return rows


def _may_call(peer: Peer, to_key: str) -> bool:
    """Звонить можно только тем, кто есть в своём же списке. Иначе водитель
    смог бы дозвониться в чужой район, а оператор — чужому водителю."""
    return any(r["key"] == to_key for r in _roster(peer))


# ── статус для панели ───────────────────────────────────────────────────────
async def handle_ice(request: web.Request):
    """Список ICE-серверов отдельно от сокета — чтобы страница могла
    подготовить соединение до того, как кто-то позвонит."""
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS_HEADERS)
    return web.json_response({"ice": ice_servers()}, headers=CORS_HEADERS)


async def on_shutdown(app):
    """Закрыть все сокеты связи при остановке сервиса.

    Без этого aiohttp честно ждёт, пока живые вебсокеты разойдутся сами, и
    перезапуск растягивается на полминуты — всё это время приложения получают
    502. Сокеты и так переподключаются сами, поэтому рвать их при остановке
    правильно: разговор всё равно не переживёт перезапуск, а простой переживать
    нечего."""
    peers = list(_PEERS.values())
    for p in peers:
        try:
            await p.ws.close(code=1012, message=b"restart")
        except Exception:
            pass
    if peers:
        log.info(f"[call] при остановке закрыто сокетов: {len(peers)}")


def setup(app):
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/api/call/ws", handle_ws)
    app.router.add_route("OPTIONS", "/api/call/ice", handle_ice)
    app.router.add_get("/api/call/ice", handle_ice)
    log.info("[call] сигналинг подключён"
             + (f", TURN {TURN_HOST}" if TURN_HOST else ", только STUN"))
