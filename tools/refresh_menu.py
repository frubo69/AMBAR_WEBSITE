#!/usr/bin/env python3
"""Переставить всем кнопку мини-приложения на текущий адрес.

Бот ставит кнопку каждому персонально (set_chat_menu_button с chat_id), и она
живёт в чате вечно — со старым адресом внутри. Поэтому смена адреса приложения
обязана сопровождаться этим проходом, иначе у людей в телеграме продолжает
открываться то, чего больше нет.
"""
import asyncio, os, sys, aiohttp
from dotenv import load_dotenv
sys.path.insert(0, "/opt/ambar")
load_dotenv("/opt/ambar/.env")
import db

BOT = os.getenv("BOT_TOKEN", "")
URL = os.getenv("WEBAPP_URL", "").rstrip("/") + "/"
TEXT = {"ru": "🍾 Заказать", "en": "🍾 Order"}


async def main():
    await db.connect()
    users = await db.get_all_customers()
    ids = [(int(u["telegram_id"]), (u.get("lang") or "ru")) for u in users if u.get("telegram_id")]
    print(f"адрес: {URL}\nпользователей: {len(ids)}")
    ok = blocked = err = 0
    async with aiohttp.ClientSession() as s:
        for i, (uid, lang) in enumerate(ids, 1):
            body = {"chat_id": uid, "menu_button": {
                "type": "web_app",
                "text": TEXT.get(lang, TEXT["ru"]),
                "web_app": {"url": URL}}}
            for attempt in range(3):
                try:
                    async with s.post(f"https://api.telegram.org/bot{BOT}/setChatMenuButton",
                                      json=body, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        d = await r.json()
                except Exception:
                    err += 1; break
                if d.get("ok"):
                    ok += 1; break
                desc = (d.get("description") or "").lower()
                if "retry after" in desc or d.get("error_code") == 429:
                    await asyncio.sleep(d.get("parameters", {}).get("retry_after", 2)); continue
                if "blocked" in desc or "not found" in desc or "deactivated" in desc:
                    blocked += 1; break
                err += 1; break
            await asyncio.sleep(0.04)          # ~25 запросов в секунду
            if i % 100 == 0:
                print(f"  {i}/{len(ids)} · обновлено {ok}, заблокировали бота {blocked}, ошибок {err}")
    print(f"ИТОГО: обновлено {ok} · заблокировали бота {blocked} · ошибок {err}")

asyncio.run(main())
