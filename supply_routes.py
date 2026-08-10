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

Лист один и сразу по точкам: «сколько всего» — это сумма по строке, и хранить
её отдельным листом значило держать два числа об одном и том же. Заодно из
файла возвращается развозка — какая бутылка в какое здание, а не общий ворох.
"""
import io
import logging
from datetime import datetime, timezone

from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS
from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES
from config_stock_order import order_key      # порядок обхода полок, как в их таблице

log = logging.getLogger("supply")

# Файл уходит в магазин — он на английском целиком. Заголовки читает их
# сотрудник, и «Подтверждено» ему ничего не говорит.
CODE_COL = "Code"          # служебная колонка, по ней идёт возврат
TOTAL_COL = "Total"          # считается формулой, а не нами
SHEET_MAIN = "Order"

# Названия точек у нас записаны кириллицей, хотя районы английские.
DIST_EN = {"jvc": "JVC", "bbay": "Business Bay", "silicon": "Silicon Oasis",
           "alguses": "Al Qusais", "tecom": "Tecom"}


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

    Один лист и сразу в разрезе точек. Отдельная «просто заявка» была лишней:
    магазин всё равно смотрит в неё, а нам потом нужно знать, куда развозить —
    и два списка приходилось сверять глазами.

    Позиции все, включая те, что сейчас не нужны. Магазину так проще: это
    привычный ему прайс, в котором он правит числа, а не список из пятидесяти
    строк, где не найти то, что он хочет предложить сверх заказа.

    Total в строке — формула. Магазин правит числа по точкам, и переписанный
    руками итог разошёлся бы с ними в первый же раз."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    day = (request.query.get("day") or "").strip()
    data = await _order_rows(day)
    # Порядок как в их таблице: заявку собирают, идя вдоль полок, и список,
    # отсортированный по количеству, заставляет бегать по залу кругами.
    rows = sorted(data.get("all_rows") or data["rows"], key=lambda r: order_key(r["id"]))
    dist = [d["id"] for d in data["districts"]]

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_MAIN

    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F2A37")
    ask = PatternFill("solid", fgColor="FFF3CD")      # что правит магазин
    sum_fill = PatternFill("solid", fgColor="EAEAEA")
    thin = Side(style="thin", color="BFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append([f"AMBAR · purchase order · {data['day']}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Please correct the quantities you can supply and send the file back. "
               "The Total column adds up by itself. Do not change the Code column."])
    ws.append([CODE_COL, "Item"] +
              [f"{OFFICE_CODES.get(o,'')} {DIST_EN.get(o, OFFICE_NAMES.get(o,o))}"
               for o in dist] + [TOTAL_COL])
    for c in ws[3]:
        c.font = head; c.fill = fill; c.alignment = Alignment(horizontal="center")

    first = 4
    n_d = len(dist)
    for r in rows:
        ws.append([r["id"], r["name"]] +
                  [(r["cells"].get(o) or {}).get("need", 0) for o in dist] + [None])
        i = ws.max_row
        c0, c1 = get_column_letter(3), get_column_letter(2 + n_d)
        # Итог строки считает сам файл: магазин правит числа по точкам, и
        # переписанная руками сумма разошлась бы с ними на первой же правке.
        ws.cell(row=i, column=3 + n_d).value = f"=SUM({c0}{i}:{c1}{i})"
        for col in range(3, 3 + n_d):
            cell = ws.cell(row=i, column=col)
            cell.fill = ask; cell.border = box
            cell.alignment = Alignment(horizontal="center")
        t = ws.cell(row=i, column=3 + n_d)
        t.font = Font(bold=True); t.fill = sum_fill; t.border = box
        t.alignment = Alignment(horizontal="center")

    last = ws.max_row
    ws.append([])
    ws.append([None, "TOTAL"])
    i = ws.max_row
    for col in range(3, 4 + n_d):
        L = get_column_letter(col)
        c = ws.cell(row=i, column=col)
        c.value = f"=SUM({L}{first}:{L}{last})"
        c.font = Font(bold=True); c.alignment = Alignment(horizontal="center")
    ws.cell(row=i, column=2).font = Font(bold=True)

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 40
    for col in range(3, 4 + n_d):
        ws.column_dimensions[get_column_letter(col)].width = 15
    ws.freeze_panes = "C4"

    # Снимок того, что просили: в файле все позиции каталога, и вернувшийся ноль
    # сам по себе не отличает «магазин отказал» от «мы и не заказывали».
    try:
        await db.zayavka_save(data["day"],
                              {r["id"]: r["need_total"] for r in rows if r["need_total"]})
    except Exception as e:
        log.warning(f"[supply] снимок заявки: {e}")

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    name = f"AMBAR-zayavka-{data['day']}.xlsx"
    log.info(f"[supply] выгрузка заявки {data['day']}: {len(rows)} позиций, "
             f"из них с потребностью {sum(1 for r in rows if r['need_total'])}")
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
    if s.startswith("="):          # формула, которую Excel не пересчитал
        return None
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


def _district_cols(cols: dict) -> dict:
    """Колонки точек по заголовкам: «B2 Business Bay» → офис.

    По коду, а не по названию: код мы ставим сами и он короткий, а название
    магазин может перевести или сократить."""
    out = {}
    for title, col in cols.items():
        head = str(title).strip()
        for oid in OFFICE_IDS:
            code = OFFICE_CODES.get(oid, "")
            if code and (head == code or head.startswith(code + " ")):
                out[oid] = col
                break
    return out


@require_owner
async def handle_import(request):
    """Ответ магазина: читаем файл и получаем поставку.

    Количество берём суммой по точкам, а не из Total: Total — формула, и если
    магазин правил файл в Google Sheets или в редакторе попроще, в ячейке
    приедет либо старое значение, либо сам текст формулы.

    Строка с нулями — это либо отказ, либо позиция, которую мы и не просили.
    Различаем по снимку заявки: без него пришлось бы либо терять отказы, либо
    записывать в них весь каталог."""
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
    ws = wb[SHEET_MAIN] if SHEET_MAIN in wb.sheetnames else wb.worksheets[0]

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

    dcols = _district_cols(cols)
    if not dcols:
        return web.json_response({"error": "no_district_columns"},
                                 status=400, headers=CORS_HEADERS)

    asked_map = {}
    try:
        asked_map = await db.zayavka_last()
    except Exception as e:
        log.warning(f"[supply] снимок заявки не прочитан: {e}")

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
        by_district = {}
        for oid, col in dcols.items():
            n = _num(row[col - 1].value) or 0
            if n > 0:
                by_district[oid] = n
        qty = sum(by_district.values())
        asked = int(asked_map.get(pid) or 0)
        if qty <= 0:
            # Отказом считаем только то, что правда просили: иначе в отказы
            # попал бы весь каталог, и водитель не нашёл бы в нём смысла.
            if asked:
                dropped.append({"id": pid, "name": p.get("name", ""), "asked": asked})
            continue
        items.append({"id": pid, "name": p.get("name", ""),
                      "asked": asked, "qty": qty, "scanned": 0,
                      "by_district": by_district})

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
