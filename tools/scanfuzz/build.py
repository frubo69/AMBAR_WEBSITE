"""Собрать стенды фаззера из НАСТОЯЩИХ приложений: камера и распознаватель
подменены (pre.js), сервер — модель (model.js), в конце — прогонщик
(runner.js). Стенды ложатся в driver/_fz.html и owner/_fz.html; run.py сам
их собирает и сам стирает. python3 tools/scanfuzz/build.py [корень]"""
import os, re, sys
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
pre = open(os.path.join(HERE, "pre.js"), encoding="utf-8").read()
model = open(os.path.join(HERE, "model.js"), encoding="utf-8").read()
runner = open(os.path.join(HERE, "runner.js"), encoding="utf-8").read()
TG = """<script>
window.Telegram = {WebApp: new Proxy({initData: 'x', version: '8.0', platform: 'android', colorScheme: 'dark',
  themeParams: {}, viewportHeight: 800, viewportStableHeight: 800, isExpanded: true,
  HapticFeedback: {impactOccurred(){}, notificationOccurred(){}, selectionChanged(){}},
  BackButton: {show(){}, hide(){}, onClick(){}, offClick(){}}, MainButton: {hide(){}, show(){}, setText(){}, onClick(){}},
  LocationManager: null, ready(){}, expand(){}, onEvent(){}, offEvent(){}, close(){}, showConfirm(t, cb){ cb(true); },
  showAlert(t, cb){ cb && cb(); }, openLink(){}, openTelegramLink(){}, setHeaderColor(){}, setBackgroundColor(){},
  enableClosingConfirmation(){}, disableVerticalSwipes(){}, requestFullscreen(){}, initDataUnsafe: {user: {id: 1, first_name: 'Худоба'}}},
  {get(t, k){ return k in t ? t[k] : (() => {}); }})};
</script>"""
for app in ("driver", "owner"):
    src = open(os.path.join(ROOT, app, "index.html"), encoding="utf-8").read()
    assert src.count("<head>") == 1 and src.count('<script src="api.js"></script>') == 1
    src = src.replace("<head>", "<head>\n<script>" + pre + "</script>", 1)
    src = src.replace('<script src="/vendor/telegram-web-app.js"></script>', TG, 1)
    src = re.sub(r"<script>if\(!window\.Telegram\|\|!window\.Telegram\.WebApp\)\{document\.write\(.*?\);\}</script>", "", src, count=1)
    src = src.replace('<script src="api.js"></script>', "<script>" + model + "</script>", 1)
    i = src.rindex("</body>")
    src = src[:i] + "<script>" + runner + "</script>\n" + src[i:]
    # Имя стенда — своё у каждого запуска: два прогона рядом не стирают
    # стенды друг друга.
    out = os.path.join(ROOT, app, f"_fz{os.getenv('FZ_TAG', '')}.html")
    open(out, "w", encoding="utf-8").write(src)
    print("стенд", out)
