"""Звонки: менеджеру без имени в настройках звонит только владелец (17 ноя 2026:
«почему у амбар (владельца) нет меня в списке контактов? он должен иметь
возможность мне позвонить, но только он»).

Что было. Верхний уровень связи — владелец, менеджеры из настроек и те, кто
назван в AMBAR_STAR_NAMES. Менеджер без имени в настройках стоял в контактах у
ВСЕХ (у владельца, старшего, операторов, водителей), а назывался так, как его
увидели при входе; после перезапуска сервиса имя забывалось, и строка
становилась безымянной «AMBAR STAR» — владелец не находил в ней человека.

Теперь. Звонить всем можно владельцу и тем, кого зовут по имени в настройках
(«AMBAR», «Старший»). Менеджер без имени — только в контактах владельца, и
набрать его в обход списка сервер не даёт. Имя, под которым человек вошёл,
хранится в базе и переживает перезапуск; STAR присылает то имя, каким
подписывает правки человека.

  A. Владелец: видит и старшего, и менеджера без имени; звонить можно.
  B. Старший: менеджера без имени в контактах нет, звонить нельзя.
  C. Сам менеджер: видит владельца и старшего, себя — нет; звонит всем.
  D. Водитель и оператор: владелец и старший на месте, менеджера нет.
  E. Сервер отказывает в обход списка: настоящий _on_message отвечает
     not_allowed старшему, оператору и водителю; владельца пропускает.
  F. Имя: до входа — «AMBAR STAR»; вошёл — имя из приложения; перезапуск —
     имя на месте; чужое имя из настроек взять нельзя; у названного в
     настройках имя не меняется и в базу не пишется.
  G. Прежнее не тронуто: старшие у всех в контактах, пары и роли те же.

    python3 tools/test_call_private.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""
os.environ["AMBAR_OWNER_IDS"] = "1"
os.environ["AMBAR_MANAGER_IDS"] = "1,2,3"                 # как в бою: владелец стоит и в менеджерах
os.environ["AMBAR_STAR_NAMES"] = "1:AMBAR,2:Старший"      # 3 — менеджер без имени
os.environ["DRIVER_BOT_TOKEN"] = "d"; os.environ["DRIVER_WEBAPP_URL"] = "https://x/driver"
os.environ["OPERATOR_BOT_TOKEN"] = "o"; os.environ["OPERATOR_WEBAPP_URL"] = "https://x/operator"
os.environ["AMBAR_OWNER_BOT_TOKEN"] = "s"; os.environ["OWNER_WEBAPP_URL"] = "https://x/owner"
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, config_staff as staff, call_routes as cr                # noqa: E402

FAIL = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


staff.DISTRICT_STAFF[:] = [{"district": "jvc", "operator": "Тест-оператор", "drivers": ["Тест-водитель"]}]
staff.drivers = lambda *a, **k: [{"name": "Тест-водитель", "district_code": "B1"}]
staff.DRIVER_IDS.update({"Тест-водитель": 11})
staff.OPERATOR_BY_ID.clear(); staff.OPERATOR_BY_ID.update({21: "Тест-оператор"})
staff.SENIOR_OPERATORS = []


class Сокет:
    def __init__(self): self.out = []
    async def send_json(self, m): self.out.append(m)


def peer(kind, key, label, own=""):
    p = cr.Peer("s-" + key, Сокет(), kind, key, label, own)
    return p


владелец = peer("star", "star:1", "AMBAR")
старший = peer("star", "star:2", "Старший")
менеджер = peer("star", "star:3", "AMBAR STAR")
водитель = peer("drv", "drv:Тест-водитель", "Тест-водитель", "Тест-оператор")
оператор = peer("op", "op:Тест-оператор", "Тест-оператор")
ключи = lambda p: [r["key"] for r in cr._roster(p)]
звёзды = lambda p: [r["key"] for r in cr._roster(p) if r["key"].startswith("star:")]


async def main():
    db._db = AsyncMongoMockClient()["call_private"]

    print("── A. владелец ─────────────────────────────────────────────")
    eq("A. в контактах старший и менеджер без имени", звёзды(владелец), ["star:2", "star:3"])
    eq("A. оба в разделе AMBAR STAR", {r["role"] for r in cr._roster(владелец) if r["key"].startswith("star:")}, {"AMBAR STAR"})
    eq("A. звонить менеджеру можно", cr._may_call(владелец, "star:3"), True)
    eq("A. дозвониться есть куда и при закрытом приложении",
       next(r["ring"] for r in cr._roster(владелец) if r["key"] == "star:3"), True)

    print("── B. старший ──────────────────────────────────────────────")
    eq("B. в контактах только владелец", звёзды(старший), ["star:1"])
    eq("B. звонить менеджеру нельзя", cr._may_call(старший, "star:3"), False)
    eq("B. владельцу — можно, как и было", cr._may_call(старший, "star:1"), True)

    print("── C. сам менеджер ─────────────────────────────────────────")
    eq("C. видит владельца и старшего, себя — нет", звёзды(менеджер), ["star:1", "star:2"])
    eq("C. звонит всем, как раньше", (cr._may_call(менеджер, "star:1"), cr._may_call(менеджер, "star:2"),
                                    cr._may_call(менеджер, "op:Тест-оператор"), cr._may_call(менеджер, "drv:Тест-водитель")),
       (True, True, True, True))

    print("── D. водитель и оператор ──────────────────────────────────")
    for кто, p in (("водитель", водитель), ("оператор", оператор)):
        eq(f"D. {кто}: владелец и старший в контактах, менеджера нет", звёзды(p), ["star:1", "star:2"])
        eq(f"D. {кто}: старшие названы словом", {r["role"] for r in cr._roster(p) if r["key"].startswith("star:")}, {"старший"})
        eq(f"D. {кто}: звонить менеджеру нельзя, владельцу и старшему можно",
           (cr._may_call(p, "star:3"), cr._may_call(p, "star:1"), cr._may_call(p, "star:2")), (False, True, True))

    print("── E. сервер отказывает в обход списка ─────────────────────────────")
    async def _тихо(*a, **k): return None
    cr._ring_both = _тихо; cr._ring_timeout = _тихо          # в телеграм из теста не звоним
    async def _обнови(*a, **k): return None
    cr.refresh_staff = _обнови
    for кто, p in (("старший", старший), ("оператор", оператор), ("водитель", водитель)):
        p.ws.out.clear()
        await cr._on_message(p, {"t": "call", "to": "star:3"})
        eq(f"E. {кто} набирает менеджера по ключу — отказ", [(m.get("t"), m.get("why")) for m in p.ws.out], [("failed", "not_allowed")])
        eq(f"E. {кто}: звонок не начат", p.call, None)
    владелец.ws.out.clear()
    await cr._on_message(владелец, {"t": "call", "to": "star:3"})
    eq("E. владелец набирает менеджера — звонок идёт", [m.get("t") for m in владелец.ws.out], ["calling"])
    eq("E. …приложение у менеджера закрыто — звоним в телеграм", "телеграм" in (владелец.ws.out[0].get("note") or ""), True)
    if владелец.call:
        await cr._end(владелец.call, "cancelled")
    await asyncio.sleep(0)

    print("── F. имя ──────────────────────────────────────────────────")
    имя = lambda p, key="star:3": next(r["name"] for r in cr._roster(p) if r["key"] == key)
    eq("F. пока не входил — безымянная строка", имя(владелец), "AMBAR STAR")
    import driver_routes, operator_routes, api_server
    driver_routes._valid_init_data = lambda *a, **k: None
    operator_routes._validate_operator_init_data = lambda *a, **k: None
    КТО = {}
    api_server.validate_owner_init_data = lambda tma: dict(КТО)
    async def вход(uid, first, as_=""):
        КТО.clear(); КТО.update({"id": uid, "first_name": first})
        return await cr._identify({"tma": "x", "as": as_})
    p = await вход(3, "Тест-имя", "тест-менеджер")
    eq("F. вошёл из STAR — назван так, как его зовёт приложение", (p.kind, p.key, p.label, имя(владелец)),
       ("star", "star:3", "тест-менеджер", "тест-менеджер"))
    eq("F. имя записано в базу", await db.call_names_get(), {3: "тест-менеджер"})
    cr._STAR_SEEN.clear(); cr._NAMES_LOADED = False            # перезапуск сервиса
    eq("F. после перезапуска, до чтения базы — безымянная", имя(владелец), "AMBAR STAR")
    await cr._names_load()
    eq("F. после перезапуска имя на месте", имя(владелец), "тест-менеджер")
    p = await вход(3, "Тест-имя", "")
    eq("F. приложение имени не прислало — остаётся прежнее", (p.label, (await db.call_names_get())[3]), ("тест-менеджер", "тест-менеджер"))
    p = await вход(3, "Тест-имя", "ambar")
    eq("F. чужое имя из настроек взять нельзя — остаётся прежнее", p.label, "тест-менеджер")
    p = await вход(3, "Тест-имя", "x" * 25)
    eq("F. слишком длинное не берём", p.label, "тест-менеджер")
    p = await вход(3, "Тест-имя", "тест-другое")
    eq("F. приложение назвало иначе — имя сменилось и записано", (p.label, (await db.call_names_get())[3]), ("тест-другое", "тест-другое"))
    cr._STAR_SEEN.clear(); await db._db.call_names.delete_many({})
    p = await вход(3, "Тест-имя", "")
    eq("F. ничего не знали и приложение молчит — имя из телеграма", (p.label, (await db.call_names_get())[3]), ("Тест-имя", "Тест-имя"))
    p = await вход(2, "Другое", "Другое имя")
    eq("F. названный в настройках остаётся собой", (p.label, имя(владелец, "star:2")), ("Старший", "Старший"))
    eq("F. …и в базу не пишется", sorted(await db.call_names_get()), [3])

    print("── G. прежнее не тронуто ──────────────────────────────────")
    eq("G. верхний уровень — трое, звонит только владелец — одному",
       (len(cr._star_ids()), sorted(cr._star_ids() - cr._star_open())), (3, [3]))
    eq("G. у водителя первым свой оператор, дальше старшие", ключи(водитель)[:3], ["op:Тест-оператор", "star:1", "star:2"])
    eq("G. оператор ↔ старший — голос", cr._audio_only("op:Тест-оператор", "star:2"), True)
    eq("G. у владельца в контактах оператор и водитель", ("op:Тест-оператор" in ключи(владелец), "drv:Тест-водитель" in ключи(владелец)), (True, True))
    eq("G. в строках нет номеров телеграма", any(str(r["name"]).isdigit() for r in cr._roster(владелец)), False)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
