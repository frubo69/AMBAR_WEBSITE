"""
Operator iPad POS — manual phone-in orders (/api/operator/*).

Customers who can't use the mini app call the office; the operator punches the
order into the POS mini-app (operator/index.html). The order enters the SAME
pipeline as app orders — operator bot cards, owner notifications, finance,
stats — but is customer-less:

  • customer_id = 0 (int, key present!) — every customer-send in the operator
    bot is guarded on falsy cid, and 0 survives the int(cid) parses baked into
    callback_data. NEVER store None/missing here.
  • source = "manual", created_by/created_by_name — the audit trail + the 📞
    badge everywhere.
  • status = "approved" at creation, NO eta/deliver_by — the operator taking
    the call IS the acceptor; timing stays verbal.

Auth is self-contained: initData validated against OPERATOR_BOT_TOKEN (the POS
launches from the operator bot), allow-list = OPERATOR_IDS. owner_auth's
installed validator belongs to the OWNER bot and must not be touched.

Mounted from api_server.main() like broadcast_routes. Heavy helpers
(_recompute_order_total_aed, tg_send/tg_edit, notify_new_order) are
lazy-imported inside handlers to avoid circular imports at module load.
"""
import os, json, time, hmac, hashlib, logging, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from functools import wraps

from aiohttp import web

import db
from owner_auth import CORS_HEADERS
from config_offices import DEFAULT_OPERATORS

log = logging.getLogger("operator_pos")

OPERATOR_BOT_TOKEN = os.getenv("OPERATOR_BOT_TOKEN", "")
OPERATOR_IDS = [int(x.strip()) for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip().isdigit()]

from config_offices import OFFICE_NAMES   # офис ≡ район, единый источник правды

# Dispatch structure: район → who takes the calls → who drives it.
# The POS flow is operator → район (his own) → driver (that район's).
# The roster itself lives in config_staff — ambar star reads the same table to
# work out whose orders, tips and delivery times these are.
from config_staff import DISTRICT_STAFF
import config_staff as _staff_mod


def _districts() -> list:
    """Районы с их людьми — как в расписании, но с учётом перестановки.

    Считаем каждый раз, а не один раз при запуске: владелец меняет, кто на
    каком районе, из своего приложения, и POS обязан узнать об этом сразу, а
    не при следующем перезапуске службы."""
    return [
        {"id": s["district"], "name": OFFICE_NAMES.get(s["district"], s["district"]),
         "operator": s["operator"], "drivers": list(s["drivers"])}
        for s in DISTRICT_STAFF
    ]


async def _fresh_districts() -> list:
    try:
        _staff_mod.apply_moves(await db.staff_map_get())
    except Exception as e:
        log.warning(f"[pos] перестановка районов не прочитана: {e}")
    return _districts()


# Оставлено для тех мест, где список нужен без ожидания: состав людей в нём
# может отставать до первого обращения к _fresh_districts().
DISTRICTS = _districts()


def _district(did: str) -> dict | None:
    return next((d for d in _districts() if d["id"] == did), None)


def _people(districts: list) -> list:
    """Кого можно выбрать за планшетом и что каждый видит.

    Районный оператор видит свои районы, старший — все: он принимает откуда
    угодно, и подменять любого из троих — его работа."""
    out, seen = [], set()
    for d in districts:
        n = d["operator"]
        if n not in seen:
            seen.add(n)
            out.append({"name": n, "senior": False, "districts": []})
        next(x for x in out if x["name"] == n)["districts"].append(d["id"])
    for sr in _staff_mod.SENIOR_OPERATORS:
        if sr["name"] in seen:
            next(x for x in out if x["name"] == sr["name"])["senior"] = True
        else:
            out.append({"name": sr["name"], "senior": True,
                        "districts": [d["id"] for d in districts]})
    return out


def _scope(people: list, who: str, districts: list) -> set:
    """Районы, которые видит выбранный за планшетом человек."""
    p = next((x for x in people if x["name"] == who), None)
    if not p:
        return set()
    return {d["id"] for d in districts} if p["senior"] else set(p["districts"])

DUBAI_TZ = timezone(timedelta(hours=4))

# Смена идёт с 12:00 до 06:00, поэтому рабочие сутки считаем от полудня до
# полудня — см. owner_routes._biz_day_start, правило общее для всей системы.
SHIFT_START_HOUR = int(os.getenv("AMBAR_SHIFT_START_HOUR", "12"))


def _biz_date(dt):
    """Дата смены, которой принадлежит момент dt (Дубай)."""
    anchor = dt.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (anchor if dt >= anchor else anchor - timedelta(days=1)).date()

_CATALOG_FILE = Path(__file__).parent / "catalog.json"
_cat_cache = {"mtime": 0.0, "items": []}


# ── auth ─────────────────────────────────────────────────────────────────────
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))


def _validate_operator_init_data(init_data: str) -> dict | None:
    """HMAC-validate Telegram WebApp initData against the OPERATOR bot token.

    Also enforces auth_date freshness — a signature never expires on its own, so
    without this a captured initData is a permanent POS credential."""
    if not OPERATOR_BOT_TOKEN:
        return None
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val = params.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", OPERATOR_BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, hash_val):
            return None
        if INIT_DATA_MAX_AGE > 0:
            try:
                age = time.time() - int(params.get("auth_date", "0"))
            except (TypeError, ValueError):
                return None
            if age > INIT_DATA_MAX_AGE or age < -300:
                log.warning(f"[pos] expired initData rejected (age {int(age)}s)")
                return None
        return json.loads(params.get("user", "{}"))
    except Exception as e:
        log.debug(f"[pos] initData parse error: {e}")
        return None


def require_operator(handler):
    @wraps(handler)                     # без этого обёртка съедает имя и docstring
    async def wrapped(request):
        if request.method == "OPTIONS":
            return web.Response(status=200, headers=CORS_HEADERS)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("tma "):
            return web.json_response({"error": "missing auth"}, status=401, headers=CORS_HEADERS)
        user = _validate_operator_init_data(auth[4:])
        if not user:
            return web.json_response({"error": "invalid initData"}, status=401, headers=CORS_HEADERS)
        uid = user.get("id")
        if uid not in OPERATOR_IDS:
            return web.json_response({"error": "not_operator"}, status=403, headers=CORS_HEADERS)
        request["op_id"] = uid
        request["op_user"] = user
        return await handler(request)
    return wrapped


def _op_name(user: dict) -> str:
    if user.get("username"):
        return "@" + user["username"]
    return (f"{user.get('first_name','')} {user.get('last_name','')}".strip()
            or str(user.get("id", "")))


# (привязки офис→оператор нет: офис ручного заказа = выбранный район)


# ── catalog ──────────────────────────────────────────────────────────────────
# ── цены телефонного заказа ─────────────────────────────────────────────────
# В каталоге две цены. `price` — онлайновая, она на 5% ниже: скидка положена
# только за заказ через приложение. Телефонный заказ идёт по полной цене
# `price_full`, поэтому POS считает ТОЛЬКО по ней и никогда по `price`.
# Пачки пива заданы в каталоге поштучно (price_12_full / price_24_full) —
# формулой они не выводятся, у части позиций свои цены.
def _full_price(p: dict, pcs=None) -> int:
    """Цена одной единицы для телефонного заказа: бутылка или пачка."""
    try:
        pcs = int(pcs)
    except (TypeError, ValueError):
        pcs = 0
    if pcs == 24:
        return int(p.get("price_24_full") or 0)
    if pcs == 12:
        return int(p.get("price_12_full") or p.get("price_full") or 0)
    return int(p.get("price_full") or 0)


async def _pos_total(items: list) -> int:
    """Итог телефонного заказа по полным ценам каталога. Свой пересчёт, а не
    api_server._recompute_order_total_aed: тот считает по онлайновым ценам и
    занижал каждый ручной заказ."""
    cat = _catalog_by_id()
    total = 0
    for it in (items or []):
        if it.get("gift"):          # подарок бесплатен
            continue
        try:
            qty = int(it.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        p = cat.get(it.get("id"))
        if p:
            total += _full_price(p, it.get("pcs")) * qty
        else:
            # Позиции нет в каталоге — доверяем строке заказа, иначе потеряем сумму.
            try:
                total += int(float(it.get("line_total", 0) or 0))
            except (TypeError, ValueError):
                pass
    return total


def _load_catalog() -> list:
    try:
        mtime = _CATALOG_FILE.stat().st_mtime
        if mtime != _cat_cache["mtime"]:
            _cat_cache["items"] = json.loads(_CATALOG_FILE.read_text())
            _cat_cache["mtime"] = mtime
    except Exception as e:
        log.error(f"[pos] catalog load failed: {e}")
    return _cat_cache["items"]


def _catalog_by_id() -> dict:
    return {p.get("id"): p for p in _load_catalog()}


# ── manual-order helpers ─────────────────────────────────────────────────────
def _esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


def _new_oid() -> str:
    return "AMB" + str(int(time.time() * 1000))[-7:]


def _build_items(raw_items: list) -> tuple[list, str]:
    """Normalize POS ticket lines into order items enriched from the catalog.
    Returns (items, error). Beer lines carry pcs (12/24) and pack pricing."""
    by_id = _catalog_by_id()
    items = []
    for line in raw_items or []:
        pid = str(line.get("id", ""))
        p = by_id.get(pid)
        if not p:
            return [], f"unknown product: {pid}"
        try:
            qty = int(line.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0 or qty > 99:
            return [], f"bad qty for {pid}"
        if not p.get("stock", True):
            return [], f"out of stock: {p.get('name', pid)}"
        is_beer = p.get("cat") == "Пиво"
        pcs = None
        if is_beer:
            pcs = 24 if str(line.get("pcs", "")) == "24" else 12
            unit = _full_price(p, pcs)
            name = f"{p.get('name','')} ×{pcs}"
        else:
            unit = _full_price(p)
            name = p.get("name", "")
        item = {"id": pid, "name": name, "price": unit, "qty": qty,
                "line_total": unit * qty}
        if pcs:
            item["pcs"] = pcs
        items.append(item)
    if not items:
        return [], "empty order"
    return items, ""


def _item_lines(items: list) -> str:
    return "\n".join(
        f"  • {i['name']} ×{i['qty']} = {i.get('line_total', i['price'] * i['qty'])} AED"
        for i in items
    )


def _card_html(order: dict) -> str:
    """Operator-bot card for a manual order (HTML). Status-aware header."""
    st = order.get("status", "approved")
    head = {"approved":  "🚗 <b>В ПУТИ</b>",
            "delivered": "✅ <b>ДОСТАВЛЕН</b>",
            "cancelled": "🚫 <b>ОТМЕНЁН</b>"}.get(st, st)
    lines = [
        f"<blockquote>📞 <b>РУЧНОЙ ЗАКАЗ — принят по телефону</b>\n"
        f"Оператор: {_esc(order.get('created_by_name','—'))}</blockquote>",
        "",
        f"{head} · <b>#{order['order_id']}</b>",
        "",
        f"🏢 Офис: <b>{_esc(order.get('office_name','—'))}</b>",
        (f"📍 Район: <b>{_esc(order.get('district','—'))}</b> · Оператор: {_esc(order.get('dispatch_operator','—'))}\n"
         f"🚗 Водитель: <b>{_esc(order.get('driver','—'))}</b>" if order.get("district") else ""),
        f"👤 {_esc(order.get('customer_name') or '—')}"
        + (f" · 📱 {_esc(order.get('phone'))}" if order.get("phone") else ""),
        f"🏠 Адрес: {_esc(order.get('address') or '—')}",
        "",
        f"🛒 <b>Позиции:</b>\n{_esc_lines(order.get('items', []))}",
        f"\n💰 <b>Итого: {order.get('total', 0)} AED</b>",
    ]
    if order.get("comment"):
        lines.append(f"\n💬 <b>Комментарий:</b> {_esc(order.get('comment'))}")
    return "\n".join(lines)


def _esc_lines(items: list) -> str:
    return "\n".join(
        f"  • {_esc(i.get('name',''))} ×{i.get('qty',1)} = {i.get('line_total', 0)} AED"
        for i in items
    )


def _card_kb(order: dict) -> dict | None:
    """Approved manual orders keep the live buttons (cid=0 is parse-safe);
    terminal states get «✅ Просмотрено» — the operator bot's existing delmsg
    handler deletes the card to keep the chat clean."""
    if order.get("status") != "approved":
        return {"inline_keyboard": [
            [{"text": "✅ Просмотрено", "callback_data": "delmsg"}],
        ]}
    oid = order["order_id"]
    return {"inline_keyboard": [
        [{"text": "✅ Доставлен", "callback_data": f"done_{oid}_0"},
         {"text": "🚫 Отменить",  "callback_data": f"opcancel_{oid}_0"}],
        [{"text": "✏️ Редактировать", "callback_data": f"edit_{oid}"},
         {"text": "👤 Клиент",        "callback_data": f"client_{oid}_0"}],
    ]}


async def _fanout_new(order: dict) -> dict:
    """Send the manual-order card to every operator; returns op_msg_ids."""
    from api_server import tg_send   # lazy: avoid circular import at load
    text = _card_html(order)
    kb = _card_kb(order)
    op_msg_ids = {}
    for op_id in OPERATOR_IDS:
        try:
            resp = await tg_send(OPERATOR_BOT_TOKEN, op_id, text,
                                 parse_mode="HTML", reply_markup=kb)
            if resp and resp.get("ok") and resp.get("result"):
                op_msg_ids[str(op_id)] = resp["result"]["message_id"]
            else:
                log.error(f"[pos] op notify {op_id} REJECTED for #{order['order_id']}: {resp}")
        except Exception as e:
            log.error(f"[pos] op notify {op_id}: {e}")
    return op_msg_ids


async def notify_driver(order: dict, kind: str = "new"):
    """Заказ водителю — текстом в его бот.

    Приложение показывает список, но водитель за рулём и в приложение не смотрит:
    заказ должен прийти сам, как сообщение, со всем нужным — адресом, составом и
    суммой.

    Телефона клиента здесь нет и быть не должно: сообщение пересылается, лежит в
    чате и переживает смену водителя. Нужно позвонить — звонит оператор.

    Если водителя нет в списке доступа, молча выходим: заказ не должен падать
    из-за того, что человеку ещё не выдали приложение."""
    import os as _os
    import html as _h
    from api_server import tg_send            # lazy: circular import at load
    import config_staff as _staff

    name = (order.get("driver") or "").strip()
    tid = _staff.DRIVER_IDS.get(name)
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return

    lines = "\n".join(
        f"• {_h.escape(str(i.get('name','')))} × {i.get('qty',0)}"
        + (f" ({i.get('pcs')} шт)" if i.get("pcs") else "")
        for i in (order.get("items") or []))
    head = {"new": "🚗 <b>НОВЫЙ ЗАКАЗ</b>",
            "edit": "✏️ <b>ЗАКАЗ ИЗМЕНЁН</b>",
            "cancel": "🚫 <b>ЗАКАЗ ОТМЕНЁН</b>"}.get(kind, "🚗 <b>ЗАКАЗ</b>")
    txt = (f"{head} #{_h.escape(order.get('order_id',''))}\n"
           f"{_h.escape(order.get('district') or order.get('office_name',''))}\n\n"
           f"📍 {_h.escape(order.get('address','') or 'адрес не указан')}\n\n"
           f"{lines}\n\n"
           f"💰 <b>{order.get('total',0)} AED</b>")
    if order.get("payment_method") == "debt":
        txt += "\n☑️ В ДОЛГ — наличные не брать"
    if order.get("comment"):
        txt += f"\n\n💬 {_h.escape(order['comment'])}"

    try:
        await tg_send(token, tid, txt, parse_mode="HTML")
        log.info(f"[driver-bot] #{order.get('order_id')} → {name}")
    except Exception as e:
        log.warning(f"[driver-bot] #{order.get('order_id')} → {name}: {e}")


async def _refresh_cards(order: dict):
    """Re-render the op cards after edit/cancel/delivered."""
    from api_server import tg_edit   # lazy
    text = _card_html(order)
    kb = _card_kb(order)
    for op_id_str, msg_id in (order.get("op_msg_ids") or {}).items():
        try:
            await tg_edit(OPERATOR_BOT_TOKEN, int(op_id_str), msg_id, text,
                          parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            log.debug(f"[pos] card refresh {op_id_str}: {e}")


def _items_sig(items: list) -> str:
    return "|".join(
        f"{i.get('id')}:{int(i.get('qty', 0) or 0)}:{i.get('price', 0)}"
        for i in sorted(items or [], key=lambda x: str(x.get("id")))
    )


def _summary(o: dict) -> dict:
    return {
        "order_id": o.get("order_id"),
        "status": o.get("status"),
        "customer_name": o.get("customer_name", "—"),
        "phone": o.get("phone", "—"),
        "address": o.get("address", ""),
        "comment": o.get("comment", ""),
        "office_id": o.get("office_id", ""),
        "office_name": o.get("office_name", ""),
        "district_id": o.get("district_id", ""),
        "district": o.get("district", ""),
        "dispatch_operator": o.get("dispatch_operator", ""),
        "driver": o.get("driver", ""),
        "total": o.get("total", 0),
        "items": [{"id": i.get("id"), "name": i.get("name"), "qty": i.get("qty", 1),
                   "price": i.get("price", 0), "pcs": i.get("pcs")}
                  for i in (o.get("items") or [])],
        "created_by_name": o.get("created_by_name", ""),
        "operator_name": o.get("operator_name", ""),
        # Источник помечаем явно. У старых онлайн-заказов поля нет вовсе, и
        # «онлайн» до сих пор означало «не помечен телефонным» — так работать
        # можно ровно пока каналов два.
        "source": o.get("source") or "app",
        "deliver_by": o.get("deliver_by", ""),
        "eta": o.get("eta", 0),
        "payment_method": o.get("payment_method", ""),
        "prepaid": bool(o.get("prepaid")),
        "lang": o.get("lang", "ru"),
        "timestamp": o.get("timestamp", ""),
    }


# ── handlers ─────────────────────────────────────────────────────────────────
@require_operator
async def handle_ping(request):
    return web.json_response({
        "ok": True,
        "operator": _op_name(request["op_user"]),
        # Офис ≡ район, отдельного переключателя офиса в POS больше нет —
        # пустой список прячет его в интерфейсе.
        "offices": [],
        "districts": (_d := await _fresh_districts()),
        # Кто может встать за планшет. Список решает и то, что человек увидит:
        # выбрал себя — видишь свои районы, выбрал старшего — все.
        "people": _people(_d),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }, headers=CORS_HEADERS)


@require_operator
async def handle_catalog(request):
    items = []
    for p in _load_catalog():
        base = _full_price(p)          # полная цена, без онлайн-скидки
        is_beer = p.get("cat") == "Пиво"
        row = {
            "id": p.get("id"), "cat": p.get("cat", ""), "name": p.get("name", ""),
            "price": base, "stock": bool(p.get("stock", True)), "isBeer": is_beer,
            "img": p.get("img", ""),   # same hosted images the customer app shows
        }
        if is_beer:
            row["pack12"] = _full_price(p, 12)
            row["pack24"] = _full_price(p, 24)
        items.append(row)
    cats = {}
    for r in items:
        cats[r["cat"]] = cats.get(r["cat"], 0) + 1
    return web.json_response({
        "items": items,
        "categories": [{"cat": c, "count": n} for c, n in cats.items()],
    }, headers=CORS_HEADERS)


@require_operator
async def handle_create(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    # Client details are OPTIONAL — the operator may not have them on the call.
    name = str(body.get("customer_name", "")).strip()
    phone = str(body.get("phone", "")).strip()
    addr = str(body.get("address", "")).strip()
    if phone:
        phone = "+" + phone.lstrip("+")

    # Dispatch is required — район (carries its оператор) + which of its drivers takes it.
    dist = _district(str(body.get("district_id", "")).strip())
    driver = str(body.get("driver", "")).strip()
    if not dist:
        return web.json_response({"error": "district_required"}, status=400, headers=CORS_HEADERS)
    if driver not in dist["drivers"]:
        return web.json_response({"error": "driver_required"}, status=400, headers=CORS_HEADERS)

    items, err = _build_items(body.get("items"))
    if err:
        return web.json_response({"error": err}, status=400, headers=CORS_HEADERS)

    uid = request["op_id"]
    # Офис ≡ район: ручной заказ приписывается тому району, который выбрал
    # оператор. Отдельного выбора офиса больше нет.
    office_id = dist["id"]

    # Authoritative total from the catalog (never trust the iPad's math).
    total = await _pos_total(items)
    # Подарка по телефону НЕТ: акция существует ради перехода в приложение, и
    # если она достаётся и голосом, переходить незачем. Оператор кладёт вино
    # только руками — и тогда это его решение, а не автоматика.

    now = datetime.now(timezone.utc).isoformat()
    op_display = _op_name(request["op_user"])
    order = {
        "order_id": _new_oid(),
        "customer_id": 0,                       # int + present — see module docstring
        "customer_name": name or "—",
        "username": "—",
        "phone": phone,
        "address": addr,
        "location": {}, "gmap_link": "", "is_gps": False,
        "items": items,
        "item_lines": _item_lines(items),
        "tip": 0, "total": total, "lang": "ru",
        "office_id": office_id,
        "office_name": OFFICE_NAMES.get(office_id, office_id),
        "comment": str(body.get("comment", "")).strip(),
        # dispatch
        "district_id": dist["id"],
        "district": dist["name"],
        "dispatch_operator": dist["operator"],
        "driver": driver,
        # Accepted at creation — the operator on the phone IS the acceptor.
        # Deliberately NO eta / deliver_by: timing stays verbal (owner's call).
        "status": "approved",
        "confirmed_at": now,
        "operator_id": uid,
        "source": "manual",
        "created_by": uid,
        "created_by_name": op_display,
        "timestamp": now,
    }
    await db.save_order(order["order_id"], order)

    op_msg_ids = await _fanout_new(order)
    await notify_driver(order, "new")
    if op_msg_ids:
        await db.update_order(order["order_id"], op_msg_ids=op_msg_ids)
        order["op_msg_ids"] = op_msg_ids

    # Owner notification — same tier pipeline as app orders, 📞 header.
    try:
        from owner_routes import notify_new_order
        from api_server import _FOUNDER_ID, _PREMIUM_IDS, _WORLDWIDE_IDS
        await notify_new_order(order["order_id"], total, order["customer_name"],
                               phone, order["address"] or "—", order["office_name"],
                               0, _FOUNDER_ID, _PREMIUM_IDS, _WORLDWIDE_IDS,
                               items=items,
                               manual={"operator": op_display,
                                       "district": dist["name"],
                                       "dispatch_operator": dist["operator"],
                                       "driver": driver})
    except Exception as e:
        log.error(f"[pos] owner notify failed: {e}")

    log.info(f"[pos] manual order #{order['order_id']} by {uid} "
             f"office={office_id} total={total}")
    return web.json_response({"order_id": order["order_id"], "total": total},
                             headers=CORS_HEADERS)


async def _get_pos_order(oid: str):
    """Заказ, которым панель вправе управлять.

    Раньше это были только телефонные. Теперь и онлайн: после принятия разницы
    между ними нет — тот же адрес, тот же водитель, то же время."""
    order = await db.get_order(oid)
    return order or None


@require_operator
async def handle_list(request):
    """Today's (Dubai) manual orders. Привязки офис→оператор пока нет —
    оператор видит все ручные заказы за сегодня."""
    uid = request["op_id"]
    # «Сегодня» для оператора — его смена (12:00→12:00), а не календарные сутки:
    # заказ, принятый в 02:00, всё ещё относится к текущей смене.
    today = _biz_date(datetime.now(DUBAI_TZ))
    out = []
    all_orders = await db.get_all_orders()
    for o in all_orders.values():
        if o.get("source") != "manual":
            continue
        try:
            ts = datetime.fromisoformat(o.get("timestamp", "")).replace(
                tzinfo=timezone.utc).astimezone(DUBAI_TZ)
        except (ValueError, TypeError):
            continue
        if _biz_date(ts) != today:
            continue
        out.append(_summary(o))
    out.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return web.json_response({"orders": out}, headers=CORS_HEADERS)


async def _customer_card(oid: str):
    """Перерисовать карточку заказа у клиента.

    Тем же текстом, что рисует бот: карточка живёт в customer_card.py как раз
    затем, чтобы два процесса не рассказывали клиенту разное."""
    from api_server import tg_edit, tg_send, BOT_TOKEN
    from customer_card import render_customer_card
    order = await db.get_order(oid)
    cid = (order or {}).get("customer_id")
    if not order or not cid:
        return                                  # телефонный заказ — некому
    text = render_customer_card(order, order.get("lang", "ru"))
    mid = order.get("customer_msg_id") or (order.get("customer_msg_ids") or [None])[0]
    if mid:
        try:
            r = await tg_edit(BOT_TOKEN, cid, mid, text, parse_mode="Markdown")
            if r and r.get("ok"):
                return
        except Exception as e:
            log.debug(f"[pos] карточка клиента {cid}/{mid}: {e}")
    try:
        r = await tg_send(BOT_TOKEN, cid, text, parse_mode="Markdown")
        if r and r.get("ok"):
            await db.update_order(oid, customer_msg_id=r["result"]["message_id"])
    except Exception as e:
        log.warning(f"[pos] клиенту {cid}: {e}")


def _lane(o: dict) -> str:
    """В какую ленту попадает заказ.

    Делим по стадии, а не по источнику: онлайн приходит требующим решения,
    телефонный рождается принятым. Источник — пометка на строке, не отдельный
    экран: после принятия вопрос уже не «откуда», а «что едет и куда»."""
    st = o.get("status", "")
    if st == "pending":
        return "new"
    if st == "approved":
        return "work"
    if st in ("delivered", "cancelled", "declined"):
        return "done"
    return ""


@require_operator
async def handle_queue(request):
    """Очередь панели: новые, в работе и закрытые за смену.

    Кто спрашивает — тот и определяет, что видно: имя выбранного за планшетом
    оператора приходит в запросе. Планшет общий, аккаунт у него один, и по
    аккаунту отличить Умара от Джанабиля нельзя."""
    districts = await _fresh_districts()
    people = _people(districts)
    who = (request.query.get("as") or "").strip()
    scope = _scope(people, who, districts)
    if not scope:
        return web.json_response({"error": "unknown_operator", "people": people},
                                 status=400, headers=CORS_HEADERS)

    today = _biz_date(datetime.now(DUBAI_TZ))
    lanes = {"new": [], "work": [], "done": []}
    counts = {"app": 0, "manual": 0}
    for o in (await db.get_all_orders()).values():
        lane = _lane(o)
        if not lane:
            continue
        # Район заказа определяется адресом ещё при создании; заказы без
        # района видит только старший — иначе они не видны вообще никому.
        oid_dist = o.get("office_id") or ""
        if oid_dist not in scope and not (oid_dist == "" and len(scope) == len(districts)):
            continue
        try:
            ts = datetime.fromisoformat(o.get("timestamp", "")).replace(
                tzinfo=timezone.utc).astimezone(DUBAI_TZ)
        except (ValueError, TypeError):
            continue
        # Новые показываем любого возраста: заказ, висящий с ночи, тем более
        # требует ответа. Закрытые — только за текущую смену.
        if lane != "new" and _biz_date(ts) != today:
            continue
        row = _summary(o)
        lanes[lane].append(row)
        if lane == "work":
            counts["manual" if o.get("source") == "manual" else "app"] += 1
    lanes["new"].sort(key=lambda x: x.get("timestamp", ""))          # старые сверху
    lanes["work"].sort(key=lambda x: x.get("deliver_by", "") or "~")
    lanes["done"].sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return web.json_response({
        "as": who, "senior": next(x["senior"] for x in people if x["name"] == who),
        "districts": [d for d in districts if d["id"] in scope],
        "new": lanes["new"], "work": lanes["work"], "done": lanes["done"],
        "counts": counts,
        "now": datetime.now(timezone.utc).isoformat(),
    }, headers=CORS_HEADERS)


@require_operator
async def handle_accept(request):
    """Принять онлайн-заказ: водитель и время одним действием.

    Отдельного «принять сейчас, назначить потом» нет намеренно — это лишний
    повод забыть, а заказ без водителя не виден никому."""
    oid = request.match_info["oid"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)

    districts = await _fresh_districts()
    people = _people(districts)
    who = (body.get("as") or "").strip()
    if not _scope(people, who, districts):
        return web.json_response({"error": "unknown_operator"}, status=400, headers=CORS_HEADERS)

    order = await db.get_order(oid)
    if not order:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "pending":
        return web.json_response({"error": "already_taken",
                                  "status": order.get("status"),
                                  "by": order.get("operator_name") or "",
                                  "driver": order.get("driver") or ""},
                                 status=409, headers=CORS_HEADERS)

    driver = str(body.get("driver", "")).strip()
    known = {n for d in districts for n in d["drivers"]}
    if driver not in known:
        return web.json_response({"error": "driver_required"}, status=400, headers=CORS_HEADERS)
    try:
        eta = int(body.get("eta") or 0)
    except (TypeError, ValueError):
        eta = 0
    if eta not in (20, 25, 30, 35, 40, 50, 60):
        return web.json_response({"error": "eta_required"}, status=400, headers=CORS_HEADERS)

    now = datetime.now(timezone.utc)
    deliver_by = (datetime.now(DUBAI_TZ) + timedelta(minutes=eta)).strftime("%H:%M")
    got = await db.claim_order(oid, {
        "status": "approved", "eta": eta, "deliver_by": deliver_by,
        "confirmed_at": now.isoformat(), "updated_at": now.isoformat(),
        # Устройство одно на всех, поэтому в заказе живут оба: чей аккаунт
        # принял и кто за ним сидел. Без имени вся статистика троих схлопнется
        # в «Планшет операторов».
        "operator_id": request["op_id"], "operator_name": who,
        "accepted_via": "pos",
        "driver": driver, "driver_assigned_at": now.isoformat(), "assigned_by": who,
    })
    if not got:
        fresh = await db.get_order(oid) or {}
        return web.json_response({"error": "already_taken",
                                  "status": fresh.get("status"),
                                  "by": fresh.get("operator_name") or "",
                                  "driver": fresh.get("driver") or ""},
                                 status=409, headers=CORS_HEADERS)

    await _customer_card(oid)          # клиенту — подтверждение со временем
    await _refresh_cards(got)          # операторам — карточки без «Принять»
    await notify_driver(got, "new")    # водителю — заказ
    log.info(f"[pos] #{oid} принял {who} · водитель {driver} · ETA {eta}")
    return web.json_response({"ok": True, "order": _summary(got)}, headers=CORS_HEADERS)


@require_operator
async def handle_patch(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "approved":
        return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    upd = {}
    items_changed = False
    if "items" in body:
        items, err = _build_items(body.get("items"))
        if err:
            return web.json_response({"error": err}, status=400, headers=CORS_HEADERS)
        total = await _pos_total(items)
        items_changed = _items_sig(items) != _items_sig(order.get("items"))
        upd.update(items=items, item_lines=_item_lines(items),
                   subtotal=total, total=total)
    # dispatch changes (район carries its operator; driver must belong to it)
    if "district_id" in body or "driver" in body:
        dist = _district(str(body.get("district_id", order.get("district_id", ""))).strip())
        driver = str(body.get("driver", order.get("driver", ""))).strip()
        if not dist:
            return web.json_response({"error": "district_required"}, status=400, headers=CORS_HEADERS)
        if driver not in dist["drivers"]:
            return web.json_response({"error": "driver_required"}, status=400, headers=CORS_HEADERS)
        upd.update(district_id=dist["id"], district=dist["name"],
                   dispatch_operator=dist["operator"], driver=driver)
    for f in ("customer_name", "phone", "address", "comment"):
        if f in body:
            v = str(body.get(f, "")).strip()
            if f == "phone" and v:
                v = "+" + v.lstrip("+")
            if f == "customer_name" and not v:
                v = "—"
            upd[f] = v
    if not upd:
        return web.json_response({"error": "nothing to update"}, status=400, headers=CORS_HEADERS)

    await db.update_order(oid, **upd)
    order.update(upd)
    await _refresh_cards(order)
    await notify_driver(order, "edit")     # у водителя на руках прошлая версия

    if items_changed:
        try:
            from owner_routes import notify_owners_force
            _items_txt = "\n".join(f"• {i.get('name','')} ×{i.get('qty',1)}"
                                   for i in order.get("items", [])) or "—"
            await notify_owners_force(
                "orders.edited",
                f"✏️ *Заказ изменён #{oid}* — оператором {_op_name(request['op_user'])} (📞 ручной)\n"
                f"💰 Новый итог: *{order.get('total', 0)} AED*\n"
                f"🛒 Позиции:\n{_items_txt}")
        except Exception as e:
            log.error(f"[pos] edited notify failed: {e}")
    return web.json_response({"ok": True, "order": _summary(order)}, headers=CORS_HEADERS)


@require_operator
async def handle_cancel(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "approved":
        return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, status="cancelled", cancelled_by="operator",
                          cancelled_at=now, updated_at=now)
    order.update(status="cancelled")
    await notify_driver(order, "cancel")   # иначе водитель повезёт отменённый заказ
    await _refresh_cards(order)
    await _customer_card(oid)              # у телефонного заказа некому — молча выйдет
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "orders.cancelled",
            f"🚫 *Заказ отменён #{oid}*\n"
            f"Оператор: {_op_name(request['op_user'])}\n"
            f"💰 {order.get('total', 0)} AED · {order.get('customer_name','—')}")
    except Exception as e:
        log.error(f"[pos] cancel notify failed: {e}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_operator
async def handle_delivered(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "approved":
        return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, status="delivered", updated_at=now)
    order.update(status="delivered")
    await _refresh_cards(order)
    await _customer_card(oid)
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "orders.delivered",
            f"✅ *Заказ доставлен #{oid}*\n"
            f"💰 {order.get('total', 0)} AED · {order.get('customer_name','—')}")
    except Exception as e:
        log.error(f"[pos] delivered notify failed: {e}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_operator
async def handle_undeliver(request):
    """Вернуть доставленный заказ обратно в доставку — то же, что кнопка
    «🔄 Вернуть в доставку» в боте оператора (operator_bot: undone_*).
    Нужна, когда «Доставлен» нажали по ошибке: заказ иначе уже закрыт и
    попадает в выручку."""
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "delivered":
        return web.json_response({"error": "not_delivered"}, status=409, headers=CORS_HEADERS)

    total = order.get("total", 0)
    cid = order.get("customer_id") or 0
    # У ручного заказа customer_id = 0 и оба вызова ниже безвредно ничего не
    # делают, но заказ мог быть заведён и на реального клиента — тогда счётчики
    # и долг надо откатить ровно так же, как это делает бот.
    try:
        await db._increment_user(cid, orders_done=-1, total_spent=-total)
    except Exception as e:
        log.error(f"[pos] undeliver counters failed for #{oid}: {e}")
    if order.get("payment_method") == "debt" and total and cid:
        try:
            if await db.unclaim_debt_delivery(oid):
                await db.add_debt(cid, -total, order_id=oid, note="delivery undone")
        except Exception as e:
            log.error(f"[pos] debt rollback failed for #{oid}: {e}")

    # Снимаем у владельца уведомление «доставлен» — иначе оно противоречит факту.
    try:
        from api_server import tg_delete
        from owner_routes import OWNER_BOT_TOKEN
        for m in order.get("_delivered_notif_msgs", []) or []:
            await tg_delete(OWNER_BOT_TOKEN, m["chat_id"], m["message_id"])
    except Exception as e:
        log.error(f"[pos] delete delivered msgs failed for #{oid}: {e}")

    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, status="approved", _delivered_notif_msgs=[], updated_at=now)
    order.update(status="approved", _delivered_notif_msgs=[])
    await _refresh_cards(order)

    try:
        from owner_routes import notify_owners_force
        op = _op_name(request["op_user"])
        await notify_owners_force(
            "orders.reverted",
            f"🔄 *Заказ возвращён в доставку #{oid}*\n"
            f"Был «доставлен» — оператор {op} вернул его в активные.\n"
            f"💰 {total} AED · {order.get('customer_name','—')}")
    except Exception as e:
        log.error(f"[pos] reverted notify failed for #{oid}: {e}")
    return web.json_response({"ok": True, "status": "approved"}, headers=CORS_HEADERS)


# ── mounting ─────────────────────────────────────────────────────────────────
def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    """Mount operator POS routes. Called from api_server.main()."""
    r = app.router
    r.add_route("OPTIONS", "/api/operator/ping", _opt)
    r.add_get("/api/operator/ping", handle_ping)
    r.add_route("OPTIONS", "/api/operator/catalog", _opt)
    r.add_get("/api/operator/catalog", handle_catalog)
    r.add_route("OPTIONS", "/api/operator/queue", _opt)
    r.add_get("/api/operator/queue", handle_queue)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/accept", _opt)
    r.add_post("/api/operator/orders/{oid}/accept", handle_accept)
    r.add_route("OPTIONS", "/api/operator/orders", _opt)
    r.add_get("/api/operator/orders", handle_list)
    r.add_post("/api/operator/orders", handle_create)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}", _opt)
    r.add_patch("/api/operator/orders/{oid}", handle_patch)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/cancel", _opt)
    r.add_post("/api/operator/orders/{oid}/cancel", handle_cancel)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/delivered", _opt)
    r.add_post("/api/operator/orders/{oid}/delivered", handle_delivered)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/undeliver", _opt)
    r.add_post("/api/operator/orders/{oid}/undeliver", handle_undeliver)
    log.info("[pos] operator routes mounted")
