"""Демо приложения водителя (18 сен 2026).

Владелец: «сделай это демо-аккаунтом, чтобы прямо там посмотреть, как выглядит
приёмка и работа с заказом, и чтобы я поставил его им на все телефоны».

Страница собирается из НАСТОЯЩЕГО driver/index.html при каждом запросе: копии,
которая может отстать от приложения, здесь нет намеренно — демо, показывающее
прошлогодний экран, хуже, чем никакого. Подменяются ровно три вещи:

  • скрипт телеграма          → заглушка из driver/demo.js;
  • api.js                    → driver/demo.js (сервер, живущий во вкладке);
  • boot()                    → demoBoot() (чинит камеру и сканер, зовёт boot).

Всё остальное — разметка, стили, логика экранов — боевое, слово в слово.
Ответ никуда не ходит: ни одного запроса за пределы вкладки демо не делает.
"""
import logging
from pathlib import Path

from aiohttp import web

log = logging.getLogger("demo")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "driver" / "index.html"
DEMO_JS = "demo.js"

TG_TAG = '<script src="/vendor/telegram-web-app.js"></script>'
TG_FALLBACK = ('<script>if(!window.Telegram||!window.Telegram.WebApp){document.write'
               '(\'<scr\'+\'ipt src="https://telegram.org/js/telegram-web-app.js"><\\/scr\'+\'ipt>\');}</script>')
API_TAG = '<script src="api.js"></script>'
BOOT = "\nboot();"

_CACHE = {"mtime": 0.0, "html": ""}


# Приложение зовёт свои картинки относительным путём («img/empty-orders.png»),
# а демо отдаётся с /demo — там этот путь упирается в корень сайта и приходит
# 404: экран без картинки. Правим на корневой; их всего несколько.
REL = (('"img/', '"/driver/img/'), ("'jsQR.js", "'/driver/jsQR.js"))


def build(src: str, ver: str = "") -> str:
    """Три подмены. Если разметка изменилась так, что подменять нечего —
    честно падаем: молча отданное демо без заглушек ушло бы в боевой сервер
    с пустой подписью и показало бы человеку экран «нет доступа»."""
    tag = f'<script src="/driver/{DEMO_JS}{ver}"></script>'
    for need, rep in ((TG_TAG, tag), (TG_FALLBACK, ""), (API_TAG, ""), (BOOT, "\ndemoBoot();")):
        if src.count(need) != 1:
            raise RuntimeError(f"демо: в driver/index.html не найден кусок {need[:40]!r}")
        src = src.replace(need, rep, 1)
    for a, b in REL:
        src = src.replace(a, b)
    return src


async def handle(request: web.Request) -> web.Response:
    try:
        st = SRC.stat()
        if _CACHE["mtime"] != st.st_mtime:
            _CACHE["html"] = build(SRC.read_text(encoding="utf-8"), f"?v={int(st.st_mtime)}")
            _CACHE["mtime"] = st.st_mtime
            log.info("[демо] страница собрана заново")
    except Exception as e:                                   # noqa: BLE001
        log.error(f"[демо] не собралось: {e}")
        return web.Response(status=500, text="demo build failed")
    return web.Response(text=_CACHE["html"], content_type="text/html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                 "Pragma": "no-cache", "Expires": "0"})


def setup(app: web.Application):
    for p in ("/demo", "/demo/", "/driver/demo", "/driver/demo/"):
        app.router.add_get(p, handle)
