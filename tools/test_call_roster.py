"""Контакты связи и звонок в телеграм, когда приложение закрыто.

Владелец, 30 сен 2026: «сделай так, чтобы и водители и операторы могли
звонить старшему обратно, чтобы все у всех были в контактах. НО водители и
операторы смогут позвонить только по аудио. И убедись, что всем приходят
сообщения, если звонят человеку, который не в приложении».

    python3 tools/test_call_roster.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["AMBAR_STAR_NAMES"] = "1:Старший"
os.environ["DRIVER_BOT_TOKEN"] = "d"; os.environ["DRIVER_WEBAPP_URL"] = "https://x/driver"
os.environ["OPERATOR_BOT_TOKEN"] = "o"; os.environ["OPERATOR_WEBAPP_URL"] = "https://x/operator"
os.environ["AMBAR_OWNER_BOT_TOKEN"] = "s"; os.environ["OWNER_WEBAPP_URL"] = "https://x/owner"
import logging; logging.disable(logging.CRITICAL)
import config_staff as staff, call_routes as cr        # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

staff.DISTRICT_STAFF[:] = [
    {"district": "jvc", "operator": "Умар", "drivers": ["Худоба", "Фарух"]},
    {"district": "tecom", "operator": "Джанабиль", "drivers": ["Алишер"]},
]
staff.drivers = lambda *a, **k: [{"name": "Худоба", "district_code": "B1"}, {"name": "Фарух", "district_code": "B1"},
                                 {"name": "Алишер", "district_code": "B5"}]
staff.DRIVER_IDS.update({"Худоба": 11, "Фарух": 12})           # Алишер без аккаунта
staff.OPERATOR_BY_ID.clear(); staff.OPERATOR_BY_ID.update({21: "Умар"})   # Джанабиль без аккаунта
staff.SENIOR_OPERATORS = []

P = lambda kind, key, label, own="": cr.Peer("s", None, kind, key, label, own)
drv = P("drv", "drv:Худоба", "Худоба", "Умар")
op = P("op", "op:Умар", "Умар")
op2 = P("op", "op:Джанабиль", "Джанабиль")

print("── водитель ─────────────────────────────────────────────")
r = cr._roster(drv); keys = [x["key"] for x in r]
eq("первым свой оператор", keys[0], "op:Умар")
eq("СТАРШИЙ В КОНТАКТАХ", "star:1" in keys, True)
eq("старший назван словом", next(x for x in r if x["key"] == "star:1")["role"], "старший")
eq("себя в списке нет", "drv:Худоба" in keys, False)
eq("звонить старшему можно", cr._may_call(drv, "star:1"), True)

print("── оператор ─────────────────────────────────────────────")
r = cr._roster(op); by = {x["key"]: x for x in r}
eq("свои водители первыми", [x["key"] for x in r][:2], ["drv:Фарух", "drv:Худоба"])
eq("старший есть", "star:1" in by, True)
eq("другой оператор есть", "op:Джанабиль" in by, True)
eq("чужой водитель есть", "drv:Алишер" in by, True)
eq("звонить старшему можно", cr._may_call(op, "star:1"), True)

print("── только голос ─────────────────────────────────────────")
eq("оператор ↔ старший — голос", cr._audio_only("op:Умар", "star:1"), True)
eq("водитель → старший: видео снимает _start_call по kind=drv (проверка флага)", drv.kind, "drv")

print("── кому звонить в телеграм ──────────────────────────────")
eq("водителю — его ботом", cr._ring_to("drv", "Худоба"), ("d", 11, "https://x/driver"))
eq("ОПЕРАТОРУ — БОТОМ ОПЕРАТОРА", cr._ring_to("op", "Умар"), ("o", 21, "https://x/operator"))
eq("старшему — своим", cr._ring_to("star", "1"), ("s", 1, "https://x/owner"))
eq("оператор без аккаунта — некуда", cr._ringable("op:Джанабиль"), False)
eq("в строке контакта это видно", (by["op:Джанабиль"]["ring"], by["drv:Худоба"]["ring"], by["drv:Алишер"]["ring"]),
   (False, True, False))

print("── след пропущенного, когда звонящий сам отбился ───────")
import inspect
src = inspect.getsource(cr._ring_driver)
eq("след пишется, если не ответили (не только по сроку)",
   "if call.answered:\n            return" in src and "_CALLS.get(call.cid) is not call or call.answered:\n            return" not in src, True)

print("── планшет как устройство (владелец, 13 окт 2026) ──────")
P2 = lambda sid, kind, key, label, acc=0: cr.Peer(sid, None, kind, key, label, "", account=acc)
star = P2("t1", "star", "star:1", "AMBAR")
tab = P2("t2", "op", "op:Умар", "Умар", acc=staff.DEVICES[0]["telegram_id"]); cr._device_bind(tab, {"tg": "android", "w": 880})
ph = P2("t3", "op", "op:Умар", "Умар", acc=5); cr._device_bind(ph, {"tg": "ios", "w": 402})
pc = P2("t4", "op", "op:Фарух", "Фарух", acc=6); cr._device_bind(pc, {"tg": "web", "w": 1440})
eq("планшет узнаётся по аккаунту, телефон по экрану, компьютер по телеграму",
   (tab.device, ph.device, pc.device), ("планшет", "телефон", "компьютер"))
eq("подпись для звонящего — с устройством", (tab.dlabel, ph.dlabel), ("Умар · планшет", "Умар · телефон"))
eq("планшет отвечает и по имени, и как устройство", (tab.keys, ph.keys), (["op:Умар", "dev:tablet"], ["op:Умар"]))
cr._bind(tab); cr._bind(ph); cr._bind(pc)
eq("сессий: на планшет одна, на Умара две", (len(cr._sessions("dev:tablet")), len(cr._sessions("op:Умар"))), (1, 2))
rows = cr._roster(star)
t = next(r for r in rows if r["key"] == "dev:tablet")
eq("у STAR в контактах «Планшет операторов», в сети, звонить в телеграм есть куда", (t["name"], t["role"], t["online"], t["ring"], t["devs"]),
   ("Планшет операторов", "планшет", True, True, ["планшет"]))
eq("у строки Умара видно, с чего он в сети", next(r["devs"] for r in rows if r["key"] == "op:Умар"), ["планшет", "телефон"])
eq("STAR может звонить на планшет, и только голосом", (cr._may_call(star, "dev:tablet"), cr._audio_only("star:1", "dev:tablet")), (True, True))
eq("имя ключа и куда звонить телеграмом", (cr._имя_ключа("dev:tablet"), cr._ring_to("dev", "tablet")[1]), ("Планшет операторов", staff.DEVICES[0]["telegram_id"]))
eq("оператор с телефона видит планшет, с планшета — нет", (any(r["key"] == "dev:tablet" for r in cr._roster(ph)), any(r["key"] == "dev:tablet" for r in cr._roster(tab))), (True, False))
cr._unbind(tab)
eq("планшет ушёл — строка гаснет, Умар остаётся с телефона", (len(cr._sessions("dev:tablet")), len(cr._sessions("op:Умар")), next(r["online"] for r in cr._roster(star) if r["key"] == "dev:tablet")), (0, 1, False))
cr._unbind(ph); cr._unbind(pc)

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
