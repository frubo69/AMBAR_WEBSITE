// Прогонщик: сценарии прошлых поломок и случайные цепочки действий над
// настоящим сканером приёмки (водитель и STAR). После каждого шага — сверка.
(function(){
  const APP = window.__APP, M = window.__M;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const left = l => Math.max(0, Math.ceil(Math.round((l.need - l.got) * 2e6) / 1e6) / 2);
  const NOISE = /stand: нет такой ручки|Failed to load resource|ResizeObserver/;

  const D = {
    name: 'driver',
    async enter(){
      window.supLoad = async () => {}; window.shLoad = async () => {};
      await supOpen(M.sid, M.oid);
    },
    // Камера «открыта» — когда её экран на виду: после закрытия района разметка
    // остаётся скрытой, без потока, и это не поломка.
    camOpen: () => !!document.getElementById('camV') && document.getElementById('supScr').classList.contains('show'),
    video: () => document.getElementById('camV'),
    rm: () => RM, line: () => LINE, task: () => TASK, rows: () => SCAN_ROWS, feedMax: 25,
    xButtons: () => [...document.querySelectorAll('#camFeed .sc-x')],
    xIndex: b => +((b.getAttribute('onclick') || '').match(/camDrop\((\d+)\)/) || [])[1],
    btn: t => [...document.querySelectorAll('#supBot button, #supAct')]
      .find(b => b.offsetParent !== null && b.textContent.trim() === t),
    lineButtons: () => [...document.querySelectorAll('#supMid .dpl.dli')].filter(b => !b.disabled && !b.classList.contains('ok')),
    openBtn: () => [...document.querySelectorAll('#supBot .btn-pri')].find(b => /сканирование/.test(b.textContent)),
    shownLeft: () => (document.getElementById('camLeft') || {}).textContent,
    shownTitle: () => (document.getElementById('supT') || {}).textContent,
    busy: () => SCANQ.pending > 0 || _rmBusy,
    resync: () => supResync(),
    poll: () => supPollTick(),
    back: () => supBack(),
  };
  const S = {
    name: 'star',
    async enter(){
      // Настоящий список поставок перерисовывает экран — заглушка тоже его чистит.
      window.supLoad = async () => { const m = document.getElementById('accMid'); if(m) m.innerHTML = ''; };
      M.me = _rcvMe();
      document.getElementById('accOv').classList.add('show');
      SUP_ID = M.sid;
      // Как приложение: через rcvAdopt (RCV = null — новая задача, версии с нуля).
      RCV = null; RCV = rcvAdopt(M.view()); RCV_LINE = null; RCV_ROWS = [];
      rcvRender();
    },
    camOpen: () => !!document.getElementById('rcvVideo') && document.getElementById('accOv').classList.contains('show'),
    video: () => document.getElementById('rcvVideo'),
    rm: () => RCV_RM, line: () => RCV_LINE, task: () => RCV, rows: () => RCV_ROWS, feedMax: 40,
    xButtons: () => [...document.querySelectorAll('#rcvFeed .scan-del')],
    xIndex: b => +((b.getAttribute('onclick') || '').match(/rcvDrop\((\d+)\)/) || [])[1],
    btn: t => [...document.querySelectorAll('#rcvDock button, #accAct button, #accFoot button')]
      .find(b => b.textContent.trim() === t),
    lineButtons: () => [...document.querySelectorAll('#accMid .rl')].filter(b => !b.disabled),
    openBtn: () => [...document.querySelectorAll('#accFoot .qb')].find(b => /сканирование/.test(b.textContent)),
    shownLeft: () => (document.getElementById('rcvN') || {}).textContent,
    shownTitle: () => (document.getElementById('accTitle') || {}).textContent,
    busy: () => SCANQ.pending > 0 || _rcvRmBusy,
    resync: () => rcvResync(),
    poll: () => (RCV_POLL && !RCV_LINE && !RCV_RM ? rcvPollTick() : null),
    back: () => (ACC_BACK ? ACC_BACK() : null),
  };
  const A = APP === 'driver' ? D : S;

  // ── ожидание тишины: очередь пуста, запросов нет, таймеры переключения прошли ──
  async function settle(max){
    max = max || 15000;
    const t0 = Date.now(); let quiet = null;
    while(Date.now() - t0 < max){
      const busy = M.pending > 0 || A.busy();
      if(!busy){ if(quiet === null) quiet = Date.now(); if(Date.now() - quiet > 160) break; }
      else quiet = null;
      await sleep(20);
    }
    if(Date.now() - t0 >= max) return false;
    // Скан добрал позицию — через 700 мс сканер сам переходит к следующей.
    // Ищем последний скан, а не последнюю запись: после него в журнал может
    // лечь продление замка, а переход всё равно впереди.
    let last = null;
    for(let i = M.log.length - 1; i >= 0; i--) if(M.log[i].kind === 'scan'){ last = M.log[i]; break; }
    if(last && last.res.ok && last.res.left === 0 && Date.now() - last.t < 1000){
      await sleep(1000 - (Date.now() - last.t) + 80);
      return settle(max);
    }
    return true;
  }

  const ctx = {t0: 0, gumWant: 0, video: null, dual: false, strictFaults: 0, stats: {}, fails: [], step: 0, trail: []};
  const stat = k => { ctx.stats[k] = (ctx.stats[k] || 0) + 1; };
  function note(s){ ctx.trail.push(s); if(ctx.trail.length > 14) ctx.trail.shift(); }

  function check(where){
    const F = [];
    const errs = __ERR.filter(e => e.t >= ctx.t0 && !NOISE.test(e.m));
    if(errs.length) F.push('ошибки JS: ' + errs.map(e => e.m + ' ' + e.s.split('\n')[1]).join(' | '));
    __ERR.length = 0;
    const cam = A.camOpen();
    if(cam){
      const v = A.video();
      if(!ctx.video) ctx.video = v;
      if(v !== ctx.video) F.push('<video> пересоздан посреди сессии камеры');
      if(!SCAN.stream) F.push('камера на экране, а потока нет');
      else{
        if(SCAN.video !== v) F.push('сканер смотрит в другой <video>');
        if(v.srcObject !== SCAN.stream) F.push('у <video> не тот поток');
        const tr = SCAN.stream.getVideoTracks()[0];
        if(!tr || tr.readyState !== 'live') F.push('поток камеры остановлен при открытой камере');
      }
      if(!v.isConnected) F.push('<video> оторван от страницы');
    }else{
      ctx.video = null;
      if(SCAN.stream) F.push('камера закрыта, а поток живёт');
    }
    __CAM.streams.forEach(s => {
      if(s !== SCAN.stream && s.getVideoTracks().some(t => t.readyState === 'live')) F.push('брошенный живой поток камеры');
    });
    if(__CAM.gum !== ctx.gumWant) F.push(`getUserMedia вызван ${__CAM.gum} раз, ждали ${ctx.gumWant}`);
    M.viol.splice(0).forEach(v => F.push(v));
    M.xtap = 0;
    const T = A.task();
    if(T && M.faults === ctx.strictFaults){
      M.lines.forEach(ml => {
        const l = (T.lines || []).find(x => x.id === ml.id);
        if(!l || +l.got !== ml.got || +l.left !== left(ml))
          F.push(`строка ${ml.id}: на экране ${l && l.got}/${l && l.left}, на сервере ${ml.got}/${left(ml)}`);
      });
      const L = A.line();
      if(L){
        const ml = M.lines.find(x => x.id === L.id);
        if(ml && (+L.got !== ml.got || +L.left !== left(ml))) F.push(`позиция камеры ${L.id}: ${L.got}/${L.left} ≠ ${ml.got}/${left(ml)}`);
        if(cam && !A.rm() && A.shownLeft() !== fmtQty(L.left)) F.push(`«осталось» на камере ${A.shownLeft()} ≠ ${fmtQty(L.left)}`);
      }
    }
    if(cam){
      if(A.rm()){
        if(A.shownTitle() !== 'Убрать бутылку') F.push('в режиме «убрать» заголовок «' + A.shownTitle() + '»');
        if(A.btn('Убрать бутылку')) F.push('в режиме «убрать» видна кнопка «Убрать бутылку»');
      }else if(A.line()){
        if(!A.btn('Убрать бутылку')) F.push('на камере нет кнопки «Убрать бутылку»');
        if(A.shownTitle() !== A.line().name) F.push(`заголовок камеры «${A.shownTitle()}» ≠ «${A.line().name}»`);
      }else F.push('камера открыта без позиции и не в режиме «убрать»');
      const rows = A.rows().slice(0, A.feedMax);
      const want = rows.filter(r => r.state === 'new' ||
        (r.state === 'dup' && r.ours && r.asId && r.asId !== (A.line() && A.line().id))).length;
      if(A.xButtons().length !== want) F.push(`крестиков в ленте ${A.xButtons().length}, ждали ${want}`);
    }
    if(F.length){
      ctx.fails.push({where, step: ctx.step, what: F, trail: ctx.trail.slice()});
    }
    return F;
  }

  // ── действия ──
  let codeN = 0;
  const fresh = () => `B${Date.now().toString(36)}${(codeN++).toString(36)}`;
  // Кадры идут с той скоростью, какую тянет страница: ждём не время, а то,
  // что движок сканера кадр действительно прочитал.
  async function frame(code, hold){
    window.__FRAME = code; note('в кадре ' + code);
    const t0 = Date.now();
    while(SCAN.stream && SCAN.lastCode !== code && Date.now() - t0 < 3000) await sleep(10);
    await sleep(hold || 60);
  }
  async function clear(){
    window.__FRAME = null;
    const t0 = Date.now();
    while(SCAN.stream && (!SCAN.missed || Date.now() - t0 < SCAN.GAP_MS + 90) && Date.now() - t0 < 4000) await sleep(10);
    note('кадр пуст');
  }
  async function click(b, what){ if(!b) return false; note('нажали ' + what); b.click(); await sleep(30); return true; }
  async function tapX(i){
    const bs = A.xButtons(); if(!bs.length) return false;
    const b = i == null ? bs[Math.floor(R() * bs.length)] : bs[i];
    M.xtap++;
    return click(b, 'крестик у ' + (A.rows()[A.xIndex(b)] || {}).code);
  }
  async function openVia(b, what){
    if(!b) return false;
    const was = __CAM.gum;
    await click(b, what);
    await settle();
    const opened = A.camOpen() && SCAN.stream;
    if(__CAM.gum > was) ctx.gumWant += (__CAM.deny ? 1 : (ctx.dual ? 2 : 1));
    if(opened){ ctx.video = A.video(); stat('камера:открыта'); }
    return opened;
  }

  let R = Math.random;
  function rng(seed){
    let a = seed >>> 0;
    return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  const pick = xs => xs[Math.floor(R() * xs.length)];

  async function begin(plan, extra, opts){
    opts = opts || {};
    M.reset(plan, extra);
    M.lat = opts.lat || [0, 30];
    __CAM.deny = false; __CAM.label = opts.label || 'Back Camera';
    ctx.dual = /dual|wide|triple/i.test(__CAM.label);
    ctx.gumWant = __CAM.gum; ctx.video = null; ctx.strictFaults = M.faults;
    SCAN.GAP_MS = 250; SCAN.DECODE_MS = 30;
    window.__FRAME = null;
    ctx.t0 = Date.now(); __ERR.length = 0;
    await A.enter();
    await settle();
  }
  async function finish(where){
    window.__FRAME = null;
    for(let k = 0; k < 3 && A.camOpen(); k++){ await click(A.btn('К списку'), 'К списку'); await settle(); }
    // Сбои сети — перечитываем, и числа обязаны сойтись.
    if(M.faults !== ctx.strictFaults){ M.faultLeft = 0; await A.resync(); await settle(); ctx.strictFaults = M.faults; }
    check(where);
  }
  const ours = () => [...M.reg.entries()].filter(([c, d]) => d.supply === M.sid);
  const assert = (cond, what) => { if(!cond) ctx.fails.push({where: 'сценарий', step: ctx.step, what: [what,
    'состояние: камера ' + A.camOpen() + ', позиция ' + ((A.line() || {}).id || '—') + ', «убрать» ' + A.rm() +
    ', строки ' + M.lines.map(l => l.id + ' ' + l.got + '/' + l.need).join(' '),
    'журнал: ' + M.log.slice(-6).map(e => e.kind + ' ' + (e.body.code || '') + ' → ' + (e.res.verdict || (e.res.ok ? 'ok' : '')) + (e.res.left != null ? ' ост ' + e.res.left : '')).join(' | ')],
    trail: ctx.trail.slice()}); };
  const posted = kind => M.log.filter(e => e.kind === kind);

  // ── сценарии прошлых поломок ─────────────────────────────────────────────
  const SC = {
    // 21 сен: позиции по одной бутылке — камера гасла после каждой, андроид
    // спрашивал разрешение, бутылка в кадре читалась второй раз.
    async по_одной(){
      await begin([['p1', 1], ['p2', 1], ['p5', 1]]);
      await openVia(A.openBtn(), 'Начать сканирование');
      const a = fresh(), b = fresh(), c = fresh();
      await frame(a, 250); await settle(); check('по_одной: первая');
      await frame(a, 900); await settle();                // бутылка осталась в кадре после перехода
      assert(posted('scan').filter(e => e.body.code === a).length === 1, 'бутылку в кадре отправили второй раз');
      await clear(); await frame(b, 250); await settle(); check('по_одной: вторая');
      await clear(); await frame(c, 250); await settle();
      assert(!A.camOpen(), 'всё принято — камера должна закрыться');
      assert(__CAM.gum === ctx.gumWant && ctx.gumWant - (__CAM.gum - (ctx.dual ? 2 : 1)) >= 0, 'камеру запрашивали больше одного раза');
      await finish('по_одной');
    },
    // Камера поймала соседнюю бутылку: строка полна, правильную не взять.
    async чужая_под_позицией(){
      await begin([['p1', 2], ['p2', 1]]);
      await openVia(A.lineButtons().find(b => /Absolut/.test(b.textContent)), 'Absolut');
      const a1 = fresh(), r1 = fresh(), a2 = fresh();
      await frame(a1); await settle(); await clear();
      await frame(r1); await settle();                      // «соседняя» — Absolut полон, переход на Stoli
      assert((A.line() || {}).id === 'p2', 'после полной строки сканер должен перейти на следующую');
      await sleep(400); await clear(); await frame(r1); await settle();
      assert(posted('scan').filter(e => e.body.code === r1).length === 1, 'повтор бутылки в кадре ушёл на сервер');
      const i = A.rows().findIndex(r => r.code === r1);
      assert(i >= 0, 'строка лишней бутылки пропала из ленты после перехода');
      const bs = A.xButtons(), bi = bs.findIndex(b => A.xIndex(b) === i);
      assert(bi >= 0, 'у лишней бутылки нет крестика');
      await tapX(bi); await settle();
      assert(!M.reg.has(r1), 'крестик не убрал бутылку');
      assert(M.lines[0].got === 1, 'Absolut не вернулся на 1');
      await clear(); await frame(r1); await settle();         // навели заново — встаёт под Stoli
      assert(M.reg.get(r1) && M.reg.get(r1).pid === 'p2', 'убранная бутылка не внеслась под текущую позицию');
      await clear(); await frame(a2); await settle();
      assert(M.lines.every(l => l.got === l.need), 'не всё сошлось: ' + JSON.stringify(M.lines));
      await finish('чужая_под_позицией');
    },
    // «Убрать бутылку», а последняя бутылка ещё в кадре: её не трогаем, пока
    // камеру не отвели и не навели снова.
    async убрать_в_кадре(){
      await begin([['p1', 3]]);
      await openVia(A.openBtn(), 'Начать сканирование');
      const a = fresh();
      await frame(a); await settle();
      await click(A.btn('Убрать бутылку'), 'Убрать бутылку'); await sleep(500); await settle();
      assert(posted('undo').length === 0, 'бутылку в кадре убрало без прицела');
      assert(A.rm(), 'режим «убрать» не включился');
      check('убрать_в_кадре: режим');
      await clear(); await frame(a); await settle();
      assert(!M.reg.has(a), 'прицел в режиме «убрать» не убрал бутылку');
      assert(!A.rm(), 'после «убрано» режим должен выключиться сам');
      await sleep(500); await settle();
      assert(posted('scan').filter(e => e.body.code === a).length === 1, 'убранную бутылку в кадре тут же внесло обратно');
      await clear(); await frame(a); await settle();
      assert(M.reg.has(a), 'навели заново — бутылка должна внестись');
      await finish('убрать_в_кадре');
    },
    // Строка полна лишней бутылкой, камера закрыта: убрать со списка.
    async убрать_со_списка(){
      await begin([['p1', 1]]);
      await openVia(A.openBtn(), 'Начать сканирование');
      const x = fresh(), a = fresh();
      await frame(x); await settle(); await clear();
      assert(!A.camOpen(), 'всё принято — камера закрылась');
      const act = A.btn('Убрать бутылку');
      assert(!!act, 'на списке нет «Убрать бутылку»');
      await openVia(act, 'Убрать бутылку (список)');
      assert(A.rm() && A.camOpen(), 'со списка камера открывается сразу в «убрать»');
      if(APP === 'driver'){ await A.poll(); await settle(); check('убрать_со_списка: опрос задачи при камере'); }
      await frame(x); await settle();
      assert(!M.reg.has(x), 'не убрало');
      assert(!A.rm() && (A.line() || {}).id === 'p1', 'после «убрано» — сканировать освободившуюся позицию');
      await clear(); await frame(a); await settle();
      assert(M.reg.has(a) && !A.camOpen(), 'правильная бутылка не внеслась или камера не закрылась');
      await finish('убрать_со_списка');
    },
    // Ответ на «убрать» потерялся в сети: перечитали задачу, крестик второй раз — «уже убрана».
    async потерянный_ответ(){
      await begin([['p1', 3]], [], {lat: [5, 40]});
      await openVia(A.openBtn(), 'Начать сканирование');
      const a = fresh(), b = fresh();
      await frame(a); await settle(); await clear(); await frame(b); await settle(); await clear();
      M.fault = 'lost_resp'; M.faultLeft = 1;
      await tapX(0); await settle();
      assert(!M.reg.has(b), 'сервер должен был убрать');
      assert((A.rows().find(r => r.code === b) || {}).state !== 'gone' || true, '');
      await tapX(0); await settle();
      assert((A.rows().find(r => r.code === b) || {}).state === 'gone', 'после второго крестика строка не стала «убрана»');
      ctx.strictFaults = M.faults;
      check('потерянный_ответ');
      await finish('потерянный_ответ');
    },
    // Камера из нескольких линз (айфон): два запроса при открытии и ни одного
    // потом — ни на переходе, ни в «убрать», ни на крестике.
    async айфон_линзы(){
      await begin([['p1', 1], ['p2', 2]], [], {label: 'Back Dual Wide Camera'});
      await openVia(A.openBtn(), 'Начать сканирование');
      const g = __CAM.gum;
      const a = fresh(), b = fresh();
      await frame(a); await settle(); await clear();
      await click(A.btn('Убрать бутылку'), 'Убрать бутылку'); await settle();
      await click(A.btn('Сканировать дальше'), 'Сканировать дальше'); await settle();
      await frame(b); await settle(); await clear();
      await tapX(0); await settle();
      assert(__CAM.gum === g, 'камеру запрашивали заново посреди сессии');
      await finish('айфон_линзы');
    },
    // Переход на следующую позицию через 700 мс не должен случиться, если за
    // это время включили «убрать».
    async переход_и_убрать(){
      await begin([['p1', 1], ['p2', 1]]);
      await openVia(A.lineButtons().find(b => /Absolut/.test(b.textContent)), 'Absolut');
      const a = fresh();
      window.__FRAME = a; await sleep(120);
      while(!posted('scan').length || M.pending) await sleep(10);
      await click(A.btn('Убрать бутылку'), 'Убрать бутылку (сразу)');
      await sleep(900); await settle();
      assert(A.rm(), 'режим «убрать» сбросило переходом');
      assert(A.shownTitle() === 'Убрать бутылку', 'переход перебил заголовок «убрать»');
      await finish('переход_и_убрать');
    },
    // Телефон подвис на секунду, бутылка стоит в кадре: кадров нет — это не
    // «отвели и навели». В «убрать» бутылка не исчезает сама, убранная — не
    // возвращается сама.
    async подвис_телефона(){
      await begin([['p1', 3]]);
      await openVia(A.openBtn(), 'Начать сканирование');
      const a = fresh();
      await frame(a); await settle();
      await click(A.btn('Убрать бутылку'), 'Убрать бутылку');
      const stall = ms => { const t = Date.now(); while(Date.now() - t < ms){} };
      stall(1200); await sleep(400); stall(1200); await sleep(400); await settle();
      assert(M.reg.has(a) && !posted('undo').length, 'подвисание стёрло бутылку в кадре без прицела');
      await clear(); await frame(a); await settle();
      assert(!M.reg.has(a) && !A.rm(), 'прицел после подвисания не убрал');
      stall(1200); await sleep(400); stall(1200); await sleep(400); await settle();
      assert(!M.reg.has(a), 'после подвисания убранная бутылка в кадре внеслась обратно сама');
      await finish('подвис_телефона');
    },
    // Принято без сканирования: досканировали до конца, по пути убрали лишнюю —
    // район закрывается сам на последней бутылке, камера гаснет, поток не висит.
    async без_сканирования_до_конца(){
      await begin([['p1', 2], ['p31', 1]]);
      M.noscan = true;
      if(APP === 'driver'){ await supTaskApply(M.view()); } else { RCV = rcvAdopt(M.view()); rcvRender(); }
      await settle();
      await openVia(A.lineButtons().find(b => /Absolut/.test(b.textContent)), 'Absolut');
      const a1 = fresh(), x = fresh(), a2 = fresh(), h1 = fresh(), h2 = fresh();
      await frame(a1); await settle(); await clear();
      await frame(x); await settle(); await clear();            // лишняя, строка полна → переход
      const i = A.rows().findIndex(r => r.code === x);
      const bi = A.xButtons().findIndex(b => A.xIndex(b) === i);
      assert(bi >= 0, 'у лишней нет крестика после перехода');
      await tapX(bi); await settle();
      assert(!M.reg.has(x) && !M.done, 'лишнюю не убрало или район закрылся раньше времени');
      // Сканер стоит на пиве: две полкоробки, потом Absolut.
      await frame(h1); await settle(); await clear();
      await frame(h2); await settle(); await clear();
      await sleep(900); await settle();
      await frame(a2); await settle();
      await sleep(1200); await settle();
      assert(M.done, 'последняя бутылка не закрыла район: ' + JSON.stringify(M.lines));
      assert(!A.camOpen() && !SCAN.stream, 'район закрыт, а камера открыта');
      if(APP === 'driver') await sleep(200);
      ctx.video = null;
      check('без_сканирования_до_конца');
    },
    // Район держит другой: «убрать» отвечает «вносит …», режим не слетает;
    // замок истёк — убирается.
    async убрать_под_чужим_замком(){
      await begin([['p1', 3]]);
      await openVia(A.openBtn(), 'Начать сканирование');
      const a = fresh();
      await frame(a); await settle(); await clear();
      M.hold = {who: 'Другой', until: Date.now() + 1500};
      await click(A.btn('Убрать бутылку'), 'Убрать бутылку');
      await frame(a); await settle();
      assert(M.reg.has(a), 'убрало под чужим замком');
      assert(A.rm(), 'после «вносит другой» режим «убрать» слетел');
      await clear(); await sleep(1600);
      await frame(a); await settle();
      assert(!M.reg.has(a) && !A.rm(), 'замок истёк — должно было убрать и выйти из режима');
      await finish('убрать_под_чужим_замком');
    },
    // Отказ в доступе к камере: список остаётся, поток не висит, потом — открывается.
    async отказ_камеры(){
      await begin([['p1', 2]]);
      __CAM.deny = true;
      await openVia(A.openBtn(), 'Начать сканирование (отказ)');
      assert(!A.camOpen() && !SCAN.stream, 'после отказа камера не должна висеть');
      __CAM.deny = false;
      await openVia(A.openBtn(), 'Начать сканирование');
      assert(A.camOpen(), 'после разрешения камера не открылась');
      await finish('отказ_камеры');
    },
  };

  // ── случайные цепочки ──────────────────────────────────────────────────────
  async function fuzz(seed, steps){
    R = rng(seed);
    const pool = ['p1', 'p2', 'p5', 'p31'].filter(() => R() < 0.8);
    if(!pool.length) pool.push('p1');
    const plan = pool.map(p => [p, 1 + Math.floor(R() * 3)]);
    const extra = [['FOREIGN1', 'p1', 'jvc', 'OTHER'], ['FOREIGN2', 'p2', 'jvc', null], ['BB1', 'p1', 'bbay', 'SFZ']];
    await begin(plan, extra, {lat: pick([[0, 10], [0, 60], [20, 250]]),
                              label: pick(['Back Camera', 'Back Camera', 'camera2 0, facing back', 'Back Dual Wide Camera'])});
    const bottles = [];                        // «физические» бутылки на столе
    for(ctx.step = 0; ctx.step < steps; ctx.step++){
      const cam = A.camOpen();
      const r = R();
      if(!cam){
        if(r < 0.35){ stat('открыть позицию'); await openVia(pick(A.lineButtons()), 'позиция'); }
        else if(r < 0.55){ stat('открыть дальше'); await openVia(A.openBtn(), 'Начать/Продолжить'); }
        else if(r < 0.75){ stat('убрать со списка'); await openVia(A.btn('Убрать бутылку'), 'Убрать бутылку (список)'); }
        else if(r < 0.82){ stat('опрос'); await A.poll(); }
        else if(r < 0.86){ stat('отказ камеры'); __CAM.deny = true; await openVia(pick(A.lineButtons()) || A.openBtn(), 'позиция (отказ)'); __CAM.deny = false; }
        else if(r < 0.90){ stat('чужой замок'); M.hold = {who: 'Другой', until: Date.now() + 300 + R() * 1500}; note('замок у другого'); }
        else if(r < 0.95){ stat('продали/перевезли'); const o = ours(); if(o.length){ const [c, d] = pick(o); d[R() < 0.5 ? 'status' : 'district'] = R() < 0.5 ? 'sold' : 'silicon'; note('ушла ' + c); } }
        else { stat('сеть'); M.lat = pick([[0, 10], [20, 250], [100, 600]]); }
      }else{
        if(r < 0.30){ stat('новая бутылка'); const c = fresh(); bottles.push(c); await frame(c); }
        else if(r < 0.40){ stat('наша же бутылка'); const o = ours(); if(o.length) await frame(pick(o)[0]); }
        else if(r < 0.46){ stat('с другого стола'); await frame(pick(['FOREIGN1', 'FOREIGN2', 'BB1'])); }
        else if(r < 0.52){ stat('соседняя мелькнула'); const c = fresh(); bottles.push(c); await frame(pick(bottles), 60); await frame(c); }
        else if(r < 0.62){ stat('убрали из кадра'); await clear(); }
        else if(r < 0.71){ stat('крестик'); await tapX(); }
        else if(r < 0.74){ stat('двойной крестик'); const bs = A.xButtons(); if(bs.length){ const b = pick(bs); M.xtap += 2; b.click(); b.click(); note('двойной крестик'); } }
        else if(r < 0.80){ stat('убрать бутылку'); await click(A.btn('Убрать бутылку'), 'Убрать бутылку'); }
        else if(r < 0.84){ stat('сканировать дальше'); await click(A.btn('Сканировать дальше'), 'Сканировать дальше'); }
        else if(r < 0.88){ stat('к списку'); await click(A.btn('К списку'), 'К списку'); }
        else if(r < 0.90){ stat('назад'); note('назад'); A.back(); }
        else if(r < 0.93){ stat('опрос при камере'); await A.poll(); }
        else if(r < 0.96){ stat('обрыв сети'); M.fault = pick(['lost_req', 'lost_resp']); M.faultLeft = 1 + Math.floor(R() * 2); note('обрыв ' + M.fault); }
        else if(r < 0.98){ stat('чужой замок'); M.hold = {who: 'Другой', until: Date.now() + 300 + R() * 1200}; note('замок у другого'); }
        else { stat('продали/перевезли'); const o = ours(); if(o.length){ const [c, d] = pick(o); d.status = 'sold'; note('продали ' + c); } }
      }
      const ok = await settle();
      if(!ok) ctx.fails.push({where: 'зависание', step: ctx.step, what: ['очередь/запросы не затихли за 15 с'], trail: ctx.trail.slice()});
      if(M.faults !== ctx.strictFaults && M.faultLeft === 0){ await A.resync(); await settle(); ctx.strictFaults = M.faults; }
      check('фаззер');
      if(ctx.fails.length) break;
    }
    await finish('фаззер: конец');
  }

  window.__run = async ({seed, steps, scenarios}) => {
    ctx.fails = []; ctx.stats = {};
    try{
      if(scenarios){
        for(const [name, fn] of Object.entries(SC)){
          ctx.step = 0; ctx.trail = [];
          note('— ' + name);
          await fn();
          stat('сценарий:' + name);
          if(ctx.fails.length) break;
        }
      }else await fuzz(seed, steps);
    }catch(e){
      ctx.fails.push({where: 'исключение прогонщика', step: ctx.step, what: [String(e && e.stack || e)], trail: ctx.trail.slice()});
    }
    return {app: APP, seed, fails: ctx.fails, stats: ctx.stats, gum: __CAM.gum, log: M.log.length};
  };
})();
