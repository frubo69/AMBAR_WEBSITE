import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "")

ADMIN_IDS = {686932322, 982022772}

AUTO_REPLIES = {
    "price": "💰 Pricing: https://example.com/pricing",
    "help": "🆘 Please describe your issue, a human will reply shortly.",
    "hello": "👋 Hi! Send us your question anytime."
}