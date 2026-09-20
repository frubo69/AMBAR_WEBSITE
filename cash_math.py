"""Наличные водителя за день: что у него в руках и как это сдать — две пачки.

Владелец, 19 сен 2026: «смысл расходов в том, чтобы старший видел и программа
считала правильно, но и в том, чтобы водитель знал, сколько по итогу денег
наличных у него остаётся на руках: заработал 1000, заправился на 100, помылся
на 50, и ещё 50 — чай операторов с заказов; он сдаёт две пачки: выручку и чай.
А если есть валюта — сколько в валюте, сколько в дирхамах и сколько чай».
И следом: «не забудь про бонус водителя, 5% с допродажи».

Одна арифметика на два экрана — итоги смены водителя и «Сбор выручки» у
старшего, по той же формуле, что «Обзор» (вал − чай − расход; владелец: «именно
то, что в обзоре отображается… в обзоре меня всё устраивает, не надо
трогать» — «Обзор» не трогаем). Раньше итоги брали чай из поля tip заказа
(клиентского чая в заказах не бывает — за неделю ни одного), а «Сбор выручки»
считал чай по каталогу, но из выручки его не вычитал, хотя чай сидит внутри
цены бутылки (сумма заказа = сумма строк, проверено на боевых заказах).

Что в руках у водителя за наличные заказы:
  • дирхамы — «взято» по расчёту (settle.taken), если рассчитывались не
    ровно, иначе сумма заказа;
  • валюта — взятая сумма в ней (settle.fx), а если рассчитались ровно
    валютой — сумма заказа по замороженному курсу (pay_fx), как её назвали
    клиенту.

Из этого:
  • чай операторов — отдельная пачка: 50 AED за «чайную» бутылку (поле tip
    позиции каталога) плюс чай клиента (поле tip заказа) — со всех
    доставленных заказов, как в «Обзоре»; чай с оплаченных онлайн достаётся
    из той же наличной выручки (tea_other — сколько его);
  • расходы, оплаченные наличными, ушли на сторону; безналом — наличных не
    тронули;
  • питание за день и бонус за допродажу водитель оставляет себе;
  • приход наличными («нам вернули», «мы должны») — прибавился.
Выручка = взято − чай − расходы наличными − питание − бонус + приход. Её
дирхамовая часть — за вычетом валюты: валюта сдаётся как есть.
"""
import json
from pathlib import Path

CATALOG = Path(__file__).parent / "catalog.json"
_TEA = {"mt": None, "rates": {}}

FX_SYM = {"USD": "$", "EUR": "€", "GBP": "£", "RUB": "₽", "TRY": "₺", "CNY": "¥",
          "KZT": "₸", "UAH": "₴", "INR": "₹", "JPY": "¥", "KRW": "₩", "GEL": "₾",
          "PLN": "zł", "CHF": "₣", "SAR": "SAR"}


def tea_rates() -> dict:
    """Чай операторов за бутылку — поле tip позиции каталога. Каталог правят
    без выката, поэтому перечитываем по времени файла."""
    try:
        mt = CATALOG.stat().st_mtime
    except OSError:
        return _TEA["rates"]
    if mt != _TEA["mt"]:
        try:
            cat = json.loads(CATALOG.read_text(encoding="utf-8"))
            _TEA["rates"] = {p.get("id"): int(p.get("tip") or 0) for p in cat
                             if isinstance(p, dict) and int(p.get("tip") or 0) > 0}
            _TEA["mt"] = mt
        except Exception:                                    # noqa: BLE001
            pass
    return _TEA["rates"]


def _n(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def order_tea(o: dict, rates: dict = None) -> int:
    """Чай операторов с заказа: «чайные» бутылки по каталогу плюс чай клиента."""
    rates = tea_rates() if rates is None else rates
    t = 0
    for it in o.get("items") or []:
        r = rates.get(it.get("id")) or 0
        q = int(_n(it.get("qty")))
        if r > 0 and q > 0:
            t += r * q
    return t + int(_n(o.get("tip")))


def is_prepaid(o: dict) -> bool:
    """Оплачено мимо водителя: крипта, карта, перевод — денег он не брал."""
    m = str(o.get("payment_method") or "").lower()
    if m in ("crypto", "card", "online", "transfer"):
        return True
    return bool(o.get("paid") or o.get("prepaid") or o.get("crypto_paid"))


def pays_cash(o: dict) -> bool:
    """Деньги за заказ прошли через руки водителя."""
    return str(o.get("payment_method") or "").lower() not in ("free", "debt") and not is_prepaid(o)


def order_money(o: dict) -> dict:
    """Что водитель держит за наличный заказ: {"aed": в дирхамах (для валюты —
    по курсу), "fx": (код, сумма в валюте) или None}."""
    total = _n(o.get("total"))
    s = o.get("settle") or {}
    taken = _n(s.get("taken"), total) if s.get("taken") is not None else total
    fx = s.get("fx") if isinstance(s.get("fx"), dict) else None
    if fx and fx.get("code") and fx.get("amount") is not None:
        return {"aed": taken, "fx": (str(fx["code"]), _n(fx.get("amount")))}
    pf = o.get("pay_fx") or {}
    if pf.get("code") and _n(pf.get("rate")) > 0 and s.get("taken") is None:
        return {"aed": total, "fx": (str(pf["code"]), round(total / _n(pf["rate"]), 2))}
    return {"aed": taken, "fx": None}


def kind_of(x: dict) -> str:
    """Вид записи расхода. У записей до разделения по видам его нет — узнаём
    по комментарию, как driver_routes._kind_of."""
    import expense_routes as _exp
    k = x.get("kind")
    if k in _exp.EXTRA_KINDS:
        return k
    c = (x.get("comment") or "").lower()
    if "бензин" in c or "топлив" in c or "fuel" in c:
        return "fuel"
    if "мойк" in c or "wash" in c:
        return "wash"
    return "other"


def piles(orders: list, extras: list, meal: int = 0) -> dict:
    """Две пачки водителя за день.

    orders — его доставленные заказы дня (любой оплаты); extras — его записи
    расходов без отклонённых; meal — питание за день (0, если не отмечен)."""
    import expense_routes as _exp
    rates = tea_rates()
    aed_in, fx = 0.0, {}
    tea, tea_other, n_cash = 0, 0, 0
    debt_list = []
    for o in orders:
        t = order_tea(o, rates)
        tea += t
        if not pays_cash(o):
            tea_other += t
            # Заказ в долг мимо наличных, но не мимо глаз: товар уехал, денег
            # за него водитель не брал, и без отдельной строки он не понимает,
            # куда делись бутылки и почему сдавать за них нечего (владелец,
            # 21 сен 2026). «Без оплаты» сюда не идёт — тот заказ не учитываем
            # нигде.
            if str(o.get("payment_method") or "").lower() == "debt":
                debt_list.append({"id": str(o.get("order_id") or o.get("_id") or ""),
                                  "who": str(o.get("customer_name") or "").strip(),
                                  "aed": round(_n(o.get("total")), 2)})
            continue
        n_cash += 1
        m = order_money(o)
        if m["fx"]:
            code, amount = m["fx"]
            x = fx.setdefault(code, {"code": code, "sym": FX_SYM.get(code, code), "amount": 0.0, "aed": 0.0})
            x["amount"] += amount
            x["aed"] += m["aed"]
        else:
            aed_in += m["aed"]
    spent, got, bonus, card_spent, card_got = {}, 0, 0, 0, 0
    gotk: dict = {}                     # приход по видам: «Нам вернули», «Мы должны»
    pending = 0
    for x in extras:
        # Заказ в долг: денег по нему никто не брал, и «сдать» от него не
        # меняется — такая запись мимо наличных (владелец, 20 сен 2026).
        if x.get("nocash"):
            continue
        kind = kind_of(x)
        a = int(_n(x.get("amount")))
        plus = bool(_exp.EXTRA_KINDS.get(kind, {}).get("plus"))
        if kind == "upsell":
            bonus += a
            continue
        if _exp.is_card(x):
            if plus:
                card_got += a
            else:
                card_spent += a
            continue
        if plus:
            got += a
            t_ = _exp.EXTRA_KINDS.get(kind, {}).get("t") or x.get("kind_t") or "Приход"
            gotk[t_] = gotk.get(t_, 0) + a
        else:
            t_ = _exp.EXTRA_KINDS.get(kind, {}).get("t") or x.get("kind_t") or "Расход"
            spent[t_] = spent.get(t_, 0) + a
        if (x.get("status") or "approved") == "pending":
            pending += 1
    spent_sum = sum(spent.values())
    fx_aed = sum(v["aed"] for v in fx.values())
    taken = aed_in + fx_aed
    revenue = taken - tea - spent_sum - meal - bonus + got
    r2 = lambda v: round(v, 2)
    return {
        "taken": r2(taken), "taken_aed": r2(aed_in), "orders_cash": n_cash,
        "fx": [{**v, "amount": r2(v["amount"]), "aed": r2(v["aed"])} for v in sorted(fx.values(), key=lambda v: v["code"])],
        "tea": tea, "tea_other": tea_other,
        "spent": [{"t": k, "aed": v} for k, v in spent.items()], "spent_sum": spent_sum,
        "got": got, "got_list": [{"t": k, "aed": v} for k, v in gotk.items()],
        "meal": int(meal or 0), "bonus": bonus,
        "card_spent": card_spent, "card_got": card_got, "pending": pending,
        # В долг: сумма, сколько заказов и какие — для строки-объяснения.
        "debt": r2(sum(x["aed"] for x in debt_list)), "debt_n": len(debt_list),
        "debt_list": debt_list,
        # Сдать: выручка (дирхамы + валюта как есть) и чай — порознь.
        "revenue": r2(revenue), "revenue_aed": r2(revenue - fx_aed),
        # Остаётся у водителя: питание и бонус за допродажу.
        "keep": int(meal or 0) + bonus,
        # Всего наличных в руках: обе пачки и своё.
        "in_hand": r2(revenue + tea + int(meal or 0) + bonus),
    }
