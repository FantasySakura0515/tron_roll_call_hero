"""Bot command handlers backed by the account supervisor (Phase 5.3).

``create_supervisor_bot_handlers`` produces a :class:`BotRuntimeHandlers`
bundle that routes every bot command to the live multi-account runtime:
``status`` reads real worker snapshots, ``start``/``stop`` control workers
through the supervisor, ``force`` runs an immediate poll on exactly one
worker, ``reauth`` drops only that worker's session cookies, and ``qr``
fans the payload out through the workers' own sessions.

Authorization, bindings, cooldowns, and audit stay in ``BotRuntime``; this
module never reads or mutates the global active profile. Replies and data are
built from sanitized snapshots/results only — no passwords, cookies, or raw
QR payloads.
"""

from __future__ import annotations

from typing import Any, Dict

from troTHU.application_runtime import MonitorApplication
from troTHU.bot_runtime import BotRuntimeHandlers
from troTHU.qr_fanout import submit_group_qr_payload


def _status_line(snapshot: Any) -> str:
    return "{}: {} (login: {}, polls: {}, last: {})".format(
        snapshot.profile,
        snapshot.phase,
        snapshot.login_status,
        snapshot.poll_count,
        snapshot.last_check_status or "-",
    )


def create_supervisor_bot_handlers(app: MonitorApplication) -> BotRuntimeHandlers:
    """Build bot handlers that control the given monitor application."""

    async def status(profile: str = "", state: str = "", command: Any = None, **_: Any) -> Dict[str, Any]:
        snapshot = app.snapshot_for(profile)
        if snapshot is None:
            return {"reply": "{}: no worker".format(profile), "phase": "absent"}
        return {"reply": _status_line(snapshot), **snapshot.to_dict()}

    async def accounts(**_: Any) -> Dict[str, Any]:
        report = app.status_report()
        lines = [
            "{}: {} ({})".format(item["profile"], item["phase"], item["login_status"])
            for item in report.get("accounts", [])
        ]
        return {"reply": "\n".join(lines) or "no accounts", **report}

    async def start(profile: str = "", command: Any = None, **_: Any) -> Dict[str, Any]:
        ok = await app.supervisor.start_account(profile) if app.supervisor is not None else False
        reply = "{} worker {}".format(profile, "started" if ok else "is not a desired account")
        return {"reply": reply, "worker_started": bool(ok)}

    async def stop(profile: str = "", command: Any = None, **_: Any) -> Dict[str, Any]:
        ok = await app.supervisor.stop_account(profile) if app.supervisor is not None else False
        reply = "{} worker {}".format(profile, "stopped" if ok else "was not running")
        return {"reply": reply, "worker_stopped": bool(ok)}

    async def force_check(profile: str = "", command: Any = None, admin: bool = False, **_: Any) -> Dict[str, Any]:
        worker = app.worker(profile)
        if worker is None:
            return {"reply": "{}: no worker".format(profile), "ok": False, "reason": "no_worker"}
        outcome = await worker.force_check()
        detail = outcome.get("decision") or outcome.get("reason") or "unknown"
        return {"reply": "{}: checked ({})".format(profile, detail), **outcome}

    async def reauth(profile: str = "", command: Any = None, admin: bool = False, **_: Any) -> Dict[str, Any]:
        worker = app.worker(profile)
        ok = worker.request_reauth() if worker is not None else False
        reply = "{}: reauth {}".format(profile, "requested" if ok else "unavailable")
        return {"reply": reply, "reauth_requested": bool(ok)}

    async def qr_submit(profile: str = "", payload: str = "", command: Any = None, **_: Any) -> Dict[str, Any]:
        fanout = bool(command.payload.get("fanout")) if command is not None else False
        profiles = None if fanout else [profile]
        group = await submit_group_qr_payload(app.supervisor, payload, profiles=profiles)
        lines = []
        for result in group.results:
            line = "{}: {}".format(result.profile, result.status.value)
            if result.error_code:
                line += " ({})".format(result.error_code)
            lines.append(line)
        reply = "\n".join(lines) or "no matching workers"
        return {"reply": reply, **group.to_dict()}

    return BotRuntimeHandlers(
        status=status,
        accounts=accounts,
        start=start,
        stop=stop,
        force_check=force_check,
        reauth=reauth,
        qr_submit=qr_submit,
    )
