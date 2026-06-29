#!/usr/bin/env python3
"""
AMBAR Operator Bot — MongoDB edition
- View new / active / completed orders per office
- Accept → ETA → countdown timer → delivered
- Edit order items (add / remove / change qty)
- Ban / unban customers
- Stats
"""
import os, asyncio, logging, math
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, MenuButtonCommands, MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import db
from customer_card import render_customer_card

load_dotenv()
OPERATOR_BOT_TOKEN   = os.getenv("OPERATOR_BOT_TOKEN", "")
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
OPERATOR_IDS         = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]
WEBAPP_URL           = os.getenv("WEBAPP_URL", "")
SUPPORT_BOT_USERNAME = "ambar_support_bot"
_TEST_ACCOUNTS = {8251195567, 6731325660}

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

PRODUCTS = [
    # ── Водка / Vodka ─────────────────────────────────────────────────────────
    {"id":"p1",  "name":"Absolut 1 ltr",              "price":95, "cat":"Водка"},
    {"id":"p2",  "name":"Stolichnaya 1 ltr",          "price":95, "cat":"Водка"},
    {"id":"p5",  "name":"Smirnoff Vodka 1 ltr",       "price":95, "cat":"Водка"},
    {"id":"p3",  "name":"Russian Standard 1 ltr",     "price":140, "cat":"Водка"},
    {"id":"p4",  "name":"Skyy Vodka 1 ltr",           "price":140, "cat":"Водка"},
    {"id":"p6",  "name":"Beluga 0.7 ltr",             "price":240, "cat":"Водка"},
    {"id":"p7",  "name":"Grey Goose 1 ltr",           "price":190, "cat":"Водка"},
    {"id":"p8",  "name":"Belvedere 1 ltr",            "price":190, "cat":"Водка"},
    {"id":"p9",  "name":"Ciroc 1 ltr",                "price":285, "cat":"Водка"},
    # ── Виски / Whisky ────────────────────────────────────────────────────────
    {"id":"p10", "name":"Red Label 1 ltr",             "price":95, "cat":"Виски"},
    {"id":"p15", "name":"Ballantines Finest 1 ltr",    "price":95, "cat":"Виски"},
    {"id":"p23", "name":"J&B 1 ltr",                   "price":140, "cat":"Виски"},
    {"id":"p11", "name":"Black Label 1 ltr",           "price":190, "cat":"Виски"},
    {"id":"p12", "name":"Jack Daniels 1 ltr",          "price":190, "cat":"Виски"},
    {"id":"p13", "name":"Chivas Regal 12Y 1 ltr",     "price":190, "cat":"Виски"},
    {"id":"p14", "name":"Jameson 1 ltr",               "price":190, "cat":"Виски"},
    {"id":"p16", "name":"Double Black 1 ltr",          "price":285, "cat":"Виски"},
    {"id":"p19", "name":"Jack Daniels Honey 1 ltr",   "price":240, "cat":"Виски"},
    {"id":"p20", "name":"Gentleman Jack 1 ltr",        "price":240, "cat":"Виски"},
    {"id":"p25", "name":"Glenfiddich 12Y 1 ltr",      "price":285, "cat":"Виски"},
    {"id":"p17", "name":"Gold Label 1 ltr",            "price":330, "cat":"Виски"},
    {"id":"p18", "name":"Chivas Regal 18Y 1 ltr",     "price":380, "cat":"Виски"},
    {"id":"p26", "name":"Glenfiddich 15Y 1 ltr",      "price":380, "cat":"Виски"},
    {"id":"p27", "name":"Glenfiddich 18Y 0.75 ltr",   "price":475, "cat":"Виски"},
    {"id":"p28", "name":"Macallan 12Y 0.7 ltr",       "price":520, "cat":"Виски"},
    {"id":"p29", "name":"Macallan 15Y 0.7 ltr",       "price":760, "cat":"Виски"},
    {"id":"p22", "name":"Chivas Royal Salute 21Y 1 ltr","price":1235,"cat":"Виски"},
    {"id":"p24", "name":"Chivas Regal 25Y 0.7 ltr",   "price":1425, "cat":"Виски"},
    {"id":"p21", "name":"Blue Label 1 ltr",            "price":1330, "cat":"Виски"},
    {"id":"p30", "name":"Macallan 18Y 0.75 ltr",      "price":1900, "cat":"Виски"},
    # ── Пиво / Beer (pack-only: 12 & 24) ─────────────────────────────────────
    {"id":"p31", "name":"Heineken 0.33 can",           "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p33", "name":"Budweiser 0.33 can",          "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p35", "name":"Stella Artois 0.33 can",      "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p37", "name":"Red Horse 0.5 can",           "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p38", "name":"Amstel Light 0.33 can",       "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p40", "name":"XXL Vodka 0.25 can",          "cat":"Пиво", "p12":95, "p24":190},
    {"id":"p32", "name":"Heineken 0.33 bottle",        "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p34", "name":"Budweiser 0.33 bottle",       "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p36", "name":"Stella Artois 0.33 bottle",   "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p41", "name":"Asahi Super Dry 0.33 bottle", "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p42", "name":"Hoegaarden 0.33 bottle",      "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p43", "name":"Corona Extra 0.355 bottle",   "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p44", "name":"Peroni Nastro 0.33 bottle",   "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p45", "name":"Smirnoff Ice 0.275 bottle",   "cat":"Пиво", "p12":140, "p24":285},
    {"id":"p46", "name":"Bacardi Breezer 0.275 bottle","cat":"Пиво", "p12":140, "p24":285},
    {"id":"p39", "name":"Guinness 0.44 can",           "cat":"Пиво", "p12":190, "p24":380},
    {"id":"p47", "name":"Carlsberg 0.5 can",          "cat":"Пиво", "p12":95, "p24":170},
    # ── Ром / Rum ─────────────────────────────────────────────────────────────
    {"id":"p48", "name":"Bacardi White 1 ltr",         "price":95, "cat":"Ром"},
    {"id":"p49", "name":"Bacardi Black 1 ltr",         "price":95, "cat":"Ром"},
    {"id":"p50", "name":"Bacardi Gold 1 ltr",          "price":95, "cat":"Ром"},
    {"id":"p51", "name":"Captain Morgan Black 1 ltr",  "price":140, "cat":"Ром"},
    {"id":"p52", "name":"Captain Morgan Gold 1 ltr",   "price":140, "cat":"Ром"},
    {"id":"p53", "name":"Malibu 1 ltr",                "price":140, "cat":"Ром"},
    # ── Вермут / Vermouth ─────────────────────────────────────────────────────
    {"id":"p54", "name":"Martini Bianco 1 ltr",        "price":140, "cat":"Вермут"},
    # ── Джин / Gin ────────────────────────────────────────────────────────────
    {"id":"p55", "name":"Gordon's 1 ltr",              "price":95, "cat":"Джин"},
    {"id":"p56", "name":"Bombay Sapphire 1 ltr",       "price":140, "cat":"Джин"},
    {"id":"p58", "name":"Gordon Pink 0.7 ltr",         "price":140, "cat":"Джин"},
    {"id":"p59", "name":"Tanqueray 1 ltr",             "price":190, "cat":"Джин"},
    {"id":"p57", "name":"Hendrick's 1 ltr",            "price":240, "cat":"Джин"},
    {"id":"p61", "name":"Malfy Con Arancia 0.7 ltr",   "price":240, "cat":"Джин"},
    {"id":"p62", "name":"Malfy Rosa 0.7 ltr",          "price":240, "cat":"Джин"},
    {"id":"p63", "name":"Drumshanbo Gunpowder 0.7 ltr","price":285, "cat":"Джин"},
    {"id":"p60", "name":"Monkey 47 0.5 ltr",           "price":330, "cat":"Джин"},
    # ── Текила / Tequila ──────────────────────────────────────────────────────
    {"id":"p64", "name":"Jose Cuervo Silver 1 ltr",    "price":95, "cat":"Текила"},
    {"id":"p65", "name":"Jose Cuervo Gold 1 ltr",      "price":95, "cat":"Текила"},
    {"id":"p66", "name":"Patron XO Cafe 0.75 ltr",     "price":240, "cat":"Текила"},
    {"id":"p67", "name":"Patron Silver 0.75 ltr",      "price":330, "cat":"Текила"},
    {"id":"p68", "name":"Patron Gold 0.75 ltr",        "price":380, "cat":"Текила"},
    {"id":"p69", "name":"Don Julio Blanco 70/75cl",    "price":380, "cat":"Текила"},
    {"id":"p70", "name":"Don Julio Reposado 70/75cl",  "price":430, "cat":"Текила"},
    {"id":"p71", "name":"Don Julio Anejo 70/75cl",     "price":520, "cat":"Текила"},
    {"id":"p72", "name":"Don Julio 1942 70/75cl",      "price":1520,"cat":"Текила"},
    {"id":"p73", "name":"Clase Azul Reposado 70/75cl", "price":1710,"cat":"Текила"},
    # ── Коньяк / Cognac ──────────────────────────────────────────────────────
    {"id":"p74", "name":"Hennessy VS 1 ltr",           "price":380, "cat":"Коньяк"},
    {"id":"p77", "name":"Remy Martin VSOP 1 ltr",      "price":380, "cat":"Коньяк"},
    {"id":"p75", "name":"Hennessy VSOP 1 ltr",         "price":475, "cat":"Коньяк"},
    {"id":"p76", "name":"Hennessy XO 1 ltr",           "price":1520,"cat":"Коньяк"},
    # ── Ликёр / Liqueur ──────────────────────────────────────────────────────
    {"id":"p78", "name":"Baileys 1 ltr",               "price":140, "cat":"Ликёр"},
    {"id":"p79", "name":"Amarula 1 ltr",               "price":140, "cat":"Ликёр"},
    {"id":"p81", "name":"Aperol 1 ltr",                "price":140, "cat":"Ликёр"},
    {"id":"p80", "name":"Jagermeister 1 ltr",          "price":190, "cat":"Ликёр"},
    {"id":"p82", "name":"Tequila Rose 0.7 ltr",        "price":240, "cat":"Ликёр"},
    # ── Арак / Arak ──────────────────────────────────────────────────────────
    {"id":"p83", "name":"Arak Touma 0.75 ltr",         "price":95, "cat":"Арак"},
    {"id":"p84", "name":"Efe Raki 1 ltr",              "price":140, "cat":"Арак"},
    # ── Шампанское / Champagne ────────────────────────────────────────────────
    {"id":"p85", "name":"Moet Brut 0.75",              "price":285, "cat":"Шампанское"},
    {"id":"p86", "name":"Moet Rose 0.75",              "price":380, "cat":"Шампанское"},
    {"id":"p88", "name":"Veuve Clicquot 0.75",         "price":430, "cat":"Шампанское"},
    {"id":"p87", "name":"Moet Ice 0.75",               "price":475, "cat":"Шампанское"},
    {"id":"p89", "name":"Ruinart Blanc 0.75",          "price":760, "cat":"Шампанское"},
    {"id":"p90", "name":"Dom Perignon 0.75",           "price":1520,"cat":"Шампанское"},
    # ── Просекко / Prosecco ───────────────────────────────────────────────────
    {"id":"p94", "name":"Martini Asti 0.75",           "price":140, "cat":"Просекко"},
    {"id":"p91", "name":"Bottega Prosecco 0.75",       "price":140, "cat":"Просекко"},
    {"id":"p95", "name":"Zonin Prosecco 0.75",         "price":140, "cat":"Просекко"},
    {"id":"p92", "name":"Bottega Rose 0.75",           "price":190, "cat":"Просекко"},
    {"id":"p93", "name":"Bottega Gold 0.75",           "price":240, "cat":"Просекко"},
    # ── Вино / Wine ───────────────────────────────────────────────────────────
    {"id":"p96",  "name":"Jacob Creek Chardonnay 0.75",    "price":95, "cat":"Вино"},
    {"id":"p97",  "name":"Pinot Grigio Cesari 0.75",      "price":95, "cat":"Вино"},
    {"id":"p98",  "name":"Le Grand Noir SB 0.75",         "price":95, "cat":"Вино"},
    {"id":"p105", "name":"Jacob Creek Shiraz 0.75",        "price":95, "cat":"Вино"},
    {"id":"p106", "name":"Le Grand Noir Merlot 0.75",     "price":95, "cat":"Вино"},
    {"id":"p115", "name":"Mateus Rose 0.75",               "price":95, "cat":"Вино"},
    {"id":"p117", "name":"Chateau Ksara Rose 0.75",        "price":95, "cat":"Вино"},
    {"id":"p99",  "name":"Calvet Sancerre 0.75",          "price":140, "cat":"Вино"},
    {"id":"p109", "name":"Chateau Saint Leon 0.75",        "price":140, "cat":"Вино"},
    {"id":"p112", "name":"La Celia Malbec 0.75",           "price":140, "cat":"Вино"},
    {"id":"p120", "name":"MiP Collection Rose 0.75",       "price":140, "cat":"Вино"},
    {"id":"p100",  "name":"Rimapere SB 0.75",              "price":190, "cat":"Вино"},
    {"id":"p104", "name":"Oyster Bay SB 0.75",             "price":190, "cat":"Вино"},
    {"id":"p107", "name":"Castel Barreyres 0.75",          "price":190, "cat":"Вино"},
    {"id":"p108", "name":"Chateau Perron 0.75",            "price":190, "cat":"Вино"},
    {"id":"p110", "name":"Campo Viejo Reserva 0.75",       "price":190, "cat":"Вино"},
    {"id":"p111", "name":"Chateau Des Laurets 0.75",       "price":190, "cat":"Вино"},
    {"id":"p116", "name":"Minuty Cotes De Provence 0.75",  "price":190, "cat":"Вино"},
    {"id":"p121", "name":"Drostdy Hof Grand Cru 5 ltr",   "price":190, "cat":"Вино"},
    {"id":"p122", "name":"Drostdy Hof Claret 5 ltr",      "price":190, "cat":"Вино"},
    {"id":"p101", "name":"Louis Moreau Chablis 0.75",      "price":240, "cat":"Вино"},
    {"id":"p102", "name":"Bourgogne Louis Jadot 0.75",     "price":240, "cat":"Вино"},
    {"id":"p103", "name":"Gavi Di Gavi 0.75",              "price":240, "cat":"Вино"},
    {"id":"p113", "name":"Campo Viejo Gran Reserva 0.75",  "price":240, "cat":"Вино"},
    {"id":"p118", "name":"Whispering Angel 0.75",          "price":240, "cat":"Вино"},
    {"id":"p119", "name":"Saint Maur Rose 0.75",           "price":240, "cat":"Вино"},
    {"id":"p114", "name":"Chateau Lagrange 0.75",          "price":760, "cat":"Вино"},
]

# Category order & emoji for the "add product" category picker
CATEGORY_ORDER = [
    ("Водка",       "🫙"),
    ("Виски",       "🥃"),
    ("Пиво",        "🍺"),
    ("Ром",         "🏴‍☠️"),
    ("Вермут",      "🍸"),
    ("Джин",        "🫒"),
    ("Текила",      "🌵"),
    ("Коньяк",      "🍷"),
    ("Ликёр",       "🍬"),
    ("Арак",        "🫗"),
    ("Шампанское",  "🍾"),
    ("Просекко",    "🥂"),
    ("Вино",        "🍇"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_operator(uid):
    return not OPERATOR_IDS or uid in OPERATOR_IDS

def get_operator_offices(uid):
    """Return ALL offices an operator belongs to.

    Operators are commonly listed in multiple offices (central+north+south),
    and they need to see orders from all of them. The previous version only
    returned the FIRST matching office, which silently hid every order from
    other offices the same operator was supposed to handle.

    Returns [] if the operator isn't in any office (will be treated by
    db.get_all_orders as 'no filter' → returns nothing for non-operators)."""
    try:
        from config_offices import OFFICE_OPERATORS
        return [oid for oid, ops in OFFICE_OPERATORS.items() if uid in ops]
    except Exception:
        return []


DUBAI_TZ = timezone(offset=__import__('datetime').timedelta(hours=4))

async def order_summary_label(o):
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
    # Status indicator for terminal states — helps operator distinguish
    # delivered vs declined vs cancelled at a glance in mixed lists.
    st_emoji = {"delivered": "✅", "declined": "❌", "cancelled": "🚫"}.get(o.get("status",""), "")
    head = f"{st_emoji} #{o['order_id']}" if st_emoji else f"#{o['order_id']}"
    parts = [head]
    if time_str:
        parts.append(time_str)
    parts.append(f"{n_items} поз.")
    parts.append(f"{o.get('total',0)} AED")
    label = " · ".join(parts)
    # Check verification status
    try:
        cid = o.get("customer_id")
        if cid:
            user_doc = await db.get_user(int(cid))
            if user_doc and not user_doc.get("verified", False):
                label = "🔴 " + label + " 🔴"
    except: pass
    return label


# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_main():
    return ReplyKeyboardMarkup([
        ["🆕 Новые заказы (ожидают ответа)",   "🟢 Активные"],
        ["✅ Завершённые",    "📊 Статистика"],
        ["🚫 Бан / Нет верификации", "❓ Помощь"],
        # ["👤 Профиль"],  # hidden — handler + helpers kept in handle_menu() below
    ], resize_keyboard=True)

async def kb_order_list(items, list_type, limit=50):
    """Compact list of orders as inline buttons.
    Limit raised from 15 → 50 (Telegram caps inline keyboards at ~100 rows);
    if truncation still happens we show an explicit ⚠️ row so the operator
    isn't silently missing pending work."""
    rows = []
    for o in items[:limit]:
        rows.append([InlineKeyboardButton(
            await order_summary_label(o),
            callback_data=f"osel_{list_type}_{o['order_id']}"
        )])
    hidden = len(items) - limit
    if hidden > 0:
        rows.append([InlineKeyboardButton(f"⚠️ Ещё {hidden} скрыто — обработайте текущие", callback_data="noop")])
    rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
    return InlineKeyboardMarkup(rows)

async def kb_order_actions(order, list_type=None):
    oid, cid = order["order_id"], order["customer_id"]
    st       = order.get("status", "")
    rows     = []
    # Check verification status
    is_unverified = False
    try:
        user_doc = await db.get_user(int(cid))
        if user_doc and not user_doc.get("verified", False):
            is_unverified = True
    except Exception:
        pass
    if is_unverified:
        # Only show verify/decline + client for unverified users
        rows.append([
            InlineKeyboardButton("✅ Верифицировать", callback_data=f"verify_{cid}"),
            InlineKeyboardButton("❌ Не верифицировать", callback_data=f"decverify_{oid}_{cid}"),
        ])
        rows.append([InlineKeyboardButton("👤 Клиент", callback_data=f"client_{oid}_{cid}")])
    else:
        if st == "pending":
            rows.append([
                InlineKeyboardButton("✅ Принять",   callback_data=f"acc_{oid}_{cid}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{oid}_{cid}"),
            ])
        if st == "approved":
            rows.append([
                InlineKeyboardButton(f"🚚 Доставлено #{oid}", callback_data=f"done_{oid}_{cid}"),
                InlineKeyboardButton("🚫 Отменить", callback_data=f"opcancel_{oid}_{cid}"),
            ])
        if st == "delivered":
            rows.append([InlineKeyboardButton("🔄 Вернуть в доставку", callback_data=f"undone_{oid}_{cid}")])
        if st == "cancelled":
            rows.append([InlineKeyboardButton("🔄 Вернуть в доставку", callback_data=f"undocancel_{oid}_{cid}")])
        rows.append([
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{oid}"),
            InlineKeyboardButton("📍 Геолокация",    callback_data=f"loc_{oid}"),
        ])
        rows.append([InlineKeyboardButton("👤 Клиент", callback_data=f"client_{oid}_{cid}")])
    if list_type:
        rows.append([InlineKeyboardButton("← К списку", callback_data=f"olist_{list_type}")])
    if order.get("review_score"):
        s = order["review_score"]
        rows.append([InlineKeyboardButton(f"{'⭐'*s} Оценка ({s}/5)", callback_data=f"rev_{oid}")])
    if st in ("delivered", "declined", "cancelled"):
        rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
    return InlineKeyboardMarkup(rows)


async def kb_client_actions(oid, cid):
    """Keyboard shown on client info view."""
    rows = []
    rows.append([InlineKeyboardButton("🚫 Забанить клиента", callback_data=f"ban_{oid}_{cid}")])
    rows.append([InlineKeyboardButton("✏️ Заметка к имени", callback_data=f"rename_{oid}_{cid}")])
    rows.append([InlineKeyboardButton("← Назад", callback_data=f"client_back_{oid}")])
    return InlineKeyboardMarkup(rows)

def kb_eta(oid, cid):
    r1 = [InlineKeyboardButton(f"⏱ {t} мин", callback_data=f"eta_{t}_{oid}_{cid}") for t in [20, 25, 30]]
    r2 = [InlineKeyboardButton(f"⏱ {t} мин", callback_data=f"eta_{t}_{oid}_{cid}") for t in [35, 40, 50]]
    return InlineKeyboardMarkup([r1, r2, [InlineKeyboardButton("← Назад", callback_data=f"eta_back_{oid}_{cid}")]])

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
    rows.append([InlineKeyboardButton("📝 Свободная позиция", callback_data=f"ei_free_{oid}")])
    rows.append([InlineKeyboardButton("✅ Готово",         callback_data=f"edit_done_{oid}")])
    return InlineKeyboardMarkup(rows)

def kb_add_categories(oid):
    """Category picker for adding a product."""
    rows = []
    for cat, emoji in CATEGORY_ORDER:
        count = sum(1 for p in PRODUCTS if p["cat"] == cat)
        rows.append([InlineKeyboardButton(f"{emoji} {cat}  ({count})", callback_data=f"ei_cat_{oid}_{cat}")])
    rows.append([InlineKeyboardButton("← Назад", callback_data=f"edit_{oid}")])
    return InlineKeyboardMarkup(rows)

def kb_add_product(oid, cat=None):
    """Product list filtered by category, sorted by price."""
    items = [p for p in PRODUCTS if p["cat"] == cat] if cat else PRODUCTS
    if cat == "Пиво":
        # Beer: show brand names only, operator picks pack size next
        items = sorted(items, key=lambda p: p["p12"])
        rows = [[InlineKeyboardButton(f"{p['name']}", callback_data=f"ei_beer_{oid}_{p['id']}")] for p in items]
    else:
        items = sorted(items, key=lambda p: p["price"])
        rows = [[InlineKeyboardButton(f"{p['name']}  {p['price']} AED", callback_data=f"ei_addp_{oid}_{p['id']}")] for p in items]
    rows.append([InlineKeyboardButton("← Назад", callback_data=f"ei_add_{oid}")])
    return InlineKeyboardMarkup(rows)

def beer_pack_price(p, pack):
    """12-pack = the listed 12-price; 24-pack = double minus a flat 5, snapped up to a clean
    0/5 in our favour (95→185, 140→275). Mirrors the customer app's beerPrice."""
    twelve = p.get("p12") or p.get("price") or 0
    if str(pack) == "12" or not twelve:
        return twelve
    return math.ceil((twelve * 2 - 5) / 5) * 5

def kb_beer_pack(oid, pid):
    """Pack size picker for a specific beer."""
    pmap = {p["id"]: p for p in PRODUCTS}
    p = pmap.get(pid)
    if not p:
        return InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data=f"ei_cat_{oid}_Пиво")]])
    rows = [
        [InlineKeyboardButton(f"📦 ×12  —  {beer_pack_price(p,'12')} AED", callback_data=f"ei_addp_{oid}_{pid}_12")],
        [InlineKeyboardButton(f"📦 ×24  —  {beer_pack_price(p,'24')} AED", callback_data=f"ei_addp_{oid}_{pid}_24")],
        [InlineKeyboardButton("← Назад", callback_data=f"ei_cat_{oid}_Пиво")],
    ]
    return InlineKeyboardMarkup(rows)

def kb_ban_confirm(cid, oid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Без причины",     callback_data=f"ban_skip_{cid}_{oid}")],
        [InlineKeyboardButton("✏️ Ввести причину", callback_data=f"ban_input_{cid}_{oid}")],
        [InlineKeyboardButton("← Отмена",           callback_data=f"ban_cancel_{oid}_{cid}")],
    ])


# ── Order card formatter ──────────────────────────────────────────────────────
async def order_card(o, full=True):
    st_map = {"pending":"🟡 Ожидает","approved":"🟢 Принят","delivered":"✅ Доставлен","declined":"🔴 Отклонён","cancelled":"🚫 Отменён клиентом"}
    st     = st_map.get(o.get("status",""), o.get("status",""))
    lines  = []
    # Preserve first order banner + source info for unverified customers
    try:
        cid = o.get("customer_id")
        if cid:
            user_doc = await db.get_user(int(cid))
            if user_doc and not user_doc.get("verified", False):
                inv_op = user_doc.get("invited_by_operator")
                inv_at = user_doc.get("invited_at")
                joined_str = ""
                if inv_at:
                    try:
                        dt = datetime.fromisoformat(str(inv_at).replace("Z","+00:00")) if isinstance(inv_at, str) else inv_at
                        joined_str = dt.astimezone(DUBAI_TZ).strftime('%d.%m.%Y %H:%M')
                    except Exception:
                        joined_str = ""
                if int(cid) in _TEST_ACCOUNTS:
                    bq = ["🟢🟢🟢 <b>ТЕСТ (НЕ НАСТОЯЩИЙ ЗАКАЗ)</b> 🟢🟢🟢"]
                elif user_doc.get("referred_by"):
                    title = "<b>НОВЫЙ КЛИЕНТ — РЕФЕРАЛ</b>"
                    bq = [f"🔴🔴🔴 {title} 🔴🔴🔴"]
                elif inv_op is not None and inv_op > 0:
                    title = "<b>НОВЫЙ КЛИЕНТ — ССЫЛКА ОПЕРАТОРА</b>"
                    bq = [f"🔴🔴🔴 {title} 🔴🔴🔴"]
                elif inv_op == 0:
                    title = "<b>НОВЫЙ КЛИЕНТ — ОБЩАЯ ССЫЛКА</b>"
                    bq = [f"🔴🔴🔴 {title} 🔴🔴🔴"]
                else:
                    title = "<b>НОВЫЙ КЛИЕНТ!</b>"
                    bq = [f"🔴🔴🔴 {title} 🔴🔴🔴"]
                if int(cid) not in _TEST_ACCOUNTS:
                    if inv_op is not None and inv_op > 0:
                        h = f"🔗 По ссылке оператора <code>{inv_op}</code>"
                        if joined_str: h += f" · вступил {joined_str}"
                        bq.append(h)
                    elif inv_op == 0:
                        h = "🔗 По общей ссылке операторов"
                        if joined_str: h += f" · вступил {joined_str}"
                        bq.append(h)
                    src = user_doc.get("verify_source", "")
                    if src:
                        src_labels = {"friend":"👥 Знакомый","operator":"📞 Оператор","social":"📱 Соцсети","search":"🔍 Интернет","other":"💬 Другое"}
                        src_detail = user_doc.get("verify_source_detail", "")
                        rec_name = user_doc.get("verify_recommender_name", "")
                        rec_phone = user_doc.get("verify_recommender_phone", "")
                        bq.append(f"📋 Источник: <b>{src_labels.get(src, src)}</b>")
                        if src == "friend" and rec_name:
                            bq.append(f"👤 {rec_name}" + (f" — {rec_phone}" if rec_phone else ""))
                        elif src_detail:
                            bq.append(f"💬 {src_detail}")
                lines.append("<blockquote>" + "\n".join(bq) + "</blockquote>")
                lines.append("")
    except Exception:
        pass
    lines.extend([f"🏢 Офис: <b>{_esc(o.get('office_name','—'))}</b>", ""])
    # Crypto-paid: loud banner right at the top so the operator sees "already paid —
    # don't collect cash" first. Persists through every lifecycle state.
    if o.get("payment_method") == "crypto" and o.get("paid"):
        lines.append("<blockquote>✅💎 <b>ОПЛАЧЕНО КРИПТОЙ</b>\n"
                     f"{o.get('crypto_amount_usdt', '?')} USDT · TRC-20 — наличные НЕ брать</blockquote>")
        lines.append("")
    lines.append(f"🆕 <b>НОВЫЙ ЗАКАЗ #{o['order_id']}</b>")
    lines.append("")
    if full:
        gmap = o.get("gmap_link","")
        addr = o.get("address","—")
        if gmap:
            lines.append(f"🏠 Адрес: {_esc(addr)}" if addr and addr != "GPS" and addr != "—" else "🏠 Адрес: GPS")
            lines.append(f"Google Maps: {gmap}")
        else:
            lines.append(f"🏠 Адрес: {_esc(addr)}")
        lines.append("")
    lines.append("🛒 <b>Позиции:</b>")
    for item in o.get("items", []):
        lt = item.get("line_total", item["price"] * item["qty"])
        lines.append(f"  • {_esc(item['name'])} ×{item['qty']} = {lt} AED")
    lines.append("")
    if o.get("tip"): lines.append(f"🎁 Чаевые: {o['tip']} AED")
    lines.append(f"💰 <b>Итого: {o.get('total',0)} AED</b>")
    comment = o.get("comment", "").strip()
    if comment:
        lines.append("")
        lines.append(f"💬 Комментарий: {_esc(comment)}")
    if o.get("status") == "approved" and o.get("deliver_by"):
        confirmed_at = o.get("confirmed_at", "")
        confirmed_time = ""
        if confirmed_at:
            try:
                from datetime import datetime as _dt
                ct = _dt.fromisoformat(confirmed_at.replace("Z", "+00:00")).astimezone(DUBAI_TZ)
                confirmed_time = ct.strftime("%H:%M")
            except Exception:
                pass
        eta_val = o.get("eta", "")
        parts_line = []
        if confirmed_time:
            parts_line.append(f"Принят в <b>{confirmed_time}</b>")
        if eta_val:
            parts_line.append(f"Время доставки <b>{eta_val} мин</b>")
        parts_line.append(f"Доставить до <b>{o['deliver_by']}</b>")
        lines.append("")
        lines.append("🏁 " + " | ".join(parts_line))
    return "\n".join(lines)

def _esc(t):
    """Escape HTML special chars."""
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


_FOUNDER_ID = 7865205960
_PREMIUM_IDS = [686932322, 1459370603]

def _card_tier(uid, orders_done=0, total_spent=0):
    """Return display label for customer card/loyalty tier."""
    if uid == _FOUNDER_ID:
        return "💎 Founder"
    if uid in _PREMIUM_IDS:
        return "⭐ Premium"
    # Loyalty tiers based on completed orders & spend
    if orders_done >= 30 or total_spent >= 15000:
        return "💠 Diamond"
    if orders_done >= 15 or total_spent >= 7000:
        return "🥇 Gold"
    if orders_done >= 5 or total_spent >= 2000:
        return "🥈 Silver"
    return "🪪 New"

async def customer_card(o):
    """Customer info card — shown when operator clicks 'Клиент'.
    Renders as HTML so user-provided fields (username/name/etc.) can contain
    Markdown special chars without breaking message parsing — _esc() escapes
    <, >, & which are the only HTML chars that need handling."""
    cid = o.get("customer_id")
    original = o.get("customer_name", "—")
    nickname = ""
    user_doc = None
    if cid:
        user_doc = await db.get_user(int(cid)) if cid else None
        if user_doc:
            original = user_doc.get("full_name") or user_doc.get("name") or original
            nickname = user_doc.get("custom_name", "")
    name_line = f"👤 <b>{_esc(original)}</b>"
    if nickname:
        name_line += f"  <i>({_esc(nickname)})</i>"
    lines = [
        name_line,
        f"📞 <code>{_esc(o.get('phone','—'))}</code>",
        f"🔗 @{_esc(o.get('username','—'))}  |  ID: <code>{_esc(o.get('customer_id','—'))}</code>",
        "",
    ]
    # Order stats
    total = done = declined = 0
    spent = 0
    if user_doc:
        total = user_doc.get("orders_total", 0)
        done = user_doc.get("orders_done", 0)
        declined = user_doc.get("orders_declined", 0)
        spent = user_doc.get("total_spent", 0)
    # Card / loyalty tier
    uid = int(cid) if cid else None
    lines.append(f"🏷 <b>{_esc(_card_tier(uid, done, spent))}</b>" if uid else "🏷 —")
    if user_doc:
        lines.append(f"📦 Заказов: <b>{total}</b>  (✅ {done} / ❌ {declined})")
        lines.append(f"💰 Потрачено: <b>{spent:,.0f} AED</b>")
    # First seen
    if user_doc and user_doc.get("first_seen"):
        fs = user_doc["first_seen"]
        if isinstance(fs, str):
            fs = datetime.fromisoformat(fs)
        lines.append(f"📅 Клиент с: <b>{fs.strftime('%d.%m.%Y')}</b>")
    # Invite attribution (always shown if set)
    if user_doc:
        inv_op = user_doc.get("invited_by_operator")
        inv_at = user_doc.get("invited_at")
        joined_str = ""
        if inv_at:
            try:
                dt = datetime.fromisoformat(str(inv_at).replace("Z","+00:00")) if isinstance(inv_at, str) else inv_at
                joined_str = dt.astimezone(DUBAI_TZ).strftime('%d.%m.%Y %H:%M')
            except Exception: pass
        if inv_op is not None and inv_op > 0:
            lines.append(f"🔗 По ссылке оператора: <code>{inv_op}</code>" + (f"  <i>({joined_str})</i>" if joined_str else ""))
        elif inv_op == 0:
            lines.append("🔗 По общей ссылке операторов" + (f"  <i>({joined_str})</i>" if joined_str else ""))
    # Verification info
    if user_doc:
        verified = user_doc.get("verified", False)
        src = user_doc.get("verify_source", "")
        src_detail = user_doc.get("verify_source_detail", "")
        rec_name = user_doc.get("verify_recommender_name", "")
        rec_phone = user_doc.get("verify_recommender_phone", "")
        lines.append("")
        if verified:
            lines.append("🔐 <b>Верифицирован</b> ✅")
        elif src:
            lines.append("🔐 <b>Ожидает верификации</b> ⏳")
        else:
            lines.append("🔐 <b>Не верифицирован</b>")
        if src:
            src_labels = {"friend": "👥 Знакомый", "operator": "📞 Оператор", "social": "📱 Соцсети", "search": "🔍 Интернет", "other": "💬 Другое"}
            lines.append(f"📋 Источник: <b>{_esc(src_labels.get(src, src))}</b>")
        if src == "friend" and rec_name:
            lines.append(f"👤 Рекомендатель: <b>{_esc(rec_name)}</b>")
            if rec_phone:
                lines.append(f"📞 Тел рекомендателя: <code>{_esc(rec_phone)}</code>")
        elif src_detail:
            lines.append(f"💬 Детали: <b>{_esc(src_detail)}</b>")
    return "\n".join(lines)


def recalc_order(order):
    pmap  = {p["id"]: p for p in PRODUCTS}
    items = order.get("items", [])
    for item in items:
        if item.get("is_custom"):
            # Custom items keep their own price
            price = item.get("price", 0)
        else:
            p = pmap.get(item["id"])
            price = p["price"] if p and "price" in p else item.get("price", 0)
        item["line_total"] = price * item["qty"]
    sub            = sum(i.get("line_total", 0) for i in items)
    order["subtotal"] = sub
    order["total"]    = sub + order.get("tip", 0)
    return order


# ── Customer status card — one live message per order, edited in place ──────
def _order_msg_id(order: dict):
    """Backwards-compatible read of the live customer msg id."""
    return order.get("customer_msg_id") or (order.get("customer_msg_ids") or [None])[0]


async def update_customer_card(oid: str, reply_markup=None):
    """Render the customer card for `oid` and edit the live msg in place.
    Falls back to sending a new msg if the old one can't be edited."""
    order = await db.get_order(oid)
    if not order: return None
    cid = order.get("customer_id")
    if not cid: return None
    lang   = order.get("lang", "ru")
    msg_id = _order_msg_id(order)
    text   = render_customer_card(order, lang)

    app = Application.builder().token(BOT_TOKEN).build()
    async with app:
        if msg_id:
            try:
                await app.bot.edit_message_text(
                    text, chat_id=cid, message_id=msg_id,
                    parse_mode="Markdown", reply_markup=reply_markup)
                return msg_id
            except Exception as e:
                log.debug(f"customer edit {cid}/{msg_id} failed ({e}); sending new")
        try:
            sent = await app.bot.send_message(
                cid, text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            log.error(f"customer send {cid}: {e}")
            return None

    await db.update_order(oid, customer_msg_id=sent.message_id)
    return sent.message_id


# ── Helper: fetch & build order list ─────────────────────────────────────────
async def _build_order_list(list_type, operator_uid):
    """Fetch orders and return (header_text, sorted_items, list_type)."""
    off = get_operator_offices(operator_uid)
    all_orders = await db.get_all_orders(off)
    if list_type == "n":
        items = sorted([o for o in all_orders.values() if o.get("status") == "pending"],
                       key=lambda x: x.get("timestamp",""), reverse=True)
        # Count unverified
        unverified_count = 0
        for o in items:
            try:
                cid = o.get("customer_id")
                if cid:
                    u = await db.get_user(int(cid))
                    if u and not u.get("verified", False):
                        unverified_count += 1
            except: pass
        header = f"🆕 *Новых заказов: {len(items)}*"
        if unverified_count:
            header += f"\n🔴 Без верификации: *{unverified_count}*"
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

    # If operator tapped a menu button, cancel any pending input flow
    _menu_keywords = ("Новые", "Активные", "Завершённые", "Бан", "Статистика", "Помощь", "Профиль")
    if any(kw in (update.message.text or "") for kw in _menu_keywords):
        ctx.user_data.pop("pending_ban", None)
        ctx.user_data.pop("pending_rename", None)
        ctx.user_data.pop("awaiting_decv_comment", None)
        ctx.user_data.pop("decv_oid", None)
        ctx.user_data.pop("decv_cid", None)
        ctx.user_data.pop("pending_free_name", None)
        ctx.user_data.pop("pending_free_price", None)

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
            # Only set the nickname (custom_name), keep original name untouched
            await db.upsert_user(cid, custom_name=new_name)
            # Build combined display name for orders: "Original (nickname)"
            user_doc = await db.get_user(cid)
            original = (user_doc or {}).get("full_name") or (user_doc or {}).get("name") or "—"
            display_name = f"{original} ({new_name})"
            all_orders = await db.get_all_orders()
            for o in all_orders.values():
                if o.get("customer_id") == cid:
                    await db.update_order(o["order_id"], customer_name=display_name)
            if pending_rename.get("msg_id"):
                order = await db.get_order(oid)
                if order:
                    try:
                        await ctx.bot.edit_message_text(
                            await customer_card(order),
                            chat_id=update.effective_chat.id,
                            message_id=pending_rename["msg_id"],
                            parse_mode="Markdown",
                            reply_markup=await kb_client_actions(oid, cid))
                    except: pass
        return

    # ── Intercept free position: price input ─────────────────────────────────
    pending_free_price = ctx.user_data.get("pending_free_price")
    if pending_free_price:
        ctx.user_data.pop("pending_free_price")
        raw = (update.message.text or "").strip().replace(",", ".")
        try: await update.message.delete()
        except: pass
        try:
            price = int(float(raw))
        except ValueError:
            if pending_free_price.get("msg_id"):
                try:
                    await ctx.bot.edit_message_text(
                        "❌ Неверная цена. Введите число (AED):",
                        chat_id=update.effective_chat.id,
                        message_id=pending_free_price["msg_id"],
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("← Отмена", callback_data=f"edit_{pending_free_price['oid']}")
                        ]]))
                except: pass
            ctx.user_data["pending_free_price"] = pending_free_price
            return
        if price <= 0:
            ctx.user_data["pending_free_price"] = pending_free_price
            return
        oid = pending_free_price["oid"]
        item_name = pending_free_price["name"]
        import time as _time
        item_id = f"custom_{int(_time.time())}_{oid[-3:]}"
        order = await db.get_order(oid)
        if order:
            items = order.get("items", [])
            items.append({"id": item_id, "name": item_name, "price": price, "qty": 1,
                          "line_total": price, "is_custom": True})
            order["items"] = items
            order = recalc_order(order)
            await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
            order = await db.get_order(oid)
            if pending_free_price.get("msg_id"):
                try:
                    await ctx.bot.edit_message_text(
                        f"✏️ *Редактирование #{oid}*\n\n"
                        + "\n".join(f"  • {i['name']} ×{i['qty']}" for i in order.get("items", [])),
                        chat_id=update.effective_chat.id,
                        message_id=pending_free_price["msg_id"],
                        parse_mode="Markdown",
                        reply_markup=kb_edit(order))
                except: pass
        return

    # ── Intercept free position: name input ───────────────────────────────────
    pending_free_name = ctx.user_data.get("pending_free_name")
    if pending_free_name:
        ctx.user_data.pop("pending_free_name")
        name = (update.message.text or "").strip()
        try: await update.message.delete()
        except: pass
        if not name:
            return
        ctx.user_data["pending_free_price"] = {
            "oid": pending_free_name["oid"],
            "name": name,
            "msg_id": pending_free_name.get("msg_id"),
        }
        if pending_free_name.get("msg_id"):
            try:
                await ctx.bot.edit_message_text(
                    f"📝 *Свободная позиция*\n\n"
                    f"Название: _{name}_\n\n"
                    f"💰 Введите цену (AED):",
                    chat_id=update.effective_chat.id,
                    message_id=pending_free_name["msg_id"],
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("← Отмена", callback_data=f"edit_{pending_free_name['oid']}")
                    ]]))
            except: pass
        return

    # ── Intercept decline verification comment ───────────────────────────────
    if ctx.user_data.get("awaiting_decv_comment"):
        ctx.user_data.pop("awaiting_decv_comment")
        comment = (update.message.text or "").strip()
        cid_dv = ctx.user_data.pop("decv_cid", 0)
        oid_dv = ctx.user_data.pop("decv_oid", "0")
        prompt_msg = ctx.user_data.pop("decv_prompt_msg", None)
        try: await update.message.delete()
        except: pass
        if prompt_msg:
            try: await ctx.bot.delete_message(update.effective_chat.id, prompt_msg)
            except: pass
        order_msg = ctx.user_data.pop("decv_order_msg", None)
        if cid_dv:
            await _do_decline_verification(ctx.bot, update.effective_chat.id, cid_dv, oid_dv, comment, edit_msg_id=order_msg)
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
                   parse_mode="Markdown", reply_markup=await kb_order_list(items, "n"))

    elif "Активные" in text:
        header, empty, items = await _build_order_list("a", uid)
        if not items:
            await send(cid, empty, reply_markup=_dismiss); return
        await send(cid, header + "\n\nНажмите на заказ для просмотра:",
                   parse_mode="Markdown", reply_markup=await kb_order_list(items, "a"))

    elif "Завершённые" in text:
        off = get_operator_offices(uid)
        all_orders = await db.get_all_orders(off)
        n_delivered = sum(1 for o in all_orders.values() if o.get("status") == "delivered")
        n_declined = sum(1 for o in all_orders.values() if o.get("status") == "declined")
        n_cancelled = sum(1 for o in all_orders.values() if o.get("status") == "cancelled")
        rows = [
            [InlineKeyboardButton(f"✅ Доставленные ({n_delivered})", callback_data="cmenu_delivered")],
            [InlineKeyboardButton(f"🔴 Отклонённые ({n_declined})", callback_data="cmenu_declined")],
            [InlineKeyboardButton(f"🚫 Отменённые ({n_cancelled})", callback_data="cmenu_cancelled")],
            [InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")],
        ]
        await send(cid, "✅ *Завершённые заказы*\n\nВыберите категорию:",
                   parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    elif "Статистика" in text:
        off = get_operator_offices(uid)
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

    elif "Помощь" in text:
        help_text = (
            "❓ *Руководство оператора AMBAR*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 *МЕНЮ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🆕 *Новые заказы* — список заказов, ожидающих обработки\n\n"
            "🟢 *Активные* — принятые заказы, которые сейчас в доставке\n\n"
            "✅ *Завершённые* — архив доставленных и отклонённых заказов\n\n"
            "📊 *Статистика* — сводка за сегодня: кол-во заказов, выручка\n\n"
            "🚫 *Бан / Нет верификации* — управление блокировками и верификацией клиентов\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📦 *РАБОТА С ЗАКАЗОМ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ Новый заказ приходит с уведомлением\n"
            "2️⃣ Нажмите *Принять* и выберите время доставки (20/40/60 мин)\n"
            "3️⃣ Система покажет дедлайн — до какого времени нужно доставить\n"
            "4️⃣ Когда доставлено — нажмите *Доставлено*\n"
            "5️⃣ Если ошиблись — можно вернуть в доставку кнопкой *Вернуть в доставку*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✏️ *РЕДАКТИРОВАНИЕ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "• Нажмите *Редактировать* на любом заказе\n"
            "• Можно менять количество (+/-), удалять и добавлять позиции\n"
            "• Сумма пересчитывается автоматически\n"
            "• Редактирование доступно на любом этапе, включая доставленные\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👤 *КЛИЕНТ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "• *Клиент* — карточка клиента: имя, телефон, история заказов, уровень лояльности\n"
            "• *Заметка к имени* — добавить пометку к имени клиента\n"
            "• *Забанить* — заблокировать клиента (с указанием причины или без)\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🔐 *ВЕРИФИКАЦИЯ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "• Новые клиенты без реферала проходят верификацию\n"
            "• Клиент указывает имя и телефон рекомендателя\n"
            "• На заказе появится 🚨 баннер и кнопка *Верифицировать*\n"
            "• После верификации клиент больше не будет проходить проверку\n"
            "• В разделе *Бан / Нет верификации* → *Без верификации* можно верифицировать вручную\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📍 *ПРОЧЕЕ*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "• *Геолокация* — показывает местоположение клиента на карте\n"
            "• *Просмотрено* — удаляет сообщение, чтобы не засорять чат\n"
            "• *К списку* — вернуться к списку заказов из карточки заказа"
        )
        await send(cid, help_text, parse_mode="Markdown", reply_markup=_dismiss)

    elif "Бан" in text:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 Забаненные", callback_data="list_banned")],
            [InlineKeyboardButton("🔐 Без верификации", callback_data="list_unverified")],
            [InlineKeyboardButton("🔴 Отклонённые", callback_data="list_declined_verify")],
            [InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")],
        ])
        await send(cid, "🚫 *Бан / Верификация*\n\nВыберите категорию:",
                   parse_mode="Markdown", reply_markup=kb)

    elif "Профиль" in text:
        mine    = await db.get_customers_invited_by(uid)
        common  = await db.get_common_invite_customers()
        personal_link = f"https://t.me/AmBarDelivery_bot?start=op_{uid}"
        common_link   = f"https://t.me/AmBarDelivery_bot?start=op"
        src_labels = {"friend":"👥 Знакомый","operator":"📞 Оператор","social":"📱 Соцсети","search":"🔍 Интернет","other":"💬 Другое"}
        def _fmt_join(ts):
            if not ts: return "—"
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")) if isinstance(ts, str) else ts
                return dt.astimezone(DUBAI_TZ).strftime("%d.%m.%Y %H:%M")
            except: return "—"
        def _render_user(u):
            tid = u.get("telegram_id")
            name = ((u.get("first_name","") or "") + " " + (u.get("last_name","") or "")).strip() or "—"
            uname = u.get("username", "")
            at = _fmt_join(u.get("invited_at"))
            phones = u.get("phones") or []
            phone = phones[0] if phones else ""
            src = u.get("verify_source", "")
            src_line = src_labels.get(src, "")
            rec_name = u.get("verify_recommender_name", "")
            rec_phone = u.get("verify_recommender_phone", "")
            src_detail = u.get("verify_source_detail", "")
            orders = u.get("orders_total", 0) or 0
            spent  = u.get("total_spent", 0) or 0
            out = []
            out.append(f"• *{name}*" + (f"  @{uname}" if uname and uname != "—" else ""))
            out.append(f"  🆔 `{tid}`")
            out.append(f"  📅 Вступил: {at}")
            if phone: out.append(f"  📞 `{phone}`")
            if src_line:
                out.append(f"  🔐 Источник: {src_line}")
                if src == "friend" and rec_name:
                    out.append(f"  👤 Рекомендовал: _{rec_name}_" + (f" — `{rec_phone}`" if rec_phone else ""))
                elif src_detail:
                    out.append(f"  💬 _{src_detail}_")
            if orders:
                out.append(f"  📦 Заказов: {orders}  •  💰 {int(spent)} AED")
            out.append("")
            return out
        def _section(bucket, title, link, empty_hint):
            verified    = [u for u in bucket if u.get("verified")]
            pending     = [u for u in bucket if not u.get("verified") and (u.get("orders_total") or 0) > 0]
            not_ordered = [u for u in bucket if (u.get("orders_total") or 0) == 0]
            out = ["━━━━━━━━━━━━━━━━━━━", f"*{title}*", f"`{link}`", ""]
            out.append(f"👥 Перешли: *{len(bucket)}*  •  ✅ Верифицированы: *{len(verified)}*")
            out.append(f"⏳ Ожидают: *{len(pending)}*  •  ⚪ Без заказа: *{len(not_ordered)}*")
            out.append("")
            if verified:
                out.append("*✅ Верифицированные клиенты:*")
                out.append("")
                for u in verified[:10]:
                    out.extend(_render_user(u))
                if len(verified) > 10:
                    out.append(f"_…и ещё {len(verified)-10}_")
            else:
                out.append(f"_{empty_hint}_")
            return out
        lines = ["👤 *Ваш профиль оператора*", ""]
        lines.extend(_section(mine, "🔗 Ваша персональная ссылка", personal_link,
                              "Пока нет верифицированных клиентов по вашей ссылке."))
        lines.append("")
        lines.extend(_section(common, "🌐 Общая ссылка операторов", common_link,
                              "Пока нет верифицированных клиентов по общей ссылке."))
        await send(cid, "\n".join(lines), parse_mode="Markdown", reply_markup=_dismiss,
                   disable_web_page_preview=True)

    else:
        await send(cid, "Используйте кнопки меню 👇", reply_markup=_dismiss)


# ── Start ─────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа."); return
    # Deep link: /start order_<oid> — jump straight to an order card
    if ctx.args and ctx.args[0].startswith("order_"):
        oid = ctx.args[0][6:]
        op_id = update.effective_user.id
        order = await db.get_order(oid)
        # Always wipe the /start command for a clean chat
        try: await update.message.delete()
        except Exception: pass
        if not order:
            await ctx.bot.send_message(op_id, f"❌ Заказ #{oid} не найден.")
            return
        # Remove any previous copy of this order for this operator so we don't duplicate
        old_mid = (order.get("op_msg_ids") or {}).get(str(op_id))
        if old_mid:
            try: await ctx.bot.delete_message(chat_id=op_id, message_id=old_mid)
            except Exception: pass
        sent = await ctx.bot.send_message(
            op_id, await order_card(order), parse_mode="HTML",
            reply_markup=await kb_order_actions(order))
        try:
            new_ids = dict(order.get("op_msg_ids") or {})
            new_ids[str(op_id)] = sent.message_id
            await db.update_order(oid, op_msg_ids=new_ids)
        except Exception as e:
            log.debug(f"update op_msg_ids failed: {e}")
        return
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

    # Clear pending input flows when operator navigates away
    if not data.startswith("rename_") and not data.startswith("ban_input_"):
        ctx.user_data.pop("pending_ban", None)
        ctx.user_data.pop("pending_rename", None)

    # ── LIST BANNED ───────────────────────────────────────────────────────────
    if data == "list_banned":
        banned = await db.get_all_banned()
        if not banned:
            await q.edit_message_text("✅ Нет заблокированных пользователей.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))
            return
        lines = []
        buttons = []
        for u in banned[:20]:
            tid = u.get("telegram_id")
            name = u.get("first_name", "") or ""
            lname = u.get("last_name", "") or ""
            full = f"{name} {lname}".strip() or str(tid)
            reason = u.get("ban_reason", "—")
            lines.append(f"• `{tid}` — {full}\n  Причина: _{reason}_")
            buttons.append([InlineKeyboardButton(f"🔓 Разбанить {full}", callback_data=f"unban_{tid}")])
        buttons.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await q.edit_message_text(
            "🚫 *Заблокированные пользователи:*\n\n" + "\n".join(lines),
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── LIST UNVERIFIED ───────────────────────────────────────────────────────
    if data == "list_unverified":
        users = await db.get_unverified_users_with_orders()
        if not users:
            await q.edit_message_text("✅ Все пользователи с заказами верифицированы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))
            return
        lines = []
        buttons = []
        for u in users[:20]:
            tid = u.get("telegram_id")
            name = u.get("first_name", "") or ""
            lname = u.get("last_name", "") or ""
            full = f"{name} {lname}".strip() or str(tid)
            src = u.get("verify_source", "")
            src_detail = u.get("verify_source_detail", "")
            rec_name = u.get("verify_recommender_name", "")
            rec_phone = u.get("verify_recommender_phone", "")
            src_labels = {"friend":"👥 Знакомый","operator":"📞 Оператор","social":"📱 Соцсети","search":"🔍 Интернет","other":"💬 Другое"}
            src_line = src_labels.get(src, "")
            if src == "friend" and rec_name:
                src_line += f": _{rec_name}_ {rec_phone}"
            elif src_detail:
                src_line += f": _{src_detail}_"
            rec_info = f"  {src_line}" if src_line else ""
            lines.append(f"• `{tid}` — {full}{rec_info}")
            buttons.append([
                InlineKeyboardButton(f"✅ {full}", callback_data=f"verify_{tid}"),
                InlineKeyboardButton(f"❌ {full}", callback_data=f"decverify_0_{tid}"),
            ])
        buttons.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await q.edit_message_text(
            "🔐 *Без верификации (есть заказы):*\n\n" + "\n".join(lines),
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── LIST DECLINED VERIFICATIONS ──────────────────────────────────────────
    if data == "list_declined_verify":
        users = await db.get_declined_verification_users()
        if not users:
            await q.edit_message_text("✅ Нет отклонённых верификаций.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))
            return
        lines = []
        buttons = []
        for u in users[:20]:
            tid = u.get("telegram_id")
            name = u.get("first_name", "") or ""
            lname = u.get("last_name", "") or ""
            full = f"{name} {lname}".strip() or str(tid)
            src = u.get("verify_source", "")
            src_detail = u.get("verify_source_detail", "")
            rec_name = u.get("verify_recommender_name", "")
            rec_phone = u.get("verify_recommender_phone", "")
            src_labels = {"friend":"👥 Знакомый","operator":"📞 Оператор","social":"📱 Соцсети","search":"🔍 Интернет","other":"💬 Другое"}
            src_line = src_labels.get(src, "")
            if src == "friend" and rec_name:
                src_line += f": _{rec_name}_ {rec_phone}"
            elif src_detail:
                src_line += f": _{src_detail}_"
            rec_info = f"  {src_line}" if src_line else ""
            lines.append(f"• `{tid}` — {full}{rec_info}")
            buttons.append([InlineKeyboardButton(f"✅ Верифицировать {full}", callback_data=f"verify_{tid}")])
        buttons.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await q.edit_message_text(
            "🔴 *Отклонённые верификации:*\n\n" + "\n".join(lines),
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── ORDER LIST: select order ─────────────────────────────────────────────
    if data.startswith("osel_"):
        parts = data.split("_", 2)  # osel, list_type, oid
        lt  = parts[1]
        oid = parts[2]
        ctx.user_data["lt"] = lt  # persist list context for sub-views
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(
                await order_card(order), parse_mode="HTML",
                reply_markup=await kb_order_actions(order, list_type=lt))
        else:
            await q.answer("❌ Заказ не найден", show_alert=True)

    # ── COMPLETED SUBMENU ──────────────────────────────────────────────────
    elif data.startswith("cmenu_"):
        category = data[6:]  # delivered / declined / cancelled / back
        off = get_operator_offices(op)
        all_orders = await db.get_all_orders(off)
        if category == "back":
            n_delivered = sum(1 for o in all_orders.values() if o.get("status") == "delivered")
            n_declined = sum(1 for o in all_orders.values() if o.get("status") == "declined")
            n_cancelled = sum(1 for o in all_orders.values() if o.get("status") == "cancelled")
            rows = [
                [InlineKeyboardButton(f"✅ Доставленные ({n_delivered})", callback_data="cmenu_delivered")],
                [InlineKeyboardButton(f"🔴 Отклонённые ({n_declined})", callback_data="cmenu_declined")],
                [InlineKeyboardButton(f"🚫 Отменённые ({n_cancelled})", callback_data="cmenu_cancelled")],
                [InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")],
            ]
            await q.edit_message_text("✅ *Завершённые заказы*\n\nВыберите категорию:",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
            return
        filtered = [o for o in all_orders.values() if o.get("status") == category]
        label_map = {"delivered": "✅ Доставленные", "declined": "🔴 Отклонённые", "cancelled": "🚫 Отменённые"}
        label = label_map.get(category, category)
        if not filtered:
            await q.edit_message_text(f"{label}: нет заказов."); return
        from collections import OrderedDict
        dates = OrderedDict()
        for o in sorted(filtered, key=lambda x: x.get("timestamp",""), reverse=True):
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                day = dt.strftime("%d.%m.%Y")
            except: day = "—"
            dates.setdefault(day, []).append(o)
        prefix_map = {"delivered": "dday", "declined": "xday", "cancelled": "cday"}
        pfx = prefix_map[category]
        rows = []
        for day, orders in dates.items():
            cnt = len(orders)
            day_total = sum(o.get("total", 0) for o in orders)
            rows.append([InlineKeyboardButton(f"📅 {day}  ({cnt})  {day_total:,.0f} AED", callback_data=f"{pfx}_{day}")])
        rows.append([InlineKeyboardButton("← Назад", callback_data="cmenu_back")])
        await q.edit_message_text(f"{label}: *{len(filtered)}*\n\nВыберите дату:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    # ── ORDER LIST: back to list ─────────────────────────────────────────────
    # ── COMPLETED: date picker ──────────────────────────────────────────────
    elif data.startswith("dday_"):
        day = data[5:]
        off = get_operator_offices(op)
        all_orders = await db.get_all_orders(off)
        done = []
        for o in all_orders.values():
            if o.get("status") != "delivered": continue
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                if dt.strftime("%d.%m.%Y") == day: done.append(o)
            except: pass
        done.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        if not done:
            await q.edit_message_text("Нет заказов за эту дату."); return
        rows = []
        for o in done[:100]:
            rows.append([InlineKeyboardButton(
                await order_summary_label(o), callback_data=f"osel_d_{o['order_id']}")])
        if len(done) > 100:
            rows.append([InlineKeyboardButton(f"⚠️ Ещё {len(done)-100} скрыто", callback_data="noop")])
        rows.append([InlineKeyboardButton("← К датам", callback_data="cmenu_delivered")])
        await q.edit_message_text(
            f"📅 *{day}*  —  {len(done)} заказов\n\nНажмите на заказ:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    # ── DECLINED-ONLY: date picker drill-in ──────────────────────────────────
    elif data.startswith("xday_"):
        day = data[5:]
        off = get_operator_offices(op)
        all_orders = await db.get_all_orders(off)
        declined = []
        for o in all_orders.values():
            if o.get("status") != "declined": continue
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                if dt.strftime("%d.%m.%Y") == day: declined.append(o)
            except: pass
        declined.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        if not declined:
            await q.edit_message_text("🔴 Нет отклонённых за эту дату."); return
        rows = []
        for o in declined[:100]:
            rows.append([InlineKeyboardButton(
                await order_summary_label(o), callback_data=f"osel_x_{o['order_id']}")])
        if len(declined) > 100:
            rows.append([InlineKeyboardButton(f"⚠️ Ещё {len(declined)-100} скрыто", callback_data="noop")])
        rows.append([InlineKeyboardButton("← К датам", callback_data="cmenu_declined")])
        await q.edit_message_text(
            f"🔴 *{day}*  —  {len(declined)} отклонённых\n\nНажмите на заказ:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    # ── CANCELLED: date drill-in ─────────────────────────────────────────────
    elif data.startswith("cday_"):
        day = data[5:]
        off = get_operator_offices(op)
        all_orders = await db.get_all_orders(off)
        cancelled = []
        for o in all_orders.values():
            if o.get("status") != "cancelled": continue
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                if dt.strftime("%d.%m.%Y") == day: cancelled.append(o)
            except: pass
        cancelled.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        if not cancelled:
            await q.edit_message_text("🚫 Нет отменённых за эту дату."); return
        rows = []
        for o in cancelled[:100]:
            rows.append([InlineKeyboardButton(
                await order_summary_label(o), callback_data=f"osel_c_{o['order_id']}")])
        if len(cancelled) > 100:
            rows.append([InlineKeyboardButton(f"⚠️ Ещё {len(cancelled)-100} скрыто", callback_data="noop")])
        rows.append([InlineKeyboardButton("← К датам", callback_data="cmenu_cancelled")])
        await q.edit_message_text(
            f"🚫 *{day}*  —  {len(cancelled)} отменённых\n\nНажмите на заказ:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    # ── VIEW REVIEW ──────────────────────────────────────────────────────────
    elif data.startswith("rev_") and not data.startswith("rename_"):
        oid = data[4:]
        order = await db.get_order(oid)
        if not order or not order.get("review_score"):
            await q.answer("Нет оценки", show_alert=True); return
        s = order["review_score"]
        tag_labels = {"speed":"Быстрая доставка","courier":"Вежливый курьер","packaging":"Аккуратная упаковка","quality":"Качество товара"}
        tags = order.get("review_tags", [])
        comment = order.get("review_comment", "")
        lines = [
            f"{'⭐'*s} *{s}/5*",
        ]
        if tags:
            lines.append("👍 " + ", ".join(tag_labels.get(t, t) for t in tags))
        if comment:
            lines.append(f"\n💬 _{comment}_")
        lt = ctx.user_data.get("lt", "d")
        await q.edit_message_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← К заказу", callback_data=f"osel_{lt}_{oid}")]
            ]))

    elif data.startswith("olist_"):
        lt = data[6:]
        _olist_redir = {"d": "cmenu_delivered", "x": "cmenu_declined", "c": "cmenu_cancelled"}
        if lt in _olist_redir:
            data = _olist_redir[lt]
            # fall through handled above — re-dispatch
            off = get_operator_offices(op)
            all_orders = await db.get_all_orders(off)
            category = {"d":"delivered","x":"declined","c":"cancelled"}[lt]
            filtered = [o for o in all_orders.values() if o.get("status") == category]
            label_map = {"delivered": "✅ Доставленные", "declined": "🔴 Отклонённые", "cancelled": "🚫 Отменённые"}
            label = label_map[category]
            if not filtered:
                await q.edit_message_text(f"{label}: нет заказов."); return
            from collections import OrderedDict
            dates = OrderedDict()
            for o in sorted(filtered, key=lambda x: x.get("timestamp",""), reverse=True):
                ts = o.get("timestamp","")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                    day = dt.strftime("%d.%m.%Y")
                except: day = "—"
                dates.setdefault(day, []).append(o)
            prefix_map = {"delivered": "dday", "declined": "xday", "cancelled": "cday"}
            pfx = prefix_map[category]
            rows = [[InlineKeyboardButton(f"📅 {d}  ({len(ords)})  {sum(o.get('total',0) for o in ords):,.0f} AED", callback_data=f"{pfx}_{d}")] for d, ords in dates.items()]
            rows.append([InlineKeyboardButton("← Назад", callback_data=f"cmenu_{category}")])
            await q.edit_message_text(f"{label}: *{len(filtered)}*\n\nВыберите дату:",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
            return
        header, empty, items = await _build_order_list(lt, op)
        if not items:
            await q.edit_message_text(empty)
            return
        await q.edit_message_text(
            header + "\n\nНажмите на заказ для просмотра:",
            parse_mode="Markdown",
            reply_markup=await kb_order_list(items, lt))

    # ── ETA back → restore order actions ────────────────────────────────────
    elif data.startswith("eta_back_"):
        parts = data.split("_")
        oid, cid = parts[2], int(parts[3])
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_reply_markup(reply_markup=await kb_order_actions(order, list_type=lt))

    # ── ACCEPT → show ETA ────────────────────────────────────────────────────
    elif data.startswith("acc_"):
        _, oid, cid = data.split("_", 2)
        if int(cid) not in _TEST_ACCOUNTS:
            order = await db.get_order(oid)
            if order and order.get("status") == "cancelled":
                await q.answer("🚫 Заказ отменён клиентом", show_alert=True)
                _done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
                try:
                    await q.edit_message_text(
                        (await order_card(order)) + "\n\n🚫 <b>Отменён клиентом</b>",
                        parse_mode="HTML", reply_markup=_done_kb)
                except: pass
                return
            # Block acceptance for unverified users
            user_doc = await db.get_user(int(cid))
            if user_doc and not user_doc.get("verified", False):
                await q.answer("🔴 Клиент не верифицирован! Сначала верифицируйте или отклоните.", show_alert=True)
                return
        await q.edit_message_reply_markup(reply_markup=kb_eta(oid, cid))

    # ── ETA selected ─────────────────────────────────────────────────────────
    elif data.startswith("eta_"):
        parts = data.split("_")
        eta, oid, cid = int(parts[1]), parts[2], int(parts[3])
        now_dubai = datetime.now(DUBAI_TZ)
        deliver_by = now_dubai + __import__('datetime').timedelta(minutes=eta)
        deliver_by_str = deliver_by.strftime("%H:%M")
        if int(cid) in _TEST_ACCOUNTS:
            _kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🚚 Доставлено #{oid}", callback_data=f"done_{oid}_{cid}"),
                 InlineKeyboardButton("🚫 Отменить", callback_data=f"opcancel_{oid}_{cid}")],
                [InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")],
            ])
            await q.edit_message_text(
                f"🟢 <b>ТЕСТ</b> — #{oid} Принят, ETA {eta} мин (до {deliver_by_str})",
                parse_mode="HTML", reply_markup=_kb)
        else:
            await db.update_order(oid, status="approved", eta=eta,
                                  operator_id=op, updated_at=datetime.now(timezone.utc).isoformat(),
                                  confirmed_at=datetime.now(timezone.utc).isoformat(),
                                  deliver_by=deliver_by_str)
            await update_customer_card(oid)
            order = await db.get_order(oid)
            if order:
                lt = ctx.user_data.get("lt")
                await q.edit_message_text(
                    await order_card(order),
                    parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    # ── DECLINE ───────────────────────────────────────────────────────────────
    elif data.startswith("dec_"):
        _, oid, cid = data.split("_", 2); cid = int(cid)
        _done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
        if int(cid) in _TEST_ACCOUNTS:
            await q.edit_message_text(
                f"🟢 <b>ТЕСТ</b> — #{oid} Отклонён\n\n✅ Просмотрено",
                parse_mode="HTML", reply_markup=_done_kb)
        else:
            order_chk = await db.get_order(oid)
            if order_chk and order_chk.get("status") == "cancelled":
                await q.answer("🚫 Заказ уже отменён клиентом", show_alert=True)
                try:
                    await q.edit_message_text(
                        (await order_card(order_chk)) + "\n\n🚫 <b>Отменён клиентом</b>",
                        parse_mode="HTML", reply_markup=_done_kb)
                except: pass
                return
            await db.update_order(oid, status="declined", updated_at=datetime.now(timezone.utc).isoformat())
            await db._increment_user(cid, orders_declined=1)
            await update_customer_card(oid)
            order = await db.get_order(oid)
            if order:
                await q.edit_message_text(
                    (await order_card(order)) + "\n\n❌ <b>Отклонён</b>",
                    parse_mode="HTML", reply_markup=_done_kb)
                try:
                    from owner_routes import notify_owners
                    await notify_owners("orders.declined",
                        f"❌ *Заказ отклонён #{oid}*\n"
                        f"Клиент: {order.get('customer_name','—')}\n"
                        f"Сумма: {order.get('total',0)} AED")
                except Exception as e:
                    log.error(f"[owner-notif] orders.declined failed: {e}")

    # ── OPERATOR CANCEL (approved → cancelled) ─────────────────────────────
    elif data.startswith("opcancel_"):
        _, oid, cid = data.split("_", 2); cid = int(cid)
        if int(cid) in _TEST_ACCOUNTS:
            await q.edit_message_text(
                f"🟢 <b>ТЕСТ</b> — #{oid} Отменён оператором\n\n✅ Просмотрено",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))
        else:
            order = await db.get_order(oid)
            if not order or order.get("status") != "approved":
                await q.answer("Заказ уже не в статусе «Принят»", show_alert=True); return
            await db.update_order(oid, status="cancelled", updated_at=datetime.now(timezone.utc).isoformat())
            await update_customer_card(oid)
            order = await db.get_order(oid)
            if order:
                lt = ctx.user_data.get("lt")
                await q.edit_message_text(
                    (await order_card(order)) + "\n\n🚫 <b>Отменён оператором</b>",
                    parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    # ── DELIVERED ─────────────────────────────────────────────────────────────
    elif data.startswith("done_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        if cid in _TEST_ACCOUNTS:
            await q.edit_message_text(
                f"🟢 <b>ТЕСТ</b> — #{oid} Доставлен\n\n✅ Просмотрено",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))
        else:
            await db.update_order(oid, status="delivered", updated_at=datetime.now(timezone.utc).isoformat())
            order = await db.get_order(oid)
            total = (order or {}).get("total", 0)
            await db._increment_user(cid, orders_done=1, total_spent=total)
            await update_customer_card(oid)
            order = await db.get_order(oid)
            if order:
                lt = ctx.user_data.get("lt")
                await q.edit_message_text(
                    (await order_card(order)) + "\n\n✅ <b>Доставлен</b>",
                    parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))
                try:
                    from owner_routes import notify_owners
                    sent = await notify_owners("orders.delivered",
                        f"✅ *Заказ доставлен #{oid}*\n"
                        f"Клиент: {order.get('customer_name','—')}\n"
                        f"Сумма: {order.get('total',0)} AED")
                    if sent:
                        await db.update_order(oid, _delivered_notif_msgs=sent)
                except Exception as e:
                    log.error(f"[owner-notif] orders.delivered failed: {e}")

    # ── UNDO DELIVERED → back to approved ────────────────────────────────────
    elif data.startswith("undone_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        order = await db.get_order(oid)
        if order and order.get("status") == "delivered":
            total = order.get("total", 0)
            await db._increment_user(cid, orders_done=-1, total_spent=-total)
            # Delete the "delivered" notification messages from @ambar_manage_bot
            try:
                from api_server import tg_delete
                from owner_routes import OWNER_BOT_TOKEN
                for m in order.get("_delivered_notif_msgs", []):
                    await tg_delete(OWNER_BOT_TOKEN, m["chat_id"], m["message_id"])
            except Exception as e:
                log.error(f"[owner-notif] delete delivered msgs failed: {e}")
            await db.update_order(oid, status="approved", _delivered_notif_msgs=[], updated_at=datetime.now().isoformat())
            await update_customer_card(oid)
            # Owner alert: order un-delivered (e.g. operator hit "Delivered" by mistake).
            try:
                from owner_routes import notify_owners_force
                _op = q.from_user
                _opn = ('@'+_op.username) if _op.username else (_op.first_name or str(_op.id))
                await notify_owners_force(
                    "orders.reverted",
                    f"🔄 *Заказ возвращён в доставку #{oid}*\n"
                    f"Был «доставлен» — оператор {_opn} вернул его в активные.\n"
                    f"💰 {total} AED · {order.get('customer_name','—')}")
            except Exception as e:
                log.error(f"[owner-notif] reverted notify failed: {e}")
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(
                (await order_card(order)) + "\n\n🔄 <b>Возвращён в доставку</b>",
                parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    # ── UNDO CANCELLED → back to approved ───────────────────────────────────
    elif data.startswith("undocancel_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        order = await db.get_order(oid)
        if order and order.get("status") == "cancelled":
            await db.update_order(oid, status="approved", updated_at=datetime.now().isoformat())
            await update_customer_card(oid)
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(
                (await order_card(order)) + "\n\n🔄 <b>Возвращён в доставку</b>",
                parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    # ── BACK TO ORDER (from sub-views) ────────────────────────────────────────
    elif data.startswith("back_order_"):
        oid = data[len("back_order_"):]
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(await order_card(order), parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

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
        await q.edit_message_text(await order_card(order), parse_mode="HTML",
                                  reply_markup=await kb_order_actions(order, list_type=lt))
        # Push the updated items/total to the customer's live msg
        await update_customer_card(oid)
        # Always notify owners the order was edited (bypasses filters + quiet).
        try:
            from owner_routes import notify_owners_force
            _items = "\n".join(f"• {i.get('name','')} ×{i.get('qty',1)}"
                               for i in order.get("items", [])) or "—"
            _op = q.from_user   # only operators can edit (the app has no client edit) — name them
            _opn = ('@'+_op.username) if _op.username else (_op.first_name or str(_op.id))
            await notify_owners_force(
                "orders.edited",
                f"✏️ *Заказ изменён #{oid}* — оператором {_opn}\n"
                f"💰 Новый итог: *{order.get('total', 0)} AED*\n"
                f"🛒 Позиции:\n{_items}")
        except Exception as e:
            log.error(f"[op] edit notify failed: {e}")

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

    elif data.startswith("ei_cat_"):
        # ei_cat_{oid}_{category}
        parts = data.split("_", 3); oid = parts[2]; cat = parts[3]
        await q.edit_message_reply_markup(reply_markup=kb_add_product(oid, cat))

    elif data.startswith("ei_add_"):
        oid = data[7:]
        await q.edit_message_reply_markup(reply_markup=kb_add_categories(oid))

    elif data.startswith("ei_beer_"):
        # ei_beer_{oid}_{pid} — show pack size picker
        parts = data.split("_"); oid = parts[2]; pid = parts[3]
        await q.edit_message_reply_markup(reply_markup=kb_beer_pack(oid, pid))

    elif data.startswith("ei_addp_"):
        parts = data.split("_")
        oid, pid = parts[2], parts[3]
        pack = parts[4] if len(parts) > 4 else None  # "12" or "24" for beer
        order = await db.get_order(oid)
        if not order: return
        pmap  = {p["id"]: p for p in PRODUCTS}
        p     = pmap.get(pid)
        if not p: return
        # Determine name and price
        if pack and p.get("p12"):
            price = beer_pack_price(p, pack)
            item_name = f"{p['name']} ×{pack}"
            item_id = f"{pid}_{pack}"
        else:
            price = p["price"]
            item_name = p["name"]
            item_id = pid
        items = order.get("items", [])
        for item in items:
            if item["id"] == item_id: item["qty"] += 1; break
        else:
            items.append({"id": item_id, "name": item_name, "price": price, "qty": 1, "line_total": price})
        order["items"] = items
        order = recalc_order(order)
        await db.update_order(oid, items=order["items"], subtotal=order["subtotal"], total=order["total"])
        order = await db.get_order(oid)
        try: await q.edit_message_reply_markup(reply_markup=kb_edit(order))
        except: pass

    # ── FREE POSITION — operator types name + price ─────────────────────────
    elif data.startswith("ei_free_"):
        oid = data[8:]
        ctx.user_data["pending_free_name"] = {"oid": oid, "msg_id": q.message.message_id}
        await q.edit_message_text(
            f"📝 *Свободная позиция*\n\nЗаказ: `#{oid}`\n\n✏️ Введите название товара:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Отмена", callback_data=f"edit_{oid}")
            ]]))

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
            await q.edit_message_text(await customer_card(order), parse_mode="Markdown", reply_markup=await kb_client_actions(oid, cid))

    # ── CLIENT INFO VIEW ───────────────────────────────────────────────────────
    elif data.startswith("client_back_"):
        oid = data[len("client_back_"):]
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(await order_card(order), parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    elif data.startswith("client_"):
        try:
            _, oid, cid_str = data.split("_", 2)
            order = await db.get_order(oid)
            if not order:
                await q.answer(f"❌ Заказ #{oid} не найден", show_alert=True)
                return
            await q.edit_message_text(
                await customer_card(order),
                parse_mode="HTML",
                reply_markup=await kb_client_actions(oid, int(cid_str)),
            )
        except Exception as e:
            log.exception(f"client_ callback failed for data={data!r}")
            try: await q.answer(f"⚠️ Ошибка: {type(e).__name__}", show_alert=True)
            except Exception: pass

    # ── RENAME CLIENT ────────────────────────────────────────────────────────
    elif data.startswith("rename_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        ctx.user_data["pending_rename"] = {"cid": cid, "oid": oid, "msg_id": q.message.message_id}
        # Show current nickname if any
        user_doc = await db.get_user(cid)
        current_nick = (user_doc or {}).get("custom_name", "")
        nick_line = f"\nТекущая заметка: _{current_nick}_" if current_nick else ""
        buttons = []
        if current_nick:
            buttons.append([InlineKeyboardButton("🗑 Убрать заметку", callback_data=f"clearnick_{oid}_{cid}")])
        buttons.append([InlineKeyboardButton("← Отмена", callback_data=f"client_{oid}_{cid}")])
        await q.edit_message_text(
            f"✏️ *Заметка к клиенту*\n\nID: `{cid}`{nick_line}\n\n_Отправьте заметку/никнейм сообщением_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))

    # ── CLEAR NICKNAME ────────────────────────────────────────────────────────
    elif data.startswith("clearnick_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        await db.upsert_user(cid, custom_name="")
        # Restore original name on all orders
        user_doc = await db.get_user(cid)
        original = (user_doc or {}).get("full_name") or (user_doc or {}).get("name") or "—"
        all_orders = await db.get_all_orders()
        for o in all_orders.values():
            if o.get("customer_id") == cid:
                await db.update_order(o["order_id"], customer_name=original)
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(await customer_card(order), parse_mode="Markdown", reply_markup=await kb_client_actions(oid, cid))

    # ── BAN (generic — show confirmation) ─────────────────────────────────────
    elif data.startswith("ban_"):
        parts = data.split("_"); oid = parts[1]; cid = int(parts[2])
        await q.edit_message_text(
            f"⚠️ *Блокировка клиента* `{cid}`\n\nВыберите действие:",
            parse_mode="Markdown", reply_markup=kb_ban_confirm(cid, oid))

    # ── VERIFY ────────────────────────────────────────────────────────────────
    elif data.startswith("verify_"):
        cid = int(data[7:])
        user_doc_before = await db.get_user(cid)
        await db.verify_user(cid)
        await db.undecline_verification(cid)
        await q.answer("✅ Клиент верифицирован")
        # Remove the "ПРОЙДИТЕ ВЕРИФИКАЦИЮ" warning message from the customer bot
        warn_mid = (user_doc_before or {}).get("verify_warn_msg_id")
        if warn_mid:
            try:
                app2 = Application.builder().token(BOT_TOKEN).build()
                async with app2:
                    try: await app2.bot.delete_message(chat_id=cid, message_id=warn_mid)
                    except Exception: pass
                await db.set_user_field(cid, verify_warn_msg_id=None)
            except Exception as e:
                log.debug(f"delete verify-warning for {cid} failed: {e}")
        # Find order_id from message text to refresh with full buttons
        import re as _re
        oid_match = _re.search(r'#(AMB\d+)', q.message.text or "")
        if oid_match:
            oid = oid_match.group(1)
            order = await db.get_order(oid)
            if order:
                lt = ctx.user_data.get("lt")
                await q.edit_message_text(
                    await order_card(order),
                    parse_mode="HTML",
                    reply_markup=await kb_order_actions(order, list_type=lt))
        else:
            # Fallback: just replace buttons
            old_kb = q.message.reply_markup
            if old_kb:
                new_rows = [row for row in old_kb.inline_keyboard
                            if not any(b.callback_data and (b.callback_data.startswith("verify_") or b.callback_data.startswith("decverify_")) for b in row)]
                new_rows.append([InlineKeyboardButton("🔐 Верифицирован ✅", callback_data="noop")])
                await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))
        # Owner notification — delete old "Запрос" msg, send "Пройдена"
        try:
            from owner_routes import notify_owners, OWNER_BOT_TOKEN
            from api_server import tg_delete
            # Delete old "Запрос верификации" messages
            old_msgs = (user_doc_before or {}).get("verify_owner_msg_ids", [])
            for m in old_msgs:
                try:
                    await tg_delete(OWNER_BOT_TOKEN, m["chat_id"], m["message_id"])
                except Exception:
                    pass
            await db.set_user_field(cid, verify_owner_msg_ids=[])
            v_user = await db.get_user(cid)
            v_name = f"{(v_user or {}).get('first_name','')} {(v_user or {}).get('last_name','')}".strip() or str(cid)
            v_uname = (v_user or {}).get("username", "")
            await notify_owners(
                "customers.verified",
                f"✅ *Верификация пройдена*\n"
                f"Клиент: {v_name}\n"
                + (f"@{v_uname}\n" if v_uname else "")
                + f"ID: `{cid}`\n"
                f"Оператор: {q.from_user.first_name or q.from_user.id}"
            )
        except Exception as e:
            log.error(f"[owner-notif] customers.verified failed: {e}")
        # Send confirmation
        _dismiss = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
        await q.message.reply_text(
            f"✅ Клиент `{cid}` верифицирован.",
            parse_mode="Markdown", reply_markup=_dismiss)

    # ── DECLINE VERIFICATION ─────────────────────────────────────────────────
    elif data.startswith("decverify_"):
        parts = data.split("_", 2)
        oid = parts[1]
        cid = int(parts[2])
        # Save the order message ID so we can update it after decline
        ctx.user_data["decv_order_msg"] = q.message.message_id
        # Show confirmation with optional comment
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отклонить без комментария", callback_data=f"decvconf_{oid}_{cid}")],
            [InlineKeyboardButton("✏️ Добавить комментарий", callback_data=f"decvcomm_{oid}_{cid}")],
            [InlineKeyboardButton("← Отмена", callback_data="delmsg")],
        ])
        await q.message.reply_text(
            f"🔴 *Отклонение верификации* `{cid}`\n\nДобавить комментарий?",
            parse_mode="Markdown", reply_markup=kb)

    # ── DECLINE VERIFY: with comment prompt ──────────────────────────────────
    elif data.startswith("decvcomm_"):
        parts = data.split("_", 2)
        oid = parts[1]
        cid = int(parts[2])
        ctx.user_data["decv_oid"] = oid
        ctx.user_data["decv_cid"] = cid
        ctx.user_data["awaiting_decv_comment"] = True
        ctx.user_data["decv_prompt_msg"] = q.message.message_id
        await q.edit_message_text(
            f"✏️ Напишите причину отклонения для клиента `{cid}`:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Отмена", callback_data="cancel_decv")]]))

    # ── DECLINE VERIFY: cancel comment ───────────────────────────────────────
    elif data == "cancel_decv":
        ctx.user_data.pop("awaiting_decv_comment", None)
        ctx.user_data.pop("decv_oid", None)
        ctx.user_data.pop("decv_cid", None)
        await q.edit_message_text("❌ Отклонение отменено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))

    # ── DECLINE VERIFY: confirm without comment ──────────────────────────────
    elif data.startswith("decvconf_"):
        parts = data.split("_", 2)
        oid = parts[1]
        cid = int(parts[2])
        order_msg = ctx.user_data.pop("decv_order_msg", None)
        try: await q.message.delete()
        except: pass
        await _do_decline_verification(ctx.bot, q.message.chat_id, cid, oid, "", edit_msg_id=order_msg)

    # ── UNBAN ─────────────────────────────────────────────────────────────────
    elif data.startswith("unban_"):
        uid_str = data[6:]
        cid = int(uid_str)
        user_doc = await db.get_user(cid)
        ban_msg_id = user_doc.get("last_ban_msg_id") if user_doc else None
        await db.unban_user(cid)
        try:
            app2 = Application.builder().token(BOT_TOKEN).build()
            unban_text = ("✅ *Ваш аккаунт разблокирован!*\n\n"
                          "Теперь вы снова можете делать заказы. Нажмите кнопку ниже 👇")
            async with app2:
                # Edit the ban msg in place if we still have it; otherwise send new.
                edited = False
                if ban_msg_id:
                    try:
                        await app2.bot.edit_message_text(
                            unban_text, chat_id=cid, message_id=ban_msg_id,
                            parse_mode="Markdown")
                        edited = True
                    except: pass
                if not edited:
                    await app2.bot.send_message(cid, unban_text, parse_mode="Markdown")
                await app2.bot.set_chat_menu_button(
                    chat_id=cid,
                    menu_button=MenuButtonWebApp(text="🍾 Заказать", web_app=WebAppInfo(url=WEBAPP_URL))
                )
        except: pass
        await q.edit_message_text(f"✅ Пользователь `{uid_str}` разблокирован.", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))


# ── Decline verification helper ──────────────────────────────────────────────
async def _do_decline_verification(bot, chat_id: int, cid: int, oid: str, comment: str, edit_msg_id: int = None):
    """Decline verification, auto-decline pending orders, notify customer."""
    await db.decline_verification(cid)
    if comment:
        await db.upsert_user(cid, verify_decline_reason=comment)
    # Auto-decline all pending orders
    pending = await db.get_pending_orders_for_user(cid)
    declined_oids = []
    for po in pending:
        po_oid = po.get("order_id")
        if po_oid:
            await db.update_order(po_oid, status="declined", updated_at=datetime.now(timezone.utc).isoformat())
            await db._increment_user(cid, orders_declined=1)
            declined_oids.append(po_oid)
    # Edit each pending order's live msg → "declined" (with support button).
    # No separate "verification declined" notification — reduces noise.
    lang_u = pending[0].get("lang", "ru") if pending else "ru"
    support_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Написать в поддержку" if lang_u == "ru" else "💬 Contact support",
                             url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
    ]])
    for po_oid in declined_oids:
        await update_customer_card(po_oid, reply_markup=support_kb)
    # Update the original order message if we have it
    if edit_msg_id and oid and oid != "0":
        order = await db.get_order(oid)
        if order:
            _done_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
            try:
                await bot.edit_message_text(
                    (await order_card(order)) + "\n\n🔴 <b>Верификация отклонена</b>",
                    chat_id=chat_id, message_id=edit_msg_id,
                    parse_mode="HTML", reply_markup=_done_kb)
            except: pass
    # Owner notification — delete old "Запрос" msg, send "Отклонена"
    try:
        from owner_routes import notify_owners, OWNER_BOT_TOKEN
        from api_server import tg_delete
        user_doc = await db.get_user(cid)
        old_msgs = (user_doc or {}).get("verify_owner_msg_ids", [])
        for m in old_msgs:
            try:
                await tg_delete(OWNER_BOT_TOKEN, m["chat_id"], m["message_id"])
            except Exception:
                pass
        await db.set_user_field(cid, verify_owner_msg_ids=[])
        v_name = f"{(user_doc or {}).get('first_name','')} {(user_doc or {}).get('last_name','')}".strip() or str(cid)
        v_uname = (user_doc or {}).get("username", "")
        comment_ln = f"\nПричина: {comment}" if comment else ""
        await notify_owners(
            "customers.verified",
            f"🔴 *Верификация отклонена*\n"
            f"Клиент: {v_name}\n"
            + (f"@{v_uname}\n" if v_uname else "")
            + f"ID: `{cid}`{comment_ln}"
        )
    except Exception as e:
        log.error(f"[owner-notif] customers.verified decline failed: {e}")
    # Report
    declined_info = f"\n📦 Отклонено заказов: {len(declined_oids)}" if declined_oids else ""
    comment_info = f"\n💬 Комментарий: {comment}" if comment else ""
    _dismiss = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
    await bot.send_message(chat_id,
        f"🔴 Верификация клиента `{cid}` отклонена.{comment_info}{declined_info}",
        parse_mode="Markdown", reply_markup=_dismiss)


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
    app.add_error_handler(on_error)
    log.info("🛠 AMBAR Operator Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def on_error(update, ctx):
    """Last-resort handler: surface any unhandled exception to the operator
    as an alert popup instead of letting it die silently. Without this,
    a BadRequest from a malformed Markdown/HTML message looks identical to
    'button does nothing' from the operator's side."""
    log.exception("Unhandled error in operator bot handler", exc_info=ctx.error)
    try:
        if update and getattr(update, "callback_query", None):
            err_name = type(ctx.error).__name__ if ctx.error else "Error"
            await update.callback_query.answer(f"⚠️ {err_name}", show_alert=True)
    except Exception:
        pass


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
