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
  • ПОПРОСИТЬ: отметить доставку, изменить состав, отменить заказ, передать
    сообщение. Все четыре — просьбы, а не действия: заказ закрывает, меняет и
    отменяет оператор. Водитель на месте видит, чего не хватает, но решать, что
    везти, почём и считать ли заказ закрытым, — не его работа;
  • записать расход — со статусом «на согласовании», пока менеджер не утвердит.
    В расходы дня такой не попадает: иначе водитель сам себе назначал бы траты.

Просьба живёт на заказе одним полем driver_req: {kind, status, by, at, …}.
Открытая просьба всегда одна — вторая заменяет первую, иначе оператор разбирал
бы очередь из противоречащих друг другу пожеланий.

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
        # Оплаченный криптой заказ водитель обязан видеть до выезда: взять
        # наличные там, где уже заплачено, дороже любой ошибки в интерфейсе.
        "prepaid": bool(o.get("prepaid")),
        "timestamp": o.get("timestamp", ""),
        "confirmed_at": o.get("confirmed_at", ""),
        "delivered_at": o.get("delivered_at", ""),
        "deliver_by": o.get("deliver_by", ""),
        # Сколько минут обещали клиенту: водитель — единственный, кто может в
        # них уложиться, и знать их он должен раньше всех.
        "eta": o.get("eta", 0),
        "driver_ack_at": o.get("driver_ack_at", ""),
        "driver_req": o.get("driver_req") or o.get("edit_request") or None,
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


KIND_TITLE = {
    "delivered": "отметил доставку",
    "cancel":    "просит отменить заказ",
    "edit":      "просит изменить состав",
    "note":      "сообщение по заказу",
    "reassign":  "не может взять заказ",
}


async def _notify_operators(oid: str, me: dict, req: dict, order: dict):
    """Просьба уходит операторам с кнопками решения — разбирать её руками по
    переписке никто не станет."""
    try:
        from api_server import tg_send, OPERATOR_BOT_TOKEN as _tok
        from operator_routes import OPERATOR_IDS
        import html as _h
        icon = {"delivered": "📦", "cancel": "🚫", "edit": "✏️", "note": "💬"}
        msg = (f"{icon.get(req['kind'],'•')} <b>Водитель {KIND_TITLE[req['kind']]}</b>\n"
               f"Заказ #{oid} · {_h.escape(me['name'])} ({me['district_code']})\n"
               f"{_h.escape(order.get('address',''))} · {order.get('total',0)} AED\n")
        if req.get("diff"):
            sign = {"add": "+", "del": "−", "qty": "→"}
            msg += "\n" + "\n".join(
                f"{sign.get(d['kind'],'·')} {_h.escape(d['name'])}"
                + (f" ×{d['qty']}" if d["kind"] != "qty" else f" {d['from']} → {d['qty']}")
                for d in req["diff"]) + f"\n\nСумма: {order.get('total',0)} → <b>{req.get('total')} AED</b>"
        if req.get("text"):
            msg += f"\n\n💬 {_h.escape(req['text'])}"
        ok = {"delivered": "✅ Подтвердить доставку", "cancel": "✅ Отменить заказ",
              "edit": "✅ Применить", "note": "✅ Принято"}[req["kind"]]
        kb = {"inline_keyboard": [[
            {"text": ok,           "callback_data": f"drvreq_ok_{oid}"},
            {"text": "🚫 Отклонить", "callback_data": f"drvreq_no_{oid}"},
        ]]}
        for op_id in OPERATOR_IDS:
            try:
                await tg_send(_tok, op_id, msg, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                log.warning(f"[driver] просьба #{oid} → {op_id}: {e}")
    except Exception as e:
        log.error(f"[driver] уведомление о просьбе #{oid}: {e}")


@require_driver
async def handle_delivered(request):
    """«Доставил» — это пинг оператору, а не закрытие заказа.

    Закрывает заказ оператор: деньги, выручка и спорные ситуации на нём. Но
    видно ему должно быть сразу и явно, что водитель уже отметил."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    req = {"kind": "delivered", "text": "", "items": None, "diff": [], "total": None,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, driver_req=req)
    log.info(f"[driver] {me['name']} отметил доставку #{oid} — ждём оператора")
    await _notify_operators(oid, me, req, o)
    try:
        from owner_routes import notify_owners
        await notify_owners("orders.driver_done",
            f"📦 *Водитель отметил доставку #{oid}*\n"
            f"{me['name']} ({me['district_code']}) · {o.get('total',0)} AED\n"
            f"_Ждёт подтверждения оператора._")
    except Exception as e:
        log.warning(f"[driver] уведомление о доставке #{oid}: {e}")
    return web.json_response({"ok": True, "order_id": oid, "driver_req": req},
                             headers=CORS_HEADERS)


@require_driver
async def handle_ack(request):
    """«Принял, еду» — не решение, а отметка: заказ водителю уже назначен.

    Оператору важно знать, что человек увидел заказ и тронулся, и через сколько.
    Никаких координат: где он в этот момент — не наше дело и в базе этого нет."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    if o.get("driver_ack_at"):
        return web.json_response({"ok": True, "driver_ack_at": o["driver_ack_at"]},
                                 headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, driver_ack_at=now, driver_ack_by=me["name"])
    log.info(f"[driver] {me['name']} принял #{oid}")
    try:
        from operator_routes import _refresh_cards
        await _refresh_cards({**o, "driver_ack_at": now})
    except Exception as e:
        log.warning(f"[driver] обновление карточек #{oid}: {e}")
    return web.json_response({"ok": True, "driver_ack_at": now}, headers=CORS_HEADERS)


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
    """Просьба водителя — предложение, а не действие.

    Виды: изменить состав (items), отменить заказ, просто сообщение. Ни одно не
    применяется само: заказ меняет, отменяет и закрывает оператор, у него же
    остаётся цена."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    text = str(body.get("text") or "").strip()[:400]
    raw_items = body.get("items")
    kind = str(body.get("kind") or "").strip()
    if kind not in ("edit", "cancel", "note", "reassign", ""):
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)

    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)

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
    elif not text and kind not in ("cancel", "reassign"):
        return web.json_response({"error": "text_or_items_required"}, status=400, headers=CORS_HEADERS)

    kind = kind or ("edit" if items else "note")
    req = {"kind": kind, "text": text, "items": items, "diff": diff, "total": total,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, driver_req=req)
    log.info(f"[driver] {me['name']} просьба «{kind}» по #{oid}: "
             f"{len(diff)} изменений, текст: {text[:40]}")
    await _notify_operators(oid, me, req, o)
    return web.json_response({"ok": True, "driver_req": req}, headers=CORS_HEADERS)


# Бензин и мойка — не «прочие расходы», а ежедневная работа машины: они
# заводятся отдельными окнами, а не поиском в списке. Всё остальное — «ещё
# расход» внизу, там комментарий обязателен.
EXPENSE_KINDS = {"fuel": "Бензин", "wash": "Мойка", "other": ""}


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
    kind = str(body.get("kind") or "other").strip()
    if kind not in EXPENSE_KINDS:
        kind = "other"
    comment = str(body.get("comment") or "").strip()[:200] or EXPENSE_KINDS[kind]
    if amount <= 0 or not comment:
        return web.json_response({"error": "amount_and_comment_required"},
                                 status=400, headers=CORS_HEADERS)
    day = _biz_day()
    item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
            "kind": kind, "by_driver": me["name"], "status": "pending",
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

    # Вид расхода у старых записей не проставлен — узнаём его по комментарию,
    # иначе вчерашний бензин уехал бы в «прочее» и водитель завёл бы второй.
    def _kind(x):
        k = x.get("kind")
        if k in EXPENSE_KINDS:
            return k
        c = (x.get("comment") or "").lower()
        if "бензин" in c or "топлив" in c or "fuel" in c: return "fuel"
        if "мойк" in c or "wash" in c:                    return "wash"
        return "other"

    for x in extras:
        x["kind"] = _kind(x)
    live = [x for x in extras if st(x) != "rejected"]
    return web.json_response({
        "day": day,
        "working": d.get("working"),
        "meal": staff.MEAL_WORKING if d.get("working") is True
                else (staff.MEAL_OFF if d.get("working") is False else 0),
        # Ставки — на экран водителю: он должен видеть правило, а не только
        # итог, иначе каждый раз спрашивает, почему сегодня 40, а не 80.
        "meal_rates": {"working": staff.MEAL_WORKING, "off": staff.MEAL_OFF},
        "extras": extras,
        "by_kind": {k: {"sum": sum(x.get("amount", 0) for x in live if x["kind"] == k),
                        "count": sum(1 for x in live if x["kind"] == k)}
                    for k in EXPENSE_KINDS},
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
        ("/api/driver/orders/{oid}/ack",        handle_ack,         "POST"),
        ("/api/driver/orders/{oid}/delivered",  handle_delivered,   "POST"),
        ("/api/driver/orders/{oid}/edit",       handle_edit_request, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info(f"[driver] routes mounted · водителей с доступом: {len(staff.DRIVER_IDS)}")
