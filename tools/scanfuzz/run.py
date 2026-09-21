"""Фаззер сканера приёмки в безголовом Chrome (владелец, 21 сен 2026:
«проведи глубокий фаззер… в том числе уже допущенные ошибки с пересбором
сканера и разрешениями на андроиде»).

Настоящие driver/index.html и owner/index.html; подменены только камера (поток
с холста, каждый getUserMedia на счету — на андроиде это запрос разрешения),
распознаватель (отдаёт код, который стенд «держит в кадре») и сервер (модель
правил supply_routes с задержками и обрывами сети). Сначала — сценарии
прошлых поломок, потом случайные цепочки; каждый прогон в свежем браузере.
После каждого шага сверка: камера не пересоздана и не брошена, лишних
запросов камеры нет, в режиме «убрать» не уходят сканы, «убрать» уходит только
из режима или по крестику, числа на экране = сервер, крестики там, где надо,
ошибок JS нет.

    python3 tools/scanfuzz/run.py [сидов] [шагов] [driver|owner ...] [--from=N] [--jobs=K]
"""
import asyncio, json, os, subprocess, sys, tempfile, time, shutil
import aiohttp
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
opt = {a.split("=")[0]: (a.split("=")[1] if "=" in a else "1") for a in sys.argv[1:] if a.startswith("--")}
SEEDS = int(args[0]) if len(args) > 0 else 10
STEPS = int(args[1]) if len(args) > 1 else 40
APPS = args[2:] or ["driver", "owner"]
PORT = int(opt.get("--port", "8791"))
TAG = f"_{os.getpid()}"                      # стенды этого запуска: driver/_fz_<pid>.html
DBG = int(opt.get("--dbg", "9500"))          # порты отладки браузеров: DBG … DBG+49
FROM = int(opt.get("--from", "0"))
JOBS = int(opt.get("--jobs", "3"))

async def one(app, name, arg, dbg):
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={dbg}", f"--user-data-dir={prof}",
                             "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                             "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                             "--disable-backgrounding-occluded-windows", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as s:
            pages = []
            for _ in range(80):
                try:
                    async with s.get(f"http://127.0.0.1:{dbg}/json/list") as r:
                        pages = [p for p in await r.json() if p.get("type") == "page"]
                        if pages: break
                except Exception:
                    pass
                await asyncio.sleep(0.15)
            ws = await s.ws_connect(pages[0]["webSocketDebuggerUrl"], max_msg_size=0)
            seq = [0]
            async def call(method, params=None, timeout=900):
                seq[0] += 1; mid = seq[0]
                await ws.send_json({"id": mid, "method": method, "params": params or {}})
                t0 = time.time()
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(1, timeout - (time.time() - t0)))
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        raise RuntimeError(f"связь с браузером оборвалась ({msg.type})")
                    m = json.loads(msg.data)
                    if m.get("method") == "Inspector.targetCrashed":
                        raise RuntimeError("вкладка упала (targetCrashed)")
                    if m.get("id") == mid:
                        if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
                        return m.get("result", {})
            await call("Page.enable"); await call("Runtime.enable"); await call("Inspector.enable")
            await call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            url = f"http://127.0.0.1:{PORT}/{app}/_fz{TAG}.html?app={'driver' if app == 'driver' else 'star'}"
            await call("Page.navigate", {"url": url})
            for _ in range(120):
                await asyncio.sleep(0.15)
                r = await call("Runtime.evaluate", {"expression": "typeof window.__run === 'function' && document.readyState === 'complete'", "returnByValue": True})
                if r.get("result", {}).get("value"): break
            await asyncio.sleep(0.6)
            r = await call("Runtime.evaluate", {"expression": f"window.__run({json.dumps(arg)})",
                                                "awaitPromise": True, "returnByValue": True, "timeout": 850000})
            if r.get("exceptionDetails"):
                return {"fails": [{"where": "исключение", "what": [str(r["exceptionDetails"])[:800]]}], "stats": {}}
            await ws.close()
            return r["result"]["value"]
    except Exception as e:
        return {"fails": [{"where": "браузер", "what": [f"{type(e).__name__}: {e}"]}], "stats": {}}
    finally:
        proc.terminate()
        try: proc.wait(5)
        except Exception: proc.kill()
        shutil.rmtree(prof, ignore_errors=True)

async def main():
    subprocess.run([sys.executable, os.path.join(HERE, "build.py"), ROOT], check=True, stdout=subprocess.DEVNULL,
                   env={**os.environ, "FZ_TAG": TAG})
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", ROOT],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.sleep(0.8)
        total = await прогон()
    finally:
        srv.terminate()
        for app in ("driver", "owner"):
            try: os.remove(os.path.join(ROOT, app, f"_fz{TAG}.html"))
            except FileNotFoundError: pass
    print("\nИТОГ:", "все прошли" if not total else f"провалов {total}")
    sys.exit(1 if total else 0)


async def прогон():
    total = 0
    for app in APPS:
        runs = [("сценарии", {"scenarios": True, "seed": 0, "steps": 0})] if FROM == 0 else []
        runs += [(f"сид {1000 + k}", {"seed": 1000 + k, "steps": STEPS}) for k in range(FROM, FROM + SEEDS)]
        stats, fails, t_app = {}, [], time.time()
        sem = asyncio.Semaphore(JOBS)
        async def go(i, name, arg):
            async with sem:
                v = await one(app, name, arg, DBG + i % 50)
                return name, v
        res = await asyncio.gather(*[go(i, n, a) for i, (n, a) in enumerate(runs)])
        for name, v in res:
            for k, n in (v.get("stats") or {}).items():
                stats[k] = stats.get(k, 0) + n
            for f in v.get("fails") or []:
                f["run"] = name; fails.append(f)
        total += len(fails)
        print(f"\n=== {app}: прогонов {len(runs)} · {time.time() - t_app:.0f} с · провалов {len(fails)}")
        print("действия:", ", ".join(f"{k} {n}" for k, n in sorted(stats.items())))
        for f in fails[:6]:
            print(f"  FAIL [{f.get('run')}] {f.get('where', '')} шаг {f.get('step')}:")
            for w in f.get("what", []): print("      -", w)
            for t in (f.get("trail") or [])[-12:]: print("        …", t)
    return total

asyncio.run(main())
