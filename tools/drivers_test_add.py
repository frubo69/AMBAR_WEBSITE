"""Тест-аккаунт водителя: завести и выдать ссылку для входа (18 сен 2026).

Тест-водитель — не один: 18 сен 2026 второй заведён старшему оператору, чтобы
он видел приложение водителя своими глазами. Имя из аргумента — это имя в
учёте: по нему пишутся его смены и дни (и прячутся из денег), по нему же ему
уходят сообщения бота. Район в записи роли не играет: тест-водитель живёт в
тест-районе, что бы ни стояло в строке.

Идемпотентно: запись уже есть — только новая ссылка. Привязанному аккаунту
ссылку выдаём лишь с --force: это смена телефона, старый теряет доступ.
Id в вывод не печатаются.

    venv/bin/python tools/drivers_test_add.py "Тест-Водитель(Парвиз)"            # сухой прогон
    venv/bin/python tools/drivers_test_add.py "Тест-Водитель(Парвиз)" --apply
"""
import asyncio, json, os, re, secrets, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import db, config_staff as staff

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


async def main(name: str, apply: bool, force: bool):
    await db.connect()
    rows = await db.get_driver_links()
    row = next((r for r in rows if r.get("name") == name), None)
    if row and not row.get("test"):
        print(f"«{name}» уже заведён настоящим водителем — тест-аккаунт под этим именем не нужен")
        return 1
    linked = bool((row or {}).get("telegram_id"))
    print(f"  {name}")
    print(f"  запись: {'есть' if row else 'новая'} · тест · телефон: {'привязан' if linked else 'нет'}")
    if linked and not force:
        print("\nаккаунт уже привязан. Новая ссылка отвяжет прежний телефон — добавьте --force")
        return 1
    if not apply:
        print("\nсухой прогон — добавьте --apply")
        return 0
    if not row:
        await db.driver_add(name, staff.DISTRICT_STAFF[0]["district"], 0, test=True)
    code = "".join(secrets.choice(ABC) for _ in range(8))
    await db.set_driver_code(name, code, 0)
    u = bot_username()
    print("\nготово. ссылка для входа (один вход, 7 дней):")
    print(f"  https://t.me/{u}?start=drv_{code}" if u else f"  код: {code}")
    rows = await db.get_driver_links()
    print("\nтест-аккаунты в базе: " + ", ".join(
        f"{r['name']}{'·тел' if r.get('telegram_id') else ''}" for r in rows if r.get("test")))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    nm = re.sub(r"\s+", " ", args[0] if args else "").strip()[:40]
    if len(nm) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(nm, "--apply" in sys.argv, "--force" in sys.argv)))
