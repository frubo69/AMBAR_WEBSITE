"""Большой прогон случайных сценариев перемещений: процессы параллельно,
зёрна подряд, итог один. Каждый процесс — tools/test_move_fuzz.py со своим
куском зёрен.

Запуск: python3 tools/fuzz_move_run.py [сценариев всего] [процессов] [шагов]
"""
import os, re, subprocess, sys, time

total = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
procs = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, (os.cpu_count() or 2) - 1)
steps = int(sys.argv[3]) if len(sys.argv) > 3 else 80
here = os.path.dirname(os.path.abspath(__file__))
per = (total + procs - 1) // procs
t0 = time.time()
jobs = []
for k in range(procs):
    n = min(per, total - k * per)
    if n <= 0:
        break
    seed0 = 100000 + k * per
    jobs.append(subprocess.Popen([sys.executable, os.path.join(here, "test_move_fuzz.py"), str(n), str(seed0), str(steps)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
checks = scen = 0
fails, stats = [], {}
for j in jobs:
    out, _ = j.communicate()
    m = re.search(r"сценариев (\d+).*проверок (\d+)", out)
    if m:
        scen += int(m.group(1)); checks += int(m.group(2))
    fails += [l for l in out.splitlines() if "FAIL" in l or "Traceback" in l]
    for l in out.splitlines():
        m2 = re.match(r"\s{4}(.+): (\d+)$", l)
        if m2:
            stats[m2.group(1)] = stats.get(m2.group(1), 0) + int(m2.group(2))
for k in sorted(stats):
    print(f"    {k}: {stats[k]}")
print(f"ИТОГ: сценариев {scen}, шагов по {steps}, проверок {checks}, процессов {len(jobs)}, "
      f"{time.time() - t0:.0f} с — " + ("все прошли" if not fails else f"провалено: {len(fails)}"))
for f in fails[:20]:
    print("  ", f)
sys.exit(1 if fails else 0)
