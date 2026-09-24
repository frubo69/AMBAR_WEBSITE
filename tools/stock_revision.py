"""Сквозная сверка склада по первичным данным. Только читает, ничего не правит.

Владелец, 24 сен 2026: «проведи глубочайшие проверки и точно убедись, что все
когда-либо сделанные процессы в плане перемещений или приёмок прошли нормально
и не отобразились на складе, вызывая противоречия».

Что сверяется:
  1. коды — район совпадает с последним переездом, цепочка переездов цела,
     каждому переезду есть строка в книге;
  2. книга переездов — районы, количества, дубли, совпадение позиции с кодом;
  3. заявки на перемещение — отдано не больше заявленного, принято не больше
     отданного, коды передачи без повторов, сумма строк = книге переездов;
  4. приёмки — принято не больше заказанного, недоборы записаны при закрытии,
     кодов заведено ровно столько, сколько отсканировано;
  5. остаток — тот же расчёт, что у склада, но БЕЗ обрезки нулём: где обрезка
     сработала, там район продал или списал больше, чем у него числилось;
  6. продажи — районы и позиции из каталога (пачки пива раскладываются через
     stock_routes.sale_item), списания против реестра кодов.

    python3 tools/stock_revision.py            # на сервере, где живая база
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import defaultdict                          # noqa: E402
import db, stock_routes as sr                                 # noqa: E402
from config_offices import OFFICE_IDS, OFFICE_CODES           # noqa: E402

БЕД = defaultdict(list)
def беда(раздел, текст): БЕД[раздел].append(текст)
def кр(o): return OFFICE_CODES.get(o, o or "—")


async def коды_и_переезды(d, cat):
    коды = {x["_id"]: x async for x in d.qr_codes.find({})}
    пер = [x async for x in d.stock_transfers.find({}).sort("at", 1)]
    по_коду = defaultdict(list)
    for t in пер:
        if t.get("code"):
            по_коду[str(t["code"])].append(t)
    for c, q in коды.items():
        ходы = q.get("moves") or []
        if q.get("district") and q["district"] not in OFFICE_IDS:
            беда("коды", f"{c}: район «{q.get('district')}» не из списка")
        if ходы and (ходы[-1].get("to") or "") != q.get("district"):
            беда("коды", f"{c}: числится на {кр(q.get('district'))}, "
                         f"последний переезд вёл в {кр(ходы[-1].get('to'))}")
        for i in range(len(ходы) - 1):
            if (ходы[i].get("to") or "") != (ходы[i + 1].get("from") or ""):
                беда("коды", f"{c}: цепочка переездов рвётся на шаге {i + 1}")
        if len(ходы) != len(по_коду.get(c, [])):
            беда("коды", f"{c}: переездов у кода {len(ходы)}, в книге {len(по_коду.get(c, []))}")
        if q.get("product_id") not in cat:
            беда("коды", f"{c}: позиции {q.get('product_id')} нет в каталоге")
    for c in (set(по_коду) - set(коды)):
        беда("коды", f"{c}: переезды есть, а кода в реестре нет")

    видели = set()
    for t in пер:
        i = str(t.get("_id"))
        if t.get("from") == t.get("to"):
            беда("переезды", f"{i}: из района в него же")
        if t.get("from") not in OFFICE_IDS or t.get("to") not in OFFICE_IDS:
            беда("переезды", f"{i}: район вне списка {t.get('from')} → {t.get('to')}")
        if float(t.get("qty") or 0) <= 0:
            беда("переезды", f"{i}: количество {t.get('qty')}")
        c = str(t.get("code") or "")
        if not c:
            continue
        ключ = (c, str(t.get("at"))[:19])
        if ключ in видели:
            беда("переезды", f"{i}: код {c} дважды в одну секунду — похоже на дубль")
        видели.add(ключ)
        q = коды.get(c)
        if q and q.get("product_id") != t.get("product_id"):
            беда("переезды", f"{i}: код {c} — {q.get('product_id')}, а в строке {t.get('product_id')}")
    return коды, пер


async def перемещения(d, пер):
    строк = 0.0
    async for mo in d.move_orders.find({}):
        mid = mo["_id"]
        for куда, task in (mo.get("tasks") or {}).items():
            lines = task.get("lines") or []
            строк += sum(float(l.get("got") or 0) for l in lines)
            for откуда in sorted({l.get("from") for l in lines} - {None}):
                мои = [l for l in lines if l.get("from") == откуда]
                g = (task.get("give") or {}).get(откуда) or {}
                qty = sum(float(l.get("qty") or 0) for l in мои)
                got = sum(float(l.get("got") or 0) for l in мои)
                recv = sum(float(l.get("recv") or 0) for l in мои)
                имя = f"{mid} {кр(откуда)}→{кр(куда)}"
                if got > qty + 1e-9:
                    беда("перемещения", f"{имя}: отдано {got:g} больше заявленного {qty:g}")
                if recv > got + 1e-9:
                    беда("перемещения", f"{имя}: принято {recv:g} больше отданного {got:g}")
                кд = [str(x) for x in (g.get("codes") or [])]
                if len(set(кд)) != len(кд):
                    беда("перемещения", f"{имя}: код в передаче повторяется")
                if кд and not {str(x) for x in (g.get("recv_codes") or [])} <= set(кд):
                    беда("перемещения", f"{имя}: получатель отсканировал код не из передачи")
                if кд and abs(float(g.get("codes_q") or 0) - got) > 1e-9:
                    беда("перемещения", f"{имя}: сумма кодов {g.get('codes_q')} ≠ отданному {got:g}")
            если_закрыта = task.get("done_at")
            if если_закрыта and not all(((task.get("give") or {}).get(s) or {}).get("accepted_at")
                                        for s in {l.get("from") for l in lines} - {None}):
                беда("перемещения", f"{mid}/{кр(куда)}: задача закрыта, а приняты не все")
    книга = sum(float(t.get("qty") or 0) for t in пер if t.get("by_kind") == "move"
                and not t.get("move_back") and not t.get("move_back_undo"))
    print(f"перемещения: отдано строками {строк:g}, переездов в книге {книга:g}"
          + ("  — сходится" if abs(строк - книга) < 1e-9 else "  — РАСХОЖДЕНИЕ"))
    if abs(строк - книга) > 1e-9:
        беда("перемещения", f"строки {строк:g} ≠ книга {книга:g}")


async def приёмки(d):
    async for s in d.supplies.find({}).sort("day", 1):
        for oid, t in (s.get("tasks") or {}).items():
            need = got = 0.0
            дыр = 0
            for it in s.get("items") or []:
                n = float((it.get("by_district") or {}).get(oid) or 0)
                if not n:
                    continue
                g = float((it.get("got") or {}).get(oid) or 0)
                need += n; got += g
                if g + 1e-9 < n: дыр += 1
                if g > n + 1e-9:
                    беда("приёмки", f'{s["_id"]} {кр(oid)}: {it.get("name")} принято {g:g} '
                                    f'при заказанных {n:g}')
            зап = len(t.get("gaps") or [])
            if t.get("done_at") and зап != дыр:
                беда("приёмки", f'{s["_id"]} {кр(oid)}: недоборов {дыр}, а записано при закрытии {зап}')
            n = await d.qr_codes.count_documents({"src": "intake", "supply_id": s["_id"], "origin": oid})
            if n != int(t.get("scanned") or 0):
                беда("приёмки", f'{s["_id"]} {кр(oid)}: кодов заведено {n}, '
                                f'а сканов записано {t.get("scanned")}')
            print(f'   {s.get("day")} {s["_id"]} {кр(oid)}: заказано {need:g}, принято {got:g}'
                  + (f', недобор {need-got:g} по {дыр} позициям' if got + 1e-9 < need else ', всё')
                  + (' · без сканирования' if t.get("noscan_at") else ''))


async def остаток(cat):
    counts = {oid: await db.get_last_stock_count(oid, before_day=None) for oid in OFFICE_IDS}
    since = {oid: sr._dt_of((counts[oid] or {}).get("counted_at") or "") for oid in OFFICE_IDS}
    sold = await sr._sold_after(since, None)
    broken = await db.writeoff_since(since, until=None)
    moved = await sr._moved_after(counts, since, None)
    noscan = await sr._noscan_after(since, None)
    sr.base_drop()
    base = await sr._district_base(sr._biz_day())
    минус = []
    for oid in OFFICE_IDS:
        сырой = {l["id"]: float(l.get("actual") or 0) for l in (counts[oid] or {}).get("lines", [])}
        came = await db.intake_since(oid, since[oid]) if since[oid] else {}
        manual = {pid: ats for pid, ats in (await db.qr_manual_events(oid, since[oid])).items() if pid in cat}
        for pid, n in came.items():
            сырой[pid] = сырой.get(pid, 0) + n
        for pid, n in (noscan.get(oid) or {}).items():
            сырой[pid] = сырой.get(pid, 0) + n
        for pid in set(sold.get(oid) or {}) | set(manual) | set(moved.get(oid) or {}):
            ev = [(-q) for _, q in ((sold.get(oid) or {}).get(pid) or [])]
            ev += [q for _, q in (manual.get(pid) or [])]
            ev += [q for _, q in ((moved.get(oid) or {}).get(pid) or [])]
            сырой[pid] = сырой.get(pid, 0) + sum(ev)
        for pid, n in (broken.get(oid) or {}).items():
            сырой[pid] = сырой.get(pid, 0) - n
        факт = (base.get(oid) or {}).get("have_exact") or {}
        for pid in set(сырой) | set(факт):
            a = round(float(сырой.get(pid, 0)) * 2) / 2
            b = float(факт.get(pid, 0) or 0)
            if a < -0.001 and abs(a - b) > 0.001:
                минус.append((oid, pid, a))
    print(f"\nпозиций, где продали или списали больше, чем числилось: {len(минус)}")
    for oid, pid, a in sorted(минус, key=lambda x: x[2]):
        print(f'   {кр(oid)} {cat.get(pid,{}).get("name",pid)[:34]:34s} не хватило {-a:g} ед.')
        беда("остаток", f'{кр(oid)} {cat.get(pid,{}).get("name",pid)}: продано на {-a:g} ед. больше, чем числилось')


async def продажи_и_списания(d, cat, коды):
    чужие = defaultdict(int)
    async for o in d.orders.find({}):
        oid = (o.get("office_id") or "").strip()
        if oid and oid not in OFFICE_IDS:
            чужие[oid] += 1
        for it in o.get("items") or []:
            pid, q = sr.sale_item(it, cat)
            if pid and pid not in cat:
                беда("продажи", f'{o.get("order_id")}: позиции {pid} нет в каталоге')
    for oid, n in чужие.items():
        беда("продажи", f"район {oid} вне списка: заказов {n}")
    async for w in d.writeoffs.find({}):
        c = str(w.get("code") or "")
        if w.get("item") and w.get("item") not in cat:
            беда("списания", f'{str(w.get("at"))[:16]}: позиции {w.get("item")} нет в каталоге')
        if c and (коды.get(c) or {}).get("status") == "active":
            беда("списания", f'{str(w.get("at"))[:16]}: бутылка {c} списана, а код живой')


async def main():
    await db.connect()
    d = db._db
    cat = sr._catalog()
    коды, пер = await коды_и_переезды(d, cat)
    print(f"кодов {len(коды)} · переездов {len(пер)}")
    await перемещения(d, пер)
    print("\nприёмки:")
    await приёмки(d)
    await остаток(cat)
    await продажи_и_списания(d, cat, коды)
    print("\n" + "=" * 68)
    if not БЕД:
        print("ПРОТИВОРЕЧИЙ НЕ НАЙДЕНО")
    for раздел in sorted(БЕД):
        print(f"\n{раздел}: {len(БЕД[раздел])}")
        for x in БЕД[раздел][:30]:
            print("   ", x)
        if len(БЕД[раздел]) > 30:
            print(f"    … и ещё {len(БЕД[раздел]) - 30}")
    return 1 if БЕД else 0


sys.exit(asyncio.run(main()))
