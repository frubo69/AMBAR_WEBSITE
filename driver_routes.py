"""
AMBAR — приложение водителя.

Кто сюда заходит
----------------
Водитель из ростера (config_staff), чей telegram_id вписан в AMBAR_DRIVER_IDS.
Имени мало: заказ знает водителя по имени, но имя может совпасть, а доступ —
это про конкретный аккаунт. Нет id в списке — нет входа, и это же служит
выдачей и отзывом доступа.

Что он может
------------
  • видеть свои заказы — те, где он назначен водителем;
  • ПОПРОСИТЬ: отметить доставку, изменить состав, отменить заказ, передать
    сообщение. Все четыре — просьбы, а не действия: заказ закрывает, меняет и
    отменяет оператор. Водитель на месте видит, чего не хватает, но решать, что
    везти, почём и считать ли заказ закрытым, — не его работа;
  • записать расход — со статусом «на согласовании», пока менеджер не утвердит.
    В расходы дня такой не попадает: иначе водитель сам себе назначал бы траты.

Просьба живёт на заказе одним полем driver_req: {kind, status, by, at, …}.
Открытая просьба всегда одна — вторая заменяет первую, иначе оператор разбирал
бы очередь из противоречащих друг другу пожеланий.

У водителей свой бот (DRIVER_BOT_TOKEN): подпись initData проверяется его
токеном, а значит вход в приложение водителя невозможен из операторского — и
наоборот. Роли не пересекаются даже случайно.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import parse_qsl

from aiohttp import web

import db
import bizday                 # день заказа = смена, в которой его приняли
import photos
import config_staff as staff
import close_req
from owner_auth import CORS_HEADERS

log = logging.getLogger("driver")

DRIVER_BOT_TOKEN = os.getenv("DRIVER_BOT_TOKEN", "")
INIT_DATA_MAX_AGE = 24 * 3600
DUBAI_TZ = timezone(timedelta(hours=4))
from bizday import SHIFT_START_HOUR      # граница суток одна на всю систему (bizday)

# Смену водитель открывает не позже 15:00 (владелец, 22 сен 2026: «это теперь
# правило»). Открыть позже можно — заказы ждут, — но старшему и операторам
# района сразу уходит оповещение. Сутки начинаются в 10:00, поэтому открытие
# ночью — тоже позже 15:00 этих суток.
SHIFT_LATE_HOUR = int(os.getenv("AMBAR_SHIFT_LATE_HOUR", "15"))


def _biz_day(ref: datetime = None) -> str:
    ref = ref or datetime.now(DUBAI_TZ)
    anchor = ref.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    return (ref if ref >= anchor else ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _valid_init_data(init_data: str, token: str) -> dict | None:
    """Проверка подписи Telegram. Та же схема, что у остальных приложений."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        # Просроченный initData — украденный initData: живой клиент присылает свежий.
        if abs(time.time() - int(pairs.get("auth_date", 0))) > INIT_DATA_MAX_AGE:
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def require_driver(fn):
    """Пускает только того, чей аккаунт вписан в AMBAR_DRIVER_IDS."""
    async def wrapper(request):
        if request.method == "OPTIONS":
            return web.Response(status=200, headers=CORS_HEADERS)
        auth = request.headers.get("Authorization", "")
        init_data = auth[4:] if auth.startswith("tma ") else ""
        user = _valid_init_data(init_data, DRIVER_BOT_TOKEN)
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401, headers=CORS_HEADERS)
        # Водителя могли переставить на другой район — узнаём об этом до того,
        # как отдадим ему заказы: иначе он до перезапуска сервиса возит по
        # старому району.
        try:
            await staff.sync()                    # реестр водителей и перестановки из базы
        except Exception as e:
            log.warning(f"[driver] реестр не прочитан: {e}")
        # Настоящий водитель — по AMBAR_DRIVER_IDS; иначе тест-водитель
        # (config.TEST_DRIVER_IDS): тот же экран, но только тест-заказы. Кто и
        # то и другое — по умолчанию настоящий, тест включает переключатель
        # в профиле приложения (заголовок X-Ambar-Test).
        from config import TEST_DRIVER_IDS as _TD_IDS
        uid = user.get("id")
        me = staff.driver_by_tg(uid)
        if uid in _TD_IDS and (not me or request.headers.get("X-Ambar-Test", "") == "1"):
            me = staff.test_driver(uid, force=True) or me
        if not me:
            log.warning(f"[driver] отказ: tg={user.get('id')} ({user.get('username')}) не в списке")
            return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
        # Вход закрыт сторожем геопозиции (geo_watch): пропала на смене и не
        # вернулась до её конца. Открывает старший, кнопкой под сообщением
        # бота. Проверяем на каждом запросе, а не при запуске: приложение
        # могло быть открыто всю ночь. Тест-водителя сторож не ведёт.
        try:
            lock = None if me.get("test") else await db.geo_lock_get(me["name"])
        except Exception as e:
            log.warning(f"[driver] замок не прочитан: {e}")
            lock = None
        if lock:
            return web.json_response({"error": "geo_locked",
                                      "since": _iso_at(lock.get("locked_at"))},
                                     status=403, headers=CORS_HEADERS)
        request["driver"] = me
        request["tg"] = user
        return await fn(request)
    wrapper.__wrapped__ = fn
    return wrapper


def _tq(me: dict) -> bool:
    """Тест-водитель: заказы берутся с test=True (db.py), настоящие ему не видны."""
    return bool((me or {}).get("test"))


def _no_test(handler):
    """Склад, приёмка и расходы тест-водителю закрыты: там настоящие бутылки
    и настоящие деньги, а тест-режим — про ход заказа."""
    @wraps(handler)
    async def wrapped(request):
        if _tq(request.get("driver")):
            return web.json_response({"error": "test_account"}, status=403, headers=CORS_HEADERS)
        return await handler(request)
    return wrapped


def _is_prepaid(o: dict) -> bool:
    """Заказ уже оплачен — денег с клиента не брать.

    Поле в базе называется не так, как его читали. Криптовый заказ рождается с
    `paid: True` и `payment_method: "crypto"`, а приложение спрашивало `prepaid`
    — которого в документе нет вовсе. Водитель видел «Наличными» на заказе,
    который клиент уже оплатил, и вёз его забирать деньги второй раз.

    Поэтому один ответ на всё приложение и три признака: как только любой из
    них сказал «оплачено», денег не берём."""
    return bool(o.get("prepaid") or o.get("paid")
                or o.get("payment_method") == "crypto")


FX_SYM = {"USD": "$", "EUR": "€", "GBP": "£", "RUB": "₽", "TRY": "₺", "CNY": "¥",
          "KZT": "₸", "UAH": "₴", "INR": "₹", "JPY": "¥", "KRW": "₩", "GEL": "₾",
          "PLN": "zł", "CHF": "₣", "SAR": "SAR"}

# Валюты, которыми берут оплату с клиента, и курс приёма — дирхамов за единицу.
# Фиксированные, не рыночные (владелец, 15 сен 2026: «это те цены, по которым
# мы принимаем от клиентов»). С 19 сен 2026 курс живёт в базе и меняется в STAR
# («Курс валют» → «Наш курс для клиентов», fx_take.py); FX_TAKE — умолчания.
# Курс обменника (rates.py) здесь не участвует.
from fx_take import DEFAULT as FX_TAKE          # noqa: E402


async def fx_take_list() -> list:
    import fx_take
    return [{"code": r["code"], "name": r["name"], "rate": r["rate"],
             "sym": FX_SYM.get(r["code"], r["code"])} for r in await fx_take.rates()]


def _fx_view(o: dict) -> dict | None:
    """Оплата в валюте: сумма считается из итога и курса на момент выбора —
    поправили состав, пересчиталась и она. Курс замораживается при выборе:
    клиент отсчитывает купюры по числу, которое ему назвали."""
    fx = o.get("pay_fx") or None
    if not fx or not fx.get("code") or not fx.get("rate"):
        return None
    try:
        rate, total = float(fx["rate"]), float(o.get("total") or 0)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    return {"code": fx["code"], "name": fx.get("name") or fx["code"],
            "sym": FX_SYM.get(fx["code"], fx["code"]), "rate": rate,
            "amount": round(total / rate, 2), "at": fx.get("at", "")}


def _chat_last(o: dict) -> dict | None:
    """Последнее сообщение оператора по заказу — коротко, для строки на карточке."""
    for m in reversed(o.get("chat") or []):
        if m.get("by") == "operator" and (m.get("text") or "").strip():
            return {"text": str(m.get("text") or "")[:160], "at": m.get("at", ""),
                    "name": m.get("name", "")}
    return None


def _order_view(o: dict) -> dict:
    """Заказ глазами водителя: адрес, состав, сумма.

    Номера клиента здесь нет и быть не может — ни в поле, ни за кнопкой. Это
    строгое правило: телефон клиента водителю не отдаётся. Нужно позвонить —
    звонит оператор."""
    return {
        "order_id": o.get("order_id", ""),
        "status": o.get("status", ""),
        "address": o.get("address", ""),
        "gmap_link": o.get("gmap_link", ""),
        "location": o.get("location") or {},
        "district": o.get("district") or o.get("office_name", ""),
        "customer_name": o.get("customer_name", ""),
        # Состав с id и ценой: без id правка состава теряла все прежние
        # позиции (они уходили на сервер без ключа), без цены экран считал
        # новую сумму только по добавленному.
        "items": [{"id": i.get("id", ""), "name": i.get("name", ""), "qty": i.get("qty", 0),
                   "pcs": i.get("pcs"), "price": i.get("price", 0),
                   "line_total": i.get("line_total", 0), "gift": bool(i.get("gift"))}
                  for i in (o.get("items") or [])],
        "total": int(o.get("total", 0) or 0),
        # Откуда заказ: из приложения — цены со скидкой, по телефону — полные.
        # По этому же признаку водителю показываются цены при добавлении.
        "source": o.get("source") or "app",
        "tip": o.get("tip", 0) or 0,
        "comment": o.get("comment", ""),
        "payment_method": o.get("payment_method", ""),
        # Клиент платит валютой: код, курс на момент выбора, сумма в валюте.
        "pay_fx": _fx_view(o),
        # Последний ответ оператора — на карточку, не открывая разговор.
        "chat_last": _chat_last(o),
        # Оплаченный криптой заказ водитель обязан видеть до выезда: взять
        # наличные там, где уже заплачено, дороже любой ошибки в интерфейсе.
        "prepaid": _is_prepaid(o),
        "timestamp": o.get("timestamp", ""),
        "confirmed_at": o.get("confirmed_at", ""),
        "delivered_at": o.get("delivered_at", ""),
        "deliver_by": o.get("deliver_by", ""),
        # Сколько минут обещали клиенту: водитель — единственный, кто может в
        # них уложиться, и знать их он должен раньше всех.
        "eta": o.get("eta", 0),
        "driver_ack_at": o.get("driver_ack_at", ""),
        # «На месте» нажата — в карточке вместо неё «Доставил». Отметка того,
        # кто везёт сейчас: после переназначения новый водитель жмёт заново.
        "arrived_at": (o.get("driver_arrived_at", "")
                       if o.get("driver_arrived_by") and o.get("driver_arrived_by") == o.get("driver") else ""),
        "driver_req": o.get("driver_req") or o.get("edit_request") or None,
        # Допродажа: что водитель добавил уже в пути и сколько чая за это.
        "upsell": o.get("upsell") or None,
        # Разговор по заказу: сколько всего и есть ли непрочитанное. Саму
        # переписку кладём только в открытый заказ — в списке она не нужна, а
        # весит больше всего остального вместе взятого.
        "chat_n": len(o.get("chat") or []),
        "chat_new": _chat_new(o, "driver"),
        "chat_at": o.get("chat_at", ""),
        # Расчёт наличными: сколько денег реально оказалось в руке. Пока строки
        # нет — заказ считается рассчитанным ровно, и водителю ничего не
        # показывается: обычный случай не должен занимать место на экране.
        "settle": o.get("settle") or None,
    }


def _chat_new(o: dict, side: str) -> int:
    """Сколько сообщений собеседника пришло после того, как эта сторона
    последний раз открывала разговор."""
    seen = str(o.get(f"chat_seen_{side}") or "")
    return sum(1 for m in (o.get("chat") or [])
               if m.get("by") != side and str(m.get("at") or "") > seen)


def _chat_view(o: dict) -> list:
    return [{"by": m.get("by", ""), "name": m.get("name", ""),
             "text": m.get("text", ""), "at": str(m.get("at") or ""),
             "kind": m.get("kind", "")} for m in (o.get("chat") or [])]


# ── Смена водителя ──────────────────────────────────────────────────────────
# Смена начинается не в полдень, а тогда, когда человек её открыл.
#
# Раньше она начиналась по часам: наступило двенадцать — считается, что все
# работают. Из-за этого нельзя было ответить ни на один вопрос про день: во
# сколько человек реально вышел, был ли он на связи, чем кончилась смена.
#
# Открыть смену можно при двух условиях, и оба проверяются не на слово:
#
#   1. Оператор отметил водителя вышедшим. Кто сегодня работает, решает не
#      сам водитель — это же решение определяет питание.
#   2. Идёт живая трансляция геопозиции. Без неё оператор не видит машину, а
#      значит не может ни распределить заказ, ни ответить клиенту «водитель в
#      пяти минутах».
#
# Трансляция телеграма живёт восемь часов, а смена — восемнадцать. Значит за
# ночь её включают два-три раза, и это нормально: смена НЕ закрывается, когда
# трансляция кончилась. Требовать её заново при каждом действии значило бы
# останавливать работу посреди подъезда. Про конец напоминает бот за десять
# минут (geo_nag), а если водитель замолчал надолго — узнаёт оператор.
GEO_FRESH_SEC = 15 * 60         # столько считаем точку свежей при открытии


def _dt_of(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", ""))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _iso_at(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


async def _geo_state(name: str, since=None) -> dict:
    """Что сейчас с геопозицией водителя: идёт ли трансляция и до какого часа.

    since — с какого момента считать стояние (открытие смены, полдень): то,
    что человек стоял дома до смены, в счёт не идёт."""
    rows = await db.driver_pos_all([name])
    r = (rows or [{}])[0] if rows else {}
    now = datetime.now(timezone.utc)
    at, until = _dt_of(r.get("at")), _dt_of(r.get("until"))
    fresh = bool(at and (now - at).total_seconds() < GEO_FRESH_SEC)
    live = bool(until and until > now)
    left = int((until - now).total_seconds() // 60) if live else 0
    # С какой минуты стоит на месте (якорь из db.driver_pos_set). Пропал —
    # только без движения GEO_LOST_SEC подряд: точка раз в несколько минут из
    # кармана — это стоянка, а не пропажа.
    # Трансляция остановлена (сам выключил, удалил чат, заблокировал бота)
    # после последней точки: точка ещё свежая, а связи уже нет.
    stopped_at = _dt_of(r.get("stopped_at"))
    stopped = bool(stopped_at and (not at or stopped_at >= at))
    mv_at = _dt_of(r.get("mv_at")) or at
    since = _dt_of(since)
    if mv_at and since and since > mv_at:
        mv_at = since
    still = int((now - mv_at).total_seconds()) if mv_at else None
    lost = bool(live and still is not None and still >= db.GEO_LOST_SEC)
    # Бессрочная трансляция приходит сроком на десятки лет. Показывать «хватит
    # ещё на 596523 ч» бессмысленно: у неё просто нет конца, так и говорим.
    endless = left > 24 * 60
    return {"ok": fresh and live, "fresh": fresh, "stream": live,
            "until": "" if endless else (_iso_at(until) if until else ""),
            "left_min": 0 if endless else left, "endless": endless,
            "age_sec": int((now - at).total_seconds()) if at else None,
            "still_sec": still, "lost": lost, "stopped": stopped,
            # для сторожа: трансляция идёт. Стоянка — не пропажа: водитель,
            # который стоит, геопозицию не выключал (владелец, 19 сен 2026).
            "watch_ok": live}


def _must_left(d: dict) -> list:
    """Обязательные расходы, по которым водитель ещё не ответил.

    «Ответил» — это либо запись, либо честное «сегодня не было». Пустое место
    ответом не считается: смена, закрытая молча, через неделю не отличается от
    смены, где просто забыли про бензин."""
    no = d.get("no_expense") or {}
    extras = d.get("extras") or []
    return [k for k in MUST_ANSWER
            if not no.get(k) and not any(_kind_of(x) == k for x in extras)]


async def _in_route(me: dict) -> list:
    """Заказы, которые водитель прямо сейчас везёт.

    Смену с ними закрывать нельзя: после закрытия отметить доставку уже не
    получится, и заказ повиснет — ни выручки, ни отказа."""
    day = _biz_day()
    start = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    try:
        orders = await db.get_orders_in_range(f(start), f(start + timedelta(days=1)), test=_tq(me))
    except Exception as e:
        log.warning(f"[driver] заказы в пути не прочитаны: {e}")
        return []
    return [o.get("order_id") for o in orders
            if (o.get("driver") or "").strip() == me["name"]
            and o.get("status") == "approved"]


async def _geo_for(me: dict, since=None) -> dict:
    """Геопозиция для смены. Тест-водителю трансляция из LOCATOR не положена
    (его аккаунт там числится владельцем), поэтому «на связи» для него —
    свежая точка из самого приложения; сторож его не ведёт."""
    geo = await _geo_state(me["name"], since=since)
    if _tq(me):
        alive = bool(geo["fresh"] and not geo.get("stopped"))
        geo.update(ok=alive, stream=alive, watch_ok=alive,
                   lost=False, endless=True, until="", left_min=0)
    return geo


def _as_dt(v):
    """Момент из базы: datetime как есть, строка ISO — в datetime (старые записи)."""
    if hasattr(v, "isoformat"):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def _after_close(me: dict, day: str, d: dict) -> dict | None:
    """Смена закрыта, а новую оператор ещё не открыл — водителю только итоги.

    Владелец, 15 сен 2026: «не давай водителю переоткрыть смену, можно только
    просматривать отчёт, пока оператор не откроет новую смену». Считается по
    событиям, а не по суткам: закрыл вечером, а утром смену района ещё не
    открыли — он всё ещё видит вчерашние итоги. Возвращает {day, closed_at}
    той смены или None, если запрета нет."""
    if d.get("shift_open_at") and not d.get("shift_close_at"):
        return None                                   # смена идёт
    if d.get("shift_close_at"):
        last = {"day": day, "closed_at": d["shift_close_at"]}
    else:
        try:
            last = await db.driver_last_closed(me["name"], before=day)
        except Exception as e:                         # noqa: BLE001
            log.warning(f"[driver] последняя смена {me['name']} не прочиталась: {e}")
            return None
    if not last:
        return None
    at = _as_dt(last["closed_at"])
    try:
        # Тест-водитель в тест-районе: там смену оператор не открывает никогда,
        # поэтому ему хватает новой смены в любом районе — иначе после первого
        # закрытия он был бы заперт навсегда.
        opened = at is None or await db.shift_opened_after(
            at, "" if _tq(me) else (me.get("district") or ""))
    except Exception as e:                             # noqa: BLE001
        log.warning(f"[driver] открытие смены после закрытия не прочиталось: {e}")
        opened = True                                  # не читается — не запираем
    return None if opened else last


# Перемещения смену пока не держат (владелец, 19 сен 2026: «пока что сделай
# так, что водители могут закрыть смену без закрытой заявки на перемещение;
# эту заявку пока оставим в покое»). Замок 18 сен (_moves_left) цел: в
# состоянии смены шага «Отработать перемещения» нет, закрытие его не
# проверяет; вернуть — True. Сами заявки живут как жили — на «Товаре».
MOVES_HOLD_SHIFT = False


async def _moves_left(me: dict) -> list:
    """Незакрытые перемещения района водителя — с обеих сторон.

    Владелец, 18 сен 2026: «не давай им закрыть смену, пока все перемещения не
    отработаны». Перемещение висит на районе, а не на человеке, и держит оба
    района: тот, что отдаёт (side=give — сканирует он), пока не отсканировал
    всё, и тот, что забирает (side=take), пока не нажал «Принял». Иначе
    бутылки остаются лежать не там, где их ждёт заявка."""
    if _tq(me):
        return []
    try:
        import move_routes
        rows = await move_routes.pending_for_district(me.get("district") or "")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] перемещения {me['name']} не прочитаны: {e}")
        return []
    out = []
    for r in rows:
        give = r["side"] == "give"
        out.append({"move_id": r["move_id"], "side": r["side"],
                    # district — ключ задачи (район-получатель), как в запросах.
                    "district": r["district"], "from": r["from"],
                    # С кем передача: кому отдаём — или от кого принимаем.
                    "code": r["to_code"] if give else r["from_code"],
                    "name": r["to_name"] if give else r["from_name"],
                    "status": r["status"], "giver": r["giver"],
                    "need": r["need"], "got": r["got"], "left": r["left"],
                    "positions": r["left_positions"]})
    return out


async def _intake_left(me: dict) -> list:
    """Незавершённые приёмки водителя: взял или начал, но не завершил.

    Смену с ними не закрыть (владелец, 16 сен 2026): закрытая смена и
    неприятый товар района — дыра в учёте. Принятое без сканирования сюда
    не входит: бутылки уже на полке, долг по кодам ведёт чек-лист старшего,
    а держать смену открытой до последнего кода — значит держать её сутками."""
    if _tq(me):
        return []
    try:
        sups = await db.supplies_with_open_tasks(limit=12)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] приёмки {me['name']} не прочитаны: {e}")
        return []
    from config_offices import OFFICE_CODES, OFFICE_NAMES
    out = []
    for sup in sups:
        for oid, t in (sup.get("tasks") or {}).items():
            if (t.get("driver") or "").strip() != me["name"]:
                continue
            if t.get("done_at") or t.get("cancelled_at") or t.get("noscan_at"):
                continue
            # В единицах склада: план и принятое — коробки у пива, коды несут qty.
            import supply_routes as _sr
            need = got = left = 0.0
            for it in sup.get("items") or []:
                n = int((it.get("by_district") or {}).get(oid) or 0)
                if not n:
                    continue
                g = _sr._qn((it.get("got") or {}).get(oid) or 0)
                need += n; got += g; left += _sr._left_units(n, g)
            fix = lambda v: int(v) if v == int(v) else round(v * 2) / 2
            out.append({"sid": sup.get("_id") or sup.get("supply_id") or "", "district": oid,
                        "code": OFFICE_CODES.get(oid, oid), "name": OFFICE_NAMES.get(oid, oid),
                        "need": fix(need), "got": fix(got), "left": fix(left),
                        "started": bool(t.get("started_at"))})
    return out


def _shift_late(day: str, now: datetime) -> bool:
    """Смена открыта позже SHIFT_LATE_HOUR учётных суток day."""
    return now >= bizday.day_start(day).replace(hour=SHIFT_LATE_HOUR, minute=0)


async def _district_open(me: dict, day: str) -> bool:
    """Открыл ли оператор сегодня смену района водителя."""
    try:
        return bool(me.get("district")) and me["district"] in (await db.shift_opens_for_day(day))
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] открытие смены района не прочиталось: {e}")
        return False


def _mde(s: str) -> str:
    """Имя в Markdown-сообщении: служебные знаки — буквами."""
    return re.sub(r"([_*`\[])", r"\\\1", str(s or ""))


async def _late_alert(me: dict, now: datetime) -> None:
    """Смену открыли позже 15:00 — старшему (STAR) и операторам района."""
    import html as _html
    from config_offices import OFFICE_CODES, OFFICE_NAMES
    oid = me.get("district") or ""
    where = f"{OFFICE_CODES.get(oid, '')} {OFFICE_NAMES.get(oid, oid)}".strip()
    at = now.astimezone(bizday.DUBAI_TZ).strftime("%H:%M")
    name = me.get("name") or "—"
    try:
        from owner_routes import notify_owners
        await notify_owners("driver.late_shift",
                            f"⏰ *Поздно открыл смену* — {_mde(name)}, {_mde(where)}\n"
                            f"Открыл в {at}, а правило — до {SHIFT_LATE_HOUR}:00.")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] старшему о поздней смене не ушло: {e}")
    try:
        import op_route
        await op_route.send(f"⏰ <b>Поздно открыл смену</b>: {_html.escape(name)}, {_html.escape(where)} — "
                            f"в {at}. Правило — до {SHIFT_LATE_HOUR}:00.", district=oid, retry_plain=True)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] операторам о поздней смене не ушло: {e}")
    log.info(f"[driver] {name}: смена открыта поздно, в {at}")


async def _shift_view(me: dict) -> dict:
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    # Оператор может открыть смену района, никого не отметив (владелец, 22 сен
    # 2026 — временно, пока не ведём, кто когда уехал): тогда неотмеченный
    # водитель открывает смену сам. Отмеченный «дома» — нет.
    district_open = await _district_open(me, day)
    geo = await _geo_for(me)
    must = [] if _tq(me) else _must_left(d)
    opened, closed = d.get("shift_open_at"), d.get("shift_close_at")
    route = await _in_route(me) if opened and not closed else []
    intake = await _intake_left(me) if opened and not closed else []
    moves = await _moves_left(me) if MOVES_HOLD_SHIFT and opened and not closed else []
    after = await _after_close(me, day, d)
    # День района закрыт оператором — значит заказов сегодня больше не будет, и
    # неотвеченные расходы превращаются из «успею» в «держу всех». Водителю про
    # это надо сказать, а не ждать, пока он сам зайдёт на вкладку.
    закрыт, закрыт_в = False, None
    try:
        doc_ = (await db.shifts_for_day(day)).get(me.get("district") or "") or {}
        закрыт = bool(doc_)
        закрыт_в = doc_.get("closed_at") or doc_.get("at") or doc_.get("ts")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] закрытие дня не прочиталось: {e}")
    return {
        "day": day, "working": True if _tq(me) else d.get("working"), "day_closed": закрыт,
        "day_closed_at": _iso_at(закрыт_в) if закрыт_в else "",
        "opened": bool(opened), "opened_at": _iso_at(opened),
        "closed": bool(closed), "closed_at": _iso_at(closed),
        # Смена закрыта, новой оператор не открывал: «открыть снова» нет,
        # итоги закрытой (report_day) смотреть можно.
        "after_close": bool(after),
        "report_day": after["day"] if after else "",
        "report_closed_at": _iso_at(after["closed_at"]) if after else "",
        "geo": geo, "must": must,
        # Куда включать трансляцию: приложение ведёт в бот геопозиции.
        "geo_bot": await __import__("geo_watch").geo_bot_link(),
        "must_names": [EXPENSE_KINDS.get(k) or k for k in must],
        "in_route": route,
        # Незавершённые приёмки: пока есть — «Закрыть смену» не активна.
        "intake": intake,
        # Неотработанные перемещения района — отдать или забрать. Держат смену
        # так же, как приёмка: закрыть её сервер всё равно не даст.
        "moves": moves,
        "can_open": (d.get("working") is True or _tq(me) or (d.get("working") is None and district_open))
                    and geo["ok"] and not (opened and not closed) and not after,
        "district_open": district_open,
        "late_hour": SHIFT_LATE_HOUR,
        # Смену закрывает сам водитель, когда отдал последний заказ и ответил
        # по расходам. Ждать закрытия дня оператором он не обязан: иначе смена
        # висела бы до утра, а «закрыть» упиралось в чужое действие.
        "can_close": bool(opened) and not closed and not must and not route and not intake
                     and not moves,
        # Просьба закрыть смену раньше оператора (18 сен 2026): текущий запрос,
        # кто его решает и когда можно просить снова. Отпустили — шаг «оператор
        # закрыл смену» пройден так же, как если бы закрыли весь район.
        "close_req": None if _tq(me) else close_req.view(d),
        "released": (d.get("close_req") or {}).get("status") == "ok",
        "operator": (close_req.driver_card(me["name"]).get("operator") or me.get("operator") or ""),
    }


@require_driver
async def handle_profile(request):
    """Профиль водителя: его зарплата за месяц и его же списания. Чужого здесь
    не бывает — имя берём из подписи телеграма, а не из запроса."""
    me = request["driver"]
    month = str(request.query.get("month") or "")[:7]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        month = _biz_day()[:7]
    import finance_routes as fin
    try:
        card = await fin.person_card(me["name"], month)
    except Exception as e:                        # noqa: BLE001
        log.error(f"[driver] профиль {me['name']}: {e}")
        return web.json_response({"error": "book"}, status=500, headers=CORS_HEADERS)
    from config_offices import OFFICE_NAMES, OFFICE_CODES
    card["district"] = me.get("district") or staff.base_district(me["name"]) or ""
    card["district_name"] = OFFICE_NAMES.get(card["district"], "")
    card["code"] = OFFICE_CODES.get(card["district"], "")
    try:
        doc = await db.get_driver_by_name(me["name"]) or {}
    except Exception:                             # noqa: BLE001
        doc = {}
    since = doc.get("created") or doc.get("linked_at") or doc.get("at")
    card["since"] = str(since)[:10] if since else ""
    card["active"] = not bool(doc.get("hidden") or doc.get("blocked"))
    # Когда вышел на работу — по периодам в «Зарплатах» (владелец, 21 сен
    # 2026): «Работает с» — начало текущего периода, а не день, когда водителя
    # завели в систему; уехал — «Не работает». Периодов нет — как было.
    w = card.get("work_now") or {}
    if w.get("set"):
        card["since"] = w.get("since") or card["since"]
        card["active"] = card["active"] and bool(w.get("on"))
    return web.json_response(card, headers=CORS_HEADERS)


@require_driver
async def handle_shift(request):
    return web.json_response(await _shift_view(request["driver"]), headers=CORS_HEADERS)


async def _shift_summary(me: dict, day: str | None = None) -> dict:
    """Итоги смены перед закрытием (владелец, 14 сен 2026): сколько наличных
    должно быть на руках, отдельно и заметно — чай, который операторы
    заработали с заказов, и остальное по смене одной страницей. Считается с
    тех же заказов и записей, что и другие экраны: второй арифметики рядом с
    первой быть не должно.

    Наличные на руках = взято наличными за заказы (по расчёту, если
    рассчитывались не ровно) − расходы дня + приход (вернули/должны). Траты
    и приход безналом наличных не трогают — в «на руках» их нет, они
    отдельно (spent_card, got_card).

    day — за какой день: после закрытия водитель смотрит итоги закрытой смены,
    пока оператор не открыл новую, а сутки к утру уже могли смениться."""
    day = day or _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    since, until = bizday.window_utc(day, day)
    try:
        orders = await db.get_orders_in_range(since, until, test=_tq(me))
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] итоги смены {me['name']}: заказы не прочитаны: {e}")
        orders = []
    mine = [o for o in orders if (o.get("driver") or "").strip() == me["name"]
            and o.get("status") == "delivered" and bizday.order_day(o) == day]
    pay = {k: {"n": 0, "aed": 0} for k in ("cash", "app", "crypto", "debt", "free")}
    cash_taken = 0.0
    tips = tips_cash = 0
    by_op: dict = {}
    for o in mine:
        total = int(o.get("total") or 0)
        tip = int(o.get("tip") or 0)
        m = str(o.get("payment_method") or "").lower()
        if m == "free":
            pay["free"]["n"] += 1; pay["free"]["aed"] += total
            continue
        if m == "debt":
            k = "debt"
        elif m == "crypto" or o.get("crypto_paid"):
            k = "crypto"
        elif _is_prepaid(o):
            k = "app"
        else:
            k = "cash"
            s = o.get("settle") or {}
            try:
                taken = float(s["taken"]) if s.get("taken") is not None else float(total)
            except (TypeError, ValueError):
                taken = float(total)
            cash_taken += taken
            tips_cash += tip
        pay[k]["n"] += 1; pay[k]["aed"] += total
        tips += tip
        who = staff.base_operator(o.get("office_id") or "") or "Оператор"
        by_op[who] = by_op.get(who, 0) + tip
    extras = [x for x in (d.get("extras") or []) if (x.get("status") or "approved") != "rejected"]
    for x in extras:
        x["kind"] = _kind_of(x)
    plus_ = lambda x: bool(EXTRA_KINDS.get(x["kind"], {}).get("plus"))
    # Записи «мимо наличных» (заказ в долг) в пачки не идут — ни в наличные,
    # ни в безнал: денег по такому заказу не брали.
    наличн = [x for x in extras if not x.get("nocash")]
    spent = sum(int(x.get("amount") or 0) for x in наличн if not plus_(x) and not is_card(x))
    got = sum(int(x.get("amount") or 0) for x in наличн if plus_(x) and not is_card(x))
    spent_card = sum(int(x.get("amount") or 0) for x in наличн if not plus_(x) and is_card(x))
    got_card = sum(int(x.get("amount") or 0) for x in наличн if plus_(x) and is_card(x))
    # В «Расходах» итогов — только то, что двигало деньги. Заказ в долг деньги
    # не двигал: строкой среди расходов он ломал сложение («итого» его не
    # считает) и пугал водителя лишней суммой. Он объяснён там, где ему место,
    # — среди заказов (владелец, 21 сен 2026).
    by_kind = [{"id": k, "t": v["t"], "plus": bool(v.get("plus")),
                "aed": sum(int(x.get("amount") or 0) for x in наличн if x["kind"] == k),
                "n": sum(1 for x in наличн if x["kind"] == k)}
               for k, v in EXTRA_KINDS.items()]
    by_kind = [r for r in by_kind if r["n"]]
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(
            hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ).astimezone(timezone.utc)
        wos = await db.writeoff_list(since=start, by=me["name"], limit=60)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] итоги смены {me['name']}: списания не прочитаны: {e}")
        wos = []
    wos = [w for w in wos if (w.get("state") or "ok") != "no"]
    gross = sum(v["aed"] for k, v in pay.items() if k != "free")
    # Две пачки (владелец, 19 сен 2026): сдать выручку и чай операторов
    # порознь, валюту — как есть; своё (питание, бонус за допродажу) — у
    # водителя. Та же арифметика, что в «Сборе выручки» у старшего.
    import cash_math
    hand = cash_math.piles(mine, extras, staff.meal_of(d) if d.get("working") is not None else 0)
    tea_by: dict = {}
    for o in mine:
        if not cash_math.pays_cash(o):
            continue
        t = cash_math.order_tea(o)
        if t:
            who = (o.get("created_by_name") if o.get("source") == "manual" else "") or "из приложения"
            tea_by[who] = tea_by.get(who, 0) + t
    # Бонус за допродажу: 5% от добавленного водителем в пути (по доставленным).
    ups = [o.get("upsell") for o in mine if o.get("upsell")]
    upsell = {"bonus": sum(int(u.get("bonus") or 0) for u in ups),
              "aed": round(sum(float(u.get("aed") or 0) for u in ups), 2),
              "n": sum(int(l.get("qty") or 0) for u in ups for l in (u.get("lines") or [])),
              "orders": len(ups)}
    return {
        "day": day, "opened_at": _iso_at(d.get("shift_open_at")),
        "upsell": upsell,
        "closed_at": _iso_at(d.get("shift_close_at")),
        "on_hand": int(round(cash_taken - spent + got)),
        "cash_taken": int(round(cash_taken)), "spent": spent, "got": got,
        "spent_card": spent_card, "got_card": got_card,
        "hand": {**hand, "tea_by": [{"who": w, "aed": a} for w, a in sorted(tea_by.items(), key=lambda x: -x[1])]},
        "tips": tips, "tips_cash": tips_cash, "tips_other": tips - tips_cash,
        "tips_by": [{"who": w, "aed": a} for w, a in sorted(by_op.items(), key=lambda x: -x[1]) if a],
        "orders": sum(v["n"] for k, v in pay.items() if k != "free"), "gross": gross,
        "pay": pay,
        "expenses": by_kind, "exp_n": len(наличн),
        "exp_pending": sum(1 for x in наличн if (x.get("status") or "approved") == "pending"),
        "writeoffs": len(wos), "writeoff_qty": sum(float(w.get("qty") or 0) for w in wos),
    }


@require_driver
async def handle_shift_summary(request):
    """Итоги смены. ?day= — за другой день, но не из будущего: после
    закрытия смены водитель смотрит её итоги и назавтра, пока оператор не
    открыл новую. Чужого здесь не бывает — имя из подписи телеграма."""
    day = str(request.query.get("day") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day > _biz_day():
        day = ""
    return web.json_response(await _shift_summary(request["driver"], day or None),
                             headers=CORS_HEADERS)


@require_driver
async def handle_shift_open(request):
    """Открыть смену: отметка оператора плюс живая трансляция."""
    me = request["driver"]
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    if d.get("shift_open_at") and not d.get("shift_close_at"):
        return web.json_response({"error": "already_open"}, status=409, headers=CORS_HEADERS)
    # Закрытую смену заново не открыть, пока оператор не откроет следующую
    # (владелец, 15 сен 2026). Проверка на сервере: кнопки в приложении нет,
    # но старая версия приложения или прямой запрос её бы обошли.
    after = await _after_close(me, day, d)
    if after:
        return web.json_response({"error": "after_close", "day": after["day"]},
                                 status=409, headers=CORS_HEADERS)
    self_mark = False
    if d.get("working") is not True and not _tq(me):
        # Отметка оператора больше не обязательна (владелец, 22 сен 2026,
        # временно): район открыт, а водитель не отмечен — открывает смену сам,
        # и это и есть его выход (питание рабочего дня). Отмечен «дома» — нет.
        if d.get("working") is False:
            return web.json_response({"error": "marked_off"}, status=409, headers=CORS_HEADERS)
        if not await _district_open(me, day):
            return web.json_response({"error": "not_marked"}, status=409, headers=CORS_HEADERS)
        self_mark = True
    geo = await _geo_for(me)
    if not geo["ok"]:
        return web.json_response({"error": "no_geo", "geo": geo},
                                 status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc)
    await db.save_driver_day(day, me["name"], {
        "shift_open_at": now, "shift_close_at": None,
        # Тест-водителя на смену никто не отмечает — отмечается сам; метка test
        # держит его день подальше от отчётов.
        **({"working": True, "test": True} if _tq(me) else {}),
        **({"working": True, "self_marked": True} if self_mark else {})})
    if self_mark:
        try:
            await db.shift_crew_add(day, me.get("district") or "", me["name"])
        except Exception as e:                               # noqa: BLE001
            log.warning(f"[driver] {me['name']}: в бригаду района не записан: {e}")
    log.info(f"[driver] {me['name']}: смена открыта · трансляция ещё {geo['left_min']} мин"
             + (" · без отметки оператора" if self_mark else ""))
    if not _tq(me) and _shift_late(day, now):
        await _late_alert(me, now)
    return web.json_response(await _shift_view(me), headers=CORS_HEADERS)


@require_driver
async def handle_shift_close(request):
    """Закрыть смену. Пока обязательные расходы без ответа — нельзя.

    Это не бюрократия: «сегодня не заправлялся» — такой же ответ, как чек на
    двести дирхам, и он занимает одно нажатие."""
    me = request["driver"]
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    if not d.get("shift_open_at"):
        return web.json_response({"error": "not_open"}, status=409, headers=CORS_HEADERS)
    if d.get("shift_close_at"):
        return web.json_response({"error": "already_closed"}, status=409, headers=CORS_HEADERS)
    must = [] if _tq(me) else _must_left(d)
    if must:
        return web.json_response({"error": "expenses_left", "must": must},
                                 status=409, headers=CORS_HEADERS)
    route = await _in_route(me)
    if route:
        return web.json_response({"error": "orders_in_route", "ids": route},
                                 status=409, headers=CORS_HEADERS)
    # Приёмка взята или начата, но не завершена — сначала она (владелец,
    # 16 сен 2026). Завершить или отдать задачу — в самой задаче.
    intake = await _intake_left(me)
    if intake:
        return web.json_response({"error": "intake_open", "tasks": intake},
                                 status=409, headers=CORS_HEADERS)
    # Перемещения района — так же, как приёмка: закрытая смена с неувезёнными
    # бутылками означает, что завтра заявка снова попросит их купить. Пока
    # выключено (MOVES_HOLD_SHIFT, 19 сен 2026).
    moves = await _moves_left(me) if MOVES_HOLD_SHIFT else []
    if moves:
        return web.json_response({"error": "moves_open", "tasks": moves},
                                 status=409, headers=CORS_HEADERS)
    # Сначала смену района закрывает оператор, и только потом — водитель свою
    # (владелец, 13 сен 2026: «третьим шагом должно быть оператор закрыл смену,
    # и уже четвёртым — водитель закрывает»). До этого ждать не требовали.
    try:
        день_закрыт = bool((await db.shifts_for_day(day)).get(me.get("district") or ""))
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] закрытие дня не прочиталось: {e}")
        день_закрыт = False
    # Или оператор района отпустил этого водителя раньше (18 сен 2026) — тогда
    # смена района идёт дальше, а он свою закрывает.
    отпущен = (d.get("close_req") or {}).get("status") == "ok"
    if not день_закрыт and not отпущен and not _tq(me):
        return web.json_response({"error": "day_open"}, status=409, headers=CORS_HEADERS)
    await db.save_driver_day(day, me["name"], {"shift_close_at": datetime.now(timezone.utc)})
    log.info(f"[driver] {me['name']}: смена закрыта")
    return web.json_response(await _shift_view(me), headers=CORS_HEADERS)


def needs_shift(handler):
    """Не пускать к работе, пока смена не открыта."""
    @wraps(handler)
    async def wrapped(request):
        me = request.get("driver") or {}
        day = _biz_day()
        # День, в который запрет появился, не запираем: водитель, который
        # сейчас стоит у двери клиента, не должен упереться в новый экран
        # из-за того, что обновление вышло посреди его смены.
        try:
            if day <= await db.driver_gate_since(day):
                return await handler(request)
        except Exception as e:
            log.warning(f"[driver] начало запрета не прочитано: {e}")
        d = await db.get_driver_day(day, me.get("name") or "") or {}
        if not d.get("shift_open_at") or d.get("shift_close_at"):
            return web.json_response({"error": "shift_closed"}, status=409,
                                     headers=CORS_HEADERS)
        return await handler(request)
    return wrapped


@require_driver
async def handle_ping(request):
    me = request["driver"]
    # Приложение сообщает, что у него с геопозицией. Без этого причина
    # «почему точка не уходит» остаётся догадкой: версия телеграма живёт на
    # телефоне, и с сервера её не видно никак. Пишем строкой в журнал — ни в
    # какую базу это не ложится и никого не касается, кроме отладки.
    q = request.query
    if q.get("v") or q.get("lm"):
        log.info(f"[driver] {me['name']}: телеграм {q.get('v','?')} · "
                 f"геопозиция {q.get('lm','?')}")
    # Скрытый режим переживает закрытие приложения и смену телефона: он живёт
    # на сервере, а не во вкладке браузера. Иначе достаточно было бы выгрузить
    # приложение из памяти, чтобы маскировка слетела в самый неподходящий момент.
    return web.json_response({
        "ok": True,
        "driver": {**{k: me[k] for k in ("id", "name", "district", "district_code",
                                         "district_name", "operator")},
                   "test": _tq(me),
                   # Тестер, который заодно настоящий водитель, переключает режим сам.
                   "test_allowed": (request.get("tg") or {}).get("id") in
                                   __import__("config").TEST_DRIVER_IDS},
        "day": _biz_day(),
        "panic": bool(await db.panic_get(me["name"])),
    }, headers=CORS_HEADERS)


@require_driver
@needs_shift
async def handle_settle(request):
    """Рассчитались не ровно: водитель говорит, сколько денег у него в руке.

    Одно поле вместо двух кнопок «дал сдачу» и «остался должен». Это не
    бухгалтерская абстракция, а физика: водитель считает не долг, а деньги, и
    вводит ровно то, что видит. Разницу с суммой заказа считаем сами.

      взял больше суммы — сдачи не нашлось, мы должны клиенту;
      взял меньше      — клиент остался должен.

    Долг у клиента один, со знаком: плюс — должен он, минус — должны мы. Так
    не нужно ни второй сущности, ни второго экрана, ни второго журнала, а
    зачесть одно другим можно просто сложением.

    Правку повторного расчёта считаем от прошлой: водитель может ошибиться и
    ввести заново, и второе число должно заменить первое, а не удвоить его."""
    me = request["driver"]
    oid = (request.match_info.get("oid") or "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)
    if _is_prepaid(o) or o.get("payment_method") in ("debt", "transfer", "free"):
        # Там, где деньги не идут через руки водителя, и расчёта быть не может.
        return web.json_response({"error": "not_cash"}, status=400, headers=CORS_HEADERS)
    try:
        # Взяли валютой — сумма в дирхамах приходит ниже, из fx.
        taken = round(float(body.get("taken")), 2) if body.get("taken") is not None \
            else (0.0 if isinstance(body.get("fx"), dict) else round(float(None), 2))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)
    total = int(o.get("total") or 0)
    # Взяли валютой: сумма приходит в ней, в дирхамы переводим по курсу
    # заказа (зафиксирован при выборе валюты) — тем же, что назвали клиенту.
    fx_in = body.get("fx") if isinstance(body.get("fx"), dict) else None
    fx_rec = None
    if fx_in and fx_in.get("code"):
        pf = o.get("pay_fx") or {}
        try:
            rate = float(pf.get("rate") or 0) if pf.get("code") == fx_in.get("code") else 0.0
            amount = round(float(fx_in.get("amount")), 2)
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)
        if rate <= 0:
            return web.json_response({"error": "no_fx"}, status=400, headers=CORS_HEADERS)
        if amount < 0:
            return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)
        taken = round(amount * rate, 2)
        fx_rec = {"code": fx_in["code"], "amount": amount, "rate": rate,
                  "sym": FX_SYM.get(fx_in["code"], fx_in["code"])}
    if not (0 <= taken <= total + 100000):
        return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)

    diff = round(taken - total, 2)
    prev = round(float((o.get("settle") or {}).get("diff") or 0), 2)
    now = datetime.now(timezone.utc)
    await db.update_order(oid, settle={
        "taken": taken, "diff": diff, "by": me["name"], "at": now.isoformat(),
        **({"fx": fx_rec} if fx_rec else {})})

    cid = int(o.get("customer_id") or 0)
    if cid and diff != prev and not o.get("test"):
        # Знак: взяли больше — уходим в минус, это наш долг клиенту.
        # По тест-заказу долг не пишем: деньги в нём не настоящие.
        await db.add_debt(cid, -(diff - prev), order_id=oid,
                          note=f"расчёт наличными · {me['name']}")
    log.info(f"[driver] {me['name']}: заказ {oid} — взято {taken} из {total} "
             f"(разница {diff:+})")
    return web.json_response({"ok": True, "diff": diff,
                              "debt": (await db.get_debt(cid)) if cid else 0},
                             headers=CORS_HEADERS)


@require_driver
@needs_shift
async def handle_debt_settle(request):
    """Водитель вернул клиенту то, что мы были должны.

    Долг закрывается там же, где возник, — на адресе. Иначе он висит до тех
    пор, пока о нём вспомнит владелец, а клиент вспоминает раньше."""
    me = request["driver"]
    oid = (request.match_info.get("oid") or "").strip()
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)
    if o.get("test"):
        return web.json_response({"error": "test_account"}, status=403, headers=CORS_HEADERS)
    cid = int(o.get("customer_id") or 0)
    if not cid:
        return web.json_response({"error": "no_customer"}, status=400, headers=CORS_HEADERS)
    debt = await db.get_debt(cid)
    if debt >= 0:
        return web.json_response({"error": "nothing_owed", "debt": debt},
                                 status=400, headers=CORS_HEADERS)
    await db.add_debt(cid, -debt, order_id=oid, note=f"вернул водитель · {me['name']}")
    log.info(f"[driver] {me['name']}: вернул клиенту {-debt} по заказу {oid}")
    return web.json_response({"ok": True, "returned": -debt, "debt": 0.0},
                             headers=CORS_HEADERS)


def _needs_settle(o: dict) -> bool:
    """Брал ли водитель наличные по этому заказу."""
    return not (_is_prepaid(o) or o.get("payment_method") in ("debt", "crypto", "transfer", "free"))


# ── Списания ────────────────────────────────────────────────────────────────
def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


# Пределы общие с чеками: см. photos. Держать их порознь было нечем — снимок
# с той же камеры, а разойдясь, они означали бы, что бой можно снять, а чек нет.
WO_MAX_PHOTO = photos.MAX_PHOTO
WO_MAX_THUMB = photos.MAX_THUMB  # превью в самой записи: список должен открываться
WO_MAX_QTY = 240                # ящик пива это 24; больше похоже на опечатку


@require_driver
@_no_test
@needs_shift
async def handle_writeoff_add(request):
    """Списать товар: бой, брак, просрочка, потеря.

    Фотография обязательна. Это единственное, что отделяет списание от способа
    закрыть недостачу: рассказать можно что угодно, показать разбитую бутылку —
    только разбив её. Поэтому нет ни одной ветки, где запись проходит без фото.

    Списываем на район водителя: товар лежал на его полке, и вычесть его надо
    из его же остатка, иначе заявка привезёт туда лишнее."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    from operator_routes import _load_catalog
    cat = {p.get("id"): p for p in _load_catalog()}
    pid = (body.get("item") or "").strip()
    if pid not in cat:
        return web.json_response({"error": "no_item"}, status=400, headers=CORS_HEADERS)
    try:
        from stock_routes import _round_step, _num
        qty = _num(_round_step(float(str(body.get("qty") or 0).replace(",", "."))))
    except (TypeError, ValueError):
        qty = 0
    if not (0.5 <= qty <= WO_MAX_QTY):
        return web.json_response({"error": "bad_qty"}, status=400, headers=CORS_HEADERS)
    kind = (body.get("kind") or "").strip()
    if kind not in db.WRITEOFF_KINDS:
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)

    raw = (body.get("photo") or "")
    if "," in raw[:64]:
        raw = raw.split(",", 1)[1]
    if len(raw) > WO_MAX_PHOTO:
        return web.json_response({"error": "photo_big"}, status=400, headers=CORS_HEADERS)
    try:
        import base64
        photo = base64.b64decode(raw, validate=True) if raw else b""
    except Exception:
        photo = b""
    # Проверяем не длину строки, а начало файла: пустая или битая картинка
    # ничего не доказывает, а выглядит в истории точно так же.
    # Та же логика, что у старшего: снимок нужен там, где он что-то
    # доказывает. Потеря — это отсутствие предмета, снимать нечего; она всё
    # равно уйдёт на согласование, и решать будут по слову и остатку.
    if len(photo) < 2000 or photo[:2] not in (b"\xff\xd8", b"\x89P"):
        if kind != "потеря" or raw:
            return web.json_response({"error": "no_photo"}, status=400, headers=CORS_HEADERS)
        photo = b""

    # Превью лежит в самой записи: иначе список списаний открывается пустыми
    # квадратами, а ради квадратов раздел никто открывать не станет.
    thumb = (body.get("thumb") or "")
    if not thumb.startswith("data:image/") or len(thumb) > WO_MAX_THUMB:
        thumb = ""

    now = datetime.now(timezone.utc)
    wid = await db.writeoff_add({
        "at": now, "day": _biz_day(), "item": pid, "thumb": thumb,
        "name": cat[pid].get("name", ""), "qty": qty, "kind": kind,
        "note": (body.get("note") or "").strip()[:200],
        "district": me.get("district") or "", "district_code": me.get("district_code") or "",
        # telegram_id, не id: id водителя — это его ключ-слово («hudoba»),
        # и int() на нём ронял запрос целиком — «Не отправилось».
        "by": me["name"], "by_id": int(me.get("telegram_id") or 0),
        "supply_id": (body.get("supply_id") or "").strip()[:40],
    }, photo)
    try:
        import stock_routes
        stock_routes.base_drop()          # заявка должна узнать об этом сразу
    except Exception as e:
        log.warning(f"[writeoff] кэш заявки не сброшен: {e}")
    log.info(f"[writeoff] {me['name']} ({me.get('district_code','')}): "
             f"{kind} · {cat[pid].get('name','')} × {qty} · ждёт согласования")
    await _writeoff_tell(wid, me, cat[pid], qty, kind,
                         (body.get("note") or "").strip()[:200], photo)
    return web.json_response({"ok": True, "id": wid, "state": "pending"},
                             headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_writeoff_scan(request):
    """Списать бутылку по коду с крышки — как у старшего, но в ожидании
    решения. body: {code, kind, note?, photo, thumb?}

    Код знает позицию, район и что это ровно одна бутылка; водителю остаётся
    сказать, что случилось, и показать. В реестре бутылка при скане НЕ
    помечается: до согласования списание — заявление, а не факт, и остаток
    трогать нельзя. Помечает её решение (db.writeoff_decide). Утеря сюда не
    ходит: потерянную бутылку к камере не поднесёшь."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str(body.get("code") or "").strip()
    kind = str(body.get("kind") or "").strip()
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    if kind not in db.WRITEOFF_KINDS or kind == "потеря":
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)
    from config_offices import OFFICE_IDS, OFFICE_CODES
    from stock_routes import WO_SAY, _wo_say
    doc = await db.qr_get(code)
    if not doc:
        return _wo_say("unknown", code=code)
    name = doc.get("product_name") or ""
    st = (doc.get("status") or "active").strip()
    if st == "written":
        return _wo_say("already", code=code, name=name)
    if st != "active":
        return _wo_say(st if st in WO_SAY else "gone", code=code, name=name)
    if await db.writeoff_pending_by_code(code):
        return _wo_say("already", code=code, name=name)
    from operator_routes import _load_catalog
    cat = {p.get("id"): p for p in _load_catalog()}
    pid = str(doc.get("product_id") or "")
    p = cat.get(pid)
    if not p:
        return _wo_say("no_item", code=code, name=name)
    district = (doc.get("district") or "").strip()
    if district not in OFFICE_IDS:
        return _wo_say("nohome", code=code, name=p.get("name", ""))

    raw = (body.get("photo") or "")
    if "," in raw[:64]:
        raw = raw.split(",", 1)[1]
    if len(raw) > WO_MAX_PHOTO:
        return web.json_response({"error": "photo_big"}, status=400, headers=CORS_HEADERS)
    try:
        import base64
        photo = base64.b64decode(raw, validate=True) if raw else b""
    except Exception:
        photo = b""
    if len(photo) < 2000 or photo[:2] not in (b"\xff\xd8", b"\x89P"):
        return web.json_response({"error": "no_photo"}, status=400, headers=CORS_HEADERS)
    thumb = (body.get("thumb") or "")
    if not thumb.startswith("data:image/") or len(thumb) > WO_MAX_THUMB:
        thumb = ""
    note = (body.get("note") or "").strip()[:200]
    now = datetime.now(timezone.utc)
    wid = await db.writeoff_add({
        "at": now, "day": _biz_day(), "item": pid, "thumb": thumb,
        "name": p.get("name", ""), "qty": 1, "kind": kind, "note": note,
        "district": district, "district_code": OFFICE_CODES.get(district, ""),
        "by": me["name"], "by_id": int(me.get("telegram_id") or 0),
        "code": code, "label": doc.get("label") or "",
    }, photo)
    try:
        import stock_routes
        stock_routes.base_drop()
    except Exception as e:
        log.warning(f"[writeoff] кэш заявки не сброшен: {e}")
    log.info(f"[writeoff] {me['name']} сканом: {kind} · {p.get('name','')} · "
             f"{OFFICE_CODES.get(district, district)} · ждёт согласования")
    await _writeoff_tell(wid, me, p, 1, kind, note, photo)
    return _wo_say("ok", code=code, id=wid, name=p.get("name", ""),
                   label=doc.get("label") or "", district=district,
                   district_code=OFFICE_CODES.get(district, ""), state="pending")


async def _writeoff_tell(wid: str, me: dict, p: dict, qty: int, kind: str,
                         note: str, photo: bytes):
    """Владельцу — сразу, и сразу же снимком с двумя кнопками.

    Списание ждёт его решения: до «Согласовать» товар со склада не вычитается.
    Значит сообщение — не новость, а вопрос, и отвечать на него надо там же,
    где он задан. Гонять владельца в приложение ради двух кнопок под
    фотографией, которую он и так видит, — лишний шаг в единственном месте,
    где решение занимает секунду.

    Снимок здесь тот же, что ушёл в базу: он и есть всё доказательство, и
    пересказ его словами ничего не решает."""
    try:
        import writeoff_msg as wm
        from owner_routes import notify_owners_photo
        # Цена продажная, «по прайсу»: закупочной система не знает, а выдумывать
        # себестоимость в сообщении о убытке — врать в цифре, по которой примут
        # решение. Делим на единицу учёта: списывают бутылки, а пиво в прайсе
        # стоит ящиком.
        price = int(p.get("price_24_full") or p.get("price_full") or p.get("price") or 0)
        unit = 24 if p.get("price_24_full") or p.get("price_12_full") else 1
        aed = round(price / max(1, unit) * qty)
        cap = wm.caption(p.get("name", ""), qty, kind, me["name"],
                         me.get("district_code") or "", note, aed)
        sent = await notify_owners_photo("stock.writeoff", cap, photo,
                                         reply_markup=wm.keyboard(wid))
        # Запоминаем, куда ушло: когда один владелец решит, у остальных надо
        # снять кнопки — иначе второй жмёт по решённому и упирается в отказ.
        if sent:
            await db.writeoff_note_msg(wid, sent)
        else:
            # Снимок не ушёл — телеграм отказал, картинка не открылась, что
            # угодно. Вопрос всё равно должен дойти: несогласованное списание
            # висит и держит товар на полке, и молчать о нём нельзя. Без
            # кнопок — решать придётся в приложении.
            from owner_routes import notify_owners
            await notify_owners(
                "stock.writeoff",
                f"🗑 Списание · {kind}\n{p.get('name','')} × {qty}"
                f" · {aed} AED\n{me['name']} ({me.get('district_code') or '—'})"
                + (f"\n{note}" if note else "")
                + "\n\nЖдёт согласования в панели — со склада не вычтено.",
                parse_mode=None)
    except Exception as e:
        log.warning(f"[writeoff] владельцу не ушло: {e}")


# ── перемещение бутылок ──────────────────────────────────────────────────────
# Водитель перевозит бутылки между районами так же, как старший: код с
# крышки, район назначения, одна крышка — один переезд в обе книги (реестр +
# stock_transfers). Отменить может только свой переезд; старший — любой.

@require_driver
@_no_test
async def handle_move_scan(request):
    """{code, to} → вердикт как у старшего (ok / same / unknown / …)."""
    import stock_routes
    from config_offices import OFFICE_IDS
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = re.sub(r"\s+", "", str(body.get("code") or ""))[:120]
    dst = str(body.get("to") or "").strip()
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    if dst not in OFFICE_IDS:
        return web.json_response({"error": "bad_district"}, status=400, headers=CORS_HEADERS)
    res = await stock_routes.move_by_code(code, dst, request["tg"].get("id") or 0,
                                         me["name"], "driver")
    return web.json_response(res, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_code_info(request):
    """?code= → что за бутылка, до переезда: название и фото товара из каталога,
    где числится, когда записана, сколько раз ездила. Водитель сначала видит,
    что везёт, и только потом выбирает куда. Telegram-id не отдаём."""
    import stock_routes
    from config_offices import OFFICE_IDS, OFFICE_CODES, OFFICE_NAMES
    code = re.sub(r"\s+", "", str(request.query.get("code") or ""))[:120]
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    districts = [{"id": o, "code": OFFICE_CODES.get(o, ""), "name": OFFICE_NAMES.get(o, "")} for o in OFFICE_IDS]
    doc = await db.qr_get(code)
    if not doc:
        return web.json_response({"ok": False, "verdict": "unknown", "say": stock_routes.MOVE_SAY["unknown"],
                                  "code": code, "districts": districts}, headers=CORS_HEADERS)
    st = (doc.get("status") or "active").strip()
    pid = str(doc.get("product_id") or "")
    p = stock_routes._catalog().get(pid) or {}
    src = (doc.get("district") or "").strip()
    moves = doc.get("moves") or []
    last = moves[-1] if moves else None
    verdict = "ok" if st == "active" else st
    if verdict == "ok" and src not in OFFICE_IDS:
        verdict = "nohome"
    if verdict == "ok" and not p:
        verdict = "no_item"
    return web.json_response({
        "ok": verdict == "ok", "verdict": verdict, "say": stock_routes.MOVE_SAY.get(verdict, ""),
        "code": code, "label": doc.get("label") or "",
        "name": p.get("name") or doc.get("product_name") or "",
        "img": p.get("img") or "", "cat": p.get("cat") or "",
        "price": stock_routes._price(p) if p else 0,
        "district": {"id": src, "code": OFFICE_CODES.get(src, ""), "name": OFFICE_NAMES.get(src, "")},
        # Даты из Mongo — datetime, JSON их не берёт: на каждой НАШЕЙ бутылке
        # ручка падала в 500, а чужой код (без документа) проходил (14 сен 2026).
        "at": _iso_at(doc.get("at")), "moves": len(moves),
        "last_move": ({"from_code": OFFICE_CODES.get(str(last.get("from") or ""), ""),
                       "to_code": OFFICE_CODES.get(str(last.get("to") or ""), ""),
                       "at": _iso_at(last.get("at"))} if last else None),
        "districts": districts,
    }, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_move_undo(request):
    """{code} — вернуть бутылку: только если последний переезд — мой."""
    import stock_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = re.sub(r"\s+", "", str(body.get("code") or ""))[:120]
    if not code:
        return web.json_response({"error": "no_code"}, status=400, headers=CORS_HEADERS)
    st, payload = await stock_routes.move_undo_by_code(code, only_driver=me["name"])
    return web.json_response(payload, status=st, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_move_del(request):
    """Убрать строку своей истории: бутылка едет обратно в обе книги."""
    me = request["driver"]
    tid = (request.match_info.get("tid") or "").strip()
    doc = await db.get_stock_transfer(tid)
    if not doc:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if doc.get("by_kind") != "driver" or (doc.get("by_name") or "") != me["name"]:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)
    if (doc.get("src") or "") == "qr" and doc.get("code"):
        back = await db.qr_move_undo(str(doc["code"]), tid)
        if not back:
            # Бутылку после меня перевёз кто-то ещё — мой переезд уже история,
            # возвращать её «ко мне» значило бы перечеркнуть чужой.
            return web.json_response({"error": "moved_on"}, status=409, headers=CORS_HEADERS)
    ok = await db.delete_stock_transfer(tid)
    if ok:
        import stock_routes
        stock_routes.base_drop()         # бутылка вернулась — склад видит сразу
    return web.json_response({"ok": ok}, status=200 if ok else 404, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_moves(request):
    """Мои переезды за неделю, сгруппированные как у старшего, плюс районы —
    их список водителю нужен, чтобы выбрать, куда везёт."""
    import stock_routes
    from config_offices import OFFICE_IDS, OFFICE_NAMES, OFFICE_CODES
    me = request["driver"]
    since = (datetime.strptime(stock_routes._biz_day(), "%Y-%m-%d") - timedelta(days=6))
    rows = await db.get_stock_transfers_by_driver(me["name"], since.strftime("%Y-%m-%d"))
    out = stock_routes.group_transfers(rows, days=7)
    return web.json_response(
        {"day": stock_routes._biz_day(), "rows": out,
         "districts": [{"id": o, "code": OFFICE_CODES.get(o, ""), "name": OFFICE_NAMES.get(o, o)}
                       for o in OFFICE_IDS]},
        headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


def _comp_share(comp: dict, name: str) -> int:
    if not comp.get("amount"):
        return 0
    split = comp.get("split") or []
    if split:
        return next((int(x.get("amount") or 0) for x in split if (x.get("who") or "") == name), 0)
    return int(comp.get("amount") or 0)


@require_driver
async def handle_writeoffs(request):
    """Мои списания за сегодня — чтобы видеть, что запись прошла."""
    me = request["driver"]
    day = _biz_day()
    start = datetime.strptime(day, "%Y-%m-%d").replace(
        hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ).astimezone(timezone.utc)
    rows = await db.writeoff_list(since=start, by=me["name"], limit=60)
    return web.json_response({"day": day, "rows": [{
        "id": r.get("_id"), "item": r.get("item"), "name": r.get("name", ""),
        "qty": int(r.get("qty") or 0), "kind": r.get("kind", ""),
        # Решение владельца водитель должен видеть у себя: пока списание висит
        # несогласованным, эти бутылки числятся за ним, и узнавать об этом в
        # конце смены поздно.
        "state": r.get("state") or "ok",
        "decided_note": r.get("decided_note", ""),
        # Удержание — то, что водителя касается напрямую: узнать о нём в день
        # выплаты значит поспорить тогда, когда доказывать уже нечем.
        # Виноватых несколько — водителю показываем его долю, а не всё.
        "comp": _comp_share(r.get("comp") or {}, me["name"]),
        "comp_note": (r.get("comp") or {}).get("note", ""),
        # isoformat, а не str(): у str разделитель — пробел, и сафари такую
        # дату не разбирает вовсе.
        "note": r.get("note", ""), "at": _iso(r.get("at")),
    } for r in rows]}, headers=CORS_HEADERS)


@require_driver
async def handle_pos(request):
    """Одна точка из приложения.

    Живую трансляцию включает сам телеграм — из мини-аппа её не запустить, это
    его нативная кнопка. Зато приложение умеет спросить у телефона, где он
    сейчас, и прислать одну точку. Для слежения за сменой этого мало, а вот
    для дела достаточно: водитель открывает приложение десятки раз за ночь, и
    точка обновляется сама, без единого нажатия.

    Точка ложится туда же, куда и трансляция, и живёт по тем же правилам: одна
    последняя на водителя, маршрут за смену, стирается при её закрытии."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        lat, lon = float(body.get("lat")), float(body.get("lon"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_coords"}, status=400, headers=CORS_HEADERS)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return web.json_response({"error": "bad_coords"}, status=400, headers=CORS_HEADERS)
    acc = body.get("acc")
    now = datetime.now(timezone.utc)
    await db.driver_pos_set(me["name"], _biz_day(), lat, lon, now,
                            acc=acc if isinstance(acc, (int, float)) else None)
    return web.json_response({"ok": True, "at": now.isoformat()}, headers=CORS_HEADERS)


@require_driver
async def handle_geo_help(request):
    """Объяснить в чате, как включить трансляцию.

    Приложение зовёт это, когда само взять точку не может: старый клиент или
    выключенная в телефоне геолокация. Тогда остаётся трансляция — она живёт в
    скрепке телеграма и работает в любой версии.

    Кнопки «я здесь» здесь больше нет: приложение берёт точку само, а кнопка
    висела бы серой полосой под полем ввода всю смену. Заодно снимаем ту, что
    осталась с прошлых версий, — клавиатура живёт в чате, пока её не убрать."""
    me = request["driver"]
    import os as _os
    from api_server import tg_send
    import config_staff as _staff
    tid = _staff.DRIVER_IDS.get((me.get("name") or "").strip())
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return web.json_response({"ok": False}, headers=CORS_HEADERS)
    import geo_watch as _gw
    link = await _gw.geo_bot_link()
    text = ("Чтобы оператор видел, где вы, включите трансляцию геопозиции в "
            "отдельном боте" + (f": {link}" if link else " геопозиции") + "\n\n"
            "Там: скрепка (📎) → «Геопозиция» → «Транслировать» → «Пока не выключу».\n\n"
            "Один раз: телефон будет присылать точку сам, даже когда телеграм "
            "свёрнут, а тот чат можно убрать в архив. Маршрут за смену стирается, "
            "когда её закрывают.")
    kb = {"remove_keyboard": True}
    # Ответ телеграма проверяем: он умеет отвечать «принято» кодом 200 и
    # отказом внутри тела. Молча отрапортовать успех и не отправить — худшее из
    # возможного: человек ждёт сообщение, которого нет.
    try:
        res = await tg_send(token, tid, text, parse_mode=None, reply_markup=kb)
    except Exception as e:
        log.warning(f"[driver] кнопка «я здесь» не ушла ({me.get('name')}): {e}")
        return web.json_response({"ok": False}, headers=CORS_HEADERS)
    if not (res or {}).get("ok"):
        log.error(f"[driver] кнопка «я здесь» отвергнута телеграмом "
                  f"({me.get('name')}): {(res or {}).get('description')}")
        return web.json_response({"ok": False}, headers=CORS_HEADERS)
    # В реестр чата водителя: скрытый режим стирает только записанное, а без
    # этой строки инструкция про геопозицию оставалась бы в чате после шторы.
    try:
        _mid = ((res or {}).get("result") or {}).get("message_id")
        if _mid:
            await db.drv_msg_add(int(tid), int(_mid), datetime.now(timezone.utc))
    except Exception as e:
        log.debug(f"[driver] реестр гео-сообщения: {e}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


@require_driver
async def handle_panic(request):
    """Экстренная ситуация: приложение маскируется, наши узнают.

    Ответ намеренно пустой и мгновенный: водитель нажимает это, когда рядом
    кто-то есть, и никакого «отправлено» на экране появиться не должно."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    on = bool(body.get("on"))
    now = datetime.now(timezone.utc)
    await db.panic_set(me["name"], on, now.isoformat(),
                       {"district": me.get("district", ""),
                        "district_code": me.get("district_code", ""),
                        "operator": me.get("operator", "")})
    log.warning(f"[driver] {me['name']}: скрытый режим {'ВКЛЮЧЁН' if on else 'снят'}")
    if on:
        try:
            n = await _wipe_chat(me)
            if n:
                log.warning(f"[driver] {me['name']}: убрано сообщений в чате {n}")
        except Exception as e:
            log.error(f"[driver] чат не почищен: {e}")
    # Прикрытие. Пустой чат маскирует не хуже, чем полный палит: человек с
    # телефоном в руках объясняет, почему у него в переписке с ботом пусто.
    # Поэтому туда ложится приглашение в игру — то же самое, что кладёт
    # оператор, когда прячет водителя со своего планшета. Зовём его же функцию,
    # а не пишем вторую такую: две разошлись бы, и у одного из двух путей
    # прикрытие однажды перестало бы сниматься.
    try:
        from operator_routes import _drv_cover
        await _drv_cover(me["name"], on)
    except Exception as e:                                   # noqa: BLE001
        log.error(f"[driver] прикрытие: {e}")
    try:
        await _notify_panic(me, on, now)
    except Exception as e:
        log.error(f"[driver] тревога не ушла: {e}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def _wipe_chat(me: dict) -> int:
    """Убрать переписку бота с этим водителем.

    Маскировать приложение и оставить чат — половина дела: в сообщениях бота
    адреса, суммы и состав заказов, и открыть их можно, не заходя в приложение.
    Поэтому вместе со шторой уходит и переписка.

    Чего это НЕ делает и не должно:
      • не трогает ничего, кроме чата этого водителя с его ботом. Токен здесь
        только водительский, chat_id — только его;
      • не трогает AMBAR STAR, операторов и старших: их переписка — это
        документы по деньгам и заказам, и удалять её нельзя ни при каких
        обстоятельствах;
      • ничего не удаляет в базе. Заказы, расходы и история остаются целыми:
        убираются копии сообщений в телеграме, а не сами данные.

    Телеграм разрешает боту удалять сообщения не старше двух суток. Что старше,
    останется — сказать об этом честно лучше, чем притворяться, что чат чист.
    """
    import os as _os
    from api_server import tg_delete
    import config_staff as _staff
    tid = _staff.DRIVER_IDS.get((me.get("name") or "").strip())
    token = _os.getenv("DRIVER_BOT_TOKEN", "")
    if not (tid and token):
        return 0
    ids = await db.drv_msgs_take(int(tid))
    gone = 0
    for mid in ids:
        try:
            res = await tg_delete(token, tid, mid)
            if (res or {}).get("ok"):
                gone += 1
        except Exception:
            pass
    return gone


async def _notify_panic(me: dict, on: bool, now):
    """Старшему и на планшет района — сразу, владельцу — тоже.

    Отдельно предупреждаем, что писать водителю в бот нельзя: уведомление
    всплывёт баннером на его экране и выдаст маскировку с головой."""
    from api_server import tg_send, OPERATOR_BOT_TOKEN
    import config_staff as staff
    day = _biz_day()
    live = 0
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
        f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
        orders = await db.get_orders_in_range(f(start), f(start + timedelta(days=1)))
        live = sum(1 for o in orders if (o.get("driver") or "").strip() == me["name"]
                   and o.get("status") == "approved")
    except Exception:
        pass

    hhmm = now.astimezone(DUBAI_TZ).strftime("%H:%M")
    if on:
        text = ("🆘 *ЭКСТРЕННАЯ СИТУАЦИЯ*\n"
                f"{me['name']} · {me.get('district_code','')} {me.get('district_name','')}\n"
                f"Приложение переведено в скрытый режим в {hhmm}.\n"
                f"Заказов в работе: {live}.\n\n"
                "*Не пишите ему в приложение и в бот* — уведомление всплывёт у него "
                "на экране. Позвоните.")
    else:
        text = (f"🟢 *Скрытый режим снят*\n{me['name']} · "
                f"{me.get('district_code','')} {me.get('district_name','')} · {hhmm}")

    ids = list(staff.SENIOR_IDS) + [d["telegram_id"] for d in staff.DEVICES]
    if OPERATOR_BOT_TOKEN:
        for uid in ids:
            try:
                await tg_send(OPERATOR_BOT_TOKEN, uid, text)
            except Exception as e:
                log.error(f"[driver] тревога {uid}: {e}")
    try:
        from owner_routes import notify_owners_force
        await notify_owners_force("driver.panic", text)
    except Exception as e:
        log.error(f"[driver] тревога владельцу: {e}")


@require_driver
async def handle_orders(request):
    """Заказы, назначенные мне. В работе — сверху, доставленные за смену — ниже,
    чтобы было видно, что уже закрыто, и не звонить туда второй раз."""
    me = request["driver"]
    day = _biz_day()
    # Окно с запасом назад: заказ, созданный до полудня и принятый после
    # открытия смены, относится к этой смене (bizday.order_day).
    since, until = bizday.window_utc(day, day)
    orders = await db.get_orders_in_range(since, until, test=_tq(me))
    mine = [o for o in orders if (o.get("driver") or "").strip() == me["name"]
            and (o.get("status") != "delivered" or bizday.order_day(o) == day)]
    active = [_order_view(o) for o in mine if o.get("status") == "approved"]
    done = [_order_view(o) for o in mine if o.get("status") == "delivered"]
    # Кому из этих клиентов мы должны. Долг возник, когда у водителя не нашлось
    # сдачи, и закрыть его проще всего в следующий приезд — но только если
    # водитель о нём узнает раньше, чем уедет.
    try:
        ids = {int(o.get("customer_id") or 0) for o in mine} - {0}
        owed = await db.debts_of(list(ids)) if ids else {}
        for v, o in list(zip(active, [x for x in mine if x.get("status") == "approved"])) + \
                    list(zip(done, [x for x in mine if x.get("status") == "delivered"])):
            d = owed.get(int(o.get("customer_id") or 0), 0)
            v["owed"] = round(-d, 2) if d < 0 else 0
    except Exception as e:
        log.warning(f"[driver] долги клиентов не прочитаны: {e}")
    active.sort(key=lambda x: x.get("confirmed_at") or x.get("timestamp") or "")
    done.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return web.json_response({
        "day": day, "active": active, "done": done,
        "total_aed": sum(x["total"] for x in done),
        # Чем берут с клиента и по какому курсу — для меню «Валютой» на карточке.
        "fx_take": await fx_take_list(),
        # Скрытый режим едет вместе с заказами, а не только при запуске: его
        # может включить старший с планшета, и ждать перезапуска приложения в
        # такой момент нельзя. Опрос идёт каждые пять секунд — этого хватает.
        "panic": bool(await db.panic_get(me["name"])),
    }, headers=CORS_HEADERS)


@require_driver
async def handle_history(request):
    """Мои заказы за прошлые дни. Водителю это нужно не из любопытства: спор
    «я это возил» решается списком, а не памятью."""
    me = request["driver"]
    try:
        days = max(1, min(120, int(request.query.get("days", "30"))))
    except ValueError:
        days = 14
    today = _biz_day()
    first = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    last = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    since, until = bizday.window_utc(first, last)
    # История берётся за месяц и больше. С предельными пятьюстами заказами по
    # всем районам ранние дни просто исчезали бы из его истории — а водитель
    # смотрит её как раз затем, чтобы сверить свои деньги за период.
    orders = await db.get_orders_in_range(since, until, limit=None, test=_tq(me))
    mine = [o for o in orders
            if (o.get("driver") or "").strip() == me["name"] and o.get("status") == "delivered"
            and bizday.in_days(o, first, last)]

    # По дням: за какой день сколько увёз — так видно и объём, и выходные.
    #
    # Наличные считаем отдельно от оплаченного онлайн и долга. Для водителя это
    # разные деньги: за наличные он отчитывается, остальное проходит мимо него,
    # и общая сумма на вопрос «сколько я сдал» не отвечает.
    def _mins(o):
        """Сколько заказ ехал. Нужно и водителю, и в спорах о скорости."""
        a, b = o.get("confirmed_at"), o.get("delivered_at")
        if not a or not b:
            return None
        try:
            t0 = datetime.fromisoformat(str(a).replace("Z", ""))
            t1 = datetime.fromisoformat(str(b).replace("Z", ""))
        except (ValueError, TypeError):
            return None
        m = (t1 - t0).total_seconds() / 60
        return int(round(m)) if 0 <= m < 600 else None

    by_day = {}
    for o in mine:
        key = bizday.order_day(o) or ""
        g = by_day.setdefault(key, {"day": key, "count": 0, "aed": 0, "cash": 0,
                                    "online": 0, "mins": [], "orders": []})
        total = int(o.get("total", 0) or 0)
        g["count"] += 1
        g["aed"] += total
        if _is_prepaid(o) or o.get("payment_method") in ("debt", "transfer", "free"):
            g["online"] += total
        else:
            g["cash"] += total
        m = _mins(o)
        if m is not None:
            g["mins"].append(m)
        g["orders"].append({**_order_view(o), "mins": m})

    days_list = sorted(by_day.values(), key=lambda x: x["day"], reverse=True)
    all_mins = []
    for g in days_list:
        g["orders"].sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        all_mins += g["mins"]
        g["avg_min"] = int(round(sum(g["mins"]) / len(g["mins"]))) if g["mins"] else 0
        g.pop("mins")
    return web.json_response({
        "days": days_list,
        "range": days,
        # Сегодняшние рабочие сутки — от сервера: до полудня телефон и сервер
        # считают «сегодня» разными днями, и листалка съезжала бы на день.
        "today": today,
        "totals": {
            "count": len(mine),
            "aed": sum(int(o.get("total", 0) or 0) for o in mine),
            "cash": sum(g["cash"] for g in days_list),
            "online": sum(g["online"] for g in days_list),
            "avg_min": int(round(sum(all_mins) / len(all_mins))) if all_mins else 0,
            "worked": len(days_list),
            "best": max((g["aed"] for g in days_list), default=0),
        },
        # Оставлены для совместимости со старым приложением на телефоне
        # водителя: оно обновится не в ту же секунду, что сервер.
        "total_count": len(mine),
        "total_aed": sum(int(o.get("total", 0) or 0) for o in mine),
    }, headers=CORS_HEADERS)


KIND_TITLE = {
    "delivered": "привёз заказ",
    "cancel":    "просит отменить заказ",
    "edit":      "просит изменить состав",
    "note":      "сообщение по заказу",
    "reassign":  "не может взять заказ",
}


@require_driver
async def handle_rates(request):
    """Курсы для расчёта валютой — те же, что в «Финансах» у владельца:
    наличный курс обменника, где он свежий, иначе рыночный. Дирхамов за
    единицу валюты. Основные валюты — первыми, остальные по коду."""
    import rates as _rates
    try:
        d = await _rates.get_rates()
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[driver] курсы не прочитаны: {e}")
        d = {"rates": [], "main": []}
    main = list(d.get("main") or [])
    out = []
    for r in d.get("rates") or []:
        code = str(r.get("code") or "")
        rate = r.get("cash_aed") or r.get("aed")
        if not code or not rate or code == "AED":
            continue
        out.append({"code": code, "name": r.get("name") or code, "sym": FX_SYM.get(code, code),
                    "rate": float(rate), "cash": bool(r.get("cash_aed")),
                    "market": float(r.get("aed") or 0) or None, "main": code in main})
    # USDT: платежи в нём принимаем, курс — доллар один к одному
    usd = next((r for r in out if r["code"] == "USD"), None)
    if usd:
        out.append({"code": "USDT", "name": "Tether USDT", "sym": "USDT", "rate": usd["rate"],
                    "cash": usd["cash"], "market": usd["market"], "main": True, "kind": "crypto"})
    for r in out:
        r.setdefault("kind", "fiat")
    # история по дням: вчерашний курс для процента и последние дни для графика
    try:
        hist = await db.fx_days(8)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[driver] история курсов не прочитана: {e}")
        hist = []
    today = datetime.now(DUBAI_TZ).strftime("%Y-%m-%d")
    past = [h for h in hist if str(h.get("_id")) != today][:7]          # свежие сверху
    for r in out:
        code = "USD" if r["code"] == "USDT" else r["code"]
        vals = []
        for h in reversed(past):                                        # от старых к новым
            v = (h.get("rates") or {}).get(code)
            if v:
                vals.append(round(float(v), 6))
        prev = vals[-1] if vals else None
        r["prev"] = prev
        r["change"] = round((r["rate"] / prev - 1) * 100, 2) if prev else None
        r["spark"] = (vals + [r["rate"]]) if len(vals) >= 2 else []
    out.sort(key=lambda r: (0 if r["main"] else 1,
                            main.index(r["code"]) if r["code"] in main else 0, r["code"]))
    return web.json_response({"rates": out, "at": d.get("fetched_iso") or "",
                              "silent": bool(d.get("silent")), "days": len(past),
                              "ok": bool(out)}, headers=CORS_HEADERS)


@require_driver
@needs_shift
async def handle_fx(request):
    """Клиент платит валютой: {code} — выбрать, {code: ""} — снова дирхамы.
    Курс замораживается на момент выбора; менять можно, пока заказ в пути."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str(body.get("code") or "").strip().upper()[:5]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_yours"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status"}, status=409, headers=CORS_HEADERS)
    if not _needs_settle(o):
        return web.json_response({"error": "not_cash"}, status=400, headers=CORS_HEADERS)
    if not code or code == "AED":
        await db.update_order(oid, pay_fx=None)
        log.info(f"[driver] {me['name']} #{oid}: расчёт снова в дирхамах")
        return web.json_response({"ok": True, "pay_fx": None}, headers=CORS_HEADERS)
    # Курс приёма — наш, из STAR (fx_take), а не обменника: клиенту называют
    # ровно то число, по которому у нас берут.
    import fx_take
    row = next((r for r in await fx_take.rates() if r["code"] == code), None)
    if not row:
        return web.json_response({"error": "no_rate"}, status=400, headers=CORS_HEADERS)
    rate = row["rate"]
    fx = {"code": code, "name": row["name"], "rate": float(rate),
          "at": datetime.now(timezone.utc).isoformat(), "by": me["name"]}
    await db.update_order(oid, pay_fx=fx)
    o["pay_fx"] = fx
    log.info(f"[driver] {me['name']} #{oid}: оплата в {code} по {rate}")
    return web.json_response({"ok": True, "pay_fx": _fx_view(o)}, headers=CORS_HEADERS)


@require_driver
async def handle_req_withdraw(request):
    """Отозвать свою открытую просьбу (правку или отмену): передумал сам —
    оператору не нужно решать то, чего уже не просят."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    req = o.get("driver_req") or o.get("edit_request") or {}
    if req.get("status") != "open" or req.get("kind") not in ("edit", "cancel", "note"):
        return web.json_response({"error": "no_open_request"}, status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, driver_req={**req, "status": "withdrawn", "decided_at": now,
                                           "decided_by": me["name"]})
    try:
        from api_server import tg_send, OPERATOR_BOT_TOKEN as _tok
        from operator_routes import OPERATOR_IDS
        what = {"edit": "правку состава", "cancel": "просьбу отменить", "note": "сообщение"}
        for op_id in OPERATOR_IDS:
            await tg_send(_tok, op_id, f"↩️ Водитель {me['name']} отозвал {what.get(req.get('kind'), 'просьбу')} "
                                       f"по заказу #{oid} — решать больше нечего.")
    except Exception as e:                       # noqa: BLE001
        log.warning(f"[driver] отзыв просьбы #{oid}: операторам не ушло: {e}")
    log.info(f"[driver] {me['name']} отозвал просьбу «{req.get('kind')}» по #{oid}")
    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def _notify_operators(oid: str, me: dict, req: dict, order: dict):
    """Просьба уходит операторам с кнопками решения — разбирать её руками по
    переписке никто не станет."""
    try:
        from api_server import tg_send, OPERATOR_BOT_TOKEN as _tok
        from operator_routes import OPERATOR_IDS
        import html as _h
        icon = {"delivered": "📦", "cancel": "🚫", "edit": "✏️", "note": "💬"}
        msg = (f"{icon.get(req['kind'],'•')} <b>Водитель {KIND_TITLE[req['kind']]}</b>\n"
               f"Заказ #{oid} · {_h.escape(me['name'])} ({me['district_code']})\n"
               f"{_h.escape(order.get('address',''))} · {order.get('total',0)} AED\n")
        if req.get("diff"):
            sign = {"add": "+", "del": "−", "qty": "→"}
            msg += "\n" + "\n".join(
                f"{sign.get(d['kind'],'·')} {_h.escape(d['name'])}"
                + (f" ×{d['qty']}" if d["kind"] != "qty" else f" {d['from']} → {d['qty']}")
                for d in req["diff"]) + f"\n\nСумма: {order.get('total',0)} → <b>{req.get('total')} AED</b>"
        if req.get("text"):
            msg += f"\n\n💬 {_h.escape(req['text'])}"
        ok = {"delivered": "✅ Подтвердить доставку", "cancel": "✅ Отменить заказ",
              "edit": "✅ Применить", "note": "✅ Принято"}[req["kind"]]
        kb = {"inline_keyboard": [[
            {"text": ok,           "callback_data": f"drvreq_ok_{oid}"},
            {"text": "🚫 Отклонить", "callback_data": f"drvreq_no_{oid}"},
        ]]}
        if order.get("test"):
            from config import TEST_OPERATOR_IDS
            msg = "🧪 <b>ТЕСТ</b> · " + msg
        for op_id in (sorted(TEST_OPERATOR_IDS) if order.get("test") else OPERATOR_IDS):
            try:
                await tg_send(_tok, op_id, msg, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                log.warning(f"[driver] просьба #{oid} → {op_id}: {e}")
    except Exception as e:
        log.error(f"[driver] уведомление о просьбе #{oid}: {e}")


@require_driver
@needs_shift
async def handle_delivered(request):
    """«Доставил» — это пинг оператору, а не закрытие заказа.

    Закрывает заказ оператор: деньги, выручка и спорные ситуации на нём. Но
    видно ему должно быть сразу и явно, что водитель уже привёз."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    # Деньги закрываются на адресе или не закрываются никогда.
    #
    # Наличный заказ нельзя отметить доставленным, не сказав, сошлись ли деньги.
    # Не из недоверия: разница всплывает через сутки, когда никто уже не помнит,
    # кто кому остался должен, и превращается в спор. Вопрос задаётся ровно там,
    # где на него ещё есть ответ, — у двери.
    #
    # Там, где наличные через руки водителя не идут, вопроса нет: онлайн-оплата
    # и заказ «в долг» — это другая история, и спрашивать про сдачу с человека,
    # который денег не брал, значит приучить его нажимать не глядя.
    if _needs_settle(o) and not (body.get("settled") is True or (o.get("settle") or {})):
        return web.json_response({"error": "need_settle", "total": o.get("total") or 0},
                                 status=400, headers=CORS_HEADERS)
    # Просьба водителя — одна на заказ. Открытая правка состава или отмена
    # затиралась бы отметкой о доставке молча: сначала решение по ней.
    cur = o.get("driver_req") or o.get("edit_request") or {}
    if cur.get("status") == "open" and cur.get("kind") in ("edit", "cancel"):
        return web.json_response({"error": "req_open", "kind": cur.get("kind")},
                                 status=409, headers=CORS_HEADERS)
    req = {"kind": "delivered", "text": "", "items": None, "diff": [], "total": None,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, driver_req=req)
    log.info(f"[driver] {me['name']} привёз заказ #{oid} — ждём оператора")
    await _notify_operators(oid, me, req, o)
    try:
        from owner_routes import notify_owners
        await notify_owners("orders.driver_done",
            f"📦 *Водитель привёз заказ #{oid}*\n"
            f"{me['name']} ({me['district_code']}) · {o.get('total',0)} AED\n"
            f"_Ждёт подтверждения оператора._", test=bool(o.get("test")))
    except Exception as e:
        log.warning(f"[driver] уведомление о доставке #{oid}: {e}")
    return web.json_response({"ok": True, "order_id": oid, "driver_req": req},
                             headers=CORS_HEADERS)


# «На месте» (владелец, 18 сен 2026): «добавь кнопку „на месте“ у водителя,
# чтобы, когда он подъезжал, его оператору приходило сообщение с текстом
# „Идемте выходите пожалуйста, <марка и номер машины>“ — чтобы оператор мог
# этот текст взять и скопировать». Кнопка — в карточке активного заказа
# вместо «Доставил»; нажал — та же кнопка становится «Доставил».
ARRIVED_TEXT = "Идемте выходите пожалуйста"


async def _driver_car(name: str) -> dict | None:
    """Машина водителя из общего списка («Кто на каком районе» в STAR)."""
    mine = [c for c in await db.cars_fleet() if str(c.get("driver") or "") == name]
    return max(mine, key=lambda c: str(c.get("at") or "")) if mine else None


def arrived_phrase(car: dict | None) -> str:
    """Что оператор отправит клиенту: «…, Hyundai Elantra 97448» — марка и
    номер. Машина не закреплена — фраза без неё."""
    what = " ".join(x for x in (str((car or {}).get("model") or "").strip(),
                                str((car or {}).get("plate") or "").strip()) if x)
    return f"{ARRIVED_TEXT}, {what}" if what else ARRIVED_TEXT


async def _tell_arrived(oid: str, me: dict, order: dict, phrase: str, has_car: bool) -> int:
    """Оператору района заказа — тем же маршрутом, что и сам заказ (op_route:
    свой оператор, ушёл в скрытый режим — ближайший; старшие и планшет — как
    всегда). Фраза — моноширинным текстом: в телеграме он копируется
    нажатием; в личке к нему ещё кнопка «Скопировать текст». Сколько чатов
    получили."""
    import html as _h
    import op_route
    msg = (f"📍 <b>Водитель на месте</b> · заказ #{_h.escape(oid)}\n"
           f"{_h.escape(me['name'])} ({_h.escape(me.get('district_code') or '')})"
           + (f" · {_h.escape(order.get('address') or '')}" if order.get("address") else "") + "\n\n"
           f"<code>{_h.escape(phrase)}</code>\n"
           + ("Нажмите на текст — он скопируется." if has_car
              else "Машина за водителем не закреплена — марку и номер уточните у водителя."))
    if order.get("test"):
        msg = "🧪 <b>ТЕСТ</b> · " + msg
    # Кнопка копирования — только в личке: в общем чате её не на всех
    # телефонах покажет, а текст и так копируется нажатием.
    kb = lambda chat: ({"inline_keyboard": [[{"text": "Скопировать текст", "copy_text": {"text": phrase[:256]}}]]}
                       if int(chat) > 0 else None)
    try:
        ids = await op_route.send(msg, district=order.get("office_id") or order.get("district_id") or "",
                                  parse_mode="HTML", reply_markup=kb, test=bool(order.get("test")),
                                  retry_plain=True)
    except Exception as e:                       # noqa: BLE001
        log.error(f"[driver] «на месте» #{oid}: операторам не ушло: {e}")
        return 0
    return len(ids or {})


@require_driver
@needs_shift
async def handle_arrived(request):
    """«На месте»: водитель подъехал — оператору уходит фраза для клиента.
    Отметка одна на водителя: второе нажатие ничего не шлёт повторно."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    if not await db.order_mark_arrived(oid, me["name"], now):
        cur = await db.get_order(oid) or {}
        if cur.get("status") != "approved":
            return web.json_response({"error": "wrong_status", "status": cur.get("status")},
                                     status=409, headers=CORS_HEADERS)
        return web.json_response({"ok": True, "again": True, "arrived_at": cur.get("driver_arrived_at", "")},
                                 headers=CORS_HEADERS)
    car = await _driver_car(me["name"])
    phrase = arrived_phrase(car)
    sent = await _tell_arrived(oid, me, o, phrase, bool(car))
    log.info(f"[driver] {me['name']} на месте #{oid}: «{phrase}» → чатов {sent}")
    return web.json_response({"ok": True, "arrived_at": now, "phrase": phrase, "sent": sent},
                             headers=CORS_HEADERS)


@require_driver
@needs_shift
async def handle_ack(request):
    """«Принял, еду» — не решение, а отметка: заказ водителю уже назначен.

    Оператору важно знать, что человек увидел заказ и тронулся, и через сколько.
    Никаких координат: где он в этот момент — не наше дело и в базе этого нет."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)
    if o.get("driver_ack_at"):
        return web.json_response({"ok": True, "driver_ack_at": o["driver_ack_at"]},
                                 headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    await db.update_order(oid, driver_ack_at=now, driver_ack_by=me["name"])
    log.info(f"[driver] {me['name']} принял #{oid}")
    try:
        from operator_routes import _refresh_cards
        await _refresh_cards({**o, "driver_ack_at": now})
    except Exception as e:
        log.warning(f"[driver] обновление карточек #{oid}: {e}")
    return web.json_response({"ok": True, "driver_ack_at": now}, headers=CORS_HEADERS)


@require_driver
async def handle_catalog(request):
    """Каталог для правки состава — только то, что есть в наличии.

    Цены здесь полные: водитель довозит телефонный заказ, а скидка положена
    только за заказ через приложение."""
    from operator_routes import _load_catalog, _full_price
    from api_server import _catalog_unit_price
    # Для списания нужен весь список: разбитая бутылка есть на полке и тогда,
    # когда позиция снята с продажи.
    everything = request.query.get("all") == "1"
    items, cats = [], []
    # Порядок — как в каталоге у операторов: полки идут как идут, водитель
    # ищет глазами по знакомому ряду, а не по алфавиту.
    for p in _load_catalog():
        if not (p.get("stock") or everything):
            continue
        pack = bool(p.get("price_24_full"))
        num = lambda v: int(v) if float(v).is_integer() else round(float(v), 2)
        row = {"id": p.get("id"), "name": p.get("name", ""), "cat": p.get("cat", ""),
               # полные цены (телефонный заказ) и цены приложения (со скидкой):
               # экран берёт те, что у источника заказа
               "price": _full_price(p), "app": num(_catalog_unit_price(p, None)), "pack": pack}
        if pack:
            row.update(p12=_full_price(p, 12), p24=_full_price(p, 24),
                       app12=num(_catalog_unit_price(p, 12)), app24=num(_catalog_unit_price(p, 24)))
        items.append(row)
        if row["cat"] and row["cat"] not in cats:
            cats.append(row["cat"])
    return web.json_response({"items": items, "cats": cats}, headers=CORS_HEADERS)


def _diff_lines(old_items: list, new_items: list) -> list:
    """Что именно поменялось: убрали, добавили, изменили количество.

    Оператор должен увидеть разницу, а не два списка — сравнивать их глазами
    в чате он не станет."""
    was = {i.get("id"): i for i in (old_items or [])}
    now = {i.get("id"): i for i in (new_items or [])}
    out = []
    for pid, i in now.items():
        prev = was.get(pid)
        q, pq = int(i.get("qty") or 0), int((prev or {}).get("qty") or 0)
        if not prev:
            out.append({"kind": "add", "name": i.get("name", ""), "qty": q})
        elif q != pq:
            out.append({"kind": "qty", "name": i.get("name", ""), "from": pq, "qty": q})
    for pid, i in was.items():
        if pid not in now:
            out.append({"kind": "del", "name": i.get("name", ""), "qty": int(i.get("qty") or 0)})
    return out


@require_driver
@needs_shift
async def handle_edit_request(request):
    """Просьба водителя — предложение, а не действие.

    Виды: изменить состав (items), отменить заказ, просто сообщение. Ни одно не
    применяется само: заказ меняет, отменяет и закрывает оператор, у него же
    остаётся цена."""
    oid = (request.match_info.get("oid") or "").strip()
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    text = str(body.get("text") or "").strip()[:400]
    raw_items = body.get("items")
    kind = str(body.get("kind") or "").strip()
    if kind not in ("edit", "cancel", "note", "reassign", ""):
        return web.json_response({"error": "bad_kind"}, status=400, headers=CORS_HEADERS)

    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    if o.get("status") != "approved":
        return web.json_response({"error": "wrong_status", "status": o.get("status")},
                                 status=409, headers=CORS_HEADERS)

    items, diff, total = None, [], None
    if isinstance(raw_items, list):
        from operator_routes import _catalog_by_id, _unit_price_for, _order_total_for
        cat = _catalog_by_id()
        source = o.get("source") or "app"
        items = []
        for it in raw_items:
            p = cat.get(it.get("id"))
            if not p:
                continue
            try:
                qty = max(0, int(it.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            pcs = it.get("pcs")
            # Цена — по источнику заказа: из приложения со скидкой, по
            # телефону полная. Правка водителя цену заказа не меняет.
            price = _unit_price_for(source, p, pcs)
            price = int(price) if float(price).is_integer() else round(price, 2)
            items.append({"id": p["id"], "name": p.get("name", ""), "qty": qty,
                          **({"pcs": int(pcs)} if pcs else {}),
                          "price": price, "line_total": round(price * qty, 2)})
        if not items:
            return web.json_response({"error": "empty_items"}, status=400, headers=CORS_HEADERS)
        # Подарок приложения остаётся при заказе: водитель его не правит, а
        # без этой строки правка «убирала» бы подарок у клиента.
        gifts = [i for i in (o.get("items") or []) if i.get("gift")]
        items = gifts + items
        diff = _diff_lines(o.get("items"), items)
        if not diff and not text:
            return web.json_response({"error": "nothing_changed"}, status=400, headers=CORS_HEADERS)
        total = await _order_total_for(o, items)
    elif not text and kind not in ("cancel", "reassign"):
        return web.json_response({"error": "text_or_items_required"}, status=400, headers=CORS_HEADERS)

    kind = kind or ("edit" if items else "note")
    req = {"kind": kind, "text": text, "items": items, "diff": diff, "total": total,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, driver_req=req)
    log.info(f"[driver] {me['name']} просьба «{kind}» по #{oid}: "
             f"{len(diff)} изменений, текст: {text[:40]}")
    await _notify_operators(oid, me, req, o)
    return web.json_response({"ok": True, "driver_req": req}, headers=CORS_HEADERS)


# Бензин и мойка — не «прочие расходы», а ежедневная работа машины: они
# заводятся отдельными окнами, а не поиском в списке. Всё остальное — «ещё
# расход» внизу, там комментарий обязателен.
# Виды расходов — те же, что у старшего, и берём их оттуда же. Пока у водителя
# был свой короткий список, всё, что не бензин и не мойка, приезжало к старшему
# безымянным «доп. расходом»: он видел сумму и строчку словами, а к какому виду
# она относится — угадывал. Два списка рядом расходятся в первую же неделю.
from expense_routes import EXTRA_KINDS, PAY_T, is_card, asks_pay   # noqa: E402

# Названия для тех видов, о которых водителя спрашивают каждый день.
EXPENSE_KINDS = {"fuel": "Бензин", "wash": "Мойка", "parking": "Парковка", "other": ""}

# Про эти два водитель обязан ответить каждую смену — суммой или «не было».
# Молчание здесь неотличимо от забывчивости, а забытая заправка всплывает
# через неделю, когда вспомнить её уже нельзя.
# Парковка — тоже обязательный ответ (макет владельца, 14 сен 2026: плитка
# «Нужно заполнить»): запись или «не было», как у бензина и мойки.
MUST_ANSWER = ("fuel", "wash", "parking")

# А там, где деньги отдают на стороне, нужен ещё и чек: снимок бумажки —
# единственное, чем такая трата подтверждается. Требуем его при первой записи;
# когда водитель правит сумму у той же траты, старый чек остаётся в силе, пока
# он не переснял его сам. Гонять человека к колонке из-за исправленной цифры —
# способ отучить его записывать вовсе.
MUST_RECEIPT = tuple(k for k, v in EXTRA_KINDS.items() if v.get("receipt"))


def _kind_of(x: dict) -> str:
    """Вид расхода. У записей до разделения по видам его нет — узнаём по
    комментарию, иначе вчерашний бензин уедет в «прочее» и водитель заведёт
    второй."""
    k = x.get("kind")
    if k in EXTRA_KINDS:
        return k
    c = (x.get("comment") or "").lower()
    if "бензин" in c or "топлив" in c or "fuel" in c: return "fuel"
    if "мойк" in c or "wash" in c:                    return "wash"
    return "other"


# Что за бутылка под этим кодом и можно ли её отдать.
#
# Со склада она пока не уходит: пока владелец не согласовал расход, это
# заявление водителя, а не факт — то же правило, что у списаний. Реестр меняется
# в момент согласования, там же, где расход становится деньгами.
GUARD_SAY = {"no_code": "код не прочитан", "unknown": "нет в реестре",
             "written": "уже списана", "sold": "ушла с заказом",
             "deleted": "убрана из реестра",
             "no_item": "нет в каталоге", "taken": "эту уже записали"}


async def _guard_bottle(code) -> tuple[dict | None, str]:
    code = str(code or "").strip()
    if not code:
        return None, "no_code"
    doc = await db.qr_get(code)
    if not doc:
        return None, "unknown"
    # Годится только та, что в остатке. Перечислять негодные статусы нельзя:
    # так уже промахнулись мимо «убрана из реестра», и следующий новый статус
    # промахнулся бы точно так же. Разрешаем один, отказываем всем остальным.
    st = (doc.get("status") or "active").strip()
    if st != "active":
        return None, st
    from operator_routes import _load_catalog
    p = {x.get("id"): x for x in _load_catalog()}.get(str(doc.get("product_id") or ""))
    if not p:
        return None, "no_item"
    # Ту же бутылку дважды не отдают. Проверяем среди ждущих решения: уже
    # согласованная выйдет из реестра сама и споткнётся строчкой выше.
    if await db.expense_by_code(code):
        return None, "taken"

    # Цена за бутылку, а не за учётную единицу: у пива единица — ящик из 24, и
    # охраннику отдают одну банку, а не ящик.
    цена = 0
    try:
        import stock_value, stock_routes
        unit = max(1, int(stock_routes._unit(p) or 1))
        цена = int(round(float((await stock_value.cost_map()).get(p["id"]) or 0) / unit))
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] цена бутылки не посчиталась: {e}")
    return {"cost": max(0, цена),
            "item": {"code": code, "bottle": p.get("name", ""),
                     "bottle_id": p["id"], "label": doc.get("label") or "",
                     "district": (doc.get("district") or "").strip()}}, ""


@require_driver
async def handle_bottle_look(request):
    """GET /api/driver/bottle?code=… — что за бутылка под этим кодом.

    Отдельным запросом на скане, а не проверкой при отправке: узнать «эта уже
    продана» надо у камеры, пока бутылка в руке и её можно вернуть на полку,
    а не через минуту после того, как её отдали.

    Код едет параметром, а не куском адреса. Коды мы не печатаем — берём те,
    что уже стоят на бутылках, а там попадаются ссылки: в реестре лежит,
    например, «http://en.m.wikipedia.org». В адресе такой код рвётся на части,
    и запрос уходит в никуда вместо честного «не наша бутылка»."""
    б, беда = await _guard_bottle(request.query.get("code"))
    if беда:
        return web.json_response({"ok": False, "verdict": беда,
                                  "say": GUARD_SAY.get(беда, "не наша бутылка")},
                                 headers=CORS_HEADERS)
    return web.json_response({"ok": True, "cost": б["cost"], **б["item"]},
                             headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_expense_add(request):
    """Расход на согласование. Сразу в расходы дня он не попадает: иначе водитель
    сам себе назначал бы траты. Менеджер утверждает в панели «Учёт»."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    try:
        amount = max(0, int(round(float(body.get("amount") or 0))))
    except (TypeError, ValueError):
        amount = 0
    kind = str(body.get("kind") or "other").strip()
    if kind not in EXTRA_KINDS:
        kind = "other"
    вид = EXTRA_KINDS[kind]
    if вид.get("auto"):                    # чай за допродажи пишет сервер, не человек
        return web.json_response({"error": "auto_only", "kind": kind}, status=400, headers=CORS_HEADERS)
    comment = (str(body.get("comment") or "").strip()[:200]
               or EXPENSE_KINDS.get(kind) or вид["t"])
    day = _biz_day()

    # «Не было» — обязательный ответ, а не расход. Пока водитель молчит, нельзя
    # отличить пустую заправку от забытой, и смена не считается сданной.
    if body.get("none") is not None and kind in MUST_ANSWER:
        none = bool(body.get("none"))
        await db.set_driver_no_expense(day, me["name"], kind, none)
        log.info(f"[driver] {me['name']}: {EXPENSE_KINDS[kind]} — "
                 + ("не было" if none else "ответ снят"))
        return web.json_response({"ok": True, "none": none, "kind": kind},
                                 headers=CORS_HEADERS)

    # Убрать снимок машины у мойки: водитель снял не то и новый сделает позже.
    # Запись остаётся, но без снимка и снова на решение; после закрытия смены
    # трогать её уже нельзя — как и переснимать.
    if body.get("car_del") and kind == "wash":
        d = await db.get_driver_day(day, me["name"]) or {}
        prev = next((x for x in (d.get("extras") or []) if _kind_of(x) == kind), None)
        if not prev:
            return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
        if d.get("shift_close_at"):
            return web.json_response({"error": "shift_closed"}, status=409,
                                     headers=CORS_HEADERS)
        await db.driver_expense_car_clear(day, me["name"], prev["id"])
        try:
            await db.expense_photo_del(prev["id"] + ":car")
        except Exception as e:                              # noqa: BLE001
            log.warning(f"[driver] снимок мойки не стёрт: {e}")
        log.info(f"[driver] {me['name']} убрал снимок машины у мойки")
        return web.json_response({"ok": True, "id": prev["id"]}, headers=CORS_HEADERS)

    # Как платили — наличными или безналом (владелец, 18 сен 2026: «заставь его
    # где-то выбрать, он заплатил наличными или безналичная оплата была»).
    # Новое приложение шлёт поле всегда, и без ответа запись не примем. Старое,
    # открытое до обновления, его не знает — его запись считается наличной,
    # как все до этого, а ломать ему отправку посреди смены незачем.
    pay = None
    if "pay" in body and asks_pay(kind):
        pay = str(body.get("pay") or "").strip()
        if pay not in PAY_T:
            return web.json_response({"error": "pay_required", "kind": kind},
                                     status=400, headers=CORS_HEADERS)

    # Охрана — единственный расход, где платят не деньгами, а бутылкой. Сумму
    # тут спрашивать не у кого и незачем: код с крышки знает, что это за
    # бутылка, где она числится и сколько за неё отдали при закупке. Водитель
    # говорит только, кому отдал.
    бутылка = None
    if kind == "guard":
        # Либо бутылка (код с QR — цену называет закупка), либо наличные
        # (сумма руками) — одно из двух, иного не дано (владелец, 14 сен 2026).
        # Пришли оба — верим коду: сумма при бутылке не вписывается.
        code = str(body.get("code") or "").strip()
        if code:
            бутылка, беда = await _guard_bottle(code)
            if беда:
                return web.json_response({"error": беда}, status=400, headers=CORS_HEADERS)
            amount = бутылка["cost"]
        elif amount <= 0:
            return web.json_response({"error": "code_or_amount"},
                                     status=400, headers=CORS_HEADERS)
        if not comment:
            return web.json_response({"error": "no_comment"},
                                     status=400, headers=CORS_HEADERS)
    elif amount <= 0 or not comment:
        return web.json_response({"error": "amount_and_comment_required"},
                                 status=400, headers=CORS_HEADERS)
    photo, беда = photos.decode(body.get("photo"))
    if беда:
        return web.json_response({"error": беда}, status=400, headers=CORS_HEADERS)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Бензин и мойка — одна запись за смену, водитель правит её же. Заправился
    # дважды — пишет общее число, а не заводит вторую строку: в приложении у
    # каждого вида одно поле, и оно должно совпадать с тем, что в учёте.
    # Доп. расход правится по своему id: их за смену бывает несколько.
    ent_id = str(body.get("id") or "").strip()
    prev = None
    if ent_id or kind in ("fuel", "wash"):
        d = await db.get_driver_day(day, me["name"]) or {}
        extras = d.get("extras") or []
        prev = (next((x for x in extras if x.get("id") == ent_id), None) if ent_id
                else next((x for x in extras if _kind_of(x) == kind), None))
        if ent_id and prev is None:
            return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
        # Запись, которую поставил сервер (заказ в долг, бонус за допродажу),
        # водитель не правит: она про заказ, а не про его траты.
        if prev is not None and prev.get("auto"):
            return web.json_response({"error": "auto_only", "kind": kind},
                                     status=400, headers=CORS_HEADERS)

    # Отклонённая запись заполняется заново: её снимки уже не доказательство,
    # и старшему нельзя подсунуть тот же чек с другой суммой.
    # Снимок, который старший принял отдельно, остаётся в силе и у отклонённой
    # записи: переснимать надо только то, что отклонили.
    # Отклонён один снимок — второй остаётся годным, пока его не отклонили;
    # старший его мог и не трогать. Отклонена запись целиком (ни один снимок
    # не отклонён отдельно) — заново нужны оба.
    отклонён = bool(prev) and (prev.get("status") or "approved") == "rejected"
    по_снимку = отклонён and ((prev or {}).get("photo_ok") == "no"
                              or (prev or {}).get("car_ok") == "no")
    целиком = отклонён and not по_снимку
    чек_ок = bool((prev or {}).get("photo")) and not целиком and prev.get("photo_ok") != "no"
    машина_ок = bool((prev or {}).get("car_photo")) and not целиком and prev.get("car_ok") != "no"
    if kind in MUST_RECEIPT and not photo and not чек_ок:
        return web.json_response({"error": "no_photo", "kind": kind},
                                 status=400, headers=CORS_HEADERS)
    # Мойка подтверждается двумя снимками: чеком и машиной в процессе мойки —
    # спереди, в контуре, только с камеры. Второй лежит под id+":car".
    # Переснять можно сколько угодно, но до конца смены: после закрытия кадр
    # «с мойки» уже ничего не доказывает.
    car_photo, беда = photos.decode(body.get("car_photo")) if kind == "wash" else (b"", "")
    if беда:
        return web.json_response({"error": беда}, status=400, headers=CORS_HEADERS)
    if kind == "wash" and not car_photo and not машина_ок:
        return web.json_response({"error": "no_car_photo", "kind": kind},
                                 status=400, headers=CORS_HEADERS)
    if kind == "wash" and (photo or car_photo) and prev and (d or {}).get("shift_close_at"):
        return web.json_response({"error": "shift_closed"}, status=409,
                                 headers=CORS_HEADERS)
    car_thumb = photos.thumb(body.get("car_thumb")) if car_photo else ""
    thumb = photos.thumb(body.get("thumb")) if photo else ""

    if prev:
        await db.update_driver_expense(day, me["name"], prev["id"], amount, comment,
                                       thumb if photo else None,
                                       kind=kind, kind_t=вид["t"],
                                       plus=bool(вид.get("plus")),
                                       car_thumb=car_thumb if car_photo else None,
                                       pay=pay)
        item = {**prev, "amount": amount, "comment": comment, "kind": kind,
                "kind_t": вид["t"], "plus": bool(вид.get("plus")),
                "status": "pending", "edited_at": now_iso, **({"pay": pay} if pay else {})}
        if photo: item.update({"photo": True, "thumb": thumb})
        if car_photo: item.update({"car_photo": True, "car_thumb": car_thumb})
        log.info(f"[driver] {me['name']} поправил {comment}: "
                 f"{prev.get('amount')} → {amount} AED"
                 + (" (чек переснят)" if photo else "")
                 + (" (машина переснята)" if car_photo else ""))
    else:
        item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
                "kind": kind, "kind_t": вид["t"], "plus": bool(вид.get("plus")),
                "by_driver": me["name"], "status": "pending", "at": now_iso}
        if pay: item["pay"] = pay
        if photo: item.update({"photo": True, "thumb": thumb})
        if car_photo: item.update({"car_photo": True, "car_thumb": car_thumb})
        if бутылка: item.update(бутылка["item"])
        await db.add_driver_expense(day, me["name"], item)
    # Снимок кладём после самой записи: строка без чека — это повод переспросить,
    # а чек без строки не значит ничего и найтись уже не сможет.
    if photo:
        try:
            await db.expense_photo_set(item["id"], photo, thumb)
        except Exception as e:                              # noqa: BLE001
            log.warning(f"[driver] чек не сохранён: {e}")
    if car_photo:
        try:
            await db.expense_photo_set(item["id"] + ":car", car_photo, car_thumb)
        except Exception as e:                              # noqa: BLE001
            log.warning(f"[driver] снимок машины не сохранён: {e}")
    # Вписал сумму после «не было» — ответ снимается сам: два взаимоисключающих
    # ответа на один вопрос хуже, чем ни одного.
    if kind in MUST_ANSWER:
        await db.set_driver_no_expense(day, me["name"], kind, False)
        log.info(f"[driver] {me['name']} просит {amount} AED — {comment}")
    try:
        from owner_routes import notify_owners
        was = f" (было {prev.get('amount')})" if prev else ""
        await notify_owners(
            "expenses.request",
            f"{'🧾 *Расход изменён*' if prev else '💸 *Расход на согласование*'}\n"
            f"{me['name']} ({me['district_code']}) — {amount} AED{was}"
            + (f" · {PAY_T[item['pay']]}" if item.get("pay") in PAY_T else "") + "\n"
            f"_{comment}_")
    except Exception as e:
        log.warning(f"[driver] уведомление о расходе: {e}")
    return web.json_response({"ok": True, "item": item}, headers=CORS_HEADERS)


# ── Разговор по заказу ──────────────────────────────────────────────────────
# Раньше водитель мог только «попросить»: отправил — и жди решения. Половина
# ситуаций на адресе так не решается. Клиент не открывает дверь; адрес не тот;
# клиент хочет другое, но не то, что записано. На всё это нужен не запрос, а
# разговор — и он должен идти внутри приложения, привязанным к заказу, а не в
# личной переписке, где его потом никто не найдёт.
CASE_KIND = {
    "client":  "Клиент не отвечает",
    "address": "Не найти адрес",
    "wait":    "Прошу подождать",
    "other":   "Вопрос по заказу",
}


@require_driver
async def handle_chat(request):
    """Переписка по заказу. Открытие ленты считается прочтением."""
    me = request["driver"]
    oid = (request.match_info.get("oid") or "").strip()
    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    if _chat_new(o, "driver"):
        await db.order_chat_seen(oid, "driver", now)
    return web.json_response({"order_id": oid, "chat": _chat_view(o),
                              "operator": o.get("operator_name") or ""},
                             headers=CORS_HEADERS)


@require_driver
async def handle_chat_send(request):
    """Сообщение оператору. С видом — открывает обращение по заказу.

    Обращение и разговор — одно и то же: первая реплика и есть повод. Отдельная
    «карточка обращения» рядом с перепиской была бы двумя записями об одном
    событии, и они разъехались бы в первый же спорный день."""
    me = request["driver"]
    oid = (request.match_info.get("oid") or "").strip()
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    text = str(body.get("text") or "").strip()[:600]
    kind = str(body.get("kind") or "").strip()
    if kind and kind not in CASE_KIND:
        kind = "other"
    if not text and not kind:
        return web.json_response({"error": "empty"}, status=400, headers=CORS_HEADERS)

    o = await db.get_order(oid)
    if not o or (o.get("driver") or "").strip() != me["name"]:
        return web.json_response({"error": "not_your_order"}, status=403, headers=CORS_HEADERS)

    now = datetime.now(timezone.utc).isoformat()
    msg = {"by": "driver", "name": me["name"], "text": text or CASE_KIND[kind],
           "at": now, "kind": kind}
    doc = await db.order_chat_add(oid, msg)
    log.info(f"[driver] {me['name']} пишет по #{oid}"
             + (f" ({kind})" if kind else "") + f": {msg['text'][:50]}")
    try:
        await _notify_chat(oid, me, msg, doc or o)
    except Exception as e:
        log.warning(f"[driver] уведомление о сообщении: {e}")
    return web.json_response({"ok": True, "chat": _chat_view(doc or o)},
                             headers=CORS_HEADERS)


async def _notify_chat(oid: str, me: dict, msg: dict, o: dict):
    """Операторам — в бот. В панели сообщение и так всплывёт в ленте, но
    оператор может стоять к ней спиной, а водитель ждёт ответа на адресе."""
    from api_server import tg_send, OPERATOR_BOT_TOKEN
    from operator_routes import OPERATOR_IDS
    if not OPERATOR_BOT_TOKEN:
        return
    head = ("❓ *" + CASE_KIND.get(msg.get("kind"), "") + "*\n") if msg.get("kind") else "💬 *Водитель пишет*\n"
    text = (head + f"#{oid} · {me['district_code']} · {me['name']}\n"
            f"{o.get('address', '')}\n\n_{msg['text']}_\n\n"
            "Ответить — в панели, карточка заказа.")
    if o.get("test"):
        from config import TEST_OPERATOR_IDS
        text = "🧪 *ТЕСТ* · " + text
    for uid in (sorted(TEST_OPERATOR_IDS) if o.get("test") else OPERATOR_IDS):
        try:
            await tg_send(OPERATOR_BOT_TOKEN, uid, text)
        except Exception as e:
            log.warning(f"[driver] сообщение оператору {uid}: {e}")


@require_driver
async def handle_expenses(request):
    """Мои расходы за смену — и что из них уже утвердили."""
    me = request["driver"]
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    extras = list(d.get("extras") or [])
    st = lambda x: x.get("status") or "approved"      # старые записи — от менеджера

    for x in extras:
        x["kind"] = _kind_of(x)
    live = [x for x in extras if st(x) != "rejected"]
    no = d.get("no_expense") or {}
    return web.json_response({
        "day": day,
        "working": d.get("working"),
        # На что водитель ответил «не было» — и по чему ещё молчит.
        "no_expense": {k: bool(no.get(k)) for k in MUST_ANSWER},
        "must_answer": list(MUST_ANSWER),
        "pending_answer": [k for k in MUST_ANSWER
                           if not no.get(k)
                           and not any(_kind_of(x) == k for x in extras)],
        "meal": staff.meal_of(d),
        # Ставки — на экран водителю: он должен видеть правило, а не только
        # итог, иначе каждый раз спрашивает, почему сегодня 40, а не 80.
        "meal_rates": {"working": staff.MEAL_WORKING, "off": staff.MEAL_OFF},
        "extras": extras,
        "kinds": [{"id": k, "t": v["t"], "receipt": bool(v.get("receipt")),
                   "plus": bool(v.get("plus")), "pay": asks_pay(k)} for k, v in EXTRA_KINDS.items()],
        "by_kind": {k: {"sum": sum(x.get("amount", 0) for x in live if x["kind"] == k),
                        "count": sum(1 for x in live if x["kind"] == k)}
                    for k in EXTRA_KINDS},
        "pending": sum(x.get("amount", 0) for x in extras if st(x) == "pending"),
        "approved": sum(x.get("amount", 0) for x in extras if st(x) == "approved"),
    }, headers=CORS_HEADERS)


# ── Приём товара ────────────────────────────────────────────────────────────
# Водитель забирает поставку в магазине и сканирует каждую бутылку. Вся логика
# и все проверки — в supply_routes: документ поставки принадлежит ему, и второй
# набор правил рядом с первым разошёлся бы в первую же неделю.
#
# Здесь только вход: кто спрашивает и от чьего имени.
@require_driver
async def handle_supply_list(request):
    """Что можно забрать: мои задачи, свободные, взятые другими."""
    import supply_routes
    me = request["driver"]
    if _tq(me):
        return web.json_response({"mine": [], "free": [], "extra": [], "taken": []},
                                 headers=CORS_HEADERS)
    return web.json_response(
        await supply_routes.tasks_for_driver(me["name"], me.get("district") or ""),
        headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_supply_history(request):
    """Мои закрытые приёмки за месяц (владелец, 14 сен 2026: «пусть не
    исчезает, а перемещается в историю, чтобы проваливаться и просматривать»).
    Строка — район, база, когда закрыли, сколько принято; карточка — та же
    задача, только для просмотра (GET /supply/{sid})."""
    import supply_routes
    me = request["driver"]
    try:
        days = max(1, min(90, int(request.query.get("days", "30"))))
    except ValueError:
        days = 30
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    for sup in await db.supplies_since(since):
        sid = sup.get("_id")
        for oid, t in (sup.get("tasks") or {}).items():
            if (t.get("driver") or "") != me["name"] or not t.get("done_at"):
                continue
            v = supply_routes._task_view(sid, sup, oid, t, me["name"])
            rows.append({k: v.get(k) for k in ("supply_id", "district", "district_code", "district_name",
                                                 "extra", "base", "day", "done_at", "noscan_at",
                                                 "need", "got", "positions")})
    rows.sort(key=lambda r: str(r.get("done_at") or ""), reverse=True)
    return web.json_response({"rows": rows[:40]}, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_claim(request):
    """Взять задачу. Достаётся одному — кто нажал первым."""
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = request.match_info.get("sid") or ""
    oid = str(body.get("district") or "").strip()
    ok, task = await db.supply_task_claim(sid, oid, me["name"],
                                          request["tg"].get("id") or 0,
                                          datetime.now(timezone.utc))
    if not ok:
        return web.json_response({"ok": False, "error": "taken",
                                  "driver": (task or {}).get("driver") or ""},
                                 status=409, headers=CORS_HEADERS)
    log.info(f"[driver] {me['name']} взял приёмку {sid}/{oid}")
    sup = await db.supply_get(sid)
    return web.json_response(
        supply_routes._task_view(sid, sup, oid, (sup.get("tasks") or {}).get(oid) or {},
                                 me["name"]),
        headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_release(request):
    """Отдать задачу обратно: не еду."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = request.match_info.get("sid") or ""
    oid = str(body.get("district") or "").strip()
    ok = await db.supply_task_release(sid, oid, me["name"])
    return web.json_response({"ok": ok}, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_supply_task(request):
    """Одна задача целиком — экран приёмки."""
    import supply_routes
    me = request["driver"]
    sid = request.match_info.get("sid") or ""
    oid = (request.query.get("district") or "").strip()
    sup = await db.supply_get(sid)
    task = ((sup or {}).get("tasks") or {}).get(oid)
    if not sup or not task:
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    return web.json_response(supply_routes._task_view(sid, sup, oid, task, me["name"]),
                             headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_scan(request):
    """Одна бутылка в приёмку."""
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400, headers=CORS_HEADERS)
    code = re.sub(r"\s+", "", str(body.get("code") or ""))[:120]
    if not code:
        return web.json_response({"error": "empty_code"}, status=400, headers=CORS_HEADERS)
    res = await supply_routes.task_scan(
        request.match_info.get("sid") or "",
        str(body.get("district") or "").strip(),
        str(body.get("product_id") or "").strip(),
        code, me["name"], request["tg"].get("id") or 0,
        str(body.get("at_dev") or ""),
        erev=body.get("erev"))
    return web.json_response(res, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_undo(request):
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await supply_routes.task_undo(
        request.match_info.get("sid") or "",
        str(body.get("district") or "").strip(),
        re.sub(r"\s+", "", str(body.get("code") or ""))[:120], me["name"])
    return web.json_response(res, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_supply_finish(request):
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await supply_routes.task_finish(
        request.match_info.get("sid") or "",
        str(body.get("district") or "").strip(),
        me["name"], str(body.get("note") or ""))
    return web.json_response(res, headers=CORS_HEADERS)


@require_driver
async def handle_expense_photo(request):
    """Свой снимок у своей траты за эту смену — посмотреть перед тем, как
    переснять или убрать. Чужие записи отсюда не отдаются."""
    me = request["driver"]
    item_id = (request.match_info.get("item_id") or "").strip()
    d = await db.get_driver_day(_biz_day(), me["name"]) or {}
    if not any(x.get("id") == item_id for x in (d.get("extras") or [])):
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    # ?car=1 — машина на мойке, второй снимок той же записи.
    img = await db.expense_photo(item_id + (":car" if request.query.get("car") else ""))
    if not img:
        return web.json_response({"error": "no_photo"}, status=404, headers=CORS_HEADERS)
    ctype = "image/png" if img[:2] == b"\x89P" else "image/jpeg"
    return web.Response(body=img, content_type=ctype,
                        headers={**CORS_HEADERS, "Cache-Control": "private, max-age=300"})


@require_driver
@_no_test
async def handle_supply_hold(request):
    """Занять задачу под сканирование (камера открыта) или отпустить."""
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await supply_routes.task_hold(
        request.match_info.get("sid") or "",
        str(body.get("district") or "").strip(), me["name"],
        request["tg"].get("id") or 0, bool(body.get("on", True)))
    return web.json_response(res, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_buy(request):
    """Закупочная цена позиции заявки на другую базу — вписывает водитель по
    приезду, до сканирования: {district, product_id, price, qty?}. Пустая
    цена стирает запись. Ответ — задача целиком, с ценами и итогом."""
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = request.match_info.get("sid") or ""
    oid = str(body.get("district") or "").strip()
    pid = str(body.get("product_id") or "").strip()
    sup = await db.supply_get(sid)
    if not sup or sup.get("status") != "open":
        return web.json_response({"error": "not_found"}, status=404, headers=CORS_HEADERS)
    if (sup.get("kind") or "main") != "extra":
        return web.json_response({"error": "not_extra"}, status=400, headers=CORS_HEADERS)
    task = (sup.get("tasks") or {}).get(oid) or {}
    if task.get("driver") != me["name"]:
        return web.json_response({"error": "not_mine"}, status=403, headers=CORS_HEADERS)
    try:
        price = round(float(body.get("price") or 0), 2)
        qty = int(body.get("qty") or 0)
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_number"}, status=400, headers=CORS_HEADERS)
    r = await supply_routes.buy_set(sup, pid, price, qty, me["name"],
                                    int((request.get("tg") or {}).get("id") or 0))
    if not r["ok"]:
        return web.json_response({"error": r["error"]}, status=400, headers=CORS_HEADERS)
    sup = await db.supply_get(sid)
    return web.json_response(
        supply_routes._task_view(sid, sup, oid, (sup.get("tasks") or {}).get(oid) or {}, me["name"]),
        headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


@require_driver
@_no_test
async def handle_supply_noscan(request):
    """Товар забрали, коды не читали. Задача остаётся открытой — досканировать."""
    import supply_routes
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await supply_routes.task_noscan(
        request.match_info.get("sid") or "",
        str(body.get("district") or "").strip(), me["name"])
    return web.json_response(res, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


# ── Перемещение между районами ───────────────────────────────────────────────
# Ручки живут в move_routes, здесь только права: водитель, не тестовый.
# ── просьба закрыть смену раньше оператора (18 сен 2026) ─────────────────────
# Решает оператор района в своей панели; логика — в close_req.py.
@require_driver
@_no_test
async def handle_close_request(request):
    """POST {reason, text} — попросить оператора района отпустить раньше."""
    me = request["driver"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    try:
        закрыт = bool((await db.shifts_for_day(day)).get(me.get("district") or ""))
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[driver] закрытие дня не прочиталось: {e}")
        закрыт = False
    code, res = await close_req.ask(day, me, d, str(body.get("reason") or ""),
                                    str(body.get("text") or ""), await _in_route(me), закрыт)
    if code == 200:
        res["shift"] = await _shift_view(me)
    return web.json_response(res, status=code, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_close_withdraw(request):
    """POST — передумал: отозвать запрос, пока оператор не ответил."""
    me = request["driver"]
    day = _biz_day()
    code, res = await close_req.withdraw(day, me["name"], await db.get_driver_day(day, me["name"]))
    if code == 200:
        res["shift"] = await _shift_view(me)
    return web.json_response(res, status=code, headers=CORS_HEADERS)


@require_driver
@_no_test
async def handle_move_list(request):
    import move_routes
    return await move_routes.handle_drv_list(request)


@require_driver
@_no_test
async def handle_move_claim(request):
    import move_routes
    return await move_routes.handle_drv_claim(request)


@require_driver
@_no_test
async def handle_move_release(request):
    import move_routes
    return await move_routes.handle_drv_release(request)


@require_driver
@_no_test
async def handle_move_scan(request):
    import move_routes
    return await move_routes.handle_drv_scan(request)


@require_driver
@_no_test
async def handle_move_start(request):
    import move_routes
    return await move_routes.handle_drv_start(request)


@require_driver
@_no_test
async def handle_move_accept(request):
    import move_routes
    return await move_routes.handle_drv_accept(request)


# Получатель сканирует то, что ему отдали (владелец, 19 сен 2026).
@require_driver
@_no_test
async def handle_move_receive(request):
    import move_routes
    return await move_routes.handle_drv_receive(request)


# Ревизия своего района (владелец, 19 сен 2026): та же, что у старшего в
# STAR; район — из входа водителя, день — сегодняшний (driver_audit.py).
@require_driver
@_no_test
async def handle_audit(request):
    import driver_audit
    return await driver_audit.handle_state(request)


@require_driver
@_no_test
async def handle_audit_brief(request):
    import driver_audit
    return await driver_audit.handle_brief(request)


@require_driver
@_no_test
async def handle_audit_start(request):
    import driver_audit
    return await driver_audit.handle_start(request)


@require_driver
@_no_test
async def handle_audit_scan(request):
    import driver_audit
    return await driver_audit.handle_scan(request)


@require_driver
@_no_test
async def handle_audit_undo(request):
    import driver_audit
    return await driver_audit.handle_undo(request)


@require_driver
@_no_test
async def handle_audit_finish(request):
    import driver_audit
    return await driver_audit.handle_finish(request)


def setup(app):
    r = app.router
    routes = (
        ("/api/driver/ping",                    handle_ping,        "GET"),
        ("/api/driver/orders",                  handle_orders,      "GET"),
        ("/api/driver/history",                 handle_history,     "GET"),
        ("/api/driver/catalog",                 handle_catalog,     "GET"),
        ("/api/driver/expenses",                handle_expenses,    "GET"),
        ("/api/driver/expenses",                handle_expense_add, "POST"),
        ("/api/driver/expenses/photo/{item_id}", handle_expense_photo, "GET"),
        ("/api/driver/bottle",                  handle_bottle_look, "GET"),
        ("/api/driver/supply",                  handle_supply_list, "GET"),
        ("/api/driver/supply/history",          handle_supply_history, "GET"),   # раньше {sid}: иначе «history» — это sid
        ("/api/driver/writeoff",                handle_writeoff_add, "POST"),
        ("/api/driver/writeoff/scan",           handle_writeoff_scan, "POST"),
        ("/api/driver/writeoffs",               handle_writeoffs,   "GET"),
        ("/api/driver/stock/moves",             handle_moves,       "GET"),
        ("/api/driver/stock/code",              handle_code_info,   "GET"),
        ("/api/driver/stock/move",              handle_move_scan,   "POST"),
        ("/api/driver/stock/move/undo",         handle_move_undo,   "POST"),
        ("/api/driver/stock/move/{tid}",        handle_move_del,    "DELETE"),
        ("/api/driver/profile",                 handle_profile,     "GET"),
        ("/api/driver/shift",                   handle_shift,       "GET"),
        ("/api/driver/shift/summary",           handle_shift_summary, "GET"),
        ("/api/driver/shift/open",              handle_shift_open,  "POST"),
        ("/api/driver/shift/close",             handle_shift_close, "POST"),
        ("/api/driver/shift/close-request",     handle_close_request, "POST"),
        ("/api/driver/shift/close-request/withdraw", handle_close_withdraw, "POST"),
        ("/api/driver/panic",                   handle_panic,       "POST"),
        ("/api/driver/pos",                     handle_pos,         "POST"),
        ("/api/driver/geo/help",                handle_geo_help,    "POST"),
        ("/api/driver/orders/{oid}/settle",     handle_settle,      "POST"),
        ("/api/driver/orders/{oid}/debt-back",  handle_debt_settle, "POST"),
        ("/api/driver/orders/{oid}/ack",        handle_ack,         "POST"),
        ("/api/driver/orders/{oid}/arrived",    handle_arrived,     "POST"),
        ("/api/driver/orders/{oid}/delivered",  handle_delivered,   "POST"),
        ("/api/driver/orders/{oid}/edit",       handle_edit_request, "POST"),
        ("/api/driver/orders/{oid}/edit/withdraw", handle_req_withdraw, "POST"),
        ("/api/driver/orders/{oid}/fx",         handle_fx,          "POST"),
        ("/api/driver/rates",                   handle_rates,       "GET"),
        ("/api/driver/orders/{oid}/chat",       handle_chat,        "GET"),
        ("/api/driver/orders/{oid}/chat",       handle_chat_send,   "POST"),
        ("/api/driver/supply/{sid}",            handle_supply_task,   "GET"),
        ("/api/driver/supply/{sid}/claim",      handle_supply_claim,  "POST"),
        ("/api/driver/supply/{sid}/release",    handle_supply_release, "POST"),
        ("/api/driver/supply/{sid}/scan",       handle_supply_scan,   "POST"),
        ("/api/driver/supply/{sid}/undo",       handle_supply_undo,   "POST"),
        ("/api/driver/supply/{sid}/finish",     handle_supply_finish, "POST"),
        ("/api/driver/supply/{sid}/noscan",     handle_supply_noscan, "POST"),
        ("/api/driver/supply/{sid}/hold",       handle_supply_hold,   "POST"),
        ("/api/driver/supply/{sid}/buy",        handle_supply_buy,    "POST"),
        # Перемещение между районами: отдающий начинает и сканирует, получатель
        # принимает («Принял» / «Принял неровно»). claim/release — старый
        # порядок, для приложения, открытого до обновления.
        ("/api/driver/move",                    handle_move_list,     "GET"),
        ("/api/driver/move/{mid}/claim",        handle_move_claim,    "POST"),
        ("/api/driver/move/{mid}/release",      handle_move_release,  "POST"),
        ("/api/driver/move/{mid}/scan",         handle_move_scan,     "POST"),
        ("/api/driver/move/{mid}/start",        handle_move_start,    "POST"),
        ("/api/driver/move/{mid}/accept",       handle_move_accept,   "POST"),
        ("/api/driver/move/{mid}/receive",      handle_move_receive,  "POST"),
        # Ревизия своего района.
        ("/api/driver/audit",                   handle_audit,         "GET"),
        ("/api/driver/audit/brief",             handle_audit_brief,   "GET"),
        ("/api/driver/audit/start",             handle_audit_start,   "POST"),
        ("/api/driver/audit/scan",              handle_audit_scan,    "POST"),
        ("/api/driver/audit/undo",              handle_audit_undo,    "POST"),
        ("/api/driver/audit/finish",            handle_audit_finish,  "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        # Любой метод, а не словарь из двух: маршрут с DELETE однажды уронил
        # эту петлю на середине списка, и всё ниже (смена, скрытый режим,
        # геопозиция, доставка) осталось несмонтированным — молча, с одной
        # строкой в журнале.
        r.add_route(method, path, handler)
    log.info(f"[driver] routes mounted · водителей с доступом: {len(staff.DRIVER_IDS)}")
