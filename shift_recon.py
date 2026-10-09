"""Сверка смены: водитель ↔ оператор ↔ старший (владелец, 9 окт 2026).

Повод. За 27 сен на B2 водители сдали 6 200 по бумажке, приложение показало
5 900: забытая бутылка никому не видна, пока старший не пересчитает деньги
руками. Факта в системе не было нигде. Владелец: «полная система сверки — по
сумме и по позициям, водитель поправляет оператора, без новых вкладок».

Как это устроено
----------------
Водитель перед закрытием смены сверяет ТРИ ШАГА — деньги, заказы, товар:
  1. Деньги: что сдать старшему (выручка, чай, валюта как есть) и сколько
     наличных у него на руках по приложению — он вписывает, сколько насчитал;
  2. Заказы: список заказов смены;
  3. Товар: что продано по позициям; «Поправить» — степперы и «Добавить
     позицию», правки уходят оператору и НЕ применяются сами.
«Всё верно» ставит отметку на шаг; между шагами ходят свободно, отметка
снимается тем же местом. Все три отмечены — итоги подтверждены.

Несовпадение (наличных не столько, сколько по приложению, или правки товара)
— оператору яркое сообщение в бот и мигающий район в панели, пока не снято:
  • правку он принимает («+2 Red Label» — заказ той же смены этим водителем)
    или отклоняет; «−1» — правит заказ руками и жмёт «Готово»;
  • по деньгам — «Так и есть»: наличных столько, факт записан за водителем.
Приложение само не меняется: только через заказы. Подтверждённый факт
уходит старшему в «Сбор выручки» (по приложению · по факту · разница), а
разница — в книгу дня строкой «Собрал по факту»; недостача — на решение в
штрафы (cash_short), излишек горит в чек-листе, пока владелец не посмотрит.
Последнее слово за тем, кто считал деньги руками: старший вписывает «Сдали
по факту» в карточке района.

Валюта в дирхамы не пересчитывается (владелец, 9 окт): «на руках» и «сдать»
— только дирхамы, доллары отдаются как есть.
"""
import logging
from datetime import datetime, timezone, timedelta

import db
import bizday
import cash_math
import config_staff as staff
from config_offices import OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger("recon")
STEPS = (1, 2, 3)
MAX_FIX = 30
DUBAI_TZ = timezone(timedelta(hours=4))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _i(v, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _hm(v) -> str:
    dt = bizday.parse_ts(v)
    return dt.astimezone(DUBAI_TZ).strftime("%H:%M") if dt else ""


def _fmt(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


# ── что по приложению ───────────────────────────────────────────────────────

def mine(orders: list, name: str, day: str) -> list:
    """Доставленные заказы этого водителя за учётный день."""
    return [o for o in orders if (o.get("driver") or "").strip() == name
            and o.get("status") == "delivered" and bizday.order_day(o) == day]


def cash_app(orders: list, driver_day: dict | None, name: str, day: str) -> int:
    """Дирхамы на руках по приложению — без валюты (валюта сдаётся как есть).
    Та же арифметика, что в итогах смены водителя и в «Сборе выручки»
    (cash_math.piles)."""
    d = driver_day or {}
    extras = [x for x in (d.get("extras") or []) if (x.get("status") or "approved") != "rejected"]
    meal = staff.meal_of(d) if d.get("working") is not None else 0
    h = cash_math.piles(mine(orders, name, day), extras, meal)
    return _i(h["in_hand"] - sum(x["aed"] for x in h["fx"]))


def _catalog() -> tuple[dict, dict]:
    """{id: позиция}, {id: место в каталоге} — порядок полок как у операторов."""
    try:
        from operator_routes import _load_catalog
        cat = _load_catalog()
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[recon] каталог не прочитан: {e}")
        cat = []
    by_id = {p.get("id"): p for p in cat if isinstance(p, dict)}
    return by_id, {p.get("id"): i for i, p in enumerate(cat) if isinstance(p, dict)}


def sold_lines(orders: list) -> list:
    """Продано за смену по позициям: [{id, name, cat, qty, aed, pack}] в порядке
    каталога. Пиво — упаковками (pcs), как в заказе."""
    by_id, idx = _catalog()
    acc: dict = {}
    for o in orders:
        for it in o.get("items") or []:
            pid = str(it.get("id") or "")
            if not pid:
                continue
            pcs = _i(it.get("pcs")) or 0
            key = (pid, pcs)
            p = by_id.get(pid) or {}
            r = acc.setdefault(key, {"id": pid, "name": p.get("name") or str(it.get("name") or "").split(" ×")[0],
                                     "cat": p.get("cat") or "", "qty": 0, "aed": 0.0,
                                     **({"pack": pcs} if pcs else {})})
            q = _i(it.get("qty"))
            r["qty"] += q
            try:
                r["aed"] += float(it.get("line_total") if it.get("line_total") is not None
                                  else float(it.get("price") or 0) * q)
            except (TypeError, ValueError):
                pass
    rows = sorted(acc.values(), key=lambda r: (idx.get(r["id"], 10_000), r.get("pack") or 0))
    for r in rows:
        r["aed"] = _i(r["aed"])
    return rows


def pay_label(o: dict) -> str:
    split = cash_math.parts_label(o)
    if split:
        return split
    m = str(o.get("payment_method") or "").lower()
    if m == "free":
        return "без оплаты"
    if m == "debt":
        return "в долг"
    if m == "crypto" or o.get("crypto_paid"):
        return "крипта"
    if m == "transfer":
        return "перевод"
    if cash_math.is_prepaid(o):
        return "в приложении"
    return ""


def order_rows(orders: list) -> list:
    """Заказы смены строками: время · адрес · способ оплаты · сумма."""
    out = []
    for o in sorted(orders, key=lambda o: str(o.get("delivered_at") or o.get("timestamp") or "")):
        out.append({"id": str(o.get("order_id") or ""),
                    "t": _hm(o.get("delivered_at") or o.get("confirmed_at") or o.get("timestamp")),
                    "a": str(o.get("address") or o.get("customer_name") or "").strip() or f"#{o.get('order_id') or ''}",
                    "pay": pay_label(o), "aed": _i(o.get("total"))})
    return out


# ── документ сверки ─────────────────────────────────────────────────────────

def _ok_set(doc: dict) -> set:
    out = set()
    for x in (doc or {}).get("ok") or []:
        try:
            if int(x) in STEPS:
                out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


def view(doc: dict | None, cash_now: int | None) -> dict:
    """Состояние сверки для приложений. diff — наличные по счёту водителя минус
    по приложению СЕЙЧАС (принятая правка меняет приложение — разница тает)."""
    d = doc or {}
    ok = sorted(_ok_set(d))
    cash = d.get("cash")
    diff = None if cash is None or cash_now is None else _i(cash) - _i(cash_now)
    fixes = [{"pid": f.get("pid"), "name": f.get("name") or "", "delta": _i(f.get("delta")),
              **({"pcs": _i(f.get("pcs"))} if f.get("pcs") else {}),
              "at": f.get("at") or "", "ok": f.get("ok"), "by": f.get("by") or "",
              "order_id": f.get("order_id") or ""} for f in d.get("fixes") or []]
    fix_open = sum(1 for f in fixes if f["ok"] is None)
    confirmed = bool(d.get("confirmed_at"))
    # Несовпадение по деньгам — после отметки шага «Деньги», пока оператор не
    # подтвердил факт; правки — пока есть без ответа.
    mismatch = 1 in ok and diff not in (None, 0) and d.get("op_fact") is None
    return {"ok": ok, "cash": None if cash is None else _i(cash), "cash_app": cash_now, "diff": diff,
            "confirmed": confirmed, "confirmed_at": d.get("confirmed_at") or "",
            "fixes": fixes, "fixes_status": d.get("fixes_status") or "", "fix_open": fix_open,
            "op_fact": d.get("op_fact"), "op_gap": d.get("op_gap"),
            "op_fact_by": d.get("op_fact_by") or "", "op_fact_at": d.get("op_fact_at") or "",
            "mismatch": bool(mismatch), "alert": bool(mismatch or fix_open),
            "started": bool(ok or cash is not None or fixes)}


async def driver_apply(name: str, day: str, district: str, body: dict, cash_now: int) -> dict:
    """Действие водителя: {cash} — вписал наличные; {step, ok} — отметка на
    шаг; {fixes:[{pid, delta, pcs}]} — правки товара оператору (пустой список —
    отозвать). ValueError — что не так."""
    doc = await db.recon_get(day, name) or {"day": day, "driver": name, "district": district,
                                             "ok": [], "fixes": [], "fixes_status": ""}
    if district and not doc.get("district"):
        doc["district"] = district
    ok = _ok_set(doc)
    if "cash" in body:
        cash = body.get("cash")
        if cash is None or str(cash).strip() == "":
            doc["cash"] = None
        else:
            v = _i(cash, -1)
            if v < 0 or v > 999_999:
                raise ValueError("bad_cash")
            doc["cash"] = v
        doc["cash_app"] = cash_now
        ok.discard(1)
    if "step" in body:
        step = _i(body.get("step"))
        if step not in STEPS:
            raise ValueError("bad_step")
        if body.get("ok"):
            if step == 1 and doc.get("cash") is None:
                raise ValueError("need_cash")
            if step == 3 and doc.get("fixes_status") == "sent":
                raise ValueError("fixes_open")
            ok.add(step)
            doc.setdefault("ok_at", {})[str(step)] = _now_iso()
            if step == 1:
                doc["cash_app"] = cash_now
        else:
            ok.discard(step)
    if "fixes" in body:
        by_id, _ = _catalog()
        was = doc.get("fixes") or []
        fixes = []
        for f in (body.get("fixes") or [])[:MAX_FIX]:
            pid = str((f or {}).get("pid") or "")
            delta = _i((f or {}).get("delta"))
            pcs = _i((f or {}).get("pcs")) or 0
            if not pid or pid not in by_id or delta == 0 or abs(delta) > 99:
                continue
            if any(x["pid"] == pid and (x.get("pcs") or 0) == pcs for x in fixes):
                continue
            prev = next((x for x in was if x.get("pid") == pid and _i(x.get("delta")) == delta
                         and (_i(x.get("pcs")) or 0) == pcs), None)
            fixes.append(prev or {"pid": pid, "name": by_id[pid].get("name") or "", "delta": delta,
                                  **({"pcs": pcs} if pcs else {}), "at": _now_iso(), "ok": None})
        doc["fixes"] = fixes
        doc["fixes_status"] = ("sent" if any(f.get("ok") is None for f in fixes)
                               else "done" if fixes else "")
        ok.discard(3)
    doc["ok"] = sorted(ok)
    if set(STEPS) <= ok:
        doc["confirmed_at"] = doc.get("confirmed_at") or _now_iso()
    else:
        doc["confirmed_at"] = ""
    doc["updated_at"] = _now_iso()
    saved = await db.recon_put(day, name, doc)
    return saved or doc


def alert_text(doc: dict, v: dict) -> str:
    """Яркое сообщение оператору (эмодзи здесь можно — это телеграм)."""
    oid = doc.get("district") or ""
    где = f"{OFFICE_CODES.get(oid, '')} · {doc.get('driver') or ''}".strip(" ·")
    lines = [f"🔴 <b>Сверка смены · {где}</b>"]
    if v["mismatch"]:
        d = v["diff"]
        lines.append(f"Наличных на руках <b>{_fmt(v['cash'])}</b> · по приложению {_fmt(v['cash_app'])} · "
                     f"<b>{'+' if d > 0 else '−'}{_fmt(abs(d))} AED</b>")
    open_f = [f for f in v["fixes"] if f["ok"] is None]
    if open_f:
        lines.append("Правки товара:")
        for f in open_f:
            lines.append(f"{'+' if f['delta'] > 0 else '−'}{abs(f['delta'])} {f['name']}"
                         + (f" ×{f['pcs']}" if f.get("pcs") else ""))
    lines.append("Откройте «Итоги» в панели оператора.")
    return "\n".join(lines)


async def maybe_alert(doc: dict, cash_now: int, test: bool = False) -> bool:
    """Несовпадение изменилось — оператору района яркое сообщение. То же самое
    второй раз не шлём (alert_sig)."""
    v = view(doc, cash_now)
    if not v["alert"] or test:
        return False
    sig = (f"{v['diff'] if v['mismatch'] else ''}|"
           + ",".join(f"{f['pid']}:{f['delta']}:{f.get('pcs') or 0}" for f in v["fixes"] if f["ok"] is None))
    if (doc.get("alert_sig") or "") == sig:
        return False
    try:
        import op_route
        await op_route.send(alert_text(doc, v), district=doc.get("district") or "", register=False)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[recon] оператору не сказали ({doc.get('driver')}): {e}")
        return False
    await db.recon_put(doc["day"], doc["driver"], {"alert_sig": sig, "alert_at": _now_iso()})
    log.info(f"[recon] {doc.get('driver')} {doc.get('day')}: оператору — {sig}")
    return True


# ── оператор ────────────────────────────────────────────────────────────────

async def op_fact(day: str, name: str, by: str, cash_now: int) -> dict | None:
    """«Так и есть»: наличных у водителя столько, сколько он насчитал. Разница
    с приложением на этот момент — факт за водителем."""
    doc = await db.recon_get(day, name)
    if not doc or doc.get("cash") is None:
        return None
    gap = _i(doc["cash"]) - _i(cash_now)
    saved = await db.recon_put(day, name, {"op_fact": _i(doc["cash"]), "op_gap": gap, "op_cash_app": _i(cash_now),
                                           "op_fact_by": by, "op_fact_at": _now_iso()})
    log.info(f"[recon] {name} {day}: оператор {by} подтвердил наличные {doc['cash']} · разница {gap:+d}")
    return saved


async def op_fact_undo(day: str, name: str) -> dict | None:
    doc = await db.recon_get(day, name)
    if not doc:
        return None
    return await db.recon_put(day, name, {"op_fact": None, "op_gap": None, "op_fact_by": "", "op_fact_at": ""})


async def op_fix(day: str, name: str, pid: str, delta: int, pcs: int, ok: bool, by: str,
                 order_id: str = "") -> dict | None:
    """Ответ оператора на правку товара: принял (с номером созданного заказа,
    если он его создал) или отклонил. Все отвечены — fixes_status = done."""
    doc = await db.recon_get(day, name)
    if not doc:
        return None
    fixes, hit = [], False
    for f in doc.get("fixes") or []:
        if f.get("pid") == pid and _i(f.get("delta")) == _i(delta) and (_i(f.get("pcs")) or 0) == (pcs or 0):
            f = {**f, "ok": bool(ok), "by": by, "answered_at": _now_iso(),
                 **({"order_id": order_id} if order_id else {})}
            hit = True
        fixes.append(f)
    if not hit:
        return None
    status = "sent" if any(f.get("ok") is None for f in fixes) else "done"
    return await db.recon_put(day, name, {"fixes": fixes, "fixes_status": status})


# ── сводки для панели, STAR и закрытия района ───────────────────────────────

async def day_docs(day: str) -> dict:
    """{водитель: документ} за день."""
    try:
        return {d.get("driver"): d for d in await db.recon_for_day(day)}
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[recon] сверки за {day} не прочитаны: {e}")
        return {}


async def day_views(day: str, orders: list, driver_days: dict | None = None) -> dict:
    """{водитель: view} — с разницей по приложению СЕЙЧАС."""
    docs = await day_docs(day)
    if not docs:
        return {}
    if driver_days is None:
        try:
            driver_days = {r.get("driver"): r for r in await db.get_driver_days(day)}
        except Exception as e:                               # noqa: BLE001
            log.warning(f"[recon] дни водителей за {day}: {e}")
            driver_days = {}
    out = {}
    for name, doc in docs.items():
        v = view(doc, cash_app(orders, driver_days.get(name), name, day))
        v["district"] = doc.get("district") or staff.base_district(name) or ""
        v["name"] = name
        out[name] = v
    return out


def _district_of(v: dict, name: str) -> str:
    return v.get("district") or staff.base_district(name) or ""


async def alerts_for(day: str, scope: set | None, orders: list) -> list:
    """Районы, где горит сверка: [{district, code, drivers: [{name, diff, fixes}]}].
    Для опроса панели — она мигает районом, пока несовпадение не снято."""
    views = await day_views(day, orders)
    by: dict = {}
    for name, v in views.items():
        if not v["alert"]:
            continue
        oid = _district_of(v, name)
        if scope is not None and oid not in scope:
            continue
        by.setdefault(oid, []).append({"name": name, "diff": v["diff"] if v["mismatch"] else None,
                                       "fixes": v["fix_open"]})
    return [{"district": oid, "code": OFFICE_CODES.get(oid, ""), "drivers": ds}
            for oid, ds in sorted(by.items())]


async def district_problems(day: str, district: str, on_shift: list, orders: list) -> list:
    """Что мешает закрыть район с чистой совестью: кто итоги не подтвердил, у
    кого несовпадение без ответа оператора, чьи правки без ответа.
    [{driver, kind: unconfirmed|cash|fixes, diff, n}]."""
    views = await day_views(day, orders)
    names = list(on_shift or [])
    for name, v in views.items():
        if _district_of(v, name) == district and name not in names:
            names.append(name)
    out = []
    for name in names:
        v = views.get(name)
        if not v:
            out.append({"driver": name, "kind": "unconfirmed"})
            continue
        if v["mismatch"]:
            out.append({"driver": name, "kind": "cash", "diff": v["diff"]})
        if v["fix_open"]:
            out.append({"driver": name, "kind": "fixes", "n": v["fix_open"]})
        if not v["confirmed"] and not v["mismatch"] and not v["fix_open"]:
            out.append({"driver": name, "kind": "unconfirmed"})
    return out


def problems_text(problems: list) -> str:
    """Одной фразой для окна-вопроса оператора."""
    parts = []
    for p in problems:
        if p["kind"] == "cash":
            d = _i(p.get("diff"))
            parts.append(f"{p['driver']}: наличных {'+' if d > 0 else '−'}{_fmt(abs(d))}")
        elif p["kind"] == "fixes":
            n = _i(p.get("n"))
            parts.append(f"{p['driver']}: {n} {'правка' if n == 1 else 'правки' if 1 < n < 5 else 'правок'} без ответа")
        else:
            parts.append(f"{p['driver']}: итоги не подтвердил")
    return "; ".join(parts)


async def star_rows(day: str, orders: list, marks: dict) -> dict:
    """Для «Сбора выручки»: {район: {fact, fact_by, fact_at, gap, gap_src, rows}}.
    fact — вписанное старшим («Сдали по факту», отметка cashfact:район);
    без него разница складывается из подтверждённых оператором фактов."""
    views = await day_views(day, orders)
    out: dict = {}
    for name, v in views.items():
        oid = _district_of(v, name)
        r = out.setdefault(oid, {"rows": [], "op_gap": 0, "op_n": 0})
        r["rows"].append({"name": name, "cash": v["cash"], "cash_app": v["cash_app"], "diff": v["diff"],
                          "ok": len(v["ok"]), "confirmed": v["confirmed"], "confirmed_at": v["confirmed_at"],
                          "op_fact": v["op_fact"], "op_gap": v["op_gap"], "op_fact_by": v["op_fact_by"],
                          "op_fact_at": v["op_fact_at"],
                          "fixes": v["fixes"], "fix_open": v["fix_open"], "mismatch": v["mismatch"],
                          "alert": v["alert"]})
        if v["op_fact"] is not None:
            r["op_gap"] += _i(v["op_gap"])
            r["op_n"] += 1
    for oid, r in out.items():
        r["rows"].sort(key=lambda x: x["name"])
    res = {}
    for oid in set(out) | {k[9:] for k in (marks or {}) if k.startswith("cashfact:")}:
        r = out.get(oid) or {"rows": [], "op_gap": 0, "op_n": 0}
        m = (marks or {}).get(f"cashfact:{oid}") or {}
        fact = m.get("fact")
        res[oid] = {"fact": None if fact is None else _i(fact), "fact_by": m.get("by") or "",
                    "fact_at": m.get("at") or "", "fact_gap": None if fact is None else _i(m.get("gap")),
                    "op_gap": r["op_gap"], "op_n": r["op_n"], "rows": r["rows"]}
    return res


async def book_gaps(day_from: str, day_to: str) -> dict:
    """{день: разница с приложением} для книги дня — там, где факт известен:
    вписанное старшим по району (cashfact, с его разницей) главнее
    подтверждённого оператором."""
    days = []
    try:
        d0 = datetime.strptime(day_from, "%Y-%m-%d")
        d1 = datetime.strptime(day_to, "%Y-%m-%d")
    except ValueError:
        return {}
    while d0 <= d1:
        days.append(d0.strftime("%Y-%m-%d"))
        d0 += timedelta(days=1)
    try:
        marks_all = await db.checklist_many(days)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[recon] отметки факта: {e}")
        marks_all = {}
    try:
        docs = await db.recon_between(day_from, day_to)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[recon] сверки {day_from}–{day_to}: {e}")
        docs = []
    out: dict = {}
    star = {}
    for day, marks in (marks_all or {}).items():
        for k, m in (marks or {}).items():
            if k.startswith("cashfact:") and (m or {}).get("fact") is not None:
                star[(day, k[9:])] = _i((m or {}).get("gap"))
                out[day] = out.get(day, 0) + _i((m or {}).get("gap"))
    for d in docs:
        if d.get("op_fact") is None:
            continue
        oid = d.get("district") or staff.base_district(d.get("driver") or "") or ""
        if (d.get("day"), oid) in star:
            continue                                          # старший перебил своим числом
        out[d["day"]] = out.get(d["day"], 0) + _i(d.get("op_gap"))
    return out


async def checklist_items(day: str, orders: list) -> list:
    """Строка чек-листа «Сверка смены»: несовпадения без ответа оператора,
    правки без ответа и подтверждённые факты с разницей (излишек горит, пока
    владелец не посмотрит). [{text, bad}]."""
    views = await day_views(day, orders)
    out = []
    for name, v in sorted(views.items()):
        кто = " ".join(x for x in (OFFICE_CODES.get(_district_of(v, name), ""), name) if x)
        if v["mismatch"]:
            d = v["diff"]
            out.append({"text": f"{кто} {'+' if d > 0 else '−'}{_fmt(abs(d))} — ждёт оператора", "bad": True})
        if v["fix_open"]:
            out.append({"text": f"{кто} · {v['fix_open']} правк. без ответа", "bad": True})
        if v["op_fact"] is not None and _i(v["op_gap"]):
            g = _i(v["op_gap"])
            out.append({"text": f"{кто} {'+' if g > 0 else '−'}{_fmt(abs(g))} по факту", "bad": g < 0})
    return out
