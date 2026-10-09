// Итоги смены по районам и водителям — только смотреть (владелец, 30 сен
// 2026: «операторы заполняют заказы, но не видят, сколько сегодня на районах
// было заказов и сколько это в деньгах… и сколько какой водитель наработал»).
//
// Сверху итог по всем районам, ниже карточка на район: сколько заказов и на
// какую сумму, под ней водители района тем же счётом. «Вчера» — потому что
// смена кончается под утро, и спрашивают о ней уже после, когда сегодняшний
// день ещё пуст. Заказ «без оплаты» в штуках есть, в деньгах нет. Пока окно
// открыто на сегодняшнем дне, числа перечитываются сами.
//
// Сверка смены (владелец, 9 окт 2026): под водителем — наличных на руках против
// приложения, правки товара и ответы оператора: «Так и есть» по деньгам
// (факт за водителем — старшему в «Сбор выручки» и в книгу дня), «Принять» /
// «Отклонить» по правкам («+N» принятая — заказ той же смены этим
// водителем; «−N» оператор правит в заказе руками и жмёт «Готово»). Пока
// несовпадение без ответа, код района мигает красным.
//
// Отдельным файлом, как stock.js: окно приносит свою разметку и стили (.oday-*).
(function(){
  const ST = {d: null, err: '', day: 'today', t: null, at: 0, busy: false};
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const me = () => { try{ return curOp || ''; }catch(e){ return ''; } };
  const tap = k => { try{ window.haptic(k || 'light'); }catch(e){} };
  const n = v => String(Math.round(+v || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const pl = (k, a, b, c) => { k = Math.abs(k) % 100; const m = k % 10;
    return k > 10 && k < 20 ? c : m === 1 ? a : m > 1 && m < 5 ? b : c; };
  const зак = k => `${k} ${pl(k, 'заказ', 'заказа', 'заказов')}`;
  const X = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const CAR = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 13h16"/><path d="M6 13V8a3 3 0 0 1 3-3h6a3 3 0 0 1 3 3v5"/><circle cx="8" cy="17" r="2"/><circle cx="16" cy="17" r="2"/></svg>';
  const MONTHS = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  const dayRu = s => { const p = String(s || '').split('-'); return p.length === 3 ? `${+p[2]} ${MONTHS[+p[1] - 1]}` : ''; };

  function mount(){
    if($('odayOv')) return;
    const css = document.createElement('style');
    css.textContent = `
.oday-sheet{width:min(560px,100%);height:min(92vh,92dvh);display:flex;flex-direction:column;padding:18px 16px 0;overflow:hidden}
.oday-hd{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0}
.oday-hd b{flex:1;min-width:0;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.oday-hd i{display:block;font-style:normal;font-size:12.5px;color:var(--sub);font-weight:500;letter-spacing:0;text-transform:none;margin-top:2px}
.oday-seg{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:4px;border-radius:13px;background:var(--card);border:1px solid var(--border3);flex-shrink:0}
.oday-seg button{height:34px;border-radius:10px;border:0;background:none;color:var(--sub);font-family:inherit;font-size:13px;font-weight:600}
.oday-seg button.on{background:var(--gold-soft);color:var(--gold2)}
.oday-body{flex:1;min-height:0;overflow:auto;margin:12px -16px 0;padding:0 16px 18px;-webkit-overflow-scrolling:touch}
.oday-tot{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.oday-tile{padding:13px 14px;border-radius:14px;background:var(--card);border:1px solid var(--border3)}
.oday-tile u{display:block;text-decoration:none;font-size:11.5px;color:var(--sub);font-weight:600;letter-spacing:.06em;text-transform:uppercase}
.oday-tile b{display:block;margin-top:5px;font-family:'Space Grotesk',sans-serif;font-size:24px;font-weight:700;white-space:nowrap}
.oday-tile b s{text-decoration:none;font-size:13px;font-weight:600;color:var(--sub);margin-left:5px}
.oday-tile.gold b{color:var(--gold2)}
.oday-c{border-radius:16px;background:var(--card);border:1px solid var(--border3);margin-bottom:10px;overflow:hidden}
.oday-ch{display:flex;align-items:center;gap:11px;padding:13px 14px}
.oday-code{min-width:38px;padding:5px 0;border-radius:9px;background:var(--card2);color:var(--sub);font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:13px;text-align:center;flex-shrink:0}
.oday-c.mine .oday-code{background:var(--gold-soft);color:var(--gold2)}
.oday-cn{flex:1;min-width:0}
.oday-cn b{display:block;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.oday-cn i{display:block;font-style:normal;font-size:12px;color:var(--sub);margin-top:2px}
.oday-cn i em{font-style:normal;color:var(--gold2)}
.oday-cv{text-align:right;flex-shrink:0;font-family:'Space Grotesk',sans-serif;font-size:19px;font-weight:700;color:var(--gold2);white-space:nowrap}
.oday-cv s{text-decoration:none;font-size:11.5px;font-weight:600;color:var(--sub);margin-left:4px}
.oday-c.z .oday-cv{color:var(--sub);opacity:.6}
.oday-dr{display:flex;align-items:center;gap:10px;padding:10px 14px;border-top:1px solid var(--border3);font-size:13.5px}
.oday-dr .ico{color:var(--sub);display:flex;flex-shrink:0}
.oday-dn{flex:1;min-width:0;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.oday-dk{color:var(--sub);font-size:12.5px;white-space:nowrap}
.oday-dk em{font-style:normal;color:var(--gold2)}
.oday-dv{min-width:64px;text-align:right;font-family:'Space Grotesk',sans-serif;font-weight:700;white-space:nowrap}
.oday-dr.z .oday-dn,.oday-dr.z .oday-dv{color:var(--sub);font-weight:500}
.oday-note{font-size:12px;color:var(--sub);line-height:1.5;margin:12px 2px 0}
/* Сверка под водителем: панель с цветной кромкой (красная — ждёт ответа,
   золотая — сверяет, зелёная — всё сошлось), зоны «Деньги» и «Товар» со
   своими заголовками, внизу мини-шаги водителя и статус. */
.orc{position:relative;margin:0 10px 10px;border-radius:14px;background:var(--card2);border:1px solid var(--border3);overflow:hidden}
.orc::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--gold2)}
.orc.alert::before{background:#E05555}
.orc.ok::before{background:#3ddc84}
.orc-z{padding:10px 14px 12px 16px}
.orc-z + .orc-z,.orc-foot{border-top:1px solid var(--border3)}
.orc-h{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--sub);margin-bottom:8px}
.orc-money{display:flex;align-items:flex-end;gap:14px}
.orc-num{flex:1;min-width:0}
.orc-num i{display:block;font-style:normal;font-size:11.5px;color:var(--sub);margin-bottom:3px;white-space:nowrap}
.orc-num b{display:block;font-family:'Space Grotesk',sans-serif;font-size:20px;font-weight:700;line-height:1.1;white-space:nowrap}
.orc-diff{flex:0 0 auto;align-self:center;padding:7px 11px;border-radius:10px;font-family:'Space Grotesk',sans-serif;font-size:16px;font-weight:700;background:var(--gold-soft);color:var(--gold2);white-space:nowrap}
.orc-diff.dn{background:rgba(224,85,85,.14);color:#E05555}
.orc-diff.ok{display:inline-flex;align-items:center;gap:5px;font-family:inherit;font-size:12.5px;background:rgba(61,220,132,.12);color:#3ddc84}
.orc-diff.wait{font-family:inherit;font-size:12.5px;background:var(--card);color:var(--sub)}
.orc-act{margin-top:10px;display:flex;justify-content:flex-end}
.orc-btn{height:34px;padding:0 14px;border-radius:10px;border:1px solid var(--border3);background:var(--card);color:var(--text);font-family:inherit;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.orc-btn.gold{background:linear-gradient(135deg,#E8C98A,#C9A96E);color:#1a1408;border-color:transparent}
.orc-btn:active{transform:scale(.97)}
.orc-st{margin-top:10px;display:flex;align-items:center;gap:6px;font-size:12.5px;color:#3ddc84}
.orc-st svg{width:13px;height:13px;flex-shrink:0}
.orc-lnk{margin-left:auto;background:none;border:0;color:var(--sub);font-family:inherit;font-size:12.5px;font-weight:600;cursor:pointer;padding:4px 0 4px 10px}
.orc-fix{display:flex;align-items:center;gap:10px;padding:7px 0;flex-wrap:wrap}
.orc-fix + .orc-fix{border-top:1px solid var(--border3)}
.orc-d{flex:0 0 auto;min-width:38px;height:28px;padding:0 8px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;font-size:14px;font-weight:700;background:var(--gold-soft);color:var(--gold2)}
.orc-d.dn{background:rgba(224,85,85,.14);color:#E05555}
.orc-n{flex:1 1 110px;min-width:0;font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.orc-bs{flex:0 0 auto;margin-left:auto;display:flex;gap:6px}
.orc-bs .orc-btn{height:30px;padding:0 11px;font-size:12.5px}
.orc-res{flex:0 0 auto;margin-left:auto;font-size:12.5px;color:var(--sub);white-space:nowrap}
.orc-res.ok{color:#3ddc84;display:inline-flex;align-items:center;gap:4px}
.orc-res svg{width:12px;height:12px}
.orc-foot{display:flex;align-items:center;justify-content:space-between;gap:6px 10px;flex-wrap:wrap;padding:9px 14px 10px 16px}
.orc-steps{display:flex;gap:5px}
.orc-steps b{display:inline-flex;align-items:center;gap:4px;height:22px;padding:0 7px;border-radius:7px;font-size:10.5px;font-weight:700;color:var(--sub);background:var(--card);border:1px solid var(--border3);white-space:nowrap}
.orc-steps b.done{color:#3ddc84;border-color:rgba(61,220,132,.35);background:rgba(61,220,132,.07)}
.orc-steps b.pend{color:var(--gold2);border-color:rgba(201,169,110,.45);background:rgba(201,169,110,.08)}
.orc-steps b svg{width:11px;height:11px}
.oday-c.alert .oday-code{background:rgba(224,85,85,.18);color:#E05555;animation:odayBlink 1s steps(2) infinite}
@keyframes odayBlink{50%{opacity:.3}}
.oday-sk{height:118px;border-radius:16px;background:var(--card);margin-bottom:10px;opacity:.6}
@media(max-width:520px){
  .oday-sheet{padding:16px 12px 0;border-radius:20px 20px 0 0;height:94dvh;align-self:flex-end}
  .oday-body{margin:12px -12px 0;padding:0 12px 18px}
}`;
    document.head.appendChild(css);
    const ov = document.createElement('div');
    ov.className = 'ov'; ov.id = 'odayOv';
    ov.addEventListener('click', e => { if(e.target === ov) close(); });
    ov.innerHTML = `<div class="sheet oday-sheet">
      <div class="oday-hd"><b>Итоги смены<i id="odaySub"></i></b>
        <button class="oc-x" onclick="opDay.close()" aria-label="Закрыть">${X}</button></div>
      <div class="oday-seg"><button id="odayT" onclick="opDay.day('today')">Сегодня</button>
        <button id="odayY" onclick="opDay.day('yesterday')">Вчера</button></div>
      <div class="oday-body" id="odayBody"></div></div>`;
    document.body.appendChild(ov);
  }

  const hm = iso => { if(!iso) return ''; const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Dubai'}); };
  const sign = v => (v > 0 ? '+' : '−') + n(Math.abs(v || 0));
  const CHECK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 7"/></svg>';
  // Сверка одного водителя: панель из зон — «Деньги» (на руках · по
  // приложению · разница, «Так и есть»), «Товар» (правки с ответами), внизу
  // мини-шаги водителя (что он отметил) и статус.
  const CLOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
  function rcHtml(v){
    const r = v.recon; if(!r || !r.started) return '';
    const who = esc(v.name), ok = r.ok || [], ok1 = ok.includes(1);
    const cls = r.alert ? 'alert' : (r.confirmed && !r.alert ? 'ok' : '');
    // деньги
    let money = '';
    if(r.cash != null){
      const d = r.diff || 0;
      const badge = !ok1 ? '<span class="orc-diff wait">считает</span>'
        : r.op_fact != null ? `<span class="orc-diff${(r.op_gap || 0) < 0 ? ' dn' : ''}">${sign(r.op_gap)}</span>`
        : d ? `<span class="orc-diff${d < 0 ? ' dn' : ''}">${sign(d)}</span>`
        : `<span class="orc-diff ok">${CHECK}сошлось</span>`;
      const after = r.op_fact != null
        ? `<div class="orc-st">${CHECK}так и есть${r.op_fact_by ? ' · ' + esc(r.op_fact_by) : ''}${r.op_fact_at ? ' · ' + hm(r.op_fact_at) : ''}
             <button class="orc-lnk" onclick="opDay.fact('${who}', false)">Снять</button></div>`
        : (ok1 && d) ? `<div class="orc-act"><button class="orc-btn gold" onclick="opDay.fact('${who}', true)">Так и есть</button></div>` : '';
      money = `<div class="orc-z"><div class="orc-h">Деньги</div>
        <div class="orc-money">
          <span class="orc-num"><i>На руках</i><b>${n(r.cash)}</b></span>
          <span class="orc-num"><i>По приложению</i><b>${n(r.cash_app)}</b></span>${badge}</div>${after}</div>`;
    }
    // товар
    const fixes = (r.fixes || []).map(f => {
      const args = `'${who}','${esc(f.pid)}',${+f.delta},${+(f.pcs || 0)}`;
      const res = f.ok === true ? `<span class="orc-res ok">${CHECK}${f.order_id ? 'принято · #' + esc(f.order_id) : 'готово'}</span>`
        : f.ok === false ? '<span class="orc-res">отклонено</span>'
        : `<span class="orc-bs"><button class="orc-btn gold" onclick="opDay.fix(${args},true)">${f.delta > 0 ? 'Принять' : 'Готово'}</button><button class="orc-btn" onclick="opDay.fix(${args},false)">Отклонить</button></span>`;
      return `<div class="orc-fix"><span class="orc-d${f.delta < 0 ? ' dn' : ''}">${f.delta > 0 ? '+' : '−'}${Math.abs(f.delta)}</span>
        <span class="orc-n">${esc(f.name)}${f.pcs ? ' ×' + f.pcs : ''}</span>${res}</div>`;
    }).join('');
    const goods = fixes ? `<div class="orc-z"><div class="orc-h">Товар</div>${fixes}</div>` : '';
    // итог: мини-шаги водителя и статус
    const steps = [[1, 'Деньги'], [2, 'Заказы'], [3, 'Товар']].map(([k, t]) => {
      const done = ok.includes(k), pend = k === 3 && r.fixes_status === 'sent';
      return `<b class="${done ? 'done' : pend ? 'pend' : ''}">${done ? CHECK : pend ? CLOCK : ''}${t}</b>`; }).join('');
    const status = r.confirmed ? `<span class="orc-res ok">${CHECK}подтвердил${r.confirmed_at ? ' · ' + hm(r.confirmed_at) : ''}</span>`
      : '<span class="orc-res">сверяет</span>';
    return `<div class="orc ${cls}">${money}${goods}<div class="orc-foot"><span class="orc-steps">${steps}</span>${status}</div></div>`;
  }
  const when = () => { const d = new Date(ST.at);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

  function paint(){
    const box = $('odayBody'); if(!box) return;
    $('odayT').classList.toggle('on', ST.day === 'today');
    $('odayY').classList.toggle('on', ST.day === 'yesterday');
    const d = ST.d;
    if(!d){
      $('odaySub').textContent = ST.err ? '' : 'загрузка…';
      box.innerHTML = ST.err
        ? (window.loadFail ? window.loadFail('opDay.day()') : '<div class="oday-note">Не удалось загрузить</div>')
        : '<div class="oday-tot"><div class="oday-sk" style="height:76px;margin:0"></div><div class="oday-sk" style="height:76px;margin:0"></div></div>'
          + Array(4).fill('<div class="oday-sk"></div>').join('');
      return;
    }
    $('odaySub').textContent = `${dayRu(d.day)}${d.today ? ' · обновлено ' + when() : ''}`;
    const mine = new Set(d.mine || []), t = d.total || {};
    const cards = (d.districts || []).map(r => {
      const parts = [зак(r.done)];
      if(r.work) parts.push(`<em>в работе ${r.work}</em>`);
      if(r.new) parts.push(`<em>новых ${r.new}</em>`);
      if(r.cancelled) parts.push(`отменено ${r.cancelled}`);
      return `<div class="oday-c${mine.has(r.id) ? ' mine' : ''}${r.done ? '' : ' z'}${r.recon_alert ? ' alert' : ''}">
        <div class="oday-ch"><span class="oday-code">${esc(r.code)}</span>
          <span class="oday-cn"><b>${esc(r.name)}</b><i>${parts.join(' · ')}</i></span>
          <span class="oday-cv">${n(r.aed)}<s>AED</s></span></div>
        ${(r.drivers || []).map(v => `<div class="oday-dr${v.done || v.work ? '' : ' z'}">
          <span class="ico">${CAR}</span><span class="oday-dn">${esc(v.name)}</span>
          <span class="oday-dk">${зак(v.done)}${v.work ? ` · <em>везёт ${v.work}</em>` : ''}</span>
          <span class="oday-dv">${n(v.aed)}</span></div>${rcHtml(v)}`).join('')}</div>`;
    }).join('');
    box.innerHTML = `<div class="oday-tot">
        <div class="oday-tile"><u>Заказов</u><b>${n(t.done)}${t.work ? `<s>+ ${t.work} в работе</s>` : ''}</b></div>
        <div class="oday-tile gold"><u>Выручка</u><b>${n(t.aed)}<s>AED</s></b></div></div>
      ${cards}
      <div class="oday-note">Считаются доставленные заказы смены. Заказ «без оплаты» в числе заказов есть,
        в выручке его нет. Водитель с другого района стоит там, куда он вёз.</div>`;
  }

  async function load(тихо){
    if(ST.busy) return; ST.busy = true;
    const day = ST.day;
    try{
      const d = await window.opApi.opFetch('/api/operator/day/board', {params: {as: me(), day}});
      // Пока шёл ответ, день могли переключить — тогда он уже не нужен.
      if(day === ST.day){ ST.d = d; ST.err = ''; ST.at = Date.now(); }
    }catch(e){ if(day === ST.day && (!тихо || !ST.d)){ ST.d = null; ST.err = '1'; } }
    finally{ ST.busy = false; }
    if(day === ST.day && $('odayOv') && $('odayOv').classList.contains('show')){
      const b = $('odayBody'), y = b ? b.scrollTop : 0;
      paint();
      if(b && тихо) b.scrollTop = y;
    }
    if(day !== ST.day) return load();
  }

  async function open(){
    if(!me()){ try{ window.showOv('opOv'); }catch(e){} return; }
    mount();
    Object.assign(ST, {d: null, err: '', day: 'today'});
    paint(); $('odayOv').classList.add('show'); tap('light');
    await load();
    if(ST.t) clearInterval(ST.t);
    ST.t = setInterval(() => { if(!document.hidden && ST.day === 'today'
      && $('odayOv').classList.contains('show')) load(true); }, 20000);
  }

  function close(){
    const o = $('odayOv'); if(o) o.classList.remove('show');
    if(ST.t){ clearInterval(ST.t); ST.t = null; }
  }
  function day(which){
    if(which && which === ST.day && ST.d) return;
    if(which) ST.day = which;
    ST.d = null; ST.err = ''; tap('sel'); paint(); load();
  }

  // Ответы оператора по сверке. После ответа — перечитать: числа «по
  // приложению» могли сдвинуться (принятая правка — новый заказ).
  async function post(path, body, okText){
    try{
      await window.opApi.opFetch(path, {method: 'POST', body: {as: me(), day: ST.d && ST.d.day, ...body}});
      tap('ok'); if(okText){ try{ window.toast(okText, 'ok'); }catch(e){} }
    }catch(e){
      const err = ((e && e.payload) || {}).error;
      try{ window.toast(err === 'answered' ? 'Уже отвечено' : err === 'not_yours' ? 'Не ваш район'
                        : err === 'no_cash' ? 'Водитель ещё не вписал наличные' : 'Не получилось', 'err'); }catch(e2){}
    }
    ST.busy = false; await load(true);
  }
  function fact(name, ok){ return post('/api/operator/recon/fact', {driver: name, ok}, ok ? 'Наличные подтверждены' : 'Подтверждение снято'); }
  function fix(name, pid, delta, pcs, ok){ return post('/api/operator/recon/fix', {driver: name, pid, delta, pcs, ok}, ok ? 'Правка принята' : 'Правка отклонена'); }

  window.opDay = {open, close, day, fact, fix};
})();
