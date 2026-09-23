"""Ступени света у фонарика (владелец, 22 сен 2026: «можем сделать так, чтобы
можно было регулировать яркость вот этого фонарика? максимально органично»;
«и у старшего тоже, вообще везде, где используется фонарик»).

Настоящие сканеры водителя и STAR в безголовом Chrome (tools/scanfuzz/stand.py),
камера подставная (tools/scanfuzz/pre.js): вспышка и диапазон экспозиции
включаются флагами, а всё, что приложение просит у камеры, складывается в
__CAM.applied — по нему и видно, куда встал свет. Проверяем то, что чувствует
палец:
  • нажал — горит, нажал ещё раз — погасло, и кадр вернулся как был;
  • потянул вниз — свет убавился ровно на столько ступеней, сколько проехал
    палец, и линейка показывает ту же ступень;
  • ступень переживает выключение фонарика;
  • камера ступеней не даёт — регулировать нечего, а кнопка работает как была.

    python3 tools/test_torch.py [driver|owner …]
"""
import asyncio, io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanfuzz"))
from stand import Stands, run, report                        # noqa: E402

PORT, DBG = 8794, 9600
TAG = f"_t{os.getpid()}"
APPS = [a for a in sys.argv[1:] if not a.startswith("-")] or ["driver", "owner"]

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

  // Телефон без диапазона: НАСТОЯЩИХ ступеней у него нет — это и должно быть
  // видно (линейка там всё равно будет, но гасит она картинку; см. JS_SCREEN).
  __CAM.dim = false;
  const s2 = await navigator.mediaDevices.getUserMedia({video: true});
  const t2 = s2.getVideoTracks()[0];
  eq('у такой камеры настоящих ступеней нет', AmbarTorch.dimmable(t2), false);
  eq('…а фонарик включается как раньше', await AmbarTorch.apply(t2, true), true);
  s2.getTracks().forEach(t => t.stop());
  return out;
})()"""

# Телефон, который убавлять свет не умеет (у владельца такой: в журнале
# «вспышка есть · ступеней нет»). Регулятор обязан работать и там — только
# гасит он картинку на экране, а не сам светодиод, и говорит об этом.
JS_SCREEN = """(async () => {
  const out = [];
  const eq = (имя, дали, ждём) => out.push([имя, JSON.stringify(дали), JSON.stringify(ждём),
                                            JSON.stringify(дали) === JSON.stringify(ждём)]);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const M = window.__M;
  try{ localStorage.removeItem('ambar_torch_lvl'); }catch(e){}
  __CAM.torch = true; __CAM.dim = false;          // вспышка есть, ступеней нет
  let сказали = '';
  const _t = window.toast || window._toast;
  (window.toast ? window : window).toast = m => { сказали = m; };
  window._toast = m => { сказали = m; };

  const id = await (async () => { %ENTER% })();
  let b = null;
  for(let i = 0; i < 80 && !(b && b.offsetParent); i++){ await sleep(100); b = document.getElementById(id); }
  eq('кнопка фонарика видна и без ступеней у камеры', !!(b && b.offsetParent), true);
  if(!b) return out;
  const v = SCAN.video;

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

  await тык();
  eq('фонарик включился', b.classList.contains('on'), true);

  жест('down', 300);
  for(let k = 1; k <= 3; k++) жест('move', 300 + 22 * k);
  await sleep(150);
  const влинейке = document.querySelector('.atl-dial')?.classList.contains('on');
  жест('up', 300 + 66); await sleep(250);
  eq('линейка есть и на таком телефоне', влинейке, true);
  eq('ступень уехала на три вниз', AmbarTorch.level, 5);
  eq('приглушилась картинка', /brightness\(0\.\d+\)/.test(v.style.filter || ''), true);
  eq('и человеку сказали, что гаснет именно картинка', /картинк/i.test(сказали), true);

  const было = v.style.filter;
  жест('down', 300); жест('move', 300 - 44); await sleep(120); жест('up', 300 - 44); await sleep(250);
  eq('вверх — светлее', v.style.filter !== было && AmbarTorch.level === 7, true);

  await тык();
  eq('погасили фонарик — картинка вернулась как была', v.style.filter || '', '');
  return out;
})()"""


def все_кнопки():
    """Каждая кнопка фонарика — через общий показ со ступенями.

    Экранов со сканером много (у старшего их восемь: приёмка, перемещения,
    проверка, свободная проверка…), и забыть один — значит оставить там кнопку
    без регулировки, о чём узнаешь только от человека (владелец, 23 сен 2026:
    «в свободной проверке тоже реализуй»). Проверяем исходник: у каждой кнопки
    с id …Torch рядом стоит camTorchShow/qrTorchShow.
    """
    import re
    rows = []
    for путь, показ in (("driver/index.html", "camTorchShow"), ("owner/index.html", "qrTorchShow")):
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), путь),
                      encoding="utf-8").read()
        lines = src.split("\n")
        ids = sorted(set(re.findall(r'id="(\w*Torch)"', src)))
        забыли = []
        for i in ids:
            места = [n for n, l in enumerate(lines) if f"getElementById('{i}')" in l]
            if not места or not any(показ + "(" in "\n".join(lines[n:n + 3]) for n in места):
                забыли.append(i)
        rows.append((f"{путь}: кнопок фонарика {len(ids)}, все со ступенями",
                     f"забыли: {забыли}" if забыли else "все", "все", not забыли))
    return rows


async def main():
    провалы = report("исходник", все_кнопки())
    with Stands(PORT, TAG):
        for i, app in enumerate(APPS):
            провалы += report(app, await run(app, JS, port=PORT, dbg=DBG + i, tag=TAG))
            провалы += report(app + " · камера ступеней не даёт",
                              await run(app, JS_SCREEN, port=PORT, dbg=DBG + 10 + i, tag=TAG))
    print("\nИТОГ:", "все прошли" if not провалы else f"провалено {провалы}")
    return 1 if провалы else 0


sys.exit(asyncio.run(main()))
