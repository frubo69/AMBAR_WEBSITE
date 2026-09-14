"""История LEGO в бот геопозиции (15 сен 2026): части не длиннее сообщения
телеграма, по-английски, бот шлёт их все и переживает «слишком часто»."""
import asyncio, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import lego_history as lh, geo_bot
FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)
eq("частей ≥ 10", len(lh.PARTS) >= 10, True)
eq("каждая часть ≤ 4096", max(len(p) for p in lh.PARTS) <= 4096, True)
eq("без кириллицы", any(re.search("[А-Яа-яЁё]", p) for p in lh.PARTS), False)
eq("всего знаков > 30 000", lh.TOTAL > 30000, True)
class Bot:
    def __init__(self): self.sent = []; self.fail_once = True
    async def send_message(self, chat, text, **kw):
        from telegram.error import RetryAfter
        if self.fail_once: self.fail_once = False; raise RetryAfter(0)
        self.sent.append((chat, len(text)))
class Ctx: bot = Bot()
async def main():
    geo_bot._aio_sleep_orig = None
    import asyncio as _aio
    real_sleep = _aio.sleep
    _aio.sleep = lambda s: real_sleep(0)          # без пауз в тесте
    try:
        await geo_bot._lego_wall(Ctx, 42, "Худоба")
    finally:
        _aio.sleep = real_sleep
    eq("отправлены все части, RetryAfter пережит", len(Ctx.bot.sent), len(lh.PARTS))
    eq("все в один чат", {c for c, _ in Ctx.bot.sent}, {42})
    print("ИТОГ:", "все прошли" if not FAIL else f"провалено {len(FAIL)}: {FAIL}")
    sys.exit(1 if FAIL else 0)
asyncio.run(main())
