#!/usr/bin/env python3
"""Учебное восстановление: доказать, что копия рабочая.

Копия, которую ни разу не разворачивали, — это файл, а не резервная копия.
Раз в месяц берём самую свежую, поднимаем её в отдельную базу, сверяем
количество документов с боевой и удаляем учебную. Итог уходит владельцу в
телеграм: не «копии вроде есть», а «сегодня развернул, всё сошлось».

    python3 tools/restore_drill.py           # прогнать и отчитаться
    python3 tools/restore_drill.py --quiet   # без сообщения в телеграм
"""
import argparse
import asyncio
import glob
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ambar")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # рядом лежит backup.py

import motor.motor_asyncio                                      # noqa: E402
from backup import _env, _uri, restore, DIR                     # noqa: E402

DRILL_DB = "ambar_drill"
# Коллекции, по которым сверяем: то, потеря чего заметна деньгами и людьми.
WATCH = ["orders", "users", "owner_notifications", "qr_codes", "support_messages",
         "zayavki", "supplies", "crypto_invoices"]


def notify(text: str) -> None:
    token = _env("AMBAR_OWNER_BOT_TOKEN")
    ids = [i for i in _env("AMBAR_OWNER_IDS").replace(" ", "").split(",") if i.isdigit()]
    if not (token and ids):
        return
    for uid in ids:
        data = urllib.parse.urlencode({"chat_id": uid, "text": text,
                                       "parse_mode": "Markdown"}).encode()
        try:
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as e:
            print(f"не отправилось {uid}: {e}")


async def drill(quiet: bool) -> int:
    files = sorted(glob.glob(os.path.join(DIR, "ambar-*.jsonl.gz")))
    if not files:
        notify("🔴 *AMBAR — проверка копий*\nКопий нет вообще.")
        return 1
    newest = files[-1]

    cli = motor.motor_asyncio.AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=20000)
    live = cli["ambar"]
    live_counts = {c: await live[c].count_documents({}) for c in WATCH}

    await restore(newest, DRILL_DB, yes=True)
    drilled = cli[DRILL_DB]
    got = {c: await drilled[c].count_documents({}) for c in WATCH}
    await cli.drop_database(DRILL_DB)

    # Копия снята раньше, чем сделан этот замер, поэтому меньше — норма,
    # больше — нет: значит в боевой чего-то не хватает.
    bad = [c for c in WATCH if got[c] > live_counts[c] or got[c] == 0 < live_counts[c]]
    when = datetime.fromtimestamp(os.path.getmtime(newest)).strftime("%d.%m %H:%M")
    lines = [f"• {c}: копия {got[c]} · сейчас {live_counts[c]}" for c in WATCH]
    head = ("🟢 *AMBAR — копия проверена*" if not bad
            else "🔴 *AMBAR — проверка копии не сошлась*")
    text = (f"{head}\nФайл от {when}, {os.path.getsize(newest)/1024:.0f} КБ\n\n"
            + "\n".join(lines))
    print(text)
    if not quiet:
        notify(text)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(drill(a.quiet)))


if __name__ == "__main__":
    main()
