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

Отчёт владельца кошелька (12 окт 2026, wallet_ledger). Кошелёк — счёт на бирже:
в сети видны лишь переводы на адрес, а траты с баланса биржи и сам остаток —
нет. Поэтому, как только отчёт загружен, остаток берём из него:

  на счёте по книге = остаток по отчёту (последняя строка с «Баланс после»)
                      + пришло по сети после неё (переводы, не вошедшие в отчёт)
                      − потрачено после неё по нашим записям (РП− криптой,
                        выводы в наличные — те, что не из самого отчёта);
  свободная         = на счёте по книге − счёт РП.

Расходы из отчёта ложатся в книгу записями РП− «криптой» с cr_free: крипта
пришла на счёт РП из свободной (cr_in) и тем же днём ушла (exp) — счёт РП не
меняется, статья получает факт. До отчёта — как раньше, по сети и открытию.
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
# Что лежит на кошельках по последнему чтению сети (USDT): предложение в РП+
# не должно быть больше того, что есть на самом деле (владелец, 3 окт 2026:
# «показатель должен совпадать с фактическим в крипте»). Читается в sync().
_BAL: dict = {"usdt": None, "at": 0.0}


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
    # Остаток кошельков — заодно: по нему предложение в РП+ режется до факта.
    # Сеть не ответила по любому адресу — прежнее значение остаётся.
    всего, есть = 0.0, True
    for a in адреса:
        try:
            b = await tron.get_balance(a)
        except Exception:                                    # noqa: BLE001
            b = None
        if not b or b.get("unknown"):
            есть = False
            break
        всего += float(b.get("usdt") or 0)
    if есть:
        _BAL.update(usdt=всего, at=time.time())
    return {"ok": ок, "new": новых}


def wallet_aed() -> float | None:
    """Сколько лежит на кошельках в наших дирхамах по последнему чтению; None —
    ещё не читали."""
    return None if _BAL["usdt"] is None else _r(float(_BAL["usdt"]) * rate())


def _deltas(days: list, entries: list) -> tuple:
    """Движения по дням: счёт РП {день: изменение} и итоги для свободной.

    fee_in — всё, что пришло на счёт РП из свободной мимо распределения: часть
    комиссии вывода и расходы из отчёта кошелька (cr_free у РП− «криптой»):
    те и другие тем же днём тратятся, счёт РП в итоге не меняют."""
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
            # расход из отчёта кошелька: крипта пришла из свободной и ушла
            cf = float(e.get("cr_free") or 0)
            if cf:
                fee_in += cf; put(day, cf)
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


async def _by_report(entries: list, rp: float) -> dict | None:
    """Остаток по отчёту владельца кошелька и что было после него. None —
    отчёта ещё не загружали."""
    import wallet_ledger
    bal = await wallet_ledger.report_balance()
    if not bal or bal.get("aed") is None:
        return None
    ts0, day0 = int(bal["ts"] or 0), str(bal.get("day") or "")
    k = rate()
    # Пришло по сети после отчёта: переводы, которых в отчёте нет (сошедшиеся
    # с его строками уже сидят в остатке).
    свои = await db.wallet_ledger_txids()
    moves = await db.crypto_moves("in")
    inflow_after = _r(sum(float(m.get("usdt") or 0) * k for m in moves
                          if int(m.get("ts") or 0) > ts0 - 60_000 and m.get("txid") not in свои
                          and int(m.get("ts") or 0) > ts0 - 60_000))
    # Потрачено после отчёта по нашим записям — не из самого отчёта: РП−
    # криптой (кроме строк комиссии: комиссия целиком сидит в выводе) и выводы
    # в наличные вместе с комиссией.
    spent_after = 0.0
    for e in entries:
        if e.get("wal"):
            continue
        day = str(e.get("day") or "")
        at = e.get("at")
        try:
            at_ms = int(at.timestamp() * 1000) if at else 0
        except Exception:                                   # noqa: BLE001
            at_ms = 0
        после = day > day0 or (day == day0 and at_ms > ts0)
        if not после:
            continue
        if e.get("book") == "rp" and e.get("pay") == "crypto" and not e.get("fee_of"):
            spent_after += float(e.get("amount") or 0)
        elif e.get("book") == "in" and e.get("src") == "crypto":
            spent_after += float(e.get("amount") or 0) + float(e.get("fee") or 0)
    total = _r(float(bal["aed"]) + inflow_after - spent_after)
    return {"balance": _r(bal["aed"]), "ts": ts0, "day": day0, "inflow_after": inflow_after,
            "spent_after": _r(spent_after), "total": total, "free": _r(total - rp)}


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
                "free_fact": 0.0, "wallet": wallet_aed(),
                "rp": _r(alloc + fee_in - exp - wd_rp), "book": 0.0, "out": 0.0,
                "by_day": по_дням, "start_day": START_DAY}
    moves = await db.crypto_moves()
    inflow = _r(sum(float(m.get("aed") or 0) for m in moves if m.get("dir") == "in"))
    out = _r(sum(float(m.get("aed") or 0) for m in moves if m.get("dir") == "out"))
    open_ = _r(c.get("open_aed"))
    free = _r(open_ + inflow - alloc - fee_in - wd_free)
    rp = _r(alloc + fee_in - exp - wd_rp)
    # Свободная по факту: не больше того, что лежит на кошельках сверх счёта
    # РП. Ушло с кошелька без записи в книге — в РП+ этого не предложим.
    wallet = wallet_aed()
    chain = wallet
    free_fact = free if wallet is None else max(0.0, min(free, _r(wallet - rp)))
    src = "chain"
    # Отчёт владельца кошелька загружен — остаток берём из него (владелец, 12
    # окт 2026): деньги лежат на бирже, адрес в сети показывает лишь то, что
    # биржа ещё не забрала к себе.
    rep = None
    try:
        rep = await _by_report(entries, rp)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[crypto] остаток по отчёту не посчитан: {e}")
    if rep:
        src = "report"
        wallet = rep["total"]
        free = rep["free"]
        free_fact = max(0.0, free)
    return {"ready": True, "rate": float(c.get("rate") or rate()), "open": open_,
            "inflow": inflow, "alloc": alloc, "exp": exp, "wd_free": wd_free, "wd_rp": wd_rp,
            "fee_in": fee_in,
            "free": free, "free_fact": free_fact, "wallet": wallet, "rp": rp, "book": _r(free + rp),
            # Ушло с кошелька по сети и записано в книге (РП− криптой + выводы):
            # разница — то, что ушло, а в книгу ещё не внесли (или наоборот).
            "out": out, "out_book": _r(exp + wd_free + wd_rp),
            "src": src, "chain": chain, "report": rep,
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
