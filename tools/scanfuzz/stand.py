"""Настоящее приложение в безголовом Chrome — на один кусок своего JS.

Стенд тот же, что у фаззера (build.py): настоящие driver/index.html и
owner/index.html, подменены камера со звуком (pre.js) и сервер (model.js).
Отсюда им пользуются точечные проверки — tools/test_torch.py (ступени света),
tools/test_scan_sound.py (звук сканера): поднять стенд, открыть сканер,
выполнить свой JS, забрать его ответ, при желании снять экран.

    из tools/scanfuzz/stand import run
    results = asyncio.run(run("driver", JS, port=8794, dbg=9600))
"""
import asyncio, base64, json, os, shutil, subprocess, sys, tempfile, time
import aiohttp

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# Открыть сканер приёмки и вернуть id кнопки фонарика — у каждого свой путь.
ENTER = {
    "driver": """
      M.reset([['p1', 3], ['p2', 3]]);
      window.supLoad = async () => {}; window.shLoad = async () => {};
      await supOpen(M.sid, M.oid);
      await new Promise(r => setTimeout(r, 300));
      [...document.querySelectorAll('#supBot .btn-pri')].find(b => /сканирование/.test(b.textContent)).click();
      return 'camTorch';""",
    "owner": """
      M.reset([['p1', 3], ['p2', 3]]);
      window.supLoad = async () => { const m = document.getElementById('accMid'); if(m) m.innerHTML = ''; };
      M.me = _rcvMe();
      document.getElementById('accOv').classList.add('show');
      SUP_ID = M.sid;
      RCV = null; RCV = rcvAdopt(M.view()); RCV_LINE = null; RCV_ROWS = [];
      rcvRender();
      await new Promise(r => setTimeout(r, 200));
      [...document.querySelectorAll('#accFoot .qb')].find(b => /сканирование/.test(b.textContent)).click();
      return 'rcvTorch';""",
}


class Stands:
    """Стенды на время проверки: собрать, раздать по http, потом убрать."""

    def __init__(self, port: int, tag: str = ""):
        self.port, self.tag = port, tag or f"_t{os.getpid()}"
        self.srv = None

    def __enter__(self):
        subprocess.run([sys.executable, os.path.join(HERE, "build.py"), ROOT],
                       check=True, stdout=subprocess.DEVNULL, env={**os.environ, "FZ_TAG": self.tag})
        self.srv = subprocess.Popen([sys.executable, "-m", "http.server", str(self.port),
                                     "--bind", "127.0.0.1", "--directory", ROOT],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        return self

    def __exit__(self, *e):
        if self.srv: self.srv.terminate()
        for app in ("driver", "owner"):
            try: os.remove(os.path.join(ROOT, app, f"_fz{self.tag}.html"))
            except FileNotFoundError: pass


async def run(app: str, js: str, *, port: int, dbg: int, tag: str, shot: str = "") -> object:
    """Выполнить js в стенде приложения и вернуть его ответ. `%ENTER%` в js
    заменяется на открытие сканера. shot — куда положить снимок экрана."""
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={dbg}",
                             f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
                             "--autoplay-policy=no-user-gesture-required", "about:blank"],
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

            async def call(method, params=None, timeout=300):
                seq[0] += 1; mid = seq[0]
                await ws.send_json({"id": mid, "method": method, "params": params or {}})
                t0 = time.time()
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(1, timeout - (time.time() - t0)))
                    m = json.loads(msg.data)
                    if m.get("id") == mid:
                        if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
                        return m.get("result", {})

            await call("Page.enable"); await call("Runtime.enable")
            await call("Emulation.setDeviceMetricsOverride",
                       {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            await call("Page.navigate", {"url": f"http://127.0.0.1:{port}/{app}/_fz{tag}.html"
                                                f"?app={'driver' if app == 'driver' else 'star'}"})
            for _ in range(120):
                await asyncio.sleep(0.15)
                r = await call("Runtime.evaluate", {"expression": "document.readyState === 'complete' && !!window.__M",
                                                    "returnByValue": True})
                if r.get("result", {}).get("value"): break
            await asyncio.sleep(0.6)
            r = await call("Runtime.evaluate", {"expression": js.replace("%ENTER%", ENTER[app]),
                                                "awaitPromise": True, "returnByValue": True, "timeout": 120000})
            if r.get("exceptionDetails"):
                return [["исключение в странице", str(r["exceptionDetails"])[:400], "", False]]
            if shot:
                png = await call("Page.captureScreenshot", {"format": "png"})
                open(shot, "wb").write(base64.b64decode(png["data"]))
            await ws.close()
            return r["result"]["value"]
    except Exception as e:                                   # noqa: BLE001
        return [["браузер", f"{type(e).__name__}: {e}", "", False]]
    finally:
        proc.terminate()
        try: proc.wait(5)
        except Exception: proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


def report(app: str, rows: list) -> int:
    """Напечатать строки проверок и вернуть число провалов."""
    print(f"\n{ {'driver': 'водитель', 'owner': 'STAR'}.get(app, app) }:")
    плохо = 0
    for имя, дали, ждём, ок in rows:
        print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали}" + ("" if ок else f" ≠ {ждём}"))
        плохо += 0 if ок else 1
    return плохо
