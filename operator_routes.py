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

from config_offices import OFFICE_NAMES, OFFICE_CODES   # офис ≡ район, единый источник правды

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
         # Код района отдаём с сервера. Панель считала его по месту в списке, и
         # у оператора с двумя районами Тиком становился B2 вместо B5.
         "code": OFFICE_CODES.get(s["district"], ""),
         "operator": s["operator"], "drivers": list(s["drivers"])}
        for s in DISTRICT_STAFF
    ]


async def _fresh_districts() -> list:
    try:
        _staff_mod.apply_moves(await db.staff_map_get(), await db.driver_map_get())
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


# Пока смена не открыта, заказы не обрабатываются. Смотреть можно всё: запрет
# на чтение никого не дисциплинирует, а мешает всем.
_OPENS = {"day": "", "at": 0.0, "set": set()}


def _opens_drop():
    _OPENS["at"] = 0.0


async def _is_open(district: str) -> bool:
    import time as _t
    day = _biz_date(datetime.now(DUBAI_TZ)).isoformat()
    # День, в который запрет появился, не считается: люди уже работают, и
    # запирать их посреди смены за то, что утром такой кнопки не было, нельзя.
    try:
        if day <= await db.shift_gate_since(day):
            return True
    except Exception as e:
        log.warning(f"[pos] начало запрета не прочитано: {e}")
    if _OPENS["day"] != day or _t.monotonic() - _OPENS["at"] > 20:
        _OPENS.update(day=day, at=_t.monotonic(),
                      set=set((await db.shift_opens_for_day(day)).keys()))
    return district in _OPENS["set"]


def needs_open(handler):
    """Не пускать к действию, пока смена района не открыта."""
    @wraps(handler)
    async def wrapped(request):
        try:
            district = await _district_of(request)
        except Exception as e:
            log.warning(f"[pos] район для проверки смены не определён: {e}")
            district = ""
        if district and not await _is_open(district):
            return web.json_response({"error": "shift_not_open", "district": district},
                                     status=409, headers=CORS_HEADERS)
        return await handler(request)
    return wrapped


async def _district_of(request) -> str:
    """Район действия: у заказа — свой, у создания — из тела, иначе по человеку."""
    oid = (request.match_info.get("oid") or "").strip()
    if oid:
        o = await db.get_order(oid)
        return (o or {}).get("office_id") or ""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    d = str(body.get("district") or body.get("district_id")
            or body.get("office_id") or "").strip()
    if d:
        return d
    districts = await _fresh_districts()
    scope = _scope(_people(districts), str(body.get("as") or "").strip(), districts)
    return next(iter(scope)) if len(scope) == 1 else ""


_RU_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _day_ru(iso: str) -> str:
    """«2026-08-11» → «11 августа». Пустое — «прошлую смену»."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return f"{d.day} {_RU_MONTHS[d.month - 1]}"
    except (ValueError, TypeError, IndexError):
        return "прошлую смену"


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
    from api_server import tg_send, tg_edit   # lazy: circular import at load
    import config_staff as _staff

    name = (order.get("driver") or "").strip()
    tid = _staff.DRIVER_IDS.get(name)
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return

    # Пока заказ не принят, водителю ехать некуда. Оператор может выбрать его
    # заранее, правя состав, — но это ещё не назначение: назначением заказ
    # становится в момент принятия. Отмену шлём только тому, кому уже говорили
    # везти: остальным сообщать не о чем.
    st = (order.get("status") or "").strip()
    told = bool(order.get("driver_msg_id")) and \
           (order.get("driver_msg_to") or "").strip() == name
    if kind == "cancel":
        if not told:
            return
    elif st != "approved":
        log.debug(f"[driver-bot] #{order.get('order_id')} ещё не принят — водителю молчим")
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

    # Правим то же сообщение, а не шлём новое. Иначе после трёх правок у
    # водителя в чате три версии одного заказа, и какая из них верная — видно
    # только по времени. Новое отправляем, если заказ передали другому: у него
    # этого сообщения ещё нет.
    oid = order.get("order_id", "")
    prev_id = order.get("driver_msg_id")
    prev_to = (order.get("driver_msg_to") or "").strip()
    if prev_id and prev_to == name:
        try:
            r = await tg_edit(token, tid, prev_id, txt, parse_mode="HTML")
            if r and r.get("ok"):
                log.info(f"[driver-bot] #{oid} → {name}: сообщение обновлено")
                return
            # «message is not modified» — тоже успех: у водителя уже то же самое.
            if "not modified" in str((r or {}).get("description", "")).lower():
                return
        except Exception as e:
            log.debug(f"[driver-bot] правка #{oid}: {e}")
    try:
        r = await tg_send(token, tid, txt, parse_mode="HTML")
        if r and r.get("ok") and r.get("result"):
            await db.update_order(oid, driver_msg_id=r["result"]["message_id"],
                                  driver_msg_to=name)
        log.info(f"[driver-bot] #{oid} → {name}")
    except Exception as e:
        log.warning(f"[driver-bot] #{oid} → {name}: {e}")


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
        "customer_id": o.get("customer_id", 0),
        "username": o.get("username", ""),
        "phone_shared": o.get("phone_shared", ""),
        "phone_extra": o.get("phone_extra", ""),
        "gmap_link": o.get("gmap_link", ""),
        "is_gps": bool(o.get("is_gps")),
        "tip": o.get("tip", 0),
        "review_score": o.get("review_score", 0),
        "operator_name": o.get("operator_name", ""),
        # Источник помечаем явно. У старых онлайн-заказов поля нет вовсе, и
        # «онлайн» до сих пор означало «не помечен телефонным» — так работать
        # можно ровно пока каналов два.
        "source": o.get("source") or "app",
        "deliver_by": o.get("deliver_by", ""),
        "eta": o.get("eta", 0),
        "payment_method": o.get("payment_method", ""),
        # Оплачено онлайн: у криптового заказа в базе стоит paid и
        # payment_method, а поля prepaid нет вовсе — читаем все три.
        "prepaid": bool(o.get("prepaid") or o.get("paid")
                        or o.get("payment_method") == "crypto"),
        "lang": o.get("lang", "ru"),
        "timestamp": o.get("timestamp", ""),
        # Просьба водителя едет вместе с заказом: она меняет то, что оператор
        # должен с ним сделать, и прятать её за отдельным запросом нельзя.
        "driver_req": o.get("driver_req") or o.get("edit_request") or None,
        "delivered_by_driver": o.get("delivered_by_driver", ""),
        # Заведён задним числом: такой правится прямо в закрытых, а не через
        # возврат в доставку — в доставке он никогда не был.
        "backfilled": bool(o.get("backfilled")),
        "backfill_day": o.get("backfill_day", ""),
        # Водитель отметил, что увидел заказ и выехал. Координат тут нет и не
        # будет: где он едет — не хранится нигде, чтобы историю перемещений
        # нельзя было ни украсть, ни собрать.
        "driver_ack_at": o.get("driver_ack_at", ""),
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
@needs_open
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

    # Заказ задним числом: смену не заполнили вовремя (приложение лежало, было
    # некогда), и выручка за тот день должна встать на своё место, а не на
    # сегодня. Такой заказ рождается сразу доставленным: он уже случился,
    # принимать и назначать по нему нечего.
    back = str(body.get("back_date", "")).strip()
    back_dt = None
    if back:
        try:
            d = datetime.strptime(back, "%Y-%m-%d").date()
        except ValueError:
            return web.json_response({"error": "bad_date"}, status=400, headers=CORS_HEADERS)
        today = _biz_date(datetime.now(DUBAI_TZ))
        if d > today:
            return web.json_response({"error": "future_date"}, status=400, headers=CORS_HEADERS)
        # Текущая смена ещё идёт — «задним числом» её не заполняют. Такой заказ
        # проходит обычным путём, и закрывает его кнопка «Доставлен».
        if d == today:
            back = ""
            d = None
        if (today - d).days > 30:
            return web.json_response({"error": "too_old"}, status=400, headers=CORS_HEADERS)
        if d:
            # Ставим вечернее время той смены: минуты берём с текущих часов, чтобы
            # несколько заказов подряд не слиплись в одну секунду, а час — 20:00,
            # он гарантированно внутри той же смены (она начинается в 12:00).
            n = datetime.now(DUBAI_TZ)
            back_dt = datetime(d.year, d.month, d.day, 20, n.minute, n.second,
                               tzinfo=DUBAI_TZ).astimezone(timezone.utc)

    now = (back_dt or datetime.now(timezone.utc)).isoformat()
    entered_at = datetime.now(timezone.utc).isoformat()
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
        # Задним числом — сразу доставлен: заказ уже состоялся.
        "status": "delivered" if back_dt else "approved",
        "confirmed_at": now,
        "operator_id": uid,
        "source": "manual",
        "created_by": uid,
        "created_by_name": op_display,
        "timestamp": now,
        **({"delivered_at": now, "delivered_by": op_display,
            # След того, что заказ внесён позже: деньги, попавшие в отчёт задним
            # числом, обязаны быть отличимы от обычных.
            "backfilled": True, "backfill_day": back, "backfill_at": entered_at}
           if back_dt else {}),
    }
    await db.save_order(order["order_id"], order)

    if back_dt:
        # Ни карточек операторам, ни сообщения водителю: решать по этому заказу
        # нечего, везти — тем более. Владельцу — отдельным событием.
        try:
            from owner_routes import notify_owners_force
            d_h, d_m = back[8:10], back[5:7]
            _it = "\n".join(f"• {i.get('name','')} ×{i.get('qty',1)}" for i in items) or "—"
            await notify_owners_force(
                "orders.backfilled",
                f"📅 *Заказ внесён задним числом*\n"
                f"За {d_h}.{d_m} · #{order['order_id']}\n"
                f"Внёс: {op_display} · район {dist['name']} · водитель {driver}\n"
                f"💰 *{total} AED* — уже числится доставленным\n"
                f"🛒 Позиции:\n{_it}")
        except Exception as e:
            log.error(f"[pos] backfill notify failed: {e}")
        log.info(f"[pos] заказ задним числом #{order['order_id']} за {back} "
                 f"({op_display}, {total} AED)")
        return web.json_response({"order_id": order["order_id"], "total": total,
                                  "back_date": back}, headers=CORS_HEADERS)

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


async def _needs_verify(order: dict) -> bool:
    """Ждёт ли этот заказ решения по верификации.

    Правило одно на всю систему и живёт в api_server: человек проверен, если
    верифицирован формально ЛИБО уже получал заказ — курьер видел его в лицо.
    Пока решения нет, заказ принимать нельзя, и панель обязана вести себя так
    же, как бот: там на непроверенном клиенте вместо «Принять» стоят
    «Верифицировать» и «Не верифицировать».
    """
    cid = int(order.get("customer_id") or 0)
    if not cid:
        return False                      # телефонный заказ — верифицировать некого
    from api_server import _is_vetted
    return not _is_vetted(await db.get_user(cid))


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
    # Оператор может стоять на прошлой смене — тогда «за день» показывает её,
    # а не сегодняшнюю. Новые и в работе остаются живыми в любом случае:
    # заказ, требующий ответа сейчас, не должен исчезать оттого, что человек
    # заполняет позавчерашний день.
    day = today
    ask = (request.query.get("day") or "").strip()
    if ask:
        try:
            day = datetime.strptime(ask, "%Y-%m-%d").date()
        except ValueError:
            day = today
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
        # требует ответа. В работе — всегда текущие. Закрытые — за выбранный день.
        if lane == "work" and _biz_date(ts) != today:
            continue
        if lane == "done" and _biz_date(ts) != day:
            continue
        row = _summary(o)
        if lane == "new":
            row["needs_verify"] = await _needs_verify(o)
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
        "counts": counts, "day": day.isoformat(),
        "now": datetime.now(timezone.utc).isoformat(),
    }, headers=CORS_HEADERS)


@require_operator
@needs_open
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
    # Сервер держит те же ворота, что и экран: кнопку можно не показать, но
    # запрос всё равно придёт — со старой вкладки, с чужого устройства, откуда
    # угодно.
    if await _needs_verify(order):
        return web.json_response({"error": "needs_verify"}, status=409, headers=CORS_HEADERS)
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
async def handle_customer(request):
    """Карточка клиента — то же, что «Клиент» в боте.

    Оператор решает не по одной строке адреса: сколько раз человек заказывал,
    сколько отменил, верифицирован ли и нет ли на нём заметки. Без этого
    принять заказ можно только вслепую."""
    try:
        cid = int(request.match_info["cid"])
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_id"}, status=400, headers=CORS_HEADERS)
    u = await db.get_user(cid) or {}
    orders = [o for o in (await db.get_all_orders()).values()
              if int(o.get("customer_id") or 0) == cid]
    orders.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return web.json_response({
        "customer_id": cid,
        "name": u.get("full_name") or u.get("name") or "—",
        "note": u.get("custom_name", ""),
        "username": u.get("username", ""),
        "phone_verified": u.get("phone_verified", ""),
        "verified": bool(u.get("verified")),
        "verify_source": u.get("verify_source", ""),
        "verify_recommender_name": u.get("verify_recommender_name", ""),
        "banned": bool(u.get("banned")),
        "ban_reason": u.get("ban_reason", ""),
        "first_seen": str(u.get("first_seen") or ""),
        "orders_total": u.get("orders_total", 0),
        "orders_done": u.get("orders_done", 0),
        "orders_declined": u.get("orders_declined", 0),
        "total_spent": u.get("total_spent", 0),
        "invited_via": u.get("invited_via", ""),
        "last": [{"order_id": o.get("order_id"), "status": o.get("status"),
                  "total": o.get("total", 0), "timestamp": o.get("timestamp", ""),
                  "address": o.get("address", "")} for o in orders[:8]],
    }, headers=CORS_HEADERS)


@require_operator
async def handle_customer_act(request):
    """Действия по клиенту: верификация, заметка, блокировка.

    Те же три, что в боте. Разблокировка тоже здесь: заблокировать по ошибке
    легко, а искать потом, где это отменить, — отдельное мучение."""
    try:
        cid = int(request.match_info["cid"])
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_id"}, status=400, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    act = (body.get("act") or "").strip()
    me = request["op_id"]
    if act == "verify":
        await db.verify_user(cid)
        try:
            await db.undecline_verification(cid)
        except Exception:
            pass
    elif act == "note":
        await db.set_user_field(cid, custom_name=str(body.get("note", "")).strip())
    elif act == "ban":
        await db.ban_user(cid, str(body.get("reason", "")).strip() or "без причины", me)
    elif act == "decline_verify":
        # Как в боте: отказ по человеку закрывает и его неотвеченные заказы —
        # иначе они остались бы висеть в очереди навсегда.
        await db.decline_verification(cid)
        if body.get("reason"):
            await db.set_user_field(cid, verify_decline_reason=str(body["reason"]).strip())
        now = datetime.now(timezone.utc).isoformat()
        for po in await db.get_pending_orders_for_user(cid):
            poid = po.get("order_id")
            if not poid:
                continue
            await db.update_order(poid, status="declined", updated_at=now,
                                  declined_at=now, decline_reason="верификация не пройдена")
            try:
                await db._increment_user(cid, orders_declined=1)
            except Exception:
                pass
            await _customer_card(poid)
            await _refresh_cards(await db.get_order(poid) or {})
    elif act == "unban":
        await db.set_user_field(cid, banned=False, ban_reason="")
    else:
        return web.json_response({"error": "unknown_act"}, status=400, headers=CORS_HEADERS)
    log.info(f"[pos] клиент {cid}: {act} (оператор {me})")
    request.match_info["cid"] = str(cid)
    return await handle_customer(request)


@require_operator
@needs_open
async def handle_decline(request):
    """Отклонить новый заказ — то же, что «Отклонить» в боте."""
    oid = request.match_info["oid"]
    order = await db.get_order(oid)
    if not order:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "pending":
        return web.json_response({"error": "already_taken", "status": order.get("status"),
                                  "by": order.get("operator_name") or ""},
                                 status=409, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    now = datetime.now(timezone.utc).isoformat()
    got = await db.claim_order(oid, {
        "status": "declined", "updated_at": now, "declined_at": now,
        "operator_id": request["op_id"], "operator_name": (body.get("as") or "").strip(),
        "decline_reason": str(body.get("reason", "")).strip(),
    })
    if not got:
        fresh = await db.get_order(oid) or {}
        return web.json_response({"error": "already_taken", "status": fresh.get("status"),
                                  "by": fresh.get("operator_name") or ""},
                                 status=409, headers=CORS_HEADERS)
    await _customer_card(oid)
    await _refresh_cards(got)
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_operator
async def handle_one(request):
    """Один заказ целиком — для карточки в панели."""
    order = await db.get_order(request.match_info["oid"])
    if not order:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    row = _summary(order)
    row["needs_verify"] = await _needs_verify(order)
    # Разговор кладём только в открытый заказ: в списке он не нужен, а весит
    # больше всей остальной карточки.
    row["chat"] = [{"by": m.get("by", ""), "name": m.get("name", ""),
                    "text": m.get("text", ""), "at": str(m.get("at") or ""),
                    "kind": m.get("kind", "")} for m in (order.get("chat") or [])]
    # Открыли карточку — значит прочитали.
    if _chat_new(order, "operator"):
        await db.order_chat_seen(row["order_id"], "operator",
                                 datetime.now(timezone.utc).isoformat())
    return web.json_response({"order": row}, headers=CORS_HEADERS)


def _chat_new(o: dict, side: str) -> int:
    seen = str(o.get(f"chat_seen_{side}") or "")
    return sum(1 for m in (o.get("chat") or [])
               if m.get("by") != side and str(m.get("at") or "") > seen)


@require_operator
async def handle_driver_chats(request):
    """Разговоры с водителями — второй лист в «Поддержке».

    Клиенты и водители лежат рядом не случайно: и там и там оператор отвечает
    на вопрос, и вопрос ждёт. Разница в том, что клиент ждёт у телефона, а
    водитель — у чужой двери, поэтому его непрочитанное всегда сверху."""
    districts = await _fresh_districts()
    people = _people(districts)
    who = (request.query.get("as") or "").strip()
    scope = _scope(people, who, districts)
    rows = []
    for o in (await db.get_all_orders()).values():
        chat = o.get("chat") or []
        if not chat:
            continue
        dist = o.get("office_id") or ""
        if scope and dist not in scope and not (dist == "" and len(scope) == len(districts)):
            continue
        last = chat[-1]
        rows.append({
            "order_id": o.get("order_id", ""), "driver": o.get("driver", ""),
            "address": o.get("address", ""), "status": o.get("status", ""),
            "district": _code_of(dist, districts),
            "n": len(chat), "unread": _chat_new(o, "operator"),
            "last": last.get("text", ""), "last_by": last.get("by", ""),
            "last_at": str(last.get("at") or ""),
            "kind": next((m.get("kind") for m in reversed(chat) if m.get("kind")), ""),
            "live": o.get("status") == "approved",
        })
    # Сверху непрочитанное, потом живые заказы, потом по свежести.
    rows.sort(key=lambda r: (not r["unread"], not r["live"], r["last_at"]), reverse=False)
    rows.sort(key=lambda r: (bool(r["unread"]), r["live"], r["last_at"]), reverse=True)
    return web.json_response({"chats": rows[:60],
                              "unread": sum(1 for r in rows if r["unread"])},
                             headers=CORS_HEADERS)


@require_operator
async def handle_chat_send(request):
    """Ответ водителю по заказу.

    Оператор до сих пор мог только решить по просьбе — «принять» или
    «отклонить». Половина ситуаций на адресе так не решается: сначала надо
    спросить. Ответ уходит водителю в бот и остаётся на заказе."""
    oid = request.match_info["oid"]
    order = await db.get_order(oid)
    if not order:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or "").strip()[:600]
    if not text:
        return web.json_response({"error": "empty"}, status=400, headers=CORS_HEADERS)
    who = (body.get("as") or "").strip() or _op_name(request["op_user"])
    now = datetime.now(timezone.utc).isoformat()
    doc = await db.order_chat_add(oid, {"by": "operator", "name": who,
                                        "text": text, "at": now, "kind": ""})
    log.info(f"[pos] {who} отвечает по #{oid}: {text[:50]}")
    drv = (order.get("driver") or "").strip()
    if drv:
        await tell_driver(drv, f"💬 <b>Оператор по заказу #{oid}</b>\n{_esc_html(text)}")
    return web.json_response({"ok": True, "chat": [
        {"by": m.get("by", ""), "name": m.get("name", ""), "text": m.get("text", ""),
         "at": str(m.get("at") or ""), "kind": m.get("kind", "")}
        for m in ((doc or order).get("chat") or [])]}, headers=CORS_HEADERS)


def _esc_html(x) -> str:
    return (str(x or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


@require_operator
async def handle_patch(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    # Править можно и непринятый заказ: состав чаще всего и правят до принятия —
    # человек позвонил и попросил добавить бутылку. Проверка на «в пути»
    # осталась с тех пор, когда панель знала только телефонные заказы, а те
    # рождаются принятыми.
    # Заказ, заведённый задним числом, рождается доставленным — вернуть его в
    # доставку нельзя, он там никогда и не был. Значит правится как есть, а
    # AMBAR STAR узнаёт о каждой такой правке: это деньги закрытого дня.
    back = bool(order.get("backfilled"))
    if not back and order.get("status") not in ("pending", "approved"):
        return web.json_response({"error": "order is closed",
                                  "status": order.get("status")},
                                 status=409, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=CORS_HEADERS)

    upd = {}
    items_changed = False
    was_total = order.get("total", 0)
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

    # О правке заказа задним числом сообщаем всегда, даже если поменяли один
    # адрес: день уже закрыт, и владелец должен знать, что в нём что-то трогали.
    if items_changed or back:
        try:
            from owner_routes import notify_owners_force
            _items_txt = "\n".join(f"• {i.get('name','')} ×{i.get('qty',1)}"
                                   for i in order.get("items", [])) or "—"
            who = _op_name(request["op_user"])
            if back:
                day = order.get("backfill_day") or ""
                head = f"✏️ *Заказ за {_day_ru(day)} изменён #{oid}* — оператором {who}"
                money = (f"💰 Итог: *{order.get('total', 0)} AED* (было {was_total})"
                         if order.get("total", 0) != was_total
                         else f"💰 Итог: *{order.get('total', 0)} AED* — без изменений")
                what = ", ".join(k for k in upd if k in
                                 ("items", "address", "comment", "customer_name",
                                  "phone", "district_id", "driver")) or "—"
                await notify_owners_force(
                    "orders.edited",
                    f"{head}\n{money}\n📝 Поправили: {what}\n🛒 Позиции:\n{_items_txt}")
            else:
                await notify_owners_force(
                    "orders.edited",
                    f"✏️ *Заказ изменён #{oid}* — оператором {who} (📞 ручной)\n"
                    f"💰 Новый итог: *{order.get('total', 0)} AED*\n"
                    f"🛒 Позиции:\n{_items_txt}")
        except Exception as e:
            log.error(f"[pos] edited notify failed: {e}")
    return web.json_response({"ok": True, "order": _summary(order)}, headers=CORS_HEADERS)


@require_operator
@needs_open
async def handle_cancel(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "approved":
        return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    who = (body.get("as") or "").strip() or _op_name(request["op_user"])
    req = order.get("driver_req") or {}
    if req.get("kind") == "cancel" and req.get("status") == "open":
        await db.update_order(oid, driver_req={**req, "status": "applied", "decided_by": who,
                                               "decided_at": datetime.now(timezone.utc).isoformat()})
        await tell_driver(req.get("by", ""), f"🚫 Оператор отменил заказ #{oid} по вашей просьбе")
    await _do_cancel(oid, order, who, str(body.get("reason") or "").strip()[:200])
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def tell_driver(name: str, text: str):
    """Короткое сообщение водителю в его бот. Решение оператора должно до него
    доехать: молчание он прочитает как «не заметили».

    Кроме одного случая. Если водитель включил скрытый режим, значит в его
    телефон смотрит кто-то ещё — и всплывший баннер от «AMBAR Водитель» выдаст
    маскировку вернее, чем что угодно на экране. Пока режим не снят, мы молчим:
    оператору об этом сказано в самой тревоге, связываться нужно звонком."""
    import os as _os
    from api_server import tg_send
    import config_staff as _staff
    tid = _staff.DRIVER_IDS.get((name or "").strip())
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return
    try:
        if await db.panic_get((name or "").strip()):
            log.warning(f"[pos] {name} в скрытом режиме — сообщение не отправлено")
            return
    except Exception as e:
        log.warning(f"[pos] проверка скрытого режима: {e}")
    try:
        await tg_send(token, tid, text, parse_mode="HTML")
    except Exception as e:
        log.warning(f"[pos] сообщение водителю {name}: {e}")


async def _close_delivered(oid: str, order: dict, who: str, by_driver: str = ""):
    """Закрыть заказ доставкой. Единственная дверь: и кнопка оператора, и
    подтверждение отметки водителя идут сюда, иначе выручка считалась бы
    по-разному в зависимости от того, кто нажал."""
    now = datetime.now(timezone.utc).isoformat()
    fields = {"status": "delivered", "updated_at": now, "delivered_at": now,
              "delivered_by": who}
    if by_driver:
        fields["delivered_by_driver"] = by_driver
    await db.update_order(oid, **fields)
    order.update(status="delivered")
    await _refresh_cards(order)
    await _customer_card(oid)
    try:
        from owner_routes import notify_owners
        sent = await notify_owners(
            "orders.delivered",
            f"✅ *Заказ доставлен #{oid}*\n"
            f"💰 {order.get('total', 0)} AED · {order.get('customer_name','—')}"
            + (f"\nОтметил водитель {by_driver}, подтвердил {who}" if by_driver else ""))
        # Сохраняем id уведомлений: возврат из доставленных их снимает.
        if sent:
            await db.update_order(oid, _delivered_notif_msgs=sent)
    except Exception as e:
        log.error(f"[pos] delivered notify failed: {e}")


async def _do_cancel(oid: str, order: dict, who: str, reason: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, status="cancelled", cancelled_by=who,
                          cancelled_at=now, updated_at=now,
                          **({"cancel_reason": reason} if reason else {}))
    order.update(status="cancelled")
    await notify_driver(order, "cancel")   # иначе водитель повезёт отменённый заказ
    await _refresh_cards(order)
    await _customer_card(oid)              # у телефонного заказа некому — молча выйдет
    try:
        from owner_routes import notify_owners
        await notify_owners(
            "orders.cancelled",
            f"🚫 *Заказ отменён #{oid}*\n"
            f"Оператор: {who}\n"
            + (f"Причина: {reason}\n" if reason else "")
            + f"💰 {order.get('total', 0)} AED · {order.get('customer_name','—')}")
    except Exception as e:
        log.error(f"[pos] cancel notify failed: {e}")


@require_operator
@needs_open
async def handle_delivered(request):
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    if order.get("status") != "approved":
        return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    who = (body.get("as") or "").strip() or _op_name(request["op_user"])
    req = order.get("driver_req") or {}
    # Водитель уже отмечал доставку — закрываем его же просьбу, чтобы она не
    # висела в «требует внимания» после того, как вопрос решён.
    if req.get("kind") == "delivered" and req.get("status") == "open":
        await db.update_order(oid, driver_req={**req, "status": "applied",
                                               "decided_by": who,
                                               "decided_at": datetime.now(timezone.utc).isoformat()})
        await tell_driver(req.get("by", ""), f"✅ Оператор подтвердил доставку заказа #{oid}")
    await _close_delivered(oid, order, who, req.get("by", "") if req.get("kind") == "delivered" else "")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_operator
@needs_open
async def handle_undeliver(request):
    """Вернуть закрытый заказ обратно в доставку — то же, что кнопка
    «🔄 Вернуть в доставку» в боте оператора (operator_bot: undone_*).

    Возвращаем и «доставлен» (нажали по ошибке — заказ уже попал в выручку), и
    «отменён»: отмену жмут и по звонку водителя, и промахом, а восстановить
    заказ было нечем — оставалось заводить его заново другим номером."""
    oid = request.match_info["oid"]
    order = await _get_pos_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    was = order.get("status")
    if was not in ("delivered", "cancelled"):
        return web.json_response({"error": "not_closed"}, status=409, headers=CORS_HEADERS)

    total = order.get("total", 0)
    cid = order.get("customer_id") or 0
    if was == "cancelled":
        # Отмена ничего не начисляла — откатывать нечего, только вернуть статус
        # и позвать водителя обратно.
        now = datetime.now(timezone.utc).isoformat()
        await db.update_order(oid, status="approved", cancelled_by="", cancelled_at="",
                              updated_at=now)
        order.update(status="approved")
        await notify_driver(order, "new")
        await _refresh_cards(order)
        await _customer_card(oid)
        try:
            from owner_routes import notify_owners_force
            await notify_owners_force(
                "orders.reverted",
                f"🔄 *Отменённый заказ вернули в доставку #{oid}*\n"
                f"Оператор: {_op_name(request['op_user'])}\n"
                f"💰 {total} AED · {order.get('customer_name','—')}")
        except Exception as e:
            log.error(f"[pos] uncancel notify failed for #{oid}: {e}")
        return web.json_response({"ok": True, "status": "approved"}, headers=CORS_HEADERS)
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


# ── лента: что требует внимания и что уже произошло ──────────────────────────
# Смена оператора — это не только очередь. Заказ может висеть непринятым, пока
# все смотрят в другую вкладку; водитель может отметить доставку и ждать
# подтверждения; клиент — задать вопрос. Всё это в одном месте, отсортированное
# по тому, насколько долго ждёт, чтобы ни один заказ не остался без внимания.

def _code_of(dist_id: str, districts: list) -> str:
    return next((d.get("code", "") for d in districts if d["id"] == dist_id), "")


def _mins_since(ts: str) -> int:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - d).total_seconds() // 60))
    except Exception:
        return 0


def _late_by(o: dict) -> int:
    """На сколько минут просрочено обещанное время. 0 — не просрочено."""
    hhmm = (o.get("deliver_by") or "").strip()
    if not hhmm or ":" not in hhmm:
        return 0
    try:
        h, m = (int(x) for x in hhmm.split(":", 1))
    except ValueError:
        return 0
    now = datetime.now(DUBAI_TZ)
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # Смена переходит за полночь: обещанные 00:30 при текущих 23:50 — завтра.
    if (due - now).total_seconds() < -12 * 3600:
        due += timedelta(days=1)
    elif (due - now).total_seconds() > 12 * 3600:
        due -= timedelta(days=1)
    late = int((now - due).total_seconds() // 60)
    return late if late > 0 else 0


DRV_REQ_TITLE = {
    "delivered": "водитель отметил доставку",
    "cancel":    "водитель просит отменить",
    "edit":      "водитель просит правку",
    "note":      "сообщение от водителя",
    "reassign":  "водитель не может взять заказ",
    "chat":      "водитель ждёт ответа",
}


@require_operator
async def handle_feed(request):
    districts = await _fresh_districts()
    people = _people(districts)
    who = (request.query.get("as") or "").strip()
    scope = _scope(people, who, districts)
    if not scope:
        return web.json_response({"error": "unknown_operator", "people": people},
                                 status=400, headers=CORS_HEADERS)
    today = _biz_date(datetime.now(DUBAI_TZ))
    need, recent = [], []

    for o in (await db.get_all_orders()).values():
        oid = o.get("order_id") or ""
        dist = o.get("office_id") or ""
        if dist not in scope and not (dist == "" and len(scope) == len(districts)):
            continue
        st = o.get("status", "")
        req = _req_of(o)
        base = {"order_id": oid, "total": o.get("total", 0),
                "address": o.get("address", ""), "driver": o.get("driver", ""),
                "district": _code_of(dist, districts)}

        # Непрочитанное сообщение водителя — такой же повод вмешаться, как и
        # просьба: он стоит на адресе и ждёт ответа.
        n_new = _chat_new(o, "operator")
        if n_new and st == "approved":
            last = (o.get("chat") or [])[-1]
            need.append({**base, "type": "chat", "kind": "chat",
                         "title": DRV_REQ_TITLE.get("chat", "водитель пишет"),
                         "sub": last.get("name", ""), "text": last.get("text", ""),
                         "at": last.get("at", ""), "mins": _mins_since(last.get("at", "")),
                         "unread": n_new, "weight": 0})
        if req and req.get("status") == "open":
            need.append({**base, "type": "driver_req", "kind": req.get("kind") or "edit",
                         "title": DRV_REQ_TITLE.get(req.get("kind") or "edit", "просьба водителя"),
                         "sub": req.get("by", ""), "text": req.get("text", ""),
                         "at": req.get("at", ""), "mins": _mins_since(req.get("at", "")),
                         "weight": 0})
        if st == "pending":
            mins = _mins_since(o.get("timestamp", ""))
            need.append({**base, "type": "pending", "kind": "pending",
                         "title": "заказ не принят", "sub": o.get("customer_name", ""),
                         "at": o.get("timestamp", ""), "mins": mins,
                         "weight": 1 if mins >= 5 else 3})
        elif st == "approved":
            # Назначили и молчит: либо не видит приложение, либо не заметил.
            # Дальше это превращается в опоздание, поэтому спрашиваем раньше.
            if o.get("driver") and not o.get("driver_ack_at"):
                mins = _mins_since(o.get("confirmed_at") or o.get("timestamp", ""))
                if mins >= 3:
                    need.append({**base, "type": "no_ack", "kind": "no_ack",
                                 "title": "водитель не отозвался",
                                 "sub": o.get("driver", ""),
                                 "at": o.get("confirmed_at") or o.get("timestamp", ""),
                                 "mins": mins, "weight": 2})
            late = _late_by(o)
            if late >= 5:
                need.append({**base, "type": "late", "kind": "late",
                             "title": "просрочено обещанное время",
                             "sub": f"обещали к {o.get('deliver_by','')}",
                             "at": o.get("confirmed_at") or o.get("timestamp", ""),
                             "mins": late, "weight": 2})

        # Недавнее — только за смену: лента про «что было сегодня», а не архив.
        for fld, kind, title in (("delivered_at", "delivered", "доставлен"),
                                 ("cancelled_at", "cancelled", "отменён"),
                                 ("declined_at", "declined", "отклонён"),
                                 ("confirmed_at", "accepted", "принят")):
            ts = o.get(fld)
            if not ts:
                continue
            try:
                d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if _biz_date(d.astimezone(DUBAI_TZ)) != today:
                continue
            # «Принят» показываем только пока заказ едет: закрытый заказ
            # интереснее строкой о том, чем он закончился.
            if kind == "accepted" and st != "approved":
                continue
            recent.append({**base, "type": "event", "kind": kind, "title": title,
                           "sub": (o.get("delivered_by") or o.get("cancelled_by")
                                   or o.get("operator_name") or ""),
                           "at": d.isoformat(), "mins": _mins_since(d.isoformat())})

    # Поддержка: вопрос без ответа — та же незакрытая задача, что и заказ.
    try:
        for t in await _support_rows():
            if not t.get("wait"):
                continue
            need.append({"type": "support", "kind": "support", "order_id": t.get("order_id", ""),
                         "title": "вопрос в поддержку без ответа",
                         "sub": (t.get("client") or {}).get("name", ""),
                         "text": t.get("last_text", ""), "key": t.get("key", ""),
                         "at": t.get("last_ts", ""), "mins": _mins_since(t.get("last_ts", "")),
                         "total": 0, "address": "", "driver": "", "district": "",
                         "weight": 1})
    except Exception as e:
        log.error(f"[pos] лента: поддержка не собралась: {e}")

    need.sort(key=lambda x: (x["weight"], -x["mins"]))
    recent.sort(key=lambda x: x["at"], reverse=True)
    return web.json_response({
        "need": need[:60],
        "recent": recent[:40],
        "count": sum(1 for x in need if x["weight"] <= 2),
    }, headers=CORS_HEADERS)


# ── просьбы водителя ─────────────────────────────────────────────────────────
# Водитель ничего не решает сам: «доставил», «поменять состав», «отменить» —
# это просьбы. Оператор соглашается в одно нажатие, отклоняет, либо правит
# состав по-своему и соглашается уже со своим. Решение всегда за оператором,
# потому что на нём деньги, клиент и спор, если что-то пойдёт не так.

def _req_of(order: dict) -> dict:
    return order.get("driver_req") or order.get("edit_request") or {}


@require_operator
@needs_open
async def handle_driver_req(request):
    oid = request.match_info["oid"]
    order = await db.get_order(oid)
    if not order:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        body = {}
    act = (body.get("act") or "").strip()
    who = (body.get("as") or "").strip() or _op_name(request["op_user"])
    req = _req_of(order)
    if not req or req.get("status") != "open":
        return web.json_response({"error": "no_open_request",
                                  "status": req.get("status", "")},
                                 status=409, headers=CORS_HEADERS)
    kind = req.get("kind") or "edit"
    now = datetime.now(timezone.utc).isoformat()
    drv = req.get("by", "")

    if act == "reject":
        await db.update_order(oid, driver_req={**req, "status": "rejected",
                                               "decided_by": who, "decided_at": now})
        await tell_driver(drv, {
            "delivered": f"❌ Оператор не подтвердил доставку #{oid} — свяжитесь с ним",
            "cancel":    f"❌ Отмена заказа #{oid} отклонена — заказ везём",
            "edit":      f"❌ Правка по заказу #{oid} отклонена",
            "note":      f"❌ Сообщение по заказу #{oid} отклонено",
        }[kind])
        await _refresh_cards(await db.get_order(oid) or order)
        log.info(f"[pos] просьба «{kind}» по #{oid} отклонена ({who})")
        return web.json_response({"ok": True, "status": "rejected"}, headers=CORS_HEADERS)

    if act != "approve":
        return web.json_response({"error": "bad_act"}, status=400, headers=CORS_HEADERS)

    # Оператор мог поправить состав по-своему — тогда применяем его, а не
    # водительский: последнее слово и цена остаются здесь.
    if kind == "edit":
        raw = body.get("items")
        items = None
        if isinstance(raw, list) and raw:
            items, _ = _build_items(raw)
        items = items or req.get("items")
        if not items:
            return web.json_response({"error": "empty_items"}, status=400, headers=CORS_HEADERS)
        total = await _pos_total(items)
        await db.update_order(oid, items=items, total=total, updated_at=now,
                              driver_req={**req, "status": "applied", "decided_by": who,
                                          "decided_at": now, "applied_total": total})
        order = await db.get_order(oid) or order
        await _refresh_cards(order)
        await _customer_card(oid)
        await notify_driver(order, "edit")
        await tell_driver(drv, f"✅ Заказ #{oid} изменён оператором · итог {total} AED")
        try:
            from owner_routes import notify_owners_force
            _it = "\n".join(f"• {i.get('name','')} ×{i.get('qty',1)}"
                             for i in order.get("items", [])) or "—"
            await notify_owners_force(
                "orders.edited",
                f"✏️ *Заказ изменён #{oid}* — по просьбе водителя {drv}\n"
                f"Одобрил: {who}\n💰 Новый итог: *{total} AED*\n🛒 Позиции:\n{_it}")
        except Exception as e:
            log.error(f"[pos] edited notify failed: {e}")
        log.info(f"[pos] правка по #{oid} применена ({who})")
        return web.json_response({"ok": True, "status": "applied", "total": total},
                                 headers=CORS_HEADERS)

    if kind == "delivered":
        if order.get("status") != "approved":
            return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
        await db.update_order(oid, driver_req={**req, "status": "applied",
                                               "decided_by": who, "decided_at": now})
        await _close_delivered(oid, order, who, drv)
        await tell_driver(drv, f"✅ Оператор подтвердил доставку заказа #{oid}")
        log.info(f"[pos] доставка #{oid} подтверждена ({who})")
        return web.json_response({"ok": True, "status": "applied"}, headers=CORS_HEADERS)

    if kind == "cancel":
        if order.get("status") != "approved":
            return web.json_response({"error": "order is closed"}, status=409, headers=CORS_HEADERS)
        await db.update_order(oid, driver_req={**req, "status": "applied",
                                               "decided_by": who, "decided_at": now})
        await _do_cancel(oid, order, who, req.get("text", ""))
        await tell_driver(drv, f"🚫 Заказ #{oid} отменён оператором по вашей просьбе")
        log.info(f"[pos] отмена #{oid} по просьбе водителя ({who})")
        return web.json_response({"ok": True, "status": "applied"}, headers=CORS_HEADERS)

    # note — просто «принято»: водителю важно знать, что прочитали.
    await db.update_order(oid, driver_req={**req, "status": "applied",
                                           "decided_by": who, "decided_at": now})
    await tell_driver(drv, f"👌 Оператор принял ваше сообщение по заказу #{oid}")
    await _refresh_cards(await db.get_order(oid) or order)
    return web.json_response({"ok": True, "status": "applied"}, headers=CORS_HEADERS)


# ── поддержка: переписки с клиентами ─────────────────────────────────────────
# Клиент пишет из своего приложения (api_server /api/support/send), оператор до
# сих пор отвечал только реплаем в телеграме бота поддержки. Здесь та же
# переписка, но в панели: список тикетов + чат, из которого можно и ответить, и
# написать первым. Формат сообщений общий с приложением клиента:
#   {role: "user"|"operator", type, text|url+caption, ts, by?}
# conv_key: "{uid}" или "{uid}_{order_id}"; общий вопрос — "{uid}_general".

def _conv_parts(key: str) -> tuple[int, str]:
    head, _, tail = (key or "").partition("_")
    uid = int(head) if head.isdigit() else 0
    return uid, ("" if tail in ("", "general") else tail)


def _msg_preview(m: dict) -> str:
    return (m.get("text") or m.get("caption")
            or ("Фото" if m.get("type") == "photo" else ""))[:90]


def _uname(v) -> str:
    """Ник клиента, годный для ссылки.

    У клиента без ника в базе лежит прочерк — так его кладут карточки заказа.
    Панель считала прочерк ником, писала в шапке «@—» и вела кнопку «Телеграм»
    на t.me/—, то есть в никуда."""
    s = str(v or "").strip().lstrip("@")
    return "" if s in ("", "—", "-", "None") else s


def _client_brief(uid: int, u: dict) -> dict:
    return {
        "id": uid,
        "name": (u.get("first_name") or u.get("name") or u.get("full_name")
                 or (str(uid) if uid else "—")),
        "username": _uname(u.get("username")),
        # Ника нет почти у половины клиентов, а подтверждённый номер есть:
        # телеграм открывает чат и по номеру, и оператору больше незачем
        # искать человека руками.
        "phone": str(u.get("phone_verified") or ""),
        "verified": bool(u.get("verified")),
        "banned": bool(u.get("banned") or u.get("is_banned")),
        # Чаще всего пишет тот, кто застрял на верификации: оператор должен
        # видеть это в списке, а не выяснять по ходу разговора.
        "verify_wait": bool(u.get("verify_requested") and not u.get("verified")
                            and not u.get("verify_declined")),
        "verify_declined": bool(u.get("verify_declined")),
    }


async def _support_rows() -> list:
    """Тикеты, отсортированные по тому, кому оператор нужен прямо сейчас.

    Первым — вопрос по непринятому заказу: человек ждёт и решения, и ответа.
    Дальше — по заказу в работе, дальше остальные без ответа, и только потом
    отвеченное. Хронология здесь врёт: свежий «спасибо» не важнее вчерашнего
    вопроса, на который никто не ответил."""
    docs = await db.support_threads_brief(400)
    orders = await db.get_all_orders()
    uids = set()
    pre = []
    for d in docs:
        key = (d.get("conv_key") or "").strip()
        msgs = d.get("messages") or []
        if not key or not msgs:
            continue
        uid, oid = _conv_parts(key)
        if not uid:
            continue
        uids.add(uid)
        pre.append((key, uid, oid, msgs[-1], d.get("channel") or ""))
    users = await db.users_by_ids(list(uids))

    now = datetime.now(timezone.utc)

    def _hours(ts) -> float:
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return (now - d).total_seconds() / 3600
        except Exception:
            return 1e9

    rows = []
    for key, uid, oid, last, channel in pre:
        o = orders.get(oid) if oid else None
        lane = _lane(o) if o else ""
        # «Ждёт ответа» — это сегодняшний вопрос или вопрос по живому заказу.
        # Мартовское «где курьер?» ответа уже не ждёт, и если считать его
        # ждущим, счётчик показывает 31 и не значит ничего.
        wait = (last.get("role") == "user") and (lane in ("new", "work")
                                                 or _hours(last.get("ts")) <= 24)
        if wait:
            prio = 0 if lane == "new" else (1 if lane == "work" else 2)
        else:
            prio = 3 if lane in ("new", "work") else 4
        rows.append({
            "key": key,
            "order_id": oid,
            "order_status": (o or {}).get("status", ""),
            "order_lane": lane,
            "order_total": (o or {}).get("total", 0),
            "client": _client_brief(uid, users.get(uid) or {}),
            "wait": wait,
            "prio": prio,
            "last_ts": last.get("ts", ""),
            "last_role": last.get("role", ""),
            "last_text": _msg_preview(last),
            "channel": channel,
        })
    rows.sort(key=lambda r: (r["prio"], _neg_ts(r["last_ts"])))
    return rows


def _neg_ts(ts: str):
    """Ключ сортировки «новее — выше» для строковых ISO-дат."""
    return tuple(-ord(c) for c in (ts or ""))


@require_operator
async def handle_support_list(request):
    rows = await _support_rows()
    return web.json_response({
        "threads": rows[:200],
        "waiting": sum(1 for r in rows if r["wait"]),
    }, headers=CORS_HEADERS)


@require_operator
async def handle_support_thread(request):
    """Переписка целиком + все обращения этого же клиента.

    История нужна рядом: тот же человек мог спрашивать про прошлый заказ, и
    без этого оператор отвечает вслепую."""
    key = (request.query.get("key") or "").strip()
    uid, oid = _conv_parts(key)
    if not uid:
        return web.json_response({"error": "bad_key"}, status=400, headers=CORS_HEADERS)
    msgs = await db.get_support_conv(key)
    u = await db.get_user(uid) or {}
    order = (await db.get_order(oid)) if oid else None
    others = [r for r in await _support_rows() if r["client"]["id"] == uid and r["key"] != key]
    return web.json_response({
        "key": key,
        "order_id": oid,
        "channel": await db.support_channel(key),
        "order": {"order_id": oid, "status": (order or {}).get("status", ""),
                  "total": (order or {}).get("total", 0),
                  "address": (order or {}).get("address", ""),
                  "lane": _lane(order) if order else ""} if order else None,
        "client": {**_client_brief(uid, u),
                   "phone": u.get("phone_verified", ""),
                   "orders_total": u.get("orders_total", 0),
                   "note": u.get("custom_name", "")},
        "messages": msgs,
        "others": others,
    }, headers=CORS_HEADERS)


@require_operator
async def handle_support_send(request):
    """Ответ оператора из панели.

    Пишем в ту же переписку, что и бот поддержки, и точно так же пинаем клиента
    в телеграм: приложение у него закрыто, и иначе ответ никто не увидит."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = (body.get("key") or "").strip()
    text = (body.get("text") or "").strip()
    uid, oid = _conv_parts(key)
    if not uid or not text:
        return web.json_response({"error": "bad_request"}, status=400, headers=CORS_HEADERS)
    if len(text) > 3000:
        text = text[:3000]

    who = (body.get("as") or "").strip() or _op_name(request["op_user"])
    ts = datetime.now(timezone.utc).isoformat()

    try:
        channel = await db.support_channel(key)
    except Exception:
        channel = ""

    # Отправляем до записи, чтобы записать вместе с судьбой отправки. Ответ
    # ложится в переписку в любом случае, но «ушло» и «дошло» — разные вещи:
    # клиент мог не открывать бот или заблокировать его, и тогда оператор
    # закрывает вопрос, которого никто не услышал.
    via_bot = channel in ("bot", "mainbot")
    sent_ok = False
    try:
        import support_inbox
        from api_server import tg_send, BOT_TOKEN
        if channel in (support_inbox.CHANNEL_SUPPORT, support_inbox.CHANNEL_MAIN):
            # Клиент писал в телеграм-бот — туда же кладём и сам ответ, целиком:
            # приложение он может вообще не открывать, а половина этих людей
            # висит на верификации и внутрь просто не попадает.
            if channel == support_inbox.CHANNEL_SUPPORT:
                r = await support_inbox.send_as_support(uid, f"💬 {text}")
            else:
                r = await support_inbox.send_as_main(uid, f"💬 {text}")
        else:
            r = await tg_send(BOT_TOKEN, uid,
                              "💬 *Новое сообщение от поддержки*"
                              + (f" по заказу #{oid}" if oid else "")
                              + "\n\nОткройте приложение, чтобы прочитать ответ.",
                              parse_mode="Markdown")
        sent_ok = bool((r or {}).get("ok"))
        if not sent_ok:
            log.warning(f"[pos] support nudge to {uid} rejected: {r}")
    except Exception as e:
        log.error(f"[pos] support nudge to {uid} failed: {e}")

    # Из приложения ответ виден и без телеграма — там не дошёл лишь звонок в
    # дверь. А из бота сам ответ и есть сообщение: не ушло — значит не дошло.
    delivered = sent_ok or not via_bot

    msg = {"role": "operator", "type": "text", "text": text, "ts": ts, "by": who}
    if not delivered:
        msg["delivered"] = False
    await db.append_support_msg(key, msg)

    try:
        from owner_routes import notify_owners
        u = await db.get_user(uid) or {}
        cname = u.get("first_name") or u.get("name") or str(uid)
        await notify_owners(
            "support.replied",
            f"🎧 *Оператор ответил в поддержке*\n"
            f"Клиент: {cname} (@{u.get('username') or '—'})\n"
            f"Контекст: {'заказ #' + oid if oid else 'общий вопрос'}\n"
            f"Оператор: {who}\n"
            f"_«{text[:150]}»_",
            meta={"conv_key": key, "order_id": oid})
    except Exception as e:
        log.error(f"[pos] support.replied notify failed: {e}")

    log.info(f"[pos] поддержка → {uid} ({who}, доставлено={delivered})")
    return web.json_response({"ok": True, "msg": msg, "delivered": delivered},
                             headers=CORS_HEADERS)


@require_operator
async def handle_support_customers(request):
    """Поиск клиента, чтобы написать первым.

    Ищем по имени, нику, телефону и id — оператор помнит человека по-разному, а
    список из двух тысяч имён листать бесполезно."""
    q = (request.query.get("q") or "").strip().lower().lstrip("@")
    users = await db.get_all_customers()
    digits = "".join(c for c in q if c.isdigit())
    hits = []
    for u in users:
        uid = int(u.get("telegram_id") or 0)
        if not uid:
            continue
        name = (u.get("first_name") or u.get("name") or u.get("full_name") or "")
        uname = _uname(u.get("username"))
        phone = str(u.get("phone_verified") or "")
        if q:
            hit = (q in name.lower() or q in uname.lower() or q in str(uid)
                   or (digits and digits in phone))
            if not hit:
                continue
        hits.append((str(u.get("last_seen") or u.get("first_seen") or ""), uid, u, phone))
    # Сначала те, кто был здесь недавно. Раньше список обрывался на первых
    # сорока в порядке базы — с пустым поиском это были самые старые клиенты,
    # и «Написать клиенту» открывалось архивом двухлетней давности.
    hits.sort(key=lambda h: h[0], reverse=True)
    out = [{**_client_brief(uid, u), "phone": phone,
            "orders_total": u.get("orders_total", 0), "last_seen": seen}
           for seen, uid, u, phone in hits[:40]]
    return web.json_response({"customers": out}, headers=CORS_HEADERS)


# ── mounting ─────────────────────────────────────────────────────────────────
def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


# ── Конец смены ─────────────────────────────────────────────────────────────
# Операторы работают до утра и уходят спать примерно тогда, когда встаёт
# старший. «Смена закрыта» — это заявление человека, а не срабатывание часов:
# программа не знает, будет ли ещё один звонок, а оператор знает.
#
# Из этого заявления следует всё остальное: продажи дня окончательны, значит
# по ним можно считать, чего не хватает, и собирать заявку в магазин. Поэтому
# закрытие смены — не запись в журнал, а спусковой крючок.
#
# Закрывается по району, а не «вообще»: районы работают независимо, и один
# оператор не может отвечать за чужую точку.
async def _shift_state(day, districts: list, scope: set) -> dict:
    closed = await db.shifts_for_day(day.isoformat())
    opens = await db.shift_opens_for_day(day.isoformat())
    # Читаем окно смены, а не всю историю. Полный список — это мегабайт, и на
    # нашем тарифе Atlas он идёт восемь секунд: скорость там режется по объёму.
    # Именно из-за него окно закрытия смены висело — и на открытии, и на каждом
    # нажатии «Закрыть». Незакрытые заказы orders_from отдаёт любого возраста,
    # так что заказ, висящий со вчера, из подсчёта не выпадет.
    since = (datetime(day.year, day.month, day.day, SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
             - timedelta(hours=1))
    orders = list((await db.orders_from(
        since.astimezone(timezone.utc).isoformat().replace("+00:00", ""))).values())
    # Кто из водителей не ответил по обязательным расходам. Оператору это видно
    # здесь, а не только в напоминании от бота: напоминание приходит ему, а
    # сделать он ничего не может, если не знает, кого подтолкнуть.
    silent = {}
    try:
        from driver_routes import MUST_ANSWER, _kind_of
        for d in await db.get_driver_days(day.isoformat()):
            if d.get("working") is not True:
                continue
            no = d.get("no_expense") or {}
            extras = d.get("extras") or []
            if any(not no.get(k) and not any(_kind_of(x) == k for x in extras)
                   for k in MUST_ANSWER):
                silent[d.get("driver") or ""] = True
    except Exception as e:
        log.warning(f"[pos] расходы водителей не прочитаны: {e}")
    out = []
    for d in districts:
        if d["id"] not in scope:
            continue
        mine = [o for o in orders if (o.get("office_id") or "") == d["id"]]
        # Незакрытые заказы — единственное, что мешает считать день посчитанным:
        # заказ в работе ещё может стать выручкой, а может отмениться.
        open_now = [o for o in mine if _lane(o) in ("new", "work")]
        done = [o for o in mine if o.get("status") == "delivered"
                and _biz_date_of(o) == day]
        c = closed.get(d["id"])
        op = opens.get(d["id"])
        out.append({
            "district": d["id"], "code": d.get("code", ""), "name": d.get("name", ""),
            "operator": d.get("operator", ""),
            "opened": bool(op), "opened_at": str((op or {}).get("opened_at") or ""),
            "opened_by": (op or {}).get("by_name") or "",
            "crew": (op or {}).get("drivers") or {},
            "drivers": list(_staff_mod.DISTRICT_DRIVERS.get(d["id"], [])),
            "closed": bool(c), "closed_at": str((c or {}).get("closed_at") or ""),
            "closed_by": (c or {}).get("by_name") or "",
            "open": len(open_now),
            "open_ids": [o.get("order_id") for o in open_now][:12],
            # Заказ в пути — не то же самое, что незакрытый. Висящий с вечера
            # непринятый заказ закрыть смену не мешает, а тот, что человек
            # сейчас везёт, — мешает: смена закрыта, а бутылка едет.
            "in_route": len([o for o in mine if _lane(o) == "work"]),
            "in_route_ids": [o.get("order_id") for o in mine if _lane(o) == "work"][:12],
            "in_route_drivers": sorted({(o.get("driver") or "").strip()
                                        for o in mine if _lane(o) == "work"} - {""}),
            "orders": len(done),
            "revenue": sum(int(o.get("total") or 0) for o in done),
            "silent": [n for n in _staff_mod.DISTRICT_DRIVERS.get(d["id"], [])
                       if silent.get(n)],
        })
    return {"day": day.isoformat(), "districts": out,
            "all_closed": bool(out) and all(x["closed"] for x in out),
            "all_open": bool(out) and all(x["opened"] for x in out)}


def _biz_date_of(o: dict):
    try:
        return _biz_date(datetime.fromisoformat(o.get("timestamp", "")).replace(
            tzinfo=timezone.utc).astimezone(DUBAI_TZ))
    except (ValueError, TypeError):
        return None


# ── где водители ───────────────────────────────────────────────────────────
# Оператор видит своих, владелец — всех. Клиенту и другим водителям координаты
# не отдаются нигде и никогда.
# Сколько точка считается свежей. Две минуты: дольше — и закрытое приложение
# выглядит как живая связь, короче — метка мигает у того, кто просто идёт к
# машине с телефоном в кармане.
FRESH_SEC = 120


async def _orders_by_driver(day) -> dict:
    """{водитель: {в пути, доставлено за смену}}.

    Список водителей без этого отвечает только на «где он», а спрашивают
    обычно «кому отдать следующий» — и тут решает не расстояние само по себе,
    а расстояние вместе с тем, сколько у человека уже на руках."""
    since = (datetime(day.year, day.month, day.day, SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
             - timedelta(hours=1))
    orders = (await db.orders_from(
        since.astimezone(timezone.utc).isoformat().replace("+00:00", ""))).values()
    out = {}
    for o in orders:
        name = (o.get("driver") or "").strip()
        if not name:
            continue
        st = (o.get("status") or "").strip()
        if st not in ("approved", "delivered"):
            continue
        r = out.setdefault(name, {"live": 0, "done": 0})
        if st == "approved":
            r["live"] += 1
        elif _biz_date_of(o) == day:
            r["done"] += 1
    return out


async def drivers_live(names: list, day, want_track: str = "") -> dict:
    if not isinstance(day, str):
        day_obj, day = day, day.isoformat()
    else:
        day_obj = datetime.strptime(day, "%Y-%m-%d").date()
    rows = {r["driver"]: r for r in await db.driver_pos_all(names)}
    try:
        work = await _orders_by_driver(day_obj)
    except Exception as e:
        log.warning(f"[where] заказы водителей не прочитаны: {e}")
        work = {}
    now = datetime.now(timezone.utc)
    out = []
    for name in names:
        w = work.get(name) or {}
        r = rows.get(name)
        if not r:
            out.append({"driver": name, "has": False,
                        "orders": w.get("live", 0), "done": w.get("done", 0)})
            continue
        at = _dt_utc(r.get("at"))
        age = int((now - at).total_seconds()) if at else None
        until = _dt_utc(r.get("until"))
        out.append({
            "driver": name, "has": True,
            "lat": r.get("lat"), "lon": r.get("lon"),
            "at": r.get("at"), "age": age, "acc": r.get("acc"),
            # «На связи» — это про свежесть точки, а не про способ, которым
            # она пришла. Точка из приложения приходит без срока трансляции, и
            # раньше такой водитель показывался серым, будто молчит, хотя он
            # только что открыл приложение.
            "live": bool(age is not None and age < FRESH_SEC),
            # А идёт ли трансляция — отдельно: от неё зависит, будет ли видно
            # водителя, когда он уберёт телефон в карман.
            "stream": bool(until and until > now),
            # Сколько минут трансляции осталось: у неё потолок в восемь часов,
            # и знать, что она кончится через двадцать минут, полезнее, чем
            # узнать это по погасшей метке. Считаем здесь — на телефоне часы
            # свои, и разбор чужого времени в браузере уже подводил.
            "left": int((until - now).total_seconds() // 60)
                    if (until and until > now) else 0,
            "day": r.get("day") or "",
            "orders": w.get("live", 0), "done": w.get("done", 0),
        })
    res = {"drivers": out, "day": day}
    if want_track:
        res["track"] = await db.driver_track(want_track, day)
        res["track_of"] = want_track
    return res


def _dt_utc(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", ""))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


@require_operator
async def handle_where(request):
    districts = await _fresh_districts()
    scope = _scope(_people(districts), (request.query.get("as") or "").strip(), districts)
    names = []
    for oid in scope:
        names += list(_staff_mod.DISTRICT_DRIVERS.get(oid) or [])
    data = await drivers_live(names, _biz_date(datetime.now(DUBAI_TZ)),
                              (request.query.get("track") or "").strip())
    # К каждому — район и оператор: на карте пять точек, и без подписи они
    # одинаковые.
    who = {}
    for d in districts:
        for n in (_staff_mod.DISTRICT_DRIVERS.get(d["id"]) or []):
            who[n] = {"district": d["id"], "code": d.get("code", ""),
                      "name": d.get("name", ""), "operator": d.get("operator", "")}
    for r in data["drivers"]:
        r.update(who.get(r["driver"]) or {})
    return web.json_response(data, headers=CORS_HEADERS,
                             dumps=lambda o: __import__("json").dumps(o, default=str))


@require_operator
async def handle_shift(request):
    districts = await _fresh_districts()
    people = _people(districts)
    who = (request.query.get("as") or "").strip()
    scope = _scope(people, who, districts)
    if not scope:
        return web.json_response({"error": "unknown_operator"}, status=400,
                                 headers=CORS_HEADERS)
    return web.json_response(
        await _shift_state(_biz_date(datetime.now(DUBAI_TZ)), districts, scope),
        headers=CORS_HEADERS)


# Смена открывается явно, и это не формальность.
#
# До сих пор рабочий день начинался сам собой: заказ приходил — оператор его
# брал. Из-за этого никто не знал двух вещей, которые нужны каждый день: кто
# сегодня за пультом и кто из водителей вышел. Второе стоит денег буквально —
# питание платят по факту выхода, и отмечал его старший, глядя в потолок.
#
# Теперь день начинается с одного экрана: оператор отмечает свою бригаду и
# открывает смену. Пока она не открыта, заказы обрабатывать нельзя — смотреть
# можно всё.
@require_operator
async def handle_shift_open(request):
    """Открыть смену района и отметить, кто из водителей сегодня работает."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    districts = await _fresh_districts()
    who = str(body.get("as") or "").strip()
    scope = _scope(_people(districts), who, districts)
    oid = str(body.get("district") or "").strip()
    if oid not in scope:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)

    crew = body.get("drivers") or {}
    names = list(_staff_mod.DISTRICT_DRIVERS.get(oid) or [])
    # Отмечены должны быть все: пропущенный водитель — это не «неизвестно», а
    # человек без питания, который об этом узнает в конце месяца.
    missing = [n for n in names if not isinstance(crew.get(n), bool)]
    if missing:
        return web.json_response({"error": "crew_incomplete", "missing": missing},
                                 status=400, headers=CORS_HEADERS)

    day = _biz_date(datetime.now(DUBAI_TZ)).isoformat()
    ok = await db.shift_open(day, oid, {
        "opened_at": datetime.now(timezone.utc), "by": request.get("op_id") or 0,
        "by_name": who, "operator": next((d.get("operator", "") for d in districts
                                          if d["id"] == oid), ""),
        "drivers": {n: bool(crew.get(n)) for n in names}})
    if not ok:
        return web.json_response({"error": "already_open"}, status=409, headers=CORS_HEADERS)
    _opens_drop()

    # Питание считается по этой отметке — она же и есть факт выхода.
    for n in names:
        try:
            await db.save_driver_day(day, n, {"working": bool(crew.get(n))})
        except Exception as e:
            log.warning(f"[pos] выход {n} не сохранён: {e}")
    log.info(f"[pos] смена открыта: {oid} · {who} · "
             f"вышли {sum(1 for n in names if crew.get(n))} из {len(names)}")
    await _tell_crew(oid, names, crew, who)
    return web.json_response({"ok": True, "day": day, "district": oid,
                              "drivers": {n: bool(crew.get(n)) for n in names}},
                             headers=CORS_HEADERS)


async def _tell_crew(district: str, names: list, crew: dict, who: str):
    """Сказать водителям, что их отметили.

    Питание считается по этой отметке, а узнавать о своих деньгах из чужого
    отчёта человек не должен. Заодно это проверка оператора: отметил не того —
    тот сразу напишет."""
    import os as _os
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not token:
        return
    try:
        from api_server import tg_send
        import config_staff as st
        for n in names:
            cid = st.DRIVER_IDS.get(n)
            if not cid:
                continue
            work = bool(crew.get(n))
            txt = (f"🚗 Смена открыта. {who} отметил, что вы сегодня работаете.\n"
                   f"Питание за сегодня — {st.MEAL_WORKING} AED."
                   if work else
                   f"🏠 Смена открыта. {who} отметил, что сегодня вы не работаете.\n"
                   f"Питание за сегодня — {st.MEAL_OFF} AED.\n\n"
                   f"Если это ошибка — напишите оператору.")
            await tg_send(token, cid, txt)
    except Exception as e:
        log.warning(f"[pos] бригаде не сообщили ({district}): {e}")


@require_operator
async def handle_shift_log(request):
    """История смен — та же, что видит владелец.

    По всем районам, а не только по своему: оператор каждый день спрашивает,
    закрылись ли соседи, и до сих пор спрашивал голосом."""
    try:
        days = max(1, min(31, int(request.query.get("days", "7") or 7)))
    except ValueError:
        days = 7
    today = _biz_date(datetime.now(DUBAI_TZ)).isoformat()
    d0 = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    import stock_routes
    rows = stock_routes.shift_log_rows(await db.shift_journal(d0, today))
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    return web.json_response({"from": d0, "to": today, "list": [
        {"day": k, "rows": by_day[k]} for k in sorted(by_day, reverse=True)]},
        headers=CORS_HEADERS)


@require_operator
async def handle_shift_close(request):
    """Закрыть смену района.

    Незакрытые заказы не мешают: бывает, что заказ висит с вечера и уже никуда
    не поедет. Но их число уезжает старшему вместе с итогом — молча потерянный
    заказ хуже некрасивой цифры."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    districts = await _fresh_districts()
    people = _people(districts)
    who = str(body.get("as") or "").strip()
    scope = _scope(people, who, districts)
    oid = str(body.get("district") or "").strip()
    if oid not in scope:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)

    day = _biz_date(datetime.now(DUBAI_TZ))
    # Состояние считаем один раз на все районы: свой район берём из него же, а
    # «все ли закрылись» — после того, как отметим себя. Раньше здесь было два
    # полных подсчёта подряд, и закрытие смены ждало обоих.
    full = await _shift_state(day, districts, {d["id"] for d in districts})
    mine = next((x for x in full["districts"] if x["district"] == oid), None)
    if not mine:
        return web.json_response({"error": "unknown_district"}, status=400,
                                 headers=CORS_HEADERS)
    # Пока по району что-то едет, смену не закрываем. Это не формальность:
    # после закрытия по заказу нельзя отметить доставку, и он повисает между
    # выручкой и потерей.
    if mine.get("in_route"):
        return web.json_response(
            {"error": "orders_in_route", "count": mine["in_route"],
             "ids": mine.get("in_route_ids") or [],
             "drivers": mine.get("in_route_drivers") or []},
            status=409, headers=CORS_HEADERS)
    ok = await db.shift_close(day.isoformat(), oid, {
        "closed_at": datetime.now(timezone.utc), "by": request.get("op_id") or 0,
        "by_name": who, "operator": mine["operator"],
        "orders": mine["orders"], "revenue": mine["revenue"],
        "open": mine["open"], "open_ids": mine["open_ids"]})
    if not ok:
        return web.json_response({"error": "already_closed"}, status=409,
                                 headers=CORS_HEADERS)
    log.info(f"[pos] смена закрыта: {oid} · {who} · {mine['orders']} заказов · "
             f"{mine['revenue']} AED · висит {mine['open']}")

    # Смена закрыта — маршрут за неё больше никому не нужен. Точка «сейчас»
    # остаётся: по ней видно, что водитель ещё в сети.
    try:
        names = list(_staff_mod.DISTRICT_DRIVERS.get(oid) or [])
        if names:
            n = await db.driver_track_clear(names)
            if n:
                log.info(f"[pos] треки за смену стёрты: {oid} · {n}")
    except Exception as e:
        log.warning(f"[pos] треки не стёрты ({oid}): {e}")

    mine["closed"] = True
    mine["closed_by"] = who
    mine["closed_at"] = str(datetime.now(timezone.utc))
    full["all_closed"] = all(x["closed"] for x in full["districts"])
    if full["all_closed"]:
        # Заявку собирает тот, кто закрылся последним, — и ровно один раз:
        # пометка дня ставится атомарно, второму вернётся False.
        try:
            import shift_end
            await shift_end.on_all_closed(day.isoformat(), full)
        except Exception as e:
            log.error(f"[pos] конец смены: {e}")
    return web.json_response({"ok": True, "all_closed": full["all_closed"],
                              **{k: mine[k] for k in ("orders", "revenue", "open")}},
                             headers=CORS_HEADERS)


@require_operator
async def handle_shift_reopen(request):
    """Открыть смену обратно: закрыли, а телефон зазвонил."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    districts = await _fresh_districts()
    scope = _scope(_people(districts), str(body.get("as") or "").strip(), districts)
    oid = str(body.get("district") or "").strip()
    if oid not in scope:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)
    day = _biz_date(datetime.now(DUBAI_TZ)).isoformat()
    ok = await db.shift_reopen(day, oid)
    if ok:
        log.info(f"[pos] смена открыта заново: {oid}")
        # Заявку по этому дню могли уже собрать и отправить. Владелец должен
        # узнать об этом сразу, а не обнаружить расхождение в магазине: файл
        # у него на руках уже неполный.
        try:
            snap = await db.shift_day_snapshot(day)
            if snap:
                from owner_routes import notify_owners
                d = next((x for x in districts if x["id"] == oid), {})
                await notify_owners(
                    "shift.closed",
                    f"↩️ *Район открыт заново — {day}*\n"
                    f"{d.get('code','')} {d.get('name', oid)}. Заявка по этому дню "
                    f"уже собрана и отправлена: файл на руках неполный.\n\n"
                    f"Когда район закроют снова, пришлю уточнённую заявку.")
        except Exception as e:
            log.error(f"[pos] предупреждение о переоткрытии не ушло: {e}")
    return web.json_response({"ok": ok}, headers=CORS_HEADERS)


def setup(app):
    """Mount operator POS routes. Called from api_server.main()."""
    r = app.router
    r.add_route("OPTIONS", "/api/operator/where", _opt)
    r.add_get("/api/operator/where", handle_where)
    r.add_route("OPTIONS", "/api/operator/shift", _opt)
    r.add_get("/api/operator/shift", handle_shift)
    r.add_route("OPTIONS", "/api/operator/shift/log", _opt)
    r.add_get("/api/operator/shift/log", handle_shift_log)
    r.add_route("OPTIONS", "/api/operator/shift/open", _opt)
    r.add_post("/api/operator/shift/open", handle_shift_open)
    r.add_route("OPTIONS", "/api/operator/shift/close", _opt)
    r.add_post("/api/operator/shift/close", handle_shift_close)
    r.add_route("OPTIONS", "/api/operator/shift/reopen", _opt)
    r.add_post("/api/operator/shift/reopen", handle_shift_reopen)
    r.add_route("OPTIONS", "/api/operator/ping", _opt)
    r.add_get("/api/operator/ping", handle_ping)
    r.add_route("OPTIONS", "/api/operator/catalog", _opt)
    r.add_get("/api/operator/catalog", handle_catalog)
    r.add_route("OPTIONS", "/api/operator/queue", _opt)
    r.add_get("/api/operator/queue", handle_queue)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/accept", _opt)
    r.add_post("/api/operator/orders/{oid}/accept", handle_accept)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/decline", _opt)
    r.add_post("/api/operator/orders/{oid}/decline", handle_decline)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/one", _opt)
    r.add_get("/api/operator/orders/{oid}/one", handle_one)
    r.add_route("OPTIONS", "/api/operator/customer/{cid}", _opt)
    r.add_get("/api/operator/customer/{cid}", handle_customer)
    r.add_route("OPTIONS", "/api/operator/customer/{cid}/act", _opt)
    r.add_post("/api/operator/customer/{cid}/act", handle_customer_act)
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
    r.add_route("OPTIONS", "/api/operator/feed", _opt)
    r.add_get("/api/operator/feed", handle_feed)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/driver-req", _opt)
    r.add_post("/api/operator/orders/{oid}/driver-req", handle_driver_req)
    r.add_route("OPTIONS", "/api/operator/orders/{oid}/chat", _opt)
    r.add_post("/api/operator/orders/{oid}/chat", handle_chat_send)
    r.add_route("OPTIONS", "/api/operator/driver-chats", _opt)
    r.add_get("/api/operator/driver-chats", handle_driver_chats)
    r.add_route("OPTIONS", "/api/operator/support", _opt)
    r.add_get("/api/operator/support", handle_support_list)
    r.add_route("OPTIONS", "/api/operator/support/thread", _opt)
    r.add_get("/api/operator/support/thread", handle_support_thread)
    r.add_route("OPTIONS", "/api/operator/support/send", _opt)
    r.add_post("/api/operator/support/send", handle_support_send)
    r.add_route("OPTIONS", "/api/operator/support/customers", _opt)
    r.add_get("/api/operator/support/customers", handle_support_customers)
    log.info("[pos] operator routes mounted")
