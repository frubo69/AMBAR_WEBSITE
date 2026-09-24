"""Стенд экрана недостачи из настоящего owner/index.html: строка ревизии,
список ненайденных бутылок и карточка бутылки с её историей.

Берёт стили и сами функции панели регуляркой — как fb_stand_gen: смотреть
надо на то, что человек увидит, а не на пересказ.

    python3 tools/aud_stand_gen.py <dir>   → <dir>/stand.html?view=row|list|card
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
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


ФУНКЦИИ = ["escS", "pluralize", "fmtQty", "_siRow", "_siTap", "scanInfo", "scanInfoHide",
           "_distName", "_when", "_audU", "_audCoded", "_bizToday", "_dayIsoFor", "_audDayT", "audRow",
           "audMissing", "audBottle", "_audHap"]

СТРОКА = {
    "id": "p59", "no": 41, "name": "Tanqueray 1 ltr", "unit": 1, "price": 220,
    "expected": 3, "coded": 3, "actual": 2, "diff": 1, "noqr": 0, "gone": 0,
    "missing": [{"code": "16807", "label": "tan#000021", "at": "2026-09-18 14:51:42",
                 "from": "silicon", "from_code": "B3", "moved_at": "2026-09-20 18:08:58"}],
}
МНОГО = {
    "id": "p1", "no": 1, "name": "Absolut 1 ltr", "unit": 1, "price": 180,
    "expected": 21, "coded": 21, "actual": 16, "diff": 5, "noqr": 0, "gone": 0,
    "missing": [{"code": f"229{i:02d}", "label": f"abs#0004{i:02d}", "at": "2026-09-21 16:08:55",
                 "from": "", "from_code": "", "moved_at": ""} for i in range(6)]
        + [{"code": f"224{i:02d}", "label": f"abs#0002{i:02d}", "at": "2026-09-18 05:12:00",
            "from": "", "from_code": "", "moved_at": ""} for i in range(6)],
}
БУТЫЛКА = {
    "code": "16807", "label": "tan#000021", "product_name": "Tanqueray 1 ltr",
    "district": "tecom", "status": "active",
    "story": [{"at": "2026-09-18T14:51:42", "what": "заведена", "who": "AMBAR STAR", "where": "tecom"},
              {"at": "2026-09-20T18:08:58", "what": "переезд", "who": "AMBAR STAR", "where": "silicon → tecom"},
              {"at": "2026-09-22T14:10:00", "what": "ревизия", "who": "", "where": "tecom"}],
}

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · недостача</title>
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg,#0a0b10);color:#fff;font-family:Inter,system-ui,sans-serif}}
  #phone{{width:390px;margin:0 auto;padding:18px 14px 40px;box-sizing:border-box}}
  .aud-rows{{border-radius:16px;overflow:hidden;background:var(--card)}}
  h4{{margin:18px 0 8px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
    color:rgba(255,255,255,.45);font-weight:700}}
</style></head><body>
<div id="phone">
  <h4>Строка ревизии</h4>
  <div class="aud-rows" id="rows"></div>
  <h4>Что откроется по нажатию</h4>
  <div id="hint" style="font-size:12px;color:rgba(255,255,255,.45)">…</div>
</div>
<div class="scan-info-scrim" id="scanInfoScrim" onclick="scanInfoHide()"></div>
<div class="scan-info" id="scanInfo" role="dialog" aria-label="Бутылка">
  <div class="scan-info-grab"></div>
  <div id="scanInfoBody"></div>
</div>
<script>
{const('_CHEV', r"`;\n")}
{const('SHIFT_START_HOUR', chr(10))}
{const('MONTHS_GEN')}
{const('MONTHS_RU')}
{const('_SI_ICON')}
const STK = {{district: 'tecom', districts: [
  {{id:'jvc',code:'B1',name:'JVC'}}, {{id:'bbay',code:'B2',name:'Бизнес Бей'}},
  {{id:'silicon',code:'B3',name:'Силикон'}}, {{id:'alguses',code:'B4',name:'Алгусес'}},
  {{id:'tecom',code:'B5',name:'Тиком'}}]}};
STK.sheet = {{day: '2026-09-23', rows: [{СТРОКА!r}, {МНОГО!r}]}};
const ownerApi = {{ ownerFetch: async () => ({БУТЫЛКА!r}) }};
const Telegram = {{WebApp:{{HapticFeedback:{{impactOccurred(){{}},notificationOccurred(){{}},selectionChanged(){{}}}}}}}};
{''.join(fn(n) for n in ФУНКЦИИ)}
const view = new URLSearchParams(location.search).get('view') || 'row';
document.getElementById('rows').innerHTML = STK.sheet.rows.map(audRow).join('');
document.getElementById('hint').textContent =
  view === 'row' ? 'нажатие на строку открывает список ненайденных' : '';
if(view === 'list') audMissing('p59');
if(view === 'many') audMissing('p1');
if(view === 'card') audBottle('16807', 'p59');
</script></body></html>"""

html = html.replace("&quot;", '"')
open(os.path.join(OUT, "stand.html"), "w", encoding="utf-8").write(html)
print(os.path.join(OUT, "stand.html"))
