"""Свободная заявка Барракуде (владелец, 8 ноя 2026: «И почему нет свободной
заявки в баракуде?» → «делай как предложил, заранее проверь на баги и
противоречия в логике все пофикси»). mongomock + настоящие обработчики:
  • создание {free: true}: база всегда Барракуда, количества целые (пиво
    2,5 → 3, «0,5» → 1), статус wait, номер F…, водителям не видна, но заявка
    по норме её уже вычитает (pending_qty);
  • до ответа состав правится (handle_own_lines): целыми, итоги не режутся;
  • файл магазину (export ?sid / send {sid}): лист как у заявки дня, только
    районы заявки, снимок заявки дня не трогается, sent_at/sent_n растут;
  • ответ магазина (import ?sid=): отказы, урезанное, дописанное — против
    собственного снимка ask_by; статус open, at — момент ответа, день —
    календарный по Дубаю; основная заявка и её правки не трогаются;
  • у водителя: отдельной заявкой с меткой «Барракуда · свободная», без
    цен (prices_ok/cost нет), «нет в наличии» не бывает;
  • повторная загрузка до скана — заменяет (раз 2), после скана — 409;
  • недобор района не рождает черновик на другую базу (как у основной);
  • отмена до ответа — целиком, базу не переименовать, оператору не видна,
    чек-лист не считает её ответом на заявку дня, в финансах — деньги
    магазина, а не другой базы; хронология: собрана → выслан → ответ.

    python3 tools/test_free_order.py
"""
import asyncio, io, json, os, sys, types
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
from openpyxl import Workbook, load_workbook                      # noqa: E402
import db, supply_routes as sr, supply_log                        # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

def unwrap(h):
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return h

BEER, VODKA, STOLI, GIN = "p47", "p1", "p2", "p55"

async def call(handler, body=None, sid="", method="POST", query=None):
    q = query or {}
    r = make_mocked_request(method, "/x" + ("?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else ""),
                            match_info={"sid": sid} if sid else {})
    r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    async def _json(): return body or {}
    r.json = _json
    resp = await unwrap(handler)(r)
    try:
        return resp.status, json.loads(resp.text)
    except Exception:                                # noqa: BLE001 — файл
        return resp.status, resp.body

# Ответ магазина — multipart с одним полем file и ?sid=&as=.
class _Field:
    name = "file"
    def __init__(self, raw): self.raw = raw
    async def read(self, decode=False): return self.raw
class _Reader:
    def __init__(self, raw): self.left = [_Field(raw)]
    async def next(self): return self.left.pop() if self.left else None
class _Req(dict):
    def __init__(self, raw, **q): super().__init__(owner_id=1); self.raw = raw; self.query = q
    async def multipart(self): return _Reader(self.raw)

HEAD = ["№", "Item", "Price, AED", "B1 JVC", "B2 Business Bay", "B3 Silicon Oasis",
        "B4 Al Qusais", "B5 Tecom", "Total", "Amount, AED"]

def book(rows) -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = "Order"
    ws.append([""] + HEAD)
    for r in rows: ws.append([""] + list(r))
    b = io.BytesIO(); wb.save(b); return b.getvalue()

async def load(rows, **q):
    resp = await unwrap(sr.handle_import)(_Req(book(rows), **q))
    return resp.status, json.loads(resp.text)

# Телеграм для handle_send — подменный api_server: файл «уходит», ответ ok.
SENT = []
def fake_api_server():
    m = types.ModuleType("api_server")
    class FormData:
        def __init__(self): self.f = {}
        def add_field(self, k, v, **kw): self.f[k] = (v, kw)
    class _Resp:
        def __init__(self, form): self.form = form
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self):
            SENT.append(self.form.f)
            return {"ok": True, "result": {"message_id": len(SENT)}}
    class ClientSession:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, data=None): return _Resp(data)
    class ClientTimeout:
        def __init__(self, total=0): pass
    m._aiohttp = types.SimpleNamespace(FormData=FormData, ClientSession=ClientSession, ClientTimeout=ClientTimeout)
    async def _remember(*a, **k): return None
    m._remember_owner_msg = _remember
    sys.modules["api_server"] = m

async def main():
    db._db = AsyncMongoMockClient()["ambar_free_order"]
    import owner_routes, operator_routes
    async def _say(*a, **k): return []
    owner_routes.notify_owners = _say; owner_routes.notify_owners_force = _say
    owner_routes.OWNER_BOT_TOKEN = "test-token"
    sr._costs_refresh = _say
    async def _none(*a, **k): return None
    ЗАЯВКА_ДНЯ = []
    async def _answer(day, items, asked_full): ЗАЯВКА_ДНЯ.append(day)
    sr._answer_to_order = _answer
    СТАРЫЕ_СНЯТЫ = []
    async def _stale(sid): СТАРЫЕ_СНЯТЫ.append(sid); return []
    sr._stale_tell = _stale
    async def _tell(*a, **k): return None
    sr._cancel_tell = _tell
    sr._prices_tell = _none
    СНИМОК = {"_id": "2026-11-07", "asked": {VODKA: 99}, "by": {VODKA: {"tecom": 99}}}
    async def _snap(): return dict(СНИМОК)
    db.zayavka_last_full = _snap
    fake_api_server()
    cat = sr._catalog_by_id()
    nm = lambda p: cat[p]["name"]
    НОМЕР, _ = sr._nums()
    today = sr._now_dubai().strftime("%Y-%m-%d")

    print("Создание")
    st, r = await call(sr.handle_extra_create, {"free": True, "base": "Чужая база", "as": "Старший", "items": [
        {"id": VODKA, "by_district": {"jvc": 10}},
        {"id": STOLI, "by_district": {"jvc": 5}},
        {"id": BEER, "by_district": {"bbay": 2.5, "jvc": "0,5"}}]})
    eq("создана", (st, r.get("ok"), r.get("free_order")), (200, True, True))
    sid = r["supply_id"]
    sup = await db.supply_get(sid)
    its = {i["id"]: i for i in sup["items"]}
    eq("номер F…, база — Барракуда, что бы ни прислали", (sid[:1], sup["base"], sup["kind"]), ("F", "Барракуда", "extra"))
    eq("статус wait, файл ещё не выслан", (sup["status"], sup.get("sent_at"), sup.get("sent_n"), r["sent_at"]), ("wait", None, 0, ""))
    eq("пиво магазину целыми: 2,5 → 3, 0,5 → 1", (its[BEER]["by_district"], its[BEER]["qty"]), ({"bbay": 3, "jvc": 1}, 4))
    eq("итог 19, районы", (sup["total_qty"], {o: t["qty"] for o, t in sup["tasks"].items()}), (19, {"jvc": 16, "bbay": 3}))
    eq("снимка просимого до ответа нет", sup.get("ask_by"), None)
    eq("в журнале — «свободная заявка собрана»",
       [e["kind"] for e in await db.supply_events(sid)], ["free_created"])

    print("\nПока ждём ответа")
    t = await sr.tasks_for_driver("Худоба", "jvc")
    eq("водителям не видна", (t["mine"], t["free"], t["extra"], t["taken"]), ([], [], [], []))
    pend = await sr.pending_qty()
    eq("заявка по норме уже вычитает её", (pend.get(("jvc", VODKA)), pend.get(("bbay", BEER)), pend.get(("jvc", BEER))), (10.0, 3.0, 1.0))
    v = sr._task_view(sid, sup, "jvc", sup["tasks"]["jvc"], "Худоба")
    eq("задача без цен, с меткой", ("prices_ok" in v, "cost" in v, v["extra"], v["free_order"], v["base"]),
       (False, False, True, True, "Барракуда · свободная"))
    eq("строки без цены и без «нет в наличии»", [k for k in ("price", "na", "units") if k in v["lines"][0]], [])
    eq("цен не ждёт", sr._prices_missing(sup, "jvc"), [])
    eq("правка строки — целыми (пиво 1,5 → 2)", sr._line_qty(1.5, sup, cat[BEER]), 2)
    st, r = await call(sr.handle_own_lines, {"district": "jvc", "as": "Старший",
                                             "lines": [{"product_id": VODKA, "qty": 12}, {"product_id": BEER, "qty": 1.5}]}, sid=sid)
    sup = await db.supply_get(sid); its = {i["id"]: i for i in sup["items"]}
    eq("состав правится до ответа", (st, its[VODKA]["by_district"], its[BEER]["by_district"], its[BEER]["qty"]),
       (200, {"jvc": 12}, {"bbay": 3, "jvc": 2}, 5))
    eq("итоги после правки целые и верные", (sup["tasks"]["jvc"]["qty"], sup["total_qty"]), (19, 22))
    st, r = await call(sr.handle_base_set, {"base": "Marina", "as": "Старший"}, sid=sid)
    eq("базу свободной не переименовать", (st, r.get("error")), (403, "free_order"))
    brief = sr._sup_brief(sup)
    eq("строка списка: свободная, можно отменить", (brief["free_order"], brief["can_cancel"], brief["status"]), (True, 2, "wait"))

    print("\nФайл магазину")
    st, raw = await call(sr.handle_export, method="GET", query={"sid": sid})
    eq("файл отдан", (st, raw[:2]), (200, b"PK"))
    ws = load_workbook(io.BytesIO(raw), data_only=True)["Order"]
    eq("заголовок — дополнительная заявка днём по Дубаю", ws["B1"].value, f"Additional purchase order · {today}")
    head = [c.value for c in ws[3] if c.value]
    eq("колонки — только районы заявки", head, ["№", "Item", "Price, AED", "B1 JVC", "B2 Business Bay", "Total", "Amount, AED"])
    rows = {ws.cell(row=i, column=3).value: (ws.cell(row=i, column=2).value, ws.cell(row=i, column=5).value, ws.cell(row=i, column=6).value)
            for i in range(4, 7)}
    eq("строки — текущий состав с номерами", rows.get(nm(VODKA)), (НОМЕР[VODKA], 12, None))
    eq("пиво коробками", rows.get(nm(BEER)), (НОМЕР[BEER], 2, 3))
    eq("снимок заявки дня не тронут", await db._db.zayavki.count_documents({}), 0)
    st, r = await call(sr.handle_send, {"sid": sid, "as": "Старший"})
    sup = await db.supply_get(sid)
    eq("выслан: подпись, отметка, журнал", (st, SENT[-1]["caption"][0], bool(sup["sent_at"]), sup["sent_n"], sup["sent_by"]),
       (200, f"Свободная заявка в магазин · {today}", True, 1, "Старший"))
    eq("имя файла — своё", r["file"], f"AMBAR-zayavka-extra-{today}.xlsx")
    st, r = await call(sr.handle_send, {"sid": sid, "as": "Старший"})
    eq("выслать ещё раз можно", (st, (await db.supply_get(sid))["sent_n"]), (200, 2))
    st, r = await call(sr.handle_send, {"sid": "F000000-000000", "as": "Старший"})
    eq("чужой номер — 404", st, 404)

    print("\nОтвет магазина")
    st, a = await load([(НОМЕР[VODKA], nm(VODKA), 100, 8, 0, 0, 0, 0),        # просили 12 — урезал до 8
                        (НОМЕР[BEER], nm(BEER), 50, 2, 3, 0, 0, 0),           # как просили
                        (НОМЕР[GIN], nm(GIN), 70, 0, 4, 0, 0, 0)],            # не просили — дописал
                       sid=sid)                                                  # Stolichnaya не прислал вовсе
    eq("ответ лёг в ту же заявку", (st, a["ok"], a["free_order"], a["supply_id"], a["replaced"]), (200, True, True, sid, False))
    eq("дали 17, просили 22, не дали 9", (a["total_qty"], a["asked_qty"], a["gap_qty"]), (17, 22, 9))
    eq("отказ — строки нет в файле", [(d["id"], d["asked"], d.get("missing")) for d in a["dropped"]], [(STOLI, 5, True)])
    eq("урезано", [(s["id"], s["asked"], s["qty"], s["by_district"]) for s in a["short"]], [(VODKA, 12, 8, {"jvc": 4})])
    eq("дописано магазином", [(x["id"], x["qty"]) for x in a["extra"]], [(GIN, 4)])
    sup = await db.supply_get(sid)
    eq("открыта водителям, день — сегодня по Дубаю", (sup["status"], sup["day"], sup["answer_n"]), ("open", today, 1))
    eq("снимок просимого заморожен ответом", sup["ask_by"], {VODKA: {"jvc": 12}, STOLI: {"jvc": 5}, BEER: {"bbay": 3, "jvc": 2}})
    eq("собрана и выслана — помнит, at — момент ответа", (bool(sup["created_at"]), sup["sent_n"], sup["at"] > sup["created_at"]), (True, 2, True))
    eq("заявка дня и прошлые задачи не тронуты", (ЗАЯВКА_ДНЯ, СТАРЫЕ_СНЯТЫ), ([], []))
    eq("статус-ошибка: выслать после ответа нельзя", (await call(sr.handle_send, {"sid": sid}))[1], {"error": "free_status", "status": "open"})
    tl = await supply_log.timeline(sid)
    kinds = [e["kind"] for e in tl["events"]]
    eq("хронология: собрана → выслан → ответ", (kinds[0], "sent" in kinds, kinds[-1], tl["kind"]), ("free_created", True, "answer", "extra"))
    # Запись журнала — как собрали (19), правка до ответа — отдельным шагом.
    eq("шаг «собрана» подписан автором и составом", (tl["events"][0]["who"], tl["events"][0]["note"]), ("Старший", "19 бутылок · 3 позиции"))
    eq("правка состава — свой шаг", "line_edit" in kinds, True)

    print("\nУ водителя")
    t = await sr.tasks_for_driver("Худоба", "jvc")
    eq("отдельной заявкой, свой район первым", [(x["district"], x["base"], x["free_order"]) for x in t["extra"]],
       [("jvc", "Барракуда · свободная", True), ("bbay", "Барракуда · свободная", True)])
    eq("без экрана цен", ("prices_ok" in t["extra"][0], "cost" in t["extra"][0]), (False, False))
    ok, _ = await db.supply_task_claim(sid, "jvc", "Худоба", 7, datetime.now(timezone.utc))
    eq("район берётся", ok, True)
    na = await sr.na_set(sid, "jvc", VODKA, True, "Худоба")
    eq("«нет в наличии» не бывает", na, {"ok": False, "verdict": "not_extra"})
    bs = await sr.buy_set(await db.supply_get(sid), BEER, 60.0, 0, "Худоба", 7)
    eq("цену на позицию не впишешь", bs, {"ok": False, "error": "not_in_shortfall"})
    sup_op, no = await operator_routes._extra_only(types.SimpleNamespace(match_info={"sid": sid}))
    eq("оператору не отдаётся", (sup_op, no.status), (None, 403))

    print("\nПовторная загрузка")
    await asyncio.sleep(1.1)
    st, b = await load([(НОМЕР[VODKA], nm(VODKA), 100, 9, 0, 0, 0, 0),
                        (НОМЕР[BEER], nm(BEER), 50, 2, 3, 0, 0, 0)], sid=sid)
    sup = await db.supply_get(sid)
    eq("до скана — заменена, раз 2, водитель при районе", (st, b["replaced"], sup["answer_n"], sup["replaced_n"], sup["tasks"]["jvc"]["driver"]),
       (200, True, 2, 1, "Худоба"))
    eq("отказы считаются против того же снимка", ([d["id"] for d in sup["dropped"]], [(s["id"], s["qty"]) for s in sup["short"]]), ([STOLI], [(VODKA, 9)]))
    res = await sr.task_scan(sid, "jvc", VODKA, "FREE-0001", "Худоба", 7)
    eq("скан ложится без цен", res.get("ok"), True)
    st, c = await load([(НОМЕР[VODKA], nm(VODKA), 100, 9, 0, 0, 0, 0)], sid=sid)
    eq("после скана заменить нельзя", (st, c["error"], c["districts"]), (409, "already_started", ["B1"]))

    print("\nНедобор и чек-лист")
    fin = await sr.task_finish(sid, "jvc", "Худоба")
    eq("район закрыт с недобором — черновика на другую базу нет", (fin["ok"], bool(fin["gaps"]), fin["next_draft"]), (True, True, ""))
    eq("в базе черновиков нет", await db._db.supplies.count_documents({"status": "draft"}), 0)
    chk = await owner_routes._chk_supply(today)
    eq("чек-лист: свободная заявка — не ответ на заявку дня", chk["exists"], False)
    eq("…но открытая приёмка по ней видна", [(x["free_order"], x["left"]) for x in chk["open_list"]], [(True, ["bbay"])])

    print("\nОтмена до ответа")
    st, r = await call(sr.handle_extra_create, {"free": True, "as": "Старший", "items": [{"id": VODKA, "by_district": {"tecom": 2}}]})
    sid2 = r["supply_id"]
    pend = await sr.pending_qty()
    eq("вторая тоже вычитается", pend.get(("tecom", VODKA)), 2.0)
    st, r = await call(sr.handle_cancel, {"as": "Старший"}, sid=sid2)
    sup2 = await db.supply_get(sid2)
    eq("отменена целиком", (st, r["cancelled"], sup2["status"], sup2["cancelled_by"]), (200, "wait", "cancelled", "Старший"))
    eq("из вычета ушла", ("tecom", VODKA) in await sr.pending_qty(), False)
    eq("журнал: отмена до ответа", [e["note"] for e in await db.supply_events(sid2) if e["kind"] == "supply_cancelled"], ["до ответа магазина"])
    st, r = await call(sr.handle_send, {"sid": sid2})
    eq("отменённой файл не высылается", (st, r), (409, {"error": "free_status", "status": "cancelled"}))

    print("\nФинансы")
    import finance_routes as fr
    p = await fr._purchases([today])
    s = p[today]
    eq("деньги свободной заявки — у магазина, не у другой базы", (s["ordered_extra"], s["ordered"] > 0,
       [(x["kind"], x["base"]) for x in s["supplies"] if x["id"] == sid]), (0, True, [("main", "Барракуда · свободная")]))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
