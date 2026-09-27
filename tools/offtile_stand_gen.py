"""Стенд плитки района из настоящего owner/index.html.

Владелец, 27 сен 2026: «пиши только актуальных водителей, которые реально
работают, не уехали». Стенд показывает три случая рядом: все на месте, один
уехал, уехали все.

    python3 tools/offtile_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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


РАЙОНЫ = [
    {"id": "bbay", "code": "B2", "name": "Бизнес Бей", "operator": "Умар",
     "drivers": ["Парвиз", "Авазбек", "Баха"], "away": {},
     "aed": 7330, "orders": 7, "phones": [{"label": "002", "number": "+971 50 929 7577"}]},
    {"id": "alguses", "code": "B4", "name": "Алгусес", "operator": "Фарух",
     "drivers": ["Сунат", "Даврон", "Джавид"], "away": {"Даврон": "2026-09-10"},
     "aed": 3315, "orders": 4, "phones": [{"label": "004", "number": "+971 50 929 7577"}]},
    {"id": "tecom", "code": "B5", "name": "Тиком", "operator": "Джанабиль",
     "drivers": ["Файзуло", "Алишер"], "away": {"Файзуло": "2026-09-24", "Алишер": "2026-09-25"},
     "aed": 0, "orders": 0, "phones": []},
]

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · плитка района</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px 24px}}
  .stand h4{{font:700 11px/1 'Space Grotesk',sans-serif;letter-spacing:.1em;
    text-transform:uppercase;color:var(--muted);margin:16px 0 8px}}
</style></head><body>
<div class="stand">
  <h4>все на месте · один уехал · уехали все</h4>
  <div id="offCards"></div>
  <div id="offSpeed" style="display:none"></div>
  <div id="offSub" style="display:none"></div>
</div>
<script>
{line(r"function conv\(aed\)\{.*")}
{line(r"function fmt\(aed\)\{.*")}
{line(r"function escS\(s\)\{.*")}
{fn("pluralize")}
let curCur = 'AED', CUR_RATE = 3.6725, OFF_SORT = 'aed', OFF_LAST = null;
{line(r"const ICO_OP  = `[^`]*`;")}
{line(r"const ICO_CAR = `[^`]*`;")}
const OFF_SORT_LBL = {{aed: 'по выручке'}}, OFF_KNOWN = {{}};
function OFF_TONE(){{ return '#d4b478'; }}
function _offDayLabel(){{ return 'Сегодня · 27.09'; }}
const РЯД = {json.dumps(РАЙОНЫ, ensure_ascii=False)};
// Ровно тот кусок renderOffices, который рисует строку команды.
{fn("renderOffices")}
renderOffices(РЯД);
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
