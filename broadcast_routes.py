"""
Self-serve broadcast — owner sends promos to customers straight from ambar star.

Same language logic as broadcast_promo.py / broadcast_nudge.py:
  • language_code ru*                → Russian text + RU image
  • language_code en* / other / none → English text + EN image
(no-language customers get the English version — never skipped)

Endpoints (all owner-authenticated, mounted by setup(app) from api_server.main):
  GET  /api/owner/broadcast/audience            → reach counts per target (live preview)
  POST /api/owner/broadcast/upload   (multipart)→ save an image, returns {url}
  POST /api/owner/broadcast/test                → send ONE message to the owner (preview)
  POST /api/owner/broadcast/send                → start a background blast, returns {job_id}
  GET  /api/owner/broadcast/status?job_id=       → live counters (sent/skipped/failed/…)
  GET  /api/owner/broadcast/recent               → last broadcasts (history + stats)

Sending is server-side via the CUSTOMER bot (BOT_TOKEN), ~20 msg/s, with a
per-job sent-set so a chat is never messaged twice. Text is sent as plain text
(no Markdown parsing) so anything the owner types is delivered verbatim.
"""
import os, json, uuid, asyncio, logging
from pathlib import Path
from datetime import datetime, timezone

import aiohttp
from aiohttp import web

import db
from owner_auth import require_owner, CORS_HEADERS

log = logging.getLogger("broadcast")

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
WEBAPP_URL  = os.getenv("WEBAPP_URL", "https://ambar-delivery.com/")
STATIC_DIR  = Path(__file__).parent

TARGETS = ("all", "ru", "en", "nolang", "vip")

# Live job registry (in-memory): job_id -> job dict. Status reads here while a
# blast runs; the DB doc (via _persist) is the durable copy for history/restart.
_JOBS: dict = {}


# ── language + targeting ────────────────────────────────────────────────────
# Язык телеграма — догадка, а не факт. Человек может сидеть в английском
# интерфейсе и читать по-русски: такому английская рассылка приходит как
# «не моё», хотя он наш постоянный клиент.
#
# Поэтому есть список тех, кому всегда идёт русский, чем бы ни был подписан их
# телеграм. Он живёт в .env (BROADCAST_RU_IDS, id через запятую), а не в коде:
# репозиторий открытый, и telegram id клиента в нём лежать не должен.
FORCE_RU_IDS = {int(x.strip()) for x in os.getenv("BROADCAST_RU_IDS", "").split(",")
                if x.strip().isdigit()}


def _bucket(u: dict) -> str:
    """Русский или английский для этого получателя.

    Берёт пользователя целиком, а не один language_code: решение зависит и от
    того, кто это. Список исключений сильнее языка телеграма — он и заведён
    ровно для случаев, когда телеграм говорит одно, а человек читает другое."""
    try:
        if int(u.get("telegram_id") or 0) in FORCE_RU_IDS:
            return "ru"
    except (TypeError, ValueError):
        pass
    lc = (u.get("language_code") or "").strip().lower()
    return "ru" if lc.startswith("ru") else "en"   # everyone else → English


def _is_vip(u: dict) -> bool:
    try:
        from owner_routes import _card_for_user
        return _card_for_user(u).get("type") != "standard"
    except Exception:
        return False


def _match_target(u: dict, bucket: str, target: str) -> bool:
    if target == "all":    return True
    if target == "ru":     return bucket == "ru"
    if target == "en":     return bucket == "en"
    if target == "nolang": return not (u.get("language_code") or "").strip()
    if target == "vip":    return _is_vip(u)
    return True


def _btn(bucket: str) -> dict:
    label = "🔑  Открыть AMBAR" if bucket == "ru" else "🔑  Open AMBAR"
    return {"inline_keyboard": [[{"text": label, "web_app": {"url": WEBAPP_URL}}]]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_path(url: str):
    """Map a returned /uploads/... URL back to a safe on-disk path."""
    if not url:
        return None
    p = (STATIC_DIR / url.lstrip("/")).resolve()
    try:
        p.relative_to((STATIC_DIR / "uploads").resolve())
    except ValueError:
        return None
    return p


# ── Telegram senders (customer bot) ─────────────────────────────────────────
async def _tg(sess, method: str, payload: dict) -> dict:
    async with sess.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload) as r:
        return await r.json()


async def _tg_photo_file(sess, chat_id, path: Path, caption: str, kb: dict) -> dict:
    data = aiohttp.FormData()
    data.add_field("chat_id", str(chat_id))
    if caption:
        data.add_field("caption", caption[:1024])
    data.add_field("reply_markup", json.dumps(kb))
    with open(path, "rb") as f:
        data.add_field("photo", f, filename="promo.jpg", content_type="image/jpeg")
        async with sess.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data) as r:
            return await r.json()


async def _send_one(sess, chat_id, text: str, img_url: str, file_ids: dict, bucket: str) -> bool:
    """Send one promo. Reuses a file_id per bucket after the first upload."""
    kb = _btn(bucket)
    if img_url:
        fid = file_ids.get(bucket)
        if fid:
            r = await _tg(sess, "sendPhoto", {"chat_id": chat_id, "photo": fid,
                                              "caption": text[:1024], "reply_markup": kb})
        else:
            path = _local_path(img_url)
            if path and path.exists():
                r = await _tg_photo_file(sess, chat_id, path, text, kb)
            else:  # image vanished from disk → don't drop the send, go text-only
                r = await _tg(sess, "sendMessage", {"chat_id": chat_id, "text": text or "AMBAR",
                                                    "reply_markup": kb})
        if r.get("ok"):
            photos = (r.get("result") or {}).get("photo")
            if photos:
                file_ids[bucket] = photos[-1]["file_id"]
            return True
        return False
    else:
        r = await _tg(sess, "sendMessage", {"chat_id": chat_id, "text": text or "AMBAR",
                                            "reply_markup": kb, "disable_web_page_preview": True})
        return bool(r.get("ok"))


# ── background blast ────────────────────────────────────────────────────────
async def _persist(job: dict):
    keep = ("job_id", "target", "created_by", "created_at", "state", "total", "reach",
            "sent", "skipped_banned", "skipped_filter", "failed", "by_ru", "by_en",
            "done_at", "ru_text", "en_text", "ru_image", "en_image")
    try:
        await db.save_broadcast({k: job.get(k) for k in keep})
    except Exception as e:
        log.error(f"[broadcast] persist failed: {e}")


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "sent_set"}


async def _run_job(job_id: str):
    job = _JOBS[job_id]
    try:
        users = await db.get_all_customers()
        job["total"] = len(users)
        # reach = matching, reachable customers (drives the progress bar)
        reach = 0
        for u in users:
            if u.get("telegram_id") and not u.get("is_banned") and \
               _match_target(u, _bucket(u), job["target"]):
                reach += 1
        job["reach"] = reach
        await _persist(job)

        async with aiohttp.ClientSession() as sess:
            file_ids: dict = {}
            for u in users:
                if job.get("cancel"):
                    break
                tid = u.get("telegram_id")
                if not tid:
                    continue
                if u.get("is_banned"):
                    job["skipped_banned"] += 1
                    continue
                bucket = _bucket(u)
                if not _match_target(u, bucket, job["target"]):
                    job["skipped_filter"] += 1
                    continue
                if tid in job["sent_set"]:
                    continue
                text = job["ru_text"] if bucket == "ru" else job["en_text"]
                # Русский из списка исключений может попасть в рассылку, где
                # русский текст не заполняли вовсе (цель «en» или «без языка»).
                # Отправить ему пустоту хуже, чем отправить на другом языке.
                text = text or job["en_text"] or job["ru_text"]
                img  = (job["ru_image"] if bucket == "ru" else job["en_image"]) \
                    or job["en_image"] or job["ru_image"]
                try:
                    ok = await _send_one(sess, tid, text, img, file_ids, bucket)
                except Exception as e:
                    ok = False
                    log.debug(f"[broadcast] send {tid} error: {e}")
                if ok:
                    job["sent"] += 1
                    job["by_ru" if bucket == "ru" else "by_en"] += 1
                    job["sent_set"].add(tid)
                else:
                    job["failed"] += 1
                if (job["sent"] + job["failed"]) % 20 == 0:
                    await _persist(job)
                await asyncio.sleep(0.05)   # ~20/s, under Telegram's broadcast limit

        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        log.error(f"[broadcast] job {job_id} crashed: {e}")
    job["done_at"] = _now()
    await _persist(job)


# ── handlers ────────────────────────────────────────────────────────────────
@require_owner
async def handle_bcast_audience(request):
    users = await db.get_all_customers()
    c = {"total": len(users), "all": 0, "ru": 0, "en": 0, "nolang": 0, "vip": 0,
         "banned": 0, "nochat": 0}
    for u in users:
        if not u.get("telegram_id"):
            c["nochat"] += 1
            continue
        if u.get("is_banned"):
            c["banned"] += 1
            continue
        c["all"] += 1
        b = _bucket(u)
        c["ru" if b == "ru" else "en"] += 1
        if not (u.get("language_code") or "").strip():
            c["nolang"] += 1
        if _is_vip(u):
            c["vip"] += 1
    return web.json_response(c, headers=CORS_HEADERS)


@require_owner
async def handle_bcast_upload(request):
    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != "image":
        return web.json_response({"error": "no image field"}, status=400, headers=CORS_HEADERS)
    updir = STATIC_DIR / "uploads" / "broadcast"
    updir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:12]}.jpg"
    fpath = updir / fname
    size = 0
    with open(fpath, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            if size > 8 * 1024 * 1024:   # 8 MB cap
                f.close()
                try: fpath.unlink()
                except OSError: pass
                return web.json_response({"error": "too large (max 8MB)"}, status=413, headers=CORS_HEADERS)
            f.write(chunk)
    return web.json_response({"url": f"/uploads/broadcast/{fname}"}, headers=CORS_HEADERS)


@require_owner
async def handle_bcast_test(request):
    body = await request.json()
    owner_id = request["owner_id"]
    bucket = body.get("bucket") if body.get("bucket") in ("ru", "en") else "ru"
    ru_text = (body.get("ru_text") or "").strip()
    en_text = (body.get("en_text") or "").strip()
    text = ru_text if bucket == "ru" else en_text
    img = (body.get("ru_image") if bucket == "ru" else body.get("en_image")) \
        or body.get("en_image") or body.get("ru_image") or ""
    if not text and not img:
        return web.json_response({"error": "nothing to send in this language"},
                                 status=400, headers=CORS_HEADERS)
    async with aiohttp.ClientSession() as sess:
        ok = await _send_one(sess, owner_id, text, img, {}, bucket)
    return web.json_response({"ok": bool(ok)}, headers=CORS_HEADERS)


@require_owner
async def handle_bcast_send(request):
    # One blast at a time — refuse to start a second concurrent send.
    if any(j.get("state") == "sending" for j in _JOBS.values()):
        return web.json_response({"error": "already_sending"}, status=409, headers=CORS_HEADERS)

    body = await request.json()
    ru_text = (body.get("ru_text") or "").strip()
    en_text = (body.get("en_text") or "").strip()
    ru_image = body.get("ru_image") or ""
    en_image = body.get("en_image") or ""
    target = body.get("target") if body.get("target") in TARGETS else "all"

    need_ru = target in ("all", "ru", "vip")
    need_en = target in ("all", "en", "nolang", "vip")
    if need_ru and not (ru_text or ru_image):
        return web.json_response({"error": "ru_empty"}, status=400, headers=CORS_HEADERS)
    if need_en and not (en_text or en_image):
        return web.json_response({"error": "en_empty"}, status=400, headers=CORS_HEADERS)
    for t in (ru_text, en_text):
        if len(t) > 4096:
            return web.json_response({"error": "text_too_long"}, status=400, headers=CORS_HEADERS)
    if (ru_image or en_image):
        for t in (ru_text, en_text):
            if len(t) > 1024:
                return web.json_response({"error": "caption_too_long"}, status=400, headers=CORS_HEADERS)

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id, "target": target,
        "ru_text": ru_text, "en_text": en_text,
        "ru_image": ru_image, "en_image": en_image,
        "created_by": request["owner_id"], "created_at": _now(),
        "state": "sending", "total": 0, "reach": 0,
        "sent": 0, "skipped_banned": 0, "skipped_filter": 0, "failed": 0,
        "by_ru": 0, "by_en": 0, "done_at": "",
        "sent_set": set(),
    }
    _JOBS[job_id] = job
    await _persist(job)
    asyncio.ensure_future(_run_job(job_id))
    return web.json_response({"job_id": job_id}, headers=CORS_HEADERS)


@require_owner
async def handle_bcast_status(request):
    jid = request.query.get("job_id", "")
    job = _JOBS.get(jid)
    if job:
        return web.json_response(_public(job), headers=CORS_HEADERS)
    doc = await db.get_broadcast(jid)
    if not doc:
        return web.json_response({"error": "not found"}, status=404, headers=CORS_HEADERS)
    return web.json_response(doc, headers=CORS_HEADERS)


@require_owner
async def handle_bcast_recent(request):
    docs = await db.get_recent_broadcasts(10)
    return web.json_response({"items": docs}, headers=CORS_HEADERS)


def _opt(request):
    return web.Response(status=200, headers=CORS_HEADERS)


def setup(app):
    """Mount broadcast routes. Called from api_server.main()."""
    r = app.router
    r.add_route("OPTIONS", "/api/owner/broadcast/audience", _opt)
    r.add_get("/api/owner/broadcast/audience", handle_bcast_audience)
    r.add_route("OPTIONS", "/api/owner/broadcast/upload", _opt)
    r.add_post("/api/owner/broadcast/upload", handle_bcast_upload)
    r.add_route("OPTIONS", "/api/owner/broadcast/test", _opt)
    r.add_post("/api/owner/broadcast/test", handle_bcast_test)
    r.add_route("OPTIONS", "/api/owner/broadcast/send", _opt)
    r.add_post("/api/owner/broadcast/send", handle_bcast_send)
    r.add_route("OPTIONS", "/api/owner/broadcast/status", _opt)
    r.add_get("/api/owner/broadcast/status", handle_bcast_status)
    r.add_route("OPTIONS", "/api/owner/broadcast/recent", _opt)
    r.add_get("/api/owner/broadcast/recent", handle_bcast_recent)
    log.info("[broadcast] routes mounted")
