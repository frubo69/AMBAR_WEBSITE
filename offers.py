"""Персональное предложение (оффер): состав, подарок и скидка под одну ссылку.

Владелец, 31 окт 2026: «чтобы по кнопке в оффере корзина собиралась сама и
скидка применялась сама; вино в подарок уже идёт по акции — два бесплатных
не отдавать; заказ по одному офферу — только один; документ чей угодно; срок
три дня; операторы должны видеть, что заказ по спецпредложению».

Как устроено
------------
Документ оффера лежит в db.offers: код, состав корзины (id, qty, pcs),
подарок (id, qty), сумма скидки, срок. Ссылка в PDF —
t.me/<бот>?startapp=offer_<код>; приложение по коду забирает оффер
(GET /api/offer/<код>), собирает корзину и шлёт код вместе с заказом.

Скидку и подарок кладёт СЕРВЕР, как и подарок за тысячу: присланные клиентом
строки с флагами gift/offer или с минусом отбрасываются. Скидка — строка
состава с отрицательной ценой (`offer: True`, `is_custom: True`): итог везде
считается из строк, поэтому она видна оператору, водителю и в деньгах без
отдельных веток. Подарок — обычная подарочная строка (`gift: True`), и
config_gift.apply второй бутылки не добавит: подарок в заказе уже есть.

Один заказ на оффер: при приёме заказа код занимается атомарно (db.offer_claim).
Отказ или отмена заказа возвращают код в оборот (пока не истёк срок),
доставка закрывает его навсегда. Оба перехода — в db.update_order.
"""
from __future__ import annotations
import re, secrets
from datetime import datetime, timezone, timedelta

DAYS = 3                                   # срок действия с момента создания
LINE_ID = "offer"                          # id строки скидки в составе заказа
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без 0/O, 1/I
CODE_RE = re.compile(r"^[A-Z0-9]{6,16}$")
PARAM_RE = re.compile(r"^offer_([A-Za-z0-9]{6,16})$")

NAME = {"ru": "Персональное предложение", "en": "Personal offer"}


def new_code(n: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def norm_code(raw) -> str:
    """Код из запроса: `offer_XXXX`, `XXXX`, любой регистр → `XXXX` или ''."""
    s = str(raw or "").strip()
    m = PARAM_RE.match(s)
    if m:
        s = m.group(1)
    s = s.upper()
    return s if CODE_RE.match(s) else ""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def new_doc(items: list, discount, gift: dict | None = None, *, title: str = "",
            total=None, note: str = "", now: datetime | None = None, days: int = DAYS,
            code: str = "") -> dict:
    """Документ оффера для db.offers. items — [{id, qty, pcs?}], gift — {id, qty}."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for it in items or []:
        row = {"id": str(it.get("id") or ""), "qty": int(it.get("qty") or 0)}
        if not row["id"] or row["qty"] <= 0:
            raise ValueError(f"bad offer item: {it!r}")
        if it.get("pcs"):
            row["pcs"] = int(it["pcs"])
        rows.append(row)
    if not rows:
        raise ValueError("offer without items")
    d = int(discount or 0)
    if d < 0:
        raise ValueError("negative discount")
    doc = {"code": code or new_code(), "status": "new", "items": rows, "discount": d,
           "title": title or NAME["en"], "note": note,
           "created_at": _iso(now), "expires_at": _iso(now + timedelta(days=days)),
           "order_id": None, "used_by": None, "used_at": None}
    if gift and gift.get("id"):
        doc["gift"] = {"id": str(gift["id"]), "qty": int(gift.get("qty") or 1)}
    if total is not None:
        doc["total"] = int(total)
    return doc


def state(doc: dict | None, now: datetime | None = None) -> str:
    """'active' — можно заказывать; 'used' — заказ по нему уже есть;
    'expired' — срок вышел; 'unknown' — такого кода нет."""
    if not doc:
        return "unknown"
    st = doc.get("status") or "new"
    if st in ("ordered", "delivered"):
        return "used"
    exp = _parse(doc.get("expires_at"))
    now = now or datetime.now(timezone.utc)
    if exp and now >= exp:
        return "expired"
    return "active"


def public_view(doc: dict | None, now: datetime | None = None) -> dict:
    """Что отдаётся приложению. Чужих полей (кто заказал, номер заказа) тут нет."""
    st = state(doc, now)
    out = {"ok": True, "state": st}
    if st != "active":
        return out
    out.update({"code": doc["code"], "items": [dict(i) for i in doc.get("items") or []],
                "discount": int(doc.get("discount") or 0), "expires_at": doc.get("expires_at"),
                "title": doc.get("title") or NAME["en"]})
    if doc.get("gift"):
        out["gift"] = dict(doc["gift"])
    return out


def _pcs(it) -> int:
    try:
        return int(it.get("pcs") or 0)
    except (TypeError, ValueError):
        return 0


def covered(doc: dict, items: list) -> bool:
    """В корзине есть всё из оффера в нужном количестве (лишнее — не помеха).
    Пачка пива считается той же позицией только при той же фасовке."""
    for need in doc.get("items") or []:
        have = 0
        for it in items or []:
            if it.get("gift") or it.get("offer"):
                continue
            if str(it.get("id")) != need["id"] or _pcs(it) != int(need.get("pcs") or 0):
                continue
            try:
                have += int(it.get("qty") or 0)
            except (TypeError, ValueError):
                pass
        if have < int(need.get("qty") or 0):
            return False
    return True


def clean_client_items(items: list) -> list:
    """Строки, которые клиенту класть нельзя: подарок, скидка, минус в цене.
    Их кладёт сервер — иначе любой POST получил бы бесплатную бутылку."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if it.get("gift") or it.get("offer") or str(it.get("id")) == LINE_ID:
            continue
        try:
            if float(it.get("price") or 0) < 0 or float(it.get("line_total") or 0) < 0:
                continue
        except (TypeError, ValueError):
            continue
        out.append(it)
    return out


def gift_line(doc: dict, catalog_by_id: dict) -> dict | None:
    g = doc.get("gift") or {}
    if not g.get("id"):
        return None
    p = (catalog_by_id or {}).get(g["id"]) or {}
    return {"id": g["id"], "name": p.get("name") or g["id"], "qty": int(g.get("qty") or 1),
            "price": 0, "line_total": 0, "gift": True, "offer_gift": True}


def discount_line(doc: dict, lang: str = "ru") -> dict | None:
    d = int(doc.get("discount") or 0)
    if d <= 0:
        return None
    name = NAME["en"] if str(lang).lower().startswith("en") else NAME["ru"]
    return {"id": LINE_ID, "name": name, "qty": 1, "price": -d, "line_total": -d,
            "offer": True, "is_custom": True, "code": doc.get("code")}


def apply(items: list, doc: dict, catalog_by_id: dict, lang: str = "ru") -> list:
    """Состав заказа по офферу: строки клиента + подарок + скидка."""
    out = [dict(i) for i in clean_client_items(items)]
    g = gift_line(doc, catalog_by_id)
    if g:
        out.append(g)
    d = discount_line(doc, lang)
    if d:
        out.append(d)
    return out


def keep_lines(old_items: list, new_items: list) -> list:
    """Строка скидки переживает правку состава (панель и водитель присылают
    состав без неё — или с её эхом). Снимать скидку может только сервер."""
    kept = [dict(i) for i in (old_items or []) if i.get("offer")]
    out = [dict(i) for i in (new_items or []) if not i.get("offer") and str(i.get("id")) != LINE_ID]
    return out + kept


def order_fields(doc: dict) -> dict:
    """Что из оффера остаётся на заказе — для карточек, лент и поиска."""
    f = {"code": doc.get("code"), "discount": int(doc.get("discount") or 0),
         "title": doc.get("title") or NAME["en"]}
    if doc.get("gift"):
        f["gift"] = dict(doc["gift"])
    return f


def follow_status(order_status: str) -> str | None:
    """Статус оффера по статусу заказа: отказ/отмена возвращают код в оборот,
    доставка закрывает, остальное — занят."""
    st = str(order_status or "")
    if st in ("cancelled", "declined", "rejected"):
        return "new"
    if st == "delivered":
        return "delivered"
    return "ordered"
