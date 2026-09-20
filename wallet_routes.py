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
import io
import logging
import time as _t
from datetime import datetime, timedelta, timezone

DUBAI = timezone(timedelta(hours=4))

from aiohttp import web

import db
import tron
from config import TRON_RECEIVE_ADDRESS, TRON_OLD_ADDRESSES
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


async def old_addresses() -> list:
    """Прежние кошельки: из настроек и из уже выставленных счетов. Сменили
    адрес — старый остаётся на экране «Старым кошельком», деньги там."""
    seen, out = {TRON_RECEIVE_ADDRESS}, []
    try:
        известные = await db.crypto_invoice_addresses()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] адреса счетов: {e}")
        известные = []
    for a in list(TRON_OLD_ADDRESSES) + list(известные):
        a = str(a or "").strip()
        if a and a not in seen:
            seen.add(a); out.append(a)
    return out


async def _view(address: str) -> dict:
    """Один кошелёк: баланс, переводы, кто за ними стоит, итоги."""
    balance = await tron.get_balance(address)
    transfers = await tron.get_transfers(address)
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
    # Назначения платежей — из отчёта владельца кошелька (см. handle_report).
    purposes = {}
    try:
        purposes = await db.wallet_purposes(ids)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] назначения не подшились: {e}")

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
            "purpose": (purposes.get(tx) or {}).get("text") or "",
            "purpose_by": (purposes.get(tx) or {}).get("by_name") or "",
        })

    # Строка в журнал: единственный способ проверить эти числа, не влезая в
    # чужой экран. Сходится ли остаток с балансом — видно сразу.
    log.info(f"[wallet] {_short(address)}: переводов {len(rows)} · пришло "
             f"{round(через + напрямую, 2)} (через приложение {round(через, 2)}) · "
             f"ушло {round(ушло, 2)} · остаток {round(через + напрямую - ушло, 2)} · "
             f"баланс {(balance or {}).get('usdt')}")

    return {
        "address": _short(address),
        "address_full": address,
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
                   "n": len(rows), "purposed": sum(1 for r in rows if r.get("purpose"))},
    }


async def _build() -> dict:
    """Текущий кошелёк — как раньше, плюс прежние списком `old`. Экран
    показывает первый, а старые — по переключателю, «Старый кошелёк»."""
    main = await _view(TRON_RECEIVE_ADDRESS)
    old = []
    for a in await old_addresses():
        v = await _view(a)
        v["label"] = "Старый кошелёк"
        old.append(v)
    # Сколько всего оплачено криптой по нашим счетам. Отдельно от ленты и
    # нарочно: лента — это последние переводы кошелька, а вопрос «сколько
    # прошло через приложение» про всю историю, и ответ на него лежит у нас, а
    # не в блокчейне — и не зависит от того, на какой адрес платили.
    paid = {}
    try:
        paid = await db.crypto_paid_totals()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] итог по счетам не посчитан: {e}")
    return {**main, "label": "Кошелёк", "paid": paid, "old": old,
            "at": int(_t.time() * 1000)}


async def _all_transfers() -> list | None:
    """Переводы по всем кошелькам — для сверки и выгрузки. None — сеть молчит."""
    out, any_ok = [], False
    for a in [TRON_RECEIVE_ADDRESS] + await old_addresses():
        t = await tron.get_transfers(a)
        if t is None:
            continue
        any_ok = True
        out += t
    return out if any_ok else None


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


# ── сопоставление с заказами ────────────────────────────────────────────────
# Прямое поступление можно узнать по сумме: заказ стоит N дирхам, а по нашему
# курсу это ровно столько-то USDT. Совпало число и совпал день — почти наверняка
# это он и есть.
#
# «Почти» здесь и есть вся суть, поэтому сопоставление НЕ привязывает молча. Оно
# показывает, что нашло, и привязывает только то, у чего нет второго кандидата:
# два заказа на одну сумму в один день — это не совпадение, а монетка, и решать
# такое должен человек.
MATCH_DAYS = 2            # на сколько дней вокруг перевода ищем заказ
MATCH_TOL = 0.02          # допуск по сумме: 2 % или 1 USDT, что больше
DEAD = ("cancelled", "declined", "canceled")


def _usdt_of(total_aed) -> float:
    from config import CRYPTO_AED_PER_USDT
    rate = CRYPTO_AED_PER_USDT or 3.5
    return round(float(total_aed or 0) / rate, 2)


async def _match(apply: bool, who: str = "") -> dict:
    """Свести прямые поступления с заказами по сумме и дню."""
    from datetime import timedelta
    transfers = await _all_transfers()
    if transfers is None:
        return {"error": "offline"}
    ids = [t["txid"] for t in transfers if t.get("txid")]
    byid = await db.crypto_invoices_by_txids(ids)
    links = await db.wallet_links()
    # Кандидаты — только безымянные приходы: у оплаты по счёту связь уже есть.
    сироты = [t for t in transfers
              if t.get("in") and t.get("txid")
              and t["txid"] not in byid and t["txid"] not in links]
    if not сироты:
        return {"pairs": [], "orphans": 0, "taken": 0, "ambiguous": 0}

    lo = min(t["ts"] for t in сироты) - MATCH_DAYS * 86400_000
    hi = max(t["ts"] for t in сироты) + MATCH_DAYS * 86400_000
    start = datetime.fromtimestamp(lo / 1000, timezone.utc).isoformat()
    end = datetime.fromtimestamp(hi / 1000, timezone.utc).isoformat()
    orders = await db.get_orders_in_range(
        start, end, limit=None,
        fields=["order_id", "timestamp", "total", "status", "customer_name",
                "payment_method", "paid"])
    # Заказ, за который уже заплатили криптой по счёту, второй раз не платят.
    занятые = {(v or {}).get("order_id") for v in byid.values()}
    занятые |= {(v or {}).get("order_id") for v in links.values()}
    свободные = [o for o in orders
                 if str(o.get("order_id") or "") not in занятые
                 and (o.get("status") or "") not in DEAD
                 and float(o.get("total") or 0) > 0]

    pairs, взято, спорных = [], 0, 0
    занято_сейчас = set()
    for t in sorted(сироты, key=lambda x: x["ts"]):
        нужно = t["amount"]
        допуск = max(1.0, нужно * MATCH_TOL)
        рядом = []
        for o in свободные:
            oid = str(o.get("order_id") or "")
            if not oid or oid in занято_сейчас:
                continue
            try:
                ts = datetime.fromisoformat(
                    str(o.get("timestamp") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            разрыв = abs((ts.timestamp() * 1000) - t["ts"]) / 3600_000
            if разрыв > MATCH_DAYS * 24:
                continue
            ожидали = _usdt_of(o.get("total"))
            расхождение = abs(ожидали - нужно)
            if расхождение <= допуск:
                рядом.append((расхождение, разрыв, oid, o, ожидали))
        рядом.sort(key=lambda x: (round(x[0], 2), x[1]))
        if not рядом:
            continue
        # Второй кандидат с той же суммой — это монетка, а не совпадение.
        спорно = len(рядом) > 1 and round(рядом[1][0], 2) == round(рядом[0][0], 2)
        расхождение, разрыв, oid, o, ожидали = рядом[0]
        if спорно:
            спорных += 1
        pairs.append({
            "txid": t["txid"], "amount": t["amount"], "ts": t["ts"],
            "order_id": oid, "order_total": int(o.get("total") or 0),
            "order_usdt": ожидали, "order_ts": str(o.get("timestamp") or ""),
            "name": o.get("customer_name") or "",
            "gap_h": round(разрыв, 1), "off": round(расхождение, 2),
            "sure": not спорно,
            "others": len(рядом) - 1,
        })
        if спорно:
            continue
        занято_сейчас.add(oid)
        if apply:
            await db.wallet_link_set(t["txid"], {
                "order_id": oid, "amount": t["amount"],
                "by": 0, "by_name": who or "сопоставление",
                "auto": True, "at": datetime.now(timezone.utc)})
            взято += 1
    if apply:
        _drop_cache()
        log.info(f"[wallet] сопоставление: привязано {взято} из {len(сироты)}")
    return {"pairs": pairs, "orphans": len(сироты), "taken": взято,
            "ambiguous": спорных}


@require_owner
async def handle_match(request):
    """GET — что нашлось, POST — привязать найденное без спорных."""
    apply = request.method == "POST"
    who = ""
    if apply:
        try:
            who = str((await request.json()).get("as") or "").strip()[:40]
        except Exception:
            who = ""
    out = await _match(apply, who)
    if out.get("error") == "offline":
        return web.json_response(out, status=503, headers=CORS_HEADERS)
    if apply and out.get("taken"):
        try:
            from owner_routes import notify_owners_force, _md
            await notify_owners_force(
                "wallet.link",
                "🔗 *Поступления сведены с заказами*\n"
                + _md(who or "—") + "\n\n"
                + f"• привязано {out['taken']} "
                + ("· спорных оставлено " + str(out["ambiguous"])
                   if out.get("ambiguous") else "· спорных нет"))
        except Exception as e:                   # noqa: BLE001
            log.error(f"[wallet] письмо о сопоставлении не ушло: {e}")
    return web.json_response(out, headers=CORS_HEADERS)


# ── файлом в чат ────────────────────────────────────────────────────────────
# Список переводов на экране — чтобы посмотреть, а файл — чтобы работать: свести
# с бухгалтерией, отправить дальше, оставить у себя. Скачать из мини-приложения
# некуда, поэтому бот кладёт документ владельцу в переписку, как и заявку.


def _book(data: dict, only_linked: bool = False):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    # Переводы всех кошельков — текущего и прежних, с пометкой, чей перевод.
    rows = []
    for w in [data] + list(data.get("old") or []):
        for r in (w.get("transfers") or []):
            if only_linked and not r.get("order_id"):
                continue
            rows.append({**r, "wallet": w.get("label") or "Кошелёк",
                         "waddr": w.get("address") or ""})
    rows.sort(key=lambda r: -(r.get("ts") or 0))
    wb = Workbook()
    ws = wb.active
    ws.title = "Переводы"
    шапка = ["Дата", "Время", "Направление", "USDT", "Заказ", "Как связано",
             "Вторая сторона", "Кошелёк", "Транзакция"]
    ws.append(шапка)
    for i, _ in enumerate(шапка, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1A1A32")
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        д = datetime.fromtimestamp((r.get("ts") or 0) / 1000, timezone.utc)
        д = д.astimezone(DUBAI)
        связь = ("счёт из бота" if r.get("order_id") and not r.get("linked")
                 else "сверка / вручную" if r.get("linked") else "")
        ws.append([д.strftime("%d.%m.%Y"), д.strftime("%H:%M"),
                   "приход" if r.get("in") else "расход",
                   round(float(r.get("amount") or 0), 2),
                   str(r.get("order_id") or ""), связь,
                   str(r.get("peer") or ""),
                   f"{r.get('wallet')} {r.get('waddr')}".strip(),
                   str(r.get("txid") or "")])
    for кол, ширина in zip("ABCDEFGHI", (12, 8, 13, 12, 16, 18, 20, 26, 46)):
        ws.column_dimensions[кол].width = ширина
    ws.freeze_panes = "A2"

    ws.append([])
    # Итоги — по каждому кошельку отдельно: складывать остатки двух адресов в
    # одно число значило бы скрыть, где именно лежат деньги.
    start = ws.max_row + 1
    for w in [data] + list(data.get("old") or []):
        итог = w.get("totals") or {}
        имя = f"{w.get('label') or 'Кошелёк'} {w.get('address') or ''}".strip()
        ws.append([f"{имя}: пришло", "", "", итог.get("in")])
        ws.append([f"{имя}: ушло", "", "", итог.get("out")])
        ws.append([f"{имя}: остаток", "", "",
                   round((итог.get("in") or 0) - (итог.get("out") or 0), 2)])
    ws.append(["Оплачено по нашим счетам", "", "", (data.get("paid") or {}).get("usdt")])
    for i in range(start, ws.max_row + 1):
        ws.cell(row=i, column=1).font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(rows)


@require_owner
async def handle_export(request):
    """Прислать переводы файлом в чат с ботом."""
    from api_server import _aiohttp
    from owner_routes import OWNER_BOT_TOKEN
    if not OWNER_BOT_TOKEN:
        return web.json_response({"error": "no_bot"}, status=500, headers=CORS_HEADERS)
    only = str(request.query.get("linked") or "") in ("1", "true")
    data = _CACHE["data"] if _CACHE["data"] else await _build()
    raw, n = _book(data, only)
    имя = ("ambar-postupleniya" if only else "ambar-koshelek") + \
          f"-{datetime.now(DUBAI).strftime('%Y%m%d')}.xlsx"
    итог = data.get("totals") or {}
    старые = sum((w.get("balance") or {}).get("usdt") or 0 for w in (data.get("old") or []))
    подпись = (f"Поступления, сведённые с заказами · {n}" if only else
               f"Кошелёк USDT · {n} "
               + ("перевод" if n % 10 == 1 and n % 100 != 11 else "переводов")
               + f"\nПришло {итог.get('in')} · ушло {итог.get('out')} · "
               + f"остаток {round((итог.get('in') or 0) - (итог.get('out') or 0), 2)}"
               + (f"\nСтарый кошелёк: {round(старые, 2)} USDT" if data.get("old") else ""))
    form = _aiohttp.FormData()
    form.add_field("chat_id", str(request.get("owner_id") or 0))
    form.add_field("caption", подпись)
    form.add_field("document", raw, filename=имя,
                   content_type="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")
    url = f"https://api.telegram.org/bot{OWNER_BOT_TOKEN}/sendDocument"
    to = _aiohttp.ClientTimeout(total=30)
    try:
        async with _aiohttp.ClientSession(timeout=to) as sess:
            async with sess.post(url, data=form) as r:
                res = await r.json()
    except Exception as e:                       # noqa: BLE001
        log.error(f"[wallet] отправка файла: {e}")
        return web.json_response({"error": "send_failed"}, status=502, headers=CORS_HEADERS)
    if not res.get("ok"):
        log.error(f"[wallet] телеграм отказал: {res.get('description')}")
        return web.json_response({"error": "telegram"}, status=502, headers=CORS_HEADERS)
    # В реестр переписки владельца: по тревоге файл должен уходить вместе со
    # всеми — в нём номера транзакций и суммы.
    try:
        from api_server import _remember_owner_msg
        await _remember_owner_msg(OWNER_BOT_TOKEN, request.get("owner_id") or 0, res)
    except Exception as e:                       # noqa: BLE001
        log.debug(f"[wallet] реестр файла: {e}")
    return web.json_response({"ok": True, "rows": n}, headers=CORS_HEADERS)


# ── Отчёт владельца кошелька ────────────────────────────────────────────────
# Владелец, 20 сен 2026: «тот человек, которому принадлежит криптокошелёк, раз
# в неделю нам скидывает отчёт с назначениями платежей — добавь возможность
# загружать эти отчёты в кошелёк, чтобы они автоматически сравнивались с
# платежами и каждый платёж приобретал своё назначение».
#
# Формат отчёта заранее неизвестен и меняться будет не у нас, поэтому разбор
# терпимый: xlsx, csv и просто текст; заголовок ищем по словам, а нет его —
# читаем строку как «дата · сумма · назначение». Сверяем по хешу перевода,
# если он в отчёте есть, иначе по сумме и дню. Спорное (две одинаковые суммы
# в один день) не назначаем сами — отдаём человеку списком.
_HEAD_DATE = ("дата", "date", "время", "time", "когда")
_HEAD_SUM = ("сумма", "amount", "value", "usdt", "приход", "кредит", "credit")
_HEAD_TEXT = ("назначен", "purpose", "описан", "description", "коммент", "comment",
              "примечан", "note", "детал", "detail", "за что", "основание")
_HEAD_TX = ("hash", "хеш", "txid", "tx", "транзак", "id перевода")
# Вид операции: в отчёте владельца это колонка «Заказ» / «Расход» / «Операция».
_HEAD_OP = ("операц", "заказ", "расход", "тип", "вид")


def _num(v):
    """Сумма из ячейки: «1 234,50», «1,234.50 USDT», «+120» — всё одно число."""
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v or "").strip().replace("\u00a0", " ")
    t = "".join(c for c in t if c.isdigit() or c in ".,-+ ")
    t = t.replace(" ", "")
    if not t or t in ("-", "+"):
        return None
    if "," in t and "." in t:                     # 1,234.50 или 1.234,50
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _when(v):
    """Дата из ячейки — datetime или None. Час и минуты, если они есть."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=DUBAI)
    t = str(v or "").strip()
    if not t:
        return None
    t = t.replace("T", " ").replace("/", ".").replace("-", ".")
    for f in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
              "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
              "%d.%m.%y %H:%M", "%d.%m.%y"):
        try:
            return datetime.strptime(t[:len(f) + 4].strip(), f).replace(tzinfo=DUBAI)
        except ValueError:
            continue
    return None


def _tx(v):
    t = "".join(str(v or "").split()).lower()
    return t if len(t) >= 40 and all(c in "0123456789abcdef" for c in t) else ""


# Куда шли деньги — по виду операции. Нужно только чтобы «Вывод USDT» не
# прихватил чей-то приход той же суммы: строгого списка видов у отчёта нет,
# поэтому это подсказка, а не закон. «Возврат расхода» — приход, хотя слово
# «расход» в нём есть, поэтому приходные слова проверяем первыми.
_WAY_IN = ("возврат", "пополнен", "приход", "заказ", "получен")
_WAY_OUT = ("вывод", "снятие", "снял", "sim", "сим", "отправ", "перевод в", "списан")


def _way(text: str):
    """True — ушло с кошелька, False — пришло, None — не поняли.

    Смотрим только на вид операции — то, что до точки: в описании «Вывод USDT ·
    Пополнение VIP ENOC» слово «пополнение» говорит, куда ушли деньги, а не
    откуда пришли, и по всему тексту вывод читался бы приходом."""
    t = (text or "").split(" · ")[0].lower()
    for w in _WAY_IN:
        if w in t:
            return False
    for w in _WAY_OUT:
        if w in t:
            return True
    return None


def _cells(name: str, raw: bytes) -> list:
    """Файл → листы, лист → строки ячеек. Каждый лист разбирается отдельно: в
    отчёте владельца кошелька их семь, и заголовки у них разные."""
    if name.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        листы = []
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)
                    if any(c is not None and str(c).strip() for c in r)]
            if rows:
                листы.append(rows)
        return листы
    txt = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            txt = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    if txt is None:
        return []
    lines = [l for l in txt.splitlines() if l.strip()]
    if not lines:
        return []
    делитель = max((";", "\t", ","), key=lambda d: sum(l.count(d) for l in lines[:20]))
    if sum(l.count(делитель) for l in lines[:20]) == 0:
        return [[[l] for l in lines]]
    return [[[c.strip().strip('"') for c in l.split(делитель)] for l in lines]]


def _columns(rows: list) -> tuple:
    """Ищем строку заголовка и номера колонок. Нет заголовка — (None, {}).

    Смотрим глубже первых строк: в отчёте владельца кошелька шапка листа
    «Заказы» стоит тринадцатой — над ней итоги по месяцам."""
    for i, row in enumerate(rows[:30]):
        имена = [str(c or "").strip().lower() for c in row]
        col = {}
        for j, h in enumerate(имена):
            if not h:
                continue
            for ключ, слова in (("date", _HEAD_DATE), ("sum", _HEAD_SUM),
                                ("text", _HEAD_TEXT), ("tx", _HEAD_TX)):
                if ключ not in col and any(w in h for w in слова):
                    col[ключ] = j
        # Вид операции («Приход от клиента», «Вывод USDT») — слева от монеты;
        # вместе с описанием из него и получается назначение платежа.
        if "text" in col:
            for j, h in enumerate(имена):
                if j < col["text"] and any(w in h for w in _HEAD_OP):
                    col["op"] = j
                    break
        if "text" in col and ("sum" in col or "tx" in col):
            return i, col
    return None, {}


def _report_rows(name: str, raw: bytes) -> list:
    """Отчёт → [{when, amount, text, txid}] — всё, что удалось прочитать.

    Одна и та же операция в отчёте встречается дважды: на листе месяца и в
    сводном листе «Заказы» или «Расходы». Повторы схлопываем — иначе половина
    строк честно «не нашлась» бы просто потому, что перевод уже занят."""
    out, seen = [], {}
    for rows in _cells(name, raw):
        head, col = _columns(rows)
        for row in rows[(head + 1) if head is not None else 0:]:
            row = list(row)
            if col:
                бери = lambda k: row[col[k]] if col.get(k) is not None and len(row) > col[k] else None
                текст = " ".join(str(бери("text") or "").split())
                вид = " ".join(str(бери("op") or "").split())
                сумма = _num(бери("sum"))
                когда = _when(бери("date"))
                хеш = _tx(бери("tx"))
                # Назначение — вид операции и описание вместе: «Вывод USDT ·
                # снятие наличных», «Приход от клиента · заказ».
                части = [ч for ч in (вид, текст) if ч]
                if len(части) == 2 and части[0].lower() == части[1].lower():
                    части = части[:1]
                текст = " · ".join(части)
            else:
                хеш = next((_tx(c) for c in row if _tx(c)), "")
                когда = next((_when(c) for c in row if _when(c)), None)
                сумма = next((_num(c) for c in row if _num(c) is not None and not _tx(c)
                              and _when(c) is None), None)
                текст = " ".join(str(c).strip() for c in row
                                 if str(c or "").strip() and _num(c) is None and _when(c) is None
                                 and not _tx(c))[:200]
            текст = " ".join(текст.split())[:200]
            if not текст or (сумма is None and not хеш):
                continue
            # Строки итогов («Июнь 2026 (13 оп.)», «Смена #1») суммы и даты не
            # имеют — они сюда и не попадают: без даты и без хеша не берём.
            if когда is None and not хеш:
                continue
            ключ = (хеш or "", round(abs(сумма or 0), 2),
                    когда.strftime("%Y%m%d%H%M") if когда else "", текст.lower())
            if ключ in seen:
                continue
            seen[ключ] = True
            out.append({"when": когда, "amount": сумма, "text": текст, "txid": хеш,
                        "way": _way(текст)})
    return out


def _match(rows: list, transfers: list) -> tuple:
    """Сводим строки отчёта с переводами: (нашли, спорные, не нашли).

    Хеша в отчёте владельца кошелька нет, поэтому опора — сумма и время. Время
    сходится минута в минуту, а сумма в отчёте округлена до копеек и бывает
    меньше пришедшей на комиссию отправителя — около двух десятых процента.
    Поэтому мерки две: строгая годится в любой день, широкая — только для
    перевода в те же четверть часа, где и без копеек всё понятно.

    Несколько кандидатов рядом — берём ближайший, и только если он ближе
    второго больше чем на пять минут. Иначе строка спорная: решает человек."""
    свободные = [t for t in transfers if t.get("txid")]
    по_хешу = {t["txid"]: t for t in свободные}
    занято, нашли, спорные, мимо = set(), [], [], []
    строго = lambda t, s: abs(abs(t.get("amount") or 0) - s) <= max(0.02, s * 0.002)
    широко = lambda t, s: abs(abs(t.get("amount") or 0) - s) <= max(0.10, s * 0.012)
    сек = lambda t, w: abs((t.get("ts") or 0) / 1000 - w.timestamp())
    день = lambda t: datetime.fromtimestamp((t.get("ts") or 0) / 1000, DUBAI).date()

    def взять(r, t):
        занято.add(t["txid"]); нашли.append((r, t))

    def своим(пул, r):
        """Приход к приходу, вывод к выводу — если такие вообще есть."""
        if r.get("way") is None:
            return пул
        свои = [t for t in пул if bool(t.get("in")) != r["way"]]
        return свои or пул

    for r in rows:
        if r["txid"] and r["txid"] in по_хешу and r["txid"] not in занято:
            взять(r, по_хешу[r["txid"]]); continue
        if r["amount"] is None:
            мимо.append(r); continue
        сумма = abs(r["amount"])
        ок = [t for t in свободные if t["txid"] not in занято]
        точные = своим([t for t in ок if строго(t, сумма)], r)
        if not r["when"]:
            if len(точные) == 1:
                взять(r, точные[0])
            elif точные:
                спорные.append((r, точные[:6]))
            else:
                мимо.append(r)
            continue
        четверть = своим([t for t in ок if сек(t, r["when"]) <= 900
                          and широко(t, сумма)], r)
        четверть.sort(key=lambda t: сек(t, r["when"]))
        if четверть:
            if len(четверть) == 1 or сек(четверть[1], r["when"]) - сек(четверть[0], r["when"]) > 300:
                взять(r, четверть[0])
            else:
                спорные.append((r, четверть[:6]))
            continue
        # Рядом никого — ищем по всему дню, потом в двух днях вокруг: время в
        # отчёте иногда проставлено задним числом, а сумма всё та же.
        точные.sort(key=lambda t: сек(t, r["when"]))
        свой_день = r["when"].astimezone(DUBAI).date()
        около = ([t for t in точные if день(t) == свой_день]
                 or [t for t in точные if abs((день(t) - свой_день).days) <= 2])
        if len(около) == 1:
            взять(r, около[0])
        elif около:
            спорные.append((r, около[:6]))
        else:
            мимо.append(r)
    return нашли, спорные, мимо


def _row_out(r: dict) -> dict:
    return {"text": r["text"], "amount": r["amount"], "txid": r["txid"],
            "when": r["when"].strftime("%d.%m.%Y") if r["when"] else "",
            "ts": int(r["when"].timestamp() * 1000) if r["when"] else 0,
            "out": bool(r.get("way"))}


@require_owner
async def handle_report(request):
    """POST {name, data(base64), as} — отчёт владельца кошелька с назначениями.

    Разбираем, сверяем с переводами и проставляем назначение каждому, что
    сошлось. Спорное и ненайденное возвращаем списком — это ответ человеку, а
    не ошибка: отчёт может быть за другой период или с чужими строками."""
    import base64
    try:
        body = await request.json()
    except Exception:                            # noqa: BLE001
        return web.json_response({"error": "bad_request"}, status=400, headers=CORS_HEADERS)
    имя = str(body.get("name") or "отчёт")[:120]
    try:
        raw = base64.b64decode(str(body.get("data") or ""), validate=False)
    except Exception:                            # noqa: BLE001
        raw = b""
    if not raw:
        return web.json_response({"error": "empty"}, status=400, headers=CORS_HEADERS)
    if len(raw) > 8 * 1024 * 1024:
        return web.json_response({"error": "too_big"}, status=400, headers=CORS_HEADERS)
    try:
        rows = _report_rows(имя, raw)
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[wallet] отчёт {имя} не разобран: {e}")
        return web.json_response({"error": "unreadable"}, status=400, headers=CORS_HEADERS)
    if not rows:
        return web.json_response({"error": "no_rows"}, status=400, headers=CORS_HEADERS)
    transfers = await _all_transfers()
    if transfers is None:
        return web.json_response({"error": "offline"}, status=502, headers=CORS_HEADERS)
    нашли, спорные, мимо = _match(rows, transfers)
    # Не нашли — это две разные вещи. Трата из остатка («снятие наличных с
    # банкомата») перевода на кошельке и не оставляет: её не ищут, о ней
    # сообщают. А вот платёж без перевода — повод посмотреть глазами.
    траты = [r for r in мимо if r.get("way")]
    платежи = [r for r in мимо if not r.get("way")]
    who = str(body.get("as") or "").strip()[:60]
    now = datetime.now(timezone.utc)
    for r, t in нашли:
        await db.wallet_purpose_set(t["txid"], {
            "text": r["text"], "amount": t.get("amount"), "ts": t.get("ts"),
            "src": "report", "report": имя, "by_name": who, "at": now})
    _drop_cache()
    await db.wallet_report_add({"at": now, "by_name": who, "name": имя,
                                "rows": len(rows), "matched": len(нашли),
                                "disputed": len(спорные), "missed": len(мимо),
                                "spent": len(траты)})
    log.info(f"[wallet] отчёт «{имя}»: строк {len(rows)} · назначено {len(нашли)} · "
             f"спорных {len(спорные)} · трат без перевода {len(траты)} · "
             f"платежей не нашли {len(платежи)} · {who or '—'}")
    return web.json_response({
        "ok": True, "rows": len(rows), "matched": len(нашли),
        "disputed": [{**_row_out(r), "candidates": [
            {"txid": t["txid"], "amount": t.get("amount"), "ts": t.get("ts")} for t in c[:4]]}
            for r, c in спорные[:20]],
        "missed": [_row_out(r) for r in платежи[:20]],
        "spent": [_row_out(r) for r in траты[:20]],
        "disputed_n": len(спорные), "missed_n": len(платежи),
        "spent_n": len(траты),
        "spent_sum": round(sum(abs(r["amount"] or 0) for r in траты), 2),
    }, headers=CORS_HEADERS)


@require_owner
async def handle_purpose(request):
    """POST {txid, text, as} — назначение рукой: поправить или дописать то,
    чего в отчёте не было. Пустой текст — снять."""
    try:
        body = await request.json()
    except Exception:                            # noqa: BLE001
        return web.json_response({"error": "bad_request"}, status=400, headers=CORS_HEADERS)
    txid = str(body.get("txid") or "").strip()
    text = " ".join(str(body.get("text") or "").split())[:200]
    if not txid:
        return web.json_response({"error": "no_txid"}, status=400, headers=CORS_HEADERS)
    who = str(body.get("as") or "").strip()[:60]
    if text:
        await db.wallet_purpose_set(txid, {"text": text, "src": "hand", "by_name": who,
                                           "at": datetime.now(timezone.utc)})
    else:
        await db.wallet_purpose_del(txid)
    _drop_cache()
    log.info(f"[wallet] назначение {txid[:10]}…: {text or '— снято'} · {who or '—'}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    app.router.add_route("OPTIONS", "/api/owner/wallet", _opt)
    app.router.add_get("/api/owner/wallet", handle_wallet)
    app.router.add_route("OPTIONS", "/api/owner/wallet/link", _opt)
    app.router.add_post("/api/owner/wallet/link", handle_link)
    app.router.add_route("OPTIONS", "/api/owner/wallet/unlink", _opt)
    app.router.add_post("/api/owner/wallet/unlink", handle_unlink)
    app.router.add_route("OPTIONS", "/api/owner/wallet/export", _opt)
    app.router.add_post("/api/owner/wallet/export", handle_export)
    app.router.add_route("OPTIONS", "/api/owner/wallet/match", _opt)
    app.router.add_get("/api/owner/wallet/match", handle_match)
    app.router.add_post("/api/owner/wallet/match", handle_match)
    # Отчёт владельца кошелька с назначениями платежей (20 сен 2026).
    app.router.add_route("OPTIONS", "/api/owner/wallet/report", _opt)
    app.router.add_post("/api/owner/wallet/report", handle_report)
    app.router.add_route("OPTIONS", "/api/owner/wallet/purpose", _opt)
    app.router.add_post("/api/owner/wallet/purpose", handle_purpose)
    log.info("[wallet] routes mounted")
