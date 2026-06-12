"""Manual QR fan-out through account workers (Phase 4.1).

The raw QR payload is parsed exactly once, then routed to every matching
running worker; each worker submits and verifies with its own session and
state. The aggregated :class:`GroupSubmissionResult` carries one per-profile
result, so partial failures stay attributable. The raw payload and its ``data``
secret never appear in results, events, or logs.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from tron_roll_call_hero.account_models import (
    AttendanceType,
    GroupSubmissionResult,
    SubmissionResult,
    SubmissionStatus,
)
from tron_roll_call_hero.qr_account import submit_parsed_qr_account
from tron_roll_call_hero.qr_rollcall import parse_qr_payload


async def submit_group_qr_payload(
    supervisor: Any,
    raw_payload: str,
    *,
    provider: str = "",
    profiles: Optional[Sequence[str]] = None,
) -> GroupSubmissionResult:
    """Parse a QR payload once and submit it through matching running workers."""
    try:
        qr_data = parse_qr_payload(str(raw_payload or ""))
    except ValueError:
        qr_data = None
    rid = str(qr_data.rollcall_id or "").strip() if qr_data is not None else ""
    if not rid:
        return GroupSubmissionResult(rollcall_id="", results=())

    wanted_provider = str(provider or "").strip().lower()
    wanted_profiles = [str(item) for item in profiles] if profiles else None

    running = supervisor.running_profiles() if supervisor is not None else ()
    targets = wanted_profiles if wanted_profiles is not None else list(running)

    results = []
    for profile in targets:
        worker = supervisor.worker(profile) if supervisor is not None else None
        context = getattr(worker, "context", None) if worker is not None else None
        if worker is None or context is None or profile not in running:
            results.append(
                SubmissionResult(
                    profile=profile,
                    provider_key=getattr(getattr(worker, "spec", None), "provider_key", ""),
                    rollcall_id=rid,
                    attendance_type=AttendanceType.QR,
                    status=SubmissionStatus.SKIPPED_NOT_APPLICABLE,
                    error_code="worker_not_running",
                )
            )
            continue
        if wanted_provider and context.provider_key.lower() != wanted_provider:
            results.append(
                SubmissionResult(
                    profile=profile,
                    provider_key=context.provider_key,
                    rollcall_id=rid,
                    attendance_type=AttendanceType.QR,
                    status=SubmissionStatus.SKIPPED_NOT_APPLICABLE,
                    error_code="provider_mismatch",
                )
            )
            continue
        results.append(await submit_parsed_qr_account(context, qr_data))

    return GroupSubmissionResult(rollcall_id=rid, results=tuple(results))
