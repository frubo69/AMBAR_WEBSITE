// Книжка клиентов (владелец, 13 окт 2026): «один раз номер и имя забиваешь —
// он запоминается; в следующий раз либо из контактов нашёл, нажал — и всё
// автозаполнилось, либо начал вводить номер — внизу, как в поиске,
// предлагаются номера: чем больше цифр, тем меньше вариантов».
//
// Три вещи в одном файле, как day.js и stock.js — окно приносит свою разметку
// и стили (.bk-*):
//   1. подсказки под полями «Имя» и «Телефон» в чеке; знакомый номер,
//      набранный целиком, подхватывается сам;
//   2. плашка клиента над полями и его адреса фишками; у оптовика в строках
//      чека его цена, обычная зачёркнута, под чеком скидка по ценнику;
//   3. окно «Клиенты»: поиск, недавние, карточка с телефонами, адресами
//      (подпись, заметка водителю), заказами и ценником; «В заказ»,
//      «Повторить заказ», новый клиент рукой.
// Цены оптовика считает сервер (clients.apply_prices); здесь — те же
// числа, чтобы итог в чеке не расходился с тем, что придёт в ответе.
(function(){
  const ST = {client: null, addrId: '', prices: {}, acT: null, acFor: '', auto: '',
              list: [], q: '', card: null, view: 'list', busy: false, total: 0, form: null};
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const tap = k => { try{ window.haptic(k || 'light'); }catch(e){} };
  const say = (m, c) => { try{ window.toast(m, c); }catch(e){} };
  const n = v => String(Math.round(+v || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const api = (p, o) => window.opApi.opFetch(p, o);
  const CAT = () => { try{ return CATALOG || []; }catch(e){ return window.CATALOG || []; } };
  const TK = () => { try{ return TICKET; }catch(e){ return window.TICKET || {}; } };
  const OP = () => { try{ return curOp || ''; }catch(e){ return window.curOp || ''; } };
  const ini = name => (String(name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('') || '•').toUpperCase();
  const SVG = (d, w) => `<svg viewBox="0 0 24 24" width="${w || 16}" height="${w || 16}" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const I = {
    x: '<path d="M6 6l12 12M18 6L6 18"/>', back: '<path d="M15 5l-7 7 7 7"/>', plus: '<path d="M12 5v14M5 12h14"/>',
    pin: '<path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/>',
    phone: '<path d="M4.5 3.5h4l2 5-2.5 1.5a12 12 0 0 0 6 6L15.5 13l5 2v4a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 3 5.1 1.5 1.5 0 0 1 4.5 3.5z"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>', chev: '<path d="M9 5l7 7-7 7"/>',
    note: '<path d="M20 15a3 3 0 0 1-3 3H8l-4 3V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3z"/>',
    book: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 0-3 3z"/><path d="M5 4v16a3 3 0 0 1 3-3h11"/>',
    repeat: '<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4v5h5"/>',
  };
  const MONTHS = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
  const dayRu = s => { const p = String(s || '').slice(0, 10).split('-'); return p.length === 3 ? `${+p[2]} ${MONTHS[+p[1] - 1]}` : ''; };

  // ── цены ─────────────────────────────────────────────────────────────
  const keyOf = l => l.pcs ? `${l.id}_${l.pcs}` : l.id;
  function regular(l){
    const p = CAT().find(x => x.id === l.id); if(!p) return 0;
    return l.pcs === 24 ? p.pack24 : (l.pcs === 12 ? p.pack12 : p.price);
  }
  function price(l){
    if(!ST.client) return null;
    const v = ST.prices[keyOf(l)]; if(v == null) return null;
    const r = regular(l);
    return (+v > 0 && +v < r) ? +v : null;
  }
  const regOf = l => price(l) != null ? regular(l) : null;
  function discount(){
    let d = 0;
    Object.values(TK() || {}).forEach(l => { const p = price(l); if(p != null) d += (regular(l) - p) * l.qty; });
    return d;
  }
  // под чеком — скидка по ценнику, когда она есть
  function afterTicket(){
    const card = document.querySelector('#tlines .tk-card'); if(!card) return;
    const d = discount();
    const old = card.querySelector('.bk-disc'); if(old) old.remove();
    if(d > 0) card.insertAdjacentHTML('beforeend', `<div class="bk-disc"><span>Скидка по ценнику</span><b>−${n(d)} AED</b></div>`);
  }

  // ── узлы в чеке: плашка, подсказки, адреса ───────────────────────────
  function fldOf(id){ const el = $(id); return el ? el.closest('.fld') : null; }
  function mountForm(){
    const hdr = document.querySelector('#custFields .cust-h');
    if(hdr && !$('bkBar')){ const d = document.createElement('div'); d.id = 'bkBar'; hdr.insertAdjacentElement('afterend', d); }
    const ph = fldOf('cPhone'), nm = fldOf('cName'), ad = fldOf('cAddr');
    if(ph && !$('bkAcPhone')){ const d = document.createElement('div'); d.id = 'bkAcPhone'; d.className = 'bk-ac'; ph.insertAdjacentElement('afterend', d); }
    if(nm && !$('bkAcName')){ const d = document.createElement('div'); d.id = 'bkAcName'; d.className = 'bk-ac'; nm.insertAdjacentElement('afterend', d); }
    if(ad && !$('bkAddr')){ const d = document.createElement('div'); d.id = 'bkAddr'; d.className = 'bk-addr'; ad.insertAdjacentElement('beforebegin', d); }
  }
  function paintBar(){
    const b = $('bkBar'); if(!b) return;
    const c = ST.client;
    if(!c){ b.innerHTML = ''; return; }
    const own = Object.keys(ST.prices || {}).length;
    b.innerHTML = `<div class="bk-bar${c.wholesale ? ' w' : ''}">
      <div class="bk-av${c.wholesale ? ' w' : ''}">${esc(ini(c.name || c.phone))}</div>
      <div class="bk-bar-b"><div class="bk-bar-t">${esc(c.name || c.phone || 'Клиент')}${c.wholesale ? '<span class="bk-tag w">Оптовик</span>' : ''}</div>
        <div class="bk-bar-s">${c.wholesale && own ? `Личный ценник применён · ${own} ${plural(own, 'позиция', 'позиции', 'позиций')}` : 'Из книжки'}</div></div>
      <button class="bk-x" onclick="BOOK.detach()" aria-label="Снять клиента">${SVG(I.x, 14)}</button>
    </div>`;
  }
  function paintAddr(){
    const box = $('bkAddr'); if(!box) return;
    const c = ST.client, list = (c && c.addresses) || [];
    if(!c || !list.length){ box.innerHTML = ''; return; }
    box.innerHTML = `<div class="bk-achips">${list.map(a => `
      <button class="bk-ach${a.id === ST.addrId ? ' on' : ''}" onmousedown="event.preventDefault()" onclick="BOOK.pickAddr('${esc(a.id)}')">
        ${a.label ? `<b>${esc(a.label)}</b>` : ''}<span>${esc(a.text)}</span></button>`).join('')}
      <button class="bk-ach add${ST.addrId ? '' : ' on'}" onmousedown="event.preventDefault()" onclick="BOOK.pickAddr('')">+ Новый</button>
    </div>`;
  }
  function pickAddr(id){
    const c = ST.client; const a = ((c && c.addresses) || []).find(x => x.id === id);
    const addr = $('cAddr'), cm = $('cComment');
    ST.addrId = a ? a.id : '';
    if(a){
      if(addr) addr.value = a.text;
      if(a.note && cm && !cm.value.trim()){ cm.value = a.note; try{ cmtOpen(false); }catch(e){} }
    } else if(addr){ addr.value = ''; try{ addr.focus(); }catch(e){} }
    paintAddr(); tap('sel');
  }

  // ── применить клиента к чеку ─────────────────────────────────────────
  function apply(c, opts){
    opts = opts || {};
    ST.client = c; ST.prices = (c.wholesale && c.prices) ? c.prices : {};
    const nm = $('cName'), ph = $('cPhone');
    if(nm && c.name) nm.value = c.name;
    if(ph && c.phone) ph.value = c.phone;
    const addrs = c.addresses || [];
    // адрес: последний, по которому возили; остальные — фишками
    ST.addrId = '';
    if(addrs.length){ ST.addrId = addrs[0].id; const a = $('cAddr'); if(a) a.value = addrs[0].text;
      const cm = $('cComment'); if(addrs[0].note && cm && !cm.value.trim()){ cm.value = addrs[0].note; try{ cmtOpen(false); }catch(e){} } }
    acHide();
    paintBar(); paintAddr();
    try{ renderTicket(); }catch(e){}
    if(!opts.quiet){ tap('light'); if(opts.open){ try{ openTicket(); }catch(e){} } }
  }
  function detach(){
    const had = !!ST.client;
    ST.client = null; ST.prices = {}; ST.addrId = ''; ST.auto = '';
    paintBar(); paintAddr();
    if(had){ try{ renderTicket(); }catch(e){} }
  }
  // правка заказа: клиент и снимок цен — из самого заказа
  function fromOrder(o){
    if(!o || !o.client_id){ detach(); return; }
    ST.client = {id: o.client_id, name: o.customer_name || '', phone: o.phone || '', wholesale: !!o.wholesale, addresses: []};
    ST.prices = o.own_prices || {}; ST.addrId = o.address_id || '';
    paintBar(); paintAddr();
    api(`/api/operator/clients/${encodeURIComponent(o.client_id)}`).then(c => {
      if(!ST.client || ST.client.id !== c.id) return;
      ST.client.addresses = c.addresses || []; ST.client.wholesale = c.wholesale; ST.client.name = c.name || ST.client.name;
      if(!ST.addrId){ const cur = ($('cAddr') || {}).value || ''; const hit = (c.addresses || []).find(a => a.text === cur); if(hit) ST.addrId = hit.id; }
      paintBar(); paintAddr();
    }).catch(() => {});
  }

  // ── подсказки под полями ─────────────────────────────────────────────
  const digits = s => String(s || '').replace(/\D/g, '');
  function acHide(){ ['bkAcPhone', 'bkAcName'].forEach(id => { const b = $(id); if(b) b.innerHTML = ''; }); }
  function acInit(){
    [['cPhone', 'bkAcPhone', true], ['cName', 'bkAcName', false]].forEach(([fid, box, isPhone]) => {
      const el = $(fid); if(!el) return;
      el.addEventListener('input', () => {
        clearTimeout(ST.acT);
        const q = isPhone ? digits(el.value) : el.value.trim();
        if(q.length < 2){ const b = $(box); if(b) b.innerHTML = ''; return; }
        if(ST.client && isPhone && digits(ST.client.phone) === digits(el.value)) return;
        ST.acT = setTimeout(() => acQuery(fid, box, isPhone), 160);
      });
      el.addEventListener('blur', () => setTimeout(() => { const b = $(box); if(b) b.innerHTML = ''; }, 180));
      el.addEventListener('keydown', e => { if(e.key === 'Escape'){ const b = $(box); if(b) b.innerHTML = ''; } });
    });
  }
  async function acQuery(fid, box, isPhone){
    const el = $(fid); if(!el) return;
    const q = isPhone ? digits(el.value) : el.value.trim();
    let rows = [];
    try{ rows = (await api('/api/operator/clients', {params: {q, limit: 6}})).clients || []; }catch(e){ rows = []; }
    const now = isPhone ? digits(el.value) : el.value.trim();
    if(now !== q) return;                      // пока ждали — набрали дальше
    const b = $(box); if(!b) return;
    if(!rows.length){ b.innerHTML = ''; return; }
    // номер набран целиком и он один такой — подхватываем сами
    if(isPhone && q.length >= 9 && rows.length === 1){
      const tail = digits(rows[0].phone).replace(/^971/, '').replace(/^0/, '');
      const typed = q.replace(/^971/, '').replace(/^0/, '');
      if(tail === typed && ST.auto !== rows[0].id){ ST.auto = rows[0].id; return pickRow(rows[0]); }
    }
    b.innerHTML = rows.map(r => `<button class="bk-acr" onmousedown="event.preventDefault()" onclick="BOOK.pickId('${esc(r.id)}')">
        <span class="bk-av sm${r.wholesale ? ' w' : ''}">${esc(ini(r.name || r.phone))}</span>
        <span class="bk-acr-b"><b>${esc(r.name || r.phone)}${r.wholesale ? '<i class="bk-tag w">Оптовик</i>' : ''}</b><small>${esc(r.phone)}${r.address ? ' · ' + esc(r.address) : ''}</small></span>
      </button>`).join('');
  }
  async function pickRow(r){
    try{ const c = await api(`/api/operator/clients/${encodeURIComponent(r.id)}`); apply(c, {}); say(`${c.name || c.phone} — из книжки`); }
    catch(e){ say('Не удалось открыть клиента', 'err'); }
  }
  const pickId = id => pickRow({id});

  // ── окно «Клиенты» ───────────────────────────────────────────────────
  function mount(){
    if($('bookOv')) return;
    const css = document.createElement('style');
    css.textContent = `
.bk-ac{display:flex;flex-direction:column;gap:2px;margin:-4px 0 9px}
.bk-ac:empty{display:none}
.bk-acr{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;border-radius:12px;background:var(--card);border:1px solid var(--border2);text-align:left;font-family:inherit;color:var(--text)}
.bk-acr:active{background:var(--card2)}
.bk-acr-b{flex:1;min-width:0}
.bk-acr-b b{display:flex;align-items:center;gap:6px;font-size:13.5px;font-weight:700}
.bk-acr-b small{display:block;font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.bk-av{width:38px;height:38px;border-radius:12px;background:var(--card3);display:flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:14px;color:var(--gold2);flex-shrink:0}
.bk-av.sm{width:30px;height:30px;border-radius:9px;font-size:11px}
.bk-av.w{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#1a1408}
.bk-tag{display:inline-flex;align-items:center;height:17px;padding:0 6px;border-radius:6px;font-size:9px;font-weight:800;letter-spacing:.5px;text-transform:uppercase;font-style:normal}
.bk-tag.w{background:var(--gold-soft);color:var(--gold2);border:1px solid rgba(201,169,110,.35)}
.bk-tag.v{background:rgba(91,141,239,.14);color:#8fb0ff}
.bk-bar{display:flex;align-items:center;gap:11px;background:var(--card);border:1px solid var(--border2);border-radius:14px;padding:10px 10px 10px 12px;margin:0 0 10px}
.bk-bar.w{background:linear-gradient(135deg,rgba(201,169,110,.2),rgba(201,169,110,.06));border-color:rgba(201,169,110,.45)}
.bk-bar-b{flex:1;min-width:0}
.bk-bar-t{display:flex;align-items:center;gap:7px;font-size:14.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-bar-s{font-size:11.5px;color:var(--gold2);margin-top:2px}
.bk-bar:not(.w) .bk-bar-s{color:var(--muted)}
.bk-x{width:30px;height:30px;border-radius:9px;border:1px solid var(--border2);display:flex;align-items:center;justify-content:center;color:var(--sub);flex-shrink:0}
.bk-addr:empty{display:none}
.bk-achips{display:flex;gap:7px;overflow:auto;padding:0 0 8px;-webkit-overflow-scrolling:touch}
.bk-ach{flex-shrink:0;max-width:220px;border:1px solid var(--border2);background:var(--card);border-radius:11px;padding:7px 11px;font-size:12px;color:var(--sub);text-align:left;font-family:inherit}
.bk-ach b{display:block;font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:1px}
.bk-ach span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-ach.on{border-color:rgba(201,169,110,.6);background:var(--gold-soft);color:var(--text)}
.bk-ach.on b{color:var(--gold2)}
.bk-ach.add{color:var(--gold2);font-weight:700}
.bk-disc{display:flex;justify-content:space-between;padding:8px 13px;font-size:12.5px;color:var(--sub);border-top:1px solid var(--border2)}
.bk-disc b{color:var(--gold2);font-family:'Space Grotesk',sans-serif}
.tline .nm s{color:var(--muted);font-weight:500;margin-left:5px}
.tline .lt s{display:block;font-size:11px;color:var(--muted);font-weight:600}
.q-flag.w{background:var(--gold-soft);color:var(--gold2)}
/* окно */
.bk-sheet{width:min(560px,100%);height:min(92vh,92dvh);display:flex;flex-direction:column;padding:18px 16px 0;overflow:hidden}
.bk-hd{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0}
.bk-hd b{flex:1;min-width:0;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.bk-hd i{display:block;font-style:normal;font-size:12.5px;color:var(--sub);font-weight:500;letter-spacing:0;text-transform:none;margin-top:2px}
.bk-ib{width:36px;height:36px;border-radius:11px;border:1px solid var(--border3);background:var(--card);display:flex;align-items:center;justify-content:center;color:var(--sub);flex-shrink:0}
.bk-ib.gold{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#1a1408;border:none}
.bk-srch{display:flex;align-items:center;gap:9px;background:var(--card);border:1px solid var(--border3);border-radius:13px;padding:0 12px;height:44px;flex-shrink:0}
.bk-srch svg{color:var(--muted);flex-shrink:0}
.bk-srch input{background:none;border:0;padding:0;height:100%;font-size:14.5px}
.bk-body{flex:1;min-height:0;overflow:auto;margin:12px -16px 0;padding:0 16px 18px;-webkit-overflow-scrolling:touch}
.bk-cap{font-size:10px;font-weight:800;letter-spacing:1.6px;text-transform:uppercase;color:var(--muted);margin:12px 4px 7px}
.bk-row{display:flex;align-items:center;gap:11px;width:100%;background:var(--card);border:1px solid var(--border3);border-radius:15px;padding:11px 11px 11px 13px;margin-bottom:8px;text-align:left;font-family:inherit;color:var(--text)}
.bk-row:active{background:var(--card2)}
.bk-row-b{flex:1;min-width:0}
.bk-row-n{display:flex;align-items:center;gap:7px;font-size:14.5px;font-weight:700;flex-wrap:wrap}
.bk-row-s{font-size:11.5px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bk-row .chev{color:var(--muted);flex-shrink:0}
.bk-empty{padding:40px 16px;text-align:center;color:var(--muted);font-size:13px}
.bk-hero{display:flex;align-items:center;gap:13px;background:var(--card);border:1px solid var(--border3);border-radius:17px;padding:14px;margin-bottom:10px}
.bk-hero.w{border-color:rgba(201,169,110,.4);background:linear-gradient(180deg,rgba(201,169,110,.14),rgba(201,169,110,.04))}
.bk-hero .bk-av{width:50px;height:50px;border-radius:15px;font-size:17px}
.bk-hero-n{font-family:'Space Grotesk',sans-serif;font-size:18px;font-weight:700}
.bk-hero-s{font-size:12px;color:var(--sub);margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.bk-kpi{display:flex;gap:8px;margin-bottom:10px}
.bk-kpi div{flex:1;background:var(--card);border:1px solid var(--border3);border-radius:13px;padding:10px 12px}
.bk-kpi b{display:block;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;color:var(--gold2)}
.bk-kpi span{display:block;font-size:10.5px;color:var(--muted);margin-top:2px}
.bk-card{background:var(--card);border:1px solid var(--border3);border-radius:15px;margin-bottom:10px;overflow:hidden}
.bk-li{display:flex;align-items:center;gap:11px;padding:11px 13px;border-top:1px solid var(--border2);min-height:48px}
.bk-li:first-child{border-top:0}
.bk-li .ic{width:18px;height:18px;color:var(--muted);flex-shrink:0;display:flex}
.bk-li-b{flex:1;min-width:0}
.bk-li-t{font-size:14px;font-weight:600;word-break:break-word}
.bk-li-s{font-size:11.5px;color:var(--muted);margin-top:2px}
.bk-li .lab{font-size:9.5px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:var(--gold2);background:var(--gold-soft);padding:3px 7px;border-radius:6px;flex-shrink:0;border:0;font-family:inherit}
.bk-li .lab.none{color:var(--muted);background:none;border:1px dashed var(--border3)}
.bk-li .r{font-family:'Space Grotesk',sans-serif;font-weight:700;color:var(--gold2);font-size:14px;flex-shrink:0}
.bk-li a{color:inherit;text-decoration:none}
.bk-li.add{color:var(--gold2);font-weight:700;font-size:13.5px;background:none;border-top:1px solid var(--border2);width:100%;text-align:left;font-family:inherit}
.bk-pl{display:flex;align-items:center;gap:10px;padding:9px 13px;border-top:1px solid var(--border2);font-size:13px}
.bk-pl:first-child{border-top:0}
.bk-pl .n{flex:1;min-width:0;font-weight:600}
.bk-pl .o{color:var(--muted);text-decoration:line-through;font-size:12px}
.bk-pl .h{font-family:'Space Grotesk',sans-serif;font-weight:700;color:var(--gold2);min-width:48px;text-align:right}
.bk-form{background:var(--card2);border:1px solid var(--border3);border-radius:14px;padding:12px;margin:0 0 10px;display:flex;flex-direction:column;gap:8px}
.bk-form input,.bk-form textarea{background:var(--card);border:1px solid var(--border2);border-radius:10px;padding:10px 12px;font-size:14px}
.bk-form .bt{display:flex;gap:8px}
.bk-form .bt button{flex:1;height:42px;border-radius:11px;font-family:inherit;font-size:14px;font-weight:700;border:1px solid var(--border3);color:var(--sub);background:var(--card)}
.bk-form .bt button.go{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#1a1408;border:none}
.bk-form .bt button.dn{color:var(--dn)}
.bk-foot{flex-shrink:0;display:flex;gap:8px;padding:10px 0 calc(14px + var(--safe-bottom));border-top:1px solid var(--border2);background:var(--bg3)}
.bk-foot button{flex:1;height:48px;border-radius:13px;font-family:inherit;font-size:14.5px;font-weight:800;border:1px solid var(--border3);color:var(--sub);background:var(--card);display:flex;align-items:center;justify-content:center;gap:8px}
.bk-foot button.go{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#1a1408;border:none}
@media (max-width:640px){.bk-sheet{width:100%;height:100%;border-radius:0;padding-top:calc(14px + var(--safe-top))}}`;
    document.head.appendChild(css);
    const ov = document.createElement('div');
    ov.className = 'ov'; ov.id = 'bookOv';
    ov.innerHTML = `<div class="sheet bk-sheet">
      <div class="bk-hd" id="bkHd"></div>
      <div id="bkTop"></div>
      <div class="bk-body" id="bkBody"></div>
      <div class="bk-foot" id="bkFoot" style="display:none"></div>
    </div>`;
    document.body.appendChild(ov);
    ov.addEventListener('click', e => { if(e.target === ov) close(); });
  }
  function close(){ hideOv('bookOv'); }
  async function open(){
    mount(); ST.view = 'list'; ST.form = null;
    showOv('bookOv'); tap('sel');
    paintList(); await load('');
  }
  async function load(q){
    ST.q = q;
    try{ const r = await api('/api/operator/clients', {params: {q, limit: q ? 30 : 30}}); if(ST.q !== q) return; ST.list = r.clients || []; ST.total = r.total || 0; }
    catch(e){ ST.list = []; }
    if(ST.view === 'list') paintList();
  }
  function paintList(){
    $('bkHd').innerHTML = `<button class="bk-ib" onclick="BOOK.close()" aria-label="Закрыть">${SVG(I.x, 15)}</button>
      <b>Клиенты<i>${ST.total ? `${ST.total} ${plural(ST.total, 'клиент', 'клиента', 'клиентов')}` : 'книжка'}</i></b>
      <button class="bk-ib gold" onclick="BOOK.newForm()" aria-label="Новый клиент">${SVG(I.plus, 17)}</button>`;
    $('bkTop').innerHTML = `<div class="bk-srch">${SVG(I.search, 17)}<input id="bkQ" placeholder="Имя, телефон, адрес" autocomplete="off" value="${esc(ST.q)}" oninput="BOOK.search(this.value)"></div>`;
    $('bkFoot').style.display = 'none';
    const rows = ST.list;
    $('bkBody').innerHTML = (ST.form === 'new' ? newFormHtml() : '') + (rows.length ? `<div class="bk-cap">${ST.q ? 'Найдено' : 'Недавние'}</div>` + rows.map(r => `
      <button class="bk-row" onclick="BOOK.card('${esc(r.id)}')">
        <span class="bk-av${r.wholesale ? ' w' : ''}">${esc(ini(r.name || r.phone))}</span>
        <span class="bk-row-b"><span class="bk-row-n">${esc(r.name || r.phone)}${r.wholesale ? '<i class="bk-tag w">Оптовик</i>' : ''}${r.tg ? '<i class="bk-tag v">Приложение</i>' : ''}</span>
          <span class="bk-row-s">${esc(r.phone)}${r.address ? ' · ' + esc(r.address) : ''}${r.addresses_n > 1 ? ` · ${r.addresses_n} ${plural(r.addresses_n, 'адрес', 'адреса', 'адресов')}` : ''}</span></span>
        <span class="chev">${SVG(I.chev, 16)}</span>
      </button>`).join('') : `<div class="bk-empty">${ST.q ? 'Никого не нашли' : 'Книжка пуста — заполнится с первого заказа'}</div>`);
  }
  let _sT = null;
  function search(v){ clearTimeout(_sT); _sT = setTimeout(() => load(v.trim()), 180); }
  function newFormHtml(d){
    d = d || {};
    return `<div class="bk-form" id="bkNew">
      <input id="bkNName" placeholder="Имя" autocomplete="off" value="${esc(d.name || '')}">
      <input id="bkNPhone" placeholder="+971 …" inputmode="tel" autocomplete="off" value="${esc(d.phone || '')}">
      <input id="bkNNote" placeholder="Заметка" autocomplete="off" value="${esc(d.note || '')}">
      <div class="bt"><button onclick="BOOK.formOff()">Отмена</button><button class="go" onclick="BOOK.newSave()">Сохранить</button></div>
    </div>`;
  }
  function newForm(){ ST.form = 'new'; paintList(); try{ $('bkNName').focus(); }catch(e){} }
  function formOff(){ ST.form = null; if(ST.view === 'list') paintList(); else paintCard(ST.card); }
  async function newSave(){
    const name = ($('bkNName') || {}).value || '', phone = ($('bkNPhone') || {}).value || '', note = ($('bkNNote') || {}).value || '';
    if(!phone.replace(/\D/g, '')){ say('Нужен телефон', 'err'); return; }
    try{
      const r = await api('/api/operator/clients', {method: 'POST', body: {name, phone, note, as: OP()}});
      ST.form = null; say('Записан в книжку', 'ok'); tap('heavy');
      ST.card = r.client; ST.view = 'card'; paintCard(r.client); load('');
    }catch(e){
      const p = (e && e.payload) || {};
      if(p.error === 'phone_taken'){ say(`Номер уже у ${p.client.name || p.client.phone}`); ST.form = null; return card(p.client.id); }
      say('Не удалось записать', 'err');
    }
  }
  async function card(id){
    ST.view = 'card'; ST.form = null;
    $('bkBody').innerHTML = '<div class="bk-empty">Загрузка…</div>';
    try{ ST.card = await api(`/api/operator/clients/${encodeURIComponent(id)}`); }
    catch(e){ say('Не удалось открыть', 'err'); return; }
    paintCard(ST.card);
  }
  function paintCard(c){
    if(!c) return;
    const st = c.stats || {};
    $('bkHd').innerHTML = `<button class="bk-ib" onclick="BOOK.back()" aria-label="Назад">${SVG(I.back, 15)}</button><b>Клиент</b>
      <button class="bk-ib" onclick="BOOK.editForm()" aria-label="Правка">${SVG(I.note, 16)}</button>`;
    $('bkTop').innerHTML = '';
    const addrs = c.addresses || [];
    const prices = c.wholesale ? Object.entries(c.prices || {}) : [];
    const cat = CAT();
    const nameOf = k => { const [pid, pcs] = k.split('_'); const p = cat.find(x => x.id === pid); return (p ? p.name : pid) + (pcs ? ` ×${pcs}` : ''); };
    const regOfKey = k => { const [pid, pcs] = k.split('_'); const p = cat.find(x => x.id === pid); if(!p) return 0; return pcs === '24' ? p.pack24 : pcs === '12' ? p.pack12 : p.price; };
    $('bkBody').innerHTML = `
      ${ST.form === 'edit' ? editFormHtml(c) : ''}
      <div class="bk-hero${c.wholesale ? ' w' : ''}"><div class="bk-av${c.wholesale ? ' w' : ''}">${esc(ini(c.name || c.phone))}</div>
        <div><div class="bk-hero-n">${esc(c.name || c.phone || '—')}</div>
          <div class="bk-hero-s">${c.wholesale ? '<span class="bk-tag w">Оптовик</span>' : ''}${c.tg ? '<span class="bk-tag v">Приложение</span>' : ''}${c.wholesale ? `<span>личный ценник · ${prices.length} ${plural(prices.length, 'позиция', 'позиции', 'позиций')}</span>` : ''}${c.note ? `<span>${esc(c.note)}</span>` : ''}</div></div></div>
      <div class="bk-kpi"><div><b>${st.orders_n || 0}</b><span>${plural(st.orders_n || 0, 'заказ', 'заказа', 'заказов')}</span></div><div><b>${n(st.total_sum || 0)}</b><span>сумма, AED</span></div><div><b>${c.last_order_at ? dayRu(c.last_order_at) : '—'}</b><span>последний</span></div></div>
      <div class="bk-card">${(c.phones || []).map((p, i) => `<div class="bk-li"><span class="ic">${SVG(I.phone, 18)}</span><div class="bk-li-b"><div class="bk-li-t"><a href="tel:${esc(p.replace(/\s/g, ''))}">${esc(p)}</a></div>${i ? '<div class="bk-li-s">второй</div>' : ''}</div></div>`).join('')}</div>
      <div class="bk-cap">Адреса</div>
      <div class="bk-card">${addrs.map(a => ST.form === 'addr:' + a.id ? addrFormHtml(a) : `
        <div class="bk-li"><span class="ic">${SVG(I.pin, 18)}</span><div class="bk-li-b"><div class="bk-li-t">${esc(a.text)}</div>${a.note ? `<div class="bk-li-s">${esc(a.note)}</div>` : ''}</div>
          <button class="lab${a.label ? '' : ' none'}" onclick="BOOK.addrForm('${esc(a.id)}')">${esc(a.label || 'подпись')}</button></div>`).join('')}
        ${ST.form === 'addr:new' ? addrFormHtml({}) : `<button class="bk-li add" onclick="BOOK.addrForm('new')"><span class="ic">${SVG(I.plus, 18)}</span>Добавить адрес</button>`}
      </div>
      ${prices.length ? `<div class="bk-cap">Личный ценник</div><div class="bk-card">${prices.map(([k, v]) => `<div class="bk-pl"><span class="n">${esc(nameOf(k))}</span><span class="o">${n(regOfKey(k))}</span><span class="h">${n(v)}</span></div>`).join('')}</div>` : ''}
      ${(c.orders || []).length ? `<div class="bk-cap">Заказы</div><div class="bk-card">${c.orders.map(o => `
        <div class="bk-li"><div class="bk-li-b"><div class="bk-li-t">${esc(dayRu(o.day || o.timestamp))}${o.district ? ' · ' + esc(o.district) : ''} · ${esc(stT(o.status))}${o.wholesale ? ' <i class="bk-tag w">ценник</i>' : ''}</div>
          <div class="bk-li-s">${esc((o.items || []).map(i => `${i.name} ×${i.qty}`).join(', '))}</div></div><span class="r">${n(o.total)}</span></div>`).join('')}</div>` : ''}`;
    const last = (c.orders || []).find(o => (o.items || []).length);
    $('bkFoot').style.display = '';
    $('bkFoot').innerHTML = `${last ? `<button onclick="BOOK.repeat()">${SVG(I.repeat, 16)}Повторить заказ</button>` : ''}<button class="go" onclick="BOOK.toOrder()">В заказ</button>`;
  }
  const stT = s => ({pending: 'новый', approved: 'в пути', delivered: 'доставлен', cancelled: 'отменён', declined: 'отклонён'})[s] || s || '';
  function back(){ ST.view = 'list'; ST.form = null; paintList(); }
  function editFormHtml(c){
    return `<div class="bk-form" id="bkEdit">
      <input id="bkEName" placeholder="Имя" autocomplete="off" value="${esc(c.name || '')}">
      <input id="bkEPhone" placeholder="+971 …" inputmode="tel" autocomplete="off" value="${esc(c.phone || '')}">
      <input id="bkENote" placeholder="Заметка" autocomplete="off" value="${esc(c.note || '')}">
      <div class="bt"><button onclick="BOOK.formOff()">Отмена</button><button class="go" onclick="BOOK.editSave()">Сохранить</button></div>
    </div>`;
  }
  function editForm(){ ST.form = 'edit'; paintCard(ST.card); try{ $('bkEName').focus(); }catch(e){} }
  async function editSave(){
    const c = ST.card; if(!c) return;
    const body = {id: c.id, name: ($('bkEName') || {}).value || '', note: ($('bkENote') || {}).value || '', as: OP()};
    const ph = ($('bkEPhone') || {}).value || ''; if(ph.replace(/\D/g, '') !== (c.phone || '').replace(/\D/g, '')) body.phone = ph;
    try{ const r = await api('/api/operator/clients', {method: 'POST', body}); ST.form = null; ST.card = {...c, ...r.client}; paintCard(ST.card); say('Сохранено', 'ok'); load(ST.q); }
    catch(e){ const p = (e && e.payload) || {}; say(p.error === 'phone_taken' ? `Номер уже у ${p.client.name || p.client.phone}` : 'Не удалось сохранить', 'err'); }
  }
  function addrFormHtml(a){
    const isNew = !a.id;
    return `<div class="bk-form" style="margin:8px">
      ${isNew ? `<textarea id="bkAText" rows="2" placeholder="Район, здание, квартира"></textarea>` : `<div class="bk-li-t" style="padding:2px 2px 0">${esc(a.text)}</div>`}
      <input id="bkALabel" placeholder="Подпись: Дом, Офис…" autocomplete="off" value="${esc(a.label || '')}">
      <input id="bkANote" placeholder="Заметка водителю" autocomplete="off" value="${esc(a.note || '')}">
      <div class="bt">${isNew ? '' : `<button class="dn" onclick="BOOK.addrDel('${esc(a.id)}')">Убрать</button>`}<button onclick="BOOK.formOff()">Отмена</button><button class="go" onclick="BOOK.addrSave('${esc(a.id || '')}')">Сохранить</button></div>
    </div>`;
  }
  function addrForm(id){ ST.form = 'addr:' + id; paintCard(ST.card); }
  async function addrSave(id){
    const c = ST.card; if(!c) return;
    const body = {label: ($('bkALabel') || {}).value || '', note: ($('bkANote') || {}).value || ''};
    if(id) body.id = id; else body.text = ($('bkAText') || {}).value || '';
    if(!id && !body.text.trim()){ say('Нужен адрес', 'err'); return; }
    try{ const r = await api(`/api/operator/clients/${encodeURIComponent(c.id)}/address`, {method: 'POST', body}); ST.form = null; ST.card = {...c, ...r.client}; paintCard(ST.card); tap('light'); }
    catch(e){ say('Не удалось сохранить адрес', 'err'); }
  }
  async function addrDel(id){
    const c = ST.card; if(!c) return;
    try{ const r = await api(`/api/operator/clients/${encodeURIComponent(c.id)}/address`, {method: 'POST', body: {id, delete: true}}); ST.form = null; ST.card = {...c, ...r.client}; paintCard(ST.card); }
    catch(e){ say('Не удалось убрать', 'err'); }
  }
  function toOrder(){ const c = ST.card; if(!c) return; close(); apply(c, {open: true}); say(`${c.name || c.phone} — в заказе`); }
  function repeat(){
    const c = ST.card; if(!c) return;
    const last = (c.orders || []).find(o => (o.items || []).length); if(!last) return;
    const cat = CAT(), tk = TK();
    let put = 0;
    Object.keys(tk).forEach(k => delete tk[k]);
    last.items.forEach(i => { if(!i.id || !cat.find(x => x.id === i.id)) return; const key = i.pcs ? `${i.id}_${i.pcs}` : i.id; tk[key] = {id: i.id, pcs: i.pcs || undefined, qty: +i.qty || 1}; put++; });
    close(); apply(c, {open: true});
    say(put ? `Повтор: ${put} ${plural(put, 'позиция', 'позиции', 'позиций')}` : 'Позиций прошлого заказа нет в каталоге', put ? 'ok' : 'err');
  }
  function plural(k, a, b, c){ k = Math.abs(k) % 100; const m = k % 10; return k > 10 && k < 20 ? c : m === 1 ? a : m > 1 && m < 5 ? b : c; }

  window.BOOK = {price, regOf, discount, afterTicket, apply, detach, fromOrder, pickAddr, pickId,
                 clientId: () => ST.client ? ST.client.id : '', addressId: () => ST.addrId,
                 open, close, search, card, back, newForm, formOff, newSave, editForm, editSave, addrForm, addrSave, addrDel, toOrder, repeat};
  window.bookOpen = open;
  function init(){ mount(); mountForm(); acInit(); }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
