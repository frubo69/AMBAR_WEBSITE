"""Пробег и замок смены на случайных цепочках событий.

Владелец, 1 окт 2026: «что требует глубочайших тестов» — третьим пунктом
пробег. Правило может не пустить на смену весь парк, а причин у показания пять:
вышел на работу, сел на машину, начался месяц, «переснять», ремонт — плюс
пропуск старшего, машины без водителя и водители без машины, отъезд и возврат.

Здесь случайная цепочка событий идёт через НАСТОЯЩИЕ ручки (водителя и
старшего) на базе в памяти, часы — свои, чтобы листать дни и месяцы. Рядом
ведётся вторая модель — событиями, а не запросами: кто кому что должен. После
каждого события сверяем для каждого водителя:

  • нужно ли показание и ПОЧЕМУ — сервер и модель отвечают одинаково;
  • нет тупика: кому показание нужно, тот может его сдать, и после этого
    на смену его пускают (замок снят сразу);
  • лишнего не спрашивают: сданное показание действует, пока не случится одно
    из событий-причин;
  • замок смены стоит тогда и только тогда, когда показание нужно;
  • машина в ремонте смену не держит;
  • пробег машины по журналу не убывает;
  • ремонт: «сейчас в сервисе» у старшего = машины с отметкой.

    python3 tools/fuzz_car.py                    # 300 цепочек по 120 событий
    python3 tools/fuzz_car.py --runs 5000 --steps 200 --seed 3
"""
import argparse, asyncio, base64, json, os, random, sys
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
import db, bizday, config_staff as staff, car_intake as ci         # noqa: E402
import driver_routes as dr, owner_routes as orr, owner_auth        # noqa: E402

КАДР = base64.b64encode(b"\xff\xd8" + b"o" * 3000).decode()
БЕДЫ = []
ЧАСЫ = {"t": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)}     # 13:00 Дубай


class _Часы(datetime):
    """Свои часы: каждое «сейчас» на секунду позже прошлого, дни листаем сами.
    Так у событий строгий порядок без пауз, а месяцы меняются за миллисекунды."""
    @classmethod
    def now(cls, tz=None):
        ЧАСЫ["t"] += timedelta(seconds=1)
        t = ЧАСЫ["t"]
        return t.astimezone(tz) if tz else t.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls.now(timezone.utc).replace(tzinfo=None)


for _m in (db, bizday, dr, orr):
    _m.datetime = _Часы
_настоящий_sync = staff.sync
owner_auth.install_validator(lambda s: {"id": 1, "first_name": "Старший"} if s == "1" else None)
dr._valid_init_data = lambda init, token: {"id": 1}
dr._geo_for = lambda me: asyncio.sleep(0, {"ok": True, "left_min": 60})
_я = {"me": None}
dr.staff.driver_by_tg = lambda uid: _я["me"]


async def _тихо(*a, **k):
    return None
dr.staff.sync = _тихо
orr.car_intake_tell = _тихо


def беда(метка, что, **ctx):
    if len(БЕДЫ) < 30:
        БЕДЫ.append((метка, что, ctx))


async def зови(h, method="GET", body=None, auth="tma 1", path="/x"):
    r = make_mocked_request(method, path, headers={"Authorization": auth})
    if body is not None:
        async def js():
            return body
        r.json = js
    resp = await h(r)
    try:
        return resp.status, json.loads(resp.text)
    except Exception:                                    # noqa: BLE001
        return resp.status, {}


def сегодня():
    return bizday.biz_day()


class Модель:
    """Вторая модель — событиями. Ничего не спрашивает у базы: помнит, кто за
    какой машиной, с какого номера события, и какие показания ещё в силе."""

    def __init__(self):
        self.seq = 0
        self.car_of = {}          # водитель → машина
        self.holder = {}          # машина → водитель ('' — свободна)
        self.взял = {}            # машина → номер события, когда её закрепили за нынешним
        self.repair = set()
        self.since = {}           # водитель → день начала периода
        self.away = set()
        self.rows = []            # показания и пропуски: seq, driver, car, day, km, id, void

    def шаг(self):
        self.seq += 1
        return self.seq

    def закрепить(self, car, driver):
        """Как owner_routes._car_assign без обмена: машина — водителю."""
        прежний = self.holder.get(car, "")
        if прежний == driver:
            return
        if прежний:
            self.car_of.pop(прежний, None)
        self.holder[car] = driver
        if driver:
            self.car_of[driver] = car
        self.взял[car] = self.шаг()

    def _последнее(self, driver, car=None):
        best = None
        for x in self.rows:
            if x["driver"] == driver and not x["void"] and (car is None or x["car"] == car):
                if best is None or x["seq"] > best["seq"]:
                    best = x
        return best

    def пробег(self, car):
        best = None
        for x in self.rows:
            if x["car"] == car and x["km"] and not x["void"] and (best is None or x["seq"] > best["seq"]):
                best = x
        return best["km"] if best else 0

    def нужно(self, driver, день):
        """(нужно, почему, без_машины)."""
        since = self.since.get(driver, "")
        любое = self._последнее(driver)
        новый = bool(since and since >= ci.FROM_DAY and (любое is None or любое["day"] < since))
        первое = день[:7] + "-01"
        car = self.car_of.get(driver)
        if not car:
            свободные = [c for c, h in self.holder.items() if not h]
            why = "new" if новый else "month" if (любое is None or любое["day"] < первое) else ""
            return (bool(why and свободные), why if свободные else "", not свободные)
        if car in self.repair:
            return (False, "", False)
        моё = self._последнее(driver, car)
        if новый:
            return (True, "new", False)
        if self.взял.get(car, 0) > (моё["seq"] if моё else -1) and self.взял.get(car, 0) > 0:
            return (True, "car", False)
        if моё is None or моё["day"] < первое:
            return (True, "month", False)
        return (False, "", False)


async def прогон(seed, шагов):
    r = random.Random(seed)
    метка = f"seed={seed}"
    db._db = AsyncMongoMockClient()["fuzz_car_%d" % seed]
    ЧАСЫ["t"] = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
    staff.AWAY.clear(); staff.SINCE.clear()
    m = Модель()
    водители = [f"В{i}" for i in range(r.randint(2, 5))]
    for n in водители:
        await db.driver_add(n, r.choice(["jvc", "tecom"]), 1)
        # часть работает давно (до рубежа), часть — без периода вовсе
        if r.random() < 0.7:
            начало = r.choice(["2026-06-01", "2026-09-01", "2026-09-29"])
            await db._db.fin_people.insert_one({"_id": n, "work": [{"from": начало, "to": ""}]})
            m.since[n] = начало
    машины = []
    for i in range(r.randint(2, 6)):
        cid = await db.car_add(f"Авто{i}", "серый", f"{10000 + i}")
        машины.append(cid); m.holder[cid] = ""; m.взял[cid] = 0
    await _настоящий_sync(force=True)
    await staff.sync_away(сегодня())
    # до включения месячного правила машины уже за кем-то закреплены
    for n, cid in zip(водители, машины):
        if r.random() < 0.8:
            await db.car_set_driver(cid, n)
            m.holder[cid] = n; m.car_of[n] = cid; m.взял[cid] = 0
    ЧАСЫ["t"] = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    await staff.sync_away(сегодня())

    def я(n):
        _я["me"] = {"name": n, "district": "jvc", "district_code": "B1"}
        return _я["me"]

    async def сверка(после):
        день = сегодня()
        for n in водители:
            if n in m.away:
                continue
            st = await ci.state({"name": n})
            want = m.нужно(n, день)
            got = (st["need"], st["why"], st["nocar"])
            if got != want:
                return беда(метка, "сервер и модель разошлись: нужно ли показание", после=после, водитель=n,
                            сервер=got, модель=want, день=день, машина=m.car_of.get(n), since=m.since.get(n))
            # замок смены ⇔ нужно показание
            я(n)
            # смену каждый раз открываем с чистого листа: уже открытую правило не трогает
            await db._db.driver_days.delete_many({"driver": n})
            await db.save_driver_day(день, n, {"working": True})
            dr._biz_day = lambda *a, **k: день
            код, тело = await зови(dr.handle_shift_open, "POST", {})
            заперто = (код == 409 and тело.get("error") == "need_car")
            if заперто != want[0]:
                return беда(метка, "замок смены не совпал с «нужно показание»", после=после, водитель=n,
                            нужно=want[0], ответ=(код, тело.get("error")))
        # пробег машины по журналу не убывает
        rows = await db._db.car_intakes.find({"km": {"$gt": 0}, "void": {"$ne": True}}).sort("at", 1).to_list(5000)
        посл = {}
        for x in rows:
            c = x.get("car_id")
            if c in посл and x["km"] < посл[c]:
                return беда(метка, "пробег машины убыл", после=после, машина=c, было=посл[c], стало=x["km"])
            посл[c] = x["km"]
        # ремонт: у старшего «сейчас в сервисе» = машины с отметкой
        код, тело = await зови(orr.handle_cars_repairs)
        if sorted(x["car_id"] for x in тело.get("now", [])) != sorted(m.repair):
            return беда(метка, "«в ремонте сейчас» у старшего ≠ отметкам", после=после,
                        экран=sorted(x["car_id"] for x in тело.get("now", [])), модель=sorted(m.repair))
        return True

    async def сдать(n, cid=None, как="ok"):
        """Водитель сдаёт показание. Возвращает True, если принято."""
        я(n)
        car = m.car_of.get(n) or cid
        было = m.пробег(car) if car else 0
        km = (было + r.randint(0, 400)) if было else r.randint(20000, 300000)
        body = {"km": km, "photo": КАДР, **({"car_id": cid} if cid else {})}
        if как == "меньше" and было > 5:
            body["km"] = было - r.randint(1, 5)
        if как == "прыжок" and было:
            body["km"] = было + ci.KM_JUMP + 10
        код, тело = await зови(dr.handle_car_intake_post, "POST", body)
        if код == 400 and тело.get("error") in ("jump", "small"):
            код, тело = await зови(dr.handle_car_intake_post, "POST", {**body, "confirm": 1})
        if код == 200:
            if cid and not m.car_of.get(n):
                m.закрепить(cid, n)
            m.rows.append({"seq": m.шаг(), "driver": n, "car": m.car_of[n], "day": сегодня(), "km": тело["km"], "void": False})
        return код, тело

    async def ход_показание():
        n = r.choice([x for x in водители if x not in m.away] or водители)
        if n in m.away:
            return "все уехали"
        нужно, why, _ = m.нужно(n, сегодня())
        cid = None
        if not m.car_of.get(n):
            свободные = [c for c, h in m.holder.items() if not h]
            cid = r.choice(свободные) if свободные else None
        как = r.choice(["ok"] * 6 + ["меньше", "прыжок"])
        было_до = m.пробег(m.car_of.get(n) or cid) if (m.car_of.get(n) or cid) else 0
        код, тело = await сдать(n, cid, как)
        if нужно:
            if как == "меньше" and было_до > 5:
                if (код, тело.get("error")) != (400, "less"):
                    беда(метка, "пробег меньше прошлого приняли", водитель=n, ответ=(код, тело))
            elif код != 200:
                беда(метка, "ТУПИК: показание нужно, а сдать его нельзя", водитель=n, почему=why, ответ=(код, тело), как=как)
            else:
                # сдал — замок снят сразу
                if (await ci.state({"name": n}))["need"]:
                    беда(метка, "сдал показание, а оно нужно снова", водитель=n, почему=why)
        elif код == 200:
            беда(метка, "показание приняли, хотя оно не требовалось", водитель=n)
        return f"показание {n} ({как}) → {код}"

    async def ход_день():
        прыжок = r.choice([1, 1, 1, 2, 5, 12, 31, 40])
        ЧАСЫ["t"] += timedelta(days=прыжок)
        await staff.sync_away(сегодня())
        return f"+{прыжок} дн → {сегодня()}"

    async def ход_машина():
        cid = r.choice(машины)
        n = r.choice(водители + [""])
        if n in m.away:
            return "уехавшему машину не даём"
        mode = r.choice(["take", "swap"])
        прежний, была = m.holder.get(cid, ""), m.car_of.get(n) if n else None
        err = await orr._car_assign(cid, n, mode)
        if err:
            return f"машина: {err}"
        if прежний == n:
            return "машина: без изменений"
        # модель: как _car_assign
        if прежний:
            m.car_of.pop(прежний, None)
        m.holder[cid] = n
        m.взял[cid] = m.шаг()
        if n:
            m.car_of[n] = cid
            if была and была != cid:
                кому = прежний if (mode == "swap" and прежний) else ""
                m.holder[была] = кому
                m.взял[была] = m.шаг()
                if кому:
                    m.car_of[кому] = была
        return f"машина {cid[-4:]} → {n or 'свободна'} ({mode})"

    async def ход_пропуск():
        n = r.choice(водители)
        if n in m.away:
            return "пропуск: уехал"
        нужно = m.нужно(n, сегодня())[0]
        код, тело = await зови(orr.handle_car_intake_skip, "POST", {"driver": n, "note": "фазз"})
        if нужно != (код == 200):
            беда(метка, "пропуск: ответ не по правилу", водитель=n, нужно=нужно, ответ=(код, тело))
        if код == 200:
            m.rows.append({"seq": m.шаг(), "driver": n, "car": m.car_of.get(n), "day": сегодня(), "km": 0, "void": False})
        return f"пропуск {n} → {код}"

    async def ход_переснять():
        живые = await db._db.car_intakes.find({"void": {"$ne": True}}).sort("at", 1).to_list(5000)
        if not живые:
            return "переснять: нечего"
        i = r.randrange(len(живые))
        код, _ = await зови(orr.handle_car_intake_void, "POST", {"id": живые[i]["_id"]})
        if код == 200:
            # в модели записи идут в том же порядке, что в базе (по времени)
            мои = [x for x in m.rows if not x["void"]]
            мои[i]["void"] = True
        return f"переснять → {код}"

    async def ход_ремонт():
        кто = r.choice(["водитель", "старший"])
        if кто == "водитель":
            n = r.choice(водители)
            car = m.car_of.get(n)
            if n in m.away or not car:
                return "ремонт: некому"
            back = car in m.repair
            я(n)
            km = (m.пробег(car) + r.randint(0, 60)) or r.randint(20000, 90000)
            код, тело = await зови(dr.handle_car_repair, "POST", {"km": km, "photo": КАДР, "back": back, "confirm": 1})
            if код != 200:
                return беда(метка, "ремонт у водителя не прошёл", водитель=n, back=back, ответ=(код, тело))
            m.rows.append({"seq": m.шаг(), "driver": n, "car": car, "day": сегодня(), "km": km, "void": False})
        else:
            car = r.choice(машины)
            back = car in m.repair
            km = r.choice([0, m.пробег(car) + r.randint(0, 60)])
            код, тело = await зови(orr.handle_cars_repair, "POST", {"car_id": car, "back": back, "km": km or "", "as": "Старший", "confirm": 1})
            if код != 200:
                return беда(метка, "ремонт у старшего не прошёл", машина=car, back=back, ответ=(код, тело))
            if km:
                m.rows.append({"seq": m.шаг(), "driver": "Старший", "car": car, "day": сегодня(), "km": km, "void": False})
        (m.repair.discard if back else m.repair.add)(car)
        return f"ремонт {кто}: {'забрал' if back else 'сдал'}"

    async def ход_отъезд():
        n = r.choice(водители)
        день = сегодня()
        doc = await db._db.fin_people.find_one({"_id": n}) or {"_id": n, "work": []}
        work = list(doc.get("work") or [])
        if n in m.away:
            work.append({"from": день, "to": ""})
            m.away.discard(n); m.since[n] = день
        else:
            if not work:
                work = [{"from": "2026-06-01", "to": ""}]
            вчера = (datetime.strptime(день, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            if work[-1].get("from", "") > вчера:
                return "отъезд: вышел только сегодня"
            work[-1] = {**work[-1], "to": вчера}
            m.away.add(n)
        await db._db.fin_people.replace_one({"_id": n}, {"_id": n, "work": work}, upsert=True)
        await staff.sync_away(день)
        if (n in staff.AWAY) != (n in m.away):
            беда(метка, "отъезд: сервер и модель по-разному считают, уехал ли", водитель=n, сервер=n in staff.AWAY)
        return f"{'уехал' if n in m.away else 'вернулся'} {n}"

    ходы = [ход_показание] * 9 + [ход_день] * 5 + [ход_машина] * 4 + [ход_пропуск] * 1 + [ход_переснять] * 2 \
        + [ход_ремонт] * 3 + [ход_отъезд] * 1
    журнал = []
    if await сверка("начало") is not True:
        return
    for шаг in range(шагов):
        ход = r.choice(ходы)
        try:
            что = await ход()
        except Exception as e:                           # noqa: BLE001
            import traceback
            return беда(метка, f"{ход.__name__} упал: {type(e).__name__}: {e}", шаг=шаг,
                        след=traceback.format_exc()[-500:], журнал=журнал[-6:])
        журнал.append(f"{шаг}: {что}")
        if БЕДЫ:
            БЕДЫ[-1][2].setdefault("журнал", журнал[-8:])
            return
        if await сверка(f"{шаг}: {что}") is not True:
            БЕДЫ[-1][2]["журнал"] = журнал[-8:]
            return
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=300); ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    ок = 0
    for k in range(a.runs):
        if asyncio.run(прогон(a.seed + k, a.steps)) is True:
            ок += 1
        if len(БЕДЫ) >= 5:
            break
    print(f"пробег и замок смены: цепочек {a.runs} по {a.steps} событий — чистых {ок}, бед {len(БЕДЫ)}")
    for где, что, ctx in БЕДЫ[:5]:
        print(f"\nБЕДА [{где}] {что}")
        for k, v in ctx.items():
            print(f"    {k}: {str(v)[:700]}")
    print("\nИТОГ:", "всё сошлось" if not БЕДЫ else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if not БЕДЫ else 1


if __name__ == "__main__":
    sys.exit(main())
