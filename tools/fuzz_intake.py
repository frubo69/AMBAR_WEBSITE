"""Приёмка товара на случайных цепочках — весь путь бутылки, от ответа магазина
до полки.

Владелец, 1 окт 2026: «что требует глубочайших тестов» — четвёртым пунктом
приёмка. Старый фаззер (test_fuzz_intake.py) гоняет одну поставку: скан,
«убрать», замки. Здесь — всё, что появилось после: ответ магазина и его
повторная загрузка, черновик докупки, заявки на другие базы и их цепочка
(«нет в наличии» → район закрыт → следующая доп. заявка), приёмка без
сканирования со снимком чека, недовоз и решение старшего, отмена района и
заявки, «снять с водителя», новый день рядом со вчерашней приёмкой.

Случайная цепочка идёт через настоящие функции и ручки на базе в памяти.
Рядом — вторая модель: что должно ответить каждое действие и где после него
лежит каждая единица. После КАЖДОГО шага:

  • ответ сервера = ответ модели; документы поставок = модель (статус, план,
    кто взял, принято, недовоз, «нет в наличии», отчёт, недобор при закрытии);
  • реестр: сумма кодов приёмки = принятому по строке, принято не выше плана;
  • склад района = пересчёт + коды + принятое без кодов;
  • «заказано и не забрано» = то, что правда ждёт на базе;
  • СОХРАНЕНИЕ: по каждому району и позиции
        заказано = принято + лежит без кодов + ждёт на базе + не дали —
    слагаемые берутся из трёх разных мест сервера (строки поставки, склад,
    заявка), и ни одна единица не считается дважды и не пропадает;
  • ЦЕПОЧКА: недобор закрытого района доп. заявки целиком лежит в следующих
    доп. заявках и ровно один раз; живые черновики не просят больше, чем
    осталось непокрытым; непокрытый недобор виден на экране (qty_left).

    python3 tools/fuzz_intake.py                    # 200 цепочек по 140 шагов
    python3 tools/fuzz_intake.py --runs 5000 --steps 220 --seed 7
"""
import argparse, asyncio, io, json, math, os, random, sys, time
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from openpyxl import Workbook                                      # noqa: E402
import db, supply_routes as sr, stock_routes as SR                 # noqa: E402

ОФИСЫ = ["jvc", "bbay", "silicon"]
ВОД = {"jvc": "Первый", "bbay": "Второй", "silicon": "Третий"}     # выдуманные
ТОВАР = ["p1", "p2", "p5", "p31"]                                  # p31 — пиво, код = полкоробки
СЧЁТ = {"jvc": {"p1": 5, "p2": 1, "p31": 3}, "bbay": {"p1": 4, "p5": 2}, "silicon": {"p2": 2}}
КАДР = b"\xff\xd8" + b"x" * 60
T0 = datetime.now(timezone.utc) - timedelta(hours=3)
БЕДЫ = []
ВЕТКИ = {}
МИР = {"day": "2026-10-01", "ask": {"asked": {}, "by": {}}}
QTY, ИМЯ = {}, {}
HEAD = ["№", "Item", "Price, AED", "B1 JVC", "B2 Business Bay", "B3 Silicon Oasis",
        "B4 Al Qusais", "B5 Tecom", "Total", "Amount, AED"]


def ветка(k):
    ВЕТКИ[k] = ВЕТКИ.get(k, 0) + 1


def беда(seed, шаг, что, дали=None, ждали=None, след=()):
    БЕДЫ.append((seed, шаг, что, дали, ждали))
    if len(БЕДЫ) <= 3 and not МИР.get("тихо"):
        print(f"  БЕДА зерно={seed} шаг={шаг}: {что}\n       получили {дали!r}\n       ждали    {ждали!r}")
        for t in list(след)[-12:]:
            print("       …", t)


def unwrap(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


# ── запросы к ручкам старшего ────────────────────────────────────────────────
class _Поле:
    name = "file"
    def __init__(self, raw): self.raw = raw
    async def read(self, decode=False): return self.raw


class _Читатель:
    def __init__(self, raw): self.left = [_Поле(raw)]
    async def next(self): return self.left.pop() if self.left else None


class Зап(dict):
    def __init__(self, body=None, sid="", raw=b""):
        super().__init__(owner_id=1)
        self._b, self.match_info, self.query, self.raw = body or {}, {"sid": sid}, {}, raw
    async def json(self): return self._b
    async def multipart(self): return _Читатель(self.raw)


async def ручка(h, body=None, sid="", raw=b""):
    r = await unwrap(h)(Зап(body, sid, raw))
    return r.status, json.loads(r.text)


def книга(rows) -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = "Order"
    ws.append([""] + HEAD)
    for r in rows:
        ws.append([""] + list(r))
    b = io.BytesIO(); wb.save(b); return b.getvalue()


# ── mongomock: позиционный $ по индексу (как в test_fuzz_intake.py) ──────────
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


async def _тихо(*a, **k):
    return []


def подготовь():
    """Один раз на процесс: заглушки сообщений и то, чего нет у базы в памяти."""
    import owner_routes, pay_notify
    owner_routes.notify_owners = _тихо
    owner_routes.notify_owners_force = _тихо
    owner_routes.notify_owners_photo = _тихо
    pay_notify.tell_safe = _тихо
    sr._cancel_tell = _тихо
    sr._answer_to_order = _тихо
    sr._buy_note = lambda *a, **k: None
    db.supply_take, db.supply_untake = _take, _untake
    async def _снимок():
        return МИР["ask"]
    db.zayavka_last_full = _снимок
    SR._biz_day = lambda *a, **k: МИР["day"]
    cat = SR._catalog()
    for p in ТОВАР:
        QTY[p] = float(SR.code_qty(cat[p])); ИМЯ[p] = cat[p]["name"]
    assert QTY["p31"] == 0.5 and QTY["p1"] == 1.0


# ── модель ───────────────────────────────────────────────────────────────────
def вверх(need, got):
    """Сколько осталось — до полкоробки вверх."""
    return max(0.0, math.ceil(round(float(need) - float(got), 6) * 2) / 2)


def целых(g):
    """Недобор для следующей базы — целыми, половина вверх."""
    return int(float(g) + 0.5)


class Район:
    def __init__(self):
        self.driver = ""; self.got = {}; self.miss = {}; self.na = {}
        self.pend = None; self.noscan = False; self.done = False; self.cancelled = False
        self.gaps = {}; self.codes = []


class Заявка:
    def __init__(self, sid, kind, status, day, parent="", base=""):
        self.sid, self.kind, self.status, self.day = sid, kind, status, day
        self.parent, self.base = parent, base
        self.plan = {}            # район → позиция → сколько
        self.items = []           # позиции в составе (порядок документа)
        self.tasks = {}           # район → Район
        self.гап = {}             # недобор по ответу магазина: позиция → район → сколько
        self.цены = {}
        self.был_открыт = status == "open"
        self.авто = False         # собрана системой из недобора

    def need(self, o, p):
        return int((self.plan.get(o) or {}).get(p) or 0)

    def need_eff(self, o, p):
        t = self.tasks[o]
        return max(0.0, self.need(o, p) - float(t.miss.get(p) or 0) - float(t.na.get(p) or 0))

    def got(self, o, p):
        return float(self.tasks[o].got.get(p) or 0)

    def left(self, o):
        return sum(max(0.0, self.need_eff(o, p) - self.got(o, p)) for p in self.items)

    def цен_нет(self, o):
        if self.kind != "extra":
            return False
        t = self.tasks[o]
        return any(self.need(o, p) > 0 and not t.na.get(p) and float(self.цены.get(p) or 0) <= 0
                   for p in self.items)

    def трогать(self, o, who, owner):
        t = self.tasks.get(o)
        return bool(t) and (t.driver == who or bool(owner and t.noscan))


class Мир:
    def __init__(self, seed):
        self.seed = seed
        self.з = {}                # номер → Заявка
        self.n = 0
        self.след = []
        self.дней = 0

    def код(self):
        self.n += 1
        return f"q{self.seed}-{self.n:05d}"

    def открытые(self):
        return [z for z in self.з.values() if z.status == "open"]

    def дети(self, sid, живые=True):
        """Заявки из недобора этой. Живые — все, кроме черновика, отменённого
        до отправки: отменённая ПОСЛЕ отправки свой недобор несёт сама (он на
        её экране), а за отменённым черновиком никто не поехал."""
        return [z for z in self.з.values() if z.parent == sid
                and (not живые or z.status != "cancelled" or z.был_открыт)]

    def недобор(self, z):
        """Недобор заявки: позиция → район → единиц (целыми, как его несут на
        другую базу): ответ магазина, закрытые с недобором и отменённые районы."""
        out = {}
        def put(p, o, n):
            if n > 0:
                out.setdefault(p, {}); out[p][o] = out[p].get(o, 0) + n
        for p, by in z.гап.items():
            for o, n in by.items():
                put(p, o, int(n))
        for o, t in z.tasks.items():
            if t.done:
                for p, g in t.gaps.items():
                    put(p, o, целых(g))
            elif t.cancelled:
                for p in z.items:
                    put(p, o, целых(вверх(z.need(o, p), z.got(o, p))))
        return out

    # закрытие района — одно правило для всех дверей
    def закрой(self, z, o, новые):
        t = z.tasks[o]
        t.done = True
        t.gaps = {}
        for p in z.items:
            if z.need(o, p):
                g = вверх(z.need(o, p), z.got(o, p))
                if g > 0:
                    t.gaps[p] = g
        if all(x.done or x.cancelled for x in z.tasks.values()):
            z.status = "done"
        if z.kind == "extra" and t.gaps:
            строки = {p: целых(g) for p, g in t.gaps.items() if целых(g) > 0}
            if строки:
                ч = next((c for c in self.з.values() if c.parent == z.sid and c.status == "draft"), None)
                if not ч:
                    ч = Заявка("?%d" % len(новые), "extra", "draft", z.day, parent=z.sid)
                    ч.авто = True
                    новые.append(ч)
                    ветка("цепочка:следующая доп. заявка")
                    if self.з.get(z.parent) and self.з[z.parent].kind == "extra":
                        ветка("цепочка:третья база")
                else:
                    ветка("цепочка:второй район в тот же черновик")
                for p, n in строки.items():
                    if p not in ч.items:
                        ч.items.append(p)
                    ч.plan.setdefault(o, {})[p] = n
                # задачи черновика собираются заново из состава
                ч.tasks = {x: Район() for x in ч.plan if any(ч.plan[x].values())}


# ── сверка ───────────────────────────────────────────────────────────────────
async def сверка(м, шаг):
    seed, след = м.seed, м.след
    docs = {d["_id"]: d for d in await db._db.supplies.find({}).to_list(length=None)}
    if set(docs) != set(м.з):
        беда(seed, шаг, "набор поставок в базе ≠ модели", sorted(docs), sorted(м.з), след); return
    коды = {}
    for c in await db._db.qr_codes.find({}).to_list(length=None):
        k = (c.get("supply_id"), c.get("origin") or c.get("district"), c.get("product_id"))
        v = коды.setdefault(k, [0.0, 0]); v[0] += float(c.get("qty") or 1); v[1] += 1
    for sid, z in м.з.items():
        d = docs[sid]
        if (d.get("status"), d.get("kind") or "main", d.get("from_supply") or "") != (z.status, z.kind, z.parent):
            беда(seed, шаг, f"{sid}: статус/вид/родитель",
                 (d.get("status"), d.get("kind") or "main", d.get("from_supply") or ""),
                 (z.status, z.kind, z.parent), след)
        план = {}
        for it in d.get("items") or []:
            for o, n in (it.get("by_district") or {}).items():
                if int(n or 0):
                    план.setdefault(o, {})[it["id"]] = int(n)
        want = {o: {p: n for p, n in by.items() if n} for o, by in z.plan.items()}
        want = {o: by for o, by in want.items() if by}
        if план != want:
            беда(seed, шаг, f"{sid}: состав по районам", план, want, след)
        if sorted(d.get("tasks") or {}) != sorted(z.tasks):
            беда(seed, шаг, f"{sid}: районы", sorted(d.get("tasks") or {}), sorted(z.tasks), след)
            continue
        for o, t in z.tasks.items():
            dt = d["tasks"][o]
            вид = (dt.get("driver") or "", bool(dt.get("done_at")), bool(dt.get("cancelled_at")),
                   bool(dt.get("noscan_at")))
            if вид != (t.driver, t.done, t.cancelled, t.noscan):
                беда(seed, шаг, f"{sid}/{o}: водитель/закрыт/отменён/без сканирования", вид,
                     (t.driver, t.done, t.cancelled, t.noscan), след)
            ждёт = (dt.get("short") or {}).get("status") == "pending"
            if ждёт != (t.pend is not None):
                беда(seed, шаг, f"{sid}/{o}: отчёт о недовозе ждёт", ждёт, t.pend is not None, след)
            elif ждёт:
                дали = sorted((x["id"], float(x["qty"])) for x in dt["short"]["lines"])
                if дали != sorted(t.pend):
                    беда(seed, шаг, f"{sid}/{o}: строки отчёта", дали, sorted(t.pend), след)
            n_codes = 0
            for it in d.get("items") or []:
                p = it["id"]
                got = float((it.get("got") or {}).get(o) or 0)
                if abs(got - float(t.got.get(p) or 0)) > 1e-9:
                    беда(seed, шаг, f"{sid}/{o}/{p}: принято", got, t.got.get(p) or 0, след)
                if got > z.need(o, p) + 1e-9:
                    беда(seed, шаг, f"{sid}/{o}/{p}: принято больше плана", got, z.need(o, p), след)
                рг = коды.get((sid, o, p)) or [0.0, 0]
                n_codes += рг[1]
                if abs(рг[0] - got) > 1e-9:
                    беда(seed, шаг, f"{sid}/{o}/{p}: сумма кодов ≠ принятому", рг[0], got, след)
                for поле, мод in (("miss", t.miss), ("na", t.na)):
                    v = float((dt.get(поле) or {}).get(p) or 0)
                    if abs(v - float(мод.get(p) or 0)) > 1e-9:
                        беда(seed, шаг, f"{sid}/{o}/{p}: {поле}", v, мод.get(p) or 0, след)
            if int(dt.get("scanned") or 0) != n_codes:
                беда(seed, шаг, f"{sid}/{o}: scanned ≠ числу кодов", dt.get("scanned"), n_codes, след)
            if t.done:
                gaps = {g["id"]: float(g["gap"]) for g in dt.get("gaps") or []}
                if gaps != {p: float(g) for p, g in t.gaps.items()}:
                    беда(seed, шаг, f"{sid}/{o}: недобор при закрытии", gaps, t.gaps, след)

    # ── склад, «ждёт на базе», сохранение ────────────────────────────────────
    основа = await SR._district_base(МИР["day"])
    ждёт_срв = await sr.pending_qty()
    без_кодов = await SR._noscan_after({})
    for o in ОФИСЫ:
        have = (основа.get(o) or {}).get("have_exact") or {}
        for p in ТОВАР:
            принято = полка = ждёт = не_дали = заказано = 0.0
            for z in м.з.values():
                t = z.tasks.get(o)
                if not t or not z.был_открыт or not z.need(o, p):
                    if t and z.был_открыт:
                        принято += z.got(o, p)
                    continue
                заказано += z.need(o, p)
                got = z.got(o, p); принято += got
                rem = max(0.0, z.need_eff(o, p) - got)
                if t.done:
                    не_дали += float(t.gaps.get(p) or 0)
                elif t.cancelled or z.status != "open":
                    не_дали += z.need(o, p) - got
                else:
                    не_дали += min(z.need(o, p) - got, float(t.miss.get(p) or 0) + float(t.na.get(p) or 0))
                    if t.noscan:
                        полка += rem
                    else:
                        ждёт += rem
            want = СЧЁТ.get(o, {}).get(p, 0) + принято + полка
            if abs(float(have.get(p, 0)) - want) > 1e-9:
                беда(seed, шаг, f"склад {o}/{p}", float(have.get(p, 0)), want, след)
            if abs(float(ждёт_срв.get((o, p)) or 0) - ждёт) > 1e-9:
                беда(seed, шаг, f"заказано и не забрано {o}/{p}", ждёт_срв.get((o, p)) or 0, ждёт, след)
            # Сохранение — слагаемые сервера, не модели.
            срв = (float(have.get(p, 0)) - СЧЁТ.get(o, {}).get(p, 0)      # принято + без кодов
                   + float(ждёт_срв.get((o, p)) or 0) + не_дали)
            if abs(срв - заказано) > 1e-9:
                беда(seed, шаг, f"сохранение {o}/{p}: принято+полка+ждёт+не дали ≠ заказано",
                     срв, заказано, след)
            if abs(float((без_кодов.get(o) or {}).get(p) or 0) - полка) > 1e-9:
                беда(seed, шаг, f"без кодов на полке {o}/{p}", (без_кодов.get(o) or {}).get(p) or 0, полка, след)

    # ── что видят люди ───────────────────────────────────────────────────────
    # Водитель: каждая задача, по которой что-то принято (кодами или без них),
    # есть в его приложении ровно раз, пока район не закрыт, — чьей бы она ни
    # была и какой бы давности ни была заявка.
    o = ОФИСЫ[шаг % len(ОФИСЫ)]
    вид = await sr.tasks_for_driver(ВОД[o], o) if шаг % 3 == 0 else None
    видно = {}
    for k in ("mine", "free", "extra", "taken"):
        for v in (вид or {}).get(k) or []:
            видно[(v["supply_id"], v["district"])] = видно.get((v["supply_id"], v["district"]), 0) + 1
    if any(n != 1 for n in видно.values()):
        беда(seed, шаг, "задача показана водителю дважды", видно, "по одному разу", след)
    for sid, z in м.з.items() if вид is not None else ():
        for x, t in z.tasks.items():
            живая = z.status == "open" and not t.done and not t.cancelled
            if живая and (t.codes or t.noscan) and (sid, x) not in видно:
                беда(seed, шаг, f"{sid}/{x}: приёмка с принятым товаром пропала из приложения водителя",
                     sorted(видно), (sid, x), след)
            if not живая and (sid, x) in видно:
                беда(seed, шаг, f"{sid}/{x}: закрытая или отменённая задача видна водителю", True, False, след)
    # Старший: долг «принято без кодов» — ровно те районы, где он есть.
    долг = sorted((x["supply_id"], x["district"], float(x["left"])) for x in await sr.noscan_tasks())
    want = sorted((sid, x, sum(вверх(z.need_eff(x, p), z.got(x, p)) for p in z.items if z.need(x, p)))
                  for sid, z in м.з.items() if z.status == "open"
                  for x, t in z.tasks.items() if t.noscan and not t.done)
    want = [w for w in want if w[2] > 0]
    if долг != want:
        беда(seed, шаг, "долг «принято без кодов» у старшего", долг, want, след)

    # ── цепочка докупок ──────────────────────────────────────────────────────
    for sid, z in м.з.items():
        if not z.был_открыт:
            continue
        нед = м.недобор(z)
        живые = м.дети(sid)
        по_поз = {}
        for c in живые:
            for o, by in c.plan.items():
                for p, n in by.items():
                    v = по_поз.setdefault(p, [0, 0]); v[0 if c.status == "draft" else 1] += n
        for p in set(нед) | set(по_поз):
            G = sum((нед.get(p) or {}).values())
            D, C = по_поз.get(p, [0, 0])
            if D > max(0, G - C):
                беда(seed, шаг, f"{sid}/{p}: черновики просят больше, чем осталось непокрытым",
                     {"черновики": D, "уже везут": C, "недобор": G}, "черновики ≤ недобор − везут", след)
        # Недобор закрытого района доп. заявки лежит в следующих — целиком и раз.
        if z.kind == "extra":
            for o, t in z.tasks.items():
                if not t.done:
                    continue
                for p, g in t.gaps.items():
                    есть = sum(int((c.plan.get(o) or {}).get(p) or 0) for c in м.дети(sid, живые=False)
                               if c.авто)
                    if есть != целых(g):
                        беда(seed, шаг, f"{sid}/{o}/{p}: недобор района не дошёл до следующей доп. заявки",
                             есть, целых(g), след)
        # Что видит старший: непокрытый остаток недобора.
        d = docs[sid]
        дети = (await db.supplies_children([sid])).get(sid) or []
        экран = sr._cover(sr._shortfall(d), дети)
        for r in экран["rows"]:
            p = r["id"]
            G = sum((нед.get(p) or {}).values())
            if not r.get("by_district"):
                continue
            покрыто = sum(по_поз.get(p, [0, 0]))
            if int(r.get("left") or 0) != max(0, G - покрыто) and G == int(r.get("gap") or 0):
                беда(seed, шаг, f"{sid}/{p}: на экране непокрытого недобора", r.get("left"),
                     max(0, G - покрыто), след)


# ── один прогон ──────────────────────────────────────────────────────────────
async def прогон(seed, шагов):
    rnd = random.Random(seed)
    db._db = AsyncMongoMockClient()[f"ambar_fuzz_intake_{seed}"]
    SR.base_drop()
    МИР["day"] = "2026-10-01"
    by = {p: {o: rnd.choice([0, 0, 1, 2, 3, 4]) for o in ОФИСЫ} for p in ТОВАР}
    by = {p: {o: n for o, n in v.items() if n} for p, v in by.items()}
    МИР["ask"] = {"asked": {p: sum(v.values()) for p, v in by.items() if v},
                  "by": {p: dict(v) for p, v in by.items() if v}}
    for o in ОФИСЫ:
        await db.save_stock_count(o, "2026-09-20", {
            "district": o, "day": "2026-09-20", "counted_at": T0.isoformat(), "first_time": False,
            "counted_by": 0, "lines": [{"id": p, "name": ИМЯ[p], "price": 100,
                                         "unit": SR._unit(SR._catalog()[p]), "actual": q, "counted": True}
                                        for p, q in СЧЁТ.get(o, {}).items()]})
    м = Мир(seed)
    след = м.след
    было = len(БЕДЫ)

    async def привяжи(новые):
        """Модель ждёт новых документов — находим их в базе и даём номера."""
        if not новые:
            return True
        свежие = [d for d in await db._db.supplies.find({}).to_list(length=None) if d["_id"] not in м.з]
        if len(свежие) != len(новые):
            беда(seed, шаг, "новых поставок в базе", sorted(d["_id"] for d in свежие),
                 [(z.kind, z.status, z.parent) for z in новые], след)
            return False
        for z in новые:
            d = next((x for x in свежие if (x.get("kind") or "main", x.get("status"),
                                            x.get("from_supply") or "") == (z.kind, z.status, z.parent)), None)
            if not d:
                беда(seed, шаг, "новая поставка не того вида",
                     [(x["_id"], x.get("kind"), x.get("status")) for x in свежие],
                     (z.kind, z.status, z.parent), след)
                return False
            свежие.remove(d)
            z.sid = d["_id"]
            м.з[z.sid] = z
        return True

    async def отпусти(sid, o):
        await db._db.supplies.update_one({"_id": sid}, {"$unset": {f"tasks.{o}.hold": ""}})

    def цель(только=None, вид=None, живые=0.85, свободные=False):
        """Район для действия. Чаще — тот, с которым правда работают: открытая
        заявка, район не закрыт и взят водителем; реже — любой, чтобы отказы
        («не твоя», «закрыта», «отменена») тоже проверялись."""
        кто = [z for z in м.з.values() if (только is None or z.status in только)
               and (вид is None or z.kind == вид) and z.tasks]
        if not кто:
            return None, None
        if rnd.random() < живые:
            пары = [(z, o) for z in кто if z.status == "open" for o, t in sorted(z.tasks.items())
                    if not t.done and not t.cancelled]
            свои = [x for x in пары if bool(x[0].tasks[x[1]].driver) != свободные]
            if свои and rnd.random() < 0.9:
                return rnd.choice(свои)
            if пары:
                return rnd.choice(пары)
        z = rnd.choice(кто)
        return z, rnd.choice(sorted(z.tasks))

    for шаг in range(шагов):
        a = rnd.random()
        новые = []

        if шаг == 0 or a < 0.035:                            # ответ магазина (и повторная загрузка)
            rows, дают = [], {}
            for k, p in enumerate(ТОВАР, 1):
                просили = МИР["ask"]["by"].get(p) or {}
                r = rnd.random()
                if r < 0.10:
                    continue                                 # строки нет вовсе
                if r < 0.20:
                    g = {}
                elif r < 0.55:
                    g = dict(просили)
                elif r < 0.85:
                    g = {o: rnd.randint(0, n) for o, n in просили.items()}
                else:
                    g = {o: просили.get(o, 0) + rnd.choice([0, 1]) for o in ОФИСЫ}
                g = {o: n for o, n in g.items() if n > 0}
                дают[p] = g
                rows.append((k if rnd.random() < 0.8 else k + 40, ИМЯ[p], 10,
                             g.get("jvc", 0), g.get("bbay", 0), g.get("silicon", 0), 0, 0))
            состав = {p: g for p, g in дают.items() if g}
            гап = {}
            for p, просили in МИР["ask"]["by"].items():
                всего, дали = sum(просили.values()), sum((дают.get(p) or {}).values())
                if not дали:
                    гап[p] = dict(просили)
                elif дали < всего:
                    g = {o: max(0, n - (дают[p].get(o) or 0)) for o, n in просили.items()}
                    гап[p] = {o: n for o, n in g.items() if n}
            гап = {p: g for p, g in гап.items() if g}
            st, r = await ручка(sr.handle_import, raw=книга(rows))
            след.append(f"ответ магазина {состав} → {st} {r.get('error') or ('замена' if r.get('replaced') else 'новая')}")
            старая = next((z for z in м.з.values() if z.kind == "main" and z.status == "open"
                           and z.day == МИР["day"]), None)
            начато = старая and any(t.noscan or t.done or t.codes for t in старая.tasks.values())
            if not состав:
                ветка("ответ:пусто")
                if st != 400:
                    беда(seed, шаг, "пустой ответ магазина", st, 400, след)
            elif начато:
                ветка("ответ:уже принимают")
                if (st, r.get("error")) != (409, "already_started"):
                    беда(seed, шаг, "повторная загрузка по начатой приёмке", (st, r.get("error")),
                         (409, "already_started"), след)
            else:
                if st != 200 or bool(r.get("replaced")) != bool(старая):
                    беда(seed, шаг, "загрузка ответа", (st, r.get("replaced"), r.get("error")),
                         (200, bool(старая)), след)
                    break
                ветка("ответ:замена" if старая else "ответ:новая")
                z = Заявка(r["supply_id"], "main", "open", МИР["day"])
                z.items = [p for p in ТОВАР if p in состав]
                for p, g in состав.items():
                    for o, n in g.items():
                        z.plan.setdefault(o, {})[p] = n
                z.tasks = {o: Район() for o in z.plan}
                z.гап = гап
                if старая:
                    if r["supply_id"] != старая.sid:
                        беда(seed, шаг, "замена сменила номер", r["supply_id"], старая.sid, след)
                    for o, t in z.tasks.items():
                        ст = старая.tasks.get(o)
                        if ст and not ст.cancelled:
                            t.driver = ст.driver
                    for c in м.дети(старая.sid):
                        if c.status == "draft":
                            c.status = "cancelled"
                м.з[z.sid] = z
                # Пришла заявка — взятые и не начатые задачи ПРОШЛЫХ основных снимаются.
                for x in м.з.values():
                    if x.kind == "main" and x.status == "open" and x is not z:
                        for t in x.tasks.values():
                            if t.driver and not (t.done or t.cancelled or t.noscan or t.codes):
                                t.driver = ""
                # Черновик докупки: недобор минус то, что уже везут с других баз.
                везут = {}
                for c in м.дети(z.sid):
                    if c.status != "draft":
                        for o, by_ in c.plan.items():
                            for p, n in by_.items():
                                везут[p] = везут.get(p, 0) + n
                надо = {p: max(0, sum(g.values()) - везут.get(p, 0)) for p, g in гап.items()}
                if any(надо.values()):
                    ч = Заявка("?", "extra", "draft", МИР["day"], parent=z.sid)
                    ч.авто = True
                    if not await привяжи([ч]):
                        break
                    d = await db.supply_get(ч.sid)
                    for it in d.get("items") or []:
                        p = it["id"]; ч.items.append(p)
                        for o, n in (it.get("by_district") or {}).items():
                            if int(n or 0) > int((гап.get(p) or {}).get(o) or 0):
                                беда(seed, шаг, f"черновик докупки: {p}/{o} больше недобора района", n,
                                     (гап.get(p) or {}).get(o), след)
                            ч.plan.setdefault(o, {})[p] = int(n)
                    ч.tasks = {o: Район() for o in ч.plan}
                    for p, n in надо.items():
                        есть = sum(int((ч.plan.get(o) or {}).get(p) or 0) for o in ч.plan)
                        if есть != n:
                            беда(seed, шаг, f"черновик докупки: {p} всего", есть, n, след)
                elif r.get("draft"):
                    беда(seed, шаг, "черновик докупки собран, а недобора нет", r.get("draft"), None, след)

        elif a < 0.045 and м.дней < 2:                       # наступил новый день
            м.дней += 1
            МИР["day"] = (datetime.strptime(МИР["day"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            SR.base_drop()
            след.append(f"новый день {МИР['day']}")

        elif a < 0.17:                                       # взять район / снять с водителя
            берём = rnd.random() < 0.8
            z, o = цель(свободные=берём)
            if not z:
                continue
            t = z.tasks[o]
            if берём:
                кто = ВОД[o] if rnd.random() < 0.8 else rnd.choice(list(ВОД.values()))
                ok, _ = await db.supply_task_claim(z.sid, o, кто, 1, datetime.now(timezone.utc))
                след.append(f"взять {z.sid}/{o} {кто} → {ok}")
                want = z.status == "open" and not t.driver and not t.done and not t.cancelled
                if ok != want:
                    беда(seed, шаг, "взять район", ok, want, след); break
                if ok:
                    t.driver = кто; ветка("взять:ok")
            else:
                st, r = await ручка(sr.handle_release, {"district": o}, z.sid)
                след.append(f"снять {z.sid}/{o} → {r.get('ok')}")
                want = bool(t.driver) and not t.done
                if bool(r.get("ok")) != want:
                    беда(seed, шаг, "снять с водителя", r.get("ok"), want, след); break
                if want:
                    t.driver = ""; ветка("снять:ok")

        elif a < 0.47:                                       # скан
            z, o = цель(только=("open", "open", "open", "done", "cancelled", "draft"))
            if not z:
                continue
            t = z.tasks[o]
            owner = rnd.random() < 0.2
            who = "STAR" if owner else (t.driver if t.driver and rnd.random() < 0.9 else ВОД[o])
            вар = [p for p in z.items if z.need(o, p)] or z.items or ТОВАР
            p = rnd.choice(вар) if rnd.random() < 0.93 else rnd.choice(ТОВАР)
            все = [c for x in м.з.values() for tt in x.tasks.values() for c, _ in tt.codes]
            code = rnd.choice(все) if все and rnd.random() < 0.05 else м.код()
            if z.status != "open":
                want = "no_supply"
            elif t.cancelled:
                want = "cancelled"
            elif not z.трогать(o, who, owner):
                want = "not_mine"
            elif t.done:
                want = "closed"
            elif not owner and z.цен_нет(o):
                want = "prices_needed"
            elif p not in z.items:
                want = "not_in_supply"
            elif code in все:
                want = "known"
            elif z.got(o, p) + QTY[p] > z.need(o, p) + 1e-9:
                want = "full"
            else:
                want = "taken"
            r = await sr.task_scan(z.sid, o, p, code, who, 1, "", owner)
            await отпусти(z.sid, o)
            след.append(f"скан {z.sid}/{o} {p} {who} → {r.get('verdict')}")
            ветка("скан:" + str(r.get("verdict")))
            if r.get("verdict") != want:
                беда(seed, шаг, "скан: вердикт", r.get("verdict"), want, след); break
            if want == "taken":
                t.got[p] = z.got(o, p) + QTY[p]
                t.codes.append((code, p))
                закрылся = t.noscan and z.left(o) <= 1e-9
                if bool(r.get("finished")) != bool(закрылся):
                    беда(seed, шаг, "последняя бутылка «без сканирования» закрывает район",
                         r.get("finished"), закрылся, след); break
                if закрылся:
                    м.закрой(z, o, новые); ветка("скан:закрыл район")

        elif a < 0.55:                                       # убрать бутылку
            z, o = цель(только=("open", "open", "done"))
            if not z:
                continue
            t = z.tasks[o]
            owner = rnd.random() < 0.2
            who = "STAR" if owner else (t.driver or ВОД[o])
            чужие = [c for x in м.з.values() for oo, tt in x.tasks.items() for c, _ in tt.codes
                     if not (x is z and oo == o)]
            if t.codes and rnd.random() < 0.85:
                code, p = rnd.choice(t.codes)
            elif чужие:
                code, p = rnd.choice(чужие), None
            else:
                code, p = "нет-такого", None
            if t.done or t.cancelled or z.status != "open":
                want = "closed"
            elif not z.трогать(o, who, owner):
                want = "not_mine"
            elif p is None:
                want = "not_ours"
            else:
                want = None
            r = await sr.task_undo(z.sid, o, code, who, owner)
            след.append(f"убрать {z.sid}/{o} {code} {who} → {r.get('verdict') or 'ok'}")
            ветка("убрать:" + str(r.get("verdict") or "ok"))
            if (None if r.get("ok") else r.get("verdict")) != want:
                беда(seed, шаг, "убрать: вердикт", r.get("verdict") or "ok", want or "ok", след); break
            if r.get("ok"):
                t.got[p] = z.got(o, p) - QTY[p]
                t.codes.remove((code, p))

        elif a < 0.62:                                       # принять без сканирования
            z, o = цель(только=("open", "open", "open", "done"))
            if not z:
                continue
            t = z.tasks[o]
            owner = rnd.random() < 0.25
            who = "STAR" if owner else (t.driver or ВОД[o])
            кадр = b"" if (not owner and rnd.random() < 0.2) else КАДР
            short, exp = None, []
            if rnd.random() < 0.35:
                ps = rnd.sample(z.items, rnd.randint(1, len(z.items)))
                lines = [{"id": p, "qty": rnd.choice([0.5, 1, 1, 2, 5])} for p in ps]
                short = {"lines": lines, "note": ""}
                for l in lines:
                    q = round(min(l["qty"], max(0.0, z.need_eff(o, l["id"]) - z.got(o, l["id"]))) * 2) / 2
                    if q > 0:
                        exp.append((l["id"], q))
            if z.status != "open":
                want = "no_supply"
            elif t.cancelled:
                want = "cancelled"
            elif t.driver != who and not owner:
                want = "not_mine"
            elif t.done:
                want = "closed"
            elif t.noscan:
                want = "already"
            elif not owner and z.цен_нет(o):
                want = "prices_needed"
            elif not owner and not кадр:
                want = "photo_needed"
            elif short and t.pend is None and not exp:
                want = "short_empty"
            else:
                want = None
            r = await sr.task_noscan(z.sid, o, who, owner, short, кадр if not owner else b"", "")
            след.append(f"без сканирования {z.sid}/{o} {who} чек={'да' if кадр else 'нет'} "
                        f"недовоз={exp} → {r.get('verdict') or 'ok'}")
            ветка("без сканирования:" + str(r.get("verdict") or "ok"))
            if (None if r.get("ok") else r.get("verdict")) != want:
                беда(seed, шаг, "без сканирования: вердикт", r.get("verdict") or "ok", want or "ok", след); break
            if r.get("ok"):
                t.noscan = True
                if short and t.pend is None:
                    t.pend = exp
                if not owner and not await db.supply_noscan_photo(z.sid, o):
                    беда(seed, шаг, "снимок чека базы не сохранён", False, True, след)

        elif a < 0.70:                                       # отчёт о недовозе
            z, o = цель(только=("open", "open", "open", "done"))
            if not z:
                continue
            t = z.tasks[o]
            ps = rnd.sample(z.items, rnd.randint(1, len(z.items)))
            lines = [{"id": p, "qty": rnd.choice([0.5, 1, 1, 2, 3, 9])} for p in ps]
            if rnd.random() < 0.2:
                lines.append(dict(rnd.choice(lines)))        # одна позиция двумя строками
            сумма = {}
            for l in lines:
                сумма[l["id"]] = сумма.get(l["id"], 0) + l["qty"]
            exp = []
            for p, q in сумма.items():
                q = round(min(q, max(0.0, z.need_eff(o, p) - z.got(o, p))) * 2) / 2
                if q > 0:
                    exp.append((p, q))
            want = ("closed" if (t.done or t.cancelled) else "pending" if t.pend is not None
                    else "empty" if not exp else None)
            r = await sr.short_report(z.sid, o, t.driver or "оператор",
                                      rnd.choice(["driver", "operator"]), lines, "")
            след.append(f"недовоз {z.sid}/{o} {exp} → {r.get('verdict') or 'ok'}")
            ветка("недовоз:" + str(r.get("verdict") or "ok"))
            if (None if r.get("ok") else r.get("verdict")) != want:
                беда(seed, шаг, "недовоз: вердикт", r.get("verdict") or "ok", want or "ok", след); break
            if r.get("ok"):
                t.pend = exp

        elif a < 0.77:                                       # решение старшего по недовозу
            z, o = цель()
            if not z:
                continue
            t = z.tasks[o]
            да = rnd.random() < 0.7
            r = await sr.short_decide(z.sid, o, да, "STAR")
            след.append(f"решение {z.sid}/{o} {'да' if да else 'нет'} → {r.get('verdict') or 'ok'}")
            ветка("решение:" + str(r.get("verdict") or "ok"))
            if bool(r.get("ok")) != (t.pend is not None):
                беда(seed, шаг, "решение по недовозу", r.get("verdict") or "ok", t.pend is not None, след); break
            if r.get("ok"):
                закрыт = t.done or t.cancelled
                if да and not закрыт:
                    for p, q in t.pend:
                        t.miss[p] = float(t.miss.get(p) or 0) + q
                t.pend = None
                закроется = да and not закрыт and z.left(o) <= 1e-9
                if bool(r.get("finished")) != bool(закроется):
                    беда(seed, шаг, "подтверждённый недовоз закрывает район, если принимать нечего",
                         r.get("finished"), закроется, след); break
                if закроется:
                    м.закрой(z, o, новые); ветка("решение:закрыл район")

        elif a < 0.83:                                       # закрыть район
            z, o = цель(только=("open", "open", "open", "done"))
            if not z:
                continue
            t = z.tasks[o]
            owner = rnd.random() < 0.25
            who = "STAR" if owner else (t.driver or ВОД[o])
            if not z.трогать(o, who, owner):
                want = "not_mine"
            elif t.done:
                want = "closed"
            elif t.noscan and not owner and z.left(o) > 1e-9:
                want = "noscan_left"
            elif t.cancelled:
                want = "closed"
            else:
                want = None
            r = await sr.task_finish(z.sid, o, who, "", owner)
            след.append(f"закрыть {z.sid}/{o} {who} → {r.get('verdict') or 'ok'}")
            ветка("закрыть:" + str(r.get("verdict") or "ok"))
            if (None if r.get("ok") else r.get("verdict")) != want:
                беда(seed, шаг, "закрыть район: вердикт", r.get("verdict") or "ok", want or "ok", след); break
            if r.get("ok"):
                м.закрой(z, o, новые)
                if bool(r.get("next_draft")) != bool(новые or (z.kind == "extra" and any(целых(g) for g in t.gaps.values()))):
                    беда(seed, шаг, "следующая доп. заявка в ответе", r.get("next_draft"), bool(новые), след)

        elif a < 0.89:                                       # доп. заявка: цена / нет в наличии
            z, o = цель(только=("open",), вид="extra")
            if not z:
                continue
            t = z.tasks[o]
            if rnd.random() < 0.6:
                # Водитель у прилавка вписывает цены: чаще все разом, реже одну (или стирает).
                разом = rnd.random() < 0.6
                плохо = False
                for p in (z.items if разом else [rnd.choice(z.items)]):
                    цена = rnd.choice([12, 35, 80]) if разом else rnd.choice([0, 12, 35, 80])
                    r = await sr.buy_set(await db.supply_get(z.sid), p, цена, 0, "Первый", 1)
                    след.append(f"цена {z.sid} {p} = {цена} → {r}")
                    if not r.get("ok"):
                        беда(seed, шаг, "цена позиции доп. заявки", r, "ok", след); плохо = True; break
                    z.цены[p] = цена; ветка("цена:ok")
                if плохо:
                    break
            else:
                p = rnd.choice(z.items) if rnd.random() < 0.9 else rnd.choice(ТОВАР)
                on = rnd.random() < 0.7
                who = t.driver if t.driver and rnd.random() < 0.9 else "Чужой"
                if t.driver != who:
                    want = "not_mine"
                elif t.done or t.cancelled:
                    want = "closed"
                elif p not in z.items or z.need(o, p) <= 0:
                    want = "not_in_supply"
                elif on and z.got(o, p) > 0:
                    want = "taken"
                else:
                    want = None
                r = await sr.na_set(z.sid, o, p, on, who)
                след.append(f"нет в наличии {z.sid}/{o} {p} {'да' if on else 'снять'} → {r.get('verdict') or 'ok'}")
                ветка("нет в наличии:" + str(r.get("verdict") or "ok"))
                if (None if r.get("ok") else r.get("verdict")) != want:
                    беда(seed, шаг, "нет в наличии: вердикт", r.get("verdict") or "ok", want or "ok", след); break
                if r.get("ok"):
                    if on:
                        t.na[p] = z.need(o, p)
                        if z.цены.get(p) and sum(z.need(x, p) for x in z.tasks) == z.need(o, p):
                            z.цены[p] = 0
                    else:
                        t.na.pop(p, None)

        elif a < 0.94:                                       # отмена района / заявки / черновика
            z, o = цель(только=("open", "open", "draft", "done", "cancelled"))
            if not z:
                continue
            часть = rnd.random() < 0.7
            сила = rnd.random() < 0.5
            body = {"as": "STAR", "force": сила}
            if часть:
                body["districts"] = [o]
            st, r = await ручка(sr.handle_cancel, body, z.sid)
            след.append(f"отмена {z.sid} {'район ' + o if часть else 'вся'} force={сила} → {st} {r.get('error') or r.get('cancelled')}")
            if z.status == "draft":
                if st != 200:
                    беда(seed, шаг, "отмена черновика", st, 200, след); break
                z.status = "cancelled"; ветка("отмена:черновик")
            elif z.status != "open":
                if (st, r.get("error")) != (409, "not_open"):
                    беда(seed, шаг, "отмена закрытой заявки", (st, r.get("error")), (409, "not_open"), след); break
            else:
                цели = [x for x in ([o] if часть else sorted(z.tasks))
                        if not (z.tasks[x].done or z.tasks[x].cancelled or z.tasks[x].noscan)]
                принято = sum(z.got(x, p) for x in цели for p in z.items)
                if not цели:
                    want = (409, "nothing_to_cancel")
                elif принято > 0 and not сила:
                    want = (409, "already_taken")
                else:
                    want = (200, None)
                if (st, r.get("error")) != want:
                    беда(seed, шаг, "отмена: ответ", (st, r.get("error")), want, след); break
                ветка("отмена:" + str(r.get("error") or "ok"))
                if st == 200:
                    for x in цели:
                        z.tasks[x].cancelled = True; z.tasks[x].driver = ""
                    if all(t.done or t.cancelled for t in z.tasks.values()):
                        z.status = "done" if any(t.done for t in z.tasks.values()) else "cancelled"

        else:                                                # черновик → водителям / заявка на базу руками
            черн = [z for z in м.з.values() if z.status == "draft"]
            if черн and rnd.random() < 0.75 and len(м.открытые()) < 9:
                z = rnd.choice(черн)
                база = rnd.choice(["", "База Один", "База Два", "База Три"])
                st, r = await ручка(sr.handle_draft_confirm, {"base": база, "as": "STAR"}, z.sid)
                след.append(f"черновик {z.sid} → база «{база}» {st}")
                want = 400 if not база else 200
                if st != want:
                    беда(seed, шаг, "подтверждение черновика", st, want, след); break
                if st == 200:
                    z.status = "open"; z.base = база; z.был_открыт = True; ветка("черновик:отправлен")
            elif len(м.открытые()) < 9:
                # Мастер «на другую базу»: берёт непокрытый остаток недобора.
                род = [z for z in м.з.values() if z.был_открыт]
                if not род:
                    continue
                z = rnd.choice(род)
                нед = м.недобор(z)
                покрыто = {}
                for c in м.дети(z.sid):
                    for o_, by_ in c.plan.items():
                        for p, n in by_.items():
                            покрыто[p] = покрыто.get(p, 0) + n
                items = []
                for p, by_ in нед.items():
                    ост = sum(by_.values()) - покрыто.get(p, 0)
                    берём = {}
                    for o_, n in by_.items():
                        k = min(n, max(0, ост))
                        if k > 0:
                            берём[o_] = k; ост -= k
                    if берём:
                        items.append({"id": p, "by_district": берём})
                if not items:
                    continue
                st, r = await ручка(sr.handle_extra_create, {"base": "База Руками", "items": items,
                                                              "source": "shortfall", "from_supply": z.sid,
                                                              "as": "STAR"})
                след.append(f"заявка на базу из недобора {z.sid}: {items} → {st}")
                if st != 200:
                    беда(seed, шаг, "заявка на другую базу", (st, r), 200, след); break
                c = Заявка("?", "extra", "open", МИР["day"], parent=z.sid, base="База Руками")
                for it in items:
                    c.items.append(it["id"])
                    for o_, n in it["by_district"].items():
                        c.plan.setdefault(o_, {})[it["id"]] = n
                c.tasks = {o_: Район() for o_ in c.plan}
                новые.append(c); ветка("заявка на базу:руками")

        if len(БЕДЫ) > было:
            break
        if not await привяжи(новые):
            break
        await сверка(м, шаг)
        if len(БЕДЫ) > было:
            break
    return len(БЕДЫ) == было


НАДО = ["ответ:новая", "ответ:замена", "ответ:уже принимают", "скан:taken", "скан:full", "скан:known",
        "скан:not_mine", "скан:closed", "скан:cancelled", "скан:no_supply", "скан:prices_needed",
        "скан:закрыл район", "убрать:ok", "убрать:not_ours", "убрать:closed",
        "без сканирования:ok", "без сканирования:photo_needed", "без сканирования:already",
        "без сканирования:prices_needed", "недовоз:ok", "недовоз:pending", "недовоз:empty",
        "решение:ok", "решение:закрыл район", "закрыть:ok", "закрыть:noscan_left",
        "нет в наличии:ok", "нет в наличии:taken", "цена:ok", "отмена:ok", "отмена:already_taken",
        "отмена:черновик", "черновик:отправлен", "заявка на базу:руками", "взять:ok", "снять:ok",
        "цепочка:следующая доп. заявка", "цепочка:второй район в тот же черновик", "цепочка:третья база"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=200); ap.add_argument("--steps", type=int, default=140)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    подготовь()
    t0 = time.time()
    чистых = 0
    for k in range(a.runs):
        if asyncio.run(прогон(a.seed + k, a.steps)):
            чистых += 1
        elif len(БЕДЫ) >= 3:
            break
        if (k + 1) % 250 == 0:
            print(f"  … {k + 1} цепочек, чистых {чистых}, {time.time() - t0:.0f} с", flush=True)
    print(f"цепочек {a.runs} × до {a.steps} шагов · чистых {чистых} · {time.time() - t0:.1f} с")
    print("ответы:", ", ".join(f"{k} {v}" for k, v in sorted(ВЕТКИ.items())))
    нет = [k for k in НАДО if k not in ВЕТКИ]
    if нет and a.runs >= 100:
        print("  не пройдены ветки:", нет)
    print("ИТОГ:", "все прошли" if not БЕДЫ and not (нет and a.runs >= 100) else
          f"бед {len(БЕДЫ)}: {[(b[0], b[1], b[2]) for b in БЕДЫ[:5]]}")
    return 1 if БЕДЫ or (нет and a.runs >= 100) else 0


if __name__ == "__main__":
    sys.exit(main())
