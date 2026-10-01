"""Каждая ручка — за проверкой прав. Постоянная проверка, а не разовая.

Две проверки, и обе обязаны пройти.

1. ИСХОДНИК. Декоратор над `def` — это факт. В рантайме так нельзя:
   `require_operator` ставит `@wraps`, и `inspect.signature` показывает подпись
   обёрнутой функции, а `require_driver` `@wraps` не ставит вовсе. Файлы берём
   не по списку, а все, где есть `def setup(`: 1 окт 2026 выяснилось, что в
   списке не было finance_routes.py, и ручка решения по штрафу, оставшаяся без
   `@require_owner`, прошла проверку «все закрыты».

2. ЖИВОЙ СТУК. Собираем приложение тем же `api_server.build_app()`, что уходит
   в бой, и стучимся в КАЖДЫЙ зарегистрированный маршрут:
     • без заголовка авторизации;
     • с мусором вместо подписи;
     • с настоящей подписью ЧУЖОЙ роли (водитель → ручки владельца и т. д.).
   Везде обязан быть отказ 401/403. Ответил чем-то ещё — значит, обработчик
   начал работать до проверки прав, и это провал, даже если он вернул 400.
   Своей ролью стучимся тоже (только GET): если своя роль не проходит, стук
   ничего не доказывает.

Исключения — закрытым списком, каждое с причиной.

    python3 tools/audit_auth.py            # обе проверки
    python3 tools/audit_auth.py --static   # только исходник (быстро)
"""
import ast, asyncio, io, os, re, sys

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
# Модули, где права проверяет не декоратор, а ключ в адресе или сама ручка.
# В статической проверке их пропускаем — их проверяет живой стук.
СВОЯ_ОХРАНА = {"track_routes.py", "pixoo_routes.py", "demo_page.py", "rates.py", "stock_value.py",
               "api_server.py"}


def _имя(n):
    return getattr(n, "id", None) or getattr(n, "attr", None)


def файлы(корень=КОРЕНЬ):
    """Все модули, которые регистрируют маршруты: есть `def setup(`."""
    out = []
    for f in sorted(os.listdir(корень)):
        if f.endswith(".py") and re.search(r"(?m)^def setup\(", io.open(os.path.join(корень, f), encoding="utf-8").read()):
            out.append(f)
    return out


def проверить(корень=КОРЕНЬ):
    провалы, всего, смотрели = [], 0, []
    for f in файлы(корень):
        if f in СВОЯ_ОХРАНА:
            continue
        смотрели.append(f)
        t = ast.parse(io.open(os.path.join(корень, f), encoding="utf-8").read())
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
                провалы.append((f, имя, tuple(d or '?' for d in деко[имя])))   # кортеж: список в set не кладётся
    return всего, sorted(set(провалы)), смотрели


# ── живой стук ──────────────────────────────────────────────────────────────
OWNER, DRIVER, OPERATOR, CUSTOMER = "OWNER", "DRIVER", "OPERATOR", "CUSTOMER"
РОЛИ = (OWNER, DRIVER, OPERATOR, CUSTOMER)

# Чьи ручки по адресу. Порядок важен: первое совпадение.
ЧЬЁ = (
    ("/api/owner/",    OWNER),
    ("/api/driver/",   DRIVER),
    ("/api/operator/", OPERATOR),
)
# Ручки клиента (приложение покупателя): подпись клиентского бота.
КЛИЕНТ = ("/api/me", "/api/review", "/api/review-skip", "/api/active-order", "/api/verify-request",
          "/api/orders", "/api/order", "/api/cancel-order", "/api/points-history",
          "/api/crypto/", "/api/support/")
# Открыто без подписи — закрытым списком, с причиной. Значение: что ОБЯЗАНА
# вернуть ручка на стук с пустыми руками (None — что угодно, это витрина).
ОТКРЫТО = {
    "/":                 (None, "статика: страницы мини-приложений"),
    "/{path}":           (None, "статика: страницы мини-приложений"),
    "/demo":             (None, "демо водителя — страница без данных"),
    "/demo/":            (None, "демо водителя — страница без данных"),
    "/driver/demo":      (None, "демо водителя — страница без данных"),
    "/api/call/ice":     (None, "STUN/TURN до звонка; пароль TURN живёт десять минут"),
    "/api/call/ws":      (None, "сокет: первое сообщение — представление, иначе рвём"),
    "/api/room/ice":     (None, "тот же список STUN/TURN, что у /api/call/ice"),
    "/api/room/ws":      (None, "сокет комнаты: вход по коду комнаты первым сообщением"),
}
# Закрыто ключом в адресе или теле (не телеграмной подписью): с выдуманным
# ключом обязан быть отказ или «не найдено», но не данные.
ПО_КЛЮЧУ = ("/track", "/api/track", "/pixoo/", "/api/owner/archive/file")
# Клиентское приложение на неверную подпись в трёх списках отвечает не отказом,
# а пустым списком (так сделано давно: вне телеграма экран не должен падать).
# Данных в таком ответе быть не должно — это и проверяем.
ПУСТОЙ_ОТВЕТ = {"/api/orders": "orders", "/api/support/messages": "messages", "/api/points-history": "history"}
ОТКАЗ = (401, 403)


def _fakes():
    """Подписи ролей: каждая проверка принимает только своё слово. В бою роль
    отличает токен бота, которым подписаны данные; здесь — слово."""
    import api_server, owner_auth, driver_routes, operator_routes, config_staff as staff
    owner_auth.install_validator(lambda s: {"id": 1, "first_name": "Аудит"} if s == OWNER else None)
    driver_routes._valid_init_data = lambda init, token: {"id": 777, "first_name": "Аудит"} if init == DRIVER else None
    api_server.validate_init_data = lambda s: {"id": 999, "first_name": "Аудит"} if s == CUSTOMER else None
    operator_routes._validate_operator_init_data = lambda s: {"id": 888, "first_name": "Аудит"} if s == OPERATOR else None
    operator_routes.OPERATOR_IDS = list(set(list(operator_routes.OPERATOR_IDS) + [888]))
    # Оплата криптой в проверочном окружении выключена и отвечает 503 раньше
    # подписи; включаем, чтобы дойти до самой проверки прав.
    api_server.CRYPTO_REAL_MODE = True

    async def _nosync(*a, **k):
        return None
    staff.sync = _nosync
    staff.driver_by_tg = lambda uid: ({"name": "Аудит-водитель", "district": "jvc", "district_code": "B1",
                                       "district_name": "JVC", "operator": "Аудит"} if uid == 777 else None)


def _адрес(шаблон: str, pat) -> str:
    """Настоящий адрес под шаблон маршрута. У параметра бывает своё правило
    ({action:ban|unban}, {id:\\d+}); подставь «x» — запрос уйдёт мимо ручки и
    вернёт 405, ничего не проверив. Правило достаём из собранного шаблона."""
    url = re.sub(r"\{[^}]+\}", "x", шаблон)
    if pat is None or pat.fullmatch(url):
        return url
    правила = dict(re.findall(r"\(\?P<(\w+)>((?:[^()]|\([^()]*\))*)\)", pat.pattern))

    def значение(m):
        rx = правила.get(m.group(1), "")
        for проба in ("x", "1", *[a for a in re.split(r"[|()]", rx) if a and re.fullmatch(r"[\w-]+", a)]):
            try:
                if re.fullmatch(rx, проба):
                    return проба
            except re.error:
                break
        return "1"
    return re.sub(r"\{(\w+)\}", значение, шаблон)


def _чьё(path: str):
    for pre, role in ЧЬЁ:
        if path.startswith(pre):
            return role
    if any(path == k or (k.endswith("/") and path.startswith(k)) for k in КЛИЕНТ):
        return CUSTOMER
    return None


async def стук(подсадить=None):
    """подсадить(app) — для самопроверки: добавить в собранное приложение
    заведомо дырявые ручки и убедиться, что стук их ловит."""
    os.environ["MONGO_URI"] = ""
    os.environ.setdefault("AMBAR_OWNER_IDS", "1")
    import logging
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, КОРЕНЬ)
    from aiohttp.test_utils import TestClient, TestServer
    from mongomock_motor import AsyncMongoMockClient
    import aiohttp, db, api_server
    db._db = AsyncMongoMockClient()["ambar_audit_auth"]
    app = api_server.build_app()
    _fakes()
    if подсадить:
        подсадить(app)
    # Приложение не запускаем по-настоящему: ни фоновых задач, ни базы, ни ботов.
    app.on_startup.clear(); app.on_cleanup.clear(); app.on_shutdown.clear()

    маршруты, видел = [], set()
    for r in app.router.routes():
        if r.method in ("OPTIONS", "HEAD") or not r.resource:
            continue
        info = r.resource.get_info()
        шаблон = info.get("path") or info.get("formatter") or ""
        if not шаблон or (r.method, шаблон) in видел:
            continue
        видел.add((r.method, шаблон))
        маршруты.append((r.method, шаблон, _адрес(шаблон, info.get("pattern"))))

    провалы, неясно, своя, не_пустило = [], [], {r: [0, 0] for r in РОЛИ}, []
    счёт = {"закрыто подписью": 0, "закрыто ключом": 0, "открыто по списку": 0}
    timeout = aiohttp.ClientTimeout(total=6)
    async with TestClient(TestServer(app)) as cl:
        async def код(method, url, auth=None, клиент=False, сырой=False):
            h = {"Authorization": auth} if auth else {}
            слово = (auth or "")[4:] if (auth or "").startswith("tma ") else ""
            try:
                kw = {}
                if method in ("POST", "PATCH", "DELETE"):
                    # Клиентское приложение шлёт подпись в теле, а снимок — формой.
                    if клиент and url.endswith("/send-image"):
                        form = aiohttp.FormData(); form.add_field("initData", слово)
                        form.add_field("image", b"\xff\xd8" + b"0" * 64, filename="a.jpg", content_type="image/jpeg")
                        kw = {"data": form}
                    else:
                        kw = {"json": {"initData": слово} if клиент else {}}
                async with cl.request(method, url, headers=h, timeout=timeout, **kw) as resp:
                    if клиент and not сырой and resp.status == 200 and url in ПУСТОЙ_ОТВЕТ:
                        try:
                            body = await resp.json()
                        except Exception:                    # noqa: BLE001
                            body = None
                        поле = (body or {}).get(ПУСТОЙ_ОТВЕТ[url]) if isinstance(body, dict) else body
                        return "пусто" if not поле else 200
                    return resp.status
            except asyncio.TimeoutError:
                return "таймаут"
            except Exception as e:                           # noqa: BLE001
                return f"сбой:{type(e).__name__}"

        for method, шаблон, url in маршруты:
            # Ключ в адресе главнее приставки: у архива адрес владельца, а
            # открывается он одноразовым токеном, не подписью.
            if any(шаблон.startswith(k) for k in ПО_КЛЮЧУ):
                c = await код(method, url)
                счёт["закрыто ключом"] += 1
                if c not in (400, 401, 403, 404):
                    провалы.append((method, шаблон, "ключ из головы", c))
                continue
            роль = _чьё(шаблон)
            if роль is None:
                if шаблон in ОТКРЫТО or any(шаблон.startswith(k) for k in ("/driver/demo", "/demo")):
                    счёт["открыто по списку"] += 1
                    continue
                неясно.append((method, шаблон))
                continue
            счёт["закрыто подписью"] += 1
            # 1–2. С пустыми руками и с мусором.
            кл = роль == CUSTOMER
            ок = ОТКАЗ + (("пусто",) if кл else ())
            for что, auth in (("без подписи", None), ("мусор", "tma мусор"), ("не tma", "Bearer OWNER")):
                c = await код(method, url, auth, кл)
                if c not in ок:
                    провалы.append((method, шаблон, что, c))
            # 3. Настоящая подпись чужой роли.
            for чужая in РОЛИ:
                if чужая == роль:
                    continue
                c = await код(method, url, "tma " + чужая, кл)
                if c not in ок:
                    провалы.append((method, шаблон, f"подпись роли {чужая}", c))
            # 4. Своя роль — только чтение: стук должен доказывать, что слово работает.
            if method == "GET":
                c = await код(method, url, "tma " + роль, кл, сырой=True)
                своя[роль][1] += 1
                if c not in ОТКАЗ:
                    своя[роль][0] += 1
                else:
                    не_пустило.append((роль, шаблон, c))
    return {"маршрутов": len(маршруты), "счёт": счёт, "провалы": провалы, "неясно": неясно, "своя": своя,
            "не_пустило": не_пустило}


def main():
    только_исходник = "--static" in sys.argv
    всего, провалы, смотрели = проверить()
    print(f"ИСХОДНИК: файлов {len(смотрели)}, ручек {всего}; разрешено без охраны: "
          f"{len(МОЖНО)} — {', '.join(sorted(МОЖНО))}")
    if провалы:
        print("\nБЕЗ ПРОВЕРКИ ПРАВ (по исходнику):")
        for f, h, d in провалы:
            print(f"  {f:22} {h:30} декораторы: {d or '—'}")
    плохо = bool(провалы)
    if not только_исходник:
        r = asyncio.run(стук())
        print(f"\nСТУК: маршрутов {r['маршрутов']} — " + ", ".join(f"{k}: {v}" for k, v in r["счёт"].items()))
        for роль, (ок, всего_) in r["своя"].items():
            if всего_:
                print(f"  своя подпись {роль}: проходит {ок} из {всего_} GET")
                if ок < max(1, всего_ * 0.6):
                    print(f"  ВНИМАНИЕ: подпись {роль} почти нигде не проходит — стук по этой роли ничего не доказывает")
                    плохо = True
        for роль, p_, c in r["не_пустило"]:
            print(f"    своя подпись {роль} не прошла: {p_} → {c}")
        if r["неясно"]:
            плохо = True
            print("\nМАРШРУТЫ ВНЕ СПИСКОВ (чьи они — не сказано; впишите в ЧЬЁ, КЛИЕНТ, ПО_КЛЮЧУ или ОТКРЫТО):")
            for m, p in r["неясно"]:
                print(f"  {m:6} {p}")
        if r["провалы"]:
            плохо = True
            print("\nПУСТИЛО БЕЗ ПРАВ:")
            for m, p, что, c in r["провалы"]:
                print(f"  {m:6} {p:58} {что:26} → {c}")
    print("\nИТОГ:", "все ручки закрыты" if not плохо else "ЕСТЬ ДЫРЫ — см. выше")
    sys.exit(1 if плохо else 0)


if __name__ == "__main__":
    main()
