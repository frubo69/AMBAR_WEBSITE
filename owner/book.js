// Книжка клиентов в STAR (владелец, 13 окт 2026): та же книжка, что у
// операторов, плюс деньги по клиенту и личный ценник оптовика. Экран живёт в
// разделе «Учёт» (accOpen('book')): список с поиском → карточка → ценник.
// Отдельным файлом, как у оператора book.js: разметка и стили (.bk-*) свои.
(function(){
  const ST = {list: [], q: '', total: 0, card: null, ed: null, open: {}, busy: false};
  const $ = id => document.getElementById(id);
  const esc = s => (window.escS ? escS(s) : String(s == null ? '' : s));
  const n = v => String(Math.round(+v || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const api = (p, o) => window.ownerApi.ownerFetch(p, o);
  const tap = k => { try{ Telegram.WebApp.HapticFeedback.impactOccurred(k || 'light'); }catch(e){} };
  const say = m => { try{ _toast(m); }catch(e){} };
  const ini = name => (String(name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('') || '•').toUpperCase();
  const pl = (k, a, b, c) => { k = Math.abs(k) % 100; const m = k % 10; return k > 10 && k < 20 ? c : m === 1 ? a : m > 1 && m < 5 ? b : c; };
  const SVG = (d, w) => `<svg viewBox="0 0 24 24" width="${w || 16}" height="${w || 16}" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const I = {pin: '<path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/>',
             phone: '<path d="M4.5 3.5h4l2 5-2.5 1.5a12 12 0 0 0 6 6L15.5 13l5 2v4a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 3 5.1 1.5 1.5 0 0 1 4.5 3.5z"/>',
             search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>', chev: '<path d="M9 5l7 7-7 7"/>'};
  const MONTHS = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
  const dayRu = s => { const p = String(s || '').slice(0, 10).split('-'); return p.length === 3 ? `${+p[2]} ${MONTHS[+p[1] - 1]}` : ''; };

  function css(){
    if($('bkCss')) return;
    const st = document.createElement('style'); st.id = 'bkCss';
    st.textContent = `
.bk-srch{display:flex;align-items:center;gap:9px;background:var(--card);border:1px solid var(--border2);border-radius:14px;padding:0 14px;height:46px;margin-bottom:12px}
.bk-srch svg{color:var(--muted);flex-shrink:0}
.bk-srch input{flex:1;background:none;border:0;padding:0;height:100%;font-size:14.5px;color:var(--text);font-family:inherit;outline:none}
.bk-row{display:flex;align-items:center;gap:12px;width:100%;background:var(--card);border:1px solid var(--border2);border-radius:16px;padding:12px 12px 12px 14px;margin-bottom:8px;text-align:left;font-family:inherit;color:var(--text);cursor:pointer}
.bk-row:active{background:var(--card2)}
.bk-av{width:42px;height:42px;border-radius:13px;background:var(--card2);display:flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:15px;color:var(--gold2);flex-shrink:0}
.bk-av.w{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#1a1408}
.bk-row-b{flex:1;min-width:0}
.bk-row-n{display:flex;align-items:center;gap:7px;font-size:15px;font-weight:600;flex-wrap:wrap}
.bk-row-s{font-size:12px;color:var(--muted);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-row .sum{font-family:'Space Grotesk',sans-serif;font-weight:700;color:var(--gold2);font-size:14px;flex-shrink:0}
.bk-tag{display:inline-flex;align-items:center;height:18px;padding:0 7px;border-radius:6px;font-size:9.5px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;font-style:normal}
.bk-tag.w{background:var(--gold-soft);color:var(--gold2);border:1px solid rgba(201,169,110,.35)}
.bk-tag.v{background:rgba(91,141,239,.14);color:#8fb0ff}
.bk-tag.off{color:var(--muted);border:1px dashed var(--border2)}
.bk-hero{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--border2);border-radius:18px;padding:16px;margin-bottom:10px}
.bk-hero.w{border-color:rgba(201,169,110,.4);background:linear-gradient(180deg,rgba(201,169,110,.14),rgba(201,169,110,.04))}
.bk-hero .bk-av{width:54px;height:54px;border-radius:16px;font-size:19px}
.bk-hero-n{font-family:'Space Grotesk',sans-serif;font-size:19px;font-weight:700}
.bk-hero-s{font-size:12px;color:var(--sub);margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.bk-kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}
.bk-kpi div{background:var(--card);border:1px solid var(--border2);border-radius:14px;padding:11px 10px;min-width:0}
.bk-kpi b{display:block;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;color:var(--gold2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-kpi span{display:block;font-size:10px;color:var(--muted);margin-top:2px}
.bk-cap{font-size:10px;font-weight:800;letter-spacing:1.6px;text-transform:uppercase;color:var(--muted);margin:14px 4px 7px;display:flex;align-items:center;justify-content:space-between}
.bk-cap i{font-style:normal;color:var(--sub);letter-spacing:.3px;text-transform:none;font-weight:600}
.bk-card{background:var(--card);border:1px solid var(--border2);border-radius:16px;margin-bottom:10px;overflow:hidden}
.bk-li{display:flex;align-items:center;gap:12px;padding:12px 14px;border-top:1px solid var(--border2);min-height:50px}
.bk-li:first-child{border-top:0}
.bk-li .ic{width:19px;height:19px;color:var(--muted);flex-shrink:0;display:flex}
.bk-li-b{flex:1;min-width:0}
.bk-li-t{font-size:14px;font-weight:600;word-break:break-word}
.bk-li-s{font-size:11.5px;color:var(--muted);margin-top:2px}
.bk-li .lab{font-size:9.5px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:var(--gold2);background:var(--gold-soft);padding:3px 7px;border-radius:6px;flex-shrink:0}
.bk-li .r{font-family:'Space Grotesk',sans-serif;font-weight:700;color:var(--gold2);font-size:14px;flex-shrink:0}
.bk-rule{display:flex;flex-wrap:wrap;gap:6px;padding:12px 14px;border-bottom:1px solid var(--border2)}
.bk-rule .chip{font-family:inherit}
.bk-ch{display:flex;align-items:center;gap:10px;width:100%;padding:11px 14px;background:none;border:0;border-top:1px solid var(--border2);color:var(--text);font-family:inherit;text-align:left;cursor:pointer}
.bk-ch b{flex:1;font-size:13.5px;font-weight:700}
.bk-ch i{font-style:normal;font-size:11.5px;color:var(--muted)}
.bk-ch .chev{color:var(--muted);transition:transform .15s}
.bk-ch.on .chev{transform:rotate(90deg)}
.bk-pr{display:none}
.bk-pr.on{display:block}
.bk-pl{display:flex;align-items:center;gap:10px;padding:7px 14px 7px 18px;border-top:1px solid var(--border2);font-size:13px}
.bk-pl .n{flex:1;min-width:0;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-pl .o{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;min-width:44px;text-align:right}
.bk-pl input{width:74px;padding:6px 8px;border-radius:9px;background:var(--bg3);border:1px solid var(--border2);color:var(--gold2);font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:14px;text-align:right}
.bk-pl input:focus{border-color:rgba(201,169,110,.5);outline:none}
.bk-pl input.set{border-color:rgba(201,169,110,.45);background:var(--gold-soft)}
.bk-pl.view .h{font-family:'Space Grotesk',sans-serif;font-weight:700;color:var(--gold2);min-width:50px;text-align:right}
.bk-pl.view .o{text-decoration:line-through}
.bk-foot2{display:flex;gap:8px}
.bk-foot2 .qb{flex:1}
.bk-foot2 .qb.sec{background:var(--card);color:var(--sub);border:1px solid var(--border2)}`;
    document.head.appendChild(st);
  }

  // ── список ───────────────────────────────────────────────────────────
  async function open(){
    css(); ACC_BACK = null; ST.card = null; ST.ed = null;
    _setText('accTitle', 'Книжка клиентов'); _setText('accSub', 'загрузка…');
    $('accFoot').innerHTML = '';
    paintList();
    await load(ST.q);
  }
  async function load(q){
    ST.q = q;
    try{ const r = await api('/api/owner/clients', {params: q ? {q} : undefined}); if(ST.q !== q) return; ST.list = r.clients || []; ST.total = r.total || 0; }
    catch(e){ $('accMid').innerHTML = loadFail('accBook()'); return; }
    if(!ST.card) paintList();
  }
  let _t = null;
  function search(v){ clearTimeout(_t); _t = setTimeout(() => load(v.trim()), 180); }
  function paintList(){
    if(ACC_VIEW !== 'book') return;
    _setText('accTitle', 'Книжка клиентов');
    _setText('accSub', ST.total ? `${ST.total} ${pl(ST.total, 'клиент', 'клиента', 'клиентов')}` : 'книжка операторов');
    const rows = ST.list;
    const w = rows.filter(r => r.wholesale);
    $('accMid').innerHTML = `<div class="bk-srch">${SVG(I.search, 17)}<input id="bkQ" placeholder="Имя, телефон, адрес" autocomplete="off" value="${esc(ST.q)}" oninput="BK.search(this.value)"></div>
      ${!ST.q && w.length ? `<div class="bk-cap">Оптовики<i>${w.length}</i></div>` + w.map(row).join('') : ''}
      ${rows.length ? `<div class="bk-cap">${ST.q ? 'Найдено' : 'Недавние'}<i>${rows.length}</i></div>` + rows.filter(r => ST.q || !r.wholesale).map(row).join('')
        : `<div class="scan-empty">${ST.q ? 'Никого не нашли' : 'Книжка пуста'}</div>`}`;
    $('accFoot').innerHTML = '';
  }
  const row = r => `<button class="bk-row" onclick="BK.card('${esc(r.id)}')">
      <span class="bk-av${r.wholesale ? ' w' : ''}">${esc(ini(r.name || r.phone))}</span>
      <span class="bk-row-b"><span class="bk-row-n">${esc(r.name || r.phone)}${r.wholesale ? '<i class="bk-tag w">Оптовик</i>' : ''}${r.tg ? '<i class="bk-tag v">Приложение</i>' : ''}</span>
        <span class="bk-row-s">${esc(r.phone)}${r.address ? ' · ' + esc(r.address) : ''} · ${r.orders_n} ${pl(r.orders_n, 'заказ', 'заказа', 'заказов')}</span></span>
      <span class="sum">${r.orders_n ? n(r.total_sum || 0) : ''}</span></button>`;

  // ── карточка ─────────────────────────────────────────────────────────
  async function card(id){
    ACC_BACK = () => { ST.card = null; ST.ed = null; open(); };
    $('accMid').innerHTML = '<div class="exp-empty">Загрузка…</div>'; $('accFoot').innerHTML = '';
    try{ ST.card = await api(`/api/owner/clients/${encodeURIComponent(id)}`); }
    catch(e){ $('accMid').innerHTML = loadFail(`BK.card('${esc(id)}')`); return; }
    ST.ed = null; paint();
  }
  function paint(){
    const c = ST.card; if(!c) return;
    const st = c.stats || {}, w = c.wholesale_doc || {};
    const ed = ST.ed;
    _setText('accTitle', c.name || c.phone || 'Клиент');
    _setText('accSub', [c.phone, c.tg ? 'в приложении' : '', c.wholesale ? 'оптовик' : ''].filter(Boolean).join(' · '));
    const cats = {};
    (c.editor && c.editor.rows || []).forEach(r => { (cats[r.cat || 'Прочее'] = cats[r.cat || 'Прочее'] || []).push(r); });
    const pr = ed ? ed.prices : (w.prices || {});
    const rule = ed ? ed.rule : (w.rule || {pct: 0, cats: [], round: 5});
    const setN = Object.keys(pr).length;
    const ценник = ed ? `
      <div class="bk-cap">Личный ценник<i>${setN} ${pl(setN, 'позиция', 'позиции', 'позиций')}</i></div>
      <div class="bk-card">
        <div class="bk-rule">
          ${[5, 10, 15, 20].map(p => `<button class="chip${+rule.pct === p ? ' on' : ''}" onclick="BK.rulePct(${p})">−${p}%</button>`).join('')}
          <button class="chip${rule.round === 5 ? ' on' : ''}" onclick="BK.ruleRound()">до 5</button>
        </div>
        <div class="bk-rule" style="padding-top:8px">
          <button class="chip${!(rule.cats || []).length ? ' on' : ''}" onclick="BK.ruleCat('')">Всё</button>
          ${(c.editor.cats || []).map(k => `<button class="chip${(rule.cats || []).includes(k) ? ' on' : ''}" onclick="BK.ruleCat('${esc(k)}')">${esc(k)}</button>`).join('')}
          <button class="chip" style="margin-left:auto;color:var(--gold2)" onclick="BK.ruleApply()">Применить</button>
        </div>
        ${Object.entries(cats).map(([k, rows]) => { const has = rows.filter(r => pr[r.key] != null).length; return `
          <button class="bk-ch${ST.open[k] ? ' on' : ''}" onclick="BK.toggle('${esc(k)}')"><b>${esc(k)}</b><i>${has ? has + ' из ' : ''}${rows.length}</i><span class="chev">${SVG(I.chev, 15)}</span></button>
          <div class="bk-pr${ST.open[k] ? ' on' : ''}">${rows.map(r => `<div class="bk-pl"><span class="n">${esc(r.name)}</span><span class="o">${n(r.regular)}</span>
            <input inputmode="numeric" class="${pr[r.key] != null ? 'set' : ''}" value="${pr[r.key] != null ? pr[r.key] : ''}" placeholder="—" data-key="${esc(r.key)}" oninput="BK.setPrice(this)"></div>`).join('')}</div>`; }).join('')}
      </div>` : `
      <div class="bk-cap">Личный ценник<i>${c.wholesale ? setN + ' ' + pl(setN, 'позиция', 'позиции', 'позиций') : (setN ? 'выключен' : 'нет')}</i></div>
      <div class="bk-card">${setN ? Object.entries(pr).map(([k, v]) => { const r = (c.editor.rows || []).find(x => x.key === k) || {name: k, regular: 0}; return `<div class="bk-pl view"><span class="n">${esc(r.name)}</span><span class="o">${n(r.regular)}</span><span class="h">${n(v)}</span></div>`; }).join('')
        : '<div class="scan-empty">Обычные цены</div>'}</div>`;
    $('accMid').innerHTML = `
      <div class="bk-hero${c.wholesale ? ' w' : ''}"><div class="bk-av${c.wholesale ? ' w' : ''}">${esc(ini(c.name || c.phone))}</div>
        <div style="min-width:0"><div class="bk-hero-n">${esc(c.name || c.phone || '—')}</div>
          <div class="bk-hero-s">${c.wholesale ? '<span class="bk-tag w">Оптовик</span>' : ''}${c.tg ? '<span class="bk-tag v">Приложение</span>' : ''}${(c.aka || []).length ? `<span>он же ${esc(c.aka.join(', '))}</span>` : ''}${c.note ? `<span>${esc(c.note)}</span>` : ''}${c.created_at ? `<span>с ${esc(dayRu(c.created_at))}</span>` : ''}</div></div></div>
      <div class="bk-kpi"><div><b>${st.orders_n || 0}</b><span>${pl(st.orders_n || 0, 'заказ', 'заказа', 'заказов')}</span></div><div><b>${n(st.total_sum || 0)}</b><span>сумма, AED</span></div><div><b>${st.discount ? '−' + n(st.discount) : '0'}</b><span>скидка дали</span></div><div><b>${st.margin != null ? n(st.margin) : '—'}</b><span>маржа к закупке</span></div></div>
      <div class="bk-card">${(c.phones || []).map((p, i) => `<div class="bk-li"><span class="ic">${SVG(I.phone, 18)}</span><div class="bk-li-b"><div class="bk-li-t">${esc(p)}</div>${i ? '<div class="bk-li-s">второй</div>' : ''}</div></div>`).join('')}</div>
      ${(c.addresses || []).length ? `<div class="bk-cap">Адреса<i>${c.addresses.length}</i></div><div class="bk-card">${c.addresses.map(a => `
        <div class="bk-li"><span class="ic">${SVG(I.pin, 18)}</span><div class="bk-li-b"><div class="bk-li-t">${esc(a.text)}</div>${a.note ? `<div class="bk-li-s">${esc(a.note)}</div>` : ''}</div>${a.label ? `<span class="lab">${esc(a.label)}</span>` : ''}</div>`).join('')}</div>` : ''}
      ${ценник}
      ${(c.orders || []).length ? `<div class="bk-cap">Заказы<i>${c.orders.length}</i></div><div class="bk-card">${c.orders.map(o => `
        <div class="bk-li"><div class="bk-li-b"><div class="bk-li-t">${esc(dayRu(o.day || o.timestamp))}${o.district ? ' · ' + esc(o.district) : ''} · ${esc(stT(o.status))}${o.wholesale ? ' <i class="bk-tag w">ценник</i>' : ''}</div>
          <div class="bk-li-s">${esc((o.items || []).map(i => `${i.name} ×${i.qty}`).join(', '))}</div></div><span class="r">${n(o.total)}</span></div>`).join('')}</div>` : ''}`;
    $('accFoot').innerHTML = ed ? `<div class="qb-wrap bk-foot2"><button class="qb sec" onclick="BK.edOff()">Отмена</button><button class="qb" onclick="BK.save()">Сохранить ценник</button></div>`
      : `<div class="qb-wrap bk-foot2">${setN ? `<button class="qb sec" onclick="BK.toggleActive()">${c.wholesale ? 'Выключить' : 'Включить'}</button>` : ''}<button class="qb" onclick="BK.edOn()">${setN ? 'Править ценник' : 'Личный ценник'}</button></div>`;
  }
  const stT = s => ({pending: 'новый', approved: 'в пути', delivered: 'доставлен', cancelled: 'отменён', declined: 'отклонён'})[s] || s || '';

  // ── правка ценника ───────────────────────────────────────────────────
  function edOn(){ const w = ST.card.wholesale_doc || {}; ST.ed = {prices: {...(w.prices || {})}, rule: {...(w.rule || {pct: 0, cats: [], round: 5})}}; paint(); tap(); }
  function edOff(){ ST.ed = null; paint(); }
  function toggle(k){ ST.open[k] = !ST.open[k]; paint(); }
  function rulePct(p){ const r = ST.ed.rule; r.pct = +r.pct === p ? 0 : p; paint(); }
  function ruleRound(){ const r = ST.ed.rule; r.round = r.round === 5 ? 0 : 5; paint(); }
  function ruleCat(k){ const r = ST.ed.rule; if(!k){ r.cats = []; } else { const s = new Set(r.cats || []); s.has(k) ? s.delete(k) : s.add(k); r.cats = [...s]; } paint(); }
  function ruleApply(){
    const r = ST.ed.rule; if(!(+r.pct > 0)){ say('Выбери процент'); return; }
    const rows = ST.card.editor.rows || [];
    let k = 0;
    rows.forEach(x => {
      if((r.cats || []).length && !(r.cats || []).includes(x.cat)) return;
      let v = x.regular * (1 - r.pct / 100);
      v = r.round === 5 ? Math.floor(v / 5 + 0.5) * 5 : Math.round(v);
      if(v > 0 && v < x.regular){ ST.ed.prices[x.key] = v; k++; }
    });
    (r.cats || []).length ? r.cats.forEach(c => ST.open[c] = true) : Object.keys(ST.open).forEach(c => ST.open[c] = true);
    paint(); say(`Посчитано: ${k} ${pl(k, 'позиция', 'позиции', 'позиций')}`); tap();
  }
  function setPrice(el){
    const k = el.dataset.key, v = parseInt(String(el.value).replace(/\D/g, ''), 10);
    if(isFinite(v) && v > 0) ST.ed.prices[k] = v; else delete ST.ed.prices[k];
    el.classList.toggle('set', isFinite(v) && v > 0);
    const r = (ST.card.editor.rows || []).find(x => x.key === k);
    if(r && isFinite(v) && v > r.regular){ el.style.color = 'var(--dn)'; } else el.style.color = '';
  }
  async function save(){
    const c = ST.card, ed = ST.ed; if(!c || !ed) return;
    const rows = c.editor.rows || [];
    const bad = Object.entries(ed.prices).filter(([k, v]) => { const r = rows.find(x => x.key === k); return r && v > r.regular; });
    if(bad.length){ say(`Выше обычной: ${bad.length} ${pl(bad.length, 'позиция', 'позиции', 'позиций')}`); return; }
    try{
      const r = await api(`/api/owner/clients/${encodeURIComponent(c.id)}/prices`, {method: 'POST', body: {prices: ed.prices, rule: +ed.rule.pct > 0 ? ed.rule : null, active: true, as: _meName()}});
      say(`Ценник сохранён · ${r.prices_n} ${pl(r.prices_n, 'позиция', 'позиции', 'позиций')}`); tap('medium');
      await card(c.id);
    }catch(e){ const p = (e && e.payload) || {}; say(p.error === 'bad_prices' ? 'Есть цены выше обычных' : 'Не удалось сохранить'); }
  }
  async function toggleActive(){
    const c = ST.card; if(!c) return; const w = c.wholesale_doc || {};
    try{
      await api(`/api/owner/clients/${encodeURIComponent(c.id)}/prices`, {method: 'POST', body: {prices: w.prices || {}, rule: w.rule || null, active: !c.wholesale, as: _meName()}});
      say(c.wholesale ? 'Ценник выключен' : 'Ценник включён'); tap(); await card(c.id);
    }catch(e){ say('Не удалось'); }
  }

  window.BK = {search, card, edOn, edOff, toggle, rulePct, ruleRound, ruleCat, ruleApply, setPrice, save, toggleActive};
  window.accBook = open;
})();
