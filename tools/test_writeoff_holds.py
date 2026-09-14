"""Удержания по списаниям — в зарплату (владелец, 14 сен 2026). Без базы."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import finance_routes as fin, finance_pay as pay
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
ROWS = [
  {"_id": "w1", "day": "2026-09-14", "kind": "бой", "name": "Absolut 1 ltr", "qty": 1, "state": "ok",
   "comp": {"who": "Худоба", "amount": 62, "note": "уронил", "by_name": "Старший", "at": "2026-09-14T10:00:00"}},
  {"_id": "w2", "day": "2026-09-12", "kind": "потеря", "name": "Hennessy", "qty": 2,
   "comp": {"who": "Худоба, Фарух", "amount": 300, "note": "", "by_name": "Старший",
            "split": [{"who": "Худоба", "amount": 200}, {"who": "Фарух", "amount": 100}]}},
  {"_id": "w3", "day": "2026-08-30", "kind": "брак", "name": "Jack", "qty": 1, "state": "ok",
   "comp": {"who": "Фарух", "amount": 50, "note": "", "by_name": "Старший"}},
]
async def comps(who="", limit=2000):
    def hit(r):
        c = r["comp"]
        return not who or c["who"] == who or any(x["who"] == who for x in c.get("split") or [])
    return [r for r in ROWS if hit(r)]
fin.db.writeoff_comps = comps

async def main():
    all_ = await fin.writeoff_holds()
    eq("всего записей (одиночное + две доли + одно)", len(all_), 4)
    mine = await fin.writeoff_holds("Худоба")
    eq("Худоба: две записи", [x["amount"] for x in mine], [62, 200])
    eq("вид и причина", (mine[0]["kind"], mine[0]["reason"]), ("hold", "Бой · Absolut 1 ltr × 1"))
    eq("месяц списания", (mine[0]["from"], mine[0]["day"]), ("2026-09", "2026-09-14"))
    eq("метка источника", (mine[0]["src"], mine[0]["wid"], mine[0]["_id"]), ("writeoff", "w1", "wo:w1:0"))
    eq("доля из раскладки", mine[1]["reason"], "Потеря · Hennessy × 2")
    # в ведомости: сентябрь — минус 262, август — минус 0 у Худобы
    p = {"name": "Худоба", "_month": "2026-09"}
    eff = dict(rate=3000, unit="month", cur="AED", days=None, note="", rate_month="2026-09")
    r = pay.person_month(p, "2026-09", eff, 26, mine, [], 3.67)
    eq("сентябрь: минус", r["minus"], 262); eq("сентябрь: к выплате", r["to_pay"], 3000 - 262)
    eq("строки помечены src", [x["src"] for x in r["items"]], ["writeoff", "writeoff"])
    r8 = pay.person_month(p, "2026-08", eff, 26, mine, [], 3.67)
    eq("август: удержаний сентября ещё нет", r8["minus"], 0)
    r10 = pay.person_month(p, "2026-10", eff, 26, mine, [], 3.67)
    eq("октябрь: всё уже удержано в сентябре", r10["minus"], 0)
    hist = fin._penalty_history(mine, "2026-09")
    eq("история: src в строках", [h["src"] for h in hist], ["writeoff", "writeoff"])
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
