"""Крипта в РП на случайных цепочках: кошелёк, распределение, расходы, вывод.

Владелец, 2 окт 2026: «это очень тонкий, но невероятно важный подсчёт… какие
угодно глубочайшие тесты проводи, прогони миллионы сценариев».

Случайная цепочка идёт через НАСТОЯЩИЕ ручки финансов и настоящую книгу крипты
на базе в памяти; кошелёк — свой, выдуманный: приходы от клиентов, переводы
между своими кошельками, уход с кошелька, молчащая сеть. Дни листаются, месяц
переходит в следующий. Рядом — вторая модель в целых сотых, написанная
отдельно: что должен ответить сервер и где после этого лежит каждый дирхам.

После КАЖДОГО шага:

  • ответ ручки = ответ модели (принято / отказ и почему);
  • свободная крипта и крипта РП у сервера = у модели; обе не меньше нуля;
  • крипта РП не в минусе НИ В ОДИН день обоих месяцев (и равна модели по дням);
  • СОХРАНЕНИЕ: открытие + пришло = свободно + на счету РП + потрачено криптой
    + выведено в наличные — ни один дирхам не появился и не пропал;
  • РП целиком: открытие + РП+ − РП− = наличные + крипта;
  • сейф (наличные) дня = вчера + движения наличных; оплаченное криптой и
    крипта в РП+ в сейф не попадают;
  • предложение: вся свободная крипта — первому дню, который ждёт; наличными —
    сколько не хватило до нормы; следующему ждущему — остаток;
  • вывод в наличные: сперва из свободной, остальное с крипта-счёта РП;
  • комиссия вывода: наша половина уходит из крипты сверх наличных, своей
    строкой РП− «криптой»; убрали вывод — ушла и она, убрали её — вывод остался;
  • перенос: следующий месяц открывается остатком этого;
  • кошелёк на экране: книга и разница с тем, что лежит на самом деле;
  • сверка с сетью дважды ничего не меняет; свои переводы — не приход.
В конце прогона — экран: ДДС дня и месяца (настоящие функции приложения в
node) обязаны сходиться с сейфом.

    python3 tools/fuzz_crypto.py                     # 150 цепочек по 90 шагов
    python3 tools/fuzz_crypto.py --runs 5000 --steps 150 --seed 7
    python3 tools/fuzz_crypto.py --real --runs 8 --steps 60     # на VPS: настоящая монга

--real — та же проверка на настоящей монге из MONGO_URI (.env): каждая цепочка
в своей временной базе ambar_fuzz_cr_*, которую стирает за собой; боевую базу
не трогает и не читает. Экран (node) в этом режиме не гоняется.
"""
import argparse, asyncio, json, os, random, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
REAL = "--real" in sys.argv
URI = ""
if REAL:                             # адрес монги — до fuzz_finance: тот его стирает
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    URI = os.environ.get("MONGO_URI", "")
import fuzz_finance as FF                                          # noqa: E402

C = lambda v: int(round(float(v or 0) * 100))                      # noqa: E731
БЕДЫ = []
ВЕТКИ = {}
МИР = {}
СТАРТ = "2026-10-01"
КУРС = 3.5


def ветка(k):
    ВЕТКИ[k] = ВЕТКИ.get(k, 0) + 1


def беда(seed, шаг, что, дали=None, ждали=None, след=()):
    БЕДЫ.append((seed, шаг, что, дали, ждали))
    if len(БЕДЫ) <= 3 and not МИР.get("тихо"):
        print(f"  БЕДА зерно={seed} шаг={шаг}: {что}\n       получили {дали!r}\n       ждали    {ждали!r}")
        for t in list(след)[-14:]:
            print("       …", t)


def _дни(месяц):
    n = 31 if месяц.endswith("10") else 30
    return [f"{месяц}-{i:02d}" for i in range(1, n + 1)]


def _завтра(day):
    from datetime import date, timedelta
    y, m, d = map(int, day.split("-"))
    return (date(y, m, d) + timedelta(days=1)).isoformat()


# ── вторая модель: целые сотые ───────────────────────────────────────────────
class Модель:
    def __init__(self):
        self.ready = False
        self.open = 0
        self.inflow = 0                 # дочитанный сервером приход
        self.ждут = []                  # приходы, которых сервер ещё не видел: сотые
        self.alloc = {}                 # день → сотые (подтверждённые)
        self.ok = set()
        self.поле = {}                  # день → collected_cr в документе дня (сотые) или нет ключа
        self.записи = {}                # id → {вид, день, сумма, pay, f, r}

    def движения(self):
        по = {}
        for d, v in self.alloc.items():
            if v:
                по[d] = по.get(d, 0) + v
        for e in self.записи.values():
            if e["вид"] == "rp" and e["pay"] == "crypto":
                по[e["день"]] = по.get(e["день"], 0) - e["сумма"]
            elif e["вид"] == "in":
                # вывод: с крипта-счёта РП ушло r; часть комиссии из свободной (ff)
                # на счёт РП пришла — и тут же потрачена своей строкой расхода
                по[e["день"]] = по.get(e["день"], 0) - e["r"] + e.get("ff", 0)
        return {d: v for d, v in по.items() if v}

    def rp(self):
        return sum(self.движения().values())

    def free(self):
        return self.open + self.inflow - sum(self.alloc.values()) \
            - sum(e["f"] + e.get("ff", 0) for e in self.записи.values() if e["вид"] == "in")

    def на_конец(self, day):
        return sum(v for d, v in self.движения().items() if d <= day)

    def мин_с(self, day):
        """Сколько можно забрать из крипты РП днём day: наименьший остаток на
        конец этого дня и каждого следующего."""
        по = self.движения()
        точки = [day] + [d for d in по if d > day]
        return min(sum(v for d, v in по.items() if d <= t) for t in точки)

    def потрачено(self):
        return sum(e["сумма"] for e in self.записи.values() if e["вид"] == "rp" and e["pay"] == "crypto")

    def выведено(self):
        return sum(e["f"] + e["r"] for e in self.записи.values() if e["вид"] == "in")

    def раскладка(self, day, new, was):
        """Ответ на «положить new крипты в РП+ дня» — None или причина отказа."""
        if new <= 0 and was <= 0:
            return None
        if new > 0 and not self.ready:
            return "crypto_unready"
        if new > 0 and day < СТАРТ:
            return "crypto_early"
        if new - was > self.free():
            return "no_free"
        if new < was and was - new > self.мин_с(day):
            return "cr_spent"
        return None


# ── подготовка ───────────────────────────────────────────────────────────────
async def _готовь_настоящую(seed):
    """То же, что FF._сервер_готовь, но база — временная на настоящей монге."""
    import base64
    import motor.motor_asyncio
    import db, finance_routes as fr, owner_auth, rates, backdate
    FF.КАДР = base64.b64encode(b"\xff\xd8" + b"o" * 3000).decode()
    имя = f"ambar_fuzz_cr_{int(time.time())}_{seed}"
    assert имя.startswith("ambar_fuzz_cr_") and URI
    opts = {"serverSelectionTimeoutMS": 8000}
    if URI.startswith("mongodb+srv://") or "tls=true" in URI or "ssl=true" in URI:
        import certifi
        opts["tlsCAFile"] = certifi.where()
    МИР["клиент"] = motor.motor_asyncio.AsyncIOMotorClient(URI, **opts)
    МИР["база"] = имя
    db._db = МИР["клиент"][имя]
    owner_auth.install_validator(lambda s: {"id": 1, "first_name": "Фазз"} if s == "1" else None)

    async def _курс():
        return {"rates": [{"code": "USD", "aed": 3.6725, "cash_aed": 3.67}]}
    rates.get_rates = _курс

    async def _молча(*a, **k):
        return None
    backdate.notify = _молча
    # На сервере в .env настоящие токены ботов: ни одно сообщение из прогона
    # уйти не должно. 2 окт 2026 первый запуск шёл через проверку прав, та не
    # узнала выдуманного «Фазза» и подняла владельцам тревогу о попытке входа.
    import owner_routes
    owner_auth.install_alerter(None)
    for имя in ("notify_owners", "notify_owners_force", "notify_owners_photo"):
        if hasattr(owner_routes, имя):
            setattr(owner_routes, имя, _молча)
    return db, fr


async def готовь(seed):
    db, fr = await (_готовь_настоящую(seed) if REAL else FF._сервер_готовь())
    import tron, wallet_routes as wr, crypto_book as cb, pay_notify
    МИР.update(today="2026-10-02", bal={"MAIN": 0.0, "OLD": 0.0}, tr={"MAIN": [], "OLD": []},
               net=True, n=0, last_ts=0, тихо=МИР.get("тихо"))
    fr._biz_day = lambda *a, **k: МИР["today"]
    wr.TRON_RECEIVE_ADDRESS = "MAIN"

    async def _old():
        return ["OLD"]
    wr.old_addresses = _old
    wr._rate = lambda: КУРС

    async def _bal(a):
        return {"usdt": МИР["bal"][a], "trx": 9.0} if МИР["net"] else None

    async def _tr(a, limit=200, pages=6, min_ts=0):
        return [dict(t) for t in МИР["tr"][a] if t["ts"] >= min_ts] if МИР["net"] else None
    tron.get_balance, tron.get_transfers = _bal, _tr

    async def _тихо(*a, **k):
        return None
    pay_notify.tell_safe = _тихо

    async def _пусто(*a, **k):
        return {}
    wr._crypto_orders = _пусто
    return db, fr, cb, wr


def _перевод(кошелёк, usdt, вход, peer):
    МИР["n"] += 1
    МИР["bal"][кошелёк] = round(МИР["bal"][кошелёк] + (usdt if вход else -usdt), 6)
    # Время блока — как в жизни: не позже «сейчас» и строго по порядку.
    МИР["last_ts"] = max(МИР.get("last_ts", 0) + 1, int(time.time() * 1000))
    МИР["tr"][кошелёк].append({"txid": f"t{МИР['n']:05d}", "amount": usdt, "in": вход, "peer": peer,
                               "ts": МИР["last_ts"]})


def _дождись():
    """Часы сети не обгоняют настоящие: книга, заведённая сейчас, не должна
    увидеть «будущий» перевод, который уже лежит в остатке."""
    while int(time.time() * 1000) <= МИР.get("last_ts", 0):
        time.sleep(0.001)


# ── один прогон ──────────────────────────────────────────────────────────────
async def прогон(seed, шагов):
    try:
        return await _прогон(seed, шагов)
    finally:
        if REAL and МИР.get("клиент") is not None:
            await МИР["клиент"].drop_database(МИР["база"])
            МИР["клиент"].close(); МИР["клиент"] = None


def _голая(h):
    """Ручка без проверки прав: на сервере владельцы настоящие, и выдуманного
    «Фазза» среди них нет — права проверяет tools/audit_auth.py, здесь считаем."""
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


async def _прогон(seed, шагов):
    r = random.Random(seed)
    db, fr, cb, wr = await готовь(seed)
    зови = (lambda h, m, b: FF._зови(_голая(h), m, b)) if REAL else FF._зови
    м = Модель()
    след = []
    было = len(БЕДЫ)
    МИР["bal"]["MAIN"] = r.choice([0.0, 0.43203, 12.0, 415.43])
    for мес in ("2026-10", "2026-11"):
        await зови(fr.handle_month_set, "POST", {"month": мес, "field": "norm", "value": r.choice([0, 3000, 9700]), "as": "Ф"})

    def прошлые():
        return [d for d in _дни("2026-10") + _дни("2026-11") if d <= МИР["today"]] + ["2026-09-30"]

    def сумма():
        return r.choice([25, 100, 192.5, 500, 1000, 2000, 3333.33, 0.5, 7000])

    async def синхр():
        до = МИР["net"]
        _дождись()
        res = await cb.sync()
        if not до:
            if res["ok"]:
                беда(seed, шаг, "сеть молчит, а сверка говорит «всё прочитано»", res, False, след)
            return
        if not м.ready:
            м.ready = True
            м.open = C(round((МИР["bal"]["MAIN"] + МИР["bal"]["OLD"]) * КУРС, 2))
            м.ждут.clear()               # что пришло до книги — уже в открытии
        м.inflow += sum(м.ждут)
        if res["new"] < len(м.ждут):
            беда(seed, шаг, "сверка: новых переводов меньше, чем пришло", res["new"], len(м.ждут), след)
        м.ждут.clear()

    for шаг in range(шагов):
        a = r.random()
        if шаг == 0 or a < 0.10:                                     # сверка с сетью
            МИР["net"] = r.random() > 0.15
            await синхр()
            след.append(f"сверка, сеть {'есть' if МИР['net'] else 'молчит'}")
            if МИР["net"] and r.random() < 0.3:                      # дважды — без удвоения
                res = await cb.sync()
                if res["new"]:
                    беда(seed, шаг, "повторная сверка записала переводы второй раз", res["new"], 0, след)
            МИР["net"] = True
            ветка("сверка")

        elif a < 0.22:                                               # клиент заплатил
            usdt = r.choice([10.0, 57.14, 100.0, 285.714286, 1000.0, 0.5, 33.333333, 571.43])
            _перевод(r.choice(["MAIN", "MAIN", "OLD"]), usdt, True, f"TClient{r.randint(1, 9)}")
            м.ждут.append(C(round(usdt * КУРС, 2)))
            след.append(f"пришло {usdt} USDT")
            if r.random() < 0.7:
                await синхр()
            ветка("приход")

        elif a < 0.26:                                               # между своими / ушло наружу
            if r.random() < 0.5 and МИР["bal"]["MAIN"] > 1:
                usdt = round(МИР["bal"]["MAIN"] * r.choice([0.3, 1.0]), 6)
                _перевод("MAIN", usdt, False, "OLD"); _перевод("OLD", usdt, True, "MAIN")
                след.append(f"между своими {usdt}"); ветка("между своими")
            else:
                к = r.choice(["MAIN", "OLD"])
                if МИР["bal"][к] > 1:
                    usdt = round(МИР["bal"][к] * r.choice([0.5, 1.0]), 6)
                    _перевод(к, usdt, False, "TOut")
                    след.append(f"ушло наружу {usdt}"); ветка("уход")
            if r.random() < 0.5:
                await синхр()

        elif a < 0.36:                                               # выручка наличными
            день = r.choice(прошлые())
            МИР["n"] += 1
            await db._db.orders.insert_one({
                "order_id": f"F{МИР['n']}", "timestamp": f"{день}T{r.randint(11, 19):02d}:10:00",
                "status": "delivered", "total": r.choice([150, 2000, 12000, 40000]), "tip": 0, "office_id": "jvc"})
            след.append(f"выручка {день}")

        elif a < 0.43:                                               # следующий день
            if МИР["today"] < "2026-11-06":
                МИР["today"] = _завтра(МИР["today"])
                след.append(f"наступило {МИР['today']}"); ветка("день")

        elif a < 0.60:                                               # подтвердить раскладку
            день = r.choice(прошлые())
            book = await fr.build(день[:7], light=True)
            d = next((x for x in book["days"] if x["day"] == день), None)
            if not d:
                continue
            k = r.random()
            тело = {"day": день, "aside": d.get("aside") or 0, "collected": d.get("collected") or 0, "as": "Ф"}
            if k < 0.5:
                cr = d.get("collected_cr") or 0
            elif k < 0.65:
                cr = 0
            elif k < 0.9:
                cr = round(max(0, м.free()) / 100 * r.choice([0.3, 1, 1.4]) + r.choice([0, 0, 50]), 2)
            else:
                cr = None
            if cr is not None:
                тело["collected_cr"] = cr
            new = C(cr) if cr is not None else м.поле.get(день, 0)
            was = м.alloc.get(день, 0) if день in м.ok else 0
            ждём = м.раскладка(день, new, was)
            код, отв = await зови(fr.handle_day_ok, "POST", тело)
            след.append(f"подтвердить {день} криптой {cr} → {код} {отв.get('error') or ''}")
            ветка("подтвердить:" + (отв.get("error") or "ok"))
            if (отв.get("error") if код != 200 else None) != ждём:
                беда(seed, шаг, "подтвердить день: ответ", отв.get("error") or код, ждём or 200, след); break
            if код == 200:
                м.ok.add(день); м.alloc[день] = new; м.поле[день] = new

        elif a < 0.68:                                               # вписать / снять крипту дня
            день = r.choice(прошлые())
            v = r.choice([None, 0, round(max(0, м.free()) / 100 * r.choice([0.5, 1, 1.2]), 2), 100, 2500])
            new = C(v)
            was = м.alloc.get(день, 0) if день in м.ok else 0
            ждём = м.раскладка(день, new, was)
            код, отв = await зови(fr.handle_day_set, "POST", {"day": день, "field": "collected_cr", "value": v, "as": "Ф"})
            след.append(f"крипта дня {день} = {v} → {код} {отв.get('error') or ''}")
            ветка("поле:" + (отв.get("error") or "ok"))
            if (отв.get("error") if код != 200 else None) != ждём:
                беда(seed, шаг, "вписать крипту дня: ответ", отв.get("error") or код, ждём or 200, след); break
            if код == 200:
                if v is None:
                    м.поле.pop(день, None)
                else:
                    м.поле[день] = new
                if день in м.ok:
                    м.alloc[день] = new

        elif a < 0.80:                                               # расход из РП / зарплата
            день = r.choice(прошлые())
            amt = сумма()
            чем = r.choice(["", "cash", "crypto", "crypto"])
            if r.random() < 0.75:
                h, тело = fr.handle_entry_add, {"day": день, "book": "rp", "amount": amt, "comment": "фазз",
                                                "photo": FF.КАДР, "thumb": "", "pay": чем, "as": "Ф"}
            else:
                h, тело = fr.handle_pay_out, {"name": "Человек", "amount": amt, "day": день, "pay": чем, "as": "Ф"}
            ждём = None
            if чем == "crypto":
                ждём = "crypto_unready" if not м.ready else "no_crypto" if C(amt) > м.мин_с(день) else None
            код, отв = await зови(h, "POST", тело)
            след.append(f"расход {день} {amt} {чем or 'наличными'} → {код} {отв.get('error') or ''}")
            ветка("расход:" + (чем or "cash") + ":" + (отв.get("error") or "ok"))
            if (отв.get("error") if код != 200 else None) != ждём:
                беда(seed, шаг, "расход из РП: ответ", отв.get("error") or код, ждём or 200, след); break
            if ждём == "no_crypto" and C(отв.get("have")) != м.мин_с(день):
                беда(seed, шаг, "отказ: сколько крипты есть", отв.get("have"), м.мин_с(день) / 100, след)
            if код == 200:
                м.записи[отв["id"]] = {"вид": "rp", "день": день, "сумма": C(amt), "pay": чем if чем == "crypto" else "", "f": 0, "r": 0}

        elif a < 0.90:                                               # вывод крипты в наличные
            день = r.choice(прошлые())
            amt = сумма()
            # комиссия — в USDT, как её сняли; на нас половина
            usdt = r.choice([0, 0, 0, 1, 2.5, 4, 10.33, 0.01])
            наша = C(round(round(usdt * КУРС, 2) * 0.5, 2))
            ждём, f, rr, ff = None, 0, 0, 0
            if not м.ready:
                ждём = "crypto_unready"
            else:
                св, рп = max(0, м.free()), max(0, м.мин_с(день))
                надо = C(amt) + наша
                if надо > св + рп:
                    ждём = "no_crypto"
                else:
                    из_св = min(надо, св)
                    f = min(C(amt), из_св); ff = из_св - f; rr = C(amt) - f
            тело = {"day": день, "book": "in", "amount": amt, "comment": "Из крипты", "src": "crypto", "as": "Ф"}
            if usdt:
                тело["fee_usdt"] = usdt
            код, отв = await зови(fr.handle_entry_add, "POST", тело)
            след.append(f"вывод {день} {amt} комиссия {usdt} USDT → {код} {отв.get('error') or ''} своб {отв.get('cr_free')} "
                        f"рп {отв.get('cr_rp')} наша {отв.get('fee_ours')} из своб {отв.get('fee_free')}")
            ветка("вывод:" + (отв.get("error") or ("из свободной" if rr == 0 else "с РП" if f == 0 else "пополам")))
            if (отв.get("error") if код != 200 else None) != ждём:
                беда(seed, шаг, "вывод в наличные: ответ", отв.get("error") or код, ждём or 200, след); break
            if код == 200:
                if (C(отв.get("cr_free")), C(отв.get("cr_rp"))) != (f, rr):
                    беда(seed, шаг, "вывод: сколько из свободной и сколько с РП",
                         (отв.get("cr_free"), отв.get("cr_rp")), (f / 100, rr / 100), след); break
                if (C(отв.get("fee_ours")), C(отв.get("fee_free")), bool(отв.get("fee_id"))) != (наша, ff if наша else 0, наша > 0):
                    беда(seed, шаг, "вывод: наша половина комиссии и сколько её из свободной",
                         (отв.get("fee_ours"), отв.get("fee_free"), отв.get("fee_id")), (наша / 100, ff / 100, наша > 0), след); break
                м.записи[отв["id"]] = {"вид": "in", "день": день, "сумма": C(amt), "pay": "", "f": f, "r": rr,
                                       "ff": ff if наша else 0, "ком": отв.get("fee_id") or ""}
                if наша:
                    м.записи[отв["fee_id"]] = {"вид": "rp", "день": день, "сумма": наша, "pay": "crypto", "f": 0, "r": 0,
                                               "вывод": отв["id"]}
                    ветка("вывод:с комиссией" + (":часть с РП" if ff < наша else ""))

        elif a < 0.96:                                               # убрать запись
            if м.записи:
                eid = r.choice(sorted(м.записи))
                код, отв = await зови(fr.handle_entry_del, "DELETE", {"id": eid, "as": "Ф"})
                след.append(f"убрать {eid} {м.записи[eid]} → {код}")
                if код != 200:
                    беда(seed, шаг, "убрать запись", код, 200, след); break
                e = м.записи.pop(eid); ветка("убрать")
                if e.get("ком") and e["ком"] in м.записи:            # вывод тянет свою комиссию
                    del м.записи[e["ком"]]; ветка("убрать:вывод с комиссией")
                if e.get("вывод") and e["вывод"] in м.записи:         # убрали одну комиссию
                    м.записи[e["вывод"]]["ff"] = 0; м.записи[e["вывод"]]["ком"] = ""
                    ветка("убрать:только комиссию")

        else:                                                        # наличные поля дня — крипту не трогают
            день = r.choice(прошлые())
            await зови(fr.handle_day_set, "POST", {"day": день, "field": r.choice(["aside", "collected", "handed_fact"]),
                                                   "value": r.choice([None, 0, 500, 9000]), "as": "Ф"})
            след.append(f"наличное поле {день}")

        if len(БЕДЫ) > было:
            break
        await сверка(seed, шаг, м, fr, cb, wr, след, r)
        if len(БЕДЫ) > было:
            break
    if len(БЕДЫ) > было:
        return None
    # Снимки для экрана: оба месяца, как их получает приложение.
    out = []
    for мес in ("2026-10", "2026-11"):
        b = await fr.build(мес)
        out.append(json.loads(json.dumps({k: b[k] for k in ("days", "opening", "safe", "month")}, default=str)))
    return out


async def сверка(seed, шаг, м, fr, cb, wr, след, r):
    st = await cb.state()
    if st["ready"] != м.ready:
        return беда(seed, шаг, "книга заведена", st["ready"], м.ready, след)
    if (C(st["free"]), C(st["rp"])) != ((м.free(), м.rp()) if м.ready else (0, м.rp())):
        return беда(seed, шаг, "свободная крипта и крипта РП", (st["free"], st["rp"]), (м.free() / 100, м.rp() / 100), след)
    if м.free() < 0 or м.rp() < 0:
        return беда(seed, шаг, "счёт в минусе", (м.free() / 100, м.rp() / 100), "≥ 0", след)
    if м.ready:
        лево = C(st["open"]) + C(st["inflow"])
        право = C(st["free"]) + C(st["rp"]) + C(st["exp"]) + C(st["wd_free"]) + C(st["wd_rp"])
        if лево != право:
            return беда(seed, шаг, "СОХРАНЕНИЕ: открытие + пришло ≠ свободно + РП + потрачено + выведено",
                        лево / 100, право / 100, след)
        if (C(st["open"]), C(st["inflow"])) != (м.open, м.inflow) or C(st["exp"]) != м.потрачено() \
                or C(st["wd_free"]) + C(st["wd_rp"]) != м.выведено():
            return беда(seed, шаг, "книга по частям", {k: st[k] for k in ("open", "inflow", "exp", "wd_free", "wd_rp")},
                        (м.open / 100, м.inflow / 100, м.потрачено() / 100, м.выведено() / 100), след)
    месяцы = ["2026-10"] + (["2026-11"] if МИР["today"] >= "2026-11-01" or шаг % 5 == 0 else [])
    книги = {}
    for мес in месяцы:
        b = книги[мес] = await fr.build(мес, light=True)
        s = b["safe"]
        if (b["crypto"]["ready"], C(b["crypto"]["free"]), C(b["crypto"]["rp"])) != (st["ready"], C(st["free"]), C(st["rp"])):
            return беда(seed, шаг, f"{мес}: крипта в книге месяца ≠ книге крипты", b["crypto"], (st["free"], st["rp"]), след)
        if abs(C(s["total"]) - C(s["b"]) - C(s["rp"]) - C(s["np"])) > 1:
            return беда(seed, шаг, f"{мес}: сейф ≠ сумме наличных стопок", s["total"], (s["b"], s["rp"], s["np"]), след)
        if abs(C(s["rp_all"]) - (C(s["open_rp"]) + C(s["open_rp_cr"]) + C(s["rp_in"]) - C(s["rp_out"]))) > 1:
            return беда(seed, шаг, f"{мес}: РП целиком ≠ открытие + РП+ − РП−", s["rp_all"],
                        (s["open_rp"], s["open_rp_cr"], s["rp_in"], s["rp_out"]), след)
        if abs(C(s["rp_all"]) - C(s["rp"]) - C(s["rp_cr"])) > 1:
            return беда(seed, шаг, f"{мес}: РП целиком ≠ наличные + крипта", s["rp_all"], (s["rp"], s["rp_cr"]), след)
        prev = C(s["open_b"]) + C(s["open_rp"]) + C(s["open_np"])
        норма = C((b.get("budget") or {}).get("norm"))
        остаток = м.free() if м.ready else 0
        for d in b["days"]:
            день = d["day"]
            if abs(C(d["stack_rp_cr"]) - м.на_конец(день)) > 0:
                return беда(seed, шаг, f"{день}: крипта РП на конец дня", d["stack_rp_cr"], м.на_конец(день) / 100, след)
            if C(d["stack_rp_cr"]) < 0:
                return беда(seed, шаг, f"{день}: крипта РП в минусе", d["stack_rp_cr"], "≥ 0", след)
            ждёт = bool(d.get("pending"))
            движ = (0 if ждёт else max(0, C(d["base"]))) + C(d["extra_rp"]) - (C(d["expenses_sum"]) - C(d["expenses_cr_sum"])) \
                - C(d["payouts_sum"]) - C(d["pay_b"]) - C(d["pay_b_extra"])
            if abs(C(d["stack_total"]) - prev - движ) > 1:
                return беда(seed, шаг, f"{день}: сейф (наличные) ≠ вчера + движения наличных",
                            d["stack_total"], (prev + движ) / 100, след)
            prev = C(d["stack_total"])
            # предложение крипты и наличных
            if d["ok"]:
                хотим = м.alloc.get(день, 0)
            elif день <= МИР["today"] and C(d["base"]) > 0 and день >= СТАРТ and м.ready:
                хотим = min(м.поле[день], max(0, остаток)) if день in м.поле else max(0, остаток)
                остаток -= хотим
            else:
                хотим = 0
            if C(d["collected_cr"]) != хотим:
                return беда(seed, шаг, f"{день}: крипта в РП+ дня (предложение или подтверждённое)",
                            d["collected_cr"], хотим / 100, след)
            if d["manual"].get("collected") is None and день <= МИР["today"] and C(d["base"]) > 0 and норма:
                # до нормы — целыми дирхамами вверх: наличные копейками не докладывают
                до_нормы = -(-max(0, норма - хотим) // 100) * 100
                нал = min(до_нормы, max(0, C(d["base"]) - C(d["aside"])))
                if abs(C(d["collected"]) - нал) > 1:
                    return беда(seed, шаг, f"{день}: наличными в РП+ = чего не хватило до нормы после крипты",
                                d["collected"], нал / 100, след)
        if abs(C(s["rp_cr"]) - м.на_конец(b["days"][-1]["day"])) > 0:
            return беда(seed, шаг, f"{мес}: крипта РП на конец месяца", s["rp_cr"], м.на_конец(b["days"][-1]["day"]) / 100, след)
    if len(книги) == 2:
        o, n = книги["2026-10"]["safe"], книги["2026-11"]["safe"]
        if (C(n["open_rp_cr"]), C(n["open_rp"]), C(n["open_b"]), C(n["open_np"])) != (C(o["rp_cr"]), C(o["rp"]), C(o["b"]), C(o["np"])):
            return беда(seed, шаг, "перенос: ноябрь открывается не остатком октября",
                        (n["open_rp_cr"], n["open_rp"], n["open_b"], n["open_np"]), (o["rp_cr"], o["rp"], o["b"], o["np"]), след)
    if шаг % 7 == 3:
        _дождись()
        w = await wr._build()
        kn = w.get("book") or {}
        лежит = C(round((МИР["bal"]["MAIN"] + МИР["bal"]["OLD"]) * КУРС, 2))
        if м.ждут:                       # экран кошелька сам дочитал приход
            м.inflow += sum(м.ждут); м.ждут.clear()
            if not м.ready:
                pass
        if not м.ready:
            м.ready = True; м.open = лежит; м.inflow = 0
        if (kn.get("ready"), C(kn.get("free")), C(kn.get("rp")), C(kn.get("wallet"))) != (True, м.free(), м.rp(), лежит):
            return беда(seed, шаг, "экран кошелька: книга", kn, (м.free() / 100, м.rp() / 100, лежит / 100), след)
        if C(kn.get("diff")) != лежит - м.free() - м.rp():
            return беда(seed, шаг, "экран кошелька: разница с книгой", kn.get("diff"), (лежит - м.free() - м.rp()) / 100, след)
        if C(w.get("ours_all_aed")) != лежит:
            return беда(seed, шаг, "экран кошелька: наших денег", w.get("ours_all_aed"), лежит / 100, след)
    return True


НАДО = ["сверка", "приход", "между своими", "уход", "день", "подтвердить:ok", "подтвердить:no_free",
        "подтвердить:crypto_early", "поле:ok", "поле:no_free", "поле:cr_spent", "расход:crypto:ok",
        "расход:crypto:no_crypto", "расход:cash:ok", "вывод:из свободной", "вывод:с РП", "вывод:пополам",
        "вывод:no_crypto", "убрать", "вывод:с комиссией", "вывод:с комиссией:часть с РП",
        "убрать:вывод с комиссией", "убрать:только комиссию"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=150); ap.add_argument("--steps", type=int, default=90)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--screen", type=int, default=60)
    ap.add_argument("--real", action="store_true")
    a = ap.parse_args()
    if REAL:
        print("настоящая монга: каждая цепочка — во временной базе ambar_fuzz_cr_*, стирается за собой")
    t0 = time.time()
    чистых, снимки = 0, []
    for k in range(a.runs):
        out = asyncio.run(прогон(a.seed + k, a.steps))
        if out:
            чистых += 1
            if len(снимки) < a.screen * 2:
                снимки += out
        elif len(БЕДЫ) >= 3:
            break
        if (k + 1) % 250 == 0:
            print(f"  … {k + 1} цепочек, чистых {чистых}, {time.time() - t0:.0f} с", flush=True)
    print(f"цепочек {a.runs} × {a.steps} шагов · чистых {чистых} · {time.time() - t0:.1f} с")
    print("ответы:", ", ".join(f"{k} {v}" for k, v in sorted(ВЕТКИ.items())))
    плохо, всего = FF.экран(снимки) if снимки and not REAL else ([], 0)
    print(f"экран: книг {len(снимки)} · расхождений ДДС с сейфом {всего}")
    for x in плохо[:5]:
        print("   ", x)
    нет = [k for k in НАДО if k not in ВЕТКИ] if a.runs >= 100 else []
    if нет:
        print("  не пройдены ветки:", нет)
    ок = not БЕДЫ and not всего and not нет
    print("ИТОГ:", "все прошли" if ок else f"бед {len(БЕДЫ)}: {[(b[0], b[1], b[2]) for b in БЕДЫ[:5]]}")
    return 0 if ок else 1


if __name__ == "__main__":
    sys.exit(main())
