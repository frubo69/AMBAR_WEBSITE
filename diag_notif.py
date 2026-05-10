"""Diagnostic: call the REAL get_owners_subscribed_to and trace every step."""
import asyncio, json, os, sys
from dotenv import load_dotenv
load_dotenv()

TARGET = 686932322

async def main():
    # Use the REAL db module — same code path as the running server
    import db
    from config import OWNER_IDS, MANAGER_IDS

    await db.connect()
    _db = db._db

    # 1. Raw DB dump for target user
    docs = await _db.owner_prefs.find({"owner_id": TARGET}).to_list(10)
    print(f"\n{'='*60}")
    print(f"RAW owner_prefs docs for {TARGET}: {len(docs)}")
    print(f"{'='*60}")
    for d in docs:
        d["_id"] = str(d["_id"])
        print(json.dumps(d, indent=2, default=str))

    # 2. ALL docs in owner_prefs
    all_docs = await _db.owner_prefs.find({}).to_list(200)
    print(f"\n{'='*60}")
    print(f"ALL owner_prefs docs: {len(all_docs)}")
    print(f"{'='*60}")
    for d in all_docs:
        oid = d.get("owner_id")
        master = d.get("master")
        has_json = "prefs_json" in d
        has_dict = "prefs" in d
        keys = list(d.keys())
        print(f"  owner_id={oid} master={master} has_prefs_json={has_json} has_prefs_dict={has_dict} keys={keys}")
        if has_json:
            try:
                p = json.loads(d["prefs_json"])
                print(f"    prefs_json parsed OK, orders.new={p.get('orders.new')}")
            except Exception as e:
                print(f"    prefs_json PARSE ERROR: {e}")
                print(f"    raw prefs_json: {repr(d['prefs_json'][:200])}")
        if has_dict:
            p = d.get("prefs")
            print(f"    prefs type={type(p).__name__}")
            if isinstance(p, dict):
                print(f"    prefs keys={list(p.keys())[:10]}")
                print(f"    orders.new={p.get('orders.new')}")

    # 3. Call the REAL get_owners_subscribed_to
    print(f"\n{'='*60}")
    print(f"CALLING db.get_owners_subscribed_to('orders.new')")
    print(f"{'='*60}")
    print(f"  OWNER_IDS  = {OWNER_IDS}")
    print(f"  MANAGER_IDS = {MANAGER_IDS}")
    result = await db.get_owners_subscribed_to("orders.new")
    print(f"  RESULT = {result}")
    print(f"  {TARGET} in result? {TARGET in result}")

    # 4. Also test other event keys
    for ek in ["orders.new1000", "orders.new500", "orders.declined", "orders.delivered"]:
        r = await db.get_owners_subscribed_to(ek)
        print(f"  {ek} → {r}")

    # 5. Test tg_send directly
    print(f"\n{'='*60}")
    print(f"TEST tg_send to {TARGET}")
    print(f"{'='*60}")
    tok = os.getenv("AMBAR_OWNER_BOT_TOKEN", "")
    if not tok:
        print("  OWNER_BOT_TOKEN is empty!")
    else:
        import aiohttp
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        payload = {"chat_id": TARGET, "text": "FULL PIPELINE TEST", "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload) as resp:
                r = await resp.json()
                print(f"  ok={r.get('ok')} error={r.get('description','none')}")

    db.close()

asyncio.run(main())
