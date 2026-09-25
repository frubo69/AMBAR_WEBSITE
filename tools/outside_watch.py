#!/usr/bin/env python3
"""Сторож СНАРУЖИ: смотрит на сервер с чужой машины и пишет владельцу в бот.

Зачем отдельный. Наш `ambar-watchdog` крутится на самом сервере, и 25 сен 2026,
когда у машины на 52 минуты пропала сеть, он бодро доложил «всё в порядке»:
проверить себя снаружи он не может, отправить тревогу — тем более. О простое
владелец узнал от водителей.

Чем проверяем — и почему не домом. Домен ambar-delivery.com на части сетей
режется по SNI (соединение рвут на рукопожатии TLS), поэтому «домен не
открылся» ничего не доказывает. Стучимся по адресу:

  • баннер SSH на 22-м порту — чистый текст, ни домена, ни TLS. Пришёл
    «SSH-2.0-…» — значит у машины есть сеть и живёт хотя бы один процесс.
    В провал 25 сен порт соединение принимал, а баннер не приходил — ровно
    эта проверка и поймала бы;
  • HTTP по адресу с заголовком Host — отвечает ли nginx. Любой код (хоть
    403) годится: важно, что ответ пришёл.

Раздельно, а не «или»: если молчат обе — у машины нет сети; если только
HTTP — сервер жив, а сайт лёг, и это разные разговоры.

Тревога — со второй подряд неудачи (одиночный чих сети не будит), напоминание
раз в полчаса, пока лежит, и сообщение о возврате с длительностью.

Настройки — ВНЕ репозитория, он публичный: ~/.ambar_watch.json, права 600
  {"ip": "…", "host": "…", "token": "…", "chat_ids": [1, 2]}

Запуск: python3 tools/outside_watch.py         (одна проверка)
        python3 tools/outside_watch.py --test  (проверка + сообщение в бот)
Раз в минуту — через launchd, см. tools/outside_watch.plist
"""
import json, os, socket, subprocess, sys, time, urllib.request

НАСТРОЙКИ = os.path.expanduser("~/.ambar_watch.json")
СОСТОЯНИЕ = os.path.expanduser("~/.ambar_watch.state")
ЖУРНАЛ = os.path.expanduser("~/Library/Logs/ambar_watch.log")
ПОВТОР = 30 * 60          # пока лежит — напоминать раз в полчаса
ПОРОГ = 2                 # тревога со второй подряд неудачи


def лог(строка):
    try:
        with open(ЖУРНАЛ, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + строка + "\n")
    except OSError:
        pass


def настройки():
    with open(НАСТРОЙКИ, encoding="utf-8") as f:
        return json.load(f)


def ssh_жив(ip, секунд=6):
    """Баннер SSH. Порт может принять соединение и молчать — это и есть беда."""
    try:
        with socket.create_connection((ip, 22), timeout=секунд) as s:
            s.settimeout(секунд)
            return s.recv(64).startswith(b"SSH-")
    except OSError:
        return False


def http_жив(ip, host, секунд=8):
    """Отвечает ли nginx. Любой код — ответ; важно, что он есть."""
    req = urllib.request.Request(f"http://{ip}/", headers={"Host": host},
                                 method="GET")
    try:
        with urllib.request.urlopen(req, timeout=секунд) as r:
            return True, r.status
    except urllib.error.HTTPError as e:
        return True, e.code                      # 403 от nginx — тоже ответ
    except Exception:
        return False, 0


def написать(token, ids, текст):
    """Отправка через curl, а не через питон.

    На Маке владельца HTTPS идёт через перехватывающий прокси со своим
    корневым сертификатом: curl доверяет ему из системной связки ключей, а
    питон со своим набором — нет и падает на проверке. Сторож, который не
    может отправить тревогу, бесполезен, поэтому берём то, что работает.
    """
    беда = None
    for chat in ids:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "20", "-o", "/dev/null", "-w", "%{http_code}",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "--data-urlencode", f"chat_id={chat}",
             "--data-urlencode", f"text={текст}",
             "--data-urlencode", "disable_web_page_preview=true"],
            capture_output=True, text=True)
        if r.returncode or r.stdout.strip() != "200":
            беда = f"код {r.stdout.strip() or '—'} {r.stderr.strip()[:120]}"
    if беда:
        лог(f"не смог написать в бот: {беда}")
    return беда is None


def прошлое():
    try:
        with open(СОСТОЯНИЕ, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"плохо": 0, "лежит_с": 0, "сказано": 0}


def сохранить(s):
    try:
        with open(СОСТОЯНИЕ, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except OSError as e:
        лог(f"не смог сохранить состояние: {e}")


def сколько(секунд):
    м = int(секунд // 60)
    if м < 60:
        return f"{м} мин"
    return f"{м // 60} ч {м % 60:02d} мин"


def main():
    c = настройки()
    ip, host = c["ip"], c["host"]
    ssh = ssh_жив(ip)
    http, код = http_жив(ip, host)
    s = прошлое()
    сейчас = int(time.time())

    if ssh and http:
        # всё хорошо: если лежал — сказать, что встал
        if s.get("лежит_с"):
            написать(c["token"], c["chat_ids"],
                     f"Сервер снова отвечает. Лежал {сколько(сейчас - s['лежит_с'])}.")
            лог(f"поднялся, лежал {сколько(сейчас - s['лежит_с'])}")
        сохранить({"плохо": 0, "лежит_с": 0, "сказано": 0})
        лог(f"в порядке (http {код})")
        return 0

    плохо = s.get("плохо", 0) + 1
    беда = ("не отвечает совсем: ни SSH, ни сайт — похоже, у машины пропала сеть"
            if not ssh and not http else
            "сайт не отвечает, а сервер живой — лёг nginx или приложение"
            if ssh else
            "сайт отвечает, а SSH молчит")
    лог(f"плохо ({плохо}): ssh={ssh} http={http} код={код}")

    if плохо < ПОРОГ:                                  # одиночный чих не будим
        s.update({"плохо": плохо}); сохранить(s)
        return 0

    лежит_с = s.get("лежит_с") or сейчас
    сказано = s.get("сказано", 0)
    if not s.get("лежит_с") or сейчас - сказано >= ПОВТОР:
        сколько_же = "" if not s.get("лежит_с") else f" Уже {сколько(сейчас - лежит_с)}."
        написать(c["token"], c["chat_ids"], f"AMBAR: {беда}.{сколько_же}")
        сказано = сейчас
    сохранить({"плохо": плохо, "лежит_с": лежит_с, "сказано": сказано})
    return 1


if __name__ == "__main__":
    if "--test" in sys.argv:
        c = настройки()
        ssh = ssh_жив(c["ip"]); http, код = http_жив(c["ip"], c["host"])
        print(f"SSH-баннер: {'есть' if ssh else 'НЕТ'} · HTTP: {'есть, код ' + str(код) if http else 'НЕТ'}")
        написать(c["token"], c["chat_ids"],
                 "Проверка сторожа снаружи: он на месте и умеет писать сюда. "
                 f"Сейчас — SSH {'есть' if ssh else 'нет'}, сайт {'есть' if http else 'нет'}.")
        print("сообщение отправлено")
        sys.exit(0)
    sys.exit(main())
