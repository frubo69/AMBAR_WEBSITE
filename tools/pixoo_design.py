#!/usr/bin/env python3
"""Залить на Pixoo-64 макет владельца пиксель в пиксель и поверх — живое число.

    python3 tools/pixoo_design.py <png> <IP рамки> <адрес цифры> [x y w h [font]]

<png> — макет 64×64 или увеличенный ровно в целое число раз (точки берутся из
центров клеток). Прямоугольник x y w h (в пикселях рамки) вырезается под живое
число: заливается цветом фона, и туда ставится элемент «текст по ссылке» — рамка
сама перечитывает его каждые 30 с. По умолчанию — место цифры «128» в макете
от 17 сен 2026: x=15 y=28 w=34 h=16, шрифт 2 (он точно рисуется на этой прошивке)."""
import base64, json, sys, urllib.request
from PIL import Image
import numpy as np

if len(sys.argv) < 4:
    print(__doc__); sys.exit(1)
png, ip, url = sys.argv[1:4]
x, y, w, h = (int(v) for v in (sys.argv[4:8] or (15, 28, 34, 16)))
font = int(sys.argv[8]) if len(sys.argv) > 8 else 4   # 4 — самый крупный шрифт с цифрами на этой прошивке (2 — мелкий; 0,1,3,5–11 цифр не рисуют)

im = Image.open(png).convert("RGB"); W, H = im.size
a = np.array(im)
xs = [int((i + 0.5) * W / 64) for i in range(64)]; ys = [int((j + 0.5) * H / 64) for j in range(64)]
frame = np.array([[a[yy][xx] for xx in xs] for yy in ys], dtype=np.uint8)
# фон под цифру — самый частый цвет по краю вырезаемого прямоугольника
border = np.concatenate([frame[y - 1, x:x + w], frame[y + h, x:x + w], frame[y:y + h, x - 1], frame[y:y + h, x + w]])
vals, counts = np.unique(border.reshape(-1, 3), axis=0, return_counts=True)
bg = vals[counts.argmax()]
frame[y:y + h, x:x + w] = bg
Image.fromarray(frame).save(png.rsplit(".", 1)[0] + "-64.png")

def post(body):
    req = urllib.request.Request(f"http://{ip}/post", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    print(body["Command"], "→", d); return d

post({"Command": "Draw/ResetHttpGifId"})
post({"Command": "Draw/SendHttpGif", "PicNum": 1, "PicWidth": 64, "PicOffset": 0, "PicID": 1,
      "PicSpeed": 1000, "PicData": base64.b64encode(frame.tobytes()).decode()})
post({"Command": "Draw/ClearHttpText"})
post({"Command": "Draw/SendHttpItemList", "ItemList": [
    {"TextId": 1, "type": 23, "x": x, "y": y + 1, "dir": 0, "font": font, "TextWidth": w, "Textheight": h,
     "speed": 100, "update_time": 30, "align": 2, "TextString": url, "color": "#FFD84A"}]})
print("готово: макет на рамке, число по ссылке обновляется каждые 30 с; фон под цифрой", tuple(int(v) for v in bg))
