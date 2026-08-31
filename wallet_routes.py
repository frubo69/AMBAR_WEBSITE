"""
AMBAR — кошелёк USDT: сколько лежит и что по нему ходило.

Кошелёк у нас уже был, но виден он был только изнутри одного вопроса: watcher
спрашивал «пришёл ли платёж по счёту №такому-то», и всё, что не относилось к
заказу, для приложения не существовало. Перевод мимо заказа, уход с кошелька,
сам остаток — про это нельзя было узнать, не открыв блокчейн-обозреватель.

Здесь тот же ключ TronGrid и те же права: только чтение. Приватных ключей у
сервера нет и не будет — отправить отсюда ничего нельзя, и кнопки такой не
появится.

Переводы сверяем со своими счетами по txid: приход, у которого счёт нашёлся, —
это оплата заказа, а приход без счёта и есть то, ради чего экран и заводился.
"""
import logging
import time as _t

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
    transfers = await tron.get_transfers(TRON_RECEIVE_ADDRESS, limit=60)
    # None и пустой список — разные ответы: первое значит «не дозвонились», и
    # говорить в этом случае «переводов нет» — врать.
    offline = transfers is None
    transfers = transfers or []

    # Чей это приход: свои счета знают txid.
    byid = {}
    try:
        byid = await db.crypto_invoices_by_txids([t["txid"] for t in transfers if t.get("txid")])
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] счета к переводам не подшились: {e}")

    свои = 0.0
    чужие = 0.0
    rows = []
    for t in transfers:
        inv = byid.get(t.get("txid") or "")
        if t.get("in"):
            if inv:
                свои += t["amount"]
            else:
                чужие += t["amount"]
        rows.append({
            **t,
            "peer": _short(t.get("peer") or ""),
            "order_id": (inv or {}).get("order_id") or "",
        })

    return {
        "address": _short(TRON_RECEIVE_ADDRESS),
        "balance": balance or {"usdt": 0.0, "trx": 0.0, "unknown": True},
        "offline": offline or balance is None,
        "transfers": rows,
        "totals": {"orders": round(свои, 2), "other": round(чужие, 2)},
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


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/wallet", _opt)
    app.router.add_get("/api/owner/wallet", handle_wallet)
    log.info("[wallet] routes mounted")
