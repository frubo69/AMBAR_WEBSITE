"""Комната на двоих: открыл ссылку — можешь позвонить тому, кто открыл её же.

Отдельно от амбаровской связи и намеренно.

Там звонок опирается на то, чего здесь нет вовсе: подпись телеграма, список
своих, строгая иерархия «сверху вниз», чужой оператор за общей панелью. Здесь
никого не надо узнавать — здесь есть ссылка, и человек, который её открыл, и
есть собеседник. Втащить это в тот же код значило бы завести в нём ветку «а
если не надо ничего проверять», то есть дыру рядом с проверками, ради которых
он и написан.

Общего у них ровно одно и то самое ценное — список серверов для обхода NAT
(`call_routes.ice_servers`): TURN с логином на десять минут. Без него звонок
между двумя мобильными сетями просто не соединяется.

Что здесь есть:
    комната — строка из ссылки, до двух человек, живёт, пока в ней кто-то есть;
    сигналинг — перекладывание пакетов между двумя, содержимое не читаем;
    звонок — «позвонить», «ответить», «отбой»: соединение поднимается по
             согласию, а не само при открытии ссылки. Человек, открывший её
             ночью, не должен от этого никому дозвониться.

Чего здесь нет и не будет: имён из базы, истории, доступа к чему-либо
амбаровскому. Комната знает две вещи — кто в ней сейчас и как их зовут с их же
слов.
"""
import asyncio
import json
import logging
import time

from aiohttp import web, WSMsgType

log = logging.getLogger("room")

CORS = {"Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type"}

# Комнаты живут в памяти: разговор всё равно не переживёт перезапуск, а
# хранить в базе то, что осмысленно ровно пока открыта вкладка, незачем.
_ROOMS: dict[str, list] = {}

MAX_IN_ROOM = 2
MAX_MSG = 64 * 1024          # sdp бывает крупным, всё остальное — мелочь
ROOM_RE = 40                 # длиннее ссылка не бывает


class Guest:
    __slots__ = ("ws", "room", "name", "sid")

    def __init__(self, ws, room, name, sid):
        self.ws, self.room, self.name, self.sid = ws, room, name, sid

    async def send(self, **m):
        try:
            await self.ws.send_json(m)
        except Exception:
            pass


def _clean(s, n=40):
    return "".join(c for c in str(s or "") if c.isprintable())[:n].strip()


async def _tell_other(g: Guest, **m):
    for x in _ROOMS.get(g.room, []):
        if x.sid != g.sid:
            await x.send(**m)


async def handle_ice(request):
    """Тот же список, что у амбаровской связи: TURN один на всех, и держать
    для него вторую выдачу логинов значило бы держать два разных ответа на
    один вопрос."""
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=CORS)
    from call_routes import ice_servers
    return web.json_response({"ice": ice_servers()}, headers=CORS)


async def handle_ws(request):
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=MAX_MSG)
    await ws.prepare(request)

    room = _clean(request.query.get("room"), ROOM_RE)
    name = _clean(request.query.get("name"), 24) or "Гость"
    if not room:
        await ws.send_json({"t": "bad", "why": "no_room"})
        await ws.close()
        return ws

    сидят = _ROOMS.setdefault(room, [])
    if len(сидят) >= MAX_IN_ROOM:
        # Третий — не участник, а случайность: ссылку переслали дальше.
        # Говорим прямо, а не соединяем кого попало с кем попало.
        await ws.send_json({"t": "full"})
        await ws.close()
        return ws

    me = Guest(ws, room, name, f"{int(time.time()*1000)}-{len(сидят)}")
    сидят.append(me)
    другой = next((x for x in сидят if x.sid != me.sid), None)
    # Кто пришёл вторым, тот и предлагает связь. Иначе оба предложат разом, и
    # соединение встанет колом на столкновении предложений.
    await me.send(t="here", first=(другой is None),
                  peer=(другой.name if другой else ""))
    if другой:
        await другой.send(t="peer", name=me.name)
    log.info(f"[room] {room}: вошёл {name} ({len(сидят)} в комнате)")

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            t = str(m.get("t") or "")
            # Сигнальные пакеты и мелочь разговора перекладываем как есть:
            # что внутри sdp, сервер не знает и знать не должен — голос и
            # картинка идут мимо него.
            if t in ("sdp", "ice", "ring", "accept", "reject", "bye",
                     "camstate", "micstate", "name"):
                await _tell_other(me, **{**m, "t": t})
    except Exception as e:                                   # noqa: BLE001
        log.debug(f"[room] {room}: {e}")
    finally:
        сидят = _ROOMS.get(room) or []
        _ROOMS[room] = [x for x in сидят if x.sid != me.sid]
        await _tell_other(me, t="left", name=me.name)
        if not _ROOMS[room]:
            _ROOMS.pop(room, None)
        log.info(f"[room] {room}: вышел {name}")
    return ws


def setup(app):
    r = app.router
    r.add_route("OPTIONS", "/api/room/ice", handle_ice)
    r.add_get("/api/room/ice", handle_ice)
    r.add_get("/api/room/ws", handle_ws)
    log.info("[room] routes mounted")
