"""Проверка прав обязана ловить дыры — проверяем саму проверку.

1 окт 2026: ручка решения по штрафу осталась без `@require_owner`, а
`audit_auth.py` сказал «все ручки закрыты» — он смотрел список файлов, в котором
не было finance_routes.py. Здесь аудиту подсаживают заведомо дырявые ручки, и
он обязан найти каждую.

    python3 tools/test_audit_auth.py
"""
import asyncio, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import audit_auth as A                                            # noqa: E402
from aiohttp import web                                           # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

print("── исходник ───────────────────────────────────────────────────")
всего, провалы, смотрели = A.проверить(ROOT)
eq("в настоящем коде дыр нет", провалы, [])
eq("финансы теперь в проверке", "finance_routes.py" in смотрели, True)
eq("файлы берутся сами, а не по списку", len(смотрели) >= 10, True)

# Тот самый случай: декоратор сняли с ручки решения по штрафу.
tmp = tempfile.mkdtemp()
try:
    for f in A.файлы(ROOT):
        shutil.copy(os.path.join(ROOT, f), os.path.join(tmp, f))
    p = os.path.join(tmp, "finance_routes.py")
    s = open(p, encoding="utf-8").read()
    assert "@require_owner\nasync def handle_fine_decide(" in s
    open(p, "w", encoding="utf-8").write(s.replace("@require_owner\nasync def handle_fine_decide(", "async def handle_fine_decide("))
    _, провалы, _ = A.проверить(tmp)
    eq("СНЯТЫЙ ДЕКОРАТОР ПОЙМАН", [(f, h) for f, h, _ in провалы], [("finance_routes.py", "handle_fine_decide")])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("── живой стук ─────────────────────────────────────────────────")
r = asyncio.run(A.стук())
eq("в настоящем приложении всё закрыто", (r["провалы"], r["неясно"]), ([], []))
eq("проверены сотни маршрутов", r["маршрутов"] > 300, True)
eq("своя подпись проходит у каждой роли", all(ок >= max(1, n * 0.6) for ок, n in r["своя"].values() if n), True)


def дыры(app):
    import driver_routes
    async def голая(request):                       # вовсе без охраны
        return web.json_response({"secret": 1})
    async def сперва_работа(request):               # отвечает 400 раньше, чем спросит права
        return web.json_response({"error": "invalid_json"}, status=400)
    async def чужая(request):
        return web.json_response({"ok": True})
    app.router.add_get("/api/owner/__hole_plain", голая)
    app.router.add_post("/api/owner/__hole_early", сперва_работа)
    app.router.add_get("/api/owner/__hole_role", driver_routes.require_driver(чужая))   # ручка владельца под охраной водителя
    app.router.add_get("/api/__nobody", голая)                                          # маршрут вне списков

r = asyncio.run(A.стук(дыры))
пути = {p for _, p, _, _ in r["провалы"]}
eq("ручка без охраны поймана", "/api/owner/__hole_plain" in пути, True)
eq("ручка, отвечающая до проверки прав, поймана", "/api/owner/__hole_early" in пути, True)
eq("ручка владельца под охраной водителя поймана",
   [(p, что) for _, p, что, _ in r["провалы"] if p == "/api/owner/__hole_role"], [("/api/owner/__hole_role", "подпись роли DRIVER")])
eq("маршрут, про который не сказано чей он, — отдельным списком", ("GET", "/api/__nobody") in r["неясно"], True)
eq("и ничего лишнего не задело", пути, {"/api/owner/__hole_plain", "/api/owner/__hole_early", "/api/owner/__hole_role"})

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
