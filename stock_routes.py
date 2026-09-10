"""
AMBAR — склад: пересчёт, перемещения, заявка и норма.

Что это заменяет
----------------
Бумажную таблицу «отчёт-заявка». В ней продажи считаются вычитанием
(«было + привезли ± перемещение − осталось»), поэтому бой, пересорт и
воровство молча растворяются в выручке: недостача неотличима от продажи.

Приложение знает продажи ИЗ ЗАКАЗОВ — по факту. Поэтому остаток можно
предсказать заранее:

    ожидается = прошлый пересчёт + приход ± перемещения − продано по заказам

Менеджер вводит фактический остаток, и разница — это недостача, отдельно от
продаж. Важно: недостача бывает и там, где ничего не продавали — бой, пересорт,
унесённая водителем бутылка. Поэтому позиции без продаж не «застывают»: каждая
возвращается на проверку, если её не считали дольше STALE_DAYS.

Дальше из тех же чисел собирается заявка:

    заявка = норма − остаток на руках

А саму норму больше не нужно выдумывать: она выводится из средних продаж за
последние дни, и приложение показывает, где норма завышена и сколько денег
из-за этого заморожено на полке.

Весь модуль под require_owner: доступ только владельцу и менеджерам.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
import photos
import backdate
from owner_auth import require_owner, CORS_HEADERS
from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES
from config_stock_order import order_key      # порядок обхода полок, как в таблице

log = logging.getLogger("stock")

DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12       # рабочие сутки 12:00 → 12:00, как во всей системе
NORM_COVER_DAYS = 3         # на сколько дней запаса рассчитана норма по умолчанию
STALE_DAYS = 7              # через сколько дней позицию пора проверить заново
HISTORY_DEPTH = 45          # сколько пересчётов смотреть назад в поисках проверки


# ── сутки ────────────────────────────────────────────────────────────────────
def _biz_day(ref: datetime = None) -> str:
    """Дата рабочих суток. Смена идёт с полудня, поэтому ночной пересчёт
    относится к уходящему дню и не расходится с выручкой."""
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _days_between(day_a: str, day_b: str):
    """Сколько дней прошло между двумя рабочими сутками. None — если не считали."""
    if not day_a:
        return None
    try:
        a = datetime.strptime(day_a, "%Y-%m-%d")
        b = datetime.strptime(day_b, "%Y-%m-%d")
    except ValueError:
        return None
    return max(0, (b - a).days)


def _moves_by_pid(moves: list, district: str) -> dict:
    """{product_id: сколько прибавилось району за день перемещениями}.

    Складываем сырые количества и округляем один раз в конце: перемещение
    сканом идёт по бутылке, а у пива в единице учёта их двадцать четыре, и
    построчное округление стёрло бы каждую в ноль."""
    raw = {}
    for m in moves:
        pid = m.get("product_id")
        try:
            q = float(m.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if not pid or not q:
            continue
        if m.get("from") == district: raw[pid] = raw.get(pid, 0) - q
        if m.get("to")   == district: raw[pid] = raw.get(pid, 0) + q
    return {pid: _round_step(v) for pid, v in raw.items() if _round_step(v)}


async def _last_checked(district: str, day: str) -> dict:
    """{product_id: дата последней РУЧНОЙ проверки}.

    Строка, перенесённая расчётом (counted=False), проверкой не считается:
    её никто не видел, и бой или недостача по ней остались бы незамеченными.
    У старых пересчётов поля нет — там сохраняли только то, что смотрели."""
    out = {}
    for c in await db.get_stock_counts_recent(district, before_day=day, limit=HISTORY_DEPTH):
        d = c.get("day") or ""
        for l in c.get("lines", []):
            pid = l.get("id")
            if pid and l.get("counted", True) and pid not in out:
                out[pid] = d
    return out


def _dt_of(iso: str):
    """Момент из строки, как её пишет пересчёт. Пустая или кривая — None: это
    значит «пересчёта не было», и приход добавлять не к чему."""
    s = str(iso or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _day_bounds(day: str, days: int = 1):
    """(начало, конец) в UTC-ISO для выборки заказов за N рабочих суток."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    return f(d), f(d + timedelta(days=days))


# ── единица учёта ────────────────────────────────────────────────────────────
# Крепкое и вино считают бутылками, пиво — ящиками. Ящик двадцать четыре, и
# половина ящика — обычное дело: двенадцать банок продали, двенадцать остались.
# Отсюда и остатки вида 9.5 в рабочей таблице. Так что единица пива — ящик, а
# шаг — половина; целыми бутылками пиво на складе никто не считает.
CASE = 24                   # бутылок в ящике
STEP = 0.5                  # мельче половины ящика не бывает


def _unit(p: dict) -> int:
    """Сколько бутылок в одной учётной единице позиции."""
    return CASE if (p.get("price_24_full") or p.get("price_12_full")) else 1


def _round_step(v) -> float:
    """К ближайшей половине. Считают глазами, дробей мельче не бывает.

    Нечисло — ноль и запись в лог: одна битая строка не должна ронять
    весь пересчёт района."""
    try:
        return round(round(float(v) / STEP) * STEP, 2)
    except (TypeError, ValueError):
        log.warning(f"[stock] в остатке нечисло: {v!r} — считаем нулём")
        return 0.0


def _num(v):
    """9.5 остаётся 9.5, а 9.0 показывается как 9 — лишний ноль только мешает."""
    v = _round_step(v)
    return int(v) if v == int(v) else v


# ── продажи из заказов ───────────────────────────────────────────────────────
def _qty(it: dict) -> int:
    """Бутылок в строке заказа. Пиво идёт пачками: qty=1 при pcs=12 — это
    двенадцать бутылок, и со склада уйдут именно двенадцать."""
    try:
        q = int(it.get("qty") or 0)
    except (TypeError, ValueError):
        return 0
    if q <= 0:
        return 0
    try:
        pcs = int(it.get("pcs") or 0)
    except (TypeError, ValueError):
        pcs = 0
    return q * (pcs if pcs else 1)


async def _registry_was(district: str, day: str) -> dict:
    """Сколько лежит на точке по реестру кодов — на начало пересчитываемых суток.

    Коды заводят поштучно, значит реестр знает количество точно. Из него
    вычитаем то, что с тех пор ушло: проданное по доставленным заказам и
    списанное. Сегодняшние продажи не трогаем — их вычтет сам лист, тем же
    способом, что и после ручного пересчёта."""
    codes = await db.qr_by_product_district(district)
    if not codes:
        return {}
    since = (await db.qr_since_by_district()).get(district)
    if not since:
        return {}
    cat = _catalog()
    start, _ = _day_bounds(day, 1)
    since_iso = since.isoformat() if hasattr(since, "isoformat") else str(since)
    out = {pid: n / _unit(cat.get(pid) or {}) for pid, n in codes.items()}
    for o in await db.sold_since(min(since_iso, start)):
        if (o.get("office_id") or "") != district:
            continue
        ts = str(o.get("timestamp") or "")
        if ts < since_iso or ts >= start:
            continue
        for it in (o.get("items") or []):
            pid, q = it.get("id"), _qty(it)
            if pid in out and q:
                out[pid] -= q / _unit(cat.get(pid) or {})
    try:
        # skip_coded: считаем ОТ РЕЕСТРА, а списанная сканом бутылка из него
        # уже вышла — вычитать её ещё раз значит потерять её дважды.
        for pid, n in ((await db.writeoff_since({district: since}, skip_coded=True,
                                                skip_audit=False))
                       .get(district) or {}).items():
            if pid in out:
                out[pid] -= n / _unit(cat.get(pid) or {})
    except Exception as e:
        log.warning(f"[stock] списания в реестре не учтены ({district}): {e}")
    # Переезды сканом реестр показывает сразу: бутылка уже числится на новом
    # офисе. Но лист прибавит их ещё раз, отдельной строкой «перемещения», —
    # поэтому сегодняшние из отправной точки вычитаем. Вчерашние оставляем:
    # они и есть часть того, что лежит на полке к началу суток.
    try:
        for m in await db.get_stock_transfers(day):
            if (m.get("src") or "") != "qr":
                continue
            pid, q = m.get("product_id"), float(m.get("qty") or 0)
            if not pid or not q:
                continue
            if m.get("to")   == district: out[pid] = out.get(pid, 0) - q
            if m.get("from") == district: out[pid] = out.get(pid, 0) + q
    except Exception as e:
        log.warning(f"[stock] переезды сканом в реестре не учтены ({district}): {e}")
    return {pid: max(0, _round_step(v)) for pid, v in out.items() if v > 0}


async def _sold(day: str, district: str | None = None, days: int = 1) -> dict:
    """{product_id: продано в учётных единицах}.

    Заказы знают бутылки, склад считает ящиками — здесь одно переводится в
    другое, иначе проданная пачка пива выглядела бы как пропавшие двенадцать.
    Только доставленные: отменённый заказ товар со склада не уносит."""
    start, end = _day_bounds(day, days)
    orders = await db.get_orders_in_range(start, end)
    cat = _catalog()
    out = {}
    for o in orders:
        if o.get("status") != "delivered":
            continue
        if district and (o.get("office_id") or "") != district:
            continue
        for it in (o.get("items") or []):
            pid = it.get("id")
            q = _qty(it)
            if pid and q:
                out[pid] = out.get(pid, 0) + q / _unit(cat.get(pid) or {})
    return {k: _round_step(v) for k, v in out.items()}


def _catalog():
    from owner_routes import _read_catalog
    return {p.get("id"): p for p in _read_catalog()}


def _price(p: dict) -> int:
    """Цена одной учётной единицы — полная, без скидки приложения: пропавшее
    стоит столько, сколько за него платят. Для пива это цена ящика, поэтому
    недостача в полящика оценивается в цену двенадцати бутылок сама собой."""
    if p.get("price_24_full"):
        return int(p["price_24_full"])
    return int(p.get("price_full") or p.get("price") or 0)


# ══ норма ═══════════════════════════════════════════════════════════════════
# Раньше норма была плоским средним за две недели: сложили продажи, поделили на
# четырнадцать, умножили на три дня запаса. Такое среднее врёт дважды.
#
# Первое: пятница и вторник в этом деле — разные дни, а среднее размазывает их
# в одно число. Заказывая по нему, к выходным не хватает, а к среде лишнее
# стоит на полке.
#
# Второе: две позиции с одинаковым средним, но разным разбросом требуют разного
# запаса. Та, что уходит ровно по две в день, и та, что то ноль, то восемь, —
# это разный риск остаться без товара, и одинаковая норма для них неправильна.
#
# Третье — и главное. Длинное окно врёт, когда дело растёт. В истории есть
# недели, когда продаж почти не было: бизнес разгонялся. Среднее по всему окну
# делит сегодняшний спрос пополам, и заказ выходит вдвое меньше нужного —
# ошибка куда дороже лишней бутылки на полке.
#
# Поэтому здесь четыре вещи:
#   • окно начинается там, где дело реально шло: мёртвая полоса в начале
#     отбрасывается, а не усредняется;
#   • свежие дни весят больше старых — вес падает вдвое каждую неделю;
#   • ожидаемый спрос считается по дням недели, на которые придётся запас;
#   • сверху страховой запас по разбросу, и только ходовым позициям.
NORM_HIST_DAYS = 56          # дальше восьми недель не смотрим
NORM_HALF_LIFE = 7           # за столько дней вес дня падает вдвое
NORM_DEAD_SHARE = 0.25       # день ниже этой доли от обычного — дело не шло
NORM_DEAD_RUN = 3            # столько мёртвых дней подряд считаем остановкой
NORM_MIN_ACTIVE = 14         # короче двух недель окно не режем
NORM_DOW_MIN = 3             # меньше трёх наблюдений — дню недели не верим
NORM_DOW_CLAMP = (0.55, 2.2) # правдоподобные границы: остальное — выброс
# Насколько глубоко страхуемся от разброса. Ходовое — почти наверняка, среднее —
# обычно, редкое — не страхуем вовсе.
NORM_Z = {"top": 1.28, "mid": 0.84, "tail": 0.0}
# Страхуем не весь запас, а срок подвоза: заявка уходит утром и приезжает в тот
# же день. Риск остаться без товара живёт эти сутки, а не все три дня — на три
# дня считается сам запас, и страховать их ещё раз значит платить дважды.
NORM_LEAD_DAYS = 1
NORM_SAFETY_CAP = 0.5        # подушка не больше половины спроса за период
NORM_TOP_SHARE = 0.7         # позиции, дающие 70% штук, считаем ходовыми
NORM_MID_SHARE = 0.95

_DEMAND = {"key": None, "at": 0.0, "data": None}


async def _demand(day: str, days: int = NORM_HIST_DAYS) -> dict:
    """Продажи по дням: {район: {позиция: [шт в день 0..N-1]}} + дни недели.

    Читаем заказы один раз на все районы и держим пять минут: заявка спрашивает
    норму по каждому из пяти районов, и пять одинаковых выборок за восемь
    недель — это ровно тот способ, которым база и укладывается."""
    import time as _t
    key = f"{day}:{days}"
    if _DEMAND["key"] == key and _t.time() - _DEMAND["at"] < 300:
        return _DEMAND["data"]

    # Окно заканчивается вчерашним днём: сегодняшний ещё идёт, и его неполные
    # продажи занизили бы среднее ровно в тот момент, когда собирают заявку.
    last = datetime.strptime(day, "%Y-%m-%d")
    first = last - timedelta(days=days)
    start, end = _day_bounds(first.strftime("%Y-%m-%d"), days)
    # Без предела и с проекцией: обрезка отрезала бы старые дни окна, и норма
    # просела бы там, где продажи как раз есть. Из заказа нужны четыре поля —
    # остальное по сети не тащим.
    orders = await db.get_orders_in_range(start, end, limit=None,
                                          fields=["timestamp", "office_id",
                                                  "status", "items"])
    cat = _catalog()

    idx = {}                      # 'YYYY-MM-DD' → номер дня в окне
    wd = []
    for i in range(days):
        d = first + timedelta(days=i)
        idx[d.strftime("%Y-%m-%d")] = i
        wd.append(d.weekday())

    per = {}
    for o in orders:
        if o.get("status") != "delivered":
            continue
        try:
            ts = datetime.fromisoformat(str(o.get("timestamp") or "")).replace(
                tzinfo=timezone.utc).astimezone(DUBAI_TZ)
        except (ValueError, TypeError):
            continue
        i = idx.get(_biz_day(ts))
        if i is None:
            continue
        dist = o.get("office_id") or ""
        for it in (o.get("items") or []):
            pid, q = it.get("id"), _qty(it)
            if not pid or not q:
                continue
            row = per.setdefault(dist, {}).setdefault(pid, [0.0] * days)
            row[i] += q / _unit(cat.get(pid) or {})

    data = {"days": days, "wd": wd, "per": per, "from": _active_from(per, days)}
    _DEMAND.update(key=key, at=_t.time(), data=data)
    return data


def _active_from(per: dict, days: int) -> int:
    """С какого дня окна история годится в расчёт.

    В начале истории бизнес разгонялся: продаж почти нет. Если такие дни
    усреднить с рабочими, спрос выйдет вдвое меньше настоящего — и заказ тоже.
    Поэтому мёртвую полосу в начале отрезаем.

    Обычный день — медиана последних двух недель: это заведомо «как сейчас».
    Ниже четверти от него — день, когда не работали. Одиночный такой день
    (выходной, поломка) историю не рубит: нужна полоса подряд."""
    tot = [0.0] * days
    for rows in per.values():
        for ser in rows.values():
            for i, v in enumerate(ser):
                tot[i] += v
    tail = sorted(tot[-14:])
    med = tail[len(tail) // 2] if tail else 0
    if med <= 0:
        return 0
    edge = med * NORM_DEAD_SHARE
    cut, run = 0, 0
    for i in range(days):
        if tot[i] < edge:
            run += 1
        else:
            if run >= NORM_DEAD_RUN:
                cut = i          # окно начинается сразу после мёртвой полосы
            run = 0
    return max(0, min(cut, days - NORM_MIN_ACTIVE))


def _class_of(rows: dict) -> dict:
    """Ходовое, среднее или редкое — по доле в штуках района.

    Классы нужны затем, что страховой запас стоит денег: по ходовым он окупается
    сорванным заказом, по редким — просто лежит."""
    tot = {pid: sum(v) for pid, v in rows.items()}
    order = sorted(tot.items(), key=lambda x: -x[1])
    total = sum(tot.values()) or 1
    out, acc = {}, 0.0
    for pid, v in order:
        # Класс определяет доля, накопленная ДО этой позиции: та, что пересекает
        # границу, ещё принадлежит верхней группе. Иначе две одинаковые по
        # продажам позиции, случайно оказавшиеся по разные стороны черты,
        # получили бы разный запас — а разницы между ними нет никакой.
        out[pid] = "top" if acc < NORM_TOP_SHARE else (
                   "mid" if acc < NORM_MID_SHARE else "tail")
        acc += v / total
    return out


async def _suggested_norms(district: str, day: str, detail: bool = False) -> dict:
    """{позиция: норма} по продажам района.

    С detail=True отдаёт вместо числа разбор: сколько ждём продать за дни
    покрытия, сколько к этому добавили подушкой и к какому классу отнесли
    позицию. Это нужно экрану норм: править число, не видя, из чего оно
    сложилось, — то же самое, что придумывать его на глаз."""
    d = await _demand(day)
    src = (d["per"].get(district) or {})
    if not src:
        return {}
    # Окно — только та часть истории, когда дело шло.
    lo = d["from"]
    rows = {pid: ser[lo:] for pid, ser in src.items()}
    wd, n = d["wd"][lo:], d["days"] - lo
    # Свежий день весит больше старого: вес падает вдвое каждую неделю. Так
    # расчёт успевает за ростом, но не дёргается от одного удачного вечера.
    wt = [0.5 ** ((n - 1 - i) / NORM_HALF_LIFE) for i in range(n)]
    wsum = sum(wt) or 1.0
    cls = _class_of(rows)

    # На какие дни недели придётся запас: считаем спрос именно этих дней, а не
    # усреднённого дня вообще.
    base_day = datetime.strptime(day, "%Y-%m-%d")
    cover_wd = [(base_day + timedelta(days=i + 1)).weekday()
                for i in range(NORM_COVER_DAYS)]

    norms = {}
    for pid, series in rows.items():
        total = sum(series)
        if total <= 0:
            continue
        base = sum(wt[i] * series[i] for i in range(n)) / wsum
        if base <= 0:
            continue
        # Форма недели — отношение, а не количество: её считаем ровным средним,
        # и поэтому она не зависит от того, сколько продавали в целом.
        flat = total / n
        # Коэффициент дня недели: во сколько раз этот день отличается от обычного.
        k = {}
        for w in range(7):
            vals = [series[i] for i in range(n) if wd[i] == w]
            if len(vals) >= NORM_DOW_MIN and flat > 0:
                f = (sum(vals) / len(vals)) / flat
                k[w] = min(NORM_DOW_CLAMP[1], max(NORM_DOW_CLAMP[0], f))
            else:
                k[w] = 1.0
        expect = sum(base * k[w] for w in cover_wd)

        # Разброс считаем по остаткам от предсказания, а не вокруг среднего.
        # Иначе пятничный всплеск, который мы только что учли коэффициентом,
        # был бы застрахован второй раз — и позиция с ярким, но предсказуемым
        # ритмом получила бы запас больше, чем непредсказуемая.
        res = [series[i] - flat * k[wd[i]] for i in range(n)]
        sigma = (sum(x * x for x in res) / max(1, n - 1)) ** 0.5
        z = NORM_Z[cls.get(pid, "tail")]
        safety = z * sigma * (NORM_LEAD_DAYS ** 0.5)
        # И сверху потолок. Формула считает разброс так, будто продажи ложатся
        # ровным колоколом, а у редких позиций они идут комками: тридцать дней
        # ноль, потом кто-то забрал пять шампанских разом. По такому разбросу
        # подушка выходит больше самого спроса — это уже не защита от нехватки,
        # а деньги, стоящие на полке, и обещание «норма на три дня» перестаёт
        # быть правдой.
        safety = min(safety, expect * NORM_SAFETY_CAP)

        norm = max(1, -(-int((expect + safety) * 100) // 100))          # ceil
        norms[pid] = ({"norm": norm, "expect": round(expect, 2),
                       "safety": round(safety, 2), "base": round(base, 2),
                       "cls": cls.get(pid, "tail")} if detail else norm)
    return norms


# ── лист пересчёта ───────────────────────────────────────────────────────────
@require_owner
async def handle_sheet(request):
    """Что проверять в районе и сколько ожидается. ?district=jvc[&day=]"""
    district = (request.query.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = (request.query.get("day") or "").strip() or _biz_day()

    prev = await db.get_last_stock_count(district, before_day=day)
    first_time = prev is None
    prev_lines = {l["id"]: l for l in (prev or {}).get("lines", [])}
    # Реестр как отправная точка.
    #
    # Пересчёта на точке могло не быть ни разу, но если бутылки заводили
    # кодами, приложение прекрасно знает, сколько их лежит: каждая внесена
    # поштучно. Говорить в этом случае «сравнивать не с чем» — неправда, из-за
    # которой человек вбивает руками то, что уже посчитано.
    #
    # Ручной пересчёт остаётся главнее: он про физическую полку, а реестр —
    # про то, что в неё клали. Поэтому из реестра берём, только когда пересчёта
    # не было вовсе.
    from_registry = False
    if first_time:
        try:
            reg = await _registry_was(district, day)
        except Exception as e:
            log.warning(f"[stock] реестр не прочитан ({district}): {e}")
            reg = {}
        if reg:
            prev_lines = {pid: {"id": pid, "actual": q} for pid, q in reg.items()}
            first_time = False
            from_registry = True
    sold = await _sold(day, district)
    moves = await db.get_stock_transfers(day)
    existing = await db.get_stock_count(district, day)
    done = {l["id"]: l for l in (existing or {}).get("lines", [])}
    cat = _catalog()
    # Ревизию проходят камерой: у позиции, бутылки которой заведены кодами,
    # факт берётся из прохода, а не с клавиатуры. Поэтому лист должен знать
    # две вещи про каждую строку — сколько бутылок у неё с кодами и сколько
    # из них сегодня увидела камера. Разница между ними и есть недостача.
    try:
        codes = await db.qr_by_product_district(district)
        scanned = await db.audit_scan_counts(district, day)
        scan_stats = await db.audit_scan_stats(district, day)
    except Exception as e:
        log.warning(f"[stock] проход камерой не прочитан ({district}): {e}")
        codes, scanned, scan_stats = {}, {}, {"total": 0, "odd": 0, "at": ""}

    # Когда каждую позицию последний раз считали руками. Продажи вычитаются
    # сами, но бой и воровство продажами не считаются — позицию, которую давно
    # никто не видел, надо вернуть на проверку, даже если её не заказывали.
    last_checked = await _last_checked(district, day)

    # Перемещения: ушедшее из района вычитаем, пришедшее прибавляем.
    #
    # Округляем сумму, а не каждую строку: бутылку пива возят по одной, а
    # считают ящиками — двадцать четыре отдельных переезда по 1/24 ящика при
    # построчном округлении дали бы двадцать четыре нуля вместо ящика.
    move_by_pid = _moves_by_pid(moves, district)

    rows = []
    for pid, p in cat.items():
        was = _round_step((prev_lines.get(pid) or {}).get("actual") or 0)
        s = _round_step(sold.get(pid) or 0)
        mv = _round_step(move_by_pid.get(pid) or 0)
        ago = _days_between(last_checked.get(pid), day)
        unit = _unit(p)
        rows.append({
            "id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
            "no": order_key(pid) + 1,          # номер строки в рабочей таблице
            "price": _price(p),
            # Пиво считают ящиками по CASE бутылок и половинками ящика — фронт
            # должен знать и шаг, и что вообще стоит за единицей.
            "unit": unit, "step": STEP if unit > 1 else 1,
            "unit_name": "ящик" if unit > 1 else "бутылка",
            "was": _num(was), "sold": _num(s), "moved_qty": _num(mv),
            # На первом пересчёте сравнивать не с чем — вводим как отправную точку.
            "expected": None if first_time else _num(max(0, was + mv - s)),
            "touched": bool(s or mv),
            "checked_day": last_checked.get(pid, ""),
            "days_ago": ago,
            "stale": (not first_time) and (ago is None or ago >= STALE_DAYS),
            "actual": (done.get(pid) or {}).get("actual"),
            # Отметка ревизии: ok — проверено и сошлось, diff — расхождение.
            # Хранится с прошлого захода, иначе ревизию нельзя прервать.
            "mark": (done.get(pid) or {}).get("mark"),
            # Сколько бутылок позиции заведено кодами и сколько из них увидела
            # камера. None — не увидела ни одной: это не ноль, а «до полки ещё
            # не дошли», и путать их нельзя.
            "coded": _num(codes.get(pid, 0) / unit) if codes.get(pid) else 0,
            "scanned": _num(scanned[pid] / unit) if pid in scanned else None,
        })
    # Сначала то, где расхождение видно сразу (двигалось), потом то, что давно
    # не проверяли: именно там прячутся бой и недостача без продаж. А внутри
    # каждой группы — порядок таблицы, то есть порядок полок: считать удобнее
    # подряд, чем прыгать по залу за алфавитом.
    rows.sort(key=lambda r: (not r["touched"], not r["stale"], order_key(r["id"])))

    return web.json_response({
        "district": district,
        "district_name": OFFICE_NAMES.get(district, district),
        "district_code": OFFICE_CODES.get(district, ""),
        "day": day, "first_time": first_time, "from_registry": from_registry,
        "prev_day": (prev or {}).get("day", ""),
        # Проход камерой: сколько бутылок записано, сколько позиций он закрыл и
        # у скольких позиций коды вообще есть. Последнее — потолок скана: то,
        # что кодами не заведено, придётся считать глазами.
        "scan": {**scan_stats, "positions": len(scanned),
                 "coded_positions": sum(1 for pid in codes if codes[pid])},
        "audit": {
            "started_at":  (existing or {}).get("audit_started_at", ""),
            "finished_at": (existing or {}).get("audit_finished_at", ""),
            "marked": sum(1 for r in rows if r["mark"]),
        },
        # Когда последний раз сохраняли — карточка ревизии пишет это словами.
        "counted_at": (existing or {}).get("counted_at", ""),
        "touched_count": sum(1 for r in rows if r["touched"]),
        "stale_count": sum(1 for r in rows if r["stale"] and not r["touched"]),
        "stale_days": STALE_DAYS,
        "total_count": len(rows),
        "saved": bool(existing),
        "rows": rows,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_save(request):
    """Сохранить пересчёт или ревизию.

    body: {district, day?, lines:[{id, actual, income?, auto?, mark?}],
           audit?: bool, finish?: bool}

    Ревизия — это тот же пересчёт, но пройденный целиком и с отметкой на каждой
    позиции: ok — посмотрел, сошлось; diff — не сошлось. Закрыть её можно
    только когда отмечены все, иначе «ревизия проведена» означало бы «часть
    полок посмотрели, а часть посчитали на глаз»."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = str(body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()

    prev = await db.get_last_stock_count(district, before_day=day)
    first_time = prev is None
    prev_lines = {l["id"]: l for l in (prev or {}).get("lines", [])}
    sold = await _sold(day, district)
    moves = await db.get_stock_transfers(day)
    cat = _catalog()

    mv_by = _moves_by_pid(moves, district)

    is_audit = bool(body.get("audit"))
    finish = bool(body.get("finish"))
    lines, short_qty, short_aed, over_qty, counted_qty = [], 0, 0, 0, 0
    marked = matched = mismatched = 0
    for raw in (body.get("lines") or []):
        pid = raw.get("id"); p = cat.get(pid)
        if not p:
            continue
        try:
            actual = max(0.0, _round_step(raw.get("actual") or 0))
        except (TypeError, ValueError):
            continue
        try:
            income = max(0.0, _round_step(raw.get("income") or 0))
        except (TypeError, ValueError):
            income = 0.0
        was = _round_step((prev_lines.get(pid) or {}).get("actual") or 0)
        s   = _round_step(sold.get(pid) or 0)
        mv  = _round_step(mv_by.get(pid) or 0)
        expected = None if first_time else max(0.0, _round_step(was + income + mv - s))
        # Строка, до которой не дошли руки. Она сохраняется расчётным значением
        # — иначе завтра не с чем будет сравнивать и заявка не узнает остаток, —
        # но проверкой не считается: бой и воровство продажами не пахнут и
        # вылезают именно там, где никто не смотрел.
        auto = bool(raw.get("auto")) and expected is not None
        if auto:
            actual = expected
        diff = None if expected is None else _round_step(expected - actual)  # >0 — не хватает
        price = _price(p)
        if diff:
            if diff > 0: short_qty += diff; short_aed += diff * price
            else:        over_qty  += -diff
        if not auto:
            counted_qty += 1
        mark = raw.get("mark") if raw.get("mark") in ("ok", "diff") else None
        if mark:
            marked += 1
            if mark == "ok": matched += 1
            else:            mismatched += 1
        lines.append({"id": pid, "name": p.get("name", ""), "price": price,
                      "unit": _unit(p),
                      "was": was, "income": income, "moved_qty": mv, "sold": s,
                      "expected": expected, "actual": actual, "diff": diff,
                      "counted": not auto, "mark": mark})

    if finish and marked < len(lines):
        return web.json_response(
            {"error": "audit_incomplete", "marked": marked, "total": len(lines)},
            status=409, headers=CORS_HEADERS)

    now_iso = datetime.now(timezone.utc).isoformat()
    prev_doc = await db.get_stock_count(district, day) or {}
    doc = {"district": district, "district_name": OFFICE_NAMES.get(district, district),
           "day": day, "first_time": first_time,
           "counted_by": request["owner_id"],
           "counted_at": now_iso,
           "lines": lines, "short_qty": _num(short_qty), "short_aed": round(short_aed),
           "over_qty": _num(over_qty),
           "counted_qty": counted_qty, "total_qty": len(lines)}
    if is_audit or marked:
        # Чем проходили ревизию — часть её итога. Через месяц «сошлось» от
        # ревизии, пройденной камерой, и от ревизии, отмеченной галочками,
        # стоят разного, и в истории это должно быть видно.
        try:
            doc["scan_qty"] = (await db.audit_scan_stats(district, day)).get("total", 0)
        except Exception as e:
            log.warning(f"[stock] проход камерой не записан ({district}): {e}")
        doc["audit_started_at"] = prev_doc.get("audit_started_at") or now_iso
        doc["marked_qty"] = marked
        doc["matched_qty"] = matched
        doc["mismatch_qty"] = mismatched
        if finish:
            doc["audit_finished_at"] = now_iso
            doc["audit_by"] = request["owner_id"]
    await db.save_stock_count(district, day, doc)
    base_drop()                      # остатки изменились — заявку считать заново
    log.info(f"[stock] {district} {day}: {len(lines)} позиций, "
             f"недостача {short_qty} шт / {short_aed} AED")
    await backdate.notify(day, str(body.get("as") or ""),
                          "ревизия" if is_audit else "пересчёт склада",
                          f"{OFFICE_CODES.get(district, district)} — {len(lines)} позиций"
                          + (f", недостача {short_aed} AED" if short_aed else ""))
    return web.json_response(
        {"ok": True, "day": day, "first_time": first_time,
         "short_qty": _num(short_qty), "short_aed": round(short_aed),
         "over_qty": _num(over_qty),
         "counted_qty": counted_qty, "total_qty": len(lines),
         "audit": bool(is_audit or marked), "finished": bool(finish),
         "scan_qty": int(doc.get("scan_qty") or 0),
         "marked": marked, "matched": matched, "mismatched": mismatched,
         "lines": [l for l in lines if l["diff"]]}, headers=CORS_HEADERS)


# ── перемещения между районами ───────────────────────────────────────────────
@require_owner
async def handle_transfer(request):
    """Перевезти товар из района в район. body: {from, to, product_id, qty, day?}

    Перемещение не меняет общий остаток — только чей он. Поэтому в пересчёте
    оно вычитается у отдающего и прибавляется принимающему, и недостача из-за
    переезда не появляется."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    src, dst = str(body.get("from") or ""), str(body.get("to") or "")
    pid = str(body.get("product_id") or "")
    try:
        qty = _round_step(body.get("qty") or 0)     # полящика переехать тоже может
    except (TypeError, ValueError):
        qty = 0
    if src not in OFFICE_IDS or dst not in OFFICE_IDS or src == dst:
        return web.json_response({"error": "bad_districts"}, status=400, headers=CORS_HEADERS)
    if pid not in _catalog() or qty <= 0:
        return web.json_response({"error": "bad_item"}, status=400, headers=CORS_HEADERS)

    day = str(body.get("day") or "").strip() or _biz_day()
    doc = {"day": day, "from": src, "to": dst, "product_id": pid,
           "product_name": _catalog()[pid].get("name", ""), "qty": _num(qty),
           "by": request["owner_id"],
           "at": datetime.now(timezone.utc).isoformat()}
    await db.add_stock_transfer(doc)
    log.info(f"[stock] перемещение {qty}×{pid}: {src} → {dst} ({day})")
    await backdate.notify(day, str(body.get("as") or ""), "перемещение между офисами",
                          f"{_catalog()[pid].get('name','')} · {qty} шт · "
                          f"{OFFICE_CODES.get(src, src)} → {OFFICE_CODES.get(dst, dst)}")
    return web.json_response({"ok": True, **doc}, headers=CORS_HEADERS)


# ── перемещение сканом ───────────────────────────────────────────────────────
# Бутылку возят по одной, и в руках у человека не список позиций, а сама
# бутылка. Поэтому позицию не выбирают: код на крышке уже знает, что это за
# товар и на каком офисе он числится, — остаётся сказать, куда переезжает.
# Один скан = одна бутылка.
#
# Пишем в две книги сразу, и обе обязательны:
#   • реестр кодов — там бутылка меняет офис, иначе ревизия на новом месте
#     скажет «числится на B2», а на старом будет вечно её ждать;
#   • перемещения — оттуда пересчёт берёт поправку к ожидаемому остатку,
#     иначе переезд выглядел бы недостачей у одного и излишком у другого.
MOVE_SAY = {
    "unknown":  "нет в реестре",
    "written":  "была списана",
    "sold":     "ушла с заказом",
    "same":     "уже здесь",
    "nohome":   "офис не указан",
    "no_item":  "нет в каталоге",
    "busy":     "её уже перевезли",
    "deleted":  "убрана из реестра",
}


def _move_reply(verdict: str, **extra):
    return web.json_response(_move_res(verdict, **extra), headers=CORS_HEADERS)


def _move_res(verdict: str, **extra) -> dict:
    return {"ok": verdict == "ok", "verdict": verdict,
            "say": MOVE_SAY.get(verdict, ""), **extra}


async def move_by_code(code: str, dst: str, by, by_name: str = "",
                       by_kind: str = "owner", day: str = "") -> dict:
    """Перевезти одну бутылку по коду с крышки — ядро, общее для старшего и
    водителя. Ответ — словарь с вердиктом, не HTTP: кто спрашивал, тот и
    завернёт. Отказ — не ошибка запроса, а ответ про бутылку: списанную и уже
    уехавшую камера ловит так же легко, как обычную, и человеку надо сказать
    словами, что с ней не так, а не показать красный сбой.

    В книге переездов остаётся, кто вёз: by_kind «driver»/«owner» и имя. По
    ним водитель отменяет только своё, а старший видит в истории, чей переезд."""
    doc = await db.qr_get(code)
    if not doc:
        return _move_res("unknown", code=code)
    name = doc.get("product_name") or ""
    label = doc.get("label") or ""
    # Везти можно только то, что числится в остатке. Список негодных статусов
    # уже однажды отстал от жизни — мимо «убрана из реестра» промахнулись все
    # сканеры сразу. Поэтому разрешаем один статус, а не запрещаем известные.
    st = (doc.get("status") or "active").strip()
    if st != "active":
        return _move_res(st, code=code, name=name, label=label)
    src = (doc.get("district") or "").strip()
    if src == dst:
        return _move_res("same", code=code, name=name, label=label,
                         **{"from": src, "from_code": OFFICE_CODES.get(src, "")})
    if src not in OFFICE_IDS:
        return _move_res("nohome", code=code, name=name, label=label)
    pid = str(doc.get("product_id") or "")
    p = _catalog().get(pid)
    if not p:
        return _move_res("no_item", code=code, name=name, label=label)

    # Количество — в учётных единицах позиции: бутылка крепкого это единица, а
    # бутылка пива — двадцать четвёртая часть ящика. Не округляем: округлит
    # лист, сложив все переезды позиции за день.
    qty = 1 / _unit(p)
    day = str(day or "").strip() or _biz_day()
    at = datetime.now(timezone.utc).isoformat()
    tid = await db.add_stock_transfer(
        {"day": day, "from": src, "to": dst, "product_id": pid,
         "product_name": p.get("name", ""), "qty": qty, "src": "qr", "code": code,
         "by": by, "by_name": str(by_name or "")[:60], "by_kind": by_kind, "at": at})
    if not await db.qr_move(code, src, dst, tid, by, at):
        # Бутылку успели перевезти между чтением и записью — поправку к остатку
        # оставлять нельзя, иначе она уедет дважды.
        await db.delete_stock_transfer(tid)
        return _move_res("busy", code=code, name=name, label=label)
    log.info(f"[stock] переезд по коду {code}: {src} → {dst} ({pid}) — {by_kind} {by_name}")
    return _move_res("ok", code=code, name=p.get("name", "") or name, label=label,
                     transfer_id=tid, bottles=1, to=dst,
                     to_code=OFFICE_CODES.get(dst, ""),
                     **{"from": src, "from_code": OFFICE_CODES.get(src, "")})


async def move_undo_by_code(code: str, only_driver: str = None) -> tuple:
    """Отменить последний переезд бутылки. (status, payload).

    only_driver — имя водителя: отменить можно только переезд, который сделал
    он сам; чужой (и переезд старшего) — «not_yours», бутылка остаётся."""
    d = await db.qr_get(code)
    last = ((d or {}).get("moves") or [])[-1:]
    if not last:
        return 404, {"error": "not_moved"}
    tid = str(last[0].get("transfer") or "")
    if only_driver is not None:
        tr = await db.get_stock_transfer(tid) if tid else None
        if not tr or tr.get("by_kind") != "driver" or (tr.get("by_name") or "") != only_driver:
            return 403, {"error": "not_yours"}
    last = await db.qr_move_undo(code)
    if not last:
        return 404, {"error": "not_moved"}
    if last.get("transfer"):
        await db.delete_stock_transfer(str(last["transfer"]))
    log.info(f"[stock] переезд отменён {code}: назад на {last.get('from')}")
    return 200, {"ok": True, "code": code, "to": last.get("from") or ""}


def group_transfers(rows: list, days: int = 0) -> list:
    """Переезды сканом — одной строкой на позицию.

    Скан пишет строку на каждую бутылку: это правда учёта, но не то, что
    человек хочет читать. Тридцать одинаковых строк «B1 → B3» — это «перевезли
    тридцать бутылок», и показывать надо так. Кто вёз — в ключе группы:
    переезд водителя и переезд старшего той же позиции тем же путём — разные
    строки, у них разные права на отмену."""
    groups, out = {}, []
    for r in rows:
        r["id"] = str(r.pop("_id", ""))
        r["from_name"] = OFFICE_NAMES.get(r.get("from"), r.get("from"))
        r["to_name"] = OFFICE_NAMES.get(r.get("to"), r.get("to"))
        r["from_code"] = OFFICE_CODES.get(r.get("from"), "")
        r["to_code"] = OFFICE_CODES.get(r.get("to"), "")
        r["by_kind"] = r.get("by_kind") or "owner"
        r["by_name"] = r.get("by_name") or ""
        r.pop("by", None)                      # числовой id наружу не отдаём
        if (r.get("src") or "") != "qr":
            r["ids"] = [r["id"]]
            r["bottles"] = 0
            out.append(r)
            continue
        key = (r.get("day"), r.get("from"), r.get("to"), r.get("product_id"),
               r["by_kind"], r["by_name"])
        g = groups.get(key)
        if not g:
            g = dict(r, ids=[], codes=[], bottles=0, qty=0.0, id="")
            groups[key] = g
            out.append(g)
        g["ids"].append(r["id"])
        g["codes"].append(str(r.get("code") or ""))
        g["bottles"] += 1
        g["qty"] = round(g["qty"] + float(r.get("qty") or 0), 4)
    for g in out:
        if g.get("bottles"):
            g["qty"] = _num(g["qty"])
    if days > 0:
        out.sort(key=lambda g: (str(g.get("day") or ""), str(g.get("at") or "")), reverse=True)
    return out


@require_owner
async def handle_transfer_scan(request):
    """Перевезти одну бутылку по коду с крышки. body: {code, to, day?, as?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = str(body.get("code") or "").strip()
    dst = str(body.get("to") or "").strip()
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    if dst not in OFFICE_IDS:
        return web.json_response({"error": "bad_districts"}, status=400, headers=CORS_HEADERS)
    res = await move_by_code(code, dst, request["owner_id"],
                             str(body.get("as") or "").strip(), "owner",
                             str(body.get("day") or ""))
    return web.json_response(res, headers=CORS_HEADERS)


@require_owner
async def handle_transfer_scan_undo(request):
    """Отменить последний переезд бутылки. body: {code}

    Рука быстрее головы: не ту бутылку поднесли к камере — и это должно
    отменяться там же, где случилось, а не поиском строки в списке. Старший
    отменяет любой переезд — и свой, и водительский."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = str(body.get("code") or "").strip()
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    st, payload = await move_undo_by_code(code)
    return web.json_response(payload, status=st, headers=CORS_HEADERS)


@require_owner
async def handle_transfer_delete(request):
    """Убрать перемещение, введённое по ошибке.

    У переезда сканом две записи — поправка к остатку и офис бутылки в
    реестре. Убирать одну и оставлять другую нельзя: бутылка так и осталась бы
    числиться на новом месте, а ожидаемый остаток вернулся бы к старому."""
    tid = (request.match_info.get("tid") or "").strip()
    doc = await db.get_stock_transfer(tid) or {}
    if (doc.get("src") or "") == "qr" and doc.get("code"):
        await db.qr_move_undo(str(doc["code"]), tid)
    ok = await db.delete_stock_transfer(tid)
    return web.json_response({"ok": ok}, status=200 if ok else 404, headers=CORS_HEADERS)


@require_owner
async def handle_transfers(request):
    """Что сегодня перевозили. Переезды сканом — одной строкой на позицию.

    Скан пишет строку на каждую бутылку: это правда учёта, но не то, что
    человек хочет читать. Тридцать одинаковых строк «B1 → B3» — это «перевезли
    тридцать бутылок», и показывать надо так."""
    day = (request.query.get("day") or "").strip() or _biz_day()
    # История: ?days=N отдаёт последние N дней, сгруппированные так же, но с
    # днём в ключе группы — переезд вторника и переезд среды не сливаются.
    try:
        days = int(request.query.get("days") or 0)
    except ValueError:
        days = 0
    if days > 0:
        since = (datetime.strptime(_biz_day(), "%Y-%m-%d") - timedelta(days=days - 1))
        rows = await db.get_stock_transfers_since(since.strftime("%Y-%m-%d"))
    else:
        rows = await db.get_stock_transfers(day)
    out = group_transfers(rows, days)
    return web.json_response({"day": day, "days": days, "transfers": out},
                             headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


# ── заявка ───────────────────────────────────────────────────────────────────
_BASE = {"key": None, "at": 0.0, "data": None}
BASE_TTL = 60          # секунд


def base_drop():
    """Забыть основу заявки: пересчитали склад или приняли товар."""
    _BASE["key"] = None


async def _sold_after(since: dict) -> dict:
    """{район: {позиция: [(момент, продано в учётных единицах), …]}} после его
    пересчёта, по времени.

    Не суммой, а событиями: продажа списывает то, что лежало на полке в её
    момент, и бутылке, внесённой позже, не достаётся — см. _district_base.

    Читаем один раз от самого старого пересчёта и уже в памяти отсекаем по
    каждому району свой момент: пересчёты у всех разные, а пять выборок вместо
    одной — это пять раз по мегабайту на тарифе, где скорость режется объёмом."""
    live = [s for s in since.values() if s]
    if not live:
        return {}
    first = min(live).astimezone(timezone.utc).isoformat().replace("+00:00", "")
    orders = await db.sold_since(first)
    cat = _catalog()
    out = {}
    for o in orders:
        oid = o.get("office_id") or ""
        edge = since.get(oid)
        if not edge:
            continue
        ts = _dt_of(o.get("timestamp") or "")
        if not ts or ts <= edge:
            continue
        for it in (o.get("items") or []):
            pid, q = it.get("id"), _qty(it)
            if not pid or not q:
                continue
            out.setdefault(oid, {}).setdefault(pid, []).append(
                (ts, q / _unit(cat.get(pid) or {})))
    for rows in out.values():
        for ev in rows.values():
            ev.sort(key=lambda e: e[0])
    return out


async def _district_base(day: str) -> dict:
    """Остатки, приход и продажи после пересчёта, норма — по каждому району.

    Это вся тяжесть заявки: пять чтений склада, пять по приходу и расчёт нормы
    по восьми неделям продаж. От ручных правок ничего из этого не зависит, а
    правку жмут по одному нажатию на клетку — и раньше каждое такое нажатие
    пересчитывало всю заявку с нуля. Держим минуту: пересчёт склада и приёмка
    сбрасывают кэш сами, а больше основе меняться неоткуда."""
    import time as _t
    if _BASE["key"] == day and _t.monotonic() - _BASE["at"] < BASE_TTL:
        return _BASE["data"]
    cat = _catalog()
    # Пересчёт — снимок на момент времени. Пока его не повторили, честный
    # остаток = снимок + приход − продажи. Оба слагаемых обязательны и по
    # одной причине: без прихода программа закажет то, что уже привезли, без
    # продаж — не закажет то, что уже продали. Второе дороже: это пустая полка.
    counts, since = {}, {}
    for oid in OFFICE_IDS:
        counts[oid] = await db.get_last_stock_count(oid, before_day=None)
        since[oid] = _dt_of((counts[oid] or {}).get("counted_at") or "")
    sold = await _sold_after(since)
    try:
        broken = await db.writeoff_since(since)
    except Exception as e:
        log.warning(f"[stock] списания не учтены: {e}")
        broken = {}

    out = {}
    for oid in OFFICE_IDS:
        cnt = counts[oid]
        have = {l["id"]: float(l.get("actual") or 0) for l in (cnt or {}).get("lines", [])}
        came, sales = {}, sold.get(oid) or {}
        gone = {pid: sum(q for _, q in ev) for pid, ev in sales.items()}
        try:
            if since[oid]:
                came = await db.intake_since(oid, since[oid])
        except Exception as e:
            log.warning(f"[stock] приход после пересчёта не учтён ({oid}): {e}")
        for pid, n in came.items():
            # Приёмка считает бутылки, склад — учётные единицы: ящик пива это
            # одна единица и двадцать четыре кода.
            have[pid] = (have.get(pid) or 0) + n / _unit(cat.get(pid) or {})
        # «Внести новый товар» — тоже приход: бутылку завели кодом руками,
        # значит она лежит на полке, и склад обязан её показать — даже там,
        # где пересчёта не было. Коды, которыми лишь закрывали долг «QR не
        # внесён», сюда не идут: те бутылки в пересчёте уже есть.
        manual = {}
        try:
            manual = {pid: ats for pid, ats in (await db.qr_manual_events(oid, since[oid])).items()
                      if pid in cat}
        except Exception as e:
            log.warning(f"[stock] внесённое руками не учтено ({oid}): {e}")
        if manual:
            log.info(f"[stock] внесённое руками {oid}: "
                     + ", ".join(f"{pid}×{len(ats)}" for pid, ats in manual.items()))
        # Продажи и ручной приход — по времени, а не суммами. Продажа списывает
        # то, что лежало на полке в её момент, и не глубже нуля; бутылка,
        # внесённая позже, ей не достаётся. Суммами было иначе: пересчёт «0»,
        # три продажи, потом одна внесённая бутылка — 0 + 1 − 3 = 0, и владелец
        # искал на складе бутылку, которую только что завёл. Внёс — видно,
        # при любом раскладе; исчезнуть она может только продажей после.
        for pid in set(sales) | set(manual):
            ev = [(ts, -q) for ts, q in (sales.get(pid) or [])]
            ev += [(at, 1 / _unit(cat.get(pid) or {})) for at in (manual.get(pid) or [])]
            ev.sort(key=lambda e: e[0])
            bal = have.get(pid) or 0
            for _, dq in ev:
                bal = max(0, bal + dq)
            have[pid] = bal
        # Разбитая бутылка ушла со склада так же честно, как проданная. Без
        # этого вычитания заявка возит на полку то, чего на ней уже нет, а
        # недостача каждый раз выглядит ошибкой пересчёта.
        for pid, n in (broken.get(oid) or {}).items():
            have[pid] = max(0, (have.get(pid) or 0) - n / _unit(cat.get(pid) or {}))
        out[oid] = {"have": {k: int(v) for k, v in have.items()},
                    "sug": await _suggested_norms(oid, day), "came": came,
                    "gone": gone, "lost": broken.get(oid) or {},
                    "counted": (cnt or {}).get("day", "")}
    _BASE.update(key=day, at=_t.monotonic(), data=out)
    return out


async def order_rows(day: str = "") -> dict:
    """Заявка на закупку: сколько довезти в каждый район, чтобы вернуться к норме.

    заявка = норма − остаток на руках. Норма берётся сохранённая, а если её не
    задавали — рассчитанная по продажам. Отдаём и то и другое, чтобы владелец
    видел, где норма расходится с реальным спросом.

    Отдельной функцией, потому что этим же расчётом выгружается Excel для
    магазина: держать вторую копию формулы нельзя — разойдутся молча.
    """
    day = (day or "").strip() or _biz_day()
    cat = _catalog()
    saved_norms = await db.get_stock_norms()
    edits = await db.zayavka_edits(day)      # ручные правки поверх расчёта
    rows, total_aed, total_qty = [], 0, 0
    frozen_aed = 0          # деньги, стоящие на полке сверх реального спроса

    per_district = await _district_base(day)

    for pid, p in cat.items():
        price = _price(p)
        cells, item_total = {}, 0
        row_edited = False
        for oid in OFFICE_IDS:
            d = per_district[oid]
            have = int(d["have"].get(pid) or 0)
            sug = int(d["sug"].get(pid) or 0)
            norm = int((saved_norms.get(f"{oid}:{pid}") or sug or 0))
            calc = max(0, norm - have)
            # Правка заменяет расчёт, но не стирает его: рядом остаётся число,
            # которое предлагала программа, иначе непонятно, от чего отступили.
            fix = (edits.get(pid) or {}).get(oid)
            need = max(0, int(fix)) if fix is not None else calc
            if fix is not None and int(fix) != calc:
                row_edited = True
            cells[oid] = {"have": have, "norm": norm, "suggested": sug,
                          "need": need, "calc": calc,
                          # Сколько из «есть» приехало уже после пересчёта и
                          # сколько с тех пор продали: владелец должен видеть,
                          # что число не с полки, а посчитанное.
                          "came": int(d["came"].get(pid) or 0),
                          "gone": int(d["gone"].get(pid) or 0),
                          "edited": fix is not None and int(fix) != calc}
            item_total += need
            if sug and norm > sug:
                frozen_aed += (norm - sug) * price
        if item_total:
            total_qty += item_total
            total_aed += item_total * price
        rows.append({"id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
                     # Цена — за учётную единицу, а у пива это ящик. Сколько в
                     # нём бутылок, приложение само не знает, поэтому единицу
                     # отдаём рядом с ценой: иначе «цена за бутылку» на экране
                     # оказывается ценой за двадцать четыре.
                     "price": price, "unit": _unit(p), "unit_name": "ящик" if _unit(p) > 1 else "бутылка",
                     "need_total": item_total, "cells": cells,
                     "calc_total": sum(c["calc"] for c in cells.values()),
                     "edited": row_edited})

    rows.sort(key=lambda r: (-r["need_total"], r["name"]))
    # Табак считается наравне со всем остальным — норма, остаток, недостача, —
    # но в заявку магазину не идёт: сигареты мы пока берём в другом месте.
    # Поэтому он уходит из общих чисел и из книги для магазина в свой список:
    # сколько докупить, владелец всё равно должен видеть, просто не здесь.
    import tobacco
    smokes = [r for r in rows if r["cat"] in tobacco.NON_ALCOHOL]
    rows = [r for r in rows if r["cat"] not in tobacco.NON_ALCOHOL]
    total_qty = sum(r["need_total"] for r in rows)
    total_aed = sum(r["need_total"] * r["price"] for r in rows)
    return {
        "day": day,
        "districts": [{"id": o, "code": OFFICE_CODES.get(o, ""),
                       "name": OFFICE_NAMES.get(o, o),
                       "counted": per_district[o]["counted"],
                       "came": sum(per_district[o]["came"].values())} for o in OFFICE_IDS],
        "total_qty": total_qty, "total_aed": total_aed,
        "edited_count": sum(1 for r in rows if r["edited"]),
        "frozen_aed": frozen_aed,
        "cover_days": NORM_COVER_DAYS, "window_days": NORM_HIST_DAYS,
        "rows": [r for r in rows if r["need_total"] > 0],
        # Весь каталог, включая позиции без потребности: в Excel для
        # магазина едут все, чтобы он мог дописать то, чего мы не заказали.
        # Табака здесь нет и быть не должно — он закупается мимо магазина.
        "all_rows": rows,
        "tobacco_rows": [r for r in smokes if r["need_total"] > 0],
        "tobacco_qty": sum(r["need_total"] for r in smokes),
        "tobacco_aed": sum(r["need_total"] * r["price"] for r in smokes),
    }


@require_owner
async def handle_order(request):
    return web.json_response(await order_rows(request.query.get("day") or ""),
                             headers=CORS_HEADERS)


@require_owner
async def handle_set_norm(request):
    """Задать норму вручную. body: {district, product_id, norm}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    d, pid = str(body.get("district") or ""), str(body.get("product_id") or "")
    try:
        norm = max(0, int(body.get("norm") or 0))
    except (TypeError, ValueError):
        norm = 0
    if d not in OFFICE_IDS or pid not in _catalog():
        return web.json_response({"error": "bad_args"}, status=400, headers=CORS_HEADERS)
    await db.set_stock_norm(d, pid, norm, request["owner_id"])
    base_drop()
    return web.json_response({"ok": True, "district": d, "product_id": pid, "norm": norm},
                             headers=CORS_HEADERS)


@require_owner
async def handle_norms(request):
    """Нормы района: что предлагает расчёт и что стоит на самом деле.

    Норма — это обещание: столько бутылок мы держим на полке, чтобы хватило
    на дни покрытия. Расчёт делает его из продаж, но последнее слово за
    владельцем: он знает про завоз, праздник и клиента, который завтра купит
    ящик. Поэтому здесь видно оба числа сразу — и во что обходится разница."""
    d = (request.query.get("district") or "").strip() or OFFICE_IDS[0]
    if d not in OFFICE_IDS:
        return web.json_response({"error": "bad_district"}, status=400, headers=CORS_HEADERS)
    day = (request.query.get("day") or "").strip() or _biz_day()
    cat = _catalog()
    base = await _district_base(day)
    saved = await db.get_stock_norms()
    det = await _suggested_norms(d, day, detail=True)
    have = (base.get(d) or {}).get("have") or {}

    rows, frozen, manual = [], 0, 0
    for pid, p in cat.items():
        info = det.get(pid) or {}
        sug = int(info.get("norm") or 0)
        fix = saved.get(f"{d}:{pid}")
        norm = int(fix if fix is not None else sug)
        if fix is not None and int(fix) != sug:
            manual += 1
        price = _price(p) / max(1, _unit(p))
        # Замороженное — только то, что стоит сверх расчёта: норма как таковая
        # не убыток, а разница между «решили» и «посчитали» — деньги, которые
        # держит на полке решение, а не спрос.
        if sug and norm > sug:
            frozen += (norm - sug) * price
        if not (sug or norm or have.get(pid)):
            continue
        rows.append({
            "id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
            "price": round(price, 2), "norm": norm, "suggested": sug,
            "manual": fix is not None, "have": int(have.get(pid) or 0),
            "expect": info.get("expect", 0), "safety": info.get("safety", 0),
            "per_day": info.get("base", 0), "cls": info.get("cls", ""),
        })
    # Сверху то, где расчёт и решение расходятся сильнее всего: если норму
    # смотрят, то ради этих строк.
    rows.sort(key=lambda r: (-abs(r["norm"] - r["suggested"]), -r["norm"], r["name"]))
    return web.json_response({
        "day": day, "district": d,
        "districts": [{"id": o, "code": OFFICE_CODES.get(o, ""),
                       "name": OFFICE_NAMES.get(o, o)} for o in OFFICE_IDS],
        "cover_days": NORM_COVER_DAYS, "window_days": NORM_HIST_DAYS,
        "manual": manual, "frozen_aed": round(frozen, 2),
        "norm_qty": sum(r["norm"] for r in rows),
        "norm_aed": round(sum(r["norm"] * r["price"] for r in rows), 2),
        "rows": rows,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_norm_reset(request):
    """Убрать ручную норму: позиция возвращается к расчёту.

    Отдельным действием, а не «поставьте ноль»: ноль — это тоже решение,
    и означает «не держим вовсе»."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    d, pid = str(body.get("district") or ""), str(body.get("product_id") or "")
    if d not in OFFICE_IDS or pid not in _catalog():
        return web.json_response({"error": "bad_args"}, status=400, headers=CORS_HEADERS)
    await db.del_stock_norm(d, pid)
    base_drop()
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


# ── сводка дня ───────────────────────────────────────────────────────────────
@require_owner
async def handle_status(request):
    day = (request.query.get("day") or "").strip() or _biz_day()
    counts = await db.get_stock_counts_for_day(day)
    by_d = {c["district"]: c for c in counts}
    try:
        await audit_sync_pending()
    except Exception as e:                           # noqa: BLE001
        log.warning(f"[audit] состояние не сверено: {e}")
    auds = await db.audits_by_day(day)
    districts = [{
        "id": oid, "code": OFFICE_CODES.get(oid, ""), "name": OFFICE_NAMES.get(oid, oid),
        "done": oid in by_d,
        # Ревизия за этот день: idle / running / pending / closed.
        "audit": _audit_view(auds.get(oid))["state"] if auds.get(oid) else "idle",
        "first_time": bool((by_d.get(oid) or {}).get("first_time")),
        "short_qty": int((by_d.get(oid) or {}).get("short_qty") or 0),
        "short_aed": int((by_d.get(oid) or {}).get("short_aed") or 0),
        "counted_at": (by_d.get(oid) or {}).get("counted_at", ""),
    } for oid in OFFICE_IDS]
    # Сколько списаний ждёт решения — здесь, а не в своём разделе: панель учёта
    # тянет этот ответ и так, а список списаний вместе с превью снимков весит
    # мегабайты и ради одного числа его грузить незачем.
    try:
        pend = len(await db.writeoff_pending(limit=200))
    except Exception as e:
        log.warning(f"[stock] очередь списаний не посчитана: {e}")
        pend = 0
    return web.json_response({
        "day": day,
        "done": sum(1 for d in districts if d["done"]), "total": len(districts),
        "short_aed": sum(d["short_aed"] for d in districts),
        "short_qty": sum(d["short_qty"] for d in districts),
        "writeoff_pending": pend,
        "districts": districts,
    }, headers=CORS_HEADERS)


# ── ревизия сканированием ───────────────────────────────────────────────────
# Ревизия отвечает на вопрос «что лежит на полке», и честнее камеры на него не
# отвечает ничто: галочка означает «посмотрел», а код означает «вот эта самая
# бутылка, и вот она здесь». Поэтому проход камерой идёт по всему району
# подряд, без выбора позиции: каждый код сам находит свою строку, а то, чего
# камера не увидела, остаётся недостачей.
#
# Вердикт считает сервер, а не приложение: правило одно на всех, и подменить
# его с телефона нельзя.
#
#   ok       наша бутылка, заведена на этой точке — так и должно быть
#   other    наша, но числится на другом районе: физически она здесь, значит
#            здесь и считаем, а расхождение по бумагам показываем глазами
#   written  списанная бутылка на полке: либо списали зря, либо не ту
#   sold     ушла с заказом, а лежит здесь — то же самое, вопрос к учёту
#   alien    в реестре нет вовсе: код с чужой наклейки или бутылка, которую
#            не завели. Такую в счёт не берём — приписать её некуда
def _scan_verdict(doc: dict, district: str) -> str:
    if not doc:
        return "alien"
    st = (doc.get("status") or "active").strip()
    # Убранная из реестра — для пересчёта та же чужая: в остатке её нет, и
    # приписать её некуда. Так же на неё смотрит проверка бутылки у оператора.
    if st == "deleted":
        return "alien"
    if st == "written":
        return "written"
    if (doc.get("district") or "").strip() != district:
        return "other"
    return "sold" if st == "sold" else "ok"


def _scan_state(district: str, day: str, counts: dict, odd: list,
                stats: dict, cat: dict) -> dict:
    """Состояние прохода — одинаковое и после скана, и при открытии экрана."""
    return {
        "district": district, "day": day, **stats,
        "counts": counts,
        "positions": len(counts),
        "odd": [{"code": o.get("code", ""), "verdict": o.get("verdict", ""),
                 "label": o.get("label", ""),
                 "name": o.get("product_name", "") or
                         (cat.get(o.get("product_id") or "") or {}).get("name", ""),
                 "at": _iso_of(o.get("at"))} for o in odd],
    }


@require_owner
async def handle_audit_scan(request):
    """Записать бутылку в проход. body: {district, code, day?}

    Повтор — не ошибка человека, а обычное дело: камера легко ловит ту же
    крышку дважды. Поэтому отвечаем спокойно и говорим, что эта бутылка уже
    посчитана; вставить её вторым разом всё равно нельзя — ключ занят."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = str(body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()
    import re as _re
    code = _re.sub(r"\s+", "", str(body.get("code") or ""))[:120]
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)

    doc = await db.qr_get(code)
    verdict = _scan_verdict(doc, district)
    pid = (doc or {}).get("product_id") or ""
    cat = _catalog()
    p = cat.get(pid) or {}
    fresh = await db.audit_scan_add(district, day, code, {
        "at": datetime.now(timezone.utc), "by": request["owner_id"],
        "product_id": pid if verdict != "alien" else "",
        "product_name": (doc or {}).get("product_name") or p.get("name", ""),
        "label": (doc or {}).get("label") or "",
        "verdict": verdict,
        "home": (doc or {}).get("district") or "",
    })
    counts = await db.audit_scan_counts(district, day)
    unit = _unit(p) if p else 1
    if verdict == "alien":
        log.warning(f"[audit] {district}: код не из реестра — {code[:40]}")
    return web.json_response({
        "ok": True, "new": fresh, "code": code, "verdict": verdict,
        "product_id": pid, "name": (doc or {}).get("product_name") or p.get("name", ""),
        "label": (doc or {}).get("label") or "",
        "home": (doc or {}).get("district") or "",
        "home_code": OFFICE_CODES.get((doc or {}).get("district") or "", ""),
        # Счёт по позиции — в бутылках: на экране скана человек считает
        # бутылки, а не ящики, и делить их пополам там незачем.
        "count": int(counts.get(pid) or 0),
        "unit": unit,
        "total": sum(counts.values()),
        "positions": len(counts),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_audit_scan_state(request):
    """Что уже насчитал проход. Экран открывается не с нуля: ревизию прерывают
    и возвращаются к ней, и человек должен видеть, продолжает он счёт или
    начинает заново."""
    district = (request.query.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = (request.query.get("day") or "").strip() or _biz_day()
    return web.json_response(
        _scan_state(district, day, await db.audit_scan_counts(district, day),
                    await db.audit_scan_odd(district, day),
                    await db.audit_scan_stats(district, day), _catalog()),
        headers=CORS_HEADERS)


@require_owner
async def handle_audit_scan_undo(request):
    """Убрать бутылку из прохода — ту, которую только что записали зря.

    Убираем конкретный код, а не «последний по базе»: район могут проходить
    вдвоём, и последним окажется чужой скан."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district = str(body.get("district") or "").strip()
    day = str(body.get("day") or "").strip() or _biz_day()
    import re as _re
    code = _re.sub(r"\s+", "", str(body.get("code") or ""))[:120]
    ok = await db.audit_scan_del(district, day, code)
    counts = await db.audit_scan_counts(district, day)
    return web.json_response({"ok": ok, "code": code, "total": sum(counts.values()),
                              "positions": len(counts)}, headers=CORS_HEADERS)


@require_owner
async def handle_audit_scan_reset(request):
    """Начать проход заново. Нужно редко и всегда по одной причине: посреди
    ревизии выяснилось, что считали не ту полку."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    district = str(body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()
    n = await db.audit_scan_clear(district, day)
    log.info(f"[audit] {district} {day}: проход сброшен, снято {n}")
    return web.json_response({"ok": True, "removed": n}, headers=CORS_HEADERS)


# ── ревизия: ожидаемое, лист, старт, завершение, решения ────────────────────
# Ревизия отвечает на вопрос «совпадает ли то, что числится, с тем, что
# лежит». Числится — по кодам: склад это внесённое кодами, а бутылки без кодов
# лежат отдельным красным долгом «QR код не внесён» и в ожидаемое не идут —
# камера их всё равно не увидит, и записать их недостачей было бы враньём.
# Лежит — то, что увидела камера: по коду, поштучно.
#
# Жизнь ревизии: начата → идёт проход → завершена (пересчёт записан) →
# закрыта, когда по недостаче решено, кто платит, а излишек внесён на район.
# Пока не закрыта — район горит «ждёт решения», и это намеренно: программа
# ведёт человека по шагам, а не оставляет расхождение висеть молча.
#
# Всё здесь — в бутылках. Остальной склад считает учётными единицами (у пива
# это ящик), но человек у полки сканирует бутылки, и в отчёте «не хватает 3»
# должно значить три бутылки. В пересчёт (stock_counts) итог записывается в
# единицах — тем же документом, что и раньше, чтобы заявка и стоимость склада
# читали его как обычный пересчёт.

def _bottles(units, unit: int) -> int:
    return int(round(float(units or 0) * unit))


async def _moves_since_count(district: str, cnt: dict, cat: dict) -> dict:
    """Перемещения после последнего пересчёта — в бутылках, со знаком.

    Основа заявки переездов не знает; для ревизии они обязательны: бутылка,
    увезённая вчера на B2, — не недостача на B5."""
    if not cnt or not cnt.get("day"):
        return {}
    since_at = str(cnt.get("counted_at") or "")
    out: dict = {}
    for m in await db.get_stock_transfers_since(cnt["day"]):
        if since_at and str(m.get("at") or "") <= since_at:
            continue
        pid = m.get("product_id")
        if not pid or pid not in cat:
            continue
        q = _bottles(m.get("qty") or 0, _unit(cat[pid]))
        if not q:
            continue
        if m.get("to") == district:
            out[pid] = out.get(pid, 0) + q
        if m.get("from") == district:
            out[pid] = out.get(pid, 0) - q
    return out


async def _audit_expected(district: str, day: str) -> tuple:
    """({позиция: ожидается бутылок}, {позиция: бутылок без кодов}, был ли пересчёт).

    Пересчёт был — от него, как основа заявки и стоимость склада: снимок +
    приход − продажи − списания, плюс переезды после снимка, минус бутылки без
    кодов. Так число в ревизии совпадает с тем, что показывает карточка склада.
    Пересчёта не было — от реестра, как лист."""
    cat = _catalog()
    base = await _district_base(day)
    b = base.get(district) or {}
    exp: dict = {}
    noqr: dict = {}
    if b.get("counted"):
        have = b.get("have") or {}
        detail: dict = {}
        try:
            import qr_routes
            await qr_routes.unscanned_by_district(None, detail)
        except Exception as e:                       # noqa: BLE001
            log.warning(f"[audit] невнесённые не посчитаны ({district}): {e}")
        miss = detail.get(district) or {}
        cnt = await db.get_last_stock_count(district)
        moved = await _moves_since_count(district, cnt, cat)
        for pid, p in cat.items():
            n = _bottles(have.get(pid) or 0, _unit(p)) + int(moved.get(pid) or 0)
            noqr[pid] = int(miss.get(pid) or 0)
            exp[pid] = max(0, n - noqr[pid])
        return exp, noqr, True
    reg = await _registry_was(district, day)
    sold = await _sold(day, district)
    mv = _moves_by_pid(await db.get_stock_transfers(day), district)
    for pid, p in cat.items():
        v = float(reg.get(pid) or 0) + float(mv.get(pid) or 0) - float(sold.get(pid) or 0)
        exp[pid] = max(0, _bottles(v, _unit(p)))
        noqr[pid] = 0
    return exp, noqr, False


async def _audit_lines(district: str, day: str) -> tuple:
    """Строки ревизии: ожидается / увидела камера / разница — в бутылках."""
    exp, noqr, counted = await _audit_expected(district, day)
    counts = await db.audit_scan_counts(district, day)
    cat = _catalog()
    await _loss_load()
    lines = []
    for pid, p in cat.items():
        e = int(exp.get(pid) or 0)
        a = int(counts.get(pid) or 0)
        d = e - a
        lines.append({
            "id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
            "no": order_key(pid) + 1, "unit": _unit(p),
            "price": _price(p),                        # прайс за учётную единицу
            "expected": e, "actual": a, "diff": d,     # >0 — не хватает бутылок
            "noqr": int(noqr.get(pid) or 0),
            "loss": _loss_of(pid, d) if d > 0 else 0,  # закупка за пропавшие
        })
    lines.sort(key=lambda r: r["no"])
    return lines, counted


def _audit_totals(lines: list) -> dict:
    short = [l for l in lines if l["diff"] > 0]
    over = [l for l in lines if l["diff"] < 0]
    live = [l for l in lines if l["expected"] or l["actual"]]
    return {
        "expected": sum(l["expected"] for l in lines),
        "actual": sum(l["actual"] for l in lines),
        "short_qty": sum(l["diff"] for l in short),
        "short_aed": sum(l["loss"] for l in short),
        "over_qty": sum(-l["diff"] for l in over),
        "noqr": sum(l["noqr"] for l in lines),
        "positions": len(live),
        "matched": sum(1 for l in live if not l["diff"]),
        "mismatched": len(short) + len(over),
    }


def _audit_view(a: dict | None) -> dict:
    """Состояние ревизии для экрана — одно на лист, отчёт и список районов."""
    a = a or {}
    fin = bool(a.get("finished_at"))
    state = ("closed" if a.get("closed_at") else "pending" if fin
             else "running" if a.get("started_at") else "idle")
    return {
        "state": state,
        "started_at": a.get("started_at", ""), "started_by": a.get("started_by_name", ""),
        "finished_at": a.get("finished_at", ""), "closed_at": a.get("closed_at", ""),
        "short": a.get("short") or None, "over": a.get("over") or None,
        "alien": int(a.get("alien") or 0),
        "result": a.get("result") or None,
    }


def _audit_rows_saved(a: dict) -> list:
    """Строки завершённой ревизии — из сохранённого, с именами из каталога.
    Живой пересчёт после завершения показал бы нули: пересчёт уже записан,
    и ожидаемое сравнялось с фактом."""
    cat = _catalog()
    rows = []
    for l in (a or {}).get("lines") or []:
        p = cat.get(l.get("id")) or {}
        rows.append({**l, "name": p.get("name", ""), "cat": p.get("cat", ""),
                     "no": order_key(l.get("id") or "") + 1, "unit": _unit(p),
                     "price": _price(p)})
    rows.sort(key=lambda r: r["no"])
    return rows


async def _audit_report(district: str, day: str, a: dict = None) -> dict:
    a = a if a is not None else (await db.audit_get(district, day) or {})
    a, alien = await _audit_sync(district, day, a)
    rows = [r for r in _audit_rows_saved(a) if r.get("diff")]
    rows.sort(key=lambda r: (-abs(r.get("loss") or 0), -abs(r["diff"])))
    return {
        "district": district,
        "district_name": OFFICE_NAMES.get(district, district),
        "district_code": OFFICE_CODES.get(district, ""),
        "day": day, "audit": _audit_view(a), "rows": rows,
        "totals": a.get("result") or {},
        "alien": alien,
    }


def _district_of(request, body: dict = None) -> tuple:
    src = body if body is not None else request.query
    district = str(src.get("district") or "").strip()
    day = str(src.get("day") or "").strip() or _biz_day()
    return district, day


@require_owner
async def handle_audit_sheet(request):
    """Лист ревизии: что числится и что увидела камера. ?district=&day=

    Идёт — считается живьём от каждого скана. Завершена — из сохранённого,
    чтобы страница района и после закрытия показывала, чем ревизия кончилась."""
    district, day = _district_of(request)
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    a = await db.audit_get(district, day) or {}
    if a.get("finished_at"):
        rows = _audit_rows_saved(a)
        counted = True
        totals = a.get("result") or _audit_totals(rows)
    else:
        rows, counted = await _audit_lines(district, day)
        totals = _audit_totals(rows)
    stats = await db.audit_scan_stats(district, day)
    try:
        coded = sum((await db.qr_by_product_district(district)).values())
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[audit] коды района не посчитаны ({district}): {e}")
        coded = 0
    return web.json_response({
        "district": district,
        "district_name": OFFICE_NAMES.get(district, district),
        "district_code": OFFICE_CODES.get(district, ""),
        "day": day, "counted": counted, "rows": rows, "totals": totals,
        # Сколько бутылок на районе заведено кодами. Ноль — ревизии нечего
        # считать, камера ответит «нет в реестре» на каждую бутылку.
        "coded": int(coded),
        "audit": _audit_view(a), "scan": stats,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_audit_start(request):
    """Начать ревизию района. body: {district, day?, as?}. Повтор — не ошибка:
    ревизию прерывают и возвращаются к ней."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    a = await db.audit_get(district, day) or {}
    if a.get("finished_at"):
        return web.json_response({"error": "finished", "audit": _audit_view(a)},
                                 status=409, headers=CORS_HEADERS)
    if not a.get("started_at"):
        a = await db.audit_set(district, day, {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_by": int(request["owner_id"] or 0),
            "started_by_name": str(body.get("as") or "").strip()[:60]})
        log.info(f"[audit] {district} {day}: ревизия начата")
    return web.json_response({"ok": True, "audit": _audit_view(a)}, headers=CORS_HEADERS)


@require_owner
async def handle_audit_finish(request):
    """Завершить ревизию: записать пересчёт по камере и разобрать, чего не
    хватает и чего лишнее. body: {district, day?, as?}

    Сошлось — ревизия закрыта сразу. Иначе она ждёт решений: по недостаче —
    кто платит, по излишку — внести на район."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    a = await db.audit_get(district, day) or {}
    if a.get("finished_at"):
        return web.json_response({"error": "finished", "audit": _audit_view(a)},
                                 status=409, headers=CORS_HEADERS)
    if not a.get("started_at"):
        return web.json_response({"error": "not_started"}, status=409, headers=CORS_HEADERS)

    await _audit_refresh_alien(district, day)
    lines, counted = await _audit_lines(district, day)
    tot = _audit_totals(lines)
    stats = await db.audit_scan_stats(district, day)
    codes = await db.audit_scan_codes(district, day)
    alien = sum(1 for c in codes if (c.get("verdict") or "") == "alien")
    by_pid: dict = {}
    for c in codes:
        pid = c.get("product_id") or ""
        if pid and (c.get("verdict") or "ok") != "ok":
            by_pid.setdefault(pid, []).append({
                "code": c.get("code", ""), "verdict": c.get("verdict", ""),
                "home": c.get("home", ""), "home_code": OFFICE_CODES.get(c.get("home") or "", "")})
    short_lines = [{"id": l["id"], "name": l["name"], "qty": l["diff"],
                    "loss": l["loss"], "price": l["price"], "unit": l["unit"]}
                   for l in lines if l["diff"] > 0]
    over_lines = []
    for l in lines:
        if l["diff"] >= 0:
            continue
        odd = by_pid.get(l["id"]) or []
        over_lines.append({
            "id": l["id"], "name": l["name"], "qty": -l["diff"], "unit": l["unit"],
            "other": sum(1 for c in odd if c["verdict"] == "other"),
            "written": sum(1 for c in odd if c["verdict"] == "written"),
            "sold": sum(1 for c in odd if c["verdict"] == "sold"),
            "codes": odd})
    short = ({"qty": tot["short_qty"], "aed": tot["short_aed"], "lines": short_lines}
             if short_lines else None)
    over = ({"qty": tot["over_qty"], "lines": over_lines} if over_lines else None)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    who = str(body.get("as") or "").strip()[:60]
    # Пересчёт — обычным документом, в учётных единицах: с него дальше живут
    # заявка и стоимость склада, и ревизия для них — просто свежий снимок.
    doc_lines = []
    for l in lines:
        u = l["unit"]
        e, act = _num(l["expected"] / u), _num(l["actual"] / u)
        doc_lines.append({"id": l["id"], "name": l["name"], "price": l["price"], "unit": u,
                          "was": e, "income": 0, "moved_qty": 0, "sold": 0,
                          "expected": e, "actual": act, "diff": _num(e - act),
                          "counted": True, "mark": "ok" if not l["diff"] else "diff"})
    doc = {"district": district, "district_name": OFFICE_NAMES.get(district, district),
           "day": day, "first_time": False,
           "counted_by": request["owner_id"], "counted_at": now_iso,
           "lines": doc_lines,
           "short_qty": tot["short_qty"], "short_aed": tot["short_aed"],
           "over_qty": tot["over_qty"],
           "counted_qty": len(doc_lines), "total_qty": len(doc_lines),
           "scan_qty": int(stats.get("total") or 0),
           "audit_started_at": a.get("started_at") or now_iso,
           "audit_finished_at": now_iso, "audit_by": request["owner_id"],
           "marked_qty": len(doc_lines), "matched_qty": tot["matched"],
           "mismatch_qty": tot["mismatched"]}
    await db.save_stock_count(district, day, doc)
    base_drop()
    fields = {
        "finished_at": now_iso, "finished_by": int(request["owner_id"] or 0),
        "finished_by_name": who, "short": short, "over": over, "alien": alien,
        "result": {**tot, "scan_qty": int(stats.get("total") or 0), "counted": counted},
        "lines": [{"id": l["id"], "expected": l["expected"], "actual": l["actual"],
                   "diff": l["diff"], "noqr": l["noqr"], "loss": l["loss"]} for l in lines],
    }
    # Сошлось и чужих кодов нет — закрыта сразу. Чужие коды — это бутылки без
    # места в учёте; ревизия ждёт, пока их внесут (см. _audit_alien).
    if not short and not over and not alien:
        fields["closed_at"] = now_iso
    a = await db.audit_set(district, day, fields)
    log.info(f"[audit] {district} {day}: завершена — не хватает {tot['short_qty']} бут "
             f"/ {tot['short_aed']} AED, излишек {tot['over_qty']}, не в реестре {alien}, "
             f"сканом {stats.get('total', 0)}")
    await backdate.notify(day, who, "ревизия",
                          f"{OFFICE_CODES.get(district, district)} — сканом {stats.get('total', 0)}"
                          + (f", не хватает {tot['short_aed']} AED" if tot["short_aed"] else ""))
    return web.json_response(await _audit_report(district, day, a), headers=CORS_HEADERS)


def _audit_close_if_done(a: dict, fields: dict, now_iso: str, alien_left: int = 0) -> None:
    """Закрыть ревизию, когда решено всё, что требовало решения: недостача,
    излишек и бутылки, чьих кодов не было в реестре."""
    short = fields.get("short", a.get("short"))
    over = fields.get("over", a.get("over"))
    if ((not short or short.get("resolved_at")) and (not over or over.get("resolved_at"))
            and not alien_left):
        if not a.get("closed_at"):
            fields["closed_at"] = now_iso


# ── коды не из реестра ──────────────────────────────────────────────────
# Камера увидела код, которого реестр не знает: бутылка стоит на полке, но
# ни в остатке, ни в пересчёте её нет — приписать её некуда. Такой итог тоже
# должен кончаться решением, а не строкой «кодов не из реестра: N»: бутылку
# вносят как новый товар (приход, src=new), и ревизия закрывается, когда
# внесены все. Решение проверяется по реестру, а не по нажатию кнопки:
# внесли — значит решено.
async def _audit_alien(district: str, day: str) -> dict | None:
    """{qty, left, done, codes:[{code, done, name}]} или None, если чужих кодов не было."""
    codes = [c for c in await db.audit_scan_codes(district, day)
             if (c.get("verdict") or "") == "alien"]
    if not codes:
        return None
    out = []
    for c in codes:
        code = c.get("code") or ""
        doc = await db.qr_get(code) or {}
        done = bool(doc) and (doc.get("status") or "active") != "deleted"
        out.append({"code": code, "done": done,
                    "name": (doc.get("product_name") or "") if done else "",
                    "home_code": OFFICE_CODES.get(doc.get("district") or "", "") if done else ""})
    left = sum(1 for x in out if not x["done"])
    return {"qty": len(out), "left": left, "done": len(out) - left, "codes": out}


async def _audit_refresh_alien(district: str, day: str) -> int:
    """Перед подсчётом: коды, внесённые в реестр после скана, получают
    настоящий вердикт и позицию — иначе возобновлённая ревизия увидела бы
    в них недостачу (в реестре есть, камерой «не видела»)."""
    n = 0
    cat = _catalog()
    for c in await db.audit_scan_codes(district, day):
        if (c.get("verdict") or "") != "alien":
            continue
        doc = await db.qr_get(c.get("code") or "")
        v = _scan_verdict(doc, district)
        if v == "alien":
            continue
        pid = (doc or {}).get("product_id") or ""
        p = cat.get(pid) or {}
        if await db.audit_scan_update(district, day, c.get("code") or "", {
                "verdict": v, "product_id": pid,
                "product_name": (doc or {}).get("product_name") or p.get("name", ""),
                "label": (doc or {}).get("label") or "",
                "home": (doc or {}).get("district") or ""}):
            n += 1
    return n


async def _audit_sync(district: str, day: str, a: dict) -> tuple:
    """(ревизия, коды не из реестра). Завершённая, где всё решено и чужие коды
    уже внесены, закрывается здесь — внесение идёт другим экраном, и момент
    «внесли последнюю» ревизия узнаёт при следующем взгляде на себя."""
    a = a or {}
    alien = await _audit_alien(district, day) if int(a.get("alien") or 0) else None
    if a.get("finished_at") and not a.get("closed_at"):
        fields: dict = {}
        _audit_close_if_done(a, fields, datetime.now(timezone.utc).isoformat(),
                             alien["left"] if alien else 0)
        if fields:
            a = await db.audit_set(district, day, fields)
            log.info(f"[audit] {district} {day}: закрыта — коды внесены")
    return a, alien


async def audit_sync_pending() -> list:
    """Ревизии, которые всё ещё ждут решения; те, где ждали только внесения
    чужих кодов и коды внесены, закрываются по пути. У каждой — alien_left."""
    out = []
    for a in await db.audits_pending():
        district, day = a.get("district", ""), a.get("day", "")
        a, alien = await _audit_sync(district, day, a)
        if a.get("closed_at"):
            continue
        out.append({**a, "alien_left": alien["left"] if alien else 0})
    return out


@require_owner
async def handle_audit_short(request):
    """Решение по недостаче. body: {district, day?, blame: bool, who?, amount?,
    split?: [{who, amount}], note?, as?}

    Недостача записывается списаниями вида «недостача» — по одному на позицию,
    своей рукой владельца, сразу учтёнными. Со склада они ничего не вычитают:
    пересчёт ревизии уже записан без этих бутылок (см. writeoff_since,
    skip_audit). Удержание, если виновный назван, ставится на первое из них —
    тем же удержанием, что у боя: водителю уходит сообщение, в финансах это
    долг."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    a = await db.audit_get(district, day) or {}
    short = a.get("short")
    if not a.get("finished_at") or not short:
        return web.json_response({"error": "no_short"}, status=409, headers=CORS_HEADERS)
    if short.get("resolved_at"):
        return web.json_response({"error": "resolved"}, status=409, headers=CORS_HEADERS)
    blame = bool(body.get("blame"))
    who = str(body.get("who") or "").strip()[:60]
    note = str(body.get("note") or "").strip()[:200]
    try:
        amount = max(0, int(round(float(body.get("amount") or 0))))
    except (TypeError, ValueError):
        amount = 0
    split = _split_parse(body)
    if split:
        who = ", ".join(x["who"] for x in split)
        amount = sum(x["amount"] for x in split)
    if blame and (not who or not amount):
        return web.json_response({"error": "who_and_amount"}, status=400, headers=CORS_HEADERS)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    by_name = str(body.get("as") or "").strip()[:60] or "владелец"
    cat = _catalog()
    wids = []
    for l in short.get("lines") or []:
        pid = l.get("id")
        if pid not in cat or not int(l.get("qty") or 0):
            continue
        wid = await db.writeoff_add({
            "at": now, "day": day, "item": pid, "thumb": "",
            "name": cat[pid].get("name", ""), "qty": int(l["qty"]), "kind": "недостача",
            "note": note, "district": district,
            "district_code": OFFICE_CODES.get(district, ""),
            "by": by_name, "by_id": int(request["owner_id"] or 0),
            "own": True, "state": "ok",
            "decided_at": now, "decided_by": int(request["owner_id"] or 0),
            "decided_by_name": by_name,
            "src": "audit", "audit_day": day,
        }, b"")
        wids.append(wid)
    try:
        await db.writeoff_none_clear(day)
    except Exception:                               # noqa: BLE001
        pass
    comp = None
    if blame and wids:
        doc = await db.writeoff_compensate(wids[0], who, amount, note,
                                           request.get("owner_id") or 0, by_name, split)
        if doc:
            comp = _comp_view(doc)
            try:
                await _writeoff_comp_tell(doc)
            except Exception as e:                  # noqa: BLE001
                log.warning(f"[audit] про удержание не сообщили: {e}")
    short = {**short, "resolved_at": now_iso, "blame": blame,
             "who": who if blame else "", "amount": amount if blame else 0,
             "split": split if blame else None, "note": note, "writeoffs": wids,
             "by_name": by_name}
    fields = {"short": short}
    alien = await _audit_alien(district, day) if int(a.get("alien") or 0) else None
    _audit_close_if_done(a, fields, now_iso, alien["left"] if alien else 0)
    a = await db.audit_set(district, day, fields)
    base_drop()
    log.info(f"[audit] {district} {day}: недостача — "
             f"{'удержано %s с %s' % (amount, who) if blame else 'без виновного'}"
             f" · списаний {len(wids)}")
    return web.json_response({**(await _audit_report(district, day, a)), "comp": comp},
                             headers=CORS_HEADERS)


@require_owner
async def handle_audit_over(request):
    """Внести излишек на район. body: {district, day?, as?}

    Бутылка с чужой точки переезжает сюда — реестр и книга переездов, как у
    обычного перемещения. Но переезд датируется до пересчёта ревизии: сам
    пересчёт эту бутылку уже видел здесь, и поправка «плюс один» после него
    посчитала бы её дважды. У точки-отправителя поправка работает как надо —
    там пересчёт старше. Списанная или проданная, а на полке живая —
    возвращается в остаток."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    a = await db.audit_get(district, day) or {}
    over = a.get("over")
    if not a.get("finished_at") or not over:
        return web.json_response({"error": "no_over"}, status=409, headers=CORS_HEADERS)
    if over.get("resolved_at"):
        return web.json_response({"error": "resolved"}, status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    by_name = str(body.get("as") or "").strip()[:60]
    me = int(request["owner_id"] or 0)
    cat = _catalog()
    # Секундой раньше пересчёта — см. пояснение выше.
    try:
        fin = datetime.fromisoformat(str(a.get("finished_at")))
        before = (fin - timedelta(seconds=1)).isoformat()
    except Exception:                               # noqa: BLE001
        before = now_iso
    moved = restored = 0
    for l in over.get("lines") or []:
        pid = l.get("id")
        p = cat.get(pid) or {}
        for c in l.get("codes") or []:
            code, v = c.get("code") or "", c.get("verdict") or ""
            if not code:
                continue
            if v == "other":
                src = (await db.qr_get(code) or {}).get("district") or ""
                if src == district:
                    continue
                tid = await db.add_stock_transfer(
                    {"day": day, "from": src, "to": district, "product_id": pid,
                     "product_name": p.get("name", ""), "qty": 1 / max(1, _unit(p)),
                     "src": "qr", "code": code, "by": me, "by_name": by_name,
                     "by_kind": "owner", "audit": day, "at": before})
                if await db.qr_move(code, src, district, tid, me, before):
                    moved += 1
                else:
                    await db.delete_stock_transfer(tid)
            elif v in ("written", "sold"):
                if await db.qr_restore(code, district, me, now_iso):
                    restored += 1
    over = {**over, "resolved_at": now_iso, "moved": moved, "restored": restored,
            "by_name": by_name}
    fields = {"over": over}
    alien = await _audit_alien(district, day) if int(a.get("alien") or 0) else None
    _audit_close_if_done(a, fields, now_iso, alien["left"] if alien else 0)
    a = await db.audit_set(district, day, fields)
    base_drop()
    log.info(f"[audit] {district} {day}: излишек внесён — перевезено {moved}, "
             f"возвращено {restored}")
    return web.json_response(await _audit_report(district, day, a), headers=CORS_HEADERS)


async def _audit_undo_short(a: dict) -> int:
    """Снять решение по недостаче: списания стираются, каждому, с кого
    удерживали, уходит «Удержание снято». Сколько записей стёрто."""
    short = (a or {}).get("short") or {}
    if not short.get("resolved_at"):
        return 0
    n = 0
    for wid in short.get("writeoffs") or []:
        doc = await db.writeoff_get(wid)
        if not doc:
            continue
        comp = doc.get("comp") or {}
        if comp.get("amount"):
            for part in comp.get("split") or [{"who": comp.get("who")}]:
                try:
                    await _writeoff_comp_tell({**doc, "comp": {}, "by": part.get("who") or ""})
                except Exception as e:                      # noqa: BLE001
                    log.warning(f"[audit] о снятом удержании не сообщили: {e}")
        if await db.writeoff_del(wid):
            n += 1
    return n


async def _audit_undo_over(a: dict) -> tuple:
    """Вернуть внесённый излишек: переезды назад, возвращённые в остаток
    бутылки снова помечаются как были. (переездов, возвратов)."""
    over = (a or {}).get("over") or {}
    if not over.get("resolved_at"):
        return 0, 0
    moves = restored = 0
    for l in over.get("lines") or []:
        for c in l.get("codes") or []:
            code, v = c.get("code") or "", c.get("verdict") or ""
            if not code:
                continue
            if v == "other":
                st, _ = await move_undo_by_code(code)
                if st == 200:
                    moves += 1
            elif v in ("written", "sold"):
                if await db.qr_unrestore(code, v):
                    restored += 1
    return moves, restored


def _fresh(block: dict, keys: tuple) -> dict:
    """Решение снято — от блока остаётся только то, что нашла ревизия."""
    return {k: v for k, v in (block or {}).items() if k in keys}


@require_owner
async def handle_audit_short_undo(request):
    """Снять решение по недостаче и выбрать заново. body: {district, day?}
    Ревизия при этом снова «ждёт решения»."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    a = await db.audit_get(district, day) or {}
    short = a.get("short") or {}
    if not a.get("finished_at") or not short.get("resolved_at"):
        return web.json_response({"error": "not_resolved"}, status=409, headers=CORS_HEADERS)
    n = await _audit_undo_short(a)
    await db.audit_set(district, day, {"short": _fresh(short, ("qty", "aed", "lines"))})
    a = await db.audit_unset(district, day, ["closed_at"])
    base_drop()
    log.info(f"[audit] {district} {day}: решение по недостаче снято, списаний стёрто {n}")
    return web.json_response(await _audit_report(district, day, a), headers=CORS_HEADERS)


@require_owner
async def handle_audit_over_undo(request):
    """Вернуть внесённый излишек и решить заново. body: {district, day?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    a = await db.audit_get(district, day) or {}
    over = a.get("over") or {}
    if not a.get("finished_at") or not over.get("resolved_at"):
        return web.json_response({"error": "not_resolved"}, status=409, headers=CORS_HEADERS)
    moves, restored = await _audit_undo_over(a)
    await db.audit_set(district, day, {"over": _fresh(over, ("qty", "lines"))})
    a = await db.audit_unset(district, day, ["closed_at"])
    base_drop()
    log.info(f"[audit] {district} {day}: излишек возвращён — переездов {moves}, возвратов {restored}")
    return web.json_response(await _audit_report(district, day, a), headers=CORS_HEADERS)


@require_owner
async def handle_audit_reopen(request):
    """Возобновить завершённую ревизию. body: {district, day?, as?}

    Завершить можно рано — забыли полку, спутали район. Всё, что ревизия
    успела записать, откатывается: пересчёт стирается (снимок запишется заново
    при завершении), списания недостачи удаляются, а снятое удержание уходит
    водителю сообщением; переезды излишка возвращаются, бутылки, возвращённые
    в остаток, снова помечаются как были. Сканы остаются — продолжают с того
    места, где остановились."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    district, day = _district_of(request, body)
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    a = await db.audit_get(district, day) or {}
    if not a.get("finished_at"):
        return web.json_response({"error": "not_finished"}, status=409, headers=CORS_HEADERS)
    now_iso = datetime.now(timezone.utc).isoformat()
    by_name = str(body.get("as") or "").strip()[:60]
    undone = {"writeoffs": await _audit_undo_short(a)}
    undone["moves"], undone["restored"] = await _audit_undo_over(a)
    await db.delete_stock_count(district, day)
    a = await db.audit_unset(district, day, ["finished_at", "finished_by", "finished_by_name",
                                            "closed_at", "short", "over", "alien", "result", "lines"])
    a = await db.audit_set(district, day, {"reopened_at": now_iso, "reopened_by_name": by_name,
                                          "reopens": int(a.get("reopens") or 0) + 1})
    base_drop()
    log.info(f"[audit] {district} {day}: возобновлена — снято списаний {undone['writeoffs']}, "
             f"переездов {undone['moves']}, возвратов {undone['restored']}")
    return web.json_response({"ok": True, "audit": _audit_view(a), "undone": undone},
                             headers=CORS_HEADERS)


@require_owner
async def handle_audit_report(request):
    """Отчёт ревизии: чем кончилась и что решено. ?district=&day="""
    district, day = _district_of(request)
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)
    a = await db.audit_get(district, day)
    if not a or not a.get("finished_at"):
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    return web.json_response(await _audit_report(district, day, a), headers=CORS_HEADERS)


def _minutes_between(a, b):
    """Сколько минут прошло между двумя отметками ISO; не разобрать — None."""
    try:
        t1 = datetime.fromisoformat(str(a).replace("Z", "+00:00").replace(" ", "T"))
        t2 = datetime.fromisoformat(str(b).replace("Z", "+00:00").replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    if t1.tzinfo is None: t1 = t1.replace(tzinfo=timezone.utc)
    if t2.tzinfo is None: t2 = t2.replace(tzinfo=timezone.utc)
    return max(0, int((t2 - t1).total_seconds() // 60))


@require_owner
async def handle_audits(request):
    """История ревизий: что и когда закрывали. Только заголовки — сами позиции
    приходят с /stock/result, когда открывают конкретную."""
    try:
        limit = max(1, min(200, int(request.query.get("limit", "60") or 60)))
    except ValueError:
        limit = 60
    rows = await db.get_finished_audits(limit=limit)
    out = []
    for c in rows:
        a = await db.audit_get(c.get("district", ""), c.get("day", "")) or {}
        v = _audit_view(a)
        short, over = (v["short"] or {}), (v["over"] or {})
        res = v["result"] or {}
        started = c.get("audit_started_at", "")
        finished = c.get("audit_finished_at", "")
        out.append({
            "state": v["state"] if a else "closed",
            "closed_at": v["closed_at"],
            "short_open": bool(short and not short.get("resolved_at")),
            "over_open": bool(over and not over.get("resolved_at")),
            "district": c.get("district", ""),
            "district_code": OFFICE_CODES.get(c.get("district"), ""),
            "district_name": OFFICE_NAMES.get(c.get("district"), c.get("district", "")),
            "day": c.get("day", ""),
            "scan_qty": int(c.get("scan_qty") or 0),
            "started_at": started,
            "finished_at": finished,
            "total": int(c.get("total_qty") or 0),
            "matched": int(c.get("matched_qty") or 0),
            "mismatched": int(c.get("mismatch_qty") or 0),
            "short_qty": c.get("short_qty") or 0,
            "short_aed": int(c.get("short_aed") or 0),
            "over_qty": c.get("over_qty") or 0,
            # Для страницы истории: кто считал и сколько это заняло, чем
            # кончилось решение по недостаче и куда делся излишек.
            "started_by": v["started_by"],
            "finished_by": a.get("finished_by_name", "") or "",
            "minutes": _minutes_between(started, finished),
            "expected": res.get("expected"), "actual": res.get("actual"),
            "positions": res.get("positions"), "alien": v["alien"],
            "short_resolved": bool(short.get("resolved_at")),
            "short_blame": bool(short.get("blame")),
            "short_who": short.get("who", "") or "",
            "short_amount": int(short.get("amount") or 0),
            "over_resolved": bool(over.get("resolved_at")),
            "over_moved": int(over.get("moved") or 0),
            "over_restored": int(over.get("restored") or 0),
            "alien_open": bool(a and v["alien"] and v["state"] == "pending"),
        })
    return web.json_response({"audits": out}, headers=CORS_HEADERS)


@require_owner
async def handle_result(request):
    district = (request.query.get("district") or "").strip()
    day = (request.query.get("day") or "").strip() or _biz_day()
    c = await db.get_stock_count(district, day)
    if not c:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    c.pop("_id", None)
    lines = c.get("lines", [])
    # В отчёте нужны и расхождения, и то, что менеджер отметил крестиком: он мог
    # поправить число до совпадения с ожидаемым, и diff обнулился — но факт,
    # что на полке лежало иначе, из отчёта пропадать не должен.
    c["lines"] = sorted(
        [l for l in lines if l.get("diff") or l.get("mark") == "diff"],
        key=lambda l: -abs((l.get("diff") or 0) * (l.get("price") or 0)))
    c["counted_lines"] = sum(1 for l in lines if l.get("counted"))
    c["district_code"] = OFFICE_CODES.get(c.get("district"), "")
    return web.json_response(c, headers=CORS_HEADERS)


@require_owner
async def handle_order_edit(request):
    """Поправить количество в заявке руками.

    Правится клетка «позиция × точка», а не итог: развозить всё равно по
    точкам, и правка «дай на десять меньше» без указания, где именно меньше,
    не превращается в заявку. Пустое значение снимает правку и возвращает
    расчёт."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)

    day = (body.get("day") or "").strip() or _biz_day()
    pid = (body.get("id") or "").strip()
    if pid not in _catalog():
        return web.json_response({"error": "unknown_product"}, status=400, headers=CORS_HEADERS)
    district = (body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)

    qty = body.get("qty")
    if qty is None or qty == "":
        await db.zayavka_edit_set(day, pid, district, None)
    else:
        try:
            qty = max(0, min(9999, int(qty)))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_qty"}, status=400, headers=CORS_HEADERS)
        await db.zayavka_edit_set(day, pid, district, qty)

    data = await order_rows(day)
    row = next((r for r in data["all_rows"] if r["id"] == pid), None)
    return web.json_response({"ok": True, "row": row,
                              "total_qty": data["total_qty"], "total_aed": data["total_aed"],
                              "edited_count": data["edited_count"],
                              "rows_count": len(data["rows"])},
                             headers=CORS_HEADERS)


@require_owner
async def handle_order_reset(request):
    """Снять правки: по одной позиции или по всей заявке."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    day = (body.get("day") or "").strip() or _biz_day()
    await db.zayavka_edit_clear(day, (body.get("id") or "").strip() or None)
    data = await order_rows(day)
    return web.json_response({"ok": True, "total_qty": data["total_qty"],
                              "total_aed": data["total_aed"],
                              "edited_count": data["edited_count"],
                              "rows": data["rows"], "districts": data["districts"]},
                             headers=CORS_HEADERS)


def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


# ── Журнал смен ─────────────────────────────────────────────────────────────
# Открытие и закрытие лежат в разных коллекциях, потому что это разные события,
# но читают их вместе: вопрос всегда один — что было со сменой. Кто открыл, во
# сколько, кто вышел, кто закрыл и с каким итогом.
def shift_log_rows(rows: list, days: dict = None) -> list:
    """days — {(день, водитель): запись driver_days}: когда водитель САМ открыл
    и закрыл смену в своём приложении. Отметка оператора («working») говорит
    только, что его ждали; вышел ли он — отвечает его собственное открытие."""
    from config_offices import OFFICE_CODES as _C, OFFICE_NAMES as _N
    days = days or {}
    out = []
    for r in rows:
        d = r.get("district") or ""
        crew = r.get("drivers") or {}
        day = r.get("day", "")
        def _crew(n, v):
            dd = days.get((day, n)) or {}
            return {"name": n, "working": bool(v),
                    "opened_at": _iso_of(dd.get("shift_open_at")) if dd.get("shift_open_at") else "",
                    "closed_at": _iso_of(dd.get("shift_close_at")) if dd.get("shift_close_at") else ""}
        out.append({
            "kind": r.get("kind"), "day": day, "district": d,
            "code": _C.get(d, ""), "name": _N.get(d, d),
            "at": _iso_of(r.get("at")), "by": r.get("by_name") or "",
            "operator": r.get("operator") or "",
            "crew": [_crew(n, v) for n, v in sorted(crew.items())],
            "orders": int(r.get("orders") or 0), "revenue": int(r.get("revenue") or 0),
            "open": int(r.get("open") or 0),
        })
    return out


@require_owner
async def handle_shift_log(request):
    """История смен: кто открыл, кого отметил, кто закрыл и с каким итогом."""
    try:
        days = max(1, min(90, int(request.query.get("days", "14") or 14)))
    except ValueError:
        days = 14
    today = _biz_day()
    d0 = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    # ?day=ГГГГ-ММ-ДД — один день: экран смен листают полосой дня, а не
    # прокручивают две недели подряд.
    one = (request.query.get("day") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", one):
        d0 = today = one
        days = 1
    # Открытия смен самими водителями — вторым запросом за тот же отрезок:
    # по ним видно, кто реально вышел, а не кого отметили.
    try:
        dd = {(x.get("day"), x.get("driver")): x
              for x in await db.get_driver_days_range(d0, today)}
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[shifts] дни водителей не прочитаны: {e}")
        dd = {}
    rows = shift_log_rows(await db.shift_journal(d0, today), dd)
    # Группируем по суткам: смену смотрят днями, а не событиями подряд.
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    days_out = [{"day": k, "rows": rs,
                 "orders": sum(x["orders"] for x in rs if x["kind"] == "close"),
                 "revenue": sum(x["revenue"] for x in rs if x["kind"] == "close"),
                 "opened": sum(1 for x in rs if x["kind"] == "open"),
                 "closed": sum(1 for x in rs if x["kind"] == "close")}
                for k, rs in sorted(by_day.items(), reverse=True)]
    return web.json_response({"days": days, "from": d0, "to": today,
                              "list": days_out}, headers=CORS_HEADERS)


# ── Списания ────────────────────────────────────────────────────────────────
# Закупочные цены держим тем же слоем, что и оценка склада: поставка → прайс →
# рука владельца. Второй источник цены рядом с первым разошёлся бы молча.
_LOSS_CACHE: dict = {}


def _loss_of(pid: str, qty: int) -> int:
    """Во что обошлась потеря: закупка за бутылку × количество, AED.

    Цена в прайсе стоит за учётную единицу — у пива это ящик из двадцати
    четырёх, а бьётся одна банка. Поэтому делим.

    Цены нет — возвращаем ноль, и это честнее выдуманной: сумму тогда впишет
    человек, а не программа наугад."""
    try:
        цена = float(_LOSS_CACHE.get(pid) or 0)
        if not цена:
            return 0
        unit = max(1, _unit(_catalog().get(pid) or {}))
        return int(round(цена / unit * max(0, int(qty or 0))))
    except Exception:                                        # noqa: BLE001
        return 0


async def _loss_load():
    """Подтянуть цены закупки. Зовётся перед выдачей списаний: строка сама
    ходить в базу не может, а считать без цен — значит показать нули."""
    global _LOSS_CACHE
    try:
        import stock_value
        _LOSS_CACHE = await stock_value.cost_map()
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[writeoff] цены закупки не прочитаны: {e}")


def _wo_row(r: dict, cat: dict) -> dict:
    """Одна строка списания для владельца — и в очереди, и в истории."""
    qty = int(r.get("qty") or 0)
    pid = r.get("item") or ""
    # Считаем и деньги: «пять бутылок» и «пять бутылок Хеннесси» — разные
    # новости, а понять это по названию можно, только зная прайс наизусть.
    # Цена продажная: закупочной система не знает, и честнее назвать это
    # «по прайсу», чем выдать выдуманную себестоимость за факт. Делим на
    # единицу учёта: списывают бутылки, а цена у пива — за ящик.
    p = cat.get(pid) or {}
    return {
        "id": r.get("_id"), "at": _iso_of(r.get("at")), "day": r.get("day", ""),
        "item": pid, "name": r.get("name", "") or p.get("name", ""),
        "qty": qty, "aed": round(_price(p) / max(1, _unit(p)) * qty, 2),
        # Во что обошлась потеря НАМ — по закупке, а не по прайсу. С водителя
        # удерживают убыток, а не упущенную выручку: он разбил бутылку, а не
        # украл наценку.
        "loss": _loss_of(pid, qty),
        "kind": r.get("kind", ""), "note": r.get("note", ""),
        "by": r.get("by", ""), "district": r.get("district", ""),
        "district_code": r.get("district_code", ""), "thumb": r.get("thumb", ""),
        # Списания старше согласования поля не имеют вовсе — они были учтены
        # сразу, и показывать их вечно ждущими решения нельзя.
        "state": r.get("state") or "ok",
        "decided_at": _iso_of(r.get("decided_at")) if r.get("decided_at") else "",
        "decided_by_name": r.get("decided_by_name", ""),
        "decided_note": r.get("decided_note", ""),
        # Удержание: с кого и сколько. Пусто — списали за счёт компании.
        "comp": ({"who": (r.get("comp") or {}).get("who", ""),
                  "amount": int((r.get("comp") or {}).get("amount") or 0),
                  "note": (r.get("comp") or {}).get("note", ""),
                  "by_name": (r.get("comp") or {}).get("by_name", "")}
                 if (r.get("comp") or {}).get("amount") else None),
    }


# ── списание руками владельца ────────────────────────────────────────────────
# Водитель записывает бой у себя, но не всё бьётся при водителе: коробку роняют
# на приёмке, бутылку находят разбитой на полке утром, просрочку замечают при
# пересчёте. Раньше такое было некуда записать, и оно уходило в недостачу —
# то есть выглядело воровством.
#
# Фотография обязательна ровно там, где она что-то доказывает: разбитую бутылку
# видно, брак видно, просрочку видно. Утеря — это как раз отсутствие предмета,
# и требовать снимок «того, чего нет» значит требовать пустой кадр.
#
# Своё списание владелец не согласовывает сам с собой: он и есть тот, чьё
# решение требуется, поэтому запись сразу учтённая.
# Пределы общие с чеками: см. photos. Прежние три мегабайта были недостижимы —
# тело запроса резалось раньше, и проверка ниже никогда не срабатывала.
WO_MAX_PHOTO = photos.MAX_PHOTO
WO_MAX_THUMB = photos.MAX_THUMB


@require_owner
async def handle_writeoff_add(request):
    """POST /api/owner/stock/writeoff — списать самому.

    body: {item, qty, kind, district, note?, day?, photo?, thumb?, as?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    cat = _catalog()
    pid = str(body.get("item") or "").strip()
    if pid not in cat:
        return web.json_response({"error": "no_item"}, status=400, headers=CORS_HEADERS)
    try:
        qty = int(body.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if not (1 <= qty <= 240):
        return web.json_response({"error": "bad_qty"}, status=400, headers=CORS_HEADERS)
    kind = str(body.get("kind") or "").strip()
    if kind not in db.WRITEOFF_KINDS:
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)
    district = str(body.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return web.json_response({"error": "unknown_district"}, status=400, headers=CORS_HEADERS)

    raw = str(body.get("photo") or "")
    if "," in raw[:64]:
        raw = raw.split(",", 1)[1]
    if len(raw) > WO_MAX_PHOTO:
        return web.json_response({"error": "photo_big"}, status=400, headers=CORS_HEADERS)
    photo = b""
    if raw:
        try:
            import base64
            photo = base64.b64decode(raw, validate=True)
        except Exception:
            photo = b""
        # Проверяем начало файла, а не длину строки: битая картинка ничего не
        # доказывает, а в истории выглядит так же, как настоящая.
        if len(photo) < 2000 or photo[:2] not in (b"\xff\xd8", b"\x89P"):
            return web.json_response({"error": "bad_photo"}, status=400, headers=CORS_HEADERS)
    if not photo and kind != "потеря":
        return web.json_response({"error": "no_photo"}, status=400, headers=CORS_HEADERS)

    thumb = str(body.get("thumb") or "")
    if not thumb.startswith("data:image/") or len(thumb) > WO_MAX_THUMB:
        thumb = ""

    who = str(body.get("as") or "").strip()[:60] or "владелец"
    day = str(body.get("day") or "").strip() or _biz_day()
    now = datetime.now(timezone.utc)
    # Ключ от двойной отправки: сеть моргнула, кнопку нажали ещё раз — запись
    # одна. Ключ придумывает окно списания при открытии и шлёт с каждой
    # попыткой; вторая попытка получает ту же запись, а не вторую.
    cid = str(body.get("cid") or "").strip()[:48]
    if cid:
        dup = await db.writeoff_by_cid(cid)
        if dup:
            return web.json_response({"ok": True, "id": dup.get("_id"), "dup": True,
                                      "comp": _comp_view(dup)}, headers=CORS_HEADERS)
    wid = await db.writeoff_add({
        "at": now, "day": day, "item": pid, "thumb": thumb, "cid": cid,
        "name": cat[pid].get("name", ""), "qty": qty, "kind": kind,
        "note": str(body.get("note") or "").strip()[:200],
        "district": district, "district_code": OFFICE_CODES.get(district, ""),
        "by": who, "by_id": int(request["owner_id"] or 0),
        "own": True,                       # записал владелец, а не водитель
        "state": "ok",                     # своё решение принимать не у кого
        "decided_at": now, "decided_by": int(request["owner_id"] or 0),
        "decided_by_name": who,
    }, photo)
    # Отметка «ничего не списывали» с этим днём больше не совместима.
    try: await db.writeoff_none_clear(day)
    except Exception: pass
    base_drop()                            # заявка должна узнать сразу
    log.info(f"[writeoff] владелец списал: {kind} · {cat[pid].get('name','')} × {qty} "
             f"· {district} · {day}")
    await backdate.notify(day, who, "списание", 
                          f"{kind} · {cat[pid].get('name','')} × {qty} · "
                          f"{OFFICE_CODES.get(district, district)}")
    # Виновный, если владелец его назвал. Списание, записанное им самим, уже
    # согласовано — значит и удержание ставится сразу, одним действием, а не
    # вторым заходом в историю.
    comp = await _blame_set(wid, body, request, who, note=str(body.get("note") or ""))
    return web.json_response({"ok": True, "id": wid, "comp": comp},
                             headers=CORS_HEADERS)


async def _blame_set(wid: str, body: dict, request, by_name: str, note: str = ""):
    """Поставить удержание на только что записанное списание.

    Виновного называет человек: программа знает, чья бутылка и кто списывал, но
    не знает, кто её уронил. Сумму она посчитать может — по закупке за бутылку —
    и считает, если её не прислали. Присланная своя сильнее: бывает разбитая
    коробка, где половину списывают на компанию.

    Ноль или пустой виновный — удержания нет вовсе. Это не ошибка: списать за
    счёт компании такое же решение, как удержать."""
    кто = str(body.get("who") or "").strip()[:60]
    if not кто:
        return None
    doc = await db.writeoff_get(wid)
    if not doc:
        return None
    сумма = body.get("comp")
    await _loss_load()
    сколько = (int(сумма) if str(сумма or "").strip().lstrip("-").isdigit()
               else _loss_of(doc.get("item") or "", int(doc.get("qty") or 0)))
    split = _split_parse(body)
    if split:
        кто = ", ".join(x["who"] for x in split)
        сколько = sum(x["amount"] for x in split)
    if сколько <= 0:
        return None
    обновл = await db.writeoff_compensate(wid, кто, сколько, note[:200],
                                        request.get("owner_id") or 0, by_name, split)
    if not обновл:
        return None
    log.info(f"[writeoff] {wid}: удержано {сколько} с {кто} ({by_name})")
    try:
        await _writeoff_comp_tell(обновл)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[writeoff] про удержание не сообщили: {e}")
    return _comp_view(обновл)


@require_owner
async def handle_writeoff_scan(request):
    """Списать бутылку по коду с крышки. body: {code, kind, photo, note?, day?, as?}

    Позицию, район и количество спрашивать не у кого: код знает, что это за
    бутылка и где она числится, а одна крышка — это одна бутылка. Человеку
    остаётся сказать, что с ней случилось, и показать это.

    Утеря сюда не ходит: чтобы отсканировать бутылку, надо держать её в руках,
    а потерянную не держат. Для неё есть запись руками."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = str(body.get("code") or "").strip()
    kind = str(body.get("kind") or "").strip()
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    if kind not in db.WRITEOFF_KINDS or kind == "потеря":
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)

    doc = await db.qr_get(code)
    if not doc:
        return _wo_say("unknown", code=code)
    st = (doc.get("status") or "active").strip()
    if st == "written":
        return _wo_say("already", code=code, name=doc.get("product_name") or "")
    # Всё, что не в остатке, списать нельзя: убранной из реестра бутылки для
    # склада не существует, и вычитать её значит потерять одну настоящую.
    if st != "active":
        return _wo_say(st if st in WO_SAY else "gone",
                       code=code, name=doc.get("product_name") or "")
    pid = str(doc.get("product_id") or "")
    p = _catalog().get(pid)
    if not p:
        return _wo_say("no_item", code=code, name=doc.get("product_name") or "")
    district = (doc.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return _wo_say("nohome", code=code, name=p.get("name", ""))

    raw = str(body.get("photo") or "")
    if "," in raw[:64]:
        raw = raw.split(",", 1)[1]
    if len(raw) > WO_MAX_PHOTO:
        return web.json_response({"error": "photo_big"}, status=400, headers=CORS_HEADERS)
    try:
        import base64
        photo = base64.b64decode(raw, validate=True) if raw else b""
    except Exception:
        photo = b""
    if len(photo) < 2000 or photo[:2] not in (b"\xff\xd8", b"\x89P"):
        return web.json_response({"error": "no_photo"}, status=400, headers=CORS_HEADERS)
    thumb = str(body.get("thumb") or "")
    if not thumb.startswith("data:image/") or len(thumb) > WO_MAX_THUMB:
        thumb = ""

    who = str(body.get("as") or "").strip()[:60] or "владелец"
    day = str(body.get("day") or "").strip() or _biz_day()
    now = datetime.now(timezone.utc)
    wid = await db.writeoff_add({
        "at": now, "day": day, "item": pid, "thumb": thumb,
        "name": p.get("name", ""), "qty": 1, "kind": kind,
        "note": str(body.get("note") or "").strip()[:200],
        "district": district, "district_code": OFFICE_CODES.get(district, ""),
        "by": who, "by_id": int(request["owner_id"] or 0),
        "own": True, "code": code, "label": doc.get("label") or "",
        "state": "ok", "decided_at": now,
        "decided_by": int(request["owner_id"] or 0), "decided_by_name": who,
    }, photo)
    if not await db.qr_write_off(code, wid):
        # Между чтением и записью бутылку успели списать или продать — запись
        # оставлять нельзя, иначе она вычтет со склада вторую такую же.
        await db.writeoff_del(wid)
        return _wo_say("already", code=code, name=p.get("name", ""))
    try: await db.writeoff_none_clear(day)
    except Exception: pass
    base_drop()
    log.info(f"[writeoff] сканом: {kind} · {p.get('name','')} · {district} · {day}")
    await backdate.notify(day, who, "списание сканом",
                          f"{kind} · {p.get('name','')} · {OFFICE_CODES.get(district, district)}")
    return _wo_say("ok", code=code, id=wid, name=p.get("name", ""),
                   label=doc.get("label") or "",
                   district=district, district_code=OFFICE_CODES.get(district, ""))


WO_SAY = {
    "unknown": "нет в реестре",
    "already": "уже списана",
    "sold":    "ушла с заказом",
    "no_item": "нет в каталоге",
    "nohome":  "офис не указан",
    "deleted": "убрана из реестра",
    "gone":    "её нет в остатке",
}


def _wo_say(verdict: str, **extra):
    return web.json_response({"ok": verdict == "ok", "verdict": verdict,
                              "say": WO_SAY.get(verdict, ""), **extra},
                             headers=CORS_HEADERS)


@require_owner
async def handle_writeoff_none(request):
    """POST /api/owner/stock/writeoff/none — «за этот день списаний не было».

    body: {day?, on: bool, as?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    day = str(body.get("day") or "").strip() or _biz_day()
    who = str(body.get("as") or "").strip()[:60] or "владелец"
    if body.get("on"):
        # Сказать «ничего не было» поверх записанного нельзя: это не отметка, а
        # спор с фактом. Сначала разберитесь со строками, потом отмечайте.
        rows = await db.writeoff_list(day=day, limit=1)
        if rows:
            return web.json_response({"error": "has_rows"}, status=409, headers=CORS_HEADERS)
        await db.writeoff_none_set(day, request["owner_id"], who)
        log.info(f"[writeoff] {day}: отмечено «списаний не было» · {who}")
        await backdate.notify(day, who, "отметка «списаний не было»")
    else:
        await db.writeoff_none_clear(day)
        log.info(f"[writeoff] {day}: отметка «списаний не было» снята · {who}")
    return web.json_response({"ok": True, "none": await db.writeoff_none_get(day)},
                             headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


@require_owner
async def handle_writeoffs(request):
    """История боя и брака. Фотографии — отдельными запросами: тридцать
    снимков в одном ответе это тридцать мегабайт, и открывался бы раздел
    полминуты ради списка из восьми строк.

    Ждущие решения идут отдельным списком и без окна в тридцать дней: пока
    списание не согласовано, товар числится на полке, и забытая заявка
    недельной давности — это расхождение, которое некому объяснить."""
    try:
        days = max(1, min(180, int(request.query.get("days", "30") or 30)))
    except ValueError:
        days = 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = (request.query.get("day") or "").strip() or _biz_day()
    rows = await db.writeoff_list(since=since, limit=400)
    cat = _catalog()
    await _loss_load()
    pend = [_wo_row(r, cat) for r in await db.writeoff_pending(limit=200)]
    out, by_kind, by_driver, by_person = [], {}, {}, {}
    for r in rows:
        if (r.get("state") or "ok") == "pending":
            continue        # висит в очереди сверху, второй раз не показываем
        v = _wo_row(r, cat)
        out.append(v)
        # Отклонённое в деньги не идёт: это не убыток, а недостача, и складывать
        # их в одну сумму значит потерять разницу между «разбили» и «пропало».
        if v["state"] == "no":
            continue
        qty, aed = v["qty"], v["aed"]
        k = by_kind.setdefault(v["kind"] or "—", {"kind": v["kind"] or "—",
                                                  "qty": 0, "aed": 0.0})
        k["qty"] += qty; k["aed"] = round(k["aed"] + aed, 2)
        d = by_driver.setdefault(v["by"] or "—", {"driver": v["by"] or "—",
                                                  "qty": 0, "aed": 0.0, "n": 0})
        d["qty"] += qty; d["aed"] = round(d["aed"] + aed, 2); d["n"] += 1
        # Удержания считаем по тому, С КОГО удержали, а не по тому, кто списал:
        # разбить может один, а отвечать за это — другой.
        c = v.get("comp")
        if c:
            h = by_person.setdefault(c["who"], {"who": c["who"], "amount": 0, "n": 0})
            h["amount"] += int(c["amount"] or 0); h["n"] += 1
    ok_rows = [x for x in out if x["state"] != "no"]
    return web.json_response({
        "days": days, "rows": out, "pending": pend,
        "pending_qty": sum(x["qty"] for x in pend),
        "pending_aed": round(sum(x["aed"] for x in pend), 2),
        "total_qty": sum(x["qty"] for x in ok_rows),
        "total_aed": round(sum(x["aed"] for x in ok_rows), 2),
        "by_kind": sorted(by_kind.values(), key=lambda x: -x["qty"]),
        "by_driver": sorted(by_driver.values(), key=lambda x: -x["qty"]),
        "by_person": sorted(by_person.values(), key=lambda x: -x["amount"]),
        "comp_total": sum(v["amount"] for v in by_person.values()),
        # Отметка «за этот день ничего не списывали» — про конкретный день, а не
        # про тридцать: пустой список за месяц ничего не утверждает.
        "day": day,
        "none": await db.writeoff_none_get(day),
    }, headers=CORS_HEADERS,
       dumps=lambda o: __import__("json").dumps(o, default=str))


@require_owner
async def handle_writeoff_decide(request):
    """Согласовать списание или отклонить. body: {ok: bool, note?, as?}

    До решения бутылки со склада не вычтены: списание — это заявление
    водителя, а фотография доказывает, что бутылка разбита, но не то, что она
    была наша и стояла на полке. Согласование и есть та черта, после которой
    заявление становится убытком компании.

    Отклонение ничего не удаляет. Запись остаётся, но в остаток не идёт —
    значит эти бутылки вылезут недостачей в ближайшем пересчёте, у того, у
    кого они пропали. Это и есть весь смысл: отказ не спор о фотографии, а
    возврат вопроса на полку."""
    wid = (request.match_info.get("wid") or "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = bool(body.get("ok"))
    note = str(body.get("note") or "").strip()[:200]
    who = str(body.get("as") or "").strip()[:60]
    # Вина. Три разных ответа, и путать их нельзя:
    #   blame отсутствует — вопрос не задавали, удержания нет;
    #   blame=false       — не виноват, платит компания;
    #   blame=true        — виноват, удерживаем.
    # Сумму при вине можно прислать свою; не прислали — считаем по закупке.
    # Считает сервер, а не страница: цена лежит здесь, и второй счёт на
    # телефоне разошёлся бы с этим молча.
    blame = body.get("blame")
    сумма = body.get("comp")
    doc = await db.writeoff_decide(wid, ok, request.get("owner_id") or 0, who, note)
    if not doc:
        cur = await db.writeoff_get(wid)
        if not cur:
            return web.json_response({"error": "not_found"}, status=404,
                                     headers=CORS_HEADERS)
        # Решение уже принято — вторым нажатием его не переписывают. Это не
        # ошибка вызывающего: два владельца видят одну очередь.
        return web.json_response({"error": "already_decided",
                                  "state": cur.get("state") or "ok",
                                  "by": cur.get("decided_by_name", "")},
                                 status=409, headers=CORS_HEADERS)
    if ok:
        base_drop()          # остаток изменился — заявку считать заново
    log.info(f"[writeoff] {wid}: {'согласовано' if ok else 'отклонено'} "
             f"({who or request.get('owner_id')})")

    # Удержание ставим только на согласованном: отклонённое списание не убыток
    # компании, а недостача, и она вылезет пересчётом у того, у кого пропала.
    if ok and blame is not None and bool(blame):
        await _loss_load()
        виновный = (str(body.get("who") or "").strip()[:60]
                    or str(doc.get("by") or "").strip()[:60])
        сколько = (int(сумма) if str(сумма or "").strip().lstrip("-").isdigit()
                   else _loss_of(doc.get("item") or "", int(doc.get("qty") or 0)))
        split = _split_parse(body)
        if split:
            виновный = ", ".join(x["who"] for x in split)
            сколько = sum(x["amount"] for x in split)
        if виновный and сколько > 0:
            обновл = await db.writeoff_compensate(
                wid, виновный, сколько, note, request.get("owner_id") or 0, who, split)
            if обновл:
                doc = обновл
                log.info(f"[writeoff] {wid}: удержано {сколько} с {виновный}")
                try:
                    await _writeoff_comp_tell(doc)
                except Exception as e:                       # noqa: BLE001
                    log.warning(f"[writeoff] про удержание не сообщили: {e}")

    await _writeoff_after(doc, ok, who)
    return web.json_response({"ok": True, "id": wid, "state": doc.get("state"),
                              "comp": _comp_view(doc)},
                             headers=CORS_HEADERS)


@require_owner
async def handle_writeoff_compensate(request):
    """Удержать сумму списания с виновного — или снять удержание.

    body: {who: str, amount: int, note?: str, as?: str}. Пустой who или нулевая
    сумма снимают удержание.

    Отдельным действием, а не частью согласования: решение «списываем» и
    решение «кто платит» принимают в разное время и иногда разные люди.
    Владелец жмёт «согласовать» в боте под фотографией, ещё не зная, чья это
    смена; виноватого выясняют позже. Свяжи их в один шаг — и согласование
    встанет до выяснения, а бутылки всё это время будут числиться на полке.

    Со склада удержание не меняет ничего: бутылка разбита в любом случае. Оно
    меняет только то, кто за неё заплатит."""
    wid = (request.match_info.get("wid") or "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    who = str(body.get("who") or "").strip()[:60]
    note = str(body.get("note") or "").strip()[:200]
    by_name = str(body.get("as") or "").strip()[:60]
    try:
        amount = max(0, int(round(float(body.get("amount") or 0))))
    except (TypeError, ValueError):
        amount = 0
    # Несколько виноватых: имена и сумма — из раскладки, а не из полей.
    split = _split_parse(body)
    if split:
        who = ", ".join(x["who"] for x in split)
        amount = sum(x["amount"] for x in split)
    doc = await db.writeoff_compensate(wid, who, amount, note,
                                       request.get("owner_id") or 0, by_name, split)
    if not doc:
        cur = await db.writeoff_get(wid)
        if not cur:
            return web.json_response({"error": "not_found"}, status=404,
                                     headers=CORS_HEADERS)
        # Удерживать по несогласованному нечего — и это не ошибка вызывающего,
        # а состояние, которое он мог не видеть: решение мог принять второй
        # владелец секунду назад.
        return web.json_response({"error": "not_approved",
                                  "state": cur.get("state") or "ok"},
                                 status=409, headers=CORS_HEADERS)
    log.info(f"[writeoff] {wid}: удержание "
             f"{amount} с {who or '—'} ({by_name or request.get('owner_id')})")
    try:
        await _writeoff_comp_tell(doc)
    except Exception as e:
        log.warning(f"[writeoff] про удержание не сообщили: {e}")
    return web.json_response({"ok": True, "id": wid, "comp": _comp_view(doc)},
                             headers=CORS_HEADERS)


def _split_parse(body: dict) -> list | None:
    """Раскладка удержания по нескольким людям из тела запроса:
    split: [{who, amount}, …]. Пустые имена и нули выбрасываем; меньше двух
    человек — раскладки нет, это обычное удержание."""
    raw = body.get("split")
    if not isinstance(raw, list):
        return None
    out, seen = [], set()
    for x in raw:
        if not isinstance(x, dict):
            continue
        who = str(x.get("who") or "").strip()[:60]
        try:
            amount = max(0, int(round(float(x.get("amount") or 0))))
        except (TypeError, ValueError):
            amount = 0
        if not who or not amount or who in seen:
            continue
        seen.add(who)
        out.append({"who": who, "amount": amount})
    return out if len(out) > 1 else None


def _comp_view(doc: dict):
    """Удержание для ответа страницей. Дату отдаём строкой: в самом документе
    она объектом, и json на ней падает — падал уже после записи, так что
    страница видела «не удалось» на удавшемся действии."""
    c = (doc or {}).get("comp") or {}
    if not c.get("amount"):
        return None
    return {"who": c.get("who", ""), "amount": int(c.get("amount") or 0),
            "note": c.get("note", ""), "by_name": c.get("by_name", ""),
            "split": c.get("split") or None,
            "at": _iso_of(c.get("at")) if c.get("at") else ""}


async def _writeoff_comp_tell(doc: dict):
    """Сказать водителю, что с него удержали — или что удержание сняли.

    Молча вычесть из зарплаты значит дать человеку узнать о решении в день
    выплаты и поспорить тогда, когда доказывать уже нечем. Сообщение приходит
    в тот же день и тем же путём, что и решение по списанию."""
    import os as _os
    import config_staff as _staff
    from api_server import tg_send
    comp = doc.get("comp") or {}
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not token:
        return
    name = doc.get("name") or doc.get("item") or "товар"
    qty = int(doc.get("qty") or 0)
    kind = doc.get("kind") or "списание"
    # Виноватых несколько — каждому его доля, а не общая сумма на всех.
    parts = comp.get("split") or ([{"who": comp.get("who"), "amount": comp.get("amount")}]
                                  if comp.get("amount") else [])
    if not parts:
        # Снятое удержание адресуем тому, с кого его снимали, — имени в
        # документе больше нет, поэтому берём водителя, который списывал.
        tid = _staff.DRIVER_IDS.get((doc.get("by") or "").strip())
        if tid:
            await tg_send(token, tid, f"Удержание снято\n{name} × {qty} · {kind}", parse_mode=None)
        return
    for part in parts:
        tid = _staff.DRIVER_IDS.get((part.get("who") or "").strip())
        if not tid:
            continue
        text = (f"С вас удержано {int(part.get('amount') or 0)} AED\n"
                f"{name} × {qty} · {kind}"
                + (f"\n{comp.get('note')}" if comp.get("note") else ""))
        await tg_send(token, tid, text, parse_mode=None)


async def _writeoff_after(doc: dict, ok: bool, by_name: str = ""):
    """Что происходит после решения, кроме самой записи.

    Первое — снять кнопки в чатах владельцев. Кнопка, которая больше ничего не
    делает, хуже отсутствующей: по ней жмут и получают отказ, не понимая, что
    вопрос давно закрыт.

    Второе — сказать водителю. Он ждёт ответа: от него зависит, зачтён ему бой
    или эти бутылки спросят с него в пересчёте."""
    import writeoff_msg as wm
    try:
        from owner_routes import tg_edit_caption, OWNER_BOT_TOKEN
        cat = _catalog()
        p = cat.get(doc.get("item") or "") or {}
        base = wm.caption(doc.get("name", "") or p.get("name", ""),
                          int(doc.get("qty") or 0), doc.get("kind", ""),
                          doc.get("by", ""), doc.get("district_code", ""),
                          doc.get("note", ""),
                          round(_price(p) / max(1, _unit(p)) * int(doc.get("qty") or 0)))
        cap = wm.decided_caption(base, ok, by_name)
        for m in (doc.get("msgs") or []):
            await tg_edit_caption(OWNER_BOT_TOKEN, m.get("chat_id"),
                                  m.get("message_id"), cap)
    except Exception as e:
        log.warning(f"[writeoff] кнопки не сняты: {e}")
    try:
        import os as _os
        import config_staff as _staff
        from api_server import tg_send
        tid = _staff.DRIVER_IDS.get((doc.get("by") or "").strip())
        token = _os.getenv("DRIVER_BOT_TOKEN", "")
        if tid and token:
            await tg_send(token, tid,
                          wm.driver_text(doc.get("name", ""), int(doc.get("qty") or 0),
                                         ok, doc.get("decided_note", "")),
                          parse_mode=None)
    except Exception as e:
        log.warning(f"[writeoff] водителю не ушло: {e}")


@require_owner
async def handle_writeoff_photo(request):
    """Сам снимок. Ради него всё и затевалось: строка в списке доказывает
    только то, что кто-то её написал."""
    img = await db.writeoff_photo((request.match_info.get("wid") or "").strip())
    if not img:
        return web.json_response({"error": "no_photo"}, status=404, headers=CORS_HEADERS)
    return web.Response(body=img, content_type="image/jpeg",
                        headers={**CORS_HEADERS, "Cache-Control": "private, max-age=86400"})


def _iso_of(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


def setup(app):
    r = app.router
    routes = (
        ("/api/owner/stock/sheet",     handle_sheet,     "GET"),
        ("/api/owner/stock/status",    handle_status,    "GET"),
        ("/api/owner/stock/result",    handle_result,    "GET"),
        ("/api/owner/stock/order",     handle_order,     "GET"),
        ("/api/owner/stock/order/edit",  handle_order_edit,  "POST"),
        ("/api/owner/stock/order/reset", handle_order_reset, "POST"),
        ("/api/owner/stock/transfers", handle_transfers, "GET"),
        ("/api/owner/stock/audits",    handle_audits,    "GET"),
        ("/api/owner/stock/audit/scan",       handle_audit_scan_state, "GET"),
        ("/api/owner/stock/audit/scan",       handle_audit_scan,       "POST"),
        ("/api/owner/stock/audit/scan/undo",  handle_audit_scan_undo,  "POST"),
        ("/api/owner/stock/audit/scan/reset", handle_audit_scan_reset, "POST"),
        ("/api/owner/stock/audit/sheet",  handle_audit_sheet,  "GET"),
        ("/api/owner/stock/audit/start",  handle_audit_start,  "POST"),
        ("/api/owner/stock/audit/finish", handle_audit_finish, "POST"),
        ("/api/owner/stock/audit/short",  handle_audit_short,  "POST"),
        ("/api/owner/stock/audit/over",   handle_audit_over,   "POST"),
        ("/api/owner/stock/audit/report", handle_audit_report, "GET"),
        ("/api/owner/stock/audit/reopen", handle_audit_reopen, "POST"),
        ("/api/owner/stock/audit/short/undo", handle_audit_short_undo, "POST"),
        ("/api/owner/stock/audit/over/undo",  handle_audit_over_undo,  "POST"),
        ("/api/owner/stock/count",     handle_save,      "POST"),
        ("/api/owner/stock/transfer",  handle_transfer,  "POST"),
        ("/api/owner/stock/transfer/scan",      handle_transfer_scan,      "POST"),
        ("/api/owner/stock/transfer/scan/undo", handle_transfer_scan_undo, "POST"),
        ("/api/owner/stock/norm",      handle_set_norm,  "POST"),
        ("/api/owner/stock/norms",      handle_norms,     "GET"),
        ("/api/owner/stock/norm/reset", handle_norm_reset, "POST"),
        ("/api/owner/stock/writeoffs",  handle_writeoffs, "GET"),
        ("/api/owner/stock/writeoff",       handle_writeoff_add,  "POST"),
        ("/api/owner/stock/writeoff/none",  handle_writeoff_none, "POST"),
        ("/api/owner/stock/writeoff/scan",  handle_writeoff_scan, "POST"),
        ("/api/owner/stock/shifts",     handle_shift_log, "GET"),
        ("/api/owner/stock/writeoff/{wid}/photo", handle_writeoff_photo, "GET"),
        ("/api/owner/stock/writeoff/{wid}/compensate", handle_writeoff_compensate, "POST"),
        ("/api/owner/stock/writeoff/{wid}/decide", handle_writeoff_decide, "POST"),
        ("/api/owner/stock/transfer/{tid}", handle_transfer_delete, "DELETE"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, handler)
    log.info("[stock] routes mounted")
