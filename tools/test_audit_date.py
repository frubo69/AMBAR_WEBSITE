"""Ревизия подписана календарным днём, а не учётными сутками (владелец, 18 ноя
2026: «не дели ревизии по сменам… если он начал ревизию до 10 утра, то она
датируется вчерашним днём. для ревизий сделай так, что сутки считаются как
обычно, с 12 до 12. только убедись, что это ничего не ломает в логике и
правильно синхронится со складом и сменами. это путает, когда он сделал
сегодня, а написано вчера»). В той же просьбе — история ревизий: строка статуса
в шапке района уходит, когда район раскрыт, а ревизии с принятым решением не
горят красным.

Как сделано. Ключ ревизии — учётные сутки (day, граница в десять утра) —
остался прежним: на нём лежит снимок склада, списания по решению о недостаче,
переезды по излишку и «одна ревизия района в сутки». Сдвинуть ключ значило бы
сдвинуть учёт относительно смен. Менялось то, что видит человек: у ревизии
появилась дата — календарный день её начала по Дубаю (stock_routes.audit_date),
и ею подписано всё на экранах и в сообщениях.

  A. audit_date: 06:38 — сегодня, а не вчера; полночь — граница суток.
  B. Утренняя ревизия целиком: лист, отчёт и история отдают дату сегодняшним
     числом, а ключ остаётся вчерашним.
  C. Учёт не сдвинулся: снимок склада и списания по недостаче лежат под тем же
     ключом, склад считается от этого снимка, вторая ревизия района в те же
     учётные сутки по-прежнему не начинается.
  D. «Правка задним числом»: слать или нет, решает учётный ключ, как раньше;
     исключение одно — ревизия, начатая сегодня до десяти и завершённая после
     десяти. В тексте ревизия названа календарным днём начала: начатая вчера
     утром и завершённая сегодня — «вчера», а не «позавчера».
  E. Отмена ревизии: в сообщении день её начала.
  F. Незавершённая с прошлых суток в списке районов — со своей датой.
  G. Страница STAR: «Сегодня» / «Вчера» по календарю; решённая недостача —
     жёлтым, ждущая решения — красным; статус в шапке уходит при раскрытии.

    python3 tools/test_audit_date.py
"""
import asyncio, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as SR, backdate, owner_routes              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
U = timezone.utc
O = "jvc"
KEY, CAL = "2026-11-16", "2026-11-17"        # учётные сутки и календарный день ревизии в 06:38 17 ноября
УТРО = "2026-11-17T02:38:00+00:00"           # 06:38 по Дубаю
NAMES = {"p1": "Absolut 1 ltr", "p2": "Stolichnaya 1 ltr"}
FAIL = []
ВЛАДЕЛЬЦАМ = []


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок:
        FAIL.append(имя)


async def tick():
    await asyncio.sleep(0.02)


def unwrap(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def post(handler, body):
    req = make_mocked_request("POST", "/x")
    req["owner_id"] = 1
    async def _json(): return body
    req.json = _json
    r = await unwrap(handler)(req)
    return r.status, json.loads(r.body)


async def get(handler, query=""):
    req = make_mocked_request("GET", "/x" + (("?" + query) if query else ""))
    req["owner_id"] = 1
    r = await unwrap(handler)(req)
    return json.loads(r.body)


async def чисто():
    for c in ("qr_codes", "stock_counts", "stock_audits", "audit_scans", "audit_scans_dropped",
              "orders", "writeoffs", "stock_transfers", "supplies", "qr_found"):
        await db._db[c].delete_many({})
    SR.base_drop()
    ВЛАДЕЛЬЦАМ.clear()


async def полка(коды: dict):
    """Прошлый снимок девять дней назад и коды на полке: {код: позиция}."""
    старо = datetime.now(U) - timedelta(days=9)
    cat = SR._catalog()
    счёт: dict = {}
    for code, pid in коды.items():
        счёт[pid] = счёт.get(pid, 0) + 1
        await db._db.qr_codes.insert_one({
            "_id": code, "status": "active", "product_id": pid, "product_name": NAMES[pid],
            "district": O, "origin": O, "at": старо, "src": "intake", "qty": 1, "label": code})
    await db.save_stock_count(O, "2026-11-04", {
        "district": O, "day": "2026-11-04", "counted_at": (старо + timedelta(hours=1)).isoformat(),
        "first_time": False, "counted_by": 0,
        "lines": [{"id": pid, "name": NAMES[pid], "price": 100, "unit": SR._unit(cat[pid]),
                   "actual": q, "counted": True} for pid, q in счёт.items()]})
    SR.base_drop()


def сейчас(учётные, календарь):
    """«Сейчас» для сообщения о правке задним числом: учётные сутки и
    календарный день (до десяти утра они расходятся на день)."""
    backdate.biz_day = lambda *a, **k: учётные
    backdate.cal_day = lambda *a, **k: календарь


async def ревизия(key, начата, сканы):
    """Начать ревизию под ключом key, отметить её начало моментом «начата» и
    отсканировать коды."""
    st, _ = await SR.audit_start(O, key, 1, "Тест-старший"); assert st == 200, st
    await db.audit_set(O, key, {"started_at": начата})
    await tick()
    for c in сканы:
        await SR.audit_scan(O, key, c, 1, "Тест-старший", "s1")
        await tick()


def страница():
    """Функции истории ревизий из owner/index.html — под узлом, с часами на
    заданный момент."""
    src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
    def fn(имя):
        m = re.search(r"(?ms)^function %s\(.*?^\}" % re.escape(имя), src)
        assert m, имя
        return m.group(0)
    тело = "\n".join(fn(n) for n in ("_calIso", "_audDate", "_audCalT", "_hstCal", "_hstDur",
                                    "hstRow", "hstDistCard", "hstSummary"))
    css = re.search(r"\.hst\.open \.hst-cap > span i\{([^}]*)\}", src)
    return тело, (css.group(1) if css else ""), src


УЗЕЛ = r"""
const [,, nowIso, dataJson] = process.argv;
const РЕАЛ = Date.now;
Date.now = () => new Date(nowIso).getTime();
const MONTHS_GEN = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
const escS = s => String(s), fmt = v => String(v), fmtQty = v => String(v).replace('.', ',');
const pluralize = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11) ? a : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? b : c;
const _dayName = d => `${+d.slice(8, 10)} ${MONTHS_GEN[+d.slice(5, 7) - 1]}`;
const _CHEV = '', _SC_CHEV = '';
const D = JSON.parse(dataJson);
const STK = {audits: D.audits, hstOpen: {}, hstAll: {}};
/*ТЕЛО*/
const строка = a => { const h = hstRow(a);
  return {день: (h.match(/<b>([^<]*)/) || [])[1], цвет: (h.match(/<i class="([^"]*)">/) || [])[1],
          хвост: (h.match(/<i class="([^"]*)">[^<]*<\/i><\/span>/) || [, null])[1]}; };
const шапка = (open) => { STK.hstOpen = open ? {jvc: true} : {};
  const h = hstDistCard({id: 'jvc', code: 'B1', name: 'JVC'}, D.audits);
  return {открыта: /class="sc hst open"/.test(h), статус: (h.match(/<i class="([^"]*)">([^<]*)<\/i>/) || []).slice(1, 3)}; };
console.log(JSON.stringify({сегодня: _calIso(0), вчера: _calIso(1), строки: D.audits.map(строка),
  свёрнута: шапка(false), раскрыта: шапка(true),
  сводка: (hstSummary(D.audits).match(/aud-tv">(\d+)/g) || []).map(x => x.replace(/\D/g, ''))}));
"""


def узел(когда_iso, audits):
    тело, _, _ = страница()
    f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    f.write(УЗЕЛ.replace("/*ТЕЛО*/", тело)); f.close()
    r = subprocess.run(["node", f.name, когда_iso, json.dumps({"audits": audits})], capture_output=True, text=True)
    os.unlink(f.name)
    if r.returncode:
        eq("узел выполнил код страницы", r.stderr.strip()[:300], "")
        return {}
    return json.loads(r.stdout)


async def main():
    db._db = AsyncMongoMockClient()["audit_date"]
    async def _владельцам(event_key, text, *a, **k): ВЛАДЕЛЬЦАМ.append((event_key, text))
    owner_routes.notify_owners_force = _владельцам
    owner_routes.notify_owners = _владельцам

    print("── A. какой день у ревизии ──────────────────────────────────")
    eq("A. начата в 06:38 — сегодняшняя, хотя ключ вчерашний", SR.audit_date({"started_at": УТРО, "day": KEY}), CAL)
    eq("A. 23:59 по Дубаю — ещё этот день", SR.audit_date({"started_at": "2026-11-16T19:59:00+00:00"}), "2026-11-16")
    eq("A. 00:00 по Дубаю — уже следующий", SR.audit_date({"started_at": "2026-11-16T20:00:00+00:00"}), "2026-11-17")
    eq("A. 10:01 — тот же день, что и ключ", SR.audit_date({"started_at": "2026-11-17T06:01:00+00:00", "day": "2026-11-17"}), "2026-11-17")
    eq("A. время без пояса читается как UTC", SR.audit_date({"started_at": "2026-11-17T02:38:00"}), CAL)
    eq("A. не начата — что есть: ключ или пусто", (SR.audit_date({}, KEY), SR.audit_date({"day": KEY}), SR.audit_date(None)), (KEY, KEY, ""))
    eq("A. начала нет, есть завершение — по нему", SR.audit_date({"finished_at": УТРО}), CAL)

    print("── B. утренняя ревизия: дата сегодняшняя, ключ прежний ─────────────────────")
    await чисто()
    await полка({"A1": "p1", "A2": "p1", "A3": "p1", "S1": "p2"})
    сейчас(CAL, CAL)                                  # 17 ноября после десяти
    await ревизия(KEY, УТРО, ["A1", "A2", "S1"])       # одной Absolut не хватает
    sh = await SR.audit_sheet(O, KEY)
    eq("B. лист: ключ прежний, дата — сегодняшним числом", (sh["day"], sh["audit"]["date"], sh["audit"]["state"]), (KEY, CAL, "running"))
    st, rep = await SR.audit_finish(O, KEY, 1, "Тест-старший")
    eq("B. завершена", (st, rep["audit"]["state"], rep["day"], rep["audit"]["date"]), (200, "pending", KEY, CAL))
    hist = (await get(SR.handle_audits, "limit=10"))["audits"]
    eq("B. история: одна ревизия, ключ и дата", [(a["district"], a["day"], a["date"], a["state"]) for a in hist], [(O, KEY, CAL, "pending")])
    eq("B. история: недостача одна бутылка", (hist[0]["short_qty"], hist[0]["scan_qty"]), (1, 3))
    rep2 = await get(SR.handle_audit_report, f"district={O}&day={KEY}")
    eq("B. отчёт открывается по ключу и несёт дату", (rep2["day"], rep2["audit"]["date"]), (KEY, CAL))

    print("── C. учёт не сдвинулся ────────────────────────────────────")
    cnt = await db.get_stock_count(O, KEY)
    eq("C. снимок склада лежит под учётным ключом", (bool(cnt), (cnt or {}).get("day")), (True, KEY))
    eq("C. под календарным днём снимка нет", await db.get_stock_count(O, CAL), None)
    eq("C. склад считается от этого снимка", (await db.get_last_stock_count(O, before_day=None))["day"], KEY)
    SR.base_drop()
    have = ((await SR._district_base(KEY)).get(O) or {}).get("have_exact") or {}
    eq("C. на складе то, что увидела камера", (float(have.get("p1", 0)), float(have.get("p2", 0))), (2.0, 1.0))
    st, r = await post(SR.handle_audit_short, {"district": O, "day": KEY, "blame": False, "as": "Тест-старший"})
    ws = [w async for w in db._db.writeoffs.find({})]
    eq("C. решение принято, списание — тем же учётным днём", (st, [(w.get("day"), w.get("audit_day"), w.get("src")) for w in ws]),
       (200, [(KEY, KEY, "audit")]))
    eq("C. ревизия закрыта, дата на месте", (r["audit"]["state"], r["audit"]["date"]), ("closed", CAL))
    st, r = await SR.audit_start(O, KEY, 1, "Тест-старший")
    eq("C. вторая ревизия района в те же учётные сутки не начинается", (st, r.get("error")), (409, "finished"))

    print("── D. «правка задним числом» ──────────────────────────────────")
    eq("D. утренняя ревизия, завершённая после десяти, — не задним числом",
       [t for k, t in ВЛАДЕЛЬЦАМ if k == "data.backdated"], [])
    await чисто()
    await полка({"A1": "p1", "S1": "p2"})
    await ревизия("2026-11-14", "2026-11-14T12:00:00+00:00", ["A1", "S1"])     # начата три дня назад
    st, _ = await SR.audit_finish(O, "2026-11-14", 1, "Тест-старший")
    зад = [t for k, t in ВЛАДЕЛЬЦАМ if k == "data.backdated"]
    eq("D. начатая три дня назад — задним числом, один раз", (st, len(зад)), (200, 1))
    eq("D. …и названа своим днём", ("14 ноября" in зад[0], "(3 д" in зад[0], "ревизия" in зад[0]), (True, True, True))
    await чисто()
    await полка({"A1": "p1"})
    await ревизия("2026-11-16", "2026-11-16T19:00:00+00:00", ["A1"])           # 23:00 16-го, ключ 16-е
    сейчас("2026-11-16", "2026-11-17")                # завершают в 01:00 17-го — те же учётные сутки
    st, _ = await SR.audit_finish(O, "2026-11-16", 1, "Тест-старший")
    eq("D. начата вечером, завершена ночью той же смены — не задним числом",
       (st, [t for k, t in ВЛАДЕЛЬЦАМ if k == "data.backdated"]), (200, []))
    await чисто()
    await полка({"A1": "p1"})
    await ревизия("2026-11-15", "2026-11-16T02:00:00+00:00", ["A1"])           # 06:00 16-го, ключ 15-е
    сейчас("2026-11-16", "2026-11-17")                # завершают в 07:00 17-го — уже другие учётные сутки
    st, _ = await SR.audit_finish(O, "2026-11-15", 1, "Тест-старший")
    зад = [t for k, t in ВЛАДЕЛЬЦАМ if k == "data.backdated"]
    eq("D. начата вчера утром, завершена сегодня утром — задним числом", (st, len(зад)), (200, 1))
    eq("D. …названа днём начала: «16 ноября (вчера)», а не ключом",
       ("16 ноября" in (зад or [""])[0], "(вчера)" in (зад or [""])[0], "15 ноября" in (зад or [""])[0]), (True, True, False))
    await чисто()
    await полка({"A1": "p1"})
    await ревизия("2026-11-16", "2026-11-16T19:00:00+00:00", ["A1"])           # 23:00 16-го, ключ 16-е
    сейчас("2026-11-17", "2026-11-17")                # завершают в 11:00 17-го — сутки 16-го закрыты
    st, _ = await SR.audit_finish(O, "2026-11-16", 1, "Тест-старший")
    зад = [t for k, t in ВЛАДЕЛЬЦАМ if k == "data.backdated"]
    eq("D. начата вчера вечером, завершена сегодня после десяти — задним числом, «вчера»",
       (st, len(зад), "16 ноября" in (зад or [""])[0], "(вчера)" in (зад or [""])[0]), (200, 1, True, True))
    сейчас(CAL, CAL)

    print("── E. отмена ревизии ───────────────────────────────────────")
    await чисто()
    await полка({"A1": "p1", "A2": "p1"})
    await ревизия(KEY, УТРО, ["A1"])
    st, r = await SR.audit_cancel(O, KEY, 1, "Тест-старший")
    тексты = [t for k, t in ВЛАДЕЛЬЦАМ if k == "stock.audit"]
    eq("E. отменена, сообщение одно", (st, len(тексты)), (200, 1))
    eq("E. в сообщении день начала ревизии, а не ключ", ("· за 17.11" in тексты[0], "16.11" in тексты[0]), (True, False))

    print("── F. незавершённая с прошлых суток ─────────────────────────────")
    await чисто()
    await полка({"A1": "p1", "A2": "p1"})
    await ревизия(KEY, УТРО, ["A1"])
    st = await get(SR.handle_status, "day=2026-11-17")
    d = next(x for x in st["districts"] if x["id"] == O)
    eq("F. строка «не завершена» несёт ключ и дату", ((d.get("audit_open") or {}).get("day"), (d.get("audit_open") or {}).get("date"),
                                                 (d.get("audit_open") or {}).get("scans")), (KEY, CAL, 1))

    print("── G. страница STAR ───────────────────────────────────────")
    ждёт = {"district": "jvc", "day": KEY, "date": CAL, "state": "pending", "short_qty": 7.5, "short_aed": 1000,
            "over_qty": 2, "scan_qty": 444, "started_by": "STAR", "minutes": 35}
    решена = {"district": "jvc", "day": "2026-10-31", "date": "2026-10-31", "state": "closed", "short_qty": 15.5,
              "short_aed": 2450, "over_qty": 2, "scan_qty": 517, "short_resolved": True, "over_resolved": True}
    сошлась = {"district": "jvc", "day": "2026-10-01", "date": "2026-10-01", "state": "closed", "short_qty": 0,
               "over_qty": 0, "scan_qty": 600}
    излишек = {"district": "jvc", "day": "2026-09-25", "date": "2026-09-25", "state": "closed", "short_qty": 0,
               "over_qty": 1, "scan_qty": 602, "over_resolved": True}
    старая = {"district": "jvc", "day": "2026-09-01", "state": "closed", "short_qty": 2, "short_aed": 300,
              "over_qty": 0, "scan_qty": 10, "short_resolved": True}        # записи без поля date
    все = [ждёт, решена, сошлась, излишек, старая]
    днём = узел("2026-11-17T08:00:00Z", все)          # 12:00 по Дубаю 17 ноября — день ревизии
    eq("G. в день ревизии, после десяти: «Сегодня» (было «Вчера»)", (днём["сегодня"], днём["строки"][0]["день"]), (CAL, "Сегодня"))
    eq("G. в шапке района — тоже «Сегодня»", днём["свёрнута"]["статус"], ["dn", "Сегодня · ждёт решения"])
    утром = узел("2026-11-17T02:50:00Z", все)         # 06:50 по Дубаю, сразу после ревизии
    eq("G. в 06:50 того же утра — «Сегодня»", утром["строки"][0]["день"], "Сегодня")
    завтра = узел("2026-11-18T02:46:00Z", все)        # 06:46 по Дубаю 18 ноября — как на снимке владельца
    eq("G. на следующее утро — «Вчера»", завтра["строки"][0]["день"], "Вчера")
    eq("G. остальные — числом", [s["день"] for s in завтра["строки"][1:]], ["31 октября", "1 октября", "25 сентября", "1 сентября"])
    eq("G. ждёт решения — красным, и хвост красный", (завтра["строки"][0]["цвет"], завтра["строки"][0]["хвост"]), ("dn", "dn"))
    eq("G. решение принято — жёлтым, хвост серый", (завтра["строки"][1]["цвет"], завтра["строки"][1]["хвост"]), ("warn", ""))
    eq("G. сошлась — зелёным; излишек — жёлтым; старая решённая — жёлтым",
       [s["цвет"] for s in завтра["строки"][2:]], ["ok", "warn", "warn"])
    eq("G. шапка района: последняя ждёт решения — красным", завтра["свёрнута"]["статус"], ["dn", "Вчера · ждёт решения"])
    eq("G. шапка по решённой ревизии — жёлтым", узел("2026-11-18T02:46:00Z", [решена])["свёрнута"]["статус"],
       ["warn", "31 октября · недостача 15,5 бут"])
    eq("G. раскрытый район помечен open — по нему статус и прячется", (завтра["свёрнута"]["открыта"], завтра["раскрыта"]["открыта"]), (False, True))
    _, css, src = страница()
    eq("G. стиль: статус в шапке уходит, когда район раскрыт", ("opacity:0" in css, "max-height:0" in css), (True, True))
    eq("G. сводка за 30 дней считает по дате: проведено 2, сошлось 0", завтра["сводка"][:1] + завтра["сводка"][2:3], ["2", "0"])
    eq("G. страница нигде не подписывает ревизию учётным ключом",
       ("_hstDay(a.day)" in src, "_hstDay(last.day)" in src, "_dayName(rep.day)" in src, "_audDayT(о.day)" in src), (False,) * 4)

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
