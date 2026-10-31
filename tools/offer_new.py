#!/usr/bin/env python3
"""Персональное предложение (offers.py): создать по файлу состава, посмотреть, перечислить.

Владелец, 31 окт 2026: по кнопке в оффере корзина собирается сама и скидка
применяется сама; заказ по одному офферу — один; срок три дня; документ чей
угодно. Оффер — запись в db.offers, ссылка в PDF: t.me/<бот>?startapp=offer_<код>.

Файл состава (json) пишет генератор PDF (scratchpad/offer-2000/build.py --spec):
  {"items": [{"id": "p6", "qty": 1}, {"id": "p43", "qty": 1, "pcs": 12}, ...],
   "gift": {"id": "p97", "qty": 1}, "discount": 200, "total": 1750,
   "title": "Personal offer", "note": "для кого", "days": 3}
Пиво — только с pcs (12/24): пачка другой фасовки предложению не считается.

На сервере:
  /opt/ambar/venv/bin/python /opt/ambar/tools/offer_new.py spec.json   # создать: код, ссылка, срок
  /opt/ambar/venv/bin/python /opt/ambar/tools/offer_new.py --get КОД
  /opt/ambar/venv/bin/python /opt/ambar/tools/offer_new.py --list
"""
import asyncio, json, os, sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:                                        # noqa: BLE001
    pass
import db, offers                                        # noqa: E402

BOT = os.environ.get("AMBAR_BOT_USERNAME", "AmBarDelivery_bot")
DUBAI = timezone(timedelta(hours=4))


def _catalog() -> dict:
    with open(os.path.join(ROOT, "catalog.json"), encoding="utf-8") as f:
        return {p["id"]: p for p in json.load(f)}


def _unit(p: dict, pcs) -> int:
    if pcs == 24 and p.get("price_24"):
        return int(p["price_24"])
    if pcs == 12 and p.get("price_12"):
        return int(p["price_12"])
    return int(p.get("price") or 0)


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(DUBAI).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return str(iso or "—")


def link(code: str) -> str:
    return f"https://t.me/{BOT}?startapp=offer_{code}"


def check(spec: dict, cat: dict) -> list[str]:
    """Что не так с составом — список строк; пусто = можно создавать."""
    bad = []
    for it in spec.get("items") or []:
        p = cat.get(str(it.get("id")))
        if not p:
            bad.append(f"нет позиции {it.get('id')!r}"); continue
        if int(it.get("qty") or 0) <= 0:
            bad.append(f"{p['name']}: qty {it.get('qty')!r}")
        if p.get("cat") == "Пиво" and int(it.get("pcs") or 0) not in (12, 24):
            bad.append(f"{p['name']}: пиву нужен pcs 12 или 24")
        if p.get("cat") != "Пиво" and it.get("pcs"):
            bad.append(f"{p['name']}: pcs только у пива")
        if not p.get("stock", True):
            bad.append(f"{p['name']}: нет в наличии")
    g = spec.get("gift") or {}
    if g:
        p = cat.get(str(g.get("id")))
        if not p:
            bad.append(f"подарок: нет позиции {g.get('id')!r}")
        elif not p.get("stock", True):
            bad.append(f"подарок {p['name']}: нет в наличии")
    if int(spec.get("discount") or 0) < 0:
        bad.append("скидка с минусом")
    return bad


def summary(doc: dict, cat: dict) -> str:
    rows, goods = [], 0
    for it in doc.get("items") or []:
        p = cat.get(it["id"]) or {}
        u = _unit(p, it.get("pcs")); goods += u * it["qty"]
        rows.append(f"  {p.get('name', it['id']):<28} {it['qty']:>2}" + (f" × {it['pcs']} шт" if it.get("pcs") else "      ")
                    + f" {u:>6} {u*it['qty']:>7}")
    g = doc.get("gift")
    if g:
        p = cat.get(g["id"]) or {}
        rows.append(f"  {p.get('name', g['id']):<28} {g['qty']:>2}        подарок {0:>7}")
    d = int(doc.get("discount") or 0)
    rows.append(f"  {'по прайсу':<48} {goods:>7}")
    rows.append(f"  {'скидка':<48} {-d:>7}")
    rows.append(f"  {'к оплате':<48} {goods - d:>7}")
    if doc.get("total") is not None and int(doc["total"]) != goods - d:
        rows.append(f"  !! в файле total {doc['total']}, по каталогу {goods - d}")
    return "\n".join(rows)


async def create(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    cat = _catalog()
    bad = check(spec, cat)
    if bad:
        print("состав не годится:\n  " + "\n  ".join(bad)); return 2
    doc = offers.new_doc(spec.get("items") or [], spec.get("discount") or 0, spec.get("gift"),
                         title=spec.get("title") or "", total=spec.get("total"),
                         note=spec.get("note") or "", days=int(spec.get("days") or offers.DAYS))
    await db.connect()
    if db._db_or_none() is None:
        print("нет базы"); return 1
    if not await db.offer_create(doc):
        print("не записалось"); return 1
    print(f"код      {doc['code']}\nссылка   {link(doc['code'])}\nдо       {_when(doc['expires_at'])} (Дубай)")
    print(summary(doc, cat))
    return 0


async def get(code: str) -> int:
    await db.connect()
    doc = await db.offer_get(offers.norm_code(code))
    if not doc:
        print("нет такого"); return 1
    print(f"код      {doc['code']}  {offers.state(doc)} / {doc.get('status')}\nссылка   {link(doc['code'])}"
          f"\nсоздан   {_when(doc.get('created_at'))}\nдо       {_when(doc.get('expires_at'))}"
          f"\nзаказ    {doc.get('order_id') or '—'}" + (f"  ({_when(doc.get('used_at'))})" if doc.get("used_at") else ""))
    if doc.get("note"):
        print(f"заметка  {doc['note']}")
    print(summary(doc, _catalog()))
    return 0


async def lst() -> int:
    await db.connect()
    rows = await db.offers_list(100)
    if not rows:
        print("офферов нет"); return 0
    print(f"{'код':<10} {'состояние':<10} {'скидка':>6}  {'до':<12} {'заказ':<12} заметка")
    for d in rows:
        print(f"{d['code']:<10} {offers.state(d):<10} {int(d.get('discount') or 0):>6}  {_when(d.get('expires_at')):<12} "
              f"{(d.get('order_id') or '—'):<12} {d.get('note') or ''}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 1 and argv[0] == "--list":
        return asyncio.run(lst())
    if len(argv) == 2 and argv[0] == "--get":
        return asyncio.run(get(argv[1]))
    if len(argv) == 1 and os.path.isfile(argv[0]):
        return asyncio.run(create(argv[0]))
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
