"""Демо водителя не отстаёт от приложения (18 сен 2026).

Демо (demo_page.py + driver/demo.js) собирается из живого driver/index.html.
Сломать его можно двумя способами: переименовать в приложении кусок, который
demo_page подменяет, и добавить в приложение новую ручку, о которой demo.js не
знает — тогда экран в демо промолчит и покажет пустоту.

Проверяем оба: сборку и список ручек. Запуск: python3 tools/test_demo_page.py
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import demo_page

fails = []


def ok(name, cond, got=""):
    print(("  ok  " if cond else "FAIL ") + name + (f"   {got}" if got and not cond else ""))
    if not cond:
        fails.append(name)


src = open(os.path.join(ROOT, "driver", "index.html"), encoding="utf-8").read()
js = open(os.path.join(ROOT, "driver", "demo.js"), encoding="utf-8").read()
page = demo_page.build(src, "?v=1")

print("— сборка страницы")
ok("телеграм остаётся настоящим", "telegram-web-app.js" in page)
ok("заглушка телеграма только вне телеграма", "INSIDE" in js and "platform !== 'unknown'" in js)
ok("api.js убран", '<script src="api.js">' not in page)
ok("demo.js подключён", '/driver/demo.js?v=1' in page)
ok("boot() → demoBoot()", "\ndemoBoot();" in page and "\nboot();" not in page)
ok("картинки по корневому пути", '"/driver/img/empty-orders.png"' in page and '"img/' not in page)
ok("иконка на экран Домой", "apple-touch-icon" in page and "demo-manifest.json" in page)
ok("имя демо", "<title>AMBAR · Демо</title>" in page)
ok("страница целая", len(page) > len(src) - 2000)

print("— ручки приложения известны демо")
paths = set(re.findall(r"drvFetch\('(/api/driver/[^']+)'", src))
tmpl = set(re.findall(r"drvFetch\(`(/api/driver/[^`]+)`", src))
miss = sorted(p for p in paths if f"'{p}'" not in js)
ok(f"простые пути ({len(paths)})", not miss, "нет ответа на: " + ", ".join(miss))
tails = set()
for t in tmpl:
    for part in re.sub(r"\$\{[^}]*\}", "·", t).split("/"):
        if part and part != "·" and part not in ("api", "driver"):
            tails.add(part)
miss2 = sorted(t for t in tails if t not in js)
ok(f"пути с подстановкой ({len(tails)} хвостов)", not miss2, "нет ответа на: " + ", ".join(miss2))

print("— demo.js синтаксически цел")
try:
    r = subprocess.run(["node", "--check", os.path.join(ROOT, "driver", "demo.js")],
                       capture_output=True, text=True, timeout=60)
    ok("node --check", r.returncode == 0, (r.stderr or "").strip()[:200])
except FileNotFoundError:
    print("  —   node не найден, проверку синтаксиса пропускаем")

print()
print("FAILED:", fails) if fails else print("ALL OK — демо водителя")
sys.exit(1 if fails else 0)
