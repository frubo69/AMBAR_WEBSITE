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


def _vals(s: str, key: str) -> list:
    """Все значения ключа, а не первое.

    В .env исторически лежат две строки MONGO_URI, и это не опечатка, которую
    можно молча поправить в одном месте: dotenv берёт последнюю, а глазами
    читается первая. Поэтому собираем все, а пишем ровно по одной строке на
    ключ — иначе «переключил» и «переключилось» опять разойдутся."""
    return [m.strip() for m in re.findall(rf"^{key}=(.+)$", s, re.M)]


def _kind(uri: str) -> str:
    if "127.0.0.1" in uri or "localhost" in uri:
        return "local"
    if "mongodb.net" in uri:
        return "atlas"
    return "?"


def _label(uri: str) -> str:
    return {"local": "локальная (этот сервер)",
            "atlas": "Atlas (облако)"}.get(_kind(uri), "неизвестно")


def _write(s: str, key: str, value: str) -> str:
    """Одна строка на ключ: первую заменяем, остальные убираем."""
    seen = {"n": 0}

    def sub(m):
        seen["n"] += 1
        return f"{key}={value}" if seen["n"] == 1 else None

    out = []
    for line in s.split("\n"):
        if re.match(rf"^{key}=", line):
            seen["n"] += 1
            if seen["n"] == 1:
                out.append(f"{key}={value}")
            # повторные строки просто не переносим
        else:
            out.append(line)
    if seen["n"] == 0:
        out.append(f"{key}={value}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--swap", action="store_true", help="поменять местами")
    ap.add_argument("--to", choices=("local", "atlas"), help="сделать боевой указанную")
    a = ap.parse_args()

    s = io.open(ENV, encoding="utf-8").read()
    mains, spares = _vals(s, "MONGO_URI"), _vals(s, "MONGO_URI_STANDBY")
    if not mains:
        sys.exit("в .env нет MONGO_URI — ничего не трогаю")

    # Действует последняя строка — так читает dotenv, так работает приложение.
    effective = mains[-1]
    known = {}
    for uri in mains + spares:
        k = _kind(uri)
        if k != "?":
            known[k] = uri
    if len(mains) > 1:
        print(f"внимание: строк MONGO_URI в .env — {len(mains)}, действует последняя")
    print(f"сейчас боевая:  {_label(effective)}")
    if spares:
        print(f"сейчас зеркало: {_label(spares[-1])}")

    if not (a.swap or a.to):
        print("\nничего не менял. Для смены: --swap или --to local|atlas")
        return

    target = a.to or ("atlas" if _kind(effective) == "local" else "local")
    other = "atlas" if target == "local" else "local"
    if target not in known:
        sys.exit(f"строки подключения к «{target}» в .env нет — не могу переключить")
    if other not in known:
        sys.exit(f"строки подключения к «{other}» в .env нет — не на что менять зеркало")
    if _kind(effective) == target and len(mains) == 1:
        print(f"\nбоевая уже {_label(effective)} — менять нечего")
        return

    bak = f"{ENV}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(ENV, bak)
    s = _write(s, "MONGO_URI", known[target])
    s = _write(s, "MONGO_URI_STANDBY", known[other])
    io.open(ENV, "w", encoding="utf-8").write(s)
    os.chmod(ENV, 0o600)

    after = _vals(io.open(ENV, encoding="utf-8").read(), "MONGO_URI")
    print(f"\nстало боевой:   {_label(after[-1])}  (строк MONGO_URI: {len(after)})")
    print(f"стало зеркалом: {_label(known[other])}")
    print(f"копия прежнего .env: {bak}")
    print(f"\nтеперь перезапусти сервисы:\n  systemctl restart {SERVICES}")


if __name__ == "__main__":
    main()
