"""Сообщения водителю о деньгах: тексты и то, что каждая ручка зарплат их
шлёт нужному человеку. Запуск: python3 tools/test_pay_notify.py"""
import asyncio, os, sys, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""; os.environ["DRIVER_BOT_TOKEN"] = "x"
os.environ["AMBAR_DRIVER_IDS"] = "Худоба:555"; os.environ["AMBAR_OWNER_IDS"] = "1"
logging.basicConfig(level=logging.ERROR)
from aiohttp.test_utils import make_mocked_request
import db, pay_notify as pn, finance_routes as fin, api_server, config_staff as staff

fails = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL ") + f"{name:<66} got {got!r}  want {want!r}")
    if not ok: fails.append(name)
def has(name, text, *needles):
    miss = [n for n in needles if n not in text]
    eq(name + (f" — нет {miss}" if miss else ""), not miss, True)

SENT, REG, PANIC = [], [], {"Худоба": False}
async def tg_send(token, chat_id, text, parse_mode="Markdown", **k):
    SENT.append((int(chat_id), text, parse_mode)); return {"ok": True, "result": {"message_id": len(SENT)}}
api_server.tg_send = tg_send
async def panic_get(name): return PANIC.get(name, False)
async def drv_msg_add(chat_id, mid, at): REG.append((chat_id, mid))
db.panic_get = panic_get; db.drv_msg_add = drv_msg_add

ITEMS = {}
async def fin_pay_item_add(doc): ITEMS[doc["_id"]] = dict(doc)
async def fin_pay_item_get(iid): return dict(ITEMS.get(iid) or {}) or None
async def fin_pay_item_set(iid, fields): ITEMS[iid].update(fields); return True
async def fin_pay_item_del(iid): ITEMS.pop(iid, None); return True
async def _none(*a, **k): return None
for n, f in dict(fin_pay_item_add=fin_pay_item_add, fin_pay_item_get=fin_pay_item_get, fin_pay_item_set=fin_pay_item_set,
                 fin_pay_item_del=fin_pay_item_del, fin_entry_add=_none, fin_entry_del=_none).items():
    setattr(db, n, f)
async def _touch(*a, **k): pass
async def build(month): return {"month": month}
fin._touch = _touch; fin.build = build
async def _bd(*a, **k): pass
fin.backdate.notify = _bd

def req(body):
    r = make_mocked_request("POST", "/x"); r._read_bytes = json.dumps(body).encode(); r["owner_id"] = 1; r["owner_user"] = {"id": 1}
    return r
async def call(h, body):
    r = await h.__wrapped__(req(body)); return r.status, json.loads(r.text)

async def main():
    print("— тексты")
    it = {"kind": "fine", "amount": 200, "per_month": 0, "from": "2026-09", "day": "2026-09-14", "reason": "Превышение скорости · 20 – 30 км/ч", "note": "камера на SZR"}
    t = pn.added(it, "Старший")
    has("штраф: сумма, причина, комментарий, график, кто", t, "Штраф 200 AED", "Превышение скорости", "камера на SZR", "из зарплаты за сентябрь 2026", "Назначил: Старший")
    t = pn.added({**it, "amount": 900, "per_month": 300, "from": "2026-10"}, "Старший")
    has("штраф по графику", t, "по 300 AED в месяц, начиная с октября 2026")
    has("удержание", pn.added({**it, "kind": "hold"}, ""), "Удержание 200 AED")
    has("аванс выдан днём", pn.added({"kind": "advance", "amount": 500, "day": "2026-09-14", "from": "2026-09"}, "С"), "Аванс 500 AED — выдан 14 сентября", "из зарплаты за сентябрь 2026")
    has("долг записан", pn.added({"kind": "loan", "amount": 1000, "day": "2026-09-14", "from": "2026-09", "per_month": 250}, ""), "Долг 1000 AED — записан 14 сентября", "по 250 AED в месяц")
    has("премия за месяц", pn.added({"kind": "bonus", "amount": 300, "from": "2026-09", "day": "2026-09-14"}, "С"), "Премия 300 AED за сентябрь 2026")
    has("штраф — амнистия", pn.cancelled(it, "Старший"), "Амнистия: штраф 200 AED снят", "Превышение", "Амнистию дал: Старший")
    eq("у штрафа нет слова «отменён»", "отменён" in pn.cancelled(it, "Старший"), False)
    has("удержание отменено (род)", pn.cancelled({**it, "kind": "hold"}, ""), "Удержание 200 AED отменено")
    has("пересмотр суммы", pn.edited(it, {**it, "amount": 100}, "С"), "Штраф пересмотрен: 200 → 100 AED", "Пересмотрел: С")
    has("пересмотр без суммы", pn.edited(it, {**it, "per_month": 50}, ""), "Штраф 200 AED: изменены график или комментарий", "по 50 AED в месяц")
    has("возвращён", pn.restored(it, "С"), "Штраф 200 AED возвращён", "Вернул: С")
    has("зарплата выплачена", pn.payout(3500, "2026-09-14", "2026-08", "Зарплата", "С"), "Зарплата 3500 AED", "за август 2026", "выплачена 14 сентября", "Выдал: С")
    eq("html в причине экранирован", "&lt;b&gt;" in pn.added({**it, "reason": "<b>x</b>"}, ""), True)

    print("— доставка")
    SENT.clear(); REG.clear()
    n = await pn.tell("Худоба", "т")
    eq("водителю ушло и записано в реестр", (n, SENT[0][0], REG), (1, 555, [(555, 1)]))
    eq("оператору (нет бота водителя) — ничего", await pn.tell("Умар", "т"), 0)
    PANIC["Худоба"] = True; SENT.clear()
    eq("скрытый режим — молчим", (await pn.tell("Худоба", "т"), SENT), (0, []))
    PANIC["Худоба"] = False

    print("— ручки зарплат шлют сообщения")
    SENT.clear()
    st, r = await call(fin.handle_pay_item_add, {"name": "Худоба", "kind": "fine", "amount": 200, "reason": "Опоздание", "as": "Парвиз", "day": "2026-09-14"})
    iid = r.get("id")
    eq("штраф записан и ушёл водителю", (st, len(SENT), SENT[-1][0]), (200, 1, 555))
    has("текст штрафа", SENT[-1][1], "Штраф 200 AED", "Опоздание", "Назначил: Парвиз")
    st, r = await call(fin.handle_pay_item_edit, {"id": iid, "amount": 150, "as": "Парвиз"})
    has("пересмотр ушёл", SENT[-1][1], "пересмотрен: 200 → 150 AED")
    st, r = await call(fin.handle_pay_item_del, {"id": iid, "as": "Парвиз"})
    has("амнистия ушла", SENT[-1][1], "Амнистия: штраф 150 AED снят", "Амнистию дал: Парвиз")
    st, r = await call(fin.handle_pay_item_del, {"id": iid, "as": "Парвиз"})
    eq("повторная отмена — без второго сообщения", len(SENT), 3)
    st, r = await call(fin.handle_pay_item_restore, {"id": iid, "as": "Парвиз"})
    has("возврат ушёл", SENT[-1][1], "Штраф 150 AED возвращён")
    st, r = await call(fin.handle_pay_item_add, {"name": "Худоба", "kind": "advance", "amount": 500, "as": "Парвиз", "day": "2026-09-14"})
    has("аванс ушёл", SENT[-1][1], "Аванс 500 AED — выдан 14 сентября")
    st, r = await call(fin.handle_pay_item_del, {"id": r.get("id"), "as": "Парвиз"})
    has("аванс убран — ушло", SENT[-1][1], "Аванс 500 AED убран из учёта")
    st, r = await call(fin.handle_pay_out, {"name": "Худоба", "amount": 3000, "day": "2026-09-14", "month": "2026-08", "as": "Парвиз"})
    has("зарплата ушла", SENT[-1][1], "Зарплата 3000 AED", "за август 2026")
    before = len(SENT)
    st, r = await call(fin.handle_pay_item_add, {"name": "Умар", "kind": "fine", "amount": 50, "as": "Парвиз", "day": "2026-09-14"})
    eq("штраф оператору записан, сообщений нет (нет бота водителя)", (st, len(SENT)), (200, before))
    # ошибка отправки не ломает операцию
    async def boom(*a, **k): raise RuntimeError("tg down")
    api_server.tg_send = boom
    st, r = await call(fin.handle_pay_item_add, {"name": "Худоба", "kind": "hold", "amount": 70, "as": "Парвиз", "day": "2026-09-14"})
    eq("телеграм лежит — удержание всё равно записано", (st, r.get("ok")), (200, True))
    print()
    print("FAILED:", fails) if fails else print("ALL OK — сообщения водителю о деньгах")
    sys.exit(1 if fails else 0)
asyncio.run(main())
