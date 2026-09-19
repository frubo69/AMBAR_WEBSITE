"""Курс валют к дирхаму — у обменника, по которому реально меняют наличные.

Зачем не центробанк
-------------------
Официальный курс ЦБ ОАЭ считается для налогов, а не для обмена: доллар у него
вечные 3.6725, потому что дирхам к нему привязан. Человек с наличными в руках
получит другое число — у обменника своя цена, и у него их две: за сколько он
купит нашу валюту и за сколько продаст свою.

Откуда берём (с 19 сен 2026)
----------------------------
С сайта самого Al Ansari Exchange — тем же запросом, которым его конвертер
«Foreign Exchange» считает обмен наличных (admin-ajax.php, action
foreign_action, trtype S — обменник покупает валюту у нас, B — продаёт нам).
Ключ запроса (nonce) и справочник валют лежат на странице конвертера; ключ
живёт около суток, поэтому страница перечитывается при каждом обновлении.

До 19 сен курс брался с витрины masarif.ae, и это было неверно (владелец:
«убедись, что курсы актуальны именно с обменников»): у доллара там стоял курс
денежного перевода (3.6805 — калькулятор «Send money» на главной Al Ansari),
а у евро, рубля и остальных — только перевод месячной давности, поэтому
показывался рыночный. Настоящий курс наличных у доллара в тот вечер — 3.655
(купит) / 3.677 (продаст), у евро 4.1485 / 4.2684, у рубля 0.0393 / 0.0450.

Что показываем
--------------
• cash_aed — сколько дирхамов дадут за единицу валюты в обменнике (он у нас
  покупает): по нему меняют наличные, привезённые с заказов;
• cash_buy_aed — сколько стоит купить единицу валюты в обменнике (он
  продаёт), у основных валют: по нему считается зарплата в долларах, если курс
  месяца не вписан руками (finance_routes._usd);
• aed — рыночный курс (open.er-api): только для сравнения в строке обменника.
В списке — только то, что обменник меняет (владелец, 19 сен 2026: «то, что
обменник не меняет, вообще оттуда убирай»). Число обменника, которое
расходится с рынком больше чем на треть, — не курс (у сомони Al Ansari
отвечает 0.0003 при рыночных 0.40: наличных сомони у него нет) — такой
валюты в списке нет.
"""
from __future__ import annotations

import asyncio
import base64
import json
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

SRC_NAME = "Al Ansari Exchange"
SRC_PAGE = "https://alansariexchange.com/service/foreign-exchange/"
SRC_AJAX = "https://alansariexchange.com/wp-admin/admin-ajax.php"
AED_ID = 91                 # дирхам в справочнике Al Ansari
# Рыночный курс: 166 валют, без ключа, одно обновление в сутки.
MKT_URL = "https://open.er-api.com/v6/latest/AED"
MKT_NAME = "рыночный курс"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

DUBAI_TZ = timezone(timedelta(hours=4))
TTL_SEC = 3600              # обменник меняет цену в течение дня — раз в час
RETRY_SEC = 15 * 60         # столько ждём после неудачи, прежде чем пробовать
MAX_OFF = 0.33              # дальше от рынка — не курс, а ошибка источника

# Что показываем первым. Остальные доступны в раскрытом списке.
# Сомони — седьмой по слову владельца: таджикскими платят.
MAIN = ["USD", "EUR", "GBP", "RUB", "TRY", "CNY", "TJS"]
# Что спрашиваем у обменника: основные и те, которыми платят клиенты и
# которые возят люди. Весь справочник (сотня валют) — это сотня запросов к
# чужому сайту каждый час, а смотрят из него эти.
ASK = MAIN + ["SAR", "KZT", "KGS", "UAH", "AMD", "GEL", "INR", "PKR", "CHF", "CAD",
              "AUD", "JPY", "QAR", "KWD", "OMR", "BHD", "EGP", "THB"]

# Имена по-русски для тех, кого читают чаще всего. Для остальных остаётся то,
# как валюту называет источник.
RU = {
    "USD": "Доллар США", "EUR": "Евро", "GBP": "Фунт стерлингов",
    "RUB": "Рубль", "TRY": "Турецкая лира", "CNY": "Юань",
    "INR": "Индийская рупия", "PKR": "Пакистанская рупия",
    "KZT": "Тенге", "KGS": "Сом", "TJS": "Таджикский сомони", "UAH": "Гривна",
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


# ── обменник ────────────────────────────────────────────────────────────────
def page_nonce(html: str) -> str:
    """Ключ запроса из страницы конвертера. Лежит в CC_Ajax_Object — то прямо в
    тексте, то в сжатой вставке NitroCDN (base64), то экранированным JSON."""
    m = re.search(r"CC_Ajax_Object\s*=\s*(\{.*?\})", html)
    if m:
        try:
            v = json.loads(m.group(1)).get("ajax_nonce")
            if v:
                return str(v)
        except ValueError:
            pass
    for b in re.findall(r'registerInlineScript\("[^"]+",\s*"([A-Za-z0-9+/=]+)"', html):
        try:
            t = base64.b64decode(b).decode("utf-8", "ignore")
        except Exception:                          # noqa: BLE001
            continue
        m = re.search(r"ajax_nonce[\"']?\s*:\s*[\"']([0-9a-f]+)", t)
        if m:
            return m.group(1)
    m = re.search(r'ajax_nonce\\?"\s*:\s*\\?"([0-9a-f]+)', html)
    return m.group(1) if m else ""


def page_codes(html: str) -> dict:
    """{валюта: (currfrom, cntcode)} из списка конвертера. У евро и доллара
    строк много (по странам) — берём ту, где страна и валюта совпадают, иначе
    первую."""
    out: dict = {}
    for code, cnt, to in re.findall(
            r'data-ccyname="([A-Z]{3})"\s+data-cntcode="(\d+)"\s+data-toccy="(\d+)"', html):
        if code not in out or cnt == to:
            out[code] = (int(to), int(cnt))
    return out


async def _ajax(s: aiohttp.ClientSession, nonce: str, frm: int, cnt: int, tr: str) -> float | None:
    data = {"action": "foreign_action", "currfrom": frm, "currto": AED_ID, "cntcode": cnt,
            "amt": 1, "security": nonce, "trtype": tr}
    try:
        async with s.post(SRC_AJAX, data=data,
                          headers={"X-Requested-With": "XMLHttpRequest", "Referer": SRC_PAGE}) as r:
            if r.status != 200:
                return None
            d = json.loads(await r.text())
    except Exception:                              # noqa: BLE001
        return None
    if not isinstance(d, dict) or d.get("status_msg") != "SUCCESS":
        return None
    try:
        v = float(d.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


async def _fetch_cash(market: dict | None = None) -> dict:
    """{валюта: {sell, buy, at}} — дирхамов за единицу: sell — обменник у нас
    покупает, buy — нам продаёт (только у основных). Молчит — пусто."""
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
            async with s.get(SRC_PAGE) as r:
                if r.status != 200:
                    log.warning(f"[rates] Al Ansari: страница ответила {r.status}")
                    return {}
                html = await r.text()
            nonce, codes = page_nonce(html), page_codes(html)
            if not nonce or not codes:
                log.warning(f"[rates] Al Ansari: на странице нет ключа ({bool(nonce)}) "
                            f"или списка валют ({len(codes)})")
                return {}
            sem = asyncio.Semaphore(4)

            async def one(code: str, tr: str):
                frm, cnt = codes[code]
                async with sem:
                    return code, tr, await _ajax(s, nonce, frm, cnt, tr)

            jobs = [one(c, "S") for c in ASK if c in codes]
            jobs += [one(c, "B") for c in MAIN if c in codes]
            got = await asyncio.gather(*jobs)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[rates] Al Ansari не ответил: {e}")
        return {}
    at = datetime.now(DUBAI_TZ).isoformat(timespec="minutes")
    out: dict = {}
    for code, tr, v in got:
        if not v:
            continue
        # Сверка с рынком: 0.0003 у сомони — не курс, а «не меняем».
        per_aed = (market or {}).get(code)
        if per_aed:
            mkt = 1 / per_aed
            if abs(v / mkt - 1) > MAX_OFF:
                log.info(f"[rates] Al Ansari {code} {tr}: {v} при рынке {mkt:.4f} — не курс, пропускаю")
                continue
        row = out.setdefault(code, {"at": at})
        row["sell" if tr == "S" else "buy"] = round(v, 6)
    # Продаёт не дороже, чем покупает, — не курс (у сомони обе цены 0.0003):
    # проверка на случай, когда сверить с рынком не с чем.
    for code, row in list(out.items()):
        if row.get("buy") and row.get("sell") and row["buy"] <= row["sell"]:
            log.info(f"[rates] Al Ansari {code}: продаёт {row['buy']} не дороже, чем покупает "
                     f"{row['sell']} — не курс, пропускаю")
            out.pop(code)
    return {c: r for c, r in out.items() if r.get("sell")}


# ── рынок ───────────────────────────────────────────────────────────────────
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


async def _fetch() -> list[dict] | None:
    """Только то, что меняет обменник (владелец, 19 сен 2026: «то, что
    обменник не меняет, вообще оттуда убирай»): в списке — его курс, рынок —
    лишь для сравнения в той же строке. Обменник молчит — None: держим его
    последний курс с отметкой времени, рынок вместо него не подставляем."""
    mkt = await _fetch_market()
    rates, mkt_at = mkt if mkt else ({}, "")
    if rates:
        _CACHE["mkt_rates"] = rates
    # Сверка обменника с рынком — по свежему рынку, а молчит он — по последнему
    # известному: курс за сутки не уходит на треть.
    cash = await _fetch_cash(rates or _CACHE.get("mkt_rates"))
    if not cash:
        return None
    out = []
    for code, c in cash.items():
        per_aed = rates.get(code)
        row = {"code": code, "name": RU.get(code) or code,
               "cash_aed": round(c["sell"], 4), "cash_at": c["at"]}
        if c.get("buy"):
            row["cash_buy_aed"] = round(c["buy"], 4)
        if per_aed:
            row["per_aed"] = round(per_aed, 6)
            row["aed"] = round(1 / per_aed, 4)
            # Насколько обменник дешевле рынка, когда у нас покупает, — его
            # заработок на нас.
            row["spread"] = round((c["sell"] / row["aed"] - 1) * 100, 2)
        else:
            row["aed"] = row["cash_aed"]
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
        try:                                   # снимок дня: завтра будет «вчера»
            import db as _db
            await _db.fx_day_set(datetime.now(DUBAI_TZ).strftime("%Y-%m-%d"),
                                 {r["code"]: (r.get("cash_aed") or r["aed"]) for r in rows})
        except Exception as e:                    # noqa: BLE001
            log.warning(f"[rates] снимок дня не записан: {e}")
        log.info(f"[rates] обновлено · валют {len(rows)}"
                 f" · у обменника {_CACHE.get('cash_n', 0)}"
                 + (f" · доллар рынок {usd['aed']}"
                    + (f", обменник купит {usd['cash_aed']} / продаст {usd.get('cash_buy_aed')}"
                       if usd.get("cash_aed") else ", обменник молчит")
                    if usd else ""))
        return _payload(ok=True)
    _CACHE["fail_at"] = now
    return _payload(ok=False)


def _payload(ok: bool) -> dict:
    at = _CACHE["at"]
    return {
        "source": SRC_NAME,
        "source_url": SRC_PAGE,
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
