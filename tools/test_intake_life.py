"""Приёмка в бытовых ситуациях: «без сканирования» с ручным подсчётом того,
чего не дали, недовоз и его подтверждение, нашедшиеся бутылки, брошенная и
переданная задача, отмена района, вчерашняя приёмка рядом с сегодняшней
(владелец, 29 сен 2026: «проведи глубокий анализ приёмки на самые разные
сценарии… надо сделать так, чтобы это ничего не сломало»).

Две части.

1. Сценарии по шагам — то, что случается у машины и на полке.
2. Случайные цепочки (скан, убрать, без сканирования, отчёт о недовозе,
   решение старшего, закрытие) и после КАЖДОГО шага — сверка с независимой
   моделью:
     • склад района = пересчёт + коды этой приёмки + лежащее без кодов, где
       «лежащее без кодов» = план − подтверждённый недовоз − принятое;
     • «заказано и не забрано» (его вычитает завтрашняя заявка) — то же число
       у района, который ещё принимают сканом, и ноль у остальных;
     • принятое не выше плана; сумма кодов = принятому;
     • закрытый район не меняется.

Найдено этим прогоном и исправлено 29 сен 2026:
  — район «без сканирования» с подтверждённым недовозом держал на складе
    то, чего магазин не дал;
  — «заказано и не забрано» считало подтверждённый недовоз ждущим на базе —
    завтрашняя заявка его бы не попросила;
  — одна позиция двумя строками в отчёте проходила предел по отдельности.

mongomock: позиционный items.$ бьёт в первый элемент — supply_take/untake
заменены теми же условиями по индексу (как в test_fuzz_intake.py).

    python3 tools/test_intake_life.py
    LIFE_SEEDS=400 LIFE_STEPS=120 python3 tools/test_intake_life.py
"""
import asyncio, os, random, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, supply_routes as sr, stock_routes as SR                # noqa: E402

SEEDS = int(os.getenv("LIFE_SEEDS", "60"))
STEPS = int(os.getenv("LIFE_STEPS", "70"))
DAY = "2026-09-21"
T0 = datetime.now(timezone.utc) - timedelta(hours=2)
PLAN = {"jvc": {"p1": 3, "p2": 2, "p31": 2}, "bbay": {"p1": 2, "p5": 1}}
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr", "p5": "Smirnoff Vodka 1 ltr",
         "p31": "Heineken 0.33 can"}
COUNT = {"jvc": {"p1": 5, "p2": 1, "p31": 3}, "bbay": {"p1": 4, "p5": 2}}
DRV = {"jvc": "Худоба", "bbay": "Авазбек"}
QTY = {}
FAIL = []
N = [0]


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


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


async def add_supply(sid, claimed=True, at=None):
    now = at or datetime.now(timezone.utc)
    await db._db.supplies.insert_one({
        "_id": sid, "status": "open", "at": now, "day": DAY,
        "items": [{"id": pid, "name": NAMES[pid], "qty": sum(PLAN[o].get(pid, 0) for o in PLAN),
                   "by_district": {o: PLAN[o][pid] for o in PLAN if pid in PLAN[o]},
                   "got": {o: 0 for o in PLAN if pid in PLAN[o]}}
                  for pid in sorted({p for o in PLAN for p in PLAN[o]})],
        "tasks": {o: {"driver": DRV[o] if claimed else "", "driver_id": 1,
                      "claimed_at": now if claimed else None, "started_at": None,
                      "noscan_at": None, "done_at": None, "cancelled_at": None, "erev": 0,
                      "scanned": 0} for o in PLAN}})
    SR.base_drop()
    return sid


async def fresh(sid="S1", **kw):
    await db._db.qr_codes.delete_many({})
    await db._db.supplies.delete_many({})
    return await add_supply(sid, **kw)


def code():
    N[0] += 1
    return f"c{N[0]:06d}"


async def unhold(sid, o):
    await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})


async def scan(sid, o, pid, who=None, c=None, owner=False):
    await unhold(sid, o)
    return await sr.task_scan(sid, o, pid, c or code(), who or DRV[o], 1, "", owner)


async def scan_n(sid, o, pid, n, **kw):
    return [(await scan(sid, o, pid, **kw)).get("verdict") for _ in range(n)]


async def scan_all(sid, o):
    for pid, n in PLAN[o].items():
        await scan_n(sid, o, pid, int(n / QTY[pid]))


async def stock(o):
    SR.base_drop()
    base = await SR._district_base(DAY)
    have = (base.get(o) or {}).get("have_exact") or {}
    return {p: float(have.get(p, 0)) for p in sorted(PLAN[o])}


async def task(sid, o):
    s = await db.supply_get(sid)
    return sr._task_view(sid, s, o, s["tasks"][o], DRV[o])


def base(o, **plus):
    return {p: float(COUNT[o].get(p, 0) + plus.get(p, 0)) for p in sorted(PLAN[o])}


# ── 1. сценарии ──────────────────────────────────────────────────────────────
async def scenarios():
    print("\nОбычная приёмка сканом")
    sid = await fresh()
    await scan_all(sid, "jvc")
    eq("лишняя бутылка не принимается", (await scan(sid, "jvc", "p1")).get("verdict"), "full")
    eq("закрыта без недобора", (await sr.task_finish(sid, "jvc", "Худоба")).get("gaps"), [])
    eq("склад вырос на принятое", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    eq("второй раз не закрыть", (await sr.task_finish(sid, "jvc", "Худоба")).get("verdict"), "closed")
    eq("в закрытую не сканируют", (await scan(sid, "jvc", "p1")).get("verdict"), "closed")

    print("\nБез сканирования, всё сошлось")
    sid = await fresh()
    eq("принято без сканирования", (await sr.task_noscan(sid, "jvc", "Худоба")).get("ok"), True)
    eq("товар на складе сразу", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    eq("второй раз отметить нельзя", (await sr.task_noscan(sid, "jvc", "Худоба")).get("verdict"), "already")
    await scan_n(sid, "jvc", "p1", 2)
    eq("досканировали часть — склад тот же", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    await scan_n(sid, "jvc", "p1", 1); await scan_n(sid, "jvc", "p2", 2)
    r = [await scan(sid, "jvc", "p31") for _ in range(4)][-1]
    eq("последняя бутылка закрывает район сама", r.get("finished"), True)
    eq("и склад не изменился", await stock("jvc"), base("jvc", **PLAN["jvc"]))

    print("\nБез сканирования + «чего не дали» (ручной подсчёт у машины)")
    sid = await fresh()
    r = await sr.short_report(sid, "jvc", "Худоба", "driver",
                              [{"id": "p1", "qty": 2}, {"id": "p31", "qty": 0.5}], "не было")
    eq("отчёт принят", r.get("ok"), True)
    eq("и тут же принято без сканирования", (await sr.task_noscan(sid, "jvc", "Худоба")).get("ok"), True)
    eq("пока старший молчит — склад по заявке", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    r = await sr.short_decide(sid, "jvc", True, "STAR")
    eq("старший подтвердил", (r.get("ok"), r.get("finished")), (True, False))
    eq("СКЛАД: того, чего не дали, на полке нет", await stock("jvc"),
       base("jvc", p1=1, p2=2, p31=1.5))
    v = await task(sid, "jvc")
    eq("сканировать осталось только привезённое", (v["need"], v["left"]), (4.5, 4.5))
    await scan_n(sid, "jvc", "p1", 1); await scan_n(sid, "jvc", "p2", 2)
    r = [await scan(sid, "jvc", "p31") for _ in range(3)][-1]
    eq("досканировал привезённое — район закрылся сам", r.get("finished"), True)
    eq("склад после закрытия тот же", await stock("jvc"), base("jvc", p1=1, p2=2, p31=1.5))
    s = await db.supply_get(sid)
    eq("недобор магазину — по заявке, а не по урезанному",
       {g["id"]: g["gap"] for g in s["tasks"]["jvc"]["gaps"]}, {"p1": 2, "p31": 0.5})

    print("\nСтарший отклонил недовоз")
    sid = await fresh()
    await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 2}], "")
    await sr.task_noscan(sid, "jvc", "Худоба")
    r = await sr.short_decide(sid, "jvc", False, "STAR")
    eq("отклонён", (r.get("ok"), (r["task"]["short"] or {}).get("status")), (True, "no"))
    eq("план не тронут", (await task(sid, "jvc"))["need"], 7)
    eq("склад по заявке", await stock("jvc"), base("jvc", **PLAN["jvc"]))
    eq("решить второй раз нельзя", (await sr.short_decide(sid, "jvc", True, "STAR")).get("verdict"),
       "not_pending")
    r = await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 1}], "точно нет")
    eq("после отказа можно прислать снова", r.get("ok"), True)

    print("\nНедовоз подтверждён, а бутылки нашлись")
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 1)
    await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 2}], "")
    await sr.short_decide(sid, "jvc", True, "STAR")
    eq("нашлись — сканируются", await scan_n(sid, "jvc", "p1", 2), ["taken", "taken"])
    eq("но не больше заявки", (await scan(sid, "jvc", "p1")).get("verdict"), "full")
    v = await task(sid, "jvc")
    eq("строка: осталось 0, хоть принято больше «нужного»",
       next((l["got"], l["left"]) for l in v["lines"] if l["id"] == "p1"), (3, 0))
    eq("склад — по кодам", (await stock("jvc"))["p1"], 8.0)

    print("\nСканом + недовоз: «заказано и не забрано» для завтрашней заявки")
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 1)
    eq("до отчёта ждёт всё невзятое", (await sr.pending_qty()).get(("jvc", "p1")), 2.0)
    await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 2}], "")
    eq("отчёт без решения ничего не меняет", (await sr.pending_qty()).get(("jvc", "p1")), 2.0)
    await sr.short_decide(sid, "jvc", True, "STAR")
    eq("подтверждённого недовоза на базе НЕТ", (await sr.pending_qty()).get(("jvc", "p1")), None)
    eq("остальное по-прежнему ждёт", (await sr.pending_qty()).get(("jvc", "p2")), 2.0)

    print("\nПодтверждение закрывает район, если остальное принято")
    sid = await fresh()
    await scan_n(sid, "jvc", "p2", 2); await scan_n(sid, "jvc", "p31", 4); await scan_n(sid, "jvc", "p1", 1)
    await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 2}], "")
    r = await sr.short_decide(sid, "jvc", True, "STAR")
    eq("закрылся сам", (r.get("finished"), bool(r["task"]["done_at"])), (True, True))
    eq("склад — что приняли", await stock("jvc"), base("jvc", p1=1, p2=2, p31=2))

    print("\nОтчёт висит без решения")
    sid = await fresh()
    await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p2", "qty": 1}], "")
    eq("второй, пока первый ждёт, не ложится",
       (await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 1}], "")).get("verdict"),
       "pending")
    eq("сканировать это не мешает", (await scan(sid, "jvc", "p1")).get("verdict"), "taken")
    eq("и убирать", (await sr.task_undo(sid, "jvc", f"c{N[0]:06d}", "Худоба")).get("ok"), True)

    print("\nСтранные числа в отчёте")
    for lines, ждём in (([{"id": "p1", "qty": -3}], "empty"), ([{"id": "p1", "qty": "много"}], "empty"),
                        ([{"id": "нет", "qty": 1}], "empty"), ([{"id": "p1", "qty": 0.2}], "empty"),
                        ([], "empty"), (None, "empty"), (["p1"], "empty"), ([None], "empty")):
        sid = await fresh()
        eq(f"{lines!r}", (await sr.short_report(sid, "jvc", "Худоба", "driver", lines, "")).get("verdict"), ждём)
    sid = await fresh()
    r = await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 99}], "")
    eq("больше плана — режется до плана", r["task"]["short"]["lines"][0]["qty"], 3)
    sid = await fresh()
    r = await sr.short_report(sid, "jvc", "Худоба", "driver",
                              [{"id": "p1", "qty": 2}, {"id": "p1", "qty": 2}], "")
    eq("позиция двумя строками — одна строка, не выше плана",
       [(x["id"], x["qty"]) for x in r["task"]["short"]["lines"]], [("p1", 3)])
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 2)
    r = await sr.short_report(sid, "jvc", "Худоба", "driver", [{"id": "p1", "qty": 3}], "")
    eq("не дать можно только то, что ещё не принято", r["task"]["short"]["lines"][0]["qty"], 1)

    print("\nЧужие руки")
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 1)
    eq("напарник в чужую не сканирует", (await scan(sid, "jvc", "p1", who="Фарух")).get("verdict"), "not_mine")
    eq("старший в чужую (идёт скан) — тоже",
       (await scan(sid, "jvc", "p1", who="STAR", owner=True)).get("verdict"), "not_mine")
    eq("и не закрывает её", (await sr.task_finish(sid, "jvc", "STAR", "", owner=True)).get("verdict"), "not_mine")
    eq("старший отмечает «без сканирования»", (await sr.task_noscan(sid, "jvc", "STAR", owner=True)).get("ok"), True)
    eq("и после этого вносит с полки", (await scan(sid, "jvc", "p1", who="STAR", owner=True)).get("verdict"), "taken")

    print("\nЗамок камеры")
    sid = await fresh()
    await sr.task_noscan(sid, "jvc", "Худоба")
    eq("водитель открыл камеру", (await sr.task_hold(sid, "jvc", "Худоба", 1, True)).get("ok"), True)
    r = await sr.task_scan(sid, "jvc", "p1", code(), "STAR", 1, "", True)
    eq("старшему — «занято», и сказано кем", (r.get("verdict"), r.get("by")), ("busy", "Худоба"))
    await db._db.supplies.update_one({"_id": sid}, {"$set": {
        "tasks.jvc.hold.until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    r = await sr.task_scan(sid, "jvc", "p1", code(), "STAR", 1, "", True)
    eq("телефон сел — через 40 с замок отпал", r.get("verdict"), "taken")

    print("\nЗадачу отдали посреди приёмки")
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 2)
    eq("отдал", await db.supply_task_release(sid, "jvc", "Худоба"), True)
    eq("принятое осталось", (await task(sid, "jvc"))["got"], 2)
    ok, _ = await db.supply_task_claim(sid, "jvc", "Фарух", 2, datetime.now(timezone.utc))
    eq("напарник взял", ok, True)
    eq("и продолжил", (await scan(sid, "jvc", "p1", who="Фарух")).get("verdict"), "taken")
    eq("прежний больше не сканирует", (await scan(sid, "jvc", "p2")).get("verdict"), "not_mine")
    eq("склад — все три бутылки", (await stock("jvc"))["p1"], 8.0)

    print("\nРайон отменили посреди приёмки")
    sid = await fresh()
    await scan_n(sid, "jvc", "p1", 2)
    eq("отменён", await db.supply_task_cancel(sid, "jvc", "STAR", datetime.now(timezone.utc).isoformat()), True)
    eq("скан", (await scan(sid, "jvc", "p1")).get("verdict"), "cancelled")
    eq("без сканирования", (await sr.task_noscan(sid, "jvc", "Худоба")).get("verdict"), "cancelled")
    eq("отчёт о недовозе", (await sr.short_report(sid, "jvc", "Худоба", "driver",
                                                   [{"id": "p1", "qty": 1}], "")).get("verdict"), "closed")
    eq("принятое до отмены стоит на складе", (await stock("jvc"))["p1"], 7.0)
    eq("и в завтрашнюю заявку отменённое не вычитается", (await sr.pending_qty()).get(("jvc", "p2")), None)

    print("\nВчерашняя приёмка рядом с сегодняшней")
    await fresh("OLD", at=datetime.now(timezone.utc) - timedelta(days=1))
    await scan_n("OLD", "jvc", "p1", 1)
    await add_supply("NEW")
    t = await sr.tasks_for_driver("Худоба", "jvc")
    eq("видны обе, вчерашняя помечена", sorted((x["supply_id"], x["stale"]) for x in t["mine"]),
       [("NEW", False), ("OLD", True)])
    c = code()
    eq("код во вчерашнюю", (await scan("OLD", "jvc", "p2", c=c)).get("verdict"), "taken")
    r = await scan("NEW", "jvc", "p2", c=c)
    eq("тот же код в сегодняшнюю — отказ, и это не «наша»", (r.get("verdict"), r.get("ours")), ("known", False))
    eq("убрать его из сегодняшней нельзя", (await sr.task_undo("NEW", "jvc", c, "Худоба")).get("verdict"), "not_ours")

    print("\nПроданная бутылка этой приёмки")
    sid = await fresh()
    c = code(); await scan(sid, "jvc", "p1", c=c)
    await db._db.qr_codes.update_one({"_id": c}, {"$set": {"status": "sold"}})
    eq("убрать нельзя", (await sr.task_undo(sid, "jvc", c, "Худоба")).get("verdict"), "moved")
    eq("строка её помнит", next(l["got"] for l in (await task(sid, "jvc"))["lines"] if l["id"] == "p1"), 1)


# ── 2. случайные цепочки ─────────────────────────────────────────────────────
class M:
    def __init__(self):
        self.got = {o: {p: 0.0 for p in PLAN[o]} for o in PLAN}
        self.miss = {o: {p: 0.0 for p in PLAN[o]} for o in PLAN}
        self.pend = {o: None for o in PLAN}          # строки ждущего отчёта
        self.noscan = {o: False for o in PLAN}
        self.done = {o: False for o in PLAN}
        self.codes = {o: [] for o in PLAN}           # (код, позиция)

    def need_eff(self, o, p):
        return max(0.0, PLAN[o][p] - self.miss[o][p])

    def left(self, o):
        return sum(max(0.0, self.need_eff(o, p) - self.got[o][p]) for p in PLAN[o])


async def check(m, sid, seed, step, trail):
    def bad(what, got, want):
        FAIL.append((seed, step, what))
        print(f"  FAIL seed={seed} шаг={step}: {what}\n       получили {got!r}\n       ждали    {want!r}")
        for t in trail[-10:]:
            print("       …", t)
    s = await db.supply_get(sid)
    pend = await sr.pending_qty()
    for o in PLAN:
        t = s["tasks"][o]
        if bool(t.get("done_at")) != m.done[o]:
            bad(f"{o}: закрыт", bool(t.get("done_at")), m.done[o])
        if bool(t.get("noscan_at")) != m.noscan[o]:
            bad(f"{o}: без сканирования", bool(t.get("noscan_at")), m.noscan[o])
        st = await stock(o)
        for it in s["items"]:
            p = it["id"]
            if p not in PLAN[o]:
                continue
            got = float((it.get("got") or {}).get(o) or 0)
            if abs(got - m.got[o][p]) > 1e-9:
                bad(f"{o}/{p}: принято", got, m.got[o][p])
            if got > PLAN[o][p] + 1e-9:
                bad(f"{o}/{p}: принято больше плана", got, PLAN[o][p])
            if abs(sr._miss(t, p) - m.miss[o][p]) > 1e-9:
                bad(f"{o}/{p}: недовоз", sr._miss(t, p), m.miss[o][p])
            rem = max(0.0, m.need_eff(o, p) - m.got[o][p])
            want = COUNT[o].get(p, 0) + m.got[o][p] + (rem if m.noscan[o] and not m.done[o] else 0)
            if abs(st[p] - want) > 1e-9:
                bad(f"склад {o}/{p}", st[p], want)
            wp = rem if (not m.noscan[o] and not m.done[o]) else 0
            gp = float(pend.get((o, p)) or 0)
            if abs(gp - wp) > 1e-9:
                bad(f"заказано и не забрано {o}/{p}", gp, wp)
        codes = await db._db.qr_codes.find({"supply_id": sid, "origin": o}).to_list(length=None)
        по = {}
        for c in codes:
            по[c["product_id"]] = по.get(c["product_id"], 0) + float(c.get("qty") or 1)
        for p in PLAN[o]:
            if abs(по.get(p, 0) - m.got[o][p]) > 1e-9:
                bad(f"реестр {o}/{p}", по.get(p, 0), m.got[o][p])
    if (s["status"] == "done") != all(m.done.values()):
        bad("поставка закрыта", s["status"], all(m.done.values()))


async def run_seed(seed):
    rnd = random.Random(seed)
    sid = await fresh(f"L{seed:04d}")
    m = M()
    trail = []
    for step in range(STEPS):
        if all(m.done.values()):
            break
        o = rnd.choice(list(PLAN)); who = DRV[o]
        a = rnd.random()
        if a < 0.42:
            p = rnd.choice(list(PLAN[o]))
            r = await scan(sid, o, p)
            v = r.get("verdict")
            trail.append(f"скан {o} {p} → {v}")
            want = ("closed" if m.done[o] else
                    "full" if m.got[o][p] + QTY[p] > PLAN[o][p] + 1e-9 else "taken")
            if v != want:
                FAIL.append((seed, step, "скан")); print(f"  FAIL seed={seed} шаг={step}: скан {v} ≠ {want}", trail[-6:]); break
            if v == "taken":
                m.got[o][p] += QTY[p]; m.codes[o].append((r["code"], p))
                if m.noscan[o] and m.left(o) <= 1e-9:
                    m.done[o] = True
                if bool(r.get("finished")) != m.done[o]:
                    FAIL.append((seed, step, "автозакрытие")); print(f"  FAIL seed={seed} шаг={step}: автозакрытие {r.get('finished')}", trail[-6:]); break
        elif a < 0.55 and m.codes[o]:
            c, p = rnd.choice(m.codes[o])
            await unhold(sid, o)
            r = await sr.task_undo(sid, o, c, who)
            trail.append(f"убрать {o} {p} → {r.get('verdict') or 'ok'}")
            if m.done[o]:
                if r.get("ok"):
                    FAIL.append((seed, step, "убрали из закрытого")); break
            elif r.get("ok"):
                m.got[o][p] -= QTY[p]; m.codes[o].remove((c, p))
            else:
                FAIL.append((seed, step, "убрать")); print(f"  FAIL seed={seed}: убрать {r}", trail[-6:]); break
        elif a < 0.63:
            r = await sr.task_noscan(sid, o, who)
            trail.append(f"без сканирования {o} → {r.get('verdict') or 'ok'}")
            want_ok = not m.done[o] and not m.noscan[o]
            if bool(r.get("ok")) != want_ok:
                FAIL.append((seed, step, "без сканирования")); print("  FAIL без сканирования", r.get("verdict"), trail[-6:]); break
            if want_ok:
                m.noscan[o] = True
        elif a < 0.80:
            ps = rnd.sample(list(PLAN[o]), rnd.randint(1, len(PLAN[o])))
            lines = [{"id": p, "qty": rnd.choice([0.5, 1, 1, 2, 3, 9])} for p in ps]
            r = await sr.short_report(sid, o, who, "driver", lines, "")
            trail.append(f"недовоз {o} {lines} → {r.get('verdict') or 'ok'}")
            exp = []
            for l in lines:
                p = l["id"]
                q = min(l["qty"], max(0.0, m.need_eff(o, p) - m.got[o][p]))
                q = round(q * 2) / 2
                if q > 0:
                    exp.append((p, q))
            want = ("closed" if m.done[o] else "pending" if m.pend[o] is not None
                    else "empty" if not exp else None)
            if (r.get("verdict") if not r.get("ok") else None) != want:
                FAIL.append((seed, step, "недовоз")); print(f"  FAIL seed={seed} шаг={step}: недовоз {r.get('verdict')} ≠ {want}", trail[-6:]); break
            if r.get("ok"):
                m.pend[o] = exp
                дали = sorted((x["id"], float(x["qty"])) for x in r["task"]["short"]["lines"])
                if дали != sorted((p, float(q)) for p, q in exp):
                    FAIL.append((seed, step, "строки отчёта")); print("  FAIL строки отчёта", дали, exp); break
        elif a < 0.93:
            ok = rnd.random() < 0.7
            r = await sr.short_decide(sid, o, ok, "STAR")
            trail.append(f"решение {o} {'да' if ok else 'нет'} → {r.get('verdict') or 'ok'}")
            if bool(r.get("ok")) != (m.pend[o] is not None):
                FAIL.append((seed, step, "решение")); print("  FAIL решение", r.get("verdict"), trail[-6:]); break
            if r.get("ok"):
                if ok:
                    for p, q in m.pend[o]:
                        m.miss[o][p] += q
                    if not m.done[o] and m.left(o) <= 1e-9:
                        m.done[o] = True
                m.pend[o] = None
        else:
            r = await sr.task_finish(sid, o, who)
            trail.append(f"закрыть {o} → {r.get('verdict') or 'ok'}")
            if bool(r.get("ok")) != (not m.done[o]):
                FAIL.append((seed, step, "закрыть")); break
            m.done[o] = True
        await check(m, sid, seed, step, trail)
        if FAIL:
            break


async def main():
    global QTY
    t0 = time.time()
    db._db = AsyncMongoMockClient()["ambar_life"]
    db.supply_take, db.supply_untake = _take, _untake
    cat = SR._catalog()
    QTY = {pid: float(SR.code_qty(cat[pid])) for pid in NAMES}
    SR._biz_day = lambda *a, **k: DAY
    import owner_routes
    async def _say(key, text, **kw):
        return []
    owner_routes.notify_owners = _say
    owner_routes.notify_owners_force = _say
    for o in COUNT:
        await db.save_stock_count(o, "2026-09-20", {
            "district": o, "day": "2026-09-20", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": pid, "name": NAMES[pid], "price": 100,
                                         "unit": SR._unit(cat[pid]), "actual": q, "counted": True}
                                        for pid, q in COUNT[o].items()]})
    await scenarios()
    print(f"\nСлучайные цепочки: {SEEDS} × до {STEPS} шагов")
    for seed in range(SEEDS):
        await run_seed(seed)
        if FAIL:
            break
    print(f"  {'ok  ' if not FAIL else 'FAIL'} сверка с моделью после каждого шага · {time.time() - t0:.1f} с")
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL[:5]}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
