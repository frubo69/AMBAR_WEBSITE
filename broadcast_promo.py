#!/usr/bin/env python3
"""One-shot broadcast: bilingual crypto-payment announcement (photo + caption + button).

Run ON THE VPS (where BOT_TOKEN, the user DB and WEBAPP_URL live):

    python broadcast_promo.py --test <your_telegram_id>   # preview to yourself only
    python broadcast_promo.py --send                      # blast ALL customers

Nothing is sent unless you pass --test or --send. Always --test yourself first.
"""
import asyncio, os, sys, logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import db

load_dotenv()
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

HERE = Path(__file__).resolve().parent
# The announcement image. Drop the file here as crypto_announce.png (or set PROMO_PHOTO).
PHOTO_PATH = Path(os.getenv("PROMO_PHOTO", HERE / "crypto_announce.png"))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Bilingual caption. HTML bold on the titles; rest plain. Must stay ≤ 1024 chars
# (Telegram photo-caption limit). Apostrophes/parentheses are safe in HTML mode.
CAPTION = (
    "<b>🥂  Оплата криптой</b>\n"
    "USDT (TRC-20) — уже скоро в AMBAR.\n"
    "Быстро. Приватно. Без границ.\n"
    "Это не просто оплата.\n"
    "Это свобода.\n"
    "─────────────\n"
    "<b>🥂  Crypto payments</b>\n"
    "USDT (TRC-20) — coming soon to AMBAR.\n"
    "Fast. Private. Borderless.\n"
    "This isn't just payment.\n"
    "It's freedom."
)


async def _send_one(bot, tid, photo, kb):
    """Send photo + caption + button to one chat; return the sent photo's file_id."""
    msg = await bot.send_photo(tid, photo=photo, caption=CAPTION,
                               parse_mode="HTML", reply_markup=kb)
    return msg.photo[-1].file_id if msg.photo else None


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--test", "--send"):
        print(__doc__); return
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing in .env"); return
    if not WEBAPP_URL:
        print("❌ WEBAPP_URL missing in .env"); return
    if not PHOTO_PATH.exists():
        print(f"❌ announcement photo not found: {PHOTO_PATH}"); return
    if len(CAPTION) > 1024:
        print(f"❌ caption too long for a photo ({len(CAPTION)} > 1024)"); return

    bot = Bot(token=BOT_TOKEN)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔑  Open AMBAR", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])

    # ── Preview to a single chat (yourself) ──────────────────────────────────
    if mode == "--test":
        if len(sys.argv) < 3 or not sys.argv[2].lstrip("-").isdigit():
            print("Usage: python broadcast_promo.py --test <your_telegram_id>"); return
        tid = int(sys.argv[2])
        with open(PHOTO_PATH, "rb") as f:
            await _send_one(bot, tid, f, kb)
        log.info(f"✅ test announcement sent to {tid}")
        return

    # ── Blast every customer ─────────────────────────────────────────────────
    await db.connect()
    users = await db.get_all_customers()
    total = len(users)
    sent = skipped_ban = failed = 0
    file_id = None  # upload the photo once, then reuse its file_id (fast + light)
    log.info(f"Broadcasting crypto announcement to {total} users...")
    for u in users:
        tid = u.get("telegram_id")
        if not tid:
            continue
        if u.get("is_banned"):
            skipped_ban += 1
            continue
        try:
            if file_id:
                file_id = await _send_one(bot, tid, file_id, kb) or file_id
            else:
                with open(PHOTO_PATH, "rb") as f:
                    file_id = await _send_one(bot, tid, f, kb)
            sent += 1
            if sent % 25 == 0:
                log.info(f"  sent {sent}/{total}...")
            await asyncio.sleep(0.05)  # ~20/s, under Telegram's broadcast limit
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("blocked", "deactivated", "chat not found")):
                log.debug(f"  {tid} unreachable")
            else:
                log.warning(f"  failed {tid}: {e}")
            failed += 1

    log.info(f"✅ Done. Sent: {sent}, Banned(skipped): {skipped_ban}, "
             f"Failed: {failed}, Total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
