"""Заявка на перемещение между районами (владелец, 18 сен 2026).

Она обратная приёмке. В приёмке водитель района принимает то, что ему привезли;
здесь — наоборот: если товар едет «из Бизнес Бея в JVC», это работа водителя
JVC. Он приезжает в Бизнес Бей, сканирует бутылки, которые должен увезти, и они
тем же сканом переезжают на его район. Поэтому задача висит на районе-ПОЛУЧАТЕЛЕ,
а не на том, откуда везут.

Взять задачу может любой водитель района-получателя, но достаётся она одному:
захват атомарный (db.move_task_claim), и второй водитель получает отказ, даже
если нажал в ту же секунду.

Недобора не бывает. Владелец: «водитель приезжает прямо на билдинг и забирает
эти бутылки, ничего на следующий раз не должно оставаться, прям в тот же день».
Поэтому задача закрывается САМА, когда увезено всё до последней строки, кнопки
«завершить с недобором» нет, а закрыть смену район не может, пока его
перемещения не отработаны (см. pending_for_district).

Склад двигается в момент скана, а не в конце: бутылку физически забрали, и
остаток обоих районов обязан это показывать сразу. Поэтому ядро переезда —
общий stock_routes.move_by_code, тот же, что у старшего и у ручного переезда.
"""
import json
import logging
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


def _mid() -> str:
    """MV260918-020134 — по дате, как у заявки магазину."""
    return "MV" + _now().astimezone(sr.DUBAI_TZ).strftime("%y%m%d-%H%M%S")


def _line_view(l: dict) -> dict:
    need = float(l.get("qty") or 0)
    got = float(l.get("got") or 0)
    return {"from": l.get("from"), "from_code": OFFICE_CODES.get(l.get("from"), ""),
            "from_name": OFFICE_NAMES.get(l.get("from"), ""),
            "id": l.get("id"), "name": l.get("name", ""), "unit": int(l.get("unit") or 1),
            "qty": sr._num(need), "got": sr._num(got), "left": sr._num(max(0.0, need - got)),
            "done": got >= need - 1e-9}


def task_view(mid: str, doc: dict, oid: str, task: dict, me: str = "") -> dict:
    """Карточка задачи: куда везём, откуда и что осталось."""
    lines = [_line_view(l) for l in (task.get("lines") or [])]
    need = sum(l["qty"] for l in lines)
    got = sum(l["got"] for l in lines)
    by_src = {}
    for l in lines:
        s = by_src.setdefault(l["from"], {"district": l["from"], "code": l["from_code"],
                                          "name": l["from_name"], "qty": 0.0, "got": 0.0,
                                          "lines": []})
        s["qty"] += l["qty"]; s["got"] += l["got"]; s["lines"].append(l)
    for s in by_src.values():
        s["qty"] = sr._num(s["qty"]); s["got"] = sr._num(s["got"])
        s["done"] = s["got"] >= s["qty"] - 1e-9
    driver = task.get("driver") or ""
    return {
        "move_id": mid, "day": doc.get("day", ""), "at": _iso(doc.get("at")),
        "district": oid, "district_code": OFFICE_CODES.get(oid, ""),
        "district_name": OFFICE_NAMES.get(oid, oid),
        "driver": driver, "mine": bool(me) and driver == me,
        "claimed_at": _iso(task.get("claimed_at")), "started_at": _iso(task.get("started_at")),
        "done_at": _iso(task.get("done_at")), "cancelled_at": _iso(task.get("cancelled_at")),
        "need": sr._num(need), "got": sr._num(got), "left": sr._num(max(0.0, need - got)),
        "positions": len(lines), "left_positions": sum(1 for l in lines if not l["done"]),
        "sources": sorted(by_src.values(), key=lambda s: s["code"]),
        "lines": lines,
        "status": ("cancelled" if task.get("cancelled_at") else
                   "done" if task.get("done_at") else
                   "live" if got > 0 else
                   "claimed" if driver else "free"),
    }


async def create(rows: list, by: str = "STAR", note: str = "") -> dict:
    """Собрать заявку из строк расчёта: [{from, to, id, qty}].

    Строки одного района-получателя ложатся в одну задачу — водитель объезжает
    все районы, откуда ему нужно забрать, одной ходкой."""
    cat = sr._catalog()
    tasks = {}
    skipped = []
    for r in rows:
        src, dst = str(r.get("from") or ""), str(r.get("to") or "")
        pid = str(r.get("id") or "")
        qty = sr._round_step(r.get("qty") or 0)
        p = cat.get(pid)
        if src not in OFFICE_IDS or dst not in OFFICE_IDS or src == dst or not p or qty <= 0:
            skipped.append(r); continue
        t = tasks.setdefault(dst, {"lines": [], "driver": "", "driver_id": 0,
                                   "claimed_at": None, "started_at": None,
                                   "done_at": None, "cancelled_at": None})
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
    mid = _mid()
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
    """Что видит водитель: своя задача, свободные своего района и то, что
    заберут у него самого (владелец: отдающий должен видеть список, чтобы не
    удивляться)."""
    mine, free, taken, give = [], [], [], []
    for doc in await db.move_orders_open():
        mid = doc["_id"]
        for oid, t in (doc.get("tasks") or {}).items():
            if t.get("done_at") or t.get("cancelled_at"):
                continue
            v = task_view(mid, doc, oid, t, name)
            if oid == district:
                if v["mine"]:
                    mine.append(v)
                elif not v["driver"]:
                    free.append(v)
                else:
                    taken.append(v)
            elif any(l["from"] == district for l in v["lines"]):
                # Отдающему — только его строки и куда они уедут.
                give.append({**v, "lines": [l for l in v["lines"] if l["from"] == district],
                             "sources": [], "to_code": v["district_code"],
                             "to_name": v["district_name"]})
    for g in give:
        g["need"] = sr._num(sum(l["qty"] for l in g["lines"]))
        g["got"] = sr._num(sum(l["got"] for l in g["lines"]))
        g["positions"] = len(g["lines"])
    return {"mine": mine, "free": free, "taken": taken, "give": give}


async def claim(mid: str, oid: str, name: str, tgid: int) -> dict:
    ok, task = await db.move_task_claim(mid, oid, name, tgid, _now())
    doc = await db.move_order_get(mid)
    if not doc or not task:
        return {"ok": False, "error": "gone"}
    v = task_view(mid, doc, oid, task, name)
    if not ok:
        return {"ok": False, "error": "taken", "task": v, "driver": task.get("driver") or ""}
    log.info(f"[move] {mid}/{oid}: взял {name}")
    return {"ok": True, "task": v}


async def release(mid: str, oid: str, name: str) -> dict:
    ok = await db.move_task_release(mid, oid, name)
    doc = await db.move_order_get(mid)
    task = ((doc or {}).get("tasks") or {}).get(oid) or {}
    log.info(f"[move] {mid}/{oid}: отпустил {name}" if ok else
             f"[move] {mid}/{oid}: отпустить не вышло ({name})")
    return {"ok": ok, "task": task_view(mid, doc or {}, oid, task, name) if doc else None}


def _res(verdict: str, **kw) -> dict:
    return {"ok": verdict == "ok", "verdict": verdict, **kw}


async def scan(mid: str, oid: str, code: str, name: str, tgid: int) -> dict:
    """Скан кода на чужом районе: бутылка переезжает к сканирующему.

    Проверяем не только код, но и что он из нужного района и нужной позиции —
    иначе водитель увезёт не то, а заявка останется незакрытой."""
    code = str(code or "").strip()
    doc = await db.move_order_get(mid)
    if not doc or doc.get("status") != "open":
        return _res("gone")
    task = (doc.get("tasks") or {}).get(oid)
    if not task or task.get("done_at") or task.get("cancelled_at"):
        return _res("gone")
    if (task.get("driver") or "") != name:
        return _res("not_mine", driver=task.get("driver") or "")

    qr = await db.qr_get(code)
    if not qr:
        return _res("unknown", code=code)
    pid = str(qr.get("product_id") or "")
    src = (qr.get("district") or "").strip()
    lines = task.get("lines") or []
    idx = next((i for i, l in enumerate(lines)
                if l.get("id") == pid and l.get("from") == src
                and float(l.get("got") or 0) < float(l.get("qty") or 0) - 1e-9), None)
    if idx is None:
        # Сказать словами, что не так: позиции нет в заявке, она уже добрана,
        # или бутылка лежит не в том районе, откуда её ждут.
        mine_pid = [l for l in lines if l.get("id") == pid]
        if not mine_pid:
            return _res("not_in_task", code=code, name=qr.get("product_name") or "",
                        from_code=OFFICE_CODES.get(src, ""))
        if all(float(l.get("got") or 0) >= float(l.get("qty") or 0) - 1e-9 for l in mine_pid):
            return _res("full", code=code, name=qr.get("product_name") or "")
        return _res("other_district", code=code, name=qr.get("product_name") or "",
                    from_code=OFFICE_CODES.get(src, ""),
                    want=", ".join(sorted({OFFICE_CODES.get(l["from"], "") for l in mine_pid})))

    await db.move_task_started(mid, oid, _now())
    r = await sr.move_by_code(code, oid, tgid, name, "driver")
    if not r.get("ok"):
        return _res(r.get("verdict") or "no", **{k: v for k, v in r.items()
                                                 if k not in ("ok", "verdict")})
    was = float(lines[idx].get("got") or 0)
    if not await db.move_line_got(mid, oid, idx, was, float(r.get("qty") or 1)):
        # Строку успели поправить между чтением и записью: переезд уже сделан,
        # отменять его нельзя — перечитываем и отдаём фактическое состояние.
        log.warning(f"[move] {mid}/{oid}: строка {idx} изменилась под руками")
    doc = await db.move_order_get(mid)
    task = (doc.get("tasks") or {}).get(oid) or {}
    v = task_view(mid, doc, oid, task, name)
    if v["left"] <= 0 and not task.get("done_at"):
        await db.move_task_done(mid, oid, _now())
        await db.move_order_close_if_done(mid, _now())
        doc = await db.move_order_get(mid)
        v = task_view(mid, doc, oid, (doc.get("tasks") or {}).get(oid) or {}, name)
    line = v["lines"][idx]
    return _res("ok", code=code, name=r.get("name") or line["name"], qty=r.get("qty"),
                unit=r.get("unit"), from_code=r.get("from_code"), to_code=r.get("to_code"),
                line=line, task=v, finished=bool(v["done_at"]))


async def live(day: str = "") -> dict:
    """Статус по каждому району — для STAR: кто взял, сколько увёз, когда."""
    day = str(day or "").strip() or sr._biz_day()
    out = []
    for doc in await db.move_orders_since(day):
        mid = doc["_id"]
        for oid, t in (doc.get("tasks") or {}).items():
            v = task_view(mid, doc, oid, t)
            v["by"] = doc.get("by", "")
            out.append(v)
    out.sort(key=lambda v: (v["status"] == "done", v["district_code"]))
    return {"day": day, "tasks": out,
            "open": sum(1 for v in out if v["status"] not in ("done", "cancelled"))}


async def pending_for_district(oid: str) -> list:
    """Незакрытые перемещения района — по ним не даём закрыть смену."""
    out = []
    for doc in await db.move_orders_open():
        t = (doc.get("tasks") or {}).get(oid)
        if t and not t.get("done_at") and not t.get("cancelled_at"):
            out.append(task_view(doc["_id"], doc, oid, t))
    return out


async def plan(day: str = "") -> dict:
    """Что стоит перевезти прямо сейчас: излишек одного района закрывает дыру
    другого. Это тот же расчёт, по которому считается заявка магазину, только
    до неё: сначала смотрим, нет ли нужного у соседа, и лишь потом покупаем.

    Берём только излишек НАД собственной нормой отдающего — район отдаёт то,
    что ему самому не нужно, и его полка не проседает."""
    import math
    import stock_value as sv
    day = str(day or "").strip() or sr._biz_day()
    cat = sr._catalog()
    cost = await sv.cost_map()
    base = await sr._district_base(day)
    norms = await db.get_stock_norms()
    stock, norm = {}, {}
    for oid in OFFICE_IDS:
        have = (base.get(oid) or {}).get("have_exact") or {}
        for pid in cat:
            key = f"{oid}:{pid}"
            if key not in norms:
                continue
            stock[(oid, pid)] = float(have.get(pid) or 0)
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
                x = math.floor(min(n[1], s[1]) / step) * step
                if x <= 0:
                    continue
                n[1] -= x; s[1] -= x
                rows.append({"from": s[0], "from_code": OFFICE_CODES.get(s[0], ""),
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
            "qty": sr._num(sum(r["qty"] for r in rows)),
            "aed": sum(r["aed"] for r in rows),
            "districts": sorted({r["to_code"] for r in rows})}


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
                    me["name"], request["tg"].get("id") or 0)
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_release(request):
    me, b = request["driver"], await _body(request)
    r = await release(request.match_info.get("mid") or "", str(b.get("district") or ""), me["name"])
    return web.json_response(r, headers=CORS_HEADERS, dumps=lambda o: json.dumps(o, default=str))


async def handle_drv_scan(request):
    me, b = request["driver"], await _body(request)
    r = await scan(request.match_info.get("mid") or "", str(b.get("district") or ""),
                   str(b.get("code") or ""), me["name"], request["tg"].get("id") or 0)
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
