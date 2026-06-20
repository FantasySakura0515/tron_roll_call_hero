"""Account-scoped number rollcall execution (Phase 2.5).

Splits the number rollcall flow into a code resolver (direct read of the
leaked ``number_code``) and a submission executor (submit + verify), both
acting on one explicit :class:`AccountContext`. Completion is recorded on
``account.state.completed_number`` so two accounts can answer the same
rollcall independently.

The legacy global path ``number_runtime.number(session, rcid)`` is untouched
and remains the wrapper for the single-account CLI until the worker lands.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import ssl
import uuid
from typing import Any, Iterable, Mapping, Optional

try:
    import aiohttp

    _NETWORK_ERRORS: tuple = (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError)
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tests require aiohttp
    aiohttp = None
    _NETWORK_ERRORS = (asyncio.TimeoutError, ssl.SSLError)

from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import AttendanceType, SubmissionResult, SubmissionStatus
from tron_roll_call_hero.number_rollcall import (
    NumberAttemptResult,
    NumberAttemptStatus,
    NumberCodeLookup,
    classify_number_response,
    parse_number_code_payload,
)
from tron_roll_call_hero.rollcall_account import account_completed, fetch_account_progress
from tron_roll_call_hero.tron_http import TronHttpClient, TronHttpError

NUMBER_CODE_LIMIT = 10000


def _request_ssl(account: AccountContext) -> Any:
    http = account.config.http if account.config is not None else {}
    verify = bool(http.get("verify_ssl", True)) if isinstance(http, Mapping) else True
    return False if not verify else None


class NumberCodeResolver:
    """Reads the rollcall's number code directly for one account.

    Failures (network, auth, missing code) degrade to an empty lookup so the
    caller can fall back to brute force without raising.
    """

    async def resolve_direct(
        self, account: AccountContext, rollcall_id: Any, course_id: Any = ""
    ) -> NumberCodeLookup:
        client = TronHttpClient(
            account.session,
            request_ssl=_request_ssl(account),
            endpoints=account.endpoints,
        )
        try:
            payload = await client.fetch_student_rollcalls(rollcall_id, course_id=course_id)
        except (TronHttpError, *_NETWORK_ERRORS):
            return NumberCodeLookup()
        return parse_number_code_payload(payload)


class NumberSubmissionExecutor:
    """Submits number codes and verifies completion for one account."""

    def __init__(self) -> None:
        self._device_id = uuid.uuid4().hex

    async def submit_code(self, account: AccountContext, rollcall_id: Any, code: int) -> NumberAttemptResult:
        url = "{}/api/rollcall/{}/answer_number_rollcall".format(
            account.endpoints.base_url.rstrip("/"), rollcall_id
        )
        payload = {"deviceId": self._device_id, "numberCode": "{:04d}".format(int(code))}
        kwargs: dict = {"json": payload}
        ssl_setting = _request_ssl(account)
        if ssl_setting is not None:
            kwargs["ssl"] = ssl_setting
        async with account.session.put(url, **kwargs) as resp:
            body = await resp.text()
            return classify_number_response(resp.status, body)

    async def verify_confirmed(self, account: AccountContext, rollcall_id: Any) -> bool:
        summary = await fetch_account_progress(account, rollcall_id)
        return bool(isinstance(summary, Mapping) and summary.get("confirmed_present"))


async def answer_number_rollcall(
    account: AccountContext,
    rollcall_id: Any,
    *,
    course_id: Any = "",
    resolver: Optional[NumberCodeResolver] = None,
    executor: Optional[NumberSubmissionExecutor] = None,
    code_limit: int = NUMBER_CODE_LIMIT,
) -> SubmissionResult:
    """Answer a number rollcall for one account and report the outcome."""
    rid = str(rollcall_id or "").strip()

    def _result(status: SubmissionStatus, error_code: str = "") -> SubmissionResult:
        return SubmissionResult(
            profile=account.profile,
            provider_key=account.provider_key,
            rollcall_id=rid,
            attendance_type=AttendanceType.NUMBER,
            status=status,
            error_code=error_code,
        )

    if account_completed(account, "number", rid):
        return _result(SubmissionStatus.SKIPPED_ALREADY_COMPLETE)

    resolver = resolver or NumberCodeResolver()
    executor = executor or NumberSubmissionExecutor()

    # Coordinated resolvers (the worker path) discover the code once across all
    # accounts via single-flight; each account then submits the known code once.
    resolve_code = getattr(resolver, "resolve_code", None)
    if callable(resolve_code):
        code = await resolve_code(account, rollcall_id, course_id, executor=executor, code_limit=code_limit)
        if account_completed(account, "number", rid):
            # The discoverer's winning brute-force scan already confirmed this
            # account; skip the redundant re-submit of the same code.
            return _result(SubmissionStatus.CONFIRMED)
        if not code:
            return _result(SubmissionStatus.FAILED, error_code="code_not_found")
        attempt = await executor.submit_code(account, rollcall_id, int(code))
        if attempt.status == NumberAttemptStatus.SUCCESS:
            if await executor.verify_confirmed(account, rollcall_id):
                account.state.completed_number[rid] = "{:04d}".format(int(code))
                return _result(SubmissionStatus.CONFIRMED)
            return _result(SubmissionStatus.SUBMITTED_UNCONFIRMED)
        if attempt.status == NumberAttemptStatus.UNAUTHORIZED:
            return _result(SubmissionStatus.FAILED, error_code="unauthorized")
        if attempt.status == NumberAttemptStatus.TRANSIENT_FAILURE:
            return _result(SubmissionStatus.FAILED, error_code="transient")
        return _result(SubmissionStatus.FAILED, error_code="unexpected_response")

    # Legacy per-account path (plain resolver / single-account CLI back-compat).
    lookup = await resolver.resolve_direct(account, rollcall_id, course_id)
    candidates: Iterable[int] = [int(lookup.code)] if lookup.has_code else range(code_limit)

    for candidate in candidates:
        attempt = await executor.submit_code(account, rollcall_id, candidate)
        if attempt.status == NumberAttemptStatus.SUCCESS:
            # A brute-force discovery is a shared artifact: publish it so other
            # accounts answering the same rollcall submit once instead of guessing.
            publish = getattr(resolver, "publish", None)
            if callable(publish) and not lookup.has_code:
                publish(account.provider_key, rid, "{:04d}".format(int(candidate)))
            if await executor.verify_confirmed(account, rollcall_id):
                account.state.completed_number[rid] = "{:04d}".format(int(candidate))
                return _result(SubmissionStatus.CONFIRMED)
            return _result(SubmissionStatus.SUBMITTED_UNCONFIRMED)
        if attempt.status == NumberAttemptStatus.WRONG_CODE:
            continue
        if attempt.status == NumberAttemptStatus.UNAUTHORIZED:
            return _result(SubmissionStatus.FAILED, error_code="unauthorized")
        if attempt.status == NumberAttemptStatus.TRANSIENT_FAILURE:
            return _result(SubmissionStatus.FAILED, error_code="transient")
        return _result(SubmissionStatus.FAILED, error_code="unexpected_response")

    return _result(SubmissionStatus.FAILED, error_code="code_not_found")
