"""Заявка узнаёт строку по номеру, а не по коду (владелец, 27 сен 2026).

Колонку Code убрали из файла: прятать её не вышло — просмотрщик на айфоне не
смотрит ни на hidden, ни на нулевую ширину, и код всё равно видел магазин.
Якорем стал номер позиции: место в обходе полок, 1…126, тот же, что в рабочей
таблице магазина и в СТАРе. Проверено, что номера уникальны.

Самое опасное здесь — заявка, которая ушла ВЧЕРА в старом виде и вернётся
сегодня. Она обязана прочитаться.

  • новый файл разбирается по номеру, количества по районам те же;
  • СТАРЫЙ файл с колонкой Code читается по-прежнему — и читается по коду,
    даже если номера в нём порядковые (1, 2, 3) и в другой позиции;
  • стёрли номер, оставили название — строка находится по названию;
  • номера нет ни в каком виде — строка пропускается, а не лепится к первой
    попавшейся позиции;
  • номер вне списка — в «чужие», а не в чужую строку;
  • файла без «№» и без «Code» не принимаем вовсе.

    python3 tools/test_zayavka_anchor.py
"""
import asyncio, io, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging; logging.disable(logging.CRITICAL)
from mongomock_motor import AsyncMongoMockClient                      # noqa: E402
from openpyxl import load_workbook                                    # noqa: E402
import db, supply_routes as sr, stock_value                           # noqa: E402
from config_stock_order import order_key                              # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

D = ["jvc", "bbay", "silicon", "alguses", "tecom"]
ROWS = [("p1", "Absolut 1 ltr", [3, 2, 1, 0, 4]),
        ("p31", "Heineken 0.33 can", [1, 0, 2, 0, 0]),
        ("p59", "Tanqueray 1 ltr", [0, 1, 0, 2, 0])]
COSTS = {"p1": 35.0, "p31": 79.8, "p59": 120.0}


async def rows(day):
    r = [{"id": i, "name": n, "need_total": sum(q),
          "cells": {o: {"need": v} for o, v in zip(D, q)}} for i, n, q in ROWS]
    return {"day": "2026-09-27", "districts": [{"id": o} for o in D], "rows": r, "all_rows": r}


async def costs(): return dict(COSTS)


class _Field:
    def __init__(self, raw): self.name, self.filename, self._raw = "file", "z.xlsx", raw
    async def read(self, decode=False): return self._raw
class _Reader:
    def __init__(self, raw): self.left = [_Field(raw)]
    async def next(self): return self.left.pop() if self.left else None
class _Req(dict):
    def __init__(self, raw): super().__init__(owner_id=1); self.raw = raw
    async def multipart(self): return _Reader(self.raw)


async def ввезти(raw):
    from owner_auth import require_owner
    f = sr.handle_import
    f = getattr(f, "__wrapped__", f)
    resp = await f(_Req(raw))
    return resp.status, json.loads(resp.text)


def перебрать(raw, правка):
    """Открыть книгу, дать правке поменять её и собрать обратно."""
    wb = load_workbook(io.BytesIO(raw)); правка(wb["Order"])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


async def состав(body):
    """Что легло в поставку: {позиция: {район: количество}}. В ответе ручки
    только счётчик, сами строки — в созданной поставке."""
    s = await db._db.supplies.find_one({"_id": body.get("supply_id")}) or {}
    return {i["id"]: {o: q for o, q in (i.get("by_district") or {}).items() if q}
            for i in (s.get("items") or [])}


async def main():
    db._db = AsyncMongoMockClient()["ambar_anchor"]
    sr._order_rows = rows
    stock_value.cost_map = costs
    raw, _ = await sr._build_book("")

    print("── новый файл: узнаём по номеру ───────────────────────────────")
    ws = load_workbook(io.BytesIO(raw))["Order"]
    eq("колонки Code нет", "Code" in [ws.cell(3, c).value for c in range(2, 12)], False)
    eq("номера постоянные", [ws.cell(r, 2).value for r in range(4, 7)],
       sorted(order_key(i) + 1 for i, _, _ in ROWS))
    st, body = await ввезти(raw)
    eq("импорт прошёл", (st, body.get("ok")), (200, True))
    eq("позиции те же", sorted((await состав(body))), sorted(i for i, _, _ in ROWS))
    eq("количества те же", (await состав(body))["p1"],
       {o: q for o, q in zip(D, ROWS[0][2]) if q})

    print("── СТАРЫЙ файл с колонкой Code ────────────────────────────────")
    # Как выглядела заявка до правки: слева номер ПОРЯДКОВЫЙ, рядом код.
    def состарить(w):
        w.insert_cols(3)
        w.cell(3, 3, "Code")
        for n, (pid, _, _) in enumerate(sorted(ROWS, key=lambda x: order_key(x[0])), 1):
            w.cell(3 + n, 2, n)          # порядковый номер, как было раньше
            w.cell(3 + n, 3, pid)
    старый = перебрать(raw, состарить)
    st, body = await ввезти(старый)
    eq("старый файл прочитался", (st, body.get("ok")), (200, True))
    eq("и разложился ПО КОДУ, а не по номеру", sorted((await состав(body))),
       sorted(i for i, _, _ in ROWS))
    eq("количества не перепутались", (await состав(body))["p1"],
       {o: q for o, q in zip(D, ROWS[0][2]) if q})

    print("── номер стёрли, название осталось ────────────────────────────")
    без_номера = перебрать(raw, lambda w: [w.cell(r, 2, None) for r in range(4, 7)])
    st, body = await ввезти(без_номера)
    eq("нашли по названию", sorted((await состав(body))), sorted(i for i, _, _ in ROWS))

    print("── ни номера, ни названия ─────────────────────────────────────")
    # cell(r, c, None) НЕ стирает: openpyxl присваивает только не-None.
    def стереть(w):
        for r in range(4, 7):
            for c in (2, 3):
                w.cell(r, c).value = None
    пусто = перебрать(raw, стереть)
    st, body = await ввезти(пусто)
    eq("строка пропущена, а не приклеена к первой попавшейся",
       (st, body.get("error")), (400, "nothing_confirmed"))

    print("── номер вне списка ───────────────────────────────────────────")
    def чужой(w):
        w.cell(4, 2, 9999); w.cell(4, 3, "Неизвестная бутылка")
    странный = перебрать(raw, чужой)
    st, body = await ввезти(странный)
    eq("ушёл в чужие, а не в чужую строку",
       ("p1" in (await состав(body)), len(body.get("unknown") or [])), (False, 1))

    print("── файл без ключевой колонки ──────────────────────────────────")
    без_ключа = перебрать(raw, lambda w: w.cell(3, 2, "Nr"))
    st, body = await ввезти(без_ключа)
    eq("не принимаем", (st, body.get("error")), (400, "no_key_column"))

    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
