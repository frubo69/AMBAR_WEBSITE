"""Курс валют к дирхаму — коммерческий, тот, по которому реально меняют.

Зачем не центробанк
-------------------
Официальный курс ЦБ ОАЭ считается для налогов, а не для обмена: доллар у него
вечные 3.6725, потому что дирхам к нему привязан. Человек с наличными в руках
получит другое число — у обменника своя цена. Зарплаты здесь равняются на
доллар, а водители привозят наличные, поэтому смысл имеет только курс, по
которому эти наличные примут.

Откуда берём
------------
Публичного API у обменников нет: собственные сайты Al Ansari и Al Fardan
рисуют курсы уже в браузере, а страницы банков отдают серверу 403. Единственный
путь, который отвечает обычным запросом, — витрина, где те же курсы Al Ansari
выложены готовыми, с отметкой времени по каждой валюте.

Что важно в разборе
-------------------
У валюты бывает ДВЕ строки: перевод и наличные. Разница не косметическая — у
доллара это 3.79 против 3.68, почти четыре процента. Нам нужны наличные, и
берётся всегда вторая строка; первая остаётся запасной на случай, когда второй
нет вовсе.

Свежесть источник сообщает сам, по каждой валюте отдельно — и это оказалось
решающим. На проверке 2 сентября 2026 из 69 валют витрины свежими были единицы:
доллар и фунт обновлены в тот же день, а евро, рубль, лира, юань, тенге и
сомони висели с 25 августа и были помечены самим источником как устаревшие.
Хуже того, у 51 валюты курса наличных нет вовсе — только перевод, а это другая
цена: у доллара 3.68 наличными против 3.79 переводом.

Проверка на живых числах: рубль на витрине шёл 0.0347 дирхама, при рыночных
0.0423 — на восемнадцать процентов мимо. Показать такое как «курс, по которому
поменяют» значит ошибиться в зарплате на пятую часть.

Поэтому источников два, и каждое число подписано, откуда оно:
  • рыночный курс — по всем валютам, обновляется ежесуточно и сходится с
    привязкой дирхама к доллару (3.6725) до знака;
  • курс наличных обменника — только там, где витрина отдаёт его свежим.
    Сегодня это доллар, а он здесь и главный: зарплаты равняются на него.

Устаревшее не показывается молча никогда: либо стоит отметка времени, либо
строки нет вовсе.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time as _t
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

from owner_auth import require_owner

log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}

SRC_URL = ("https://masarif.ae/currency-exchanges/al-ansari-exchange"
           "/currency-exchange-rates")
SRC_NAME = "Al Ansari Exchange"
# Рыночный курс: 166 валют, без ключа, одно обновление в сутки, и сам сообщает,
# когда будет следующее.
MKT_URL = "https://open.er-api.com/v6/latest/AED"
MKT_NAME = "рыночный курс"
# Витрина обменника считается пригодной, только если она сама назвала курс
# свежим И у валюты есть цена наличных. Всё остальное — не курс обмена.
CASH_MAX_AGE_H = 36
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

DUBAI_TZ = timezone(timedelta(hours=4))
TTL_SEC = 6 * 3600          # чаще раза в шесть часов ходить незачем: источник
                            # сам обновляется раз в сутки
RETRY_SEC = 15 * 60         # столько ждём после неудачи, прежде чем пробовать

# Что показываем первым. Остальные шесть десятков доступны в раскрытом списке.
MAIN = ["USD", "EUR", "GBP", "RUB", "TRY", "CNY"]

# Имена по-русски для тех, кого читают чаще всего. Для остальных остаётся то,
# как валюту называет источник.
RU = {
    "USD": "Доллар США", "EUR": "Евро", "GBP": "Фунт стерлингов",
    "RUB": "Рубль", "TRY": "Турецкая лира", "CNY": "Юань",
    "INR": "Индийская рупия", "PKR": "Пакистанская рупия",
    "KZT": "Тенге", "KGS": "Сом", "TJS": "Сомони", "UAH": "Гривна",
    "AMD": "Драм", "GEL": "Лари", "BYN": "Белорусский рубль",
    "AZN": "Манат", "THB": "Бат", "CHF": "Швейцарский франк",
    "JPY": "Иена", "SAR": "Саудовский риял", "QAR": "Катарский риял",
    "KWD": "Кувейтский динар", "OMR": "Оманский риал",
    "BHD": "Бахрейнский динар", "EGP": "Египетский фунт",
    "PHP": "Филиппинское песо", "IDR": "Рупия", "MYR": "Ринггит",
    "SGD": "Сингапурский доллар", "HKD": "Гонконгский доллар",
    "CAD": "Канадский доллар", "AUD": "Австралийский доллар",
    "NZD": "Новозеландский доллар", "SEK": "Шведская крона",
    "NOK": "Норвежская крона", "DKK": "Датская крона",
    "PLN": "Злотый", "CZK": "Чешская крона", "HUF": "Форинт",
    "ZAR": "Рэнд", "VND": "Донг", "LKR": "Шриланкийская рупия",
    "NPR": "Непальская рупия", "BDT": "Така", "MAD": "Дирхам Марокко",
    "JOD": "Иорданский динар", "ILS": "Шекель", "RON": "Лей",
}

_CACHE: dict = {"at": 0.0, "data": None, "fail_at": 0.0}


def _text(html: str) -> list[str]:
    """Страница в строки: разметка нам не нужна, нужен порядок значений."""
    h = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "\n", h)
    return [x.strip() for x in h.split("\n") if x.strip()]


_RATE = re.compile(r"^1\s*AED\s*=\s*([0-9][0-9.,]*)\s*([A-Z]{3})$")
_WHEN = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2})$")


def parse(html: str) -> list[dict]:
    """Разобрать витрину в список валют.

    Блок валюты выглядит так, и порядок строк в нём постоянен:

        USD
        (History)
        United States Dollar
        1 AED = 0.2637 USD      ← перевод
        1 AED = 0.2717 USD      ← наличные (может не быть)
        Sep 02, 2026 19:39
        Fresh
    """
    lines = _text(html)
    out, seen = [], set()
    for i, l in enumerate(lines):
        m = _RATE.match(l)
        if not m:
            continue
        code = m.group(2)
        if code in seen:
            continue
        # Курсы идут подряд: первый — перевод, второй — наличные.
        rates = []
        j = i
        while j < len(lines):
            mm = _RATE.match(lines[j])
            if not mm or mm.group(2) != code:
                break
            try:
                rates.append(float(mm.group(1).replace(",", "")))
            except ValueError:
                pass
            j += 1
        if not rates:
            continue
        when, fresh = "", None
        for k in range(j, min(j + 3, len(lines))):
            if _WHEN.match(lines[k]):
                when = lines[k]
            elif lines[k] in ("Fresh", "Stale"):
                fresh = lines[k] == "Fresh"
        name = lines[i - 1] if i >= 1 else code
        if name.startswith("1 AED") or name == "(History)":
            name = code
        # Наличные — вторая строка, когда она есть.
        cash = rates[1] if len(rates) > 1 else rates[0]
        if not cash:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "name": RU.get(code) or name,
            "per_aed": round(cash, 6),          # сколько валюты за 1 дирхам
            "aed": round(1 / cash, 4),          # сколько дирхамов за 1 единицу
            "transfer": round(rates[0], 6),
            "only_transfer": len(rates) == 1,   # наличных на витрине нет
            "at": when,
            "fresh": fresh,
        })
    order = {c: i for i, c in enumerate(MAIN)}
    out.sort(key=lambda r: (order.get(r["code"], 99), r["code"]))
    return out


async def _get(url: str, json_: bool = False):
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers={"User-Agent": UA}) as r:
                if r.status != 200:
                    log.warning(f"[rates] {url.split('/')[2]} ответил {r.status}")
                    return None
                return await (r.json(content_type=None) if json_ else r.text())
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[rates] не дозвонились до {url.split('/')[2]}: {e}")
        return None


async def _fetch_market() -> tuple[dict, str] | None:
    """Рыночный курс по всем валютам. Отдаёт {код: единиц за 1 дирхам}."""
    d = await _get(MKT_URL, json_=True)
    if not d or d.get("result") != "success":
        return None
    rates = {k: v for k, v in (d.get("rates") or {}).items() if v}
    if len(rates) < 50:
        return None
    return rates, str(d.get("time_last_update_utc") or "")


async def _fetch_cash() -> dict:
    """Курс наличных обменника — только по тем валютам, где витрина назвала
    его свежим. Устаревшее не берём вовсе: недельной давности курс рубля
    ошибается на пятую часть, и лучше не показать ничего."""
    html = await _get(SRC_URL)
    if not html:
        return {}
    out = {}
    for r in parse(html):
        if r.get("fresh") and not r.get("only_transfer"):
            out[r["code"]] = {"aed": r["aed"], "at": r["at"]}
    return out


async def _fetch() -> list[dict] | None:
    """Собрать список: рыночный курс по всем, наличные — где есть свежие."""
    mkt = await _fetch_market()
    if not mkt:
        return None
    rates, mkt_at = mkt
    cash = await _fetch_cash()          # молчит — не беда, рынок уже есть
    out = []
    for code, per_aed in rates.items():
        if code == "AED" or not per_aed:
            continue
        row = {
            "code": code,
            "name": RU.get(code) or code,
            "per_aed": round(per_aed, 6),
            "aed": round(1 / per_aed, 4),
        }
        c = cash.get(code)
        if c:
            row["cash_aed"] = c["aed"]
            row["cash_at"] = c["at"]
            # Насколько обменник дороже рынка — это и есть его заработок.
            row["spread"] = round((c["aed"] / row["aed"] - 1) * 100, 2)
        out.append(row)
    order = {c: i for i, c in enumerate(MAIN)}
    out.sort(key=lambda r: (order.get(r["code"], 99), r["code"]))
    _CACHE["market_at"] = mkt_at
    _CACHE["cash_n"] = len(cash)
    return out


async def get_rates(force: bool = False) -> dict:
    """Курсы с кэшем. Отдаём последнее известное, даже когда источник молчит:
    вчерашний курс с честной отметкой времени полезнее пустого экрана."""
    now = _t.time()
    свежо = _CACHE["data"] and (now - _CACHE["at"] < TTL_SEC)
    ждём = now - _CACHE["fail_at"] < RETRY_SEC
    if not force and (свежо or (ждём and _CACHE["data"])):
        return _payload(ok=True)
    rows = await _fetch()
    if rows:
        _CACHE["data"] = rows
        _CACHE["at"] = now
        _CACHE["fail_at"] = 0.0
        usd = next((r for r in rows if r["code"] == "USD"), None)
        log.info(f"[rates] обновлено · валют {len(rows)}"
                 f" · наличными {_CACHE.get('cash_n', 0)}"
                 + (f" · доллар рынок {usd['aed']}"
                    + (f", обменник {usd['cash_aed']}" if usd.get("cash_aed") else "")
                    if usd else ""))
        return _payload(ok=True)
    _CACHE["fail_at"] = now
    return _payload(ok=False)


def _payload(ok: bool) -> dict:
    at = _CACHE["at"]
    return {
        "source": SRC_NAME,
        "market": MKT_NAME,
        "rates": _CACHE["data"] or [],
        "fetched_at": int(at * 1000) if at else 0,
        "fetched_iso": (datetime.fromtimestamp(at, DUBAI_TZ).isoformat()
                        if at else ""),
        "market_at": _CACHE.get("market_at", ""),
        "cash_n": _CACHE.get("cash_n", 0),
        "ok": bool(ok and _CACHE["data"]),
        "silent": bool(_CACHE["fail_at"] and not ok),
        "main": MAIN,
    }


@require_owner
async def handle_rates(request):
    force = request.query.get("force") == "1"
    return web.json_response(await get_rates(force), headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/rates", _opt)
    app.router.add_get("/api/owner/rates", handle_rates)
    log.info("[rates] routes mounted")
