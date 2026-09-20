"""Отчёт владельца кошелька с назначениями платежей (владелец, 20 сен 2026:
«тот человек, которому принадлежит криптокошелёк, раз в неделю скидывает отчёт
с назначениями платежей — добавь возможность загружать эти отчёты в кошелёк,
чтобы они автоматически сравнивались с платежами и каждый платёж приобретал
своё назначение»).

mongomock + настоящие wallet_routes (TronGrid подменён):
  • разбор: csv с заголовком и без, точка с запятой, табуляция, xlsx;
    числа «1 234,50», «1,234.50», «120 USDT»; даты 20.09.2026 и 2026-09-20;
  • сверка: по хешу — бесспорно; по сумме и дню — если кандидат один;
    две одинаковые суммы в один день — спорное, само не назначается;
    чего нет в кошельке — «не нашли», и это не ошибка;
  • настоящий отчёт: семь листов, шапка не в первой строке, одна и та же
    операция на листе месяца и в сводном — повтор схлопывается, назначение
    собирается из вида операции и описания;
  • комиссия отправителя и округление курса: 98,29 в отчёте против 98,5007 в
    цепочке — тот же платёж, если он в те же минуты;
  • «Вывод USDT» не хватает приход той же суммы: направление важнее близости;
  • трата из остатка («снятие наличных») перевода не имеет — это не «не
    нашли», а отдельный список; направление читается по виду операции, и
    «Вывод USDT · Пополнение VIP ENOC» остаётся расходом;
  • назначение появляется у перевода в ленте кошелька и переживает повторную
    загрузку; рукой его можно поправить и снять."""
import asyncio, base64, io, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, tron, wallet_routes as W

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

DUBAI = timezone(timedelta(hours=4))
ДЕНЬ = datetime(2026, 9, 18, 13, 0, tzinfo=DUBAI)
МС = lambda d: int(d.timestamp() * 1000)
HASH1 = "a" * 64
ПЕРЕВОДЫ = [
    {"txid": HASH1, "amount": 500.0, "in": True, "ts": МС(ДЕНЬ), "peer": "TXa"},
    {"txid": "b" * 64, "amount": 1234.5, "in": True, "ts": МС(ДЕНЬ + timedelta(hours=2)), "peer": "TXb"},
    {"txid": "c" * 64, "amount": 120.0, "in": True, "ts": МС(ДЕНЬ + timedelta(days=1)), "peer": "TXc"},
    {"txid": "d" * 64, "amount": 300.0, "in": True, "ts": МС(ДЕНЬ + timedelta(days=2)), "peer": "TXd"},
    {"txid": "e" * 64, "amount": 300.0, "in": True, "ts": МС(ДЕНЬ + timedelta(days=2, hours=1)), "peer": "TXe"},
]


async def отчёт(имя, raw, who="Владелец"):
    r = make_mocked_request("POST", "/api/owner/wallet/report")
    r["owner_id"] = 1
    r._read_bytes = json.dumps({"name": имя, "data": base64.b64encode(raw).decode(), "as": who}).encode()
    h = W.handle_report
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    resp = await h(r)
    return resp.status, json.loads(resp.text)


async def main():
    db._db = AsyncMongoMockClient()["ambar_wal_rep"]
    async def transfers(addr): return list(ПЕРЕВОДЫ)
    async def balance(addr): return {"usdt": 2454.5, "trx": 100}
    tron.get_transfers, tron.get_balance = transfers, balance
    W.old_addresses = lambda: asyncio.sleep(0, result=[])

    print("── разбор: csv с заголовком ───────────────────────────────────")
    csv = ("Дата;Сумма;Назначение платежа\n"
           "18.09.2026;500;Оплата поставки Барракуда\n"
           "18.09.2026;1 234,50;Возврат клиенту Ахмеду\n"
           "19.09.2026;120 USDT;Реклама, инстаграм\n"
           "25.09.2026;999;Чего в кошельке нет\n").encode()
    st, r = await отчёт("report.csv", csv)
    eq("строк прочитано и назначено", (st, r["rows"], r["matched"]), (200, 4, 3))
    eq("непонятая строка — не ошибка, а список", (r["missed_n"], r["missed"][0]["text"]),
       (1, "Чего в кошельке нет"))
    п = await db.wallet_purposes()
    eq("у перевода на 500 своё назначение", (п.get(HASH1) or {}).get("text"), "Оплата поставки Барракуда")
    eq("«1 234,50» прочитано как 1234.5", (п.get("b" * 64) or {}).get("text"), "Возврат клиенту Ахмеду")
    eq("«120 USDT» тоже", (п.get("c" * 64) or {}).get("text"), "Реклама, инстаграм")

    print("── лента кошелька знает назначения ────────────────────────────")
    v = await W._view("TXaddr")
    стр = {t["txid"]: t for t in v["transfers"]}
    eq("назначение видно в переводе", стр[HASH1]["purpose"], "Оплата поставки Барракуда")
    eq("и посчитано, сколько платежей с назначением", v["totals"]["purposed"], 3)

    print("── спорное: две одинаковые суммы в один день ──────────────────")
    st, r = await отчёт("week.csv", "Дата;Сумма;Назначение\n20.09.2026;300;Аренда склада\n".encode())
    eq("само не назначается, отдаётся человеку", (r["matched"], r["disputed_n"]), (0, 1))
    eq("в спорном — оба кандидата", len(r["disputed"][0]["candidates"]), 2)
    eq("в базе назначения не появилось", len(await db.wallet_purposes()), 3)

    print("── хеш перевода в отчёте — бесспорно ──────────────────────────")
    st, r = await отчёт("hash.csv", f"Хеш;Назначение\n{'d' * 64};Аренда склада, сентябрь\n".encode())
    п = await db.wallet_purposes()
    eq("назначили по хешу, без сумм и дат", (r["matched"], (п.get("d" * 64) or {}).get("text")),
       (1, "Аренда склада, сентябрь"))

    print("── без заголовка и табуляцией ─────────────────────────────────")
    st, r = await отчёт("plain.txt", "20.09.2026\t300\tАренда склада\n".encode("cp1251"))
    eq("строку прочитали, но она спорная — второй кандидат занят? нет, свободен",
       (r["rows"], r["matched"] + r["disputed_n"]), (1, 1))

    print("── xlsx ───────────────────────────────────────────────────────")
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["Дата", "Сумма", "Назначение"])
    ws.append([datetime(2026, 9, 18), 500, "Поставка · исправлено"])
    buf = io.BytesIO(); wb.save(buf)
    st, r = await отчёт("report.xlsx", buf.getvalue())
    п = await db.wallet_purposes()
    eq("xlsx разобран и назначение обновилось", (st, r["matched"], (п.get(HASH1) or {}).get("text")),
       (200, 1, "Поставка · исправлено"))

    print("── рукой: поправить и снять ───────────────────────────────────")
    h = W.handle_purpose
    while hasattr(h, "__wrapped__"): h = h.__wrapped__
    rq = make_mocked_request("POST", "/x"); rq["owner_id"] = 1
    rq._read_bytes = json.dumps({"txid": HASH1, "text": "Поставка, 18 сентября", "as": "Владелец"}).encode()
    await h(rq)
    п = await db.wallet_purposes()
    eq("рукой поправили", ((п.get(HASH1) or {}).get("text"), (п.get(HASH1) or {}).get("src")),
       ("Поставка, 18 сентября", "hand"))
    rq = make_mocked_request("POST", "/x"); rq["owner_id"] = 1
    rq._read_bytes = json.dumps({"txid": HASH1, "text": "", "as": "Владелец"}).encode()
    await h(rq)
    eq("пустой текст — назначение снято", HASH1 in (await db.wallet_purposes()), False)

    print("── настоящий формат: листы, шапка ниже, повтор операции ───────")
    from openpyxl import Workbook as WB2
    wb = WB2()
    ш = ["№", "Дата", "Заказ", "Монета", "Сумма crypto", "Курс AED",
         "Приход AED", "TXID", "Описание"]
    оп = [1, datetime(2026, 9, 18, 15, 0), "Приход от клиента", "USDT",
          120, 3.5, 420, "", "Заказ №12"]
    ws = wb.active; ws.title = "Сентябрь 2026"
    ws.append(["Сентябрь 2026 (1 оп.)"]); ws.append([]); ws.append(ш); ws.append(оп)
    св = wb.create_sheet("Заказы")
    св.append(["Итого по месяцам"]); св.append([]); св.append(ш); св.append(оп)
    buf = io.BytesIO(); wb.save(buf)
    st, r = await отчёт("crypto_wallet_report.xlsx", buf.getvalue())
    п = await db.wallet_purposes()
    eq("операция с двух листов — одна строка", (st, r["rows"], r["matched"]), (200, 1, 1))
    eq("назначение — вид операции и описание", (п.get("c" * 64) or {}).get("text"),
       "Приход от клиента · Заказ №12")

    print("── комиссия, направление, траты ───────────────────────────────")
    Д2 = datetime(2026, 9, 22, 10, 0, tzinfo=DUBAI)
    ПЕРЕВОДЫ.extend([
        {"txid": "f" * 64, "amount": 98.5007, "in": True, "ts": МС(Д2), "peer": "TXf"},
        {"txid": "1" * 64, "amount": 200.0, "in": False,
         "ts": МС(Д2 + timedelta(hours=3, minutes=10)), "peer": "TX1"},
        {"txid": "2" * 64, "amount": 200.0, "in": True,
         "ts": МС(Д2 + timedelta(hours=3, minutes=2)), "peer": "TX2"},
        {"txid": "3" * 64, "amount": 77.0, "in": True, "ts": МС(Д2 + timedelta(hours=6)), "peer": "TX3"},
        {"txid": "4" * 64, "amount": 77.0, "in": True, "ts": МС(Д2 + timedelta(hours=9)), "peer": "TX4"},
    ])
    st, r = await отчёт("week2.csv", (
        "Дата;Операция;Сумма;Описание\n"
        "22.09.2026 10:00;Приход от клиента;98,29;Заказ\n"
        "22.09.2026 13:00;Вывод USDT;200;Снятие наличных\n"
        "22.09.2026 16:00;Приход от клиента;77;Заказ\n"
        "22.09.2026 18:30;Вывод USDT;12,29;Хлеб и чехол\n"
        "22.09.2026 19:00;Вывод USDT;33;Пополнение VIP ENOC\n").encode())
    п = await db.wallet_purposes()
    eq("копейки комиссии не мешают", (п.get("f" * 64) or {}).get("text"),
       "Приход от клиента · Заказ")
    eq("вывод ушёл к исходящему, хотя приход ближе по времени",
       ((п.get("1" * 64) or {}).get("text"), "2" * 64 in п),
       ("Вывод USDT · Снятие наличных", False))
    eq("две одинаковые суммы за день — берём ту, что минута в минуту",
       ((п.get("3" * 64) or {}).get("text"), "4" * 64 in п), ("Приход от клиента · Заказ", False))
    eq("трата из остатка — отдельно от «не нашли»",
       (r["matched"], r["spent_n"], r["spent_sum"], r["missed_n"], r["disputed_n"]),
       (3, 2, 45.29, 0, 0))
    # «Пополнение» в описании вывода — про то, куда ушли деньги. Направление
    # читается по виду операции, иначе расход считался бы приходом.
    eq("«Вывод USDT · Пополнение…» — всё равно расход",
       [x["out"] for x in r["spent"]], [True, True])
    eq("и в списке трат видно, что это расход", (r["spent"][0]["text"], r["spent"][0]["out"]),
       ("Вывод USDT · Хлеб и чехол", True))

    print("── история загрузок ───────────────────────────────────────────")
    ист = await db.wallet_reports()
    # Порядок внутри миллисекунды не проверяем: в бою между отчётами неделя, а
    # здесь семь загрузок укладываются в один миг, и «кто первее» ничего не значит.
    eq("каждая загрузка записана", (len(ист), sorted(x["name"] for x in ист)),
       (7, sorted(["report.csv", "week.csv", "hash.csv", "plain.txt", "report.xlsx",
                   "crypto_wallet_report.xlsx", "week2.csv"])))

    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
