"""Разъезд тёзок в зарплатах — один раз, 23 сен 2026.

У нас Парвиз — и старший оператор, и водитель на Бизнес Бей; Фарух — и
оператор, и водитель на JVC. Разные люди, а человек в ведомости — это его
имя, и водители-тёзки в неё не помещались (13 в расписании, 11 в ведомости).

Имя отдаём ВОДИТЕЛЮ: по имени водителя ходит вся автоматика — смены, штрафы,
урезанное питание, чай, удержания за бой. У оператора и старшего автоматики
нет, только вписанное руками, поэтому их записи переезжают под уточнённый
ключ: «Парвиз · старший», «Фарух · оператор».

Что переносим: карточку человека (fin_people) и оклады по месяцам
(fin_pay_months). Записи зарплат (fin_pay_items) и выплаты (fin_entries) на
этих именах отсутствуют — проверяется здесь же и, если появятся, переносятся
тоже. Скрипт можно запускать повторно: уже переехавшее он не трогает.

    python3 tools/pay_namesakes_2026-09-23.py [--вправду]
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db                                                      # noqa: E402

ВПРАВДУ = "--вправду" in sys.argv
ПЕРЕЕЗД = [("Парвиз", "Парвиз · старший", "senior"),
           ("Фарух", "Фарух · оператор", "operator")]


async def main():
    await db.connect()
    d = db._db
    for было, стало, роль in ПЕРЕЕЗД:
        print(f"\n{было} → {стало} ({роль})")
        if await d.fin_people.find_one({"_id": стало}):
            print("  уже переехал — не трогаю")
            continue
        карточка = await d.fin_people.find_one({"_id": было})
        месяцы = [x async for x in d.fin_pay_months.find({"name": было})]
        записи = await d.fin_pay_items.count_documents({"name": было})
        выплаты = await d.fin_entries.count_documents({"who": было})
        print(f"  карточка: {'есть' if карточка else 'нет'} · окладов по месяцам: {len(месяцы)}"
              f" · записей зарплат: {записи} · выплат: {выплаты}")
        for m in месяцы:
            print(f"    {m.get('month')}: {m.get('rate')} {m.get('cur')} — это оклад {роль}")
        if not ВПРАВДУ:
            print("  (прогон вхолостую, ничего не меняю — добавьте --вправду)")
            continue
        if карточка:
            новая = {k: v for k, v in карточка.items() if k != "_id"}
            новая["role"] = роль
            await d.fin_people.insert_one({"_id": стало, **новая})
            await d.fin_people.delete_one({"_id": было})
            print("  карточка перенесена")
        else:
            await d.fin_people.insert_one({"_id": стало, "role": роль})
            print("  карточка заведена")
        for кол, поле in ((d.fin_pay_months, "name"), (d.fin_pay_items, "name"),
                          (d.fin_entries, "who")):
            r = await кол.update_many({поле: было}, {"$set": {поле: стало}})
            if r.modified_count:
                print(f"  {кол.name}: перенесено {r.modified_count}")
    print("\nГотово." if ВПРАВДУ else "\nХолостой прогон. Повторите с --вправду.")


asyncio.run(main())
