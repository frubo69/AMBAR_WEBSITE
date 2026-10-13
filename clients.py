"""Книжка клиентов (владелец, 13 окт 2026).

«Один раз чей-то номер и имя забиваешь — он запоминается. В следующий раз либо
из контактов нашёл, нажал — и всё автозаполнилось, либо начал вводить номер —
внизу, как в поиске, предлагаются номера: чем больше цифр, тем меньше
вариантов. Нажал на номер — вся информация сама подгружается, вместе с
адресом и всем остальным».

Что лежит (коллекция `clients`):
  _id          короткий ключ
  name         как записали операторы (у клиента из приложения — его имя)
  phones       [{key, pretty}] — key: только цифры с кодом страны («971501234567»),
               pretty: «+971 50 123 4567»; первый — основной
  addresses    [{id, text, label, note, used, last}] — куда возили; label —
               «Дом», «Офис»…, note — заметка водителю («вход со двора»)
  note         заметка операторов о клиенте
  tg_id        клиент приложения, если это он (по телефону)
  wholesale    {active, prices: {pid | pid_12 | pid_24: цена}, rule: {pct, cats,
               round}, by, at} — личный ценник оптовика; правит только STAR
  orders_n, total_sum, last_order_at — счётчики, пересчитываются по заказам
  created_at, by, updated_at

Правила:
  • телефон один — клиент один: все написания номера сводятся к `key`;
  • книжка наполняется сама: каждый заказ с телефоном (оператора или из
    приложения) заводит или дополняет клиента и его адреса (`remember`);
  • при первом запуске книжка собирается из прошлых заказов (`seed`);
  • личный ценник применяется на сервере при создании и правке заказа
    (`apply_prices`); в заказ ложится снимок цен — правка ценника потом старые
    заказы не трогает;
  • цена из ценника не может быть выше обычной (`validate_prices`).
"""
import logging
import re
import secrets
from datetime import datetime, timezone

import db

log = logging.getLogger("clients")

SEED_KEY = "clients_seeded"
BEER = "Пиво"


# ── телефон и адрес ─────────────────────────────────────────────────────────
def norm_phone(raw) -> tuple:
    """(key, pretty). Пусто — ('', '')."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits or set(digits) == {"0"}:
        return "", ""
    if digits.startswith("00"):
        digits = digits[2:]
    # местный формат ОАЭ: 05x xxx xxxx → 9715x…; без нуля: 5x xxx xxxx
    if len(digits) == 10 and digits.startswith("0"):
        digits = "971" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "971" + digits
    if len(digits) < 7:
        return "", ""
    if digits.startswith("971") and len(digits) == 12:
        pretty = f"+971 {digits[3:5]} {digits[5:8]} {digits[8:]}"
    elif digits.startswith("7") and len(digits) == 11:
        pretty = f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:]}"
    else:
        pretty = "+" + digits
    return digits, pretty


def addr_key(text) -> str:
    t = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    return " ".join(t.split())


def _now():
    return datetime.now(timezone.utc)


def _short(c: dict) -> dict:
    """Строка подсказки и списка: без лишнего."""
    phones = c.get("phones") or []
    addrs = sorted(c.get("addresses") or [], key=lambda a: str(a.get("last") or ""), reverse=True)
    w = c.get("wholesale") or {}
    return {"id": c["_id"], "name": c.get("name") or "", "phone": phones[0]["pretty"] if phones else "",
            "phones": [p["pretty"] for p in phones],
            "address": addrs[0]["text"] if addrs else "", "addresses_n": len(addrs),
            "wholesale": bool(w.get("active") and w.get("prices")),
            "tg": bool(c.get("tg_id")), "orders_n": int(c.get("orders_n") or 0),
            "total_sum": int(round(float(c.get("total_sum") or 0))),
            "last_order_at": str(c.get("last_order_at") or "")}


def summary(c: dict) -> dict:
    """Карточка для панели: всё, что нужно, чтобы заполнить заказ."""
    s = _short(c)
    w = c.get("wholesale") or {}
    addrs = sorted(c.get("addresses") or [], key=lambda a: str(a.get("last") or ""), reverse=True)
    s.update({"addresses": [{"id": a.get("id"), "text": a.get("text") or "", "label": a.get("label") or "",
                             "note": a.get("note") or "", "last": str(a.get("last") or "")} for a in addrs],
              "note": c.get("note") or "",
              "prices": dict(w.get("prices") or {}) if s["wholesale"] else {},
              "total_sum": int(round(float(c.get("total_sum") or 0))),
              "created_at": str(c.get("created_at") or "")})
    return s


# ── чтение ──────────────────────────────────────────────────────────────────
async def get(cid: str) -> dict | None:
    return await db.client_get(cid)


async def by_phone(raw) -> dict | None:
    key, _ = norm_phone(raw)
    return await db.client_by_phone(key) if key else None


async def resolve(cid, phone) -> dict | None:
    """Клиент заказа: по ключу из книжки, а нет его — по номеру."""
    c = await get(str(cid)) if cid else None
    return c or (await by_phone(phone) if phone else None)


async def search(q: str, limit: int = 8) -> list:
    """Подсказки: цифры — по любой части номера, иначе по имени и адресу.
    Пусто — недавние."""
    q = str(q or "").strip()
    digits = re.sub(r"\D", "", q)
    if digits and len(digits) >= 2 and len(digits) >= len(q) - 4:
        # «0501234» и «+971 50 1234» — один и тот же хвост
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("971"):
            tail = digits[3:]
        elif digits.startswith("0"):
            tail = digits[1:]
        else:
            tail = digits
        rows = await db.clients_find({"phones.key": {"$regex": re.escape(tail or digits)}}, limit)
    elif q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        rows = await db.clients_find({"$or": [{"name": rx}, {"addresses.text": rx}]}, limit)
    else:
        rows = await db.clients_find({}, limit)
    return [_short(c) for c in rows]


# ── запоминание ─────────────────────────────────────────────────────────────
def _addr_entry(text: str, note: str = "") -> dict:
    return {"id": secrets.token_hex(4), "text": " ".join(str(text).split())[:200], "label": "",
            "note": str(note or "")[:200], "used": 1, "last": _now()}


async def remember(order: dict, by: str = "", count: bool = True) -> dict | None:
    """Заказ с телефоном → клиент в книжке (новый или знакомый), адрес в его
    адресах, ключ клиента в самом заказе. count — считать заказ (при
    создании), False — только дополнить (при правке)."""
    if order.get("test"):
        return None
    key, pretty = norm_phone(order.get("phone"))
    name = str(order.get("customer_name") or "").strip()
    if name in ("—", "-"):
        name = ""
    addr = " ".join(str(order.get("address") or "").split())
    now = _now()
    c = await db.client_by_phone(key) if key else None
    if c is None and order.get("client_id"):
        c = await db.client_get(str(order["client_id"]))     # выбран из книжки, телефон не вводили
    if c is None and not key:
        return None
    if c is None:
        c = {"_id": secrets.token_hex(6), "name": name, "phones": [{"key": key, "pretty": pretty}],
             "addresses": [], "note": "",
             "orders_n": 0, "total_sum": 0.0, "last_order_at": None,
             "created_at": now, "by": by or str(order.get("created_by_name") or ""), "updated_at": now}
        if order.get("customer_id"):
            c["tg_id"] = int(order["customer_id"])
        await db.client_put(c)
        log.info(f"[clients] новый клиент {pretty}{' · ' + name if name else ''}")
    upd: dict = {"updated_at": now}
    if name and not c.get("name"):
        upd["name"] = name
    elif name and c.get("name") and name.lower() != str(c["name"]).lower():
        aka = [a for a in (c.get("aka") or []) if a.lower() != name.lower()]
        upd["aka"] = (aka + [name])[-5:]
    if order.get("customer_id") and not c.get("tg_id"):
        upd["tg_id"] = int(order["customer_id"])
    addrs = list(c.get("addresses") or [])
    hit = next((a for a in addrs if addr_key(a.get("text")) == addr_key(addr)), None) if addr else None
    if addr and hit is None:
        addrs.append(_addr_entry(addr))
        upd["addresses"] = addrs
        aid = addrs[-1]["id"]
    elif hit:
        hit["used"] = int(hit.get("used") or 0) + (1 if count else 0)
        hit["last"] = now
        upd["addresses"] = addrs
        aid = hit["id"]
    else:
        aid = ""
    if count:
        upd["orders_n"] = int(c.get("orders_n") or 0) + 1
        upd["total_sum"] = float(c.get("total_sum") or 0) + float(order.get("total") or 0)
        upd["last_order_at"] = now
    await db.client_set(c["_id"], upd)
    c.update(upd)
    try:
        if order.get("order_id") and (order.get("client_id") != c["_id"] or (aid and order.get("address_id") != aid)):
            await db.update_order(order["order_id"], client_id=c["_id"], **({"address_id": aid} if aid else {}))
            order["client_id"] = c["_id"]
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[clients] ключ клиента в заказ не записан: {e}")
    return c


async def seed() -> dict:
    """Один раз: книжка из прошлых заказов — телефон, имя, адреса, счётчики.
    Заказы получают ключ клиента."""
    if await db.setting_get(SEED_KEY):
        return {"done": True, "skipped": True}
    rows = await db.orders_with_phone()
    by_key: dict = {}
    for o in rows:
        key, pretty = norm_phone(o.get("phone"))
        if not key:
            continue
        c = by_key.get(key)
        if c is None:
            c = by_key[key] = {"key": key, "pretty": pretty, "name": "", "aka": [], "addresses": {},
                               "orders": [], "n": 0, "sum": 0.0, "last": None, "first": None, "tg": 0}
        name = str(o.get("customer_name") or "").strip()
        if name and name not in ("—", "-"):
            if c["name"] and name.lower() != c["name"].lower() and name not in c["aka"]:
                c["aka"].append(c["name"])
            c["name"] = name
        if o.get("customer_id") and not c["tg"]:
            c["tg"] = int(o["customer_id"])
        ts = str(o.get("timestamp") or "")
        addr = " ".join(str(o.get("address") or "").split())
        if addr:
            k = addr_key(addr)
            a = c["addresses"].get(k)
            if a is None:
                c["addresses"][k] = {"id": secrets.token_hex(4), "text": addr[:200], "label": "", "note": "",
                                     "used": 1, "last": ts}
            else:
                a["used"] += 1; a["last"] = max(a["last"], ts); a["text"] = addr[:200]
        c["orders"].append(o.get("order_id"))
        if o.get("status") not in ("cancelled", "declined"):
            c["n"] += 1; c["sum"] += float(o.get("total") or 0)
        c["last"] = max(c["last"] or ts, ts); c["first"] = min(c["first"] or ts, ts)
    made = 0
    for key, c in by_key.items():
        if await db.client_by_phone(key):
            continue
        doc = {"_id": secrets.token_hex(6), "name": c["name"], "aka": c["aka"][-5:],
               "phones": [{"key": key, "pretty": c["pretty"]}],
               "addresses": list(c["addresses"].values()), "note": "",
               "orders_n": c["n"], "total_sum": round(c["sum"], 2), "last_order_at": c["last"],
               "created_at": _now(), "by": "seed", "updated_at": _now(), "seeded": True}
        if c["tg"]:
            doc["tg_id"] = c["tg"]
        await db.client_put(doc)
        await db.orders_set_client([x for x in c["orders"] if x], doc["_id"])
        made += 1
    await db.setting_set(SEED_KEY, {"at": _now(), "clients": made, "orders": len(rows)})
    log.info(f"[clients] книжка собрана из заказов: клиентов {made}, заказов {len(rows)}")
    return {"done": True, "clients": made, "orders": len(rows)}


# ── личный ценник ───────────────────────────────────────────────────────────
def price_key(pid: str, pcs=None) -> str:
    try:
        pcs = int(pcs or 0)
    except (TypeError, ValueError):
        pcs = 0
    return f"{pid}_{pcs}" if pcs in (12, 24) else str(pid)


def wholesale_active(c: dict | None) -> bool:
    w = (c or {}).get("wholesale") or {}
    return bool(w.get("active") and w.get("prices"))


def apply_prices(items: list, prices: dict) -> tuple:
    """Подставить личные цены в строки заказа (строки — после _build_items:
    в price лежит обычная цена). Возвращает (строки, снимок использованных цен)."""
    used: dict = {}
    if not prices:
        return items, used
    for it in items:
        if it.get("gift"):
            continue
        k = price_key(it.get("id"), it.get("pcs"))
        own = prices.get(k)
        if own is None:
            continue
        try:
            own = float(own)
        except (TypeError, ValueError):
            continue
        reg = float(it.get("reg") if it.get("own") else it.get("price") or 0)
        if own <= 0 or own >= reg:
            continue
        own_v = int(own) if own.is_integer() else round(own, 2)
        it["reg"] = int(reg) if float(reg).is_integer() else round(reg, 2)
        it["price"] = own_v
        it["line_total"] = round(own_v * int(it.get("qty") or 0), 2)
        if float(it["line_total"]).is_integer():
            it["line_total"] = int(it["line_total"])
        it["own"] = True
        used[k] = own_v
    return items, used


def strip_prices(items: list) -> list:
    """Снять личные цены: вернуть обычные."""
    for it in items:
        if it.get("own"):
            it["price"] = it.pop("reg", it["price"])
            it["line_total"] = it["price"] * int(it.get("qty") or 0)
            it.pop("own", None)
    return items


def regular_prices(catalog: dict) -> dict:
    """Обычные цены телефонного заказа по ключам ценника: бутылка, и у пива —
    пачки 12 и 24 (operator_routes._full_price)."""
    from operator_routes import _full_price
    out = {}
    for pid, p in catalog.items():
        out[pid] = _full_price(p)
        if p.get("cat") == BEER:
            for pcs in (12, 24):
                v = _full_price(p, pcs)
                if v:
                    out[f"{pid}_{pcs}"] = v
    return out


def round5(v: float) -> int:
    """До ближайших 5, половина — вверх (382,5 → 385), а не по-банковски."""
    import math
    return int(math.floor(v / 5.0 + 0.5) * 5)


def rule_prices(rule: dict, catalog: dict) -> dict:
    """Ценник по правилу: минус pct% на категории cats (пусто — на всё),
    округление до 5 (round=5) или до дирхама."""
    try:
        pct = float(rule.get("pct") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    if pct <= 0 or pct >= 100:
        return {}
    cats = set(rule.get("cats") or [])
    rnd = int(rule.get("round") or 0)
    reg = regular_prices(catalog)
    out = {}
    for k, v in reg.items():
        pid = k.split("_")[0]
        p = catalog.get(pid) or {}
        if cats and (p.get("cat") or "") not in cats:
            continue
        x = v * (1 - pct / 100.0)
        x = round5(x) if rnd == 5 else int(round(x))
        if 0 < x < v:
            out[k] = x
    return out


def validate_prices(prices: dict, catalog: dict) -> tuple:
    """(чистый ценник, ошибки[]) — цена не выше обычной, не ниже нуля, позиция
    из каталога."""
    reg = regular_prices(catalog)
    clean, bad = {}, []
    for k, v in (prices or {}).items():
        k = str(k)
        if k not in reg:
            bad.append({"key": k, "error": "unknown"}); continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            bad.append({"key": k, "error": "bad_price"}); continue
        if v <= 0:
            continue                      # пусто/ноль — обычная цена
        if v > reg[k]:
            bad.append({"key": k, "error": "above_regular", "regular": reg[k]}); continue
        clean[k] = int(v) if v.is_integer() else round(v, 2)
    return clean, bad


# ── статистика по клиенту ───────────────────────────────────────────────────
async def orders_of(cid: str, limit: int = 30) -> list:
    rows = await db.orders_by_client(cid, limit)
    out = []
    for o in rows:
        items = o.get("items") or []
        out.append({"order_id": o.get("order_id"), "day": o.get("day") or "", "status": o.get("status") or "",
                    "total": int(round(float(o.get("total") or 0))), "district": o.get("district") or o.get("office_name") or "",
                    "office_id": o.get("office_id") or "", "driver": o.get("driver") or "",
                    "wholesale": bool(o.get("own_prices")), "n": sum(int(i.get("qty") or 0) for i in items),
                    "items": [{"id": i.get("id") or "", "pcs": i.get("pcs"), "name": i.get("name") or "",
                               "qty": i.get("qty") or 0, "price": i.get("price"), "reg": i.get("reg")} for i in items][:12],
                    "payment_method": o.get("payment_method") or "", "timestamp": str(o.get("timestamp") or "")})
    return out


async def stats(cid: str, costs: dict | None = None) -> dict:
    """Заказов, сумма, скидка по ценнику, маржа (если дали закупку) — по
    доставленным и идущим заказам клиента."""
    rows = await db.orders_by_client(cid, 5000)
    n = 0; total = 0.0; disc = 0.0; margin = 0.0; margin_known = 0
    for o in rows:
        if o.get("status") in ("cancelled", "declined"):
            continue
        n += 1; total += float(o.get("total") or 0)
        for i in o.get("items") or []:
            q = int(i.get("qty") or 0)
            if i.get("own") and i.get("reg") is not None:
                disc += (float(i["reg"]) - float(i.get("price") or 0)) * q
            if costs is not None:
                cst = costs.get(i.get("id"))
                if cst:
                    per = float(cst) * (int(i.get("pcs") or 0) / 24.0 if i.get("pcs") else 1.0)
                    margin += (float(i.get("price") or 0) - per) * q
                    margin_known += 1
    return {"orders_n": n, "total_sum": int(round(total)), "discount": int(round(disc)),
            "margin": int(round(margin)) if costs is not None else None, "margin_known": margin_known}
