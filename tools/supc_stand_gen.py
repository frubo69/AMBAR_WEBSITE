"""Стенд экрана «Отмена заявки» из настоящего owner/index.html.
Запуск: python3 tools/supc_stand_gen.py <dir> → <dir>/supc.html (?sel=1 — отмечены два района)"""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)
def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S); assert m, name; return m.group(0)
def const(name, end=r";\n"):
    m = re.search(r"(?:const|let) " + re.escape(name) + r"\s*=\s*.*?" + end, src, re.S); assert m, name; return m.group(0)
parts = [const("IC_P", r"\n\};\n"), const("ICO_LIST"), const("ICO_BASE"), const("_SC_CHEV", r"`;\n"),
         fn("IC"), fn("escS"), fn("_setText"), fn("pluralize"), fn("askDialog"), fn("closePinPop"),
         fn("_supName"), fn("_supOpenAll"), fn("_supMain"), fn("supCancelOpen"), fn("accSupCancel"),
         fn("_supcActive"), fn("_supcSelN"), fn("supcRender"), fn("supcToggle"), fn("supcAll"), fn("supcGo")]
overlay = src[src.index('<div class="stk-ov" id="accOv">'):]
overlay = overlay[:overlay.index('<div class="stk-foot" id="accFoot"></div>') + len('<div class="stk-foot" id="accFoot"></div>')] + "\n</div>"
stubs = r"""
let ACC_BACK = null, ACC_VIEW = null, ACC_SUP = null, SUP_D = null;
const LOG = []; function log(m){ LOG.push(m); document.getElementById('err').textContent = LOG.join('\n'); }
window.onerror = (m, s, l, c, e) => log('ERROR ' + m + ' @' + l + ':' + c);
window.Telegram = {WebApp:{HapticFeedback:{selectionChanged(){},notificationOccurred(){},impactOccurred(){}}}};
function fmt(n){ return (+n||0).toLocaleString('ru-RU').replace(/,/g,' '); }
function _hm(iso){ return String(iso||'').slice(11,16); }
function _toast(m){ log('TOAST ' + m); }
function _meName(){ return 'Стенд'; }
function accOrder(){ log('accOrder'); } function accSupply(){ log('accSupply'); } function accExtra(){ log('accExtra'); }
function accOpen(id){ document.getElementById('accOv').classList.add('show'); ACC_VIEW = id; if(id === 'supcancel') accSupCancel(); }
const LIST = [{supply_id:'S1', kind:'main', status:'open', total_qty:415, at:'2026-09-11T09:55:00', districts:5},
              {supply_id:'X1', kind:'extra', base:'ABC', status:'open', total_qty:1, at:'2026-09-11T22:28:00', districts:1}];
const D = {jbr:{code:'B1',name:'Марина'}, dt:{code:'B2',name:'Даунтаун'}, bb:{code:'B3',name:'Бизнес-бей'}, alg:{code:'B4',name:'Алгусес'}, sil:{code:'B5',name:'Силикон'}};
const VIEWS = {
  S1: {supply_id:'S1', kind:'main', status:'open', total_qty:415, districts:D, tasks:[
    {district:'jbr', need:120, got:0, driver:'', done_at:'', noscan_at:'', cancelled_at:''},
    {district:'dt',  need:90,  got:35, driver:'Али', done_at:'', noscan_at:'', cancelled_at:''},
    {district:'bb',  need:80,  got:80, driver:'Умар', done_at:'2026-09-11T14:02:00', noscan_at:'', cancelled_at:''},
    {district:'alg', need:75,  got:75, driver:'Сунат', done_at:'', noscan_at:'2026-09-11T13:40:00', noscan_by:'Сунат', cancelled_at:''},
    {district:'sil', need:50,  got:0, driver:'', done_at:'', noscan_at:'', cancelled_at:'2026-09-11T12:10:00'}]},
  X1: {supply_id:'X1', kind:'extra', base:'ABC', status:'open', total_qty:1, districts:D, tasks:[
    {district:'alg', need:1, got:0, driver:'', done_at:'', noscan_at:'', cancelled_at:''}]}};
window.ownerApi = {ownerFetch: async (path, opts) => {
  if(path === '/api/owner/supply') return {supplies: LIST};
  const m = path.match(/\/api\/owner\/supply\/([^/]+)$/); if(m) return VIEWS[decodeURIComponent(m[1])];
  log('POST ' + path + ' ' + JSON.stringify(opts && opts.body)); return {ok:true};
}};
const Q = new URLSearchParams(location.search);
document.getElementById('phone').style.width = (Q.get('w') || 390) + 'px';
(async () => {
  supCancelOpen('');
  await new Promise(r => setTimeout(r, 80));
  if(Q.get('sel')){ supcToggle('S1','jbr'); supcToggle('S1','dt'); }
  if(Q.get('all')){ supcAll('S1'); }
  await new Promise(r => setTimeout(r, 50));
  let over = 0;
  document.querySelectorAll('.supc-b').forEach(el => { if(el.scrollWidth > el.clientWidth + 1) over++; });
  log('MEASURE w=' + document.getElementById('phone').scrollWidth + ' OVERFLOW ' + over + ' rows=' + document.querySelectorAll('.supc-r').length
      + ' foot=' + (document.querySelector('#accFoot .qb') || {}).textContent + ' sub=' + document.getElementById('accSub').textContent);
})();
"""
html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>стенд · отмена заявки</title>
<style>{styles[0]}</style><style>{styles[1]}</style>
<style>html,body{{height:auto;min-height:0;overflow:visible}} body{{padding:0;background:#0a0a14}}
#phone{{position:relative;margin:0;width:390px;background:var(--bg)}}
#phone .stk-ov{{position:relative;inset:auto;height:auto;min-height:0}} #phone .stk-ov.show{{animation:none}}
#phone .stk-mid{{overflow:visible;flex:none}}
#err{{white-space:pre-wrap;font:11px/1.3 monospace;color:#f88;padding:10px;max-width:390px}}</style></head>
<body><div id="phone">{overlay}</div><div id="err"></div>
<script>
{chr(10).join(parts)}
{stubs}
</script></body></html>"""
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "supc.html"), "w", encoding="utf-8").write(html)
open(os.path.join(OUT, "supc_check.js"), "w", encoding="utf-8").write(re.search(r"<script>(.*)</script>", html, re.S).group(1))
print("supc.html written")
