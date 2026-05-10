"""Quick diagnostic: check prefs state + test tg_send for a specific user."""
import asyncio, json, os
from dotenv import load_dotenv
load_dotenv()

import motor.motor_asyncio, certifi, aiohttp

MONGO_URI = os.getenv("MONGO_URI", "")
OWNER_BOT_TOKEN = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
TARGET = 686932322

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_URI, tlsCAFile=certifi.where())
    db = client.ambar

    # 1. Show ALL prefs docs for this user
    docs = await db.owner_prefs.find({"owner_id": TARGET}).to_list(length=10)
    print(f"\n=== owner_prefs docs for {TARGET}: {len(docs)} ===")
    for d in docs:
        d.pop("_id", None)
        if "prefs_json" in d:
            try:
                d["prefs_PARSED"] = json.loads(d["prefs_json"])
            except:
                d["prefs_PARSED"] = "PARSE_ERROR"
        print(json.dumps(d, indent=2, default=str))

    # 2. Test get_owners_subscribed_to logic manually
    print(f"\n=== manual subscriber check for orders.new ===")
    cursor = db.owner_prefs.find(
        {},
        {"_id": 0, "owner_id": 1, "master": 1, "prefs_json": 1, "prefs": 1})
    rows = await cursor.to_list(length=200)
    for r in rows:
        oid = r.get("owner_id")
        master = r.get("master")
        if "prefs_json" in r:
            try: p = json.loads(r["prefs_json"])
            except: p = {}
            src = "prefs_json"
        else:
            p = r.get("prefs") or {}
            src = "prefs_dict"
        has_new = p.get("orders.new")
        print(f"  {oid}: master={master} src={src} orders.new={has_new}")

    # 3. Test sending a message
    print(f"\n=== test tg_send to {TARGET} ===")
    if not OWNER_BOT_TOKEN:
        print("  OWNER_BOT_TOKEN is empty!")
    else:
        url = f"https://api.telegram.org/bot{OWNER_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TARGET, "text": "test notification from diag script", "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                result = await resp.json()
                print(f"  status={resp.status}")
                print(f"  response={json.dumps(result, indent=2)}")

    client.close()

asyncio.run(main())
