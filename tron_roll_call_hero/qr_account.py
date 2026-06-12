"""Account-scoped student QR execution (Phase 2.7).

Submits a manually supplied QR payload for one explicit
:class:`AccountContext`: the account's session and endpoints carry the request,
completion lands in ``account.state.completed_qr``, and the pending-QR registry
is touched only for this account's profile/provider pair. The raw QR payload
never reaches state snapshots, events, or results.

Teacher-assisted QR stays on the legacy single-account adapter until Phase 4.
The legacy global path ``qr_runtime.submit_qr_payload`` is untouched.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import ssl
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import aiohttp

    _NETWORK_ERRORS: tuple = (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError)
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tests require aiohttp
    aiohttp = None
    _NETWORK_ERRORS = (asyncio.TimeoutError, ssl.SSLError)

from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import AttendanceType, SubmissionResult, SubmissionStatus
from tron_roll_call_hero.pending_qr import remove_pending_qr
from tron_roll_call_hero.qr_rollcall import answer_qr_rollcall, parse_qr_payload
from tron_roll_call_hero.rollcall_account import account_completed, fetch_account_progress
from tron_roll_call_hero.runtime_events import account_event
from tron_roll_call_hero.tron_http import UnauthorizedError, UnexpectedResponseError


def _request_ssl(account: AccountContext) -> Any:
    http = account.config.http if account.config is not None else {}
    verify = bool(http.get("verify_ssl", True)) if isinstance(http, Mapping) else True
    return False if not verify else None


def _emit(account: AccountContext, status: str, rollcall_id: str) -> None:
    services = account.services
    events = getattr(services, "events", None) if services is not None else None
    if events is not None:
        events.emit(
            account_event(
                "qr_submission",
                profile=account.spec.profile,
                provider_key=account.spec.provider_key,
                status=status,
                rollcall_id=rollcall_id,
                attendance_type="qr",
            )
        )


async def submit_qr_payload_account(
    account: AccountContext,
    raw_payload: str,
    *,
    pending_dir: Optional[Path] = None,
) -> SubmissionResult:
    """Parse and submit a manual QR payload for one account."""
    try:
        qr_data = parse_qr_payload(str(raw_payload or ""), base_url=account.endpoints.base_url)
    except ValueError:
        qr_data = None
    if qr_data is None or not str(qr_data.rollcall_id or "").strip():
        _emit(account, SubmissionStatus.FAILED.value, "")
        return SubmissionResult(
            profile=account.profile,
            provider_key=account.provider_key,
            rollcall_id="",
            attendance_type=AttendanceType.QR,
            status=SubmissionStatus.FAILED,
            error_code="invalid_payload",
        )
    return await submit_parsed_qr_account(account, qr_data, pending_dir=pending_dir)


async def submit_parsed_qr_account(
    account: AccountContext,
    qr_data: Any,
    *,
    pending_dir: Optional[Path] = None,
) -> SubmissionResult:
    """Submit an already-parsed QR payload for one account and report the outcome."""
    rid = str(qr_data.rollcall_id or "").strip()

    def _result(status: SubmissionStatus, rid: str, error_code: str = "") -> SubmissionResult:
        _emit(account, status.value, rid)
        return SubmissionResult(
            profile=account.profile,
            provider_key=account.provider_key,
            rollcall_id=rid,
            attendance_type=AttendanceType.QR,
            status=status,
            error_code=error_code,
        )

    if not rid:
        return _result(SubmissionStatus.FAILED, "", error_code="invalid_payload")

    if account_completed(account, "qr", rid):
        return _result(SubmissionStatus.SKIPPED_ALREADY_COMPLETE, rid)

    try:
        await answer_qr_rollcall(
            account.session,
            qr_data,
            device_id=uuid.uuid4().hex,
            request_ssl=_request_ssl(account),
            base_url=account.endpoints.base_url,
        )
    except UnauthorizedError:
        return _result(SubmissionStatus.FAILED, rid, error_code="unauthorized")
    except UnexpectedResponseError:
        return _result(SubmissionStatus.FAILED, rid, error_code="unexpected_response")
    except _NETWORK_ERRORS:
        return _result(SubmissionStatus.FAILED, rid, error_code="transient")

    if pending_dir is not None:
        remove_pending_qr(
            pending_dir,
            profile=account.spec.profile,
            rollcall_id=rid,
            provider=account.spec.provider_key,
        )

    summary = await fetch_account_progress(account, rid)
    if isinstance(summary, Mapping) and summary.get("confirmed_present"):
        account.state.completed_qr.add(rid)
        return _result(SubmissionStatus.CONFIRMED, rid)
    return _result(SubmissionStatus.SUBMITTED_UNCONFIRMED, rid)
