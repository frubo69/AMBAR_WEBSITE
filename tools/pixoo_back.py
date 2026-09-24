#!/usr/bin/env python3
"""Вернуть рамку Pixoo-64 на наш экран: макет и живое число клиентов.

Владелец, 24 сен 2026: «че с рамкой, куда исчез счётчик… она переключилась на
просто стоковые анимации». Так и есть: число мы отдаём каждые полминуты, рамка
его исправно скачивает, но показывает свой канал. Наш экран живёт в канале
«своя картинка», и рамка уходит с него сама — после выключения питания, потери
вайфая или нажатия на корпусе.

Запускать с ноутбука или телефона в ТОЙ ЖЕ Wi-Fi сети, что и рамка: сервер до
неё не достаёт, она живёт только в домашней сети.

    python3 tools/pixoo_back.py <IP рамки>                  # посмотреть, что с ней
    python3 tools/pixoo_back.py <IP рамки> <адрес цифры>    # вернуть наш экран

Адрес цифры — тот же, что в приложении Divoom (…/pixoo/КЛЮЧ). Ключ в репозитории
не храним: репозиторий публичный.
"""
import base64, json, sys, urllib.request
from pathlib import Path

КАНАЛЫ = {0: "лица (часы)", 1: "облако Divoom", 2: "визуализатор",
          3: "своя картинка — наш экран", 4: "стоковые анимации"}
МАКЕТ = Path(__file__).with_name("pixoo_design.png")
# Место под цифру в макете от 17 сен 2026 и шрифт, который на этой прошивке
# точно рисует цифры (см. tools/pixoo_design.py).
ОКНО = (15, 28, 34, 16)
ШРИФТ = 4


def post(ip: str, body: dict, тихо: bool = False) -> dict:
    req = urllib.request.Request(f"http://{ip}/post", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    if not тихо:
        print(" ", body["Command"], "→", d)
    return d


def канал(ip: str) -> int:
    d = post(ip, {"Command": "Channel/GetIndex"}, тихо=True)
    return int(d.get("SelectIndex", -1))


def кадр():
    """Макет 64×64 пиксель в пиксель, с вырезанным местом под цифру."""
    from PIL import Image
    import numpy as np
    im = Image.open(МАКЕТ).convert("RGB")
    W, H = im.size
    a = np.array(im)
    xs = [int((i + 0.5) * W / 64) for i in range(64)]
    ys = [int((j + 0.5) * H / 64) for j in range(64)]
    frame = np.array([[a[yy][xx] for xx in xs] for yy in ys], dtype=np.uint8)
    x, y, w, h = ОКНО
    края = np.concatenate([frame[y - 1, x:x + w], frame[y + h, x:x + w],
                           frame[y:y + h, x - 1], frame[y:y + h, x + w]])
    цвета, сколько = np.unique(края.reshape(-1, 3), axis=0, return_counts=True)
    frame[y:y + h, x:x + w] = цвета[сколько.argmax()]
    return frame


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ip = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        i = канал(ip)
    except Exception as e:                                   # noqa: BLE001
        print(f"рамка {ip} не отвечает: {e}\nПроверьте, что вы в той же сети и адрес верный.")
        return 1
    print(f"рамка {ip}: сейчас канал {i} — {КАНАЛЫ.get(i, 'неизвестный')}")
    if not url:
        print("\nЧтобы вернуть наш экран, повторите с адресом цифры:"
              "\n    python3 tools/pixoo_back.py " + ip + " http://…/pixoo/КЛЮЧ")
        return 0
    if not МАКЕТ.exists():
        print(f"макета нет: {МАКЕТ}")
        return 1
    x, y, w, h = ОКНО
    frame = кадр()
    print("возвращаю наш экран:")
    post(ip, {"Command": "Channel/SetIndex", "SelectIndex": 3})
    post(ip, {"Command": "Draw/ResetHttpGifId"})
    post(ip, {"Command": "Draw/SendHttpGif", "PicNum": 1, "PicWidth": 64, "PicOffset": 0,
              "PicID": 1, "PicSpeed": 1000,
              "PicData": base64.b64encode(frame.tobytes()).decode()})
    post(ip, {"Command": "Draw/ClearHttpText"})
    post(ip, {"Command": "Draw/SendHttpItemList", "ItemList": [
        {"TextId": 1, "type": 23, "x": x, "y": y + 1, "dir": 0, "font": ШРИФТ,
         "TextWidth": w, "Textheight": h, "speed": 100, "update_time": 30,
         "align": 2, "TextString": url, "color": "#FFD84A"}]})
    print(f"\nготово: канал {канал(ip)} — наш экран, число рамка перечитывает каждые 30 с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
