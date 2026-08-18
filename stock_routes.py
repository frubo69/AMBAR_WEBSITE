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
from datetime import datetime, timedelta, timezone

from aiohttp import web

import db
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


async def _suggested_norms(district: str, day: str) -> dict:
    """{позиция: норма} по продажам района."""
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

        norms[pid] = max(1, -(-int((expect + safety) * 100) // 100))   # ceil
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
    sold = await _sold(day, district)
    moves = await db.get_stock_transfers(day)
    existing = await db.get_stock_count(district, day)
    done = {l["id"]: l for l in (existing or {}).get("lines", [])}
    cat = _catalog()

    # Когда каждую позицию последний раз считали руками. Продажи вычитаются
    # сами, но бой и воровство продажами не считаются — позицию, которую давно
    # никто не видел, надо вернуть на проверку, даже если её не заказывали.
    last_checked = await _last_checked(district, day)

    # Перемещения: ушедшее из района вычитаем, пришедшее прибавляем.
    move_by_pid = {}
    for m in moves:
        pid, q = m.get("product_id"), _round_step(m.get("qty") or 0)
        if not pid or not q:
            continue
        if m.get("from") == district:
            move_by_pid[pid] = move_by_pid.get(pid, 0) - q
        if m.get("to") == district:
            move_by_pid[pid] = move_by_pid.get(pid, 0) + q

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
        "day": day, "first_time": first_time,
        "prev_day": (prev or {}).get("day", ""),
        "audit": {
            "started_at":  (existing or {}).get("audit_started_at", ""),
            "finished_at": (existing or {}).get("audit_finished_at", ""),
            "marked": sum(1 for r in rows if r["mark"]),
        },
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

    mv_by = {}
    for m in moves:
        pid, q = m.get("product_id"), _round_step(m.get("qty") or 0)
        if not pid or not q:
            continue
        if m.get("from") == district: mv_by[pid] = mv_by.get(pid, 0) - q
        if m.get("to")   == district: mv_by[pid] = mv_by.get(pid, 0) + q

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
    return web.json_response(
        {"ok": True, "day": day, "first_time": first_time,
         "short_qty": _num(short_qty), "short_aed": round(short_aed),
         "over_qty": _num(over_qty),
         "counted_qty": counted_qty, "total_qty": len(lines),
         "audit": bool(is_audit or marked), "finished": bool(finish),
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
    return web.json_response({"ok": True, **doc}, headers=CORS_HEADERS)


@require_owner
async def handle_transfer_delete(request):
    """Убрать перемещение, введённое по ошибке."""
    tid = (request.match_info.get("tid") or "").strip()
    ok = await db.delete_stock_transfer(tid)
    return web.json_response({"ok": ok}, status=200 if ok else 404, headers=CORS_HEADERS)


@require_owner
async def handle_transfers(request):
    day = (request.query.get("day") or "").strip() or _biz_day()
    rows = await db.get_stock_transfers(day)
    for r in rows:
        r["id"] = str(r.pop("_id", ""))
        r["from_name"] = OFFICE_NAMES.get(r.get("from"), r.get("from"))
        r["to_name"] = OFFICE_NAMES.get(r.get("to"), r.get("to"))
    return web.json_response({"day": day, "transfers": rows}, headers=CORS_HEADERS)


# ── заявка ───────────────────────────────────────────────────────────────────
_BASE = {"key": None, "at": 0.0, "data": None}
BASE_TTL = 60          # секунд


def base_drop():
    """Забыть основу заявки: пересчитали склад или приняли товар."""
    _BASE["key"] = None


async def _sold_after(since: dict) -> dict:
    """{район: {позиция: продано в учётных единицах}} после его пересчёта.

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
            row = out.setdefault(oid, {})
            row[pid] = row.get(pid, 0) + q / _unit(cat.get(pid) or {})
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
        came, gone = {}, sold.get(oid) or {}
        try:
            if since[oid]:
                came = await db.intake_since(oid, since[oid])
        except Exception as e:
            log.warning(f"[stock] приход после пересчёта не учтён ({oid}): {e}")
        for pid, n in came.items():
            # Приёмка считает бутылки, склад — учётные единицы: ящик пива это
            # одна единица и двадцать четыре кода.
            have[pid] = (have.get(pid) or 0) + n / _unit(cat.get(pid) or {})
        for pid, n in gone.items():
            have[pid] = max(0, (have.get(pid) or 0) - n)
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
                     "price": price, "need_total": item_total, "cells": cells,
                     "calc_total": sum(c["calc"] for c in cells.values()),
                     "edited": row_edited})

    rows.sort(key=lambda r: (-r["need_total"], r["name"]))
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
        "all_rows": rows,
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


# ── сводка дня ───────────────────────────────────────────────────────────────
@require_owner
async def handle_status(request):
    day = (request.query.get("day") or "").strip() or _biz_day()
    counts = await db.get_stock_counts_for_day(day)
    by_d = {c["district"]: c for c in counts}
    districts = [{
        "id": oid, "code": OFFICE_CODES.get(oid, ""), "name": OFFICE_NAMES.get(oid, oid),
        "done": oid in by_d,
        "first_time": bool((by_d.get(oid) or {}).get("first_time")),
        "short_qty": int((by_d.get(oid) or {}).get("short_qty") or 0),
        "short_aed": int((by_d.get(oid) or {}).get("short_aed") or 0),
        "counted_at": (by_d.get(oid) or {}).get("counted_at", ""),
    } for oid in OFFICE_IDS]
    return web.json_response({
        "day": day,
        "done": sum(1 for d in districts if d["done"]), "total": len(districts),
        "short_aed": sum(d["short_aed"] for d in districts),
        "short_qty": sum(d["short_qty"] for d in districts),
        "districts": districts,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_audits(request):
    """История ревизий: что и когда закрывали. Только заголовки — сами позиции
    приходят с /stock/result, когда открывают конкретную."""
    rows = await db.get_finished_audits(limit=40)
    out = []
    for c in rows:
        out.append({
            "district": c.get("district", ""),
            "district_code": OFFICE_CODES.get(c.get("district"), ""),
            "district_name": OFFICE_NAMES.get(c.get("district"), c.get("district", "")),
            "day": c.get("day", ""),
            "started_at": c.get("audit_started_at", ""),
            "finished_at": c.get("audit_finished_at", ""),
            "total": int(c.get("total_qty") or 0),
            "matched": int(c.get("matched_qty") or 0),
            "mismatched": int(c.get("mismatch_qty") or 0),
            "short_qty": c.get("short_qty") or 0,
            "short_aed": int(c.get("short_aed") or 0),
            "over_qty": c.get("over_qty") or 0,
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


# ── Списания ────────────────────────────────────────────────────────────────
@require_owner
async def handle_writeoffs(request):
    """История боя и брака. Фотографии — отдельными запросами: тридцать
    снимков в одном ответе это тридцать мегабайт, и открывался бы раздел
    полминуты ради списка из восьми строк."""
    try:
        days = max(1, min(180, int(request.query.get("days", "30") or 30)))
    except ValueError:
        days = 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await db.writeoff_list(since=since, limit=400)
    cat = _catalog()
    out, by_kind, by_driver = [], {}, {}
    for r in rows:
        qty = int(r.get("qty") or 0)
        pid = r.get("item") or ""
        # Считаем и деньги: «пять бутылок» и «пять бутылок Хеннесси» — разные
        # новости, а понять это по названию можно, только зная прайс наизусть.
        # Цена продажная: закупочной система не знает, и честнее назвать это
        # «по прайсу», чем выдать выдуманную себестоимость за факт. Делим на
        # единицу учёта: списывают бутылки, а цена у пива — за ящик.
        p = cat.get(pid) or {}
        aed = round(_price(p) / max(1, _unit(p)) * qty, 2)
        out.append({
            "id": r.get("_id"), "at": _iso_of(r.get("at")), "day": r.get("day", ""),
            "item": pid, "name": r.get("name", "") or (cat.get(pid) or {}).get("name", ""),
            "qty": qty, "aed": aed, "kind": r.get("kind", ""), "note": r.get("note", ""),
            "by": r.get("by", ""), "district": r.get("district", ""),
            "district_code": r.get("district_code", ""),
        })
        k = by_kind.setdefault(r.get("kind", "—"), {"kind": r.get("kind", "—"),
                                                    "qty": 0, "aed": 0.0})
        k["qty"] += qty; k["aed"] = round(k["aed"] + aed, 2)
        d = by_driver.setdefault(r.get("by", "—"), {"driver": r.get("by", "—"),
                                                    "qty": 0, "aed": 0.0, "n": 0})
        d["qty"] += qty; d["aed"] = round(d["aed"] + aed, 2); d["n"] += 1
    return web.json_response({
        "days": days, "rows": out,
        "total_qty": sum(x["qty"] for x in out),
        "total_aed": round(sum(x["aed"] for x in out), 2),
        "by_kind": sorted(by_kind.values(), key=lambda x: -x["qty"]),
        "by_driver": sorted(by_driver.values(), key=lambda x: -x["qty"]),
    }, headers=CORS_HEADERS)


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
        ("/api/owner/stock/count",     handle_save,      "POST"),
        ("/api/owner/stock/transfer",  handle_transfer,  "POST"),
        ("/api/owner/stock/norm",      handle_set_norm,  "POST"),
        ("/api/owner/stock/writeoffs",  handle_writeoffs, "GET"),
        ("/api/owner/stock/writeoff/{wid}/photo", handle_writeoff_photo, "GET"),
        ("/api/owner/stock/transfer/{tid}", handle_transfer_delete, "DELETE"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, handler)
    log.info("[stock] routes mounted")
