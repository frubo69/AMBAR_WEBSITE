"""Бюджет нового месяца сам переезжает из прошлого (владелец, 2 окт 2026: «у нас
каждый месяц бюджет одинаковый»). Раньше месяц начинался пустым; нажали «По
образцу» вместо «Из прошлого месяца» — и октябрь остался без единой суммы.

    python3 tools/test_budget_carry.py
"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_finance as FF                                          # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

СЕГОДНЯ = ["2026-09-30"]


async def main():
    db, fr = await FF._сервер_готовь()
    fr._biz_day = lambda *a, **k: СЕГОДНЯ[0]
    сент = [("Аренда офис", 26000, "rent"), ("Гараж и ТО", 10000, "auto"), ("Визы", 11000, ""),
            ("Мониторинг чатов", 440, "ads"), ("Бензин", 0, "")]
    for i, (name, plan, group) in enumerate(сент):
        await db.fin_budget_set({"_id": f"s{i}", "month": "2026-09", "ord": i, "name": name, "plan": plan,
                                 "group": group, "due": 5 if group == "rent" else 0, "by": "владелец"})
    await db.fin_budget_set({"_id": "sal", "month": "2026-09", "ord": 9, "name": "Зарплаты", "plan": 0, "kind": "salary"})
    async def статьи(m): return [(l["name"], l["plan"]) for l in await db.fin_budget_get(m)]

    print("Пока идёт сентябрь — октябрь не трогаем")
    await fr.build("2026-10", light=True)
    eq("будущий месяц пуст", await статьи("2026-10"), [])

    print("\nНаступил октябрь: первое же открытие книги переносит бюджет")
    СЕГОДНЯ[0] = "2026-10-01"
    b = await fr.build("2026-10", light=True)
    eq("статьи и суммы — как в сентябре, без старой строки «Зарплаты»",
       sorted(x for x in await статьи("2026-10") if x[0] != "Что-то ещё"), sorted((n, p) for n, p, _ in сент))
    eq("срок платежа и раздел переехали", [(l["group"], l["due"]) for l in await db.fin_budget_get("2026-10") if l["name"] == "Аренда офис"], [("rent", 5)])
    eq("книга месяца видит план", b["budget"]["empty"], False)
    n = len(await db.fin_budget_get("2026-10"))
    await asyncio.gather(*[fr.build("2026-10", light=True) for _ in range(5)])
    eq("повторные и одновременные открытия ничего не задваивают", len(await db.fin_budget_get("2026-10")), n)
    eq("сентябрь остался как был", len(await db.fin_budget_get("2026-09")), 6)

    print("\nВычистили бюджет руками — заново он не появляется")
    for l in await db.fin_budget_get("2026-10"):
        await db.fin_budget_del(l["_id"])
    await fr.build("2026-10", light=True)
    eq("пусто и остаётся пустым", await статьи("2026-10"), [])
    код, r = await FF._зови(fr.handle_budget_fill, "POST", {"month": "2026-10", "from": "prev", "as": "Т"})
    eq("а кнопка «Из прошлого месяца» по-прежнему работает", (код, r.get("n")), (200, 5))

    print("\nМесяц, где статьи уже есть, не трогаем; прошлый пустой — тоже")
    СЕГОДНЯ[0] = "2026-11-01"
    await db.fin_budget_set({"_id": "n1", "month": "2026-11", "ord": 0, "name": "Своя статья", "plan": 77})
    await fr.build("2026-11", light=True)
    eq("ноябрь со своей статьёй не перезаписан", [x for x in await статьи("2026-11") if x[0] != "Что-то ещё"], [("Своя статья", 77)])
    await fr.build("2026-08", light=True)
    eq("прошедший август задним числом не заполняется", await статьи("2026-08"), [])

    print("\nВ прошлом месяце бюджета не было — переносить нечего, кнопки остаются")
    СЕГОДНЯ[0] = "2027-03-01"
    b = await fr.build("2027-03", light=True)
    eq("пусто", (await статьи("2027-03"), b["budget"]["empty"]), ([], True))

    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
