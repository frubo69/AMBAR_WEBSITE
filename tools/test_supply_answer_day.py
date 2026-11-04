"""Ответ магазина датируется днём закупки заявки, на которую он пришёл, а не
учётными сутками момента загрузки (владелец, 4 ноя 2026: «отработали заявку
3 числа, значит и пиши её 3-м числом»). 3 ноя 2026 ответ, загруженный в
09:14 по Дубаю, лёг поставкой на 2 ноября, а правками — на заявку 1 ноября,
у которой уже была своя поставка.

    python3 tools/test_supply_answer_day.py
"""
import asyncio, io, json, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from openpyxl import Workbook                                     # noqa: E402
import db, supply_routes as sr                                    # noqa: E402
from bizday import DUBAI_TZ                                       # noqa: E402

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

# Часы теста: handle_import спрашивает «сейчас по Дубаю» через sr._now_dubai.
CLOCK = [datetime(2026, 11, 3, 9, 14, tzinfo=DUBAI_TZ)]
sr._now_dubai = lambda: CLOCK[0]

def dubai(*a): return datetime(*a, tzinfo=DUBAI_TZ)

async def load(rows):
    resp = await unwrap(sr.handle_import)(_Req(book(rows)))
    return resp.status, json.loads(resp.text)

async def answers(sid):
    return [e async for e in db._db.supply_events.find({"sid": sid, "kind": {"$in": ["answer", "answer_replaced"]}})]

async def main():
    db._db = AsyncMongoMockClient()["ambar_ansday"]
    cat = sr._catalog_by_id()
    p1, p2 = list(cat)[:2]
    nm = lambda p: cat[p]["name"]
    async def _tell(*a, **k): pass
    sr._cancel_tell = _tell
    async def _stale(*a, **k): return []
    sr._stale_tell = _stale

    # Заявка 1 ноября (смена 1-го, закупка 2-го): файл выслан 2-го в 06:00,
    # магазин ответил, ответ записан правками — p1 дали 7 из 10.
    cells = {p1: {"jvc": {"have": 3, "norm": 13, "suggested": 10, "calc": 10}}}
    await db.zayavka_freeze_set("2026-11-01", cells, "2026-11-02")
    await db.zayavka_save("2026-11-01", {p1: 10}, {p1: {"jvc": 10}})
    await db._db.zayavki.update_one({"_id": "2026-11-01"}, {"$set": {"at": dubai(2026, 11, 2, 6, 0)}})
    await db.zayavka_edit_set("2026-11-01", p1, "jvc", 7)
    # Заявка 2 ноября (закупка 3-го): собрана при закрытии смены 3-го в 06:00,
    # файл выслан тогда же — просили p1 20 и p2 9.
    await db.zayavka_freeze_set("2026-11-02", {p1: {"jvc": {"have": 0, "norm": 20, "suggested": 20, "calc": 20}},
                                               p2: {"jvc": {"have": 1, "norm": 10, "suggested": 9, "calc": 9}}},
                                "2026-11-03")
    await db.zayavka_save("2026-11-02", {p1: 20, p2: 9}, {p1: {"jvc": 20}, p2: {"jvc": 9}})
    await db._db.zayavki.update_one({"_id": "2026-11-02"}, {"$set": {"at": dubai(2026, 11, 3, 6, 0)}})

    print("answer_day: 3 ноября 09:14 — учётные сутки ещё 2 ноября")
    eq("день поставки и документ", await sr.answer_day(), ("2026-11-03", "2026-11-02"))
    CLOCK[0] = dubai(2026, 11, 3, 11, 0)
    eq("после 10:00 — то же", await sr.answer_day(), ("2026-11-03", "2026-11-02"))
    CLOCK[0] = dubai(2026, 11, 3, 9, 14)

    print("\nОтвет загружен 3 ноября в 09:14: p1 дали 15, p2 не дали")
    st, a = await load([(1, nm(p1), 10, 15, 0, 0, 0, 0)])
    eq("поставка заведена", (st, a["ok"], a["replaced"]), (200, True, False))
    sid = a["supply_id"]
    sup = await db._db.supplies.find_one({"_id": sid})
    eq("день поставки — 3 ноября, а не 2-е", sup.get("day"), "2026-11-03")
    eq("недобор посчитан по снимку 2 ноября", (a["asked_qty"], a["gap_qty"]), (29, 14))
    ev = await answers(sid)
    eq("журнал: ответ под 3 ноября на заявку 2 ноября",
       [(e.get("day"), (e.get("data") or {}).get("order_day")) for e in ev], [("2026-11-03", "2026-11-02")])
    eq("правки легли на заявку 2 ноября", await db.zayavka_edits("2026-11-02"), {p1: {"jvc": 15}, p2: {"jvc": 0}})
    eq("заявка 1 ноября не тронута", await db.zayavka_edits("2026-11-01"), {p1: {"jvc": 7}})
    eq("экран заявки на 3 ноября видит поставку", (await db.supply_of_day("2026-11-03")).get("_id"), sid)

    print("\nТот же ответ загружен заново в 11:00 — уже 3 ноября по учёту")
    CLOCK[0] = dubai(2026, 11, 3, 11, 0)
    await asyncio.sleep(1.1)
    st, b = await load([(1, nm(p1), 10, 18, 0, 0, 0, 0)])
    eq("заменена та же поставка, не вторая", (st, b["replaced"], b["supply_id"]), (200, True, sid))
    eq("поставка в базе одна", await db._db.supplies.count_documents({}), 1)
    eq("день остался 3 ноября", (await db._db.supplies.find_one({"_id": sid})).get("day"), "2026-11-03")
    eq("правки на заявке 2 ноября обновлены", await db.zayavka_edits("2026-11-02"), {p1: {"jvc": 18}, p2: {"jvc": 0}})

    print("\nСмену 3 ноября закрыли поздно, после 10:00: документ 3-го, закупка 4-го")
    await db._db.supplies.update_one({"_id": sid}, {"$set": {"status": "done"}})
    await db.zayavka_freeze_set("2026-11-03", {p1: {"jvc": {"have": 2, "norm": 20, "suggested": 18, "calc": 18}}},
                                "2026-11-04")
    await db.zayavka_save("2026-11-03", {p1: 18}, {p1: {"jvc": 18}})
    await db._db.zayavki.update_one({"_id": "2026-11-03"}, {"$set": {"at": dubai(2026, 11, 3, 10, 30)}})
    CLOCK[0] = dubai(2026, 11, 3, 12, 0)
    eq("день не уезжает в завтра", await sr.answer_day(), ("2026-11-03", "2026-11-03"))
    await asyncio.sleep(1.1)
    st, c = await load([(1, nm(p1), 10, 18, 0, 0, 0, 0)])
    eq("новая поставка, день 3 ноября", (st, c["replaced"], c["supply_id"] != sid,
                                        (await db._db.supplies.find_one({"_id": c["supply_id"]})).get("day")),
       (200, False, True, "2026-11-03"))
    eq("ответ записан в документ 3 ноября", await db.zayavka_edits("2026-11-03"), {p1: {"jvc": 18}})

    print("\nФайл выслан заново через три дня — поставка не утягивается в прошлое")
    CLOCK[0] = dubai(2026, 11, 6, 12, 0)
    eq("день — учётные сутки сейчас, документ — высланный", await sr.answer_day(), ("2026-11-06", "2026-11-03"))

    print("\nСнимка нет вовсе")
    await db._db.zayavki.delete_many({})
    CLOCK[0] = dubai(2026, 11, 7, 9, 0)
    eq("учётные сутки, как раньше", await sr.answer_day(), ("2026-11-06", "2026-11-06"))

    print("\nИтог:", "FAIL " + ", ".join(FAIL) if FAIL else "всё прошло")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
