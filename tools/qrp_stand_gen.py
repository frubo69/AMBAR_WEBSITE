"""Стенд списка позиций «Внести QR коды» из настоящего owner/index.html.

Пришли на район за долгом по пересчёту — в списке должны быть только позиции,
у которых код не внесён, а не весь каталог. Стенд показывает оба вида: как
экран выглядит с долгом и что остаётся, если нажать «весь каталог».

    python3 tools/qrp_stand_gen.py <dir> [ширина]  → <dir>/stand.html?view=need|all
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


# Каталог как в приложении: номер — место в обходе полок.
КАТАЛОГ = [
    {"id": "p1",  "no": 1,  "name": "Absolut 1 ltr",            "cat": "Водка"},
    {"id": "p10", "no": 2,  "name": "Red Label 1 ltr",          "cat": "Виски"},
    {"id": "p11", "no": 3,  "name": "Black Label 1 ltr",        "cat": "Виски"},
    {"id": "p12", "no": 4,  "name": "Jack Daniels 1 ltr",       "cat": "Виски"},
    {"id": "p13", "no": 5,  "name": "Chivas Regal 12Y 1 ltr",   "cat": "Виски"},
    {"id": "p31", "no": 6,  "name": "Heineken 0.33 can",        "cat": "Пиво", "unit": 24},
    {"id": "p33", "no": 7,  "name": "Budweiser 0.33 can",       "cat": "Пиво", "unit": 24},
    {"id": "p43", "no": 11, "name": "Corona Extra 0.355 bottle", "cat": "Пиво", "unit": 24},
    {"id": "p5",  "no": 46, "name": "Smirnoff Vodka 1 ltr",     "cat": "Водка"},
    {"id": "p2",  "no": 47, "name": "Stolichnaya 1 ltr",        "cat": "Водка"},
    {"id": "p59", "no": 41, "name": "Tanqueray 1 ltr",          "cat": "Джин"},
    {"id": "p78", "no": 39, "name": "Baileys 1 ltr",            "cat": "Ликёр"},
]
# Долг по пересчёту: код не внесён только у трёх позиций.
ДОЛГ = {"p1": 12, "p31": 4, "p59": 1}

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · внести QR коды</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px 24px}}
</style></head><body>
<div class="stand">
  <div class="pk-search">
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input id="qrQ" placeholder="Поиск по названию" oninput="qrRenderList()"
      autocomplete="off" spellcheck="false">
  </div>
  <div class="pk-cats" id="qrCats"></div>
  <div id="qrList"></div>
</div>
<script>
{line(r"const _CHEV = `[^`]*`;")}
{line(r"const _PK_TRI = `[^`]*`;")}
{line(r"function escS\(s\)\{.*")}
{fn("fmtQty")}
const Telegram = {{WebApp:{{HapticFeedback:{{selectionChanged(){{}}}}}}}};
const QR_CAT = {json.dumps(КАТАЛОГ, ensure_ascii=False)};
const QR_DIST = 'jvc', QR_FOUND = null;
let QR_CATF = 'all', QR_NEED = true, QR_ALL = false;
const ACC_QR = {{unscanned_by_product: {{jvc: {json.dumps(ДОЛГ)}}}, slugs: {{}}}};
function _lockOn(){{ return null; }}
function qrStart(){{}}
{fn("_qrIsBeer")}
{fn("_qrNeedTag")}
{line(r"function _qrNeedMap\(\)\{.*")}
{line(r"function _qrOnlyNeed\(\)\{.*")}
{fn("_qrBase")}
{fn("qrShowAll")}
{fn("qrRenderCats")}
{fn("qrCat")}
{fn("qrRenderList")}
if(new URLSearchParams(location.search).get('view') === 'all') QR_ALL = true;
qrRenderCats(); qrRenderList();
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
