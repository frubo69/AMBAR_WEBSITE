"""
AMBAR — приложение водителя.

Кто сюда заходит
----------------
Водитель из ростера (config_staff), чей telegram_id вписан в AMBAR_DRIVER_IDS.
Имени мало: заказ знает водителя по имени, но имя может совпасть, а доступ —
это про конкретный аккаунт. Нет id в списке — нет входа, и это же служит
выдачей и отзывом доступа.

Что он может
------------
  • видеть свои заказы — те, где он назначен водителем;
  • отметить доставку — та же операция, что кнопка «Доставлен» в боте;
  • попросить правку — сам он заказ не меняет, а отправляет просьбу оператору:
    цена и состав остаются под операторским контролем;
  • записать расход — со статусом «на согласовании», пока менеджер не утвердит.
    В расходы дня такой не попадает: иначе водитель сам себе назначал бы траты.

У водителей свой бот (DRIVER_BOT_TOKEN): подпись initData проверяется его
токеном, а значит вход в приложение водителя невозможен из операторского — и
наоборот. Роли не пересекаются даже случайно.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from aiohttp import web

import db
import config_staff as staff
from owner_auth import CORS_HEADERS

log = logging.getLogger("driver")

DRIVER_BOT_TOKEN = os.getenv("DRIVER_BOT_TOKEN", "")
INIT_DATA_MAX_AGE = 24 * 3600
DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _valid_init_data(init_data: str, token: str) -> dict | None:
    """Проверка подписи Telegram. Та же схема, что у остальных приложений."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        # Просроченный initData — украденный initData: живой клиент присылает свежий.
        if abs(time.time() - int(pairs.get("auth_date", 0))) > INIT_DATA_MAX_AGE:
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def require_driver(fn):
    """Пускает только того, чей аккаунт вписан в AMBAR_DRIVER_IDS."""
    async def wrapper(request):
        if request.method == "OPTIONS":
            return web.Response(status=200, headers=CORS_HEADERS)
        auth = request.headers.get("Authorization", "")
        init_data = auth[4:] if auth.startswith("tma ") else ""
        user = _valid_init_data(init_data, DRIVER_BOT_TOKEN)
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401, headers=CORS_HEADERS)
        me = staff.driver_by_tg(user.get("id"))
        if not me:
            log.warning(f"[driver] отказ: tg={user.get('id')} ({user.get('username')}) не в списке")
            return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
        request["driver"] = me
        request["tg"] = user
        return await fn(request)
    wrapper.__wrapped__ = fn
    return wrapper


def _order_view(o: dict) -> dict:
    """Заказ глазами водителя: адрес, состав, сумма.

    Номера клиента здесь нет и быть не может — ни в поле, ни за кнопкой. Это
    строгое правило: телефон клиента водителю не отдаётся. Нужно позвонить —
    звонит оператор."""
    return {
        "order_id": o.get("order_id", ""),
        "status": o.get("status", ""),
        "address": o.get("address", ""),
        "gmap_link": o.get("gmap_link", ""),
        "location": o.get("location") or {},
        "district": o.get("district") or o.get("office_name", ""),
        "customer_name": o.get("customer_name", ""),
        "items": [{"name": i.get("name", ""), "qty": i.get("qty", 0), "pcs": i.get("pcs")}
                  for i in (o.get("items") or [])],
        "total": int(o.get("total", 0) or 0),
        "comment": o.get("comment", ""),
        "payment_method": o.get("payment_method", ""),
        "timestamp": o.get("timestamp", ""),
        "confirmed_at": o.get("confirmed_at", ""),
        "deliver_by": o.get("deliver_by", ""),
        "edit_request": o.get("edit_request") or None,
    }


@require_driver
async def handle_ping(request):
    me = request["driver"]
    return web.json_response({
        "ok": True,
        "driver": {k: me[k] for k in ("id", "name", "district", "district_code",
                                      "district_name", "operator")},
        "day": _biz_day(),
    }, headers=CORS_HEADERS)


@require_driver
async def handle_orders(request):
    """Заказы, назначенные мне. В работе — сверху, доставленные за смену — ниже,
    чтобы было видно, что уже закрыто, и не звонить туда второй раз."""
    me = request["driver"]
    day = _biz_day()
    start = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    orders = await db.get_orders_in_range(f(start), f(start + timedelta(days=1)))
    mine = [o for o in orders if (o.get("driver") or "").strip() == me["name"]]
    active = [_order_view(o) for o in mine if o.get("status") == "approved"]
    done = [_order_view(o) for o in mine if o.get("status") == "delivered"]
    active.sort(key=lambda x: x.get("confirmed_at") or x.get("timestamp") or "")
    done.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return web.json_response({
        "day": day, "active": active, "done": done,
        "total_aed": sum(x["total"] for x in done),
    }, headers=CORS_HEADERS)


@require_driver
async def handle_history(request):
    """Мои заказы за прошлые дни. Водителю это нужно не из любопытства: спор
    «я это возил» решается списком, а не памятью."""
    me = request["driver"]
    try:
        days = max(1, min(60, int(request.query.get("days", "14"))))
    except ValueError:
        days = 14
    today = _biz_day()
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)
             ).replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    end = datetime.strptime(today, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    orders = await db.get_orders_in_range(f(start), f(end))
    mine = [o for o in orders
            if (o.get("driver") or "").strip() == me["name"] and o.get("status") == "delivered"]

    # По дням: за какой день сколько увёз — так видно и объём, и выходные.
    by_day = {}
    for o in mine:
        dt = o.get("confirmed_at") or o.get("timestamp") or ""
        try:
            d = datetime.fromisoformat(str(dt)).replace(tzinfo=timezone.utc).astimezone(DUBAI_TZ)
            key = _biz_day(d)
        except (ValueError, TypeError):
            key = ""
        g = by_day.setdefault(key, {"day": key, "count": 0, "aed": 0, "orders": []})
        g["count"] += 1
        g["aed"] += int(o.get("total", 0) or 0)
        g["orders"].append(_order_view(o))
    days_list = sorted(by_day.values(), key=lambda x: x["day"], reverse=True)
    for g in days_list:
        g["orders"].sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return web.json_response({
        "days": days_list,
        "total_count": len(mine),
        "total_aed": sum(int(o.get("total", 0) or 0) for o in mine),
    }, headers=CORS_HEADERS)


@require_driver
async def handle_delivered(request):
    """Отметить доставку. Только свой заказ и только из работы — закрыть чужой
    или закрыть дважды нельзя."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, status="delivered", updated_at=now,
                          delivered_by_driver=me["name"], delivered_at=now)
    log.info(f"[driver] {me['name']} доставил #{oid}")
    # Владельцу — тем же событием, что и доставка из бота, чтобы отметка из
    # приложения не выглядела иначе. Но откуда она пришла, сказано прямо:
    # закрыл водитель сам, а не оператор за него.
    try:
        from owner_routes import notify_owners
        sent = await notify_owners("orders.delivered",
            f"✅ *Заказ доставлен #{oid}*\n"
            f"Клиент: {o.get('customer_name','—')}\n"
            f"Сумма: {o.get('total',0)} AED\n"
            f"Отметил водитель: {me['name']} ({me['district_code']})")
        if sent:
            await db.update_order(oid, _delivered_notif_msgs=sent)
    except Exception as e:
        log.warning(f"[driver] уведомление о доставке #{oid}: {e}")

    # Карточка в операторском чате должна перестать звать к действию: заказ
    # закрыт, и оператор не должен звонить клиенту следом за водителем.
    try:
        from api_server import tg_send, OPERATOR_BOT_TOKEN as _tok
        for op_id, mid in (o.get("op_msg_ids") or {}).items():
            try:
                await tg_send(_tok, int(op_id),
                              f"✅ Заказ #{oid} доставлен — отметил {me['name']}",
                              parse_mode="HTML", reply_to_message_id=int(mid))
            except Exception:
                pass
    except Exception as e:
        log.warning(f"[driver] отметка в операторском чате #{oid}: {e}")
    return web.json_response({"ok": True, "order_id": oid}, headers=CORS_HEADERS)


@require_driver
async def handle_catalog(request):
    """Каталог для правки состава — только то, что есть в наличии.

    Цены здесь полные: водитель довозит телефонный заказ, а скидка положена
    только за заказ через приложение."""
    from operator_routes import _load_catalog, _full_price
    items = []
    for p in _load_catalog():
        if not p.get("stock"):
            continue
        pack = bool(p.get("price_24_full"))
        items.append({"id": p.get("id"), "name": p.get("name", ""), "cat": p.get("cat", ""),
                      "price": _full_price(p), "pack": pack,
                      **({"p12": _full_price(p, 12), "p24": _full_price(p, 24)} if pack else {})})
    items.sort(key=lambda x: (x["cat"], x["name"]))
    cats = sorted({x["cat"] for x in items if x["cat"]})
    return web.json_response({"items": items, "cats": cats}, headers=CORS_HEADERS)


def _diff_lines(old_items: list, new_items: list) -> list:
    """Что именно поменялось: убрали, добавили, изменили количество.

    Оператор должен увидеть разницу, а не два списка — сравнивать их глазами
    в чате он не станет."""
    was = {i.get("id"): i for i in (old_items or [])}
    now = {i.get("id"): i for i in (new_items or [])}
    out = []
    for pid, i in now.items():
        prev = was.get(pid)
        q, pq = int(i.get("qty") or 0), int((prev or {}).get("qty") or 0)
        if not prev:
            out.append({"kind": "add", "name": i.get("name", ""), "qty": q})
        elif q != pq:
            out.append({"kind": "qty", "name": i.get("name", ""), "from": pq, "qty": q})
    for pid, i in was.items():
        if pid not in now:
            out.append({"kind": "del", "name": i.get("name", ""), "qty": int(i.get("qty") or 0)})
    return out


@require_driver
async def handle_edit_request(request):
    """Правка от водителя — предложение, а не действие.

    Два вида: изменить состав (items) или сообщить о проблеме (text). Ни то ни
    другое не применяется само: заказ меняет оператор, у него же остаётся цена.
    Водитель на месте видит, чего не хватает, — но решать, что везти и почём, не
    его работа."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    text = str(body.get("text") or "").strip()[:400]
    raw_items = body.get("items")

    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)

    items, diff, total = None, [], None
    if isinstance(raw_items, list):
        from operator_routes import _catalog_by_id, _full_price, _pos_total
        cat = _catalog_by_id()
        items = []
        for it in raw_items:
            p = cat.get(it.get("id"))
            if not p:
                continue
            try:
                qty = max(0, int(it.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            pcs = it.get("pcs")
            price = _full_price(p, pcs)
            items.append({"id": p["id"], "name": p.get("name", ""), "qty": qty,
                          **({"pcs": int(pcs)} if pcs else {}),
                          "price": price, "line_total": price * qty})
        if not items:
            return web.json_response({"error": "empty_items"}, status=400, headers=CORS_HEADERS)
        diff = _diff_lines(o.get("items"), items)
        if not diff and not text:
            return web.json_response({"error": "nothing_changed"}, status=400, headers=CORS_HEADERS)
        total = await _pos_total(items)
    elif not text:
        return web.json_response({"error": "text_or_items_required"}, status=400, headers=CORS_HEADERS)

    req = {"text": text, "items": items, "diff": diff, "total": total,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, edit_request=req)
    log.info(f"[driver] {me['name']} правка #{oid}: "
             f"{len(diff)} изменений, текст: {text[:40]}")

    # Операторам — с кнопками: разбирать чужую просьбу руками по чату никто не
    # станет, а применить её должен именно оператор.
    try:
        from api_server import tg_send, OPERATOR_BOT_TOKEN as _tok
        from operator_routes import OPERATOR_IDS
        import html as _h
        sign = {"add": "+", "del": "−", "qty": "→"}
        lines = "\n".join(
            f"{sign.get(d['kind'],'·')} {_h.escape(d['name'])}"
            + (f" ×{d['qty']}" if d["kind"] != "qty" else f" {d['from']} → {d['qty']}")
            for d in diff) or "—"
        msg = (f"✏️ <b>Водитель просит правку</b>\n"
               f"Заказ #{oid} · {_h.escape(me['name'])} ({me['district_code']})\n")
        if diff:
            msg += (f"\n{lines}\n\n"
                    f"Сумма: {o.get('total', 0)} → <b>{total} AED</b>")
        if text:
            msg += f"\n\n💬 {_h.escape(text)}"
        kb = {"inline_keyboard": [[
            {"text": "✅ Применить", "callback_data": f"drvedit_ok_{oid}"},
            {"text": "🚫 Отклонить",  "callback_data": f"drvedit_no_{oid}"},
        ]]} if items else None
        for op_id in OPERATOR_IDS:
            try:
                await tg_send(_tok, op_id, msg, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                log.warning(f"[driver] правка #{oid} → {op_id}: {e}")
    except Exception as e:
        log.error(f"[driver] уведомление о правке #{oid}: {e}")
    return web.json_response({"ok": True, "edit_request": req}, headers=CORS_HEADERS)


@require_driver
async def handle_expense_add(request):
    """Расход на согласование. Сразу в расходы дня он не попадает: иначе водитель
    сам себе назначал бы траты. Менеджер утверждает в панели «Учёт»."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    try:
        amount = max(0, int(round(float(body.get("amount") or 0))))
    except (TypeError, ValueError):
        amount = 0
    comment = str(body.get("comment") or "").strip()[:200]
    if amount <= 0 or not comment:
        return web.json_response({"error": "amount_and_comment_required"},
                                 status=400, headers=CORS_HEADERS)
    day = _biz_day()
    item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
            "by_driver": me["name"], "status": "pending",
            "at": datetime.now(timezone.utc).isoformat()}
    await db.add_driver_expense(day, me["name"], item)
    log.info(f"[driver] {me['name']} просит {amount} AED — {comment}")
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "expenses.request",
            f"💸 *Расход на согласование*\n"
            f"{me['name']} ({me['district_code']}) — {amount} AED\n"
            f"_{comment}_")
    except Exception as e:
        log.warning(f"[driver] уведомление о расходе: {e}")
    return web.json_response({"ok": True, "item": item}, headers=CORS_HEADERS)


@require_driver
async def handle_expenses(request):
    """Мои расходы за смену — и что из них уже утвердили."""
    me = request["driver"]
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    extras = list(d.get("extras") or [])
    st = lambda x: x.get("status") or "approved"      # старые записи — от менеджера
    return web.json_response({
        "day": day,
        "working": d.get("working"),
        "meal": staff.MEAL_WORKING if d.get("working") is True
                else (staff.MEAL_OFF if d.get("working") is False else 0),
        "extras": extras,
        "pending": sum(x.get("amount", 0) for x in extras if st(x) == "pending"),
        "approved": sum(x.get("amount", 0) for x in extras if st(x) == "approved"),
    }, headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/driver/ping",                    handle_ping,        "GET"),
        ("/api/driver/orders",                  handle_orders,      "GET"),
        ("/api/driver/history",                 handle_history,     "GET"),
        ("/api/driver/catalog",                 handle_catalog,     "GET"),
        ("/api/driver/expenses",                handle_expenses,    "GET"),
        ("/api/driver/expenses",                handle_expense_add, "POST"),
        ("/api/driver/orders/{oid}/delivered",  handle_delivered,   "POST"),
        ("/api/driver/orders/{oid}/edit",       handle_edit_request, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info(f"[driver] routes mounted · водителей с доступом: {len(staff.DRIVER_IDS)}")
