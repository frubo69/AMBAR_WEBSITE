"""Стенд карточки района в «Сборе выручки» — из настоящего owner/index.html.

Старший забирает либо всё сразу, либо ровно за один день (владелец, 28 сен
2026), и разбивка по дням стала рядом кнопок. Посмотреть на неё можно только
снимком: живой экран собирается из денег за пять дней.

    python3 tools/cr_stand_gen.py <dir> [ширина] [вид]   → <dir>/stand.html

вид: carry (по умолчанию · дни с кнопками) | taken (забрали за один день)
     | done (забрал всё) | pend (расходы не согласованы)
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
W = sys.argv[2] if len(sys.argv) > 2 else "430"
ВИД = sys.argv[3] if len(sys.argv) > 3 else "carry"
src = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)


def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)


def блок(нач, кон):
    i = src.index(нач)
    j = src.index("\n" + кон, i) + len(кон) + 1
    return src[i:j]


def кусок(нач, хвост):
    """От строки `нач` до конца строки, где встретился `хвост`."""
    i = src.index(нач)
    k = src.index(хвост, i)
    j = src.index("\n", k + len(хвост))
    return src[i:j]


def line(pat):
    m = re.search(pat, src)
    assert m, pat
    return m.group(0)


D = {"day": "2026-09-27", "today": "2026-09-27"}
РАЙОН = {
    "id": "jvc", "code": "B1", "name": "JVC", "operator": "Оператор",
    "drivers": ["Водитель"], "orders": 14, "cash": 6120, "tips": 700, "spend": 285,
    "debt": 0, "debt_n": 0, "spend_card": 0, "fx": [], "spend_pending": 0,
    "net": 5135, "items": [], "carry": [
        {"day": "2026-09-26", "net": 4310, "tips": 550, "cash": 5000, "spend": 140, "orders": 11},
        {"day": "2026-09-25", "net": 2780, "tips": 400, "cash": 3300, "spend": 120, "orders": 8},
    ],
    "carry_net": 7090, "carry_tips": 950, "taken": [],
    "net_all": 12225, "tips_all": 1650, "empty": False, "done": False,
    "solo_today": False, "done_at": "", "later": False, "later_at": "",
}
if ВИД == "taken":
    РАЙОН = dict(РАЙОН, carry=[РАЙОН["carry"][0]], carry_net=4310, carry_tips=550,
                 taken=[{"day": "2026-09-25", "net": 2780, "tips": 400, "at": ""}],
                 net_all=9445, tips_all=1250)
elif ВИД == "done":
    РАЙОН = dict(РАЙОН, done=True, done_at="2026-09-27T18:12:00+00:00")
elif ВИД == "pend":
    РАЙОН = dict(РАЙОН, spend_pending=240)

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд · сбор выручки</title>
<link rel="stylesheet" href="{os.path.join(ROOT, "vendor", "fonts.css")}">
<style>{''.join(styles)}</style>
<style>
  body{{margin:0;background:var(--bg);width:{W}px}}
  .stand{{padding:14px 12px}}
</style></head><body>
<div class="stand" id="stand"></div>
<script>
{line(r"function escS\(s\)\{.*")}
{блок("const IC_P = {", "};")}
{fn("IC")}
{кусок("const xsvg = (d, w = 19) =>", "</svg>`;")}
{line(r"const FOLD_MIN = \d+;.*")}
{fn("foldEnd")}
{fn("pluralize")}
function conv(a){{ return +a || 0; }}
{line(r"function fmt\(aed\)\{.*")}
{кусок("const _dayName = d =>", "(d || ''); };")}
{кусок("const _hm = iso =>", "timeZone:'Asia/Dubai'}) : ''; };")}
const CR = {json.dumps(D)}, CR_OPEN = new Set();
{fn("crItem")}
{fn("crCard")}
document.getElementById('stand').innerHTML = crCard({json.dumps(РАЙОН, ensure_ascii=False)});
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
путь = os.path.join(OUT, "stand.html")
open(путь, "w", encoding="utf-8").write(html)
print(путь)
