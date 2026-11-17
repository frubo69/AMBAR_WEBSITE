"""Прицел сканера: читается только окно рамки (владелец, 17 ноя 2026: «сделать
как прицел, чтобы на приёмке и ревизии камеры не цеплялись другие бутылки…
поле прямо по центру экрана, чтобы я конкретно это поле наводил на QR-код, и
только оно сканировало… сканируют водку, а вместе с водкой цепляют вино, и
вино заносится как водка»).

Было: сканер читал квадрат в 82 % кадра, а рамка на экране меньше (в STAR —
64 % ширины экрана, у водителя уголки стояли выше и уже). Код рядом с рамкой
читался. Наклейка сама не знает, на какой она бутылке: код записывается на
позицию, выбранную на экране.

Проверяем не глазами, а исполнением: из обеих страниц вынимается настоящий
SCAN (его _aimZone и _loop), настоящий jsQR.js читает кадр 1080×1920, на
котором нарисованы настоящие QR-коды. Вместо <video> и <canvas> — заглушки:
вырезают из кадра ровно тот кусок, который попросил сканер.

  A. Код в окне прицела — читается.
  B. Код рядом с окном (в старой зоне 82 %) — с прицелом не читается, без
     прицела читается: это и была беда.
  C. В кадре оба кода — записывается только тот, что в окне.
  D. Код наполовину в окне — не читается.
  E. Вырезается ровно окно: числа посчитаны руками, а не той же формулой.
  F. Окно в другом месте и другого размера — читается оно: зона идёт за
     рамкой на экране, а не за числами в коде.
  G. Экран едет и сжат переходом, рамка «клюёт» — окно в кадре то же.
  H. Прицел есть, а рамку не измерить — кадр пропускается, шире не читаем.
     Если так секунду подряд (рамки на экране нет вовсе) — камера читает
     середину кадра, как раньше: совсем не читать хуже.
  I. Камера без класса aim читает как раньше: тот же кусок кадра и те же
     размеры (таких камер не осталось — это запас на новый экран).
  J. Прицел стоит на каждой камере сканера в обоих приложениях. Сначала
     просили приёмку и ревизию; в тот же день на «Поставить прицел и на
     остальные камеры?» — «Да, поставь». Новая камера без прицела роняет J.

    python3 tools/test_scan_aim.py
"""
import json, os, re, subprocess, sys, tempfile
import segno

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []
W, H = 1080, 1920                      # кадр камеры: просим 1920×1080, телефон отдаёт стоя


def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


def кадр(коды):
    """Серый кадр W×H: светлая стена, тёмные полки, на нём наклейки с кодами.
    коды: [(текст, cx, cy, точек_на_модуль, тихая_зона_в_модулях)], место — в
    точках кадра. Возвращает байты и прямоугольники наклеек."""
    buf = bytearray([200]) * (W * H)
    for y0 in range(60, H, 240):
        for y in range(y0, min(H, y0 + 14)):
            buf[y * W:(y + 1) * W] = bytes([90]) * W
    места = {}
    for текст, cx, cy, mod, тихая in коды:
        m = segno.make(текст, error="m", micro=False).matrix
        n = len(m); side = (n + 2 * тихая) * mod
        x0, y0 = round(cx - side / 2), round(cy - side / 2)
        assert x0 >= 0 and y0 >= 0 and x0 + side <= W and y0 + side <= H, текст
        for y in range(y0, y0 + side):
            buf[y * W + x0:y * W + x0 + side] = bytes([245]) * side
        for r in range(n):
            for c in range(n):
                if m[r][c]:
                    xx = x0 + (тихая + c) * mod
                    for y in range(y0 + (тихая + r) * mod, y0 + (тихая + r + 1) * mod):
                        buf[y * W + xx:y * W + xx + mod] = bytes([20]) * mod
        места[текст] = (x0, y0, x0 + side, y0 + side)
    return bytes(buf), места


def в_кадр(x, y, box):
    """Точка экрана → точка кадра. box = (лево, верх, ширина, высота) видео на
    экране; видео растянуто до краёв и обрезано (object-fit: cover)."""
    scale = max(box[2] / W, box[3] / H)
    return ((x - box[0] + (W * scale - box[2]) / 2) / scale,
            (y - box[1] + (H * scale - box[3]) / 2) / scale)


УЗЕЛ = r"""
const fs = require('fs');
const [,, jsqrPath, pagePath, casesPath] = process.argv;
const jsQR = require(jsqrPath);
const src = fs.readFileSync(pagePath, 'utf8');
const m = src.match(/const SCAN = \{[\s\S]*?\n\};/);
if(!m){ console.log(JSON.stringify({error: 'no SCAN'})); process.exit(0); }
const SCAN = new Function('jsQR', 'requestAnimationFrame', 'cancelAnimationFrame',
  'navigator', 'document', 'SFX', 'AmbarTorch', m[0] + '\nreturn SCAN;')(
  jsQR, () => 0, () => {}, {}, {}, {wake(){}}, {screen(){}, has(){ return false; }});
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = [];
for(const c of cases){
  const gray = fs.readFileSync(c.frame);
  const asked = [];
  const fr = c.fr;       // рамка на экране: середина, размер по вёрстке, размер на экране
  const f = fr && {offsetWidth: fr.ow, offsetHeight: fr.oh,
    getBoundingClientRect: () => ({left: fr.cx - fr.rw / 2, top: fr.cy - fr.rh / 2, width: fr.rw, height: fr.rh})};
  const box = {classList: {contains: k => !!c.aim && k === 'aim'},
               querySelector: s => { asked.push(s); return f || null; }};
  const b = c.box;       // видео: место на экране и размер по вёрстке
  const v = {videoWidth: c.W, videoHeight: c.H, readyState: 4, HAVE_ENOUGH_DATA: 4, isConnected: true,
    clientWidth: b.ow, clientHeight: b.oh, offsetWidth: b.ow, offsetHeight: b.oh,
    getBoundingClientRect: () => ({left: b.x, top: b.y, width: b.rw, height: b.rh}),
    parentElement: box};
  const calls = [];
  const px = (x, y) => (x < 0 || y < 0 || x >= c.W || y >= c.H) ? 0 : gray[y * c.W + x];
  const ctx = {buf: null,
    drawImage(_v, sx, sy, sw, sh, dx, dy, dw, dh){
      const o = new Uint8ClampedArray(dw * dh * 4);
      for(let j = 0; j < dh; j++){
        const y = sy + (j + .5) * sh / dh - .5, y0 = Math.floor(y), fy = y - y0;
        for(let i = 0; i < dw; i++){
          const x = sx + (i + .5) * sw / dw - .5, x0 = Math.floor(x), fx = x - x0;
          const g = px(x0, y0) * (1 - fx) * (1 - fy) + px(x0 + 1, y0) * fx * (1 - fy)
                  + px(x0, y0 + 1) * (1 - fx) * fy + px(x0 + 1, y0 + 1) * fx * fy;
          const k = (j * dw + i) * 4; o[k] = o[k + 1] = o[k + 2] = g; o[k + 3] = 255;
        }
      }
      this.buf = o; calls.push({sx, sy, sw, sh, dw, dh});
    },
    getImageData(){ return {data: this.buf}; }};
  const got = [];
  Object.assign(SCAN, {stream: {getTracks: () => []}, video: v, cv: {width: 0, height: 0}, ctx,
    seen: new Set(), seenAt: new Map(), lastCode: null, missed: true, frames: 0, t0: Date.now(),
    lastHit: Date.now(), lastDecode: -1e9, alt: false, blind: 0,
    onCode: code => got.push(code), onIdle: null, onRepeat: null, onHold: null, onLost: null});
  for(let n = 0; n < (c.frames || 6); n++) SCAN._loop(1000 + n * 200);
  const r2 = x => Math.round(x * 100) / 100;
  out.push({name: c.name, got, asked: [...new Set(asked)],
    crops: [...new Set(calls.map(k => [r2(k.sx), r2(k.sy), r2(k.sw), r2(k.sh)].join(' ')))],
    sizes: [...new Set(calls.map(k => k.dw + 'x' + k.dh))].sort(), decodes: calls.length});
}
console.log(JSON.stringify(out));
"""


def прогон(страница, случаи, папка):
    """Запускает настоящий SCAN страницы узлом. случаи: [{name, frame(bytes), …}]."""
    for i, c in enumerate(случаи):
        путь = os.path.join(папка, f"{os.path.basename(os.path.dirname(страница))}_{i}.gray")
        open(путь, "wb").write(c["frame"]); c["frame"] = путь
        c["W"], c["H"] = W, H
    cj = os.path.join(папка, "cases.json"); open(cj, "w", encoding="utf-8").write(json.dumps(случаи))
    js = os.path.join(папка, "run.js"); open(js, "w", encoding="utf-8").write(УЗЕЛ)
    r = subprocess.run(["node", js, os.path.join(os.path.dirname(страница), "jsQR.js"), страница, cj],
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        eq(f"узел выполнил сканер {страница}", (r.returncode, r.stderr.strip()[:300]), (0, ""))
        return {}
    v = json.loads(r.stdout)
    if isinstance(v, dict):
        eq(f"в {страница} найден SCAN", v, [])
        return {}
    return {x["name"]: x for x in v}


def батарея(имя, страница, селектор, box, fr, окно_руками, старая_руками, папка):
    """Один и тот же набор для страницы. box = (лево, верх, ширина, высота)
    видео на экране; fr = (середина x, середина y, сторона) рамки на экране."""
    print(f"── {имя} ─────────────────────────────────────────────")
    bx = {"x": box[0], "y": box[1], "ow": box[2], "oh": box[3], "rw": box[2], "rh": box[3]}
    f0 = {"cx": fr[0], "cy": fr[1], "ow": fr[2], "oh": fr[2], "rw": fr[2], "rh": fr[2]}
    ц = в_кадр(fr[0], fr[1], box)                       # середина окна в кадре
    scale = max(box[2] / W, box[3] / H)
    пол = fr[2] / scale / 2                             # пол-окна в точках кадра
    # сосед: под окном, вплотную, но целиком вне его
    сосед_mod, сосед_тихая = (5, 4) if имя == "STAR" else (4, 2)
    сосед_side = (21 + 2 * сосед_тихая) * сосед_mod
    сосед = (ц[0], ц[1] + пол + 12 + сосед_side / 2)
    # на краю: середина наклейки ровно на правом крае окна
    край = (ц[0] + пол, ц[1])

    к_цель, м_цель = кадр([("VODKA-1", ц[0], ц[1], 8, 4)])
    к_сосед, м_сосед = кадр([("WINE-1", сосед[0], сосед[1], сосед_mod, сосед_тихая)])
    к_оба, _ = кадр([("VODKA-1", ц[0], ц[1], 8, 4), ("WINE-1", сосед[0], сосед[1], сосед_mod, сосед_тихая)])
    к_край, _ = кадр([("EDGE-1", край[0], край[1], 6, 4)])
    # другое окно: меньше и в левом верхнем углу видимой части
    fr2 = (box[0] + box[2] * 0.30, box[1] + box[3] * 0.26, round(fr[2] * 0.7))
    ц2 = в_кадр(fr2[0], fr2[1], box)
    f2 = {"cx": fr2[0], "cy": fr2[1], "ow": fr2[2], "oh": fr2[2], "rw": fr2[2], "rh": fr2[2]}
    к_там, _ = кадр([("THERE-1", ц2[0], ц2[1], 6, 4)])
    # экран сжат и сдвинут переходом (0,9), рамка «клюёт» (1,055): вёрстка та же
    k = 0.9
    bx_t = {"x": box[0] * k + 21, "y": box[1] * k + 40, "ow": box[2], "oh": box[3], "rw": box[2] * k, "rh": box[3] * k}
    f_t = {"cx": bx_t["x"] + (fr[0] - box[0]) * k, "cy": bx_t["y"] + (fr[1] - box[1]) * k,
           "ow": fr[2], "oh": fr[2], "rw": fr[2] * k * 1.055, "rh": fr[2] * k * 1.055}

    r = прогон(страница, [
        dict(name="цель", frame=к_цель, aim=True, box=bx, fr=f0),
        dict(name="сосед", frame=к_сосед, aim=True, box=bx, fr=f0),
        dict(name="сосед без прицела", frame=к_сосед, aim=False, box=bx, fr=f0),
        dict(name="оба", frame=к_оба, aim=True, box=bx, fr=f0, frames=12),
        dict(name="край", frame=к_край, aim=True, box=bx, fr=f0),
        dict(name="край без прицела", frame=к_край, aim=False, box=bx, fr=f0),
        dict(name="окно в другом месте", frame=к_там, aim=True, box=bx, fr=f2),
        dict(name="код на старом месте окна", frame=к_цель, aim=True, box=bx, fr=f2),
        dict(name="экран едет", frame=к_цель, aim=True, box=bx_t, fr=f_t),
        dict(name="рамку не измерить", frame=к_цель, aim=True, box=bx, fr=dict(f0, ow=0, oh=0)),
        dict(name="рамки нет", frame=к_цель, aim=True, box=bx, fr=None),
        dict(name="рамки нет секунду", frame=к_цель, aim=True, box=bx, fr=None, frames=20),
        dict(name="цель без прицела", frame=к_цель, aim=False, box=bx, fr=f0),
    ], папка)
    if not r: return
    g = lambda n: r[n]

    eq("A. код в окне читается", g("цель")["got"], ["VODKA-1"])
    eq("A. сканер спросил свою рамку", g("цель")["asked"], [селектор])
    # сосед действительно стоял в старой зоне и вне окна — иначе проверка B пустая
    sx, sy, sw, sh = старая_руками
    x0, y0, x1, y1 = м_сосед["WINE-1"]
    eq("B. сосед целиком в старой зоне чтения", (x0 >= sx, y0 >= sy, x1 <= sx + sw, y1 <= sy + sh), (True,) * 4)
    eq("B. сосед целиком под окном", y0 > окно_руками[1] + окно_руками[3], True)
    eq("B. код рядом с окном не читается", g("сосед")["got"], [])
    eq("B. …а без прицела читался", g("сосед без прицела")["got"], ["WINE-1"])
    eq("C. оба в кадре — записан только тот, что в окне", g("оба")["got"], ["VODKA-1"])
    eq("D. код наполовину в окне не читается", g("край")["got"], [])
    eq("D. …а без прицела читался", g("край без прицела")["got"], ["EDGE-1"])
    eq("E. вырезается ровно окно (числа руками)", g("цель")["crops"],
       [" ".join(str(x) for x in окно_руками)])
    eq("E. в распознавание идёт 512 и 256 по очереди", g("цель")["sizes"], ["256x256", "512x512"])
    eq("F. окно в другом месте — читается оно", g("окно в другом месте")["got"], ["THERE-1"])
    eq("F. код там, где окно стояло раньше, не читается", g("код на старом месте окна")["got"], [])
    eq("G. экран едет, рамка клюёт — окно то же", g("экран едет")["crops"], g("цель")["crops"])
    eq("G. …и код в нём читается", g("экран едет")["got"], ["VODKA-1"])
    eq("H. рамку не измерить — кадр пропущен", (g("рамку не измерить")["decodes"], g("рамку не измерить")["got"]), (0, []))
    eq("H. рамки нет — кадр пропущен", (g("рамки нет")["decodes"], g("рамки нет")["got"]), (0, []))
    h = g("рамки нет секунду")      # 20 кадров: 11 пропущены, дальше — прежнее чтение
    eq("H. рамки нет секунду — читаем середину кадра, как раньше", (h["decodes"], h["crops"], h["got"]),
       (9, [" ".join(str(x) for x in старая_руками)], ["VODKA-1"]))
    eq("I. без класса aim — прежний кусок кадра", g("цель без прицела")["crops"],
       [" ".join(str(x) for x in старая_руками)])
    eq("I. без класса aim — прежние размеры", g("цель без прицела")["sizes"], ["256x256", "512x512"])
    eq("I. без класса aim рамку не спрашивают", g("цель без прицела")["asked"], [])
    eq("I. без класса aim код в середине читается", g("цель без прицела")["got"], ["VODKA-1"])


def разметка():
    """J. Прицел — на каждой камере сканера. Камеру узнаём по её <video>:
    он стоит первой строкой внутри коробки."""
    print("── где стоит прицел ─────────────────────────────────────────")
    def камеры(путь, чем):
        src = open(os.path.join(ROOT, путь), encoding="utf-8").read().split("\n")
        есть, нет = [], []
        for i, строка in enumerate(src):
            if not re.search(чем, строка): continue
            m = re.search(r'<video id="([A-Za-z0-9_]+)"', src[i + 1])
            кто = m.group(1) if m else f"строка {i + 1}"
            (есть if re.search(r'class="[^"]*\baim\b', строка) else нет).append(кто)
        return sorted(есть), sorted(нет)
    есть, нет = камеры("owner/index.html", r'<div class="scan-cam[ "]')
    eq("STAR: прицел на всех девяти камерах", есть,
       ["chkVideo", "gdVideo", "mvcVideo", "mvtVideo", "qrVideo", "qrVideo", "qrVideo", "qrVideo", "rcvVideo"])
    eq("STAR: камер без прицела нет", нет, [])
    есть, нет = камеры("driver/index.html", r'<div class="cam[ "$]')
    eq("водитель: прицел на всех шести камерах", есть, ["audVid", "botV", "camV", "ckV", "mvV", "woV"])
    eq("водитель: камер без прицела нет", нет, [])
    # у водителя камера перемещения и проверки — во весь свободный экран: её окно
    # квадратное и того же размера, что в STAR (прежняя рамка была высотой через поля)
    src = open(os.path.join(ROOT, "driver", "index.html"), encoding="utf-8").read()
    eq("водитель: окно полноэкранной камеры — квадрат, как в STAR",
       ".cam.mv-cam.aim .cam-frame{top:50%;width:min(64%,264px);height:auto;padding-bottom:0}" in src, True)
    # окно рисует сама рамка — значит, затемнение и зона чтения одно и то же место
    for путь, правило in (("owner/index.html", ".scan-cam.aim .scan-frame::before{"),
                          ("driver/index.html", ".cam.aim .cam-frame::before{")):
        src = open(os.path.join(ROOT, путь), encoding="utf-8").read()
        i = src.find(правило)
        кусок = src[i:i + 260] if i >= 0 else ""
        eq(f"{путь}: затемнение рисует рамка, по своему краю",
           ("inset:0" in кусок, "border-radius:inherit" in кусок, "200vmax" in кусок), (True, True, True))
    # у водителя ответ на скан живёт плашкой в середине рамки; пустая, она стояла
    # тёмной пилюлей ровно там, куда наводят код
    src = open(os.path.join(ROOT, "driver", "index.html"), encoding="utf-8").read()
    eq("водитель: пустой плашки в середине окна нет", ".cam.aim .cam-badge:empty{display:none}" in src, True)


def main():
    with tempfile.TemporaryDirectory() as папка:
        # STAR: камера во весь экран 430×800, рамка 264 на 46 % высоты.
        # Руками: scale = 800/1920 = 1/2,4; окно 264·2,4 = 633,6; середина
        # (215+10)·2,4 = 540 и 368·2,4 = 883,2 → угол 223,2 / 566,4.
        # Старая зона: 82 % от видимых 1032 = 846,24 → угол 116,88 / 536,88.
        батарея("STAR", os.path.join(ROOT, "owner", "index.html"), ".scan-frame",
                (0, 0, 430, 800), (215, 0.46 * 800, 264),
                (223.2, 566.4, 633.6, 633.6), (116.88, 536.88, 846.24, 846.24), папка)
        # Водитель: квадрат камеры 404×404 (стоит не в углу экрана), окно 64 %
        # на 48 % высоты. Руками: видно 1080×1080 кадра; окно 0,64·1080 = 691,2;
        # середина 540 и 420 + 0,48·1080 = 938,4 → угол 194,4 / 592,8.
        # Старая зона: 82 % от 1080 = 885,6 → угол 97,2 / 517,2.
        батарея("водитель", os.path.join(ROOT, "driver", "index.html"), ".cam-frame",
                (13, 120, 404, 404), (13 + 202, 120 + 0.48 * 404, 0.64 * 404),
                (194.4, 592.8, 691.2, 691.2), (97.2, 517.2, 885.6, 885.6), папка)
    разметка()
    print("\nИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    return 1 if FAIL else 0


sys.exit(main())
