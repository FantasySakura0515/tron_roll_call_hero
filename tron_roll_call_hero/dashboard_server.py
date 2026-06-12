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

DASHBOARD_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tron-roll-call-hero dashboard</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
         background: #111418; color: #e6e6e6; margin: 0; padding: 1.5rem; }
  h1 { font-size: 1.1rem; margin: 0 0 1rem; }
  h2 { font-size: .95rem; margin: 1.5rem 0 .5rem; color: #9ad; }
  .cards { display: flex; flex-wrap: wrap; gap: .8rem; }
  .card { border: 1px solid #2a3038; border-radius: 8px; padding: .8rem 1rem;
          min-width: 220px; background: #171b21; }
  .card .profile { font-weight: 700; }
  .dot { display: inline-block; width: .6rem; height: .6rem; border-radius: 50%;
         margin-right: .4rem; vertical-align: baseline; }
  .dot.green { background: #4caf50; } .dot.yellow { background: #d8b021; }
  .dot.red { background: #e05252; }
  .meta { font-size: .8rem; color: #9aa3ad; margin-top: .3rem; }
  button { background: #243042; color: #dce6f0; border: 1px solid #33415a;
           border-radius: 5px; padding: .25rem .6rem; cursor: pointer; margin-right: .4rem;
           font: inherit; font-size: .8rem; }
  button:hover { background: #2e3d55; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  th, td { border-bottom: 1px solid #2a3038; padding: .35rem .5rem; text-align: left; }
  th { color: #9aa3ad; font-weight: 600; }
  textarea { width: 100%; min-height: 4rem; background: #171b21; color: #e6e6e6;
             border: 1px solid #2a3038; border-radius: 6px; padding: .5rem; font: inherit; }
  select { background: #171b21; color: #e6e6e6; border: 1px solid #2a3038;
           border-radius: 5px; padding: .25rem; font: inherit; }
  #qr-results, #action-result { font-size: .85rem; color: #9ad; white-space: pre-line;
                                margin-top: .5rem; }
</style>
</head>
<body>
<h1>tron-roll-call-hero — 多帳號監控儀表板</h1>

<h2>帳號</h2>
<div class="cards" id="cards"></div>
<div id="action-result"></div>

<h2>QR 提交</h2>
<textarea id="qr-payload" placeholder="貼上 QR payload"></textarea>
<div style="margin-top:.5rem">
  目標 <select id="qr-target"><option value="">全部</option></select>
  <button id="qr-send">送出</button>
</div>
<div id="qr-results"></div>

<h2>今日點名事件</h2>
<table>
  <thead><tr><th>時間</th><th>事件</th><th>rollcall</th><th>帳號</th><th>狀態</th></tr></thead>
  <tbody id="events"></tbody>
</table>

<h2>近 7 天統計</h2>
<table>
  <thead><tr><th>帳號</th><th>confirmed</th><th>submitted</th><th>failed</th><th>skipped</th><th>最後成功</th></tr></thead>
  <tbody id="stats"></tbody>
</table>

<script>
(function () {
  var token = new URLSearchParams(location.search).get("token") || "";
  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({"X-Dashboard-Token": token}, options.headers || {});
    return fetch(path, options).then(function (res) { return res.json(); });
  }
  function phaseColor(phase, healthy) {
    if (!healthy || phase === "login_failed" || phase === "crashed") return "red";
    if (phase === "monitoring") return "green";
    return "yellow";
  }
  function esc(value) {
    var div = document.createElement("div");
    div.textContent = String(value == null ? "" : value);
    return div.innerHTML;
  }
  function act(path, profile) {
    api(path, {method: "POST",
               headers: {"Content-Type": "application/json"},
               body: JSON.stringify({profile: profile})})
      .then(function (body) {
        document.getElementById("action-result").textContent = JSON.stringify(body);
        refresh();
      });
  }
  function renderCards(report) {
    var cards = document.getElementById("cards");
    var target = document.getElementById("qr-target");
    var selected = target.value;
    cards.innerHTML = "";
    target.innerHTML = '<option value="">全部</option>';
    (report.accounts || []).forEach(function (acc) {
      var card = document.createElement("div");
      card.className = "card";
      card.innerHTML =
        '<div class="profile"><span class="dot ' + phaseColor(acc.phase, acc.healthy) + '"></span>' +
        esc(acc.profile) + "</div>" +
        '<div class="meta">phase: ' + esc(acc.phase) + " / login: " + esc(acc.login_status) + "</div>" +
        '<div class="meta">polls: ' + esc(acc.poll_count) + " / err: " + esc(acc.last_error_code || "-") + "</div>" +
        '<div style="margin-top:.5rem"></div>';
      var row = card.lastChild;
      var force = document.createElement("button");
      force.textContent = "Force";
      force.onclick = function () { act("/dashboard/api/force", acc.profile); };
      var reauth = document.createElement("button");
      reauth.textContent = "Reauth";
      reauth.onclick = function () { act("/dashboard/api/reauth", acc.profile); };
      row.appendChild(force);
      row.appendChild(reauth);
      cards.appendChild(card);
      var option = document.createElement("option");
      option.value = acc.profile;
      option.textContent = acc.profile;
      target.appendChild(option);
    });
    target.value = selected;
  }
  function renderEvents(body) {
    var rows = (body.events || []).map(function (ev) {
      var when = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString() : "-";
      return "<tr><td>" + esc(when) + "</td><td>" + esc(ev.event) + "</td><td>" +
        esc(ev.rollcall_id || "-") + "</td><td>" + esc(ev.profile) + "</td><td>" +
        esc(ev.status) + "</td></tr>";
    });
    document.getElementById("events").innerHTML = rows.join("");
  }
  function renderStats(body) {
    var accounts = body.accounts || {};
    var rows = Object.keys(accounts).sort().map(function (profile) {
      var entry = accounts[profile];
      var last = entry.last_confirmed_ts
        ? new Date(entry.last_confirmed_ts * 1000).toLocaleString() : "-";
      return "<tr><td>" + esc(profile) + "</td><td>" + esc(entry.confirmed) + "</td><td>" +
        esc(entry.submitted_unconfirmed) + "</td><td>" + esc(entry.failed) + "</td><td>" +
        esc(entry.skipped) + "</td><td>" + esc(last) + "</td></tr>";
    });
    document.getElementById("stats").innerHTML = rows.join("");
  }
  function refresh() {
    api("/dashboard/api/status").then(renderCards);
    api("/dashboard/api/events?limit=50").then(renderEvents);
    api("/dashboard/api/stats?days=7").then(renderStats);
  }
  document.getElementById("qr-send").onclick = function () {
    var profile = document.getElementById("qr-target").value;
    api("/dashboard/api/qr", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({payload: document.getElementById("qr-payload").value,
                              profiles: profile ? [profile] : null})})
      .then(function (body) {
        var lines = (body.results || []).map(function (item) {
          return item.profile + ": " + item.status + (item.error_code ? " (" + item.error_code + ")" : "");
        });
        document.getElementById("qr-results").textContent = lines.join("\\n") || JSON.stringify(body);
      });
  };
  refresh();
  setInterval(refresh, 3000);
})();
</script>
</body>
</html>
"""


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
