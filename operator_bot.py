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
from datetime import datetime
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


# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_main():
    return ReplyKeyboardMarkup([
        ["🆕 Новые заказы",   "🟢 Активные"],
        ["✅ Завершённые",    "📊 Статистика"],
        ["🚫 Забаненные",     "ℹ️ Помощь"],
    ], resize_keyboard=True)

def kb_order_actions(order):
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
    rows.append([InlineKeyboardButton("🚫 Забанить клиента", callback_data=f"ban_{oid}_{cid}")])
    return InlineKeyboardMarkup(rows)

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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Да, заблокировать", callback_data=f"ban_confirm_{cid}_{oid}"),
        InlineKeyboardButton("❌ Отмена",             callback_data=f"ban_cancel_{oid}"),
    ]])


# ── Order card formatter ──────────────────────────────────────────────────────
def order_card(o, full=True):
    st_map = {"pending":"🟡 Ожидает","approved":"🟢 Принят","delivered":"✅ Доставлен","declined":"🔴 Отклонён"}
    ts     = o.get("timestamp","")[:16].replace("T"," ")
    st     = st_map.get(o.get("status",""), o.get("status",""))
    lines  = [f"📦 *Заказ #{o['order_id']}*  |  {st}", f"🕐 {ts}  |  🏢 {o.get('office_name','—')}", ""]
    if full:
        lines += [
            f"👤 *{o.get('customer_name','—')}*",
            f"📞 Телефон: `{o.get('phone','—')}`",
            f"🔗 @{o.get('username','—')}  |  ID: `{o.get('customer_id','—')}`",
            f"🏠 Адрес: {o.get('address','—')}",
        ]
        loc = o.get("location", {})
        if loc.get("lat"):
            lines.append(f"📍 GPS: {loc['lat']:.5f}, {loc['lon']:.5f}")
        lines.append("")
    lines.append("🛒 *Позиции:*")
    for item in o.get("items", []):
        lt = item.get("line_total", item["price"] * item["qty"])
        lines.append(f"  • {item['name']} ×{item['qty']} = {lt} AED")
    lines.append("")
    if o.get("tip"): lines.append(f"🎁 Чаевые: {o['tip']} AED")
    lines.append(f"💰 *Итого: {o.get('total',0)} AED*")
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
                if (o or {}).get("status") == "delivered":
                    return
            try:
                await app.bot.edit_message_text(
                    tx["t"].format(m=rem//60, s=rem%60),
                    chat_id=cid, message_id=msg.message_id, parse_mode="Markdown")
            except: break

    if oid:
        order = await db.get_order(oid)
        if not order or order.get("status") == "delivered":
            return
        await db.update_order(oid, status="delivered", updated_at=datetime.now().isoformat())
    await cleanup_and_deliver(cid, oid, lang)


# ── Menu handler ──────────────────────────────────────────────────────────────
async def handle_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return

    text = update.message.text
    uid  = update.effective_user.id
    off  = get_operator_office(uid)
    all_orders = await db.get_all_orders(off)

    if "Новые" in text:
        items = sorted([o for o in all_orders.values() if o.get("status") == "pending"],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        if not items:
            await update.message.reply_text("✅ Новых заказов нет.", reply_markup=kb_main()); return
        await update.message.reply_text(f"🆕 *Новых заказов: {len(items)}*", parse_mode="Markdown", reply_markup=kb_main())
        for o in items[:10]:
            await update.message.reply_text(order_card(o), parse_mode="Markdown", reply_markup=kb_order_actions(o))

    elif "Активные" in text:
        items = sorted([o for o in all_orders.values() if o.get("status") == "approved"],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        if not items:
            await update.message.reply_text("✅ Активных нет.", reply_markup=kb_main()); return
        await update.message.reply_text(f"🟢 *Активных: {len(items)}*", parse_mode="Markdown", reply_markup=kb_main())
        for o in items[:10]:
            await update.message.reply_text(order_card(o), parse_mode="Markdown", reply_markup=kb_order_actions(o))

    elif "Завершённые" in text:
        items = sorted([o for o in all_orders.values() if o.get("status") in ("delivered","declined")],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        if not items:
            await update.message.reply_text("Нет завершённых.", reply_markup=kb_main()); return
        await update.message.reply_text(f"✅ *Завершённых: {len(items)}*", parse_mode="Markdown", reply_markup=kb_main())
        for o in items[:15]:
            await update.message.reply_text(order_card(o, full=False), parse_mode="Markdown")

    elif "Статистика" in text:
        today = datetime.now().strftime("%Y-%m-%d")
        tod   = [o for o in all_orders.values() if o.get("timestamp","").startswith(today)]
        rev   = sum(o.get("total",0) for o in tod if o.get("status")=="delivered")
        await update.message.reply_text(
            f"📊 *Статистика сегодня — {today}*\n\n"
            f"🆕 Новых: *{len([o for o in tod if o.get('status')=='pending'])}*\n"
            f"🟢 Принято: *{len([o for o in tod if o.get('status')=='approved'])}*\n"
            f"✅ Доставлено: *{len([o for o in tod if o.get('status')=='delivered'])}*\n"
            f"🔴 Отклонено: *{len([o for o in tod if o.get('status')=='declined'])}*\n"
            f"📦 Всего: *{len(tod)}*\n\n"
            f"💰 *Выручка: {int(rev)} AED*",
            parse_mode="Markdown", reply_markup=kb_main())

    elif "Забаненные" in text:
        banned = await db.get_all_banned()
        if not banned:
            await update.message.reply_text("✅ Забаненных нет.", reply_markup=kb_main()); return
        lines = ["🚫 *Заблокированные пользователи:*\n"]
        rows  = []
        for u in banned[:15]:
            uid_str = str(u.get("telegram_id", u.get("tg_id", "?")))
            ts      = (u.get("banned_at","") or "")[:10]
            lines.append(f"• ID `{uid_str}` — {u.get('ban_reason','—')} ({ts})")
            rows.append([InlineKeyboardButton(f"🔓 Разбанить {uid_str}", callback_data=f"unban_{uid_str}")])
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows) if rows else None)

    elif "Помощь" in text:
        await update.message.reply_text(
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
            parse_mode="Markdown", reply_markup=kb_main())

    else:
        await update.message.reply_text("Используйте кнопки меню 👇", reply_markup=kb_main())


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

    # ── ACCEPT → show ETA ────────────────────────────────────────────────────
    if data.startswith("acc_"):
        _, oid, cid = data.split("_", 2)
        await q.edit_message_reply_markup(reply_markup=kb_eta(oid, cid))

    # ── ETA selected ─────────────────────────────────────────────────────────
    elif data.startswith("eta_"):
        parts = data.split("_")
        eta, oid, cid = int(parts[1]), parts[2], int(parts[3])
        await db.update_order(oid, status="approved", eta=eta,
                              operator_id=op, updated_at=datetime.now().isoformat())
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        name  = order.get("customer_name","") if order else ""
        tx    = {"ru": f"✅ *Заказ #{oid} принят!*\n\n🕐 Доставка через *{eta} минут*",
                 "en": f"✅ *Order #{oid} confirmed!*\n\n🕐 Delivery in *{eta} minutes*"}
        acc_msg = await notify(cid, tx.get(lang, tx["ru"]))
        if acc_msg:
            o = await db.get_order(oid)
            if o:
                ids = o.get("customer_msg_ids", []) + [acc_msg.message_id]
                await db.update_order(oid, customer_msg_ids=ids)
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🚚 Доставлено #{oid}", callback_data=f"done_{oid}_{cid}")
        ]]))
        await q.message.reply_text(
            f"✅ *#{oid}* принят | 👤 {name} | ⏱ {eta} мин\n\nНажмите «Доставлено» после вручения:",
            parse_mode="Markdown")
        asyncio.create_task(run_countdown(cid, eta, lang, oid))

    # ── DECLINE ───────────────────────────────────────────────────────────────
    elif data.startswith("dec_"):
        _, oid, cid = data.split("_", 2); cid = int(cid)
        await db.update_order(oid, status="declined", updated_at=datetime.now().isoformat())
        await db._increment_user(cid, orders_declined=1)
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        tx    = {"ru": f"❌ *Заказ #{oid} отменён.*", "en": f"❌ *Order #{oid} cancelled.*"}
        await notify(cid, tx.get(lang, tx["ru"]))
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"❌ #{oid} — отклонён. Клиент уведомлён.")

    # ── DELIVERED ─────────────────────────────────────────────────────────────
    elif data.startswith("done_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        await db.update_order(oid, status="delivered", updated_at=datetime.now().isoformat())
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        total = (order or {}).get("total", 0)
        await db._increment_user(cid, orders_done=1, total_spent=total)
        await cleanup_and_deliver(cid, oid, lang)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"✅ #{oid} — доставлен. Клиент уведомлён.")

    # ── LOCATION ──────────────────────────────────────────────────────────────
    elif data.startswith("loc_"):
        oid   = data[4:]
        order = await db.get_order(oid)
        if not order: await q.answer("❌ Заказ не найден", show_alert=True); return
        loc   = order.get("location", {})
        if loc.get("lat"):
            await q.message.reply_location(latitude=loc["lat"], longitude=loc["lon"])
            await q.message.reply_text(f"📍 GPS клиента для заказа #{oid}\n🏠 Адрес: {order.get('address','—')}")
        else:
            await q.message.reply_text(f"📍 GPS недоступен\n\n🏠 Адрес: {order.get('address','—')}")

    # ── EDIT done ─────────────────────────────────────────────────────────────
    elif data.startswith("edit_done_"):
        oid   = data[len("edit_done_"):]
        order = await db.get_order(oid)
        if not order: return
        await q.edit_message_text(order_card(order), parse_mode="Markdown",
                                  reply_markup=kb_order_actions(order))

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

    # ── BAN ───────────────────────────────────────────────────────────────────
    elif data.startswith("ban_confirm_"):
        parts = data.split("_"); cid = int(parts[2]); oid = parts[3]
        await db.ban_user(cid, reason="Заблокирован оператором", by=op)
        try:
            app2 = Application.builder().token(BOT_TOKEN).build()
            async with app2:
                # Notify user
                await app2.bot.send_message(
                    cid, "🚫 *Ваш аккаунт заблокирован.*\n\nОбратитесь в поддержку.",
                    parse_mode="Markdown")
                # Remove the "Заказать" Mini App button so they can't open the app
                await app2.bot.set_chat_menu_button(
                    chat_id=cid, menu_button=MenuButtonCommands())
        except: pass
        await q.edit_message_text(
            f"🚫 *Пользователь заблокирован*\n\nID: `{cid}`\nЗаказ: `#{oid}`\nЗаблокировал: оператор `{op}`",
            parse_mode="Markdown")

    elif data.startswith("ban_cancel_"):
        oid   = data[len("ban_cancel_"):]
        order = await db.get_order(oid)
        try:
            if order:
                await q.edit_message_text(
                    f"❌ *Блокировка отменена*\n\nПользователь НЕ заблокирован. Заказ `#{oid}` без изменений.",
                    parse_mode="Markdown", reply_markup=kb_order_actions(order))
            else:
                await q.edit_message_text("❌ Блокировка отменена.", parse_mode="Markdown")
        except: pass

    elif data.startswith("ban_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        await q.message.reply_text(
            f"⚠️ Заблокировать клиента `{cid}`?\n\nОн не сможет пользоваться ботом.",
            parse_mode="Markdown", reply_markup=kb_ban_confirm(cid, oid))

    # ── UNBAN ─────────────────────────────────────────────────────────────────
    elif data.startswith("unban_"):
        uid_str = data[6:]
        await db.unban_user(int(uid_str))
        # Restore the "Заказать" Mini App button
        try:
            app2 = Application.builder().token(BOT_TOKEN).build()
            async with app2:
                await app2.bot.set_chat_menu_button(
                    chat_id=int(uid_str),
                    menu_button=MenuButtonWebApp(text="🍾 Заказать", web_app=WebAppInfo(url=WEBAPP_URL))
                )
        except: pass
        await q.edit_message_text(f"✅ Пользователь `{uid_str}` разблокирован.", parse_mode="Markdown")


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
