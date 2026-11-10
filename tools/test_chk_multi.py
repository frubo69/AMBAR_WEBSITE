"""Чек-лист: по строке на каждую открытую приёмку и на каждую свободную
заявку без ответа (владелец, 10 ноя 2026: «если у нас несколько активных
приёмок/заявок, как сделать так, чтобы из чек-листа можно было в любую из них
попасть» → «Да, по строке»).

mongomock + настоящий owner_routes.handle_checklist:
  • три открытые приёмки — основная вчерашняя (B1 без водителя), заявка на
    базу сегодня (B2, водитель есть) и свободная заявка Барракуде сегодня
    (B1 взят, B3 без водителя) — три строки «Принятие поставки», самая старая
    первой, у каждой свой номер (sid), своя подпись и свой цвет: вчерашняя
    просрочена, сегодняшние — по окну своего дня, а не по вчерашней;
  • свободные заявки без ответа магазина — строка «Свободная заявка» на
    каждую: не высланная красная «пора», высланная зелёная «ждём ответ
    магазина»; обе ведут в свою заявку (go free, sid); отменённой нет;
  • две просьбы завершить без сканирования — две строки, у каждой одна
    задача; два остатка без сканирования — две строки;
  • «сегодня не заказываем» ни одной из этих строк не гасит;
  • одна открытая приёмка и ни одной свободной — одна строка с прежним
    текстом, строк свободной заявки нет.

    python3 tools/test_chk_multi.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta, timezone                     # noqa: E402
from aiohttp.test_utils import make_mocked_request                      # noqa: E402
from mongomock_motor import AsyncMongoMockClient                        # noqa: E402
import db, owner_routes as orr, supply_routes as sr                     # noqa: E402

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


async def rows():
    r = make_mocked_request("GET", "/api/owner/checklist"); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    h = orr.handle_checklist
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return json.loads(resp.text)["rows"]


def task(driver="", done=False):
    return {"qty": 10, "positions": 1, "scanned": 0, "driver": driver, "driver_id": 1 if driver else 0,
            "claimed_at": None, "started_at": None, "done_at": datetime.now(timezone.utc) if done else None,
            "last_at": None, "undo": 0, "note": "", "gaps": [], "flags": [], "cancelled_at": None}


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk_multi"]; d = db._db
    now = datetime.now(orr.DUBAI_TZ)
    day = orr._biz_day_start(now).date().isoformat()
    вчера = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    oid = list(orr.OFFICE_IDS)
    C = lambda o: orr.OFFICE_CODES.get(o, o)
    utc = datetime.now(timezone.utc)
    sent = utc - timedelta(hours=2)
    await d.supplies.insert_many([
        {"_id": "S1", "at": utc - timedelta(days=1), "status": "open", "day": вчера, "kind": "main", "items": [],
         "tasks": {oid[0]: task()}},
        {"_id": "X2", "at": utc - timedelta(hours=3), "status": "open", "day": day, "kind": "extra", "base": "Al Ain",
         "items": [], "tasks": {oid[1]: task("Водитель Б")}},
        {"_id": "F3", "at": utc - timedelta(hours=2), "status": "open", "day": day, "kind": "extra", "base": "Барракуда",
         "free_order": True, "items": [], "tasks": {oid[0]: task("Водитель А"), oid[2]: task()}},
        {"_id": "F4", "at": utc - timedelta(hours=3), "status": "wait", "day": day, "kind": "extra", "base": "Барракуда",
         "free_order": True, "created_at": utc - timedelta(hours=3), "sent_at": sent, "items": [], "tasks": {}, "total_qty": 44},
        {"_id": "F5", "at": utc - timedelta(hours=1), "status": "wait", "day": day, "kind": "extra", "base": "Барракуда",
         "free_order": True, "created_at": utc - timedelta(hours=1), "sent_at": None, "items": [], "tasks": {}, "total_qty": 12},
        {"_id": "F6", "at": utc - timedelta(hours=5), "status": "cancelled", "day": day, "kind": "extra", "base": "Барракуда",
         "free_order": True, "created_at": utc - timedelta(hours=5), "cancelled_at": utc - timedelta(hours=4),
         "items": [], "tasks": {}},
    ])
    # Просьбы и остатки — как их отдаёт supply_routes (чек-лист зовёт эти функции).
    at1, at2 = (utc - timedelta(minutes=40)).isoformat(), (utc - timedelta(minutes=20)).isoformat()
    async def fin_requests():
        return [{"supply_id": "S1", "district": oid[0], "code": C(oid[0]), "name": orr.OFFICE_NAMES.get(oid[0], ""),
                 "by": "Водитель А", "at": at1, "note": "полка закрыта", "left": 3, "need": 10, "got": 7,
                 "lines": [], "extra": False, "base": ""},
                {"supply_id": "X2", "district": oid[1], "code": C(oid[1]), "name": orr.OFFICE_NAMES.get(oid[1], ""),
                 "by": "Водитель Б", "at": at2, "note": "", "left": 1, "need": 10, "got": 9,
                 "lines": [], "extra": True, "base": "Al Ain"}]
    async def unscanned_tasks():
        return [{"supply_id": "S1", "district": oid[0], "code": C(oid[0]), "name": orr.OFFICE_NAMES.get(oid[0], ""),
                 "day": вчера, "driver": "Водитель А", "by": "Водитель А", "ok_by": "Старший", "at": at1,
                 "qty": 2, "left": 2, "need": 10, "got": 8, "lines": [{"id": "p1", "name": "Absolut", "left": 2}],
                 "extra": False, "base": ""},
                {"supply_id": "X2", "district": oid[1], "code": C(oid[1]), "name": orr.OFFICE_NAMES.get(oid[1], ""),
                 "day": day, "driver": "Водитель Б", "by": "Водитель Б", "ok_by": "Старший", "at": at2,
                 "qty": 5, "left": 5, "need": 10, "got": 5, "lines": [{"id": "p2", "name": "Jack", "left": 5}],
                 "extra": True, "base": "Al Ain"}]
    sr.fin_requests, sr.unscanned_tasks = fin_requests, unscanned_tasks

    print("── три открытые приёмки и две свободные заявки без ответа ──────")
    R = await rows()
    sup = [r for r in R if r["id"] == "supply_in"]
    eq("три строки «Принятие поставки», самая старая первой, у каждой свой номер",
       [(r["t"], r.get("sid"), r["go"]) for r in sup],
       [("Принятие поставки", "S1", "supply"), ("Принятие поставки", "F3", "supply"), ("Принятие поставки", "X2", "supply")])
    eq("подписи — каждая про свою поставку",
       [r["s"] for r in sup],
       [f"Барракуда {orr._day_human(вчера)} · не закрыт {C(oid[0])} · без водителя 1",
        f"Барракуда · свободная {orr._day_human(day)} · не закрыты {C(oid[0])}, {C(oid[2])} · без водителя 1",
        f"Al Ain {orr._day_human(day)} · не закрыт {C(oid[1])}"])
    today_state = orr._chk_state(False, now, orr._chk_at(day, 12.0), orr._chk_at(day, 21.0))
    eq("цвет: вчерашняя просрочена, сегодняшние — по окну своего дня",
       [r["state"] for r in sup], ["late", today_state, today_state])
    eq("просрочка вчерашней больше суток, у сегодняшних — нет",
       (sup[0]["late_min"] > 12 * 60, all(r["late_min"] < 12 * 60 for r in sup[1:])), (True, True))
    eq("n — незакрытые районы каждой", [r["n"] for r in sup], [1, 2, 1])
    free = [r for r in R if r["id"] == "free_order"]
    eq("две строки «Свободная заявка»: высланная ждёт, не высланная — пора; отменённой нет",
       [(r["t"], r.get("sid"), r["go"], r["state"]) for r in free],
       [("Свободная заявка", "F4", "free", "soon"), ("Свободная заявка", "F5", "free", "now")])
    eq("подписи свободных заявок",
       [r["s"] for r in free],
       [f"Барракуда {orr._day_human(day)} · файл выслан {sent.astimezone(orr.DUBAI_TZ).strftime('%H:%M')} · ждём ответ магазина",
        f"Барракуда {orr._day_human(day)} · файл не выслан"])
    ids = [r["id"] for r in R]
    eq("порядок — по плану: заявка в магазин, приёмки, свободные заявки, потом остальное",
       ids[ids.index("order_sent"):ids.index("order_sent") + 6],
       ["order_sent", "supply_in", "supply_in", "supply_in", "free_order", "free_order"])
    fr = [r for r in R if r["id"] == "fin_req"]
    eq("две просьбы — две строки, у каждой одна задача",
       [(r["s"], [t["supply_id"] for t in r["tasks"]], r["state"], r["due"]) for r in fr],
       [(f"{C(oid[0])} {orr.OFFICE_NAMES.get(oid[0], '')} · Водитель А · без кодов 3 бутылки · полка закрыта", ["S1"], "late",
         datetime.fromisoformat(at1).astimezone(orr.DUBAI_TZ).strftime("%H:%M")),
        (f"{C(oid[1])} {orr.OFFICE_NAMES.get(oid[1], '')} · Водитель Б · без кодов 1 бутылка", ["X2"], "late",
         datetime.fromisoformat(at2).astimezone(orr.DUBAI_TZ).strftime("%H:%M"))])
    un = [r for r in R if r["id"] == "unscanned_left"]
    eq("два остатка без сканирования — две строки, у каждой свой район и число",
       [(r["tasks"][0]["district"], r["n"], len(r["tasks"])) for r in un], [(oid[0], 2, 1), (oid[1], 5, 1)])
    eq("подпись остатка — как у одиночной строки",
       un[0]["s"], f"{C(oid[0])} {orr.OFFICE_NAMES.get(oid[0], '')} · 2 бутылки · Absolut 2 · приёмка "
                   f"{orr._day_human(вчера)} · Водитель А · разрешил Старший")

    print("── «сегодня не заказываем» ничего из этого не гасит ───────────")
    await db.checklist_set(day, "order_skip", True, "1")
    R = await rows()
    eq("заявка в магазин зелёная, приёмки и свободные заявки на месте",
       ([r["s"] for r in R if r["id"] == "order_sent"],
        [r.get("sid") for r in R if r["id"] == "supply_in"], [r.get("sid") for r in R if r["id"] == "free_order"]),
       (["сегодня заявки нет"], ["S1", "F3", "X2"], ["F4", "F5"]))
    await db.checklist_set(day, "order_skip", False, "1")

    print("── одна открытая приёмка — одна строка, как раньше ────────────")
    await d.supplies.delete_many({"_id": {"$in": ["X2", "F3", "F4", "F5"]}})
    async def none(): return []
    sr.fin_requests, sr.unscanned_tasks = none, none
    R = await rows()
    eq("одна строка со старым текстом, строк свободной заявки нет",
       ([(r["s"], r.get("sid")) for r in R if r["id"] == "supply_in"], [r for r in R if r["id"] == "free_order"]),
       ([(f"Барракуда {orr._day_human(вчера)} · не закрыт {C(oid[0])} · без водителя 1", "S1")], []))

    print("\nИТОГ:", "все проверки прошли" if not FAIL else f"ПРОВАЛЕНО {len(FAIL)}: " + "; ".join(FAIL))
    return 0 if not FAIL else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
