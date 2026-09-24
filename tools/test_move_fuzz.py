"""Перемещения между районами — случайные сценарии с проверкой инвариантов.

Владелец, 18 сен 2026: «прогони миллион тестов при миллионе разных сценариев…
и убедись, что это ничего не ломает в логике». Здесь не примеры, а случайные
дни: заявки от STAR и от операторов (в том числе кривые), «Начать
перемещение», сканы — правильные, чужие, повторные, списанные, одновременные,
со старым чтением документа, — «Принял» и «Принял неровно» от своих и чужих,
снятие задачи и заявки целиком, а ещё старший из STAR: берёт на себя одну
передачу «откуда → куда» или всё с района (так зовёт приложение, открытое до
обновления), возвращает водителям одну или все и сканирует сам — по каждой
передаче, куда везёт.
После КАЖДОГО шага проверяется всё, что должно быть правдой всегда:

  I1  код числится там, куда его увёз последний переезд, и переездов у кода
      ровно столько, сколько записей в книге;
  I2  в сети ни одна бутылка не появилась и не пропала (по каждой позиции);
  I3  остаток района = пересчёт + приехало − уехало (по книге переездов);
  I4  счёт каждой строки = сумма удачных сканов по ней, и не больше заказа;
      сумма всех строк = сумма переездов «по заявке» в книге;
  I5  статусы сходятся: «отдано» ⇔ отсканировано всё; «принято» — только
      отданное; задача закрыта ⇔ приняты все передачи; заявка закрыта ⇔
      закрыты или сняты все задачи;
  I6  списки водителя: «отдать» — ровно неотданные передачи его района (кроме
      взятых старшим), «забрать» — ровно непринятые передачи в его район;
      замок смены — то же;
  I7  «в пути» для заявки закупки = неотданный остаток открытых строк, в обе
      стороны;
  I8  отказ сканера склад не двигает;
  I9  заявка закупки считает полку так, будто перемещения сделаны (выборочно);
  I10 сверка получателя (с 19 сен 2026 он сканирует то, что ему отдали):
      коды передачи — ровно удачные сканы отдающего; засчитанные у
      получателя — только из них и без повторов; счёт «принято» по строке =
      удачным сканам получателя и не больше отданного; «Принял» ровно — только
      когда отсканировано всё; «не всё пришло» записывает то, что
      отсканировали; скан получателя склад не двигает.

Запуск: python3 tools/test_move_fuzz.py [сценариев] [первое зерно]
По умолчанию — 30 сценариев (для общего прогона тестов). Большой прогон —
tools/fuzz_move_run.py: процессы параллельно, зёрна подряд.
"""
import asyncio, math, os, random, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, stock_routes as SR, move_routes as MV
from config_offices import OFFICE_IDS, OFFICE_CODES
import json as _json
from aiohttp.test_utils import make_mocked_request


async def own_call(kind: str, mid: str, body: dict) -> dict:
    """Тот же приём, но через ручку STAR (владелец, 20 сен 2026: «почему из
    АМБАР СТАР нельзя отсканировать товар, который я принимаю?»): старший
    принимает за район-получатель, своего района у него нет."""
    r = make_mocked_request("POST", f"/api/owner/move/{mid}/{kind}", match_info={"mid": mid})
    r["owner_id"] = 1
    r._read_bytes = _json.dumps(body).encode()
    h = MV.handle_own_receive if kind == "receive" else MV.handle_own_accept
    return _json.loads((await h(r)).text)

D = "2026-09-18"; T0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
SR._biz_day = lambda *a, **k: D
CAT = SR._catalog()
BOTTLES = [p for p, x in CAT.items() if SR._unit(x) == 1][:40]
BEERS = [p for p, x in CAT.items() if SR._unit(x) > 1][:10]
NAMES = {o: [f"{OFFICE_CODES[o]}-{k}" for k in "абв"] for o in OFFICE_IDS}
STARS = ["STAR", "STAR-2"]                 # входы AMBAR STAR — «старшие»

def boss_of(t, src):
    """Кто из STAR взял передачу src → (задача t) на себя."""
    return (((t.get("give") or {}).get(src) or {}).get("senior") or {}).get("name") or ""


CHECKS = [0]
# Что на самом деле случилось за прогон — чтобы видеть, что сценарии не пустые:
# сколько бутылок отдали, сколько сканов отбито и чем, сколько приняли.
STATS = {}
def stat(k, n=1):
    STATS[k] = STATS.get(k, 0) + n

# Сколько раз место в строке НЕ заняли атомарно — это гонки, которые поймал
# фильтр базы, а не проверка перед ним.
_reserve = db.move_line_reserve
async def _counted_reserve(*a, **k):
    r = await _reserve(*a, **k)
    stat("гонка: место не занято (фильтр базы)" if not r else "место занято")
    return r
db.move_line_reserve = _counted_reserve
class Bad(Exception):
    pass
def ok(cond, what):
    CHECKS[0] += 1
    if not cond:
        raise Bad(what)


class World:
    """Одна случайная «смена»: полки, коды, заявки и то, что о них знаем сами."""

    def __init__(self, seed):
        self.r = random.Random(seed)
        self.seed = seed
        self.codes = {}            # код → {pid, qty, district, status}
        self.start = {}            # (район, позиция) → остаток по пересчёту
        self.mids = []
        self.model_got = {}        # (mid, куда, i) → сколько отдано по нашим подсчётам
        self.model_recv = {}       # (mid, куда, i) → сколько отсканировал получатель
        self.deliv = {}            # (mid, куда, откуда) → коды удачных сканов отдающего, с повторами
        self.moves_qty = 0.0       # сумма удачных сканов
        self.log = []

    async def setup(self):
        r = self.r
        db._db = AsyncMongoMockClient()[f"fz{self.seed}"]; d = db._db
        self.d = d
        self.pids = r.sample(BOTTLES, 3) + r.sample(BEERS, 1)
        n = 0
        for oid in OFFICE_IDS:
            lines = []
            for pid in self.pids:
                beer = SR._unit(CAT[pid]) > 1
                have = r.choice([0, 0, 1, 2, 3, 4, 6]) if not beer else r.choice([0, 0.5, 1, 1.5, 2])
                self.start[(oid, pid)] = float(have)
                lines.append({"id": pid, "name": pid, "price": 100, "unit": SR._unit(CAT[pid]),
                              "actual": have, "counted": True})
                # Кодов обычно столько, сколько лежит; иногда меньше («QR не внесён»).
                step = 0.5 if beer else 1
                k = int(round(have / step))
                if r.random() < 0.2:
                    k = max(0, k - r.randint(1, 2))
                for _ in range(k):
                    n += 1
                    cid = f"c{n}"
                    await d.qr_codes.insert_one({"_id": cid, "status": "active", "product_id": pid,
                        "product_name": CAT[pid]["name"], "district": oid, "origin": oid,
                        "src": "cover", "qty": step, "at": T0})
                    self.codes[cid] = {"pid": pid, "qty": step, "district": oid, "status": "active"}
            await db.save_stock_count(oid, D, {"district": oid, "day": D, "counted_at": T0.isoformat(),
                "first_time": True, "counted_by": 0, "lines": lines})
            for pid in self.pids:
                if r.random() < 0.8:
                    await db.set_stock_norm(oid, pid, r.choice([0, 1, 2, 3, 4]), 0)
        # Мёртвые коды: списанный и проданный — сканер их не пропускает.
        for st in ("written", "sold"):
            n += 1
            cid = f"c{n}"
            oid = r.choice(OFFICE_IDS)
            await d.qr_codes.insert_one({"_id": cid, "status": st, "product_id": self.pids[0],
                "product_name": "x", "district": oid, "origin": oid, "src": "cover", "qty": 1, "at": T0})
            self.codes[cid] = {"pid": self.pids[0], "qty": 1, "district": oid, "status": st}

    # ── действия ────────────────────────────────────────────────────────────
    def rand_rows(self):
        r = self.r
        rows = []
        for _ in range(r.randint(1, 5)):
            src, dst = r.sample(OFFICE_IDS, 2)
            pid = r.choice(self.pids)
            beer = SR._unit(CAT[pid]) > 1
            qty = r.choice([0.5, 1, 1, 1.5, 2, 3]) if beer else r.choice([1, 1, 2, 3, 2.5, 0.4])
            rows.append({"from": src, "to": dst, "id": pid, "qty": qty})
        # Кривые строки: сам себе, чужая позиция, ноль, мусор.
        if r.random() < 0.25:
            o = r.choice(OFFICE_IDS)
            rows.append(r.choice([{"from": o, "to": o, "id": self.pids[0], "qty": 1},
                                  {"from": o, "to": "nowhere", "id": self.pids[0], "qty": 1},
                                  {"from": o, "to": r.choice(OFFICE_IDS), "id": "p-no", "qty": 1},
                                  {"from": o, "to": r.choice(OFFICE_IDS), "id": self.pids[1], "qty": 0}]))
        return rows

    async def act_create(self):
        r = self.r
        rows = self.rand_rows()
        if r.random() < 0.5:
            res = await MV.create(rows, by="STAR")
        else:
            scope = set(r.sample(OFFICE_IDS, r.randint(1, 2)))
            to = r.choice(OFFICE_IDS)
            lines = [{"from": x["from"], "id": x["id"], "qty": x["qty"]} for x in rows]
            res = await MV.create_by_operator("Оп", to, lines, "", scope)
            if to not in scope and not all(l["from"] in scope for l in lines):
                ok(res == {"ok": False, "error": "not_yours"}, f"оператор создал чужое: {res}")
                return
        stat("заявка создана" if res.get("ok") else "заявка: отказ")
        if res.get("ok"):
            self.mids.append(res["move_id"])
            doc = await db.move_order_get(res["move_id"])
            for to, t in doc["tasks"].items():
                for i, l in enumerate(t["lines"]):
                    self.model_got[(res["move_id"], to, i)] = 0.0
                    self.model_recv[(res["move_id"], to, i)] = 0.0
                    step = SR.code_qty(CAT[l["id"]])
                    ok(abs(l["qty"] / step - round(l["qty"] / step)) < 1e-9 and l["qty"] > 0,
                       f"строка не целым числом кодов: {l}")
                    ok(l["from"] != to, "строка сама себе")

    async def open_pairs(self):
        out = []
        for mid in self.mids:
            doc = await db.move_order_get(mid)
            if not doc or doc.get("status") != "open":
                continue
            for to, t in (doc.get("tasks") or {}).items():
                if t.get("done_at") or t.get("cancelled_at"):
                    continue
                for src in sorted({l["from"] for l in t["lines"]}):
                    out.append((mid, to, src, doc))
        return out

    def pick_code(self, src, pid=None):
        r = self.r
        pool = [c for c, x in self.codes.items() if x["district"] == src and x["status"] == "active"
                and (pid is None or x["pid"] == pid)]
        return r.choice(pool) if pool else None

    async def act_scan(self, concurrent=False, stale=False):
        r = self.r
        pairs = await self.open_pairs()
        if not pairs:
            return
        # Чаще — передача, где ещё есть что отдать: так сценарий доходит до
        # «отдано» и «принято», а не топчется на отказах.
        live = [x for x in pairs if any(float(l["got"]) < float(l["qty"]) and l["from"] == x[2]
                                        for l in x[3]["tasks"][x[1]]["lines"])]
        mid, to, src, doc = r.choice(live if live and r.random() < 0.85 else pairs)
        t = doc["tasks"][to]
        # Кто сканирует: чаще свой (отдающий), иногда получатель или посторонний.
        roll = r.random()
        who_d = src if roll < 0.8 else (to if roll < 0.9 else r.choice(OFFICE_IDS))
        name = r.choice(NAMES[who_d])
        want = [l for l in t["lines"] if l["from"] == src]
        undone = [l for l in want if float(l["got"]) < float(l["qty"])]
        l = r.choice(undone if undone and r.random() < 0.85 else want)
        kind = r.random()
        if kind < 0.7:
            code = self.pick_code(src, l["id"])        # правильная бутылка
        elif kind < 0.8:
            code = self.pick_code(r.choice(OFFICE_IDS))  # случайная, скорее всего чужая
        elif kind < 0.87:
            moved = [c for c, x in self.codes.items() if x["district"] == to]
            code = r.choice(moved) if moved else None    # уже у получателя (повторный скан)
        elif kind < 0.93:
            code = r.choice([c for c, x in self.codes.items() if x["status"] != "active"])
        else:
            code = "нет-такого"
        if not code:
            return
        if r.random() < 0.7:
            await MV.give_start(mid, to, name, 1, who_d)
        before = {c: dict(x) for c, x in self.codes.items()}
        if concurrent:
            other = self.pick_code(src, l["id"]) or code
            res = await asyncio.gather(MV.scan(mid, to, code, name, 1, who_d),
                                       MV.scan(mid, to, other, r.choice(NAMES[who_d]), 2, who_d))
            codes = [code, other]
        elif stale:
            # Гонка «прочитал старое»: второй скан видит документ до первого.
            snap = await db.move_order_get(mid)
            other = self.pick_code(src, l["id"])
            first = await MV.scan(mid, to, code, name, 1, who_d)
            real = db.move_order_get
            calls = {"n": 0}
            async def stale_get(m):
                calls["n"] += 1
                return snap if calls["n"] == 1 else await real(m)
            db.move_order_get = stale_get
            try:
                second = await MV.scan(mid, to, other, name, 1, who_d) if other else None
            finally:
                db.move_order_get = real
            res = [first] + ([second] if second else [])
            codes = [code] + ([other] if second else [])
        else:
            res = [await MV.scan(mid, to, code, name, 1, who_d)]
            codes = [code]
        srcs = {ll["from"] for ll in t["lines"]}
        boss = boss_of(t, who_d)
        moved_now = {c for c, x in zip(codes, res) if x.get("ok")}
        for c, x in zip(codes, res):
            stat("скан: отдано" if x.get("ok") else f"скан: отказ {x.get('verdict')}")
            if x.get("ok"):
                # Отдать может только район, который в этой задаче отдаёт, и
                # только бутылку со своего района — по своей строке; и только
                # пока задачу не взял старший.
                ok(not boss, f"водитель отсканировал передачу, взятую старшим {boss}")
                ok(who_d in srcs, f"отсканировал не отдающий: {who_d}, отдают {srcs}")
                ok(before.get(c, {}).get("district") == who_d, f"ушёл код не с района отдающего: {c}")
                self.codes[c]["district"] = to
                self.moves_qty += float(x["qty"])
                i = next(k for k, ll in enumerate(t["lines"]) if ll["from"] == who_d and ll["id"] == self.codes[c]["pid"])
                self.model_got[(mid, to, i)] += float(x["qty"])
                self.deliv.setdefault((mid, to, who_d), []).append(c)
            else:
                v = x.get("verdict")
                if boss:
                    ok(v in ("senior_took", "gone"), f"передача у старшего, а водителю: {v}")
                elif who_d == to:
                    ok(v in ("giver_scans", "gone"), f"получатель: {v}")
                elif who_d not in srcs:
                    ok(v in ("not_giver", "gone"), f"посторонний: {v}")
                # Отказ склад не двигает (I8): код остался, где был (если его
                # же не увёз соседний удачный скан того же шага). Сверку модели
                # с базой делает I1.
                if c in self.codes and c not in moved_now:
                    ok(before[c]["district"] == self.codes[c]["district"], f"отказ сдвинул код {c}")

    async def act_accept(self):
        r = self.r
        pairs = await self.open_pairs()
        if not pairs:
            return
        given = [x for x in pairs if all(float(l["got"]) >= float(l["qty"]) - 1e-9
                                         for l in x[3]["tasks"][x[1]]["lines"] if l["from"] == x[2])]
        mid, to, src, doc = r.choice(given if given and r.random() < 0.8 else pairs)
        who_d = to if r.random() < 0.85 else r.choice(OFFICE_IDS)
        name = r.choice(NAMES[who_d])
        g = MV.give_view(mid, doc, to, doc["tasks"][to], src)
        if who_d == to and r.random() < 0.25:
            # «Приехало не всё» из STAR — тот же путь, что у водителя.
            res = await own_call("accept", mid, {"district": to, "from": src,
                                                 "ok": r.random() < 0.5, "as": "Старший"})
            stat("решение из STAR")
        elif r.random() < 0.6:
            res = await MV.accept(mid, to, src, name, 1, who_d)
        else:
            lines = [{"id": l["id"], "got": max(0, l["got"] - r.choice([0, 0, 0.5, 1]))} for l in g["lines"]]
            res = await MV.accept(mid, to, src, name, 1, who_d, ok=False, lines=lines,
                                  note=r.choice(["", "", "разбита"]))
        stat("принял: " + ("ok" if res.get("ok") and not res.get("already") else
                           "уже принято" if res.get("already") else str(res.get("error"))))
        if res.get("ok") and not res.get("already"):
            stat("принял неровно" if res["task"]["status"] == "diff" else "принял ровно")
        if who_d != to:
            ok(res.get("error") == "not_your_district", f"принял чужой район: {res}")
        elif g["status"] != "given":
            ok(not res.get("ok") or res.get("already"), f"принято неотданное: {g['status']} {res}")
        elif res.get("ok") and not res.get("already"):
            if res["task"]["status"] == "done":
                ok(g["recv_left"] <= 1e-9, f"«Принял» при неотсканированном: {g['recv_left']}")
            else:
                # «Не всё пришло»: записано то, что отсканировал получатель.
                for dl in res["task"]["accept_lines"]:
                    ln = next(l for l in g["lines"] if l["id"] == dl["id"])
                    ok(abs(float(dl["got"]) - float(ln["recv"])) < 1e-9 and abs(float(dl["sent"]) - float(ln["got"])) < 1e-9,
                       f"«не всё пришло» не по сканам: {dl} при {ln}")
                # Непринятое уезжает обратно к отдающему (владелец, 24 сен
                # 2026). Ждём поимённо те же коды, что считаем сами: отдающий
                # отсканировал, получатель — нет, бутылка цела и ещё у него.
                grec = ((doc.get("tasks") or {})[to].get("give") or {}).get(src) or {}
                принял = {str(c) for c in (grec.get("recv_codes") or [])}
                нужно = {l["id"]: float(l["got"]) - float(l["recv"]) for l in g["lines"]}
                ждём = []
                for c in [str(x) for x in (grec.get("codes") or [])]:
                    m = self.codes.get(c)
                    if c in принял or not m or m["district"] != to or m["status"] != "active":
                        continue
                    if нужно.get(m["pid"], 0) <= 1e-9:
                        continue
                    нужно[m["pid"]] -= float(m["qty"])
                    ждём.append(c)
                back = ((await db.move_order_get(mid))["tasks"][to]["give"][src].get("back_codes")) or []
                ok(sorted(back) == sorted(ждём),
                   f"вернулось не то: {sorted(back)} вместо {sorted(ждём)}")
                for c in ждём:
                    self.codes[c]["district"] = src
        elif g["recv_left"] > 1e-9 and res.get("error") not in ("gone",):
            ok(res.get("error") in ("scan_all", "diff_empty"), f"«Принял» без сканов не отбит словом: {res}")

    async def act_receive(self):
        """Получатель сканирует то, что ему отдали (с 19 сен 2026): свои коды
        передачи, повторы, любые бутылки сети, скан с чужого района."""
        r = self.r
        pairs = await self.open_pairs()
        if not pairs:
            return
        given = [x for x in pairs if all(float(l["got"]) >= float(l["qty"]) - 1e-9
                                         for l in x[3]["tasks"][x[1]]["lines"] if l["from"] == x[2])]
        mid, to, src, doc = r.choice(given if given and r.random() < 0.85 else pairs)
        t = doc["tasks"][to]
        gv = (t.get("give") or {}).get(src) or {}
        g = MV.give_view(mid, doc, to, t, src)
        codes = list(gv.get("codes") or [])
        was = set(gv.get("recv_codes") or [])
        roll = r.random()
        if roll < 0.65 and codes:
            c = r.choice([x for x in codes if x not in was] or codes)
        elif roll < 0.8 and was:
            c = r.choice(sorted(was))                           # повтор
        else:
            c = r.choice(sorted(self.codes))                    # любая бутылка сети
        who_d = to if r.random() < 0.9 else r.choice(OFFICE_IDS)
        before = {k: x["district"] for k, x in self.codes.items()}
        # Треть приёмов — из STAR: старший приезжает и сканирует сам.
        if who_d == to and r.random() < 0.34:
            x = await own_call("receive", mid, {"district": to, "from": src, "code": c, "as": "Старший"})
            stat("приём из STAR")
        else:
            x = await MV.receive(mid, to, src, c, r.choice(NAMES[who_d]), 1, who_d)
        stat("получатель: " + ("засчитан" if x.get("ok") else str(x.get("verdict"))))
        docs = {q["_id"]: q for q in await self.d.qr_codes.find({}).to_list(length=100000)}
        for k, dist in before.items():
            ok(docs[k]["district"] == dist, f"скан получателя сдвинул код {k}")
        if who_d != to:
            ok(x.get("verdict") == "not_your_district", f"скан получателя чужим районом: {x}")
            return
        if x.get("ok"):
            ok(g["status"] == "given", f"скан получателя засчитан при «{g['status']}»")
            ok(c in codes and c not in was, f"засчитан не тот код: {c}")
            i = next(k for k, ll in enumerate(t["lines"]) if ll["from"] == src and ll["id"] == self.codes[c]["pid"])
            self.model_recv[(mid, to, i)] += float(x["qty"])
            if x.get("finished"):
                stat("получатель отсканировал всё — принято само")
                ok(x["task"]["status"] == "done", f"последний скан не принял: {x['task']['status']}")
        else:
            v = x.get("verdict")
            if g["status"] in ("done", "diff"):
                ok(v in ("accepted", "gone"), f"скан в принятую: {v}")
            elif g["status"] != "given":
                ok(v in ("not_given", "gone"), f"скан в неотданную: {v}")
            elif c in was:
                ok(v == "again", f"повтор не узнан: {v}")
            else:
                ok(c not in codes, f"код передачи не засчитан: {c} {v}")

    async def act_senior(self):
        """Старший из STAR: взять на себя одну передачу или всё с района, вернуть
        водителям одну или все, сканировать сам — по передаче, которую взял."""
        r = self.r
        pairs = await self.open_pairs()
        if not pairs:
            return
        roll = r.random()
        if roll < 0.35:
            # По районам, куда везут (владелец, 18 сен 2026: «старший взял себе
            # два района — остальные видны водителям и свободны»): одна передача.
            drop = r.random() < 0.4
            held = [x for x in pairs if boss_of(x[3]["tasks"][x[1]], x[2])]
            mid, to, src, doc = r.choice(held if drop and held and r.random() < 0.75 else pairs)
            t = doc["tasks"][to]
            st_ = MV.give_view(mid, doc, to, t, src)["status"]
            boss = boss_of(t, src)
            me = boss if (drop and boss and r.random() < 0.8) else r.choice(STARS)
            before = {(m_, to_, s_): boss_of(d_["tasks"][to_], s_) for (m_, to_, s_, d_) in pairs}
            if drop:
                res = await MV.senior_drop(src, me, to=to, mid=mid)
                stat("старший: вернул передачу" if res.get("ok") else "старший: вернуть передачу нечего")
                want = st_ in ("wait", "pause", "live") and boss == me
                ok(bool(res.get("ok")) == want and res.get("dropped") == (1 if want else 0),
                   f"вернуть {src}→{to} ({st_}, взял {boss or 'никто'}) от {me}: {res}")
            else:
                res = await MV.senior_take(src, me, 1, to=to, mid=mid)
                stat("старший: взял передачу" if res.get("ok") else f"старший: не взял передачу {res.get('error')}")
                if st_ not in ("wait", "pause", "live"):
                    ok(not res.get("ok") and res.get("error") == "nothing", f"взял отданную {src}→{to} ({st_}): {res}")
                elif boss in ("", me):
                    ok(res.get("ok") and res.get("took") == 1, f"свободную {src}→{to} не взял: {res}")
                else:
                    ok(res.get("error") == "taken" and res.get("senior") == boss, f"взятую {boss} — {res}")
            # Сдвинулась ровно эта передача — остальные у тех же, что были.
            for (m_, to_, s_, d_) in await self.open_pairs():
                was = before.get((m_, to_, s_))
                now = boss_of(d_["tasks"][to_], s_)
                if (m_, to_, s_) == (mid, to, src):
                    if res.get("ok"):
                        ok(now == ("" if drop else me), f"после {'возврата' if drop else 'взятия'} у {src}→{to}: «{now}»")
                    else:
                        ok(now == was, f"отказ сдвинул хозяина {src}→{to}: {was} → {now}")
                elif was is not None:
                    ok(now == was, f"взятие {src}→{to} задело {s_}→{to_}: {was} → {now}")
            return
        if roll < 0.5:
            src, me = r.choice(OFFICE_IDS), r.choice(STARS)
            # Кто держит ЕЩЁ НЕ ОТДАННЫЕ передачи района: отданное целиком брать
            # уже нечего, и сервер честно отвечает «нечего», а не «взял другой».
            owners = {boss_of(d_["tasks"][to_], s_) for (m_, to_, s_, d_) in pairs if s_ == src
                      and MV.give_view(m_, d_, to_, d_["tasks"][to_], s_)["status"] in ("wait", "pause", "live")}
            res = await MV.senior_take(src, me, 1)
            stat("старший: взял район" if res.get("ok") else f"старший: не взял {res.get('error')}")
            others = owners - {"", me}
            if others and not res.get("ok"):
                ok(res.get("error") == "taken", f"взять с района, где взял другой: {res}")
            return
        if roll < 0.55:
            src, me = r.choice(OFFICE_IDS), r.choice(STARS)
            mine = [x for x in pairs if x[2] == src and boss_of(x[3]["tasks"][x[1]], src) == me
                    and MV.give_view(x[0], x[3], x[1], x[3]["tasks"][x[1]], src)["status"] in ("wait", "pause", "live")]
            res = await MV.senior_drop(src, me)
            stat("старший: снял с себя" if res.get("ok") else "старший: снять нечего")
            ok(res.get("dropped", 0) == len(mine), f"снял {res.get('dropped')}, взято им {len(mine)}")
            return
        taken = [x for x in pairs if boss_of(x[3]["tasks"][x[1]], x[2])]
        if taken and r.random() < 0.8:
            mid, to, src, doc = r.choice(taken)
            me = boss_of(doc["tasks"][to], src) if r.random() < 0.9 else r.choice(STARS)
        else:
            mid, to, src, doc = r.choice(pairs)
            me = r.choice(STARS)
        t = doc["tasks"][to]
        boss = boss_of(t, src)
        want = [l for l in t["lines"] if l["from"] == src and float(l["got"]) < float(l["qty"])]
        pick = r.choice(want) if want and r.random() < 0.85 else None
        code = (self.pick_code(src, pick["id"]) if pick else None) or self.pick_code(r.choice(OFFICE_IDS))
        if not code:
            return
        before = self.codes[code]["district"]
        x = await MV.scan(mid, to, code, me, 1, src, senior=True)
        stat("старший: отдано" if x.get("ok") else f"старший: отказ {x.get('verdict')}")
        if x.get("ok"):
            ok(boss == me, f"старший {me} отсканировал передачу, взятую {boss or 'никем'}")
            ok(before == src, f"старший увёз бутылку не с того района: {before} вместо {src}")
            self.codes[code]["district"] = to
            self.moves_qty += float(x["qty"])
            i = next(k for k, ll in enumerate(t["lines"]) if ll["from"] == src and ll["id"] == self.codes[code]["pid"])
            self.model_got[(mid, to, i)] += float(x["qty"])
            self.deliv.setdefault((mid, to, src), []).append(code)
        else:
            ok(self.codes[code]["district"] == before, f"отказ старшему сдвинул код {code}")
            if boss != me:
                ok(x.get("verdict") in ("not_taken", "senior_other", "gone"), f"не взявшему — {x.get('verdict')}")

    async def act_cancel(self):
        r = self.r
        if not self.mids:
            return
        mid = r.choice(self.mids)
        doc = await db.move_order_get(mid)
        if not doc:
            return
        to = r.choice(list(doc["tasks"]))
        roll = r.random()
        if roll < 0.4:
            await db.move_order_cancel(mid, to, datetime.now(timezone.utc))
        elif roll < 0.5:
            await db.move_order_cancel(mid, "", datetime.now(timezone.utc))
        else:
            scope = set(r.sample(OFFICE_IDS, 2))
            res = await MV.cancel_by_operator(mid, to, scope)
            t = doc["tasks"][to]
            if res.get("ok"):
                ok(to in scope and str(doc.get("by", "")).endswith("· оператор")
                   and not any(float(l.get("got") or 0) > 0 for l in t["lines"]),
                   f"оператор снял недозволенное: {res}")

    # ── инварианты ──────────────────────────────────────────────────────────
    async def check(self, full=False):
        d = self.d
        # I1: код — там, куда увёз последний переезд.
        docs = {x["_id"]: x async for x in d.qr_codes.find({})}
        trs = [x async for x in d.stock_transfers.find({})]
        by_code = {}
        for tr in trs:
            if tr.get("code"):                       # возврат числом кода не имеет
                by_code.setdefault(tr["code"], []).append(tr)
        for cid, x in self.codes.items():
            doc = docs[cid]
            ok(doc["district"] == x["district"], f"код {cid}: в базе {doc['district']}, по сканам {x['district']}")
            ok(len(doc.get("moves") or []) == len(by_code.get(cid, [])), f"код {cid}: переезды ≠ книга")
        # I3/I2: остатки по книге переездов.
        SR.base_drop()
        base = await SR._district_base(D)
        flow = {}
        for tr in trs:
            flow[(tr["to"], tr["product_id"])] = flow.get((tr["to"], tr["product_id"]), 0) + float(tr["qty"])
            flow[(tr["from"], tr["product_id"])] = flow.get((tr["from"], tr["product_id"]), 0) - float(tr["qty"])
        for pid in self.pids:
            tot0 = tot1 = 0.0
            for oid in OFFICE_IDS:
                have = float(((base[oid].get("have_exact") or {}).get(pid)) or 0)
                want = self.start[(oid, pid)] + flow.get((oid, pid), 0)
                ok(abs(have - want) < 1e-6, f"остаток {oid}/{pid}: {have} ≠ {want}")
                tot0 += self.start[(oid, pid)]; tot1 += have
            ok(abs(tot0 - tot1) < 1e-6, f"в сети {pid}: было {tot0}, стало {tot1}")
        # I4/I5: строки, передачи, задачи, заявки.
        sum_got = 0.0
        inc, out = {}, {}
        give_by, take_by = {o: set() for o in OFFICE_IDS}, {o: set() for o in OFFICE_IDS}
        for mid in self.mids:
            doc = await db.move_order_get(mid)
            all_closed = True
            for to, t in doc["tasks"].items():
                srcs = {l["from"] for l in t["lines"]}
                for i, l in enumerate(t["lines"]):
                    got, need = float(l["got"]), float(l["qty"])
                    ok(got <= need + 1e-9, f"{mid}/{to}/{i}: отдано {got} больше заказа {need}")
                    ok(got >= -1e-9, f"{mid}/{to}/{i}: отрицательный счёт")
                    ok(abs(got - self.model_got[(mid, to, i)]) < 1e-9,
                       f"{mid}/{to}/{i}: счёт {got}, удачных сканов на {self.model_got[(mid, to, i)]}")
                    sum_got += got
                    left = need - got
                    if doc["status"] == "open" and not t.get("done_at") and not t.get("cancelled_at") and left > 1e-9:
                        inc[(to, l["id"])] = inc.get((to, l["id"]), 0) + left
                        out[(l["from"], l["id"])] = out.get((l["from"], l["id"]), 0) + left
                for src in srcs:
                    gv = (t.get("give") or {}).get(src) or {}
                    ls = [l for l in t["lines"] if l["from"] == src]
                    given = all(float(l["got"]) >= float(l["qty"]) - 1e-9 for l in ls)
                    # I10: сверка получателя.
                    cs, rc = gv.get("codes") or [], gv.get("recv_codes") or []
                    dv = self.deliv.get((mid, to, src), [])
                    ok(len(cs) == len(set(cs)), f"{mid}/{to}/{src}: код передачи дважды")
                    ok(set(cs) == set(dv), f"{mid}/{to}/{src}: коды передачи ≠ удачным сканам отдающего")
                    ok(abs(float(gv.get("codes_q") or 0) - sum(float(l["got"]) for l in ls)) < 1e-9,
                       f"{mid}/{to}/{src}: отдано с записью кода ≠ отданному")
                    ok(abs(sum(self.codes[c]["qty"] for c in dv) - sum(float(l["got"]) for l in ls)) < 1e-9,
                       f"{mid}/{to}/{src}: удачные сканы ≠ отданному")
                    if len(dv) != len(set(dv)):
                        stat("I10: бутылка приехала по одной передаче дважды")
                    ok(len(rc) == len(set(rc)) and set(rc) <= set(cs), f"{mid}/{to}/{src}: засчитан чужой или повтор")
                    for i, l in enumerate(t["lines"]):
                        if l["from"] != src:
                            continue
                        rv = float(l.get("recv") or 0)
                        ok(rv <= float(l["got"]) + 1e-9, f"{mid}/{to}/{i}: принято {rv} больше отданного")
                        ok(abs(rv - self.model_recv[(mid, to, i)]) < 1e-9,
                           f"{mid}/{to}/{i}: принято {rv}, удачных сканов получателя {self.model_recv[(mid, to, i)]}")
                    if gv.get("accepted_at") and gv.get("accept_ok", True):
                        ok(all(abs(float(l.get("recv") or 0) - float(l["got"])) < 1e-9 for l in ls),
                           f"{mid}/{to}/{src}: «принято ровно», а отсканировано не всё")
                    if gv.get("accepted_at"):
                        ok(given, f"{mid}/{to}/{src}: принято неотданное")
                    if gv.get("done_at"):
                        ok(given, f"{mid}/{to}/{src}: «отдано» при неотсканированных")
                    live = doc["status"] == "open" and not t.get("done_at") and not t.get("cancelled_at")
                    if live and not given and not boss_of(t, src):
                        give_by[src].add((mid, to))
                    if live and not gv.get("accepted_at"):
                        take_by[to].add((mid, src))
                acc_all = all(((t.get("give") or {}).get(s) or {}).get("accepted_at") for s in srcs)
                if t.get("done_at"):
                    ok(acc_all, f"{mid}/{to}: задача закрыта, а приняты не все")
                elif not t.get("cancelled_at") and doc["status"] == "open":
                    ok(not acc_all, f"{mid}/{to}: приняты все, а задача открыта")
                if not (t.get("done_at") or t.get("cancelled_at")):
                    all_closed = False
            if doc["status"] == "done":
                ok(all_closed, f"{mid}: заявка закрыта при живых задачах")
            if doc["status"] == "open":
                ok(not all_closed, f"{mid}: все задачи закрыты, а заявка открыта")
        # Отданное строками = переезды заявки. Возврат непринятого и его отмена
        # считаются отдельно: это движение обратно, а не отдача (24 сен 2026).
        moved = sum(float(tr["qty"]) for tr in trs if tr.get("by_kind") == "move"
                    and not tr.get("move_back") and not tr.get("move_back_undo"))
        ok(abs(sum_got - moved) < 1e-9, f"строки {sum_got} ≠ переездов по заявке {moved}")
        back = sum(float(tr["qty"]) for tr in trs if tr.get("move_back"))
        undone = sum(float(tr["qty"]) for tr in trs if tr.get("move_back_undo"))
        ok(back >= undone - 1e-9, f"вернули меньше, чем отменили возвратов: {back} < {undone}")
        ok(abs(self.moves_qty - moved) < 1e-9, f"удачных сканов {self.moves_qty} ≠ книга {moved}")
        # I7: «в пути» — неотданный остаток, в обе стороны.
        pi, po = await MV.pending_qty()
        norm = lambda m: {k: round(v, 6) for k, v in m.items() if v > 1e-9}
        ok(norm(pi) == norm(inc), f"едет: {norm(pi)} ≠ {norm(inc)}")
        ok(norm(po) == norm(out), f"уходит: {norm(po)} ≠ {norm(out)}")
        # I6: списки водителя и замок смены.
        for oid in OFFICE_IDS:
            v = await MV.tasks_for_driver(NAMES[oid][0], oid)
            ok({(x["move_id"], x["district"]) for x in v["give"]} == give_by[oid],
               f"{oid}: «отдать» {[(x['move_id'], x['district']) for x in v['give']]} ≠ {give_by[oid]}")
            ok({(x["move_id"], x["from"]) for x in v["take"]} == take_by[oid],
               f"{oid}: «забрать» ≠ модели")
            for x in v["take"]:
                ok((x["status"] == "given") == (x["left"] <= 0), f"{oid}: активная карточка при неотданном")
            pend = await MV.pending_for_district(oid)
            ok({(x["move_id"], x["district"]) for x in pend if x["side"] == "give"} == give_by[oid], f"{oid}: замок «отдать»")
            ok({(x["move_id"], x["from"]) for x in pend if x["side"] == "take"} == take_by[oid], f"{oid}: замок «забрать»")
        # I9: заявка закупки — выборочно (дорогая).
        if full:
            o = await SR.order_rows(D)
            norms = await db.get_stock_norms()
            for row in o["all_rows"]:
                if row["id"] not in self.pids:
                    continue
                for oid in OFFICE_IDS:
                    c = row["cells"][oid]
                    have = float(((base[oid].get("have_exact") or {}).get(row["id"])) or 0)
                    nrm = float(c["norm"])
                    want = int(math.ceil(round(max(0.0, nrm - (have - out.get((oid, row["id"]), 0))
                                                   - inc.get((oid, row["id"]), 0)), 6)))
                    ok(c["calc"] == want, f"заявка {oid}/{row['id']}: {c['calc']} ≠ {want}")

    async def finish(self):
        """Конец дня: всё, что можно отдать, — отдают свои, всё отданное —
        получатель сканирует (последний скан принимает сам). Что упирается в
        бутылку без кода, так и остаётся открытым (недобора не бывает —
        снимает старший): проверяем, что оно держит смену."""
        for _ in range(3):
            for mid, to, src, doc in await self.open_pairs():
                t = doc["tasks"][to]
                boss = boss_of(t, src)
                name = boss or NAMES[src][0]
                if not boss:
                    await MV.give_start(mid, to, name, 1, src)
                for l in [l for l in t["lines"] if l["from"] == src]:
                    need = float(l["qty"]) - float(l["got"])
                    while need > 1e-9:
                        c = self.pick_code(src, l["id"])
                        if not c:
                            break
                        x = await (MV.scan(mid, to, c, name, 1, src, senior=True) if boss
                                   else MV.scan(mid, to, c, name, 1, src))
                        stat("конец дня: отдано" if x.get("ok") else f"конец дня: отказ {x.get('verdict')}")
                        if not x.get("ok"):
                            break
                        self.codes[c]["district"] = to
                        self.moves_qty += float(x["qty"])
                        i = next(k for k, ll in enumerate(t["lines"]) if ll["from"] == src and ll["id"] == l["id"])
                        self.model_got[(mid, to, i)] += float(x["qty"])
                        self.deliv.setdefault((mid, to, src), []).append(c)
                        need -= float(x["qty"])
                    await self.check()
            for mid, to, src, doc in await self.open_pairs():
                t = doc["tasks"][to]
                gv = (t.get("give") or {}).get(src) or {}
                if gv.get("accepted_at"):
                    continue
                was = set(gv.get("recv_codes") or [])
                for c in [c for c in (gv.get("codes") or []) if c not in was]:
                    x = await MV.receive(mid, to, src, c, NAMES[to][0], 1, to)
                    if not x.get("ok"):
                        stat(f"конец дня: скан получателя — {x.get('verdict')}")
                        break
                    i = next(k for k, ll in enumerate(t["lines"]) if ll["from"] == src and ll["id"] == self.codes[c]["pid"])
                    self.model_recv[(mid, to, i)] += float(x["qty"])
                    if x.get("finished"):
                        stat("конец дня: принял сканами")
                await self.check()

    async def run(self, steps):
        await self.setup()
        acts = [(self.act_create, 7), (self.act_scan, 50), (self.act_accept, 8), (self.act_receive, 24),
                (self.act_cancel, 4),
                (self.act_senior, 10),
                (lambda: self.act_scan(concurrent=True), 8), (lambda: self.act_scan(stale=True), 6)]
        pool = [f for f, w in acts for _ in range(w)]
        for i in range(steps):
            f = self.r.choice(pool)
            await f()
            await self.check(full=(i % 12 == 0))
        await self.check(full=True)
        await self.finish()
        await self.check(full=True)
        for mid in self.mids:
            doc = await db.move_order_get(mid)
            stat(f"заявка в конце: {doc['status']}")
            for t in doc["tasks"].values():
                stat("задача закрыта приёмкой" if t.get("done_at") else
                     "задача снята" if t.get("cancelled_at") else "задача в работе")


async def main(n, seed0, steps):
    bad = []
    for s in range(seed0, seed0 + n):
        w = World(s)
        try:
            await w.run(steps)
        except Bad as e:
            bad.append((s, str(e)))
            print(f"  FAIL зерно {s}: {e}")
    for k in sorted(STATS):
        print(f"    {k}: {STATS[k]}")
    print(f"ИТОГ: сценариев {n} (зёрна {seed0}…{seed0 + n - 1}), шагов по {steps}, "
          f"проверок {CHECKS[0]} — " + ("все прошли" if not bad else f"провалено {len(bad)}"))
    return bad


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    seed0 = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    bad = asyncio.run(main(n, seed0, steps))
    sys.exit(1 if bad else 0)
