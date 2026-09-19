"""Курс валют — прямо с сайта обменника (владелец, 19 сен 2026: «убедись, что
курсы валют обновляются правильно и они актуальны именно с обменников»).

Поддельный «сайт Al Ansari» на aiohttp: страница конвертера с ключом и списком
валют и admin-ajax, который отвечает курсами, как настоящий (S — обменник
покупает у нас, B — продаёт нам). Проверяется:
  • ключ со страницы — прямо, из сжатой вставки NitroCDN (base64) и из
    экранированного JSON; список валют — строка страны, совпавшей с валютой;
  • cash_aed — «купит», cash_buy_aed — «продаст» (у основных валют);
  • ответ дальше трети от рынка (сомони 0.0003) — не курс, остаётся рынок;
  • ключа нет — обменник молчит, остаётся рынок; рынок молчит — строки из
    обменника; молчат оба — None;
  • зарплата в долларах (курс месяца не вписан) — по «продаст»."""
import asyncio, base64, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "")
import logging
logging.disable(logging.CRITICAL)
from aiohttp import web
from aiohttp.test_utils import TestServer
import rates as R

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

LIST = ('<span data-ccyname="USD" data-cntcode="92" data-toccy="92"></span>'
        '<span data-ccyname="EUR" data-cntcode="146" data-toccy="75"></span>'
        '<span data-ccyname="EUR" data-cntcode="14" data-toccy="75"></span>'
        '<span data-ccyname="RUB" data-cntcode="63" data-toccy="63"></span>'
        '<span data-ccyname="TJS" data-cntcode="217" data-toccy="217"></span>'
        '<span data-ccyname="USD" data-cntcode="69" data-toccy="92"></span>')
RATES = {(92, "S"): 3.65500002, (92, "B"): 3.67699997, (75, "S"): 4.14850004, (75, "B"): 4.26840007,
         (63, "S"): 0.0393, (63, "B"): 0.045, (217, "S"): 0.0003, (217, "B"): 0.0003}
STATE = {"page": "plain", "seen": []}


async def page(request):
    obj = json.dumps({"ajax_url": "x", "ajax_nonce": "abc123"})
    if STATE["page"] == "plain":
        js = f"<script>var CC_Ajax_Object = {obj};</script>"
    elif STATE["page"] == "nitro":
        b = base64.b64encode(f"var CC_Ajax_Object = {obj};".encode()).decode()
        js = f'<script>NPRL.registerInlineScript("cc-ajax-js-extra", "{b}");</script>'
    elif STATE["page"] == "escaped":
        js = '<script>x="{\\"ajax_nonce\\":\\"abc123\\"}"</script>'
    else:
        js = ""
    return web.Response(text=f"<html>{js}<ul class='currency-select send-currency'>{LIST}</ul></html>",
                        content_type="text/html")


async def ajax(request):
    f = await request.post()
    STATE["seen"].append((f.get("currfrom"), f.get("cntcode"), f.get("trtype")))
    if f.get("security") != "abc123" or f.get("action") != "foreign_action" or f.get("currto") != "91":
        return web.Response(text="-1")
    v = RATES.get((int(f.get("currfrom")), f.get("trtype")))
    if v is None:
        return web.json_response({"status_msg": "FAILED"})
    return web.json_response({"amount": f"{v:.2f}", "rate": f"{v:.8f}", "status_msg": "SUCCESS"})


MARKET = {"USD": 1 / 3.6725, "EUR": 1 / 4.2157, "RUB": 1 / 0.0435, "TJS": 1 / 0.3973, "GBP": 1 / 4.9104}
MKT_ON = {"on": True}
async def fake_market():
    if not MKT_ON["on"]:
        return None
    return dict(MARKET), "Sat, 19 Sep 2026 00:02:31 +0000"


async def main():
    app = web.Application()
    app.router.add_get("/page", page)
    app.router.add_post("/ajax", ajax)
    srv = TestServer(app)
    await srv.start_server()
    R.SRC_PAGE, R.SRC_AJAX = str(srv.make_url("/page")), str(srv.make_url("/ajax"))
    R.ASK = ["USD", "EUR", "RUB", "TJS", "GBP"]
    R.MAIN = ["USD", "EUR", "GBP", "RUB", "TJS"]
    R._fetch_market = fake_market

    print("── ключ и список валют ──────────────────────────────────────")
    for kind in ("plain", "nitro", "escaped"):
        STATE["page"] = kind
        html = await (await __import__("aiohttp").ClientSession().get(R.SRC_PAGE)).text()
        eq(f"ключ со страницы ({kind})", R.page_nonce(html), "abc123")
    codes = R.page_codes(html)
    eq("доллар — строка США, евро — первая страна (все одного курса)", (codes["USD"], codes["EUR"]), ((92, 92), (75, 146)))

    print("── курсы обменника ──────────────────────────────────────────")
    STATE["page"] = "plain"; STATE["seen"].clear()
    rows = {r["code"]: r for r in await R._fetch()}
    u, e, rb, t = rows["USD"], rows["EUR"], rows["RUB"], rows["TJS"]
    eq("доллар: купит 3.655, продаст 3.677, рынок 3.6725", (u["cash_aed"], u["cash_buy_aed"], u["aed"]), (3.655, 3.677, 3.6725))
    eq("евро: купит 4.1485, продаст 4.2684", (e["cash_aed"], e["cash_buy_aed"]), (4.1485, 4.2684))
    eq("рубль: купит 0.0393, продаст 0.045", (rb["cash_aed"], rb["cash_buy_aed"]), (0.0393, 0.045))
    eq("сомони 0.0003 при рынке 0.40 — не курс: только рынок", ("cash_aed" in t, t["aed"]), (False, 0.3973))
    eq("фунт — у обменника нет в списке: рынок", ("cash_aed" in rows["GBP"], rows["GBP"]["aed"]), (False, 4.9104))
    eq("время курса — наше, когда спросили", bool(u.get("cash_at")), True)
    eq("«продаст» спрашиваем только у основных, «купит» — у всех из списка",
       sorted(set(tr for _, _, tr in STATE["seen"])), ["B", "S"])
    eq("у обменника — три валюты (сомони отсеян)", R._CACHE.get("cash_n"), 3)

    print("── источники молчат ─────────────────────────────────────────")
    STATE["page"] = "none"
    rows = {r["code"]: r for r in await R._fetch()}
    eq("ключа нет — обменник молчит, стоит рынок", ("cash_aed" in rows["USD"], rows["USD"]["aed"]), (False, 3.6725))
    STATE["page"] = "plain"; MKT_ON["on"] = False
    rows = {r["code"]: r for r in await R._fetch()}
    eq("рынок молчит — строки из обменника, рыночное поле = обменник", (sorted(rows), rows["USD"]["aed"]),
       (["EUR", "RUB", "USD"], 3.655))
    R._CACHE.pop("mkt_rates", None)
    rows = {r["code"]: r for r in await R._fetch()}
    eq("рынка не было ни разу — сомони отсеян по «продаёт не дороже, чем покупает»", sorted(rows), ["EUR", "RUB", "USD"])
    STATE["page"] = "none"
    eq("молчат оба — None", await R._fetch(), None)
    MKT_ON["on"] = True; STATE["page"] = "plain"

    print("── зарплата в долларах ──────────────────────────────────────")
    import finance_routes as fr
    R._CACHE.update(at=0.0, data=None, fail_at=0.0)
    async def _nosnap(*a, **k): return None
    import db
    db.fx_day_set = _nosnap
    got = await fr._usd({})
    eq("курс месяца не вписан — по «продаст» обменника", (got["usd"], got["usd_auto"], got["usd_set"]), (3.677, 3.677, False))
    got = await fr._usd({"usd": 3.7})
    eq("вписан руками — он", (got["usd"], got["usd_set"]), (3.7, True))

    await srv.close()
    print("\nвсё прошло" if not FAIL else f"\nНЕ ПРОШЛИ: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
