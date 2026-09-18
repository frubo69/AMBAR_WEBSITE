"""Повторное внесение убранной бутылки (владелец, 19 сен 2026: «эти бутылки
перепутали на приёмке, поэтому их сразу после приёмки удалили и завели через
„внести“, но уже под правильными наименованиями» — и склад показал по каждой
на одну меньше; «исправь и сделай, чтобы не повторялось»).

Сценарий 18 сен целиком, через настоящие ручки STAR (aiohttp-сервер с
qr_routes) и настоящую приёмку водителя (supply_routes.task_scan):
  • на приёмке Rose и Prosecco записали наоборот; STAR убрал оба кода и внёс
    заново строкой «QR код не внесён» (cover) под верными названиями — склад
    обязан остаться 3 и 3 (было 2 и 2), приход — с поставки, с тем же
    временем и водителем;
  • то же кнопкой «Внести новый товар» — ручного прихода не появляется;
  • вернуть бутылку с поставки на другой район нельзя (перемещением);
  • отмена скана («✕», «Выйти») не стирает запись с приходом, а возвращает её
    в убранные с прежним названием; тронутую с тех пор — не трогает;
  • новая запись отменяется как раньше — стиранием;
  • переименовали бутылку, которая уже переезжала, — переезд везёт новую
    позицию (и отмена возвращает старую);
  • пиво ↔ бутылка: код несёт qty новой позиции, вторая половина коробки
    (intake_extra) у бутылки снимается;
  • внесённые руками (new) и кодом к посчитанной (cover) — как прежде;
  • два возврата разом — проходит один;
  • история внесений: «внесена заново» строкой и не в счёт внесённых;
    история бутылки — «внесена заново · была …»; ответы сериализуются."""
import asyncio, json, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, qr_routes as QR, supply_routes as sr, owner_auth

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-18"
T0 = datetime.now(timezone.utc) - timedelta(hours=6)          # пересчёт утром
SR._biz_day = lambda *a, **k: D
owner_auth.install_validator(lambda s: {"id": int(s)} if s.isdigit() else None)
async def _quiet(*a, **k): return None
sr._notify_done = _quiet
QR._drop_send = _quiet
ROSE, PROS, RAKI, CHAT, ABS, STOL, BEER = "p92", "p91", "p84", "p111", "p1", "p2", "p31"


async def have(oid, pid):
    SR.base_drop()
    return (await SR._district_base(D))[oid]["have_exact"].get(pid, 0)


async def debt(oid, pid):
    d = {}
    await QR.unscanned_by_district(None, d)
    return (d.get(oid) or {}).get(pid, 0)


def supply(sid, oid, lines, driver="Азиз", driver_id=201):
    return {"_id": sid, "status": "open", "at": T0 + timedelta(hours=1), "day": D,
            "items": [{"id": pid, "name": name, "qty": n, "by_district": {oid: n}, "got": {oid: 0}}
                      for pid, name, n in lines],
            "tasks": {oid: {"driver": driver, "driver_id": driver_id, "claimed_at": T0, "started_at": None,
                            "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                            "scanned": 0, "qty": sum(n for _, _, n in lines), "positions": len(lines)}}}


async def main():
    db._db = AsyncMongoMockClient()["ambar_readd"]; d = db._db
    for oid, per in {"silicon": {ROSE: 2, PROS: 2, STOL: 0}, "jvc": {RAKI: 1, CHAT: 1, ABS: 4, STOL: 0, BEER: 2},
                     "alguses": {ROSE: 0}}.items():
        await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
            "first_time": True, "counted_by": 0,
            "lines": [{"id": p, "name": p, "price": 100, "unit": SR._unit(SR._catalog()[p]), "actual": q,
                       "counted": True} for p, q in per.items()]})

    app = web.Application(); QR.setup(app)
    async with TestClient(TestServer(app)) as cl:
        async def own(method, path, body=None):
            r = await cl.request(method, path, json=body, headers={"Authorization": "tma 1"})
            return r.status, (await r.json() if r.content_type == "application/json" else await r.text())
        async def scan(code, pid, oid, mode):
            return await own("POST", "/api/owner/qr/scan", {"code": code, "product_id": pid, "district": oid, "mode": mode})
        async def drop(code):
            return await own("POST", "/api/owner/qr/drop", {"code": code, "as": "STAR"})
        async def undo(code):
            return await own("POST", "/api/owner/qr/undo", {"code": code})

        print("── приёмка: Rose и Prosecco записаны наоборот ─────────────────")
        # По позиции на поставку: позиционное items.$ у mongomock с двумя
        # строками пишет не в ту (так же и в test_stock_e2e).
        await d.supplies.insert_one(supply("S1", "silicon", [(ROSE, "Bottega Rose 0.75", 1)]))
        await d.supplies.insert_one(supply("S1P", "silicon", [(PROS, "Bottega Prosecco 0.75", 1)]))
        r1 = await sr.task_scan("S1P", "silicon", PROS, "X-ROSE", "Азиз", 201, "2026-09-18T13:11:13.808Z", False)
        r2 = await sr.task_scan("S1", "silicon", ROSE, "X-PROS", "Азиз", 201, "2026-09-18T13:12:07.986Z", False)
        f1 = await sr.task_finish("S1", "silicon", "Азиз")
        f2 = await sr.task_finish("S1P", "silicon", "Азиз")
        eq("приняли обе, задачи закрыты без недобора", (r1["verdict"], r2["verdict"], f1["gaps"], f2["gaps"]),
           ("taken", "taken", [], []))
        eq("склад Силикон: Rose 2+1, Prosecco 2+1", (await have("silicon", ROSE), await have("silicon", PROS)), (3, 3))
        arrived = {c: (await db.qr_get(c)) for c in ("X-ROSE", "X-PROS")}

        print("── STAR убрал оба и внёс заново строкой «QR код не внесён» ────")
        # Две бутылки каждой позиции из пересчёта кодов не имеют: долг без QR
        # до удаления — 2, после — 3 (убранный код), после возврата — снова 2.
        d0 = (await debt("silicon", ROSE), await debt("silicon", PROS))
        eq("долг без QR до удаления: 2 и 2", d0, (2, 2))
        eq("убрать из реестра", [(await drop(c))[1].get("ok") for c in ("X-ROSE", "X-PROS")], [True, True])
        eq("убранные — всё ещё приход: склад 3 и 3, долг без QR 3 и 3",
           (await have("silicon", ROSE), await have("silicon", PROS), await debt("silicon", ROSE), await debt("silicon", PROS)),
           (3, 3, 3, 3))
        st, a = await scan("X-ROSE", ROSE, "silicon", "cover")
        st2, b = await scan("X-PROS", PROS, "silicon", "cover")
        eq("внесены заново — «записана»", (st, a.get("new"), st2, b.get("new")), (200, True, 200, True))
        eq("СКЛАД НЕ ПОТЕРЯЛ: Rose 3, Prosecco 3 (с ошибкой было бы 2 и 2)",
           (await have("silicon", ROSE), await have("silicon", PROS)), (3, 3))
        eq("долг без QR — как до удаления", (await debt("silicon", ROSE), await debt("silicon", PROS)), d0)
        x = await db.qr_get("X-ROSE")
        eq("код: верное название, приход с поставки, время и водитель приёмки",
           (x["product_id"], x["src"], x["at"] == arrived["X-ROSE"]["at"], x["by"], x["driver"], x["supply_id"], x["origin"]),
           (ROSE, "intake", True, 201, "Азиз", "S1P", "silicon"))
        eq("отметка возврата: кто, когда, было", (x["re_by"], bool(x["re_at"]), x["re_from"], x["re_src"]),
           (1, True, "Bottega Prosecco 0.75", "cover"))
        eq("приход по позициям: по одной, уже верными названиями", await db.intake_since("silicon", T0),
           {ROSE: 1.0, PROS: 1.0})
        eq("в «внесённые руками» после поставки не попали", await db.qr_marked_since(T0, ["silicon"]), {})
        eq("строки поставок: по одной (поставка знает, сколько пришло, не какой код)",
           [(i["id"], i["got"]["silicon"]) for sid in ("S1", "S1P") for i in (await db.supply_get(sid))["items"]],
           [(ROSE, 1), (PROS, 1)])

        print("── то же кнопкой «Внести новый товар» ─────────────────────────")
        await drop("X-ROSE")
        st, a = await scan("X-ROSE", ROSE, "silicon", "new")
        x = await db.qr_get("X-ROSE")
        eq("приход не задвоен и не пропал: склад 3, src intake", (a.get("new"), await have("silicon", ROSE), x["src"]), (True, 3, "intake"))
        eq("ручного прихода нет", await db.qr_manual_events("silicon", T0), {})

        print("── на другой район — нельзя ───────────────────────────────────")
        await drop("X-ROSE")
        st, a = await scan("X-ROSE", ROSE, "alguses", "new")
        eq("отказ: числится на B3", (st, a.get("ok"), a.get("error"), a.get("district"), a.get("district_code")),
           (200, False, "elsewhere", "silicon", "B3"))
        eq("запись осталась убранной, Алгусес не вырос", ((await db.qr_get("X-ROSE"))["status"], await have("alguses", ROSE)),
           ("deleted", 0))
        st, a = await scan("X-ROSE", ROSE, "silicon", "cover")
        eq("на свой район — пожалуйста", (a.get("new"), (await db.qr_get("X-ROSE"))["status"]), (True, "active"))

        print("── отмена скана: не стирать приход, а вернуть как было ────────")
        await drop("X-PROS")
        before = await db.qr_get("X-PROS")
        st, a = await scan("X-PROS", ROSE, "silicon", "cover")            # снова ошиблись позицией
        st, u = await undo("X-PROS")
        after = await db.qr_get("X-PROS")
        eq("отмена прошла и вернула в убранные", (u.get("ok"), u.get("restored"), after["status"]), (True, True, "deleted"))
        eq("запись — ровно как до скана", {k: v for k, v in after.items()}, {k: v for k, v in before.items()})
        eq("склад не тронут: 3 и 3", (await have("silicon", ROSE), await have("silicon", PROS)), (3, 3))
        st, a = await scan("X-PROS", PROS, "silicon", "cover")
        eq("вносится заново верной позицией", (a.get("new"), (await db.qr_get("X-PROS"))["product_id"], await have("silicon", PROS)),
           (True, PROS, 3))
        st, u = await undo("X-PROS")
        eq("второй раз «Выйти» — тоже возврат в убранные, не стирание",
           (u.get("restored"), (await db.qr_get("X-PROS")) is not None, await have("silicon", PROS)), (True, True, 3))
        await scan("X-PROS", PROS, "silicon", "cover")
        st, u = await undo("X-ROSE")                                        # его вернули раньше…
        st2, u2 = await undo("X-ROSE")                                      # …и повторное нажатие
        eq("двойная отмена не проходит дважды", (u.get("ok"), u2.get("ok"), u2.get("why")), (True, False, "touched"))
        await scan("X-ROSE", ROSE, "silicon", "cover")

        print("── тронутую после возврата — не отменяем ──────────────────────")
        await drop("X-ROSE")
        await scan("X-ROSE", ROSE, "silicon", "cover")
        mv = await SR.move_by_code("X-ROSE", "jvc", 1, "STAR", "senior")
        st, u = await undo("X-ROSE")
        eq("увезли на JVC — отмена отказана, бутылка на месте", (mv["verdict"], u.get("ok"), u.get("why"),
           (await db.qr_get("X-ROSE"))["status"]), ("ok", False, "touched", "active"))
        await SR.move_undo_by_code("X-ROSE")

        print("── новая запись отменяется, как раньше ────────────────────────")
        st, a = await scan("NEW-1", STOL, "silicon", "new")
        eq("новый товар: +1", (a.get("new"), await have("silicon", STOL)), (True, 1))
        st, u = await undo("NEW-1")
        eq("отмена стирает запись, склад 0", (u.get("ok"), u.get("restored"), await db.qr_get("NEW-1"), await have("silicon", STOL)),
           (True, False, None, 0))
        st, u = await undo("X-NOPE")
        eq("неизвестный код — why=unknown", (u.get("ok"), u.get("why")), (False, "unknown"))
        await d.qr_codes.update_one({"_id": "X-PROS"}, {"$unset": {"re_at": ""}})
        st, u = await undo("X-PROS")
        eq("запись с поставки без возврата отменой не стирается", (u.get("ok"), u.get("why"), bool(await db.qr_get("X-PROS"))),
           (False, "supply", True))

        print("── переименовали бутылку, которая уже переезжала ──────────────")
        await d.supplies.insert_one(supply("S2", "jvc", [(ABS, "Absolut 1 ltr", 1)], "Худоба", 202))
        await sr.task_scan("S2", "jvc", ABS, "M-1", "Худоба", 202, "", False)
        await sr.task_finish("S2", "jvc", "Худоба")
        mv = await SR.move_by_code("M-1", "silicon", 1, "Худоба", "driver")
        eq("Absolut: JVC 4+1−1 = 4, Силикон +1", (mv["verdict"], await have("jvc", ABS), await have("silicon", ABS)), ("ok", 4, 1))
        await drop("M-1")
        st, a = await scan("M-1", STOL, "silicon", "cover")                # на деле это Столичная
        tr = await d.stock_transfers.find_one({"code": "M-1"})
        eq("переезд везёт Столичную", (a.get("new"), tr["product_id"], tr["product_name"]), (True, STOL, "Stolichnaya 1 ltr"))
        eq("склад: Absolut JVC 4, Силикон 0; Столичная Силикон 1, JVC 0",
           (await have("jvc", ABS), await have("silicon", ABS), await have("silicon", STOL), await have("jvc", STOL)), (4, 0, 1, 0))
        st, u = await undo("M-1")
        tr = await d.stock_transfers.find_one({"code": "M-1"})
        eq("отмена вернула переезду Absolut", (u.get("restored"), tr["product_id"], await have("silicon", ABS), await have("silicon", STOL)),
           (True, ABS, 1, 0))

        print("── пиво ↔ бутылка: qty кода и вторая половина коробки ─────────")
        await d.supplies.insert_one(supply("S3", "jvc", [(BEER, "Heineken 0.33 can", 1)], "Худоба", 202))
        await sr.task_scan("S3", "jvc", BEER, "B-1", "Худоба", 202, "", False)
        await d.qr_codes.update_one({"_id": "B-1"}, {"$set": {"intake_extra": 0.5}})   # код до 17 сен: коробка одним кодом
        eq("пиво: 2 + 0,5 + 0,5", await have("jvc", BEER), 3)
        await drop("B-1")
        st, a = await scan("B-1", STOL, "jvc", "cover")
        x = await db.qr_get("B-1")
        eq("стала бутылкой: qty 1, половины коробки нет", (a.get("qty"), x["qty"], "intake_extra" in x), (1, 1, False))
        eq("склад: пиво 2, Столичная JVC 1", (await have("jvc", BEER), await have("jvc", STOL)), (2, 1))

        print("── внесённые руками и кодом к посчитанной — как прежде ────────")
        await scan("C-1", ABS, "jvc", "cover")
        eq("cover: склад JVC Absolut 4", await have("jvc", ABS), 4)
        await drop("C-1")
        st, a = await scan("C-1", ABS, "jvc", "new")
        x = await db.qr_get("C-1")
        eq("вернули кнопкой «новый товар» — приход (+1), src new, время — сейчас",
           (a.get("new"), x["src"], await have("jvc", ABS), x["at"] > T0.replace(tzinfo=None)), (True, "new", 5, True))
        await drop("C-1")
        eq("убрали — ручной приход снят", await have("jvc", ABS), 4)

        print("── два возврата разом ─────────────────────────────────────────")
        await drop("X-ROSE")
        res = await asyncio.gather(scan("X-ROSE", ROSE, "silicon", "cover"), scan("X-ROSE", ROSE, "silicon", "new"))
        eq("вернули один раз", sorted(bool(b.get("new")) for _, b in res), [False, True])
        eq("склад Rose Силикон 3", await have("silicon", ROSE), 3)

        print("── история и ответы ───────────────────────────────────────────")
        st, h = await own("GET", "/api/owner/qr/history?days=2")
        rows = [r for g in h["groups"] for r in g["rows"]]
        rd = [r for r in rows if r["kind"] == "readd"]
        eq("история: строки «внесена заново» есть", (st, bool(rd)), (200, True))
        eq("и не в счёт внесённых за день",
           sum(g["n"] for g in h["groups"]), sum(r["n"] for r in rows if r["kind"] not in ("removed", "readd")))
        st, lk = await own("GET", "/api/owner/qr/code/X-ROSE")
        eq("история бутылки: «заведена» (приёмка) и «внесена заново»",
           (st, [e["what"] for e in lk["story"]][:1], any(e["what"].startswith("внесена заново") for e in lk["story"])),
           (200, ["заведена"], True))
        for path in ("/api/owner/qr", f"/api/owner/qr/list?product_id={ROSE}&district=silicon"):
            st, _ = await own("GET", path)
            eq(f"GET {path} — 200", st, 200)
        eq("в реестре нет вложенных снимков (они отдельно, в qr_readds)",
           [k for k in (await d.qr_codes.find_one({"_id": "X-ROSE"})) if k == "prev"], [])

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL[:8]}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
