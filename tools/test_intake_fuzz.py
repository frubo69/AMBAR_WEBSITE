"""Приёмка на случайных цепочках — короткий прогон в общем наборе.

Полный — `python3 tools/fuzz_intake.py` (там же, что проверяется). Здесь то же
на постоянных зёрнах, чтобы поломка пути бутылки всплывала при каждом запуске
тестов. Плюс проверка самого фаззера: каждую из ошибок, которые он нашёл
1 окт 2026, и несколько старых он обязан ловить, если их вернуть.

    python3 tools/test_intake_fuzz.py
"""
import asyncio, inspect, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_intake as F                                            # noqa: E402
import db, supply_routes as sr, stock_routes as SR                 # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


F.подготовь()
чистых = sum(1 for k in range(30) if asyncio.run(F.прогон(300 + k, 110)))
eq("30 цепочек по 110 шагов: сервер и модель согласны, ни одна единица не потеряна и не удвоена",
   (чистых, [(b[0], b[1], b[2]) for b in F.БЕДЫ[:2]]), (30, []))
нет = [k for k in F.НАДО if k not in F.ВЕТКИ]
eq("пройдены все ветки (ответ магазина, скан, без сканирования, недовоз, отмена, цепочка баз)", нет, [])


def подмена(mod, имя, a, b):
    """Функция модуля с испорченной строкой. Возвращает прежнюю — вернуть."""
    было = getattr(mod, имя)
    src = inspect.getsource(было)
    assert src.count(a) == 1, (имя, a)
    exec(compile(src.replace(a, b), имя + "_mut", "exec"), mod.__dict__)
    return было


async def _затирает(doc):
    await db._db.supplies.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    return True


print("── фаззер обязан ловить вернувшуюся ошибку ────────────────────")
ПОЛОМКИ = (
    ("две заявки в одну секунду — вторая затирает первую", db, "supply_insert", None, None),
    ("отменённый черновик считается покрытием недобора", sr, "_cover",
     'if ch.get("status") == "cancelled" and not (ch.get("base") or "").strip():', 'if False:'),
    ("повторная загрузка: черновик на весь недобор, хотя докупку уже везут", sr, "_draft_from_gap",
     '(r.get("left_by") or {})', '(r.get("by_district") or {})'),
    ("район с полкоробкой пива отменяется без вопроса", sr, "handle_cancel",
     "sum(float(v or 0) for o, v", "sum(int(v or 0) for o, v"),
    ("подтверждённый недовоз остаётся на складе до истечения кэша", sr, "short_decide",
     "stock_routes.base_drop()", "pass"),
    ("склад «без сканирования» держит то, чего не дали", SR, "_task_miss",
     "return max(0.0, v)", "return 0.0"),
    ("принятое без кодов считается ещё и ждущим на базе", sr, "pending_qty",
     ' or t.get("noscan_at")', ''),
    ("недобор доп. заявки не уходит в следующую", sr, "_draft_from_extra",
     "    if not строки:\n", "    if True:\n"),
    ("водитель закрывает «без сканирования» с остатком", sr, "task_finish",
     "if осталось > 1e-9:", "if False:"),
    ("«без сканирования» без снимка чека", sr, "task_noscan",
     "if not owner and not photo:", "if False:"),
)
for имя, mod, функция, a, b in ПОЛОМКИ:
    if a is None:
        было = getattr(mod, функция); setattr(mod, функция, _затирает)
    else:
        было = подмена(mod, функция, a, b)
    F.БЕДЫ.clear(); F.МИР["тихо"] = True
    for k in range(40):
        asyncio.run(F.прогон(800 + k, 110))
        if F.БЕДЫ:
            break
    eq(f"пойман: {имя}", bool(F.БЕДЫ), True)
    setattr(mod, функция, было)
F.БЕДЫ.clear(); F.МИР["тихо"] = False
eq("после возврата всё снова чисто", asyncio.run(F.прогон(1234, 110)), True)

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
