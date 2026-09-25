"""Стенд сектора «Нашли на складе» из настоящего owner/index.html.

Водитель свободным сканом натыкается на наклейку, которой нет в реестре, — в
СТАРе она встаёт строками над «Внести QR коды». Смотреть надо на то, что
человек увидит, а не на пересказ: стили и сами функции берутся из панели.

    python3 tools/qrf_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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
    m = re.search(r"(?:const|let) " + re.escape(name) + r" = .*?" + end, src, re.S)
    assert m, name
    return m.group(0)


НАХОДКИ = [
    {"code": "bud#000247", "driver": "Худоба", "district": "tecom",
     "district_code": "B5", "district_name": "Тиком", "at": "2026-09-25T18:31:00+00:00"},
    {"code": "0419283746501", "driver": "Парвиз", "district": "jvc",
     "district_code": "B1", "district_name": "JVC", "at": "2026-09-24T09:02:00+00:00"},
]

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нашли на складе</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px 20px}}
</style></head><body>
<div class="stand"><div class="qd-page qr-home"><div id="qrFound"></div>
  <div class="sc grow">
    <div class="sc-cap"><span>Внести QR коды</span><i>с незавершённого приёма</i></div>
  </div>
</div></div>
<script>
{const("SHIFT_START_HOUR", end=r"\n")}
{const("_SC_CHEV", end=r"`;\n")}
{const("_QR_ICO", end=r"`;\n")}
{const("MONTHS_GEN", end=r"\];\n")}
{fn("escS")}
{fn("_bizToday")}
{fn("_dayIsoFor")}
{fn("_qrFoundWhen")}
{fn("qrFoundSec")}
let QR_FOUND_LIST = {json.dumps(НАХОДКИ, ensure_ascii=False)};
document.getElementById('qrFound').innerHTML = qrFoundSec();
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "stand.html")
open(path, "w", encoding="utf-8").write(html)
print(path)
