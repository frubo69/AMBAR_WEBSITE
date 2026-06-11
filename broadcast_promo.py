#!/usr/bin/env python3
"""Crypto-payment announcement — LANGUAGE-AWARE broadcast.

Buckets (by the Telegram language_code we save on every app open / order):
  • language_code ru*   (opened, Russian)            → RU caption  + RU image
  • language_code en*   (opened, English)            → EN caption  + EN image
  • no language_code    (never opened the app)  ─┐
    OR any other language (uk, de, …)           ─┴→ BILINGUAL caption + EN image

Run ON THE VPS (BOT_TOKEN, the user DB and WEBAPP_URL live there):
    python broadcast_promo.py --stats                    # language split, sends NOTHING
    python broadcast_promo.py --test <id> [ru|en|both]   # preview to yourself (all 3 if omitted)
    python broadcast_promo.py --send                     # blast everyone, each in their language

Nothing is sent unless you pass --test or --send. Always --stats, then --test yourself.
Drop the two images next to this file:  crypto_ru.png  and  crypto_en.png
(or override with PROMO_PHOTO_RU / PROMO_PHOTO_EN in the env).
"""
import asyncio, os, sys, logging
from collections import Counter
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import db

load_dotenv()
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

HERE = Path(__file__).resolve().parent
PHOTO_RU = Path(os.getenv("PROMO_PHOTO_RU", HERE / "CRYPTO_PROMO_AMBAR_RU.png"))
PHOTO_EN = Path(os.getenv("PROMO_PHOTO_EN", HERE / "CRYPTO_PROMO_AMBAR_EN.png"))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Captions (HTML bold on the titles). Each must stay ≤ 1024 chars (photo-caption limit).
CAPTION_RU = (
    "Крипта? Принимаем. 🪙\n"
    "Вопросы? Не задаём. 🤫\n"
    "USDT TRC-20 — уже в AMBAR ⚡️"
)
CAPTION_EN = (
    "Crypto? Accepted. 🪙\n"
    "Questions? Not asked. 🤫\n"
    "USDT TRC-20 — live in AMBAR ⚡️"
)
CAPTION_BOTH = (
    "Крипта? Принимаем. 🪙\n"
    "Вопросы? Не задаём. 🤫\n"
    "USDT TRC-20 — уже в AMBAR ⚡️\n"
    "—\n"
    "Crypto? Accepted. 🪙\n"
    "Questions? Not asked. 🤫\n"
    "USDT TRC-20 — live in AMBAR ⚡️"
)

# bucket → (caption, image path, button label). 'both' = universal text + EN picture.
BUCKETS = {
    "ru":   (CAPTION_RU,   PHOTO_RU, "🔑  Открыть AMBAR"),
    "en":   (CAPTION_EN,   PHOTO_EN, "🔑  Open AMBAR"),
    "both": (CAPTION_BOTH, PHOTO_EN, "🔑  Open AMBAR"),
}


def bucket_for(language_code: str) -> str:
    lc = (language_code or "").strip().lower()
    if lc.startswith("ru"):
        return "ru"
    if lc.startswith("en"):
        return "en"
    return "both"   # never opened (blank) OR a language we don't have → bilingual + EN image


def _kb(label):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, web_app=WebAppInfo(url=WEBAPP_URL))]])


async def _send_one(bot, tid, photo, caption, kb):
    msg = await bot.send_photo(tid, photo=photo, caption=caption, parse_mode="HTML", reply_markup=kb)
    return msg.photo[-1].file_id if msg.photo else None


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--stats", "--test", "--send"):
        print(__doc__); return
    if not BOT_TOKEN or not WEBAPP_URL:
        print("❌ BOT_TOKEN / WEBAPP_URL missing in .env"); return
    for cap, name in ((CAPTION_RU, "RU"), (CAPTION_EN, "EN"), (CAPTION_BOTH, "BOTH")):
        if len(cap) > 1024:
            print(f"❌ {name} caption too long ({len(cap)} > 1024)"); return

    await db.connect()
    users = await db.get_all_customers()

    # ── stats: show the language split, send nothing ─────────────────────────
    if mode == "--stats":
        c = Counter(bucket_for(u.get("language_code")) for u in users if u.get("telegram_id"))
        tot = sum(c.values())
        print(f"\n  Customers with a chat id: {tot}")
        print(f"   • RU  (ru*)               → RU image            : {c['ru']}")
        print(f"   • EN  (en*)               → EN image            : {c['en']}")
        print(f"   • Other / never opened    → bilingual + EN image: {c['both']}\n")
        return

    bot = Bot(token=BOT_TOKEN)

    # ── test: preview to yourself (one bucket, or all three) ──────────────────
    if mode == "--test":
        if len(sys.argv) < 3 or not sys.argv[2].lstrip("-").isdigit():
            print("Usage: python broadcast_promo.py --test <your_telegram_id> [ru|en|both]"); return
        tid = int(sys.argv[2])
        pick = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] in BUCKETS else None
        for k in ([pick] if pick else ["ru", "en", "both"]):
            cap, img, label = BUCKETS[k]
            if not img.exists():
                print(f"❌ image missing for '{k}': {img}"); continue
            with open(img, "rb") as f:
                await _send_one(bot, tid, f, cap, _kb(label))
            log.info(f"✅ test [{k}] sent to {tid}")
            await asyncio.sleep(0.3)
        return

    # ── send: everyone, each in their language ────────────────────────────────
    for k, (cap, img, label) in BUCKETS.items():
        if not img.exists():
            print(f"❌ image missing for bucket '{k}': {img}  — drop the file and retry."); return

    file_ids = {}   # bucket → reused file_id (upload each image just once)
    sent = skipped_ban = failed = 0
    by = {"ru": 0, "en": 0, "both": 0}
    total = len(users)
    log.info(f"Broadcasting to {total} customers, language-aware...")
    for u in users:
        tid = u.get("telegram_id")
        if not tid:
            continue
        if u.get("is_banned"):
            skipped_ban += 1
            continue
        k = bucket_for(u.get("language_code"))
        cap, img, label = BUCKETS[k]
        kb = _kb(label)
        try:
            if file_ids.get(k):
                fid = await _send_one(bot, tid, file_ids[k], cap, kb)
                if fid:
                    file_ids[k] = fid
            else:
                with open(img, "rb") as f:
                    file_ids[k] = await _send_one(bot, tid, f, cap, kb)
            sent += 1; by[k] += 1
            if sent % 25 == 0:
                log.info(f"  sent {sent}/{total}  (ru={by['ru']} en={by['en']} both={by['both']})...")
            await asyncio.sleep(0.05)  # ~20/s, under Telegram's broadcast limit
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ("blocked", "deactivated", "chat not found")):
                log.debug(f"  {tid} unreachable")
            else:
                log.warning(f"  failed {tid}: {e}")
            failed += 1

    log.info(f"✅ Done. Sent: {sent} (ru={by['ru']} en={by['en']} both={by['both']}), "
             f"Banned: {skipped_ban}, Failed: {failed}, Total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
