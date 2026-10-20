"""Журнал кошелька по отчёту его владельца (владелец, 12 окт 2026).

«Всё, что он загрузил отчётом, считай достоверным и окончательным. Точно так
же веди параллельно учёт свой по транзакциям, но когда он загружает свой
отчёт — оттуда бери ВСЕ недостающие данные».

Почему так: кошелёк — счёт на бирже. В сети видны только переводы на адрес и
с адреса; траты с баланса биржи (ENOC, билеты, сим-карты, снятие наличных) в
сети не появляются — биржа просто уменьшает остаток. Поэтому сам кошелёк мы
читаем по сети (crypto_book: приход по переводам), а расходы и остаток берём
из отчёта. Отчёт главный: при расхождении прав он.

Что лежит (wallet_ledger, см. db.wallet_ledger_*): каждая строка отчёта —
навсегда, с датой, видом, суммой в USDT и дирхамах, курсом, остатком после
операции, и с тем, что из неё сделано в книге финансов. Повторная загрузка
того же или следующего отчёта строки дописывает и обновляет, ничего не задваивая.

Что уходит в книгу финансов (с crypto_book.START_DAY, раньше — только журнал):
  • трата криптой (ENOC, билет, WPS…)   → РП− «криптой» с cr_free = сумма:
    крипта пришла на счёт РП из свободной и тем же днём потрачена — счёт РП
    не трогает, свободная уменьшается, статья бюджета получает факт;
  • перевод в SIM-кошелёк                  → то же, статья «Пополнение» (Sim);
  • снятие наличных                        → Доп. РП+ «из крипты» (in, src=crypto)
    из свободной: крипта стала наличными фонда;
  • приход от клиента                      → как раньше: назначение переводу
    (wallet_purposes), в книгу идёт по сети (crypto_moves);
  • возврат расхода / пополнение владельцем → только журнал, решает человек.

Статью угадываем по словам описания (KEYWORDS); не угадали — «Что-то ещё» с
пометкой «неразобрано»: владелец разносит их в STAR («Кошелёк → По отчёту»).
Тот же расход, уже вписанный рукой (тот же день, та же сумма, криптой), второй
раз не пишем — строка журнала ссылается на него (book = dup:<id>).
"""
import hashlib
import logging
import secrets
from datetime import datetime, timezone, timedelta

import db
import bizday

log = logging.getLogger("wallet_ledger")

DUBAI = timezone(timedelta(hours=4))

# Что считать первым днём учёта — тем же, что у книги крипты.
def start_day() -> str:
    import crypto_book
    return crypto_book.start_cached()


KINDS = ("in", "topup", "refund", "cash", "sim", "out")
# Выключатель (13 окт 2026): строки отчёта в журнал ложатся всегда, а в книгу
# финансов и в остаток крипты идут только когда владелец включил — модель
# учёта крипты (курс расходов, один баланс) он ещё решает.
BOOK_KEY = "wallet_ledger_book"


async def book_enabled() -> bool:
    try:
        c = await db.setting_get(BOOK_KEY)
    except Exception:                             # noqa: BLE001
        return False
    return bool(c and c.get("on"))
KIND_T = {"in": "приход от клиента", "topup": "пополнение владельцем", "refund": "возврат расхода",
          "cash": "снятие наличных", "sim": "в SIM-кошелёк", "out": "расход криптой"}

_CASH_WORDS = ("наличн", "банкомат", "atm", "снятие", "снял", "кэш", "cash", "обнал")

# Слова описания → названия статей бюджета по порядку предпочтения. Берётся
# первая статья из списка, которая есть в бюджете месяца. Порядок правил
# важен: «пополнить баланс номеров» — сим-карта, хотя слово «баланс» есть и у
# ENOC («VIP ENOC: баланс 600»), поэтому ENOC стоит выше.
KEYWORDS = (
    (("enoc", "adnoc", "бензин", "топлив", "заправ", "petrol", "fuel", "дизел"), ("Бензин",)),
    (("авиабилет", "билет", "авиа", "flight", "ticket", "перелет", "перелёт", "рейс", "багаж"), ("Билеты",)),
    (("виз", "visa", "emirates id", "эмирейтс", "резидент", "медосмотр", "медкомис"), ("Визы",)),
    (("sim", "сим", "сим-карт", "simcard", "etisalat", "etisalat", "du ", " du", "virgin", "номер", "связ"),
     ("Пополнение", "Sim", "Покупка")),
    (("парков", "parking", "mawaqif", "паркинг"), ("Парковка",)),
    (("страхов", "пассинг", "passing", "insurance", "техосмотр"), ("Страховка/Пассинг",)),
    (("гараж", "масло", "шин", "колес", "колёс", "автосервис", "сервис машин", "ремонт машин", "ремонт авто",
      "тормоз", "аккумулятор"), ("Гараж и ТО",)),
    (("орион",), ("Орион Рент", "Другой Рент")),
    (("алексей",), ("Алексей Рент", "Другой Рент")),
    (("рент", "rent a car", "аренда машин", "аренда авто", "прокат"), ("Другой Рент",)),
    (("dewa", "коммунал", "электрич", "интернет", "wifi", "wi-fi", "empower", "chiller", "газ "), ("Коммуналка",)),
    (("продукт", "еда", "питани", "хлеб", "вода", "grocery", "food", "обед", "ужин", "завтрак"), ("Продукты",)),
    (("хоз", "ремонт", "lulu", "carrefour", "ikea", "dragon", "чехол", "бытов", "уборк", "посуд", "мебел",
      "сплит", "кондиционер", "лампоч", "инструмент", "кроват", "матрас", "наклейк", "стикер", "пакет",
      "канцел", "бумаг", "принтер"), ("Хоз. нужды",)),
    (("реклам", "пост", "промо", "таргет", "ads", "продвиж"), ("Посты",)),
    (("интеграц",), ("Интеграция бота",)),
    (("мониторинг",), ("Мониторинг чатов",)),
)
# Аренда зданий: «аренда jvc» → статья «Аренда JVC» — по совпадению второго слова.
_RENT_WORD = "аренд"
OTHER_NAME = "Что-то ещё"


def _r(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def kind(op: str, desc: str, aed, way) -> str:
    """Вид операции по виду из отчёта, описанию и знаку суммы."""
    o = (op or "").lower()
    t = (desc or "").lower()
    if "возврат" in o:
        return "refund"
    if "пополнен" in o and (aed is None or float(aed) >= 0) and way is not True:
        return "topup"
    if "sim" in o or "сим" in o:
        return "sim"
    if way is False or (way is None and aed is not None and float(aed) >= 0):
        return "in"
    if any(w in t for w in _CASH_WORDS) or any(w in o for w in ("наличн", "снятие")):
        return "cash"
    return "out"


def key(r: dict) -> str:
    """Ключ строки: минута, сумма в USDT и направление. Описание в ключ не
    входит — его в следующем отчёте могут поправить, а операция та же."""
    когда = r["when"].astimezone(DUBAI).strftime("%Y%m%d%H%M") if r.get("when") else ""
    usdt = f"{abs(float(r.get('amount') or 0)):.2f}"
    way = "out" if r.get("way") else "in"
    salt = ("|" + (r.get("text") or "").lower()) if r.get("_dup") else ""
    return hashlib.sha1(f"{когда}|{usdt}|{way}{salt}".encode()).hexdigest()[:16]


def with_keys(rows: list) -> list:
    """Проставить ключи; двум разным строкам с одной минутой и суммой —
    добавить в ключ описание, иначе они слились бы в одну."""
    счёт: dict = {}
    for r in rows:
        r["_dup"] = False
        k = key(r)
        счёт.setdefault(k, []).append(r)
    for k, xs in счёт.items():
        if len(xs) > 1:
            for r in xs:
                r["_dup"] = True
    for r in rows:
        r["key"] = key(r)
    return rows


def book_day(when: datetime) -> str:
    """День книги: смена, в которую попала операция, — то же правило, что у
    заказов и всей книги (bizday, граница 10:00 Дубай). Перевод в 01:22 ночи —
    ещё вчерашняя смена, даже в первые часы дня старта учёта: такая операция
    сидит в открытии и в книгу не идёт (владелец, 20 окт 2026: «смена у нас с
    12 до 6, а не сутки в сутки, и это применимо везде» — журнал прижимал
    ночь на 15 октября к 15-му, и по сменам 14-е ушло в минус, 15-е в плюс)."""
    return bizday.biz_day(when)


def _doc(r: dict, who: str, report: str, now: datetime) -> dict:
    when = r["when"]
    aed = r.get("aed")
    usdt = abs(float(r.get("amount") or 0))
    rate = r.get("rate")
    if aed is None and rate:
        aed = _r(usdt * float(rate)) * (-1 if r.get("way") else 1)
    k = kind(r.get("op") or "", r.get("desc") or "", aed, r.get("way"))
    return {
        "_id": r["key"], "ts": int(when.timestamp() * 1000), "when": when.astimezone(timezone.utc),
        "day": book_day(when), "month": book_day(when)[:7],
        "kind": k, "op": r.get("op") or "", "desc": r.get("desc") or "", "text": r.get("text") or "",
        "usdt": round(usdt, 6), "rate": float(rate) if rate else None,
        "aed": None if aed is None else _r(aed),
        "balance": None if r.get("balance") is None else _r(r["balance"]),
        "txid": r.get("txid_match") or r.get("txid") or "", "matched": bool(r.get("txid_match")),
        "report": report, "by": who, "upd": now,
    }


async def store(rows: list, who: str, report: str) -> dict:
    """Все строки отчёта — в журнал. Новые — целиком; старые — обновить
    описание, суммы, остаток и связь с переводом; книгу (book) не трогаем."""
    now = datetime.now(timezone.utc)
    было = await db.wallet_ledger_get([r["key"] for r in rows])
    new = upd = 0
    for r in rows:
        doc = _doc(r, who, report, now)
        old = было.get(doc["_id"])
        if old is None:
            doc["at"] = now
            doc["book"] = ""
            new += 1
        else:
            # связь с переводом не теряем, если в этот раз сеть молчала
            if not doc["txid"] and old.get("txid"):
                doc["txid"], doc["matched"] = old["txid"], bool(old.get("matched"))
            if old.get("balance") is not None and doc["balance"] is None:
                doc["balance"] = old["balance"]
            upd += 1
        await db.wallet_ledger_upsert(doc)
    return {"new": new, "updated": upd}


# ── Книга финансов ──────────────────────────────────────────────────────────
def guess_line(desc: str, k: str, lines: list) -> tuple:
    """(id статьи, угадали ли). Не угадали — «Что-то ещё», если есть."""
    t = " " + (desc or "").lower() + " "
    by_name = {}
    for ln in lines:
        by_name.setdefault((ln.get("name") or "").lower(), ln)
    def take(names):
        for n in names:
            ln = by_name.get(n.lower())
            if ln:
                return ln["_id"] if "_id" in ln else ln.get("id")
        return None
    if k == "sim":
        lid = take(("Пополнение", "Sim", "Покупка"))
        if lid:
            return lid, True
    if _RENT_WORD in t:
        # «аренда jvc» → «Аренда JVC»: ищем статью аренды, чьё второе слово есть в описании
        for n, ln in by_name.items():
            if n.startswith("аренда ") and n.split(" ", 1)[1] and n.split(" ", 1)[1] in t:
                return ln["_id"] if "_id" in ln else ln.get("id"), True
    for words, names in KEYWORDS:
        if any(w in t for w in words):
            lid = take(names)
            if lid:
                return lid, True
    return take((OTHER_NAME,)) or "", False


async def _lines(month: str) -> list:
    try:
        return [ln for ln in await db.fin_budget_get(month) if (ln.get("kind") or "") != "salary"]
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[ledger] статьи {month}: {e}")
        return []


async def _dup_of(row: dict) -> dict | None:
    """Тот же расход уже вписан рукой: тот же день книги, та же сумма (±1),
    криптой — у траты; у снятия наличных — Доп. РП+ «из крипты»."""
    aed = abs(float(row.get("aed") or 0))
    if aed <= 0:
        return None
    if row["kind"] == "cash":
        q = {"book": "in", "src": "crypto", "wal": {"$exists": False}}
        for e in await db.fin_entries_same(row["day"], aed, q):
            return e
        # сумма вывода бывает с комиссией: наличных пришло меньше на неё
        for e in await db.fin_entries_where({"day": row["day"], "book": "in", "src": "crypto",
                                             "wal": {"$exists": False}}):
            if abs(float(e.get("amount") or 0) + float(e.get("fee") or 0) - aed) <= 1.0:
                return e
        return None
    q = {"book": "rp", "pay": "crypto", "wal": {"$exists": False}}
    for e in await db.fin_entries_same(row["day"], aed, q):
        return e
    return None


async def book(keys: list, who: str) -> dict:
    """Строки журнала с этими ключами — в книгу финансов (кто ещё не там).
    Возвращает счётчики: booked, dup, unassigned, history, pending."""
    st = {"booked": 0, "dup": 0, "unassigned": 0, "history": 0, "pending": 0, "skipped": 0}
    if not await book_enabled():
        st["off"] = True
        return st
    rows = await db.wallet_ledger_get(keys)
    sd = start_day()
    now = datetime.now(timezone.utc)
    lines_by_month: dict = {}
    for k in keys:
        row = rows.get(k)
        if not row:
            continue
        b = str(row.get("book") or "")
        if b.startswith("entry:"):
            # запись могли убрать рукой — тогда второй раз не заводим
            if not await db.fin_entry_get(b[6:]):
                await db.wallet_ledger_set(k, {"book": "removed"})
            continue
        if b in ("skip", "removed", "history") or b.startswith("dup:"):
            continue                              # решено раньше: человеком или прошлой загрузкой
        if row["kind"] == "in" or row["kind"] == "topup":
            continue                              # приход учитывает сеть; пополнение — только журнал
        if str(row.get("day") or "") < sd:        # смена до начала учёта — история: она уже в открытии
            await db.wallet_ledger_set(k, {"book": "history"})
            st["history"] += 1
            continue
        if row["kind"] == "refund":
            if b != "pending":
                await db.wallet_ledger_set(k, {"book": "pending"})
            st["pending"] += 1
            continue
        dup = await _dup_of(row)
        if dup:
            await db.wallet_ledger_set(k, {"book": "dup:" + str(dup["_id"]), "line": dup.get("line") or ""})
            st["dup"] += 1
            continue
        aed = abs(float(row.get("aed") or 0))
        if aed <= 0:
            continue
        day = row["day"]
        eid = secrets.token_hex(6)
        comment = (row.get("desc") or row.get("text") or KIND_T[row["kind"]])[:120]
        if row["kind"] == "cash":
            doc = {"_id": eid, "day": day, "book": "in", "amount": aed, "comment": comment, "who": "", "line": "",
                   "kind": "", "by": who, "at": now, "photo": False, "src": "crypto",
                   "cr_free": aed, "cr_rp": 0.0, "wal": k}
            line, guessed = "", True
        else:
            month = day[:7]
            if month not in lines_by_month:
                lines_by_month[month] = await _lines(month)
            line, guessed = guess_line(row.get("desc") or "", row["kind"], lines_by_month[month])
            doc = {"_id": eid, "day": day, "book": "rp", "amount": aed, "comment": comment, "who": "", "line": line,
                   "kind": "", "by": who, "at": now, "photo": False, "pay": "crypto", "cr_free": aed,
                   "src": "wallet", "wal": k}
            if not guessed:
                doc["wal_guess"] = False
        await db.fin_entry_add(doc)
        await db.wallet_ledger_set(k, {"book": "entry:" + eid, "line": line, "guessed": guessed})
        st["booked"] += 1
        if not guessed:
            st["unassigned"] += 1
        log.info(f"[ledger] {day} {KIND_T[row['kind']]} {aed} AED «{comment}» → "
                 f"{'статья ' + line if line else 'вне плана'}{'' if guessed else ' · неразобрано'}")
    try:
        import finance_routes
        for m in {rows[k]["day"][:7] for k in keys if k in rows and rows[k].get("day")}:
            await finance_routes._touch(m)
    except Exception as e:                        # noqa: BLE001
        log.debug(f"[ledger] кэш переносов: {e}")
    return st


async def assign(k: str, line: str, who: str) -> dict:
    """Статья расходу из отчёта — рукой владельца. Пустая статья — «вне плана»."""
    row = (await db.wallet_ledger_get([k])).get(k)
    if not row:
        return {"error": "not_found"}
    b = str(row.get("book") or "")
    if not b.startswith("entry:"):
        return {"error": "not_booked"}
    e = await db.fin_entry_get(b[6:])
    if not e:
        await db.wallet_ledger_set(k, {"book": "removed"})
        return {"error": "removed"}
    if e.get("book") != "rp":
        return {"error": "not_expense"}
    if line:
        ln = await db.fin_budget_line_get(line)
        if not ln or ln.get("month") != str(e.get("day") or "")[:7]:
            return {"error": "bad_line"}
    await db.fin_entry_set(e["_id"], {"line": line, "wal_guess": True, "by_line": who})
    await db.wallet_ledger_set(k, {"line": line, "guessed": True})
    try:
        import finance_routes
        await finance_routes._touch(str(e.get("day") or "")[:7])
    except Exception:                             # noqa: BLE001
        pass
    log.info(f"[ledger] статья {line or 'вне плана'} у «{e.get('comment')}» {e.get('amount')} · {who or '—'}")
    return {"ok": True}


async def skip(k: str, who: str) -> dict:
    """Не наш расход / не писать в книгу: запись убираем, строка остаётся в журнале."""
    row = (await db.wallet_ledger_get([k])).get(k)
    if not row:
        return {"error": "not_found"}
    b = str(row.get("book") or "")
    if b.startswith("entry:"):
        e = await db.fin_entry_get(b[6:])
        if e:
            await db.fin_entry_del(e["_id"])
            try:
                import finance_routes
                await finance_routes._touch(str(e.get("day") or "")[:7])
            except Exception:                     # noqa: BLE001
                pass
    await db.wallet_ledger_set(k, {"book": "skip", "skip_by": who})
    log.info(f"[ledger] строка {k} вне книги · {who or '—'}")
    return {"ok": True}


# ── Остаток по отчёту и сводки ──────────────────────────────────────────────
async def report_balance(for_book: bool = False) -> dict | None:
    """Последний известный остаток по отчёту: {aed, usdt, ts, day, key}.
    for_book — для книги крипты: пока выключатель не включён, None."""
    if for_book and not await book_enabled():
        return None
    last = await db.wallet_ledger_last()
    if not last:
        return None
    return {"aed": _r(last.get("balance")), "usdt": round(float(last.get("balance") or 0) / float(last.get("rate") or 3.5), 2)
            if last.get("rate") else None,
            "ts": int(last.get("ts") or 0), "day": last.get("day") or "", "key": last["_id"],
            "report": last.get("report") or ""}


def row_out(r: dict) -> dict:
    b = str(r.get("book") or "")
    return {"key": r["_id"], "ts": int(r.get("ts") or 0), "day": r.get("day") or "", "month": r.get("month") or "",
            "kind": r.get("kind") or "", "kind_t": KIND_T.get(r.get("kind") or "", ""), "op": r.get("op") or "",
            "desc": r.get("desc") or "", "usdt": r.get("usdt"), "aed": r.get("aed"), "rate": r.get("rate"),
            "balance": r.get("balance"), "matched": bool(r.get("matched")),
            "book": "entry" if b.startswith("entry:") else "dup" if b.startswith("dup:") else b,
            "entry": b[6:] if b.startswith("entry:") else b[4:] if b.startswith("dup:") else "",
            "line": r.get("line") or "", "unassigned": b.startswith("entry:") and r.get("guessed") is False}


async def unassigned() -> list:
    rows = await db.wallet_ledger_list()
    return [row_out(r) for r in rows if str(r.get("book") or "").startswith("entry:") and r.get("guessed") is False]


async def summary() -> dict:
    """Для экрана кошелька: остаток по отчёту, месяц, неразобранные."""
    bal = await report_balance()
    if not bal:
        return {}
    months = await db.wallet_ledger_months()
    cur = datetime.now(DUBAI).strftime("%Y-%m")
    m = next((x for x in months if x["month"] == cur), None) or {"month": cur, "n": 0, "in_aed": 0.0, "out_aed": 0.0}
    un = await unassigned()
    return {"balance": bal["aed"], "balance_usdt": bal["usdt"], "balance_ts": bal["ts"], "report": bal["report"],
            "month": m["month"], "month_in": _r(m["in_aed"]), "month_out": _r(m["out_aed"]), "month_n": m["n"],
            "months_n": len(months), "unassigned_n": len(un), "unassigned": un[:8],
            "rows_n": sum(x["n"] for x in months)}


async def shifts(month: str) -> dict:
    """По сменам (учётный день, граница 10:00 Дубай): что пришло на кошелёк
    от клиентов по отчёту против заказов криптой в приложении (держатель
    кошелька, 18 окт 2026: «считать по сменам и сравнивать с Амбаром»).
    Расходы и выдача наличных за смену — рядом, одним числом."""
    import finance_routes
    days = finance_routes._month_days(month) if month else []
    rows = await db.wallet_ledger_list(month)
    by: dict = {}
    for r in rows:
        d = str(r.get("day") or "")
        x = by.setdefault(d, {"day": d, "in": 0.0, "in_n": 0, "extra_in": 0.0, "out": 0.0, "out_n": 0, "cash": 0.0})
        aed = float(r.get("aed") or 0)
        k = r.get("kind")
        if k == "in":
            x["in"] += aed; x["in_n"] += 1
        elif k in ("topup", "refund"):
            x["extra_in"] += aed
        elif k == "cash":
            x["cash"] += -aed; x["out"] += -aed; x["out_n"] += 1
        else:
            x["out"] += -aed; x["out_n"] += 1
    orders: dict = {}
    if days:
        try:
            sales = await finance_routes._sales(days)
            orders = {d: float(sales[d]["crypto"]) for d in days}
        except Exception as e:                            # noqa: BLE001
            log.warning(f"[ledger] заказы криптой по дням не прочитаны: {e}")
    today = finance_routes._biz_day()
    out = []
    for d in sorted(set(by) | {d for d in days if d <= today and orders.get(d)}, reverse=True):
        x = by.get(d) or {"day": d, "in": 0.0, "in_n": 0, "extra_in": 0.0, "out": 0.0, "out_n": 0, "cash": 0.0}
        o = orders.get(d, 0.0)
        out.append({**{k: (_r(v) if isinstance(v, float) else v) for k, v in x.items()},
                    "orders": _r(o), "diff": _r(x["in"] - o)})
    return {"month": month, "days": out,
            "totals": {"in": _r(sum(x["in"] for x in out)), "orders": _r(sum(x["orders"] for x in out)),
                       "diff": _r(sum(x["diff"] for x in out)), "out": _r(sum(x["out"] for x in out)),
                       "cash": _r(sum(x["cash"] for x in out))}}


async def page(month: str) -> dict:
    months = await db.wallet_ledger_months()
    if not month and months:
        month = months[-1]["month"]
    rows = [row_out(r) for r in await db.wallet_ledger_list(month)]
    rows.sort(key=lambda r: -r["ts"])
    un = await unassigned()
    # статьи — по месяцам книги: неразобранная строка может быть из другого
    # месяца, чем открытый, а статья годится только из своего
    lines: dict = {}
    for m in {month} | {str(r["day"])[:7] for r in un if r.get("day")}:
        if m:
            lines[m] = [{"id": ln["_id"], "name": ln.get("name") or ""} for ln in await _lines(m)]
    bal = await report_balance()
    return {"month": month, "months": months, "rows": rows, "lines": lines.get(month, []), "lines_by_month": lines,
            "balance": bal, "unassigned": un, "start_day": start_day(),
            "totals": {"in": _r(sum(r["aed"] for r in rows if (r["aed"] or 0) > 0)),
                       "out": _r(-sum(r["aed"] for r in rows if (r["aed"] or 0) < 0)), "n": len(rows)}}
