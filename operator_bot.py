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
    # ── Водка / Vodka ─────────────────────────────────────────────────────────
    {"id":"p1",  "name":"Absolut 1 ltr",              "price":100, "cat":"Водка"},
    {"id":"p2",  "name":"Stolichnaya 1 ltr",          "price":100, "cat":"Водка"},
    {"id":"p5",  "name":"Smirnoff Vodka 1 ltr",       "price":100, "cat":"Водка"},
    {"id":"p3",  "name":"Russian Standard 1 ltr",     "price":150, "cat":"Водка"},
    {"id":"p4",  "name":"Skyy Vodka 1 ltr",           "price":150, "cat":"Водка"},
    {"id":"p6",  "name":"Beluga 0.7 ltr",             "price":200, "cat":"Водка"},
    {"id":"p7",  "name":"Grey Goose 1 ltr",           "price":200, "cat":"Водка"},
    {"id":"p8",  "name":"Belvedere 1 ltr",            "price":200, "cat":"Водка"},
    {"id":"p9",  "name":"Ciroc 1 ltr",                "price":250, "cat":"Водка"},
    # ── Виски / Whisky ────────────────────────────────────────────────────────
    {"id":"p10", "name":"Red Label 1 ltr",             "price":100, "cat":"Виски"},
    {"id":"p15", "name":"Ballantines Finest 1 ltr",    "price":100, "cat":"Виски"},
    {"id":"p23", "name":"J&B 1 ltr",                   "price":150, "cat":"Виски"},
    {"id":"p11", "name":"Black Label 1 ltr",           "price":200, "cat":"Виски"},
    {"id":"p12", "name":"Jack Daniels 1 ltr",          "price":200, "cat":"Виски"},
    {"id":"p13", "name":"Chivas Regal 12Y 1 ltr",     "price":200, "cat":"Виски"},
    {"id":"p14", "name":"Jameson 1 ltr",               "price":200, "cat":"Виски"},
    {"id":"p16", "name":"Double Black 1 ltr",          "price":250, "cat":"Виски"},
    {"id":"p19", "name":"Jack Daniels Honey 1 ltr",   "price":250, "cat":"Виски"},
    {"id":"p20", "name":"Gentleman Jack 1 ltr",        "price":250, "cat":"Виски"},
    {"id":"p25", "name":"Glenfiddich 12Y 1 ltr",      "price":300, "cat":"Виски"},
    {"id":"p17", "name":"Gold Label 1 ltr",            "price":350, "cat":"Виски"},
    {"id":"p18", "name":"Chivas Regal 18Y 1 ltr",     "price":400, "cat":"Виски"},
    {"id":"p26", "name":"Glenfiddich 15Y 1 ltr",      "price":400, "cat":"Виски"},
    {"id":"p27", "name":"Glenfiddich 18Y 0.75 ltr",   "price":500, "cat":"Виски"},
    {"id":"p28", "name":"Macallan 12Y 0.7 ltr",       "price":500, "cat":"Виски"},
    {"id":"p29", "name":"Macallan 15Y 0.7 ltr",       "price":750, "cat":"Виски"},
    {"id":"p22", "name":"Chivas Royal Salute 21Y 1 ltr","price":1100,"cat":"Виски"},
    {"id":"p24", "name":"Chivas Regal 25Y 0.7 ltr",   "price":1300, "cat":"Виски"},
    {"id":"p21", "name":"Blue Label 1 ltr",            "price":1400, "cat":"Виски"},
    {"id":"p30", "name":"Macallan 18Y 0.75 ltr",      "price":2000, "cat":"Виски"},
    # ── Пиво / Beer (pack-only: 12 & 24) ─────────────────────────────────────
    {"id":"p31", "name":"Heineken 0.33 can",           "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p33", "name":"Budweiser 0.33 can",          "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p35", "name":"Stella Artois 0.33 can",      "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p37", "name":"Red Horse 0.5 can",           "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p38", "name":"Amstel Light 0.33 can",       "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p40", "name":"XXL Vodka 0.25 can",          "cat":"Пиво", "p12":100, "p24":200},
    {"id":"p32", "name":"Heineken 0.33 bottle",        "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p34", "name":"Budweiser 0.33 bottle",       "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p36", "name":"Stella Artois 0.33 bottle",   "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p41", "name":"Asahi Super Dry 0.33 bottle", "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p42", "name":"Hoegaarden 0.33 bottle",      "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p43", "name":"Corona Extra 0.355 bottle",   "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p44", "name":"Peroni Nastro 0.33 bottle",   "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p45", "name":"Smirnoff Ice 0.275 bottle",   "cat":"Пиво", "p12":150, "p24":300},
    {"id":"p46", "name":"Bacardi Breezer 0.275 bottle","cat":"Пиво", "p12":150, "p24":300},
    {"id":"p39", "name":"Guinness 0.44 can",           "cat":"Пиво", "p12":200, "p24":400},
    # ── Ром / Rum ─────────────────────────────────────────────────────────────
    {"id":"p47", "name":"Bacardi White 1 ltr",         "price":100, "cat":"Ром"},
    {"id":"p48", "name":"Bacardi Black 1 ltr",         "price":100, "cat":"Ром"},
    {"id":"p49", "name":"Bacardi Gold 1 ltr",          "price":100, "cat":"Ром"},
    {"id":"p50", "name":"Captain Morgan Black 1 ltr",  "price":150, "cat":"Ром"},
    {"id":"p51", "name":"Captain Morgan Gold 1 ltr",   "price":150, "cat":"Ром"},
    {"id":"p52", "name":"Malibu 1 ltr",                "price":150, "cat":"Ром"},
    # ── Вермут / Vermouth ─────────────────────────────────────────────────────
    {"id":"p53", "name":"Martini Bianco 1 ltr",        "price":100, "cat":"Вермут"},
    # ── Джин / Gin ────────────────────────────────────────────────────────────
    {"id":"p54", "name":"Gordon's 1 ltr",              "price":100, "cat":"Джин"},
    {"id":"p55", "name":"Bombay Sapphire 1 ltr",       "price":150, "cat":"Джин"},
    {"id":"p57", "name":"Gordon Pink 0.7 ltr",         "price":150, "cat":"Джин"},
    {"id":"p58", "name":"Tanqueray 1 ltr",             "price":200, "cat":"Джин"},
    {"id":"p56", "name":"Hendrick's 1 ltr",            "price":250, "cat":"Джин"},
    {"id":"p60", "name":"Malfy Con Arancia 0.7 ltr",   "price":250, "cat":"Джин"},
    {"id":"p61", "name":"Malfy Rosa 0.7 ltr",          "price":250, "cat":"Джин"},
    {"id":"p62", "name":"Drumshanbo Gunpowder 0.7 ltr","price":300, "cat":"Джин"},
    {"id":"p59", "name":"Monkey 47 0.5 ltr",           "price":350, "cat":"Джин"},
    # ── Текила / Tequila ──────────────────────────────────────────────────────
    {"id":"p63", "name":"Jose Cuervo Silver 1 ltr",    "price":100, "cat":"Текила"},
    {"id":"p64", "name":"Jose Cuervo Gold 1 ltr",      "price":100, "cat":"Текила"},
    {"id":"p65", "name":"Patron XO Cafe 0.75 ltr",     "price":250, "cat":"Текила"},
    {"id":"p66", "name":"Patron Silver 0.75 ltr",      "price":300, "cat":"Текила"},
    {"id":"p67", "name":"Patron Gold 0.75 ltr",        "price":350, "cat":"Текила"},
    {"id":"p68", "name":"Don Julio Blanco 70/75cl",    "price":350, "cat":"Текила"},
    {"id":"p69", "name":"Don Julio Reposado 70/75cl",  "price":400, "cat":"Текила"},
    {"id":"p70", "name":"Don Julio Anejo 70/75cl",     "price":550, "cat":"Текила"},
    {"id":"p71", "name":"Don Julio 1942 70/75cl",      "price":1500,"cat":"Текила"},
    {"id":"p72", "name":"Clase Azul Reposado 70/75cl", "price":1700,"cat":"Текила"},
    # ── Коньяк / Cognac ──────────────────────────────────────────────────────
    {"id":"p73", "name":"Hennessy VS 1 ltr",           "price":350, "cat":"Коньяк"},
    {"id":"p76", "name":"Remy Martin VSOP 1 ltr",      "price":400, "cat":"Коньяк"},
    {"id":"p74", "name":"Hennessy VSOP 1 ltr",         "price":450, "cat":"Коньяк"},
    {"id":"p75", "name":"Hennessy XO 1 ltr",           "price":1600,"cat":"Коньяк"},
    # ── Ликёр / Liqueur ──────────────────────────────────────────────────────
    {"id":"p77", "name":"Baileys 1 ltr",               "price":150, "cat":"Ликёр"},
    {"id":"p78", "name":"Amarula 1 ltr",               "price":150, "cat":"Ликёр"},
    {"id":"p80", "name":"Aperol 1 ltr",                "price":150, "cat":"Ликёр"},
    {"id":"p79", "name":"Jagermeister 1 ltr",          "price":200, "cat":"Ликёр"},
    {"id":"p81", "name":"Tequila Rose 0.7 ltr",        "price":200, "cat":"Ликёр"},
    # ── Арак / Arak ──────────────────────────────────────────────────────────
    {"id":"p82", "name":"Arak Touma 0.75 ltr",         "price":100, "cat":"Арак"},
    {"id":"p83", "name":"Efe Raki 1 ltr",              "price":150, "cat":"Арак"},
    # ── Шампанское / Champagne ────────────────────────────────────────────────
    {"id":"p84", "name":"Moet Brut 0.75",              "price":300, "cat":"Шампанское"},
    {"id":"p85", "name":"Moet Rose 0.75",              "price":400, "cat":"Шампанское"},
    {"id":"p87", "name":"Veuve Clicquot 0.75",         "price":450, "cat":"Шампанское"},
    {"id":"p86", "name":"Moet Ice 0.75",               "price":500, "cat":"Шампанское"},
    {"id":"p88", "name":"Ruinart Blanc 0.75",          "price":800, "cat":"Шампанское"},
    {"id":"p89", "name":"Dom Perignon 0.75",           "price":1500,"cat":"Шампанское"},
    # ── Просекко / Prosecco ───────────────────────────────────────────────────
    {"id":"p93", "name":"Martini Asti 0.75",           "price":100, "cat":"Просекко"},
    {"id":"p90", "name":"Bottega Prosecco 0.75",       "price":150, "cat":"Просекко"},
    {"id":"p94", "name":"Zonin Prosecco 0.75",         "price":150, "cat":"Просекко"},
    {"id":"p91", "name":"Bottega Rose 0.75",           "price":200, "cat":"Просекко"},
    {"id":"p92", "name":"Bottega Gold 0.75",           "price":250, "cat":"Просекко"},
    # ── Вино / Wine ───────────────────────────────────────────────────────────
    {"id":"p95",  "name":"Jacob Creek Chardonnay 0.75",    "price":100, "cat":"Вино"},
    {"id":"p96",  "name":"Pinot Grigio Cesari 0.75",      "price":100, "cat":"Вино"},
    {"id":"p97",  "name":"Le Grand Noir SB 0.75",         "price":100, "cat":"Вино"},
    {"id":"p104", "name":"Jacob Creek Shiraz 0.75",        "price":100, "cat":"Вино"},
    {"id":"p105", "name":"Le Grand Noir Merlot 0.75",     "price":100, "cat":"Вино"},
    {"id":"p114", "name":"Mateus Rose 0.75",               "price":100, "cat":"Вино"},
    {"id":"p116", "name":"Chateau Ksara Rose 0.75",        "price":100, "cat":"Вино"},
    {"id":"p99",  "name":"Calvet Sancerre 0.75",          "price":150, "cat":"Вино"},
    {"id":"p108", "name":"Chateau Saint Leon 0.75",        "price":150, "cat":"Вино"},
    {"id":"p111", "name":"La Celia Malbec 0.75",           "price":150, "cat":"Вино"},
    {"id":"p119", "name":"MiP Collection Rose 0.75",       "price":150, "cat":"Вино"},
    {"id":"p98",  "name":"Rimapere SB 0.75",              "price":200, "cat":"Вино"},
    {"id":"p103", "name":"Oyster Bay SB 0.75",             "price":200, "cat":"Вино"},
    {"id":"p106", "name":"Castel Barreyres 0.75",          "price":200, "cat":"Вино"},
    {"id":"p107", "name":"Chateau Perron 0.75",            "price":200, "cat":"Вино"},
    {"id":"p109", "name":"Campo Viejo Reserva 0.75",       "price":200, "cat":"Вино"},
    {"id":"p110", "name":"Chateau Des Laurets 0.75",       "price":200, "cat":"Вино"},
    {"id":"p115", "name":"Minuty Cotes De Provence 0.75",  "price":200, "cat":"Вино"},
    {"id":"p120", "name":"Drostdy Hof Grand Cru 5 ltr",   "price":200, "cat":"Вино"},
    {"id":"p121", "name":"Drostdy Hof Claret 5 ltr",      "price":200, "cat":"Вино"},
    {"id":"p100", "name":"Louis Moreau Chablis 0.75",      "price":250, "cat":"Вино"},
    {"id":"p101", "name":"Bourgogne Louis Jadot 0.75",     "price":250, "cat":"Вино"},
    {"id":"p102", "name":"Gavi Di Gavi 0.75",              "price":250, "cat":"Вино"},
    {"id":"p112", "name":"Campo Viejo Gran Reserva 0.75",  "price":250, "cat":"Вино"},
    {"id":"p117", "name":"Whispering Angel 0.75",          "price":250, "cat":"Вино"},
    {"id":"p118", "name":"Saint Maur Rose 0.75",           "price":250, "cat":"Вино"},
    {"id":"p113", "name":"Chateau Lagrange 0.75",          "price":800, "cat":"Вино"},
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

def get_operator_office(uid):
    try:
        from config_offices import OFFICE_OPERATORS
        for oid, ops in OFFICE_OPERATORS.items():
            if uid in ops:
                return oid
    except: pass
    return None


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
    parts = [f"#{o['order_id']}"]
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
            if user_doc and not user_doc.get("verified", False) and not user_doc.get("referred_by"):
                label = "🔴 " + label + " 🔴"
    except: pass
    return label


# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_main():
    return ReplyKeyboardMarkup([
        ["🆕 Новые заказы (ожидают ответа)",   "🟢 Активные"],
        ["✅ Завершённые",    "📊 Статистика"],
        ["🚫 Бан / Нет верификации", "❓ Помощь"],
    ], resize_keyboard=True)

async def kb_order_list(items, list_type, limit=15):
    """Compact list of orders as inline buttons."""
    rows = []
    for o in items[:limit]:
        rows.append([InlineKeyboardButton(
            await order_summary_label(o),
            callback_data=f"osel_{list_type}_{o['order_id']}"
        )])
    rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
    return InlineKeyboardMarkup(rows)

async def kb_order_actions(order, list_type=None):
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
    if st == "delivered":
        rows.append([InlineKeyboardButton("🔄 Вернуть в доставку", callback_data=f"undone_{oid}_{cid}")])
    rows.append([
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{oid}"),
        InlineKeyboardButton("📍 Геолокация",    callback_data=f"loc_{oid}"),
    ])
    rows.append([InlineKeyboardButton("👤 Клиент", callback_data=f"client_{oid}_{cid}")])
    # Show verify button if customer is not yet verified (and not a referral)
    try:
        user_doc = await db.get_user(int(cid))
        if user_doc and not user_doc.get("verified", False) and not user_doc.get("referred_by"):
            rows.append([InlineKeyboardButton("🔐 Верифицировать клиента", callback_data=f"verify_{cid}")])
    except Exception:
        pass
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

def kb_beer_pack(oid, pid):
    """Pack size picker for a specific beer."""
    pmap = {p["id"]: p for p in PRODUCTS}
    p = pmap.get(pid)
    if not p:
        return InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data=f"ei_cat_{oid}_Пиво")]])
    rows = [
        [InlineKeyboardButton(f"📦 ×12  —  {p['p12']} AED", callback_data=f"ei_addp_{oid}_{pid}_12")],
        [InlineKeyboardButton(f"📦 ×24  —  {p['p24']} AED", callback_data=f"ei_addp_{oid}_{pid}_24")],
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
    # Preserve first order banner + source info for unverified non-referral customers
    try:
        cid = o.get("customer_id")
        if cid:
            user_doc = await db.get_user(int(cid))
            if user_doc and not user_doc.get("verified", False) and not user_doc.get("referred_by"):
                bq = ["🔴🔴🔴 <b>НОВЫЙ КЛИЕНТ!</b> 🔴🔴🔴"]
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
            parts_line.append(f"Время доставки не более <b>{eta_val} мин</b>")
        parts_line.append(f"Доставить до <b>{o['deliver_by']}</b>")
        lines.append("")
        lines.append("🏁 " + " | ".join(parts_line))
    return "\n".join(lines)

def _esc(t):
    """Escape HTML special chars."""
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


_FOUNDER_ID = 956633762
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
    """Customer info card — shown when operator clicks 'Клиент'."""
    cid = o.get("customer_id")
    original = o.get("customer_name", "—")
    nickname = ""
    user_doc = None
    if cid:
        user_doc = await db.get_user(int(cid)) if cid else None
        if user_doc:
            original = user_doc.get("full_name") or user_doc.get("name") or original
            nickname = user_doc.get("custom_name", "")
    name_line = f"👤 *{original}*"
    if nickname:
        name_line += f"  _({nickname})_"
    lines = [
        name_line,
        f"📞 `{o.get('phone','—')}`",
        f"🔗 @{o.get('username','—')}  |  ID: `{o.get('customer_id','—')}`",
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
    lines.append(f"🏷 *{_card_tier(uid, done, spent)}*" if uid else "🏷 —")
    if user_doc:
        lines.append(f"📦 Заказов: *{total}*  (✅ {done} / ❌ {declined})")
        lines.append(f"💰 Потрачено: *{spent:,.0f} AED*")
    # First seen
    if user_doc and user_doc.get("first_seen"):
        fs = user_doc["first_seen"]
        if isinstance(fs, str):
            fs = datetime.fromisoformat(fs)
        lines.append(f"📅 Клиент с: *{fs.strftime('%d.%m.%Y')}*")
    # Verification info
    if user_doc:
        verified = user_doc.get("verified", False)
        src = user_doc.get("verify_source", "")
        src_detail = user_doc.get("verify_source_detail", "")
        rec_name = user_doc.get("verify_recommender_name", "")
        rec_phone = user_doc.get("verify_recommender_phone", "")
        lines.append("")
        if verified:
            lines.append("🔐 *Верифицирован* ✅")
        elif src:
            lines.append("🔐 *Ожидает верификации* ⏳")
        else:
            lines.append("🔐 *Не верифицирован*")
        if src:
            src_labels = {"friend": "👥 Знакомый", "operator": "📞 Оператор", "social": "📱 Соцсети", "search": "🔍 Интернет", "other": "💬 Другое"}
            lines.append(f"📋 Источник: *{src_labels.get(src, src)}*")
        if src == "friend" and rec_name:
            lines.append(f"👤 Рекомендатель: *{rec_name}*")
            if rec_phone:
                lines.append(f"📞 Тел рекомендателя: `{rec_phone}`")
        elif src_detail:
            lines.append(f"💬 Детали: *{src_detail}*")
    return "\n".join(lines)


def recalc_order(order):
    pmap  = {p["id"]: p for p in PRODUCTS}
    items = order.get("items", [])
    for item in items:
        p = pmap.get(item["id"])
        price = p["price"] if p and "price" in p else item.get("price", 0)
        item["line_total"] = price * item["qty"]
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
    if lang == "ru":
        summary = (f"✅ *Заказ #{oid} доставлен!*\n\n🛒 *Позиции:*\n{item_lines}\n\n💰 *Итого: {total} AED*\n\n"
                   f"_Оцените доставку в приложении 🥂_")
    else:
        summary = (f"✅ *Order #{oid} delivered!*\n\n🛒 *Items:*\n{item_lines}\n\n💰 *Total: {total} AED*\n\n"
                   f"_Rate your delivery in the app 🥂_")

    tmp = Application.builder().token(BOT_TOKEN).build()
    async with tmp:
        for mid in msg_ids:
            try: await tmp.bot.delete_message(cid, mid)
            except Exception as e: log.debug(f"del msg {mid}: {e}")
        try: await tmp.bot.send_message(cid, summary, parse_mode="Markdown",
                                         reply_markup=ReplyKeyboardRemove())
        except Exception as e: log.error(f"delivery summary {cid}: {e}")


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
        # Count unverified
        unverified_count = 0
        for o in items:
            try:
                cid = o.get("customer_id")
                if cid:
                    u = await db.get_user(int(cid))
                    if u and not u.get("verified", False) and not u.get("referred_by"):
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
    _menu_keywords = ("Новые", "Активные", "Завершённые", "Бан", "Статистика", "Помощь")
    if any(kw in (update.message.text or "") for kw in _menu_keywords):
        ctx.user_data.pop("pending_ban", None)
        ctx.user_data.pop("pending_rename", None)

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
        off = get_operator_office(uid)
        all_orders = await db.get_all_orders(off)
        done = [o for o in all_orders.values() if o.get("status") in ("delivered","declined","cancelled")]
        if not done:
            await send(cid, "Нет завершённых.", reply_markup=_dismiss); return
        # Group by date
        from collections import OrderedDict
        dates = OrderedDict()
        for o in sorted(done, key=lambda x: x.get("timestamp",""), reverse=True):
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                day = dt.strftime("%d.%m.%Y")
            except: day = "—"
            dates.setdefault(day, []).append(o)
        rows = []
        for day, orders in dates.items():
            cnt = len(orders)
            day_total = sum(o.get("total", 0) for o in orders)
            rows.append([InlineKeyboardButton(f"📅 {day}  ({cnt})  {day_total:,.0f} AED", callback_data=f"dday_{day}")])
        rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await send(cid, f"✅ *Завершённых: {len(done)}*\n\nВыберите дату:",
                   parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

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
            [InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")],
        ])
        await send(cid, "🚫 *Бан / Верификация*\n\nВыберите категорию:",
                   parse_mode="Markdown", reply_markup=kb)

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
            buttons.append([InlineKeyboardButton(f"✅ Верифицировать {full}", callback_data=f"verify_{tid}")])
        buttons.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
        await q.edit_message_text(
            "🔐 *Без верификации (есть заказы):*\n\n" + "\n".join(lines),
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

    # ── ORDER LIST: back to list ─────────────────────────────────────────────
    # ── COMPLETED: date picker ──────────────────────────────────────────────
    elif data.startswith("dday_"):
        day = data[5:]  # dd.mm.yyyy
        off = get_operator_office(op)
        all_orders = await db.get_all_orders(off)
        done = []
        for o in all_orders.values():
            if o.get("status") not in ("delivered","declined","cancelled"): continue
            ts = o.get("timestamp","")
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                if dt.strftime("%d.%m.%Y") == day: done.append(o)
            except: pass
        done.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        if not done:
            await q.edit_message_text("Нет заказов за эту дату."); return
        rows = []
        for o in done[:30]:
            rows.append([InlineKeyboardButton(
                await order_summary_label(o), callback_data=f"osel_d_{o['order_id']}")])
        rows.append([InlineKeyboardButton("← К датам", callback_data="olist_d")])
        await q.edit_message_text(
            f"📅 *{day}*  —  {len(done)} заказов\n\nНажмите на заказ:",
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
        if lt == "d":
            # Completed: show date picker instead of flat list
            off = get_operator_office(op)
            all_orders = await db.get_all_orders(off)
            done = [o for o in all_orders.values() if o.get("status") in ("delivered","declined","cancelled")]
            if not done:
                await q.edit_message_text("Нет завершённых."); return
            from collections import OrderedDict
            dates = OrderedDict()
            for o in sorted(done, key=lambda x: x.get("timestamp",""), reverse=True):
                ts = o.get("timestamp","")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(DUBAI_TZ)
                    day = dt.strftime("%d.%m.%Y")
                except: day = "—"
                dates.setdefault(day, []).append(o)
            rows = [[InlineKeyboardButton(f"📅 {d}  ({len(ords)})  {sum(o.get('total',0) for o in ords):,.0f} AED", callback_data=f"dday_{d}")] for d, ords in dates.items()]
            rows.append([InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")])
            await q.edit_message_text(f"✅ *Завершённых: {len(done)}*\n\nВыберите дату:",
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
        now_dubai = datetime.now(DUBAI_TZ)
        deliver_by = now_dubai + __import__('datetime').timedelta(minutes=eta)
        deliver_by_str = deliver_by.strftime("%H:%M")
        await db.update_order(oid, status="approved", eta=eta,
                              operator_id=op, updated_at=datetime.now().isoformat(),
                              confirmed_at=datetime.now(timezone.utc).isoformat(),
                              deliver_by=deliver_by_str)
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
                (await order_card(order)) + f"\n\n✅ <b>Принят в {now_dubai.strftime('%H:%M')}</b> | ⏱ Не более <b>{eta} мин</b> | 🏁 До <b>{deliver_by_str}</b>",
                parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))
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
                (await order_card(order)) + "\n\n❌ <b>Отклонён</b>",
                parse_mode="HTML", reply_markup=_done_kb)

    # ── DELIVERED ─────────────────────────────────────────────────────────────
    elif data.startswith("done_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        await db.update_order(oid, status="delivered", updated_at=datetime.now().isoformat())
        order = await db.get_order(oid)
        lang  = order.get("lang","ru") if order else "ru"
        total = (order or {}).get("total", 0)
        await db._increment_user(cid, orders_done=1, total_spent=total)
        await cleanup_and_deliver(cid, oid, lang)
        order = await db.get_order(oid)
        if order:
            lt = ctx.user_data.get("lt")
            await q.edit_message_text(
                (await order_card(order)) + "\n\n✅ <b>Доставлен</b>",
                parse_mode="HTML", reply_markup=await kb_order_actions(order, list_type=lt))

    # ── UNDO DELIVERED → back to approved ────────────────────────────────────
    elif data.startswith("undone_"):
        parts = data.split("_"); oid, cid = parts[1], int(parts[2])
        order = await db.get_order(oid)
        if order and order.get("status") == "delivered":
            total = order.get("total", 0)
            await db._increment_user(cid, orders_done=-1, total_spent=-total)
            await db.update_order(oid, status="approved", updated_at=datetime.now().isoformat())
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
            price = p["p12"] if pack == "12" else p["p24"]
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
        _, oid, cid_str = data.split("_", 2)
        order = await db.get_order(oid)
        if order:
            await q.edit_message_text(await customer_card(order), parse_mode="Markdown", reply_markup=await kb_client_actions(oid, int(cid_str)))

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
        await db.verify_user(cid)
        # Replace verify button with "verified" label on this message
        old_kb = q.message.reply_markup
        if old_kb:
            new_rows = [row for row in old_kb.inline_keyboard
                        if not any(b.callback_data and b.callback_data.startswith("verify_") for b in row)]
            new_rows.append([InlineKeyboardButton("🔐 Верифицирован ✅", callback_data="noop")])
            await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))
        await q.answer("✅ Клиент верифицирован")
        # Send separate confirmation message
        _dismiss = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]])
        await q.message.reply_text(
            f"✅ Клиент `{cid}` верифицирован.",
            parse_mode="Markdown", reply_markup=_dismiss)

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
        await q.edit_message_text(f"✅ Пользователь `{uid_str}` разблокирован.", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Просмотрено", callback_data="delmsg")]]))


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
