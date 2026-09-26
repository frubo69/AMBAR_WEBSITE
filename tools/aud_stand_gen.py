"""Стенд строк ревизии из настоящего owner/index.html — до старта и в ходе.

Строка «не поднесли к камере» появлялась у КАЖДОЙ позиции ещё до начала
ревизии: камера ничего не видела, значит не хватает всего. Стенд показывает
обе половины рядом, чтобы смотреть глазами, а не читать пересказ.

    python3 tools/aud_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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


# Строки как на снимке владельца: числится 8, кодов 12, камера не видела ничего.
СТРОКИ = [
    {"id": "p8",  "no": 50, "name": "Belvedere 1 ltr",   "expected": 8, "coded": 8, "actual": 0,
     "missing": [{"code": "blv#0001"}] * 12},
    {"id": "p7",  "no": 51, "name": "Grey Goose 1 ltr",  "expected": 7, "coded": 7, "actual": 0,
     "missing": [{"code": "gg#0001"}] * 12},
    {"id": "p6",  "no": 52, "name": "Beluga 0.7 ltr",    "expected": 4, "coded": 4, "actual": 0,
     "missing": [{"code": "blg#0001"}] * 8},
    {"id": "p9",  "no": 53, "name": "Ciroc 1 ltr",       "expected": 2, "coded": 2, "actual": 0,
     "missing": [{"code": "cr#0001"}] * 2},
    {"id": "p4",  "no": 54, "name": "Skyy Vodka 1 ltr",  "expected": 3, "coded": 3, "actual": 0,
     "missing": [{"code": "sk#0001"}] * 3},
]
# Та же полка, но ревизия идёт: часть уже поднесли к камере.
ИДЁТ = [dict(r, actual=a) for r, a in zip(СТРОКИ, [8, 5, 4, 0, 3])]
ИДЁТ[1]["missing"] = [{"code": "gg#0001"}] * 2
ИДЁТ[3]["missing"] = [{"code": "cr#0001"}] * 2
for r in (ИДЁТ[0], ИДЁТ[2], ИДЁТ[4]):
    r["missing"] = []

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Строки ревизии</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px 20px}}
  .stand h3{{font:700 12px/1 'Space Grotesk',sans-serif;letter-spacing:.08em;
    text-transform:uppercase;color:var(--muted);margin:18px 0 8px}}
</style></head><body>
<div class="stand">
  <h3>До старта — «Начать ревизию»</h3>
  <div class="aud-rows" id="idle"></div>
  <h3>Ревизия идёт</h3>
  <div class="aud-rows" id="run"></div>
</div>
<script>
{line(r"const _CHEV = `[^`]*`;")}
{line(r"function escS\(s\)\{.*")}
{line(r"function _audU\(r\)\{.*")}
{line(r"function _audCoded\(r\)\{.*")}
{fn("fmtQty")}
{fn("audRow")}
function audMissing(){{}}
document.getElementById('idle').innerHTML =
  {json.dumps(СТРОКИ, ensure_ascii=False)}.map(r => audRow(r, true)).join('');
document.getElementById('run').innerHTML =
  {json.dumps(ИДЁТ, ensure_ascii=False)}.map(r => audRow(r, false)).join('');
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
