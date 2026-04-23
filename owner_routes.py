"""
Routes for the owner dashboard (mini-app at /owner/).

Phase 1 surfaces only /api/owner/ping — a tiny auth-guarded smoke test.
Subsequent phases add overview/money/people/catalog endpoints here.

Register with:
    from owner_routes import setup as setup_owner_routes
    setup_owner_routes(app)
"""
import time

from aiohttp import web

from owner_auth import require_owner, CORS_HEADERS


@require_owner
async def handle_ping(request):
    """Cheap health + identity check. Returns the authenticated owner's id."""
    return web.json_response(
        {
            "ok": True,
            "owner_id": request["owner_id"],
            "server_time": int(time.time()),
        },
        headers=CORS_HEADERS,
    )


def setup(app):
    """Wire owner routes into the aiohttp app. Called from api_server.main()."""
    app.router.add_route("OPTIONS", "/api/owner/ping", handle_ping)
    app.router.add_get(             "/api/owner/ping", handle_ping)
