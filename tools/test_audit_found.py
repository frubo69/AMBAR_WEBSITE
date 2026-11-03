"""Наклейка на ревизии (владелец, 4 ноя 2026: «те бутылки, на которые я
доклеивал QR, — это те, которых, по программе, не хватало; сделай так, чтобы
они компенсировали эти недостачи»).

Ревизия B2 2 ноя 2026: на полке бутылки без читаемых кодов, камера видела
их «не в реестре», и ревизия записала Absolut, Red Label, Jager недостачей.
Владелец наклеил новые коды и внёс их кнопкой «внести новый товар» через
минуту после «Завершить» — приложение считало это приходом, а недостача так
и висела (1 500 AED вместо настоящих 800).

Теперь код, внесённый руками к бутылке, которую камера уже видела чужой, —
это та самая бутылка: она в счёте ревизии (недостачу закрывает, лишняя —
излишек), а приходом не считается (found). Ревизия идёт — лист показывает
сам; завершена и ждёт решения — результат, недостача, излишек и снимок
склада поправляются; решение принято — код остаётся обычным приходом.

mongomock + настоящие qr_routes.handle_scan / handle_undo, stock_routes
audit_start / audit_scan / audit_finish / audit_sheet / audit_cancel.

    python3 tools/test_audit_found.py
"""
import asyncio, inspect, json, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                # noqa: E402
import db, stock_routes as SR, qr_routes as QR                  # noqa: E402
import owner_routes as _orr, backdate                           # noqa: E402

async def _quiet(*a, **k): return []
_orr.notify_owners = _quiet
backdate.notify = _quiet

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

U = timezone.utc
ДЕНЬ = "2026-11-02"
def T(d, h, m=0): return datetime(2026, 11, d, h, m, tzinfo=U)


class Req(dict):
    method = "POST"
    def __init__(self, body, **kw): super().__init__(**kw); self._b = body
    async def json(self): return self._b


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def внести(code, pid, district):
    """«Внести новый товар» — настоящая ручка."""
    r = await raw(QR.handle_scan)(Req({"code": code, "product_id": pid, "district": district,
                                       "as": "STAR"}, owner_id=1))
    return json.loads(r.text)


async def отменить(code):
    r = await raw(QR.handle_undo)(Req({"code": code}, owner_id=1))
    return json.loads(r.text)


async def код(code, pid, district, at, src="intake"):
    await db._db.qr_codes.insert_one({"_id": code, "status": "active", "product_id": pid,
                                      "product_name": pid, "district": district, "origin": district,
                                      "at": at, "src": src, "qty": 1, "label": f"{pid}#{code}"})


async def пересчёт(district, day, qty: dict, at):
    cat = SR._catalog()
    await db.save_stock_count(district, day, {
        "district": district, "day": day, "counted_at": at.isoformat(),
        "first_time": False, "counted_by": 0,
        "lines": [{"id": pid, "name": cat[pid]["name"], "price": SR._price(cat[pid]),
                   "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                  for pid, q in qty.items()]})


async def скан_док(district, code):
    d = await db._db.audit_scans.find_one({"_id": db._audit_key(district, ДЕНЬ, code)})
    return (d.get("verdict"), d.get("product_id"), d.get("qty")) if d else None


async def found_of(code):
    d = await db.qr_get(code) or {}
    f = d.get("found")
    return (f.get("district"), f.get("day")) if f else None


async def лист(district):
    SR.base_drop()
    sheet = await SR.audit_sheet(district, ДЕНЬ)
    return {l["id"]: (l["expected"], l["actual"], l["diff"]) for l in sheet["rows"]}


async def живой(district, pid):
    SR.base_drop()
    return (await SR._district_base(ДЕНЬ))[district]["have_exact"].get(pid, 0)


def строка(a, pid):
    l = next((l for l in a.get("lines") or [] if l["id"] == pid), None)
    return (l["actual"], l["diff"]) if l else None


def недостача(a):
    s = a.get("short")
    return {l["id"]: l["qty"] for l in s["lines"]} if s else None


def излишек(a):
    o = a.get("over")
    return {l["id"]: l["qty"] for l in o["lines"]} if o else None


async def снимок(district, pid):
    c = await db.get_stock_count(district, ДЕНЬ) or {}
    l = next((l for l in c.get("lines") or [] if l["id"] == pid), None)
    return (l["actual"], l["diff"], l["mark"]) if l else None


async def main():
    db._db = AsyncMongoMockClient()["ambar_audit_found"]
    SR._biz_day = lambda *a, **k: ДЕНЬ
    async def _none(*a, **k): return {}, {}
    QR.unscanned_by_district = _none
    now = datetime.now(U)

    # ══ 1. Завершённая ревизия, ждёт решения — как B2 2 ноя ══════════════
    B = "bbay"
    # p1 Absolut 3 бутылки: коды c1–c3, у третьей код не читается;
    # p80 Jager 2: j1, j2; p13 Chivas 2: k1, k2 — и третья без кода вовсе.
    for c, pid in (("c1", "p1"), ("c2", "p1"), ("c3", "p1"), ("j1", "p80"), ("j2", "p80"),
                   ("k1", "p13"), ("k2", "p13")):
        await код(c, pid, B, T(1, 5))
    await пересчёт(B, "2026-11-01", {"p1": 3, "p80": 2, "p13": 2}, T(1, 6))
    code, _ = await SR.audit_start(B, ДЕНЬ, 1, "STAR")
    eq("ревизия B2 начата", code, 200)
    for c in ("c1", "c2", "j1", "k1", "k2"):
        await SR.audit_scan(B, ДЕНЬ, c, 1, "STAR")
    for c in ("n1", "n2", "n3"):                      # новые наклейки — реестр их не знает
        r = await SR.audit_scan(B, ДЕНЬ, c, 1, "STAR")
        eq(f"скан {c} — не в реестре", r["verdict"], "alien")
    code, rep = await SR.audit_finish(B, ДЕНЬ, 1, "STAR")
    eq("завершена", code, 200)
    a = await db.audit_get(B, ДЕНЬ)
    eq("недостача: Absolut 1, Jager 1", недостача(a), {"p1": 1, "p80": 1})
    eq("недостача 300 AED по прайсу", (a["result"]["short_qty"], a["result"]["short_aed"]), (2, 300))
    eq("излишка нет", излишек(a), None)
    eq("не в реестре 3", a["alien"], 3)
    eq("живой склад Absolut до внесения", await живой(B, "p1"), 2)

    print("── владелец вносит наклейки через минуту после «Завершить» ──")
    r = await внести("n1", "p1", B)
    eq("n1 внесён и засчитан в ревизии", (r.get("ok"), r.get("found")), (True, {"day": ДЕНЬ, "state": "pending"}))
    r = await внести("n2", "p80", B)
    eq("n2 засчитан", r.get("found"), {"day": ДЕНЬ, "state": "pending"})
    r = await внести("n3", "p13", B)
    eq("n3 засчитан", r.get("found"), {"day": ДЕНЬ, "state": "pending"})
    a = await db.audit_get(B, ДЕНЬ)
    eq("недостачи больше нет", недостача(a), None)
    eq("Chivas — излишек 1", излишек(a), {"p13": 1})
    eq("итог: нет недостачи, излишек 1", (a["result"]["short_qty"], a["result"]["short_aed"], a["result"]["over_qty"]), (0, 0, 1))
    eq("не в реестре 0", a["alien"], 0)
    eq("строка Absolut: факт 3, разница 0", строка(a, "p1"), (3, 0))
    eq("строка Jager: факт 2, разница 0", строка(a, "p80"), (2, 0))
    eq("строка Chivas: факт 3, разница −1", строка(a, "p13"), (3, -1))
    eq("ревизия не закрыта — излишек ждёт решения", bool(a.get("closed_at")), False)
    eq("сканы стали бутылками", (await скан_док(B, "n1"), await скан_док(B, "n3")), (("ok", "p1", 1.0), ("ok", "p13", 1.0)))
    eq("коды помечены найденными на ревизии", (await found_of("n1"), await found_of("n2"), await found_of("n3")),
       ((B, ДЕНЬ), (B, ДЕНЬ), (B, ДЕНЬ)))
    eq("снимок склада: Absolut 3 сошлось", await снимок(B, "p1"), (3, 0, "ok"))
    eq("снимок склада: Chivas 3, лишняя", await снимок(B, "p13"), (3, -1, "diff"))
    cnt = await db.get_stock_count(B, ДЕНЬ)
    eq("итоги снимка", (cnt["short_qty"], cnt["short_aed"], cnt["over_qty"], cnt["matched_qty"], cnt["mismatch_qty"]), (0, 0, 1, 2, 1))
    eq("живой склад не изменился: Absolut 3, Jager 2, Chivas 3",
       (await живой(B, "p1"), await живой(B, "p80"), await живой(B, "p13")), (3, 2, 3))
    ev = await db.qr_manual_events(B, T(1, 6))
    eq("приходом наклейки не считаются", ev, {})
    ev = await db.qr_new_since(B, "p1", T(1, 6))
    eq("qr_new_since их не видит", ev, [])

    print("── лента движений: «наклейка на ревизии», не приход ──")
    fl = await SR._flow_events(ДЕНЬ, "p1", now - timedelta(hours=1), now + timedelta(hours=1), SR._catalog())
    kinds = [(e["kind"], e.get("ref")) for e in fl.get(B) or [] if e["kind"] != "count"]
    eq("событие found с меткой, прихода нет", kinds, [("found", "abs#000001")])

    print("── отмена внесения: бутылка снова чужая, ревизия узнаёт ──")
    r = await отменить("n3")
    eq("отмена принята", r.get("ok"), True)
    a = await db.audit_get(B, ДЕНЬ)
    eq("скан n3 снова чужой", await скан_док(B, "n3"), ("alien", "", 1.0))
    eq("излишка нет", излишек(a), None)
    eq("не в реестре 1", a["alien"], 1)
    eq("строка Chivas: факт 2, разница 0", строка(a, "p13"), (2, 0))
    eq("снимок Chivas 2", await снимок(B, "p13"), (2, 0, "ok"))
    eq("живой Chivas 2", await живой(B, "p13"), 2)
    r = await внести("n3", "p13", B)
    eq("внесли снова — снова засчитан", r.get("found"), {"day": ДЕНЬ, "state": "pending"})
    a = await db.audit_get(B, ДЕНЬ)
    eq("Chivas снова излишек 1", (излишек(a), a["alien"]), ({"p13": 1}, 0))

    # ══ 2. Решение уже принято — код остаётся обычным приходом ═════════════
    J = "jvc"
    await код("c9", "p1", J, T(1, 5)); await код("c10", "p1", J, T(1, 5))
    await пересчёт(J, "2026-11-01", {"p1": 2}, T(1, 6))
    await SR.audit_start(J, ДЕНЬ, 1, "STAR")
    await SR.audit_scan(J, ДЕНЬ, "c9", 1, "STAR")
    await SR.audit_scan(J, ДЕНЬ, "n4", 1, "STAR")
    await SR.audit_finish(J, ДЕНЬ, 1, "STAR")
    a = await db.audit_get(J, ДЕНЬ)
    eq("JVC: недостача 1, не в реестре 1", (недостача(a), a["alien"]), ({"p1": 1}, 1))
    await db.audit_set(J, ДЕНЬ, {"short": {**a["short"], "resolved_at": now.isoformat()}})
    r = await внести("n4", "p1", J)
    eq("после решения — не засчитывается", (r.get("ok"), r.get("found")), (True, None))
    a = await db.audit_get(J, ДЕНЬ)
    eq("недостача как была", недостача(a), {"p1": 1})
    eq("метки нет", await found_of("n4"), None)
    eq("это обычный приход", list((await db.qr_manual_events(J, T(1, 6))).keys()), ["p1"])
    eq("живой JVC Absolut 2 (1 + приход)", await живой(J, "p1"), 2)

    # ══ 3. Ревизия идёт ═══════════════════════════════════════════════════
    Tc = "tecom"
    await код("c20", "p1", Tc, T(1, 5)); await код("c21", "p1", Tc, T(1, 5))
    await пересчёт(Tc, "2026-11-01", {"p1": 2}, T(1, 6))
    await SR.audit_start(Tc, ДЕНЬ, 1, "STAR")
    await SR.audit_scan(Tc, ДЕНЬ, "c20", 1, "STAR")
    await SR.audit_scan(Tc, ДЕНЬ, "n5", 1, "STAR")
    eq("лист: Absolut 1 из 2", (await лист(Tc))["p1"], (2, 1, 1))
    r = await внести("n5", "p1", Tc)
    eq("внесли — засчитан в идущей", r.get("found"), {"day": ДЕНЬ, "state": "running"})
    eq("лист: 2 из 2, ожидание не выросло", (await лист(Tc))["p1"], (2, 2, 0))
    eq("скан n5 — бутылка p1", await скан_док(Tc, "n5"), ("ok", "p1", 1.0))

    print("── сначала внесли, потом отсканировали ──")
    r = await внести("n6", "p1", Tc)
    eq("внесён, ревизия его ещё не видела", r.get("found"), None)
    # приход после первого скана позиции лист откатывает сам (правило 23 сен)
    eq("лист: приход после первого скана откачен", (await лист(Tc))["p1"], (2, 2, 0))
    r = await SR.audit_scan(Tc, ДЕНЬ, "n6", 1, "STAR")
    eq("скан принят как бутылка", r["verdict"], "ok")
    eq("помечен наклейкой на ревизии", await found_of("n6"), (Tc, ДЕНЬ))
    eq("лист: 3 из 2 — лишняя, ожидание 2", (await лист(Tc))["p1"], (2, 3, -1))
    await SR.audit_scan_undo(Tc, ДЕНЬ, "n6")
    eq("скан снят — снова приход", await found_of("n6"), None)
    eq("лист: как до скана", (await лист(Tc))["p1"], (2, 2, 0))
    await SR.audit_scan(Tc, ДЕНЬ, "n6", 1, "STAR")
    eq("отсканировали снова — снова наклейка", await found_of("n6"), (Tc, ДЕНЬ))

    print("── отмена захода освобождает наклейки ──")
    code, _ = await SR.audit_cancel(Tc, ДЕНЬ, 1, "STAR")
    eq("заход отменён", code, 200)
    eq("метки сняты", (await found_of("n5"), await found_of("n6")), (None, None))
    eq("оба — приход", (await db.qr_manual_events(Tc, T(1, 6))).get("p1", {}).get("qty") if isinstance((await db.qr_manual_events(Tc, T(1, 6))).get("p1"), dict) else sorted((await db.qr_manual_events(Tc, T(1, 6))).keys()), ["p1"])

    print("── наклеили и внесли ДО первого скана позиции, потом считали ──")
    S = "silicon"
    # 2 бутылки: c30 читается, c31 — нет; на вторую клеят n7 и вносят до прохода
    await код("c30", "p1", S, T(1, 5)); await код("c31", "p1", S, T(1, 5))
    await пересчёт(S, "2026-11-01", {"p1": 2}, T(1, 6))
    await SR.audit_start(S, ДЕНЬ, 1, "STAR")
    await внести("n7", "p1", S)
    eq("до прохода: приход поднял ожидание до 3", (await лист(S))["p1"], (3, 0, 3))
    await SR.audit_scan(S, ДЕНЬ, "c30", 1, "STAR")
    await SR.audit_scan(S, ДЕНЬ, "n7", 1, "STAR")
    eq("n7 — наклейка на ревизии", await found_of("n7"), (S, ДЕНЬ))
    eq("лист: 2 из 2 — недостачи нет, прихода нет", (await лист(S))["p1"], (2, 2, 0))
    print("── сброс прохода освобождает наклейки ──")
    r = await raw(SR.handle_audit_scan_reset)(Req({"district": S, "day": ДЕНЬ}, owner_id=1))
    eq("проход сброшен", json.loads(r.text).get("ok"), True)
    eq("n7 снова приход", await found_of("n7"), None)
    eq("лист: ожидание снова 3", (await лист(S))["p1"], (3, 0, 3))

    # ══ 4. Арифметика поправки с зачётом ушедшего, пока считали ════════════
    print("── поправка завершённой: зачёт ушедшего ──")
    D2 = "2026-10-20"
    # числится 10, камера 8, пока считали ушла 1 → зачёт 1, недостача 1
    a = await db.audit_set("alguses", D2, {
        "started_at": T(1, 1).isoformat(), "finished_at": T(1, 2).isoformat(),
        "lines": [{"id": "p1", "expected": 10, "coded": 10, "actual": 8, "diff": 1, "noqr": 0, "loss": 0, "went": 1}],
        "short": {"qty": 1, "aed": 100, "lines": [{"id": "p1", "qty": 1}]}, "over": None, "alien": 0, "result": {}})
    a = await SR._audit_adjust("alguses", D2, a, "p1", 1, "тест")
    eq("+1: факт 9, зачёт закрыл остальное", строка(a, "p1"), (9, 0))
    eq("сошлось — закрылась сама", bool(a.get("closed_at")), True)
    a = await SR._audit_adjust("alguses", D2, a, "p1", 1, "тест")
    eq("+1: факт 10, разница 0", строка(a, "p1"), (10, 0))
    a = await SR._audit_adjust("alguses", D2, a, "p1", 1, "тест")
    eq("+1: факт 11, лишняя", (строка(a, "p1"), излишек(a)), ((11, -1), {"p1": 1}))
    eq("излишек — открылась снова", bool(a.get("closed_at")), False)
    for ждём in ((10, 0), (9, 0), (8, 1)):
        a = await SR._audit_adjust("alguses", D2, a, "p1", -1, "тест")
    eq("−3: как было — факт 8, недостача 1", (строка(a, "p1"), недостача(a)), ((8, 1), {"p1": 1}))
    eq("недостача — не закрыта", bool(a.get("closed_at")), False)
    # старая ревизия без went: зачёт покрыл всю разницу — ушедшего хватит и дальше
    a = await db.audit_set("alguses", "2026-10-21", {
        "started_at": T(1, 1).isoformat(), "finished_at": T(1, 2).isoformat(),
        "lines": [{"id": "p1", "expected": 10, "coded": 10, "actual": 8, "diff": 0, "noqr": 0, "loss": 0}],
        "short": None, "over": None, "alien": 0, "result": {}})
    a = await SR._audit_adjust("alguses", "2026-10-21", a, "p1", 1, "тест")
    eq("старая, зачёт полный: +1 → 9, разница 0", строка(a, "p1"), (9, 0))
    eq("сошлось — закрылась", bool(a.get("closed_at")), True)
    a = await SR._audit_adjust("alguses", "2026-10-21", a, "p1", 2, "тест")
    eq("+2 → 11, лишняя", строка(a, "p1"), (11, -1))
    eq("излишек ждёт решения — открыта", bool(a.get("closed_at")), False)
    # старая, зачёт меньше разницы: ушедшее известно точно
    a = await db.audit_set("alguses", "2026-10-22", {
        "started_at": T(1, 1).isoformat(), "finished_at": T(1, 2).isoformat(),
        "lines": [{"id": "p1", "expected": 10, "coded": 10, "actual": 7, "diff": 2, "noqr": 0, "loss": 0}],
        "short": {"qty": 2, "aed": 200, "lines": [{"id": "p1", "qty": 2}]}, "over": None, "alien": 0, "result": {}})
    a = await SR._audit_adjust("alguses", "2026-10-22", a, "p1", 2, "тест")
    eq("старая, зачёт 1 из 3: +2 → 9, зачёт 1, разница 0", строка(a, "p1"), (9, 0))
    eq("недостачи нет — закрыта", (недостача(a), bool(a.get("closed_at"))), (None, True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"УПАЛО {len(FAIL)}: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
