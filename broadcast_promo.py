#!/usr/bin/env python3
"""One-shot broadcast: send bilingual promo message to all users."""
import asyncio, os, logging
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import db

load_dotenv()
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TEXT = (
    "🥂  *Цены для своих*\n\n"
    "−5% на всё в AMBAR\\.\n"
    "Для всех, кто с нами\\.\n\n"
    "Это не акция\\.\n"
    "Это привилегия\\.\n\n"
    "─────────────\n\n"
    "🥂  *Insider pricing*\n\n"
    "−5% on everything at AMBAR\\.\n"
    "For everyone who's with us\\.\n\n"
    "This isn't a promotion\\.\n"
    "It's a privilege\\."
)


async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing"); return
    if not WEBAPP_URL:
        print("❌ WEBAPP_URL missing"); return

    await db.connect()
    bot = Bot(token=BOT_TOKEN)

    users = await db.get_all_customers()
    total = len(users)
    sent = 0
    skipped_ban = 0
    failed = 0

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔑  Open AMBAR", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])

    log.info(f"Broadcasting to {total} users...")

    for u in users:
        tid = u.get("telegram_id")
        if not tid:
            continue
        if u.get("is_banned"):
            skipped_ban += 1
            continue

        try:
            await bot.send_message(tid, TEXT, parse_mode="MarkdownV2", reply_markup=kb)
            sent += 1
            if sent % 25 == 0:
                log.info(f"  sent {sent}/{total}...")
            await asyncio.sleep(0.05)
        except Exception as e:
            err = str(e)
            if "blocked" in err.lower() or "deactivated" in err.lower():
                log.debug(f"  user {tid} blocked/deactivated")
            else:
                log.warning(f"  failed {tid}: {e}")
            failed += 1

    log.info(f"✅ Done. Sent: {sent}, Banned(skipped): {skipped_ban}, Failed: {failed}, Total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
