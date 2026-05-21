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