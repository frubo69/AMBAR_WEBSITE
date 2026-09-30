"""Ответ магазина, в котором строки перенумерованы 1…N.

29 сен 2026 магазин убрал из заявки часть строк и перенумеровал остаток. Номер
в нашем файле — постоянный (место в обходе полок), и программа читала строку
по нему: из 60 строк 51 легла бы не на тот товар («№ 10 Corona» → Amstel).
Теперь название главнее номера (supply_routes.match_row).

    python3 tools/test_zayavka_renumbered.py [файл.xlsx]   # свой файл — посмотреть разбор
"""
import asyncio, io, json, os, sys
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
    ws.append(["", "Purchase order"]); ws.append(["", "Please correct the quantities"])
    ws.append([""] + HEAD)
    for r in rows:
        ws.append([""] + list(r))
    ws.append(["", "TOTAL"]); ws.append(["", "AMOUNT, AED"])
    b = io.BytesIO(); wb.save(b); return b.getvalue()

async def load(raw):
    async def _none(*a, **k): return None
    sr._draft_from_gap = _none; sr._answer_to_order = _none
    async def _stale(*a, **k): return []
    sr._stale_tell = _stale
    resp = await unwrap(sr.handle_import)(_Req(raw))
    body = json.loads(resp.text)
    sup = await db.supply_get(body.get("supply_id")) if body.get("ok") else None
    return resp.status, body, sup

async def main():
    db._db = AsyncMongoMockClient()["ambar_renum"]
    cat = sr._catalog_by_id()
    num, by_num = sr._nums()
    nm = lambda pid: cat[pid]["name"]

    print("Как узнаётся строка")
    m = lambda n, s: sr.match_row(n, s, cat, by_num)
    eq("название и номер согласны", m(num["p1"], nm("p1")), ("p1", "name"))
    eq("номер чужой, название верное — верим названию", m(num["p2"], nm("p43")), ("p43", "name"))
    eq("регистр, пробелы, точка", m(99, "  " + nm("p1").upper().replace(" ", "  ") + ". "), ("p1", "name"))
    eq("названия нет — по номеру", m(num["p31"], ""), ("p31", "num"))
    eq("ни названия, ни номера", m("", ""), (None, None))
    eq("опечатка в названии", m(3, nm("p105").replace("Shiraz", "Shiraz.")[:-1] + "5")[0], "p105")
    eq("чужой товар под нашим номером — чужой, а не наш по номеру",
       m(num["p10"], "Chateau Неизвестный Grand Cru"), (None, None))
    eq("своё название магазина для нашей позиции (config_store_names)",
       nm(m(7, "laroche chablis  st. martin")[0]), "Louis Moreau Chablis 0.75")
    from config_store_names import STORE_NAMES
    eq("все названия магазина ведут в каталог", [n for n, p in STORE_NAMES.items() if p not in cat], [])
    eq("название чуть переписали, номер наш — по номеру",
       m(num["p1"], "Absolut Vodka 1L")[0], "p1")

    print("\nФайл, который магазин перенумеровал")
    ids = ["p1", "p43", "p35", "p105", "p15"]
    rows = [(i + 1, nm(p), 10, i + 1, 0, 2, 0, 0) for i, p in enumerate(ids)]      # номера 1…5
    rows.insert(3, (4, "Chateau Неизвестный Grand Cru", 50, 3, 0, 0, 0, 0))             # своё от магазина
    st, body, sup = await load(book(rows))
    eq("загрузился", (st, body.get("ok")), (200, True))
    got = {it["id"]: (it["by_district"].get("jvc"), it["by_district"].get("silicon")) for it in sup["items"]}
    eq("каждая строка на своём товаре", got, {p: (i + 1, 2) for i, p in enumerate(ids)})
    eq("чужая строка не пропала и ни на кого не легла", body["unknown"], ["Chateau Неизвестный Grand Cru"])
    eq("сказано, сколько строк узнали по названию", body["renumbered"] >= 3, True)

    print("\nНаш файл без правок — как раньше")
    await db._db.supplies.delete_many({})
    st, body, sup = await load(book([(num[p], nm(p), 10, 5, 0, 0, 0, 1) for p in ids]))
    eq("позиции те же", sorted(it["id"] for it in sup["items"]), sorted(ids))
    eq("перенумерованных нет", (body["renumbered"], body["unknown"]), (0, []))

    print("\nНазвания стёрты, номера наши")
    await db._db.supplies.delete_many({})
    st, body, sup = await load(book([(num[p], "", 10, 5, 0, 0, 0, 1) for p in ids]))
    eq("читается по номерам", sorted(it["id"] for it in sup["items"]), sorted(ids))

    print("\nОдна позиция двумя строками")
    await db._db.supplies.delete_many({})
    st, body, sup = await load(book([(1, nm("p1"), 10, 5, 0, 0, 0, 0), (2, nm("p1"), 10, 7, 0, 0, 0, 0)]))
    eq("взята первая, вторая показана", ([(it["id"], it["by_district"]["jvc"]) for it in sup["items"]],
                                         body["unknown"]), ([("p1", 5)], ["повтор: " + nm("p1")]))

    if len(sys.argv) > 1:
        print(f"\nРазбор файла {os.path.basename(sys.argv[1])}")
        await db._db.supplies.delete_many({})
        st, body, sup = await load(open(sys.argv[1], "rb").read())
        print("  статус", st, "| позиций", body.get("items"), "| единиц", body.get("total_qty"),
              "| узнано по названию при чужом номере:", body.get("renumbered"),
              "| не узнано:", body.get("unknown"))
        for it in (sup or {}).get("items", [])[:200]:
            print("   ", it["name"], dict(it["by_district"]))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
