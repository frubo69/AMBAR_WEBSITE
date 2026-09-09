"""Каждая ручка — за проверкой прав. Постоянная проверка, а не разовая.

Смотрим ИСХОДНИК: декоратор над `def` — это факт. В рантайне так нельзя:
`require_operator` ставит `@wraps`, и `inspect.signature` показывает подпись
обёрнутой функции, а `require_driver` `@wraps` не ставит вовсе. На этом уже
проехали два бага — обработчик без прав и функция, ставшая обёрткой.

Список исключений закрытый и объяснённый: всё, чего в нём нет и что не имеет
охраны, — провал.
"""
import ast, io, os, sys

ОХРАНА = {"require_owner", "require_operator", "require_driver", "require_room"}

# Ручки без декоратора, которые такими и должны быть. Каждая — с причиной.
МОЖНО = {
    "_opt":                 "preflight OPTIONS — до авторизации, отдаёт только заголовки CORS",
    "handle_archive_file":  "открывается по одноразовому токену со сроком: у браузера "
                            "телеграмной авторизации нет (проверка внутри самой ручки)",
    "handle_ice":           "список STUN/TURN нужен странице до звонка; пароль TURN "
                            "эфемерный, живёт десять минут (call_routes.ice_servers)",
    "handle_ws":            "сокет: первое сообщение обязано быть представлением, "
                            "иначе рвём по таймауту AUTH_GRACE",
}
# Не маршруты вовсе: фоновые задачи и хуки, просто упомянуты внутри setup().
НЕ_РУЧКИ = {"_backfill_delivery_times", "_monitor_pending_orders", "_monitor_quiet_hours",
            "_start_monitors", "on_shutdown"}

ФАЙЛЫ = ["owner_routes.py", "operator_routes.py", "driver_routes.py", "qr_routes.py",
         "stock_routes.py", "expense_routes.py", "supply_routes.py", "call_routes.py",
         "wallet_routes.py", "broadcast_routes.py", "room_routes.py", "api_server.py"]


def _имя(n):
    return getattr(n, "id", None) or getattr(n, "attr", None)


def проверить(корень="."):
    провалы, всего = [], 0
    for f in ФАЙЛЫ:
        путь = os.path.join(корень, f)
        if not os.path.exists(путь):
            continue
        t = ast.parse(io.open(путь, encoding="utf-8").read())
        деко = {n.name: [_имя(d.func) if isinstance(d, ast.Call) else _имя(d)
                         for d in n.decorator_list]
                for n in ast.walk(t)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for n in ast.walk(t):
            if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "setup"):
                continue
            for x in ast.walk(n):
                if not (isinstance(x, ast.Name) and x.id in деко):
                    continue
                имя = x.id
                if имя in НЕ_РУЧКИ:
                    continue
                всего += 1
                if set(деко[имя]) & ОХРАНА or имя in МОЖНО:
                    continue
                провалы.append((f, имя, деко[имя]))
    return всего, sorted(set(провалы))


if __name__ == "__main__":
    всего, провалы = проверить(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(f"ручек проверено: {всего}")
    print(f"разрешено без охраны: {len(МОЖНО)} — {', '.join(sorted(МОЖНО))}")
    if провалы:
        print("\nБЕЗ ПРОВЕРКИ ПРАВ:")
        for f, h, d in провалы:
            print(f"  {f:22} {h:30} декораторы: {d or '—'}")
    print("\nИТОГ:", "все ручки закрыты" if not провалы else f"{len(провалы)} без охраны")
    sys.exit(1 if провалы else 0)
