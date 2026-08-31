"""
AMBAR — кошелёк USDT: сколько лежит и что по нему ходило.

Кошелёк у нас уже был, но виден он был только изнутри одного вопроса: watcher
спрашивал «пришёл ли платёж по счёту №такому-то», и всё, что не относилось к
заказу, для приложения не существовало. Перевод мимо заказа, уход с кошелька,
сам остаток — про это нельзя было узнать, не открыв блокчейн-обозреватель.

Здесь тот же ключ TronGrid и те же права: только чтение. Приватных ключей у
сервера нет и не будет — отправить отсюда ничего нельзя, и кнопки такой не
появится.

Поступления бывают двух видов, и это не про качество, а про путь. Заказ из
клиентского бота выставляет счёт — такой приход приложение узнаёт по txid.
А личный заказ оператор ведёт руками: скидывает номер кошелька, деньги падают
напрямую, счёта нет и связать их не с чем. Оба вида законны, и на экране они
так и называются: через приложение и не через приложение.
"""
import logging
import time as _t
from datetime import datetime, timezone

from aiohttp import web

import db
import tron
from config import TRON_RECEIVE_ADDRESS
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("wallet")

# Блокчейн не отвечает мгновенно, а на экран смотрят и листают его туда-сюда.
# Полминуты — это дешевле для ключа и незаметно глазу: подтверждение перевода
# всё равно занимает минуты.
TTL = 30
_CACHE: dict = {"at": 0.0, "data": None}


def _short(a: str) -> str:
    a = str(a or "")
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


async def _build() -> dict:
    balance = await tron.get_balance(TRON_RECEIVE_ADDRESS)
    transfers = await tron.get_transfers(TRON_RECEIVE_ADDRESS)
    # None и пустой список — разные ответы: первое значит «не дозвонились», и
    # говорить в этом случае «переводов нет» — врать.
    offline = transfers is None
    transfers = transfers or []

    # Через приложение или напрямую: счёт знает txid своего перевода. А то, что
    # пришло напрямую, к заказу привязывает человек — и эта связь лежит рядом.
    ids = [t["txid"] for t in transfers if t.get("txid")]
    byid, links = {}, {}
    try:
        byid = await db.crypto_invoices_by_txids(ids)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] счета к переводам не подшились: {e}")
    try:
        links = await db.wallet_links(ids)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] привязки не подшились: {e}")

    через = 0.0
    напрямую = 0.0
    привязано = 0.0
    ушло = 0.0
    rows = []
    for t in transfers:
        tx = t.get("txid") or ""
        inv = byid.get(tx)
        link = links.get(tx)
        if t.get("in"):
            if inv:
                через += t["amount"]
            else:
                напрямую += t["amount"]
                if link:
                    привязано += t["amount"]
        else:
            ушло += t["amount"]
        rows.append({
            **t,
            "peer": _short(t.get("peer") or ""),
            "order_id": (inv or {}).get("order_id") or (link or {}).get("order_id") or "",
            # Своим счётом или рукой человека — разное знание, и путать их
            # нельзя: одно проверено блокчейном, второе — чьим-то решением.
            "linked": bool(link and not inv),
            "linked_by": (link or {}).get("by_name") or "",
        })

    # Сколько всего оплачено криптой по нашим счетам. Отдельно от ленты и
    # нарочно: лента — это последние переводы кошелька, а вопрос «сколько
    # прошло через приложение» про всю историю, и ответ на него лежит у нас, а
    # не в блокчейне.
    paid = {}
    try:
        paid = await db.crypto_paid_totals()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] итог по счетам не посчитан: {e}")

    # Строка в журнал: единственный способ проверить эти числа, не влезая в
    # чужой экран. Сходится ли остаток с балансом — видно сразу.
    log.info(f"[wallet] переводов {len(rows)} · пришло {round(через + напрямую, 2)} "
             f"(через приложение {round(через, 2)}) · ушло {round(ушло, 2)} · "
             f"остаток {round(через + напрямую - ушло, 2)} · "
             f"баланс {(balance or {}).get('usdt')}")

    return {
        "paid": paid,
        "address": _short(TRON_RECEIVE_ADDRESS),
        "balance": balance or {"usdt": 0.0, "trx": 0.0, "unknown": True},
        "offline": offline or balance is None,
        # Лента отдаётся целиком: обрезка на полусотне превращала историю в
        # «последние две недели», и человек справедливо спрашивал, где
        # остальное. Разворачивает её экран, по кнопке.
        "transfers": rows[:1000],
        "more": len(rows) > 1000,
        # «Ушло» тут не ради полноты: без него ноль на балансе выглядит
        # поломкой экрана, хотя это обычная жизнь кошелька — пришло и вывели.
        "totals": {"app": round(через, 2), "direct": round(напрямую, 2),
                   "linked": round(привязано, 2),
                   "in": round(через + напрямую, 2), "out": round(ушло, 2),
                   "n": len(rows)},
        "at": int(_t.time() * 1000),
    }


@require_owner
async def handle_wallet(request):
    """Баланс и последние переводы. Ответ держим полминуты на всех сразу."""
    if not TRON_RECEIVE_ADDRESS:
        return web.json_response({"error": "no_wallet"}, status=404, headers=CORS_HEADERS)
    свежий = str(request.query.get("fresh") or "") in ("1", "true")
    if not свежий and _CACHE["data"] and _t.monotonic() - _CACHE["at"] < TTL:
        return web.json_response({**_CACHE["data"], "cached": True},
                                 headers=CORS_HEADERS)
    data = await _build()
    # Ответ, в котором не дозвонились до сети, не кэшируем: следующее открытие
    # экрана должно попробовать заново, а не показывать ту же пустоту.
    if not data["offline"]:
        _CACHE.update(at=_t.monotonic(), data=data)
    return web.json_response(data, headers=CORS_HEADERS)


def _drop_cache():
    """Экран после привязки обязан показать её сразу, а не через полминуты."""
    _CACHE.update(at=0.0, data=None)


async def _link_note(kind: str, txid: str, oid: str, amount, who: str) -> None:
    try:
        from owner_routes import notify_owners_force, _md
    except Exception as e:                       # noqa: BLE001
        log.error(f"[wallet] письмо о привязке не отправлено: {e}")
        return
    шапка = ("🔗 *Поступление привязано к заказу*" if kind == "link"
             else "🔗 *Привязка поступления снята*")
    строки = [шапка, _md(who or "—"), "",
              f"• Заказ #{_md(oid)} — {amount} USDT",
              f"`{_md(str(txid)[:32])}`"]
    try:
        await notify_owners_force("wallet.link", "\n".join(строки))
    except Exception as e:                       # noqa: BLE001
        log.error(f"[wallet] письмо о привязке не ушло: {e}")


@require_owner
async def handle_link(request):
    """Связать прямое поступление с заказом.

    Только то, что пришло без счёта: у оплаты из бота связь уже есть, и
    перебивать её рукой значит спорить с блокчейном."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    txid = str(body.get("txid") or "").strip()[:80]
    oid = str(body.get("order_id") or "").strip()[:40]
    if not txid or not oid:
        return web.json_response({"error": "txid_and_order_required"}, status=400,
                                 headers=CORS_HEADERS)
    order = await db.get_order(oid)
    if not order:
        return web.json_response({"error": "unknown_order"}, status=404, headers=CORS_HEADERS)
    inv = await db.crypto_invoices_by_txids([txid])
    if inv:
        return web.json_response({"error": "already_paid_by_invoice"}, status=409,
                                 headers=CORS_HEADERS)
    who = str(body.get("as") or "").strip()[:40]
    ok = await db.wallet_link_set(txid, {
        "order_id": oid, "amount": float(body.get("amount") or 0),
        "by": int(request.get("owner_id") or 0), "by_name": who,
        "at": datetime.now(timezone.utc)})
    if not ok:
        return web.json_response({"error": "not_saved"}, status=500, headers=CORS_HEADERS)
    _drop_cache()
    log.info(f"[wallet] привязка {txid[:16]}… → заказ {oid}")
    await _link_note("link", txid, oid, body.get("amount") or 0, who)
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_owner
async def handle_unlink(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    txid = str(body.get("txid") or "").strip()[:80]
    было = (await db.wallet_links([txid])).get(txid) or {}
    ok = await db.wallet_link_del(txid)
    _drop_cache()
    if ok:
        log.info(f"[wallet] привязка снята {txid[:16]}…")
        await _link_note("unlink", txid, было.get("order_id") or "—",
                         было.get("amount") or 0,
                         str(body.get("as") or "").strip()[:40])
    return web.json_response({"ok": ok}, headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/wallet", _opt)
    app.router.add_get("/api/owner/wallet", handle_wallet)
    app.router.add_route("OPTIONS", "/api/owner/wallet/link", _opt)
    app.router.add_post("/api/owner/wallet/link", handle_link)
    app.router.add_route("OPTIONS", "/api/owner/wallet/unlink", _opt)
    app.router.add_post("/api/owner/wallet/unlink", handle_unlink)
    log.info("[wallet] routes mounted")
