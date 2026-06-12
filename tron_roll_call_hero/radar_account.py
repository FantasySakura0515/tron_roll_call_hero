"""Account-scoped radar rollcall execution (Phase 2.6).

Runs the empty-answer-first strategy and the global_wgs84 solver fallback for
one explicit :class:`AccountContext`: the account's session and endpoints carry
every request, the solver settings come from ``account.config.radar``, and
completion lands in ``account.state.completed_radar``.

The legacy global path ``radar_runtime.radar(session, rollcall)`` is untouched
and remains the wrapper for the single-account CLI until the worker lands.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import ssl
import uuid
from typing import Any, List, Mapping, Optional

try:
    import aiohttp

    _NETWORK_ERRORS: tuple = (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError)
    _JSON_ERRORS: tuple = (aiohttp.ContentTypeError, ValueError)
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tests require aiohttp
    aiohttp = None
    _NETWORK_ERRORS = (asyncio.TimeoutError, ssl.SSLError)
    _JSON_ERRORS = (ValueError,)

from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import AttendanceType, SubmissionResult, SubmissionStatus
from tron_roll_call_hero.global_radar_solver import (
    GlobalDistanceObservation,
    global_anchor_points,
    global_radar_solver_config_from_mapping,
    solve_global_radar,
    standard_sample_points,
    supplement_sample_points,
)
from tron_roll_call_hero.radar_rollcall import build_radar_answer_payload, parse_radar_lite_payload
from tron_roll_call_hero.radar_solver import GeoPoint, RadarGeometryError
from tron_roll_call_hero.rollcall_account import account_completed, fetch_account_progress
from tron_roll_call_hero.runtime_helpers import RadarCoordinateResult, parse_radar_answer_result
from tron_roll_call_hero.tron_http import TronHttpClient, TronHttpError, UnauthorizedError

DEFAULT_MAX_QUERIES = 120


class _RadarUnauthorized(Exception):
    pass


def _request_ssl(account: AccountContext) -> Any:
    http = account.config.http if account.config is not None else {}
    verify = bool(http.get("verify_ssl", True)) if isinstance(http, Mapping) else True
    return False if not verify else None


def _radar_global_config(account: AccountContext) -> Mapping[str, Any]:
    radar = account.config.radar if account.config is not None else {}
    if isinstance(radar, Mapping) and isinstance(radar.get("global"), Mapping):
        return radar["global"]
    return {}


async def _put_answer(
    account: AccountContext,
    url: str,
    payload: Mapping[str, Any],
) -> RadarCoordinateResult:
    kwargs: dict = {"json": dict(payload)}
    ssl_setting = _request_ssl(account)
    if ssl_setting is not None:
        kwargs["ssl"] = ssl_setting
    async with account.session.put(url, **kwargs) as resp:
        body = await resp.text()
        if resp.status in (401, 403) or "login" in str(resp.url).lower():
            raise _RadarUnauthorized("radar answer unauthorized")
        return parse_radar_answer_result(resp.status, body)


async def _verify_confirmed(account: AccountContext, rollcall_id: Any) -> bool:
    summary = await fetch_account_progress(account, rollcall_id)
    return bool(isinstance(summary, Mapping) and summary.get("confirmed_present"))


async def answer_radar_rollcall(
    account: AccountContext,
    rollcall: Mapping[str, Any],
) -> SubmissionResult:
    """Answer a radar rollcall for one account and report the outcome."""
    rollcall_id = rollcall.get("rollcall_id") if isinstance(rollcall, Mapping) else None
    rid = str(rollcall_id or "").strip()

    def _result(status: SubmissionStatus, error_code: str = "") -> SubmissionResult:
        return SubmissionResult(
            profile=account.profile,
            provider_key=account.provider_key,
            rollcall_id=rid,
            attendance_type=AttendanceType.RADAR,
            status=status,
            error_code=error_code,
        )

    if account_completed(account, "radar", rid):
        return _result(SubmissionStatus.SKIPPED_ALREADY_COMPLETE)

    base_url = account.endpoints.base_url.rstrip("/")
    answer_url = "{}/api/rollcall/{}/answer".format(base_url, rollcall_id)

    async def _confirm() -> SubmissionResult:
        if await _verify_confirmed(account, rollcall_id):
            account.state.completed_radar.add(rid)
            return _result(SubmissionStatus.CONFIRMED)
        return _result(SubmissionStatus.SUBMITTED_UNCONFIRMED)

    # Stage 1: coordinate-free empty answer.
    try:
        empty_result = await _put_answer(account, answer_url, {})
    except _RadarUnauthorized:
        return _result(SubmissionStatus.FAILED, error_code="unauthorized")
    except _NETWORK_ERRORS:
        return _result(SubmissionStatus.FAILED, error_code="transient")
    if empty_result.success:
        return await _confirm()

    # Stage 2: global_wgs84 solver fallback driven by the account's config.
    global_config = _radar_global_config(account)
    solver_config = global_radar_solver_config_from_mapping(global_config)
    try:
        max_queries = int(global_config.get("max_queries", DEFAULT_MAX_QUERIES))
    except (TypeError, ValueError):
        max_queries = DEFAULT_MAX_QUERIES

    client = TronHttpClient(account.session, request_ssl=_request_ssl(account), endpoints=account.endpoints)
    try:
        user_id = await client.fetch_user_id()
        lite_url = "{}/api/rollcall/{}/lite".format(base_url, rollcall_id)
        kwargs = client.request_kwargs()
        async with account.session.get(lite_url, **kwargs) as resp:
            if resp.status in (401, 403) or "login" in str(resp.url).lower():
                return _result(SubmissionStatus.FAILED, error_code="unauthorized")
            try:
                lite_data = await resp.json() if resp.status == 200 else dict(rollcall)
            except _JSON_ERRORS:
                lite_data = dict(rollcall)
    except UnauthorizedError:
        return _result(SubmissionStatus.FAILED, error_code="unauthorized")
    except (TronHttpError, *_NETWORK_ERRORS):
        return _result(SubmissionStatus.FAILED, error_code="transient")

    lite_info = parse_radar_lite_payload(lite_data, fallback_rollcall=rollcall)
    device_id = uuid.uuid4().hex
    coordinate_url = "{}?api_version=1.76".format(answer_url)
    observations: List[GlobalDistanceObservation] = []
    request_count = 0
    estimate: Any = None

    async def _submit_point(point: GeoPoint, label: str) -> Optional[SubmissionResult]:
        nonlocal request_count
        payload = build_radar_answer_payload(
            point,
            device_id=device_id,
            user_id=user_id,
            use_beacon=lite_info.use_beacon,
            beacon_nonce=lite_info.beacon_nonce,
            accuracy=60,
        )
        try:
            result = await _put_answer(account, coordinate_url, payload)
        except _RadarUnauthorized:
            return _result(SubmissionStatus.FAILED, error_code="unauthorized")
        except _NETWORK_ERRORS:
            return None
        request_count += 1
        if result.success:
            return await _confirm()
        if result.is_scope_distance:
            observations.append(GlobalDistanceObservation(point, result.distance, label))
        return None

    async def _submit_stage(points, prefix: str) -> Optional[SubmissionResult]:
        for index, point in enumerate(points, start=1):
            if request_count >= max_queries:
                return None
            outcome = await _submit_point(point, "{}-{}".format(prefix, index))
            if outcome is not None:
                return outcome
        return None

    def _solve(initial: Optional[GeoPoint]) -> Any:
        if len(observations) < 3:
            return None
        try:
            return solve_global_radar(observations, config=solver_config, initial=initial)
        except RadarGeometryError:
            return None

    outcome = await _submit_stage(global_anchor_points(solver_config.anchor_count), "global-anchor")
    if outcome is not None:
        return outcome

    estimate = _solve(None)
    if estimate is None:
        return _result(SubmissionStatus.FAILED, error_code="not_located")

    outcome = await _submit_point(estimate.point, "estimate-anchor")
    if outcome is not None:
        return outcome

    for stage_points, prefix in (
        (standard_sample_points(estimate.point, solver_config), "local-standard"),
        (supplement_sample_points(estimate.point, solver_config), "local-supplement"),
    ):
        outcome = await _submit_stage(stage_points, prefix)
        if outcome is not None:
            return outcome
        refined = _solve(estimate.point)
        if refined is not None:
            estimate = refined
            outcome = await _submit_point(estimate.point, "estimate-{}".format(prefix))
            if outcome is not None:
                return outcome
        if request_count >= max_queries:
            break

    return _result(SubmissionStatus.FAILED, error_code="not_located")
