"""Пометить, откуда бутылка в реестре кодов: приход руками (new) или код к
уже посчитанной бутылке (cover).

С 10 сен 2026 склад считает бутылки с src=new приходом района
(stock_routes._district_base → db.qr_manual_since). Коды, снятые до этого,
поля src не имеют и приходом не считаются. Этим скриптом владелец
переводит такой код в приход, если бутылка была новой.

Без --apply только показывает, что найдено и что изменится:

  /opt/ambar/venv/bin/python /opt/ambar/tools/qr_mark_src.py --label bla_labe#000022
  /opt/ambar/venv/bin/python /opt/ambar/tools/qr_mark_src.py --label bla_labe#000022 --src new --apply
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
    ap.add_argument("--label", help="метка бутылки, например bla_labe#000022")
    ap.add_argument("--code", help="сам код, если метки нет")
    ap.add_argument("--src", choices=["new", "cover"], default="new")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not a.label and not a.code:
        ap.error("нужен --label или --code")
    await db.connect()
    d = db._db_or_none()
    if d is None:
        print("нет базы"); return 1
    q = {"_id": a.code} if a.code else {"label": a.label}
    doc = await d.qr_codes.find_one(q)
    if not doc:
        print("в реестре нет такого кода"); return 1
    print(f"{doc.get('label')} · {doc.get('product_name') or doc.get('product_id')} · "
          f"{doc.get('district')} · {str(doc.get('at'))[:16]} · status={doc.get('status')} · "
          f"src={doc.get('src') or '—'}")
    if doc.get("src") == a.src:
        print("уже помечен"); return 0
    if not a.apply:
        print(f"будет src={a.src} (запусти с --apply)"); return 0
    r = await d.qr_codes.update_one({"_id": doc["_id"]}, {"$set": {"src": a.src}})
    try:
        import stock_routes
        stock_routes.base_drop()
    except Exception:                                # noqa: BLE001
        pass
    print("помечено" if r.modified_count else "не изменилось")
    print("кэш основы склада живёт до минуты: на странице склада число появится после него")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
