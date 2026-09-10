"""Стереть след ревизии или пересчёта района за день: списания «недостача»,
записанные её решением, сам пересчёт, запись ревизии и её сканы.

Для ревизии, которую завершили по ошибке (например, «камерой 0» при полной
полке): всё, что числилось, ушло в недостачу, а пересчёт района записался
нулями — и склад «опустел». Сервер умеет то же самое через «Возобновить»,
но ревизия при этом остаётся начатой; здесь она исчезает целиком, а район
возвращается к предыдущему пересчёту.

Без --apply только показывает, что найдено и что будет стёрто:

  /opt/ambar/venv/bin/python /opt/ambar/tools/audit_purge.py --district alguses --day 2026-09-09
  /opt/ambar/venv/bin/python /opt/ambar/tools/audit_purge.py --district alguses --day 2026-09-09 --apply
"""
import argparse, asyncio, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:                                   # noqa: BLE001
    pass
import db


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", required=True)
    ap.add_argument("--day", required=True, help="день ревизии, ГГГГ-ММ-ДД")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    await db.connect()
    d = db._db_or_none()
    if d is None:
        print("нет базы"); return 1
    import stock_routes as SR
    if a.district not in SR.OFFICE_IDS:
        print(f"нет такого района: {a.district}"); return 1

    cnt = await d.stock_counts.find_one({"district": a.district, "day": a.day})
    aud = await d.stock_audits.find_one({"district": a.district, "day": a.day})
    scans = await d.audit_scans.count_documents({"district": a.district, "day": a.day})
    short = (aud or {}).get("short") or {}
    wids = list(short.get("writeoffs") or [])
    wos = [w async for w in d.writeoffs.find({"_id": {"$in": wids}}, {"img": 0})] if wids else []
    prev = await db.get_last_stock_count(a.district, before_day=a.day)

    print(f"{SR.OFFICE_NAMES.get(a.district, a.district)} · {a.day}")
    if cnt:
        lines = cnt.get("lines") or []
        print(f"  пересчёт: {str(cnt.get('counted_at'))[:16]} · строк {len(lines)} · "
              f"бутылок {sum(float(l.get('actual') or 0) for l in lines):.0f} · "
              f"камерой {cnt.get('scan_qty', '—')}")
    else:
        print("  пересчёта нет")
    if aud:
        print(f"  ревизия: начата {str(aud.get('started_at'))[:16]} · "
              f"завершена {str(aud.get('finished_at') or '—')[:16]} · "
              f"недостача {short.get('qty', 0)} бут / {short.get('aed', 0)} AED"
              + (f" · решена {str(short.get('resolved_at'))[:16]}"
                 f" ({'удержание ' + str(short.get('amount')) + ' с ' + str(short.get('who')) if short.get('blame') else 'без виновного'})"
                 if short.get("resolved_at") else ""))
        print(f"  списаний по её решению: {len(wos)} на {sum(int(w.get('qty') or 0) for w in wos)} бут")
    else:
        print("  ревизии нет")
    print(f"  сканов ревизии: {scans}")
    if prev:
        pl = prev.get("lines") or []
        print(f"  район вернётся к пересчёту {prev.get('day')} "
              f"({sum(float(l.get('actual') or 0) for l in pl):.0f} бутылок)")
    else:
        print("  предыдущего пересчёта нет — район станет непересчитанным")
    if not (cnt or aud or scans or wos):
        print("стирать нечего"); return 0
    if not a.apply:
        print("сухой прогон: ничего не стёрто (запусти с --apply)"); return 0

    n_wo = 0
    if aud:
        n_wo = await SR._audit_undo_short(aud)       # списания + «удержание снято», если было
        await d.stock_audits.delete_one({"_id": aud["_id"]})
    if cnt:
        await db.delete_stock_count(a.district, a.day)
    n_sc = await db.audit_scan_clear(a.district, a.day) if scans else 0
    print(f"стёрто: списаний {n_wo}, пересчёт {'да' if cnt else 'нет'}, "
          f"ревизия {'да' if aud else 'нет'}, сканов {n_sc}")
    print("кэш основы склада живёт до минуты: на странице склада число появится после него")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
