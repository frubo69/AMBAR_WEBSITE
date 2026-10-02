"""Книга крипты: сколько крипты пришло, сколько из неё свободно и сколько лежит
на счету РП.

Владелец, 2 окт 2026: «в ежедневном распределении РП+ сначала считай, сколько у
нас криптой пришло на кошелёк, потом — сколько надо докинуть из налички… в РП−
надо учитывать, чем платили: наличными — вычитаем из наличных, криптой — из
крипты… главное — правильный, логичный подсчёт».

Все суммы — в дирхамах, НАШИХ: USDT × курс, по которому платил клиент (3.5);
разница до рынка — посредника (wallet_routes._rate).

Два счёта:

  свободная крипта = открытие + пришло на кошелёк − распределено в РП+
                     − выведено в наличные из свободной;
  крипта РП        = распределено в РП+ − оплачено из РП криптой
                     − переведено из крипты РП в наличные РП.

Приход считаем по переводам, а не по остатку кошелька. Остаток падает в ту
секунду, когда посредник вывел деньги, а запись в книге появляется позже —
считай мы от остатка, свободная крипта уходила бы в минус на каждом выводе.
Переводы копятся в `crypto_moves` и только дописываются: книга не зависит от
того, отвечает ли сеть в минуту расчёта и сколько истории она отдаёт.

Что двигает счёт РП (всё лежит в обычной книге финансов, своей копии нет):
  • fin_days.collected_cr подтверждённого дня      → +
  • fin_entries book=rp, pay=crypto                → −
  • fin_entries book=in, src=crypto: cr_rp         → − (и столько же в наличные РП)
  • тот же вывод: fee_free                         → + (см. комиссию ниже)
Свободную: тот же collected_cr (−), cr_free вывода (−) и fee_free (−).

Комиссия вывода (владелец, 2 окт 2026: «вывели столько-то наличных, но в USDT
нам отняли комиссию — чтобы было объяснимо, куда деньги ушли лишние; 50% на
посреднике, 50% на нас»). Наличных пришло N, а крипты ушло N + наша половина
комиссии. Половина — расход РП «криптой» (обычная запись РП− с pay=crypto,
связанная с выводом). Берётся она, как и сам вывод, сперва из свободной крипты:
эта часть (fee_free) в ту же секунду ложится на счёт РП и тут же им тратится —
так расход целиком виден в РП−, а счёт РП уменьшается только на то, что взяли
с него самого.

Правило времени: счёт РП не уходит в минус НИ В ОДИН день. Расход криптой днём
D проходит, только если на конец D и каждого следующего дня в РП хватает.
"""
import logging
import time
from datetime import datetime, timezone

import db

log = logging.getLogger("crypto_book")

START_DAY = "2026-10-01"     # первый день, в чьё распределение идёт крипта
KEY = "crypto_book"
EPS = 0.005
OVERLAP_MS = 6 * 3600 * 1000  # насколько назад перечитываем сеть при сверке
FEE_OURS = 0.5                # наша доля комиссии вывода; вторая половина — посредника


def _r(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def rate() -> float:
    import wallet_routes
    return wallet_routes._rate()


async def addresses() -> list:
    import wallet_routes
    return [a for a in [wallet_routes.TRON_RECEIVE_ADDRESS] + await wallet_routes.old_addresses() if a]


async def cfg() -> dict | None:
    c = await db.setting_get(KEY)
    return c if c and c.get("start_ts") else None


async def init() -> dict | None:
    """Завести книгу: открытие = то, что лежит на кошельках сейчас; приход
    считается с этой минуты. None — сеть не ответила, попробуем в другой раз:
    открытие «наугад» испортило бы весь дальнейший счёт."""
    c = await cfg()
    if c:
        return c
    import tron
    адреса = await addresses()
    if not адреса:
        return None
    до = int(time.time() * 1000)
    usdt = 0.0
    for a in адреса:
        b = await tron.get_balance(a)
        if b is None:
            log.warning("[crypto] книга не заведена: сеть не ответила по балансу")
            return None
        usdt += float(b.get("usdt") or 0)
    после = int(time.time() * 1000)
    k = rate()
    c = {"start_ts": после, "read_from": до, "open_usdt": round(usdt, 6),
         "open_aed": _r(usdt * k), "rate": k, "at": datetime.now(timezone.utc)}
    await db.setting_set(KEY, c)
    log.info(f"[crypto] книга заведена: открытие {c['open_usdt']} USDT = {c['open_aed']} AED "
             f"по {k}, приход считаем с {после}")
    return c


async def sync() -> dict:
    """Дочитать из сети новые переводы и дописать их в книгу. Сеть молчит —
    ничего не теряем: книга остаётся прежней, дочитаем в следующий раз."""
    c = await init()
    if not c:
        return {"ok": False, "new": 0}
    import tron
    адреса = await addresses()
    свои = set(адреса)
    было = await db.crypto_moves()
    с = max(int(c["start_ts"]), max((int(m.get("ts") or 0) for m in было), default=0) - OVERLAP_MS)
    k = rate()
    новых, ок = 0, True
    for a in адреса:
        переводы = await tron.get_transfers(a, min_ts=с)
        if переводы is None:
            ок = False
            continue
        for t in переводы:
            if int(t.get("ts") or 0) < int(c["start_ts"]) or not t.get("txid"):
                # Перевод в ту секунду, когда читали остаток для открытия: он
                # мог и не попасть в остаток. Сам не решаю — говорю в журнал.
                if int(c.get("read_from") or 0) <= int(t.get("ts") or 0) < int(c["start_ts"]) \
                        and (t.get("peer") or "") not in свои:
                    log.warning(f"[crypto] перевод {str(t.get('txid'))[:12]}… на {t.get('amount')} USDT пришёлся "
                                f"на секунду открытия книги — проверить руками, вошёл ли он в открытие")
                continue
            if (t.get("peer") or "") in свои:
                continue                         # между своими кошельками — не приход и не уход
            куда = "in" if t.get("in") else "out"
            usdt = float(t.get("amount") or 0)
            if usdt <= 0:
                continue
            doc = {"_id": f"{t['txid']}:{куда}:{a[-6:]}:{usdt}", "txid": t["txid"], "dir": куда,
                   "usdt": usdt, "aed": _r(usdt * k), "rate": k, "ts": int(t["ts"]),
                   "wallet": a, "at": datetime.now(timezone.utc)}
            if await db.crypto_move_add(doc):
                новых += 1
                log.info(f"[crypto] {'пришло' if куда == 'in' else 'ушло'} {usdt} USDT = {doc['aed']} AED "
                         f"· {t['txid'][:12]}…")
    return {"ok": ок, "new": новых}


def _deltas(days: list, entries: list) -> tuple:
    """Движения по дням: счёт РП {день: изменение} и итоги для свободной."""
    по_дням: dict = {}
    alloc = exp = wd_free = wd_rp = fee_in = 0.0
    def put(day, v):
        по_дням[day] = _r(по_дням.get(day, 0.0) + v)
    for d in days:
        v = float(d.get("collected_cr") or 0)
        if v > 0:
            alloc += v; put(str(d.get("_id") or d.get("day")), v)
    for e in entries:
        day = str(e.get("day") or "")
        if e.get("book") == "rp" and e.get("pay") == "crypto":
            v = float(e.get("amount") or 0)
            exp += v; put(day, -v)
        elif e.get("book") == "in" and e.get("src") == "crypto":
            f, r = float(e.get("cr_free") or 0), float(e.get("cr_rp") or 0)
            wd_free += f; wd_rp += r
            if r:
                put(day, -r)
            # часть нашей комиссии, взятая из свободной: приходит на счёт РП
            # (и тут же тратится записью РП− «криптой»)
            ff = float(e.get("fee_free") or 0)
            if ff:
                fee_in += ff; put(day, ff)
    return по_дням, _r(alloc), _r(exp), _r(wd_free), _r(wd_rp), _r(fee_in)


def rp_min_from(по_дням: dict, day: str) -> float:
    """Наименьший остаток крипты РП на конец дня `day` и каждого дня после
    него — столько из неё можно забрать днём `day`, не уводя счёт в минус ни
    в один день."""
    бег, мин = 0.0, None
    for d in sorted(по_дням):
        if d > day and мин is None:
            мин = бег                            # остаток на конец самого day
        бег = _r(бег + по_дням[d])
        if d >= day:
            мин = бег if мин is None else min(мин, бег)
    if мин is None:
        мин = бег
    return _r(мин)


def rp_before(по_дням: dict, day: str) -> float:
    """Крипта РП на начало дня `day` — открытие месяца."""
    return _r(sum(v for d, v in по_дням.items() if d < day))


async def state() -> dict:
    """Книга целиком: свободная, на счету РП, из чего сложились. ready=False —
    книга ещё не заведена (сеть не ответила при первом запуске): крипту в
    распределение не предлагаем и не принимаем."""
    c = await cfg()
    days = await db.fin_days_crypto()
    entries = await db.fin_entries_crypto()
    по_дням, alloc, exp, wd_free, wd_rp, fee_in = _deltas(days, entries)
    if not c:
        return {"ready": False, "rate": rate(), "open": 0.0, "inflow": 0.0, "alloc": alloc,
                "exp": exp, "wd_free": wd_free, "wd_rp": wd_rp, "fee_in": fee_in, "free": 0.0,
                "rp": _r(alloc + fee_in - exp - wd_rp), "book": 0.0, "out": 0.0,
                "by_day": по_дням, "start_day": START_DAY}
    moves = await db.crypto_moves()
    inflow = _r(sum(float(m.get("aed") or 0) for m in moves if m.get("dir") == "in"))
    out = _r(sum(float(m.get("aed") or 0) for m in moves if m.get("dir") == "out"))
    open_ = _r(c.get("open_aed"))
    free = _r(open_ + inflow - alloc - fee_in - wd_free)
    rp = _r(alloc + fee_in - exp - wd_rp)
    return {"ready": True, "rate": float(c.get("rate") or rate()), "open": open_,
            "inflow": inflow, "alloc": alloc, "exp": exp, "wd_free": wd_free, "wd_rp": wd_rp,
            "fee_in": fee_in,
            "free": free, "rp": rp, "book": _r(free + rp),
            # Ушло с кошелька по сети и записано в книге (РП− криптой + выводы):
            # разница — то, что ушло, а в книгу ещё не внесли (или наоборот).
            "out": out, "out_book": _r(exp + wd_free + wd_rp),
            "by_day": по_дням, "start_day": START_DAY}


async def loop(every: int = 300):
    """Раз в пять минут дочитываем кошелёк: утреннее распределение должно
    видеть ночной приход, даже если экран кошелька никто не открывал."""
    import asyncio
    await asyncio.sleep(20)
    while True:
        try:
            r = await sync()
            if r.get("new"):
                log.info(f"[crypto] сверка: новых переводов {r['new']}")
        except Exception as e:                       # noqa: BLE001
            log.warning(f"[crypto] сверка не прошла: {e}")
        await asyncio.sleep(every)
