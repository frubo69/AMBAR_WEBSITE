# Ручка локатора смонтирована, чужое имя отбивается, механика одна на два входа.
import os, sys, asyncio, inspect
os.environ.update({"MONGO_URI":"", "OPERATOR_BOT_TOKEN":"111:o", "DRIVER_BOT_TOKEN":"111:d",
    "AMBAR_OWNER_IDS":"1", "AMBAR_DRIVER_IDS":"Худоба:111", "AMBAR_OPERATOR_IDS":"Фарух:501",
    "AMBAR_SENIOR_STAR_IDS":"Старший:555", "AMBAR_MANAGER_IDS":"1"})
sys.path.insert(0, os.getcwd())
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
FAILS=[]
def check(c,m):
    print(("  ✓ " if c else "  ✗ ")+m)
    if not c: FAILS.append(m)
async def main():
    app=web.Application()
    import owner_routes, operator_routes
    owner_routes.setup(app); operator_routes.setup(app)
    for m,p in (("POST","/api/owner/where/panic"),("POST","/api/operator/driver-panic")):
        r=await app.router.resolve(make_mocked_request(m,p))
        check(r.http_exception is None, f"{m} {p} смонтирован")
    check(hasattr(operator_routes,"drv_panic"), "общая drv_panic есть")
    sig=list(inspect.signature(operator_routes.drv_panic).parameters)
    check(sig[:4]==["name","on","кто","откуда"], f"её подпись: {sig}")
    src=inspect.getsource(owner_routes.handle_where_panic)
    check("pos.drv_panic(" in src, "владелец зовёт общую, а не копию")
    check("driver_names()" in src, "чужое имя отбивается по списку водителей")
    check("_drv_wipe" not in src and "panic_set" not in src, "в панели нет второй копии механики")
    print("\nИТОГ:", "все сценарии прошли" if not FAILS else f"{len(FAILS)} провалов")
    sys.exit(1 if FAILS else 0)
asyncio.run(main())
