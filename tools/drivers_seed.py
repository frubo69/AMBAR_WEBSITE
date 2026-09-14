"""Перенос водителей в базу (15 сен 2026).

Заводит в drivers всех водителей из расписания (config_staff.DISTRICT_STAFF)
с их районами; у кого телефон уже был в .env (AMBAR_DRIVER_IDS) — переносит
его, чтобы никого не просить привязываться заново. Исключение — имена из
SKIP_PHONE: их телефон в базу не переносится (владелец снят с «Худобы»).
Заводит «Тест-водителя» (test: true) и выдаёт ему ссылку для входа.

Идемпотентно: уже привязанных и уже заведённых не трогает. Id не печатает.
Запуск на VPS:  venv/bin/python tools/drivers_seed.py --apply
"""
import asyncio, os, secrets, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import db, config_staff as staff
from config import TEST_DRIVER_NAME

SKIP_PHONE = {"Худоба"}
ABC = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def bot_username() -> str:
    u = (os.getenv("DRIVER_BOT_USERNAME") or "").strip().lstrip("@")
    if u:
        return u
    token = os.getenv("DRIVER_BOT_TOKEN", "")
    if not token:
        return ""
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as r:
            return (json.load(r).get("result") or {}).get("username", "")
    except Exception as e:                                   # noqa: BLE001
        print("бот не назвался:", e)
        return ""


async def main(apply: bool):
    await db.connect()
    have = {r["name"]: r for r in await db.get_driver_links()}
    env_ids = staff._ENV_DRIVER_IDS
    plan = []
    for st in staff.DISTRICT_STAFF:
        for name in st["drivers"]:
            row = have.get(name)
            tid = env_ids.get(name)
            phone = "есть" if (row or {}).get("telegram_id") else ("перенос из .env" if tid and name not in SKIP_PHONE else "нет")
            plan.append((name, st["district"], "есть" if row else "новая", phone))
    for name, d, rec, phone in plan:
        print(f"  {name:12} {d:8} запись: {rec:6} телефон: {phone}")
    print(f"  {TEST_DRIVER_NAME:12} {'—':8} запись: {'есть' if TEST_DRIVER_NAME in have else 'новая':6} тест · ссылка для входа")
    if not apply:
        print("\nсухой прогон — добавьте --apply")
        return
    for name, d, rec, phone in plan:
        if rec == "новая":
            await db.driver_add(name, d, 0)
        if phone == "перенос из .env":
            await db.driver_adopt(name, env_ids[name], 0)
    if TEST_DRIVER_NAME not in have:
        await db.driver_add(TEST_DRIVER_NAME, staff.DISTRICT_STAFF[0]["district"], 0, test=True)
    code = "".join(secrets.choice(ABC) for _ in range(8))
    await db.set_driver_code(TEST_DRIVER_NAME, code, 0)
    u = bot_username()
    print("\nготово. ссылка для входа тест-водителя (один вход, 7 дней):")
    print(f"  https://t.me/{u}?start=drv_{code}" if u else f"  код: {code}")
    rows = await db.get_driver_links()
    print("\nв базе:", ", ".join(f"{r['name']}{'·тел' if r.get('telegram_id') else ''}{'·тест' if r.get('test') else ''}" for r in rows))

asyncio.run(main("--apply" in sys.argv))
