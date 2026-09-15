"""Удалённый чат и заблокированный бот геопозиции (15 сен 2026). Без базы и
без телеграма: пробы, событие my_chat_member, состояние геопозиции."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import geo_bot, driver_routes as dr
from telegram.error import BadRequest, Forbidden
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
now = datetime.now(timezone.utc)
POS = {"Худоба": {"_id": "Худоба", "at": now, "until": now + timedelta(days=100), "lat": 25.0, "lon": 55.0},
       "Фарух": {"_id": "Фарух", "at": now, "until": now + timedelta(days=100), "lat": 25.0, "lon": 55.0},
       "Азиз": {"_id": "Азиз", "at": now - timedelta(minutes=30), "lat": 25.0, "lon": 55.0}}
PROBES = [{"_id": 11, "name": "Худоба", "mid": 5}, {"_id": 22, "name": "Фарух", "mid": 6}, {"_id": 33, "name": "Азиз", "mid": 7}]
GONE, CLEARED, STREAM = [], [], []
async def probe_all(): return list(PROBES)
async def probe_clear(chat): CLEARED.append(chat)
async def pos_all(names):
    # Как в бою (db.driver_pos_all): срок и время — строками, не datetime.
    out = []
    for n in names:
        if n in POS:
            r = dict(POS[n]); r["at"] = str(r.get("at") or ""); r["until"] = str(r.get("until") or ""); out.append(r)
    return out
async def pos_stop(name, at=None): GONE.append(name); POS[name].pop("until", None); POS[name]["stopped_at"] = at or now
async def on_stream(name, on, now=None): STREAM.append((name, on)); return True
geo_bot.db.geo_probe_all = probe_all; geo_bot.db.geo_probe_clear = probe_clear
geo_bot.db.driver_pos_all = pos_all; geo_bot.db.driver_pos_stop = pos_stop
geo_bot.geo_watch.on_stream = on_stream
class Bot:
    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        if chat_id == 11: raise BadRequest("Message to edit not found")
        if chat_id == 22: raise BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")
        raise BadRequest("Message to edit not found")
async def main():
    n = await geo_bot._probe_once(Bot())
    eq("потеряна ровно одна трансляция (чат 11 удалён)", (n, GONE), (1, ["Худоба"]))
    eq("живой чат 22 не тронут", "Фарух" in GONE, False)
    eq("у Азиза трансляции нет — пробу не трогаем", CLEARED, [11])
    eq("сторожу сказано «выключил»", STREAM, [("Худоба", False)])
    # блокировка бота
    class U:  # my_chat_member
        class my_chat_member:
            class new_chat_member: status = "kicked"
            class from_user: id = 777
            class chat: id = 22
    geo_bot._role = lambda uid: ("driver", "Фарух") if uid == 777 else ("", "")
    await geo_bot.on_my_chat_member(U, None)
    eq("блокировка бота гасит трансляцию", ("Фарух" in GONE, 22 in CLEARED), (True, True))
    # состояние геопозиции: точка свежая, но трансляция остановлена
    async def dr_pos_all(names): return [dict(POS[n]) for n in names if n in POS]
    dr.db.driver_pos_all = dr_pos_all
    g = await dr._geo_state("Худоба")
    eq("реальный водитель после удаления чата: ok=False, stopped=True", (g["ok"], g["stopped"], g["fresh"]), (False, True, True))
    t = await dr._geo_for({"name": "Худоба", "test": True})
    eq("тест-водитель после удаления чата: ok=False", t["ok"], False)
    POS["Худоба"].pop("stopped_at"); POS["Худоба"]["until"] = now + timedelta(days=1)
    g2 = await dr._geo_state("Худоба")
    eq("трансляция снова идёт: ok=True", (g2["ok"], g2["stopped"]), (True, False))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
