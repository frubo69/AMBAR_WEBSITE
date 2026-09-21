/* Заявка магазину у оператора: только правка.
 *
 * Владелец, 21 сен 2026: «могут ли операторы увидеть эту заявку и
 * отредактировать её, а старший утром откроет STAR и увидит уже
 * отредактированную актуальную заявку. Им нужен только этот функционал,
 * полностью под копирку редактирование, но без всего остального — без
 * контроля статуса, без загрузки ответа магазина».
 *
 * Поэтому здесь ровно одно: таблица «позиция × район» с числами, которые
 * правятся нажатием. Заявку считает сервер (та же ручка, что у старшего), а
 * правка ложится в ту же клетку базы — у старшего она появится сама, как
 * только он откроет экран.
 *
 * Живёт отдельным файлом: экран операторов сейчас переделывают, и мешать
 * чужой работе в index.html незачем. Классы с приставкой ozv- — в приложении
 * короткие имена давно заняты.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const цел = (v) => Math.max(0, Math.min(9999, Math.round(parseFloat(String(v).replace(',', '.')) || 0)));
  const чис = (n) => String(Math.round(n * 100) / 100).replace('.', ',');
  const МЕС = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
               'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  // «20 сентября» вместо «2026-09-20»: день читают, а не сверяют по формату.
  const день = (iso) => {
    const d = new Date(String(iso || '') + 'T12:00:00');
    return isNaN(d) ? String(iso || '') : `${d.getDate()} ${МЕС[d.getMonth()]}`;
  };

  const СОХР_МС = 400;          // пачка правок уходит через эту паузу
  const ОБНОВ_МС = 20000;       // чужие правки подтягиваем сами

  let O = null;                 // {d, q, pend:{}, timer, busy, poll, focus}

  // ── разметка и стиль появляются при первом открытии ──────────────────────
  function готовь() {
    if ($('ozvOv')) return;
    const st = document.createElement('style');
    st.textContent = `
.ozv-ov{position:fixed;inset:0;z-index:150;background:rgba(0,0,10,.72);backdrop-filter:blur(6px);
  display:none;align-items:center;justify-content:center;padding:18px}
.ozv-ov.show{display:flex}
.ozv{display:flex;flex-direction:column;width:min(1180px,96vw);height:min(860px,92vh);
  border-radius:20px;overflow:hidden;background:var(--bg3);border:1px solid var(--border3);
  box-shadow:0 24px 80px rgba(0,0,0,.7)}
.ozv-hd{display:flex;align-items:flex-start;gap:12px;padding:18px 18px 12px;
  border-bottom:1px solid var(--border2)}
.ozv-hd .t{font-family:'Space Grotesk',sans-serif;font-size:18px;font-weight:700}
.ozv-hd .s{margin-top:3px;font-size:12px;color:var(--sub)}
.ozv-x{margin-left:auto;width:32px;height:32px;flex:0 0 auto;border-radius:10px;border:1px solid var(--border2);
  background:none;color:var(--sub);font-size:17px;line-height:1;cursor:pointer}
.ozv-x:active{transform:scale(.96)}
.ozv-find{position:relative;margin:12px 18px 0}
.ozv-find input{width:100%;height:38px;padding:0 34px 0 12px;border-radius:11px;font-family:inherit;
  font-size:14px;color:var(--text);background:var(--card2);border:1px solid var(--border2)}
.ozv-find button{position:absolute;right:6px;top:6px;width:26px;height:26px;border:0;border-radius:8px;
  background:none;color:var(--muted);cursor:pointer}
.ozv-l{flex:1;min-height:0;overflow:auto;padding:10px 18px 18px}
.ozv-head,.ozv-r{display:grid;grid-template-columns:minmax(0,1fr) repeat(var(--n),64px) 58px;
  align-items:center;gap:8px}
.ozv-head{position:sticky;top:0;z-index:2;padding:8px 2px;background:var(--bg3);
  font-size:10.5px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted)}
.ozv-head span{text-align:center}
.ozv-head span:first-child{text-align:left}
.ozv-r{padding:7px 2px;border-top:1px solid var(--border2)}
.ozv-n{display:flex;align-items:center;gap:10px;min-width:0}
.ozv-img{flex:0 0 auto;width:34px;height:34px;border-radius:9px;object-fit:contain;
  background:var(--card2);border:1px solid var(--border2);padding:2px}
.ozv-nb{min-width:0;font-size:13.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ozv-nb i{display:block;font-style:normal;font-size:11px;font-weight:600;color:var(--muted)}
.ozv-c{position:relative;text-align:center}
.ozv-c input{width:100%;height:34px;text-align:center;border-radius:9px;font-family:'Space Grotesk',sans-serif;
  font-size:14px;font-weight:700;color:var(--text);background:var(--card2);border:1px solid var(--border2)}
.ozv-c input:focus{outline:none;border-color:rgba(201,169,110,.5)}
.ozv-c.z input{color:var(--muted);font-weight:600}
.ozv-c.e input{color:var(--gold);border-color:rgba(201,169,110,.45);background:rgba(201,169,110,.08)}
.ozv-back{display:block;width:100%;margin-top:2px;padding:0;border:0;background:none;cursor:pointer;
  font-family:inherit;font-size:10px;color:var(--muted)}
.ozv-back:hover{color:var(--gold2)}
.ozv-t{text-align:center;font-family:'Space Grotesk',sans-serif;font-size:14px;font-weight:700}
.ozv-t.e{color:var(--gold)}
.ozv-ft{padding:11px 18px;border-top:1px solid var(--border2);font-size:12px;color:var(--muted);
  display:flex;align-items:center;gap:10px}
.ozv-ft b{color:var(--sub)}
.ozv-none{padding:26px;text-align:center;color:var(--muted);font-size:13px}
/* Телефон: пять колонок в строку не помещаются — названия ужимались до
   «Abs…». Поэтому строка становится карточкой: сверху позиция и итог, снизу
   районы в ряд, у каждого свой код над полем (владелец, 21 сен 2026: «на
   телефоне приведи это в человеческий вид»). Шапку панели опускаем под
   кнопки телеграма — safe-top. */
@media (max-width:760px){
  .ozv-ov{padding:var(--safe-top) 0 0;align-items:stretch}
  .ozv{width:100vw;height:100%;border-radius:0;border:none}
  .ozv-hd{padding:12px 14px 10px}
  .ozv-find{margin:10px 14px 0}
  .ozv-l{padding:6px 14px 16px}
  .ozv-head{display:none}
  /* Сетка по числу районов: первая строка — название во всю ширину и итог
     справа, вторая — сами клетки. Во flex они ужимались в одну строку и
     цифры не помещались. */
  .ozv-r{display:grid;grid-template-columns:repeat(var(--n),1fr);gap:9px 7px;padding:11px 0 13px}
  .ozv-n{grid-column:1 / -2;grid-row:1;align-self:center}
  .ozv-t{grid-column:-2 / -1;grid-row:1;justify-self:end;align-self:center;font-size:15px}
  .ozv-t::after{content:' ед';font-family:'DM Sans',sans-serif;font-size:10px;
    font-weight:600;color:var(--muted)}
  .ozv-c{grid-row:2;min-width:0}
  .ozv-c::before{content:attr(data-code);display:block;margin-bottom:3px;font-size:9.5px;
    font-weight:700;letter-spacing:.7px;color:var(--muted)}
  .ozv-c input{height:38px;font-size:15px}
  .ozv-back{font-size:9.5px}
  .ozv-img{width:32px;height:32px}
  .ozv-nb{font-size:13px}
}`;
    document.head.appendChild(st);
    const ov = document.createElement('div');
    ov.className = 'ozv-ov';
    ov.id = 'ozvOv';
    ov.innerHTML = '<div class="ozv" id="ozvBox"></div>';
    ov.addEventListener('click', (e) => { if (e.target === ov) opOrdClose(); });
    document.body.appendChild(ov);
  }

  // ── загрузка и обновление ────────────────────────────────────────────────
  async function загрузи(тихо) {
    try {
      const d = await opApi.opFetch('/api/operator/stock/order');
      if (!O) return;
      // Молча подменяем данные, только если никто ничего не правит: иначе
      // цифра прыгнет под пальцем.
      if (тихо && (Object.keys(O.pend).length || document.activeElement
          && document.activeElement.classList.contains('ozv-in'))) return;
      const было = O.d ? JSON.stringify(O.d.rows.map(r => [r.id, r.need_total])) : '';
      O.d = d;
      if (!тихо || было !== JSON.stringify(d.rows.map(r => [r.id, r.need_total]))) рисуй();
    } catch (e) {
      if (O && !O.d) $('ozvBox').innerHTML =
        '<div class="ozv-none">Заявка не загрузилась. Закройте и откройте заново.</div>';
    }
  }

  function строки() {
    const q = (O.q || '').trim().toLowerCase();
    const все = (O.d.rows || []);
    return q ? все.filter(r => String(r.name || '').toLowerCase().includes(q)) : все;
  }

  function рисуй() {
    const d = O.d, r = строки(), dist = d.districts || [];
    const ед = (d.rows || []).reduce((a, x) => a + (+x.need_total || 0), 0);
    const пр = d.edited_count || 0;
    $('ozvBox').innerHTML = `
      <div class="ozv-hd">
        <div><div class="t">Заявка магазину</div>
          <div class="s">${esc(день(d.day))} · ${(d.rows || []).length} ${
            скл((d.rows || []).length, 'позиция', 'позиции', 'позиций')} · ${чис(ед)} ед${
            пр ? ` · правок ${пр}` : ''}</div></div>
        <button class="ozv-x" onclick="opOrdClose()" aria-label="Закрыть">✕</button>
      </div>
      <div class="ozv-find">
        <input id="ozvQ" type="search" placeholder="Найти позицию" value="${esc(O.q || '')}"
               oninput="opOrdFind(this.value)">
        ${O.q ? `<button onclick="opOrdFind('')" aria-label="Очистить">✕</button>` : ''}
      </div>
      <div class="ozv-l" style="--n:${dist.length}">
        <div class="ozv-head"><span>Позиция</span>${
          dist.map(x => `<span>${esc(x.code || x.id)}</span>`).join('')}<span>всего</span></div>
        ${r.length ? r.map(стр).join('') : '<div class="ozv-none">Ничего не нашлось</div>'}
      </div>
      <div class="ozv-ft">Правки сохраняются сразу — старший увидит их у себя.
        <b id="ozvSave"></b></div>`;
  }

  function скл(n, a, b, c) {
    n = Math.abs(Math.round(n)) % 100;
    if (n > 10 && n < 20) return c;
    n %= 10;
    return n === 1 ? a : (n > 1 && n < 5) ? b : c;
  }

  function стр(r) {
    const dist = O.d.districts || [];
    return `<div class="ozv-r" data-id="${esc(r.id)}">
      <span class="ozv-n">
        <img class="ozv-img" src="/products/${esc(r.id)}.webp" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'">
        <span class="ozv-nb">${esc(r.name)}<i>${esc(r.unit_name || '')}</i></span>
      </span>
      ${dist.map(x => {
        const c = (r.cells || {})[x.id] || {};
        const n = +c.need || 0;
        return `<span class="ozv-c${c.edited ? ' e' : ''}${n ? '' : ' z'}" data-c="${esc(r.id)}|${esc(x.id)}"
          data-code="${esc(x.code || x.id)}">
          <input class="ozv-in" inputmode="numeric" value="${чис(n)}"
                 onchange="opOrdSet('${esc(r.id)}','${esc(x.id)}',this.value)"
                 onfocus="this.select()">
          ${c.edited ? `<button class="ozv-back" onclick="opOrdSet('${esc(r.id)}','${esc(x.id)}','${+c.calc || 0}')"
            >расчёт ${чис(+c.calc || 0)}</button>` : ''}
        </span>`;
      }).join('')}
      <span class="ozv-t${r.edited ? ' e' : ''}" data-t="${esc(r.id)}">${чис(+r.need_total || 0)}</span>
    </div>`;
  }

  // ── правка: сначала у себя, потом на сервер пачкой ───────────────────────
  function опиши(r) {
    const el = document.querySelector(`.ozv-r[data-id="${CSS.escape(r.id)}"]`);
    if (!el) return;
    const t = el.querySelector(`[data-t]`);
    if (t) { t.textContent = чис(+r.need_total || 0); t.classList.toggle('e', !!r.edited); }
    (O.d.districts || []).forEach(x => {
      const c = (r.cells || {})[x.id] || {};
      const box = el.querySelector(`[data-c="${CSS.escape(r.id + '|' + x.id)}"]`);
      if (!box) return;
      box.classList.toggle('e', !!c.edited);
      box.classList.toggle('z', !(+c.need));
      const inp = box.querySelector('input');
      if (inp && document.activeElement !== inp) inp.value = чис(+c.need || 0);
      const b = box.querySelector('.ozv-back');
      if (c.edited && !b) {
        const btn = document.createElement('button');
        btn.className = 'ozv-back';
        btn.textContent = `расчёт ${чис(+c.calc || 0)}`;
        btn.onclick = () => opOrdSet(r.id, x.id, String(+c.calc || 0));
        box.appendChild(btn);
      } else if (!c.edited && b) b.remove();
    });
  }

  function set(id, district, qty) {
    if (!O || !O.d) return;
    const r = (O.d.rows || []).find(x => x.id === id); if (!r) return;
    const c = (r.cells || (r.cells = {}))[district] || (r.cells[district] = {});
    c.need = цел(qty);
    c.edited = c.need !== (+c.calc || 0);
    r.need_total = Object.values(r.cells).reduce((a, x) => a + (+x.need || 0), 0);
    r.edited = Object.values(r.cells).some(x => x.edited);
    опиши(r);
    O.pend[id + '|' + district] = c.need;
    пометь('сохраняю…');
    clearTimeout(O.timer);
    O.timer = setTimeout(шли, СОХР_МС);
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
        if (res && res.row) {
          const r = (O.d.rows || []).find(x => x.id === res.row.id);
          if (r && !Object.keys(O.pend).some(x => x.startsWith(res.row.id + '|'))) {
            Object.assign(r, res.row);
            опиши(r);
          }
          if (res.edited_count != null) O.d.edited_count = res.edited_count;
        }
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
    O = { d: null, q: '', pend: {}, timer: null, busy: false, poll: null };
    $('ozvOv').classList.add('show');
    $('ozvBox').innerHTML = '<div class="ozv-none">Загрузка…</div>';
    await загрузи(false);
    O.poll = setInterval(() => загрузи(true), ОБНОВ_МС);
  }

  function opOrdClose() {
    if (O) { clearTimeout(O.timer); clearInterval(O.poll); шли(); }
    const ov = $('ozvOv'); if (ov) ov.classList.remove('show');
    O = null;
  }

  function opOrdFind(v) {
    if (!O) return;
    O.q = v || '';
    рисуй();
    const q = $('ozvQ'); if (q && O.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }

  window.opOrdOpen = opOrdOpen;
  window.opOrdClose = opOrdClose;
  window.opOrdFind = opOrdFind;
  window.opOrdSet = set;
})();
