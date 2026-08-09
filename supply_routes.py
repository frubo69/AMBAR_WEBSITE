"""Заявка в магазин и приход от него: Excel туда, Excel обратно.

Цикл, ради которого всё:

    1. Программа считает, чего не хватает до нормы  (stock_routes.handle_order)
    2. Выгружаем это в .xlsx и отправляем в магазин  → handle_export
    3. Магазин проставляет, что у него реально есть, и присылает файл назад
    4. Загружаем файл обратно                        → handle_import
    5. Получается поставка: что и сколько мы правда заберём
    6. Водитель видит её у себя и сканирует бутылки при получении

Ключ к 3–4 шагам — колонка с кодом позиции. Магазин правит количества и может
переставить или удалить строки; по названию сопоставлять нельзя, потому что
«Absolut 1 ltr» у них в файле легко станет «ABSOLUT BLUE 1L». Код едет в файле
и возвращается неизменным, а название рядом — чтобы человеку было понятно.
"""
import io
import logging
from datetime import datetime, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS
from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES

log = logging.getLogger("supply")

CODE_COL = "Код"          # служебная колонка, по ней идёт возврат
QTY_COL = "Заявлено"
CONFIRM_COL = "Подтверждено"


def _catalog_by_id():
    from operator_routes import _catalog_by_id as f
    return f()


async def _order_rows(day):
    """Строки заявки — тем же расчётом, что и на экране «Заявка на закупку»."""
    import stock_routes
    from aiohttp.test_utils import make_mocked_request
    # Логика заявки живёт в stock_routes и там же должна остаться: два расчёта
    # «сколько докупить» рано или поздно разойдутся, и никто не заметит.
    return await stock_routes.order_rows(day)


@require_owner
async def handle_export(request):
    """Заявка в .xlsx — тот файл, который уходит в магазин.

    Два листа: «Заявка» для магазина (что и сколько, плюс пустая колонка под
    их подтверждение) и «По точкам» для нас — магазину она не нужна, а нам
    развозить."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    day = (request.query.get("day") or "").strip()
    data = await _order_rows(day)
    rows = data["rows"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Заявка"

    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F2A37")
    ask = PatternFill("solid", fgColor="FFF3CD")      # колонку магазина видно сразу

    ws.append([f"AMBAR · заявка на закупку от {data['day']}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append([CODE_COL, "Позиция", "Категория", QTY_COL, CONFIRM_COL, "Комментарий магазина"])
    for c in ws[3]:
        c.font = head; c.fill = fill; c.alignment = Alignment(horizontal="center")

    for r in rows:
        ws.append([r["id"], r["name"], r["cat"], r["need_total"], None, None])
    for row in ws.iter_rows(min_row=4, min_col=5, max_col=6):
        for c in row:
            c.fill = ask

    ws.append([])
    ws.append([None, "ИТОГО", None, data["total_qty"], None, None])
    ws.cell(row=ws.max_row, column=2).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=4).font = Font(bold=True)

    for col, w in zip("ABCDEF", (10, 38, 16, 12, 15, 30)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"

    # Второй лист — развозка. Магазину он безразличен, нам без него непонятно,
    # куда потом раскладывать привезённое.
    ws2 = wb.create_sheet("По точкам")
    dist = [d["id"] for d in data["districts"]]
    ws2.append(["Позиция"] + [f"{OFFICE_CODES.get(o,'')} {OFFICE_NAMES.get(o,o)}" for o in dist] + ["Итого"])
    for c in ws2[1]:
        c.font = head; c.fill = fill
    for r in rows:
        ws2.append([r["name"]] + [(r["cells"].get(o) or {}).get("need", 0) for o in dist]
                   + [r["need_total"]])
    ws2.column_dimensions["A"].width = 38
    for i in range(2, len(dist) + 3):
        ws2.column_dimensions[get_column_letter(i)].width = 14
    ws2.freeze_panes = "B2"

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    name = f"AMBAR-zayavka-{data['day']}.xlsx"
    log.info(f"[supply] выгрузка заявки {data['day']}: {len(rows)} позиций")
    return web.Response(
        body=buf.read(),
        headers={**CORS_HEADERS,
                 "Content-Type": "application/vnd.openxmlformats-officedocument."
                                 "spreadsheetml.sheet",
                 "Content-Disposition": f'attachment; filename="{name}"'})


def _num(v):
    """Число из ячейки. Магазин пишет «12 шт», «12,0» и просто 12."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", ".").strip()
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


@require_owner
async def handle_import(request):
    """Ответ магазина: читаем файл и получаем поставку.

    Берём «Подтверждено», а если магазин её не заполнил — «Заявлено»: файл,
    вернувшийся без правок, значит «даём всё, что просили», и заставлять
    человека дописывать те же числа руками незачем."""
    from openpyxl import load_workbook

    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "file_required"}, status=400, headers=CORS_HEADERS)
    raw = await field.read(decode=False)
    if not raw:
        return web.json_response({"error": "empty_file"}, status=400, headers=CORS_HEADERS)

    try:
        wb = load_workbook(io.BytesIO(raw), data_only=True)
    except Exception as e:
        return web.json_response({"error": "not_xlsx", "detail": str(e)[:120]},
                                 status=400, headers=CORS_HEADERS)
    ws = wb["Заявка"] if "Заявка" in wb.sheetnames else wb.worksheets[0]

    # Ищем строку заголовков, а не полагаемся на номер: магазин мог вставить
    # сверху свою шапку с логотипом, и жёсткая «третья строка» развалилась бы.
    hdr_row, cols = None, {}
    for row in ws.iter_rows(min_row=1, max_row=15):
        vals = {str(c.value).strip(): c.column for c in row if c.value}
        if CODE_COL in vals:
            hdr_row, cols = row[0].row, vals
            break
    if not hdr_row:
        return web.json_response({"error": "no_code_column", "need": CODE_COL},
                                 status=400, headers=CORS_HEADERS)

    cat = _catalog_by_id()
    items, unknown, dropped = [], [], []
    for row in ws.iter_rows(min_row=hdr_row + 1):
        code = row[cols[CODE_COL] - 1].value
        if not code:
            continue
        pid = str(code).strip()
        p = cat.get(pid)
        if not p:
            unknown.append(pid); continue
        asked = _num(row[cols[QTY_COL] - 1].value) if QTY_COL in cols else None
        conf = _num(row[cols[CONFIRM_COL] - 1].value) if CONFIRM_COL in cols else None
        qty = conf if conf is not None else (asked or 0)
        if qty <= 0:
            # Магазин прямо сказал «нет в наличии» — это не потеря строки, а
            # ответ, и его надо показать: иначе водитель поедет за тем, чего нет.
            dropped.append({"id": pid, "name": p.get("name", ""), "asked": asked or 0})
            continue
        items.append({"id": pid, "name": p.get("name", ""),
                      "asked": asked or 0, "qty": qty, "scanned": 0})

    if not items:
        return web.json_response({"error": "nothing_confirmed", "dropped": len(dropped)},
                                 status=400, headers=CORS_HEADERS)

    now = datetime.now(timezone.utc)
    sid = "S" + now.strftime("%y%m%d-%H%M%S")
    doc = {"_id": sid, "at": now, "status": "open",
           "by": request.get("owner_id") or 0,
           "items": items, "dropped": dropped, "unknown": unknown,
           "total_qty": sum(i["qty"] for i in items)}
    await db.supply_save(doc)
    log.info(f"[supply] поставка {sid}: {len(items)} позиций, "
             f"{doc['total_qty']} бутылок, отказов {len(dropped)}")
    return web.json_response({"ok": True, "supply_id": sid,
                              "items": len(items), "total_qty": doc["total_qty"],
                              "dropped": dropped, "unknown": unknown},
                             headers=CORS_HEADERS)


@require_owner
async def handle_list(request):
    """Поставки: что в работе и что уже забрали."""
    rows = await db.supply_list(limit=30)
    return web.json_response({"supplies": rows}, headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    r.add_route("OPTIONS", "/api/owner/supply/export", _opt)
    r.add_get("/api/owner/supply/export", handle_export)
    r.add_route("OPTIONS", "/api/owner/supply/import", _opt)
    r.add_post("/api/owner/supply/import", handle_import)
    r.add_route("OPTIONS", "/api/owner/supply", _opt)
    r.add_get("/api/owner/supply", handle_list)
    log.info("[supply] routes mounted")
