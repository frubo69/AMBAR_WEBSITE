"""Стенд карточки «Приём товара» из настоящего owner/index.html.

Нужен, чтобы увидеть, как выглядит принятый район рядом с тем, куда ещё едут.

    python3 tools/pd_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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


РАЙОНЫ = [{"id": "jvc", "code": "B1", "name": "JVC"},
          {"id": "bbay", "code": "B2", "name": "Бизнес Бей"},
          {"id": "silicon", "code": "B3", "name": "Силикон"},
          {"id": "alguses", "code": "B4", "name": "Алгусес"},
          {"id": "tecom", "code": "B5", "name": "Тиком"}]
ЗАДАЧИ = {
 "jvc":     {"driver": "Худоба",  "done_at": "2026-09-25T11:20:00", "noscan_at": "2026-09-25T10:00:00", "got_units": 91},
 "bbay":    {"driver": "Авазбек", "done_at": "2026-09-25T11:51:00", "noscan_at": "2026-09-25T10:01:00", "got_units": 151},
 "silicon": {"driver": "",        "done_at": None, "noscan_at": None, "got_units": 0},
 "alguses": {"driver": "Джавид",  "done_at": None, "noscan_at": "2026-09-25T10:05:00", "got_units": 40},
 "tecom":   {"driver": "Муин",    "done_at": "2026-09-25T14:18:00", "noscan_at": "2026-09-25T10:02:00", "got_units": 101},
}

html = f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Приём товара</title>
<link rel="stylesheet" href="{os.path.join(ROOT, 'vendor', 'fonts.css')}">
<style>{''.join(styles)}</style>
<style>body{{margin:0;background:var(--bg);width:{W}px}} .stand{{padding:12px}}</style>
</head><body><div class=stand><div id=box></div></div>
<script>
let ACC_SUP = {{open: {{status: "open", total_qty: 511, tasks: {json.dumps(ЗАДАЧИ, ensure_ascii=False)}}}, list: []}};
let ORD = {{d: {{districts: {json.dumps(РАЙОНЫ, ensure_ascii=False)}}}}};
function conv(v){{ return +v || 0; }}
function _supCur(){{ return null; }}
{const("ICO_CHEV", end=r";\n")}
{const("ICO_MAP", end=r";\n")}
{fn("escS")}
{fn("fmt")}
{fn("pluralize")}
{fn("supStage")}
{fn("pathDistricts")}
document.getElementById('box').innerHTML = pathDistricts();
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
p = os.path.join(OUT, "stand.html")
open(p, "w", encoding="utf-8").write(html)
print(p)
