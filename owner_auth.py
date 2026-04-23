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
"""
from functools import wraps
from aiohttp import web

from config import OWNER_IDS, MANAGER_IDS

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
}

# Populated by api_server.main() at startup via install_validator().
_validator = None


def install_validator(fn):
    """Inject the initData validator (dict|None = fn(init_data_str))."""
    global _validator
    _validator = fn


def require_owner(handler):
    """Decorator: only allow requests whose Telegram user is in OWNER_IDS
    or MANAGER_IDS. Managers have the same access as owners here — the
    distinction is only meaningful if we later add owner-only mutations
    (e.g. firing a manager). Until then both roles share one gate."""
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
        if uid not in OWNER_IDS and uid not in MANAGER_IDS:
            return web.json_response(
                {"error": "forbidden"},
                status=403, headers=CORS_HEADERS,
            )

        request["owner_id"]   = uid
        request["owner_user"] = user
        request["is_owner"]   = uid in OWNER_IDS
        return await handler(request)
    return wrapped
