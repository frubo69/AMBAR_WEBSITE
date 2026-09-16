#!/usr/bin/env python3
"""Настроить рамку Divoom Pixoo-64 на счётчик АМБАРа (владелец, 16 сен 2026).

Запускать с компьютера или телефона в той же Wi-Fi сети, что и рамка:

    python3 tools/pixoo_setup.py <IP рамки> <адрес цифры> [<адрес второй цифры>]

Например:
    python3 tools/pixoo_setup.py 192.168.1.50 http://150-241-70-116.sslip.io/pixoo/КЛЮЧ \\
        http://150-241-70-116.sslip.io/pixoo/КЛЮЧ/online

Что делает: чистит экран (чёрный кадр 64×64), стирает прежние надписи и
ставит список элементов: заголовок AMBAR, большая цифра клиентов, которую
рамка сама обновляет каждые 30 секунд по ссылке, и, если дан второй адрес,
строка «на связи N». Рамка запоминает список, пока не выключат канал.
Шрифты и координаты — по вкусу, поле font 0–7, y в пикселях от верха."""
import base64, json, sys, urllib.request

if len(sys.argv) < 3:
    print(__doc__); sys.exit(1)
ip, url = sys.argv[1], sys.argv[2]
url2 = sys.argv[3] if len(sys.argv) > 3 else ""
EVERY = 30                     # секунд между запросами рамки к серверу

def post(body):
    req = urllib.request.Request(f"http://{ip}/post", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    print(body["Command"], "→", d)
    return d

# 1. свой кадр: чёрный, чтобы часы и картинки не просвечивали под цифрой
post({"Command": "Draw/ResetHttpGifId"})
black = base64.b64encode(bytes(64 * 64 * 3)).decode()
post({"Command": "Draw/SendHttpGif", "PicNum": 1, "PicWidth": 64, "PicOffset": 0,
      "PicID": 1, "PicSpeed": 1000, "PicData": black})
# 2. прежние надписи долой
post({"Command": "Draw/ClearHttpText"})
# 3. список элементов: 22 — свой текст, 23 — текст по ссылке (рамка обновляет сама)
items = [
    {"TextId": 1, "type": 22, "x": 0, "y": 6, "dir": 0, "font": 2, "TextWidth": 64, "Textheight": 12,
     "speed": 100, "align": 2, "TextString": "AMBAR", "color": "#E8C97A"},
    {"TextId": 2, "type": 23, "x": 0, "y": 22, "dir": 0, "font": 4, "TextWidth": 64, "Textheight": 22,
     "speed": 100, "update_time": EVERY, "align": 2, "TextString": url, "color": "#FFFFFF"},
]
if url2:
    items.append({"TextId": 3, "type": 22, "x": 0, "y": 48, "dir": 0, "font": 2, "TextWidth": 40, "Textheight": 12,
                  "speed": 100, "align": 1, "TextString": "online", "color": "#8C8EB7"})
    items.append({"TextId": 4, "type": 23, "x": 40, "y": 48, "dir": 0, "font": 2, "TextWidth": 24, "Textheight": 12,
                  "speed": 100, "update_time": EVERY, "align": 3, "TextString": url2, "color": "#3DDC84"})
post({"Command": "Draw/SendHttpItemList", "ItemList": items})
print("готово: рамка сама обновляет цифры каждые", EVERY, "с")
