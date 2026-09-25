"""Стенд строки «Цены закупки» из настоящего owner/index.html.

Нужен, чтобы посмотреть строку глазами: с подписью под ценой и без неё.

    python3 tools/opr_stand_gen.py <dir> [ширина]   → <dir>/stand.html
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
W = sys.argv[2] if len(sys.argv) > 2 else "430"
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)


def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)


def const(name, end=r";\n"):
    m = re.search(r"(?:const|let) " + re.escape(name) + r"\s*=\s*.*?" + end, src, re.S)
    assert m, name
    return m.group(0)


СТРОКИ = [
    {"id": "p15", "no": 123, "name": "Ballantines Finest 1 ltr", "unit": 1,
     "cost": 42, "cost_src": "вручную", "price_list": 100},
    {"id": "t1", "no": 124, "name": "Marlboro Gold", "unit": 1,
     "cost": 19, "cost_src": "вручную", "price_list": 25},
    {"id": "t2", "no": 125, "name": "TEREA Sienna", "unit": 1,
     "cost": 20, "cost_src": "вручную", "price_list": 27},
    {"id": "p31", "no": 126, "name": "Budweiser 0.33 can", "unit": 24,
     "cost": 95, "cost_src": "поставка", "price_list": 200},
    {"id": "p1", "no": 1, "name": "Absolut 1 ltr", "unit": 1,
     "cost": 35, "cost_src": "прайс", "price_list": 100},
]

html = f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Цены закупки</title>
<link rel="stylesheet" href="{os.path.join(ROOT, 'vendor', 'fonts.css')}">
<style>{''.join(styles)}</style>
<style>body{{margin:0;background:var(--bg);width:{W}px}} .stand{{padding:12px}}</style>
</head><body><div class=stand><div id=box></div></div>
<script>
{const("_SC_CHEV", end=r"`;\n")}
{fn("escS")}
function conv(v){{ return +v || 0; }}        // на стенде валюту не переводим
{fn("fmt")}
{fn("_oprSrc")}
{fn("_oprMark")}
{fn("_oprMarkTxt")}
{fn("_oprMarkCell")}
{fn("oprRow")}
document.getElementById('box').innerHTML =
  {json.dumps(СТРОКИ, ensure_ascii=False)}.map(oprRow).join('');
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "stand.html")
open(path, "w", encoding="utf-8").write(html)
print(path)
