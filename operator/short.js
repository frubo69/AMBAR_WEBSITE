// Недовоз от оператора (владелец, 21 сен 2026: «операторы тоже забирают —
// добавь им возможность отправлять репорт о том, что чего-то нет; конечное
// решение принимаю я через AMBAR STAR»). Ручки на сервере стояли с 29 сен,
// экрана не было.
//
// Два шага в одном окне: район → чего и сколько не дали. Тот же вопрос и те же
// правила, что у водителя: больше, чем осталось принять, поставить нельзя;
// пока прошлый отчёт ждёт решения, второй не уходит. Задачу это не трогает —
// водитель спокойно сканирует то, что привезли.
//
// Отдельным файлом: окно само приносит свою разметку и стили (классы .osh-*),
// в панели от него только кнопка.
(function(){
  const ST = {tasks: null, sel: null, qty: {}, note: '', busy: false, err: ''};
  const $ = id => document.getElementById(id);
  // curOp в панели объявлен через let — в window его нет, берём по имени.
  const me = () => { try{ return curOp || ''; }catch(e){ return ''; } };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const q = v => { v = Math.round((+v || 0) * 2) / 2; return String(v).replace('.', ','); };
  const pl = (n, a, b, c) => { n = Math.abs(Math.round(n)) % 100; const k = n % 10;
    return n > 10 && n < 20 ? c : k === 1 ? a : k > 1 && k < 5 ? b : c; };
  const say = (t, kind) => { try{ window.toast(t, kind); }catch(e){} };
  const tap = k => { try{ window.haptic(k || 'light'); }catch(e){} };
  const X = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const BACK = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';
  const CHEV = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>';

  function mount(){
    if($('oshOv')) return;
    const css = document.createElement('style');
    css.textContent = `
.osh-sheet{width:min(520px,100%);max-height:88vh;overflow:auto;display:flex;flex-direction:column}
.osh-hd{display:flex;align-items:center;gap:10px;margin:-2px 0 14px}
.osh-hd b{flex:1;min-width:0;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.osh-hd i{display:block;font-style:normal;font-size:12.5px;color:var(--sub);font-weight:500;letter-spacing:0;text-transform:none;margin-top:2px}
.osh-bk{width:34px;height:34px;border-radius:11px;background:var(--card2);border:1px solid var(--border3);color:var(--text);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.osh-list{display:flex;flex-direction:column;gap:8px}
.osh-d{display:flex;align-items:center;gap:12px;width:100%;text-align:left;padding:13px 14px;border-radius:14px;background:var(--card);border:1px solid var(--border3);color:var(--text);font-family:inherit}
.osh-d:disabled{opacity:.55}
.osh-c{min-width:38px;padding:5px 0;border-radius:9px;background:var(--gold-soft);color:var(--gold2);font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:13px;text-align:center;flex-shrink:0}
.osh-n{flex:1;min-width:0}
.osh-n b{display:block;font-size:14.5px;font-weight:600}
.osh-n i{display:block;font-style:normal;font-size:12px;color:var(--sub);margin-top:2px}
.osh-n i.wait{color:var(--gold2)}
.osh-v{font-family:'Space Grotesk',sans-serif;font-size:14px;font-weight:700;color:var(--sub);white-space:nowrap}
.osh-v u{text-decoration:none;font-weight:500;opacity:.7}
.osh-ch{color:var(--sub);display:flex}
.osh-r{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--border3)}
.osh-r:last-child{border-bottom:0}
.osh-r img{width:34px;height:44px;object-fit:contain;flex-shrink:0}
.osh-rn{flex:1;min-width:0;font-size:14px;font-weight:600}
.osh-rn i{display:block;font-style:normal;font-size:12px;font-weight:500;color:var(--sub);margin-top:2px}
.osh-st{display:flex;align-items:center;gap:4px;flex-shrink:0}
.osh-st button{width:36px;height:36px;border-radius:11px;background:var(--card2);border:1px solid var(--border3);color:var(--gold2);font-size:19px;font-weight:700;display:flex;align-items:center;justify-content:center}
.osh-st b{min-width:34px;text-align:center;font-family:'Space Grotesk',sans-serif;font-size:16px;font-weight:700;color:var(--sub)}
.osh-r.on .osh-st b{color:var(--gold2)}
.osh-card{background:var(--card);border:1px solid var(--border3);border-radius:14px;padding:2px 14px}
.osh-sum{font-size:12.5px;color:var(--sub);margin:12px 2px 10px}
.osh-note{width:100%;min-height:70px;border-radius:13px;background:var(--card);border:1px solid var(--border3);color:var(--text);font-family:inherit;font-size:14px;padding:12px 13px;resize:none}
.osh-go{width:100%;margin-top:14px;height:50px;border-radius:14px;border:0;background:linear-gradient(90deg,#E8C98A,#C9A96E);color:#1a1408;font-family:inherit;font-size:15px;font-weight:700}
.osh-go:disabled{opacity:.4}
.osh-empty{padding:34px 10px;text-align:center;color:var(--sub);font-size:13.5px;line-height:1.5}
.osh-empty b{display:block;color:var(--text);font-size:15px;margin-bottom:5px}
.osh-sk{height:62px;border-radius:14px;background:var(--card);border:1px solid var(--border3);opacity:.6}`;
    document.head.appendChild(css);
    const ov = document.createElement('div');
    ov.className = 'ov'; ov.id = 'oshOv';
    ov.addEventListener('click', e => { if(e.target === ov) close(); });
    ov.innerHTML = '<div class="sheet osh-sheet"><div id="oshBody"></div></div>';
    document.body.appendChild(ov);
  }

  function head(t, s, back){
    return `<div class="osh-hd">${back ? `<button class="osh-bk" onclick="opShort.back()" aria-label="Назад">${BACK}</button>` : ''}
      <b>${esc(t)}${s ? `<i>${esc(s)}</i>` : ''}</b>
      <button class="oc-x" onclick="opShort.close()" aria-label="Закрыть">${X}</button></div>`;
  }

  // Что подписать под районом: кто принимает и что с прошлым отчётом.
  function sub(t){
    const sh = t.short;
    if(sh && sh.status === 'pending') return ['Отчёт ждёт решения старшего', 'wait'];
    const кто = t.driver ? t.driver : 'никто не взял';
    return [`${кто} · ${t.noscan_at ? 'без сканирования' : t.got ? 'идёт приёмка' : 'ждёт'}`, ''];
  }

  function paint(){
    const box = $('oshBody'); if(!box) return;
    if(ST.sel) return paintTask(box);
    if(ST.err) { box.innerHTML = head('Недовоз') +
      `<div class="osh-empty"><b>Не удалось загрузить</b>Проверьте связь<button class="osh-go" onclick="opShort.open()">Повторить</button></div>`; return; }
    if(!ST.tasks){ box.innerHTML = head('Недовоз') +
      '<div class="osh-list"><div class="osh-sk"></div><div class="osh-sk"></div><div class="osh-sk"></div></div>'; return; }
    if(!ST.tasks.length){ box.innerHTML = head('Недовоз') +
      '<div class="osh-empty"><b>Открытых приёмок нет</b>Сообщать не о чем</div>'; return; }
    box.innerHTML = head('Недовоз', 'Выберите район') + `<div class="osh-list">${ST.tasks.map((t, i) => {
      const [s, k] = sub(t);
      const ждёт = t.short && t.short.status === 'pending';
      return `<button class="osh-d" ${ждёт || !(t.left > 0) ? 'disabled' : ''} onclick="opShort.pick(${i})">
        <span class="osh-c">${esc(t.district_code)}</span>
        <span class="osh-n"><b>${esc(t.district_name)}${t.extra && t.base ? ' · ' + esc(t.base) : ''}</b><i class="${k}">${esc(s)}</i></span>
        <span class="osh-v">${q(t.got)}<u>/${q(t.need)}</u></span>
        <span class="osh-ch">${CHEV}</span></button>`; }).join('')}</div>`;
  }

  const max = l => Math.max(0, (+l.need || 0) - (+l.got || 0));

  function paintTask(box){
    const t = ST.sel;
    const lines = (t.lines || []).filter(l => max(l) > 0);
    const всего = Object.values(ST.qty).reduce((a, b) => a + (+b || 0), 0);
    const было = ($('oshNote') || {}).value;
    if(было != null) ST.note = было;
    box.innerHTML = head(`${t.district_code} ${t.district_name}`, 'Укажите, сколько не привезли', true) +
      `<div class="osh-card">${lines.map(l => { const v = +ST.qty[l.id] || 0;
        return `<div class="osh-r ${v ? 'on' : ''}">
          <img src="/products/${esc(l.id)}.webp" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
          <span class="osh-rn">${esc(l.name)}<i>по заявке ${q(l.need)}${l.got ? ' · внесено ' + q(l.got) : ''}</i></span>
          <span class="osh-st"><button onclick="opShort.step('${esc(l.id)}',-1)" aria-label="меньше">−</button>
            <b>${q(v)}</b><button onclick="opShort.step('${esc(l.id)}',1)" aria-label="больше">+</button></span></div>`; }).join('')}</div>
       <div class="osh-sum">${всего ? `Не дали ${q(всего)} ${pl(всего, 'единицу', 'единицы', 'единиц')}` : 'Пока ничего не отмечено'}</div>
       <textarea class="osh-note" id="oshNote" placeholder="Что сказал магазин — если есть что сказать"></textarea>
       <button class="osh-go" id="oshGo" ${всего && !ST.busy ? '' : 'disabled'} onclick="opShort.send()">Отправить старшему</button>`;
    $('oshNote').value = ST.note;
  }

  async function open(){
    if(!me()){ try{ window.showOv('opOv'); }catch(e){} return; }
    mount();
    Object.assign(ST, {tasks: null, sel: null, qty: {}, note: '', busy: false, err: ''});
    paint(); $('oshOv').classList.add('show'); tap('light');
    try{
      const r = await window.opApi.opFetch('/api/operator/supply/open', {params: {as: me()}});
      ST.tasks = (r.tasks || []).filter(t => !t.done_at && !t.cancelled_at);
    }catch(e){ ST.err = '1'; }
    paint();
  }

  function close(){ const o = $('oshOv'); if(o) o.classList.remove('show'); }
  function back(){ ST.sel = null; ST.qty = {}; ST.note = ''; paint(); }
  function pick(i){ ST.sel = (ST.tasks || [])[i] || null; ST.qty = {}; ST.note = ''; tap('light'); paint(); }
  function step(pid, d){
    const l = ((ST.sel || {}).lines || []).find(x => x.id === pid); if(!l) return;
    // Пиво считают коробками, и на коробке два кода — шаг полкоробки.
    const s = (+l.unit || 1) > 1 ? 0.5 : 1;
    ST.qty[pid] = Math.max(0, Math.min((+ST.qty[pid] || 0) + d * s, max(l)));
    tap('sel'); paint();
  }

  async function send(){
    const t = ST.sel; if(!t || ST.busy) return;
    const lines = Object.entries(ST.qty).filter(([, v]) => (+v || 0) > 0).map(([id, qty]) => ({id, qty: +qty}));
    if(!lines.length) return say('Укажите, чего не дали', 'err');
    ST.note = ($('oshNote') || {}).value || '';
    ST.busy = true; paint();
    try{
      const r = await window.opApi.opFetch(`/api/operator/supply/${encodeURIComponent(t.supply_id)}/short`,
        {method: 'POST', body: {district: t.district, lines, note: ST.note.trim(), as: me()}});
      if(!r.ok) throw new Error(r.verdict || '');
      // Последнее действие закрывает окно: отчёт ушёл, делать здесь больше нечего.
      close(); tap('medium');
      say('Отправлено старшему — ждём решения');
    }catch(e){
      const v = e && e.message;
      say(v === 'pending' ? 'Прошлый отчёт ещё ждёт решения'
        : v === 'closed' ? 'Приёмка уже закрыта'
        : v === 'empty' ? 'Столько не дать не могли — проверьте числа'
        : 'Не удалось отправить', 'err');
    }finally{ ST.busy = false; if($('oshOv').classList.contains('show')) paint(); }
  }

  window.opShort = {open, close, back, pick, step, send};
})();
