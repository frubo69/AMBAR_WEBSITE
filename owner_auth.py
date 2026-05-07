"""
Auth guard for /api/owner/* endpoints.

Design notes
------------
- Reuses Telegram initData HMAC validation (same as the customer routes).
- The validator function is injected by api_server at startup rather than
  imported here, to avoid a circular import.
- On success, attaches `request["owner_id"]` and `request["owner_user"]`
  so handlers can use them without re-parsing.
- Errors return JSON with CORS headers so browser fetches surface the status
  instead of being blocked by preflight mismatch.
- Two distinct denial states:
    "blocked"      → user was explicitly denied by an owner (deny screen
                     shows "Доступ закрыт")
    "not_approved" → user is unknown — first attempt is logged + the
                     owners are pinged via @ambar_manage_bot in Russian
                     with full Telegram metadata
"""
from functools import wraps
from aiohttp import web
import logging

from config import OWNER_IDS, MANAGER_IDS
import db

log = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
}

# Populated by api_server.main() at startup via install_validator().
_validator = None
# Populated by owner_routes.setup() to break a circular import — the auth
# layer can fire owner-bot Telegram alerts without depending on the routes
# module at import time.
_alerter = None


def install_validator(fn):
    """Inject the initData validator (dict|None = fn(init_data_str))."""
    global _validator
    _validator = fn


def install_alerter(fn):
    """Inject an async callable: await fn(user_dict, request_meta_dict).
    Called once per unauthorized user per 24h to notify owners."""
    global _alerter
    _alerter = fn


def _request_meta(request) -> dict:
    """Extract the headers we want to surface in the security alert."""
    return {
        "path":       request.path,
        "method":     request.method,
        "ip":         request.headers.get("X-Forwarded-For", "")
                       or (request.remote or ""),
        "user_agent": request.headers.get("User-Agent", "")[:200],
    }


def require_owner(handler):
    """Decorator: only allow Telegram users in OWNER_IDS, MANAGER_IDS, or
    the DB-managed owner_managers list (and not blocked).

    On denial:
      * blocked user      → 403 {"error": "blocked"}
      * everyone else     → 401 {"error": "not_approved"} + log + alert"""
    @wraps(handler)
    async def wrapped(request):
        # Preflight: answer OPTIONS without auth.
        if request.method == "OPTIONS":
            return web.Response(status=200, headers=CORS_HEADERS)

        if _validator is None:
            return web.json_response(
                {"error": "validator not installed"},
                status=500, headers=CORS_HEADERS,
            )

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("tma "):
            return web.json_response(
                {"error": "unauthorized"},
                status=401, headers=CORS_HEADERS,
            )

        user = _validator(auth[4:])
        if not user:
            return web.json_response(
                {"error": "invalid initData"},
                status=401, headers=CORS_HEADERS,
            )

        uid = user.get("id")

        # Explicit blocks come first — owner can revoke access for someone
        # who was previously approved without losing the audit row.
        if await db.is_manager_blocked(uid) or await db.is_access_blocked(uid):
            return web.json_response(
                {"error": "blocked"},
                status=403, headers=CORS_HEADERS,
            )

        # Allow-list: env owners, env managers, DB managers (not blocked).
        if uid in OWNER_IDS or uid in MANAGER_IDS or await db.is_manager(uid):
            request["owner_id"]   = uid
            request["owner_user"] = user
            request["is_owner"]   = uid in OWNER_IDS
            return await handler(request)

        # Unknown user: log the attempt and alert owners (throttled).
        meta = _request_meta(request)
        try:
            res = await db.log_access_attempt(
                user, request_path=meta["path"],
                ip=meta["ip"], ua=meta["user_agent"],
            )
            if res.get("is_first_in_window") and _alerter:
                try:
                    await _alerter(user, meta, res.get("doc") or {})
                except Exception as e:
                    log.error(f"[owner-auth] alert failed for {uid}: {e}")
        except Exception as e:
            log.error(f"[owner-auth] log_access_attempt failed for {uid}: {e}")

        return web.json_response(
            {"error": "not_approved"},
            status=401, headers=CORS_HEADERS,
        )
    return wrapped
