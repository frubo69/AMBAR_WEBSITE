"""Отмена заказа после «На месте» (владелец, 3 ноя 2026).

Водитель стоял у клиента с товаром, заказ отменили — куда делись бутылки,
отмена не знала: 8 окт 2026 AMB8348160A (Baileys + Jose Cuervo Silver)
отменили утром, завели новый на один Jose Cuervo, Baileys ни продан, ни на
полке — ревизия JVC 31 окт нашла его недостачей без объяснения.

mongomock + настоящий operator_routes._do_cancel. Проверяется:
  • отмена после «На месте» заводит по строке заказа списание вида «отмена»
    (pending, src=cancel, район — полка водителя, пиво — в коробках);
  • пока ждёт — со склада не вычтено; «вернулась» (no) — не вычтено;
    «не вернулась» (ok) — вычтено, удержание по прайсу;
  • отмена до «На месте», тест-заказ, повторная отмена — записей нет / не дублируются;
  • вернули в доставку — нерешённые записи стёрты, решённые остаются;
  • тексты бота: кнопки и подпись решения для вида «отмена».

    python3 tools/test_cancel_writeoff.py
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ.setdefault("AMBAR_DRIVER_IDS", "Водитель А:7")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402
import db, stock_routes as sr, operator_routes as op              # noqa: E402
import writeoff_msg as wm                                         # noqa: E402
import config_staff as staff                                      # noqa: E402

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

CAT = {"abs": {"id": "abs", "name": "Absolut 1 ltr", "price": 100},
       "p31": {"id": "p31", "name": "Heineken 0.33 can", "price": 100, "price_24_full": 200, "price_12_full": 100}}
sr._catalog = lambda: CAT
sr._LOSS_CACHE = {"abs": 62, "p31": 120}
async def _nop(*a, **k): return None
async def _sent(*a, **k): return [{"chat_id": 1, "message_id": 5}]
NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(hours=3)
SENT = []

async def counted(pid):
    return ((await db.writeoff_since({"jvc": SINCE})).get("jvc") or {}).get(pid, 0)

async def order(oid, arrived=True, test=False, driver="Водитель А"):
    o = {"order_id": oid, "status": "approved", "office_id": "tecom", "driver": driver,
         "items": [{"id": "abs", "name": "Absolut 1 ltr", "qty": 2, "price": 100},
                   {"id": "p31", "name": "Heineken 0.33 can ×12", "qty": 1, "price": 100, "pcs": 12}],
         "total": 300, "timestamp": (NOW - timedelta(hours=1)).isoformat(),
         **({"driver_arrived_at": (NOW - timedelta(minutes=20)).isoformat()} if arrived else {}),
         **({"test": True} if test else {})}
    await db._db.orders.insert_one(dict(o))
    return o

async def pending(oid):
    return await db._db.writeoffs.find({"order_id": oid}).sort("item", 1).to_list(length=None)

async def main():
    db._db = AsyncMongoMockClient()["cancel_wo"]
    # Заглушки всего, что ходит наружу: телеграм, карточки, клиент.
    op.notify_driver = _nop; op._refresh_cards = _nop; op._customer_card = _nop
    import owner_routes
    async def notify_owners(key, text, *a, **k):
        SENT.append((key, text, k.get("reply_markup"))); return [{"chat_id": 1, "message_id": len(SENT)}]
    owner_routes.notify_owners = notify_owners
    owner_routes.notify_owners_force = _nop
    # Полка водителя А — JVC, заказ записан на Tecom: списание ложится на JVC.
    staff.district_map = lambda: {"Водитель А": "jvc"}
    sr._writeoff_after = _nop; sr._loss_load = _nop; sr._writeoff_comp_tell = _nop

    print("── отмена после «На месте» ───────────────────────────────────")
    o = await order("O1")
    await op._do_cancel("O1", o, "Оператор")
    rows = await pending("O1")
    eq("две записи — по строке заказа", [(r["item"], r["qty"], r["kind"], r["state"], r["src"]) for r in rows],
       [("abs", 2, "отмена", "pending", "cancel"), ("p31", 0.5, "отмена", "pending", "cancel")])
    eq("район — полка водителя, не район адреса", {r["district"] for r in rows}, {"jvc"})
    eq("кто и что в записи", (rows[0]["by"], rows[0]["by_id"], rows[0]["note"], rows[0]["order_id"]),
       ("Водитель А", 7, "заказ O1 · отменил Оператор", "O1"))
    asked = [x for x in SENT if x[0] == "stock.writeoff"]
    eq("вопрос владельцу ушёл текстом с кнопками, по записи на строку", [(k, bool(m)) for k, _, m in asked],
       [("stock.writeoff", True), ("stock.writeoff", True)])
    eq("кнопки: вернулась / не вернулась", [b[0]["text"] for b in asked[0][2]["inline_keyboard"]],
       ["↩️ Вернулась на полку", "🗑 Не вернулась — списать"])
    eq("сообщения запомнены — чтобы снять кнопки после решения", bool((await db.writeoff_get(rows[0]["_id"])).get("msgs")), True)
    eq("пока ждёт — со склада не вычтено", (await counted("abs"), await counted("p31")), (0, 0))

    print("── решения ───────────────────────────────────────────────────")
    a = await db.writeoff_decide(rows[0]["_id"], False, 1, "Владелец")
    eq("«вернулась» — запись остаётся, в остаток не идёт", (a["state"], await counted("abs")), ("no", 0))
    b = await db.writeoff_decide(rows[1]["_id"], True, 1, "Владелец")
    eq("«не вернулась» — вычтено полкоробки", (b["state"], await counted("p31")), ("ok", 0.5))
    eq("удержание по прайсу: отмена — полная цена", (sr._comp_basis("отмена"), sr._comp_of("abs", 2, "отмена")), ("прайс", 200))
    row = sr._wo_row(b, CAT)
    eq("строка владельцу: полкоробки, по прайсу — за полкоробки", (row["qty"], row["due"], row["kind"]), (0.5, 100, "отмена"))

    print("── тексты бота ───────────────────────────────────────────────")
    t = wm.cancel_text(rows[0])
    eq("вопрос: заголовок, строка, кто", t.split("\n")[:3], ["📦 <b>Отмена после «На месте»</b>", "Absolut 1 ltr × 2", "Водитель А (B1)"])
    eq("после решения «вернулась»", wm.decided_caption(t, False, "Владелец", "отмена").split("\n")[-1], "↩️ <b>Вернулась на полку</b> · Владелец")
    eq("после решения «не вернулась»", wm.decided_caption(t, True, "", "отмена").split("\n")[-1], "🗑 <b>Не вернулась</b> — списано")
    eq("водителю: вернулась", wm.driver_text("Absolut 1 ltr", 2, False, "", "отмена"), "По отменённому заказу: Absolut 1 ltr × 2 — на полке, вопрос закрыт.")
    eq("водителю: списано", wm.driver_text("Absolut 1 ltr", 2, True, "", "отмена").startswith("По отменённому заказу списано"), True)

    print("── когда записей быть не должно ──────────────────────────────")
    o2 = await order("O2", arrived=False)
    await op._do_cancel("O2", o2, "Оператор")
    eq("отмена до «На месте» — записей нет", await pending("O2"), [])
    o3 = await order("O3", test=True)
    await op._do_cancel("O3", o3, "Оператор")
    eq("тест-заказ — записей нет", await pending("O3"), [])
    o4 = await order("O4", driver="")
    await op._do_cancel("O4", o4, "Оператор")
    eq("без водителя — записей нет", await pending("O4"), [])
    n = len([x for x in SENT if x[0] == "stock.writeoff"])
    await op._do_cancel("O1", {**o, "status": "approved"}, "Оператор")
    eq("повторная отмена не дублирует записи и вопросы",
       (len(await pending("O1")), len([x for x in SENT if x[0] == "stock.writeoff"]) - n), (2, 0))

    print("── вернули в доставку ────────────────────────────────────────")
    o5 = await order("O5")
    await op._do_cancel("O5", o5, "Оператор")
    rows5 = await pending("O5")
    await db.writeoff_decide(rows5[0]["_id"], True, 1, "Владелец")       # одну уже решили
    dropped, left = await db.writeoff_cancel_drop("O5")
    eq("нерешённая стёрта, решённая осталась", (dropped, left, len(await pending("O5"))), (1, 1, 1))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
