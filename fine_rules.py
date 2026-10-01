"""Правила штрафов: что считается нарушением и сколько за него берут.

Владелец, 1 окт 2026 — из разбора штрафного листа: за всё время через лист
назначили один штраф, цены были только у превышения скорости, а сам список
нарушений лежал в коде приложения. «1, 2, 7» — наполнить прейскурант и сделать
его редактируемым в STAR, штраф из места нарушения, карточка дисциплины.

Разделы — постоянные (у каждого свой значок в приложении). Нарушения внутри
раздела старший добавляет, переименовывает и убирает сам; у нарушения либо одна
сумма, либо степени со своими суммами, либо ничего — тогда сумму вписывают при
назначении. Раздел «Автоматические» — то, что программа замечает сама
(fines_auto.py): там правится только сумма, названия и состав — за программой.

Хранится одним документом в settings (_id = fine_rules). Нет документа —
действует список по умолчанию, тот же, что стоял в приложении.
"""
import logging
import re
from datetime import datetime, timezone

import db

log = logging.getLogger(__name__)

SYS = "sys"
DEFAULT = [
    {"id": "auto", "t": "Авто", "items": [
        {"id": "speed", "t": "Превышение скорости", "tiers": [
            {"t": "20 – 30 км/ч", "amount": 600}, {"t": "31 – 50 км/ч", "amount": 1000},
            {"t": "51 – 70 км/ч", "amount": 1500}, {"t": "71 – 100 км/ч", "amount": 2500},
            {"t": "Более 100 км/ч", "amount": 4000}]},
        {"id": "pdd", "t": "Несоблюдение ПДД"},
        {"id": "crash", "t": "Авария по вине водителя"},
        {"id": "damage", "t": "Повреждение автомобиля"},
        {"id": "parking", "t": "Неправильная парковка"},
        {"id": "phone", "t": "Использование телефона за рулём"},
        {"id": "other", "t": "Другое"}]},
    {"id": "docs", "t": "Документы", "items": []},
    {"id": "client", "t": "Клиент", "items": []},
    {"id": "discipline", "t": "Дисциплина", "items": []},
    {"id": "look", "t": "Внешний вид", "items": []},
    {"id": "misc", "t": "Другое", "items": []},
    # То, что замечает программа. Сумма 0 — «впишут при решении».
    {"id": SYS, "t": "Автоматические", "items": [
        {"id": "geo_off", "t": "Отключение геолокации", "amount": 200},
        {"id": "exp_rejected", "t": "Отклонённый расход", "amount": 0},
        {"id": "noscan_debt", "t": "Приёмка без сканирования не досканирована", "amount": 0},
        {"id": "shift_left", "t": "Смена не закрыта", "amount": 0}]},
]
CAT_IDS = [c["id"] for c in DEFAULT]
MAX_ITEMS, MAX_TIERS, MAX_AMOUNT = 40, 8, 100_000

_cache = {"doc": None}


def _amount(v) -> int:
    try:
        n = int(round(float(str(v).replace(" ", "").replace(",", "."))))
    except (TypeError, ValueError):
        return 0
    return n if 0 < n <= MAX_AMOUNT else 0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(s or "").lower())[:24]


def clean(cats) -> list:
    """Привести присланное к виду, которому можно верить. Разделы — только
    наши и в нашем порядке; в «Автоматических» из присланного берём одну
    сумму. Мусор молча отбрасываем: это форма, а не договор."""
    got = {str(c.get("id") or ""): c for c in (cats or []) if isinstance(c, dict)}
    out = []
    for base in DEFAULT:
        src = got.get(base["id"]) or {}
        items = []
        if base["id"] == SYS:
            sums = {str(i.get("id") or ""): _amount(i.get("amount"))
                    for i in (src.get("items") or []) if isinstance(i, dict)}
            for i in base["items"]:
                items.append({**i, "amount": sums.get(i["id"], i.get("amount", 0))
                              if i["id"] in sums else i.get("amount", 0)})
        else:
            seen = set()
            for n, i in enumerate((src.get("items") if "items" in src else base["items"]) or []):
                if not isinstance(i, dict) or len(items) >= MAX_ITEMS:
                    continue
                t = " ".join(str(i.get("t") or "").split())[:60]
                if not t:
                    continue
                iid = _slug(i.get("id")) or f"v{n}"
                while iid in seen:
                    iid += "x"
                seen.add(iid)
                row = {"id": iid, "t": t}
                tiers = []
                for tr in (i.get("tiers") or [])[:MAX_TIERS]:
                    if not isinstance(tr, dict):
                        continue
                    tt, ta = " ".join(str(tr.get("t") or "").split())[:40], _amount(tr.get("amount"))
                    if tt and ta:
                        tiers.append({"t": tt, "amount": ta})
                if tiers:
                    row["tiers"] = tiers
                elif _amount(i.get("amount")):
                    row["amount"] = _amount(i.get("amount"))
                items.append(row)
        out.append({"id": base["id"], "t": base["t"], "items": items})
    return out


async def get() -> list:
    """Действующие правила: сохранённые или по умолчанию."""
    try:
        doc = await db.setting_get("fine_rules")
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] правила не прочитаны, беру прежние: {e}")
        doc = _cache["doc"]
    _cache["doc"] = doc
    return clean((doc or {}).get("cats")) if doc else clean(DEFAULT)


async def save(cats, by: str = "") -> list:
    good = clean(cats)
    await db.setting_set("fine_rules", {"cats": good, "by": str(by or "")[:60],
                                        "at": datetime.now(timezone.utc)})
    _cache["doc"] = {"cats": good}
    log.info(f"[fines] правила сохранены · {by or '—'} · нарушений: "
             f"{sum(len(c['items']) for c in good)}")
    return good


async def amount(sys_id: str, default: int = 0) -> int:
    """Сумма за нарушение, которое замечает программа."""
    try:
        for c in await get():
            if c["id"] == SYS:
                for i in c["items"]:
                    if i["id"] == sys_id:
                        return int(i.get("amount") or 0)
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[fines] сумма {sys_id} не прочитана: {e}")
    return default
