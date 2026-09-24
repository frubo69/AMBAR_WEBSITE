"""Прогнать все проверки ТОГО дерева, в котором лежит этот файл.

Так и родилась беда 24 сен 2026: мой прогонщик искал тесты по текущей папке, а
не по проверяемой копии. Я выкладывал слепок индекса, а проверял рабочее
дерево — в нём недостающие функции были, и «87 из 87» ничего не значили.
Теперь тесты берутся рядом с этим файлом и запускаются из его же дерева:
проверяется ровно то, что уедет на сервер.

    python3 tools/run_all.py                 # всё
    python3 tools/run_all.py routes close     # только по подстроке в имени

Перед выкладкой:
    git checkout-index -a -f --prefix=/tmp/snap/ && python3 /tmp/snap/tools/run_all.py
"""
import glob, os, subprocess, sys, time

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
ДЕРЕВО = os.path.dirname(ЗДЕСЬ)
ЛОГИ = os.path.join(ДЕРЕВО, ".run_all")
маски = sys.argv[1:]

os.makedirs(ЛОГИ, exist_ok=True)
тесты = sorted(glob.glob(os.path.join(ЗДЕСЬ, "test_*.py")))
if маски:
    тесты = [t for t in тесты if any(m in os.path.basename(t) for m in маски)]
print(f"дерево: {ДЕРЕВО}\nпроверок: {len(тесты)}\n")

строки = []
for путь in тесты:
    имя = os.path.basename(путь)[:-3]
    t0 = time.time()
    with open(os.path.join(ЛОГИ, имя + ".log"), "wb") as f:
        try:
            код = subprocess.run([sys.executable, путь], stdout=f, stderr=subprocess.STDOUT,
                                 cwd=ДЕРЕВО, timeout=600).returncode
        except subprocess.TimeoutExpired:
            код = "таймаут"
    строки.append((код, имя, round(time.time() - t0, 1)))
    print(("  ok   " if код == 0 else "  ПРОВАЛ "), имя, f"{строки[-1][2]}s", flush=True)

плохие = [r for r in строки if r[0] != 0]
print(f"\nпрошли {len(строки) - len(плохие)} из {len(строки)}")
for код, имя, _ in плохие:
    print(f"  провал: {имя} (код {код}) — {os.path.join(ЛОГИ, имя + '.log')}")
sys.exit(1 if плохие else 0)
