"""Обнулить склады: стереть все пересчёты, ревизии с их сканами и списания
«недостача», записанные ревизиями. Остаток становится тем, что внесено
кодами: реестр бутылок не трогается, настоящие списания водителей тоже.

Зачем: владелец начинает учёт заново — считает полки по бумажке и заводит
бутылки сканом; старые пересчёты и след ошибочной ревизии только мешают
(10 сен 2026: «обнули все склады, эта цифра уже неактуальна»).

Перед стиранием всё стираемое выгружается в JSON рядом с базой, чтобы было
куда вернуться. Без --apply — только показывает, что найдено.

  /opt/ambar/venv/bin/python /opt/ambar/tools/stock_reset.py
  /opt/ambar/venv/bin/python /opt/ambar/tools/stock_reset.py --apply
"""
import argparse, asyncio, json, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:                                   # noqa: BLE001
    pass
import db

OUT_DIR = os.environ.get("AMBAR_RESET_DIR", "/root/stock_reset")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    await db.connect()
    d = db._db_or_none()
    if d is None:
        print("нет базы"); return 1
    counts = await d.stock_counts.find({}).to_list(length=1000)
    audits = await d.stock_audits.find({}).to_list(length=1000)
    scans = await d.audit_scans.find({}).to_list(length=100000)
    wos = await d.writeoffs.find({"src": "audit"}).to_list(length=10000)

    print(f"пересчётов: {len(counts)}")
    for c in sorted(counts, key=lambda x: str(x.get("counted_at"))):
        lines = c.get("lines") or []
        print(f"  {c.get('district')!s:10} {c.get('day')} · {str(c.get('counted_at'))[:16]} · "
              f"строк {len(lines)} · с остатком {sum(1 for l in lines if float(l.get('actual') or 0) > 0)} · "
              f"единиц {sum(float(l.get('actual') or 0) for l in lines):.0f}")
    print(f"ревизий: {len(audits)}")
    for x in audits:
        s = x.get("short") or {}
        print(f"  {x.get('district')!s:10} {x.get('day')} · начата {str(x.get('started_at'))[:16]} · "
              f"завершена {str(x.get('finished_at') or '—')[:16]} · недостача {s.get('qty', 0)} бут / {s.get('aed', 0)} AED")
    print(f"сканов ревизий: {len(scans)}")
    print(f"списаний «недостача» от ревизий: {len(wos)} на {sum(int(w.get('qty') or 0) for w in wos)} бут")
    if not a.apply:
        print("сухой прогон: ничего не стёрто (запусти с --apply)"); return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUT_DIR, f"stock_reset_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"stock_counts": counts, "stock_audits": audits, "audit_scans": scans,
                   "writeoffs_audit": wos}, f, ensure_ascii=False, default=str)
    print(f"выгружено в {path} ({os.path.getsize(path)} байт)")
    r1 = await d.writeoffs.delete_many({"src": "audit"})
    r2 = await d.stock_counts.delete_many({})
    r3 = await d.stock_audits.delete_many({})
    r4 = await d.audit_scans.delete_many({})
    print(f"стёрто: списаний {r1.deleted_count}, пересчётов {r2.deleted_count}, "
          f"ревизий {r3.deleted_count}, сканов {r4.deleted_count}")
    print("кэш основы склада живёт до минуты; перезапуск ambar-api снимает его сразу")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
