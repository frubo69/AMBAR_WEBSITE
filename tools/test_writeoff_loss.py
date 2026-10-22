"""Решение по списанию и склад (владелец, 22 окт 2026: «у потери два варианта —
согласовать со списанием или без списания, там нечего отклонять; и убедись,
что при любом списании оно правильно считается на складе»).

mongomock + настоящая ручка решения. Проверяется:
  • утеря: отклонить нельзя (400 no_reject); «Списать» — со склада ушла, виновного
    нет; «Удержать» — со склада ушла, удержание по прайсу стоит;
  • брак сканом: пока ждёт — код активен, в остатке не вычтен; отклонили — код
    активен, не вычтен (вылезет недостачей); согласовали — код «written», в
    остатке от пересчёта вычтен один раз, от реестра — не вычитается ещё раз;
  • утеря без кода вычитается и от пересчёта, и от реестра; недостача ревизии
    от пересчёта не вычитается (уже в нём), от реестра — вычитается.

    python3 tools/test_writeoff_loss.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, stock_routes as sr                                     # noqa: E402

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

CAT = {"abs": {"id": "abs", "name": "Absolut 1 ltr", "price": 95}}
sr._catalog = lambda: CAT
sr._LOSS_CACHE = {"abs": 62}
async def _nop(*a, **k): return None
sr._writeoff_after = _nop
sr._loss_load = _nop
sr._writeoff_comp_tell = _nop
SINCE = datetime.now(timezone.utc) - timedelta(hours=3)
NOW = datetime.now(timezone.utc)

async def decide(wid, body):
    h = sr.handle_writeoff_decide
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    rq = make_mocked_request("POST", "/x", match_info={"wid": wid}); rq["owner_id"] = 1
    rq._read_bytes = json.dumps({**body, "as": "Владелец"}).encode()
    resp = await h(rq)
    return resp.status, json.loads(resp.text)

async def add(kind, code="", src=""):
    return await db.writeoff_add({"item": "abs", "name": "Absolut 1 ltr", "qty": 1, "kind": kind, "district": "jvc",
                                  "district_code": "B1", "by": "Водитель А", "by_id": 7, "at": NOW, "note": "",
                                  **({"code": code} if code else {}), **({"src": src} if src else {})})

async def counted(skip_coded=False, skip_audit=True):
    return ((await db.writeoff_since({"jvc": SINCE}, skip_coded=skip_coded, skip_audit=skip_audit)).get("jvc") or {}).get("abs", 0)

async def main():
    db._db = AsyncMongoMockClient()["wo_loss"]

    print("── утеря: два исхода, отклонить нельзя ───────────────────────")
    w = await add("потеря")
    eq("ждёт: со склада не вычтена", await counted(), 0)
    st, r = await decide(w, {"ok": False})
    eq("отклонить утерю — отказ", (st, r.get("error")), (400, "no_reject"))
    eq("и запись так и ждёт", (await db.writeoff_get(w))["state"], "pending")
    st, r = await decide(w, {"ok": True, "blame": False})
    eq("«Списать» на компанию — согласовано без удержания", (st, (await db.writeoff_get(w))["state"], (await db.writeoff_get(w)).get("comp")), (200, "ok", None))
    eq("со склада ушла: от пересчёта", await counted(), 1)
    eq("и от реестра тоже (кода нет)", await counted(skip_coded=True), 1)
    w2 = await add("потеря")
    st, r = await decide(w2, {"ok": True, "blame": True, "who": "Водитель А"})
    c = (await db.writeoff_get(w2)).get("comp") or {}
    eq("«Удержать с виновного» — согласовано, удержание по прайсу", (st, c.get("who"), c.get("amount")), (200, "Водитель А", 95))
    eq("со склада ушла и она", await counted(), 2)
    eq("второе решение по решённому — 409", (await decide(w2, {"ok": True}))[0], 409)

    print("── брак сканом: ждёт / отклонили / согласовали ─────────────────")
    for code in ("c1", "c2"):
        await db._db.qr_codes.insert_one({"_id": code, "status": "active", "product_id": "abs", "office_id": "jvc"})
    b1 = await add("брак", code="c1")
    eq("ждёт: код активен, в остатке не вычтен", ((await db._db.qr_codes.find_one({"_id": "c1"}))["status"], await counted()), ("active", 2))
    st, r = await decide(b1, {"ok": False})
    eq("отклонили: код активен, не вычтен — вылезет недостачей", (st, (await db._db.qr_codes.find_one({"_id": "c1"}))["status"], await counted()), (200, "active", 2))
    b2 = await add("брак", code="c2")
    st, r = await decide(b2, {"ok": True})
    eq("согласовали: код written", (st, (await db._db.qr_codes.find_one({"_id": "c2"}))["status"]), (200, "written"))
    eq("от пересчёта вычтен один раз (утери 2 + брак 1)", await counted(), 3)
    eq("от реестра кодовый брак не вычитается второй раз", await counted(skip_coded=True), 2)

    print("── недостача ревизии ──────────────────────────────────────────")
    a = await add("недостача", src="audit")
    await db._db.writeoffs.update_one({"_id": a}, {"$set": {"state": "ok"}})
    eq("от пересчёта не вычитается — она уже в нём", await counted(), 3)
    eq("от реестра вычитается — коды пропавших активны", await counted(skip_coded=True, skip_audit=False), 3)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
