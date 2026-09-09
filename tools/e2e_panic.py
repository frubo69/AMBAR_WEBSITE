"""Скрытый режим: один вход у оператора, второй у владельца, механика общая.

Ловит то, на чём я уже прокололся: при выносе общей функции над ней остался
декоратор @require_operator от обработчика. Функция стала обёрткой на request,
панель падала с TypeError, а сам обработчик остался БЕЗ проверки прав.

Поэтому здесь смотрим на сырой объект кода, а не на inspect.signature: у
обёртки стоит @wraps, и signature честно показывает подпись обёрнутой функции —
то есть врёт ровно в том случае, ради которого стенд и написан.
"""
import asyncio, inspect, os, sys

os.environ.update({"MONGO_URI": "", "OPERATOR_BOT_TOKEN": "111:o", "DRIVER_BOT_TOKEN": "111:d",
                   "AMBAR_OWNER_IDS": "1", "AMBAR_DRIVER_IDS": "Худоба:111",
                   "AMBAR_OPERATOR_IDS": "Фарух:501", "AMBAR_MANAGER_IDS": "1",
                   "AMBAR_SENIOR_STAR_IDS": "Старший:555"})
sys.path.insert(0, os.getcwd())
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

FAILS = []
def check(c, m):
    print(("  ✓ " if c else "  ✗ ") + m)
    if not c:
        FAILS.append(m)

def сырая(f):
    """Настоящие аргументы функции — из объекта кода, мимо @wraps."""
    c = f.__code__
    return list(c.co_varnames[:c.co_argcount])


async def main():
    app = web.Application()
    import owner_routes, operator_routes as pos, config_staff as staff, db
    owner_routes.setup(app); pos.setup(app)

    print("маршруты:")
    for m, p in (("POST", "/api/owner/where/panic"), ("POST", "/api/operator/driver-panic")):
        r = await app.router.resolve(make_mocked_request(m, p))
        check(r.http_exception is None, f"{m} {p}")

    print("\nкто есть кто:")
    check(сырая(pos.drv_panic) == ["name", "on", "кто", "откуда"],
          f"drv_panic — общая функция, а не обработчик: {сырая(pos.drv_panic)}")
    check(getattr(pos.drv_panic, "__wrapped__", None) is None,
          "на drv_panic не висит декоратор запроса")
    check(сырая(pos.handle_drv_panic) == ["request"],
          "handle_drv_panic принимает request")
    check(getattr(pos.handle_drv_panic, "__wrapped__", None) is not None,
          "handle_drv_panic ЗАЩИЩЁН require_operator")
    check(getattr(pos.handle_op_panic, "__wrapped__", None) is not None,
          "handle_op_panic защищён")

    print("\nни один обработчик оператора не остался без проверки прав:")
    голые = [r.handler.__name__ for r in app.router.routes()
             if r.method != "OPTIONS"
             and getattr(r.handler, "__module__", "") == "operator_routes"
             and r.handler.__name__ != "_opt"
             and getattr(r.handler, "__wrapped__", None) is None]
    check(not голые, f"голых обработчиков: {голые or 'нет'}")

    print("\nвызов проходит целиком (база и телеграм подменены):")
    следы = []
    async def f_set(who, on, at, meta=None): следы.append(("panic_set", who, on, (meta or {}).get("by")))
    async def f_wipe(name): следы.append(("wipe", name)); return 0
    async def f_cover(name, on): следы.append(("cover", name, on))
    db.panic_set, pos._drv_wipe, pos._drv_cover = f_set, f_wipe, f_cover
    import owner_routes as OR
    async def f_notify(k, t): следы.append(("владельцу", k))
    OR.notify_owners_force = f_notify
    await pos.drv_panic("Худоба", True, "тест", "панели")
    for c in следы: print("   ", c)
    check(any(c[0] == "panic_set" for c in следы), "паника поставлена")
    check(any(c[0] == "cover" for c in следы), "прикрытие положено")

    print("\nИТОГ:", "все сценарии прошли" if not FAILS else f"{len(FAILS)} провалов")
    sys.exit(1 if FAILS else 0)

asyncio.run(main())
