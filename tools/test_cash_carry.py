"""Несобранная выручка переезжает на следующий день (владелец, 19 сен 2026:
«пусть в сборе выручки будет кнопка „соберу завтра“, и программа аккумулирует
всю выручку, что он вчера не забрал — на следующий день он забирает выручку за
два дня»; «даже если не нажал, но за всю смену не нажал, что забрал, — всё
равно на следующий день писать, что надо собрать то, что вчера не собрал»).

mongomock + настоящие ручки STAR (/api/owner/cash-round, /cash-round/later,
/checklist/mark) и настоящая строка чек-листа:
  • не отмечено «Получил» — едет на завтра само, без кнопки; копится, пока не
    получат; пустые дни пропускаются, цепочка не рвётся;
  • «Получил» закрывает и перенесённые дни (via), в самом дне они остаются
    видны; «снять отметку» возвращает их в перенос;
  • получено в свой день — дальше не едет;
  • раньше начала переноса (CASH_CARRY_FROM) не смотрим;
  • старая отметка на весь день обрывает цепочку;
  • «Соберу завтра» — на всех неполученных; строка чек-листа за день не
    горит; снять — горит снова; полученный и пустой район не трогает."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, owner_routes as own, config_staff as staff, owner_auth
import cash_math as cm

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
TEA = next(iter(cm.tea_rates()))
D0, D1, D2, D3 = "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"
own.CASH_CARRY_FROM = D1                     # 17-е — до начала переноса
ORDERS = {}
def order(oid, day, office, total, **kw):
    ORDERS[oid] = {"order_id": oid, "office_id": office, "status": "delivered", "total": total,
                   "payment_method": "cash", "_day": day, **kw}
async def orders_from(since): return dict(ORDERS)
own.db.orders_from = orders_from
own._order_day = lambda o: o.get("_day")
staff.drivers = lambda: [{"name": "Водитель А", "district": "jvc", "operator": "Оператор"},
                         {"name": "Водитель Б", "district": "bbay", "operator": "Оператор"}]
own._biz_date = lambda *_: __import__("datetime").date.fromisoformat(D3)


async def main():
    db._db = AsyncMongoMockClient()["ambar_cash_carry"]
    # JVC: 17-го 500 (до переноса), 18-го 1000 + чай, 19-го ничего, 20-го 300.
    order("a", D0, "jvc", 500)
    order("b", D1, "jvc", 1050, items=[{"id": TEA, "qty": 1}])
    order("c", D3, "jvc", 300)
    # Бизнес Бей: 18-го 700, получено в тот же день; 19-го 400; 20-го пусто.
    order("d", D1, "bbay", 700)
    order("e", D2, "bbay", 400)
    await db.checklist_set(D1, "cash:bbay", True, "1")
    app = web.Application(); own.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def get(path, **params):
            r = await cl.get(path, params=params, headers={"Authorization": "tma 1"})
            return await r.json()
        async def post(path, body):
            r = await cl.post(path, json=body, headers={"Authorization": "tma 1"})
            return await r.json()
        dist = lambda cr, oid: next(x for x in cr["districts"] if x["id"] == oid)

        print("── перенос без кнопки ───────────────────────────────────────")
        cr = await get("/api/owner/cash-round", day=D3)
        j, b = dist(cr, "jvc"), dist(cr, "bbay")
        eq("JVC 20-го: своё 300 + несобранное 18-го 1000 (чай 50 отдельно), 17-е — до переноса",
           (j["net"], [(c["day"], c["net"], c["tips"]) for c in j["carry"]], j["net_all"], j["tips_all"]),
           (300, [(D1, 1000, 50)], 1300, 50))
        eq("пустое 19-е у JVC пропущено, цепочка дошла до 18-го", [c["day"] for c in j["carry"]], [D1])
        eq("Бизнес Бей 20-го пуст сам, но везёт 400 за 19-е — в сборе", (b["net"], b["net_all"], b["empty"]), (0, 400, False))
        eq("18-е Бизнес Бея получено в свой день — дальше не едет", [c["day"] for c in b["carry"]], [D2])
        eq("итог дня — со всем перенесённым", (cr["net_total"], cr["carry_total"], cr["need"]), (1700, 1400, 2))

        print("── «Получил» закрывает и перенесённое ───────────────────────")
        await post("/api/owner/checklist/mark", {"day": D3, "item": "cash:jvc", "done": True})
        cr = await get("/api/owner/cash-round", day=D3)
        j = dist(cr, "jvc")
        eq("JVC получил 20-го — перенесённое остаётся видно в этом дне", (j["done"], j["net_all"], [c["day"] for c in j["carry"]]),
           (True, 1300, [D1]))
        c18 = await get("/api/owner/cash-round", day=D1)
        eq("18-е JVC теперь «получил» (через 20-е)", dist(c18, "jvc")["done"], True)
        m = (await db.checklist_get(D1)).get("cash:jvc") or {}
        eq("отметка 18-го — via 20-го", (m.get("done"), m.get("via")), (True, D3))
        c21 = await get("/api/owner/cash-round", day="2026-09-21")
        eq("на 21-е JVC уже ничего не везёт", dist(c21, "jvc")["carry"], [])

        await post("/api/owner/checklist/mark", {"day": D3, "item": "cash:jvc", "done": False})
        cr = await get("/api/owner/cash-round", day=D3)
        eq("снял отметку — 18-е снова в переносе, не получено", (dist(cr, "jvc")["done"], [c["day"] for c in dist(cr, "jvc")["carry"]],
           dist(await get("/api/owner/cash-round", day=D1), "jvc")["done"]), (False, [D1], False))

        print("── «Соберу завтра» ──────────────────────────────────────────")
        r = await post("/api/owner/cash-round/later", {"day": D3})
        eq("отложены оба неполученных района", sorted(r["changed"]), ["bbay", "jvc"])
        eq("решено по всем — строка чек-листа не горит", (r["later"], r["settled"], r["all_done"]), (2, True, False))
        c21 = await get("/api/owner/cash-round", day="2026-09-21")
        eq("21-го JVC везёт 18-е и 20-е, Бизнес Бей — 19-е",
           ([c["day"] for c in dist(c21, "jvc")["carry"]], [c["day"] for c in dist(c21, "bbay")["carry"]],
            dist(c21, "jvc")["net_all"]), ([D3, D1], [D2], 1300))
        await post("/api/owner/checklist/mark", {"day": D3, "item": "cash:bbay", "done": True})
        cr = await get("/api/owner/cash-round", day=D3)
        eq("получил после «Соберу завтра» — получено, не отложено", (dist(cr, "bbay")["done"], dist(cr, "bbay")["later"], cr["later"]),
           (True, False, 1))
        r = await post("/api/owner/cash-round/later", {"day": D3, "on": False})
        eq("снять «Соберу завтра» — только у отложенных", (r["changed"], r["later"], r["settled"]), (["jvc"], 0, False))

        print("── чек-лист ─────────────────────────────────────────────────")
        # Чек-лист не берёт день из будущего — проверяем на 19-м: JVC везёт
        # 18-е и не получен, Бизнес Бей получен (через 20-е).
        chk = await get("/api/owner/checklist", day=D2)
        row = next(x for x in chk.get("rows", []) if x.get("id") == "cash")
        eq("19-е: не решено, в подписи — прошлые дни", (row["state"] != "done", row["s"]),
           (True, "получил 1 из 2 · выручка 1 400 AED · из них за прошлые дни 1 000"))
        await post("/api/owner/cash-round/later", {"day": D2})
        chk = await get("/api/owner/checklist", day=D2)
        row = next(x for x in chk.get("rows", []) if x.get("id") == "cash")
        eq("«Соберу завтра» на 19-е — строка решена, в подписи «соберу завтра 1»",
           (row["state"], "соберу завтра 1" in row["s"], row["n"]), ("done", True, 0))

        print("── старая отметка на весь день ──────────────────────────────")
        await db.checklist_set(D2, "cash", True, "1")
        c21 = await get("/api/owner/cash-round", day="2026-09-21")
        eq("отметка на весь 19-е обрывает цепочку Бизнес Бея", [c["day"] for c in dist(c21, "bbay")["carry"]], [])

        # Владелец, 19 сен 2026: «соберу завтра должна быть отдельно по каждому
        # району — вдруг он с какого-то забрал, а где-то просто не успевает».
        print("── «Соберу завтра» по одному району ─────────────────────────")
        order("f", D3, "silicon", 200)
        r = await post("/api/owner/cash-round/later", {"day": D3, "districts": ["silicon"]})
        eq("отложен только названный район", (r["changed"], dist(r, "silicon")["later"], dist(r, "jvc")["later"]),
           (["silicon"], True, False))
        eq("JVC не решён — строка чек-листа ещё горит", r["settled"], False)
        r = await post("/api/owner/cash-round/later", {"day": D3, "districts": ["jvc"]})
        eq("JVC — своей кнопкой; теперь решено по всем", (r["changed"], r["later"], r["settled"]), (["jvc"], 2, True))
        r = await post("/api/owner/cash-round/later", {"day": D3, "on": False, "districts": ["silicon"]})
        eq("снять у одного — второй остаётся отложенным",
           (r["changed"], dist(r, "silicon")["later"], dist(r, "jvc")["later"]), (["silicon"], False, True))
        r = await post("/api/owner/cash-round/later", {"day": D3, "districts": ["bbay"]})
        eq("полученный район не откладывается", (r["changed"], dist(r, "bbay")["done"], dist(r, "bbay")["later"]),
           ([], True, False))
    print("\nвсё прошло" if not FAIL else f"\nНЕ ПРОШЛИ: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
