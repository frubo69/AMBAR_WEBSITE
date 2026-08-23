#!/usr/bin/env python3
"""Резервная копия базы AMBAR — своя, независимая от тарифа Atlas.

Зачем своя, если у Atlas есть бэкапы: на бесплатном тарифе их нет вовсе, а на
платном они защищают от падения кластера, но не от нас самих. Удалённый по
ошибке заказ, снесённая коллекция, потерянный доступ к кабинету Atlas — от
этого спасает только копия, которая лежит отдельно и восстанавливается без
чьего-либо разрешения.

Формат — Extended JSON построчно, по документу в строке, всё в один gzip.
Он переживает любые версии драйвера, читается глазами и восстанавливается
этим же файлом. База маленькая (два мегабайта), сжимать её в двоичный архив
незачем.

    python3 tools/backup.py                    # снять копию
    python3 tools/backup.py --list             # что уже есть
    python3 tools/backup.py --restore ФАЙЛ --into ambar_test   # проверить копию
    python3 tools/backup.py --restore ФАЙЛ --into ambar --yes  # вернуть в бой

Хранение: 14 последних ежедневных и 8 воскресных. Место не жмёт — копия
весит килобайты, — но бесконечная свалка мешает найти нужную.
"""
import argparse
import asyncio
import glob
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/ambar")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import json_util                      # noqa: E402
import motor.motor_asyncio                      # noqa: E402

DIR = os.getenv("AMBAR_BACKUP_DIR", "/opt/ambar/backups")
KEEP_HOURS = 48                                 # последние двое суток — все копии
KEEP_DAILY, KEEP_WEEKLY = 14, 8
SKIP = {"qr_locks"}                             # живёт минуты, восстанавливать нечего


def _uri():
    uri = os.getenv("MONGO_URI", "")
    if not uri:                                  # .env читаем сами: скрипт живёт вне сервиса
        env = os.path.join("/opt/ambar", ".env")
        if os.path.exists(env):
            for line in open(env, encoding="utf-8"):
                if line.strip().startswith("MONGO_URI="):
                    uri = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not uri:
        sys.exit("MONGO_URI не найден")
    return uri


def _db_name(uri):
    tail = uri.rsplit("/", 1)[-1].split("?")[0]
    return tail or "ambar"


async def dump():
    os.makedirs(DIR, exist_ok=True)
    uri = _uri()
    cli = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
    db = cli["ambar"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    path = os.path.join(DIR, f"ambar-{stamp}.jsonl.gz")
    names = sorted(await db.list_collection_names())
    total = 0
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"__backup__": {"at": datetime.now(timezone.utc).isoformat(),
                                           "db": "ambar", "collections": names}}) + "\n")
        for name in names:
            if name in SKIP:
                continue
            n = 0
            f.write(json.dumps({"__collection__": name}) + "\n")
            async for doc in db[name].find({}):
                f.write(json_util.dumps(doc) + "\n")
                n += 1
            total += n
            print(f"  {name:<26} {n:>6}")
    size = os.path.getsize(path)
    print(f"\nкопия: {path} · {size/1024:.0f} КБ · {total} документов")
    _rotate()
    return path


def _stamp(path):
    """Дата и время из имени файла: ambar-20260823-0006.jsonl.gz"""
    return datetime.strptime(os.path.basename(path)[6:19], "%Y%m%d-%H%M")


def _rotate():
    """Двое суток целиком, дальше — по копии в день, дальше — по воскресеньям.

    Копии снимаются раз в час, и «последние четырнадцать файлов» — это уже не
    две недели, а полдня: считать надо по времени, а не по количеству. Место
    не жмёт (копия — треть мегабайта), но свалка из тысячи файлов мешает найти
    нужную дату."""
    files = sorted(glob.glob(os.path.join(DIR, "ambar-*.jsonl.gz")))
    if not files:
        return
    now = _stamp(files[-1])
    keep = {p for p in files if now - _stamp(p) <= timedelta(hours=KEEP_HOURS)}
    by_day = {}
    for p in files:                                  # files отсортированы: остаётся
        by_day[_stamp(p).date()] = p                 # последняя копия за день
    for d in sorted(by_day)[-KEEP_DAILY:]:
        keep.add(by_day[d])
    sundays = {d: p for d, p in by_day.items() if d.weekday() == 6}
    for d in sorted(sundays)[-KEEP_WEEKLY:]:
        keep.add(sundays[d])
    for p in files:
        if p not in keep:
            os.remove(p)
            print(f"  убрано старое: {os.path.basename(p)}")


async def restore(path, into, yes):
    if into == "ambar" and not yes:
        sys.exit("Восстановление поверх боевой базы требует --yes")
    cli = motor.motor_asyncio.AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=20000)
    db = cli[into]
    cur, batch, n, coll = None, [], 0, {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            head = json.loads(line) if line.startswith('{"__') else None
            if head and "__backup__" in head:
                print("копия от", head["__backup__"]["at"])
                continue
            if head and "__collection__" in head:
                if cur and batch:
                    await db[cur].insert_many(batch); coll[cur] = coll.get(cur, 0) + len(batch)
                cur, batch = head["__collection__"], []
                await db[cur].delete_many({})
                continue
            batch.append(json_util.loads(line))
            n += 1
            if len(batch) >= 500:
                await db[cur].insert_many(batch); coll[cur] = coll.get(cur, 0) + len(batch); batch = []
    if cur and batch:
        await db[cur].insert_many(batch); coll[cur] = coll.get(cur, 0) + len(batch)
    for k in sorted(coll):
        print(f"  {k:<26} {coll[k]:>6}")
    print(f"\nвосстановлено в базу «{into}»: {n} документов")


def show():
    files = sorted(glob.glob(os.path.join(DIR, "ambar-*.jsonl.gz")))
    if not files:
        print("копий нет"); return
    for p in files:
        st = os.stat(p)
        print(f"  {os.path.basename(p):<28} {st.st_size/1024:>7.0f} КБ  "
              f"{datetime.fromtimestamp(st.st_mtime).strftime('%d.%m %H:%M')}")
    print(f"\nвсего {len(files)} копий · {sum(os.path.getsize(p) for p in files)/1024:.0f} КБ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument("--into", default="ambar_restore_test")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.list:
        show()
    elif a.restore:
        asyncio.run(restore(a.restore, a.into, a.yes))
    else:
        asyncio.run(dump())


if __name__ == "__main__":
    main()
