"""Деньги на случайных месяцах: сейф, ДДС, распределение, Барракуда.

Владелец, 1 окт 2026: «что требует глубочайших тестов по миллионам сценариев» —
вторым пунктом выбрал деньги. За день до этого менялся сам расчёт (минус
выручки больше не уменьшает сейф), а ДДС за день и за месяц считает приложение,
не сервер. Здесь три уровня, от быстрого к настоящему:

  1. ЯДРО. finance_calc.compute на случайных месяцах против второй, отдельно
     написанной модели в целых сотых (никаких float) — стопки по дням, итоги,
     сейф, долг Барракуде. Плюс свойства: сумма стопок = сейф; сейф дня =
     сейф вчера + движения; неподтверждённый день в сейф не входит; минус
     выручки сейф не уменьшает; подтверждение дня меняет сейф ровно на выручку.
  2. ЭКРАН. Те же месяцы отдаются настоящим функциям приложения (вынуты из
     owner/index.html, исполняются в node): ДДС дня, ДДС месяца, Барракуда.
     Ни одна строка «Не сходится с сейфом» появиться не должна.
  3. СЕРВЕР ЦЕЛИКОМ. Настоящая книга (finance_routes.build) на базе в памяти:
     случайные заказы, расходы водителей и действия старшего настоящими
     ручками — подтвердить день, вписать суммы, расход, выплату, приход, оплату
     Барракуде, убрать запись. После каждого шага — те же проверки, экран
     против сервера и перенос на следующий месяц.

    python3 tools/fuzz_finance.py                 # 20 000 месяцев ядра, 2 000 на экран, 12 прогонов сервера
    python3 tools/fuzz_finance.py --core 200000   # глубже
    python3 tools/fuzz_finance.py --seed 7 --quick
"""
import argparse, asyncio, json, os, random, re, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
import finance_calc as calc                                        # noqa: E402

C = lambda v: int(round(float(v or 0) * 100))          # в сотые   # noqa: E731
БЕДЫ = []


def беда(где, что, **ctx):
    if len(БЕДЫ) < 40:
        БЕДЫ.append((где, что, ctx))


# ── случайный месяц ─────────────────────────────────────────────────────────
def _сумма(r, крупно=False):
    """Суммы, какими они бывают: чаще целые, иногда с копейками, иногда ноль."""
    k = r.random()
    if k < 0.12:
        return 0
    v = r.choice([r.randint(1, 400), r.randint(100, 9000), r.randint(5000, 60000) if крупно else r.randint(1, 3000)])
    if k > 0.9:
        v += r.choice([0.5, 0.25, 0.01, 0.99, 0.1, 0.33])
    return v


def месяц(r, n=None):
    n = n or r.randint(1, 31)
    opening = {"safe_b_open": r.choice([0, 0, _сумма(r, True)]), "debt_b_open": r.choice([0, _сумма(r, True)]),
               "rp_open": r.choice([0, _сумма(r), -r.randint(1, 500)]), "np_open": r.choice([0, _сумма(r, True), -r.randint(1, 900)]),
               # крипта на счёте РП (со 2 окт 2026): в сейф не входит, считается рядом
               "rp_cr_open": r.choice([0, 0, _сумма(r)])}
    days = []
    for i in range(n):
        cash, spend = _сумма(r, True), _сумма(r)
        d = {"day": f"2026-09-{i + 1:02d}", "gross": cash + _сумма(r), "cash": cash, "spend": spend,
             "tips": _сумма(r), "card": _сумма(r), "crypto": _сумма(r), "ordered": _сумма(r, True),
             "ordered_extra": r.choice([0, _сумма(r)])}
        t = r.random()
        if t < 0.35:
            d["handed"] = cash - spend - r.choice([0, r.randint(0, 300)])      # бывает и в минус
        elif t < 0.45:
            d["handed"] = -r.randint(1, 2000)                                  # расходов больше наличных
        if r.random() < 0.15:
            d["handed_fact"] = r.choice([0, _сумма(r, True)])
        база = d.get("handed_fact", d.get("handed", cash - spend))
        # раскладка: разумная, жадная (больше выручки) или пустая
        k = r.random()
        if k < 0.55 and база > 0:
            d["aside"] = r.choice([int(база // 2), _сумма(r), 0]); d["collected"] = r.choice([_сумма(r), 0, int(база)])
        elif k < 0.7:
            d["aside"] = _сумма(r, True); d["collected"] = _сумма(r, True)     # больше, чем сдали
        if r.random() < 0.2:
            d["extra_rp"] = _сумма(r)
        k = r.random()
        if k < 0.4:
            d["pay"] = r.choice([0, _сумма(r, True), d["ordered"]])
        elif k < 0.5:
            d["pay_b"] = _сумма(r); d["pay_b_extra"] = r.choice([0, _сумма(r)])
        d["expenses"] = [{"amount": _сумма(r) or 1, **({"pay": "crypto"} if r.random() < 0.25 else {})}
                         for _ in range(r.choice([0, 0, 1, 2, 5]))]
        # крипта в РП+ дня и вывод крипты в наличные (часть — с крипта-счёта РП)
        if r.random() < 0.3:
            d["collected_cr"] = _сумма(r)
        if d.get("extra_rp") and r.random() < 0.4:
            d["cr_cash"] = min(d["extra_rp"], _сумма(r))
        d["payouts"] = [{"amount": _сумма(r) or 1} for _ in range(r.choice([0, 0, 0, 1, 2]))]
        # как на сервере: день «ждёт», если выручка в плюсе и не подтверждён
        d["ok"] = r.random() < 0.6
        d["pending"] = bool(база > 0 and not d["ok"])
        days.append(d)
    return days, opening


# ── вторая модель: целые сотые, написана отдельно от finance_calc ───────────
def модель(days, opening):
    b, rp, np_, debt = C(opening.get("safe_b_open")), C(opening.get("rp_open")), C(opening.get("np_open")), C(opening.get("debt_b_open"))
    rp_cr = C(opening.get("rp_cr_open"))
    out = []
    for d in days:
        cash, spend = C(d.get("cash")), C(d.get("spend"))
        handed = C(d["handed"]) if d.get("handed") is not None else cash - spend
        base = C(d["handed_fact"]) if d.get("handed_fact") is not None else handed
        aside, col, extra = C(d.get("aside")), C(d.get("collected")), C(d.get("extra_rp"))
        exp = sum(C(e["amount"]) for e in d.get("expenses") or [] if e.get("pay") != "crypto")   # наличными
        exp_cr = sum(C(e["amount"]) for e in d.get("expenses") or [] if e.get("pay") == "crypto")
        pays = sum(C(e["amount"]) for e in d.get("payouts") or [])
        ждёт = bool(d.get("pending"))
        # крипта: в РП+ дня (только подтверждённого), расход криптой, перекладка в наличные
        rp_cr += (0 if ждёт else C(d.get("collected_cr"))) - exp_cr - C(d.get("cr_cash"))
        прибыль = max(0, base) - aside - col
        a_c, c_c, n_c = (0, 0, 0) if ждёт else (aside, col, прибыль)
        if d.get("pay") is not None:
            всего = C(d["pay"]); из_б = min(всего, max(0, b + a_c)); из_чп = всего - из_б
        else:
            из_б, из_чп = C(d.get("pay_b")), C(d.get("pay_b_extra"))
        b += a_c - из_б
        rp += c_c + extra - exp
        np_ += n_c - pays - из_чп
        debt += C(d.get("ordered")) - из_б - из_чп
        out.append({"b": b, "rp": rp, "np": np_, "debt": debt, "pay_b": из_б, "pay_x": из_чп,
                    "np_plus": прибыль, "base": base, "rp_cr": rp_cr})
    return out


def проверить_ядро(days, opening, метка):
    book = calc.compute(days, opening)
    m = модель(days, opening)
    prev = C(opening.get("safe_b_open")) + C(opening.get("rp_open")) + C(opening.get("np_open"))
    for i, (d, w, src) in enumerate(zip(book["days"], m, days)):
        got = (C(d["stack_b"]), C(d["stack_rp"]), C(d["stack_np"]), C(d["debt_b"]), C(d["pay_b"]), C(d["pay_b_extra"]), C(d["np_plus"]),
               C(d["stack_rp_cr"]))
        want = (w["b"], w["rp"], w["np"], w["debt"], w["pay_b"], w["pay_x"], w["np_plus"], w["rp_cr"])
        if any(abs(g - x) > 1 for g, x in zip(got, want)):
            return беда(метка, "ядро разошлось со второй моделью", день=i, ядро=got, модель=want, вход=src)
        if abs(C(d["stack_total"]) - (w["b"] + w["rp"] + w["np"])) > 1:
            return беда(метка, "сейф ≠ сумма стопок", день=i)
        # сейф дня = сейф вчера + движения; неподтверждённая выручка не входит
        ждёт = bool(src.get("pending"))
        # в сейф и из сейфа ходят только наличные: оплаченное криптой его не трогает
        exp = sum(C(e["amount"]) for e in src.get("expenses") or [] if e.get("pay") != "crypto"); pays = sum(C(e["amount"]) for e in src.get("payouts") or [])
        движ = (0 if ждёт else max(0, w["base"])) + C(src.get("extra_rp")) - exp - pays - w["pay_b"] - w["pay_x"]
        if abs(C(d["stack_total"]) - prev - движ) > 1:
            return беда(метка, "сейф дня ≠ вчера + движения", день=i, было=prev, движ=движ, стало=C(d["stack_total"]))
        prev = C(d["stack_total"])
        if src.get("pay") is not None and C(d["pay_b"]) > max(0, (m[i - 1]["b"] if i else C(opening.get("safe_b_open"))) + (0 if ждёт else C(src.get("aside")))) + 1:
            return беда(метка, "из стопки Барракуды взято больше, чем в ней было", день=i)
    s = book["safe"]
    last = m[-1] if m else {"b": C(opening.get("safe_b_open")), "rp": C(opening.get("rp_open")), "np": C(opening.get("np_open"))}
    if (abs(C(s["b"]) - last["b"]), abs(C(s["rp"]) - last["rp"]), abs(C(s["np"]) - last["np"])) > (1, 1, 1):
        return беда(метка, "сейф месяца ≠ стопки последнего дня")
    for имя, o, i_, u in (("Б", "open_b", "b_in", "b_out"), ("ЧП", "open_np", "np_in", "np_out")):
        ключ = {"Б": "b", "ЧП": "np"}[имя]
        if abs(C(s[o]) + C(s[i_]) - C(s[u]) - C(s[ключ])) > len(days) + 1:      # допуск: по сотой на день округления
            return беда(метка, f"стопка {имя}: начало + приход − расход ≠ остаток", s=s)
    # РП — фонд целиком, наличные и крипта вместе: РП+ и РП− считают обе части
    if abs(C(s["open_rp"]) + C(s["open_rp_cr"]) + C(s["rp_in"]) - C(s["rp_out"]) - C(s["rp"]) - C(s["rp_cr"])) > len(days) + 1:
        return беда(метка, "РП: начало + приход − расход ≠ наличные + крипта", s=s)
    if abs(C(s["rp_cr"]) - (m[-1]["rp_cr"] if m else C(opening.get("rp_cr_open")))) > 1 or abs(C(s["rp_all"]) - C(s["rp"]) - C(s["rp_cr"])) > 1:
        return беда(метка, "крипта РП месяца ≠ последнему дню", s=s)
    return book


def свойства(r, days, opening, метка):
    """Подтверждение одного дня меняет сейф ровно на его выручку (не больше нуля
    — если она в минусе) и ни на что другое; повторный расчёт ничего не меняет."""
    a = calc.compute(days, opening)
    if json.dumps(a, default=str, sort_keys=True) != json.dumps(calc.compute(days, opening), default=str, sort_keys=True):
        return беда(метка, "два расчёта одного месяца дали разное")
    ждут = [i for i, d in enumerate(days) if d.get("pending")]
    if not ждут:
        return
    i = r.choice(ждут)
    d2 = [dict(d) for d in days]; d2[i]["pending"] = False; d2[i]["ok"] = True
    b = calc.compute(d2, opening)
    # pay делится по наличию в стопке, поэтому сравниваем сейф целиком, не стопки
    разница = C(b["safe"]["total"]) - C(a["safe"]["total"])
    база = C(a["days"][i]["base"])
    if abs(разница - max(0, база)) > 1:
        return беда(метка, "подтверждение дня изменило сейф не на его выручку", день=i, выручка=база, разница=разница)


# ── экран: настоящие функции приложения в node ─────────────────────────────
def _функция(src, имя):
    i = src.index(f"function {имя}(")
    j = src.index("{", src.index(")", i)); depth = 0
    for k in range(j, len(src)):
        depth += src[k] == "{"; depth -= src[k] == "}"
        if depth == 0:
            return src[i:k + 1]
    raise ValueError(имя)


def экран(books):
    """books: [{days, opening, safe}] в том виде, в каком книгу получает
    приложение. Возвращает список расхождений."""
    src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
    js = "\n".join(_функция(src, f) for f in ("fbDdcParts", "fbDdcStart", "fbDdcMonthParts", "fbBParts"))
    js += r'''
const fs = require('fs');
const books = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bad = [];
const n = v => +v || 0;
books.forEach((b, bi) => {
  (b.days || []).forEach(d => {
    if(d.future) return;
    const st = fbDdcStart(b, d.day), p = fbDdcParts(d);
    const end = st.all + p.inSum - p.outSum;
    if(Math.round(end - n(d.stack_total))) bad.push({book: bi, day: d.day, what: 'ДДС дня не сходится с сейфом', end, safe: d.stack_total});
    const sub = a => a.reduce((t, r) => t + (r.items || []).reduce((q, x) => q + x.v, 0), 0);
    // «за что» под движением в сумме даёт само движение (кроме тех, где подстрок нет)
    [...p.ins, ...p.outs].forEach(r => { const s = (r.items || []).reduce((q, x) => q + x.v, 0);
      if((r.items || []).length && Math.abs(s - r.v) > 0.011) bad.push({book: bi, day: d.day, what: 'подстроки ≠ строке ' + r.name, row: r.v, items: s}); });
    const B = fbBParts(d, b);
    if(B.diff) bad.push({book: bi, day: d.day, what: 'Барракуда дня не сходится со стопкой', end: B.end, stack: d.stack_b});
    // Оплата одной суммой (pay) берёт из стопки не больше, чем в ней есть: день с
    // такой оплатой не может увести стопку ниже нуля (ниже, чем она уже была).
    if(d.manual && d.manual.pay != null && B.end < Math.min(0, B.from + B.aside) - 0.011) bad.push({book: bi, day: d.day, what: 'оплата увела стопку Барракуды в минус', from: B.from, end: B.end});
  });
  const m = fbDdcMonthParts(b);
  const end = m.from + m.p.inSum - m.p.outSum;
  if(Math.round(end - n(m.fact))) bad.push({book: bi, what: 'ДДС месяца не сходится с сейфом', end, safe: m.fact});
  // месяц = сумма дней
  let s = 0; (b.days || []).filter(d => !d.future).forEach(d => { const p = fbDdcParts(d); s += p.inSum - p.outSum; });
  if(Math.abs(s - (m.p.inSum - m.p.outSum)) > 0.011) bad.push({book: bi, what: 'ДДС месяца ≠ сумме дней', days: s, month: m.p.inSum - m.p.outSum});
});
const kinds = {}; bad.forEach(x => { kinds[x.what] = (kinds[x.what] || 0) + 1; });
console.log(JSON.stringify({n: books.length, bad: bad.slice(0, 20), total: bad.length, kinds}));
'''
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "t.js"), "w", encoding="utf-8").write(js)
        json.dump(books, open(os.path.join(tmp, "b.json"), "w", encoding="utf-8"), default=str)
        r = subprocess.run(["node", os.path.join(tmp, "t.js"), os.path.join(tmp, "b.json")], capture_output=True, text=True)
    if r.returncode:
        return [{"what": "node упал", "err": r.stderr[-600:]}], 0
    res = json.loads(r.stdout)
    if res.get("kinds"):
        res["bad"].insert(0, {"по видам": res["kinds"]})
    return res["bad"], res["total"]


def для_экрана(book, days, opening):
    """Достроить книгу ядра до того, что отдаёт сервер (finance_routes.build)."""
    for d, src in zip(book["days"], days):
        d.update(pending=bool(src.get("pending")), future=False, ins=[], extra_manual=src.get("extra_rp"),
                 manual={k: src.get(k) for k in calc.DAY_MANUAL}, supplies=[])
    return {"days": book["days"], "opening": opening, "safe": book["safe"], "month": "2026-09"}


# ── сервер целиком: настоящая книга и настоящие ручки на базе в памяти ──────
МЕС, СЛЕД, СЕГОДНЯ = "2026-09", "2026-10", "2026-09-24"
КАДР = None


async def _сервер_готовь():
    import base64
    from mongomock_motor import AsyncMongoMockClient
    import db, finance_routes as fr, owner_auth, rates, backdate
    global КАДР
    КАДР = base64.b64encode(b"\xff\xd8" + b"o" * 3000).decode()
    db._db = AsyncMongoMockClient()["fuzz_fin_%d" % random.randrange(10 ** 9)]
    owner_auth.install_validator(lambda s: {"id": 1, "first_name": "Фазз"} if s == "1" else None)
    fr._biz_day = lambda *a, **k: СЕГОДНЯ

    async def _курс():                                   # без сети
        return {"rates": [{"code": "USD", "aed": 3.6725, "cash_aed": 3.67}]}
    rates.get_rates = _курс

    async def _молча(*a, **k):
        return None
    backdate.notify = _молча
    return db, fr


async def _зови(h, method, body):
    from aiohttp.test_utils import make_mocked_request
    r = make_mocked_request(method, "/x", headers={"Authorization": "tma 1"})

    async def js():
        return body
    r.json = js
    resp = await h(r)
    try:
        return resp.status, json.loads(resp.text)
    except Exception:                                    # noqa: BLE001
        return resp.status, {}


def _книга_ок(book, метка, шаг):
    """Те же законы, что у ядра, но на готовой книге сервера."""
    o = book["opening"]
    prev = C(o.get("safe_b_open")) + C(o.get("rp_open")) + C(o.get("np_open"))
    долг = C(o.get("debt_b_open"))
    for d in book["days"]:
        if abs(C(d["stack_total"]) - C(d["stack_b"]) - C(d["stack_rp"]) - C(d["stack_np"])) > 1:
            return беда(метка, "сервер: сейф ≠ сумма стопок", шаг=шаг, день=d["day"])
        ждёт = bool(d.get("pending"))
        движ = (0 if ждёт else max(0, C(d["base"]))) + C(d["extra_rp"]) - C(d["expenses_sum"]) - C(d["payouts_sum"]) \
            - C(d["pay_b"]) - C(d["pay_b_extra"])
        if abs(C(d["stack_total"]) - prev - движ) > 1:
            return беда(метка, "сервер: сейф дня ≠ вчера + движения", шаг=шаг, день=d["day"],
                        было=prev / 100, движ=движ / 100, стало=d["stack_total"])
        prev = C(d["stack_total"])
        долг += C(d["ordered"]) - C(d["pay_b"]) - C(d["pay_b_extra"])
        if abs(долг - C(d["debt_b"])) > 1:
            return беда(метка, "сервер: долг Барракуде ≠ вчера + заявка − оплата", шаг=шаг, день=d["day"])
        if d.get("future") and (C(d["base"]) or C(d["expenses_sum"]) or d.get("pending")):
            return беда(метка, "сервер: в будущем дне есть деньги", шаг=шаг, день=d["day"])
        if abs(C(d["expenses_sum"]) - sum(C(e["amount"]) for e in d["expenses"])) > 1:
            return беда(метка, "сервер: сумма расходов ≠ записям", шаг=шаг, день=d["day"])
        # выручка дня: наличные минус чай и расходы водителей наличными (или вписанный факт)
        if d["manual"].get("handed_fact") is None and abs(C(d["base"]) - (C(d["cash"]) - C(d["tips_cash"]) - C(d["spend_cash"]))) > 1:
            return беда(метка, "сервер: выручка ≠ наличные − чай − расходы водителей", шаг=шаг, день=d["day"])
    if abs(C(book["safe"]["total"]) - prev) > 1:
        return беда(метка, "сервер: сейф месяца ≠ последнему дню", шаг=шаг)
    return True


async def сервер(seed, шагов=30):
    r = random.Random(seed)
    db, fr = await _сервер_готовь()
    метка = f"сервер seed={seed}"
    дни = [f"{МЕС}-{i:02d}" for i in range(1, 31)]
    прошлые = [d for d in дни if d <= СЕГОДНЯ]
    записи, замороз, снимки, n = [], {}, [], [0]

    async def заказ():
        n[0] += 1
        день = r.choice(прошлые)
        способ = r.choice(["", "", "", "crypto", "debt", "free", "transfer"])
        await db._db.orders.insert_one({
            "order_id": f"F{n[0]}", "timestamp": f"{день}T{r.randint(8, 19):02d}:{r.randint(0, 59):02d}:00",
            "status": r.choice(["delivered"] * 5 + ["cancelled"]), "total": r.choice([150, 200, 320, 1150, 87.5]),
            "tip": r.choice([0, 0, 20, 50]), "office_id": r.choice(["jvc", "tecom"]),
            **({"payment_method": способ, "paid": способ in ("crypto", "transfer")} if способ else {}),
            **({"test": True} if r.random() < 0.05 else {})})

    async def расход_водителя():
        n[0] += 1
        await db.add_driver_expense(r.choice(прошлые), r.choice(["Худоба", "Фарух"]), {
            "id": f"e{n[0]}", "amount": r.choice([40, 86, 120, 760, 12.5]), "kind": r.choice(["fuel", "wash", "other", "we_got"]),
            "status": r.choice(["approved", "approved", "pending", "rejected"]), "pay": r.choice(["cash", "cash", "card"]),
            "by_driver": "x"})

    async def подтвердить():
        день = r.choice(прошлые)
        book = await fr.build(МЕС, light=True)
        d = next(x for x in book["days"] if x["day"] == день)
        a, c = r.choice([d.get("aside") or 0, 0, 500]), r.choice([d.get("collected") or 0, 0, 300])
        код, _ = await _зови(fr.handle_day_ok, "POST", {"day": день, "aside": a, "collected": c, "as": "Фазз"})
        if код == 200:
            замороз[день] = (a, c)

    async def поле():
        день = r.choice(прошлые)
        f = r.choice(["handed_fact", "aside", "collected", "extra_rp", "pay", "ordered_fact"])
        v = r.choice([None, 0, 100, 2500, 9000, 33.33])
        код, _ = await _зови(fr.handle_day_set, "POST", {"day": день, "field": f, "value": v, "as": "Фазз"})
        if код == 200 and f in ("aside", "collected") and день in замороз:
            a, c = замороз[день]
            # снятое поле у подтверждённого дня возвращается к предложению — заморозки больше нет
            замороз.pop(день) if v is None else замороз.__setitem__(день, (v, c) if f == "aside" else (a, v))

    async def запись():
        book_ = r.choice(["rp", "np", "in", "rp"])
        body = {"day": r.choice(прошлые), "book": book_, "amount": r.choice([25, 192, 283, 1000, 12.5]),
                "comment": "фазз", "as": "Фазз"}
        if book_ == "rp":
            if r.random() < 0.5:
                body.update(kind=r.choice(["salary", "advance"]), who="Худоба")
            else:
                body.update(photo=КАДР, thumb="")
        if book_ == "np":
            body["who"] = "владелец"
        if book_ == "in" and r.random() < 0.5:
            body["src"] = "crypto"
        код, отв = await _зови(fr.handle_entry_add, "POST", body)
        if код == 200 and отв.get("id"):
            записи.append(отв["id"])

    async def убрать():
        if записи:
            await _зови(fr.handle_entry_del, "DELETE", {"id": записи.pop(r.randrange(len(записи))), "as": "Фазз"})

    async def начало():
        await _зови(fr.handle_month_set, "POST", {"month": МЕС, "field": r.choice(["safe_b_open", "rp_open", "np_open", "debt_b_open"]),
                                                  "value": r.choice([None, 0, 5000, 30902, -22]), "as": "Фазз"})

    ходы = [заказ] * 5 + [расход_водителя] * 2 + [подтвердить] * 3 + [поле] * 3 + [запись] * 3 + [убрать, начало]
    for шаг in range(шагов):
        ход = r.choice(ходы)
        try:
            await ход()
        except Exception as e:                           # noqa: BLE001
            return беда(метка, f"ход {ход.__name__} упал: {type(e).__name__}: {e}", шаг=шаг)
        book = await fr.build(МЕС)
        if _книга_ок(book, метка, f"{шаг}:{ход.__name__}") is not True:
            return
        # подтверждённая раскладка заморожена: новые заказы её не двигают
        for день, (a, c) in замороз.items():
            d = next(x for x in book["days"] if x["day"] == день)
            if d["ok"] and (abs(C(d["aside"]) - C(a)) > 1 or abs(C(d["collected"]) - C(c)) > 1):
                return беда(метка, "подтверждённая раскладка поплыла", шаг=шаг, день=день, было=(a, c), стало=(d["aside"], d["collected"]))
        if шаг % 6 == 5 or шаг == шагов - 1:
            снимки.append(json.loads(json.dumps({k: book[k] for k in ("days", "opening", "safe", "month")}, default=str)))
            # перенос: следующий месяц начинается с того, чем кончился этот
            nxt = await fr.build(СЛЕД, light=True)
            s, o = book["safe"], nxt["safe"]
            if (C(o["open_b"]), C(o["open_rp"]), C(o["open_np"])) != (C(s["b"]), C(s["rp"]), C(s["np"])):
                return беда(метка, "перенос на следующий месяц ≠ остатку этого", шаг=шаг,
                            конец=(s["b"], s["rp"], s["np"]), начало=(o["open_b"], o["open_rp"], o["open_np"]))
    return снимки


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", type=int, default=20000); ap.add_argument("--screen", type=int, default=2000)
    ap.add_argument("--server", type=int, default=12); ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261001); ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.quick:
        a.core, a.screen, a.server, a.steps = 1500, 300, 2, 12
    r = random.Random(a.seed)
    books = []
    for i in range(a.core):
        days, op = месяц(r)
        b = проверить_ядро(days, op, f"ядро seed={a.seed} #{i}")
        свойства(r, days, op, f"ядро seed={a.seed} #{i}")
        if b and len(books) < a.screen:
            books.append(для_экрана(b, days, op))
        if len(БЕДЫ) >= 10:
            break
    print(f"ядро: {a.core} месяцев против второй модели — бед {len(БЕДЫ)}")
    плохо, всего = экран(books)
    print(f"экран (ДДС дня, ДДС месяца, Барракуда): {len(books)} месяцев — расхождений {всего}")
    for x in плохо[:8]:
        print("   ", x)
    было = len(БЕДЫ)
    серверные = []
    for k in range(a.server):
        снимки = asyncio.run(сервер(a.seed + k, a.steps))
        серверные += снимки or []
    print(f"сервер целиком: {a.server} прогонов по {a.steps} ходов — бед {len(БЕДЫ) - было}")
    плохо2, всего2 = экран(серверные) if серверные else ([], 0)
    print(f"экран на книгах сервера: {len(серверные)} снимков — расхождений {всего2}")
    for x in плохо2[:8]:
        print("   ", x)
    for где, что, ctx in БЕДЫ[:12]:
        print(f"\nБЕДА [{где}] {что}")
        for k, v in ctx.items():
            print(f"    {k}: {str(v)[:400]}")
    чисто = not БЕДЫ and not всего and not всего2
    print("\nИТОГ:", "всё сошлось" if чисто else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if чисто else 1


if __name__ == "__main__":
    sys.exit(main())
