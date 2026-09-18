"""Норма склада правилом, а не таблицей (владелец, 18 сен 2026: «что-то между
снимком 16-го и бумажкой операторов, или что-то более умное»).

Норма каждой клетки «район × позиция» считается из настоящих продаж района и
четырёх ограничителей. Пересчитать — значит запустить этот файл заново: через
две недели он сам поднимет норму там, где продажи выросли.

    1. СПРОС. Сколько район продаёт позицию в день за окно (по умолчанию с
       1 августа — с этого дня операторы забивают продажи сплошь). Умножаем на
       COVER дней и добавляем подушку 1,28·√спрос: разовый крупный заказ не
       должен опустошать полку.

    2. ПОТОЛОК ОПЕРАТОРОВ ×1,5. Их бумажка не масштабируется по скорости: в
       Бизнес Бей Absolut уходит 6,5 бутылки в день, за десять дней это 65, а в
       листе стоит 40. Где спрос доказан продажами — разрешаем до полутора раз
       от их числа; где данных нет — их число остаётся жёстким потолком.

    3. НОЛЬ ОПЕРАТОРА — НОЛЬ. Если в листе прочерк, позиции в районе не будет:
       это их знание о районе, которого в цифрах нет. Таких клеток 37, и продажи
       вопреки прочерку были всего в четырёх — по одной бутылке.

    4. ДОРОГОЕ — ТОЛЬКО ТУДА, ГДЕ ПОКУПАЮТ. Позицию дороже TEST_MAX не ставим в
       район, который её ни разу не купил (владелец: «там, где у нас ноль, живут
       работяги, они этого никогда не купят»). Порог выбран по замеру: на 300
       AED риск упущенных продаж выходит на дно (64 AED в неделю), а каждая
       следующая сотня порога только добавляет денег на полке. Дешевле 300 —
       пробная выкладка в одну штуку: она стоит копейки и отвечает на вопрос,
       не было продаж или не было товара.

    5. ПОЛ ПРИСУТСТВИЯ. Где позиция продаётся — не меньше двух штук (полкоробки
       у пива), чтобы полка не пустела на первом же заказе.

    6. РУЧНАЯ ПРАВКА ВЛАДЕЛЬЦА СИЛЬНЕЕ ВСЕГО. Поверх расчёта ложится
       tools/norm_overrides.py — 393 клетки, которые владелец с операторами
       прошёл глазами 18 сен 2026. Пересчёт их не трогает: решение, принятое по
       знанию района, не должно затираться свежими продажами.

Что даёт против того, что было (замер на продажах августа-сентября, полный
прогон по дням с переездами между районами):

    норма-снимок 16.09   2882 ед  293 982 AED   упущено 6 771 AED в неделю
    бумажка операторов   3316 ед  342 459 AED   упущено   150 AED в неделю
    это правило          2296 ед  196 882 AED   упущено    64 AED в неделю

Снимок теряет больше всех не случайно: он закрепил случайное состояние полки.
Где 16-го чего-то не было, норма стала нулём — и заявка это больше никогда не
просила. Так из неё выпал Carlsberg (ноль во всех пяти районах, восемь коробок
продаж в неделю), Peroni и банки Stella.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/norm_rule.py            # что изменится
    PYTHONPATH=/opt/ambar venv/bin/python tools/norm_rule.py --apply    # записать
    PYTHONPATH=/opt/ambar venv/bin/python tools/norm_rule.py --rollback <копия.json>
"""
import asyncio, json, math, sys, time
from datetime import datetime, timezone

FROM_DAY = "2026-08-01"   # окно спроса: раньше продажи записаны не сплошь
COVER    = 10             # на сколько дней запаса считаем норму
CAP_K    = 1.5            # во сколько раз можно превысить цифру операторов
TEST_MAX = 300            # дороже — в район, который не покупал, не ставим
FLOOR    = 2              # минимум там, где позиция продаётся (у пива — 1 коробка)
Z        = 1.28           # подушка на разброс

try:
    from norms_from_sheet import SHEET          # запуск из tools/
    from norm_overrides import OVERRIDE
except ImportError:                             # запуск из корня
    from tools.norms_from_sheet import SHEET
    from tools.norm_overrides import OVERRIDE


def _step(sr, cat, pid):
    """Шаг нормы: у пива полкоробки, у остального бутылка."""
    return 0.5 if sr._unit(cat.get(pid) or {}) > 1 else 1


async def demand(db, sr, day_from: str = FROM_DAY) -> tuple:
    """{(район, позиция): единиц в день} за окно + число дней в окне."""
    d = db._db_or_none()
    cat = sr._catalog()
    rows = await d.orders.find({"status": "delivered"},
                               {"timestamp": 1, "office_id": 1, "items": 1}).to_list(length=None)
    days, per = set(), {}
    for o in rows:
        try:
            t = datetime.fromisoformat(str(o.get("timestamp"))).replace(
                tzinfo=timezone.utc).astimezone(sr.DUBAI_TZ)
        except (ValueError, TypeError):
            continue
        day = sr._biz_day(t)
        if day < day_from:
            continue
        days.add(day)
        for it in (o.get("items") or []):
            pid, q = it.get("id"), sr._qty(it)
            if not pid or not q:
                continue
            k = (o.get("office_id") or "", pid)
            per[k] = per.get(k, 0.0) + q / sr._unit(cat.get(pid) or {})
    n = max(1, len(days))
    return {k: v / n for k, v in per.items()}, n


def build(sr, rate: dict) -> dict:
    """Норма по правилу. → {(район, позиция): норма}."""
    cat = sr._catalog()
    sells_anywhere = {pid for (_, pid), r in rate.items() if r > 0}
    out = {}
    for oid, per in SHEET.items():
        for pid, cap in per.items():
            p = cat.get(pid) or {}
            step = _step(sr, cat, pid)
            price = sr._price(p)
            r = rate.get((oid, pid), 0.0)
            if not cap:                                   # прочерк операторов
                v = 0.0
            elif r > 0:
                exp = r * COVER
                v = max(math.ceil((exp + Z * math.sqrt(exp)) / step) * step,
                        FLOOR if step == 1 else 1)
                v = min(v, float(cap) * CAP_K)
            elif pid in sells_anywhere and price <= TEST_MAX:
                v = min(float(cap), step)                 # пробная выкладка
            else:
                v = 0.0
            out[(oid, pid)] = math.ceil(v / step) * step
    # Ручная правка владельца сильнее расчёта и переживает пересчёт: она сделана
    # глазами по каждой клетке, и затирать её свежими продажами нельзя.
    for oid, per in OVERRIDE.items():
        for pid, v in per.items():
            if (oid, pid) in out:
                out[(oid, pid)] = float(v)
    return out


async def run(db, sr, apply: bool, backup_dir: str = "/root", say=print) -> dict:
    import stock_value as sv
    cat = sr._catalog()
    cost = await sv.cost_map()
    rate, ndays = await demand(db, sr)
    new = build(sr, rate)
    old = await db.get_stock_norms()
    money = lambda t: sum(v * float(cost.get(p) or 0) for (o, p), v in t.items())
    say(f"окно спроса: с {FROM_DAY}, дней с заказами {ndays}; покрытие {COVER} дн, "
        f"потолок операторов ×{CAP_K}, проба до {TEST_MAX} AED")
    up = dn = zero = 0
    for (oid, pid), v in new.items():
        cur = old.get(f"{oid}:{pid}")
        if cur is None or abs(float(cur) - v) > 1e-9:
            if v == 0: zero += 1
            elif cur is None or v > float(cur): up += 1
            else: dn += 1
    say(f"ручных правок владельца поверх расчёта: {sum(len(v) for v in OVERRIDE.values())}")
    say(f"норма: {sum(new.values()):.0f} ед на {money(new):,.0f} AED "
        f"(было {sum(float(v) for v in old.values()):.0f} ед)".replace(",", " "))
    say(f"клеток: вырастет {up}, упадёт {dn}, обнулится {zero}")
    if not apply:
        say("пробный прогон: ничего не записано (--apply — записать)")
        return {"ok": True, "dry": True, "norm": new}

    path = f"{backup_dir}/norms_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump({"norms": old, "rule": await db.stock_norm_rule_get()},
                  f, ensure_ascii=False, default=str)
    say(f"копия прежних норм: {path}")
    for (oid, pid), v in new.items():
        await db.set_stock_norm(oid, pid, v, 0)
    await db.stock_norm_rule_set({
        "kind": "rule", "day": sr._biz_day(), "cover": COVER, "from": FROM_DAY,
        "cap_k": CAP_K, "test_max": TEST_MAX, "days": ndays,
        "manual": sum(len(v) for v in OVERRIDE.values()),
        "note": f"норма по продажам с {FROM_DAY}: запас на {COVER} дней, "
                f"потолок операторов ×{CAP_K}, дороже {TEST_MAX} AED в район без продаж не ставим",
        "at": datetime.now(timezone.utc).isoformat(), "by": 0})
    sr.base_drop()
    after = await db.get_stock_norms()
    miss = [f"{o}:{p}" for (o, p), v in new.items()
            if abs(float(after.get(f"{o}:{p}", -1)) - v) > 1e-9]
    if miss:
        say(f"ЗАПИСАЛОСЬ НЕ ВСЁ ({len(miss)}) — откатываю")
        await rollback(db, sr, path, say)
        return {"ok": False, "miss": miss, "backup": path}
    say(f"записано клеток: {len(new)}")
    return {"ok": True, "backup": path, "norm": new}


async def rollback(db, sr, path: str, say=print) -> None:
    data = json.load(open(path))
    for key, v in (data.get("norms") or {}).items():
        oid, _, pid = key.partition(":")
        await db.set_stock_norm(oid, pid, v, 0)
    rule = data.get("rule") or {}
    await db.stock_norm_rule_set({k: v for k, v in rule.items() if k != "_id"})
    sr.base_drop()
    say(f"откат: вернул {len(data.get('norms') or {})} норм и прежнее правило")


async def main():
    sys.path.insert(0, "/opt/ambar")
    import db, stock_routes as sr
    await db.connect()
    if "--rollback" in sys.argv:
        await rollback(db, sr, sys.argv[sys.argv.index("--rollback") + 1])
        return
    res = await run(db, sr, apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
