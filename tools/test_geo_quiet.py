"""Телефон молчит при живой трансляции, а у водителя заказ в пути — напоминание
ему в бот водителя, не чаще раза в двадцать минут (15 сен 2026). Без базы."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import geo_watch as gw, driver_routes as dr, db
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
ST, SENT, ROUTE = {}, [], {"v": ["AMB1"]}
async def in_route(me): return list(ROUTE["v"])
async def tell(name, text): SENT.append((name, text))
async def watch_set(name, fields, unset=None): ST.update(fields)
dr._in_route = in_route; gw._driver = tell; db.geo_watch_set = watch_set
T = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
g = lambda stream, age: {"stream": stream, "age_sec": age}
async def main():
    eq("точка 5 мин назад — рано", await gw._quiet_nag("Худоба", g(True, 300), T, dict(ST)), False)
    eq("трансляции нет — не наше дело (это «выключил»)", await gw._quiet_nag("Худоба", g(False, 1200), T, dict(ST)), False)
    ROUTE["v"] = []
    eq("молчит 20 мин, но заказов в пути нет — молчим", await gw._quiet_nag("Худоба", g(True, 1200), T, dict(ST)), False)
    ROUTE["v"] = ["AMB1"]
    eq("молчит 20 мин с заказом — напомнили", (await gw._quiet_nag("Худоба", g(True, 1200), T, dict(ST)), len(SENT), ST.get("nag_at")), (True, 1, T))
    eq("в тексте — сколько минут, iPhone и Android", ("20 мин" in SENT[0][1], "iPhone" in SENT[0][1], "Android" in SENT[0][1], "заказ в пути" in SENT[0][1]), (True, True, True, True))
    eq("через 10 минут — не повторяем", await gw._quiet_nag("Худоба", g(True, 1800), T + timedelta(minutes=10), dict(ST)), False)
    eq("через 21 минуту — снова", (await gw._quiet_nag("Худоба", g(True, 2460), T + timedelta(minutes=21), dict(ST)), len(SENT)), (True, 2))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
