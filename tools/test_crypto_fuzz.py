"""Крипта в РП на случайных цепочках — короткий прогон в общем наборе.

Полный — `python3 tools/fuzz_crypto.py` (там же, что проверяется). Здесь то же
на постоянных зёрнах плюс проверка самого фаззера: каждое правило подсчёта,
если его испортить, он обязан поймать.

    python3 tools/test_crypto_fuzz.py
"""
import asyncio, inspect, os, sys, secrets
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import fuzz_crypto as F, fuzz_finance as FF                        # noqa: E402
import db, finance_routes as fr, finance_calc as calc, crypto_book as cb   # noqa: E402

FAIL = []
def eq(имя, дали, ждём):
    ок = дали == ждём
    print(("  ok  " if ок else "  FAIL") + f" {имя}: {дали!r}" + ("" if ок else f" ≠ {ждём!r}"))
    if not ок: FAIL.append(имя)


снимки, чистых = [], 0
for k in range(25):
    out = asyncio.run(F.прогон(400 + k, 80))
    if out:
        чистых += 1; снимки += out
eq("25 цепочек по 80 шагов: сервер и модель согласны, ни один дирхам не потерян и не удвоен",
   (чистых, [(b[0], b[1], b[2]) for b in F.БЕДЫ[:2]]), (25, []))
плохо, всего = FF.экран(снимки)
eq("экран: ДДС дня и месяца сходятся с сейфом на этих книгах", (всего, плохо[:1]), (0, []))
eq("пройдены все ветки", [k for k in F.НАДО if k not in F.ВЕТКИ], [])


def подмена(mod, имя, a, b):
    было = getattr(mod, имя)
    src = inspect.getsource(было)
    assert src.count(a) == 1, (имя, a)
    exec(compile(src.replace(a, b), имя + "_mut", "exec"), mod.__dict__)
    return было


async def _двоит(doc):
    await db._db.crypto_moves.insert_one({**doc, "_id": secrets.token_hex(8)})
    return True


print("── фаззер обязан ловить испорченное правило ───────────────────")
ПОЛОМКИ = (
    ("в РП+ кладут крипты больше, чем свободно", fr, "_cr_alloc_check",
     'if delta > st["free"] + crypto_book.EPS:', "if False:"),
    ("расход криптой задним числом уводит счёт РП в минус", fr, "_cr_spend_check",
     'have = crypto_book.rp_min_from(st["by_day"], day)', 'have = st["rp"]'),
    ("вывод в наличные берёт всё с крипта-счёта РП, минуя свободную", fr, "_cr_withdraw_split",
     "f = round(min(amount, free), 2)", "f = 0.0"),
    ("расход криптой уменьшает наличные сейфа", calc, "compute",
     "rp_st = rp_st + collected_c + extra_rp - (exp_sum - exp_cr)", "rp_st = rp_st + collected_c + extra_rp - exp_sum"),
    ("перекладка крипты в наличные не уменьшает крипту РП", calc, "compute",
     "rp_cr = rp_cr + collected_cr_c - exp_cr - cr_cash", "rp_cr = rp_cr + collected_cr_c - exp_cr"),
    ("перевод между своими кошельками считается приходом", cb, "sync",
     '            if (t.get("peer") or "") in свои:\n                continue                         # между',
     '            if False:\n                continue                         # между'),
    ("свободную крипту предлагают каждому ждущему дню заново", fr, "build",
     "free_run = round(free_run - cr, 2)", "free_run = free_run"),
    ("наличными в РП+ предлагают всю норму, не вычитая крипту", fr, "build",
     'max(0.0, calc._n(budget["norm"]) - cr) - 1e-9)', 'max(0.0, calc._n(budget["norm"])) - 1e-9)'),
    ("повторная сверка записывает приход второй раз", db, "crypto_move_add", None, None),
    ("черновик неподтверждённого дня считается уже лежащим в книге", fr, "handle_day_ok",
     'было = calc._n(m.get("collected_cr")) if m.get("ok") else 0.0', 'было = calc._n(m.get("collected_cr"))'),
)
for имя, mod, функция, a, b in ПОЛОМКИ:
    if a is None:
        было = getattr(mod, функция); setattr(mod, функция, _двоит)
    else:
        было = подмена(mod, функция, a, b)
    F.БЕДЫ.clear(); F.МИР["тихо"] = True
    пойман = False
    for k in range(40):
        out = asyncio.run(F.прогон(900 + k, 80))
        if F.БЕДЫ or (out and FF.экран(out)[1]):
            пойман = True
            break
    eq(f"пойман: {имя}", пойман, True)
    setattr(mod, функция, было)
F.БЕДЫ.clear(); F.МИР["тихо"] = False
eq("после возврата всё снова чисто", bool(asyncio.run(F.прогон(4321, 80))), True)

print("\nИТОГ:", "все прошли" if not FAIL else f"провалено: {FAIL}")
sys.exit(1 if FAIL else 0)
