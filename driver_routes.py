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
import photos
import config_staff as staff
from owner_auth import CORS_HEADERS

log = logging.getLogger("driver")

DRIVER_BOT_TOKEN = os.getenv("DRIVER_BOT_TOKEN", "")
INIT_DATA_MAX_AGE = 24 * 3600
DUBAI_TZ = timezone(timedelta(hours=4))
SHIFT_START_HOUR = 12


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
            staff.apply_moves(await db.staff_map_get(), await db.driver_map_get())
        except Exception as e:
            log.warning(f"[driver] перестановка не прочитана: {e}")
        me = staff.driver_by_tg(user.get("id"))
        if not me:
            log.warning(f"[driver] отказ: tg={user.get('id')} ({user.get('username')}) не в списке")
            return web.json_response({"error": "forbidden"}, status=403, headers=CORS_HEADERS)
        request["driver"] = me
        request["tg"] = user
        return await fn(request)
    wrapper.__wrapped__ = fn
    return wrapper


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
        "items": [{"name": i.get("name", ""), "qty": i.get("qty", 0), "pcs": i.get("pcs")}
                  for i in (o.get("items") or [])],
        "total": int(o.get("total", 0) or 0),
        "comment": o.get("comment", ""),
        "payment_method": o.get("payment_method", ""),
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
        "driver_req": o.get("driver_req") or o.get("edit_request") or None,
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


async def _geo_state(name: str) -> dict:
    """Что сейчас с геопозицией водителя: идёт ли трансляция и до какого часа."""
    rows = await db.driver_pos_all([name])
    r = (rows or [{}])[0] if rows else {}
    now = datetime.now(timezone.utc)
    at, until = _dt_of(r.get("at")), _dt_of(r.get("until"))
    fresh = bool(at and (now - at).total_seconds() < GEO_FRESH_SEC)
    live = bool(until and until > now)
    left = int((until - now).total_seconds() // 60) if live else 0
    # Бессрочная трансляция приходит сроком на десятки лет. Показывать «хватит
    # ещё на 596523 ч» бессмысленно: у неё просто нет конца, так и говорим.
    endless = left > 24 * 60
    return {"ok": fresh and live, "fresh": fresh, "stream": live,
            "until": "" if endless else (_iso_at(until) if until else ""),
            "left_min": 0 if endless else left, "endless": endless,
            "age_sec": int((now - at).total_seconds()) if at else None}


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
        orders = await db.get_orders_in_range(f(start), f(start + timedelta(days=1)))
    except Exception as e:
        log.warning(f"[driver] заказы в пути не прочитаны: {e}")
        return []
    return [o.get("order_id") for o in orders
            if (o.get("driver") or "").strip() == me["name"]
            and o.get("status") == "approved"]


async def _shift_view(me: dict) -> dict:
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    geo = await _geo_state(me["name"])
    must = _must_left(d)
    opened, closed = d.get("shift_open_at"), d.get("shift_close_at")
    route = await _in_route(me) if opened and not closed else []
    return {
        "day": day, "working": d.get("working"),
        "opened": bool(opened), "opened_at": _iso_at(opened),
        "closed": bool(closed), "closed_at": _iso_at(closed),
        "geo": geo, "must": must,
        "must_names": [EXPENSE_KINDS.get(k) or k for k in must],
        "in_route": route,
        "can_open": d.get("working") is True and geo["ok"] and not opened,
        "can_close": bool(opened) and not closed and not must and not route,
    }


@require_driver
async def handle_shift(request):
    return web.json_response(await _shift_view(request["driver"]), headers=CORS_HEADERS)


@require_driver
async def handle_shift_open(request):
    """Открыть смену: отметка оператора плюс живая трансляция."""
    me = request["driver"]
    day = _biz_day()
    d = await db.get_driver_day(day, me["name"]) or {}
    if d.get("shift_open_at") and not d.get("shift_close_at"):
        return web.json_response({"error": "already_open"}, status=409, headers=CORS_HEADERS)
    if d.get("working") is not True:
        return web.json_response({"error": "not_marked"}, status=409, headers=CORS_HEADERS)
    geo = await _geo_state(me["name"])
    if not geo["ok"]:
        return web.json_response({"error": "no_geo", "geo": geo},
                                 status=409, headers=CORS_HEADERS)
    await db.save_driver_day(day, me["name"], {
        "shift_open_at": datetime.now(timezone.utc), "shift_close_at": None})
    log.info(f"[driver] {me['name']}: смена открыта · трансляция ещё {geo['left_min']} мин")
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
    must = _must_left(d)
    if must:
        return web.json_response({"error": "expenses_left", "must": must},
                                 status=409, headers=CORS_HEADERS)
    route = await _in_route(me)
    if route:
        return web.json_response({"error": "orders_in_route", "ids": route},
                                 status=409, headers=CORS_HEADERS)
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
        "driver": {k: me[k] for k in ("id", "name", "district", "district_code",
                                      "district_name", "operator")},
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
    if _is_prepaid(o) or o.get("payment_method") in ("debt", "transfer"):
        # Там, где деньги не идут через руки водителя, и расчёта быть не может.
        return web.json_response({"error": "not_cash"}, status=400, headers=CORS_HEADERS)
    try:
        taken = round(float(body.get("taken")), 2)
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)
    total = int(o.get("total") or 0)
    if not (0 <= taken <= total + 100000):
        return web.json_response({"error": "bad_amount"}, status=400, headers=CORS_HEADERS)

    diff = round(taken - total, 2)
    prev = round(float((o.get("settle") or {}).get("diff") or 0), 2)
    now = datetime.now(timezone.utc)
    await db.update_order(oid, settle={
        "taken": taken, "diff": diff, "by": me["name"], "at": now.isoformat()})

    cid = int(o.get("customer_id") or 0)
    if cid and diff != prev:
        # Знак: взяли больше — уходим в минус, это наш долг клиенту.
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
    return not (_is_prepaid(o) or o.get("payment_method") in ("debt", "crypto", "transfer"))


# ── Списания ────────────────────────────────────────────────────────────────
def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


# Пределы общие с чеками: см. photos. Держать их порознь было нечем — снимок
# с той же камеры, а разойдясь, они означали бы, что бой можно снять, а чек нет.
WO_MAX_PHOTO = photos.MAX_PHOTO
WO_MAX_THUMB = photos.MAX_THUMB  # превью в самой записи: список должен открываться
WO_MAX_QTY = 240                # ящик пива это 24; больше похоже на опечатку


@require_driver
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
        qty = int(body.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if not (1 <= qty <= WO_MAX_QTY):
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
    if len(photo) < 2000 or photo[:2] not in (b"\xff\xd8", b"\x89P"):
        return web.json_response({"error": "no_photo"}, status=400, headers=CORS_HEADERS)

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
        "by": me["name"], "by_id": int(me.get("id") or 0),
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
        "comp": (int((r.get("comp") or {}).get("amount") or 0)
                 if (r.get("comp") or {}).get("amount") else 0),
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
    text = ("Чтобы оператор видел, где вы, включите трансляцию геопозиции:\n\n"
            "скрепка (📎) → «Геопозиция» → «Транслировать» → «Пока не выключу».\n\n"
            "Включить хватит один раз: телефон будет присылать точку сам, даже "
            "когда телеграм свёрнут. Маршрут за смену стирается, когда её "
            "закрывают, а в отпуске трансляцию выключают тем же меню.")
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
    start = datetime.strptime(day, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    orders = await db.get_orders_in_range(f(start), f(start + timedelta(days=1)))
    mine = [o for o in orders if (o.get("driver") or "").strip() == me["name"]]
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
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)
             ).replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    end = datetime.strptime(today, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR, tzinfo=DUBAI_TZ)
    f = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "")
    # История берётся за месяц и больше. С предельными пятьюстами заказами по
    # всем районам ранние дни просто исчезали бы из его истории — а водитель
    # смотрит её как раз затем, чтобы сверить свои деньги за период.
    orders = await db.get_orders_in_range(f(start), f(end), limit=None)
    mine = [o for o in orders
            if (o.get("driver") or "").strip() == me["name"] and o.get("status") == "delivered"]

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
        dt = o.get("confirmed_at") or o.get("timestamp") or ""
        try:
            d = datetime.fromisoformat(str(dt)).replace(tzinfo=timezone.utc).astimezone(DUBAI_TZ)
            key = _biz_day(d)
        except (ValueError, TypeError):
            key = ""
        g = by_day.setdefault(key, {"day": key, "count": 0, "aed": 0, "cash": 0,
                                    "online": 0, "mins": [], "orders": []})
        total = int(o.get("total", 0) or 0)
        g["count"] += 1
        g["aed"] += total
        if _is_prepaid(o) or o.get("payment_method") in ("debt", "transfer"):
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
    "delivered": "отметил доставку",
    "cancel":    "просит отменить заказ",
    "edit":      "просит изменить состав",
    "note":      "сообщение по заказу",
    "reassign":  "не может взять заказ",
}


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
        for op_id in OPERATOR_IDS:
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
    видно ему должно быть сразу и явно, что водитель уже отметил."""
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
    req = {"kind": "delivered", "text": "", "items": None, "diff": [], "total": None,
           "by": me["name"], "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
    await db.update_order(oid, driver_req=req)
    log.info(f"[driver] {me['name']} отметил доставку #{oid} — ждём оператора")
    await _notify_operators(oid, me, req, o)
    try:
        from owner_routes import notify_owners
        await notify_owners("orders.driver_done",
            f"📦 *Водитель отметил доставку #{oid}*\n"
            f"{me['name']} ({me['district_code']}) · {o.get('total',0)} AED\n"
            f"_Ждёт подтверждения оператора._")
    except Exception as e:
        log.warning(f"[driver] уведомление о доставке #{oid}: {e}")
    return web.json_response({"ok": True, "order_id": oid, "driver_req": req},
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
    # Для списания нужен весь список: разбитая бутылка есть на полке и тогда,
    # когда позиция снята с продажи.
    everything = request.query.get("all") == "1"
    items = []
    for p in _load_catalog():
        if not (p.get("stock") or everything):
            continue
        pack = bool(p.get("price_24_full"))
        items.append({"id": p.get("id"), "name": p.get("name", ""), "cat": p.get("cat", ""),
                      "price": _full_price(p), "pack": pack,
                      **({"p12": _full_price(p, 12), "p24": _full_price(p, 24)} if pack else {})})
    items.sort(key=lambda x: (x["cat"], x["name"]))
    cats = sorted({x["cat"] for x in items if x["cat"]})
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
        from operator_routes import _catalog_by_id, _full_price, _pos_total
        cat = _catalog_by_id()
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
            price = _full_price(p, pcs)
            items.append({"id": p["id"], "name": p.get("name", ""), "qty": qty,
                          **({"pcs": int(pcs)} if pcs else {}),
                          "price": price, "line_total": price * qty})
        if not items:
            return web.json_response({"error": "empty_items"}, status=400, headers=CORS_HEADERS)
        diff = _diff_lines(o.get("items"), items)
        if not diff and not text:
            return web.json_response({"error": "nothing_changed"}, status=400, headers=CORS_HEADERS)
        total = await _pos_total(items)
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
from expense_routes import EXTRA_KINDS                  # noqa: E402

# Названия для тех видов, о которых водителя спрашивают каждый день.
EXPENSE_KINDS = {"fuel": "Бензин", "wash": "Мойка", "other": ""}

# Про эти два водитель обязан ответить каждую смену — суммой или «не было».
# Молчание здесь неотличимо от забывчивости, а забытая заправка всплывает
# через неделю, когда вспомнить её уже нельзя.
MUST_ANSWER = ("fuel", "wash")

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


@require_driver
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

    if amount <= 0 or not comment:
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

    if kind in MUST_RECEIPT and not photo and not (prev or {}).get("photo"):
        return web.json_response({"error": "no_photo", "kind": kind},
                                 status=400, headers=CORS_HEADERS)
    thumb = photos.thumb(body.get("thumb")) if photo else ""

    if prev:
        await db.update_driver_expense(day, me["name"], prev["id"], amount, comment,
                                       thumb if photo else None,
                                       kind=kind, kind_t=вид["t"],
                                       plus=bool(вид.get("plus")))
        item = {**prev, "amount": amount, "comment": comment, "kind": kind,
                "kind_t": вид["t"], "plus": bool(вид.get("plus")),
                "status": "pending", "edited_at": now_iso}
        if photo: item.update({"photo": True, "thumb": thumb})
        log.info(f"[driver] {me['name']} поправил {comment}: "
                 f"{prev.get('amount')} → {amount} AED"
                 + (" (чек переснят)" if photo else ""))
    else:
        item = {"id": secrets.token_hex(6), "amount": amount, "comment": comment,
                "kind": kind, "kind_t": вид["t"], "plus": bool(вид.get("plus")),
                "by_driver": me["name"], "status": "pending", "at": now_iso}
        if photo: item.update({"photo": True, "thumb": thumb})
        await db.add_driver_expense(day, me["name"], item)
    # Снимок кладём после самой записи: строка без чека — это повод переспросить,
    # а чек без строки не значит ничего и найтись уже не сможет.
    if photo:
        try:
            await db.expense_photo_set(item["id"], photo, thumb)
        except Exception as e:                              # noqa: BLE001
            log.warning(f"[driver] чек не сохранён: {e}")
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
            f"{me['name']} ({me['district_code']}) — {amount} AED{was}\n"
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
    for uid in OPERATOR_IDS:
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
        "meal": staff.MEAL_WORKING if d.get("working") is True
                else (staff.MEAL_OFF if d.get("working") is False else 0),
        # Ставки — на экран водителю: он должен видеть правило, а не только
        # итог, иначе каждый раз спрашивает, почему сегодня 40, а не 80.
        "meal_rates": {"working": staff.MEAL_WORKING, "off": staff.MEAL_OFF},
        "extras": extras,
        "kinds": [{"id": k, "t": v["t"], "receipt": bool(v.get("receipt")),
                   "plus": bool(v.get("plus"))} for k, v in EXTRA_KINDS.items()],
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
    return web.json_response(
        await supply_routes.tasks_for_driver(me["name"], me.get("district") or ""),
        headers=CORS_HEADERS)


@require_driver
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
        str(body.get("at_dev") or ""))
    return web.json_response(res, headers=CORS_HEADERS)


@require_driver
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


async def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    r = app.router
    routes = (
        ("/api/driver/ping",                    handle_ping,        "GET"),
        ("/api/driver/orders",                  handle_orders,      "GET"),
        ("/api/driver/history",                 handle_history,     "GET"),
        ("/api/driver/catalog",                 handle_catalog,     "GET"),
        ("/api/driver/expenses",                handle_expenses,    "GET"),
        ("/api/driver/expenses",                handle_expense_add, "POST"),
        ("/api/driver/supply",                  handle_supply_list, "GET"),
        ("/api/driver/writeoff",                handle_writeoff_add, "POST"),
        ("/api/driver/writeoffs",               handle_writeoffs,   "GET"),
        ("/api/driver/shift",                   handle_shift,       "GET"),
        ("/api/driver/shift/open",              handle_shift_open,  "POST"),
        ("/api/driver/shift/close",             handle_shift_close, "POST"),
        ("/api/driver/panic",                   handle_panic,       "POST"),
        ("/api/driver/pos",                     handle_pos,         "POST"),
        ("/api/driver/geo/help",                handle_geo_help,    "POST"),
        ("/api/driver/orders/{oid}/settle",     handle_settle,      "POST"),
        ("/api/driver/orders/{oid}/debt-back",  handle_debt_settle, "POST"),
        ("/api/driver/orders/{oid}/ack",        handle_ack,         "POST"),
        ("/api/driver/orders/{oid}/delivered",  handle_delivered,   "POST"),
        ("/api/driver/orders/{oid}/edit",       handle_edit_request, "POST"),
        ("/api/driver/orders/{oid}/chat",       handle_chat,        "GET"),
        ("/api/driver/orders/{oid}/chat",       handle_chat_send,   "POST"),
        ("/api/driver/supply/{sid}",            handle_supply_task,   "GET"),
        ("/api/driver/supply/{sid}/claim",      handle_supply_claim,  "POST"),
        ("/api/driver/supply/{sid}/release",    handle_supply_release, "POST"),
        ("/api/driver/supply/{sid}/scan",       handle_supply_scan,   "POST"),
        ("/api/driver/supply/{sid}/undo",       handle_supply_undo,   "POST"),
        ("/api/driver/supply/{sid}/finish",     handle_supply_finish, "POST"),
    )
    seen = set()
    for path, handler, method in routes:
        if path not in seen:
            r.add_route("OPTIONS", path, _opt); seen.add(path)
        {"GET": r.add_get, "POST": r.add_post}[method](path, handler)
    log.info(f"[driver] routes mounted · водителей с доступом: {len(staff.DRIVER_IDS)}")
