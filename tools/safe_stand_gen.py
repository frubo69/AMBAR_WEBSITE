"""Стенд карточки «Сейф» из настоящего owner/index.html.

Владелец прислал макет и просит пиксель в пиксель — значит мерить надо
снимком, а не на глаз. Стенд берёт стили и сами функции панели.

    python3 tools/safe_stand_gen.py <dir> [ширина]   → <dir>/stand.html
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


def блок(нач, кон):
    """Многострочный кусок: от начала до закрывающей строки включительно."""
    i = src.index(нач)
    j = src.index("\n" + кон, i) + len(кон) + 1
    return src[i:j]


def line(pat):
    m = re.search(pat, src)
    assert m, pat
    return m.group(0)


# Числа с макета владельца.
ВСЕГО, B, RP, NP = 30414, 0, 11, 30402
WAL = {"aed": 1, "usdt": 0.25}

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · сейф</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px}}
</style></head><body>
<div class="stand" id="stand"></div>
<script>
{line(r"const _SC_CHEV = `[^`]*`;")}
{line(r"function escS\(s\)\{.*")}
function conv(a){{ return +a || 0; }}
function fbFmt(v){{ const n = Math.round(+v || 0);
  return n.toLocaleString('ru-RU').replace(/,/g,' '); }}
function fmtUsdt(v){{ const n = +v || 0;
  return (Math.round(n*100)/100).toLocaleString('ru-RU', {{minimumFractionDigits: 2}}); }}
function fbB(key, v, o){{ o = o || {{}};
  return `<b data-f="${{key}}">${{fbFmt(v)}}<u>${{o.unit || 'AED'}}</u></b>`; }}
function accOpen(){{}}
function fbOpenB(){{}}
function fbOpenWal(){{}}
{fn("fbGo")}
{блок("const FB_ICO = {", "};")}
const b = {{safe: {{b: {B}, rp: {RP}, np: {NP}}}}}, wal = {json.dumps(WAL)}, all = {ВСЕГО};
document.getElementById('stand').innerHTML = `
  <div class="sc fb-hub fb-safe">
    <button class="fb-safe-h"><span class="fb-safe-t">Сейф</span><b class="fb-safe-v" data-f="safe.all">${{fbFmt(all)}}<u>AED</u></b><span class="fb-safe-usdt">${{fmtUsdt(wal.usdt)}}<u>USDT</u></span><em class="fb-chev r">${{_SC_CHEV}}</em></button>
    <div class="fb-safe-k">
      ${{fbGo('Барракуда', 'safe.b', b.safe.b, "", {{unit: 'в сейфе', ico: FB_ICO.b}})}}
      ${{fbGo('РП', 'safe.rp', b.safe.rp, "", {{unit: 'в сейфе', mode: 'neg', ico: FB_ICO.rp}})}}
      ${{fbGo('Крипта', 'safe.wal', wal.aed, "", {{unit: `AED · ${{fmtUsdt(wal.usdt)}} USDT`, ico: FB_ICO.wal}})}}
      ${{fbGo('ЧП', 'safe.np', b.safe.np, "", {{unit: 'в сейфе', mode: 'neg', ico: FB_ICO.np}})}}
    </div>
    <button class="sc-r fb-ddcgo"><span class="fb-ic">${{FB_ICO.ddc}}</span><span>Отчёт ДДС</span><em>${{_SC_CHEV}}</em></button>
  </div>`;
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
