"""Ручки книжки клиентов (clients.py): панели оператора — подсказки, карточка,
адреса; STAR — список, карточка с деньгами, личный ценник."""
import logging
import secrets
from datetime import datetime, timezone

from aiohttp import web

import db
import clients
from owner_auth import require_owner, CORS_HEADERS
from operator_routes import require_operator, _catalog_by_id, _op_name

log = logging.getLogger("clients")


def _j(data, status=200):
    return web.json_response(data, status=status, headers=CORS_HEADERS)


async def _body(request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:                            # noqa: BLE001
        return {}


# ── оператор ────────────────────────────────────────────────────────────────
@require_operator
async def op_search(request):
    """GET ?q=&limit= — подсказки по номеру, имени, адресу; пусто — недавние."""
    q = str(request.query.get("q") or "")[:60]
    try:
        limit = max(1, min(30, int(request.query.get("limit") or 8)))
    except ValueError:
        limit = 8
    rows = await clients.search(q, limit)
    return _j({"clients": rows, "q": q, "total": await db.clients_count()})


@require_operator
async def op_card(request):
    c = await clients.get(request.match_info["cid"])
    if not c:
        return _j({"error": "not_found"}, 404)
    s = clients.summary(c)
    s["orders"] = await clients.orders_of(c["_id"], 10)
    s["stats"] = await clients.stats(c["_id"])
    return _j(s)


@require_operator
async def op_save(request):
    """POST {id?, name, phone, note} — новый клиент или правка имени, номера,
    заметки. Номер, занятый другим, — 409 с тем, кто его держит."""
    b = await _body(request)
    name = " ".join(str(b.get("name") or "").split())[:60]
    note = str(b.get("note") or "").strip()[:200]
    key, pretty = clients.norm_phone(b.get("phone"))
    cid = str(b.get("id") or "")
    if not key and not cid:
        return _j({"error": "no_phone"}, 400)
    who = _op_name(request["op_user"])
    now = datetime.now(timezone.utc)
    if key:
        other = await db.client_by_phone(key)
        if other and other["_id"] != cid:
            return _j({"error": "phone_taken", "client": clients._short(other)}, 409)
    if cid:
        c = await clients.get(cid)
        if not c:
            return _j({"error": "not_found"}, 404)
        upd = {"updated_at": now}
        if "name" in b: upd["name"] = name
        if "note" in b: upd["note"] = note
        if key:
            phones = [p for p in (c.get("phones") or []) if p.get("key") != key]
            upd["phones"] = [{"key": key, "pretty": pretty}] + phones
        await db.client_set(cid, upd)
        c.update(upd)
    else:
        c = {"_id": secrets.token_hex(6), "name": name, "phones": [{"key": key, "pretty": pretty}],
             "addresses": [], "note": note, "orders_n": 0, "total_sum": 0.0, "last_order_at": None,
             "created_at": now, "by": who, "updated_at": now}
        await db.client_put(c)
        log.info(f"[clients] заведён рукой: {pretty} {name} · {who}")
    return _j({"ok": True, "client": clients.summary(c)})


@require_operator
async def op_address(request):
    """POST {id?, text, label, note, delete} — адрес клиента: добавить,
    подписать, убрать."""
    c = await clients.get(request.match_info["cid"])
    if not c:
        return _j({"error": "not_found"}, 404)
    b = await _body(request)
    aid = str(b.get("id") or "")
    addrs = list(c.get("addresses") or [])
    if b.get("delete"):
        addrs = [a for a in addrs if a.get("id") != aid]
    else:
        text = " ".join(str(b.get("text") or "").split())[:200]
        label = str(b.get("label") or "").strip()[:20]
        note = str(b.get("note") or "").strip()[:200]
        hit = next((a for a in addrs if a.get("id") == aid), None) if aid else None
        if hit is None:
            if not text:
                return _j({"error": "no_text"}, 400)
            dup = next((a for a in addrs if clients.addr_key(a.get("text")) == clients.addr_key(text)), None)
            if dup:
                hit = dup
            else:
                hit = clients._addr_entry(text, note); hit["used"] = 0; hit["last"] = None
                addrs.append(hit)
        if text: hit["text"] = text
        if "label" in b: hit["label"] = label
        if "note" in b: hit["note"] = note
    await db.client_set(c["_id"], {"addresses": addrs, "updated_at": datetime.now(timezone.utc)})
    c["addresses"] = addrs
    return _j({"ok": True, "client": clients.summary(c)})


# ── STAR ────────────────────────────────────────────────────────────────────
@require_owner
async def own_list(request):
    q = str(request.query.get("q") or "")[:60]
    rows = await clients.search(q, 200 if not q else 50)
    return _j({"clients": rows, "total": await db.clients_count()})


async def _editor(c: dict) -> dict:
    """Что нужно экрану ценника: обычные цены по ключам, названия, категории."""
    cat = _catalog_by_id()
    reg = clients.regular_prices(cat)
    rows = []
    for k, v in reg.items():
        pid, _, pcs = k.partition("_")
        p = cat.get(pid) or {}
        rows.append({"key": k, "id": pid, "name": (p.get("name") or pid) + (f" ×{pcs}" if pcs else ""),
                     "cat": p.get("cat") or "", "regular": v})
    cats = []
    for r in rows:
        if r["cat"] and r["cat"] not in cats:
            cats.append(r["cat"])
    return {"rows": rows, "cats": cats}


@require_owner
async def own_card(request):
    c = await clients.get(request.match_info["cid"])
    if not c:
        return _j({"error": "not_found"}, 404)
    s = clients.summary(c)
    w = c.get("wholesale") or {}
    s["wholesale_doc"] = {"active": bool(w.get("active")), "prices": dict(w.get("prices") or {}),
                          "rule": w.get("rule") or None, "by": w.get("by") or "", "at": str(w.get("at") or "")}
    s["aka"] = list(c.get("aka") or [])
    s["orders"] = await clients.orders_of(c["_id"], 30)
    costs = None
    try:
        import stock_value
        costs = await stock_value.cost_map()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[clients] закупка для маржи: {e}")
    s["stats"] = await clients.stats(c["_id"], costs)
    s["editor"] = await _editor(c)
    return _j(s)


@require_owner
async def own_save(request):
    b = await _body(request)
    c = await clients.get(request.match_info["cid"])
    if not c:
        return _j({"error": "not_found"}, 404)
    upd = {"updated_at": datetime.now(timezone.utc)}
    if "name" in b: upd["name"] = " ".join(str(b.get("name") or "").split())[:60]
    if "note" in b: upd["note"] = str(b.get("note") or "").strip()[:200]
    if "phone" in b:
        key, pretty = clients.norm_phone(b.get("phone"))
        if key:
            other = await db.client_by_phone(key)
            if other and other["_id"] != c["_id"]:
                return _j({"error": "phone_taken", "client": clients._short(other)}, 409)
            upd["phones"] = [{"key": key, "pretty": pretty}] + [p for p in (c.get("phones") or []) if p.get("key") != key]
    await db.client_set(c["_id"], upd)
    c.update(upd)
    return _j({"ok": True, "client": clients.summary(c)})


@require_owner
async def own_prices(request):
    """POST {prices: {key: price}, rule: {pct, cats, round}|null, active, as} —
    личный ценник. Правило считает цены, явные цены главнее правила; цена
    выше обычной не принимается."""
    b = await _body(request)
    c = await clients.get(request.match_info["cid"])
    if not c:
        return _j({"error": "not_found"}, 404)
    cat = _catalog_by_id()
    rule = b.get("rule") if isinstance(b.get("rule"), dict) else None
    prices = {}
    if rule:
        prices.update(clients.rule_prices(rule, cat))
    explicit, bad = clients.validate_prices(b.get("prices") or {}, cat)
    if bad:
        return _j({"error": "bad_prices", "bad": bad[:20]}, 400)
    prices.update(explicit)
    who = str(b.get("as") or "").strip()[:60]
    active = bool(b.get("active", True)) and bool(prices)
    w = {"active": active, "prices": prices, "rule": rule, "by": who, "at": datetime.now(timezone.utc)}
    await db.client_set(c["_id"], {"wholesale": w, "updated_at": w["at"]})
    c["wholesale"] = w
    log.info(f"[clients] ценник {c.get('name') or c['_id']}: позиций {len(prices)}, "
             f"{'включён' if active else 'выключен'} · {who or '—'}")
    return _j({"ok": True, "client": clients.summary(c), "prices_n": len(prices)})


@require_owner
async def own_delete(request):
    ok = await db.client_del(request.match_info["cid"])
    return _j({"ok": ok}, 200 if ok else 404)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    for path, method, h in (
        ("/api/operator/clients", "GET", op_search),
        ("/api/operator/clients", "POST", op_save),
        ("/api/operator/clients/{cid}", "GET", op_card),
        ("/api/operator/clients/{cid}/address", "POST", op_address),
        ("/api/owner/clients", "GET", own_list),
        ("/api/owner/clients/{cid}", "GET", own_card),
        ("/api/owner/clients/{cid}", "POST", own_save),
        ("/api/owner/clients/{cid}", "DELETE", own_delete),
        ("/api/owner/clients/{cid}/prices", "POST", own_prices),
    ):
        {"GET": r.add_get, "POST": r.add_post, "DELETE": r.add_delete}[method](path, h)
    for path in ("/api/operator/clients", "/api/operator/clients/{cid}", "/api/operator/clients/{cid}/address",
                 "/api/owner/clients", "/api/owner/clients/{cid}", "/api/owner/clients/{cid}/prices"):
        r.add_route("OPTIONS", path, _opt)
    log.info("[clients] routes mounted")
