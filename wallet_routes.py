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

    # Через приложение или напрямую: счёт знает txid своего перевода.
    byid = {}
    try:
        byid = await db.crypto_invoices_by_txids([t["txid"] for t in transfers if t.get("txid")])
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] счета к переводам не подшились: {e}")

    через = 0.0
    напрямую = 0.0
    ушло = 0.0
    rows = []
    for t in transfers:
        inv = byid.get(t.get("txid") or "")
        if t.get("in"):
            if inv:
                через += t["amount"]
            else:
                напрямую += t["amount"]
        else:
            ушло += t["amount"]
        rows.append({
            **t,
            "peer": _short(t.get("peer") or ""),
            "order_id": (inv or {}).get("order_id") or "",
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

    return {
        "paid": paid,
        "address": _short(TRON_RECEIVE_ADDRESS),
        "balance": balance or {"usdt": 0.0, "trx": 0.0, "unknown": True},
        "offline": offline or balance is None,
        # Лента на экран — последние полсотни: дальше её никто не листает, а
        # считаем мы по всем.
        "transfers": rows[:50],
        # «Ушло» тут не ради полноты: без него ноль на балансе выглядит
        # поломкой экрана, хотя это обычная жизнь кошелька — пришло и вывели.
        "totals": {"app": round(через, 2), "direct": round(напрямую, 2),
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


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/wallet", _opt)
    app.router.add_get("/api/owner/wallet", handle_wallet)
    log.info("[wallet] routes mounted")
