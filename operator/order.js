/* Заявка магазину у оператора: только правка, экран — как у старшего.
 *
 * Владелец, 21 сен 2026: «могут ли операторы увидеть эту заявку и
 * отредактировать её… им нужен только этот функционал, сугубо редактирование»;
 * и следом, про телефон: «сделай это ровно так же, как у старшего».
 *
 * Поэтому здесь не свой вид, а тот же самый: список позиций, как «Общая
 * заявка» у старшего. Строка — номер, фото, название и итог по всем районам;
 * раскрывается в пять строк районов, у каждой «на полке N» и счётчик «− N +»,
 * снизу «Вернуть расчёт».
 * Разметка и стили перенесены из STAR один в один и заперты внутри #ozvBox,
 * чтобы не задеть ничего в операторской.
 *
 * Чего здесь нет намеренно: статусов заявки, отправки в магазин и загрузки
 * ответа — это работа старшего.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const цел = (v) => Math.max(0, Math.min(9999, Math.round(parseFloat(String(v).replace(',', '.')) || 0)));
  const чис = (n) => String(Math.round((+n || 0) * 100) / 100).replace('.', ',');
  const МЕС = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
               'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const день = (iso) => {
    const d = new Date(String(iso || '') + 'T12:00:00');
    return isNaN(d) ? String(iso || '') : `${d.getDate()} ${МЕС[d.getMonth()]}`;
  };
  const скл = (n, a, b, c) => {
    n = Math.abs(Math.round(n)) % 100;
    if (n > 10 && n < 20) return c;
    n %= 10;
    return n === 1 ? a : (n > 1 && n < 5) ? b : c;
  };
  const ШЕВРОН = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
    stroke-width="2.6" stroke-linecap="round"><path d="M9 6l6 6-6 6"/></svg>`;

  const СОХР_МС = 400;          // пачка правок уходит через эту паузу
  const ОБНОВ_МС = 20000;       // чужие правки подтягиваем сами

  let O = null;   // {d, q, fold:{}, open, pend:{}, timer, busy, poll}

  function готовь() {
    if ($('ozvOv')) return;
    const st = document.createElement('style');
    st.textContent = `
.ozv-ov{position:fixed;inset:0;z-index:150;background:rgba(0,0,10,.72);backdrop-filter:blur(6px);
  display:none;align-items:center;justify-content:center;padding:18px}
.ozv-ov.show{display:flex}
.ozv{display:flex;flex-direction:column;width:min(760px,96vw);height:min(880px,92vh);
  border-radius:20px;overflow:hidden;background:var(--bg3);border:1px solid var(--border3);
  box-shadow:0 24px 80px rgba(0,0,0,.7)}
.ozv-hd{display:flex;align-items:flex-start;gap:12px;padding:18px 18px 12px;
  border-bottom:1px solid var(--border2)}
.ozv-hd .t{font-family:'Space Grotesk',sans-serif;font-size:18px;font-weight:700}
.ozv-hd .s{margin-top:3px;font-size:12px;color:var(--sub)}
.ozv-x{margin-left:auto;flex:0 0 auto;width:32px;height:32px;border-radius:10px;
  border:1px solid var(--border2);background:none;color:var(--sub);font-size:17px;
  line-height:1;cursor:pointer}
.ozv-x:active{transform:scale(.96)}
.ozv-l{flex:1;min-height:0;overflow:auto;padding:14px 16px 18px}
.ozv-ft{padding:11px 18px;border-top:1px solid var(--border2);font-size:12px;color:var(--muted)}
.ozv-ft b{color:var(--sub);font-weight:600}
#ozvBox #ozvRows > .ord-r{border-radius:16px;margin-bottom:8px;padding:0 14px;
  background:linear-gradient(180deg,#161629 0%,#0F0F20 100%);
  border:1px solid rgba(201,169,110,.28)}
#ozvBox #ozvRows > .ord-r.open{border-color:rgba(201,169,110,.5)}
@media (max-width:760px){
  .ozv-ov{padding:var(--safe-top) 0 0;align-items:stretch}
  .ozv{width:100vw;height:100%;border-radius:0;border:none}
  .ozv-hd{padding:12px 14px 10px}
  .ozv-l{padding:12px 12px 16px}
}
#ozvBox .ord-r:not(.fixed) .ord-reset{display:none}
#ozvBox .ord-find{position:relative;display:flex;align-items:center;margin:0 0 10px}
#ozvBox .ord-find svg{position:absolute;left:12px;width:15px;height:15px;fill:none;stroke:var(--muted);
  stroke-width:1.9;stroke-linecap:round;pointer-events:none}
#ozvBox .ord-find input{width:100%;padding:11px 34px;border-radius:12px;background:var(--card);
  border:1px solid var(--border2);color:var(--text);font-family:'DM Sans',sans-serif;
  font-size:13px;outline:none;-webkit-appearance:none}
#ozvBox .ord-find input:focus{border-color:var(--border3)}
#ozvBox .ord-find-x{position:absolute;right:6px;width:26px;height:26px;border:0;border-radius:8px;
  background:none;color:var(--muted);font-size:13px;cursor:pointer}
#ozvBox .ord-dl{margin-top:10px;padding-top:2px;border-top:1px solid rgba(201,169,110,.12)}
#ozvBox .ord-cap{display:flex;align-items:center;min-height:50px;padding:0 16px;
  font-family:'Space Grotesk',sans-serif;font-size:18px;font-weight:700;
  letter-spacing:1.1px;text-transform:uppercase;line-height:1.15;color:var(--gold);
  background:linear-gradient(180deg,rgba(201,169,110,.2),rgba(201,169,110,.08));
  border-bottom:1px solid rgba(201,169,110,.34)}
#ozvBox .shl-b i.warn{color:var(--warn)}
#ozvBox .ord-grp{font-size:9.5px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:var(--muted);padding:2px 4px 8px}
#ozvBox .ord-dists{border-radius:16px;overflow:hidden;
  background:linear-gradient(180deg,#161629 0%,#0F0F20 100%);
  border:1px solid rgba(201,169,110,.28)}
#ozvBox .ord-dists .shl-card{margin:0;border-radius:0;border:0;background:transparent}
#ozvBox .ord-dists .shl-card + .shl-card{border-top:1px solid rgba(201,169,110,.14)}
#ozvBox .ord-dh{width:100%;border:0;background:none;color:var(--text);font-family:inherit;
  text-align:left;cursor:pointer;padding:0;-webkit-tap-highlight-color:transparent}
#ozvBox .ord-dh:active{opacity:.75}
#ozvBox .ord-dh-go{flex:0 0 auto;display:flex;color:var(--muted);margin-left:-2px;
  transition:transform .18s var(--e1)}
#ozvBox .ord-dc.open .ord-dh-go{transform:rotate(90deg)}
#ozvBox .ord-r-img{flex:0 0 auto;width:32px;height:32px;border-radius:9px;overflow:hidden;
  background:var(--card2);border:1px solid var(--border2)}
#ozvBox .ord-r-img img{width:100%;height:100%;object-fit:cover;display:block}
#ozvBox .ord-r{border-radius:0;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.05);overflow:hidden}
#ozvBox .ord-r:first-child{border-top:0}
#ozvBox .ord-r.fixed .ord-r-n{color:var(--gold2)}
#ozvBox .ord-r-h{display:flex;align-items:center;gap:10px;width:100%;padding:11px 0;
  border:0;background:none;color:var(--text);font-family:inherit;cursor:pointer;text-align:left}
#ozvBox .ord-r-h:active{opacity:.7}
#ozvBox .ord-r-n{flex:1;min-width:0;font-size:13px;font-weight:600;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
#ozvBox .ord-r-d{font-size:11px;color:var(--muted);white-space:nowrap}
#ozvBox .ord-r-v{font-family:'DM Sans',sans-serif;font-size:14px;font-weight:700;
  font-variant-numeric:tabular-nums;min-width:34px;text-align:right}
#ozvBox .ord-r-v.gold{color:var(--gold2)}
#ozvBox .ord-r-go{width:12px;text-align:center;color:var(--muted);font-size:14px;flex-shrink:0}
#ozvBox .ord-r-b{padding:2px 0 10px}
#ozvBox .ord-c{display:flex;align-items:center;gap:10px;padding:9px 0}
#ozvBox .ord-c + .ord-c{border-top:1px solid rgba(255,255,255,.04)}
#ozvBox .ord-c-n{flex:1;min-width:0;font-size:12px;color:var(--sub);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
#ozvBox .ord-c-n b{color:var(--gold2);font-weight:700;margin-right:4px}
#ozvBox .ord-c-calc{font-size:10px;color:var(--muted);white-space:nowrap}
#ozvBox .ord-st{display:flex;align-items:center;gap:2px;flex-shrink:0}
#ozvBox .ord-st button{width:32px;height:32px;border-radius:9px;border:1px solid var(--border3);
  background:var(--card2);color:var(--text);font-size:17px;font-weight:600;cursor:pointer;
  display:flex;align-items:center;justify-content:center}
#ozvBox .ord-st button:active{background:var(--card)}
#ozvBox .ord-st input{width:46px;height:32px;text-align:center;border:0;background:none;
  color:var(--text);font-family:'DM Sans',sans-serif;font-size:14px;font-weight:700;
  font-variant-numeric:tabular-nums;outline:none;-webkit-appearance:none}
#ozvBox .ord-st input.fixed{color:var(--gold2)}
#ozvBox .ord-c-big{justify-content:center;padding:8px 0 4px}
#ozvBox .ord-c-big .ord-st{gap:14px}
#ozvBox .ord-c-big .ord-st button{width:56px;height:52px;border-radius:15px;font-size:24px;font-weight:600}
#ozvBox .ord-c-big .ord-st input{width:84px;height:52px;font-size:28px;letter-spacing:-.5px}
#ozvBox .ord-reset{width:100%;margin-top:10px;padding:10px;border-radius:10px;cursor:pointer;
  font-family:'DM Sans',sans-serif;font-size:12px;font-weight:600;color:var(--muted);
  background:var(--card2);border:1px solid var(--border2)}
#ozvBox .ord-reset:active{color:var(--text)}
#ozvBox .shl-v b.dn{color:var(--dn)}
#ozvBox .exp-empty{color:var(--muted);font-size:12.5px;text-align:center;padding:8px}
#ozvBox .shl-card{padding:12px 16px;border-radius:16px;color:var(--text);
  background:linear-gradient(180deg,#161629 0%,#0F0F20 100%);
  border:1px solid rgba(201,169,110,.28)}
#ozvBox .shl-card.done{opacity:.8}
#ozvBox .shl-h{display:flex;align-items:center;gap:13px;min-height:42px}
#ozvBox .shl-code{flex:0 0 auto;width:42px;height:42px;border-radius:13px;display:grid;place-items:center;
  font-family:'Space Grotesk',sans-serif;font-size:14px;font-weight:700;letter-spacing:.3px;
  color:var(--gold2);background:var(--gold-soft);border:1px solid rgba(201,169,110,.22)}
#ozvBox .shl-b{flex:1;min-width:0}
#ozvBox .shl-b b{display:block;font-family:'Space Grotesk',sans-serif;font-size:14px;font-weight:700;
  letter-spacing:.6px;text-transform:uppercase;line-height:1.2;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#ozvBox .shl-b i{display:block;font-style:normal;font-size:11.5px;color:var(--muted);margin-top:3px;line-height:1.35}
#ozvBox .shl-v{flex:0 0 auto;text-align:right}
#ozvBox .shl-v b{display:block;font-family:'Space Grotesk',sans-serif;font-size:15px;font-weight:700;
  letter-spacing:-.2px;color:var(--gold2);line-height:1;white-space:nowrap;font-variant-numeric:tabular-nums}
#ozvBox .shl-v b.live{color:var(--up)}
#ozvBox .shl-v b.op,#ozvBox .shl-v b.cl{color:var(--text);display:flex;align-items:center;
  justify-content:flex-end;gap:5px}
#ozvBox .shl-v b.cl{margin-top:5px}
#ozvBox .shl-v b s{text-decoration:none;display:inline-flex;line-height:0}
#ozvBox .shl-v b s.up{color:var(--up)} .shl-v b s.dn{color:var(--dn)}
#ozvBox .shl-b i em{font-style:normal;color:var(--dn)}
#ozvBox .shl-v b u{text-decoration:none;font-family:'DM Sans',sans-serif;font-size:9px;font-weight:600;
  letter-spacing:.4px;color:var(--muted);margin-left:4px}
#ozvBox .shl-v i{display:block;font-style:normal;font-family:'DM Sans',sans-serif;font-size:9px;
  font-weight:600;letter-spacing:.4px;text-transform:uppercase;color:var(--muted);margin-top:4px;white-space:nowrap}
#ozvBox .shl-v i em{font-style:normal;color:var(--dn)}
#ozvBox .ord-r-i{flex:0 0 auto;width:22px;font-family:'DM Sans',sans-serif;font-size:11px;
  font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right;
  padding-right:4px}
#ozvBox .ord-r.zero .ord-r-n{color:var(--sub)}
#ozvBox .ord-r.zero .ord-r-v{color:var(--muted);opacity:.6}`;
    document.head.appendChild(st);
    const ov = document.createElement('div');
    ov.className = 'ozv-ov';
    ov.id = 'ozvOv';
    ov.innerHTML = '<div class="ozv" id="ozvBox"></div>';
    ov.addEventListener('click', (e) => { if (e.target === ov) opOrdClose(); });
    document.body.appendChild(ov);
  }

  async function загрузи(тихо) {
    try {
      const d = await opApi.opFetch('/api/operator/stock/order');
      if (!O) return;
      // Молча подменяем данные, только когда никто ничего не набирает.
      if (тихо && (Object.keys(O.pend).length
          || (document.activeElement && document.activeElement.tagName === 'INPUT'))) return;
      const было = O.d ? JSON.stringify(O.d.rows.map(r => [r.id, r.need_total])) : '';
      O.d = d;
      if (!тихо || было !== JSON.stringify(d.rows.map(r => [r.id, r.need_total]))) рисуй();
    } catch (e) {
      if (O && !O.d) $('ozvBox').innerHTML =
        '<div class="exp-empty">Заявка не загрузилась. Закройте и откройте заново.</div>';
    }
  }

  // ── разметка: та же, что на экране старшего ──────────────────────────────
  function рисуй() {
    const d = O.d, dist = d.districts || [];
    const q = (O.q || '').trim().toLowerCase();
    const все = d.rows || [];
    const список = q ? все.filter(r => String(r.name || '').toLowerCase().includes(q)) : все;
    const ед = все.reduce((a, x) => a + (+x.need_total || 0), 0);
    const ном = {}; (d.all_rows || все).forEach((r, i) => { ном[r.id] = i + 1; });
    $('ozvBox').innerHTML = `
      <div class="ozv-hd">
        <div><div class="t">Заявка магазину</div>
          <div class="s">${esc(день(d.day))} · ${все.length} ${
            скл(все.length, 'позиция', 'позиции', 'позиций')} · ${чис(ед)} ед${
            d.edited_count ? ` · правок ${d.edited_count}` : ''}</div></div>
        <button class="ozv-x" onclick="opOrdClose()" aria-label="Закрыть">✕</button>
      </div>
      <div class="ozv-l">
        <div class="ord-find">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
          <input id="ozvQ" type="search" placeholder="Найти позицию" value="${esc(O.q || '')}"
                 oninput="opOrdFind(this.value)">
        </div>
        ${q ? `<div class="ord-grp">Найдено · ${список.length}</div>` : ''}
        <div id="ozvRows">${список.length
          ? список.map(r => строка(r, dist, ном[r.id])).join('')
          : `<div class="exp-empty">${q ? 'Ничего не нашлось'
              : 'Довозить нечего — остатков хватает'}</div>`}</div>
      </div>
      <div class="ozv-ft">Правки сохраняются сразу — старший увидит их у себя. <b id="ozvSave"></b></div>`;
  }

  // Строка позиции: та же разметка, что в «Общей заявке» у старшего.
  function строка(r, dist, num) {
    const open = O.open === r.id;
    const cells = dist.map(x => ({ x, c: r.cells[x.id] || {} }));
    return `<div class="ord-r${open ? ' open' : ''}${r.edited ? ' fixed' : ''}${
        r.need_total ? '' : ' zero'}" data-id="${esc(r.id)}">
      <button class="ord-r-h" onclick="opOrdRow('${esc(r.id)}')">
        <span class="ord-r-i">${num || ''}</span>
        <span class="ord-r-img"><img src="/products/${esc(r.id)}.webp" alt="" loading="lazy"
          onerror="this.style.visibility='hidden'"></span>
        <span class="ord-r-n">${esc(r.name)}</span>
        <span class="ord-r-v${r.edited ? ' gold' : ''}">${чис(r.need_total)}</span>
        <span class="ord-r-go">${open ? '⌄' : '›'}</span>
      </button>
      ${open ? `<div class="ord-r-b">
        ${cells.map(o => `<div class="ord-c" data-d="${esc(o.x.id)}">
          <span class="ord-c-n"><b>${esc(o.x.code)}</b> ${esc(o.x.name)}</span>
          <span class="ord-c-calc">${o.c.edited ? `расчёт ${чис(o.c.calc)}`
            : `на полке ${чис(o.c.have || 0)}`}</span>
          <span class="ord-st">
            <button onclick="opOrdStep('${esc(r.id)}','${esc(o.x.id)}',-1)" aria-label="Меньше">−</button>
            <input inputmode="numeric" value="${чис(o.c.need || 0)}"
                   onchange="opOrdSet('${esc(r.id)}','${esc(o.x.id)}',this.value)"
                   class="${o.c.edited ? 'fixed' : ''}">
            <button onclick="opOrdStep('${esc(r.id)}','${esc(o.x.id)}',1)" aria-label="Больше">+</button>
          </span>
        </div>`).join('')}
        <button class="ord-reset" onclick="opOrdReset('${esc(r.id)}')">
          Вернуть расчёт · <b>${чис(r.calc_total)}</b></button>
      </div>` : ''}
    </div>`;
  }

  // ── правка: сначала у себя, потом на сервер пачкой ───────────────────────
  function set(id, district, qty) {
    if (!O || !O.d) return;
    const r = (O.d.rows || []).find(x => x.id === id); if (!r) return;
    const c = (r.cells || (r.cells = {}))[district] || (r.cells[district] = {});
    c.need = цел(qty);
    c.edited = c.need !== (+c.calc || 0);
    r.need_total = Object.values(r.cells).reduce((a, x) => a + (+x.need || 0), 0);
    r.edited = Object.values(r.cells).some(x => x.edited);
    O.pend[id + '|' + district] = c.need;
    рисуй();
    пометь('сохраняю…');
    clearTimeout(O.timer);
    O.timer = setTimeout(шли, СОХР_МС);
  }

  function step(id, district, d) {
    const r = (O.d.rows || []).find(x => x.id === id); if (!r) return;
    const c = r.cells[district] || {};
    set(id, district, (+c.need || 0) + d);
    try { tg.HapticFeedback.selectionChanged(); } catch (e) {}
  }

  async function reset(id) {
    if (!O || !O.d) return;
    Object.keys(O.pend).forEach(k => { if (k.startsWith(id + '|')) delete O.pend[k]; });
    пометь('сохраняю…');
    try {
      await opApi.opFetch('/api/operator/stock/order/reset',
        { method: 'POST', body: { day: O.d.day, id } });
    } catch (e) { пометь('не сохранилось'); }
    await загрузи(false);
    пометь('сохранено');
    setTimeout(() => пометь(''), 1500);
  }

  function пометь(t) { const el = $('ozvSave'); if (el) el.textContent = t || ''; }

  async function шли() {
    if (!O || O.busy) return;
    const ключи = Object.keys(O.pend);
    if (!ключи.length) return;
    O.busy = true;
    try {
      for (const k of ключи) {
        const qty = O.pend[k], i = k.indexOf('|');
        const res = await opApi.opFetch('/api/operator/stock/order/edit', {
          method: 'POST',
          body: { day: O.d.day, id: k.slice(0, i), district: k.slice(i + 1), qty: String(qty) },
        });
        if (O.pend[k] === qty) delete O.pend[k];
        if (res && res.edited_count != null) O.d.edited_count = res.edited_count;
      }
      пометь(Object.keys(O.pend).length ? 'сохраняю…' : 'сохранено');
      setTimeout(() => { if (!Object.keys(O.pend).length) пометь(''); }, 1500);
    } catch (e) {
      O.pend = {};
      пометь('не сохранилось — перечитал');
      await загрузи(false);
    } finally {
      O.busy = false;
      if (Object.keys(O.pend).length) O.timer = setTimeout(шли, 80);
    }
  }

  // ── вход и выход ─────────────────────────────────────────────────────────
  async function opOrdOpen() {
    готовь();
    O = { d: null, q: '', fold: {}, open: null, pend: {}, timer: null, busy: false, poll: null };
    $('ozvOv').classList.add('show');
    $('ozvBox').innerHTML = '<div class="exp-empty">Загрузка…</div>';
    await загрузи(false);
    O.poll = setInterval(() => загрузи(true), ОБНОВ_МС);
  }

  function opOrdClose() {
    if (O) { clearTimeout(O.timer); clearInterval(O.poll); шли(); }
    const ov = $('ozvOv'); if (ov) ov.classList.remove('show');
    O = null;
  }

  function opOrdRow(key) {
    if (!O) return;
    O.open = O.open === key ? null : key;
    try { tg.HapticFeedback.selectionChanged(); } catch (e) {}
    рисуй();
  }

  function opOrdFind(v) {
    if (!O) return;
    O.q = v || '';
    O.open = null;
    рисуй();
    const q = $('ozvQ');
    if (q && O.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }

  window.opOrdOpen = opOrdOpen;
  window.opOrdClose = opOrdClose;
  window.opOrdRow = opOrdRow;
  window.opOrdFind = opOrdFind;
  window.opOrdReset = reset;
  window.opOrdSet = set;
  window.opOrdStep = step;
})();
