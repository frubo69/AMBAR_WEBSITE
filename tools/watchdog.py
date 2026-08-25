#!/usr/bin/env python3
"""Сторож: замечает поломки раньше, чем их заметит смена.

База переехала на наш сервер, и вместе с ней к нам переехала обязанность
следить. Молчаливая поломка — худшее, что может случиться: копия не снялась
неделю, диск кончился ночью, mongod не поднялся после перезагрузки. Всё это
видно за секунду, если кто-то смотрит; поэтому смотрит этот скрипт, а пишет —
владельцу в телеграм.

Пишет только про изменения. Пока всё плохо — молчит после первого сообщения,
пока не починится: сторож, который шлёт одно и то же каждые десять минут,
через день отправляется в глухую папку, и это конец всей затее.

    python3 tools/watchdog.py           # проверить и оповестить при изменении
    python3 tools/watchdog.py --report  # показать состояние и ничего не слать
"""
import argparse
import json
import os
import shutil
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/ambar")
STATE = ROOT / ".watchdog-state.json"
BACKUPS = ROOT / "backups"
SERVICES = ["ambar-api", "ambar-bot", "ambar-operator", "ambar-owner-bot",
            "ambar-driver-bot", "ambar-support", "ambar-promo-bot", "mongod"]

BACKUP_MAX_MIN = 100          # копия раз в час + разбег 5 минут
DISK_MAX_PCT = 85
MEM_MIN_MB = 400


def _env(name: str) -> str:
    v = os.getenv(name, "")
    if v:
        return v
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def _sh(*cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def check_services() -> list:
    bad = []
    for s in SERVICES:
        if _sh("systemctl", "is-active", s) != "active":
            bad.append(s)
    return [f"сервис не работает: {', '.join(bad)}"] if bad else []


def check_mongo() -> list:
    uri = _env("MONGO_URI")
    if "127.0.0.1" not in uri:
        return []                      # боевая в облаке — следить нечего
    code = ("import pymongo,sys;"
            "c=pymongo.MongoClient(sys.argv[1],serverSelectionTimeoutMS=5000);"
            "print(c.admin.command('ping')['ok'])")
    out = _sh(str(ROOT / "venv/bin/python"), "-c", code, uri)
    return [] if out.strip() == "1.0" else ["база не отвечает на локальном сервере"]


def check_backup() -> list:
    files = sorted(BACKUPS.glob("ambar-*.jsonl.gz"))
    if not files:
        return ["резервных копий нет вообще"]
    newest = max(files, key=lambda p: p.stat().st_mtime)
    age_min = (datetime.now().timestamp() - newest.stat().st_mtime) / 60
    if age_min > BACKUP_MAX_MIN:
        return [f"последняя копия сделана {age_min/60:.1f} ч назад — копии не снимаются"]
    if newest.stat().st_size < 50_000:
        return [f"последняя копия подозрительно мала: {newest.stat().st_size/1024:.0f} КБ"]
    return []


def check_errors() -> list:
    """Необработанные исключения в логах за последние минуты.

    Сутки заказы из приложения падали на одной строке кода, и узнали мы об
    этом от клиента. Сторож смотрел на базу, копии, диск и память — но не на
    то, отвечает ли API вообще. Теперь смотрит: любое падение обработчика
    видно в течение десяти минут, вместе с текстом ошибки.
    """
    out = _sh("journalctl", "-u", "ambar-api", "-u", "ambar-operator",
              "--since", "-12min", "--no-pager")
    if not out:
        return []
    lines = out.split("\n")
    # Считаем именно падения запросов: одиночные WARNING — это норма жизни.
    kinds: dict = {}
    for i, line in enumerate(lines):
        if "Error handling request" in line or "Traceback (most recent call last)" in line:
            # тип ошибки лежит в конце трассировки — ищем ниже по тексту
            for nxt in lines[i:i + 25]:
                m = re.search(r"([A-Za-z_]+Error|Exception)\b:?(.*)$", nxt)
                if m and "Traceback" not in nxt:
                    key = (m.group(1) + ":" + m.group(2).strip())[:120]
                    kinds[key] = kinds.get(key, 0) + 1
                    break
    if not kinds:
        return []
    top = sorted(kinds.items(), key=lambda kv: -kv[1])[:3]
    total = sum(kinds.values())
    lines_out = " · ".join(f"{k} ×{n}" for k, n in top)
    return [f"падений запросов за 12 мин: {total} — {lines_out}"]


def check_offsite() -> list:
    """Копия наружу уходит четыре раза в сутки. Если её нет больше суток —
    третьего места у нас фактически не осталось, и знать об этом надо до того,
    как оно понадобится."""
    stamp = ROOT / ".b2-last"
    if not stamp.exists():
        return []                      # выгрузка ещё не настроена — не шумим
    try:
        at = datetime.fromisoformat(stamp.read_text().strip())
    except Exception:
        return ["не разобрать отметку о выгрузке в хранилище"]
    hours = (datetime.now(timezone.utc) - at).total_seconds() / 3600
    if hours > 26:
        return [f"копия не уходила в хранилище {hours/24:.1f} сут"]
    return []


def check_disk() -> list:
    u = shutil.disk_usage("/")
    pct = u.used / u.total * 100
    if pct > DISK_MAX_PCT:
        return [f"диск занят на {pct:.0f}% — свободно {u.free/2**30:.1f} ГБ"]
    return []


def check_mem() -> list:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.strip().split()[0]) // 1024
        if info.get("MemAvailable", 0) < MEM_MIN_MB:
            return [f"мало памяти: свободно {info['MemAvailable']} МБ"]
    except Exception:
        pass
    return []


def check_api() -> list:
    code = _sh("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "10",
               "http://127.0.0.1:8080/api/owner/finance")
    # 401 — это здоровый ответ: обработчик жив и требует авторизацию.
    return [] if code in ("200", "401", "403", "404") else [f"API не отвечает (код {code or '—'})"]


CHECKS = [check_services, check_mongo, check_errors, check_backup,
          check_offsite, check_disk, check_mem, check_api]


def notify(text: str) -> None:
    token = _env("AMBAR_OWNER_BOT_TOKEN")
    ids = [i for i in _env("AMBAR_OWNER_IDS").replace(" ", "").split(",") if i.isdigit()]
    if not (token and ids):
        print("некому слать: нет токена или списка владельцев")
        return
    for uid in ids:
        data = urllib.parse.urlencode({"chat_id": uid, "text": text,
                                       "parse_mode": "Markdown"}).encode()
        try:
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as e:
            print(f"не отправилось {uid}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="показать и не слать")
    a = ap.parse_args()

    problems = []
    for c in CHECKS:
        try:
            problems += c()
        except Exception as e:
            problems.append(f"проверка {c.__name__} упала: {e}")

    if a.report:
        print("\n".join(problems) if problems else "всё в порядке")
        return

    try:
        prev = json.loads(STATE.read_text())
    except Exception:
        prev = {"problems": []}
    was = set(prev.get("problems", []))
    now = set(problems)

    if now and now != was:
        notify("🔴 *AMBAR — сервер*\n\n" + "\n".join(f"• {p}" for p in sorted(now)))
    elif was and not now:
        notify("🟢 *AMBAR — сервер*\nВсё восстановилось.")

    STATE.write_text(json.dumps(
        {"problems": sorted(now), "at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False))
    print("\n".join(problems) if problems else "всё в порядке")


if __name__ == "__main__":
    main()
