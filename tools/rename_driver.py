"""Переименовать водителя во всей базе (владелец, 19 сен 2026: «водителя
Бахадыр поменяй имя на Баха везде»).

Рабочее имя водителя — ключ во многих местах: реестр drivers, дни смены,
заказы, коды приёмки, машины, перемещения, приёмки, треки, точка на карте,
сторож геопозиции, зарплатная карточка, экипаж в открытии смены (ключом
словаря drivers.<имя>), журнал звонков, уведомления. Поменять в одном месте
— значит отвязать человека от его же истории, поэтому здесь — все сразу:

  • строковое значение, равное старому имени, — на новое (точным $set по пути,
    включая элементы массивов: extras.3.by_driver);
  • ключ словаря, равный старому имени, — значение под новым ключом ($set
    drivers.Баха) и старый ключ прочь ($unset drivers.Бахадыр), вложенное
    имя внутри значения — тоже новое;
  • имя внутри текста — только в текстах уведомлений (owner_notifications.
    text) и только в той же форме (склонений там нет — проверено перед
    запуском); в прочих текстах — отчёт, не трогаем;
  • _id с именем (driver_pos, driver_geo_watch, fin_people — имя целиком;
    driver_tracks «день:имя», fin_pay_months «месяц|имя») — запись
    пересоздаётся под новым _id, старая удаляется; если под новым уже пишет
    живой процесс — оставляем новую, старую удаляем.

Имя в расписании районов (config_staff.py) меняется в коде тем же коммитом;
после запуска — перезапуск всех ambar-* (роли и маршруты читаются при старте)
и повторный прогон: процессы до перезапуска могли записать что-то под старым
именем. Копия всех затронутых записей — /opt/ambar/backups/. Без --apply
только показывает.

    venv/bin/python tools/rename_driver.py СТАРОЕ НОВОЕ [--apply]
"""
import asyncio, gzip, os, sys
from collections import Counter
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

SKIP = {"expense_photos", "writeoff_photos", "photos", "tg_files"}     # байты снимков
TEXT_OK = {("owner_notifications", "text")}


def plan_doc(coll: str, doc: dict, old: str, new: str):
    """(sets, unsets, text_skipped) для документа без смены _id. Ключ словаря
    со старым именем — это $set значения под новым ключом и $unset старого."""
    sets, unsets, skipped = {}, {}, []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                if path == "" and k == "_id":
                    continue
                p = f"{path}.{k}" if path else str(k)
                if k == old:
                    sets[f"{path}.{new}" if path else new] = swap(v, old, new)
                    unsets[p] = ""
                    continue
                walk(v, p)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}.{i}")
        elif isinstance(x, str):
            if x == old:
                sets[path] = new
            elif old in x:
                if (coll, path) in TEXT_OK:
                    sets[path] = x.replace(old, new)
                else:
                    skipped.append(path)
    walk(doc, "")
    return sets, unsets, skipped


def swap(x, old: str, new: str):
    """Полная копия с заменой — для записей, у которых меняется _id."""
    if isinstance(x, dict):
        return {(new if k == old else k): swap(v, old, new) for k, v in x.items()}
    if isinstance(x, list):
        return [swap(v, old, new) for v in x]
    if isinstance(x, str):
        return new if x == old else x
    return x


def new_id(_id, old: str, new: str):
    if not isinstance(_id, str):
        return None
    if _id == old:
        return new
    for sep in (":", "|"):
        if _id.endswith(sep + old):
            return _id[: -len(old)] + new
        if _id.startswith(old + sep):
            return new + _id[len(old):]
    return None


def has(x, old: str) -> bool:
    if isinstance(x, dict):
        return any(k == old or has(v, old) for k, v in x.items())
    if isinstance(x, list):
        return any(has(v, old) for v in x)
    return isinstance(x, str) and old in x


async def rename(d, old: str, new: str, apply: bool, backup_dir: str = "") -> dict:
    """Два прохода: сначала собрать и сохранить копию всего, что тронем, потом
    менять. Возвращает сводку."""
    plan, stat, left = [], Counter(), []
    for coll in sorted(await d.list_collection_names()):
        if coll in SKIP:
            continue
        async for doc in d[coll].find({}):
            if not has(doc, old):
                continue
            nid = new_id(doc.get("_id"), old, new)
            if isinstance(doc.get("_id"), str) and old in doc["_id"] and nid is None:
                left.append(f"{coll}._id {doc['_id']!r} — не понимаю, как переименовать ключ")
                continue
            if nid is not None:
                plan.append((coll, doc, nid, None, None))
                stat[(coll, "новый _id")] += 1
                continue
            sets, unsets, skipped = plan_doc(coll, doc, old, new)
            left += [f"{coll}.{p} — имя внутри текста, не трогаю" for p in skipped]
            if sets or unsets:
                plan.append((coll, doc, None, sets, unsets))
                stat[(coll, "ключи" if unsets else "значения")] += 1
    out = {"docs": len(plan), "stat": stat, "left": left, "backup": ""}
    if not apply or not plan:
        return out
    if backup_dir:
        from bson import json_util
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"rename_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.jsonl.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for coll, doc, *_ in plan:
                f.write(json_util.dumps({"__collection__": coll, "doc": doc}, ensure_ascii=False) + "\n")
        out["backup"] = path
    for coll, doc, nid, sets, unsets in plan:
        if nid is not None:
            fresh = swap({k: v for k, v in doc.items() if k != "_id"}, old, new)
            if await d[coll].find_one({"_id": nid}, {"_id": 1}):
                stat[(coll, "новый _id уже был — старый удалён")] += 1
            else:
                await d[coll].insert_one({"_id": nid, **fresh})
            await d[coll].delete_one({"_id": doc["_id"]})
            continue
        upd = {}
        if sets:
            upd["$set"] = sets
        if unsets:
            upd["$unset"] = unsets
        await d[coll].update_one({"_id": doc["_id"]}, upd)
    return out


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__); return 2
    old, new = args
    apply = "--apply" in sys.argv
    await db.connect()
    res = await rename(db._db, old, new, apply, "/opt/ambar/backups")
    for (coll, what), n in sorted(res["stat"].items()):
        print(f"  {coll}: {what} — {n}")
    for s in res["left"][:20]:
        print("  !", s)
    print(f"документов: {res['docs']}", "· сделано" if apply else "· это просмотр; сделать — с --apply")
    if res["backup"]:
        print("копия до правки:", res["backup"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
