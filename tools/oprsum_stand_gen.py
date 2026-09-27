"""Стенд карточки «Прайс» из настоящего owner/index.html.

Владелец, 27 сен 2026: слева — сколько категорий, «в закупке» и «в продаже» —
СУММЫ цен по всему прайсу, и накрутка считается из них. Стенд показывает
карточку на живых числах: 126 категорий, 21 516 закупки, 41 050 продажи.

    python3 tools/oprsum_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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


def line(pat):
    m = re.search(pat, src)
    assert m, pat
    return m.group(0)


# Прайс как на сервере 27.09: 126 позиций, сумма закупки 21 516, продажи 41 050.
# Раскладываем эти суммы на 126 строк так, чтобы накрутка вышла та же.
СТРОКИ = []
for i in range(126):
    зак = round(21516 / 126, 2)
    прод = round(41050 / 126, 2)
    СТРОКИ.append({"id": f"p{i+1}", "name": f"Позиция {i+1}",
                   "cost": зак, "price_list": прод, "price_app": прод})

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · Прайс</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px 24px}}
</style></head><body>
<div class="stand" id="stand"></div>
<script>
{line(r"function conv\(aed\)\{.*")}
{line(r"function fmt\(aed\)\{.*")}
{fn("fmtQty")}
{fn("pluralize")}
{fn("_oprTotals")}
{fn("_oprMarkTxt")}
let curCur = 'AED', CUR_RATE = 3.6725;
const OPR = {{rows: {json.dumps(СТРОКИ, ensure_ascii=False)}}};
function oprAlert(){{ return ''; }}
{fn("oprSumCard")}
document.getElementById('stand').innerHTML = oprSumCard();
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
