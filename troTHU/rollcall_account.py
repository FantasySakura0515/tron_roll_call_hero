"""Account-scoped polling and progress (Phase 2.4).

Reuses the pure rollcall decision engine and the explicit progress fetcher, but
scopes them to one account: poll count and last progress live on
``account.state`` and ``my_user_no`` comes from ``account.spec.user`` (not a
guess from the active profile name). Completion is read from per-account state.

The legacy ``rollcall_runtime``/``rollcall_progress`` global paths are untouched.

This module does not directly import ``troTHU.runtime_context``. It does import
``rollcall_progress`` (the designated progress module), which loads the legacy
context transitively; the functions used here take explicit session/endpoints.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from troTHU.account_context import AccountContext
from troTHU.rollcall_engine import decide_rollcall
from troTHU.rollcall_progress import fetch_rollcall_progress as _fetch_progress
from troTHU.tron_http import TronHttpClient


def _request_ssl(account: AccountContext) -> Any:
    http = account.config.http if account.config is not None else {}
    verify = bool(http.get("verify_ssl", True)) if isinstance(http, Mapping) else True
    return False if not verify else None


async def poll_rollcall_decision(account: AccountContext):
    """Fetch this account's active rollcalls and decide what to do.

    Increments ``account.state.poll_count`` and returns a ``RollcallDecision``.
    """
    account.state.poll_count += 1
    client = TronHttpClient(account.session, request_ssl=_request_ssl(account), endpoints=account.endpoints)
    result = await client.fetch_rollcalls()
    payload = getattr(result, "payload", {})
    rollcalls = payload.get("rollcalls") if isinstance(payload, Mapping) else []
    return decide_rollcall(rollcalls if isinstance(rollcalls, list) else [])


async def fetch_account_progress(account: AccountContext, rollcall_id: Any) -> Dict[str, Any]:
    """Fetch check-in progress for this account and store it on its state."""
    summary = await _fetch_progress(
        account.session,
        rollcall_id,
        endpoints=account.endpoints,
        request_ssl=_request_ssl(account),
        my_user_no=account.spec.user,
    )
    if isinstance(summary, Mapping) and summary.get("ok"):
        account.state.last_progress = dict(summary)
    return summary


def account_completed(account: AccountContext, attendance_type: str, rollcall_id: Any) -> bool:
    rid = str(rollcall_id or "").strip()
    if not rid:
        return False
    kind = str(attendance_type or "").lower()
    state = account.state
    if kind == "number":
        return rid in state.completed_number
    if kind == "radar":
        return rid in state.completed_radar
    if kind in ("qr", "qrcode"):
        return rid in state.completed_qr
    return False
