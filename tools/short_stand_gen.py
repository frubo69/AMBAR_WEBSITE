"""Стенд списка «Осталось докупить» доп. заявки. python3 tools/short_stand_gen.py <dir> → short.html"""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)
def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S); assert m, name; return m.group(0)
def const(name, end=r";\n"):
    m = re.search(r"(?:const|let) " + re.escape(name) + r"\s*=\s*.*?" + end, src, re.S); assert m, name; return m.group(0)
parts = [const("_SC_CHEV", r"`;\n"), const("ICO_XPLUS"), const("ICO_SHARE"), const("ICO_TG"),
         fn("escS"), fn("_setText"), fn("pluralize"), fn("shortBody"), fn("_supSum"), fn("_supTotal")]
overlay = src[src.index('<div class="stk-ov" id="accOv">'):]
overlay = overlay[:overlay.index('<div class="stk-foot" id="accFoot"></div>') + len('<div class="stk-foot" id="accFoot"></div>')] + "\n</div>"
stubs = r"""
let SUP_BUYS = {}, SUP_ID = '';
const LOG = []; function log(m){ LOG.push(m); document.getElementById('err').textContent = LOG.join('\n'); }
window.onerror = (m, s, l, c) => log('ERROR ' + m + ' @' + l + ':' + c);
function fmt(n){ return (+n||0).toLocaleString('ru-RU').replace(/,/g,' '); }
function xOpen(id){ log('xOpen ' + id); } function xNew(f){ log('xNew ' + f); }
const D = {
  supply_id:'X1', kind:'extra', base:'ABC', day:'2026-09-11', buys:{},
  tasks:[{district:'jbr', district_code:'B1'},{district:'dt', district_code:'B2'},{district:'alg', district_code:'B4'}],
  shortfall:{qty:19, qty_left:9, rows:[
    {id:'gin', name:'Gordon\'s London Dry 0.7', why:'gap', gap:14, by_district:{jbr:4, dt:10}, covered:10, covered_by:{dt:10}, left_by:{jbr:4}, left:4, bases:['DEF']},
    {id:'rum', name:'Bacardi Carta Blanca 0.7', why:'cancelled', gap:5, by_district:{alg:5}, covered:0, covered_by:{}, left_by:{alg:5}, left:5, bases:[]}],
    children:[{supply_id:'X2', base:'DEF', status:'open', qty:10, at:'t'}]}};
const Q = new URLSearchParams(location.search);
document.getElementById('phone').style.width = (Q.get('w') || 390) + 'px';
document.getElementById('accOv').classList.add('show');
_setText('accTitle', 'Осталось докупить');
if(Q.get('all')){ D.shortfall.qty_left = 0; D.shortfall.rows.forEach(r => { r.left = 0; r.left_by = {}; }); }
shortBody(D);
log('MEASURE w=' + document.getElementById('phone').scrollWidth + ' sub=' + document.getElementById('accSub').textContent);
"""
html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>стенд · недобор</title>
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
open(os.path.join(OUT, "short.html"), "w", encoding="utf-8").write(html)
open(os.path.join(OUT, "short_check.js"), "w", encoding="utf-8").write(re.search(r"<script>(.*)</script>", html, re.S).group(1))
print("short.html written")
