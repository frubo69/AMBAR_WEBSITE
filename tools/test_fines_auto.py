"""Штрафы, требующие решения (владелец, 22 сен 2026: «кто-то поздно открыл
смену — после 15:00 — туда автоматически уходит сформированный штраф с двумя
кнопками: назначить или не назначать; всё уходит в историю штрафов, назначен
он в итоге или нет, — с исходом»). mongomock + настоящие db, fines_auto,
driver_routes._late_alert и finance_routes (ручка решения, история):
  • поздняя смена — штраф на решение, один на человека и день; старшему в
    сообщении — строка о штрафе и кнопка «Решить по штрафу» (STAR ?go=fines);
  • сумма — с которой этот вид назначили в прошлый раз, первый раз — пусто;
  • «Не назначать» — в зарплатах ничего, водителю ничего, в истории исход
    «Не назначен»; «Назначить» — обычный штраф (водителю — сообщение), в
    истории со своим статусом и пометкой auto;
  • решают один раз (409), без суммы назначить нельзя (400)."""
import asyncio, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["OWNER_WEBAPP_URL"] = "https://star.example/"
from datetime import datetime, timezone, timedelta
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, fines_auto, driver_routes as dr, finance_routes as fr

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

DUBAI = timezone(timedelta(hours=4))
OWN, OPS, TOLD = [], [], []
fake_owner = types.ModuleType("owner_routes")
async def notify_owners(key, text, **k): OWN.append((key, text, k.get("reply_markup")))
fake_owner.notify_owners = notify_owners
fake_op = types.ModuleType("op_route")
async def op_send(text, district="", **k): OPS.append((district, text))
fake_op.send = op_send
sys.modules["owner_routes"] = fake_owner; sys.modules["op_route"] = fake_op
async def tell_safe(name, text, parse_mode="HTML"): TOLD.append((name, text)); return 1
fr._pn.tell_safe = tell_safe
async def _touch(*a, **k): pass
fr._touch = _touch
BUILDS = []
async def build(month, *a, **k): BUILDS.append(month); return {"month": month}
fr.build = build
fr._biz_day = lambda *a, **k: "2026-09-22"


async def decide(body):
    r = make_mocked_request("POST", "/api/owner/finance/fines/decide")
    r._read_bytes = json.dumps(body).encode(); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = fr.handle_fine_decide
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_fines"]; d = db._db

    print("── поздняя смена → штраф на решение ────────────────────────────")
    at = datetime(2026, 9, 22, 16, 20, tzinfo=DUBAI)
    await dr._late_alert({"name": "Худоба", "district": "jvc"}, at, "2026-09-22")
    p = await d.fine_pending.find_one({})
    eq("записан: кто, день, за что, подробности, ждёт", (p["_id"], p["name"], p["day"], p["reason"], p["note"], p["status"]),
       ("late_shift:2026-09-22:Худоба", "Худоба", "2026-09-22", "Поздно открыл смену",
        "открыл в 16:20, правило — до 15:00", "pending"))
    eq("сумма первый раз пустая — впишут при назначении", p["amount"], None)
    eq("старшему — строка о штрафе и кнопка в STAR на «Штрафы»",
       ("Штраф ждёт решения" in OWN[0][1], OWN[0][2]["inline_keyboard"][0][0]["text"],
        OWN[0][2]["inline_keyboard"][0][0]["web_app"]["url"]),
       (True, "Решить по штрафу", "https://star.example/?go=fines"))
    eq("операторам района — как раньше, без штрафа", (len(OPS), "штраф" in OPS[0][1].lower()), (1, False))
    OWN.clear()
    await dr._late_alert({"name": "Худоба", "district": "jvc"}, at + timedelta(minutes=40), "2026-09-22")
    eq("тот же день ещё раз — второй записи нет", await d.fine_pending.count_documents({}), 1)
    eq("и кнопки в повторном сообщении нет", (OWN[0][2], "Штраф ждёт" in OWN[0][1]), (None, False))
    await dr._late_alert({"name": "Али", "district": "bbay"}, at, "2026-09-22")
    eq("ждут решения двое", sorted(x["name"] for x in await fines_auto.pending()), ["Али", "Худоба"])

    print("── решения ─────────────────────────────────────────────────────")
    st, b = await decide({"id": "late_shift:2026-09-22:Худоба", "decision": "assign", "amount": 0, "month": "2026-09", "as": "Макар"})
    eq("назначить без суммы — нельзя", (st, b.get("error")), (400, "bad_amount"))
    st, b = await decide({"id": "late_shift:2026-09-22:Худоба", "decision": "assign", "amount": 100, "month": "2026-09", "as": "Макар"})
    eq("назначил 100", (st, b.get("ok"), b.get("book")), (200, True, {"month": "2026-09"}))
    it = await d.fin_pay_items.find_one({})
    eq("в зарплатах обычный штраф: сумма, день нарушения, с этого месяца, разом, за что",
       (it["kind"], it["amount"], it["day"], it["from"], it["per_month"], it["reason"], it["note"], it["by"], it["auto"]),
       ("fine", 100, "2026-09-22", "2026-09", 0, "Поздно открыл смену", "открыл в 16:20, правило — до 15:00", "Макар", "late_shift"))
    eq("водителю — сообщение о штрафе", (TOLD[0][0], "Штраф 100 AED" in TOLD[0][1], "Поздно открыл смену" in TOLD[0][1]),
       ("Худоба", True, True))
    p = await d.fine_pending.find_one({"_id": "late_shift:2026-09-22:Худоба"})
    eq("запись решена: назначен, кем, id штрафа", (p["status"], p["decided_by"], p["item"] == it["_id"], p["amount"]),
       ("assigned", "Макар", True, 100))
    st, b = await decide({"id": "late_shift:2026-09-22:Худоба", "decision": "skip", "month": "2026-09", "as": "Слон"})
    eq("второй раз решить нельзя — 409 и свежая книга", (st, b.get("error"), b.get("book")), (409, "decided", {"month": "2026-09"}))
    TOLD.clear()
    st, b = await decide({"id": "late_shift:2026-09-22:Али", "decision": "skip", "month": "2026-09", "as": "Слон"})
    eq("не назначил", (st, b.get("ok")), (200, True))
    eq("в зарплатах ничего нового, водителю ничего", (await d.fin_pay_items.count_documents({}), TOLD), (1, []))
    eq("ждут решения — никто", await fines_auto.pending(), [])

    print("── сумма в следующий раз ───────────────────────────────────────")
    await dr._late_alert({"name": "Худоба", "district": "jvc"}, at + timedelta(days=1), "2026-09-23")
    eq("предложена сумма прошлого назначения — 100",
       [(x["day"], x["amount"]) for x in await fines_auto.pending()], [("2026-09-23", 100)])

    print("── история штрафов ─────────────────────────────────────────────")
    items = [dict(x) for x in await d.fin_pay_items.find({}).to_list(10)]
    h = fr._penalty_history(items, "2026-09", declined=await fines_auto.declined())
    eq("в истории оба: не назначенный — с исходом, назначенный — штрафом",
       sorted((x["name"], x["declined"], x["auto"], x["amount"]) for x in h),
       [("Али", True, "late_shift", 0), ("Худоба", False, "late_shift", 100)])
    skip = next(x for x in h if x["declined"])
    eq("у «Не назначен» — за что, подробности и кто решил",
       (skip["id"], skip["reason"], skip["note"], skip["by"], skip["day"]),
       ("auto:late_shift:2026-09-22:Али", "Поздно открыл смену", "открыл в 16:20, правило — до 15:00", "Слон", "2026-09-22"))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
