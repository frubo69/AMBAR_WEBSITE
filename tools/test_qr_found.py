"""Водитель нашёл наклейку, которой нет в реестре: весть владельцу и его решение.

Свободный скан в приложении водителя нужен ровно для этого: в приёмке не сошёлся
один QR, водитель обходит мешки и пикает подряд, а невнесённая наклейка всплывает
красной строкой. Что с ней делать, водитель не решает — он видит мешок, а не то,
чем его считать (владелец, 25 сен 2026: «как только они обнаружат не внесённый
QR, пусть приходит сообщение в АМБАР СТАР, и можно будет либо ничего с ним не
делать, либо зачислить на склад как позицию, я сам выбираю какую»).

  • находка записывается, владельцам уходит одно сообщение — и только одно,
    сколько бы раз по тем же мешкам ни прошли;
  • код, который в реестре есть, находкой не считается и вести не даёт;
  • список СТАРа отдаёт находку с районом и водителем;
  • «ничего не делать» — уходит с экрана, на складе ничего не меняется;
  • «зачислить» — обычный скан владельца: бутылка становится приходом, находка
    закрывается сама, повторного решения не просит.

    python3 tools/test_qr_found.py
"""
import asyncio, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, driver_routes as dr, qr_routes as QR, stock_routes as SR  # noqa: E402
import owner_routes                                               # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

ДЕНЬ, РАЙОН, ВОДИТЕЛЬ = "2026-09-25", "tecom", "Худоба"
ЧУЖОЙ_ID = 987654321   # такого числа в тексте быть не должно
ВЕСТИ = []


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt: return h
        h = nxt


async def notify(kind, text, *a, **k):
    ВЕСТИ.append((kind, text)); return None


async def нашёл(code, driver=ВОДИТЕЛЬ, район=РАЙОН):
    req = make_mocked_request("POST", "/x")
    req["driver"] = {"name": driver, "district": район}; req["tg"] = {"id": ЧУЖОЙ_ID}
    req._read_bytes = json.dumps({"code": code}).encode()
    r = await raw(dr.handle_code_found)(req)
    return r.status, json.loads(r.text)


async def список():
    req = make_mocked_request("GET", "/x"); req["owner_id"] = 1
    r = await raw(QR.handle_found_list)(req)
    return json.loads(r.text)["rows"]


async def оставить(code):
    req = make_mocked_request("POST", "/x"); req["owner_id"] = 1
    req._read_bytes = json.dumps({"code": code, "as": "STAR"}).encode()
    r = await raw(QR.handle_found_skip)(req)
    return r.status, json.loads(r.text)


async def зачислить(code, pid, район=РАЙОН):
    req = make_mocked_request("POST", "/x"); req["owner_id"] = 1
    req._read_bytes = json.dumps({"code": code, "product_id": pid, "district": район,
                                  "mode": "new", "as": "STAR"}).encode()
    r = await raw(QR.handle_scan)(req)
    return r.status, json.loads(r.text)


async def на_складе(pid, район=РАЙОН):
    SR.base_drop()
    b = await SR._district_base(ДЕНЬ)
    return (b.get(район) or {}).get("have_exact", {}).get(pid, 0)


async def main():
    db._db = AsyncMongoMockClient()["ambar_qr_found"]
    dr.notify_owners = notify
    owner_routes.notify_owners = notify
    SR._biz_day = lambda *a, **k: ДЕНЬ

    print("── водитель пикает мешки ──────────────────────────────────────")
    st, r = await нашёл("bud#000247")
    eq("ручка ответила: находка новая", (st, r.get("ok"), r.get("known"), r.get("new")),
       (200, True, False, True))
    eq("записана с районом и водителем",
       [(x["code"], x["driver"], x["district"]) for x in await список()],
       [("bud#000247", ВОДИТЕЛЬ, РАЙОН)])
    eq("район назван по-человечески", (await список())[0]["district_code"], "B5")
    eq("владельцам ушла одна весть", (len(ВЕСТИ), ВЕСТИ[0][0]), (1, "qr.found"))
    eq("в вести — код и кто нашёл",
       ("bud#000247" in ВЕСТИ[0][1], ВОДИТЕЛЬ in ВЕСТИ[0][1]), (True, True))
    eq("числового id в вести нет", str(ЧУЖОЙ_ID) in ВЕСТИ[0][1], False)

    st, r = await нашёл("bud#000247")
    eq("тот же код второй раз — не новая находка", (st, r.get("new")), (200, False))
    eq("ВТОРОЙ ВЕСТИ НЕТ", len(ВЕСТИ), 1)
    eq("и в списке она одна", len(await список()), 1)

    print("── код, который в реестре есть ────────────────────────────────")
    await db._db.qr_codes.insert_one({"_id": "есть", "status": "active", "district": РАЙОН,
                                      "product_id": "p1", "product_name": "Absolut 1 ltr",
                                      "qty": 1, "src": "new"})
    st, r = await нашёл("есть")
    eq("находкой не считается", (st, r.get("known")), (200, True))
    eq("и вести не даёт", len(ВЕСТИ), 1)
    eq("в списке не появилась", len(await список()), 1)

    print("── «ничего с ним не делать» ───────────────────────────────────")
    было = await на_складе("p1")
    st, r = await оставить("bud#000247")
    eq("ручка ответила", (st, r.get("ok"), r.get("closed")), (200, True, True))
    eq("с экрана ушла", await список(), [])
    eq("на складе ничего не изменилось", await на_складе("p1"), было)
    st, r = await оставить("bud#000247")
    eq("второй раз — не ошибка, но и решать нечего", (st, r.get("closed")), (200, False))

    print("── «зачислить на склад позицией» ──────────────────────────────")
    ВЕСТИ.clear()
    st, r = await нашёл("bud#000248")
    eq("новая находка ждёт решения", (st, len(await список())), (200, 1))
    было = await на_складе("p1")
    st, r = await зачислить("bud#000248", "p1")
    eq("скан владельца прошёл", (st, r.get("ok"), r.get("new")), (200, True, True))
    eq("бутылка встала на склад приходом", await на_складе("p1"), было + 1)
    eq("НАХОДКА ЗАКРЫЛАСЬ САМА — отдельной кнопки не нужно", await список(), [])
    got = await db._db.qr_found.find_one({"_id": "bud#000248"})
    eq("в записи видно, чем кончилось", (got.get("status"), got.get("decided_by")),
       ("added", "STAR"))
    c = await db._db.qr_codes.find_one({"_id": "bud#000248"})
    eq("код в реестре — приходом на тот район", (c.get("src"), c.get("district")), ("new", РАЙОН))

    print("── пиво: код — половина коробки ───────────────────────────────")
    st, r = await нашёл("heiny#000001")
    было = await на_складе("p31")
    st, r = await зачислить("heiny#000001", "p31")
    eq("зачислено половиной коробки", (r.get("ok"), r.get("qty")), (True, 0.5))
    eq("склад вырос на 0.5", round(await на_складе("p31") - было, 2), 0.5)
    eq("и эта находка закрыта", await список(), [])

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
