"""Заявка на перемещение между районами (владелец, 18 сен 2026).

Сканирует тот, кто ОТДАЁТ, а принимает — кому везут (владелец, 18 сен 2026,
вечером: «водитель Алгусеса сканирует товар, который надо переместить с его
района, и отдаёт водителю, который приехал с Тикома»; и следом: «заявка
отобразится у двух сторон; у отдающей она активная — нажать „Начать
перемещение“, потом сканировать; после того как всё отсканировал водитель
отдающей стороны, у принимающего заявка из серой становится активной, и
появляется кнопка „Принял“ / „Принял неровно“ — заметнее „Принял“»).

  • Работа — передача: пара «откуда → куда». Хранится заявка по району-
    ПОЛУЧАТЕЛЮ (tasks.<куда>), строки — с районом «откуда», а у каждой пары
    своя передача: tasks.<куда>.give.<откуда>.
  • Отдающий: «Начать перемещение», потом сканирует каждую бутылку, пока
    передаёт её приехавшему водителю. Скан и есть передача: тем же сканом
    бутылка уходит с остатка отдающего на остаток получателя. Отдавать может
    любой водитель района — кто сканировал последним, тот и видится отдающим.
  • Получатель: пока отдают, карточка серая — смотреть, не нажимать. Всё
    отсканировали — «Принял» (главная) или «Принял неровно»: сколько пришло
    на самом деле и что не так. Расхождение склад сам не двигает — какой именно
    бутылки нет, неизвестно; его видят старший и оператор и решают.
  • Задача района закрывается, когда он принял передачи от всех, заявка —
    когда закрыты все задачи.

Недобора не бывает. Владелец: «водитель приезжает прямо на билдинг и забирает
эти бутылки, ничего на следующий раз не должно оставаться, прям в тот же день».
Поэтому кнопки «отдать не всё» нет, а смену не закрывает ни район, который
отдаёт (пока не отсканировал всё), ни район, который забирает (пока не принял),
см. pending_for_district. Застряло — бутылки на полке нет — снимает старший.

Склад двигается в момент скана, а не в конце: бутылку физически передали, и
остаток обоих районов обязан это показывать сразу. Ядро переезда — общий
stock_routes.move_by_code, тот же, что у старшего.

Счёт строки не уходит за заявку даже при двух сканах в одну секунду (у района
два-три водителя, и отдавать могут двое сразу): место в строке занимается
атомарно ДО переезда (db.move_line_reserve), а если бутылка не переехала —
возвращается.
"""
import json
import logging
import math
from datetime import datetime, timezone

from aiohttp import web

import db
import stock_routes as sr
from config_offices import OFFICE_IDS, OFFICE_CODES, OFFICE_NAMES

log = logging.getLogger("ambar.move")
CORS_HEADERS = sr.CORS_HEADERS


def _now():
    return datetime.now(timezone.utc)


def _iso(v) -> str:
    return "" if not v else (v if isinstance(v, str) else v.isoformat())


async def _mid() -> str:
    """MV260918-020134 — по дате, как у заявки магазину. Две заявки в одну
    секунду получили бы один номер: добавляем букву, пока номер занят."""
    base = "MV" + _now().astimezone(sr.DUBAI_TZ).strftime("%y%m%d-%H%M%S")
    mid = base
    for suffix in "abcdefghij":
        if not await db.move_order_get(mid):
            return mid
        mid = base + suffix
    return base + _now().strftime("%f")[:3]


def _line_view(l: dict) -> dict:
    need = float(l.get("qty") or 0)
    got = float(l.get("got") or 0)
    return {"from": l.get("from"), "from_code": OFFICE_CODES.get(l.get("from"), ""),
            "from_name": OFFICE_NAMES.get(l.get("from"), ""),
            "id": l.get("id"), "name": l.get("name", ""), "unit": int(l.get("unit") or 1),
            "qty": sr._num(need), "got": sr._num(got), "left": sr._num(max(0.0, need - got)),
            "done": got >= need - 1e-9}


def _pair_status(task: dict, g: dict, need: float, got: float) -> str:
    """Где передача пары «откуда → куда»:
      wait — никто не начал; pause — начали и бросили (отдано не всё, никто не
      отдаёт); live — отдают; given — отсканировано всё, ждёт «Принял»;
      done — принято; diff — принято неровно; cancelled — задачу сняли."""
    if task.get("cancelled_at"):
        return "cancelled"
    if g.get("accepted_at"):
        return "done" if g.get("accept_ok", True) else "diff"
    if got >= need - 1e-9:
        return "given"
    if g.get("driver"):
        return "live"
    return "pause" if got > 0 else "wait"


def _senior_of(g: dict) -> str:
    """Кто из STAR взял передачу пары на себя (владелец, 18 сен 2026)."""
    return ((g or {}).get("senior") or {}).get("name") or ""


def _accept_view(g: dict) -> dict:
    return {"accepted_at": _iso(g.get("accepted_at")), "accepted_by": g.get("accepted_by") or "",
            "accept_ok": bool(g.get("accept_ok", True)) if g.get("accepted_at") else None,
            "accept_lines": g.get("accept_lines") or [], "accept_note": g.get("accept_note") or ""}


def _sources(lines: list, task: dict) -> list:
    """Строки задачи по районам, откуда их отдают: передача каждой пары."""
    give = task.get("give") or {}
    by_src = {}
    for l in lines:
        s = by_src.setdefault(l["from"], {"district": l["from"], "code": l["from_code"],
                                          "name": l["from_name"], "qty": 0.0, "got": 0.0,
                                          "lines": []})
        s["qty"] += l["qty"]; s["got"] += l["got"]; s["lines"].append(l)
    for src, s in by_src.items():
        g = give.get(src) or {}
        s["status"] = _pair_status(task, g, s["qty"], s["got"])
        s["done"] = s["got"] >= s["qty"] - 1e-9          # отсканировано всё
        s["left"] = sr._num(max(0.0, s["qty"] - s["got"]))
        s["qty"] = sr._num(s["qty"]); s["got"] = sr._num(s["got"])
        s["giver"] = g.get("driver") or ""
        s["given_at"] = _iso(g.get("done_at"))
        s["senior"] = _senior_of(g)
        s.update(_accept_view(g))
    return sorted(by_src.values(), key=lambda s: s["code"])


def task_view(mid: str, doc: dict, oid: str, task: dict, me: str = "") -> dict:
    """Задача района-получателя — для STAR и оператора: откуда ему отдают,
    кто отдаёт, что принято."""
    lines = [_line_view(l) for l in (task.get("lines") or [])]
    need = sum(l["qty"] for l in lines)
    got = sum(l["got"] for l in lines)
    sources = _sources(lines, task)
    driver = task.get("driver") or ""
    # «Отдают» — только пока кто-то отдаёт (или бросил на середине). Отдали и
    # приняли у одного, остальные ещё не начинали — это «ждёт отдающих».
    live_ = any(s["status"] in ("live", "pause") for s in sources)
    return {
        "move_id": mid, "day": doc.get("day", ""), "at": _iso(doc.get("at")),
        "by": doc.get("by", ""), "note": doc.get("note", ""),
        "district": oid, "district_code": OFFICE_CODES.get(oid, ""),
        "district_name": OFFICE_NAMES.get(oid, oid),
        "driver": driver, "mine": bool(me) and driver == me,
        "claimed_at": _iso(task.get("claimed_at")), "started_at": _iso(task.get("started_at")),
        "done_at": _iso(task.get("done_at")), "cancelled_at": _iso(task.get("cancelled_at")),
        "need": sr._num(need), "got": sr._num(got), "left": sr._num(max(0.0, need - got)),
        "positions": len(lines), "left_positions": sum(1 for l in lines if not l["done"]),
        "sources": sources,
        "givers": sorted({s["giver"] for s in sources if s["giver"]}),
        # Кто из STAR взял передачи этого района на себя — по районам, откуда везут.
        "seniors": sorted({s["senior"] for s in sources if s["senior"]}),
        # Приняли неровно хотя бы у одного отдающего — старшему решать.
        "diff": any(s["status"] == "diff" for s in sources),
        "lines": lines,
        "status": ("cancelled" if task.get("cancelled_at") else
                   "done" if task.get("done_at") else
                   "given" if lines and got >= need - 1e-9 else
                   "live" if live_ else "free"),
    }


def give_view(mid: str, doc: dict, oid: str, task: dict, src: str, me: str = "") -> dict:
    """Передача одной пары «src → oid» — карточка у обеих сторон: у отдающего
    («Отдать в …») и у получателя («Забрать из …»)."""
    lines = [_line_view(l) for l in (task.get("lines") or []) if l.get("from") == src]
    need = sum(l["qty"] for l in lines)
    got = sum(l["got"] for l in lines)
    left = max(0.0, need - got)
    g = (task.get("give") or {}).get(src) or {}
    giver = g.get("driver") or ""
    return {
        "move_id": mid, "day": doc.get("day", ""), "at": _iso(doc.get("at")),
        "by": doc.get("by", ""), "note": doc.get("note", ""),
        # district — ключ задачи (район-получатель): им зовут ручки.
        "district": oid, "district_code": OFFICE_CODES.get(oid, ""),
        "district_name": OFFICE_NAMES.get(oid, oid),
        "to_code": OFFICE_CODES.get(oid, ""), "to_name": OFFICE_NAMES.get(oid, oid),
        "from": src, "from_code": OFFICE_CODES.get(src, ""), "from_name": OFFICE_NAMES.get(src, src),
        "giver": giver, "mine": bool(me) and giver == me,
        # Передачу взял на себя старший (тот, кто в AMBAR STAR): забирает он.
        "senior": _senior_of(g),
        # driver — у приложения, открытого до обновления, это «кто едет».
        "driver": task.get("driver") or "",
        "started_at": _iso(g.get("claimed_at") or g.get("started_at")),
        "given_at": _iso(g.get("done_at")),
        **_accept_view(g),
        "need": sr._num(need), "got": sr._num(got), "left": sr._num(left),
        "positions": len(lines), "left_positions": sum(1 for l in lines if not l["done"]),
        "sources": [], "lines": lines,
        "status": _pair_status(task, g, need, got),
    }


async def pending_qty() -> tuple:
    """Неотданный остаток открытых заявок: (к району едет, из района уйдёт),
    оба — {(район, позиция): количество}.

    Заявка закупки и расчёт перемещений считают полку так, будто открытые
    перемещения уже сделаны: получателю это приход, отдающему — расход. Иначе
    отдающий, у которого забирают до нормы, не попросит купить взамен, а
    расчёт предложит перевезти то же самое второй раз."""
    inc, out = {}, {}
    for doc in await db.move_orders_open():
        for to, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            for l in t.get("lines") or []:
                left = float(l.get("qty") or 0) - float(l.get("got") or 0)
                if left <= 1e-9:
                    continue
                pid, src = l.get("id"), l.get("from")
                inc[(to, pid)] = inc.get((to, pid), 0.0) + left
                out[(src, pid)] = out.get((src, pid), 0.0) + left
    return inc, out


async def create(rows: list, by: str = "STAR", note: str = "") -> dict:
    """Собрать заявку из строк: [{from, to, id, qty}].

    Строки одного района-получателя ложатся в одну задачу — его водитель
    объезжает все районы, откуда ему отдают, одной ходкой."""
    cat = sr._catalog()
    tasks = {}
    skipped = []
    for r in rows:
        src, dst = str(r.get("from") or ""), str(r.get("to") or "")
        pid = str(r.get("id") or "")
        p = cat.get(pid)
        qty = sr._round_step(r.get("qty") or 0)
        if p and qty > 0:
            # Строка — целым числом кодов: у бутылки код — бутылка, у пива —
            # полкоробки. Половину бутылки отсканировать нечем, и такая строка
            # не закрылась бы никогда, держа смену обоих районов.
            step = sr.code_qty(p)
            qty = max(step, math.floor(qty / step + 0.5) * step)
        if src not in OFFICE_IDS or dst not in OFFICE_IDS or src == dst or not p or qty <= 0:
            skipped.append(r); continue
        t = tasks.setdefault(dst, {"lines": [], "driver": "", "driver_id": 0,
                                   "claimed_at": None, "started_at": None,
                                   "done_at": None, "cancelled_at": None, "give": {}})
        # Одна строка на пару «откуда × позиция»: два одинаковых переезда из
        # одного района — это одна работа, и счёт у неё общий.
        same = next((l for l in t["lines"] if l["from"] == src and l["id"] == pid), None)
        if same:
            same["qty"] = sr._num(float(same["qty"]) + qty)
        else:
            t["lines"].append({"from": src, "id": pid, "name": p.get("name", ""),
                               "unit": sr._unit(p), "qty": sr._num(qty), "got": 0})
    if not tasks:
        return {"ok": False, "error": "empty", "skipped": skipped}
    mid = await _mid()
    doc = {"_id": mid, "at": _now(), "day": sr._biz_day(), "by": str(by or "")[:60],
           "note": str(note or "")[:200], "status": "open", "tasks": tasks}
    await db.move_order_add(doc)
    log.info(f"[move] заявка {mid}: районов {len(tasks)}, строк "
             f"{sum(len(t['lines']) for t in tasks.values())}, создал {by}")
    return {"ok": True, "move_id": mid, "districts": len(tasks),
            "lines": sum(len(t["lines"]) for t in tasks.values()),
            "qty": sr._num(sum(l["qty"] for t in tasks.values() for l in t["lines"])),
            "skipped": skipped}


async def tasks_for_driver(name: str, district: str) -> dict:
    """Что видит водитель района — передачи обеих сторон:
      give — отдать: с его района, пока не отсканировано всё (кнопки: «Начать
             перемещение», потом сканер);
      take — забрать: на его район, пока не принято (серые, пока отдают;
             отдали всё — «Принял» / «Принял неровно»).
    mine / free / taken — пустые: так приложение, открытое до обновления, не
    покажет кнопок старого порядка, где сканировал получатель."""
    give, take = [], []
    for doc in await db.move_orders_open():
        mid = doc["_id"]
        for oid, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            srcs = sorted({l.get("from") for l in t.get("lines") or []} - {None})
            if oid == district:
                for src in srcs:
                    g = give_view(mid, doc, oid, t, src, name)
                    if g["status"] in ("wait", "pause", "live", "given"):
                        take.append(g)
            elif district in srcs and not _senior_of((t.get("give") or {}).get(district)):
                # Передачу взял на себя старший — отдаёт он сам, водителям
                # отдающего района сканировать нечего, и карточки у них нет.
                g = give_view(mid, doc, oid, t, district, name)
                if g["status"] in ("wait", "pause", "live"):
                    give.append(g)
    # Своё начатое — первым, потом чужое начатое, потом ждущее. У получателя
    # первым — то, что уже можно принять.
    grank = {"live": 0, "pause": 1, "wait": 2}
    give.sort(key=lambda g: (not g["mine"], grank.get(g["status"], 3), g["at"]))
    trank = {"given": 0, "live": 1, "pause": 2, "wait": 3}
    take.sort(key=lambda g: (trank.get(g["status"], 4), g["at"]))
    return {"give": give, "take": take, "mine": [], "free": [], "taken": []}


async def give_start(mid: str, oid: str, name: str, tgid: int, district: str) -> dict:
    """«Начать перемещение» — отдающий берётся передавать: его имя видят
    получатель, старший и соседи по району. Не замок: продолжить может любой
    водитель района (кто сканировал последним — тот и отдающий), а лишнего
    отдать не даст сама строка."""
    doc = await db.move_order_get(mid)
    task = ((doc or {}).get("tasks") or {}).get(oid)
    if not doc or doc.get("status") != "open" or not task or task.get("done_at") \
            or task.get("cancelled_at"):
        return {"ok": False, "error": "gone"}
    if district == oid or not any(l.get("from") == district for l in task.get("lines") or []):
        return {"ok": False, "error": "not_giver"}
    boss = _senior_of((task.get("give") or {}).get(district))
    if boss:
        return {"ok": False, "error": "senior_took", "senior": boss}
    g = give_view(mid, doc, oid, task, district, name)
    if g["status"] not in ("wait", "pause", "live"):
        return {"ok": False, "error": "given", "task": g}
    await db.move_give_start(mid, oid, district, name, tgid, _now())
    doc = await db.move_order_get(mid)
    task = (doc.get("tasks") or {}).get(oid) or {}
    log.info(f"[move] {mid}/{oid}: {name} начал отдавать из {district}")
    return {"ok": True, "task": give_view(mid, doc, oid, task, district, name)}


async def accept(mid: str, oid: str, src: str, name: str, tgid: int, district: str,
                 ok: bool = True, lines: list = None, note: str = "") -> dict:
    """Получатель принял передачу src → oid. «Принял» — всё сошлось; «Принял
    неровно» — сколько пришло на самом деле по каждой позиции и что не так.

    Принять можно только отданное целиком: пока отдающий сканирует, карточка
    у получателя серая. Расхождение склад не двигает — какой именно бутылки
    нет, неизвестно; его видят старший и оператор."""
    if district != oid:
        return {"ok": False, "error": "not_your_district"}
    doc = await db.move_order_get(mid)
    task = ((doc or {}).get("tasks") or {}).get(oid)
    if not doc or doc.get("status") != "open" or not task or task.get("cancelled_at"):
        return {"ok": False, "error": "gone"}
    g = give_view(mid, doc, oid, task, src, name)
    if not g["lines"]:
        return {"ok": False, "error": "gone"}
    if g["status"] in ("done", "diff"):
        return {"ok": True, "already": True, "task": g}
    if g["status"] != "given":
        return {"ok": False, "error": "not_given", "task": g}
    note = str(note or "").strip()[:300]
    diff = []
    if not ok:
        got_by = {}
        for x in lines or []:
            if isinstance(x, dict) and x.get("id"):
                try:
                    got_by[str(x["id"])] = max(0.0, min(9999.0, sr._round_step(x.get("got"))))
                except Exception:                            # noqa: BLE001
                    pass
        for l in g["lines"]:
            if l["id"] in got_by and abs(got_by[l["id"]] - float(l["got"])) > 1e-9:
                diff.append({"id": l["id"], "name": l["name"], "unit": l["unit"],
                             "sent": l["got"], "got": sr._num(got_by[l["id"]])})
        if not diff and not note:
            # «Неровно», но ничего не поменяли и ничего не написали — сказать нечего.
            return {"ok": False, "error": "diff_empty", "task": g}
    rec = {"accepted_at": _now(), "accepted_by": name, "accepted_by_id": tgid,
           "accept_ok": bool(ok), "accept_lines": diff, "accept_note": note}
    if not await db.move_give_accept(mid, oid, src, rec):
        doc = await db.move_order_get(mid)
        task = (doc.get("tasks") or {}).get(oid) or {}
        return {"ok": True, "already": True, "task": give_view(mid, doc, oid, task, src, name)}
    doc = await db.move_order_get(mid)
    task = (doc.get("tasks") or {}).get(oid) or {}
    srcs = {l.get("from") for l in task.get("lines") or []}
    if all(((task.get("give") or {}).get(x) or {}).get("accepted_at") for x in srcs):
        await db.move_task_done(mid, oid, _now())
        await db.move_order_close_if_done(mid, _now())
        doc = await db.move_order_get(mid)
        task = (doc.get("tasks") or {}).get(oid) or {}
    log.info(f"[move] {mid}/{oid}: {name} принял из {src}" + ("" if ok else f" неровно: {diff} «{note}»"))
    return {"ok": True, "task": give_view(mid, doc, oid, task, src, name),
            "task_done": bool(task.get("done_at"))}


async def senior_take(src: str, name: str, by: int = 0) -> dict:
    """Старший берёт на себя всё, что надо забрать с района src (владелец,
    18 сен 2026: «сделай возможность старшему взять заявку на перемещение так
    же на себя на какие-то определённые районы»; «чтобы он видел, что должен с
    JVC взять и куда отвезти, как у водителей»). Старший — тот, кто работает в
    AMBAR STAR. Берутся все передачи с этого района во всех открытых заявках,
    ещё не отданные целиком; дальше он сканирует по каждой — куда везёт.
    Водителям района src эти передачи сканировать нечего. Передача,
    которую уже взял другой старший, остаётся у него."""
    if src not in OFFICE_IDS:
        return {"ok": False, "error": "bad_district"}
    took, others = 0, set()
    for doc in await db.move_orders_open():
        for to, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            if not any(l.get("from") == src for l in t.get("lines") or []):
                continue
            g = give_view(doc["_id"], doc, to, t, src)
            if g["status"] not in ("wait", "pause", "live"):
                continue
            if g["senior"] and g["senior"] != name:
                others.add(g["senior"]); continue
            if await db.move_pair_senior(doc["_id"], to, src, name, by, _now()):
                took += 1
    if not took:
        return {"ok": False, "error": "taken" if others else "nothing", "senior": ", ".join(sorted(others))}
    log.info(f"[move] старший {name} взял на себя всё с {src}: передач {took}")
    return {"ok": True, "took": took, "others": sorted(others)}


async def senior_drop(src: str, name: str) -> dict:
    """Снять с себя всё, что взял с района src: дальше отдают водители района,
    как обычно. Что старший уже отсканировал, осталось у получателей."""
    n = 0
    for doc in await db.move_orders_open():
        for to, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            if _senior_of((t.get("give") or {}).get(src)) != name:
                continue
            # Отданное целиком остаётся за ним — это уже история: снимается
            # только то, что ещё надо отдать.
            if give_view(doc["_id"], doc, to, t, src)["status"] not in ("wait", "pause", "live"):
                continue
            if await db.move_pair_senior_drop(doc["_id"], to, src, name):
                n += 1
    log.info(f"[move] старший {name} снял с себя {src}: передач {n}")
    return {"ok": n > 0, "dropped": n}


# Старый порядок: «Взять» у получателя. Приложение, открытое до обновления,
# ещё может нажать — отвечаем, как раньше, но ни сканер, ни приёмка от этой
# отметки не зависят.
async def claim(mid: str, oid: str, name: str, tgid: int, district: str = None) -> dict:
    if district is not None and oid != district:
        return {"ok": False, "error": "not_your_district"}
    ok, task = await db.move_task_claim(mid, oid, name, tgid, _now())
    doc = await db.move_order_get(mid)
    if not doc or not task:
        return {"ok": False, "error": "gone"}
    v = task_view(mid, doc, oid, task, name)
    if not ok:
        return {"ok": False, "error": "taken", "task": v, "driver": task.get("driver") or ""}
    return {"ok": True, "task": v}


async def release(mid: str, oid: str, name: str, district: str = None) -> dict:
    if district is not None and oid != district:
        return {"ok": False, "error": "not_your_district", "task": None}
    ok = await db.move_task_release(mid, oid, name)
    doc = await db.move_order_get(mid)
    task = ((doc or {}).get("tasks") or {}).get(oid) or {}
    return {"ok": ok, "task": task_view(mid, doc, oid, task, name) if doc else None}


def _res(verdict: str, **kw) -> dict:
    return {"ok": verdict == "ok", "verdict": verdict, **kw}


async def scan(mid: str, oid: str, code: str, name: str, tgid: int, district: str = "",
               senior: bool = False) -> dict:
    """Отдающий сканирует бутылку — она уходит на район-получатель oid.

    district — район того, кто сканирует: отдают только со своего района и
    только свои строки. Проверяем и код, и что он лежит у отдающего, и что
    позиция есть в его строках, — иначе уйдёт не то, а заявка не закроется.

    senior — сканирует старший из STAR, взявший передачу на себя: district
    тогда — район, ОТКУДА он забирает (передачу этого района он и взял);
    водителям этого района её сканировать нечего."""
    code = str(code or "").strip()
    src = str(district or "")
    doc = await db.move_order_get(mid)
    if not doc or doc.get("status") != "open":
        return _res("gone")
    task = (doc.get("tasks") or {}).get(oid)
    if not task or task.get("done_at") or task.get("cancelled_at"):
        return _res("gone")
    lines = task.get("lines") or []
    boss = _senior_of((task.get("give") or {}).get(src))
    if senior:
        if not any(l.get("from") == src for l in lines):
            return _res("not_giver")
        if boss != name:
            return _res("senior_other" if boss else "not_taken", senior=boss)
        if ((task.get("give") or {}).get(src) or {}).get("accepted_at"):
            return _res("gone")
    else:
        if boss:
            # Передачу взял на себя старший — он и сканирует.
            return _res("senior_took", senior=boss)
        if src == oid:
            # Получатель не сканирует: бутылку сканирует тот, кто её отдаёт.
            return _res("giver_scans",
                        from_codes=sorted({OFFICE_CODES.get(l.get("from"), "") for l in lines}))
        if not any(l.get("from") == src for l in lines):
            return _res("not_giver")
        if ((task.get("give") or {}).get(src) or {}).get("accepted_at"):
            return _res("gone")

    qr = await db.qr_get(code)
    if not qr:
        return _res("unknown", code=code)
    pname = qr.get("product_name") or ""
    st = (qr.get("status") or "active").strip()
    if st != "active":
        return _res(st, code=code, name=pname, say=sr.MOVE_SAY.get(st, ""))
    pid = str(qr.get("product_id") or "")
    at = (qr.get("district") or "").strip()
    mine = [i for i, l in enumerate(lines) if l.get("from") == src]
    if at == oid:
        # Бутылка уже числится у получателя. Если последним переездом она
        # ушла от этого же отдающего — это второй скан той же бутылки; иначе
        # это бутылка самого получателя, и отдавать её некому.
        last = (qr.get("moves") or [])[-1:]
        if last and (last[0] or {}).get("from") == src:
            return _res("given", code=code, name=pname, to_code=OFFICE_CODES.get(oid, ""))
        return _res("other_district", code=code, name=pname, from_code=OFFICE_CODES.get(at, ""))
    if at != src:
        return _res("other_district", code=code, name=pname, from_code=OFFICE_CODES.get(at, ""))
    idx = next((i for i in mine if lines[i].get("id") == pid), None)
    if idx is None:
        return _res("not_in_task", code=code, name=pname)
    need = float(lines[idx].get("qty") or 0)
    add = float(qr.get("qty") or 1)
    if float(lines[idx].get("got") or 0) >= need - 1e-9:
        return _res("full", code=code, name=pname)

    # Место в строке — до переезда и атомарно: два отдающих в одну секунду не
    # передадут по строке больше, чем в ней заказано.
    if not await db.move_line_reserve(mid, oid, idx, add, need):
        doc2 = await db.move_order_get(mid)
        t2 = ((doc2 or {}).get("tasks") or {}).get(oid) or {}
        if not doc2 or doc2.get("status") != "open" or t2.get("done_at") or t2.get("cancelled_at"):
            return _res("gone")
        got2 = float(((t2.get("lines") or [])[idx]).get("got") or 0)
        if got2 >= need - 1e-9:
            return _res("full", code=code, name=pname)
        # Остаток строки меньше кода (дробь в старой заявке) — последний код
        # закрывает строку; условие — ровно прежнее значение, без гонки.
        if not await db.move_line_reserve_exact(mid, oid, idx, got2, add):
            return _res("busy", code=code, name=pname)

    r = await sr.move_by_code(code, oid, tgid, name, "move", expect_from=src)
    if not r.get("ok"):
        await db.move_line_unreserve(mid, oid, idx, add)
        v = r.get("verdict") or "no"
        if v == "same":
            v = "given"
        return _res(v, **{k: x for k, x in r.items() if k not in ("ok", "verdict")})

    now = _now()
    await db.move_task_started(mid, oid, now)
    await db.move_give_mark(mid, oid, src, name, tgid, now)
    doc = await db.move_order_get(mid)
    task = (doc.get("tasks") or {}).get(oid) or {}
    g = give_view(mid, doc, oid, task, src, name)
    if g["left"] <= 0:
        # Отдано всё: у получателя карточка становится активной — «Принял».
        await db.move_give_done(mid, oid, src, now)
        doc = await db.move_order_get(mid)
        task = (doc.get("tasks") or {}).get(oid) or {}
        g = give_view(mid, doc, oid, task, src, name)
    line = next(l for l in g["lines"] if l["id"] == pid)
    log.info(f"[move] {mid}/{oid}: {name} отдал {code} ({pid}) из {src} · {g['got']}/{g['need']}")
    return _res("ok", code=code, name=r.get("name") or line["name"], qty=r.get("qty"),
                unit=r.get("unit"), from_code=r.get("from_code"), to_code=r.get("to_code"),
                line=line, task=g,
                # finished — эта передача отсканирована целиком: отдавать больше
                # нечего, дальше «Принял» у получателя.
                finished=g["left"] <= 0)


async def live(day: str = "") -> dict:
    """Статус по каждому району-получателю — для STAR: кто и сколько отдал,
    что принято, где приняли неровно."""
    day = str(day or "").strip() or sr._biz_day()
    out = []
    # Открытые заявки берём все, а не только сегодняшние: учётные сутки
    # сменяются в десять утра, а незакрытое перемещение живёт, пока его не
    # довезут. Иначе вчерашняя заявка исчезает у старшего, оставаясь у
    # водителей, — и он думает, что её сняли.
    docs = {d["_id"]: d for d in await db.move_orders_since(day)}
    for d0 in await db.move_orders_open():
        docs.setdefault(d0["_id"], d0)
    for doc in sorted(docs.values(), key=lambda x: str(x.get("at") or "")):
        mid = doc["_id"]
        for oid, t in (doc.get("tasks") or {}).items():
            v = task_view(mid, doc, oid, t)
            # Снятую заявку целиком её задачи не переживают: у водителей они уже
            # исчезли (там выборка по status=open), и у старшего не должны
            # висеть свободными — иначе он снимает то, чего нет.
            if doc.get("status") == "cancelled" and v["status"] not in ("done",):
                v["status"] = "cancelled"
            v["by"] = doc.get("by", "")
            out.append(v)
    out.sort(key=lambda v: (v["status"] == "done", v["district_code"]))
    return {"day": day, "tasks": out,
            "open": sum(1 for v in out if v["status"] not in ("done", "cancelled"))}


async def pending_for_district(oid: str) -> list:
    """Незакрытые перемещения района — по ним не даём закрыть смену. Обе
    стороны: отдающий (side=give), пока не отсканировал всё, и получатель
    (side=take), пока не принял. Отдающий без приехавшего не отдаст, получатель
    без отдающего не примет — держит обоих."""
    out = []
    for doc in await db.move_orders_open():
        for to, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            srcs = sorted({l.get("from") for l in t.get("lines") or []} - {None})
            if to == oid:
                for src in srcs:
                    g = give_view(doc["_id"], doc, to, t, src)
                    if g["status"] in ("wait", "pause", "live", "given"):
                        out.append({**g, "side": "take"})
            elif oid in srcs and not _senior_of((t.get("give") or {}).get(oid)):
                # Взял старший — отдаёт он, смену водителей отдающего не держим.
                g = give_view(doc["_id"], doc, to, t, oid)
                if g["status"] in ("wait", "pause", "live"):
                    out.append({**g, "side": "give"})
    return out


async def plan(day: str = "") -> dict:
    """Что стоит перевезти прямо сейчас: излишек одного района закрывает дыру
    другого. Это тот же расчёт, по которому считается заявка магазину, только
    до неё: сначала смотрим, нет ли нужного у соседа, и лишь потом покупаем.

    Берём только излишек НАД собственной нормой отдающего — район отдаёт то,
    что ему самому не нужно, и его полка не проседает.

    Бутылка может лежать на полке, но не числиться в реестре кодов («QR код не
    внесён») — такую сканером не взять. Строку из-за этого НЕ режем: порядок дня
    у нас обратный — сначала район досканирует свой долг по кодам, и только
    потом едут за перемещением (владелец, 18 сен 2026). Но рядом со строкой
    едет no_codes: сколько из неё пока без кодов, чтобы было видно, где сперва
    надо внести товар.

    Открытые заявки на перемещение расчёт считает сделанными: у получателя
    уже есть то, что к нему едет, у отдающего уже нет того, что он отдаёт, и
    коды, обещанные заявке, второй раз не обещаются. Иначе, пока бутылки не
    переехали, расчёт предлагал бы перевезти то же самое второй раз."""
    import stock_value as sv
    day = str(day or "").strip() or sr._biz_day()
    cat = sr._catalog()
    cost = await sv.cost_map()
    base = await sr._district_base(day)
    norms = await db.get_stock_norms()
    inc, out = await pending_qty()
    # {район: {позиция: сколько числится кодами} } — это и есть потолок скана.
    try:
        reg = await db.qr_by_product_district_all()
    except Exception as e:                            # noqa: BLE001
        log.warning(f"[move] реестр кодов не прочитан: {e}")
        reg = {}
    for (oid, pid), n in out.items():
        per = reg.setdefault(oid, {})
        per[pid] = max(0.0, float(per.get(pid) or 0) - n)
    stock, norm = {}, {}
    for oid in OFFICE_IDS:
        have = (base.get(oid) or {}).get("have_exact") or {}
        for pid in cat:
            key = f"{oid}:{pid}"
            if key not in norms:
                continue
            stock[(oid, pid)] = (float(have.get(pid) or 0) + inc.get((oid, pid), 0.0)
                                 - out.get((oid, pid), 0.0))
            norm[(oid, pid)] = float(norms[key])
    rows = []
    for pid in {p for (_, p) in norm}:
        step = 0.5 if sr._unit(cat.get(pid) or {}) > 1 else 1
        need = [[o, norm[(o, pid)] - stock[(o, pid)]] for o in OFFICE_IDS
                if (o, pid) in norm and norm[(o, pid)] - stock[(o, pid)] > 0]
        surp = [[o, stock[(o, pid)] - norm[(o, pid)]] for o in OFFICE_IDS
                if (o, pid) in norm and stock[(o, pid)] - norm[(o, pid)] >= 1]
        for n in need:
            for s in surp:
                if n[1] <= 0 or s[1] <= 0:
                    continue
                можно = float((reg.get(s[0]) or {}).get(pid) or 0)
                x = math.floor(min(n[1], s[1]) / step) * step
                if x <= 0:
                    continue
                n[1] -= x; s[1] -= x
                # Сколько из этой строки сканером пока не взять: столько район
                # должен сперва внести по пересчёту. Счёт кодов уменьшаем, чтобы
                # один и тот же код не зачёлся двум строкам.
                без = max(0.0, x - можно)
                reg.setdefault(s[0], {})[pid] = max(0.0, можно - x)
                rows.append({"no_codes": sr._num(без),
                             "from": s[0], "from_code": OFFICE_CODES.get(s[0], ""),
                             "to": n[0], "to_code": OFFICE_CODES.get(n[0], ""),
                             "id": pid, "name": (cat.get(pid) or {}).get("name", ""),
                             "unit": sr._unit(cat.get(pid) or {}), "qty": sr._num(x),
                             "aed": round(x * float(cost.get(pid) or 0)),
                             # Чтобы владелец видел, почему строка тут: сколько
                             # у отдающего остаётся и что у получателя пусто.
                             "from_left": sr._num(stock[(s[0], pid)] - x),
                             "to_have": sr._num(stock[(n[0], pid)]),
                             "to_norm": sr._num(norm[(n[0], pid)])})
    rows.sort(key=lambda r: (-r["aed"], r["name"]))
    return {"day": day, "rows": rows,
            # Сколько во всей заявке пока без кодов: это работа «внести товар»,
            # которую район делает до того, как за перемещением приедут.
            "no_codes": sr._num(sum(r["no_codes"] for r in rows)),
            "qty": sr._num(sum(r["qty"] for r in rows)),
            "aed": sum(r["aed"] for r in rows),
            "districts": sorted({r["to_code"] for r in rows})}


# ── Заявка от оператора (владелец, 18 сен 2026) ──────────────────────────────
# «Сделай так, чтобы операторы могли создавать заявку на перемещение — ровно
# такую, как сегодня водители видят», и следом: «это должна быть не умная
# заявка — операторы должны уметь собирать её вручную». Заявка та же самая
# (create), водители видят её так же; оператор сам выбирает, откуда и куда, что
# и сколько. Здесь ничего не подсказывается и не считается — только остатки и
# коды по районам, чтобы было видно, что где лежит.
async def board(scope: set) -> dict:
    """Позиции, которые где-то лежат, и по каждой — сколько её в каждом районе,
    сколько там кодов (столько сканером и отдадут) и сколько из этого уже
    уходит по открытым заявкам."""
    day = sr._biz_day()
    cat = sr._catalog()
    base = await sr._district_base(day)
    _, out = await pending_qty()
    try:
        reg = await db.qr_by_product_district_all()
    except Exception as e:                            # noqa: BLE001
        log.warning(f"[move] реестр кодов не прочитан: {e}")
        reg = {}
    products = []
    for pid, p in cat.items():
        have = {oid: sr._num(((base.get(oid) or {}).get("have_exact") or {}).get(pid) or 0) for oid in OFFICE_IDS}
        if not any(have.values()):
            continue                                  # нигде не лежит — везти нечего
        codes = {oid: sr._num((reg.get(oid) or {}).get(pid) or 0) for oid in OFFICE_IDS}
        # Сколько из лежащего уже отдают по открытым заявкам: те же бутылки
        # второй раз не обещают — оператор видит это рядом с остатком.
        gone = {oid: sr._num(out[(oid, pid)]) for oid in OFFICE_IDS if out.get((oid, pid), 0) > 1e-9}
        products.append({"id": pid, "name": p.get("name", ""), "cat": p.get("cat", ""),
                         "img": p.get("img", ""), "unit": sr._unit(p),
                         "have": have, "codes": codes, "out": gone})
    return {"day": day,
            "districts": [{"id": o, "code": OFFICE_CODES.get(o, ""), "name": OFFICE_NAMES.get(o, o),
                           "mine": o in scope} for o in OFFICE_IDS],
            "products": products}


async def create_by_operator(who: str, to: str, lines: list, note: str, scope: set) -> dict:
    """Заявка оператора: всё в один район, откуда — по строкам. Хотя бы одна
    сторона своя: везут к нему — или всё, что везут, забирают у него. Чужие
    районы между собой оператор не двигает."""
    rows = [{"from": str(l.get("from") or ""), "to": to, "id": str(l.get("id") or ""),
             "qty": l.get("qty")} for l in (lines or []) if isinstance(l, dict)]
    if to not in scope and not (rows and all(r["from"] in scope for r in rows)):
        return {"ok": False, "error": "not_yours"}
    r = await create(rows, by=f"{who} · оператор", note=note)
    return r


async def live_for(scope: set) -> dict:
    """Перемещения, которые касаются районов оператора: везут к ним или от них."""
    lv = await live()
    out = []
    for v in lv["tasks"]:
        into = v["district"] in scope
        away = any(l.get("from") in scope for l in v["lines"])
        if not (into or away):
            continue
        out.append({**v, "side": "in" if into else "out"})
    return {"day": lv["day"], "tasks": out,
            "open": sum(1 for v in out if v["status"] not in ("done", "cancelled"))}


async def cancel_by_operator(mid: str, district: str, scope: set) -> dict:
    """Снять неначатую задачу своего района. Начатую — нельзя: часть бутылок
    уже передана и лежит у получателя, снимать поздно; это решает владелец в
    STAR."""
    if district not in scope:
        return {"ok": False, "error": "not_yours"}
    doc = await db.move_order_get(mid)
    # Заявку владельца (STAR) оператор не снимает: её собирали и проверяли там,
    # и решение снять — тоже там. Своё и других операторов — можно.
    if not str((doc or {}).get("by") or "").endswith("· оператор"):
        return {"ok": False, "error": "owner_order"}
    t = ((doc or {}).get("tasks") or {}).get(district)
    if not t or t.get("done_at") or t.get("cancelled_at"):
        return {"ok": False, "error": "gone"}
    if any(float(l.get("got") or 0) > 0 for l in (t.get("lines") or [])):
        givers = sorted({(g or {}).get("driver") or "" for g in (t.get("give") or {}).values()} - {""})
        return {"ok": False, "error": "started", "driver": ", ".join(givers) or t.get("driver") or ""}
    ok = await db.move_order_cancel(mid, district, _now())
    if ok:
        log.info(f"[move] {mid}/{district}: оператор снял задачу")
    return {"ok": bool(ok)}


# ── Ручки ────────────────────────────────────────────────────────────────────
async def handle_drv_list(request):
    me = request["driver"]
    return web.json_response(await tasks_for_driver(me["name"], me.get("district") or ""),
                             headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def _body(request) -> dict:
    try:
        return await request.json()
    except Exception:                                  # noqa: BLE001
        return {}


async def handle_drv_claim(request):
    me, b = request["driver"], await _body(request)
    r = await claim(request.match_info.get("mid") or "", str(b.get("district") or ""),
                    me["name"], request["tg"].get("id") or 0, me.get("district") or "")
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_release(request):
    me, b = request["driver"], await _body(request)
    r = await release(request.match_info.get("mid") or "", str(b.get("district") or ""), me["name"],
                      me.get("district") or "")
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_scan(request):
    me, b = request["driver"], await _body(request)
    # district в запросе — район-получатель (ключ задачи); отдаёт водитель со
    # своего района, и его район берём из подписи, а не из запроса.
    r = await scan(request.match_info.get("mid") or "", str(b.get("district") or ""),
                   str(b.get("code") or ""), me["name"], request["tg"].get("id") or 0,
                   me.get("district") or "")
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_start(request):
    """POST {district} — «Начать перемещение»: district — район-получатель."""
    me, b = request["driver"], await _body(request)
    r = await give_start(request.match_info.get("mid") or "", str(b.get("district") or ""),
                         me["name"], request["tg"].get("id") or 0, me.get("district") or "")
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_accept(request):
    """POST {district, from, ok, lines:[{id, got}], note} — «Принял» / «Принял неровно»."""
    me, b = request["driver"], await _body(request)
    r = await accept(request.match_info.get("mid") or "", str(b.get("district") or ""),
                     str(b.get("from") or ""), me["name"], request["tg"].get("id") or 0,
                     me.get("district") or "", ok=b.get("ok") is not False,
                     lines=b.get("lines") if isinstance(b.get("lines"), list) else [],
                     note=str(b.get("note") or ""))
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


def _own_name(request, b) -> str:
    """Кем подписан старший — полем «as», как во всех решениях из STAR."""
    return str((b or {}).get("as") or "").strip()[:60] or "старший"


async def handle_own_take(request):
    """POST {from, as} — старший берёт на себя всё, что надо забрать с района."""
    b = await _body(request)
    r = await senior_take(str(b.get("from") or ""), _own_name(request, b), int(request.get("owner_id") or 0))
    return web.json_response(r, status=200 if r.get("ok") else 409, headers=CORS_HEADERS,
                             dumps=lambda o: json.dumps(o, default=str))


async def handle_own_drop(request):
    """POST {from, as} — снять с себя всё, что взял с района."""
    b = await _body(request)
    r = await senior_drop(str(b.get("from") or ""), _own_name(request, b))
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_own_scan(request):
    """POST {district, from, code, as} — старший сканирует бутылку в передачу
    «from → district», которую взял на себя."""
    b = await _body(request)
    r = await scan(request.match_info.get("mid") or "", str(b.get("district") or ""),
                   str(b.get("code") or ""), _own_name(request, b),
                   int(request.get("owner_id") or 0), str(b.get("from") or ""), senior=True)
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_own_plan(request):
    return web.json_response(await plan(request.query.get("day") or ""),
                             headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_own_create(request):
    b = await _body(request)
    rows = b.get("rows")
    if not isinstance(rows, list) or not rows:
        p = await plan()
        rows = p["rows"]
    r = await create(rows, by=str(b.get("by") or "STAR"), note=str(b.get("note") or ""))
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_own_live(request):
    return web.json_response(await live(request.query.get("day") or ""),
                             headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_own_cancel(request):
    b = await _body(request)
    ok = await db.move_order_cancel(request.match_info.get("mid") or "",
                                    str(b.get("district") or ""), _now())
    return web.json_response({"ok": ok}, headers=CORS_HEADERS)
