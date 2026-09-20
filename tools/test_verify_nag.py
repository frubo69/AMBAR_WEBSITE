"""Клиент застрял на верификации (владелец, 21 сен 2026: «пусть если клиент за
2 минуты не заполняет окно верификации, операторам приходит сообщение, что
клиент застрял, и ему нужно через поддержку помочь её пройти»).

mongomock + настоящий verify_nag; телеграм подменён, наружу ничего не уходит.

  • заказ, который ждёт анкеты дольше двух минут: клиенту — письмо в основной
    бот с кнопкой анкеты, операторам — обращение в бот поддержки с привязкой
    к клиенту (ответ реплаем уйдёт ему), владельцу — «застрял на верификации»;
  • пишем один раз: второй проход молчит;
  • молчим, если анкету уже отправили, если заказ моложе двух минут или старше
    двух часов, если его отменили и ночью;
  • «заказ не принят» про такие заказы больше не приходит: о них говорит этот
    сторож, а не нянька непринятых.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
os.environ["BOT_TOKEN"] = "111:test"
os.environ["SUPPORT_BOT_TOKEN"] = "222:test"
os.environ["OPERATOR_IDS"] = "501,502"
os.environ["BOT_USERNAME"] = "ambar_bot"
import logging
logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient
import db, support_inbox, verify_nag as VN

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

ОТПРАВЛЕНО = []          # (токен, метод, payload)
ВЛАДЕЛЬЦУ = []           # (ключ, текст)


async def _tg(token, method, payload):
    ОТПРАВЛЕНО.append((token, method, payload))
    return {"ok": True, "result": {"message_id": 1000 + len(ОТПРАВЛЕНО)}}


async def _notify_owners(key, text, **kw):
    ВЛАДЕЛЬЦУ.append((key, text))
    return []


async def заказ(oid, uid, *, минут=5, **kw):
    await db.save_order(oid, {
        "order_id": oid, "customer_id": uid, "customer_name": "Ахмед",
        "total": 250, "office_name": "JVC", "status": "pending",
        "pending_verification": True,
        "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=минут)).isoformat(),
        **kw})


def кому(метод="sendMessage"):
    return [p["chat_id"] for t, m, p in ОТПРАВЛЕНО if m == метод]


async def main():
    db._db = AsyncMongoMockClient()["ambar_verify"]
    support_inbox._tg = _tg
    import owner_routes
    owner_routes.notify_owners = _notify_owners
    VN._quiet = lambda now=None: False

    await db.upsert_user(7001, first_name="Ахмед", phone_verified="+971501234567")

    print("── две минуты молчания — говорим всем ─────────────────────────")
    await заказ("A-1042", 7001)
    n = await VN.once()
    eq("написали по одному заказу", n, 1)
    клиенту = [p for t, m, p in ОТПРАВЛЕНО if p.get("chat_id") == 7001]
    eq("клиенту — одно письмо в основной бот", (len(клиенту), клиенту[0]["chat_id"]), (1, 7001))
    eq("в нём заказ и сумма", "#A-1042" in клиенту[0]["text"] and "250" in клиенту[0]["text"], True)
    eq("и кнопки: анкета и поддержка",
       [b[0]["text"].split()[0] for b in клиенту[0]["reply_markup"]["inline_keyboard"]], ["✅", "💬"])
    eq("операторам — обоим", sorted(x for x in кому() if x != 7001), [501, 502])
    оп = next(p for t, m, p in ОТПРАВЛЕНО if p.get("chat_id") == 501)
    eq("оператор видит, кто и с каким заказом застрял",
       ("Ахмед" in оп["text"], "#A-1042" in оп["text"], "+971501234567" in оп["text"]),
       (True, True, True))
    eq("и подсказку, как ответить", "Ответьте на это сообщение" in оп["text"], True)
    карта = await db.get_support_map_entry("1002")
    eq("ответ реплаем найдёт клиента и его чат",
       ((карта or {}).get("user_id"), (карта or {}).get("channel")),
       (7001, support_inbox.CHANNEL_MAIN))
    eq("владельцу — честная причина, а не «не принят»",
       (ВЛАДЕЛЬЦУ[0][0], "верификац" in ВЛАДЕЛЬЦУ[0][1].lower()),
       ("orders.verify_stuck", True))
    o = (await db.get_all_orders())["A-1042"]
    eq("на заказе отметка, что уже сказали", bool(o.get("verify_nag_at")), True)

    print("── второй проход молчит ───────────────────────────────────────")
    было = len(ОТПРАВЛЕНО)
    eq("никому ничего", (await VN.once(), len(ОТПРАВЛЕНО)), (0, было))

    print("── когда молчим ───────────────────────────────────────────────")
    await заказ("A-1043", 7001, минут=1)                      # моложе двух минут
    eq("заказ моложе порога", await VN.once(), 0)
    await заказ("A-1044", 7001, status="cancelled")           # отменён
    eq("отменённый", await VN.once(), 0)
    await db.upsert_user(7002, first_name="Мария")
    # Отдельной записью: в upsert_user это поле стоит в $setOnInsert и в одном
    # запросе с $set конфликтует — молча побеждает False.
    await db.set_user_field(7002, verify_requested=True)
    await заказ("A-1045", 7002)
    eq("анкету уже отправил", await VN.once(), 0)
    await заказ("A-1047", 7001, минут=180)                    # пролежал три часа
    eq("старый заказ не будим", await VN.once(), 0)
    await заказ("A-1046", 7001)
    VN._quiet = lambda now=None: True
    eq("ночью", await VN.once(), 0)
    VN._quiet = lambda now=None: False
    eq("а утром — да", await VN.once(), 1)

    print("── имя бота неизвестно — кнопка всё равно есть ────────────────")
    os.environ["BOT_USERNAME"] = ""; os.environ["WEBAPP_URL"] = "https://150-241-70-116.sslip.io/"
    VN._BOT_NAME["v"] = None                      # getMe в тесте имени не вернёт
    kb = await VN._client_kb()
    eq("кнопка анкеты становится web_app по адресу из окружения",
       "web_app" in kb["inline_keyboard"][0][0], True)
    eq("и это не наш домен, а адрес из .env",
       kb["inline_keyboard"][0][0]["web_app"]["url"].startswith("https://150-241-70-116"), True)
    os.environ["BOT_USERNAME"] = "ambar_bot"; VN._BOT_NAME["v"] = None

    print("── «не принят» про такие заказы молчит ────────────────────────")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "owner_routes.py"), encoding="utf-8").read()
    i = src.index("async def _monitor_pending_orders")
    eq("нянька непринятых пропускает застрявших на верификации",
       'if o.get("pending_verification"):' in src[i:i + 1600], True)

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
