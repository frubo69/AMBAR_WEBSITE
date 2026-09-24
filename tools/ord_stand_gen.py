"""Стенд заявки по районам из настоящего owner/index.html: карточки районов,
включая выключенный из заявки («не везём сюда»).

    python3 tools/ord_stand_gen.py <dir>   → <dir>/stand.html?view=on|off
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)


def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)


def const(name, end=r";\n"):
    m = re.search(r"(?:const|let)\s+" + re.escape(name) + r"\s*=\s*.*?" + end, src, re.S)
    assert m, name
    return m.group(0)


ФУНКЦИИ = ["escS", "pluralize", "fmt", "fmtQty", "conv", "foldEnd", "ordDistricts", "ordRow"]

РАЙОНЫ = [
    {"id": "jvc", "code": "B1", "name": "JVC", "off": True},
    {"id": "bbay", "code": "B2", "name": "Бизнес Бей", "off": False},
    {"id": "silicon", "code": "B3", "name": "Силикон", "off": True},
    {"id": "alguses", "code": "B4", "name": "Алгусес", "off": True},
    {"id": "tecom", "code": "B5", "name": "Тиком", "off": True},
]
def клетки(need, calc, off):
    return {"need": need, "calc": calc, "off": off, "have": 2, "norm": 12,
            "moving": 0, "leaving": 0, "came": 0, "gone": 0, "edited": False}
СТРОКИ = [
    {"id": "p1", "name": "Absolut 1 ltr", "cost": 34.65, "price": 180, "unit": 1,
     "need_total": 10, "cells": {}},
    {"id": "p7", "name": "Grey Goose 1 ltr", "cost": 96.6, "price": 320, "unit": 1,
     "need_total": 6, "cells": {}},
    {"id": "p31", "name": "Heineken 0.33 can", "cost": 4.2, "price": 12, "unit": 24,
     "need_total": 4, "cells": {}},
]
for r in СТРОКИ:
    for d in РАЙОНЫ:
        n = {"p1": 10, "p7": 6, "p31": 4}[r["id"]]
        r["cells"][d["id"]] = клетки(0 if d["off"] else n, n, d["off"])

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · заявка по районам</title>
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg,#0a0b10);color:#fff;font-family:Inter,system-ui,sans-serif}}
  #phone{{width:390px;margin:0 auto;padding:18px 14px 40px;box-sizing:border-box}}
</style></head><body>
<div id="phone"><div id="box"></div></div>
<script>
{const('ICO_CHEV', chr(10))}
const curCur = 'AED', CUR_RATE = 1;
let ORD = {{fold: {{}}, open: null, q: '', d: null}};
function _toast(){{}}
const Telegram = {{WebApp:{{HapticFeedback:{{selectionChanged(){{}},notificationOccurred(){{}},impactOccurred(){{}}}}}}}};
function ordDistOff(){{}}
function ordOpen(){{}}
function ordFold(){{}}
function foldSeen(){{}}
{''.join(fn(n) for n in ФУНКЦИИ)}
const РАЙОНЫ = {json.dumps(РАЙОНЫ, ensure_ascii=False)};
const СТРОКИ = {json.dumps(СТРОКИ, ensure_ascii=False)};
const view = new URLSearchParams(location.search).get('view') || 'off';
const dist = view === 'on' ? РАЙОНЫ.map(d => Object.assign({{}}, d, {{off: false}})) : РАЙОНЫ;
const rows = view === 'on' ? СТРОКИ.map(r => Object.assign({{}}, r, {{cells: Object.fromEntries(
    Object.entries(r.cells).map(([k, c]) => [k, Object.assign({{}}, c, {{need: c.calc, off: false}})]))}}))
  : СТРОКИ;
ORD.d = {{districts: dist, rows, all_rows: rows, day: '2026-09-24'}};
document.getElementById('box').innerHTML = ordDistricts(rows, dist, '');
</script></body></html>"""

open(os.path.join(OUT, "stand.html"), "w", encoding="utf-8").write(html)
print(os.path.join(OUT, "stand.html"))
