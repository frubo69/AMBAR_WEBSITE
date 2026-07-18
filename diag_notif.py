"""Diagnostic: why does owner/manager X get (or not get) a given notification?

Runs the REAL db.get_owners_subscribed_to — the same code path the server uses —
so the answer matches production exactly.

    python diag_notif.py                 # default target
    python diag_notif.py 8854333070      # any owner/manager id
    python diag_notif.py 8854333070 --send   # also fire a test message to them
"""
import asyncio, json, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

args = [a for a in sys.argv[1:] if not a.startswith("-")]
TARGET = int(args[0]) if args and args[0].lstrip("-").isdigit() else 686932322
DO_SEND = "--send" in sys.argv

# The events worth checking at a glance — order matters for readability.
CHECK_EVENTS = [
    "orders.new", "orders.new500", "orders.new1000",
    "orders.delivered", "orders.cancelled", "orders.declined",
    "customers.new", "customers.verify",
    "support.new", "support.complaint", "support.replied",
    "timing.notAccepted5",
]


async def main():
    import db
    from config import OWNER_IDS, MANAGER_IDS

    await db.connect()
    _db = db._db

    print(f"\n{'='*64}\nTARGET {TARGET}\n{'='*64}")
    is_owner = TARGET in OWNER_IDS
    is_env_mgr = TARGET in MANAGER_IDS
    try:
        is_db_mgr = await db.is_manager(TARGET)
    except Exception as e:
        is_db_mgr = f"(lookup failed: {e})"
    print(f"  in OWNER_IDS      : {is_owner}")
    print(f"  in MANAGER_IDS    : {is_env_mgr}")
    print(f"  db.is_manager     : {is_db_mgr}")
    if not (is_owner or is_env_mgr or is_db_mgr is True):
        print("  ⚠️  NOT a known owner/manager → would receive NOTHING from notify_owners()")

    # ── prefs doc ────────────────────────────────────────────────────────
    docs = await _db.owner_prefs.find({"owner_id": TARGET}).to_list(10)
    print(f"\n{'-'*64}\nowner_prefs docs: {len(docs)}\n{'-'*64}")
    if not docs:
        print("  none → server applies _DEFAULT_PREFS (orders.new ON, orders.delivered OFF)")
    for d in docs:
        d["_id"] = str(d["_id"])
        master = d.get("master")
        quiet = d.get("quiet")
        print(f"  master={master}  quiet={quiet}  updated_at={d.get('updated_at')}")
        p = None
        if "prefs_json" in d:
            try:
                p = json.loads(d["prefs_json"])
            except Exception as e:
                print(f"  prefs_json PARSE ERROR: {e}  raw={repr(d['prefs_json'][:160])}")
        elif isinstance(d.get("prefs"), dict):
            p = d["prefs"]
        if isinstance(p, dict):
            on = sorted(k for k, v in p.items() if v is True)
            off = sorted(k for k, v in p.items() if v is not True)
            print(f"  ✅ ON  ({len(on)}): {', '.join(on) or '—'}")
            print(f"  ⛔ OFF ({len(off)}): {', '.join(off) or '—'}")

    # ── the real subscriber check ────────────────────────────────────────
    print(f"\n{'-'*64}\nWould {TARGET} receive… (real get_owners_subscribed_to)\n{'-'*64}")
    for ek in CHECK_EVENTS:
        try:
            subs = await db.get_owners_subscribed_to(ek)
            mark = "✅" if TARGET in subs else "❌"
            print(f"  {mark} {ek:<24} (total recipients: {len(subs)})")
        except Exception as e:
            print(f"  ⚠️  {ek:<24} lookup failed: {e}")

    print("\n  NOTE: manual (phone) and crypto orders bypass these prefs entirely —")
    print("        they force-send to every manager.")

    # ── optional live send ───────────────────────────────────────────────
    if DO_SEND:
        tok = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
        print(f"\n{'-'*64}\nTest send via owner bot\n{'-'*64}")
        if not tok:
            print("  AMBAR_OWNER_BOT_TOKEN is empty!")
        else:
            import aiohttp
            url = f"https://api.telegram.org/bot{tok}/sendMessage"
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json={"chat_id": TARGET,
                                             "text": "🔔 diag_notif test"}) as resp:
                    r = await resp.json()
                    print(f"  ok={r.get('ok')} error={r.get('description','none')}")
                    if not r.get("ok"):
                        print("  → if 'chat not found': that account never pressed /start in the owner bot")

    db.close()

asyncio.run(main())
