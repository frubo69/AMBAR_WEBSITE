"""Ступени света у фонарика (владелец, 22 сен 2026: «можем сделать так, чтобы
можно было регулировать яркость вот этого фонарика? максимально органично»;
«и у старшего тоже, вообще везде, где используется фонарик»).

Настоящие сканеры водителя и STAR в безголовом Chrome, камера подставная
(tools/scanfuzz/pre.js): вспышка и диапазон экспозиции включаются флагами, а
всё, что приложение просит у камеры, складывается в __CAM.applied — по нему и
видно, куда встал свет. Проверяем то, что чувствует палец:
  • нажал — горит, нажал ещё раз — погасло, и кадр вернулся как был;
  • потянул вниз — свет убавился ровно на столько ступеней, сколько проехал
    палец, и линейка показывает ту же ступень;
  • ступень переживает выключение фонарика;
  • камера ступеней не даёт — регулировать нечего, а кнопка работает как была.

    python3 tools/test_torch.py [driver|owner …]
"""
import asyncio, json, os, shutil, subprocess, sys, tempfile, time
import aiohttp

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAG = f"_t{os.getpid()}"
PORT = 8794
APPS = [a for a in sys.argv[1:] if not a.startswith("-")] or ["driver", "owner"]

# Открыть сканер и найти кнопку фонарика — у каждого приложения свой путь.
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

JS = """(async () => {
  const out = [];
  const eq = (имя, дали, ждём) => out.push([имя, JSON.stringify(дали), JSON.stringify(ждём),
                                            JSON.stringify(дали) === JSON.stringify(ждём)]);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const M = window.__M;
  try{ localStorage.removeItem('ambar_torch_lvl'); }catch(e){}
  __CAM.torch = true; __CAM.dim = true;

  const id = await (async () => { %ENTER% })();
  let b = null;
  for(let i = 0; i < 80 && !(b && b.offsetParent); i++){ await sleep(100); b = document.getElementById(id); }
  eq('кнопка фонарика видна', !!(b && b.offsetParent), true);
  if(!b) return out;

  // Палец: на телефоне касания, на настольном — указатели. Шлём то, что слушает
  // сама кнопка, — иначе проверка проверяет не то, чем пользуются.
  const тач = 'ontouchstart' in window;
  const точка = y => new Touch({identifier: 1, target: b, clientX: 10, clientY: y});
  const жест = (что, y) => {
    if(тач){
      const имя = {down: 'touchstart', move: 'touchmove', up: 'touchend'}[что];
      b.dispatchEvent(new TouchEvent(имя, {bubbles: true, cancelable: true,
        touches: что === 'up' ? [] : [точка(y)], changedTouches: [точка(y)]}));
    }else{
      const имя = {down: 'pointerdown', move: 'pointermove', up: 'pointerup'}[что];
      b.dispatchEvent(new PointerEvent(имя, {bubbles: true, cancelable: true, clientY: y, pointerId: 1}));
    }
  };
  const тык = async () => { жест('down', 300); жест('up', 300); await sleep(300); };
  const тяга = async шагов => {                      // вниз — тусклее, вверх — ярче
    жест('down', 300);
    for(let k = 1; k <= Math.abs(шагов); k++) жест('move', 300 + Math.sign(шагов) * 22 * k);
    await sleep(60);
    жест('up', 300 + шагов * 22);
    await sleep(300);
  };
  const свет = () => { const a = [...__CAM.applied].reverse().find(x => 'exposureCompensation' in x);
                       return a ? Math.round(a.exposureCompensation * 100) / 100 : null; };
  const вспышка = () => { const a = [...__CAM.applied].reverse().find(x => 'torch' in x);
                          return a ? a.torch : null; };
  const линейка = () => { const d = document.querySelector('.atl-dial'); return !!(d && d.classList.contains('on')); };
  const ступень = () => { const d = document.querySelector('.atl-dial');
                          return d ? [...d.children].findIndex(i => i.className === 'now') + 1 : 0; };

  __CAM.applied = [];
  await тык();
  eq('нажали — фонарик горит', [вспышка(), b.classList.contains('on')], [true, true]);
  eq('свет на полной ступени — как камера снимает сама', свет(), 0);
  eq('линейка показалась сама, чтобы о ступенях узнали', линейка(), true);
  await sleep(1000);
  eq('и через секунду убралась', линейка(), false);

  // Вниз на три ступени: 8 → 5. Диапазон −2…0, шаг 0.1 → −0.9.
  __CAM.applied = [];
  await тяга(3);
  eq('потянули вниз — свет убавлен на три ступени', [AmbarTorch.level, свет()], [5, -0.9]);
  eq('фонарик от тяги не погас', [вспышка(), b.classList.contains('on')], [null, true]);
  eq('линейку отпустили — убралась', линейка(), false);

  // Тянем вверх две ступени и смотрим линейку прямо посреди жеста.
  жест('down', 300); жест('move', 300 - 22); жест('move', 300 - 44);
  await sleep(120);
  const впроцессе = [линейка(), ступень(), AmbarTorch.level];
  жест('up', 300 - 44);
  await sleep(300);
  eq('пока тянем — линейка видна и показывает ту же ступень', впроцессе, [true, 7, 7]);

  __CAM.applied = [];
  await тык();
  eq('погасили — и кадр вернулся как был', [вспышка(), свет()], [false, 0]);
  __CAM.applied = [];
  await тык();
  eq('зажгли снова — ступень запомнилась', [вспышка(), свет(), AmbarTorch.level], [true, -0.3, 7]);
  await тык();

  // Телефон без диапазона: регулировать нечего — линейки нет, кнопка прежняя.
  __CAM.dim = false;
  const s2 = await navigator.mediaDevices.getUserMedia({video: true});
  const t2 = s2.getVideoTracks()[0];
  eq('камера ступеней не даёт — и линейки не будет', AmbarTorch.dimmable(t2), false);
  eq('…а фонарик включается как раньше', await AmbarTorch.apply(t2, true), true);
  s2.getTracks().forEach(t => t.stop());
  return out;
})()"""


async def one(app, dbg):
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={dbg}", f"--user-data-dir={prof}",
                             "--no-first-run", "--no-default-browser-check",
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
            await call("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/{app}/_fz{TAG}.html"
                                                f"?app={'driver' if app == 'driver' else 'star'}"})
            for _ in range(120):
                await asyncio.sleep(0.15)
                r = await call("Runtime.evaluate", {"expression": "document.readyState === 'complete' && !!window.__M",
                                                    "returnByValue": True})
                if r.get("result", {}).get("value"): break
            await asyncio.sleep(0.6)
            r = await call("Runtime.evaluate", {"expression": JS.replace("%ENTER%", ENTER[app]),
                                                "awaitPromise": True, "returnByValue": True, "timeout": 120000})
            if r.get("exceptionDetails"):
                return [["исключение в странице", str(r["exceptionDetails"])[:400], "", False]]
            await ws.close()
            return r["result"]["value"]
    except Exception as e:
        return [["браузер", f"{type(e).__name__}: {e}", "", False]]
    finally:
        proc.terminate()
        try: proc.wait(5)
        except Exception: proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


async def main():
    subprocess.run([sys.executable, os.path.join(HERE, "scanfuzz", "build.py"), ROOT],
                   check=True, stdout=subprocess.DEVNULL, env={**os.environ, "FZ_TAG": TAG})
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", ROOT],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    провалы = 0
    try:
        await asyncio.sleep(0.8)
        for i, app in enumerate(APPS):
            print(f"\n{'водитель' if app == 'driver' else 'STAR'}:")
            for имя, дали, ждём, ок in await one(app, 9600 + i):
                print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали}" + ("" if ок else f" ≠ {ждём}"))
                провалы += 0 if ок else 1
    finally:
        srv.terminate()
        for app in ("driver", "owner"):
            try: os.remove(os.path.join(ROOT, app, f"_fz{TAG}.html"))
            except FileNotFoundError: pass
    print("\nИТОГ:", "все прошли" if not провалы else f"провалено {провалы}")
    return 1 if провалы else 0


sys.exit(asyncio.run(main()))
