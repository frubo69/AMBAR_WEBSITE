"""Снимки и проверки стенда: python3 shot.py view[?params] ... → view.png + строки ERROR/MEASURE/OVERLAP/TOAST."""
import subprocess, sys, re, os, html
CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE = "http://127.0.0.1:8773/stand.html"
for spec in sys.argv[1:]:
    view, _, extra = spec.partition("?")
    url = f"{BASE}?view={view}" + (("&" + extra) if extra else "")
    name = re.sub(r"[^\w]+", "_", spec.lower())
    dom = subprocess.run(["perl", "-e", "alarm 60; exec @ARGV", CH, "--headless=new", "--dump-dom", "--virtual-time-budget=4000",
                          "--window-size=390,900", "--hide-scrollbars", url], capture_output=True, text=True).stdout
    m = re.search(r'<div id="err">(.*?)</div>', dom, re.S)
    err = html.unescape(m.group(1)) if m else "(no err div)"
    lines = [l for l in err.split("\n") if l.strip()]
    bad = [l for l in lines if l.startswith(("ERROR", "REJECT", "SCENARIO", "TOAST"))]
    meas = [l for l in lines if l.startswith(("MEASURE", "OVERLAP"))]
    h = re.search(r'MEASURE phone=(\d+) scrollW=(\d+)', err)
    ph = re.search(r'<div id="phone"[^>]*>', dom)
    # высота страницы — из числа строк не узнать; снимем с запасом и обрежем по контенту
    H = int(os.environ.get("H", "2400"))
    subprocess.run(["perl", "-e", "alarm 60; exec @ARGV", CH, "--headless=new", f"--screenshot={name}_raw.png", "--virtual-time-budget=4000",
                    f"--window-size=390,{H}", "--hide-scrollbars", url], capture_output=True)
    try:
        from PIL import Image, ImageChops
        im = Image.open(f"{name}_raw.png").convert("RGB")
        bg = Image.new("RGB", im.size, im.getpixel((5, im.size[1] - 5)))
        bbox = ImageChops.difference(im, bg).getbbox()
        if bbox: im = im.crop((0, 0, im.size[0], min(im.size[1], bbox[3] + 24)))
        im.save(f"{name}.png"); os.remove(f"{name}_raw.png")
        size = im.size
    except Exception as e:
        size = ("PIL?", e)
    print(f"== {spec}: {size}")
    for l in bad: print("   ", l[:300])
    for l in meas: print("   ", l[:160])
