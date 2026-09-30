"""Повторная загрузка ответа магазина заменяет поставку, а не заводит вторую
(владелец, 30 сен 2026). 29 сен файл залили трижды: у водителей стало по три
одинаковые задачи, «ждёт на базе» показало 1506 вместо 502.

    python3 tools/test_supply_reupload.py
"""
import asyncio, io, json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from openpyxl import Workbook                                     # noqa: E402
import db, supply_routes as sr                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

class _Field:
    name = "file"
    def __init__(self, raw): self.raw = raw
    async def read(self, decode=False): return self.raw
class _Reader:
    def __init__(self, raw): self.left = [_Field(raw)]
    async def next(self): return self.left.pop() if self.left else None
class _Req(dict):
    def __init__(self, raw): super().__init__(owner_id=1); self.raw = raw
    async def multipart(self): return _Reader(self.raw)

HEAD = ["№", "Item", "Price, AED", "B1 JVC", "B2 Business Bay", "B3 Silicon Oasis",
        "B4 Al Qusais", "B5 Tecom", "Total", "Amount, AED"]

def book(rows) -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = "Order"
    ws.append([""] + HEAD)
    for r in rows: ws.append([""] + list(r))
    b = io.BytesIO(); wb.save(b); return b.getvalue()

СКАЗАНО = []

async def load(rows):
    resp = await unwrap(sr.handle_import)(_Req(book(rows)))
    return resp.status, json.loads(resp.text)

async def main():
    db._db = AsyncMongoMockClient()["ambar_reup"]
    cat = sr._catalog_by_id()
    nm = lambda p: cat[p]["name"]
    async def _none(*a, **k): return None
    sr._answer_to_order = _none
    async def _tell(drivers, whole=True): СКАЗАНО.extend(drivers)
    sr._cancel_tell = _tell
    async def _stale(*a, **k): return []
    sr._stale_tell = _stale
    # Просили больше, чем дали — чтобы собирался черновик докупки.
    async def _snap(): return {"asked": {"p1": 20, "p2": 9}, "by": {"p1": {"jvc": 20}, "p2": {"jvc": 9}}}
    db.zayavka_last_full = _snap

    print("Первая загрузка")
    st, a = await load([(1, nm("p1"), 10, 5, 3, 0, 0, 0), (2, nm("p2"), 10, 4, 0, 0, 0, 0)])
    eq("поставка заведена", (st, a["ok"], a["replaced"]), (200, True, False))
    sid = a["supply_id"]
    eq("черновик докупки собран", bool(a["draft"]), True)
    x1 = a["draft"]["supply_id"]
    ok, _ = await db.supply_task_claim(sid, "jvc", "Худоба", 7, datetime.now(timezone.utc))
    ok2, _ = await db.supply_task_claim(sid, "bbay", "Авазбек", 8, datetime.now(timezone.utc))
    eq("водители взяли районы", (ok, ok2), (True, True))

    print("\nВторая загрузка — исправленный ответ, район B2 из него ушёл")
    await asyncio.sleep(1.1)                        # номер черновика — по секундам
    st, b = await load([(1, nm("p1"), 10, 8, 0, 0, 0, 0), (2, nm("p2"), 10, 9, 0, 0, 0, 0)])
    eq("заменена, номер тот же", (st, b["ok"], b["replaced"], b["supply_id"]), (200, True, True, sid))
    откр = await db._db.supplies.find({"status": "open"}).to_list(None)
    eq("открытая поставка одна", [s["_id"] for s in откр], [sid])
    sup = await db.supply_get(sid)
    eq("состав новый", {i["id"]: i["by_district"] for i in sup["items"]}, {"p1": {"jvc": 8}, "p2": {"jvc": 9}})
    eq("кто взял район — остался при нём", (sup["tasks"]["jvc"]["driver"], sup["tasks"]["jvc"]["driver_id"]), ("Худоба", 7))
    eq("версия состава выросла — водителю покажут, что изменилось", sup["tasks"]["jvc"]["erev"], 1)
    eq("района, которого больше нет, в задачах нет", sorted(sup["tasks"]), ["jvc"])
    eq("его водителю сказано", СКАЗАНО, [("Авазбек", "bbay")])
    eq("отмечено, что это замена", (sup["replaced_n"], bool(sup["first_at"])), (1, True))
    старый = await db.supply_get(x1)
    eq("прошлый черновик докупки снят", старый["status"], "cancelled")
    черн = await db._db.supplies.find({"status": "draft"}).to_list(None)
    eq("новый черновик один и от новой разницы", [(d["from_supply"], d["total_qty"]) for d in черн], [(sid, 12)])
    eq("«ждёт на базе» — не удвоилось", (await sr.pending_qty()).get(("jvc", "p1")), 8.0)

    print("\nТретья загрузка — а водитель уже начал принимать")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.jvc.scanned": 2}})
    await asyncio.sleep(1.1)
    st, c = await load([(1, nm("p1"), 10, 1, 0, 0, 0, 0)])
    eq("отказ, и сказано, какой район", (st, c.get("error"), c.get("districts")), (409, "already_started", ["B1"]))
    sup = await db.supply_get(sid)
    eq("поставка не тронута", {i["id"]: i["by_district"] for i in sup["items"]}, {"p1": {"jvc": 8}, "p2": {"jvc": 9}})
    eq("и второй не появилось", await db._db.supplies.count_documents({"status": "open"}), 1)
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.jvc.scanned": 0,
                                                             "tasks.jvc.noscan_at": datetime.now(timezone.utc)}})
    st, c = await load([(1, nm("p1"), 10, 1, 0, 0, 0, 0)])
    eq("принято без сканирования — тоже нельзя", (st, c.get("error")), (409, "already_started"))

    print("\nОткрыл камеру, но ничего не принял — заменить можно")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"tasks.jvc.noscan_at": None,
                                                             "tasks.jvc.started_at": datetime.now(timezone.utc)}})
    st, c = await load([(1, nm("p1"), 10, 6, 0, 0, 0, 0)])
    eq("заменена", (st, c.get("replaced")), (200, True))
    sup = await db.supply_get(sid)
    eq("версия состава снова выросла", (sup["tasks"]["jvc"]["erev"], sup["replaced_n"]), (2, 2))

    print("\nПоставка прошлого дня не заменяется")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"day": "2026-01-01"}})
    await asyncio.sleep(1.1)
    st, d = await load([(1, nm("p1"), 10, 2, 0, 0, 0, 0)])
    eq("заведена новая", (st, d["replaced"], d["supply_id"] != sid), (200, False, True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
