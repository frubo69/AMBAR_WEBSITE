"""Стенд карточки заказа в очереди оператора из настоящего operator/index.html.

Нужен для одного вопроса: как выглядит заказ, вернувшийся в доставку из другой
смены (#AMB1590679, 25 сен 2026). Смотреть надо на то, что увидит оператор, а
не на пересказ.

    python3 tools/opq_stand_gen.py <dir> [ширина]   → <dir>/stand.html
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
W = sys.argv[2] if len(sys.argv) > 2 else "820"
src = open(os.path.join(ROOT, "operator", "index.html"), encoding="utf-8").read()
styles = re.findall(r"<style>(.*?)</style>", src, re.S)


def fn(name):
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}\n", src, re.S)
    assert m, name
    return m.group(0)


def fn1(name):
    """Однострочная функция: у fn() якорь — закрывающая скобка со своей строки."""
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\(.*\n", src)
    assert m, name
    return m.group(0)


def const(name, end=r";\n"):
    m = re.search(r"(?:const|let) " + re.escape(name) + r"\s*=\s*.*?" + end, src, re.S)
    assert m, name
    return m.group(0)


ТОВАР = [{"id": "p116", "name": "Minuty Cotes De Provence 0.75", "qty": 2, "price": 200},
         {"id": "p115", "name": "Mateus Rose 0.75", "qty": 2, "price": 100}]
ОБЩЕЕ = {"office_id": "tecom", "office_name": "Тиком", "district": "Тиком",
         "customer_name": "Клиент", "address": "Tecom, 4 этаж", "phone": "—",
         "driver": "Муин", "total": 600, "items": ТОВАР, "source": "manual",
         "payment_method": "cash", "timestamp": "2026-09-25T01:59:50+00:00",
         "deliver_by": "02:40", "customer_id": 0}
ЗАКАЗЫ = [
    {**ОБЩЕЕ, "order_id": "AMB1590679", "status": "approved", "day": "2026-09-24"},
    {**ОБЩЕЕ, "order_id": "AMB1590712", "status": "approved", "day": "2026-09-25",
     "deliver_by": "20:15"},
]

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Очередь оператора</title>
<link rel="stylesheet" href="{os.path.join(ROOT, 'vendor', 'fonts.css')}">
<style>{''.join(styles)}</style>
<style>body{{margin:0;background:var(--bg);width:{W}px}} .stand{{padding:14px}}</style>
</head><body><div class="stand"><div id="box"></div></div>
<script>
let Q = {{districts: [{{id: "tecom", code: "B5", name: "Тиком"}}], today: "2026-09-25"}};
const DRQ = {{note: {{t: "просьба", ok: "Одобрить", cls: "go"}}}};
{const("_MON_RU", end=r"\];\n")}
{const("_esc", end=r"\n")}
{fn("_dpIso")}
{fn("_age")}
{fn("_fxNote")}
{fn("_distCode")}
{fn1("_distName")}
{fn("_openReq")}
{fn("_reqStrip")}
{fn("_itemRows")}
{const("ICO_ACT", end=r"\n\};\n")}
{const("_actIco", end=r";\n")}
{fn("_dayFlag")}
{fn("_card")}
document.getElementById('box').innerHTML =
  '<div class="q-grid">' + {json.dumps(ЗАКАЗЫ, ensure_ascii=False)}
    .map(o => _card(o, 'work')).join('') + '</div>';
</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "stand.html")
open(path, "w", encoding="utf-8").write(html)
print(path)
