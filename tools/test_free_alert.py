"""Заказ без оплаты — тревога владельцам в STAR (владелец, 11 окт 2026: «чтобы
заказ без оплаты, например как у founder, сопровождался алертом»).

  • free=True: сообщение уходит ВСЕМ владельцам мимо настроек ярусов (как
    крипта и ручной), с заголовком «ЗАКАЗ БЕЗ ОПЛАТЫ» и строкой «БЕЗ ОПЛАТЫ»;
    в ленту STAR ложится своё событие orders.free;
  • обычный заказ — как раньше: по ярусам, события orders.free нет.

    python3 tools/test_free_alert.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
import db, owner_routes as OR                                      # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

SAVED, SENT = [], []
async def _ins(key, text, meta=None): SAVED.append((key, text, meta))
async def _send(token, chat, text, parse_mode="Markdown", reply_markup=None): SENT.append((chat, text)); return {"ok": True, "result": {"message_id": 1}}
async def _all(): return [10, 20]
async def _subs(key): return [10] if key in ("orders.new", "customers.vip") else []


async def main():
    db.insert_notification = _ins; db.get_all_manager_ids = _all; db.get_owners_subscribed_to = _subs
    OR._send_md = _send; OR.OWNER_BOT_TOKEN = "t"
    await OR.notify_new_order("AMB1", 295, "Фаундер", "—", "Marina", "Marina", 777, 777, set(), set(),
                              items=[{"name": "Absolut 1 ltr", "qty": 1}], free=True)
    # Всем владельцам и фаундеру (его код добавляет в адресаты принудительной рассылки).
    alert = [(c, t) for c, t in SENT if "ЗАКАЗ БЕЗ ОПЛАТЫ" in t]
    eq("без оплаты: ушло всем владельцам, заголовок и строка", (sorted(c for c, _ in alert), all("в выручку не идёт" in t and "Фаундер" in t for _, t in alert)), ([10, 20, 777], True))
    eq("в ленте STAR своё событие orders.free", [k for k, _, _ in SAVED if k == "orders.free"], ["orders.free"])
    eq("VIP-сообщение фаундера тоже есть (независимо)", any("VIP" in t for _, t in SENT), True)
    SAVED.clear(); SENT.clear()
    await OR.notify_new_order("AMB2", 295, "Клиент", "—", "Marina", "Marina", 5, 777, set(), set(),
                              items=[{"name": "Absolut 1 ltr", "qty": 1}])
    eq("обычный заказ — по ярусам, одному подписчику, без orders.free", ([c for c, _ in SENT], [k for k, _, _ in SAVED]), ([10], ["orders.new"]))
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
