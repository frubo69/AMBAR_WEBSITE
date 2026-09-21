"""Стенд книги учёта из настоящего owner/index.html: все <style> целиком (фишки .chip живут не в первых двух), блок
книги (от «Финансы: книга учёта денег» до начала кошелька) и
нужные ему функции панели — вырезаются регуляркой; остальное — заглушки.
Запуск: python3 tools/fb_stand_gen.py <dir>  → <dir>/stand.html"""
import os, re, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()

styles = re.findall(r"<style>(.*?)</style>", src, re.S)
# конец блока — начало кошелька: карточка «Прайс» из «Финансов» убрана 19 сен 2026 (1586906),
# и метка «Прайс: закупка и продажа», по которой резали раньше, со страницы ушла
fb_js = src[src.index("// ── Финансы: книга учёта денег"):src.index("(function initWalBlock(){")]

def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)

def const(name, end=r";\n"):
    m = re.search(r"(?:const|let) " + re.escape(name) + r" = .*?" + end, src, re.S)
    assert m, name
    return m.group(0)

parts = [
    "let DAY_OFFSET = 0; let _flipTimer; let ACC_BACK = null; let ACC_VIEW = null; const SHIFT_START_HOUR = 12;",
    # кошелёк в стенде никуда не ходит: выдуманный остаток, чтобы строка «Крипта» рисовалась
    "let ACC_WAL = {balance: {usdt: 1000}};",
    const("MONTHS_RU"), const("MONTHS_GEN"), const("ACC_WDF"), const("IC_P", r"\n\};\n"),
    const("_SC_CHEV", r"`;\n"),
    fn("IC"), fn("escS"), fn("_setText"), fn("_woFull"), fn("_bizToday"), fn("_dayIsoFor"), fn("accDay"),
    fn("accIsBack"), fn("accDayWord"), fn("dayBarOff"), fn("dayBarLabels"), fn("dayBarPaint"),
    fn("_dayNavFor"), fn("_dayNav"), fn("bindDayBars"), fn("daySwipe"), fn("dayBarStep"),
    fn("dayBarArrow"), fn("dayBarHome"), fn("_heroFlip"), fn("askDialog"), fn("closePinPop"),
    fn("pluralize"), fn("fmtUsdt"), "let CREW = null;", fn("crewLoad"), fn("crewDrvCount"),
    # время записи в отчёте ДДС — настоящий _hm (он многострочный, режем по краям)
    src[src.index("const _hm = iso => {"):src.index("timeZone:'Asia/Dubai'}) : ''; };\n")
        + len("timeZone:'Asia/Dubai'}) : ''; };\n")],
]
# календарь одной даты (платёж статьи): разметка и функции — настоящие
_d0 = src.index('<div class="cmd-overlay" id="dateOverlay">')
_d1 = src.index('id="dpApplyBtn"', _d0)
_d1 = src.index("\n", _d1); _d1 = src.index("\n", _d1 + 1); _d1 = src.index("\n", _d1 + 1); _d1 = src.index("\n", _d1 + 1)
date_html = src[_d0:_d1]
def line(prefix):
    """Одна строка исходника, начинающаяся с prefix (объявление или функция в одну строку)."""
    m = re.search(r"^" + re.escape(prefix) + r".*$", src, re.M)
    assert m, prefix
    return m.group(0) + "\n"
parts += [line("let dpTarget ="), line("let dpView ="), line("let dpFrom ="), line("let dpTo ="), line("const MONTHS_RU_SHORT ="),
          line("let dpSingle ="), line("const _ymd ="), "const dateOv = document.getElementById('dateOverlay');",
          fn("openDate"), line("function closeDate()"), fn("dpStep"), line("function sameDay("), line("function strDate("),
          fn("renderDp"), fn("pickDp"), fn("updateDpLbl"), fn("dpApply"), line("function dpReset()"),
          "function movePill(){}"]
overlay = src[src.index('<div class="stk-ov" id="accOv">'):]
overlay = overlay[:overlay.index('<div class="stk-foot" id="accFoot"></div>') + len('<div class="stk-foot" id="accFoot"></div>')] + "\n</div>"
# страница «Анализ» — поверх главной, наезжает справа
_a0 = src.index('<div class="pg-ov" id="finAnOv">')
an_html = src[_a0:src.index('<!-- /fbRcpOv -->', _a0) + len('<!-- /fbRcpOv -->')]   # + окно чека

stubs = r"""
// ── заглушки панели ──
const LOG = [];
function log(m){ LOG.push(m); const e = document.getElementById('err'); if(e) e.textContent = LOG.join('\n'); }
window.onerror = (m, s, l, c, e) => log('ERROR ' + m + ' @' + l + ':' + c + ' ' + (e && e.stack || ''));
window.addEventListener('unhandledrejection', e => log('REJECT ' + (e.reason && (e.reason.stack || e.reason))));
window.Telegram = {WebApp: {HapticFeedback: {selectionChanged(){}, impactOccurred(){}, notificationOccurred(){}},
  initDataUnsafe: {user: {first_name: 'Стенд'}}, showConfirm(t, cb){ cb(true); }}};
function _toast(m){ log('TOAST ' + m); }
async function askYes(t){ log('ASK ' + t.split('\n')[0]); return true; }
function accAskBack(){ return true; }
function _meName(){ return 'Стенд'; }
function _navSave(){}
function accLoadSummaries(){}
window.ownerApi = {ownerFetch: async (path, opts = {}) => {
  const {method = 'GET', params, body} = opts;
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const r = await fetch(path + qs, {method, headers: body ? {'Content-Type': 'application/json'} : {},
                                     body: body ? JSON.stringify(body) : undefined});
  if(!r.ok) throw new Error('api ' + r.status);
  return r.json();
},
ownerBlob: async (path) => {                    // чек: как в аппе — байты и blob-адрес
  const r = await fetch(path);
  if(!r.ok) throw new Error('api ' + r.status);
  return URL.createObjectURL(await r.blob());
}};
function accOpen(id){
  document.getElementById('accOv').classList.add('show');
  ACC_VIEW = id;
  document.getElementById('accMid').innerHTML = '<div class="exp-empty">Загрузка…</div>';
  document.getElementById('accFoot').innerHTML = '';
  document.getElementById('accAct').innerHTML = '';
  if(id === 'finday') return accFinDay();
  if(id === 'finb') return accFinB();
  if(id === 'finrp') return accFinRP();
  if(id === 'finnp') return accFinNP();
  if(id === 'finsafe') return accFinSafe();
  if(id === 'finddc') return accFinDdc();
  if(id === 'finbud') return accFinBud();
  if(id === 'finpay') return accFinPay();
  if(id === 'finperson') return accFinPerson();
  if(id === 'finitems') return accFinItems();
  if(id === 'cashround'){ _setText('accTitle', 'Сбор выручки'); document.getElementById('accMid').innerHTML = '<div class="exp-empty">(экран сбора выручки)</div>'; }
}
function accBack(){
  if(ACC_BACK){ const f = ACC_BACK; ACC_BACK = null; f(); return; }
  document.getElementById('accOv').classList.remove('show'); ACC_VIEW = null; fbHub();
}
function accDayGo(off){
  off = Math.max(0, Math.min(365, off | 0));
  if(off === DAY_OFFSET) return;
  DAY_OFFSET = off;
  const on = document.getElementById('accOv').classList.contains('show');
  if(on && document.getElementById('finDdcCard')){ fbDdcReload(); return; }
  if(on && document.getElementById('finDayCard')){ fbDayReload(); }
}
// ── сценарий по параметрам ──
const Q = new URLSearchParams(location.search);
document.getElementById('phone').style.width = (Q.get('w') || 390) + 'px';
(async () => {
  try{
    if(Q.get('off')) DAY_OFFSET = +Q.get('off');
    if(Q.get('month')) FB.month = Q.get('month');
    if(Q.get('person')) FB.person = Q.get('person');
    if(Q.get('norm')) FB.normOpen = true;
    if(Q.get('open')){ for(const g of Q.get('open').split(',')){ if(g === 'auto') FB.autoOpen = true; if(g === 'home') FB.homeOpen = true; if(g === 'sim') FB.simOpen = true; } }
    if(Q.get('bud')) FB.bud = Q.get('bud');
    if(Q.get('edit')){ const [k, ...rest] = Q.get('edit').split(':'); FB.edit = {kind: k, id: rest.join(':')}; }
    await fbHub();
    const v = Q.get('view') || 'hub';
    if(v !== 'hub') await accOpen(v);
    if(Q.get('add')){ fbAddToggle(Q.get('add')); }
    if(Q.get('kind')) fbKindPick(Q.get('kind'));
    if(Q.get('norm')){ FB.normOpen = true; }
    if(Q.get('page')){ const [k, ...r] = Q.get('page').split(':'); fbPageOpen(k, r.join(':')); await new Promise(r => setTimeout(r, 400)); }
    if(Q.get('page') && Q.get('edit')){ const [k, ...r] = Q.get('edit').split(':'); fbFillEdit(k, r.join(':')); await new Promise(r => setTimeout(r, 200)); }
    if(Q.get('wheel')){ fbPillOpen(); await new Promise(r => setTimeout(r, 400)); }
    if(Q.get('cal')){ openDate(Q.get('cal') === 'span' ? 'fbSpan' : 'fbNext'); await new Promise(r => setTimeout(r, 300)); }
    // ?work=from|to — календарь «Вышел на работу» / «Уехал» у человека
    if(Q.get('work')){ fbWorkPick(Q.get('work')); await new Promise(r => setTimeout(r, 300)); }
    if(Q.get('days')){ const [a1, b1] = Q.get('days').split('-').map(Number);   // отметить даты в календаре
      const cell = n => [...document.querySelectorAll('#dpGrid .dp-cell:not(.off)')].find(e => e.textContent === String(n));
      if(Q.get('fwd')) { [...document.querySelectorAll('.dp-month-hdr .dp-nav')].pop().click(); await new Promise(r => setTimeout(r, 150)); }
      if(cell(a1)) cell(a1).click(); if(b1 && cell(b1)) cell(b1).click(); await new Promise(r => setTimeout(r, 150)); }
    if(Q.get('an')){ await fbAnOpen(); await new Promise(r => setTimeout(r, 400)); }
    if(Q.get('due')){          // срок платежа у первого билдинга — через N дней от сегодня
      const l0 = ((fbBook(FB.month).budget || {}).lines || []).find(l => l.group === 'rent' && !l.office);
      if(l0){ const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + (+Q.get('due')));
              l0.next_due = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); } }
    if(Q.get('pick')){ const b = [...document.querySelectorAll('#fbLine-rp .fb-e')].find(e => e.innerText.trim() === Q.get('pick'));
                       if(b) b.click(); await new Promise(r => setTimeout(r, 300)); }
    if(Q.get('kid')){          // строка внутри раскрытой группы выбора статьи
      const k = [...document.querySelectorAll('#fbLine-rp .fb-kid')].find(e => e.innerText.trim() === Q.get('kid'));
      if(k) k.click(); await new Promise(r => setTimeout(r, 400)); }
    if(Q.get('hist')){         // страница истории: hist=1 — все, hist=<фильтр>
      fbHistOpen(Q.get('hist') === '1' ? '' : Q.get('hist')); await new Promise(r => setTimeout(r, 400)); }
    // раскрыть фильтр: состоянием и перерисовкой, иначе в снимке он останется
    // закрытым (анимация без кадров не идёт)
    // ?fit=1 — телефон настоящей высоты (844), раздел на весь экран: так
    // проверяются экраны, которые «должны занимать страницу ровно»
    if(Q.get('fit')){
      const st = document.createElement('style');
      st.textContent = '#phone{height:' + (Q.get('fit') === '1' ? 844 : +Q.get('fit')) + 'px!important;overflow:hidden}'
        + '#phone .page{display:none}#phone .stk-ov{position:absolute!important;inset:0!important;height:100%!important;display:flex}'
        + '#phone .stk-mid{flex:1 1 auto!important;overflow-y:auto!important}';
      document.head.appendChild(st);
      await new Promise(r => setTimeout(r, 150));
    }
    if(Q.get('rppage')){ fbRpInPage(); await new Promise(r => setTimeout(r, 400));
      if(Q.get('addin')) { fbAddToggle('in'); await new Promise(r => setTimeout(r, 200)); } }
    if(Q.get('who')){          // страница «кому» у статьи по названию (who=Билеты)
      const l0 = ((fbBook(FB.month).budget || {}).lines || []).find(l => l.name === Q.get('who'));
      if(l0){ await fbWhoPage(l0.id); await new Promise(r => setTimeout(r, 500));
              if(Q.get('whoname')){ fbLinePay(l0.id, Q.get('whoname')); await new Promise(r => setTimeout(r, 400)); } } }
    if(Q.get('rpin')){ FB.rpIn = true; accFinRP(); await new Promise(r => setTimeout(r, 500)); }   // раскрыть РП+
    if(Q.get('sal')){ fbSalPay(); await new Promise(r => setTimeout(r, 400));                   // страница «Зарплаты» из РП−
      if(Q.get('salback')){ accBack(); await new Promise(r => setTimeout(r, 500)); } }
    if(Q.get('histf')){ FB.hist.open = true; fbHistPaint(); await new Promise(r => setTimeout(r, 200)); }
    if(Q.get('rcp')){          // открыть окно чека у первой записи со снимком
      const b2 = document.querySelector('.fb-rcpb'); if(b2) b2.click(); await new Promise(r => setTimeout(r, 900)); }
    if(Q.get('pay')){ const r0 = [...document.querySelectorAll('#fbPayBody .fb-pay')].find(e => e.innerText.includes(Q.get('pay')));
                      const b = r0 && r0.querySelector('.fb-paybtn'); if(b) b.click(); await new Promise(r => setTimeout(r, 250));
                      // ?ask=1 — остановиться на окне «вы уверены?», иначе подтвердить и дождаться записи
                      if(!Q.get('ask') && document.getElementById('askOk')){ document.getElementById('askOk').click(); await new Promise(r => setTimeout(r, 1200)); } }
    // «Штрафы/авансы/долги»: itemwho=<имя> (пусто — «Другой человек») открывает страницу человека,
    // itemamt=сумма [itemkind=fine|advance|loan, itemname=имя, itemcmt=за что] — заполнить и нажать «Записать»
    if(Q.get('itemwho') !== null){
      const pill = [...document.querySelectorAll('#accMid .crw-p, #accMid .crw-op, #accMid .fb-star, #accMid .fb-bl')]
        .find(e => Q.get('itemwho') ? e.dataset.n === Q.get('itemwho') : e.classList.contains('fb-bl'));
      if(pill) pill.click(); else log('SCENARIO no pill ' + Q.get('itemwho'));
      await new Promise(r => setTimeout(r, 400));
      if(Q.get('itemkind')) fbKindPick(Q.get('itemkind'));
      if(Q.get('itemname')) document.getElementById('fbName-item').value = Q.get('itemname');
      if(Q.get('itemamt')){
        document.getElementById('fbAmt-item').value = Q.get('itemamt');
        if(Q.get('itemper')) document.getElementById('fbPer-item').value = Q.get('itemper');
        if(Q.get('itemcmt')) document.getElementById('fbCmt-item').value = Q.get('itemcmt');
        document.querySelector('#fbItemBody .aud-b.go').click(); await new Promise(r => setTimeout(r, 300));
        if(document.getElementById('askOk')){ document.getElementById('askOk').click(); }
        await new Promise(r => setTimeout(r, 1500));
      }
      if(Q.get('itemdel')){      // крестик у первой записи на странице человека + «Убрать»
        const x = document.querySelector('#fbItemBody .fb-e-x'); if(x) x.click(); else log('SCENARIO no x');
        await new Promise(r => setTimeout(r, 300));
        if(document.getElementById('askOk')) document.getElementById('askOk').click();
        await new Promise(r => setTimeout(r, 1500));
      }
    }
    // штрафной лист / свободное удержание: pen=1 | hold=1, дальше по шагам нажатиями:
    // pencat=auto, penvio=speed, pentier=0, penq=поиск, penwho=имя, penhow=parts, penmonths=3, penamt, pencmt, penapply=1, penback=N
    const W = ms => new Promise(r => setTimeout(r, ms));
    const tap = (sel, pred) => { const e = [...document.querySelectorAll(sel)].find(pred || (() => true));
                                 if(e) e.click(); else log('SCENARIO no ' + sel); return e; };
    if(Q.get('pen') || Q.get('hold')){
      tap('#accMid .fb-pen2-b', e => e.innerText.includes(Q.get('pen') ? 'Штрафной' : 'Свободное')); await W(300);
      if(Q.get('pencat')){ tap('#fbPenBody .fb-penc', e => (e.getAttribute('onclick') || '').includes("'" + Q.get('pencat') + "'")); await W(300); }
      if(Q.get('penvio')){ tap('#fbPenBody .fb-pen-row', e => (e.getAttribute('onclick') || '').includes("'" + Q.get('penvio') + "'")); await W(300); }
      if(Q.get('pentier') !== null){ tap('#fbPenBody .fb-pen-tier', e => (e.getAttribute('onclick') || '').includes('(' + Q.get('pentier') + ')')); await W(300); }
      if(Q.get('penq')){ const q = document.getElementById('fbPenQ'); q.value = Q.get('penq'); fbPenFilter(q.value); await W(100); }
      if(Q.get('penwho')){ tap('#fbPenBody .fb-pen-r', e => e.dataset.n === Q.get('penwho')); await W(500); }
      if(Q.get('penhow')){ fbPenHow(Q.get('penhow')); await W(100); }
      if(Q.get('penmonths')){ fbPenMonths(+Q.get('penmonths')); await W(100); }
      if(Q.get('penamt')){ const a = document.getElementById('fbPenAmt'); a.value = Q.get('penamt'); a.dispatchEvent(new Event('input')); await W(100); }
      if(Q.get('pencmt')){ document.getElementById('fbPenCmt').value = Q.get('pencmt'); }
      if(Q.get('penapply')){ tap('#fbPenBody .fb-pen-go'); await W(1500); }
      for(let i = 0; i < +(Q.get('penback') || 0); i++){ accBack(); await W(350); }
    }
    // пересмотр из истории: penedit=N (N-я неотменённая строка), дальше penamt/penhow/penmonths/pencmt и pensave=1 или pencancel=1
    if(Q.get('penedit') !== null){
      const r = [...document.querySelectorAll('#accMid .fb-ph-r')][+Q.get('penedit')];
      if(r) r.click(); else log('SCENARIO no hist row'); await W(400);
      if(Q.get('penamt')){ const a = document.getElementById('fbPenAmt'); a.value = Q.get('penamt'); a.dispatchEvent(new Event('input')); }
      if(Q.get('penhow')){ fbPenHow(Q.get('penhow')); await W(100); }
      if(Q.get('penmonths')){ fbPenMonths(+Q.get('penmonths')); await W(100); }
      if(Q.get('pencmt') !== null){ const c = document.getElementById('fbPenCmt'); c.value = Q.get('pencmt'); c.dispatchEvent(new Event('input')); }
      if(Q.get('pensave')){ tap('#accFoot .fb-pen-go'); await W(1500); }            // кнопка в подвале: «Сохранить изменения» или «Вернуть»
      if(Q.get('pencancel')){ tap('#accFoot .fb-pen-go.dn'); await W(300); if(document.getElementById('askOk')) document.getElementById('askOk').click(); await W(1500); }
    }
    await new Promise(r => setTimeout(r, 60));
    const M = [];
    document.querySelectorAll('.fb-r, .aud-t, .shl-card, .sc-cap').forEach(el => {
      const r = el.getBoundingClientRect();
      const b = el.querySelector('b'); const u = el.querySelector('u, .aud-tu');
      M.push([el.className.replace(/\s+/g, '.').slice(0, 24), Math.round(r.width), Math.round(r.height),
              b ? Math.round(b.getBoundingClientRect().right) : '', u ? Math.round(u.getBoundingClientRect().right) : ''].join(':'));
    });
    log('MEASURE phone=' + document.getElementById('phone').getBoundingClientRect().width
        + ' scrollW=' + document.getElementById('phone').scrollWidth + ' | ' + M.join(' '));
    // наезд текста: подпись строки не должна залезать под число
    let over = 0;
    document.querySelectorAll('.fb-r').forEach(el => {
      const s = el.querySelector(':scope > span'), b = el.querySelector(':scope > b');
      if(s && b && s.getBoundingClientRect().right > b.getBoundingClientRect().left + 1) over++;
      if(s && s.scrollWidth > s.clientWidth + 1) over++;
    });
    log('OVERLAP ' + over);
    window.STAND_READY = true;
  }catch(e){ log('SCENARIO ' + (e && e.stack || e)); }
})();
"""

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>стенд · книга учёта</title>
{''.join('<style>' + st + '</style>' for st in styles)}
<style>
/* стенд: экран телефона шириной из ?w=, оверлей раскрыт на всю высоту */
html,body{{height:auto;min-height:0;overflow:visible}}
body{{padding:0;background:#0a0a14}}
#phone{{position:relative;margin:0;min-height:200px;width:390px;background:var(--bg)}}
#phone .page{{display:block;padding:14px}}
#phone .stk-ov{{position:relative;inset:auto;height:auto;min-height:0}}
#phone .stk-ov.show{{animation:none}}
#phone .stk-mid{{overflow:visible;flex:none}}
#phone .pg-ov{{position:relative;inset:auto;box-shadow:none;height:auto}}
#err{{white-space:pre-wrap;font:11px/1.3 monospace;color:#f88;padding:10px;max-width:390px;margin:0 auto}}
</style></head><body>
<div id="phone">
<div class="page active" id="pg-finance"><div id="finBook"></div></div>
{overlay}
{an_html}
{date_html}
</div>
<div id="err"></div>
<script>
{chr(10).join(parts)}
{fb_js}
{stubs}
</script></body></html>"""
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "stand.html"), "w", encoding="utf-8").write(html)
m = re.search(r"<script>(.*)</script>", html, re.S)
open(os.path.join(OUT, "stand_check.js"), "w", encoding="utf-8").write(m.group(1))
print("stand.html:", len(html), "bytes")
