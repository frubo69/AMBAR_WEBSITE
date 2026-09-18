"""Убрать из истории закупок отменённые пустышки (владелец, 18 сен 2026:
«историю заявок тоже почисть»).

В истории висят двенадцать заявок, которые остались от наладки: все отменены,
принято по ним ноль, кодов ноль. Живого в них нет, а экран истории они
занимают целиком — настоящих закупок там всего две.

Удаляем только те, у которых сошлись ВСЕ признаки пустышки:
    • статус cancelled;
    • принято ноль по всем районам;
    • ни одного кода реестра с этим supply_id;
    • ни одной закрытой задачи района.

Остальное не трогаем: заявка с принятым товаром — это склад, а не запись.
Перед удалением всё уходит в файл: восстановить можно целиком.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/supplies_purge.py           # что удалится
    PYTHONPATH=/opt/ambar venv/bin/python tools/supplies_purge.py --apply   # удалить
    PYTHONPATH=/opt/ambar venv/bin/python tools/supplies_purge.py --restore <копия.json>
"""
import asyncio, json, sys, time
from datetime import datetime


async def scan(db) -> dict:
    d = db._db_or_none()
    kill, keep = [], []
    for s in await d.supplies.find({}).sort("at", 1).to_list(500):
        sid = s["_id"]
        got = sum(sum((it.get("got") or {}).values()) for it in (s.get("items") or []))
        codes = await d.qr_codes.count_documents({"supply_id": sid})
        done = [k for k, t in (s.get("tasks") or {}).items() if t.get("done_at")]
        why = []
        if s.get("status") != "cancelled": why.append("не отменена")
        if got: why.append(f"принято {got:g}")
        if codes: why.append(f"кодов {codes}")
        if done: why.append("есть закрытые районы")
        (keep if why else kill).append({"id": sid, "day": s.get("day"), "kind": s.get("kind", "main"),
                                        "status": s.get("status"), "why": ", ".join(why),
                                        "plan": sum(sum((it.get("by_district") or {}).values())
                                                    for it in (s.get("items") or []))})
    return {"kill": kill, "keep": keep}


async def run(db, apply: bool, backup_dir: str = "/root", say=print) -> dict:
    d = db._db_or_none()
    r = await scan(db)
    say(f"в истории {len(r['kill']) + len(r['keep'])} заявок")
    for x in r["keep"]:
        say(f"  оставляем {x['id']} ({x['day']}) — {x['why']}")
    for x in r["kill"]:
        say(f"  удалить   {x['id']} ({x['day']}, {x['kind']}, план {x['plan']:g})")
    if not r["kill"]:
        say("чистить нечего"); return {"ok": True, "n": 0}
    if not apply:
        say(f"пробный прогон: удалилось бы {len(r['kill'])}, ничего не записано (--apply — удалить)")
        return {"ok": True, "dry": True, "n": len(r["kill"])}
    ids = [x["id"] for x in r["kill"]]
    docs = await d.supplies.find({"_id": {"$in": ids}}).to_list(500)
    path = f"{backup_dir}/supplies_purge_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump(docs, f, ensure_ascii=False,
                  default=lambda v: {"$dt": v.isoformat()} if isinstance(v, datetime) else str(v))
    say(f"копия: {path}")
    res = await d.supplies.delete_many({"_id": {"$in": ids}})
    say(f"удалено заявок: {res.deleted_count}")
    return {"ok": True, "n": res.deleted_count, "backup": path}


async def restore(db, path: str, say=print) -> None:
    d = db._db_or_none()

    def revive(v):
        if isinstance(v, dict):
            if set(v) == {"$dt"}: return datetime.fromisoformat(v["$dt"])
            return {k: revive(x) for k, x in v.items()}
        if isinstance(v, list): return [revive(x) for x in v]
        return v
    n = 0
    for doc in json.load(open(path)):
        doc = revive(doc)
        if not await d.supplies.find_one({"_id": doc["_id"]}):
            await d.supplies.insert_one(doc); n += 1
    say(f"возвращено заявок: {n}")


async def main():
    sys.path.insert(0, "/opt/ambar")
    import db
    await db.connect()
    if "--restore" in sys.argv:
        await restore(db, sys.argv[sys.argv.index("--restore") + 1])
        return
    res = await run(db, apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
