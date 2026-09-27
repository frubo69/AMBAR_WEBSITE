"""Стенд панели «Чек» оператора из настоящего operator/index.html.

Панель собирают на телефоне шторкой, и смотреть на неё надо целиком: состав,
район, данные клиента, подвал с итогом. Стенд берёт стили и сам renderTicket
из приложения — иначе правишь одно, а видишь другое.

    python3 tools/tk_stand_gen.py <dir> [ширина]   → <dir>/stand.html
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
W = sys.argv[2] if len(sys.argv) > 2 else "430"
src = open(os.path.join(ROOT, "operator", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)

# Разметка панели — как она лежит в приложении, до подвала включительно.
m = re.search(r'<!-- ticket -->\s*(<div class="ticket">.*?</div>\n    </div>)', src, re.S)
assert m, "разметку чека не нашёл"
панель = m.group(1)


def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)


ЧЕК = {
    "p6":  {"id": "p6",  "name": "Beluga 0.7 ltr",      "price": 250, "qty": 1},
    "p5":  {"id": "p5",  "name": "Smirnoff Vodka 1 ltr", "price": 100, "qty": 1},
    "p4":  {"id": "p4",  "name": "Skyy Vodka 1 ltr",     "price": 150, "qty": 1},
}
РАЙОНЫ = [("jvc", "JVC"), ("tecom", "Тиком"), ("bbay", "Бизнес Бей"),
          ("silicon", "Силикон"), ("alguses", "Алгусес")]

prod = os.path.join(ROOT, "products")
html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · чек оператора</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px;height:auto}}
  /* На телефоне панель — шторка снизу (translateY), поднимается классом
     body.tk-open. На стенде поднимаем её сразу и распрямляем во всю высоту. */
  .ticket{{position:static!important;width:{W}px!important;border-left:0;
    height:auto!important;min-height:100vh;transform:none!important;
    border-radius:0!important}}
  .ticket-scroll{{overflow:visible!important}}
  .tk-scrim{{display:none!important}}
</style></head><body class="tk-open">
{панель}
<script>
// На стенде картинки лежат рядом, в приложении — по /products/. Подменяем
// путь, иначе строки выглядят без снимков и проверить их нельзя.
const TICKET = {json.dumps(ЧЕК, ensure_ascii=False)};
function $(id){{ return document.getElementById(id); }}
function _esc(s){{ return String(s??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function lineUnit(l){{ return l.price; }}
function lineName(l){{ return _esc(l.name); }}
function plural(n,a,b,c){{ const d=n%10,dd=n%100;
  return (d===1&&dd!==11)?a:((d>=2&&d<=4)&&(dd<10||dd>=20))?b:c; }}
function syncTkBar(){{}}
function addLine(){{}}
function clearTicket(){{}}
function ovClose(){{}}
function haptic(){{}}
let PAY='cash';
function payPick(p){{ PAY=p; document.querySelectorAll('#paySeg .pay-b').forEach(b=>
  b.classList.toggle('on', b.dataset.p===p)); }}
function cmtOpen(){{ const b=$('cmtAdd'),f=$('cmtFld'); b.style.display='none'; f.style.display=''; }}
function askCreate(){{}}
{fn("renderTicket")}
document.getElementById('distChips').innerHTML =
  {json.dumps(РАЙОНЫ, ensure_ascii=False)}.map(([id,nm],i) =>
    `<button class="dchip${{'' }}${{i===0?' on':''}}">${{nm}}</button>`).join('');
document.getElementById('drvBlock').style.display = 'none';
renderTicket();
// Снимки в приложении лежат по /products/, а стенд открывается файлом с
// диска — подменяем путь уже в готовой разметке.
document.querySelectorAll('.tk-img').forEach(i =>
  i.setAttribute('src', 'file://{prod}/' + i.getAttribute('src').split('/').pop()));
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
