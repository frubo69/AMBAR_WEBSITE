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
      return `<div class="oday-c${mine.has(r.id) ? ' mine' : ''}${r.done ? '' : ' z'}">
        <div class="oday-ch"><span class="oday-code">${esc(r.code)}</span>
          <span class="oday-cn"><b>${esc(r.name)}</b><i>${parts.join(' · ')}</i></span>
          <span class="oday-cv">${n(r.aed)}<s>AED</s></span></div>
        ${(r.drivers || []).map(v => `<div class="oday-dr${v.done || v.work ? '' : ' z'}">
          <span class="ico">${CAR}</span><span class="oday-dn">${esc(v.name)}</span>
          <span class="oday-dk">${зак(v.done)}${v.work ? ` · <em>везёт ${v.work}</em>` : ''}</span>
          <span class="oday-dv">${n(v.aed)}</span></div>`).join('')}</div>`;
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

  window.opDay = {open, close, day};
})();
