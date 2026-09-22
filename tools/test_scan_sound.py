"""Звук сканера (владелец, 23 сен 2026: «говорят, перестаёт звук работать во
время сканирования»).

Настоящие сканеры водителя и STAR в безголовом Chrome; звуковой контекст
подменён (tools/scanfuzz/pre.js): видно каждый сыгранный тон и в каком
состоянии был контекст. Проверяем то, из-за чего звук и пропадал:
  • прочитали код — «пик» звучит;
  • айфон увёл контекст в 'interrupted' (звонок, блокировка, запуск камеры) —
    будим и звучим. Раньше будили только 'suspended', и после этого сканер
    молчал до конца смены;
  • контекст умер совсем (resume отклонён) — заводим новый, и звук вернулся;
  • звук выключен кнопкой — молчим;
  • открытие сканера будит звук само: «пик» зовётся из распознавателя, а
    оттуда айфон включать звук уже не даёт.

    python3 tools/test_scan_sound.py [driver|owner …]
"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanfuzz"))
from stand import Stands, run, report                        # noqa: E402

PORT, DBG = 8797, 9640
TAG = f"_s{os.getpid()}"
APPS = [a for a in sys.argv[1:] if not a.startswith("-")] or ["driver", "owner"]

JS = """(async () => {
  const out = [];
  const eq = (имя, дали, ждём) => out.push([имя, JSON.stringify(дали), JSON.stringify(ждём),
                                            JSON.stringify(дали) === JSON.stringify(ждём)]);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const M = window.__M;
  __CAM.torch = false; __CAM.dim = false;
  await (async () => { %ENTER% })();
  for(let i = 0; i < 80 && !SCAN.stream; i++) await sleep(100);
  eq('сканер открылся', !!SCAN.stream, true);
  SFX.on = true;

  // Открытие сканера — единственное место, куда приходят по касанию: звук
  // должен просыпаться уже здесь, до первого прочитанного кода.
  eq('звук разбудили на открытии сканера', __SND.ctxs >= 1, true);

  let n = 0;
  const код = () => 'AMB-' + (++n) + '-' + Date.now();
  const скан = async () => {                 // подержать код в кадре и убрать
    const c = код();
    __FRAME = c; await sleep(600); __FRAME = null; await sleep(300);
    return c;
  };

  __SND.tones = []; __SND.resumes = 0;
  await скан();
  eq('прочитали код — «пик» прозвучал', __SND.tones.length > 0, true);
  eq('…и контекст при этом живой', (__SND.tones[0] || {}).state, 'running');

  // Айфон после звонка, блокировки или запуска камеры: контекст жив, но молчит
  // и сам не вернётся. Это и есть «звук перестал работать во время скана».
  __SND.state = 'interrupted'; __SND.tones = []; __SND.resumes = 0;
  await скан();
  eq('контекст ушёл в interrupted — разбудили', __SND.resumes > 0, true);
  eq('…и звук вернулся', __SND.state, 'running');
  // Тон приложение отправляет в контекст всегда — вопрос в том, живой ли он:
  // в молчащий контекст «пик» уходит в никуда, и человек слышит тишину.
  eq('…и «пик» ушёл в живой контекст, а не в тишину',
     (__SND.tones[0] || {}).state, 'running');

  // Тот же случай, но контекст уже не чинится: заводим новый.
  const было = __SND.ctxs;
  __SND.state = 'suspended'; __SND.failResume = true; __SND.tones = []; __SND.closed = 0;
  await скан();
  __SND.failResume = false;
  await sleep(200);
  eq('мёртвый контекст закрыли и завели новый', [__SND.closed > 0, __SND.ctxs > было], [true, true]);
  __SND.state = 'running'; __SND.tones = [];
  await скан();
  eq('после замены звук снова звучит', __SND.tones.length > 0, true);

  // Кнопка звука: выключили — тишина, включили — снова «пик».
  SFX.on = false; __SND.tones = [];
  await скан();
  eq('звук выключен кнопкой — тишина', __SND.tones.length, 0);
  SFX.on = true; __SND.tones = [];
  await скан();
  eq('включили обратно — звучит', __SND.tones.length > 0, true);
  return out;
})()"""


async def main():
    провалы = 0
    with Stands(PORT, TAG):
        for i, app in enumerate(APPS):
            провалы += report(app, await run(app, JS, port=PORT, dbg=DBG + i, tag=TAG))
    print("\nИТОГ:", "все прошли" if not провалы else f"провалено {провалы}")
    return 1 if провалы else 0


sys.exit(asyncio.run(main()))
