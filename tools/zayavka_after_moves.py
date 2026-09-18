"""Заявка на сегодня с учётом перемещений (владелец, 18 сен 2026).

Заявка считается «норма − остаток» и про перемещения между районами не знает:
это две независимые заявки. Пока водители не развезли бутылки, она просит
купить и то, что сегодня приедет от соседа, — а это 220 единиц на 19 640 AED
дважды.

Инструмент вписывает в заявку СЕГОДНЯШНЕГО дня ручные правки — ровно те, что
владелец поставил бы руками: столько, сколько останется купить после того, как
перемещения будут сделаны. Правка заявки живёт один день и нормы не трогает,
поэтому завтра заявка снова посчитается сама — и уже правильно, потому что
бутылки к тому времени физически переедут и остаток это покажет.

Прежние правки дня снимаются: они сделаны под старую норму.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/zayavka_after_moves.py           # что впишется
    PYTHONPATH=/opt/ambar venv/bin/python tools/zayavka_after_moves.py --apply   # вписать
    PYTHONPATH=/opt/ambar venv/bin/python tools/zayavka_after_moves.py --clear   # снять правки
"""
import asyncio, math, sys


async def figure(db, sr, mv) -> dict:
    """Сколько останется купить после перемещений — по каждой клетке."""
    cat = sr._catalog()
    day = sr._biz_day()
    sr.base_drop()
    base = await sr._district_base(day)
    norms = await db.get_stock_norms()
    from config_offices import OFFICE_IDS
    stock = {}
    for key, v in norms.items():
        oid, _, pid = key.partition(":")
        if oid in OFFICE_IDS and pid in cat:
            stock[(oid, pid)] = float(((base[oid].get("have_exact") or {}).get(pid)) or 0)
    plan = await mv.plan(day)
    for r in plan["rows"]:                       # бутылки уедут — учитываем заранее
        stock[(r["to"], r["id"])] = stock.get((r["to"], r["id"]), 0) + float(r["qty"])
        stock[(r["from"], r["id"])] = stock.get((r["from"], r["id"]), 0) - float(r["qty"])
    want = {}
    for key, v in norms.items():
        oid, _, pid = key.partition(":")
        if (oid, pid) not in stock:
            continue
        want[(oid, pid)] = max(0, math.ceil(round(float(v) - stock[(oid, pid)], 6)))
    return {"day": day, "want": want, "plan": plan}


async def run(db, sr, mv, apply: bool, say=print) -> dict:
    f = await figure(db, sr, mv)
    day, want = f["day"], f["want"]
    data = await sr.order_rows(day)
    calc = {(oid, r["id"]): c["calc"] for r in data["all_rows"] for oid, c in r["cells"].items()}
    было = await db.zayavka_edits(day)
    diff = {k: v for k, v in want.items() if calc.get(k, 0) != v}
    say(f"день {day}; перемещений в расчёте {len(f['plan']['rows'])} строк "
        f"на {f['plan']['qty']} ед")
    say(f"заявка сейчас: {data['total_qty']} ед (правок в ней {sum(len(x) for x in было.values())})")
    say(f"после перемещений надо купить: {sum(want.values())} ед; правок впишется {len(diff)}")
    if not apply:
        say("пробный прогон: ничего не записано (--apply — вписать)")
        return {"ok": True, "dry": True, "diff": len(diff)}
    await db.zayavka_edit_clear(day)              # прежние правки — под старую норму
    for (oid, pid), v in diff.items():
        await db.zayavka_edit_set(day, pid, oid, v)
    sr.base_drop()
    after = await sr.order_rows(day)
    say(f"записано правок: {len(diff)}; заявка стала {after['total_qty']} ед "
        f"({after['edited_count']} строк с правкой)")
    return {"ok": True, "qty": after["total_qty"], "edits": len(diff)}


async def main():
    sys.path.insert(0, "/opt/ambar")
    import db, stock_routes as sr, move_routes as mv
    await db.connect()
    if "--clear" in sys.argv:
        await db.zayavka_edit_clear(sr._biz_day())
        sr.base_drop()
        print("правки дня сняты")
        return
    res = await run(db, sr, mv, apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
