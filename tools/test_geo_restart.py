"""Включил трансляцию заново: телеграм гасит прежнее сообщение той же
правкой, что и выключение. Новую трансляцию это гасить не должно (15 сен
2026, тест-водитель). Оба бота — LOCATOR и бот водителя. Без базы и телеграма."""
import asyncio, os, sys
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import geo_bot, driver_bot, geo_watch, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

POS, EVENTS = {}, []
async def pos_set(name, day, lat, lon, at, until=None, acc=None, stop_live=False, live=None):
    d = POS.setdefault(name, {})
    d["at"] = at
    if stop_live: d.pop("until", None); d["stopped_at"] = at
    elif until is not None: d["until"] = until
    if live: d["live_chat"], d["live_mid"] = int(live[0]), int(live[1])
    EVENTS.append(("point", name, "stop" if stop_live else ("live" if until else "once")))
async def pos_live(name):
    d = POS.get(name) or {}
    return {"chat": d.get("live_chat"), "mid": d["live_mid"]} if d.get("live_mid") else None
async def on_stream(name, on, now=None): EVENTS.append(("stream", name, "on" if on else "off")); return True
async def noop(*a, **k): return None
db.driver_pos_set = pos_set; db.driver_pos_live = pos_live
geo_watch.on_stream = on_stream
geo_bot._role = lambda uid: ("driver", "Худоба")
geo_bot._spawn = lambda coro: (coro.close(), None)[1]        # историю LEGO не шлём
geo_bot._lego_wall = noop
driver_bot.staff.driver_or_test = lambda uid: {"name": "Худоба"}
driver_bot._remember = noop

class Msg:
    def __init__(self, mid, period):
        self.message_id = mid
        self.location = NS(latitude=25.0, longitude=55.0, live_period=period, horizontal_accuracy=10)
    async def reply_text(self, *a, **k): return NS(message_id=999)
def upd(mid, period, edited):
    m = Msg(mid, period)
    return NS(effective_message=m, message=None if edited else m, edited_message=m if edited else None,
              effective_user=NS(id=7), effective_chat=NS(id=100))

async def run(handler, who):
    POS.clear(); EVENTS.clear()
    await handler(upd(1, 0x7FFFFFFF, False), None)                 # включил: сообщение 1
    eq(f"{who}: включил — точка со сроком, сторожу «включил», ведёт сообщение 1",
       (EVENTS[-2:], POS["Худоба"].get("live_mid")), ([("point", "Худоба", "live"), ("stream", "Худоба", "on")], 1))
    await handler(upd(1, 0x7FFFFFFF, True), None)                  # обычная точка правкой сообщения 1
    eq(f"{who}: точка правкой — записана, сторожа не дёргаем", EVENTS[-1], ("point", "Худоба", "live"))
    await handler(upd(2, 0x7FFFFFFF, False), None)                 # включил заново: сообщение 2
    eq(f"{who}: включил заново — ведёт сообщение 2", (POS["Худоба"].get("live_mid"), EVENTS[-1]), (2, ("stream", "Худоба", "on")))
    n = len(EVENTS)
    await handler(upd(1, None, True), None)                        # телеграм погасил прежнее сообщение 1
    eq(f"{who}: конец прежнего сообщения — не выключение: ни точки, ни «выключил», срок на месте",
       (len(EVENTS) - n, "until" in POS["Худоба"], "stopped_at" in POS["Худоба"]), (0, True, False))
    await handler(upd(2, None, True), None)                        # выключил настоящую
    eq(f"{who}: конец сообщения 2 — выключение: срок снят, сторожу «выключил»",
       (EVENTS[-2:], "until" in POS["Худоба"]), ([("point", "Худоба", "stop"), ("stream", "Худоба", "off")], False))
    # Старая запись без сведений о сообщении — как раньше: правка без срока = выключил
    POS["Худоба"] = {"until": "x"}; EVENTS.clear()
    await handler(upd(5, None, True), None)
    eq(f"{who}: запись без сообщения — выключение как раньше", EVENTS, [("point", "Худоба", "stop"), ("stream", "Худоба", "off")])

async def main():
    await run(geo_bot.on_location, "LOCATOR")
    await run(driver_bot.on_location, "бот водителя")
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
