"""Local monitoring dashboard (dashboard spec 2026-06-12).

``register_dashboard_routes`` mounts a token-protected single-page dashboard
and its JSON API onto the same aiohttp application that serves the bot
webhooks. Every response is built from sanitized snapshots, events, and
submission results only; the QR payload is never echoed back.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import secrets
from typing import Any, Callable, Mapping, Optional, Tuple

try:  # pragma: no cover - exercised through tests when aiohttp is present
    from aiohttp import web
except Exception:  # pragma: no cover
    web = None  # type: ignore[assignment]

from tron_roll_call_hero.qr_fanout import submit_group_qr_payload

DASHBOARD_HTML = "<!doctype html><title>placeholder</title>"


def generate_dashboard_token() -> str:
    return secrets.token_urlsafe(16)


def attach_dashboard(
    monitor_app: Any,
    event_store: Any,
    *,
    host: str,
    port: int,
    token: Optional[str] = None,
) -> Tuple[Callable[[Any], None], str, str]:
    """Build the (configure_app, token, url) bundle for the adapter server."""
    final_token = str(token or "") or generate_dashboard_token()

    def configure(app: Any) -> None:
        register_dashboard_routes(app, monitor_app, event_store, token=final_token)

    url = "http://{}:{}/dashboard?token={}".format(host, port, final_token)
    return configure, final_token, url


def register_dashboard_routes(
    app: Any,
    monitor_app: Any,
    event_store: Any,
    *,
    token: str,
) -> None:
    if web is None:
        raise RuntimeError("aiohttp.web is required for the dashboard")
    if not str(token or ""):
        raise ValueError("dashboard token must not be empty")

    def _authorized(request: Any) -> bool:
        provided = request.query.get("token") or request.headers.get("X-Dashboard-Token") or ""
        return secrets.compare_digest(str(provided), str(token))

    def _deny():
        return web.json_response({"ok": False, "error": "invalid dashboard token"}, status=401)

    async def _json_body(request: Any) -> Optional[Mapping[str, Any]]:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, Mapping) else None

    def _int_query(request: Any, name: str, default: int) -> int:
        try:
            return int(request.query.get(name, default))
        except (TypeError, ValueError):
            return default

    async def page(request):
        if not _authorized(request):
            return _deny()
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def status(request):
        if not _authorized(request):
            return _deny()
        return web.json_response(monitor_app.status_report())

    async def events(request):
        if not _authorized(request):
            return _deny()
        return web.json_response({"events": event_store.recent(_int_query(request, "limit", 50))})

    async def stats(request):
        if not _authorized(request):
            return _deny()
        return web.json_response(event_store.stats(_int_query(request, "days", 7)))

    async def force(request):
        if not _authorized(request):
            return _deny()
        payload = await _json_body(request)
        if payload is None:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        profile = str(payload.get("profile") or "")
        supervisor = monitor_app.supervisor
        if supervisor is None:
            return web.json_response({"ok": False, "error": "supervisor_not_running"}, status=409)
        if profile.strip().lower() == "all":
            results = []
            for running in supervisor.running_profiles():
                worker = monitor_app.worker(running)
                if worker is None:
                    continue
                outcome = await worker.force_check()
                results.append({"profile": running, **outcome})
            ok = bool(results) and all(item.get("ok") for item in results)
            return web.json_response({"ok": ok, "results": results})
        worker = monitor_app.worker(profile)
        if worker is None:
            return web.json_response(
                {"ok": False, "error": "no_worker", "profile": profile}, status=404
            )
        outcome = await worker.force_check()
        return web.json_response({"profile": profile, **outcome})

    async def reauth(request):
        if not _authorized(request):
            return _deny()
        payload = await _json_body(request)
        if payload is None:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        profile = str(payload.get("profile") or "")
        worker = monitor_app.worker(profile)
        if worker is None:
            return web.json_response(
                {"ok": False, "error": "no_worker", "profile": profile}, status=404
            )
        requested = bool(worker.request_reauth())
        return web.json_response(
            {"ok": requested, "profile": profile, "reauth_requested": requested}
        )

    async def qr(request):
        if not _authorized(request):
            return _deny()
        payload = await _json_body(request)
        if payload is None:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        raw = str(payload.get("payload") or "")
        profiles = payload.get("profiles")
        wanted = [str(item) for item in profiles] if isinstance(profiles, list) else None
        group = await submit_group_qr_payload(monitor_app.supervisor, raw, profiles=wanted)
        return web.json_response(group.to_dict())

    app.router.add_get("/dashboard", page)
    app.router.add_get("/dashboard/api/status", status)
    app.router.add_get("/dashboard/api/events", events)
    app.router.add_get("/dashboard/api/stats", stats)
    app.router.add_post("/dashboard/api/force", force)
    app.router.add_post("/dashboard/api/reauth", reauth)
    app.router.add_post("/dashboard/api/qr", qr)
