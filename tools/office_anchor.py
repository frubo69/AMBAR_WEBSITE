#!/usr/bin/env python3
"""
Готовит строку AMBAR_OFFICE_ANCHORS для /opt/ambar/.env.

╔══════════════════════════════════════════════════════════════════════════╗
║  ЧТО ВБИВАТЬ                                                             ║
║                                                                          ║
║  Точку в СЕРЕДИНЕ РАЙОНА — площадь, перекрёсток, торговый центр.         ║
║  НЕ адрес склада.                                                        ║
║                                                                          ║
║  Почему: системе нужно понять, к какому району ближе клиент, а не где    ║
║  стоит склад. Если опорная точка — публичное место, то её утечка не      ║
║  значит ничего: «центр JVC находится в центре JVC». Если же вбить сюда   ║
║  склад, то даже округлённая точка остаётся указателем на него — просто   ║
║  размазанным по клетке 2×2 км, которую можно обойти ногами.              ║
║                                                                          ║
║  Скрипт НИЧЕГО никуда не отправляет и ничего не сохраняет на диск:       ║
║  считает, печатает строку и забывает. Введённое остаётся только в этом   ║
║  окне терминала.                                                         ║
╚══════════════════════════════════════════════════════════════════════════╝

    python3 tools/office_anchor.py

Дальше: скопировать напечатанную строку в /opt/ambar/.env,
затем `systemctl restart ambar-api`.
"""
import re
import sys

# Сетка огрубления. 0.02° ≈ 2.2 км по широте и ≈ 2.0 км по долготе на широте
# Дубая. Округление необратимо — из клетки исходную точку не достать.
GRID = 0.02

OFFICES = [
    ("jvc",     "JVC"),
    ("tecom",   "Тиком"),
    ("bbay",    "Бизнес Бей"),
    ("silicon", "Силикон"),
    ("alguses", "Алгусес"),
]


def snap(value: float) -> float:
    """Притянуть координату к узлу сетки GRID."""
    return round(round(float(value) / GRID) * GRID, 4)


def parse(text: str):
    """Достать (lat, lon) из '25.06, 55.21' или из ссылки Google Maps."""
    text = (text or "").strip()
    if not text:
        return None

    # Прямая пара чисел
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    # Ссылка Google Maps: @lat,lon  |  !3dlat!4dlon  |  q=lat,lon
    for pat in (r"@(-?\d+\.\d+),(-?\d+\.\d+)",
                r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",
                r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)"):
        m = re.search(pat, text)
        if m:
            return float(m.group(1)), float(m.group(2))

    return None


def main():
    print(__doc__)
    print("Для каждого района: координаты его СЕРЕДИНЫ (не склада).")
    print("Формат: 25.06, 55.21  — или полная ссылка Google Maps с координатами.")
    print("Enter — пропустить район (тогда он не будет участвовать в гео-выборе).\n")

    parts = []
    for oid, title in OFFICES:
        while True:
            try:
                raw = input(f"  {title} ({oid}): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nОтменено.")
                return 1
            if not raw:
                print(f"    — пропущен\n")
                break
            got = parse(raw)
            if not got:
                print("    ✗ не понял. Нужны два числа «25.06, 55.21» или ссылка,")
                print("      в которой есть координаты (в короткой ссылке их нет —")
                print("      открой её, потом скопируй числа из адресной строки).")
                continue
            lat, lon = got
            slat, slon = snap(lat), snap(lon)
            print(f"    ✓ огрублено до сетки ~2 км → {slat},{slon}\n")
            parts.append(f"{oid}:{slat},{slon}")
            break

    if not parts:
        print("Ничего не введено — строка не нужна.")
        return 0

    print("\n" + "═" * 68)
    print("Вставь эту строку в /opt/ambar/.env (одной строкой):\n")
    print("AMBAR_OFFICE_ANCHORS=" + "|".join(parts))
    print("\n" + "═" * 68)
    print("Затем:  systemctl restart ambar-api")
    print("Точные координаты, которые ты вводил, нигде не сохранены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
