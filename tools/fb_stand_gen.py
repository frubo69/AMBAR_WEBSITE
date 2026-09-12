"""Стенд книги учёта из настоящего owner/index.html: оба <style> целиком, блок
книги (от «Финансы: книга учёта денег» до «Прайс: закупка и продажа») и
нужные ему функции панели — вырезаются регуляркой; остальное — заглушки.
Запуск: python3 tools/fb_stand_gen.py <dir>  → <dir>/stand.html"""
import os, re, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()

styles = re.findall(r"<style>(.*?)</style>", src, re.S)
fb_js = src[src.index("// ── Финансы: книга учёта денег"):src.index("// Прайс: закупка и продажа")]

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
    const("MONTHS_RU"), const("MONTHS_GEN"), const("ACC_WDF"), const("IC_P", r"\n\};\n"),
    const("_SC_CHEV", r"`;\n"),
    fn("IC"), fn("escS"), fn("_setText"), fn("_bizToday"), fn("_dayIsoFor"), fn("accDay"),
    fn("accIsBack"), fn("accDayWord"), fn("dayBarOff"), fn("dayBarLabels"), fn("dayBarPaint"),
    fn("_dayNavFor"), fn("_dayNav"), fn("bindDayBars"), fn("daySwipe"), fn("dayBarStep"),
    fn("dayBarArrow"), fn("dayBarHome"), fn("_heroFlip"), fn("askDialog"), fn("closePinPop"),
    fn("pluralize"),
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
          fn("renderDp"), fn("pickDp"), fn("updateDpLbl"), line("function dpReset()")]
overlay = src[src.index('<div class="stk-ov" id="accOv">'):]
overlay = overlay[:overlay.index('<div class="stk-foot" id="accFoot"></div>') + len('<div class="stk-foot" id="accFoot"></div>')] + "\n</div>"
# страница «Анализ» — поверх главной, наезжает справа
_a0 = src.index('<div class="pg-ov" id="finAnOv">')
an_html = src[_a0:src.index('<!-- /finAnOv -->', _a0) + len('<!-- /finAnOv -->')]

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
  if(document.getElementById('accOv').classList.contains('show') && document.getElementById('finDayCard')){ fbDayReload(); }
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
    if(Q.get('cal')){ openDate('fbNext'); await new Promise(r => setTimeout(r, 300)); }
    if(Q.get('an')){ await fbAnOpen(); await new Promise(r => setTimeout(r, 400)); }
    if(Q.get('pick')){ const b = [...document.querySelectorAll('#fbLine-rp .fb-e')].find(e => e.innerText.trim() === Q.get('pick'));
                       if(b) b.click(); await new Promise(r => setTimeout(r, 300)); }
    if(Q.get('pay')){ const r0 = [...document.querySelectorAll('#fbPayBody .fb-pay')].find(e => e.innerText.includes(Q.get('pay')));
                      const b = r0 && r0.querySelector('.fb-paybtn'); if(b) b.click(); await new Promise(r => setTimeout(r, 200)); }
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
<style>{styles[0]}</style>
<style>{styles[1]}</style>
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
