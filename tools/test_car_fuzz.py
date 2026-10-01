"""Пробег и замок смены на случайных цепочках — короткий прогон в общем наборе.

Полный — `python3 tools/fuzz_car.py` (там же, что проверяется). Здесь то же на
постоянных зёрнах, чтобы поломка правила «когда нужно показание» всплывала при
каждом запуске тестов. Плюс проверка самого фаззера: испорченное правило он
обязан ловить.

    python3 tools/test_car_fuzz.py
"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_car as F, car_intake as ci                            # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)

чистых = sum(1 for k in range(25) if asyncio.run(F.прогон(700 + k, 90)) is True)
eq("25 цепочек по 90 событий: сервер и модель согласны, тупиков нет", (чистых, [(b[0], b[1]) for b in F.БЕДЫ[:2]]), (25, []))

print("── короткое первое показание переспрашиваем ───────────────────")
eq("4 км у машины без истории — переспрос", ci.check_km(4, None), (4, "small"))
eq("109 км — тоже", ci.check_km("109", None)[1], "small")
eq("пятизначное — берём сразу", ci.check_km(84210, None), (84210, ""))
eq("у машины с историей правило прежнее: меньше — отказ", ci.check_km(4, {"km": 84210})[1], "less")

print("── фаззер обязан ловить испорченное правило ───────────────────")
src = open(os.path.join(ROOT, "car_intake.py"), encoding="utf-8").read()
for имя, a, b in (
    ("ремонт перестал снимать требование",
     '    if своя.get("repair"):\n        out.update(car=своя, done=моё, repair=своя.get("repair"))\n        return out\n', ''),
    ("смена машины не требует показания", '        return "car"', '        return ""'),
    ("месяц сравнивается с сегодняшним днём", 'был < day[:7] + "-01"', 'был < day'),
):
    assert src.count(a) == 1, имя
    ns = {}
    exec(compile(src.replace(a, b), "car_intake_mut", "exec"), ns)
    было = (ci.state, ci.why_need)
    ci.state, ci.why_need = ns["state"], ns["why_need"]
    F.БЕДЫ.clear()
    for k in range(20):
        asyncio.run(F.прогон(900 + k, 80))
        if F.БЕДЫ:
            break
    eq(f"пойман: {имя}", bool(F.БЕДЫ), True)
    ci.state, ci.why_need = было
F.БЕДЫ.clear()

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
