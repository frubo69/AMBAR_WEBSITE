#!/usr/bin/env python3
"""
AMBAR Operator Bot — MongoDB edition
- View new / active / completed orders per office
- Accept → ETA → countdown timer → delivered
- Edit order items (add / remove / change qty)
- Ban / unban customers
- Stats
"""
import os, asyncio, logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, MenuButtonCommands, MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import db

load_dotenv()
OPERATOR_BOT_TOKEN   = os.getenv("OPERATOR_BOT_TOKEN", "")
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
OPERATOR_IDS         = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]
WEBAPP_URL           = os.getenv("WEBAPP_URL", "")
SUPPORT_BOT_USERNAME = "ambar_support_bot"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

PRODUCTS = [
    {"id":"p1",  "name":"Hennessy VS",         "price":280},
    {"id":"p2",  "name":"Hennessy VSOP",        "price":380},
    {"id":"p3",  "name":"Hennessy XO",          "price":880},
    {"id":"p4",  "name":"Johnnie Walker Black", "price":230},
    {"id":"p5",  "name":"Johnnie Walker Blue",  "price":720},
    {"id":"p6",  "name":"Jack Daniel's",        "price":200},
    {"id":"p7",  "name":"Grey Goose",           "price":260},
    {"id":"p8",  "name":"Belvedere",            "price":290},
    {"id":"p9",  "name":"Moët & Chandon Brut",  "price":320},
    {"id":"p10", "name":"Dom Pérignon",         "price":1200},
    {"id":"p11", "name":"Don Julio Blanco",     "price":350},
    {"id":"p12", "name":"Bacardi Blanca",       "price":150},
    {"id":"p13", "name":"Bombay Sapphire",      "price":210},
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_operator(uid):
    return not OPERATOR_IDS or uid in OPERATOR_IDS

def get_operator_office(uid):
    try:
        from config_offices import OFFICE_OPERATORS
        for oid, ops in OFFICE_OPERATORS.items():
            if uid in ops:
                return oid
    except: pass
    return None


DUBAI_TZ = timezone(offset=__import__('datetime').timedelta(hours=4))

def order_summary_label(o):
    """Compact one-line label for order list buttons."""
    items = o.get("items", [])
    n_items = sum(i.get("qty", 1) for i in items)
    # Show confirmed_at time (Dubai) if available, else order timestamp
    time_str = ""
    for key in ("confirmed_at", "timestamp"):
        raw = o.get(key, "")
        if raw:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                dt_dubai = dt.astimezone(DUBAI_TZ)
                time_str = dt_dubai.strftime("%H:%M")
            except: pass
            break
    parts = [f"#{o['order_id']}"]
    if time_str:
        parts.append(time_str)
    parts.append(f"{n_items} поз.")
    parts.append(f"{o.get('total',0)} AED")
    return " · ".join(parts)


# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_main():
    return ReplyKeyboardMarkup([
        ["🆕 Новые заказы",   "🟢 Активные"],
        ["✅ Завершённые",    "📊 Статистика"],
        ["🚫 Забаненные",     "ℹ️ Помощь"],
    ], resize_keyboard=True)

def kb_order_list(items, list_type, limit=15):
    """Compact list of orders as inline buttons."""
    rows = []
    for o in items[:limit]:
        rows.append([InlineKeyboardButton(
            order_summary_label(o),
            callback_data=f"osel_{list_type}_{o['order_id']}"
        )])
    rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
    return InlineKeyboardMarkup(rows)

def kb_order_actions(order, list_type=None):
    oid, cid = order["order_id"], order["customer_id"]
    st       = order.get("status", "")
    rows     = []
    if st == "pending":
        rows.append([
            InlineKeyboardButton("✅ Принять",   callback_data=f"acc_{oid}_{cid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{oid}_{cid}"),
        ])
    if st == "approved":
        rows.append([InlineKeyboardButton(f"🚚 Доставлено #{oid}", callback_data=f"done_{oid}_{cid}")])
    rows.append([
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{oid}"),
        InlineKeyboardButton("📍 Геолокация",    callback_data=f"loc_{oid}"),
    ])
    rows.append([InlineKeyboardButton("👤 Клиент", callback_data=f"client_{oid}_{cid}")])
    if list_type:
        rows.append([InlineKeyboardButton("← К списку", callback_data=f"olist_{list_type}")])
    return InlineKeyboardMarkup(rows)


def kb_client_actions(oid, cid):
    """Keyboard shown on client info view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Забанить клиента",      callback_data=f"ban_{oid}_{cid}")],
        [InlineKeyboardButton("✏️ Переименовать клиента", callback_data=f"rename_{oid}_{cid}")],
        [InlineKeyboardButton("← Назад",                  callback_data=f"client_back_{oid}")],
    ])

def kb_eta(oid, cid):
    r1 = [InlineKeyboardButton(f"⏱ {t} мин", callback_data=f"eta_{t}_{oid}_{cid}") for t in [20, 30, 45]]
    r2 = [InlineKeyboardButton(f"⏱ {t} мин", callback_data=f"eta_{t}_{oid}_{cid}") for t in [60, 90, 120]]
    return InlineKeyboardMarkup([r1, r2])

def kb_edit(order):
    oid  = order["order_id"]
    rows = []
    for item in order.get("items", []):
        pid, name, qty = item["id"], item["name"], item["qty"]
        rows.append([
            InlineKeyboardButton(f"{name}  ×{qty}", callback_data="noop"),
            InlineKeyboardButton("➖", callback_data=f"ei_dec_{oid}_{pid}"),
            InlineKeyboardButton("➕", callback_data=f"ei_inc_{oid}_{pid}"),
            InlineKeyboardButton("🗑",  callback_data=f"ei_del_{oid}_{pid}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить товар", callback_data=f"ei_add_{oid}")])
    rows.append([InlineKeyboardButton("✅ Готово",         callback_data=f"edit_done_{oid}")])
    return InlineKeyboardMarkup(rows)

def kb_add_product(oid):
    rows = [[InlineKeyboardButton(f"{p['name']}  {p['price']} AED", callback_data=f"ei_addp_{oid}_{p['id']}")] for p in PRODUCTS]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data=f"edit_{oid}")])
    return InlineKeyboardMarkup(rows)

def kb_ban_confirm(cid, oid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Без причины",     callback_data=f"ban_skip_{cid}_{oid}")],
        [InlineKeyboardButton("✏️ Ввести причину", callback_data=f"ban_input_{cid}_{oid}")],
        [InlineKeyboardButton("← Отмена",           callback_data=f"ban_cancel_{oid}_{cid}")],
    ])


# ── Order card formatter ──────────────────────────────────────────────────────
def order_card(o, full=True):
    st_map = {"pending":"🟡 Ожидает","approved":"🟢 Принят","delivered":"✅ Доставлен","declined":"🔴 Отклонён","cancelled":"🚫 Отменён клиентом"}
    st     = st_map.get(o.get("status",""), o.get("status",""))
    lines  = [f"🏢 Офис: *{o.get('office_name','—')}*", ""]
    lines.append(f"🆕 *НОВЫЙ ЗАКАЗ #{o['order_id']}*")
    lines.append("")
    if full:
        gmap = o.get("gmap_link","")
        addr = o.get("address","—")
        if gmap:
            lines.append(f"🏠 Адрес: {addr}" if addr and addr != "GPS" and addr != "—" else "🏠 Адрес: GPS")
            lines.append(f"Google Maps: {gmap}")
        else:
            lines.append(f"🏠 Адрес: {addr}")
        lines.append("")
    lines.append("🛒 *Позиции:*")
    for item in o.get("items", []):
        lt = item.get("line_total", item["price"] * item["qty"])
        lines.append(f"  • {item['name']} ×{item['qty']} = {lt} AED")
    lines.append("")
    if o.get("tip"): lines.append(f"🎁 Чаевые: {o['tip']} AED")
    lines.append(f"💰 *Итого: {o.get('total',0)} AED*")
    comment = o.get("comment", "").strip()
    if comment:
        lines.append("")
        lines.append(f"💬 Комментарий: {comment}")
    return "\n".join(lines)


def customer_card(o):
    """Customer info card — shown when operator clicks 'Клиент'."""
    lines = [
        f"👤 *{o.get('customer_name','—')}*",
        f"📞 `{o.get('phone','—')}`",
        f"🔗 @{o.get('username','—')}  |  ID: `{o.get('customer_id','—')}`",
    ]
    return "\n".join(lines)


def recalc_order(order):
    pmap  = {p["id"]: p for p in PRODUCTS}
    items = order.get("items", [])
    for item in items:
        p = pmap.get(item["id"])
        if p: item["line_total"] = p["price"] * item["qty"]
    sub            = sum(i.get("line_total", 0) for i in items)
    order["subtotal"] = sub
    order["total"]    = sub + order.get("tip", 0)
    return order


# ── Customer notification via main bot ────────────────────────────────────────
async def notify(cid, text, reply_markup=None):
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        async with app:
            return await app.bot.send_message(cid, text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        log.error(f"notify {cid}: {e}")
        return None


# ── Cleanup + deliver ─────────────────────────────────────────────────────────
async def cleanup_and_deliver(cid: int, oid: str, lang: str):
    order = await db.get_order(oid)
    if not order: return

    items      = order.get("items", [])
    total      = order.get("total", 0)
    msg_ids    = order.get("customer_msg_ids", [])
    item_lines = "\n".join(f"  • {i['name']} ×{i['qty']}" for i in items)
    review_kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(i), callback_data=f"rev_{i}_{cid}_{lang}_{oid}") for i in range(1, 6)
    ]])
    if lang == "ru":
        summary = f"✅ *Заказ #{oid} доставлен!*\n\n🛒 *Позиции:*\n{item_lines}\n\n💰 *Итого: {total} AED*"
        thanks  = ("Спасибо 🥂\n\nОцените сервис:\n\n"
                   "_После оценки можете написать нам — просто отправьте сообщение в этот чат._")
    else:
        summary = f"✅ *Order #{oid} delivered!*\n\n🛒 *Items:*\n{item_lines}\n\n💰 *Total: {total} AED*"
        thanks  = ("Thank you 🥂\n\nRate our service:\n\n"
                   "_After rating you can leave a comment — just send a message here._")

    tmp = Application.builder().token(BOT_TOKEN).build()
    async with tmp:
        for mid in msg_ids:
            try: await tmp.bot.delete_message(cid, mid)
            except Exception as e: log.debug(f"del msg {mid}: {e}")
        try: await tmp.bot.send_message(cid, summary, parse_mode="Markdown",
                                         reply_markup=ReplyKeyboardRemove())
        except Exception as e: log.error(f"delivery summary {cid}: {e}")
        try:
            rate_msg = await tmp.bot.send_message(cid, thanks, parse_mode="Markdown", reply_markup=review_kb)
            await db.set_ustate(cid, {"to_delete_on_order": [rate_msg.message_id], "awaiting_comment": False})
        except Exception as e: log.error(f"review msg {cid}: {e}")


# ── Countdown timer ───────────────────────────────────────────────────────────
async def run_countdown(cid, eta_min, lang, oid=None):
    import time as tm
    T = {
        "ru": {"s": f"⏱ *Курьер в пути!*\n\nОсталось: *{eta_min} мин*",
               "t": "🚚 *Доставка в пути*\n\nОсталось: *{m} мин {s} сек*"},
        "en": {"s": f"⏱ *Courier is on the way!*\n\nTime left: *{eta_min} min*",
               "t": "🚚 *Delivery in progress*\n\nTime left: *{m} min {s} sec*"},
    }
    tx  = T.get(lang, T["ru"])
    app = Application.builder().token(BOT_TOKEN).build()
    async with app:
        try: msg = await app.bot.send_message(cid, tx["s"], parse_mode="Markdown")
        except: return
        if oid:
            order = await db.get_order(oid)
            if order:
                ids = order.get("customer_msg_ids", []) + [msg.message_id]
                await db.update_order(oid, customer_msg_ids=ids)
        end = tm.time() + eta_min * 60
        while True:
            await asyncio.sleep(30)
            rem = int(end - tm.time())
            if rem <= 0: break
            if oid:
                o = await db.get_order(oid)
                if (o or {}).get("status") in ("delivered", "cancelled"):
                    return
            try:
                await app.bot.edit_message_text(
                    tx["t"].format(m=rem//60, s=rem%60),
                    chat_id=cid, message_id=msg.message_id, parse_mode="Markdown")
            except: break

    if oid:
        order = await db.get_order(oid)
        if not order or order.get("status") in ("delivered", "cancelled"):
            return
        log.info(f"ETA expired for order {oid}, awaiting manual delivery confirmation")


# ── Helper: fetch & build order list ─────────────────────────────────────────
async def _build_order_list(list_type, operator_uid):
    """Fetch orders and return (header_text, sorted_items, list_type)."""
    off = get_operator_office(operator_uid)
    all_orders = await db.get_all_orders(off)
    if list_type == "n":
        items = sorted([o for o in all_orders.values() if o.get("status") == "pending"],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        header = f"🆕 *Новых заказов: {len(items)}*"
        empty  = "✅ Новых заказов нет."
    elif list_type == "a":
        items = sorted([o for o in all_orders.values() if o.get("status") == "approved"],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        header = f"🟢 *Активных: {len(items)}*"
        empty  = "✅ Активных нет."
    else:
        items = sorted([o for o in all_orders.values() if o.get("status") in ("delivered","declined","cancelled")],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        header = f"✅ *Завершённых: {len(items)}*"
        empty  = "Нет завершённых."
    return header, empty, items


# ── Menu handler ──────────────────────────────────────────────────────────────
async def handle_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return

    # ── Intercept ban reason input ─────────────────────────────────────────────
    pending = ctx.user_data.get("pending_ban")
    if pending:
        ctx.user_data.pop("pending_ban")
        op     = update.effective_user.id
        reason = (update.message.text or "").strip()
        await _do_ban(op, pending["cid"], pending["oid"], reason)
        display = reason or "Заблокирован оператором"
        try: await update.message.delete()
        except: pass
        if pending.get("msg_id"):
            try:
                await ctx.bot.edit_message_text(
                    f"🚫 *Клиент заблокирован*\n\nID: `{pending['cid']}`\n💬 Причина: _{display}_",
                    chat_id=update.effective_chat.id,
                    message_id=pending["msg_id"],
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("← К заказу", callback_data=f"back_order_{pending['oid']}")
                    ]]))
            except: pass
        return

    # ── Intercept rename input ──────────────────────────────────────────────────
    pending_rename = ctx.user_data.get("pending_rename")
    if pending_rename:
        ctx.user_data.pop("pending_rename")
        new_name = (update.message.text or "").strip()
        cid = pending_rename["cid"]
        oid = pending_rename["oid"]
        try: await update.message.delete()
        except: pass
        if new_name:
            await db.upsert_user(cid, name=new_name, full_name=new_name, custom_name=new_name)
            # Update name on ALL orders from this customer
            all_orders = await db.get_all_orders()
            for o in all_orders.values():
                if o.get("customer_id") == cid:
                    await db.update_order(o["order_id"], customer_name=new_name)
            if pending_rename.get("msg_id"):
                order = await db.get_order(oid)
                if order:
                    try:
                        await ctx.bot.edit_message_text(
                            customer_card(order),
                            chat_id=update.effective_chat.id,
                            message_id=pending_rename["msg_id"],
                            parse_mode="Markdown",
                            reply_markup=kb_client_actions(oid, cid))
                    except: pass
        return

    text = update.message.text
    uid  = update.effective_user.id

    # Delete the operator's menu tap to keep chat clean
    try: await update.message.delete()
    except: pass

    cid = update.effective_chat.id
    send = ctx.bot.send_message  # shortcut — original message is deleted
    _dismiss = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])

    if "Новые" in text:
        header, empty, items = await _build_order_list("n", uid)
        if not items:
            await send(cid, empty, reply_markup=_dismiss); return
        await send(cid, header + "\n\nНажмите на заказ для просмотра:",
                   parse_mode="Markdown", reply_markup=kb_order_list(items, "n"))

    elif "Активные" in text:
        header, empty, items = await _build_order_list("a", uid)
        if not items:
            await send(cid, empty, reply_markup=_dismiss); return
        await send(cid, header + "\n\nНажмите на заказ для просмотра:",
                   parse_mode="Markdown", reply_markup=kb_order_list(items, "a"))

    elif "Завершённые" in text:
        header, empty, items = await _build_order_list("d", uid)
        if not items:
            await send(cid, "Нет завершённых.", reply_markup=_dismiss); return
        await send(cid, header + "\n\nНажмите на заказ для просмотра:",
                   parse_mode="Markdown", reply_markup=kb_order_list(items, "d"))

    elif "Статистика" in text:
        off = get_operator_office(uid)
        all_orders = await db.get_all_orders(off)
        today = datetime.now().strftime("%Y-%m-%d")
        tod   = [o for o in all_orders.values() if o.get("timestamp","").startswith(today)]
        rev   = sum(o.get("total",0) for o in tod if o.get("status")=="delivered")
        await send(cid,
            f"📊 *Статистика сегодня — {today}*\n\n"
            f"🆕 Новых: *{len([o for o in tod if o.get('status')=='pending'])}*\n"
            f"🟢 Принято: *{len([o for o in tod if o.get('status')=='approved'])}*\n"
            f"✅ Доставлено: *{len([o for o in tod if o.get('status')=='delivered'])}*\n"
            f"🔴 Отклонено: *{len([o for o in tod if o.get('status')=='declined'])}*\n"
            f"📦 Всего: *{len(tod)}*\n\n"
            f"💰 *Выручка: {int(rev)} AED*",
            parse_mode="Markdown", reply_markup=_dismiss)

    elif "Забаненные" in text:
        banned = await db.get_all_banned()
        if not banned:
            await send(cid, "✅ Забаненных нет.", reply_markup=_dismiss); return
        lines = ["🚫 *Заблокированные пользователи:*\n"]
        rows  = []
        for u in banned[:15]:
            uid_str = str(u.get("telegram_id", u.get("tg_id", "?")))
            ts      = (u.get("banned_at","") or "")[:10]
            lines.append(f"• ID `{uid_str}` — {u.get('ban_reason','—')} ({ts})")
            rows.append([InlineKeyboardButton(f"🔓 Разбанить {uid_str}", callback_data=f"unban_{uid_str}")])
        rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await send(cid, "\n".join(lines), parse_mode="Markdown",
                   reply_markup=InlineKeyboardMarkup(rows) if rows else None)

    elif "Помощь" in text:
        await send(cid,
            "ℹ️ *AMBAR — Оператор*\n\n"
            "🆕 *Новые* — входящие заказы\n"
            "🟢 *Активные* — принятые, в доставке\n"
            "✅ *Завершённые* — история\n"
            "📊 *Статистика* — сводка за сегодня\n"
            "🚫 *Забаненные* — заблокированные клиенты\n\n"
            "На каждом заказе есть кнопки:\n"
            "✅ Принять → выбрать время → таймер запускается\n"
            "✏️ Редактировать → добавить/убрать позиции\n"
            "📍 Геолокация → увидеть точку клиента\n"
            "🚫 Забанить → заблокировать клиента",
            parse_mode="Markdown", reply_markup=_dismiss)

    else:
        await send(cid, "Используйте кнопки меню 👇", reply_markup=_dismiss)


# ── Start ─────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа."); return
    await update.effective_message.reply_text(
        "🛠 *AMBAR — Панель оператора*\n\nВыберите действие:",
        parse_mode="Markdown", reply_markup=kb_main())


# ── Inline callbacks ──────────────────────────────────────────────────────────
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data
    op   = update.effective_user.id

    if data == "noop": return

    # ── ORDER LIST: select order ─────────────────────────────────────────────
    if data.startswith("osel_"):
        parts = data.split("_", 2)  # osel, list_type, oid
        lt  = parts[1]
        oid = parts[2]
        ctx.user_data["lt"] = lt  # persist list context for sub-views
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(
                order_card(order), parse_mode="Markdown",
                reply_markup=kb_order_actions(order, list_type=lt))
        else:
            await q.answer("❌ Заказ не найден", show_alert=True)

    # ── ORDER LIST: back to list ─────────────────────────────────────────────
    elif data.startswith("olist_"):
        lt = data[6:]
        header, empty, items = await _build_order_list(lt, op)
        if not items:
            await q.edit_message_text(empty)
            return
        await q.edit_message_text(
            header + "\n\nНажмите на заказ для просмотра:",
            parse_mode="Markdown",
            reply_markup=kb_order_list(items, lt))

    # ── ACCEPT → show ETA ────────────────────────────────────────────────────
    elif data.startswith("acc_"):
        _, oid, cid = data.split("_", 2)
        order = await db.get_order(oid)
        if order and order.get("status") == "cancelled":
            await q.answer("🚫 Заказ отменён клиентом", show_alert=True)
            try: await q.edit_message_reply_markup(reply_markup=None)
            except: pass
            return
        await q.edit_message_reply_markup(reply_markup=kb_eta(oid, cid))

    # ── ETA selected ─────────────────────────────────────────────────────────
    elif data.startswith("eta_"):
        parts = data.split("_")
        eta, oid, cid = int(parts[1]), parts[2], int(parts[3])
        await db.update_order(oid, status="approved", eta=eta,
                              operator_id=op, updated_at=datetime.now().isoformat(),
                              confirmed_at=datetime.now(timezone.utc).isoformat())
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        tx    = {"ru": f"✅ *Заказ #{oid} принят!*\n\n🕐 Доставка через *{eta} минут*",
                 "en": f"✅ *Order #{oid} confirmed!*\n\n🕐 Delivery in *{eta} minutes*"}
        acc_msg = await notify(cid, tx.get(lang, tx["ru"]))
        if acc_msg:
            o = await db.get_order(oid)
            if o:
                ids = o.get("customer_msg_ids", []) + [acc_msg.message_id]
                await db.update_order(oid, customer_msg_ids=ids)
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(
                order_card(order) + f"\n\n✅ *Принят* | ⏱ {eta} мин",
                parse_mode="Markdown", reply_markup=kb_order_actions(order, list_type=lt))
        asyncio.create_task(run_countdown(cid, eta, lang, oid))

    # ── DECLINE ───────────────────────────────────────────────────────────────
    elif data.startswith("dec_"):
        _, oid, cid = data.split("_", 2); cid = int(cid)
        order_chk = await db.get_order(oid)
        if order_chk and order_chk.get("status") == "cancelled":
            await q.answer("🚫 Заказ уже отменён клиентом", show_alert=True)
            try: await q.edit_message_reply_markup(reply_markup=None)
            except: pass
            return
        await db.update_order(oid, status="declined", updated_at=datetime.now().isoformat())
        await db._increment_user(cid, orders_declined=1)
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        tx    = {"ru": f"❌ *Заказ #{oid} отменён.*", "en": f"❌ *Order #{oid} cancelled.*"}
        await notify(cid, tx.get(lang, tx["ru"]))
        if order:
            _done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
            await q.edit_message_text(
                order_card(order) + "\n\n❌ *Отклонён*",
                parse_mode="Markdown", reply_markup=_done_kb)

    # ── DELIVERED ─────────────────────────────────────────────────────────────
    elif data.startswith("done_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        await db.update_order(oid, status="delivered", updated_at=datetime.now().isoformat())
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        total = (order or {}).get("total", 0)
        await db._increment_user(cid, orders_done=1, total_spent=total)
        await cleanup_and_deliver(cid, oid, lang)
        if order:
            _done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
            await q.edit_message_text(
                order_card(order) + "\n\n✅ *Доставлен*",
                parse_mode="Markdown", reply_markup=_done_kb)

    # ── BACK TO ORDER (from sub-views) ────────────────────────────────────────
    elif data.startswith("back_order_"):
        oid = data[len("back_order_"):]
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(order_card(order), parse_mode="Markdown", reply_markup=kb_order_actions(order, list_type=lt))

    # ── LOCATION ──────────────────────────────────────────────────────────────
    elif data.startswith("loc_"):
        oid   = data[4:]
        order = await db.get_order(oid)
        if not order: await q.answer("❌ Заказ не найден", show_alert=True); return
        loc   = order.get("location", {})
        if loc.get("lat"):
            loc_msg = await q.message.reply_location(
                latitude=loc["lat"], longitude=loc["lon"],
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Просмотрел", callback_data=f"delloc_{loc['lat']}_{loc['lon']}")
                ]]))
        else:
            await q.answer("📍 GPS недоступен для этого заказа", show_alert=True)

    # ── DELETE message (location / list / empty) ────────────────────────────
    elif data in ("delmsg",) or data.startswith("delloc_"):
        try: await q.message.delete()
        except: pass

    # ── EDIT done ─────────────────────────────────────────────────────────────
    elif data.startswith("edit_done_"):
        oid   = data[len("edit_done_"):]
        order = await db.get_order(oid)
        if not order: return
        lt = ctx.user_data.get("lt")
        await q.edit_message_text(order_card(order), parse_mode="Markdown",
                                  reply_markup=kb_order_actions(order, list_type=lt))

    elif data.startswith("edit_"):
        oid   = data[5:]
        order = await db.get_order(oid)
        if not order: return
        await q.edit_message_text(
            f"✏️ *Редактирование #{oid}*\n\n"
            + "\n".join(f"  • {i['name']} ×{i['qty']}" for i in order.get("items",[])),
            parse_mode="Markdown", reply_markup=kb_edit(order))

    # ── Item inc/dec/del/add ──────────────────────────────────────────────────
    elif data.startswith("ei_inc_"):
        _, _, oid, pid = data.split("_", 3)
        order = await db.get_order(oid)
        if not order: return
        for item in order.get("items",[]):
            if item["id"] == pid: item["qty"] += 1; break
        order = recalc_order(order)
        await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
        order = await db.get_order(oid)
        try: await q.edit_message_reply_markup(reply_markup=kb_edit(order))
        except: pass

    elif data.startswith("ei_dec_"):
        _, _, oid, pid = data.split("_", 3)
        order = await db.get_order(oid)
        if not order: return
        for item in order.get("items",[]):
            if item["id"] == pid and item["qty"] > 1: item["qty"] -= 1; break
        order = recalc_order(order)
        await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
        order = await db.get_order(oid)
        try: await q.edit_message_reply_markup(reply_markup=kb_edit(order))
        except: pass

    elif data.startswith("ei_del_"):
        _, _, oid, pid = data.split("_", 3)
        order = await db.get_order(oid)
        if not order: return
        order["items"] = [i for i in order.get("items",[]) if i["id"] != pid]
        order = recalc_order(order)
        await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
        order = await db.get_order(oid)
        try: await q.edit_message_reply_markup(reply_markup=kb_edit(order))
        except: pass

    elif data.startswith("ei_add_"):
        oid = data[7:]
        await q.edit_message_reply_markup(reply_markup=kb_add_product(oid))

    elif data.startswith("ei_addp_"):
        parts = data.split("_"); oid, pid = parts[2], parts[3]
        order = await db.get_order(oid)
        if not order: return
        pmap  = {p["id"]: p for p in PRODUCTS}
        p     = pmap.get(pid)
        if not p: return
        items = order.get("items", [])
        for item in items:
            if item["id"] == pid: item["qty"] += 1; break
        else:
            items.append({"id": pid, "name": p["name"], "price": p["price"], "qty": 1, "line_total": p["price"]})
        order["items"] = items
        order = recalc_order(order)
        await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
        order = await db.get_order(oid)
        try: await q.edit_message_reply_markup(reply_markup=kb_edit(order))
        except: pass

    # ── BAN — specific prefixes first ─────────────────────────────────────────
    elif data.startswith("ban_skip_"):
        parts = data.split("_"); cid = int(parts[2]); oid = parts[3]
        ctx.user_data.pop("pending_ban", None)
        await _do_ban(op, cid, oid, "")
        await q.edit_message_text(
            f"🚫 *Клиент заблокирован*\n\nID: `{cid}`\nЗаказ: `#{oid}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← К заказу", callback_data=f"back_order_{oid}")
            ]]))

    elif data.startswith("ban_input_"):
        parts = data.split("_")
        cid = int(parts[2]); oid = parts[3]
        ctx.user_data["pending_ban"] = {"cid": cid, "oid": oid, "msg_id": q.message.message_id}
        await q.edit_message_text(
            f"✏️ *Введите причину блокировки*\n\nКлиент: `{cid}`\n\n_Отправьте текст сообщением_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Отмена", callback_data=f"client_{oid}_{cid}")
            ]]))

    elif data.startswith("ban_cancel_"):
        parts = data.split("_")
        oid = parts[2]; cid = int(parts[3])
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(customer_card(order), parse_mode="Markdown", reply_markup=kb_client_actions(oid, cid))

    # ── CLIENT INFO VIEW ───────────────────────────────────────────────────────
    elif data.startswith("client_back_"):
        oid = data[len("client_back_"):]
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(order_card(order), parse_mode="Markdown", reply_markup=kb_order_actions(order, list_type=lt))

    elif data.startswith("client_"):
        _, oid, cid_str = data.split("_", 2)
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(customer_card(order), parse_mode="Markdown", reply_markup=kb_client_actions(oid, int(cid_str)))

    # ── RENAME CLIENT ────────────────────────────────────────────────────────
    elif data.startswith("rename_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        ctx.user_data["pending_rename"] = {"cid": cid, "oid": oid, "msg_id": q.message.message_id}
        await q.edit_message_text(
            f"✏️ *Переименовать клиента*\n\nID: `{cid}`\n\n_Отправьте новое имя сообщением_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Отмена", callback_data=f"client_{oid}_{cid}")
            ]]))

    # ── BAN (generic — show confirmation) ─────────────────────────────────────
    elif data.startswith("ban_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        await q.edit_message_text(
            f"⚠️ *Блокировка клиента* `{cid}`\n\nВыберите действие:",
            parse_mode="Markdown", reply_markup=kb_ban_confirm(cid, oid))

    # ── UNBAN ─────────────────────────────────────────────────────────────────
    elif data.startswith("unban_"):
        uid_str = data[6:]
        cid = int(uid_str)
        user_doc = await db.get_user(cid)
        ban_msg_id = user_doc.get("last_ban_msg_id") if user_doc else None
        await db.unban_user(cid)
        try:
            app2 = Application.builder().token(BOT_TOKEN).build()
            async with app2:
                if ban_msg_id:
                    try:
                        await app2.bot.delete_message(chat_id=cid, message_id=ban_msg_id)
                    except: pass
                await app2.bot.send_message(
                    chat_id=cid,
                    text="✅ *Ваш аккаунт разблокирован!*\n\nТеперь вы снова можете делать заказы. Нажмите кнопку ниже 👇",
                    parse_mode="Markdown"
                )
                await app2.bot.set_chat_menu_button(
                    chat_id=cid,
                    menu_button=MenuButtonWebApp(text="🍾 Заказать", web_app=WebAppInfo(url=WEBAPP_URL))
                )
        except: pass
        await q.edit_message_text(f"✅ Пользователь `{uid_str}` разблокирован.", parse_mode="Markdown")


# ── Ban helper ────────────────────────────────────────────────────────────────
async def _do_ban(op: int, cid: int, oid: str, reason: str):
    """Ban user, send ban message with support button, save msg_id."""
    final_reason = reason.strip() or "Заблокирован оператором"
    await db.ban_user(cid, reason=final_reason, by=op)
    try:
        app2 = Application.builder().token(BOT_TOKEN).build()
        async with app2:
            ban_msg = await app2.bot.send_message(
                cid,
                "🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку — нажмите кнопку ниже.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💬 Написать в поддержку", url="https://t.me/ambar_support_bot")
                ]])
            )
            await db.set_user_field(cid, last_ban_msg_id=ban_msg.message_id)
    except: pass



# ── Init ──────────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await db.connect()


def main():
    if not OPERATOR_BOT_TOKEN: print("❌ OPERATOR_BOT_TOKEN missing"); return
    app = Application.builder().token(OPERATOR_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(CallbackQueryHandler(cb))
    log.info("🛠 AMBAR Operator Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
