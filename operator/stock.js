// Остатки по районам — только смотреть (владелец, 30 сен 2026: «чтобы
// операторы смогли смотреть остатки склада в конкретный текущий момент, не
// редактировать, а просто видеть склад каждого района по категориям,
// количеству… так же, как у нас в AMBAR STAR остатки по районам»).
//
// Тот же расчёт, что у карточки «Склад» в STAR, но без денег. Сверху районы с
// итогом — нажатие оставляет один район; ниже каталог по категориям, у каждой
// свой итог. Три разных ответа в клетке, и путать их нельзя: число — сколько
// лежит, ноль — считали и нет, прочерк — в этот район ни разу не вносили.
// Пока окно открыто, числа перечитываются сами.
//
// Отдельным файлом, как short.js: окно приносит свою разметку и стили
// (.ost-*), в панели от него только кнопка.
(function(){
  const ST = {d: null, err: '', sel: '', q: '', only: true, t: null, at: 0, busy: false};
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const me = () => { try{ return curOp || ''; }catch(e){ return ''; } };
  const tap = k => { try{ window.haptic(k || 'light'); }catch(e){} };
  // Единицы: бутылка целым, коробка пива бывает половиной.
  const q = v => { v = Math.round((+v || 0) * 2) / 2;
    const [a, b] = String(v).split('.');
    return a.replace(/\B(?=(\d{3})+(?!\d))/g, '\u202f') + (b ? ',' + b : ''); };
  const X = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const FIND = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>';
  // Порядок категорий — как на полке и в заявке: крепкое, вино, пиво, прочее.
  const CATS = ['Водка', 'Виски', 'Коньяк', 'Джин', 'Ром', 'Текила', 'Ликёр', 'Вермут', 'Арак',
                'Вино', 'Шампанское', 'Просекко', 'Пиво', 'Сигареты'];

  function mount(){
    if($('ostOv')) return;
    const css = document.createElement('style');
    css.textContent = `
.ost-sheet{width:min(760px,100%);height:min(92vh,92dvh);display:flex;flex-direction:column;padding:18px 16px 0;overflow:hidden}
.ost-hd{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0}
.ost-hd b{flex:1;min-width:0;font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.ost-hd i{display:block;font-style:normal;font-size:12.5px;color:var(--sub);font-weight:500;letter-spacing:0;text-transform:none;margin-top:2px}
.ost-strip{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:6px;flex-shrink:0}
.ost-d{display:flex;flex-direction:column;align-items:center;gap:3px;padding:9px 2px;border-radius:13px;background:var(--card);border:1px solid var(--border3);color:var(--text);font-family:inherit;min-width:0}
.ost-d.on{background:var(--gold-soft);border-color:rgba(201,169,110,.55)}
.ost-d.mine .ost-dc{color:var(--gold2)}
.ost-dc{font-family:'Space Grotesk',sans-serif;font-size:12px;font-weight:700;color:var(--sub)}
.ost-dn{font-family:'Space Grotesk',sans-serif;font-size:16px;font-weight:700;white-space:nowrap}
.ost-d.z .ost-dn{color:var(--sub);opacity:.6}
.ost-bar{display:flex;align-items:center;gap:8px;margin:10px 0;flex-shrink:0}
.ost-q{flex:1;min-width:0;display:flex;align-items:center;gap:8px;height:40px;padding:0 12px;border-radius:12px;background:var(--card);border:1px solid var(--border3);color:var(--sub)}
.ost-q input{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--text);font-family:inherit;font-size:14px;padding:0}
.ost-f{height:40px;padding:0 13px;border-radius:12px;background:var(--card);border:1px solid var(--border3);color:var(--sub);font-family:inherit;font-size:12.5px;font-weight:600;white-space:nowrap}
.ost-f.on{background:var(--gold-soft);border-color:rgba(201,169,110,.55);color:var(--gold2)}
.ost-body{flex:1;min-height:0;overflow:auto;margin:0 -16px;padding:0 16px 18px;-webkit-overflow-scrolling:touch}
.ost-t{width:100%;border-collapse:separate;border-spacing:0;font-size:13.5px}
.ost-t th{position:sticky;top:0;z-index:2;background:var(--bg3);padding:7px 4px;font-family:'Space Grotesk',sans-serif;font-size:11.5px;font-weight:700;color:var(--sub);text-align:right;border-bottom:1px solid var(--border3)}
.ost-t th.nm{text-align:left;padding-left:2px}
.ost-t th:not(.nm){width:40px;min-width:40px}
.ost-t th.tot{width:50px;min-width:50px}
.ost-t th.mine{color:var(--gold2)}
.ost-t td{padding:9px 4px;text-align:right;border-bottom:1px solid var(--border3);font-family:'Space Grotesk',sans-serif;font-weight:600;white-space:nowrap}
.ost-t td.nm{text-align:left;white-space:normal;padding-left:2px;font-family:inherit;font-weight:500;line-height:1.25}
.ost-t td.nm s{text-decoration:none;color:var(--sub);font-size:11.5px;margin-right:6px;font-family:'Space Grotesk',sans-serif}
.ost-t td.z{color:var(--sub);opacity:.45;font-weight:500}
.ost-t td.nil{color:var(--sub);opacity:.75}
.ost-t td.crit{color:var(--dn);opacity:1;font-weight:700}
.ost-t td.tot,.ost-t th.tot{color:var(--gold2)}
.ost-t td.tot.nil,.ost-t td.tot.z{color:var(--sub)}
.ost-t tr.cat td{padding:16px 4px 7px;border-bottom:1px solid rgba(201,169,110,.3);font-size:12px;color:var(--gold2);letter-spacing:.07em;text-transform:uppercase}
.ost-t tr.cat td.nm{font-family:'Space Grotesk',sans-serif;font-weight:700}
.ost-note{font-size:12px;color:var(--sub);line-height:1.5;margin:14px 2px 0}
.ost-empty{padding:40px 10px;text-align:center;color:var(--sub);font-size:13.5px}
.ost-sk{height:40px;border-radius:10px;background:var(--card);margin-bottom:8px;opacity:.6}
@media(max-width:520px){
  .ost-sheet{padding:16px 12px 0;border-radius:20px 20px 0 0;height:94dvh;align-self:flex-end}
  .ost-body{margin:0 -12px;padding:0 12px 18px}
  .ost-t{font-size:13px} .ost-t td,.ost-t th{padding-left:3px;padding-right:3px}
  .ost-dn{font-size:14.5px}
}`;
    document.head.appendChild(css);
    const ov = document.createElement('div');
    ov.className = 'ov'; ov.id = 'ostOv';
    ov.addEventListener('click', e => { if(e.target === ov) close(); });
    ov.innerHTML = `<div class="sheet ost-sheet">
      <div class="ost-hd"><b>Остатки<i id="ostSub"></i></b>
        <button class="oc-x" onclick="opStock.close()" aria-label="Закрыть">${X}</button></div>
      <div id="ostTop"></div>
      <div class="ost-body" id="ostBody"></div></div>`;
    document.body.appendChild(ov);
  }

  const when = () => { const d = new Date(ST.at);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

  function paintTop(){
    const d = ST.d; if(!d) { $('ostTop').innerHTML = ''; return; }
    const mine = new Set(d.mine || []);
    const sel = ST.sel;
    // Поле поиска при перерисовке не пересобираем: вместе с ним ушёл бы курсор.
    if(!$('ostQ')){
      $('ostTop').innerHTML = `<div class="ost-strip" id="ostStrip"></div>
        <div class="ost-bar"><label class="ost-q">${FIND}<input id="ostQ" type="text" placeholder="Поиск по названию"
          autocomplete="off" oninput="opStock.find(this.value)"></label>
          <button class="ost-f" id="ostOnly" onclick="opStock.only()">С остатком</button></div>`;
    }
    $('ostStrip').innerHTML = (d.districts || []).map(x => {
      const n = ((d.by_district || {})[x.id] || {}).bottles || 0;
      return `<button class="ost-d${sel === x.id ? ' on' : ''}${mine.has(x.id) ? ' mine' : ''}${n ? '' : ' z'}"
          onclick="opStock.pick('${esc(x.id)}')" aria-label="${esc(x.name)}">
        <span class="ost-dc">${esc(x.code)}</span><span class="ost-dn">${x.known === false ? '—' : q(n)}</span></button>`;
    }).join('');
    $('ostOnly').classList.toggle('on', ST.only);
  }

  function paint(){
    const box = $('ostBody'); if(!box) return;
    const d = ST.d;
    if(!d){
      $('ostSub').textContent = ST.err ? '' : 'загрузка…';
      $('ostTop').innerHTML = '';
      box.innerHTML = ST.err
        ? (window.loadFail ? window.loadFail('opStock.open()') : '<div class="ost-empty">Не удалось загрузить</div>')
        : Array(9).fill('<div class="ost-sk"></div>').join('');
      return;
    }
    $('ostSub').textContent = `Сейчас · всего ${q(d.total)} · обновлено ${when()}`;
    paintTop();
    const mine = new Set(d.mine || []);
    const cols = (d.districts || []).filter(x => !ST.sel || x.id === ST.sel);
    const needle = ST.q.trim().toLowerCase();
    const есть = r => ST.sel ? (+(r.have || {})[ST.sel] || 0) > 0 : (+r.bottles || 0) > 0;
    const rows = (d.items || []).filter(r =>
      (!needle || (r.name || '').toLowerCase().includes(needle) || String(r.no) === needle)
      && (!ST.only || есть(r)));
    if(!rows.length){
      box.innerHTML = `<div class="ost-empty">${needle ? 'Ничего не нашли' : 'В этом районе остатка нет'}</div>`;
      return;
    }
    const groups = {};
    rows.forEach(r => (groups[r.cat || 'Прочее'] = groups[r.cat || 'Прочее'] || []).push(r));
    const order = Object.keys(groups).sort((a, b) =>
      (CATS.indexOf(a) < 0 ? 99 : CATS.indexOf(a)) - (CATS.indexOf(b) < 0 ? 99 : CATS.indexOf(b)));
    // Критический остаток — красным: меньше, чем уходит за день, или пусто.
    const crit = d.critical || {};
    const cell = (h, oid, pid) => { const к = (crit[oid] || []).includes(pid) ? ' crit' : '';
      return h == null ? '<td class="z">—</td>' : !h ? `<td class="nil${к}">0</td>` : `<td class="${к.trim()}">${q(h)}</td>`; };
    const sum = (list, id) => list.reduce((a, r) => a + (+(r.have || {})[id] || 0), 0);
    const one = !!ST.sel;
    box.innerHTML = `<table class="ost-t"><thead><tr><th class="nm">№ и позиция</th>${
        cols.map(x => `<th class="${mine.has(x.id) ? 'mine' : ''}">${esc(x.code)}</th>`).join('')}${
        one ? '' : '<th class="tot">Всего</th>'}</tr></thead><tbody>${
      order.map(c => {
        const list = groups[c];
        return `<tr class="cat"><td class="nm">${esc(c)}</td>${
            cols.map(x => `<td>${q(sum(list, x.id))}</td>`).join('')}${
            one ? '' : `<td class="tot">${q(list.reduce((a, r) => a + (+r.bottles || 0), 0))}</td>`}</tr>`
          + list.map(r => `<tr><td class="nm"><s>${r.no || ''}</s>${esc(r.name)}</td>${
            cols.map(x => cell((r.have || {})[x.id], x.id, r.id)).join('')}${
            one ? '' : `<td class="tot ${!r.known ? 'z' : r.bottles ? '' : 'nil'}">${!r.known ? '—' : q(r.bottles)}</td>`}</tr>`).join('');
      }).join('')}</tbody></table>
      <div class="ost-note">Числа — в учётных единицах: у крепкого и вина бутылки, у пива коробки
        (0,5 — 12 банок, 1 — 24). Ноль — считали, и нет; прочерк — в этот район позицию ни разу не вносили.</div>`;
  }

  async function load(тихо){
    if(ST.busy) return; ST.busy = true;
    try{
      const d = await window.opApi.opFetch('/api/operator/stock/board', {params: {as: me()}});
      ST.d = d; ST.err = ''; ST.at = Date.now();
    }catch(e){ if(!тихо || !ST.d){ ST.d = null; ST.err = '1'; } }
    finally{ ST.busy = false; }
    if($('ostOv') && $('ostOv').classList.contains('show')){
      const b = $('ostBody'), y = b ? b.scrollTop : 0;
      paint();
      if(b && тихо) b.scrollTop = y;
    }
  }

  async function open(){
    if(!me()){ try{ window.showOv('opOv'); }catch(e){} return; }
    mount();
    Object.assign(ST, {d: null, err: '', sel: '', q: '', only: true});
    paint(); $('ostOv').classList.add('show'); tap('light');
    await load();
    if(ST.t) clearInterval(ST.t);
    // «В текущий момент»: пока окно открыто, числа перечитываются сами.
    ST.t = setInterval(() => { if(!document.hidden && $('ostOv').classList.contains('show')) load(true); }, 20000);
  }

  function close(){
    const o = $('ostOv'); if(o) o.classList.remove('show');
    if(ST.t){ clearInterval(ST.t); ST.t = null; }
  }
  function pick(id){ ST.sel = ST.sel === id ? '' : id; tap('sel'); paint(); $('ostBody').scrollTop = 0; }
  function find(v){ ST.q = String(v || ''); paint(); }
  function only(){ ST.only = !ST.only; tap('sel'); paint(); }

  window.opStock = {open, close, pick, find, only};
})();
