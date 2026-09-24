"""У каждой ручки должен быть свой OPTIONS (24 сен 2026: «ошибка код 405»).

Панель оператора и приложение водителя открываются не всегда с того же адреса,
что API (fallback-домен, свой `?api=`, сохранённый `ambar_api_url`). Тогда
браузер перед POST шлёт предполётный OPTIONS — и если у пути его нет, aiohttp
отвечает 405, а человек видит «не получилось». Так и случилось с возвратом
водителя в смену: ручка работала, а кнопка — нет.

Проверяем не глазами, а таблицей маршрутов настоящего приложения.

    python3 tools/test_cors_options.py
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


def пути(модуль) -> dict:
    """{путь: {методы}} из настоящей setup()."""
    app = web.Application()
    модуль.setup(app)
    out: dict = {}
    for r in app.router.routes():
        путь = getattr(r.resource, "canonical", "") or ""
        out.setdefault(путь, set()).add(r.method)
    return out


def main():
    for имя, модуль in (("оператор", operator_routes), ("водитель", driver_routes)):
        таб = пути(модуль)
        без = sorted(p for p, m in таб.items()
                     if p.startswith("/api/") and m - {"OPTIONS", "HEAD"} and "OPTIONS" not in m)
        eq(f"{имя}: у всех ручек есть OPTIONS", без, [])
        eq(f"{имя}: возврат в смену принимает POST и OPTIONS",
           "OPTIONS" in таб.get("/api/operator/close-request/undo", set())
           if модуль is operator_routes else True, True)
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(main())
