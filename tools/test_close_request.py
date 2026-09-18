"""Просьба водителя закрыть смену раньше оператора (владелец, 18 сен 2026).
mongomock + настоящие close_req, driver_routes.handle_shift_close и
operator_routes._released_now. Правила владельца:
  • просить можно, только когда все заказы доставлены;
  • отпуская, оператор решает — питание 80 или 40;
  • молчит оператор 10 минут — запрос уходит старшему;
  • после отказа просить снова — не раньше чем через 15 минут.
Плюс то, что следует из них: решает оператор района (чужой район — отказ),
решает ровно один раз, отпущенный закрывает смену без закрытия района, а
новый заказ ему назначить нельзя."""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["OPERATOR_BOT_TOKEN"] = "t"
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, config_staff as staff, close_req as CR
import driver_routes as dr, operator_routes as op

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"
TOLD, SENT = [], []
async def tell(name, text): TOLD.append((name, text))
op.tell_driver = tell
import api_server
async def tg_send(token, cid, text, **k): SENT.append((cid, text)); return {"ok": True}
api_server.tg_send = tg_send
import bizday; bizday.biz_day = lambda *a, **k: D
dr._biz_day = lambda *a, **k: D
SHIFTS = {}
async def sfd(day): return dict(SHIFTS)
db.shifts_for_day = sfd

ME = {"name": "Фарух", "district": "jvc", "operator": "Умар"}
MATE = {"name": "Худоба", "district": "jvc", "operator": "Умар"}
JVC = {staff.drivers()[0]["district"]} if False else {"jvc"}

async def doc(name=ME["name"]): return await db.get_driver_day(D, name)
async def ask(reason="shift_end", text="", route=None, closed=False):
    return await CR.ask(D, ME, await doc(), reason, text, route or [], closed)

async def main():
    db._db = AsyncMongoMockClient()["ambar_close"]; d = db._db
    t0 = datetime.now(timezone.utc) - timedelta(hours=9)
    for who in (ME, MATE):
        await db.save_driver_day(D, who["name"], {"working": True, "shift_open_at": t0,
                                 "no_expense": {"fuel": True, "wash": True, "parking": True}})

    # ── водитель просит ─────────────────────────────────────────────────────
    eq("заказ в пути — просить нельзя", await ask(route=["AMB1"]), (409, {"error": "orders_in_route", "ids": ["AMB1"]}))
    eq("непонятная причина", (await ask(reason="xx"))[0], 400)
    eq("«другое» без слов", await ask(reason="other"), (400, {"error": "text_required"}))
    eq("смену района уже закрыли — просить нечего", (await ask(closed=True))[1]["error"], "day_closed")
    code, res = await ask()
    eq("все доставлены — запрос ушёл", (code, res["close_req"]["status"], res["close_req"]["to"]), (200, "open", "Умар"))
    eq("второй поверх первого — нет", (await ask())[1]["error"], "exists")
    eq("отозвал сам", (await CR.withdraw(D, ME["name"], await doc()))[0], 200)
    eq("после отзыва можно сразу снова", (await ask(reason="sick"))[0], 200)
    rid = (await doc())["close_req"]["id"]
    eq("отозванный лёг в историю", [h["status"] for h in (await doc()).get("close_hist") or []], ["withdrawn"])

    # ── оператор видит ──────────────────────────────────────────────────────
    CR._drop_cache()
    mine = await CR.open_for(D, {"jvc"}, route_by_driver={"Фарух": 0}, waiting_by_district={"jvc": 2})
    eq("оператор района видит запрос с контекстом",
       [(x["driver"], x["reason_t"], x["mates"], x["waiting"]) for x in mine],
       [("Фарух", "Плохо себя чувствую", ["Худоба"], 2)])
    eq("оператор чужого района — не видит", await CR.open_for(D, {"bbay"}), [])

    # ── решение ─────────────────────────────────────────────────────────────
    eq("чужой район решать не может", (await CR.decide(D, "Фарух", rid, True, 80, "", "", "Джанабиль", {"bbay"}))[0], 403)
    eq("отпустить без выбора питания — нельзя", (await CR.decide(D, "Фарух", rid, True, None, "", "", "Умар", {"jvc"}))[1]["error"], "meal_required")
    eq("отказать без причины — нельзя", (await CR.decide(D, "Фарух", rid, False, None, "", "", "Умар", {"jvc"}))[1]["error"], "reason_required")
    eq("«не сейчас» с причиной", (await CR.decide(D, "Фарух", rid, False, None, "queue", "", "Умар", {"jvc"}))[0], 200)
    eq("второе нажатие того же запроса — «уже решил»",
       (await CR.decide(D, "Фарух", rid, True, 80, "", "", "Парвиз", set(staff.OFFICE_IDS) if hasattr(staff, "OFFICE_IDS") else {"jvc"}))[1],
       {"error": "decided", "by": "Умар", "status": "no"})
    eq("водителю ушло слово оператора", ("Умар пока не отпускает" in TOLD[-1][1], "15 минут" in TOLD[-1][1]), (True, True))
    v = CR.view(await doc())
    eq("водитель видит отказ и когда можно снова", (v["status"], v["no_reason_t"], bool(v["next_at"])), ("no", "Есть заказы в очереди", True))
    eq("сразу после отказа — рано", (await ask())[1]["error"], "too_soon")
    await d.driver_days.update_one({"day": D, "driver": "Фарух"},
                                   {"$set": {"close_req.decided_at": datetime.now(timezone.utc) - timedelta(minutes=16)}})
    eq("через 15 минут — можно", (await ask(reason="shift_end"))[0], 200)
    rid = (await doc())["close_req"]["id"]

    # ── до решения закрыть смену нельзя, после — можно ──────────────────────
    async def none(*a, **k): return []
    dr._in_route = none; dr._intake_left = none; dr._moves_left = none
    h = dr.handle_shift_close
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    async def close(who):
        r = make_mocked_request("POST", "/x"); r["driver"] = dict(who)
        resp = await h(r); return resp.status, json.loads(resp.text).get("error") or "ок"
    eq("пока не отпустили и район не закрыт — нельзя", await close(ME), (409, "day_open"))
    eq("отпустил, питание 40", (await CR.decide(D, "Фарух", rid, True, 40, "", "", "Умар", {"jvc"}))[0], 200)
    day = await doc()
    eq("питание дня — 40, решение оператора главнее отметки «вышел»", (day.get("meal_rate"), staff.meal_of(day)), (40, 40))
    eq("водителю: отпустил и сколько питание", ("отпустил вас" in TOLD[-1][1], "40 AED" in TOLD[-1][1]), (True, True))
    eq("просить снова, когда уже отпустили, — незачем", (await ask())[1]["error"], "released")
    CR._drop_cache()
    eq("отпущенному новый заказ не назначить", await op._released_now("Фарух"), True)
    eq("напарнику — можно", await op._released_now("Худоба"), False)
    eq("отпущенный закрывает смену, район при этом открыт", await close(ME), (200, "ок"))
    eq("напарник — по-прежнему ждёт закрытия района", await close(MATE), (409, "day_open"))
    eq("у напарника питание по отметке — 80", staff.meal_of(await doc("Худоба")), 80)

    # ── молчит оператор — старшему ──────────────────────────────────────────
    code, _ = await CR.ask(D, MATE, await doc("Худоба"), "car", "", [], False)
    eq("напарник попросил", code, 200)
    eq("через минуту — ещё рано звать старшего", await CR.escalate_tick(), 0)
    await d.driver_days.update_one({"day": D, "driver": "Худоба"},
                                   {"$set": {"close_req.at": datetime.now(timezone.utc) - timedelta(minutes=11)}})
    eq("через 11 минут — старшему, одним сообщением", (await CR.escalate_tick(), len(SENT)), (1, len(staff.SENIOR_IDS)))
    eq("в сообщении — кто и почему", ("Худоба" in SENT[-1][1], "Проблема с машиной" in SENT[-1][1]), (True, True))
    eq("второй проход — тишина", await CR.escalate_tick(), 0)
    CR._drop_cache()
    v = [x for x in await CR.open_for(D, {"jvc"}) if x["driver"] == "Худоба"]
    eq("оператор видит, что уже ушло старшему", [x["escalated"] for x in v], [True])
    SHIFTS["jvc"] = {"closed_at": "x"}
    CR._drop_cache()
    eq("район закрыли — запрос у оператора гаснет", await CR.open_for(D, {"jvc"}), [])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
