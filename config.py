import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("AMBAR_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# Telegram IDs allowed to access the owner dashboard (/api/owner/*).
# Separate from ADMIN_IDS — admins may have support/mod powers without
# owning the business.
OWNER_IDS = {
    int(x.strip())
    for x in os.getenv("AMBAR_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}

# Managers get the same /api/owner/* access as owners but cannot mutate the
# managers list themselves (owner-only). All IDs come from env var
# AMBAR_MANAGER_IDS (comma-separated).
MANAGER_IDS = {
    int(x.strip())
    for x in os.getenv("AMBAR_MANAGER_IDS", "").split(",")
    if x.strip().isdigit()
}

# Token for @ambar_manage_bot — the bot that launches the owner miniapp.
# Separate from the customer BOT_TOKEN because initData is HMAC'd per-bot:
# a miniapp launched from @ambar_manage_bot produces initData signed with
# THIS token, which is what validate_owner_init_data() checks.
OWNER_BOT_TOKEN = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")

AUTO_REPLIES = {
    "price": "💰 Pricing: https://example.com/pricing",
    "help": "🆘 Please describe your issue, a human will reply shortly.",
    "hello": "👋 Hi! Send us your question anytime."
}

# ── Crypto payments: USDT (TRC-20) on TRON ────────────────────────────────────
# Watch-only by design: the server only READS the chain to credit the matching
# order. No private keys ever live here — funds land directly in the merchant
# wallet at TRON_RECEIVE_ADDRESS. "Real mode" turns on only when BOTH the
# receive address and a TronGrid key are present; until then /api/crypto/*
# reports unavailable and the frontend keeps using its client-side demo.
def _getenv_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default

def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default

TRON_RECEIVE_ADDRESS = os.getenv("AMBAR_TRON_RECEIVE_ADDRESS", "").strip()
TRONGRID_API_KEY     = os.getenv("TRONGRID_API_KEY", "").strip()
TRONGRID_BASE_URL    = os.getenv("TRONGRID_BASE_URL", "https://api.trongrid.io").rstrip("/")
# USDT TRC-20 contract. Mainnet default; override for Nile/Shasta testnet.
USDT_TRC20_CONTRACT  = os.getenv("AMBAR_USDT_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").strip()

# How many USDT per 1 AED. AED is USD-pegged, so ≈ 1/3.6725. Server-authoritative
# price — the client's number is never trusted for crediting.
CRYPTO_USDT_PER_AED  = _getenv_float("AMBAR_CRYPTO_USDT_PER_AED", 1.0 / 3.6725)
# Surcharge on crypto orders to cover network + conversion fees. The customer pays
# goods_total × (1 + CRYPTO_FEE_PCT/100), converted to USDT. The order's AED total
# stays the goods value — this is purely the payment-method fee.
CRYPTO_FEE_PCT       = _getenv_float("AMBAR_CRYPTO_FEE_PCT", 10.0)  # legacy; superseded by the fixed rate below
# Fixed merchant rate: AED per 1 USDT. The crypto total = goods_AED ÷ this rate.
# The rate already bakes in the network/conversion margin, so there is NO separate
# % fee. 3.50 vs the ~3.6725 USD peg ≈ a 4.9% margin. Single formula for every tx.
CRYPTO_AED_PER_USDT  = _getenv_float("AMBAR_CRYPTO_AED_PER_USDT", 3.50)
# Admin/owner TEST override (USDT). When > 0, accounts in the admin/owner/founder
# set are charged this tiny fixed amount instead of the real order total — so the
# whole on-chain pay → detect → confirm flow can be tested for cents. Real
# customers are NEVER affected (allowlist-gated). Leave unset (0) in production.
CRYPTO_TEST_USDT     = _getenv_float("AMBAR_CRYPTO_TEST_USDT", 0.0)
# Required confirmations before crediting. TronGrid's only_confirmed already
# returns irreversible (solidified) transfers; this is a display/extra guard.
CRYPTO_REQUIRED_CONF = _getenv_int("AMBAR_CRYPTO_REQUIRED_CONF", 19)
CRYPTO_TTL_MIN       = _getenv_int("AMBAR_CRYPTO_TTL_MIN", 15)
# Step used to nudge each open invoice to a unique total so one incoming
# transfer maps to exactly one order (e.g. 70.80 vs 70.81).
CRYPTO_AMOUNT_STEP   = _getenv_float("AMBAR_CRYPTO_AMOUNT_STEP", 0.01)
# How often the background watcher polls TronGrid for incoming transfers.
CRYPTO_WATCH_INTERVAL_SEC = _getenv_int("AMBAR_CRYPTO_WATCH_SEC", 12)
# Safety switch: when on, the watcher detects + confirms invoices and logs what
# it WOULD do, but never promotes to a real order or sends any message. Lets us
# smoke-test the live chain path without notifying customers/operators.
CRYPTO_WATCH_DRYRUN  = os.getenv("AMBAR_CRYPTO_WATCH_DRYRUN", "").strip().lower() in ("1", "true", "yes", "on")

CRYPTO_REAL_MODE = bool(TRON_RECEIVE_ADDRESS and TRONGRID_API_KEY)