"""Курс Al Ansari — с машины в ОАЭ на сервер (3 ноя 2026).

Зачем
-----
С 23 октября 2026 сайт обменника закрыл для нашего сервера весь /wp-admin/:
даже пустой GET к admin-ajax.php (там живёт конвертер) отвечает страницей
Cloudflare «Sorry, you have been blocked», а из Дубая тот же адрес открыт, и
rates._fetch_cash оттуда работает без изменений — это обычная защита админки
WordPress от чужих стран или дата-центров, не ответ на наш трафик. Владелец:
«не надо ничего обходить… ищи любой способ продолжать его брать конкретно с
этого обменника», «раз в день… я не хочу зависеть от макбука». Поэтому курс
берёт этот скрипт на машине в ОАЭ раз в сутки тем же кодом, что раньше
работал на сервере, и отдаёт серверу; сервер сверяет его с рынком, хранит в
базе и показывает всем, как прежде (rates.handle_push).

Как поставить (любая машина в ОАЭ с python3 и aiohttp)
------------------------------------------------------
  ~/.ambar_fx_relay.json  — {"url": "https://<сервер>", "token": "<FX_RELAY_TOKEN из .env сервера>"}, права 600
  crontab: 30 10 * * * cd <репозиторий> && python3 tools/fx_relay.py >> ~/ambar_fx.log 2>&1

Обменник не ответил — ничего не шлём, сервер держит прошлый курс со своим
временем.
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("MONGO_URI", "")          # база здесь не нужна
try:                                            # сертификаты для https у python.org-сборки
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass
import aiohttp                                  # noqa: E402

import rates                                    # noqa: E402

CONF = os.path.expanduser("~/.ambar_fx_relay.json")


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"{_ts()} нет настроек {CONF}: {e}")
    if not c.get("url") or not c.get("token"):
        raise SystemExit(f"{_ts()} в {CONF} нужны url и token")
    return c


async def main() -> int:
    c = _conf()
    cash = await rates._fetch_cash(None)        # сверка с рынком — на сервере
    if not cash:
        print(f"{_ts()} обменник не ответил — ничего не шлём")
        return 0
    usd = cash.get("USD") or {}
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(c["url"].rstrip("/") + "/api/fx/push",
                              json={"cash": cash, "from": "mac"},
                              headers={"X-Ambar-Relay": c["token"]}) as r:
                text = await r.text()
                print(f"{_ts()} валют {len(cash)} · доллар купит {usd.get('sell')} / продаст {usd.get('buy')}"
                      f" → сервер {r.status} {text[:120]}")
                return 0 if r.status == 200 else 1
    except Exception as e:                      # noqa: BLE001
        print(f"{_ts()} сервер не принял: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
