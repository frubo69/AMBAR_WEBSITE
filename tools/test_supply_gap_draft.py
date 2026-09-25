"""Магазин дал не всё: разница видна и сразу становится черновиком докупки
(владелец, 25 сен 2026).

Две беды были рядом. Первая: строку, которой в ответе магазина нет ВОВСЕ,
разбор пропускал молча — 25.09 так исчезли 17 категорий и 180 единиц, а
«отказов» стояло ноль. Вторая: разницу надо было заметить самому и набрать
заявку на другую базу руками; незамеченная разница — это просто не купленный
товар.

  • пропавшая строка = отказ, с пометкой missing (магазин не отказал — он не
    прислал строку), и она попадает в gap_qty и в недобор;
  • честный ноль в файле остаётся обычным отказом, без пометки;
  • урезанную строку считаем как считали;
  • на разницу создаётся черновик докупки: kind=extra, source=shortfall,
    status=draft — водителю он НЕ виден (ему видны только открытые);
  • подтверждение требует имени базы и переводит в open;
  • черновик можно отменить, и приёмки это не касается.

    python3 tools/test_supply_gap_draft.py
"""
import asyncio, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, supply_routes as sup                                    # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def post(h, sid, body):
    req = make_mocked_request("POST", "/x", match_info={"sid": sid})
    req._read_bytes = json.dumps(body).encode()
    req["owner_id"] = 1
    r = await raw(h)(req)
    return r.status, json.loads(r.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_gap_draft"]
    ДЕНЬ = "2026-09-24"

    # заявка: просили три категории; магазин прислал одну целиком, одну урезал,
    # третью не прислал вовсе
    await db.zayavka_save(ДЕНЬ,
        {"p1": 20, "p10": 10, "p7": 12},
        by={"p1": {"jvc": 12, "bbay": 8}, "p10": {"jvc": 10}, "p7": {"bbay": 12}})

    doc = {"_id": "S1", "at": "now", "status": "open", "day": ДЕНЬ, "kind": "main",
           "items": [{"id": "p1", "name": "Absolut", "asked": 20, "qty": 20,
                      "by_district": {"jvc": 12, "bbay": 8}, "got": {}},
                     {"id": "p10", "name": "Red Label", "asked": 10, "qty": 6,
                      "by_district": {"jvc": 6}, "got": {}}],
           # урезанную магазин прислал, недодал 4
           "short": [{"id": "p10", "name": "Red Label", "asked": 10, "qty": 6, "gap": 4,
                      "by_district": {"jvc": 4}}],
           # а p7 он не прислал вовсе — это и есть новый случай
           "dropped": [{"id": "p7", "name": "Grey Goose", "asked": 12,
                        "by_district": {"bbay": 12}, "missing": True}],
           "extra": [], "unknown": [], "tasks": {}, "total_qty": 26,
           "asked_qty": 42, "gap_qty": 16}
    await db.supply_save(doc)

    print("── недобор видит и пропавшую строку ───────────────────────────")
    sh = sup._shortfall(doc)
    eq("категорий в недоборе", sorted(r["id"] for r in sh["rows"]), ["p10", "p7"])
    eq("единиц не хватает", sh["qty"], 16)
    p7 = next(r for r in sh["rows"] if r["id"] == "p7")
    eq("пропавшая — с адресом района", p7["by_district"], {"bbay": 12})

    print("── черновик докупки собрался сам ──────────────────────────────")
    d = await sup._draft_from_gap(doc)
    eq("черновик создан", (bool(d), d and d["total_qty"], d and d["items"]), (True, 16, 2))
    x = await db.supply_get(d["supply_id"])
    eq("это заявка на другую базу", (x["kind"], x["source"], x["from_supply"]),
       ("extra", "shortfall", "S1"))
    eq("и она ЧЕРНОВИК", x["status"], "draft")
    eq("базу ещё не назвали", x["base"], "")
    eq("районы разложены", {o: t["qty"] for o, t in x["tasks"].items()}, {"jvc": 4, "bbay": 12})

    print("── водителю черновик не виден ─────────────────────────────────")
    видно = await db.supplies_with_open_tasks(limit=20)
    eq("в списке водителя только основная", sorted(s["_id"] for s in видно), ["S1"])

    print("── подтверждение ──────────────────────────────────────────────")
    st, r = await post(sup.handle_draft_confirm, x["_id"], {"as": "STAR"})
    eq("без имени базы не подтвердить", (st, r.get("error")), (400, "no_base"))
    st, r = await post(sup.handle_draft_confirm, x["_id"], {"base": "Спинни", "as": "STAR"})
    eq("с базой — подтверждена", (st, r.get("ok")), (200, True))
    x2 = await db.supply_get(x["_id"])
    eq("стала открытой и с базой", (x2["status"], x2["base"]), ("open", "Спинни"))
    видно = await db.supplies_with_open_tasks(limit=20)
    eq("теперь водитель её видит", sorted(s["_id"] for s in видно), sorted(["S1", x["_id"]]))
    st, r = await post(sup.handle_draft_confirm, x["_id"], {"base": "Ещё", "as": "STAR"})
    eq("второй раз — не черновик", (st, r.get("error")), (409, "not_draft"))

    print("── черновик можно просто отменить ─────────────────────────────")
    d2 = await sup._draft_from_gap({**doc, "_id": "S2"})
    st, r = await post(sup.handle_cancel, d2["supply_id"], {"as": "STAR"})
    eq("отменён", (st, r.get("ok"), r.get("cancelled")), (200, True, "draft"))
    eq("статус", (await db.supply_get(d2["supply_id"]))["status"], "cancelled")
    видно = await db.supplies_with_open_tasks(limit=20)
    eq("отменённого у водителя нет", d2["supply_id"] in [s["_id"] for s in видно], False)

    print("── когда магазин дал всё — черновика нет ──────────────────────")
    eq("пусто", await sup._draft_from_gap(
        {"_id": "S3", "day": ДЕНЬ, "items": [], "short": [], "dropped": [], "tasks": {}}), None)

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
