"""Заказ, возвращённый в доставку, не исчезает с панели (#AMB1590679, 25 сен 2026).

Оператор вернул доставленный заказ, чтобы поправить состав, — и заказ пропал
отовсюду. Причина: день заказа — та смена, в которую его ПРИНЯЛИ (ночью, то
есть вчерашняя), а вернули его уже в следующую. Лента «в работе» отсекала
чужой день, а в «закрытые» он больше не подходил по статусу: ни в одной ленте,
при живом статусе и назначенном водителе.

  • доставленный заказ вчерашней смены вернули — он в ленте «в работе»;
  • и завтра тоже: правило «в работе показываем любого возраста» не про сутки;
  • строка несёт свой день — панель ставит пометку «вчера»;
  • отметка о доставке снята: заказ, снова едущий к клиенту, не может стоять
    в ленте событий строкой «доставлен»;
  • отменённый возвращается так же;
  • закрытые по-прежнему листаются по дням — это не сломано.

    python3 tools/test_order_revert.py
"""
import asyncio, copy, inspect, json, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""
os.environ.setdefault("AMBAR_OWNER_IDS", "1"); os.environ.setdefault("OPERATOR_IDS", "7")
os.environ.setdefault("OPERATOR_BOT_TOKEN", "x"); os.environ.setdefault("DRIVER_BOT_TOKEN", "x")
logging.disable(logging.WARNING)
from datetime import datetime, timedelta, timezone                 # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, operator_routes as op                                   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt: return h
        h = nxt


СЕГОДНЯ = op._biz_date(datetime.now(op.DUBAI_TZ))
ВЧЕРА = СЕГОДНЯ - timedelta(days=1)
РАЙОНЫ = [{"id": "tecom", "code": "B5", "name": "Тиком", "operator": "Умар", "drivers": ["Муин"]},
          {"id": "jvc", "code": "B1", "name": "JVC", "operator": "Умар", "drivers": ["Худоба"]}]

ORDERS: dict = {}


def заказ(oid, *, day, status, **kw):
    """Заказ, принятый в смену day: ночью, как #AMB1590679 (01:59 UTC)."""
    принят = (datetime.combine(day, datetime.min.time()) + timedelta(days=1, hours=1, minutes=59))
    o = {"order_id": oid, "status": status, "day": day.isoformat(),
         "timestamp": принят.isoformat() + "+00:00", "confirmed_at": принят.isoformat() + "+00:00",
         "office_id": "tecom", "office_name": "Тиком", "district": "Тиком", "district_id": "tecom",
         "driver": "Муин", "total": 600, "customer_id": 0, "source": "manual",
         "items": [{"id": "p116", "name": "Minuty", "qty": 2, "price": 200}],
         "op_msg_ids": {}, "updated_at": принят.isoformat() + "+00:00"}
    if status == "delivered":
        o.update(delivered_at=(принят + timedelta(minutes=6)).isoformat() + "+00:00",
                 delivered_by="Умар", delivered_by_driver="Муин")
    if status == "cancelled":
        o.update(cancelled_at=(принят + timedelta(minutes=6)).isoformat() + "+00:00",
                 cancelled_by="Умар")
    o.update(kw)
    ORDERS[oid] = o
    return o


# ── подменённая база и отправки ─────────────────────────────────────────────
async def get_order(oid): return copy.deepcopy(ORDERS.get(oid))
async def update_order(oid, **kw): ORDERS.setdefault(oid, {}).update(kw)
async def get_all_orders(office_id=None, *, test=False):
    return {k: copy.deepcopy(v) for k, v in ORDERS.items() if not v.get("test")}
async def _none(*a, **k): return None
async def _false(*a, **k): return False
async def _list(*a, **k): return []
for n, f in dict(get_order=get_order, update_order=update_order, get_all_orders=get_all_orders,
                 unclaim_order_flag=_false, unclaim_debt_delivery=_false, _increment_user=_none,
                 add_debt=_none, insert_notification=_none, get_owners_subscribed_to=_list,
                 get_all_manager_ids=_list).items():
    setattr(db, n, f)

ВЕСТИ = []
async def _fresh(*a, **k): return copy.deepcopy(РАЙОНЫ)
op._fresh_districts = _fresh
op._districts = lambda: copy.deepcopy(РАЙОНЫ)
op._people = lambda districts: [{"name": "Умар", "senior": True,
                                 "districts": [d["id"] for d in districts]}]
op._needs_verify = _false
op._refresh_cards = _none
op._customer_card = _none
op._is_open = lambda *a, **k: _true()
async def _true(*a, **k): return True
op._is_open = _true
op.notify_driver = _none
op.close_req.day_rows = _list
op.close_req.open_for = _list
op.close_req.released_names = lambda rows: set()
import owner_routes                                                # noqa: E402
async def notify_force(key, text, **kw): ВЕСТИ.append((key, text)); return []
owner_routes.notify_owners_force = notify_force
owner_routes.notify_owners = notify_force


async def очередь(day=None):
    q = "as=Умар" + (f"&day={day.isoformat()}" if day else "")
    req = make_mocked_request("GET", "/x?" + q)
    req["op_user"] = {"id": 7, "name": "Умар"}
    r = await raw(op.handle_queue)(req)
    return json.loads(r.text)


async def вернуть(oid):
    req = make_mocked_request("POST", f"/x/{oid}/undeliver", match_info={"oid": oid})
    req["op_user"] = {"id": 7, "name": "Умар"}
    req._read_bytes = b"{}"
    r = await raw(op.handle_undeliver)(req)
    return r.status, json.loads(r.text)


def ленты(q):
    return {k: [x["order_id"] for x in q[k]] for k in ("new", "work", "done")}


async def main():
    print(f"── смена сегодня {СЕГОДНЯ}, заказ принят в смену {ВЧЕРА} ──────")
    заказ("AMB1", day=ВЧЕРА, status="delivered")

    q = await очередь()
    eq("пока доставлен — в лентах его нет (закрытые листают по дням)", ленты(q),
       {"new": [], "work": [], "done": []})
    eq("в «закрытых» за вчера — есть", ленты(await очередь(ВЧЕРА))["done"], ["AMB1"])

    print("── оператор вернул его в доставку ─────────────────────────────")
    st, r = await вернуть("AMB1")
    eq("ручка ответила", (st, r.get("status")), (200, "approved"))
    o = ORDERS["AMB1"]
    eq("статус в базе", o["status"], "approved")
    eq("ОТМЕТКА О ДОСТАВКЕ СНЯТА", (o["delivered_at"], o["delivered_by"], o["delivered_by_driver"]),
       ("", "", ""))
    eq("владельцу сказали", [k for k, _ in ВЕСТИ], ["orders.reverted"])

    q = await очередь()
    eq("ЗАКАЗ В ЛЕНТЕ «В РАБОТЕ» — не исчез", ленты(q)["work"], ["AMB1"])
    строка = (q["work"] or [{}])[0]   # пусто — это и есть поломка, падать не надо
    eq("строка несёт свой день", строка.get("day"), ВЧЕРА.isoformat())
    eq("и панель знает, какой день сегодня", q.get("today"), СЕГОДНЯ.isoformat())
    eq("в «закрытых» его больше нет", ленты(await очередь(ВЧЕРА))["done"], [])
    eq("счётчик «в работе» его считает", q["counts"]["manual"], 1)

    print("── лента событий не зовёт его доставленным ────────────────────")
    req = make_mocked_request("GET", "/x?as=Умар")
    req["op_user"] = {"id": 7, "name": "Умар"}
    feed = json.loads((await raw(op.handle_feed)(req)).text)
    было = [x for x in (feed.get("recent") or []) if x.get("order_id") == "AMB1"]
    eq("строки «доставлен» нет", [x.get("kind") for x in было], [])

    print("── и завтра он никуда не денется ──────────────────────────────")
    # Панель считает «сегодня» от часов; двигаем не часы, а день заказа —
    # проверяем то же правило: в работе показываем любого возраста.
    ORDERS["AMB1"]["day"] = (ВЧЕРА - timedelta(days=6)).isoformat()
    eq("заказ недельной давности всё равно в работе", ленты(await очередь())["work"], ["AMB1"])

    print("── отменённый возвращается так же ─────────────────────────────")
    ORDERS.clear(); ВЕСТИ.clear()
    заказ("AMB2", day=ВЧЕРА, status="cancelled")
    st, r = await вернуть("AMB2")
    eq("ручка ответила", (st, r.get("status")), (200, "approved"))
    eq("отметка отмены снята", (ORDERS["AMB2"]["cancelled_at"], ORDERS["AMB2"]["cancelled_by"]),
       ("", ""))
    eq("и он в ленте «в работе»", ленты(await очередь())["work"], ["AMB2"])

    print("── закрытые по-прежнему по дням ───────────────────────────────")
    ORDERS.clear()
    заказ("AMB3", day=ВЧЕРА, status="delivered")
    заказ("AMB4", day=СЕГОДНЯ, status="delivered")
    eq("за сегодня — только сегодняшний", ленты(await очередь())["done"], ["AMB4"])
    eq("за вчера — только вчерашний", ленты(await очередь(ВЧЕРА))["done"], ["AMB3"])

    print("── закрыть его снова можно, день не мешает ────────────────────")
    ORDERS.clear(); ВЕСТИ.clear()
    заказ("AMB5", day=ВЧЕРА, status="delivered")
    await вернуть("AMB5")
    eq("вернулся в работу", ленты(await очередь())["work"], ["AMB5"])

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
