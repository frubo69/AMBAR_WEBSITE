import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "")

ADMIN_IDS = {686932322, 982022772}

# Telegram IDs allowed to access the owner dashboard (/api/owner/*).
# Keep in sync with the mini-app deployment. Separate from ADMIN_IDS — admins
# may have support/mod powers without owning the business.
OWNER_IDS = {686932322}

AUTO_REPLIES = {
    "price": "💰 Pricing: https://example.com/pricing",
    "help": "🆘 Please describe your issue, a human will reply shortly.",
    "hello": "👋 Hi! Send us your question anytime."
}