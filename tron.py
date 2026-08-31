"""
AMBAR — read-only TRON / TRC-20 watcher.

The server NEVER holds keys or signs anything. It only READS confirmed USDT
(TRC-20) transfers that land on the merchant receive address, so it can credit
the matching order. Funds stay in the merchant's own wallet — there is nothing
to sweep and no hot wallet to compromise.

Docs: https://developers.tron.network  (TronGrid HTTP API)
"""
from __future__ import annotations
import logging, time
import aiohttp
from config import TRONGRID_BASE_URL, TRONGRID_API_KEY, USDT_TRC20_CONTRACT

log = logging.getLogger(__name__)

# USDT TRC-20 uses 6 decimals; on-chain values are integers in these base units.
USDT_DECIMALS = 6
_USDT_UNIT = 10 ** USDT_DECIMALS
# Approx TRON block time, only used to *estimate* a confirmations count for the
# UI. Crediting never relies on this number — it relies on only_confirmed below.
_BLOCK_SEC = 3


async def get_incoming_usdt(address: str, since_ms: int,
                            only_confirmed: bool = True) -> list[dict]:
    """Confirmed incoming USDT TRC-20 transfers to `address` at/after `since_ms`.

    `since_ms` is epoch milliseconds (match the invoice creation time).
    With only_confirmed=True, TronGrid returns transfers from the solidified
    (irreversible) chain — that is the safe signal to credit on.

    Returns a list of dicts (newest first):
        {amount: float USDT, txid: str, ts: int ms, confirmations: int, from: str}
    Read-only and defensive: returns [] on any error, so the caller simply
    treats "no data" as "not paid yet" and retries on the next poll.
    """
    if not address or not TRONGRID_API_KEY:
        return []
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY, "Accept": "application/json"}
    url = f"{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions/trc20"
    params = {
        "only_to": "true",
        "only_confirmed": "true" if only_confirmed else "false",
        "contract_address": USDT_TRC20_CONTRACT,
        "min_timestamp": str(int(since_ms)),
        "limit": "50",
        "order_by": "block_timestamp,desc",
    }
    out: list[dict] = []
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    log.warning(f"[tron] trc20 list HTTP {r.status} for {address}")
                    return []
                data = await r.json()
    except Exception as e:
        log.warning(f"[tron] get_incoming_usdt failed for {address}: {e}")
        return []

    now_ms = time.time() * 1000
    for it in (data or {}).get("data", []):
        try:
            # Defence in depth: the query is scoped, but re-check to/contract.
            if (it.get("to") or "").strip() != address.strip():
                continue
            if ((it.get("token_info") or {}).get("address") or "") != USDT_TRC20_CONTRACT:
                continue
            raw = int(it.get("value", "0"))
            ts = int(it.get("block_timestamp", 0))
            # Estimate confirmations from age; cosmetic only (see _BLOCK_SEC).
            conf = max(0, int((now_ms - ts) / 1000 / _BLOCK_SEC)) if ts else 0
            out.append({
                "amount": round(raw / _USDT_UNIT, USDT_DECIMALS),
                "txid": it.get("transaction_id") or it.get("txID") or "",
                "ts": ts,
                "confirmations": conf,
                "from": it.get("from") or "",
            })
        except Exception as e:
            log.debug(f"[tron] parse item skipped: {e}")
            continue
    return out


# ── кошелёк целиком: сколько лежит и что по нему ходило ─────────────────────
# Watcher выше спрашивает узко — «пришёл ли платёж по этому счёту». Владельцу
# нужен другой вопрос: сколько на кошельке сейчас и что по нему вообще было,
# включая переводы мимо заказов и уходящие. Тот же ключ, те же права: читаем.


async def _get(url: str, params: dict | None = None):
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY, "Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, params=params or {},
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                log.warning(f"[tron] HTTP {r.status} на {url}")
                return None
            return await r.json()


async def get_balance(address: str) -> dict | None:
    """Сколько лежит на кошельке: USDT и TRX.

    TRX здесь не деньги, а топливо: без него с кошелька нельзя отправить даже
    свои USDT. Ноль на этой строке однажды окажется важнее баланса."""
    if not address or not TRONGRID_API_KEY:
        return None
    data = await _get(f"{TRONGRID_BASE_URL}/v1/accounts/{address}")
    if not data:
        return None
    row = ((data or {}).get("data") or [None])[0]
    if not row:
        # Кошелёк, на который ещё ничего не приходило, TRON не знает вовсе.
        return {"usdt": 0.0, "trx": 0.0, "unknown": True}
    usdt = 0.0
    for t in row.get("trc20") or []:
        for addr, raw in (t or {}).items():
            if addr == USDT_TRC20_CONTRACT:
                try:
                    usdt = round(int(raw) / _USDT_UNIT, USDT_DECIMALS)
                except (TypeError, ValueError):
                    pass
    # Ноль на балансе двусмыслен: то ли кошелёк пуст, то ли мы не нашли в
    # ответе свой контракт. Строка в журнале эту двусмысленность снимает —
    # видно, сколько токенов на счету вообще и какой из них наш.
    if not usdt:
        log.info(f"[tron] баланс USDT = 0 · токенов на счету: "
                 f"{len(row.get('trc20') or [])}")
    return {"usdt": usdt,
            "trx": round(int(row.get("balance") or 0) / 1_000_000, 6),
            "unknown": False}


async def get_transfers(address: str, limit: int = 200, pages: int = 6,
                        min_ts: int = 0) -> list[dict] | None:
    """Переводы USDT по кошельку — и приход, и расход, новые сверху.

    Отличие от get_incoming_usdt одно, но важное: там стоит only_to и порог по
    времени счёта, потому что вопрос был «оплатили ли заказ». Здесь вопросов
    нет — показываем всё, что ходило, иначе перевод мимо заказа так и остаётся
    невидимым.

    None — значит не дозвонились: это не то же самое, что «переводов нет»."""
    if not address or not TRONGRID_API_KEY:
        return None
    params = {
        "only_confirmed": "true",
        "contract_address": USDT_TRC20_CONTRACT,
        "limit": str(max(1, min(200, int(limit or 50)))),
        "order_by": "block_timestamp,desc",
    }
    if min_ts:
        params["min_timestamp"] = str(int(min_ts))
    # Страницами, а не одной выборкой: «пришло» и «ушло» имеют смысл только за
    # всю жизнь кошелька. По последней сотне переводов остаток не сходится с
    # балансом, и человек справедливо не понимает, где деньги.
    url = f"{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions/trc20"
    raw, fingerprint = [], ""
    for _ in range(max(1, int(pages or 1))):
        if fingerprint:
            params["fingerprint"] = fingerprint
        data = await _get(url, params)
        if data is None:
            # Молчание на первой странице — это молчание сети. Оборвалось в
            # середине — отдаём собранное: неполно, но честнее пустоты.
            return None if not raw else _parse_transfers(raw, address)
        page = (data or {}).get("data") or []
        raw += page
        fingerprint = ((data or {}).get("meta") or {}).get("fingerprint") or ""
        if not fingerprint or len(page) < int(params["limit"]):
            break
    return _parse_transfers(raw, address)


def _parse_transfers(raw: list, address: str) -> list[dict]:
    """Разобрать страницы в переводы, считая каждый ровно один раз.

    Страницы берутся по «отпечатку» предыдущей, и если он не сдвинулся, та же
    сотня переводов пришла бы дважды — а с ней удвоился бы и оборот. Ключ у
    перевода составной: одна транзакция может нести несколько переводов."""
    me = address.strip()
    out, seen = [], set()
    for it in raw:
        try:
            ключ = (it.get("transaction_id") or it.get("txID") or "",
                    it.get("from") or "", it.get("to") or "",
                    str(it.get("value") or ""))
            if ключ in seen:
                continue
            seen.add(ключ)
            if ((it.get("token_info") or {}).get("address") or "") != USDT_TRC20_CONTRACT:
                continue
            to, frm = (it.get("to") or "").strip(), (it.get("from") or "").strip()
            вход = to == me
            out.append({
                "amount": round(int(it.get("value", "0")) / _USDT_UNIT, USDT_DECIMALS),
                "txid": it.get("transaction_id") or it.get("txID") or "",
                "ts": int(it.get("block_timestamp", 0)),
                "in": вход,
                "peer": frm if вход else to,
            })
        except Exception as e:
            log.debug(f"[tron] перевод пропущен: {e}")
            continue
    return out
