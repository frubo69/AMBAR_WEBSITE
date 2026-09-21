"""Фаззер приёмки: скан, «убрать бутылку», чужие руки, замки, продажа и
переезд принятого, приём без сканирования, закрытие района (владелец, 21 сен
2026: «проведи глубокий фаззер, огромное количество сценариев… чтобы это нам
не сломало всю логику»).

Случайные цепочки действий над одной поставкой на два района. Рядом живёт
своя модель правил — независимо от кода сервера: что должно ответить на
каждое действие и что должно лежать в базе после него. После КАЖДОГО шага:

  • ответ сервера = ответ модели (вердикт, ours/as_id у «уже в реестре»,
    got/left у «убрано»);
  • строка задачи в базе = модель: got, и got не выше плана;
  • got строки = сумма qty кодов этой приёмки в реестре (реестр и задача не
    расходятся ни на полкоробки); scanned задачи = число её кодов;
  • убранный код исчез из реестра и снова принимается;
  • раз в несколько шагов — склад района (_district_base): пересчёт + коды
    прихода + неотсканированное «без сканирования» — ровно как в модели;
  • после закрытия района — недобор по строкам = план − принято, и в итоге
    приёмки старшему названы убранные позиции.

mongomock по умолчанию (позиционный items.$ у него бьёт в первый элемент
массива — supply_take/supply_untake заменены теми же условиями по индексу).
С --real — настоящая монга из MONGO_URI, во временной базе ambar_fuzz_*,
без замен; базу стирает за собой.

    python3 tools/test_fuzz_intake.py                  # быстрый прогон (в наборе тестов)
    FUZZ_SEEDS=300 FUZZ_STEPS=400 python3 tools/test_fuzz_intake.py
    python3 tools/test_fuzz_intake.py --real           # на VPS, временная база
"""
import asyncio, os, random, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL = "--real" in sys.argv
if not REAL:
    os.environ.setdefault("MONGO_URI", "")
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
import db, supply_routes as sr, stock_routes as SR

SEEDS = int(os.getenv("FUZZ_SEEDS", "40"))
STEPS = int(os.getenv("FUZZ_STEPS", "160"))
DAY = "2026-09-21"
T0 = datetime.now(timezone.utc) - timedelta(hours=1)
DRV = {"jvc": "Худоба", "bbay": "Авазбек"}
ACTORS = ["Худоба", "Авазбек", "STAR"]
PLAN = {"jvc": {"p1": 3, "p2": 2, "p31": 2}, "bbay": {"p1": 2, "p5": 1, "p31": 1}}
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr",
         "p31": "Heineken 0.33 can", "p3": "Russian Standard 1 ltr"}
COUNT = {"jvc": {"p1": 5, "p2": 1, "p31": 3}, "bbay": {"p1": 4, "p5": 2, "p31": 1}}
QTY = {}                                   # позиция → сколько несёт код (пиво 0.5)

FAILS = []
СКАЗАНО = []
ВЕТКИ = {}                                 # какие ответы видели — покрытие


def ветка(k):
    ВЕТКИ[k] = ВЕТКИ.get(k, 0) + 1


def fail(seed, step, what, got, want, trail):
    FAILS.append((seed, step, what))
    print(f"  FAIL seed={seed} шаг={step}: {what}\n       получили {got!r}\n       ждали    {want!r}")
    for t in trail[-8:]:
        print("       …", t)


# ── mongomock: позиционный $ по индексу ──────────────────────────────────────
async def _take(sid, district, product_id, room, now, qty=1):
    s = await db.supply_get(sid)
    if not s or s.get("status") != "open":
        return None
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) > room:
        return None
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": qty, f"items.{i}.scanned": 1,
                 f"tasks.{district}.scanned": 1, f"tasks.{district}.rev": 1},
        "$set": {f"tasks.{district}.last_at": now}})
    return await db.supply_get(sid)


async def _untake(sid, district, product_id, qty=1):
    s = await db.supply_get(sid)
    i = next((k for k, it in enumerate(s["items"]) if it["id"] == product_id), None)
    if i is None or float((s["items"][i].get("got") or {}).get(district) or 0) < qty:
        return False
    await db._db.supplies.update_one({"_id": sid}, {
        "$inc": {f"items.{i}.got.{district}": -qty, f"items.{i}.scanned": -1,
                 f"tasks.{district}.scanned": -1, f"tasks.{district}.undo": 1,
                 f"tasks.{district}.rev": 1}})
    return True


# ── модель ───────────────────────────────────────────────────────────────────
class Model:
    def __init__(self, sid):
        self.sid = sid
        self.got = {o: {p: 0.0 for p in PLAN[o]} for o in PLAN}
        self.codes = {}          # код → {pid, origin, district, status, qty, supply}
        self.removed = set()     # убранные этой поставки (снова свободны)
        self.foreign = set()     # чужие: другая приёмка или внесённые руками
        self.noscan = {o: False for o in PLAN}
        self.done = {o: False for o in PLAN}
        self.hold = {o: None for o in PLAN}      # кто держит замок (живой)
        self.undone = {o: {} for o in PLAN}      # убрано: имя позиции → сколько
        self.rev = {o: 0 for o in PLAN}          # версия задачи: +1 на каждый скан и «убрать»
        self.status_open = True
        self.n = 0

    def new_code(self):
        self.n += 1
        return f"{self.sid}-c{self.n:04d}"

    def can_touch(self, o, who):
        return who == DRV[o] or (who == "STAR" and self.noscan[o])

    def busy(self, o, who):
        h = self.hold[o]
        return h is not None and h != who

    def ours(self, c, o):
        d = self.codes.get(c)
        return bool(d) and d["supply"] == self.sid and d["origin"] == o and d["district"] == o \
            and d["status"] == "active"

    def expect_scan(self, o, pid, c, who):
        if not self.status_open:
            return {"verdict": "no_supply"}
        if not self.can_touch(o, who):
            return {"verdict": "not_mine"}
        if self.done[o]:
            return {"verdict": "closed"}
        if self.busy(o, who):
            return {"verdict": "busy"}
        if pid not in PLAN[o]:
            return {"verdict": "not_in_supply"}
        if c in self.codes:
            ours = self.ours(c, o)
            return {"verdict": "known", "ours": ours,
                    "as_id": self.codes[c]["pid"] if ours else ""}
        if self.got[o][pid] + QTY[pid] > PLAN[o][pid] + 1e-9:
            return {"verdict": "full"}
        return {"ok": True}

    def expect_undo(self, o, c, who):
        if not self.status_open or self.done[o]:
            return {"verdict": "closed"}
        if not self.can_touch(o, who):
            return {"verdict": "not_mine"}
        if self.busy(o, who):
            return {"verdict": "busy"}
        d = self.codes.get(c)
        if not d or d["supply"] != self.sid or d["origin"] != o:
            return {"verdict": "not_ours"}
        if not self.ours(c, o):
            return {"verdict": "moved"}
        return {"ok": True, "product_id": d["pid"]}


def _left(need, got):
    return max(0, round((need - got) * 2) / 2)


async def check_db(m, seed, step, trail):
    """База = модель: строки, реестр, счётчики задачи."""
    s = await db.supply_get(m.sid)
    for o in PLAN:
        for it in s["items"]:
            pid = it["id"]
            if pid not in PLAN[o]:
                continue
            got = float((it.get("got") or {}).get(o) or 0)
            if abs(got - m.got[o][pid]) > 1e-9:
                fail(seed, step, f"строка {o}/{pid}: got в базе ≠ модели", got, m.got[o][pid], trail)
            if got > PLAN[o][pid] + 1e-9:
                fail(seed, step, f"строка {o}/{pid}: принято больше плана", got, PLAN[o][pid], trail)
        # Реестр: коды этой приёмки по району и позиции.
        rows = await db._db.qr_codes.find({"supply_id": m.sid}).to_list(length=None)
        по = {}
        n = 0
        for r in rows:
            if (r.get("origin") or r.get("district")) != o:
                continue
            по[r["product_id"]] = по.get(r["product_id"], 0) + float(r.get("qty") or 1)
            n += 1
        for pid in PLAN[o]:
            if abs(по.get(pid, 0) - m.got[o][pid]) > 1e-9:
                fail(seed, step, f"реестр {o}/{pid}: сумма кодов ≠ принятому", по.get(pid, 0),
                     m.got[o][pid], trail)
        scanned = int(((s.get("tasks") or {}).get(o) or {}).get("scanned") or 0)
        if scanned != n:
            fail(seed, step, f"задача {o}: scanned ≠ числу её кодов", scanned, n, trail)
        rev = int(((s.get("tasks") or {}).get(o) or {}).get("rev") or 0)
        if rev != m.rev[o]:
            fail(seed, step, f"задача {o}: версия в базе", rev, m.rev[o], trail)
        v = sr._task_view(m.sid, s, o, (s.get("tasks") or {}).get(o) or {}, DRV[o])
        if v.get("rev") != m.rev[o]:
            fail(seed, step, f"вид задачи {o}: версия", v.get("rev"), m.rev[o], trail)
    for c in list(m.removed)[:30]:
        if await db.qr_get(c):
            fail(seed, step, f"убранный код {c} остался в реестре", True, False, trail)


async def check_stock(m, seed, step, trail):
    """Склад района — тем же расчётом, что карточка «Склад» и заявка."""
    base = await SR._district_base(DAY)
    for o in PLAN:
        have = (base.get(o) or {}).get("have_exact") or {}
        for pid in PLAN[o]:
            want = COUNT[o].get(pid, 0)
            want += sum(d["qty"] for c, d in m.codes.items()
                        if d["supply"] == m.sid and d["origin"] == o and d["pid"] == pid)
            if m.noscan[o] and not m.done[o]:
                want += max(0.0, PLAN[o][pid] - m.got[o][pid])
            got = float(have.get(pid, 0))
            if abs(got - want) > 1e-9:
                fail(seed, step, f"склад {o}/{pid}", got, want, trail)


async def run_seed(seed):
    rnd = random.Random(seed)
    sid = f"F{seed:05d}"
    m = Model(sid)
    now = datetime.now(timezone.utc)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": DAY,
        "items": [{"id": pid, "name": NAMES[pid], "qty": sum(PLAN[o].get(pid, 0) for o in PLAN),
                   "by_district": {o: PLAN[o][pid] for o in PLAN if pid in PLAN[o]},
                   "got": {o: 0 for o in PLAN if pid in PLAN[o]}}
                  for pid in sorted({p for o in PLAN for p in PLAN[o]})],
        "tasks": {o: {"driver": DRV[o], "driver_id": 1, "claimed_at": now, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0} for o in PLAN}})
    # Чужие бутылки: коды другой приёмки и внесённые руками — «уже в реестре».
    for k in range(4):
        c = f"{sid}-f{k}"
        o = rnd.choice(list(PLAN))
        await db._db.qr_codes.insert_one({"_id": c, "status": "active", "product_id": "p1",
                                          "product_name": NAMES["p1"], "district": o, "origin": o,
                                          "src": rnd.choice(["intake", "new"]), "qty": 1,
                                          "supply_id": "OTHER" if k % 2 else None, "at": T0})
        m.codes[c] = {"pid": "p1", "origin": o, "district": o, "status": "active", "qty": 1,
                      "supply": "OTHER" if k % 2 else None}
        m.foreign.add(c)
    SR.base_drop()
    trail = []

    def pick_code():
        ours = [c for c, d in m.codes.items() if d["supply"] == sid]
        r = rnd.random()
        if r < 0.45 or not ours:
            return m.new_code()
        if r < 0.75:
            return rnd.choice(ours)
        if r < 0.85 and m.removed:
            return rnd.choice(sorted(m.removed))
        if r < 0.95:
            return rnd.choice(sorted(m.foreign))
        return f"никто-{rnd.randint(0, 999)}"

    for step in range(STEPS):
        o = rnd.choice(list(PLAN))
        who = DRV[o] if rnd.random() < 0.8 else rnd.choice(ACTORS)
        a = rnd.random()
        if a < 0.45:                                         # скан
            pid = rnd.choice(list(PLAN[o])) if rnd.random() < 0.95 else "p3"
            c = pick_code()
            want = m.expect_scan(o, pid, c, who)
            r = await sr.task_scan(sid, o, pid, c, who, 1, "", who == "STAR")
            trail.append(f"скан {o} {pid} {c} {who} → {r.get('verdict') or 'ok'}")
            ветка("скан:" + (r.get("verdict") or "ok") + (
                (":своя-другая" if r.get("as_id") != pid else ":своя-та-же") if r.get("ours") else ""))
            if want.get("ok"):
                if not r.get("ok"):
                    fail(seed, step, "скан должен пройти", r.get("verdict"), "ok", trail); break
                m.got[o][pid] = round((m.got[o][pid] + QTY[pid]) * 2) / 2
                m.codes[c] = {"pid": pid, "origin": o, "district": o, "status": "active",
                              "qty": QTY[pid], "supply": sid}
                m.removed.discard(c)
                m.hold[o] = who
                m.rev[o] += 1
                if r.get("rev") != m.rev[o]:
                    fail(seed, step, "скан: версия задачи в ответе", r.get("rev"), m.rev[o], trail)
                if (r.get("got"), r.get("left")) != (m.got[o][pid], _left(PLAN[o][pid], m.got[o][pid])):
                    fail(seed, step, "скан: got/left в ответе", (r.get("got"), r.get("left")),
                         (m.got[o][pid], _left(PLAN[o][pid], m.got[o][pid])), trail)
                # Без сканирования и всё досканировали — район закрылся сам.
                if m.noscan[o] and all(m.got[o][p] >= PLAN[o][p] for p in PLAN[o]):
                    if not r.get("finished"):
                        fail(seed, step, "без сканирования: последняя бутылка закрывает район",
                             r.get("finished"), True, trail)
                    m.done[o] = True; m.hold[o] = None
                    if all(m.done.values()):
                        m.status_open = False
            else:
                if r.get("ok") or r.get("verdict") != want["verdict"]:
                    fail(seed, step, f"скан: вердикт", r.get("verdict") or "ok", want["verdict"], trail); break
                if want["verdict"] == "known" and (bool(r.get("ours")), r.get("as_id") or "") != (want["ours"], want["as_id"]):
                    fail(seed, step, "«уже в реестре»: чья бутылка и под чем записана",
                         (r.get("ours"), r.get("as_id")), (want["ours"], want["as_id"]), trail)
                if want["verdict"] in ("known", "full") and r.get("rev") != m.rev[o]:
                    fail(seed, step, f"«{want['verdict']}»: версия задачи в ответе", r.get("rev"), m.rev[o], trail)
        elif a < 0.78:                                       # убрать
            ours = [c for c, d in m.codes.items() if d["supply"] == sid]
            c = rnd.choice(ours) if ours and rnd.random() < 0.7 else pick_code()
            want = m.expect_undo(o, c, who)
            r = await sr.task_undo(sid, o, c, who, who == "STAR")
            trail.append(f"убрать {o} {c} {who} → {r.get('verdict') or 'ok'}")
            ветка("убрать:" + (r.get("verdict") or "ok"))
            if want.get("ok"):
                if not r.get("ok"):
                    fail(seed, step, "убрать должно пройти", r.get("verdict"), "ok", trail); break
                pid = want["product_id"]
                m.got[o][pid] = round((m.got[o][pid] - QTY[pid]) * 2) / 2
                m.undone[o][NAMES[pid]] = m.undone[o].get(NAMES[pid], 0) + 1
                del m.codes[c]
                m.removed.add(c)
                m.rev[o] += 1
                if r.get("rev") != m.rev[o]:
                    fail(seed, step, "убрано: версия задачи в ответе", r.get("rev"), m.rev[o], trail)
                exp = (pid, m.got[o][pid], _left(PLAN[o][pid], m.got[o][pid]), PLAN[o][pid])
                if (r.get("product_id"), r.get("got"), r.get("left"), r.get("need")) != exp:
                    fail(seed, step, "убрано: строка в ответе",
                         (r.get("product_id"), r.get("got"), r.get("left"), r.get("need")), exp, trail)
            elif r.get("ok") or r.get("verdict") != want["verdict"]:
                fail(seed, step, "убрать: вердикт", r.get("verdict") or "ok", want["verdict"], trail); break
        elif a < 0.84:                                       # замок другого / время прошло
            if rnd.random() < 0.5 and not m.done[o]:
                h = rnd.choice(ACTORS)
                await db._db.supplies.update_one({"_id": sid}, {"$set": {f"tasks.{o}.hold": {
                    "who": h, "kind": "driver", "at": datetime.now(timezone.utc),
                    "until": datetime.now(timezone.utc) + timedelta(seconds=40)}}})
                m.hold[o] = h
                trail.append(f"замок {o} у {h}")
            else:
                for x in PLAN:
                    await db._db.supplies.update_one({"_id": sid}, {"$set": {
                        f"tasks.{x}.hold.until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
                    m.hold[x] = None
                trail.append("время прошло — замки истекли")
        elif a < 0.90:                                       # продали / перевезли принятую
            act = [c for c, d in m.codes.items() if d["supply"] == sid and d["status"] == "active"
                   and d["district"] == d["origin"]]
            if act:
                c = rnd.choice(act)
                if rnd.random() < 0.5:
                    await db._db.qr_codes.update_one({"_id": c}, {"$set": {"status": "sold"}})
                    m.codes[c]["status"] = "sold"; trail.append(f"продали {c}")
                else:
                    to = "silicon"
                    await db._db.qr_codes.update_one({"_id": c}, {"$set": {"district": to}})
                    m.codes[c]["district"] = to; trail.append(f"перевезли {c}")
        elif a < 0.92:                                       # принять без сканирования
            r = await sr.task_noscan(sid, o, DRV[o], False)
            trail.append(f"без сканирования {o} → {r.get('verdict') or 'ok'}")
            ветка("без сканирования:" + (r.get("verdict") or "ok"))
            ok_want = m.status_open and not m.done[o] and not m.noscan[o]
            if bool(r.get("ok")) != ok_want:
                fail(seed, step, "без сканирования", r.get("verdict") or "ok", ok_want, trail); break
            if r.get("ok"):
                m.noscan[o] = True
        elif a < 0.925:                                      # закрыть район
            r = await sr.task_finish(sid, o, DRV[o], "магазин не дал", False)
            trail.append(f"закрыть {o} → {r.get('verdict') or 'ok'}")
            ветка("закрыть:" + (r.get("verdict") or "ok"))
            ok_want = m.status_open and not m.done[o]
            if bool(r.get("ok")) != ok_want:
                fail(seed, step, "закрыть район", r.get("verdict") or "ok", ok_want, trail); break
            if r.get("ok"):
                m.done[o] = True; m.hold[o] = None
                t = (await db.supply_get(sid))["tasks"][o]
                gaps = sorted((g["id"], g["gap"]) for g in t.get("gaps") or [])
                want_g = sorted((p, round((PLAN[o][p] - m.got[o][p]) * 2) / 2) for p in PLAN[o]
                                if m.got[o][p] < PLAN[o][p])
                if gaps != want_g:
                    fail(seed, step, f"недобор {o}", gaps, want_g, trail)
                if all(m.done.values()):
                    m.status_open = False
                await asyncio.sleep(0)
                итог = next((t for k, t in reversed(СКАЗАНО) if k == "supply.done"), "")
                for name in m.undone[o]:
                    if name not in итог:
                        fail(seed, step, f"итог приёмки {o}: не назван убранный «{name}»", итог[:200], name, trail)
        else:                                                # пусто — просто сверка
            pass
        await check_db(m, seed, step, trail)
        if step % 12 == 0 or step == STEPS - 1:
            await check_stock(m, seed, step, trail)
        if FAILS:
            break
        # Поставка закрыта целиком: ещё немного шагов — всё обязано отбиваться, —
        # и хватит, дальше одно и то же.
        if not m.status_open:
            m.tail = getattr(m, "tail", 0) + 1
            if m.tail > 8:
                await check_stock(m, seed, step, trail)
                break
    return m


async def main():
    global QTY
    t0 = time.time()
    if REAL:
        import motor.motor_asyncio
        uri = os.getenv("MONGO_URI", "")
        name = f"ambar_fuzz_{int(time.time())}"
        assert name != "ambar" and name.startswith("ambar_fuzz_")
        opts = {"serverSelectionTimeoutMS": 8000}
        if uri.startswith("mongodb+srv://") or "tls=true" in uri or "ssl=true" in uri:
            import certifi
            opts["tlsCAFile"] = certifi.where()
        client = motor.motor_asyncio.AsyncIOMotorClient(uri, **opts)
        db._db = client[name]
        print(f"настоящая монга, временная база {name}")
    else:
        from mongomock_motor import AsyncMongoMockClient
        db._db = AsyncMongoMockClient()["ambar_fuzz"]
        db.supply_take, db.supply_untake = _take, _untake
    cat = SR._catalog()
    QTY = {pid: float(SR.code_qty(cat[pid])) for pid in NAMES}
    SR._biz_day = lambda *a, **k: DAY
    import owner_routes
    async def _say(key, text, **kw):
        СКАЗАНО.append((key, text)); return []
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    for o in PLAN:
        await db.save_stock_count(o, "2026-09-20", {
            "district": o, "day": "2026-09-20", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": pid, "name": NAMES[pid], "price": 100,
                                         "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                                        for pid, q in COUNT[o].items()]})
    try:
        stats = {"ok_scans": 0, "removed": 0, "done": 0}
        for seed in range(SEEDS):
            # Каждая поставка — в чистом реестре: склад района считает все коды
            # прихода, и чужие поставки прошлых прогонов сбили бы сверку.
            await db._db.qr_codes.delete_many({})
            await db._db.supplies.delete_many({})
            m = await run_seed(seed)
            stats["ok_scans"] += sum(1 for d in m.codes.values() if d["supply"] == m.sid)
            stats["removed"] += len(m.removed)
            stats["done"] += sum(m.done.values())
            if FAILS:
                break
    finally:
        if REAL:
            await client.drop_database(name)
            print(f"временная база {name} стёрта")
    print(f"прогонов {SEEDS} × до {STEPS} шагов · в реестре к концу {stats['ok_scans']} кодов, "
          f"убрано {stats['removed']}, закрыто районов {stats['done']} · {time.time() - t0:.1f} с")
    print("ответы:", ", ".join(f"{k} {v}" for k, v in sorted(ВЕТКИ.items())))
    НАДО = ["скан:taken", "скан:known:своя-другая", "скан:known:своя-та-же", "скан:known", "скан:full",
            "скан:busy", "скан:not_mine", "скан:closed", "скан:not_in_supply", "убрать:ok",
            "убрать:not_ours", "убрать:moved", "убрать:busy", "убрать:not_mine", "убрать:closed",
            "без сканирования:ok", "закрыть:ok"]
    нет = [k for k in НАДО if k not in ВЕТКИ]
    if нет and SEEDS >= 20:
        print("  FAIL не пройдены ветки:", нет); FAILS.append(("покрытие", 0, нет))
    print("ИТОГ:", "все прошли" if not FAILS else f"провалено {len(FAILS)}")
    return 1 if FAILS else 0


sys.exit(asyncio.run(main()))
