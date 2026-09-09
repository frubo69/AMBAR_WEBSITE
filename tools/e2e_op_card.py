# Что реально уходит оператору: клавиатура в обоих видах и разметка на каждый
# чат маршрута. Наружу не летит ничего — tg_send подменён.
import os, sys, json, asyncio, importlib
os.environ.update({"MONGO_URI": "", "OPERATOR_BOT_TOKEN": "111:op",
                   "AMBAR_OPERATOR_IDS": "Фарух:501", "AMBAR_SENIOR_STAR_IDS": "Старший:555"})
sys.path.insert(0, os.getcwd())

FAILS = []
def check(c, m):
    print(("  ✓ " if c else "  ✗ ") + m)
    if not c: FAILS.append(m)

def kb(mod, chat):
    return mod.order_kb_for(777, 42)(chat)

print("— вид «app» (по умолчанию) —")
os.environ.pop("AMBAR_OP_CARD", None)
import op_card; importlib.reload(op_card)
k = kb(op_card, 501)
print("   личка:", json.dumps(k, ensure_ascii=False))
rows = k["inline_keyboard"]
check(len(rows) == 1 and len(rows[0]) == 1, "одна кнопка")
check(rows[0][0]["text"] == "Открыть в приложении", "текст кнопки")
check("web_app" in rows[0][0], "это мини-апп, а не ссылка")
check(rows[0][0]["web_app"]["url"].startswith("https://"), "адрес панели")
check(rows[0][0]["web_app"]["url"].endswith("?order=777"), "адрес ведёт в этот заказ")
check(op_card.app_url(None).endswith("/"), "без номера — просто панель")
check(op_card.app_url("A/B 1") .endswith("?order=A%2FB%201"), "номер экранируется")

g = kb(op_card, -100123)
print("   группа:", json.dumps(g, ensure_ascii=False))
check("web_app" not in json.dumps(g), "в группе кнопки мини-аппа нет")

os.environ["OPERATOR_BOT_NAME"] = "ambar_op_bot"
importlib.reload(op_card)
g2 = kb(op_card, -100123)["inline_keyboard"]
print("   группа с именем бота:", json.dumps(g2, ensure_ascii=False))
check(g2[0][0]["url"].endswith("?start=order_777"), "в группе — ссылка на этот заказ в боте")
os.environ.pop("OPERATOR_BOT_NAME", None)

print("— вид «buttons» (прежний) —")
os.environ["AMBAR_OP_CARD"] = "buttons"
importlib.reload(op_card)
b = kb(op_card, 501)["inline_keyboard"]
print("  ", json.dumps(b, ensure_ascii=False))
check([len(r) for r in b] == [2, 2, 1], "три ряда: 2 / 2 / 1")
check([c["text"] for c in b[0]] == ["✅ Принять", "❌ Отклонить"], "первый ряд")
check([c["text"] for c in b[1]] == ["✏️ Редактировать", "📍 Геолокация"], "второй ряд")
check(b[2][0]["text"] == "👤 Клиент", "третий ряд")
check(b[0][0]["callback_data"] == "acc_777_42", "callback принятия прежний")

print("— маршрут: разметка считается на каждый чат —")
os.environ.pop("AMBAR_OP_CARD", None)
importlib.reload(op_card)
import api_server, op_route
отправлено = []
async def fake_send(token, chat_id, text, parse_mode=None, reply_markup=None, **kw):
    отправлено.append((chat_id, reply_markup))
    return {"ok": True, "result": {"message_id": 900 + len(отправлено)}}
api_server.tg_send = fake_send
async def fake_chats(district=""):
    return [{"chat_id": 501, "prefix": ""}, {"chat_id": -100123, "prefix": ""}]
op_route.chats = fake_chats
op_route.db.drv_msg_add = lambda *a, **k: asyncio.sleep(0)

async def main():
    ids = await op_route.send("🆕 ЗАКАЗ #777", district="b1",
                              reply_markup=op_card.order_kb_for(777, 42))
    for chat, markup in отправлено:
        print(f"   {chat}: {json.dumps(markup, ensure_ascii=False)}")
    check(len(отправлено) == 2, "ушло в оба чата")
    check("web_app" in json.dumps(отправлено[0][1]), "в личку — мини-апп")
    check("web_app" not in json.dumps(отправлено[1][1]), "в группу — без мини-аппа")
    check(len(ids) == 2, "id сообщений вернулись")

asyncio.run(main())
print("\nИТОГ:", "все сценарии прошли" if not FAILS else f"{len(FAILS)} провалов")
sys.exit(1 if FAILS else 0)
