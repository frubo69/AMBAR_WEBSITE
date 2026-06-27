#!/usr/bin/env python3
"""AMBAR casual nudge — TEXT-ONLY, language-segmented broadcast (RU + EN only).

Sends each customer a short message in THEIR language:
  • language_code ru*  → Russian text
  • language_code en*  → English text
  • anything else / no language set → SKIPPED (no universal version)

Run ON THE VPS (BOT_TOKEN, the user DB and WEBAPP_URL live there):
    python broadcast_nudge.py --stats              # language split, sends NOTHING
    python broadcast_nudge.py --test <id> [ru|en]  # preview to yourself (both if omitted)
    python broadcast_nudge.py --send               # blast RU+EN customers, each in their language

Nothing is sent unless you pass --test or --send. Always --stats, then --test yourself.
--send is SAFE TO RE-RUN: every chat is logged so nobody is messaged twice — an
interrupted blast just resumes. Delete .broadcast_sent_nudge.txt to start a fresh campaign.
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
# Own idempotency log so it never clashes with the crypto-promo campaign.
# Bump CAMPAIGN for each new blast → a fresh send-log, so nobody from a prior nudge is skipped.
CAMPAIGN = "nudge2"
SENT_LOG = Path(os.getenv("NUDGE_SENT_LOG", HERE / (".broadcast_sent_" + CAMPAIGN + ".txt")))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Each message ≤ 4096 chars (Telegram text-message limit).
TEXT_RU = "Мы бы написали что-то умное. Но ты уже понял, зачем мы тут🤫"
TEXT_EN = "We'd write something clever. But you already know what we're here for 🤫"

# bucket → (text, button label). NO 'both'/universal bucket — those users are skipped.
BUCKETS = {
    "ru": (TEXT_RU, "🔑  Открыть AMBAR"),
    "en": (TEXT_EN, "🔑  Open AMBAR"),
}


def bucket_for(language_code):
    lc = (language_code or "").strip().lower()
    if lc.startswith("ru"):
        return "ru"
    if lc.startswith("en"):
        return "en"
    return None   # no language set / other language → skip (no universal version)


def _load_sent():
    if SENT_LOG.exists():
        return {int(x) for x in SENT_LOG.read_text().split() if x.strip().lstrip("-").isdigit()}
    return set()


def _mark_sent(tid):
    with open(SENT_LOG, "a") as f:
        f.write(f"{tid}\n")


def _kb(label):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, web_app=WebAppInfo(url=WEBAPP_URL))]])


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--stats", "--test", "--send"):
        print(__doc__); return
    if not BOT_TOKEN or not WEBAPP_URL:
        print("❌ BOT_TOKEN / WEBAPP_URL missing in .env"); return
    for cap, name in ((TEXT_RU, "RU"), (TEXT_EN, "EN")):
        if len(cap) > 4096:
            print(f"❌ {name} text too long ({len(cap)} > 4096)"); return

    await db.connect()
    users = await db.get_all_customers()

    # ── stats: show the language split, send nothing ─────────────────────────
    if mode == "--stats":
        c = Counter(bucket_for(u.get("language_code")) for u in users if u.get("telegram_id"))
        print(f"\n  Customers with a chat id: {sum(c.values())}")
        print(f"   • RU  (ru*)   → Russian text : {c['ru']}")
        print(f"   • EN  (en*)   → English text : {c['en']}")
        print(f"   • Other/none  → SKIPPED      : {c[None]}\n")
        return

    bot = Bot(token=BOT_TOKEN)

    # ── test: preview to yourself (one bucket, or both) ──────────────────────
    if mode == "--test":
        if len(sys.argv) < 3 or not sys.argv[2].lstrip("-").isdigit():
            print("Usage: python broadcast_nudge.py --test <your_telegram_id> [ru|en]"); return
        tid = int(sys.argv[2])
        pick = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] in BUCKETS else None
        for k in ([pick] if pick else ["ru", "en"]):
            txt, label = BUCKETS[k]
            await bot.send_message(tid, txt, reply_markup=_kb(label))
            log.info(f"✅ test [{k}] sent to {tid}")
            await asyncio.sleep(0.3)
        return

    # ── send: RU + EN customers, each in their language ──────────────────────
    done = _load_sent()             # chats already messaged previously → never twice
    sent = skipped_lang = skipped_ban = already = failed = 0
    by = {"ru": 0, "en": 0}
    total = len(users)
    if done:
        log.info(f"Resuming — {len(done)} chats already handled previously; they'll be skipped.")
    log.info(f"Broadcasting nudge to RU+EN customers (of {total} total)...")
    for u in users:
        tid = u.get("telegram_id")
        if not tid:
            continue
        if tid in done:             # already messaged (or unreachable) before → skip
            already += 1
            continue
        if u.get("is_banned"):
            skipped_ban += 1
            continue
        k = bucket_for(u.get("language_code"))
        if k is None:               # no language set / other language → skip, no universal version
            skipped_lang += 1
            continue
        txt, label = BUCKETS[k]
        try:
            await bot.send_message(tid, txt, reply_markup=_kb(label))
            sent += 1; by[k] += 1
            _mark_sent(tid); done.add(tid)          # record immediately — survives a crash
            if sent % 25 == 0:
                log.info(f"  sent {sent}  (ru={by['ru']} en={by['en']})...")
            await asyncio.sleep(0.05)  # ~20/s, under Telegram's broadcast limit
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ("blocked", "deactivated", "chat not found", "user is deactivated")):
                _mark_sent(tid); done.add(tid)      # permanently unreachable — don't retry
                log.debug(f"  {tid} unreachable")
            else:
                log.warning(f"  failed {tid} (will retry on re-run): {e}")
            failed += 1

    log.info(f"✅ Done. Sent: {sent} (ru={by['ru']} en={by['en']}), "
             f"SkippedNoLang: {skipped_lang}, Banned: {skipped_ban}, AlreadyDone: {already}, "
             f"Failed(retryable): {failed}, Total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
