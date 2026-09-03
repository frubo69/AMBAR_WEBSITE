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
    python3 tools/backup.py --restore ФАЙЛ --into ambar --yes --photos  # вместе со снимками

Хранение: 14 последних ежедневных и 8 воскресных. Место не жмёт — копия
весит килобайты, — но бесконечная свалка мешает найти нужную.

Снимки (чеки и кадры списаний) в эту копию не входят: они не меняются и весят
на три порядка больше остального, а копия снимается каждый час. Они зеркалятся
рядом, в backups/photos, по файлу на кадр, и только новые. Поэтому полное
восстановление — это два шага, и второй легко забыть: --photos.
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
# Снимки в почасовую копию не идут. Копия снимается раз в час и хранится
# шестьюдесятью файлами: каждая фотография попадала бы в каждую из них заново,
# в Extended JSON — ещё и на треть тяжелее, чем есть. Год работы превратил бы
# двадцать шесть мегабайт копий в десятки гигабайт. Снимки лежат рядом,
# отдельным зеркалом, и туда попадает только то, чего там ещё нет.
PHOTOS = ("expense_photos", "writeoff_photos")
SKIP |= set(PHOTOS)
PHOTO_DIR = os.path.join(DIR, "photos")


def _env(name: str) -> str:
    """Значение из окружения, а если его нет — из .env: скрипт живёт вне сервиса."""
    val = os.getenv(name, "")
    if val:
        return val
    env = os.path.join("/opt/ambar", ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _uri():
    uri = _env("MONGO_URI")
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
    await mirror_photos(db)
    _rotate()
    return path


async def mirror_photos(db, standby=None):
    """Зеркало снимков: файл на диск и, если есть, документ в запасную базу.

    Только новое. Уже лежащий кадр не переписывается: он не меняется — снимок
    либо есть, либо его никогда не делали. Удалённое из базы отсюда не убираем:
    копия, которая забывает вслед за боевой, копией не является."""
    было = новых = 0
    for name in PHOTOS:
        d = os.path.join(PHOTO_DIR, name)
        os.makedirs(d, exist_ok=True)
        есть = {f.rsplit(".", 1)[0] for f in os.listdir(d)}
        было += len(есть)
        # Читаем только те _id, которых на диске нет: тянуть все картинки из
        # базы ради сверки — ровно та работа, от которой мы и уходим.
        async for doc in db[name].find({"_id": {"$nin": list(есть)}} if есть else {}):
            img = bytes(doc.get("img") or b"")
            if not img:
                continue
            with open(os.path.join(d, f"{doc['_id']}.jpg"), "wb") as f:
                f.write(img)
            новых += 1
            if standby is not None:
                await standby[name].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    print(f"снимки: было {было}, добавлено {новых}")


async def restore_photos(db):
    """Вернуть снимки из зеркала в базу. Нужно после восстановления из jsonl:
    там их нет, и без этого шага история списаний останется без доказательств."""
    from bson.binary import Binary
    n = 0
    for name in PHOTOS:
        d = os.path.join(PHOTO_DIR, name)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            wid = f.rsplit(".", 1)[0]
            if await db[name].find_one({"_id": wid}, {"_id": 1}):
                continue
            with open(os.path.join(d, f), "rb") as fh:
                await db[name].replace_one(
                    {"_id": wid}, {"_id": wid, "img": Binary(fh.read())}, upsert=True)
            n += 1
    print(f"снимков возвращено: {n}")


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


async def restore(path, into, yes, uri: str = ""):
    if into == "ambar" and not yes:
        sys.exit("Восстановление поверх боевой базы требует --yes")
    cli = motor.motor_asyncio.AsyncIOMotorClient(uri or _uri(), serverSelectionTimeoutMS=20000)
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


async def _photos_to_standby(uri):
    cli = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
    db = cli[_db_name(uri)]
    n = 0
    for name in PHOTOS:
        d = os.path.join(PHOTO_DIR, name)
        if not os.path.isdir(d):
            continue
        есть = set()
        async for x in db[name].find({}, {"_id": 1}):
            есть.add(x["_id"])
        from bson.binary import Binary
        for f in sorted(os.listdir(d)):
            wid = f.rsplit(".", 1)[0]
            if wid in есть:
                continue
            with open(os.path.join(d, f), "rb") as fh:
                await db[name].replace_one(
                    {"_id": wid}, {"_id": wid, "img": Binary(fh.read())}, upsert=True)
            n += 1
    print(f"  снимков донесено в запасную: {n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument("--into", default="ambar_restore_test")
    ap.add_argument("--uri", default="", help="куда восстанавливать (по умолчанию — боевой кластер)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--photos", action="store_true",
                    help="вернуть снимки из зеркала в базу (после --restore)")
    ap.add_argument("--mirror", action="store_true",
                    help="после копии залить её в запасную базу (MONGO_URI_STANDBY)")
    a = ap.parse_args()
    if a.list:
        show()
    elif a.photos and not a.restore:
        uri = a.uri or _uri()
        cli = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
        asyncio.run(restore_photos(cli[a.into if a.into != "ambar_restore_test" else _db_name(uri)]))
    elif a.restore:
        asyncio.run(restore(a.restore, a.into, a.yes, a.uri))
        if a.photos:
            uri = a.uri or _uri()
            cli = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
            asyncio.run(restore_photos(cli[a.into]))
    else:
        path = asyncio.run(dump())
        if a.mirror:
            # Копия на диске защищает данные, но не работу: если кластер погасят,
            # без запасной базы приложение просто встанет. Поэтому свежий дамп
            # сразу разворачиваем в локальный mongod — переключение сводится к
            # замене MONGO_URI и перезапуску сервисов.
            tgt = _env("MONGO_URI_STANDBY")
            if not tgt:
                print("зеркало пропущено: MONGO_URI_STANDBY не задан")
            else:
                print("\nзеркалим в запасную базу…")
                asyncio.run(restore(path, _db_name(tgt), yes=True, uri=tgt))
                # Снимков в дампе нет — доносим их отдельно. Иначе переключение
                # на запасную базу означало бы историю без единой фотографии.
                asyncio.run(_photos_to_standby(tgt))


if __name__ == "__main__":
    main()
