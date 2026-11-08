"""История проверок бутылок STAR понятна и одинакова на всех экранах
(владелец, 7 и 8 ноя 2026: «история проверки бутылки не понятная»).
mongomock + настоящие qr_routes.handle_checks / _check_view / _story:
  • время уходит с поясом (+00:00), хотя монга отдаёт datetime без tzinfo —
    иначе телефон читал UTC как местное и показывал проверку на 4 часа раньше;
  • проверка без единой бутылки в историю, счёт и флажки не попадает;
  • кто проверял — рабочим именем старшего из AMBAR_SENIOR_STAR_IDS, иначе как
    назвался телеграм;
  • в истории кода свободная проверка так и зовётся, с тем, кто сканировал, и
    тем, чем бутылка была («не было в реестре»); у водителя — его имя.

    python3 tools/test_check_history.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from datetime import datetime, timedelta, timezone
from mongomock_motor import AsyncMongoMockClient
from aiohttp.test_utils import make_mocked_request
import db, qr_routes as qr, config_staff as staff

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)


async def call(h, method, url, body=None):
    r = make_mocked_request(method, url); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    if body is not None:
        async def _j(): return body
        r.json = _j
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    return json.loads((await h(r)).text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_chk_hist"]; d = db._db
    staff.SENIOR_STAR_IDS = {"Тест-старший": 777}
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)   # как отдаёт motor: без tzinfo
    beer = lambda code, label: {"code": code, "verdict": "ok", "label": label, "product_id": "p47",
                                "product_name": "Carlsberg 0.5 can", "district": "alguses", "status": "active",
                                "added_at": str(now - timedelta(hours=1))}
    await d.qr_codes.insert_one({"_id": "17581", "code": "17581", "status": "active", "product_id": "p47",
                                 "product_name": "Carlsberg 0.5 can", "district": "alguses", "label": "car#000467",
                                 "by": 777, "at": now - timedelta(hours=1)})
    await d.qr_codes.insert_one({"_id": "17588", "code": "17588", "status": "active", "product_id": "p47",
                                 "product_name": "Carlsberg 0.5 can", "district": "alguses", "label": "car#000479",
                                 "by": 777, "src": "new", "at": now - timedelta(minutes=5)})
    await d.qr_checks.insert_many([
        # свободная, одна наша банка, старший из .env, не завершена
        {"driver": "", "district": "", "by": 777, "by_name": "STAR", "at": now - timedelta(minutes=20), "open": True,
         "items": [{**beer("17581", "car#000467"), "at": now - timedelta(minutes=19)}], "total": 1, "bad": 0, "warn": 0},
        # свободная, чужой код (потом внесён руками), имя из телеграма
        {"driver": "", "district": "", "by": 5, "by_name": "Am Bar", "at": now - timedelta(minutes=15), "open": True,
         "items": [{"code": "17588", "at": now - timedelta(minutes=14), "verdict": "alien", "label": "", "product_id": "",
                    "product_name": "", "district": "", "status": "", "added_at": ""}], "total": 1, "bad": 1, "warn": 0},
        # нажал и бросил — пустая, у водителя
        {"driver": "Водитель А", "district": "jvc", "by": 777, "by_name": "STAR", "at": now - timedelta(minutes=10),
         "open": True, "items": [], "total": 0, "bad": 0, "warn": 0},
        # свободная пустая
        {"driver": "", "district": "", "by": 777, "by_name": "STAR", "at": now - timedelta(minutes=8),
         "open": True, "items": [], "total": 0, "bad": 0, "warn": 0},
        # у водителя, закрыта, две банки
        {"driver": "Водитель Б", "district": "alguses", "by": 777, "by_name": "STAR", "at": now - timedelta(days=1),
         "open": False, "items": [{**beer("17581", "car#000467"), "at": now - timedelta(days=1, minutes=-1)},
                                  {**beer("17582", "car#000468"), "at": now - timedelta(days=1, minutes=-2)}],
         "total": 2, "bad": 0, "warn": 0},
    ])

    r = await call(qr.handle_checks, "GET", "/api/owner/qr/checks?days=30")
    eq("пустых проверок в истории нет: 3 из 5", len(r["checks"]), 3)
    eq("счёт тоже без пустых", (r["totals"]["checks"], r["totals"]["bottles"], r["totals"]["bad"]), (3, 4, 1))
    eq("флажки: Водитель А без проверок, Водитель Б — одна",
       {x["driver"]: x["checks"] for x in r["drivers"]}, {"Водитель Б": 1})
    c0, c1, c2 = r["checks"]                                      # новые сверху: чужой код, банка, вчерашняя
    eq("время с поясом UTC", (c1["at"].endswith("+00:00"), c1["items"][0]["at"].endswith("+00:00")), (True, True))
    eq("время то самое", c1["at"][:19], (now - timedelta(minutes=20)).isoformat()[:19])
    eq("проверял — рабочим именем старшего", (c1["who"], c1["by_name"]), ("Тест-старший", "Тест-старший"))
    eq("не старший — как назвался телеграм", c0["who"], "Am Bar")
    eq("закрытая у водителя на месте", (c2["driver"], c2["total"], c2["open"]), ("Водитель Б", 2, False))

    eq("_iso: без tzinfo → UTC, с tzinfo — как есть, строка — строкой",
       (qr._iso(datetime(2026, 11, 7, 11, 13, 58)), qr._iso("2026-11-07"), qr._iso(None)),
       ("2026-11-07T11:13:58+00:00", "2026-11-07", ""))

    story = await qr._story(await d.qr_codes.find_one({"_id": "17588"}))
    chk = [x for x in story if "проверка" in x["what"]]
    eq("история чужого кода: свободная проверка, чем была, кто сканировал",
       [(x["what"], x["who"], x["where"]) for x in chk], [("свободная проверка · не было в реестре", "Am Bar", "")])
    eq("моменты истории с поясом", all(x["at"].endswith("+00:00") for x in story if x["at"]), True)
    story = await qr._story(await d.qr_codes.find_one({"_id": "17581"}))
    chk = [(x["what"], x["who"], x["where"]) for x in story if "проверка" in x["what"]]
    eq("история нашей банки: у водителя — его имя, свободная — имя старшего",
       sorted(chk), sorted([("проверка у водителя", "Водитель Б", "alguses"), ("свободная проверка", "Тест-старший", "")]))
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
