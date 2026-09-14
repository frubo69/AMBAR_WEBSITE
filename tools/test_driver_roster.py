"""Реестр водителей из базы и привязка по коду (15 сен 2026). Без базы."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import config_staff as staff
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

print("— реестр")
staff._ENV_DRIVER_IDS = {"Худоба": 1, "Фарух": 2}
staff.apply_roster([
    {"name": "Худоба", "district": "jvc", "telegram_id": None},          # в базе, но без телефона — .env для него не читаем
    {"name": "Фарух", "district": "tecom", "telegram_id": 22},           # телефон из базы сильнее .env, район тоже
    {"name": "Новый", "district": "bbay", "telegram_id": 3},             # водитель, которого в коде нет
    {"name": "Без района", "district": "", "telegram_id": 4},            # не водитель
    {"name": "Тест-водитель", "district": "jvc", "telegram_id": 9, "test": True},
])
eq("Худоба без телефона (владелец снят)", staff.DRIVER_IDS.get("Худоба"), None)
eq("Фарух: телефон из базы", staff.DRIVER_IDS.get("Фарух"), 22)
eq("Фарух: район из базы", staff.base_district("Фарух"), "tecom")
eq("Новый в списке водителей", "Новый" in staff.driver_names(), True)
eq("Новый в районе bbay", "Новый" in staff.DISTRICT_DRIVERS.get("bbay", []), True)
eq("без района — не водитель", "Без района" in staff.driver_names(), False)
eq("driver_by_tg(3)", (staff.driver_by_tg(3) or {}).get("name"), "Новый")
eq("тест-водитель не в расписании", "Тест-водитель" in staff.driver_names(), False)
t = staff.driver_by_tg(9) or {}
eq("driver_by_tg(9) — тест-персона", (t.get("test"), t.get("name")), (True, "Тест-водитель"))
eq("чат тест-водителя", 9 in staff.driver_chats("Тест-водитель"), True)
eq("чужой id — никто", staff.driver_by_tg(777), None)
eq("driver_chats Фарух", staff.driver_chats("Фарух"), [22])

print("— привязка по коду")
import driver_bot as dbot
now = datetime.now(timezone.utc)
ROWS = {"GOODCODE": {"name": "Худоба", "code": "GOODCODE", "code_at": now, "telegram_id": None},
        "OLDCODE1": {"name": "Азиз", "code": "OLDCODE1", "code_at": now - timedelta(days=8), "telegram_id": None}}
async def get_by_code(c): return ROWS.get(c)
async def link(c, tid, tg):
    r = ROWS.get(c)
    if not r or r.get("telegram_id"): return None
    r["telegram_id"] = tid; r["tg_name"] = tg.get("first_name"); return dict(r)
dbot.db.get_driver_by_code = get_by_code; dbot.db.link_driver = link
async def main():
    st, r = await dbot.bind_code(100, "good-code", {"first_name": "Иван", "username": "ivan"})
    eq("верный код (с дефисом, строчными)", (st, (r or {}).get("name")), ("ok", "Худоба"))
    st, r = await dbot.bind_code(101, "GOODCODE", {"first_name": "Пётр"})
    eq("тот же код второй раз — нет", st, "bad")
    st, r = await dbot.bind_code(102, "OLDCODE1", {"first_name": "X"})
    eq("просроченный код — нет", st, "bad")
    st, r = await dbot.bind_code(103, "NOPE", {"first_name": "X"})
    eq("короткий мусор — нет", st, "bad")
    for i in range(5): await dbot.bind_code(104, f"ZZZZZZZ{i}", {})
    st, r = await dbot.bind_code(104, "GOODCODE", {})
    eq("после пяти промахов — перебор", st, "rate")
    eq("владельцу сообщений без токена не шлём (тихо)", await dbot._tell_owners("x"), None)
    print("ИТОГ-1:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
asyncio.run(main())

# ── ручки STAR: выдача ссылки, отвязка, добавление — с подменённой базой ──
print("— ручки STAR")
import json as _json
from aiohttp.test_utils import make_mocked_request
import owner_routes as owr
STORE = {"Худоба": {"name": "Худоба", "district": "jvc", "telegram_id": None, "code": None},
         "Тест-водитель": {"name": "Тест-водитель", "district": "jvc", "telegram_id": 5, "test": True}}
async def links(): return [dict(v) for v in STORE.values()]
async def set_code(name, code, by): STORE[name].update({"code": code, "code_at": datetime.now(timezone.utc), "telegram_id": None})
async def unlink(name): STORE[name].update({"telegram_id": None, "code": None}); return True
async def add(name, district, by=0, test=False): STORE.setdefault(name, {"name": name, "telegram_id": None, "code": None}).update({"district": district, "test": test})
async def maps(): return {}
owr.db.get_driver_links = links; owr.db.set_driver_code = set_code; owr.db.unlink_driver = unlink; owr.db.driver_add = add
owr.db.staff_map_get = maps; owr.db.driver_map_get = maps
staff.db = owr.db
import config_staff
async def _links(): return await links()
config_staff_db = __import__('db'); config_staff_db.get_driver_links = links; config_staff_db.staff_map_get = maps; config_staff_db.driver_map_get = maps
async def username(): return "ambardriver_bot"
owr._driver_bot_username = username
def oreq(path, body):
    r = make_mocked_request("POST", path); r._read_bytes = _json.dumps(body).encode(); r["owner_id"] = 1; return r
async def call(h, r):
    resp = await h.__wrapped__(r); return resp.status, _json.loads(resp.text)
async def main2():
    st, r = await call(owr.handle_drivers_code, oreq("/api/owner/drivers/code", {"name": "Худоба"}))
    eq("ссылка: статус", st, 200); eq("ссылка: вид", (len(r.get("code", "")), r.get("link", "").startswith("https://t.me/ambardriver_bot?start=drv_")), (8, True))
    st, r = await call(owr.handle_drivers_code, oreq("/api/owner/drivers/code", {"name": "Тест-водитель"}))
    eq("привязанному без force → 409", (st, r.get("error")), (409, "linked"))
    st, r = await call(owr.handle_drivers_code, oreq("/api/owner/drivers/code", {"name": "Тест-водитель", "force": True}))
    eq("с force — новая ссылка, телефон снят", (st, STORE["Тест-водитель"]["telegram_id"]), (200, None))
    st, r = await call(owr.handle_drivers_code, oreq("/api/owner/drivers/code", {"name": "Никто"}))
    eq("неизвестный → 404", st, 404)
    st, r = await call(owr.handle_drivers_unlink, oreq("/api/owner/drivers/unlink", {"name": "Худоба"}))
    eq("отвязка: статус", st, 200); eq("отвязка: в ответе список без id", all("telegram_id" not in l for l in r.get("links", [])), True)
    st, r = await call(owr.handle_drivers_add, oreq("/api/owner/drivers/add", {"name": "Новый", "district": "bbay"}))
    eq("добавление: статус и запись", (st, STORE.get("Новый", {}).get("district")), (200, "bbay"))
    st, r = await call(owr.handle_drivers_add, oreq("/api/owner/drivers/add", {"name": "X", "district": "bbay"}))
    eq("короткое имя → 400", st, 400)
    print("ИТОГ-2:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main2())
