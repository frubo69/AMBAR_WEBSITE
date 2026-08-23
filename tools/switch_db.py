#!/usr/bin/env python3
"""Переключение боевой базы между локальной и Atlas — одной командой.

Меняет местами MONGO_URI и MONGO_URI_STANDBY в .env. Всё остальное в системе
читает только MONGO_URI, поэтому это и есть переключение: куда указывает эта
строка, там и боевая база, а вторая автоматически становится зеркалом, в
которое почасовая копия заливается сама.

    python3 tools/switch_db.py            # показать, где сейчас боевая
    python3 tools/switch_db.py --swap     # поменять местами
    python3 tools/switch_db.py --to local # явно: боевая = локальная
    python3 tools/switch_db.py --to atlas # явно: боевая = Atlas (откат)

После смены нужно перезапустить сервисы — команда печатается в конце.
Перед записью .env сохраняется рядом с отметкой времени: откат руками всегда
возможен, даже если что-то пойдёт не так.
"""
import argparse
import io
import os
import re
import shutil
import sys
from datetime import datetime

ENV = "/opt/ambar/.env"
SERVICES = ("ambar-api ambar-bot ambar-operator ambar-owner-bot "
            "ambar-driver-bot ambar-support ambar-promo-bot")


def _val(s: str, key: str) -> str:
    m = re.search(rf"^{key}=(.+)$", s, re.M)
    return m.group(1).strip() if m else ""


def _kind(uri: str) -> str:
    if "127.0.0.1" in uri or "localhost" in uri:
        return "local"
    if "mongodb.net" in uri:
        return "atlas"
    return "?"


def _label(uri: str) -> str:
    return {"local": "локальная (этот сервер)",
            "atlas": "Atlas (облако)"}.get(_kind(uri), "неизвестно")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--swap", action="store_true", help="поменять местами")
    ap.add_argument("--to", choices=("local", "atlas"), help="сделать боевой указанную")
    a = ap.parse_args()

    s = io.open(ENV, encoding="utf-8").read()
    main_uri, spare_uri = _val(s, "MONGO_URI"), _val(s, "MONGO_URI_STANDBY")
    if not main_uri or not spare_uri:
        sys.exit("в .env нет пары MONGO_URI / MONGO_URI_STANDBY — ничего не трогаю")

    print(f"сейчас боевая:  {_label(main_uri)}")
    print(f"сейчас зеркало: {_label(spare_uri)}")

    if not (a.swap or a.to):
        print("\nничего не менял. Для смены: --swap или --to local|atlas")
        return
    if a.to and _kind(main_uri) == a.to:
        print(f"\nбоевая уже {_label(main_uri)} — менять нечего")
        return
    if _kind(main_uri) == "?" or _kind(spare_uri) == "?":
        sys.exit("не могу опознать одну из строк подключения — не трогаю")

    bak = f"{ENV}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(ENV, bak)
    s = re.sub(r"^MONGO_URI=.*$", "MONGO_URI=" + spare_uri, s, count=1, flags=re.M)
    s = re.sub(r"^MONGO_URI_STANDBY=.*$", "MONGO_URI_STANDBY=" + main_uri, s, count=1, flags=re.M)
    io.open(ENV, "w", encoding="utf-8").write(s)
    os.chmod(ENV, 0o600)

    print(f"\nстало боевой:   {_label(spare_uri)}")
    print(f"стало зеркалом: {_label(main_uri)}")
    print(f"копия прежнего .env: {bak}")
    print(f"\nтеперь перезапусти сервисы:\n  systemctl restart {SERVICES}")


if __name__ == "__main__":
    main()
