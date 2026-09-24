"""Таблица маршрутов: ручка есть, и её пускают нужным методом.

Две ночные беды 24 сен 2026, обе показались человеку одинаково — «ошибка код
405»:

  • у новой ручки не было своей строки OPTIONS. Панель открывается не всегда с
    того же адреса, что API (fallback-домен, `?api=`, сохранённый
    `ambar_api_url`), и браузер перед POST шлёт предполётный запрос;
  • в коммит уехала регистрация ручки, тела которой в коммите не было.
    `setup()` упал на NameError, и ВСЁ, что регистрируется ниже, не появилось:
    оператор шесть часов не мог создать заказ (POST /api/operator/orders).

Отсюда три проверки: setup() проходит целиком, OPTIONS есть у каждой ручки, и
ручки, на которых держится работа, отвечают на свой метод.

    python3 tools/test_routes.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from aiohttp import web                                      # noqa: E402
import driver_routes, operator_routes                         # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

# Ручки, без которых работа встаёт. Не весь список — те, чьё молчание человек
# увидит как «ошибка код 405» и не поймёт причины.
ЖИЗНЕННО = {
    "оператор": [("POST", "/api/operator/orders"),          # создать заказ руками
                 ("GET", "/api/operator/orders"),
                 ("PATCH", "/api/operator/orders/{oid}"),   # поправить состав
                 ("POST", "/api/operator/orders/{oid}/accept"),
                 ("POST", "/api/operator/close-request"),   # отпустить водителя
                 ("POST", "/api/operator/close-request/undo")],
    "водитель": [("POST", "/api/driver/shift/open"),
                 ("POST", "/api/driver/shift/close"),
                 ("POST", "/api/driver/shift/close-request"),
                 ("POST", "/api/driver/orders/{oid}/delivered")],
}


def таблица(модуль) -> dict:
    """{путь: {методы}} из настоящей setup(). Упадёт — значит служба поднимется
    без части ручек, и тест обязан это показать, а не проглотить."""
    app = web.Application()
    модуль.setup(app)
    out: dict = {}
    for r in app.router.routes():
        out.setdefault(getattr(r.resource, "canonical", "") or "", set()).add(r.method)
    return out


def main():
    for имя, модуль in (("оператор", operator_routes), ("водитель", driver_routes)):
        try:
            таб = таблица(модуль)
        except Exception as e:                               # noqa: BLE001
            eq(f"{имя}: setup() проходит целиком", f"{type(e).__name__}: {e}", "без ошибок")
            continue
        eq(f"{имя}: setup() проходит целиком", "без ошибок", "без ошибок")
        без = sorted(p for p, m in таб.items()
                     if p.startswith("/api/") and m - {"OPTIONS", "HEAD"} and "OPTIONS" not in m)
        eq(f"{имя}: у всех ручек есть OPTIONS", без, [])
        нет = [f"{m} {p}" for m, p in ЖИЗНЕННО[имя] if m not in таб.get(p, set())]
        eq(f"{имя}: ручки, на которых держится работа, на месте", нет, [])
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(main())
