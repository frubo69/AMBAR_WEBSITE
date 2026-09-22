"""Чек-лист STAR: «Решения по штрафам» (владелец, 22 сен 2026: «в чек-листе
обязательно, если требуется внимание на принятие решения по штрафу»).
mongomock + настоящий owner_routes.handle_checklist:
  • ждут решения — строка есть, красная, со счётом, кто и что, ведёт в окошко;
  • решили всё — строки нет."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timezone, timedelta
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, owner_routes as orr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


async def rows():
    r = make_mocked_request("GET", "/api/owner/checklist"); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = orr.handle_checklist
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return {x["id"]: x for x in json.loads(resp.text)["rows"]}


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk"]; d = db._db
    at = datetime.now(timezone.utc) - timedelta(hours=1)
    await d.fine_pending.insert_many([
        {"_id": "late_shift:x:Баха", "kind": "late_shift", "name": "Баха", "day": "2026-09-22", "status": "pending", "at": at},
        {"_id": "geo_off:x:Худоба", "kind": "geo_off", "name": "Худоба", "day": "2026-09-22", "status": "pending",
         "amount": 200, "at": at + timedelta(minutes=30)},
        {"_id": "geo_off:y:Али", "kind": "geo_off", "name": "Али", "day": "2026-09-21", "status": "declined", "at": at}])
    r = (await rows()).get("fines")
    eq("строка есть: название, счёт, красная, ведёт в окошко",
       (r and r["t"], r and r["n"], r and r["state"], r and r["go"]), ("Решения по штрафам", 2, "late", "fines"))
    eq("подпись — кто и что решать, решённое не в счёт", sorted(r["s"].split(" · ")), ["Баха — питание", "Худоба — штраф"])
    eq("горит с первого сформированного — около часа", 55 <= r["late_min"] <= 65, True)
    await d.fine_pending.update_many({"status": "pending"}, {"$set": {"status": "declined"}})
    eq("решили всё — строки нет", "fines" in await rows(), False)
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
