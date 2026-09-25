"""Чек у «Что-то ещё» — по желанию (владелец, 25 сен 2026: «сделай так чтобы
сюда можно было загружать чеки тоже, как у водителей»).

Смысл: плитка для снимка у этого вида есть, снимок сохраняется и виден
старшему — но запись уходит и без него. Обязательным чек тут быть не может:
под «что-то ещё» пишут возвраты и долги, где чека нет в природе, и требование
снимка заперло бы саму запись.

  • «что-то ещё» без чека — принято, no_photo не выдаётся;
  • «что-то ещё» с чеком — снимок лёг в базу, у записи есть thumb;
  • у обязательных видов ничего не поменялось: парковка без чека — 400;
  • приложение узнаёт про необязательный чек по флагу receipt_opt, и он стоит
    ровно у «что-то ещё»; receipt — у бензина, мойки и парковки;
  • MUST_RECEIPT (что сервер требует) «что-то ещё» не включает;
  • то же в STAR: там свой список видов и своя карточка — вынимаем их из
    owner/index.html и прогоняем узлом, чтобы плитка и подпись проверялись
    исполнением, а не чтением.
"""
import asyncio, base64, inspect, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", ""); os.environ.setdefault("AMBAR_OWNER_IDS", "1")
import logging
logging.disable(logging.CRITICAL)
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient
import db, driver_routes as dr
from expense_routes import EXTRA_KINDS

FAIL = []
def eq(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {name}: {got!r}" + ("" if ok else f" ≠ {want!r}"))
    if not ok: FAIL.append(name)

D = "2026-09-25"
dr._biz_day = lambda *a, **k: D
PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"\x00" * 3000).decode()
ME = {"name": "Худоба", "district": "jvc", "district_code": "B1"}


def raw(h):
    while True:
        cl = inspect.getclosurevars(h).nonlocals
        nxt = cl.get("handler") or cl.get("fn") or cl.get("func")
        if not nxt:
            return h
        h = nxt


async def post(body):
    req = make_mocked_request("POST", "/x")
    req._read_bytes = json.dumps(body).encode()
    req["driver"] = ME; req["tg"] = {"id": 1}
    r = await raw(dr.handle_expense_add)(req)
    return r.status, json.loads(r.text)


def star():
    """То же самое в STAR: там свой список видов и своя карточка расхода.

    Проверяем не глазами, а исполнением: вынимаем из owner/index.html сам
    список EXP_KINDS и само выражение, которое решает, рисовать ли плитку,
    и прогоняем их узлом с заглушками."""
    import re, subprocess, tempfile
    print("── STAR: тот же чек по желанию ────────────────────────────────")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "owner", "index.html"), encoding="utf-8").read()
    kinds = re.search(r"const EXP_KINDS = \[.*?\n\];", src, re.S)
    плитка = re.search(r"    // Где чек по желанию.*?\n        : '';", src, re.S)
    if not kinds or not плитка:
        eq("нашли в owner/index.html список видов и плитку", (bool(kinds), bool(плитка)), (True, True))
        return
    js = """
%s
const EXP_SHOT = {kind:'', photo:'', thumb:''};
const xsvg = () => '', XE_ICO = {cam:'', note:''}, XE_QR = '';
const escS = s => s, fmt = v => String(v), n = 'Худоба';
function зона(k){ const снят = false, бут = null;
%s
  return плитка; }
const o = EXP_KINDS.find(k => k.id === 'other');
console.log(JSON.stringify({
  вид: {receipt_opt: !!o.receipt_opt, receipt: !!o.receipt},
  у_другого: EXP_KINDS.filter(k => k.receipt_opt).map(k => k.id),
  плитка_other: зона(o).includes('rcp-other'),
  подпись_other: (зона(o).match(/xe-tile-c">([^<]*)/) || [])[1] || '',
  подпись_fuel: (зона(EXP_KINDS.find(k => k.id === 'fuel')).match(/xe-tile-c">([^<]*)/) || [])[1] || '',
  плитки_нет_у_kfc: зона(EXP_KINDS.find(k => k.id === 'kfc')) === ''}));
""" % (kinds.group(0), плитка.group(0))
    f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    f.write(js); f.close()
    r = subprocess.run(["node", f.name], capture_output=True, text=True)
    os.unlink(f.name)
    if r.returncode:
        eq("узел выполнил код STAR", r.stderr.strip()[:200], "")
        return
    v = json.loads(r.stdout)
    eq("в STAR у «что-то ещё» чек по желанию", v["вид"], {"receipt_opt": True, "receipt": False})
    eq("и больше ни у кого", v["у_другого"], ["other"])
    eq("плитка для чека рисуется", v["плитка_other"], True)
    eq("подпись — «Чек, если есть»", v["подпись_other"], "Чек, если есть")
    eq("у бензина подпись прежняя", v["подпись_fuel"], "Чек")
    eq("там, где чека не спрашивают, плитки нет", v["плитки_нет_у_kfc"], True)


async def main():
    db._db = AsyncMongoMockClient()["ambar_other_rcp"]

    print("── «что-то ещё»: чек не обязателен ────────────────────────────")
    st, r = await post({"kind": "other", "amount": 60, "comment": "запчасть", "pay": "cash"})
    eq("без чека — принято", (st, r.get("error")), (200, None))
    eq("снимка у записи нет", (r["item"].get("photo"), r["item"].get("thumb")), (None, None))
    без_чека = r["item"]["id"]

    print("── «что-то ещё»: чек, если он есть ────────────────────────────")
    st, r = await post({"kind": "other", "amount": 75, "comment": "KFC водителям",
                        "pay": "cash", "photo": PHOTO, "thumb": PHOTO})
    eq("с чеком — принято", (st, r.get("error")), (200, None))
    eq("снимок отмечен у записи", (r["item"].get("photo"), bool(r["item"].get("thumb"))), (True, True))
    с_чеком = r["item"]["id"]
    eq("сам снимок лежит в базе", bool(await db.expense_photo(с_чеком)), True)
    eq("а у записи без чека — не лежит", bool(await db.expense_photo(без_чека)), False)

    print("── обязательный чек не расшатали ──────────────────────────────")
    st, r = await post({"kind": "parking", "amount": 20, "comment": "Marina", "pay": "cash"})
    eq("парковка без чека — по-прежнему 400 no_photo", (st, r.get("error")), (400, "no_photo"))
    st, r = await post({"kind": "fuel", "amount": 100, "comment": "", "pay": "cash"})
    eq("бензин без чека — по-прежнему 400 no_photo", (st, r.get("error")), (400, "no_photo"))
    eq("«что-то ещё» сервер не требует", "other" in dr.MUST_RECEIPT, False)
    eq("а бензин, мойку и парковку — требует",
       sorted(dr.MUST_RECEIPT), ["fuel", "parking", "wash"])

    print("── что об этом знает приложение ───────────────────────────────")
    req = make_mocked_request("GET", "/x"); req["driver"] = ME; req["tg"] = {"id": 1}
    v = json.loads((await raw(dr.handle_expenses)(req)).text)
    eq("receipt_opt — только у «что-то ещё»",
       sorted(k["id"] for k in v["kinds"] if k.get("receipt_opt")), ["other"])
    eq("receipt — у бензина, мойки и парковки",
       sorted(k["id"] for k in v["kinds"] if k.get("receipt")), ["fuel", "parking", "wash"])
    eq("у «что-то ещё» receipt не стоит — иначе приложение потребует снимок",
       [k.get("receipt") for k in v["kinds"] if k["id"] == "other"], [False])
    eq("в таблице видов — тоже по желанию",
       (EXTRA_KINDS["other"].get("receipt_opt"), EXTRA_KINDS["other"].get("receipt")), (True, None))

    star()
    print(("\nПРОВАЛЫ: " + ", ".join(FAIL)) if FAIL else "\nвсё сошлось")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
