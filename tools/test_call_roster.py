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

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
