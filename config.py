import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "")

ADMIN_IDS = {686932322, 982022772}

# Telegram IDs allowed to access the owner dashboard (/api/owner/*).
# Keep in sync with the mini-app deployment. Separate from ADMIN_IDS — admins
# may have support/mod powers without owning the business.
OWNER_IDS = {686932322}

# Managers get the same /api/owner/* access as owners but are easier to
# rotate — read from env (comma-separated TG user IDs) so adding/removing
# a manager is a .env edit + service restart, no code change.
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