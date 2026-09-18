"""Тест-режим владельца (14 сен 2026) на подменённой базе — сквозь все три
приложения: флажок «Тестовый заказ» в клиентском, тест-оператор в панели,
тест-водитель в приложении водителя, фильтр тест-заказов в db.py и адресаты
рассылок. Запуск: python3 tools/test_test_mode.py"""
import asyncio, os, sys, json, logging, copy, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Окружение задаём ДО импорта конфигов: load_dotenv существующие не перебивает.
os.environ["MONGO_URI"] = ""
os.environ["AMBAR_OWNER_IDS"] = "1"            # владелец = тестер по умолчанию
os.environ["OPERATOR_IDS"] = "7"               # настоящий оператор
os.environ["AMBAR_OPERATOR_IDS"] = ""
os.environ["AMBAR_DRIVER_IDS"] = "Худоба:555"   # настоящий водитель
os.environ["OPERATOR_BOT_TOKEN"] = "x"; os.environ["DRIVER_BOT_TOKEN"] = "x"
os.environ["AMBAR_OWNER_BOT_TOKEN"] = "x"; os.environ["BOT_TOKEN"] = "x"
for k in ("AMBAR_TEST_ORDER_IDS", "AMBAR_TEST_OPERATOR_IDS", "AMBAR_TEST_DRIVER_IDS", "AMBAR_TEST_IDS"):
    os.environ.pop(k, None)
logging.basicConfig(level=logging.ERROR)
from datetime import datetime, timezone
from aiohttp.test_utils import make_mocked_request
import config, config_staff as staff, db, api_server, op_route, owner_routes as owr
import driver_routes as dr, operator_routes as op

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<70} got {got!r}  want {want!r}")
    if not ok: fails.append(name)

TD = config.TEST_DRIVER_NAME
CAT = [{"id": "gin", "name": "Джин", "cat": "Джин", "price": 95, "price_full": 100, "stock": True}]
CATD = {p["id"]: p for p in CAT}
op._load_catalog = lambda: CAT
op._catalog_by_id = lambda: CATD
api_server._load_catalog_by_id = lambda: CATD

# ── подменённая база ────────────────────────────────────────────────────────
ORDERS, SENT, LOG = {}, [], {}
def _log(k, *a): LOG.setdefault(k, []).append(a)
async def get_order(oid): return copy.deepcopy(ORDERS.get(oid))
async def save_order(oid, data): ORDERS[oid] = copy.deepcopy(data); _log("save_order", oid, data.get("test"))
async def update_order(oid, **kw): ORDERS.setdefault(oid, {}).update(kw); _log("update_order", oid, kw)
async def claim_order(oid, fields):
    o = ORDERS.get(oid)
    if not o or o.get("status") != "pending": return None
    o.update(fields); return copy.deepcopy(o)
async def get_all_orders(office_id=None, *, test=False):
    return {k: copy.deepcopy(v) for k, v in ORDERS.items()
            if test is None or bool(v.get("test")) == bool(test)}
async def get_orders_in_range(a, b, office_id=None, limit=500, fields=None, *, test=False):
    _log("in_range", test)
    return [copy.deepcopy(v) for v in ORDERS.values() if test is None or bool(v.get("test")) == bool(test)]
async def _none(*a, **k): return None
async def _false(*a, **k): return False
async def _empty(*a, **k): return {}
async def _list(*a, **k): return []
USERS = {1: {"telegram_id": 1, "verified": True, "phone_verified": "+971500000000", "orders_total": 3},
         2: {"telegram_id": 2, "verified": True, "phone_verified": "+971500000001", "orders_total": 3}}
async def get_user(uid): return copy.deepcopy(USERS.get(uid))
async def _increment_user(uid, **kw): _log("increment", uid, kw)
async def save_address(uid, a): _log("save_address", uid)
async def add_debt(*a, **k): _log("add_debt", a)
async def insert_notification(*a, **k): _log("insert_notification", a[0])
DAYS = {}
async def get_driver_day(day, driver): return copy.deepcopy(DAYS.get((day, driver)))
async def save_driver_day(day, driver, fields): DAYS.setdefault((day, driver), {}).update(fields); _log("save_driver_day", driver, fields)
async def order_day_now(d): return "2026-09-14"
async def driver_gate_since(day): return "2999-01-01"
async def get_debt(cid): return 0.0
for n, f in dict(get_order=get_order, save_order=save_order, update_order=update_order, claim_order=claim_order,
                 get_all_orders=get_all_orders, get_orders_in_range=get_orders_in_range, get_user=get_user,
                 _increment_user=_increment_user, save_address=save_address, add_debt=add_debt,
                 insert_notification=insert_notification, get_driver_day=get_driver_day,
                 save_driver_day=save_driver_day, order_day_now=order_day_now, driver_gate_since=driver_gate_since,
                 get_debt=get_debt, is_banned=_false, upsert_user=_none, set_user_field=_none, verify_user=_none,
                 staff_map_get=_empty, driver_map_get=_empty, geo_lock_get=_none, panic_get=_false,
                 debts_of=_empty, shifts_for_day=_empty, drv_msg_add=_none, get_owners_subscribed_to=_list,
                 get_all_manager_ids=_list, is_debt_allowed=_false, is_free_allowed=_false,
                 award_referral_points=_none, get_user_orders=_list).items():
    setattr(db, n, f)

# ── отправки: только считаем ────────────────────────────────────────────────
async def tg_send(token, chat_id, text, *a, **k):
    SENT.append((int(chat_id), str(text)[:60])); return {"ok": True, "result": {"message_id": 1}}
api_server.tg_send = tg_send; api_server.tg_edit = tg_send
owr.tg_send = tg_send
api_server._rate_limited = lambda *a, **k: False
import tobacco; tobacco.order_blocked = lambda *a, **k: False
import config_offices; config_offices.resolve_office = lambda src: ("alguses", "Алгусес", "form")
import config_gift; config_gift.apply = lambda items, sub, cat: items
async def _total(items, tip=0): return float(sum(i.get("price", 0) * i.get("qty", 1) for i in items) + (tip or 0))
async def _sub(items): return float(sum(i.get("price", 0) * i.get("qty", 1) for i in items))
api_server._recompute_order_total_aed = _total; api_server._goods_subtotal_aed = _sub
op._customer_card = _none; op._refresh_cards = _none
op._needs_verify = _false
async def _true(*a, **k): return True
op._is_open = _true                      # смена района открыта — проверяем не её
import geo_watch; geo_watch.geo_bot_link = _none
if hasattr(api_server, "render_customer_card"):
    api_server.render_customer_card = lambda o, lang="ru": "card"

def raw(h):
    """Снять обёртки авторизации — как в test_driver_operator_flow."""
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt: return h
        h = nxt

# ══ 1. конфиг и роли ═══════════════════════════════════════════════════════
print("— конфиг: тест-роли по умолчанию у владельцев")
eq("TEST_ORDER_IDS = владельцы", config.TEST_ORDER_IDS, {1})
eq("TEST_OPERATOR_IDS = владельцы", config.TEST_OPERATOR_IDS, {1})
eq("TEST_DRIVER_IDS = владельцы", config.TEST_DRIVER_IDS, {1})
t = staff.test_driver(1)
eq("тест-водитель по id владельца", (t["name"], t["test"], bool(t["district"])), (TD, True, True))
eq("настоящий водитель не тест-водитель", staff.test_driver(555), None)
eq("чужой — никто", staff.driver_or_test(999), None)
eq("driver_chats тест-водителя → тест-аккаунты", staff.driver_chats(TD), [1])
eq("driver_chats настоящего", staff.driver_chats("Худоба"), [555])

# ══ 2. фильтр в db.py: запросы к Mongo ══════════════════════════════════════
print("— db.py: все выборки прячут тест по умолчанию")
FILTS = []
class _Cur:
    def __init__(self, f): self.f = f
    def sort(self, *a, **k): return self
    async def to_list(self, length=None): return []
    def __aiter__(self): return self
    async def __anext__(self): raise StopAsyncIteration
class _Coll:
    def __init__(self, name): self.name = name
    def find(self, filt, proj=None): FILTS.append((self.name, filt)); return _Cur(filt)
    def aggregate(self, pipeline, *a, **k): return _Cur(pipeline)
    async def find_one(self, *a, **k): return None
class _DB:
    def __getattr__(self, name): return _Coll(name)
_real_dbon = db._db_or_none; db._db_or_none = lambda: _DB()
db._orders_dirty()
import importlib
real = {n: getattr(importlib.import_module("db"), n) for n in ()}
async def _dbq():
    # настоящие функции db (мы подменили лишь _db_or_none)
    import db as D
    orig = {}
    for n in ("get_all_orders", "orders_from", "get_orders_in_range", "sold_since", "qr_consumed",
              "orders_between", "get_driver_days", "get_driver_days_range"):
        orig[n] = getattr(D, n)
    # восстановить настоящие реализации из исходника модуля
    src_mod = importlib.reload(D)
    src_mod._db_or_none = lambda: _DB()
    src_mod._orders_dirty()
    await src_mod.get_all_orders(); await src_mod.get_all_orders(test=True); await src_mod.get_all_orders(test=None)
    await src_mod.orders_from("2026-01-01"); await src_mod.orders_from("2026-01-01", test=True)
    await src_mod.get_orders_in_range("a", "b"); await src_mod.get_orders_in_range("a", "b", test=True)
    await src_mod.sold_since("2026-01-01"); await src_mod.qr_consumed({"alguses": datetime.now(timezone.utc)})
    await src_mod.orders_between("a", "b")
    await src_mod.get_driver_days("d"); await src_mod.get_driver_days("d", test=True)
    await src_mod.get_driver_days_range("a", "b")
    # вернуть подмены для остальных проверок
    for n, f in orig.items(): setattr(src_mod, n, f)
    for n in ("get_order", "save_order", "update_order", "claim_order", "get_user", "_increment_user",
              "save_address", "add_debt", "insert_notification", "get_driver_day", "save_driver_day",
              "order_day_now", "driver_gate_since", "get_debt"):
        setattr(src_mod, n, globals()[n])
    for n, f in dict(is_banned=_false, upsert_user=_none, set_user_field=_none, verify_user=_none,
                     staff_map_get=_empty, driver_map_get=_empty, geo_lock_get=_none, panic_get=_false,
                     debts_of=_empty, shifts_for_day=_empty, drv_msg_add=_none, get_owners_subscribed_to=_list,
                     get_all_manager_ids=_list, is_debt_allowed=_false, is_free_allowed=_false,
                     award_referral_points=_none, get_user_orders=_list).items():
        setattr(src_mod, n, f)
    return src_mod

async def main():
    await _dbq()
    f = [x for x in FILTS]
    def has(name, idx, key, val):
        eq(name, f[idx][1].get(key), val)
    has("get_all_orders() прячет тест", 0, "test", {"$ne": True})
    has("get_all_orders(test=True) — только тест", 1, "test", True)
    eq("get_all_orders(test=None) — без фильтра", "test" in f[2][1], False)
    has("orders_from прячет тест", 3, "test", {"$ne": True}); has("orders_from(test=True)", 4, "test", True)
    has("get_orders_in_range прячет тест", 5, "test", {"$ne": True}); has("get_orders_in_range(test=True)", 6, "test", True)
    has("sold_since (склад) прячет тест", 7, "test", {"$ne": True})
    has("qr_consumed (реестр) прячет тест", 8, "test", {"$ne": True})
    has("orders_between (финансы) прячет тест", 9, "test", {"$ne": True})
    has("get_driver_days без тест-водителей", 10, "driver", {"$nin": [TD]}); has("get_driver_days(test=True)", 11, "driver", {"$in": [TD]})
    has("get_driver_days_range без тест-водителей", 12, "driver", {"$nin": [TD]})
    eq("op_route.send(test=True) → только тестерам", await op_route.send("t", "alguses", test=True), {"1": 1})
    SENT.clear()

    # ══ 3. клиентское приложение: флажок и метка ═══════════════════════════
    print("— клиент: флажок ставит метку только тестеру")
    async def me(uid):
        api_server.validate_init_data = lambda s: {"id": uid, "first_name": "Т"}
        req = make_mocked_request("GET", "/api/me", headers={"Authorization": "tma x"})
        return json.loads((await api_server.handle_me(req)).text)
    eq("/api/me тестеру: test_orders", (await me(1))["test_orders"], True)
    eq("/api/me обычному: без флажка", (await me(2))["test_orders"], False)

    async def order(uid, **extra):
        api_server.validate_init_data = lambda s: {"id": uid, "first_name": "Т", "username": "t"}
        body = {"initData": "x", "items": [{"id": "gin", "name": "Джин", "qty": 1, "price": 95, "line_total": 95}],
                "phone": "+971500000000", "address": "ул. Тест 1", "office_id": "alguses", "lang": "ru", **extra}
        req = make_mocked_request("POST", "/api/order"); req._read_bytes = json.dumps(body).encode()
        r = await api_server.handle_create_order(req)
        return r.status, json.loads(r.text)
    calls = []
    async def spy_send(text, district="", parse_mode="HTML", reply_markup=None, register=True, test=False):
        calls.append(("op_route", test)); return {"1": 1} if test else {"7": 1}
    op_route.send = spy_send
    async def spy_new(*a, **k): calls.append(("owner_new", k.get("test", False)))
    async def spy_owners(ev, text, *a, **k): calls.append(("owners", ev, k.get("test", False))); return []
    REAL_NEW, REAL_OWNERS = owr.notify_new_order, owr.notify_owners
    owr.notify_new_order = spy_new; owr.notify_owners = spy_owners

    st, r = await order(1, test=True)
    oid_t = r.get("order_id")
    eq("тестер + флажок → 200, test в ответе", (st, r.get("test")), (200, True))
    eq("в базе заказ с меткой test", ORDERS[oid_t].get("test"), True)
    eq("операторам ушло как тест (op_route test=True)", ("op_route", True) in calls, True)
    eq("владельцу ушло как тест", ("owner_new", True) in calls, True)
    eq("счётчик заказов клиента не тронут", LOG.get("increment"), None)
    eq("адрес в книгу не записан", LOG.get("save_address"), None)
    calls.clear(); LOG.clear()
    st, r = await order(1)
    oid_r = r.get("order_id")
    eq("тестер без флажка → настоящий заказ", (st, r.get("test"), ORDERS[oid_r].get("test")), (200, False, None))
    eq("настоящий заказ ушёл операторам как обычно", ("op_route", False) in calls, True)
    eq("счётчик заказов клиента увеличен", bool(LOG.get("increment")), True)
    calls.clear(); LOG.clear()
    st, r = await order(2, test=True)
    eq("не тестер + флажок → метка НЕ ставится", (st, ORDERS[r["order_id"]].get("test")), (200, None))
    st, r = await order(1, test=True, payment_method="crypto", paid=True, crypto={"asset": "USDT"})
    eq("тест криптой не оформляется", (st, r.get("error")), (400, "test_no_crypto"))

    # ══ 4. панель оператора ═══════════════════════════════════════════════
    print("— оператор: тест-оператор видит и трогает только тест-заказы")
    ORDERS.clear(); SENT.clear()
    ORDERS["T1"] = {"order_id": "T1", "status": "pending", "office_id": "alguses", "customer_id": 1, "total": 95,
                    "items": [{"id": "gin", "name": "Джин", "qty": 1, "price": 95}], "timestamp": datetime.now(timezone.utc).isoformat(),
                    "address": "ул", "source": "app", "test": True}
    ORDERS["R1"] = {**copy.deepcopy(ORDERS["T1"]), "order_id": "R1", "test": False, "customer_id": 2}
    del ORDERS["R1"]["test"]
    def opreq(uid, method="GET", path="/x", body=None, oid=None, query=None, hdr=None):
        op._validate_operator_init_data = lambda s: {"id": uid, "first_name": "Оп"}
        req = make_mocked_request(method, path + (("?" + query) if query else ""),
                                  headers={"Authorization": "tma x", **(hdr or {})},
                                  match_info={"oid": oid} if oid else {})
        if body is not None: req._read_bytes = json.dumps(body).encode()
        return req
    async def call(h, req):
        r = await h(req); return r.status, json.loads(r.text)
    st, r = await call(op.handle_ping, opreq(1))
    eq("тест-оператор: ping test=True, за планшетом «Тест»", (st, r["test"], r["pinned"]), (200, True, config.TEST_PERSON))
    eq("тест-оператор: в каждом районе только тест-водитель", {tuple(d["drivers"]) for d in r["districts"]}, {(TD,)})
    eq("тест-оператор: люди — один «Тест», все районы", (len(r["people"]), r["people"][0]["senior"]), (1, True))
    st, r = await call(op.handle_ping, opreq(7))
    eq("настоящий оператор: test=False, водители настоящие", (r["test"], TD in r["districts"][0]["drivers"]), (False, False))
    st, r = await call(op.handle_ping, opreq(9))
    eq("чужой — 403", st, 403)
    # тестер, который заодно настоящий оператор: по умолчанию боевая панель,
    # тест-режим только по заголовку переключателя
    op.OPERATOR_IDS.append(1)
    st, r = await call(op.handle_ping, opreq(1))
    eq("оператор+тестер без переключателя: боевая панель, test_allowed", (st, r["test"], r["test_allowed"]), (200, False, True))
    st, r = await call(op.handle_ping, opreq(1, hdr={"X-Ambar-Test": "1"}))
    eq("оператор+тестер с переключателем: тест-режим", (st, r["test"], r["pinned"]), (200, True, config.TEST_PERSON))
    st, r = await call(op.handle_ping, opreq(7, hdr={"X-Ambar-Test": "1"}))
    eq("не тестер с заголовком — заголовок не действует", (st, r["test"], r["test_allowed"]), (200, False, False))
    st, r = await call(op.handle_ping, opreq(9, hdr={"X-Ambar-Test": "1"}))
    eq("чужой с заголовком — 403", st, 403)
    op.OPERATOR_IDS.remove(1)
    st, r = await call(op.handle_queue, opreq(1, query="as=" + config.TEST_PERSON))
    eq("очередь тест-оператора: только T1", sorted(x["order_id"] for x in r["new"]), ["T1"])
    eq("очередь тест-оператора: в районах только тест-водитель", {tuple(d["drivers"]) for d in r["districts"]}, {(TD,)})
    st, r = await call(op.handle_shift_open, opreq(1, "POST", body={"as": config.TEST_PERSON, "district": "jvc"}))
    eq("смена района из тест-режима — 403 test_mode", (st, r.get("error")), (403, "test_mode"))
    op._people = lambda districts: [{"name": "Парвиз", "senior": True, "districts": [d["id"] for d in districts]}]
    st, r = await call(op.handle_queue, opreq(7, query="as=Парвиз"))
    eq("очередь настоящего оператора: только R1", sorted(x["order_id"] for x in r["new"]), ["R1"])
    st, r = await call(op.handle_accept, opreq(1, "POST", body={"as": config.TEST_PERSON, "driver": "Худоба", "eta": 30}, oid="T1"))
    eq("тест-заказ настоящему водителю — нельзя", (st, r.get("error")), (400, "driver_required"))
    st, r = await call(op.handle_accept, opreq(1, "POST", body={"as": config.TEST_PERSON, "driver": TD, "eta": 30}, oid="R1"))
    eq("тест-оператор к настоящему заказу — 403 test_mismatch", (st, r.get("error")), (403, "test_mismatch"))
    st, r = await call(op.handle_accept, opreq(7, "POST", body={"as": "Парвиз", "driver": TD, "eta": 30}, oid="T1"))
    eq("настоящий оператор к тест-заказу — 403", (st, r.get("error")), (403, "test_mismatch"))
    st, r = await call(op.handle_accept, opreq(7, "POST", body={"as": "Парвиз", "driver": TD, "eta": 30}, oid="R1"))
    eq("настоящему заказу тест-водителя не дать", (st, r.get("error")), (400, "driver_required"))
    SENT.clear()
    st, r = await call(op.handle_accept, opreq(1, "POST", body={"as": config.TEST_PERSON, "driver": TD, "eta": 30}, oid="T1"))
    eq("тест-заказ принят тест-водителю", (st, ORDERS["T1"]["status"], ORDERS["T1"]["driver"]), (200, "approved", TD))
    eq("водителю ушло в тест-аккаунт с пометкой", any(c == 1 and "ТЕСТ" in t for c, t in SENT), True)
    eq("настоящему оператору/водителю ничего", any(c in (7, 555) for c, t in SENT), False)
    SENT.clear()
    st, r = await call(op.handle_create, opreq(1, "POST", body={"as": config.TEST_PERSON, "district_id": "jvc", "driver": TD,
                                                              "items": [{"id": "gin", "qty": 2}], "customer_name": "Т"}))
    eq("ручной заказ тест-оператора создан с меткой", (st, ORDERS[r["order_id"]].get("test")), (200, True))
    eq("карточка ручного заказа только тестерам", sorted({c for c, t in SENT}), [1])
    st, r = await call(op.handle_create, opreq(7, "POST", body={"as": "Парвиз", "district_id": "jvc", "driver": TD,
                                                              "items": [{"id": "gin", "qty": 2}]}))
    eq("настоящий оператор не назначит тест-водителя", (st, r.get("error")), (400, "driver_required"))
    # доставка тест-заказа: без счётчиков, владельцу как тест
    LOG.clear(); calls.clear()
    ORDERS["T1"]["status"] = "approved"
    await op._close_delivered("T1", ORDERS["T1"], "Тест")
    eq("тест-заказ закрыт доставкой", ORDERS["T1"]["status"], "delivered")
    eq("счётчики клиента не тронуты", LOG.get("increment"), None)
    eq("владельцу — как тест", ("owners", "orders.delivered", True) in calls, True)

    # ══ 5. приложение водителя ════════════════════════════════════════════
    print("— водитель: тест-водитель видит только свои тест-заказы, склад закрыт")
    def drreq(uid, method="GET", body=None, oid=None):
        dr._valid_init_data = lambda s, t: {"id": uid, "first_name": "В"}
        req = make_mocked_request(method, "/x", headers={"Authorization": "tma x"}, match_info={"oid": oid} if oid else {})
        if body is not None: req._read_bytes = json.dumps(body).encode()
        return req
    st, r = await call(dr.handle_ping, drreq(1))
    eq("тест-водитель: ping test=True, имя", (st, r["driver"]["test"], r["driver"]["name"]), (200, True, TD))
    st, r = await call(dr.handle_ping, drreq(555))
    eq("настоящий водитель: test=False", (st, r["driver"]["test"], r["driver"]["name"]), (200, False, "Худоба"))
    st, r = await call(dr.handle_ping, drreq(9))
    eq("чужой — 403", st, 403)
    # тестер, который заодно настоящий водитель: по умолчанию настоящий,
    # тест-режим только по заголовку переключателя
    staff.DRIVER_IDS["Худоба"] = 1; staff.DRIVER_BY_TG[1] = "Худоба"
    st, r = await call(dr.handle_ping, drreq(1))
    eq("водитель+тестер без переключателя: Худоба, test_allowed", (r["driver"]["name"], r["driver"]["test"], r["driver"]["test_allowed"]), ("Худоба", False, True))
    dr._valid_init_data = lambda s, t: {"id": 1, "first_name": "В"}
    req = make_mocked_request("GET", "/x", headers={"Authorization": "tma x", "X-Ambar-Test": "1"})
    st, r = await call(dr.handle_ping, req)
    eq("водитель+тестер с переключателем: Тест-водитель", (r["driver"]["name"], r["driver"]["test"]), (TD, True))
    eq("driver_chats тест-водителя и при боевой роли аккаунта", staff.driver_chats(TD), [1])
    del staff.DRIVER_IDS["Худоба"]; del staff.DRIVER_BY_TG[1]
    staff.DRIVER_IDS["Худоба"] = 555; staff.DRIVER_BY_TG[555] = "Худоба"
    ORDERS["T1"].update(status="approved", driver=TD, confirmed_at=datetime.now(timezone.utc).isoformat(), deliver_by="23:59")
    ORDERS["R2"] = {**copy.deepcopy(ORDERS["R1"]), "order_id": "R2", "status": "approved", "driver": TD}
    LOG.clear()
    st, r = await call(dr.handle_orders, drreq(1))
    eq("заказы тест-водителя берутся с test=True", LOG.get("in_range"), [(True,)])
    ids = sorted(x["order_id"] for x in r["active"])
    eq("тест-водитель видит T1 и ручной тест-заказ, но не R2 (настоящий с его именем)",
       ("T1" in ids, "R2" in ids, len(ids)), (True, False, 2))
    LOG.clear()
    st, r = await call(dr.handle_orders, drreq(555))
    eq("настоящий водитель берёт с test=False", LOG.get("in_range"), [(False,)])
    st, r = await call(dr.handle_supply_list, drreq(1))
    eq("закупка тест-водителю пуста", (st, r), (200, {"mine": [], "free": [], "extra": [], "taken": []}))
    for h, nm in ((dr.handle_move_scan, "move_scan"), (dr.handle_code_info, "code_info"), (dr.handle_writeoff_scan, "writeoff_scan"),
                  (dr.handle_expense_add, "expense_add"), (dr.handle_supply_claim, "supply_claim")):
        st, r = await call(h, drreq(1, "POST", body={"code": "x", "to": "jvc", "amount": 5, "kind": "fuel", "comment": "x"}, oid=None))
        eq(f"{nm} тест-водителю — 403 test_account", (st, r.get("error")), (403, "test_account"))
    st, r = await call(dr.handle_supply_list, drreq(555)) if False else (200, {})
    # смена без отметки оператора, по точке из приложения
    async def geo(name, since=None): return {"ok": False, "fresh": True, "stream": False, "until": "", "left_min": 0,
                                             "endless": False, "age_sec": 5, "still_sec": 0, "lost": False, "watch_ok": False}
    dr._geo_state = geo
    DAYS.clear(); LOG.clear()
    st, r = await call(dr.handle_shift_open, drreq(1, "POST", body={}))
    eq("смена тест-водителя открылась без отметки и без трансляции", (st, r.get("opened"), r.get("working")), (200, True, True))
    eq("день тест-водителя помечен test", DAYS[(r["day"], TD)].get("test"), True)
    st, r = await call(dr.handle_shift_open, drreq(555, "POST", body={}))
    eq("настоящему без отметки — not_marked", (st, r.get("error")), (409, "not_marked"))
    # расчёт по тест-заказу: долг не пишется; просьба — тестерам
    LOG.clear(); SENT.clear()
    st, r = await call(dr.handle_settle, drreq(1, "POST", body={"taken": 100}, oid="T1"))
    eq("расчёт записан, долг клиента не тронут", (st, ORDERS["T1"]["settle"]["taken"], LOG.get("add_debt")), (200, 100.0, None))
    st, r = await call(dr.handle_delivered, drreq(1, "POST", body={"settled": True}, oid="T1"))
    eq("«Доставил» по тест-заказу — просьба открыта", (st, ORDERS["T1"]["driver_req"]["kind"]), (200, "delivered"))
    eq("просьба ушла только тестерам с пометкой", sorted({c for c, t in SENT}) == [1] and all("ТЕСТ" in t for c, t in SENT), True)
    st, r = await call(dr.handle_debt_settle, drreq(1, "POST", body={}, oid="T1"))
    eq("возврат долга по тест-заказу закрыт", (st, r.get("error")), (403, "test_account"))

    # ══ 6. владельцу ══════════════════════════════════════════════════════
    print("— владелец: события тест-заказов только тестерам и мимо архива")
    owr.notify_owners = REAL_OWNERS
    SENT.clear(); LOG.clear()
    sent = await owr.notify_owners("orders.x", "текст", test=True)
    eq("notify_owners(test) → тестерам с пометкой", (sorted({c for c, t in SENT}), all("ТЕСТ" in t for c, t in SENT), len(sent)), ([1], True, 1))
    eq("в архив не записано", LOG.get("insert_notification"), None)
    SENT.clear()
    await owr.notify_owners_force("orders.y", "текст", test=True)
    eq("notify_owners_force(test) → тестерам", sorted({c for c, t in SENT}), [1])
    SENT.clear()
    await REAL_NEW("T9", 95, "Т", "+971", "ул", "Алгусес", 1, 0, [], [], items=[{"name": "Джин", "qty": 1}], test=True)
    eq("notify_new_order(test) → одно сообщение тестерам с пометкой",
       (sorted({c for c, t in SENT}), len(SENT), all("ТЕСТ" in t for c, t in SENT)), ([1], 1, True))
    eq("архив уведомлений пуст", LOG.get("insert_notification"), None)

    # ══ 7. второй тест-аккаунт ════════════════════════════════════════════
    # 18 сен 2026: тест-водитель больше не один. Имя берётся из записи реестра,
    # и всё, что считается по имени — дни, рассылки, фильтры — должно знать
    # оба, иначе смена второго уедет в настоящие деньги.
    print("— два тест-водителя: у каждого своё имя")
    P = "Тест-Водитель(Парвиз)"
    staff.apply_roster([{"name": "Фарух", "district": "jvc", "telegram_id": 22},
                        {"name": TD, "district": "jvc", "telegram_id": 1, "test": True},
                        {"name": P, "district": "jvc", "telegram_id": 77, "test": True}])
    t2 = staff.test_driver(77)
    eq("второй тест-аккаунт — со своим именем", (t2["name"], t2["test"]), (P, True))
    eq("второй в тест-районе, как и первый", t2["district"], staff.test_driver(1)["district"])
    eq("первый остался прежним", staff.test_driver(1)["name"], TD)
    eq("имена тест-водителей", sorted(staff.test_driver_names()), sorted([TD, P]))
    eq("оба — тестовые по имени", (staff.is_test_driver(P), staff.is_test_driver("Фарух")), (True, False))
    eq("второму пишем только в его аккаунт", staff.driver_chats(P), [77])
    eq("первому — его аккаунт, не чужой", staff.driver_chats(TD), [1])
    eq("тест-записи не водители района", [d["name"] for d in staff.drivers() if d["name"] in (TD, P)], [])
    eq("дни считаются без обоих", db._test_driver_filt(False), {"driver": {"$nin": sorted([TD, P])}})
    eq("тест-дни — оба", db._test_driver_filt(True), {"driver": {"$in": sorted([TD, P])}})
    eq("оператору тест-заказ отдаётся любому из двух", sorted(op._drivers_of(True)), sorted([TD, P]))

    print()
    print("FAILED:", fails) if fails else print("ALL OK — тест-режим: клиент, оператор, водитель, база, рассылки")
    sys.exit(1 if fails else 0)

asyncio.run(main())
