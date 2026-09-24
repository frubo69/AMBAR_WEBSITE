"""Наш телефон на руках у водителя (владелец, 24 сен 2026: «отметь у Муина,
что у него наш телефон, он его должен вернуть»).

Отметка живёт в реестре водителей рядом с машиной и видна там же, где
команда, — в «Кто на каком районе». Снимается одним нажатием, когда вернул.

    python3 tools/test_staff_gear.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.WARNING)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
from aiohttp.test_utils import make_mocked_request                # noqa: E402
import db, config_staff as staff, owner_routes as own             # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


async def зови(body):
    r = make_mocked_request("POST", "/api/owner/staff/gear")
    r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    async def js(): return body
    r.json = js
    h = own.handle_staff_gear
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


def его(payload, имя):
    return next((d for d in payload.get("drivers") or [] if d["name"] == имя), {})


async def main():
    db._db = AsyncMongoMockClient()["ambar_gear"]
    await db._db.drivers.insert_many([
        {"name": "Муин", "district": "tecom"},
        {"name": "Алишер", "district": "tecom"}])
    await staff.sync(force=True)
    eq("водители в реестре", sorted(n for n in staff.driver_names() if n in ("Муин", "Алишер")),
       ["Алишер", "Муин"])

    st, p = await зови({"driver": "Муин", "phone": True})
    eq("отметили", st, 200)
    eq("у Муина наш телефон", его(p, "Муин").get("ours_phone"), True)
    eq("и записано когда", bool(его(p, "Муин").get("ours_phone_at")), True)
    eq("у соседа ничего не поменялось", его(p, "Алишер").get("ours_phone"), False)

    st, p = await зови({"driver": "Муин", "phone": True})
    eq("повторное нажатие ничего не ломает", (st, его(p, "Муин").get("ours_phone")), (200, True))

    st, p = await зови({"driver": "Муин", "phone": False})
    eq("вернул — отметка снята", (st, его(p, "Муин").get("ours_phone")), (200, False))
    eq("и дата снята", его(p, "Муин").get("ours_phone_at"), "")

    st, b = await зови({"driver": "Не наш", "phone": True})
    eq("незнакомому — отказ", (st, b.get("error")), (400, "unknown_driver"))
    st, b = await зови({"phone": True})
    eq("без имени — отказ", (st, b.get("error")), (400, "unknown_driver"))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
