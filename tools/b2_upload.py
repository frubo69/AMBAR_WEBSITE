#!/usr/bin/env python3
"""Копия базы наружу: Backblaze B2, шифрованная и защищённая от удаления.

Зачем ещё одна копия, если есть файлы на сервере и у владельца
--------------------------------------------------------------
Файлы на сервере умирают вместе с сервером, а копия у владельца зависит от
того, включён ли его ноутбук. Здесь третье место, независимое от обоих, и —
главное — с включённой блокировкой: файл нельзя удалить или перезаписать до
конца срока даже теми ключами, которыми он загружен. Взлом сервера, ошибка
администратора, злой умысел — ничего из этого не достаёт до этих копий.

Данные шифруются до отправки: провайдер хранит непрозрачный шифротекст.
Пароль лежит в `/opt/ambar/.b2-pass` и вторым экземпляром — у владельца, в
папке с копиями. Потеряв оба, восстановить будет нельзя, поэтому их два.

    python3 tools/b2_upload.py            # отправить самую свежую копию
    python3 tools/b2_upload.py --list     # что уже лежит в хранилище
    python3 tools/b2_upload.py --file X   # отправить конкретный файл
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = "/opt/ambar"
BACKUPS = os.path.join(ROOT, "backups")
PASS_FILE = os.path.join(ROOT, ".b2-pass")
CONF = os.path.join(ROOT, ".b2.env")
API = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


def conf(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    for path in (CONF, os.path.join(ROOT, ".env")):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    return default


def _api(url: str, token: str = "", data: dict = None, basic: str = ""):
    headers = {"Authorization": basic or token}
    body = json.dumps(data).encode() if data is not None else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=60))


def auth() -> dict:
    kid, key = conf("B2_KEY_ID"), conf("B2_APP_KEY")
    if not (kid and key):
        sys.exit("нет ключей B2 — проверьте /opt/ambar/.b2.env")
    basic = "Basic " + base64.b64encode(f"{kid}:{key}".encode()).decode()
    r = _api(API, basic=basic)
    sa = r["apiInfo"]["storageApi"]
    return {"token": r["authorizationToken"], "api": sa["apiUrl"],
            "bucket_id": sa.get("bucketId"), "bucket": sa.get("bucketName"),
            "account": r["accountId"]}


def passphrase() -> str:
    """Пароль шифрования. Создаём один раз и больше не меняем: старые копии
    иначе перестанут открываться."""
    if os.path.exists(PASS_FILE):
        return open(PASS_FILE).read().strip()
    import secrets
    p = secrets.token_urlsafe(32)
    with open(PASS_FILE, "w") as f:
        f.write(p + "\n")
    os.chmod(PASS_FILE, 0o600)
    print("создан новый пароль шифрования:", PASS_FILE)
    return p


def encrypt(src: str) -> str:
    """Шифруем до отправки: у провайдера должен лежать шифротекст, а не наши
    заказы и телефоны клиентов."""
    dst = os.path.join(tempfile.gettempdir(), os.path.basename(src) + ".enc")
    with open(PASS_FILE) as _:
        pass
    r = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
         "-in", src, "-out", dst, "-pass", f"file:{PASS_FILE}"],
        capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"шифрование не удалось: {r.stderr.strip()[:200]}")
    return dst


def upload(path: str, a: dict) -> dict:
    up = _api(f"{a['api']}/b2api/v3/b2_get_upload_url", a["token"],
              {"bucketId": a["bucket_id"]})
    data = open(path, "rb").read()
    sha = hashlib.sha1(data).hexdigest()
    days = int(conf("B2_RETENTION_DAYS", "30") or 30)
    until = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)
    name = "db/" + os.path.basename(path)
    req = urllib.request.Request(up["uploadUrl"], data=data, headers={
        "Authorization": up["authorizationToken"],
        "X-Bz-File-Name": urllib.request.quote(name),
        "Content-Type": "application/octet-stream",
        "X-Bz-Content-Sha1": sha,
        # Блокировка: до этой даты файл нельзя удалить и нельзя переписать
        # никакими ключами. Ради этого всё и затевалось.
        "X-Bz-File-Retention-Mode": "compliance",
        "X-Bz-File-Retention-Retain-Until-Timestamp": str(until),
    })
    r = json.load(urllib.request.urlopen(req, timeout=300))
    r["_retain_until"] = datetime.fromtimestamp(until / 1000, timezone.utc)
    return r


def do_list(a: dict) -> None:
    r = _api(f"{a['api']}/b2api/v3/b2_list_file_names", a["token"],
             {"bucketId": a["bucket_id"], "maxFileCount": 100, "prefix": "db/"})
    total = 0
    for f in r.get("files", []):
        ts = datetime.fromtimestamp(f["uploadTimestamp"] / 1000, timezone.utc)
        ret = (f.get("fileRetention", {}).get("value", {}) or {})
        until = ret.get("retainUntilTimestamp")
        until_s = datetime.fromtimestamp(until / 1000, timezone.utc).strftime("%d.%m.%Y") if until else "—"
        total += f["contentLength"]
        print(f"  {f['fileName']:44} {f['contentLength']/1024:7.0f} КБ  "
              f"загружен {ts.strftime('%d.%m %H:%M')}  защищён до {until_s}")
    print(f"\nвсего файлов: {len(r.get('files', []))} · {total/1024/1024:.1f} МБ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--file")
    a = auth()
    args = ap.parse_args()
    print(f"хранилище: {a['bucket']}")
    if args.list:
        do_list(a)
        return
    src = args.file or (sorted(glob.glob(os.path.join(BACKUPS, "ambar-*.jsonl.gz")))[-1]
                        if glob.glob(os.path.join(BACKUPS, "ambar-*.jsonl.gz")) else "")
    if not src:
        sys.exit("нечего отправлять: копий нет")
    passphrase()
    enc = encrypt(src)
    try:
        r = upload(enc, a)
        print(f"отправлено: {r['fileName']} · {r['contentLength']/1024:.0f} КБ · "
              f"нельзя удалить до {r['_retain_until'].strftime('%d.%m.%Y')}")
        # Отметка для сторожа: ходить в B2 каждые десять минут ради проверки —
        # лишний трафик и лишние ключи в лишнем месте, а дата файла говорит
        # ровно то же самое.
        try:
            open(os.path.join(ROOT, ".b2-last"), "w").write(
                datetime.now(timezone.utc).isoformat())
        except OSError:
            pass
    finally:
        try:
            os.unlink(enc)
        except OSError:
            pass


if __name__ == "__main__":
    main()
