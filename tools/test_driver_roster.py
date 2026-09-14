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
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
