#!/usr/bin/env python3
"""Сторож рамки Pixoo-64: держит её на нашем экране.

Владелец, 29 сен 2026: «пофикси рамку, она снова слетела». Слетает не
случайно: в настройках самой рамки `PowerOnChannelId = 1`, то есть при каждом
включении она уходит на облачный канал. По API это поле не меняется —
перепробованы пять команд, все «illegal json». Значит возвращать её должен
кто-то снаружи.

Две беды, и они разные:
  • ушла с нашего канала — видно прямо из дома (Channel/GetIndex);
  • стоит на нашем канале и МОЛЧИТ — «текст по ссылке» у неё умирает, команда
    принимается (error_code 0), а за числом рамка не идёт. Изнутри дома это
    неотличимо от работы; видно только с сервера, куда она перестала
    приходить. Поэтому спрашиваем у него: /pixoo/<ключ>/seen → сколько секунд
    назад рамка была.

Лечится и то и другое одним — ПОЛНОЙ последовательностью pixoo_back (канал,
сброс, кадр, текст): «ClearHttpText + SendHttpItemList» без кадра рамку не
будит, проверено 29 сен.

Настройки — ВНЕ репозитория, он публичный: ~/.ambar_pixoo.json, права 600
  {"ip": "192.168.1.175", "url": "http://…/pixoo/КЛЮЧ"}

    python3 tools/pixoo_watch.py          # одна проверка
    python3 tools/pixoo_watch.py --force  # вернуть, не спрашивая
Раз в пять минут — через launchd, см. tools/pixoo_watch.plist
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
НАСТРОЙКИ = os.path.expanduser("~/.ambar_pixoo.json")
ЖУРНАЛ = os.path.expanduser("~/Library/Logs/ambar_pixoo.log")
НАШ_КАНАЛ = 3
# Рамка ходит раз в 30 с. Три минуты молчания — это шесть пропущенных подряд,
# случайностью уже не объяснить.
МОЛЧИТ_СЕК = 180


def лог(строка):
    try:
        with open(ЖУРНАЛ, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + строка + "\n")
    except OSError:
        pass


def канал(ip: str, секунд=8):
    req = urllib.request.Request(f"http://{ip}/post",
                                 data=json.dumps({"Command": "Channel/GetIndex"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=секунд) as r:
        return int(json.load(r).get("SelectIndex", -1))


def молчит(url: str, секунд=8) -> int:
    """Сколько секунд назад рамка приходила к нам за числом. -1 — неизвестно."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/seen", timeout=секунд) as r:
            return int(json.load(r).get("ago", -1))
    except Exception:                                    # noqa: BLE001
        return -1


def вернуть(ip: str, url: str) -> bool:
    r = subprocess.run([sys.executable, os.path.join(ЗДЕСЬ, "pixoo_back.py"), ip, url],
                       capture_output=True, text=True)
    if r.returncode:
        лог(f"вернуть не вышло: {(r.stderr or r.stdout).strip()[:160]}")
    return r.returncode == 0


def main():
    try:
        c = json.load(open(НАСТРОЙКИ, encoding="utf-8"))
    except Exception as e:                               # noqa: BLE001
        print(f"нет настроек {НАСТРОЙКИ}: {e}")
        return 2
    ip, url = c["ip"], c["url"]
    сила = "--force" in sys.argv

    try:
        к = канал(ip)
    except Exception as e:                               # noqa: BLE001
        # Дома нет, ноутбук в другой сети, рамка выключена — не беда и не повод
        # шуметь: вернёмся через пять минут.
        лог(f"рамки не видно: {str(e)[:70]}")
        return 0

    ago = молчит(url)
    беда = ("не наш канал" if к != НАШ_КАНАЛ else
            f"молчит {ago} с" if ago > МОЛЧИТ_СЕК else "")
    if not беда and not сила:
        лог(f"в порядке (канал {к}, была {ago} с назад)")
        return 0
    лог(f"возвращаю: {беда or 'по требованию'} (канал {к}, была {ago} с назад)")
    if not вернуть(ip, url):
        return 1
    time.sleep(2)
    лог(f"вернул, канал {канал(ip)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
