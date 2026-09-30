"""Где ещё видно уехавших. Зовёт настоящие ручки на живой базе (только чтение)
и ищет в ответах имена тех, кто сейчас уехал (config_staff.AWAY).

Владелец, 30 сен 2026: «НИГДЕ уехавшие водители, кроме того места, где я
указываю, уехал он или приехал, не указываются». Разрешённое место —
«Зарплаты» (книга финансов): там отъезд и отмечают.

    ./venv/bin/python tools/audit_away.py        # на сервере, из /opt/ambar
"""
import asyncio, inspect, json, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
import db, config_staff as staff


def raw(h):
    while hasattr(h, "__wrapped__"):
        h = h.__wrapped__
    return h


class Q(dict):
    """Запрос-заглушка: query, match_info и поля, которые ставит охрана."""
    def __init__(self, query=None, match=None):
        super().__init__(owner_id=1, op_user={"id": 0, "first_name": "проверка"}, tg={"id": 0})
        self.query = query or {}
        self.match_info = match or {}
        self.headers = {}
    async def json(self): return {}


async def main():
    r = db.connect()
    if inspect.isawaitable(r): await r
    await staff.sync(force=True)
    away = sorted(n for n in staff.AWAY if n in staff.all_driver_names())
    print("уехавшие водители:", away or "нет")
    if not away:
        return 0
    import owner_routes as o, operator_routes as p, expense_routes as e, call_routes as c
    import qr_routes as q, stock_value, finance_routes as f, supply_routes as sup
    op = next(iter(staff.DISTRICT_OPERATOR.values()), "")
    day = o._biz_day_start(o.datetime.now(o.DUBAI_TZ)).date().isoformat() if hasattr(o, "_biz_day_start") else ""
    МОЖНО = {"финансы: книга месяца (там отмечают отъезд)",
             # Штраф, который ждёт решения старшего, — это запись о человеке, а
             # не список работающих: убрать её может только само решение.
             "STAR: чек-лист"}
    ЧТО = [
        ("STAR: локатор", o.handle_where, {}),
        ("STAR: команда", o.handle_staff, {}),
        ("STAR: операторы и районы", o.handle_operators, {}),
        ("STAR: обзор по районам", o.handle_finance, {"period": "today"}),
        ("STAR: чек-лист", o.handle_checklist, {}),
        ("STAR: расходы смены", e.handle_day, {"day": day}),
        ("STAR: сбор выручки", getattr(o, "handle_cash_round", None), {"day": day}),
        ("STAR: проверка бутылок — кого проверяем", getattr(q, "handle_checks", None), {}),
        ("STAR: открытые приёмки — кто держит район", sup.handle_list, {}),
        ("оператор: очередь и районы", getattr(p, "handle_queue", None), {"as": op}),
        ("оператор: смена", p.handle_shift, {"as": op}),
        ("оператор: водители на карте", getattr(p, "handle_where", None), {"as": op}),
        ("оператор: итоги смены", p.handle_day_board, {"as": op}),
        ("финансы: книга месяца (там отмечают отъезд)", getattr(f, "handle_book", None), {"month": day[:7]}),
    ]
    плохо = 0
    for имя, h, query in ЧТО:
        if h is None:
            print(f"  —    {имя}: ручки с таким именем нет — пропущено"); continue
        try:
            resp = await raw(h)(Q(query))
            body = getattr(resp, "text", None) or (resp.body.decode() if getattr(resp, "body", None) else "")
            text = json.dumps(json.loads(body), ensure_ascii=False) if body else ""
        except Exception as ex:                          # noqa: BLE001
            print(f"  ?    {имя}: не вызвалась ({type(ex).__name__}: {str(ex)[:70]})"); continue
        if h is sup.handle_list:
            # История закрытых приёмок — кто принимал тогда; ищем только в
            # НЕзакрытых задачах: вот там уехавший держал бы район.
            d = json.loads(body)
            text = json.dumps([t.get("driver") for x in d.get("supplies") or []
                               if x.get("status") in ("open", "draft")
                               for t in (x.get("tasks") or {}).values()], ensure_ascii=False)
        есть = [n for n in away if f'"{n}"' in text or f"{n}," in text or f" {n}" in text]
        if есть and имя not in МОЖНО:
            плохо += 1
        print(f"  {'ok  ' if not есть else ('можно' if имя in МОЖНО else 'ЕСТЬ ')} {имя}" + (f": {есть}" if есть else ""))
    print("\nИТОГ:", "уехавших нигде, кроме разрешённого места, нет" if not плохо else f"уехавшие видны в {плохо} местах")
    return 1 if плохо else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
