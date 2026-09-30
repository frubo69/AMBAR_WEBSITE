// Доп. заявки у оператора — полный доступ (владелец, 30 сен 2026: «и править,
// и в целом иметь доступ ко всем доп. заявкам в полном объёме»).
//
// Три экрана в одном окне:
//   список  — «Ждут отправки» (черновики, собранные программой из недобора),
//             «В работе», «Закрытые»; внизу «Новая заявка»;
//   заявка  — состав по районам; район, который ещё не начали принимать,
//             правится счётчиками и «Добавить позицию»; цены закупки;
//             черновик — назвать базу и отправить водителям; отменить;
//   новая   — куда, потом что и сколько по районам.
// Сервер — тот же, что у старшего в STAR: правило одно на оба приложения.
// Основной заявки здесь нет и быть не может — сервер отвечает отказом.
//
// Отдельным файлом, как stock.js: окно приносит свою разметку и стили (.oxt-*).
(function(){
  const ST = {view: 'list', list: null, dists: [], one: null, err: '', busy: false,
              ed: {}, add: '', q: '', cat: null, nw: null, sure: false};
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const me = () => { try{ return curOp || ''; }catch(e){ return ''; } };
  const tap = k => { try{ window.haptic(k || 'light'); }catch(e){} };
  const say = (t, k) => { try{ window.toast(t, k); }catch(e){} };
  const n = v => { v = Math.round((+v || 0) * 2) / 2; const [a, b] = String(v).split('.');
    return a.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (b ? ',' + b : ''); };
  const pl = (k, a, b, c) => { k = Math.abs(Math.round(k)) % 100; const m = k % 10;
    return k > 10 && k < 20 ? c : m === 1 ? a : m > 1 && m < 5 ? b : c; };
  const ед = k => `${n(k)} ${pl(k, 'единица', 'единицы', 'единиц')}`;
  const поз = k => `${k} ${pl(k, 'позиция', 'позиции', 'позиций')}`;
  const api = (path, opts) => window.opApi.opFetch('/api/operator/extra' + path, opts);
  const X = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const BACK = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';
  const CHEV = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>';
  const PLUS = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
  const FIND = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>';
  const MONTHS = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  const dayRu = s => { const p = String(s || '').slice(0, 10).split('-'); return p.length === 3 ? `${+p[2]} ${MONTHS[+p[1] - 1]}` : ''; };

  function mount(){
    if($('oxtOv')) return;
    const css = document.createElement('style');
    css.textContent = `
.oxt-sheet{width:min(620px,100%);height:min(92vh,92dvh);display:flex;flex-direction:column;padding:18px 16px 0;overflow:hidden}
.oxt-hd{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0}
.oxt-hd b{flex:1;min-width:0;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.oxt-hd b i{display:block;font-style:normal;font-size:12.5px;color:var(--sub);font-weight:500;letter-spacing:0;text-transform:none;margin-top:2px;white-space:normal}
.oxt-bk{width:34px;height:34px;border-radius:11px;background:var(--card2);border:1px solid var(--border3);color:var(--text);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.oxt-body{flex:1;min-height:0;overflow:auto;margin:0 -16px;padding:0 16px 14px;-webkit-overflow-scrolling:touch}
.oxt-foot{flex-shrink:0;margin:0 -16px;padding:10px 16px calc(14px + env(safe-area-inset-bottom,0px));border-top:1px solid var(--border3);background:var(--bg3);display:flex;gap:8px}
.oxt-foot:empty{display:none}
.oxt-grp{font-size:11.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--sub);margin:14px 2px 8px}
.oxt-grp:first-child{margin-top:2px}
.oxt-r{display:flex;align-items:center;gap:12px;width:100%;text-align:left;padding:13px 14px;border-radius:14px;background:var(--card);border:1px solid var(--border3);color:var(--text);font-family:inherit;margin-bottom:8px}
.oxt-rn{flex:1;min-width:0}
.oxt-rn b{display:block;font-size:14.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.oxt-rn i{display:block;font-style:normal;font-size:12px;color:var(--sub);margin-top:2px}
.oxt-st{flex-shrink:0;padding:5px 10px;border-radius:999px;font-size:11px;font-weight:700;background:var(--card2);color:var(--sub)}
.oxt-st.wait,.oxt-st.live{background:var(--gold-soft);color:var(--gold2)}
.oxt-st.done{background:rgba(76,175,125,.12);color:var(--up)}
.oxt-st.no{background:rgba(224,85,85,.1);color:var(--dn)}
.oxt-ch{color:var(--sub);display:flex}
.oxt-btn{flex:1;height:48px;border-radius:14px;border:1px solid var(--border3);background:var(--card2);color:var(--text);font-family:inherit;font-size:14.5px;font-weight:700;display:flex;align-items:center;justify-content:center;gap:7px}
.oxt-btn.go{border:0;background:linear-gradient(90deg,#E8C98A,#C9A96E);color:#1a1408}
.oxt-btn.bad{background:rgba(224,85,85,.1);border-color:rgba(224,85,85,.4);color:var(--dn)}
.oxt-btn:disabled{opacity:.4}
.oxt-in{width:100%;height:46px;border-radius:13px;background:var(--card);border:1px solid var(--border3);color:var(--text);font-family:inherit;font-size:15px;font-weight:600;padding:0 14px;outline:0}
.oxt-in:focus{border-color:rgba(201,169,110,.55)}
.oxt-chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
.oxt-chip{padding:8px 12px;border-radius:999px;background:var(--card);border:1px solid var(--border3);color:var(--sub);font-family:inherit;font-size:12.5px;font-weight:600}
.oxt-chip.on{background:var(--gold-soft);border-color:rgba(201,169,110,.55);color:var(--gold2)}
.oxt-chip i{font-style:normal;margin-left:5px;font-family:'Space Grotesk',sans-serif}
.oxt-card{border-radius:16px;background:var(--card);border:1px solid var(--border3);margin-bottom:10px;overflow:hidden}
.oxt-ch2{display:flex;align-items:center;gap:11px;padding:12px 14px}
.oxt-code{min-width:38px;padding:5px 0;border-radius:9px;background:var(--gold-soft);color:var(--gold2);font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:13px;text-align:center;flex-shrink:0}
.oxt-cn{flex:1;min-width:0}
.oxt-cn b{display:block;font-size:14.5px;font-weight:600}
.oxt-cn i{display:block;font-style:normal;font-size:12px;color:var(--sub);margin-top:2px}
.oxt-cv{font-family:'Space Grotesk',sans-serif;font-size:15px;font-weight:700;color:var(--sub);white-space:nowrap}
.oxt-l{display:flex;align-items:center;gap:10px;padding:9px 14px;border-top:1px solid var(--border3);font-size:13.5px}
.oxt-l img{width:26px;height:36px;object-fit:contain;flex-shrink:0}
.oxt-ln{flex:1;min-width:0;font-weight:600;line-height:1.25}
.oxt-ln i{display:block;font-style:normal;font-size:11.5px;font-weight:500;color:var(--sub);margin-top:1px}
.oxt-lq{font-family:'Space Grotesk',sans-serif;font-weight:700;white-space:nowrap}
.oxt-lq u{text-decoration:none;color:var(--sub);font-weight:500}
.oxt-l.zero .oxt-ln{color:var(--sub);text-decoration:line-through}
.oxt-stp{display:flex;align-items:center;gap:3px;flex-shrink:0}
.oxt-stp button{width:34px;height:34px;border-radius:10px;background:var(--card2);border:1px solid var(--border3);color:var(--gold2);font-size:18px;font-weight:700;display:flex;align-items:center;justify-content:center}
.oxt-stp b{min-width:30px;text-align:center;font-family:'Space Grotesk',sans-serif;font-size:15px;font-weight:700}
.oxt-stp b.ch{color:var(--gold2)}
.oxt-add{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;padding:11px;border:0;border-top:1px solid var(--border3);background:none;color:var(--gold2);font-family:inherit;font-size:13px;font-weight:600}
.oxt-pr{width:84px;height:38px;border-radius:11px;background:var(--card2);border:1px solid var(--border3);color:var(--text);font-family:'Space Grotesk',sans-serif;font-size:15px;font-weight:700;text-align:right;padding:0 10px;outline:0;flex-shrink:0}
.oxt-note{font-size:12px;color:var(--sub);line-height:1.5;margin:4px 2px 12px}
.oxt-q{display:flex;align-items:center;gap:8px;height:42px;padding:0 12px;border-radius:12px;background:var(--card);border:1px solid var(--border3);color:var(--sub);margin:10px 0}
.oxt-q input{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--text);font-family:inherit;font-size:14px;padding:0}
.oxt-tabs{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:6px;margin-top:12px}
.oxt-empty{padding:36px 10px;text-align:center;color:var(--sub);font-size:13.5px;line-height:1.5}
.oxt-empty b{display:block;color:var(--text);font-size:15px;margin-bottom:5px}
.oxt-sk{height:64px;border-radius:14px;background:var(--card);margin-bottom:8px;opacity:.6}
@media(max-width:520px){
  .oxt-sheet{padding:16px 12px 0;border-radius:20px 20px 0 0;height:94dvh;align-self:flex-end}
  .oxt-body{margin:0 -12px;padding:0 12px 14px}
  .oxt-foot{margin:0 -12px;padding-left:12px;padding-right:12px}
}`;
    document.head.appendChild(css);
    const ov = document.createElement('div');
    ov.className = 'ov'; ov.id = 'oxtOv';
    ov.addEventListener('click', e => { if(e.target === ov) close(); });
    ov.innerHTML = `<div class="sheet oxt-sheet"><div class="oxt-hd" id="oxtHd"></div>
      <div class="oxt-body" id="oxtBody"></div><div class="oxt-foot" id="oxtFoot"></div></div>`;
    document.body.appendChild(ov);
  }

  function head(t, s, back){
    $('oxtHd').innerHTML = `${back ? `<button class="oxt-bk" onclick="opExtra.back()" aria-label="Назад">${BACK}</button>` : ''}
      <b>${esc(t)}${s ? `<i>${s}</i>` : ''}</b>
      <button class="oc-x" onclick="opExtra.close()" aria-label="Закрыть">${X}</button>`;
  }
  const fail = retry => window.loadFail ? window.loadFail(retry) : '<div class="oxt-empty"><b>Не удалось загрузить</b></div>';

  // Состояние заявки одним словом.
  function state(x){
    if(x.status === 'draft') return ['черновик', 'wait'];
    if(x.status === 'cancelled') return ['отменена', 'no'];
    if(x.status !== 'open') return [x.took < x.total_qty ? 'недобор' : 'принято', 'done'];
    const busy = (x.districts || 0) - (x.done || 0) - (x.free || 0);
    return (busy > 0 || x.done) ? ['в работе', 'live'] : ['ждёт', 'wait'];
  }

  // ── список ────────────────────────────────────────────────────────────────
  function paintList(){
    head('Доп. заявки', '');
    const box = $('oxtBody');
    $('oxtFoot').innerHTML = '';
    if(ST.err){ box.innerHTML = fail('opExtra.open()'); return; }
    if(!ST.list){ box.innerHTML = Array(5).fill('<div class="oxt-sk"></div>').join(''); return; }
    const row = x => { const st = state(x); const было = (x.tried_bases || []).filter(Boolean);
      return `<button class="oxt-r" onclick="opExtra.one('${esc(x.supply_id)}')">
        <span class="oxt-rn"><b>${esc(x.base || 'База не названа')}</b>
          <i>${ед(x.total_qty)} · ${поз(x.positions)} · ${dayRu(x.day || x.at)}${
            было.length ? ' · нет на: ' + esc(было.join(', ')) : ''}</i></span>
        <span class="oxt-st ${st[1]}">${st[0]}</span><span class="oxt-ch">${CHEV}</span></button>`; };
    const wait = ST.list.filter(x => x.status === 'draft'), open = ST.list.filter(x => x.status === 'open'),
          past = ST.list.filter(x => x.status !== 'draft' && x.status !== 'open');
    box.innerHTML = (!ST.list.length
        ? '<div class="oxt-empty"><b>Пока пусто</b>Магазин дал не всё — соберите заявку на другую базу</div>' : '')
      + (wait.length ? `<div class="oxt-grp">Ждут отправки</div>${wait.map(row).join('')}` : '')
      + (open.length ? `<div class="oxt-grp">В работе</div>${open.map(row).join('')}` : '')
      + (past.length ? `<div class="oxt-grp">Закрытые</div>${past.slice(0, 12).map(row).join('')}` : '');
    $('oxtFoot').innerHTML = `<button class="oxt-btn go" onclick="opExtra.newOpen()">${PLUS}Новая заявка</button>`;
  }

  async function loadList(){
    ST.err = '';
    try{ const r = await api('', {params: {as: me()}});
      ST.list = r.supplies || []; ST.dists = r.districts || []; }
    catch(e){ ST.list = null; ST.err = '1'; }
  }

  // ── одна заявка ───────────────────────────────────────────────────────────
  // Район можно править, пока его не начали принимать; у черновика — всегда.
  const canEdit = (d, t) => (d.status === 'draft' || d.status === 'open')
    && !t.locked && !t.done_at && !t.cancelled_at;
  const edQty = (oid, l) => (ST.ed[oid] && ST.ed[oid][l.id] != null) ? ST.ed[oid][l.id] : (l.plan != null ? l.plan : l.need);
  const dirty = () => Object.values(ST.ed).reduce((a, m) => a + Object.keys(m).length, 0);

  function paintOne(){
    const d = ST.one, box = $('oxtBody');
    if(ST.err){ head('Доп. заявка', '', true); box.innerHTML = fail(`opExtra.one('${esc(ST.sid)}')`); $('oxtFoot').innerHTML = ''; return; }
    if(!d){ head('Доп. заявка', 'загрузка…', true); box.innerHTML = Array(4).fill('<div class="oxt-sk"></div>').join(''); $('oxtFoot').innerHTML = ''; return; }
    if(ST.add) return paintAdd();
    const черн = d.status === 'draft';
    const st = d.status === 'draft' ? 'черновик' : d.status === 'open' ? 'в работе'
             : d.status === 'cancelled' ? 'отменена' : 'закрыта';
    const было = (d.tried_bases || []).filter(Boolean);
    head(d.base || 'База не названа', `${st} · ${ед(d.total_qty)} · ${dayRu(d.day || d.at)}${
      d.from_base ? ' · из недобора ' + esc(d.from_base) : ''}`, true);
    const send = !черн ? '' : `<div class="oxt-grp">Куда едем</div>
      ${было.length ? `<div class="oxt-note">Нет в наличии на: ${esc(было.join(', '))}</div>` : ''}
      <input class="oxt-in" id="oxtBase" placeholder="Название базы" maxlength="60" autocomplete="off"
             value="${esc(ST.base || '')}" oninput="opExtra.base(this.value)">
      ${recent(было).length ? `<div class="oxt-chips">${recent(было).map(b =>
        `<button class="oxt-chip" data-b="${esc(b)}" onclick="opExtra.base(this.dataset.b, 1)">${esc(b)}</button>`).join('')}</div>` : ''}`;
    const cards = (d.tasks || []).map(t => {
      const ok = canEdit(d, t);
      const plan = {}; (t.lines || []).forEach(l => plan[l.id] = l);
      // Добавленные, которых в районе ещё нет, — строками в конце.
      const добавлены = Object.keys(ST.ed[t.district] || {}).filter(p => !plan[p])
        .map(p => ({id: p, name: nameOf(p), plan: 0, need: 0, got: 0, fresh: true}));
      const lines = (t.lines || []).concat(добавлены);
      const why = t.cancelled_at ? 'отменён' : t.done_at ? 'принят'
                : t.lock_why === 'noscan' ? 'принят без сканирования' : t.locked ? 'идёт приёмка'
                : t.driver ? 'взял ' + esc(t.driver) : 'никто не взял';
      return `<div class="oxt-card"><div class="oxt-ch2"><span class="oxt-code">${esc(t.district_code)}</span>
          <span class="oxt-cn"><b>${esc(t.district_name)}</b><i>${why}</i></span>
          <span class="oxt-cv">${черн ? n(t.need) : `${n(t.got)}<u style="text-decoration:none;opacity:.7">/${n(t.need)}</u>`}</span></div>
        ${lines.map(l => { const q = edQty(t.district, l), base = l.plan != null ? l.plan : l.need;
          return `<div class="oxt-l${ok && !q ? ' zero' : ''}">
            <img src="/products/${esc(l.id)}.webp" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            <span class="oxt-ln">${esc(l.name)}${l.na ? '<i>нет в наличии</i>' : l.got ? `<i>принято ${n(l.got)}</i>` : ''}</span>
            ${ok ? `<span class="oxt-stp"><button onclick="opExtra.step('${esc(t.district)}','${esc(l.id)}',-1)" aria-label="меньше">−</button>
                <b class="${q !== base ? 'ch' : ''}">${n(q)}</b>
                <button onclick="opExtra.step('${esc(t.district)}','${esc(l.id)}',1)" aria-label="больше">+</button></span>`
              : `<span class="oxt-lq">${l.na ? '—' : n(l.plan != null ? l.plan : l.need)}</span>`}</div>`; }).join('')}
        ${ok ? `<button class="oxt-add" onclick="opExtra.add('${esc(t.district)}')">${PLUS}Добавить позицию</button>` : ''}</div>`;
    }).join('');
    // Цены закупки — у отправленной заявки: их узнают на базе.
    const items = {}; (d.tasks || []).forEach(t => (t.lines || []).forEach(l => {
      items[l.id] = items[l.id] || {id: l.id, name: l.name, unit: l.unit_name || 'бутылку'}; }));
    const prices = черн || !Object.keys(items).length ? '' : `<div class="oxt-grp">Цены закупки</div>
      <div class="oxt-card">${Object.values(items).map(l => `<div class="oxt-l" style="border-top:0;border-bottom:1px solid var(--border3)">
        <span class="oxt-ln">${esc(l.name)}<i>AED / ${esc(l.unit)}</i></span>
        <input class="oxt-pr" inputmode="decimal" placeholder="0" value="${((d.buys || {})[l.id] || {}).price || ''}"
               onfocus="this.select()" onchange="opExtra.price('${esc(l.id)}', this)"></div>`).join('')}</div>`;
    box.innerHTML = send + `<div class="oxt-grp">Состав по районам</div>` + cards + prices;
    paintFoot();
  }

  function paintFoot(){
    const d = ST.one; if(!d) return;
    const k = dirty(), живая = d.status === 'draft' || d.status === 'open';
    const можноОтменить = живая && (d.tasks || []).some(t => !t.done_at && !t.cancelled_at && t.lock_why !== 'noscan');
    $('oxtFoot').innerHTML = k
      ? `<button class="oxt-btn" onclick="opExtra.undo()">Отменить правки</button>
         <button class="oxt-btn go" onclick="opExtra.save()" ${ST.busy ? 'disabled' : ''}>Сохранить · ${k}</button>`
      : (можноОтменить ? `<button class="oxt-btn bad" onclick="opExtra.cancel()">${ST.sure ? 'Точно отменить' : 'Отменить заявку'}</button>` : '')
        + (d.status === 'draft' ? `<button class="oxt-btn go" id="oxtSend" onclick="opExtra.send()" ${
            (ST.base || '').trim() && !ST.busy ? '' : 'disabled'}>Отправить водителям</button>` : '');
  }

  const recent = skip => [...new Set((ST.list || []).map(x => x.base).filter(b => b && !(skip || []).includes(b)))].slice(0, 6);
  const nameOf = pid => ((ST.cat || []).find(p => p.id === pid) || {}).name || pid;

  async function loadCat(){
    if(ST.cat) return;
    try{ const r = await window.opApi.opFetch('/api/operator/catalog');
      // Сигареты в заявку на базу не идут — их берут отдельно.
      ST.cat = (r.items || []).filter(p => p.cat !== 'Сигареты'); }
    catch(e){ ST.cat = []; }
  }

  // Выбор позиции для района: поиск по каталогу, нажатие кладёт одну.
  function paintAdd(){
    const d = ST.one, t = (d.tasks || []).find(x => x.district === ST.add);
    head('Добавить позицию', `${esc(t.district_code)} ${esc(t.district_name)}`, true);
    const есть = new Set((t.lines || []).map(l => l.id).concat(Object.keys(ST.ed[ST.add] || {})));
    const q = ST.q.trim().toLowerCase();
    const list = (ST.cat || []).filter(p => !есть.has(p.id) && (!q || p.name.toLowerCase().includes(q))).slice(0, 60);
    if(!$('oxtFind')){
      $('oxtBody').innerHTML = `<label class="oxt-q">${FIND}<input id="oxtFind" type="text" placeholder="Поиск по названию"
        autocomplete="off" oninput="opExtra.find(this.value)"></label><div id="oxtPick"></div>`;
    }
    $('oxtPick').innerHTML = !ST.cat ? Array(5).fill('<div class="oxt-sk"></div>').join('')
      : !list.length ? '<div class="oxt-empty">Ничего не нашли</div>'
      : `<div class="oxt-card">${list.map(p => `<button class="oxt-l" style="width:100%;background:none;border:0;border-bottom:1px solid var(--border3);color:var(--text);font-family:inherit;text-align:left"
           onclick="opExtra.put('${esc(p.id)}')">
          <img src="/products/${esc(p.id)}.webp" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
          <span class="oxt-ln">${esc(p.name)}<i>${esc(p.cat)}</i></span><span class="oxt-ch" style="color:var(--gold2)">${PLUS}</span></button>`).join('')}</div>`;
    $('oxtFoot').innerHTML = '';
  }

  // ── новая заявка ──────────────────────────────────────────────────────────
  const nwTot = (oid) => Object.values(ST.nw.rows).reduce((a, r) => a + (oid ? (r[oid] || 0)
    : Object.values(r).reduce((x, y) => x + (y || 0), 0)), 0);

  function paintNew(){
    const w = ST.nw;
    head('Новая заявка', w.step === 1 ? 'Куда едем' : esc(w.base), true);
    const box = $('oxtBody');
    if(w.step === 1){
      box.innerHTML = `<input class="oxt-in" id="oxtNBase" placeholder="Название базы" maxlength="60" autocomplete="off"
          value="${esc(w.base)}" oninput="opExtra.nbase(this.value)">
        ${recent().length ? `<div class="oxt-chips">${recent().map(b =>
          `<button class="oxt-chip" data-b="${esc(b)}" onclick="opExtra.nbase(this.dataset.b, 1)">${esc(b)}</button>`).join('')}</div>` : ''}`;
      $('oxtFoot').innerHTML = `<button class="oxt-btn go" id="oxtNGo" onclick="opExtra.nstep(2)" ${w.base.trim() ? '' : 'disabled'}>Дальше</button>`;
      return;
    }
    const q = ST.q.trim().toLowerCase();
    // Набранное в этот район — сверху: его правят чаще, чем ищут новое.
    const list = (ST.cat || []).filter(p => !q || p.name.toLowerCase().includes(q))
      .sort((a, b) => ((w.rows[b.id] || {})[w.oid] ? 1 : 0) - ((w.rows[a.id] || {})[w.oid] ? 1 : 0)).slice(0, 80);
    if(!$('oxtNFind')){
      box.innerHTML = `<div class="oxt-tabs" id="oxtNTabs"></div>
        <label class="oxt-q">${FIND}<input id="oxtNFind" type="text" placeholder="Поиск по названию"
          autocomplete="off" oninput="opExtra.find(this.value)"></label><div id="oxtNList"></div>`;
    }
    $('oxtNTabs').innerHTML = ST.dists.map(x => { const k = nwTot(x.id);
      return `<button class="oxt-chip${x.id === w.oid ? ' on' : ''}" onclick="opExtra.ndist('${esc(x.id)}')">${esc(x.code)}${k ? `<i>${n(k)}</i>` : ''}</button>`; }).join('');
    $('oxtNList').innerHTML = !ST.cat ? Array(6).fill('<div class="oxt-sk"></div>').join('')
      : !list.length ? '<div class="oxt-empty">Ничего не нашли</div>'
      : `<div class="oxt-card">${list.map(p => { const v = (w.rows[p.id] || {})[w.oid] || 0;
          return `<div class="oxt-l" style="border-top:0;border-bottom:1px solid var(--border3)">
            <img src="/products/${esc(p.id)}.webp" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            <span class="oxt-ln">${esc(p.name)}<i>${esc(p.cat)}</i></span>
            <span class="oxt-stp"><button onclick="opExtra.nput('${esc(p.id)}',-1)" aria-label="меньше">−</button>
              <b class="${v ? 'ch' : ''}">${n(v)}</b>
              <button onclick="opExtra.nput('${esc(p.id)}',1)" aria-label="больше">+</button></span></div>`; }).join('')}</div>`;
    const всего = nwTot();
    $('oxtFoot').innerHTML = `<button class="oxt-btn go" onclick="opExtra.create()" ${всего && !ST.busy ? '' : 'disabled'}>${
      всего ? `Создать · ${ед(всего)}` : 'Наберите товар'}</button>`;
  }

  function paint(){
    if(!$('oxtOv')) return;
    if(ST.view === 'list') paintList(); else if(ST.view === 'one') paintOne(); else paintNew();
  }

  // ── действия ──────────────────────────────────────────────────────────────
  async function open(){
    if(!me()){ try{ window.showOv('opOv'); }catch(e){} return; }
    mount();
    Object.assign(ST, {view: 'list', list: null, one: null, err: '', ed: {}, add: '', q: '', nw: null, sure: false});
    paint(); $('oxtOv').classList.add('show'); tap('light');
    loadCat();
    await loadList(); paint();
  }
  function close(){ const o = $('oxtOv'); if(o) o.classList.remove('show'); }

  async function back(){
    if(ST.view === 'one' && ST.add){ ST.add = ''; ST.q = ''; $('oxtBody').innerHTML = ''; return paint(); }
    if(ST.view === 'new' && ST.nw.step === 2){ ST.nw.step = 1; ST.q = ''; $('oxtBody').innerHTML = ''; return paint(); }
    ST.view = 'list'; ST.one = null; ST.ed = {}; ST.nw = null; ST.err = ''; ST.sure = false;
    $('oxtBody').innerHTML = ''; paint();
    await loadList(); if(ST.view === 'list') paint();
  }

  async function one(sid){
    Object.assign(ST, {view: 'one', sid, one: null, err: '', ed: {}, add: '', base: '', sure: false});
    $('oxtBody').innerHTML = ''; paint(); tap('light');
    try{ ST.one = await api('/' + encodeURIComponent(sid), {params: {as: me()}}); }
    catch(e){ ST.err = '1'; }
    if(ST.view === 'one' && ST.sid === sid) paint();
  }

  function step(oid, pid, dir){
    const d = ST.one, t = (d.tasks || []).find(x => x.district === oid); if(!t) return;
    const l = (t.lines || []).find(x => x.id === pid) || {id: pid, plan: 0, need: 0};
    const base = l.plan != null ? l.plan : l.need;
    const v = Math.max(0, Math.min(999, edQty(oid, l) + dir));
    const m = ST.ed[oid] = ST.ed[oid] || {};
    if(v === base) delete m[pid]; else m[pid] = v;
    if(!Object.keys(m).length) delete ST.ed[oid];
    ST.sure = false; tap('sel'); paint();
  }
  function add(oid){ ST.add = oid; ST.q = ''; $('oxtBody').innerHTML = ''; tap('light'); paint(); }
  function find(v){ ST.q = String(v || ''); paint(); }
  function put(pid){
    const m = ST.ed[ST.add] = ST.ed[ST.add] || {};
    m[pid] = 1; ST.add = ''; ST.q = ''; $('oxtBody').innerHTML = ''; tap('sel'); paint();
  }
  function undo(){ ST.ed = {}; tap('light'); paint(); }

  async function save(){
    if(ST.busy || !ST.one) return; ST.busy = true; paintFoot();
    const sid = ST.one.supply_id;
    try{
      for(const [oid, m] of Object.entries(ST.ed)){
        const lines = Object.entries(m).map(([product_id, qty]) => ({product_id, qty}));
        if(!lines.length) continue;
        ST.one = await api(`/${encodeURIComponent(sid)}/lines`, {method: 'POST', body: {district: oid, lines, as: me()}});
        delete ST.ed[oid];
      }
      say('Сохранено'); tap('medium');
    }catch(e){
      const p = e.payload || {};
      say(p.error === 'district_locked' ? 'Район уже начали принимать — править нельзя' : 'Не удалось сохранить', 'err');
      // Что сервер успел принять — уже принято; показываем как есть.
      try{ ST.one = await api('/' + encodeURIComponent(sid), {params: {as: me()}}); ST.ed = {}; }catch(e2){}
    }finally{ ST.busy = false; paint(); }
  }

  function base(v, chip){
    ST.base = String(v || '');
    if(chip){ const i = $('oxtBase'); if(i) i.value = ST.base; tap('sel'); }
    document.querySelectorAll('#oxtBody .oxt-chip').forEach(c => c.classList.toggle('on', c.dataset.b === ST.base.trim()));
    paintFoot();
  }

  async function send(){
    if(ST.busy || !ST.one) return;
    const b = (ST.base || '').trim(); if(!b) return;
    ST.busy = true; paintFoot();
    try{
      await api(`/${encodeURIComponent(ST.one.supply_id)}/confirm`, {method: 'POST', body: {base: b, as: me()}});
      say('Отправлено водителям · ' + b); tap('medium');
      ST.busy = false; return back();            // последнее действие возвращает в список
    }catch(e){ say((e.payload || {}).error === 'not_draft' ? 'Её уже отправили' : 'Не удалось отправить', 'err'); }
    ST.busy = false; paintFoot();
  }

  async function cancel(){
    if(ST.busy || !ST.one) return;
    if(!ST.sure){ ST.sure = true; tap('medium'); return paintFoot(); }   // второе нажатие — решение
    ST.busy = true;
    try{
      await api(`/${encodeURIComponent(ST.one.supply_id)}/cancel`, {method: 'POST', body: {as: me(), force: true}});
      say('Заявка отменена'); ST.busy = false; return back();
    }catch(e){
      say((e.payload || {}).error === 'nothing_to_cancel' ? 'Отменять нечего — товар уже принят' : 'Не удалось отменить', 'err');
    }
    ST.busy = false; ST.sure = false; paintFoot();
  }

  async function price(pid, inp){
    const v = parseFloat(String(inp.value || '').replace(',', '.').replace(/[^\d.]/g, '')) || 0;
    try{
      const r = await api(`/${encodeURIComponent(ST.one.supply_id)}/buy`, {method: 'POST', body: {product_id: pid, price: v, as: me()}});
      if(r.buys) ST.one.buys = r.buys;
      tap('sel');
    }catch(e){ say('Не удалось сохранить цену', 'err'); }
  }

  function newOpen(){
    ST.view = 'new'; ST.q = '';
    ST.nw = {step: 1, base: '', rows: {}, oid: (ST.dists[0] || {}).id || ''};
    $('oxtBody').innerHTML = ''; tap('light'); paint();
  }
  function nbase(v, chip){
    ST.nw.base = String(v || '');
    if(chip){ const i = $('oxtNBase'); if(i) i.value = ST.nw.base; tap('sel'); }
    document.querySelectorAll('#oxtBody .oxt-chip').forEach(c => c.classList.toggle('on', c.dataset.b === ST.nw.base.trim()));
    const b = $('oxtNGo'); if(b) b.disabled = !ST.nw.base.trim();
  }
  function nstep(k){ if(k === 2 && !ST.nw.base.trim()) return; ST.nw.step = k; ST.q = ''; $('oxtBody').innerHTML = ''; tap('light'); paint(); }
  function ndist(oid){ ST.nw.oid = oid; tap('sel'); paint(); }
  function nput(pid, dir){
    const r = ST.nw.rows[pid] = ST.nw.rows[pid] || {};
    const v = Math.max(0, Math.min(999, (r[ST.nw.oid] || 0) + dir));
    if(v) r[ST.nw.oid] = v; else delete r[ST.nw.oid];
    if(!Object.keys(r).length) delete ST.nw.rows[pid];
    tap('sel'); paint();
  }
  async function create(){
    if(ST.busy) return;
    const items = Object.entries(ST.nw.rows).map(([id, by_district]) => ({id, by_district}));
    if(!items.length) return;
    ST.busy = true; paint();
    try{
      const r = await api('', {method: 'POST', body: {base: ST.nw.base.trim(), items, source: 'manual', as: me()}});
      say('Заявка создана — задачи ушли водителям'); tap('medium');
      ST.busy = false; ST.nw = null;
      await loadList();
      return one(r.supply_id);
    }catch(e){ say('Не удалось создать', 'err'); }
    ST.busy = false; paint();
  }

  window.opExtra = {open, close, back, one, step, add, find, put, undo, save, base, send, cancel, price,
                    newOpen, nbase, nstep, ndist, nput, create};
})();
