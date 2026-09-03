"""Сколько склад стоит: в бутылках, в закупке и в продаже.

Экран склада отвечал на один вопрос — сколько бутылок лежит. Но у владельца
вопросов три, и два из них про деньги: во сколько эти бутылки обошлись и
сколько за них выручим. Причём выручка двойная: цена в приложении и цена по
прайсу — это разные деньги, и разница между ними есть у каждой позиции.

Откуда что берётся
------------------
Остаток по позициям и районам — из основы рабочей таблицы, по которой
собирается заявка в магазин (`_district_base`): последний пересчёт плюс приход
и минус то, что с тех пор продали и разбили. Это единственное место, где
известно, сколько чего лежит В КАЖДОМ районе, а не всего. Берём именно основу,
а не готовые строки заявки: в них позиция без потребности не попадает, а
каталог нужен целиком.

Цены — из каталога, и их там по две на каждую учётную единицу: `price*` берёт
приложение, `price*_full` стоит по прайсу. У пива единица — ящик, поэтому
цены за ящик отдельные, и делить их на 24 нельзя: ящик стоит не как 24
бутылки.

Закупка — отдельная история. Своей себестоимости у товара в каталоге нет,
там только продажные цены. Настоящий прайс лежит в двух местах, и берём их
по очереди: сначала ручная правка владельца (она главнее всего), потом
`config_cost.py` — прайс магазина, перенесённый с листа, потом цена из
поставки, если её кто-то вносил.

Чего не знаем — не выдумываем: позиция без цены выпадает из суммы закупки, а
рядом с суммой стоит доля склада, которую она покрывает. Подставить продажную
цену вместо себестоимости нельзя — это не оценка, а враньё, неотличимое от
факта.
"""
from __future__ import annotations

import logging
import time as _t

from aiohttp import web

import db
import stock_routes
from owner_auth import require_owner

log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}

SUPPLY_LOOKBACK = 40        # по скольким последним поставкам ищем цену закупки
_COST: dict = {"at": 0.0, "map": {}}
COST_TTL = 30 * 60


def _sheet_cost() -> dict:
    """Прайс магазина с листа. Файл может отсутствовать — это не поломка."""
    try:
        import config_cost
        return dict(config_cost.COST)
    except Exception as e:                          # noqa: BLE001
        log.warning(f"[value] прайс не прочитался: {e}")
        return {}


async def cost_map() -> dict:
    """Последняя известная цена закупки по каждой позиции, за учётную единицу.

    Свежая поставка перебивает старую: цена месячной давности хуже вчерашней,
    но лучше, чем ничего."""
    now = _t.monotonic()
    if _COST["map"] and now - _COST["at"] < COST_TTL:
        return _COST["map"]
    # Слоями, от слабого к сильному: поставка → лист → рука владельца.
    out: dict = {}
    src: dict = {}
    try:
        sups = await db.supply_list(limit=SUPPLY_LOOKBACK)
        for sup in reversed(sups or []):            # свежая поставка поверх старой
            for pid, b in (sup.get("buys") or {}).items():
                try:
                    price = float((b or {}).get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    out[pid] = price
                    src[pid] = "поставка"
    except Exception as e:                          # noqa: BLE001
        log.warning(f"[value] поставки не прочитались: {e}")
    for pid, v in _sheet_cost().items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[pid] = v
            src[pid] = "прайс"
    try:
        for pid, v in (await db.cost_overrides()).items():
            try:
                v = float((v or {}).get("price") or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[pid] = v
                src[pid] = "вручную"
    except Exception as e:                          # noqa: BLE001
        log.warning(f"[value] ручные цены не прочитались: {e}")
    _COST["map"] = out
    _COST["src"] = src
    _COST["at"] = now
    return out


def _app_unit_price(p: dict, unit: int) -> int:
    """Цена учётной единицы в приложении. У ящика она своя и не равна цене
    бутылки, умноженной на 24."""
    if unit > 1:
        return int(p.get("price_24") or p.get("price_24_full")
                   or p.get("price_12") or 0)
    return int(p.get("price") or p.get("price_full") or 0)


async def build(day: str = "") -> dict:
    """Весь каталог с остатком по районам и деньгами.

    Каталог отдаётся целиком, а не только то, что лежит: пустая полка — тоже
    ответ, и он нужен ровно так же, как непустая. Но «нет» бывает двух родов, и
    путать их нельзя:

      0   — позицию в этом районе считали, и её там нет;
      —   — её туда ни разу не вносили, то есть мы про неё ничего не знаем.

    Отличить одно от другого можно только по листу пересчёта: если позиции нет
    в его строках, значит её и не считали. Район, который не считали вовсе,
    даёт прочерк по всем позициям сразу.
    """
    from config_stock_order import order_key
    # День обязателен строкой: расчёт нормы разбирает его через strptime и на
    # пустой строке падает. Раньше это делала заявка, у которой мы брали
    # строки; теперь основу спрашиваем сами — значит и день готовим сами.
    day = (day or "").strip() or stock_routes._biz_day()
    cat = stock_routes._catalog()
    base = await stock_routes._district_base(day)
    cost = await cost_map()
    src = _COST.get("src") or {}

    ids = list(stock_routes.OFFICE_IDS)
    districts = [{"id": o, "code": stock_routes.OFFICE_CODES.get(o, ""),
                  "name": stock_routes.OFFICE_NAMES.get(o, o)} for o in ids]
    zero = lambda: {"bottles": 0.0, "app": 0.0, "list": 0.0, "cost": 0.0,
                    "cost_bottles": 0.0}
    per = {o: zero() for o in ids}
    total = zero()
    items = []

    for pid, p in cat.items():
        unit = stock_routes._unit(p)
        list_u = float(stock_routes._price(p))
        app_u = float(_app_unit_price(p, unit))
        cost_u = float(cost.get(pid) or 0)
        row = {"id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
               "unit": unit, "price_app": app_u, "price_list": list_u,
               "cost": cost_u or None, "cost_src": src.get(pid, ""),
               "no": order_key(pid) + 1,
               "have": {}, "bottles": 0.0, "known": False}
        for oid in ids:
            b = base.get(oid) or {}
            имеет = b.get("have") or {}
            # Район не считали вовсе или позиции не было в его листе — про неё
            # здесь ничего не известно, и это не ноль.
            if not b.get("counted") or pid not in имеет:
                row["have"][oid] = None
                continue
            row["known"] = True
            have = float(имеет.get(pid) or 0)
            row["have"][oid] = have
            if have <= 0:
                continue
            b_ = have * unit
            row["bottles"] += b_
            slot = per[oid]
            for box in (slot, total):
                box["bottles"] += b_
                box["app"] += have * app_u
                box["list"] += have * list_u
                if cost_u:
                    box["cost"] += have * cost_u
                    box["cost_bottles"] += b_
        items.append(row)

    # Порядок как в заявке: ряд на полке идёт как идёт, и «по убыванию
    # остатка» здесь означает прыгать по залу глазами. Человек сверяется со
    # строкой листа, а строка на листе стоит на своём номере.
    items.sort(key=lambda r: r["no"])
    rnd = lambda x: round(x, 2)
    fix = lambda s: {k: (round(v) if k in ("bottles", "cost_bottles") else rnd(v))
                     for k, v in s.items()}
    covered = (total["cost_bottles"] / total["bottles"] * 100) if total["bottles"] else 0
    с_остатком = sum(1 for r in items if r["bottles"] > 0)
    out = {
        "day": day,
        "districts": districts,
        "totals": fix(total),
        "by_district": {k: fix(v) for k, v in per.items()},
        "items": [{**r, "bottles": round(r["bottles"])} for r in items],
        "cost_cover": round(covered),
        "cost_known": sum(1 for r in items if r.get("cost")),
        "items_total": len(items),
        "items_with_stock": с_остатком,
    }
    log.info(f"[value] позиций {len(items)} (с остатком {с_остатком}) · "
             f"бутылок {out['totals']['bottles']} · "
             f"прайс {out['totals']['list']:.0f} · приложение {out['totals']['app']:.0f} · "
             f"закупка {out['totals']['cost']:.0f} (покрытие {out['cost_cover']}%)")
    return out


# ── Прайс: список позиций с обеими ценами и ручной правкой ───────────────────
@require_owner
async def handle_prices(request):
    """Все позиции с продажной и закупочной ценой и с тем, откуда закупочная
    взялась. Экран цен спрашивает ровно это."""
    cat = stock_routes._catalog()
    cost = await cost_map()
    src = _COST.get("src") or {}
    from config_stock_order import order_key
    rows = []
    for pid, p in cat.items():
        unit = stock_routes._unit(p)
        rows.append({
            "id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
            "unit": unit, "unit_name": "ящик" if unit > 1 else "бутылка",
            "price_app": _app_unit_price(p, unit),
            "price_list": stock_routes._price(p),
            "cost": cost.get(pid) or None,
            "cost_src": src.get(pid, ""),
            "no": order_key(pid) + 1,
        })
    rows.sort(key=lambda r: r["no"])
    known = sum(1 for r in rows if r["cost"])
    return web.json_response({
        "rows": rows, "total": len(rows), "cost_known": known,
        "cost_cover": round(known / len(rows) * 100) if rows else 0,
    }, headers=CORS_HEADERS)


@require_owner
async def handle_cost_set(request):
    """Поправить закупочную цену руками. Пустая или ноль — снять правку и
    вернуться к прайсу из файла."""
    try:
        body = await request.json()
    except Exception:                               # noqa: BLE001
        return web.json_response({"error": "bad_json"}, status=400,
                                 headers=CORS_HEADERS)
    pid = str(body.get("id") or "").strip()
    if not pid:
        return web.json_response({"error": "no_id"}, status=400, headers=CORS_HEADERS)
    raw = body.get("price")
    try:
        price = float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_price"}, status=400, headers=CORS_HEADERS)
    if price < 0 or price > 100000:
        return web.json_response({"error": "bad_price"}, status=400, headers=CORS_HEADERS)
    who = str(body.get("as") or "").strip()[:40]
    await db.cost_override_set(pid, price, who)
    _COST["at"] = 0.0                               # пересоберём при следующем спросе
    cost = await cost_map()
    log.info(f"[value] цена закупки {pid} → {price or 'снята'} · {who or '—'}")
    return web.json_response({"ok": True, "id": pid,
                              "cost": cost.get(pid) or None,
                              "cost_src": (_COST.get("src") or {}).get(pid, "")},
                             headers=CORS_HEADERS)


@require_owner
async def handle_value(request):
    return web.json_response(await build(request.query.get("day") or ""),
                             headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/stock/value", _opt)
    app.router.add_get("/api/owner/stock/value", handle_value)
    app.router.add_route("OPTIONS", "/api/owner/stock/prices", _opt)
    app.router.add_get("/api/owner/stock/prices", handle_prices)
    app.router.add_route("OPTIONS", "/api/owner/stock/cost", _opt)
    app.router.add_post("/api/owner/stock/cost", handle_cost_set)
    log.info("[value] routes mounted")
