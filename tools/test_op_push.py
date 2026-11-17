"""Заказ операторам: всем сразу и с повтором, если телеграм не принял (владелец,
17 ноя 2026: на «🛑 Заказ #AMB38119B5D НЕ доставлен ни одному оператору!» —
«это что ещё за прикол новый» → на «Чинить: слать операторам сразу всем и
повторять, если Telegram не принял?» — «Да, чини»).

Что было 17 ноя в 21:55 по Дубаю. Телеграм одиннадцать раз подряд ответил
операторскому боту «Bad Gateway», каждый ответ шёл по 15 секунд. Рассылка шла
по очереди и внутри запроса клиента: заказ простоял 2 мин 50 с, приложение
клиента всё это время ждало ответа, владельцу сторож написал только после
последней попытки, повторять было некому. Клиент отменил заказ и оформил заново.

Проверяем настоящий op_route (send, send_order) поверх базы в памяти; телеграм
подменён сценарием: кто, когда и что отвечает.

  A. Обычный день: трое операторов, каждый ответ идёт 0,2 с — заказ уходит за
     одну такую паузу, а не за три; номера карточек записаны в заказ.
  B. Сбой как 17 ноя: телеграм держит запрос и отвечает «Bad Gateway» всем.
     Запрос клиента возвращается через FIRST_WAIT, а не через сумму ожиданий;
     в фоне идут повторы; телеграм ожил — карточки дошли, номера записаны,
     владельцам ушла одна строка «дошёл».
  C. Дошло не всем: недошедшему повторяем, заблокировавшему бота — нет;
     владельцам ничего (сторож не срабатывал).
  D. Заказ приняли из приложения или клиент отменил — повторы прекращаются:
     карточка «новый заказ» уже не нужна. Отправка, которая в этот момент уже
     летела и дошла, — убирается из чата и в заказ не пишется.
  E. Телеграм ответил «дошло», но поздно (после FIRST_WAIT): номер записан,
     второй раз в этот чат не шлём — двух карточек у оператора не будет.
  F. Таймаут и «слишком часто» повторяются, «неверный запрос» — нет.
  G. Телеграм так и не ожил: попыток ровно 1 + len(RETRY_AT), потом тишина.
  H. Прочие рассылки операторам (поздняя смена, «на месте», сверка, ревизия):
     тоже всем сразу, ждём всех, без повторов; «без кнопок» работает как раньше.
  I. Тест-заказ — только тест-операторам; владельцам о нём не пишем.
  J. Готовый список чатов (заказ после верификации) — ровно ему.
  K. api_server зовёт send_order в обоих местах, очередей по операторам там
     больше нет, слова сторожа прежние.
  L. Настоящий обработчик заказа после верификации (handle_verify_request) при
     сбое телеграма: отвечает клиенту через FIRST_WAIT, сторож пишет владельцам
     сразу, карточки доходят с повтора, заказ снят с ожидания верификации.
  M. Настоящий обработчик нового заказа (handle_create_order) при том же сбое:
     клиент получает ответ через FIRST_WAIT, сторож — сразу, карточки — с
     повтора, владельцам вторая строка. Это и есть вечер 17 ноя.

    python3 tools/test_op_push.py
"""
import asyncio, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MONGO_URI"] = ""
os.environ["AMBAR_OWNER_IDS"] = "1"
os.environ["OPERATOR_IDS"] = "7,8,9"            # старшие: получают всё
os.environ["AMBAR_OPERATOR_IDS"] = ""           # районных операторов с id нет — маршрут «всем»
os.environ["OPERATOR_BOT_TOKEN"] = "x"; os.environ["DRIVER_BOT_TOKEN"] = "x"
os.environ["AMBAR_OWNER_BOT_TOKEN"] = "x"; os.environ["BOT_TOKEN"] = "x"
os.environ["AMBAR_TEST_OPERATOR_IDS"] = "77"
for k in ("AMBAR_TEST_ORDER_IDS", "AMBAR_TEST_DRIVER_IDS", "AMBAR_TEST_IDS"):
    os.environ.pop(k, None)
logging.disable(logging.CRITICAL)
import json                                                        # noqa: E402
from mongomock_motor import AsyncMongoMockClient                   # noqa: E402
from aiohttp.test_utils import make_mocked_request                 # noqa: E402
import db, api_server, op_route, owner_routes as owr               # noqa: E402

FAIL = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def лт(имя, дали, меньше):
    ок = дали < меньше
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали:.2f} с" + ("" if ок else f" — не меньше {меньше}"))
    if not ок: FAIL.append(имя)


ОТКАЗ = {"502": (502, "Bad Gateway"), "429": (429, "Too Many Requests: retry after 3"),
         "403": (403, "Forbidden: bot was blocked by the user"),
         "400": (400, "Bad Request: can't parse entities")}


class Телеграм:
    """Сценарий ответов. script(чат, шаг, шаг…): шаг — ("ok" | "502" | "429" | "403" |
    "400" | "raise", пауза). Последний шаг повторяется. Без сценария — «дошло» сразу."""
    def __init__(self):
        self.plan, self.calls, self.now, self.peak, self.mid = {}, [], 0, 0, 100

    def script(self, чат, *шаги):
        self.plan[чат] = list(шаги)

    def сколько(self, чат):
        return sum(1 for c in self.calls if c[0] == чат)

    async def __call__(self, token, chat_id, text, parse_mode=None, reply_markup=None, **k):
        чат = int(chat_id)
        self.calls.append((чат, bool(reply_markup), str(text)[:24]))
        self.now += 1; self.peak = max(self.peak, self.now)
        try:
            шаги = self.plan.get(чат) or [("ok", 0)]
            шаг = шаги.pop(0) if len(шаги) > 1 else шаги[0]
            await asyncio.sleep(шаг[1] if len(шаг) > 1 else 0)
            if шаг[0] == "ok":
                self.mid += 1
                return {"ok": True, "result": {"message_id": self.mid}}
            if шаг[0] == "raise":
                raise asyncio.TimeoutError()
            код, слова = ОТКАЗ[шаг[0]]
            return {"ok": False, "error_code": код, "description": слова}
        finally:
            self.now -= 1


ТГ = None
ВЛАДЕЛЬЦАМ = []
УБРАНО = []


async def _убрать(token, chat_id, message_id):
    УБРАНО.append((int(chat_id), message_id))
api_server.tg_delete = _убрать


async def _владельцам(event_key, text, *a, **k):
    ВЛАДЕЛЬЦАМ.append((event_key, text))
owr.notify_owners_force = _владельцам


async def заново(oid="AMB1", status="pending", wait=0.3, retry=(0.1, 0.25, 0.45)):
    """Чистый стол: база, сценарий телеграма, короткие сроки (в бою 8 с и до 10 мин)."""
    global ТГ
    await фон()
    db._db = AsyncMongoMockClient()["op_push"]
    await db._db.orders.insert_one({"order_id": oid, "status": status, "total": 200})
    ТГ = Телеграм()
    api_server.tg_send = ТГ
    ВЛАДЕЛЬЦАМ.clear(); УБРАНО.clear()
    op_route.FIRST_WAIT, op_route.RETRY_AT = wait, tuple(retry)
    return oid


async def фон():
    """Дождаться, пока фоновые повторы закончатся сами."""
    while op_route._BG:
        await asyncio.wait(list(op_route._BG))


async def карточки(oid):
    return dict(sorted(((await db.get_order(oid)) or {}).get("op_msg_ids", {}).items()))


async def main():
    loop = asyncio.get_running_loop()

    print("── A. обычный день ─────────────────────────────────────────")
    oid = await заново(wait=1.0)                      # срок ожидания с запасом: меряем не его
    for c in (7, 8, 9): ТГ.script(c, ("ok", 0.2))
    t = loop.time()
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses", reply_markup={"inline_keyboard": []})
    dt = loop.time() - t
    eq("A. дошло всем троим, по порядку маршрута", list(out), ["7", "8", "9"])
    лт("A. заказ ушёл за одну паузу телеграма, а не за три (по очереди было бы 0,6 с)", dt, 0.45)
    eq("A. слали одновременно", ТГ.peak, 3)
    await фон()
    eq("A. номера карточек в заказе", await карточки(oid), {"7": out["7"], "8": out["8"], "9": out["9"]})
    eq("A. повторов нет, владельцам ничего", (len(ТГ.calls), ВЛАДЕЛЬЦАМ), (3, []))

    print("── B. сбой как 17 ноя: «Bad Gateway» всем ──────────────────────────")
    oid = await заново()
    # первая попытка висит 0,6 с и отказ; первый повтор — отказ; дальше телеграм ожил
    for c in (7, 8, 9): ТГ.script(c, ("502", 0.6), ("502", 0), ("ok", 0))
    t = loop.time()
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    dt = loop.time() - t
    eq("B. за время ожидания не дошло никому — зовущий пишет владельцам сторожа", out, {})
    лт("B. запрос клиента вернулся через FIRST_WAIT 0,3 с, а не через 0,6 × 3 = 1,8 с", dt, 0.55)
    eq("B. в этот момент в заказе карточек нет", await карточки(oid), {})
    await фон()
    к = await карточки(oid)
    eq("B. телеграм ожил — карточки дошли всем, номера в заказе", sorted(к), ["7", "8", "9"])
    eq("B. каждому слали трижды: первая попытка, повтор-отказ, повтор-дошло",
       [ТГ.сколько(c) for c in (7, 8, 9)], [3, 3, 3])
    eq("B. владельцам одна строка, что дошёл", len(ВЛАДЕЛЬЦАМ), 1)
    eq("B. …с попыткой и ключом сторожа", (ВЛАДЕЛЬЦАМ[0][0], "дошёл до операторов" in ВЛАДЕЛЬЦАМ[0][1],
                                          "Попытка 3" in ВЛАДЕЛЬЦАМ[0][1], f"#{oid}" in ВЛАДЕЛЬЦАМ[0][1]),
       ("orders.opFail", True, True, True))

    print("── C. дошло не всем ────────────────────────────────────────")
    oid = await заново()
    ТГ.script(8, ("502", 0), ("ok", 0))
    ТГ.script(9, ("403", 0))
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    eq("C. сразу дошло одному", list(out), ["7"])
    await фон()
    eq("C. второму дошло с повтора, номера обоих в заказе", sorted(await карточки(oid)), ["7", "8"])
    eq("C. первый номер не затёрт вторым", (await карточки(oid))["7"], out["7"])
    eq("C. заблокировавшему бота второй раз не шлём", ТГ.сколько(9), 1)
    eq("C. тому, кому дошло, второй раз не шлём", ТГ.сколько(7), 1)
    eq("C. владельцам ничего: сторож не срабатывал", ВЛАДЕЛЬЦАМ, [])

    print("── D. заказ приняли или отменили — повторы не нужны ───────────────────")
    for статус in ("approved", "cancelled"):
        oid = await заново(retry=(0.1, 0.8, 0.9, 1.0))
        for c in (7, 8, 9): ТГ.script(c, ("502", 0))
        out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
        await asyncio.sleep(0.3)                      # первый повтор прошёл, до второго далеко
        было = len(ТГ.calls)
        await db.update_order(oid, status=статус)
        await фон()
        eq(f"D. {статус}: до смены статуса повторяли", было, 6)
        eq(f"D. {статус}: после — ни одной отправки", len(ТГ.calls), 6)
        eq(f"D. {статус}: карточек нет, владельцам «дошёл» не писали", (await карточки(oid), ВЛАДЕЛЬЦАМ), ({}, []))

    oid = await заново()
    ТГ.script(9, ("ok", 0.5))                         # летит дольше срока ожидания и доходит
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    await db.update_order(oid, status="cancelled")    # клиент отменил, пока карточка шла
    await фон()
    eq("D. отменили, пока карточка шла: она убрана из чата", [c for c, _ in УБРАНО], [9])
    eq("D. …и в заказ не записана", sorted(await карточки(oid)), ["7", "8"])
    eq("D. дошедшие вовремя никто не трогал", (sorted(out), ВЛАДЕЛЬЦАМ), (["7", "8"], []))

    print("── E. «дошло», но поздно ────────────────────────────────────")
    oid = await заново()
    ТГ.script(9, ("ok", 0.5))                         # ответ пришёл после FIRST_WAIT 0,3 с
    t = loop.time()
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    dt = loop.time() - t
    eq("E. в ответе двое успевших", list(out), ["7", "8"])
    лт("E. третьего не ждали дольше FIRST_WAIT", dt, 0.45)
    await фон()
    eq("E. номер опоздавшего дописан", sorted(await карточки(oid)), ["7", "8", "9"])
    eq("E. второй раз ему не слали — двух карточек не будет", ТГ.сколько(9), 1)
    eq("E. владельцам ничего, из чата ничего не убирали", (ВЛАДЕЛЬЦАМ, УБРАНО), ([], []))

    print("── F. что повторяем, а что нет ───────────────────────────────")
    oid = await заново()
    ТГ.script(7, ("raise", 0), ("ok", 0))
    ТГ.script(8, ("429", 0), ("ok", 0))
    ТГ.script(9, ("400", 0))
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    await фон()
    eq("F. сразу не дошло никому", out, {})
    eq("F. таймаут и «слишком часто» повторены и дошли", sorted(await карточки(oid)), ["7", "8"])
    eq("F. «неверный запрос» не повторяем", ТГ.сколько(9), 1)
    eq("F. владельцам одна строка «дошёл»", len(ВЛАДЕЛЬЦАМ), 1)

    print("── G. телеграм так и не ожил ───────────────────────────────")
    oid = await заново(retry=(0.05, 0.1, 0.15))
    for c in (7, 8, 9): ТГ.script(c, ("502", 0))
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", district="alguses")
    await фон()
    eq("G. попыток ровно 1 + три повтора на каждого", [ТГ.сколько(c) for c in (7, 8, 9)], [4, 4, 4])
    eq("G. карточек нет, «дошёл» не писали", (await карточки(oid), ВЛАДЕЛЬЦАМ), ({}, []))
    await asyncio.sleep(0.2)
    eq("G. дальше тишина", len(ТГ.calls), 12)

    print("── H. прочие рассылки операторам ────────────────────────────")
    oid = await заново()
    for c in (7, 8, 9): ТГ.script(c, ("ok", 0.2))
    t = loop.time()
    out = await op_route.send("⏰ Поздно открыл смену", district="alguses", retry_plain=True)
    dt = loop.time() - t
    eq("H. дошло всем", list(out), ["7", "8", "9"])
    лт("H. тоже всем сразу", dt, 0.45)
    oid = await заново()
    ТГ.script(8, ("502", 0.5))
    ТГ.script(9, ("ok", 0.5))
    t = loop.time()
    out = await op_route.send("📍 На месте", district="alguses")
    dt = loop.time() - t
    eq("H. без wait ждём всех: опоздавший в ответе, отказавший — нет", list(out), ["7", "9"])
    await фон(); await asyncio.sleep(0.3)
    eq("H. повторов у прочих рассылок нет", (ТГ.сколько(8), len(ТГ.calls)), (1, 3))
    eq("H. заказ они не трогают", await карточки(oid), {})
    oid = await заново()
    ТГ.script(8, ("400", 0), ("ok", 0))
    кнопка = lambda chat: {"inline_keyboard": [[{"text": "Скопировать", "copy_text": {"text": "x"}}]]}
    out = await op_route.send("📍 На месте", district="alguses", reply_markup=кнопка, retry_plain=True)
    eq("H. «без кнопок»: не принял с кнопкой — ушло без неё", (sorted(out), [c[1] for c in ТГ.calls if c[0] == 8]),
       (["7", "8", "9"], [True, False]))

    print("── I. тест-заказ ───────────────────────────────────────────")
    oid = await заново()
    ТГ.script(77, ("502", 0), ("ok", 0))
    out = await op_route.send_order(oid, "🧪 ТЕСТ-ЗАКАЗ", district="alguses", test=True)
    await фон()
    eq("I. только тест-операторам", sorted({c[0] for c in ТГ.calls}), [77])
    eq("I. дошло с повтора, номер в заказе", sorted(await карточки(oid)), ["77"])
    eq("I. владельцам о тест-заказе не пишем", ВЛАДЕЛЬЦАМ, [])

    print("── J. готовый список чатов (после верификации) ─────────────────────────")
    oid = await заново()
    ТГ.script(8, ("502", 0.5), ("ok", 0))
    out = await op_route.send_order(oid, "🆕 ЗАКАЗ", to=[9, 8], reply_markup={"inline_keyboard": []})
    eq("J. ровно этим чатам, в их порядке", ([c[0] for c in ТГ.calls], list(out)), ([9, 8], ["9"]))
    await фон()
    eq("J. недошедшему повторили", sorted(await карточки(oid)), ["8", "9"])

    print("── K. как это подключено ────────────────────────────────────")
    src = open(os.path.join(ROOT, "api_server.py"), encoding="utf-8").read()
    eq("K. новый заказ и заказ после верификации идут через send_order", src.count("op_route.send_order("), 2)
    eq("K. очереди по операторам с карточкой заказа больше нет",
       ("tg_send(OPERATOR_BOT_TOKEN, op_id, combined" in src, "await op_route.send(op_text" in src), (False, False))
    eq("K. слова сторожа прежние", (src.count("НЕ доставлен ни одному оператору!*"),
                                   src.count("Он висит в списке «Новые заказы», но пуш не дошёл.")), (2, 2))
    eq("K. в бою ждём 8 с и повторяем до 10 минут",
       ("FIRST_WAIT = 8.0" in open(os.path.join(ROOT, "op_route.py"), encoding="utf-8").read(),
        "RETRY_AT = (5, 15, 35, 65, 125, 185, 245, 365, 485, 605)" in
        open(os.path.join(ROOT, "op_route.py"), encoding="utf-8").read()), (True, True))

    print("── L. настоящий обработчик: заказ после верификации ──────────────────")
    oid = await заново(oid="AMBV1")
    await db._db.orders.update_one({"order_id": oid}, {"$set": {
        "customer_id": 5, "pending_verification": True, "op_text": f"🆕 <b>НОВЫЙ ЗАКАЗ #{oid}</b>"}})
    for c in (7, 8, 9): ТГ.script(c, ("502", 0.6), ("ok", 0))
    api_server.validate_init_data = lambda s: {"id": 5, "first_name": "Тест", "username": "t"}
    async def _никому(*a, **k): return []
    owr.notify_owners = _никому
    req = make_mocked_request("POST", "/api/verify-request")
    async def _тело(): return {"initData": "x", "source": "friend", "recommender_name": "Тест-знакомый"}
    req.json = _тело
    t = loop.time()
    res = await api_server.handle_verify_request(req)
    dt = loop.time() - t
    eq("L. клиенту ответили", (res.status, json.loads(res.body).get("ok", json.loads(res.body))), (200, True))
    лт("L. …через FIRST_WAIT 0,3 с, а не через 0,6 × 3 = 1,8 с", dt, 0.6)
    eq("L. сторож написал владельцам сразу", [(k, "(после верификации) НЕ доставлен ни одному оператору" in t_)
                                              for k, t_ in ВЛАДЕЛЬЦАМ], [("orders.opFail", True)])
    o = await db.get_order(oid)
    eq("L. заказ снят с ожидания верификации, карточек пока нет", (o.get("pending_verification"), o.get("op_msg_ids")), (False, None))
    await фон()
    eq("L. с повтора карточки дошли всем старшим", sorted(await карточки(oid)), ["7", "8", "9"])
    eq("L. владельцам вторая строка — дошёл", [("дошёл до операторов" in t_) for _, t_ in ВЛАДЕЛЬЦАМ], [False, True])
    eq("L. карточка с кнопками верификации", ТГ.calls[0][1], True)

    print("── M. настоящий обработчик: новый заказ ───────────────────────────")
    await заново()
    import copy, tobacco, config_offices, config_gift, geo_watch
    ЗАКАЗЫ = {}
    async def get_order(oid): return copy.deepcopy(ЗАКАЗЫ.get(oid))
    async def save_order(oid, data): ЗАКАЗЫ[oid] = copy.deepcopy(data)
    async def update_order(oid, **kw):
        o = ЗАКАЗЫ.setdefault(oid, {})
        for k, v in kw.items():                      # как $set в монге: ключ с точкой — вложенное поле
            if "." in k:
                a, b = k.split(".", 1); o.setdefault(a, {})[b] = v
            else:
                o[k] = v
    async def get_user(uid): return {"telegram_id": uid, "verified": True, "phone_verified": "+971500000000", "orders_total": 3}
    async def _none(*a, **k): return None
    async def _false(*a, **k): return False
    async def _empty(*a, **k): return {}
    async def _list(*a, **k): return []
    async def _zero(*a, **k): return 0.0
    async def _day(*a, **k): return "2026-11-17"
    async def _far(*a, **k): return "2999-01-01"
    for n, f in dict(get_order=get_order, save_order=save_order, update_order=update_order, get_user=get_user,
                     get_all_orders=_empty, get_orders_in_range=_list, _increment_user=_none, save_address=_none,
                     add_debt=_none, insert_notification=_none, get_driver_day=_none, save_driver_day=_none,
                     order_day_now=_day, driver_gate_since=_far, get_debt=_zero, is_banned=_false,
                     upsert_user=_none, set_user_field=_none, verify_user=_none, staff_map_get=_empty,
                     driver_map_get=_empty, geo_lock_get=_none, panic_get=_false, debts_of=_empty,
                     shifts_for_day=_empty, drv_msg_add=_none, get_owners_subscribed_to=_list,
                     get_all_manager_ids=_list, is_debt_allowed=_false, is_free_allowed=_false,
                     award_referral_points=_none, get_user_orders=_list).items():
        setattr(db, n, f)
    КАТ = {"gin": {"id": "gin", "name": "Джин", "cat": "Джин", "price": 200, "price_full": 200, "stock": True}}
    api_server._load_catalog_by_id = lambda: КАТ
    api_server._rate_limited = lambda *a, **k: False
    tobacco.order_blocked = lambda *a, **k: False
    config_offices.resolve_office = lambda src: ("alguses", "Алгусес", "geo")
    config_gift.apply = lambda items, sub, cat: items
    async def _сумма(items, tip=0): return float(sum(i.get("price", 0) * i.get("qty", 1) for i in items) + (tip or 0))
    async def _товар(items): return float(sum(i.get("price", 0) * i.get("qty", 1) for i in items))
    api_server._recompute_order_total_aed = _сумма; api_server._goods_subtotal_aed = _товар
    geo_watch.geo_bot_link = _none
    if hasattr(api_server, "render_customer_card"):
        api_server.render_customer_card = lambda o, lang="ru": "card"
    async def _новый(*a, **k): return None
    owr.notify_new_order = _новый
    api_server.validate_init_data = lambda s: {"id": 2, "first_name": "Тест-клиент", "username": "t"}
    for c in (7, 8, 9): ТГ.script(c, ("502", 0.6), ("502", 0), ("ok", 0))
    тело = {"initData": "x", "items": [{"id": "gin", "name": "Джин", "qty": 1, "price": 200, "line_total": 200}],
            "phone": "+971500000000", "address": "ул. Тест 1", "office_id": "alguses", "lang": "ru"}
    req = make_mocked_request("POST", "/api/order"); req._read_bytes = json.dumps(тело).encode()
    t = loop.time()
    res = await api_server.handle_create_order(req)
    dt = loop.time() - t
    ответ = json.loads(res.text)
    oid = ответ.get("order_id") or next(iter(ЗАКАЗЫ), "")
    eq("M. клиенту ответили, заказ создан", (res.status, bool(oid), (ЗАКАЗЫ.get(oid) or {}).get("status")), (200, True, "pending"))
    лт("M. …через FIRST_WAIT 0,3 с, а не через 0,6 × 3 = 1,8 с", dt, 0.75)
    eq("M. сторож написал владельцам сразу", [(k, "НЕ доставлен ни одному оператору" in t_, f"#{oid}" in t_)
                                              for k, t_ in ВЛАДЕЛЬЦАМ], [("orders.opFail", True, True)])
    eq("M. карточек в заказе пока нет", (ЗАКАЗЫ[oid].get("op_msg_ids") or {}), {})
    await фон()
    eq("M. с повтора карточки дошли всем, номера в заказе", sorted(ЗАКАЗЫ[oid].get("op_msg_ids") or {}), ["7", "8", "9"])
    eq("M. каждому слали трижды", [ТГ.сколько(c) for c in (7, 8, 9)], [3, 3, 3])
    eq("M. владельцам вторая строка — дошёл, попытка 3", [("дошёл до операторов" in t_, "Попытка 3" in t_) for _, t_ in ВЛАДЕЛЬЦАМ],
       [(False, False), (True, True)])
    первая = next(c for c in ТГ.calls if c[0] == 7)
    eq("M. оператору шла карточка заказа с кнопкой", (первая[1], первая[2].startswith("🏢 Офис")), (True, True))
    eq("M. кроме операторов и клиента никому не слали", sorted({c[0] for c in ТГ.calls} - {7, 8, 9}), [2])

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
