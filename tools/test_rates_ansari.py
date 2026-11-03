"""Курс валют — прямо с сайта обменника (владелец, 19 сен 2026: «убедись, что
курсы валют обновляются правильно и они актуальны именно с обменников»).

Поддельный «сайт Al Ansari» на aiohttp: страница конвертера с ключом и списком
валют и admin-ajax, который отвечает курсами, как настоящий (S — обменник
покупает у нас, B — продаёт нам). Проверяется:
  • ключ со страницы — прямо, из сжатой вставки NitroCDN (base64) и из
    экранированного JSON; список валют — строка страны, совпавшей с валютой;
  • cash_aed — «купит», cash_buy_aed — «продаст» (у основных валют);
  • в списке только то, что меняет обменник (владелец: «то, что обменник не
    меняет, вообще оттуда убирай»): ответ дальше трети от рынка (сомони
    0.0003) — не курс, валюты нет; нет у обменника в списке — нет и у нас;
  • ключа нет — обменник молчит: None (держим его последний курс, рынок не
    подставляем); рынок молчит — строки из обменника;
  • зарплата в долларах (курс месяца не вписан) — по «продаст», до сотых;
  • с 3 ноя 2026 (сайт закрыл серверу /wp-admin/): конвертер не отвечает —
    один пробный запрос, остальные не шлём, и не чаще раза в сутки (владелец:
    «давай раз в день… с сервера»); курс извне (POST /api/fx/push) принимается
    только с ключом, сверяется с рынком, как свой, ложится в кэш и в базу, и
    пока он не старше суток, сервер к обменнику не ходит; после перезапуска
    курс берётся из базы с его временем и пометкой «молчит», пока сайт закрыт."""
import asyncio, base64, json, os, sys, time
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
    if STATE.get("deny"):                 # как Cloudflare серверу с 23 окт 2026
        return web.Response(text="<html>Sorry, you have been blocked</html>", status=403)
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
    u, e, rb = rows["USD"], rows["EUR"], rows["RUB"]
    eq("доллар: купит 3.655, продаст 3.677, рынок 3.6725", (u["cash_aed"], u["cash_buy_aed"], u["aed"]), (3.655, 3.677, 3.6725))
    eq("евро: купит 4.1485, продаст 4.2684", (e["cash_aed"], e["cash_buy_aed"]), (4.1485, 4.2684))
    eq("рубль: купит 0.0393, продаст 0.045", (rb["cash_aed"], rb["cash_buy_aed"]), (0.0393, 0.045))
    eq("в списке только валюты обменника: сомони (0.0003) и фунт (нет в его списке) убраны", sorted(rows), ["EUR", "RUB", "USD"])
    eq("время курса — наше, когда спросили", bool(u.get("cash_at")), True)
    eq("«продаст» спрашиваем только у основных, «купит» — у всех из списка",
       sorted(set(tr for _, _, tr in STATE["seen"])), ["B", "S"])
    eq("у обменника — три валюты (сомони отсеян)", R._CACHE.get("cash_n"), 3)

    print("── источники молчат ─────────────────────────────────────────")
    STATE["page"] = "none"
    eq("ключа нет — обменник молчит: None, рынок вместо него не подставляем", await R._fetch(), None)
    R._CACHE.update(at=0.0, data=[{"code": "USD", "cash_aed": 3.6}], fail_at=0.0)
    p = await R.get_rates(force=True)
    eq("…и в ответе — последний курс обменника с пометкой «молчит»", (p["ok"], p["silent"], p["rates"][0]["cash_aed"]), (False, True, 3.6))
    R._CACHE.update(at=0.0, data=None, fail_at=0.0)
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
    # Владелец, 23 сен 2026: «в зарплатах до сих пор число до тысячных, надо до
    # сотых». 3.677 → 3.67 без округления: курс не только показывается, но и
    # считает, и сумма в дирхамах обязана сходиться с тем, что видно.
    eq("курс месяца не вписан — «продаст» обменника, сотые без округления",
       (got["usd"], got["usd_auto"], got["usd_set"]), (3.67, 3.67, False))
    got = await fr._usd({"usd": 3.7})
    eq("вписан руками — он", (got["usd"], got["usd_set"]), (3.7, True))

    print("── конвертер закрыт серверу, курс извне ─────────────────────")
    from aiohttp.test_utils import make_mocked_request
    STATE["page"] = "plain"; STATE["seen"].clear(); STATE["deny"] = True
    eq("конвертер не отвечает — один пробный запрос, остальные не шлём",
       (await R._fetch_cash(None), len(STATE["seen"])), ({}, 1))
    STATE["deny"] = False

    def push(body, token=None):
        req = make_mocked_request("POST", "/api/fx/push", headers={"X-Ambar-Relay": token} if token else {})
        req._read_bytes = json.dumps(body).encode()
        return req
    CASH = {"USD": {"sell": 3.655, "buy": 3.677}, "EUR": {"sell": 4.1485, "buy": 4.2684},
            "TJS": {"sell": 0.0003, "buy": 0.0003}, "XXX": {"sell": 0}, "bad": {"sell": 1}}
    os.environ.pop("FX_RELAY_TOKEN", None)
    eq("ключ на сервере не задан — 401 даже с ключом", (await R.handle_push(push({"cash": CASH}, "k1"))).status, 401)
    os.environ["FX_RELAY_TOKEN"] = "k1"
    eq("без ключа — 401", (await R.handle_push(push({"cash": CASH}))).status, 401)
    eq("чужой ключ — 401", (await R.handle_push(push({"cash": CASH}, "k2"))).status, 401)
    eq("мусор вместо курса — 400", (await R.handle_push(push({"cash": "nope"}, "k1"))).status, 400)
    eq("только нули — 400", (await R.handle_push(push({"cash": {"USD": {"sell": 0}}}, "k1"))).status, 400)
    saved = {}
    async def _last_set(doc): saved["doc"] = dict(doc)
    async def _last_get(): return saved.get("doc")
    db.fx_last_set, db.fx_last_get = _last_set, _last_get
    STATE["page"] = "none"                 # серверу сайт закрыт
    R._CACHE.update(at=0.0, data=None, fail_at=0.0, restored=True)
    r = await R.handle_push(push({"cash": CASH}, "k1")); d = json.loads(r.text)
    eq("курс извне принят: доллар и евро, сомони отсеян рынком, мусор отброшен",
       (r.status, d["n"], d["usd"]["cash_aed"], d["usd"]["cash_buy_aed"]), (200, 2, 3.655, 3.677))
    p = await R.get_rates()
    eq("отдаём его как свежий, не «молчит», строки как у своего", (p["ok"], p["silent"], [(x["code"], x["cash_buy_aed"], x["aed"]) for x in p["rates"]]),
       (True, False, [("USD", 3.677, 3.6725), ("EUR", 4.2684, 4.2157)]))
    eq("…и он записан в базу с пометкой «извне»", (saved["doc"]["src"], len(saved["doc"]["rows"])), ("relay", 2))
    STATE["page"] = "plain"; STATE["seen"].clear()
    await R.get_rates()
    eq("курс извне не старше суток — сервер к обменнику не ходит", len(STATE["seen"]), 0)
    R._CACHE.update(at=0.0, data=None, fail_at=0.0, restored=False)
    saved["doc"]["at_epoch"] = time.time() - 30 * 3600
    STATE["page"] = "none"
    p = await R.get_rates()
    eq("после перезапуска — курс из базы, его время, «молчит», пока сайт закрыт",
       (p["ok"], p["silent"], p["rates"][0]["code"], abs(p["fetched_at"] / 1000 - saved["doc"]["at_epoch"]) < 2),
       (False, True, "USD", True))
    STATE["page"] = "plain"; STATE["seen"].clear()
    p = await R.get_rates()
    eq("неудача — до завтра сервер к обменнику не ходит, курс старый с пометкой «молчит»",
       (len(STATE["seen"]), p["silent"], p["rates"][0]["code"]), (0, True, "USD"))
    r = await R.handle_push(push({"cash": CASH}, "k1"))
    eq("курс привезли снова — свежий", (r.status, (await R.get_rates())["silent"]), (200, False))
    R._CACHE.update(at=0.0, data=None, fail_at=0.0, restored=True); STATE["seen"].clear(); STATE["deny"] = True
    for _ in range(3):
        await R.get_rates()
    eq("без курса на руках и при закрытой двери — один заход в сутки, не на каждый запрос", len(STATE["seen"]), 1)
    STATE["deny"] = False
    os.environ.pop("FX_RELAY_TOKEN", None)

    await srv.close()
    print("\nвсё прошло" if not FAIL else f"\nНЕ ПРОШЛИ: {FAIL}")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
