"""Сканер не исчезает посреди скана (владелец, 20 сен 2026: «посреди скана
приложение вылетает»).

Что было: раздел «Перемещения» обновляет список сам — раз в пять секунд, пока
он открыт, и раз в полминуты фоном. Обновление переписывает accMid целиком, а
камера проверки живёт внутри него. Сторож стоял только на старом сканере
(`!MVT`), про новый (`MVC`, проверка отложенного) не знал — и первое же
обновление сносило <video> посреди скана. Хуже того, поток оставался жить в
оторванном узле: камера снимала, распознавание крутилось, и через минуту
телефон убивал вкладку — приложение перезагружалось само.

Проверяем инварианты глазами исходника (браузера в наборе тестов нет):
  • список не рисуется, пока открыт любой сканер раздела;
  • за свежими данными в это время не ходим;
  • оторванную камеру сканер гасит сам, чем бы её ни оторвали.

Запуск: python3 tools/test_scan_guard.py
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "owner", "index.html"), encoding="utf-8").read()

FAIL = []


def ok(name, cond, got=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f" — {got}"))
    if not cond:
        FAIL.append(name)


def тело(имя: str, длина: int = 1400) -> str:
    """Кусок исходника от объявления функции — читаем как текст, без разбора JS."""
    m = re.search(r"(?:async )?function " + re.escape(имя) + r"\s*\(", SRC)
    return SRC[m.start():m.start() + длина] if m else ""


print("— список не рисуется поверх камеры")
ok("есть один признак «раздел занят сканером»", "const mvoСкан = () =>" in SRC)
признак = re.search(r"const mvoСкан = \(\) => (.+)", SRC)
знает = признак.group(1) if признак else ""
ok("признак знает оба сканера перемещений", "MVT" in знает and "MVC" in знает, знает)
ok("mvoPaint отказывается рисовать при живом сканере",
   re.search(r"function mvoPaint\(\)\{\s*\n\s*if\(mvoСкан\(\)\) return;", SRC) is not None,
   "первой строкой mvoPaint должен стоять отказ")
ok("mvoLoad не ходит за данными во время скана", "if(HIDE || mvoСкан()) return;" in тело("mvoLoad", 200))
ok("mvoPlan тоже", "if(mvoСкан()) return;" in тело("mvoPlan", 200))
ok("фоновый опрос раз в полминуты молчит при камере",
   "!document.hidden && !mvoСкан()) mvoLoad(); }, 30000)" in SRC)
ok("старой проверки «только MVT» не осталось",
   "ACC_VIEW === 'moves' && !MVT" not in SRC)

print("— оторванная камера гаснет сама")
цикл = тело("_loop", 700) or SRC[SRC.index("  _loop(ts){"):SRC.index("  _loop(ts){") + 700]
ok("сканер замечает, что video выкинули со страницы", "!this.video.isConnected" in цикл)
ok("и останавливает камеру", re.search(r"isConnected\)\{\s*\n\s*this\.stop\(\);", цикл) is not None)
ok("и сообщает экрану", "this.onLost" in цикл and "onLost:null" in SRC)
ok("чужой onLost не переживает запуск", "this.onHold = onHold; this.onLost = null;" in SRC)

print("— экраны перемещений подписаны на потерю камеры")
ok("проверка отложенного", "SCAN.onLost = () => { if(MVC) mvcClose(); };" in SRC)
ok("передача и приём", "SCAN.onLost = () => { if(MVT) mvtClose(); };" in SRC)

print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
sys.exit(1 if FAIL else 0)
