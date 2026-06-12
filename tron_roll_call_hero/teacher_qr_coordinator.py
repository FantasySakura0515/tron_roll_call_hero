"""Teacher-assisted QR coordination across accounts (Phase 4.2).

One coordinator owns the teacher account: its session, its login, the course
resolution, and the lifecycle of the teacher-side QR rollcalls. Student
accounts call :meth:`TeacherQrCoordinator.assist`; the teacher rollcall for a
given student rollcall is created exactly once (single-flight), the rotating
QR ``data`` is fetched fresh for every submission and only ever lives in
memory, and the teacher rollcall is stopped once every interested account has
completed — or on :meth:`stop_assist`/:meth:`shutdown`.

Coordinator failures surface as per-account ``SubmissionResult`` values, so a
broken teacher account never affects Number/Radar monitoring.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set, Tuple

try:
    import aiohttp

    _NETWORK_ERRORS: tuple = (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError)
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tests require aiohttp
    aiohttp = None
    _NETWORK_ERRORS = (asyncio.TimeoutError, ssl.SSLError)

from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import AttendanceType, SubmissionResult, SubmissionStatus
from tron_roll_call_hero.qr_account import submit_parsed_qr_account
from tron_roll_call_hero.qr_rollcall import QrCodeData
from tron_roll_call_hero.teacher_rollcall import build_teacher_rollcall_payload, extract_rollcall_id
from tron_roll_call_hero.tron_http import (
    TronHttpClient,
    TronHttpError,
    UnauthorizedError,
    has_session_cookie,
)


def _default_session_factory() -> Any:
    return aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))


def _rollcall_id(rollcall: Any) -> str:
    if isinstance(rollcall, Mapping):
        for key in ("rollcall_id", "rollcallId", "id"):
            value = rollcall.get(key)
            if value not in (None, ""):
                return str(value)
    return str(rollcall or "").strip()


class TeacherQrCoordinator:
    """Coordinates teacher-assisted QR sign-ins for many student accounts."""

    def __init__(
        self,
        *,
        endpoints: Any,
        credentials: Tuple[str, str],
        course_id: str = "",
        session_factory: Optional[Callable[[], Any]] = None,
        request_ssl: Any = None,
        poll_interval: float = 2.0,
        confirm_window: float = 45.0,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._endpoints = endpoints
        self._credentials = credentials
        self._configured_course_id = str(course_id or "")
        self._session_factory = session_factory or _default_session_factory
        self._request_ssl = request_ssl
        self._poll_interval = max(0.001, float(poll_interval))
        self._confirm_window = max(0.01, float(confirm_window))
        self._sleep = sleep or asyncio.sleep

        self._session: Any = None
        self._login_ok = False
        self._login_status = "not_attempted"
        # student rollcall id -> prepared teacher rollcall info (no QR data!)
        self._prepared: Dict[str, Dict[str, Any]] = {}
        self._prepare_tasks: Dict[str, asyncio.Task] = {}
        self._interested: Dict[str, Set[str]] = {}
        self._completed: Dict[str, Set[str]] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # Teacher session
    # ------------------------------------------------------------------
    def _client(self) -> TronHttpClient:
        return TronHttpClient(self._session, request_ssl=self._request_ssl, endpoints=self._endpoints)

    async def _ensure_ready(self) -> bool:
        if self._closed:
            return False
        if self._login_ok:
            return True
        if self._session is None:
            self._session = self._session_factory()
        client = self._client()
        domain = getattr(self._endpoints, "session_cookie_domain", "")
        try:
            self._session.cookie_jar.clear()
            form = await client.fetch_login_form()
            outcome = await client.submit_login(form, self._credentials[0], self._credentials[1])
        except (TronHttpError, *_NETWORK_ERRORS):
            self._login_status = "error"
            return False
        self._login_ok = bool(outcome.has_session and has_session_cookie(self._session, domain))
        self._login_status = "success" if self._login_ok else "rejected"
        return self._login_ok

    # ------------------------------------------------------------------
    # Prepare (single-flight per student rollcall)
    # ------------------------------------------------------------------
    async def _resolve_course_id(self, client: TronHttpClient) -> str:
        if self._configured_course_id:
            return self._configured_course_id
        payload = await client.fetch_my_courses()
        courses = payload.get("courses") if isinstance(payload, Mapping) else []
        for course in courses or []:
            if isinstance(course, Mapping) and course.get("id") not in (None, ""):
                return str(course["id"])
        return ""

    async def _prepare(self, student_rid: str) -> Dict[str, Any]:
        entry = self._prepared.get(student_rid)
        if entry is not None:
            return entry
        task = self._prepare_tasks.get(student_rid)
        if task is None or task.done():
            task = asyncio.create_task(self._do_prepare(student_rid))
            self._prepare_tasks[student_rid] = task
        return await asyncio.shield(task)

    async def _do_prepare(self, student_rid: str) -> Dict[str, Any]:
        try:
            client = self._client()
            course_id = await self._resolve_course_id(client)
            if not course_id:
                raise TronHttpError("teacher has no usable course")
            created = await client.create_teacher_rollcall(
                course_id, build_teacher_rollcall_payload(kind="qr")
            )
            teacher_rid = extract_rollcall_id(created)
            if not teacher_rid:
                raise TronHttpError("teacher rollcall response missing id")
            with contextlib.suppress(TronHttpError):
                await client.start_teacher_rollcall(teacher_rid)
            entry = {
                "student_rollcall_id": student_rid,
                "teacher_rollcall_id": str(teacher_rid),
                "course_id": str(course_id),
                "created_at": time.monotonic(),
            }
            self._prepared[student_rid] = entry
            return entry
        finally:
            self._prepare_tasks.pop(student_rid, None)

    # ------------------------------------------------------------------
    # Student-facing API
    # ------------------------------------------------------------------
    async def assist(self, account: AccountContext, rollcall: Any) -> SubmissionResult:
        rid = _rollcall_id(rollcall)

        def _result(status: SubmissionStatus, error_code: str = "") -> SubmissionResult:
            return SubmissionResult(
                profile=account.profile,
                provider_key=account.provider_key,
                rollcall_id=rid,
                attendance_type=AttendanceType.QR,
                status=status,
                error_code=error_code,
            )

        if not rid:
            return _result(SubmissionStatus.FAILED, error_code="invalid_rollcall")
        if self._closed:
            return _result(SubmissionStatus.FAILED, error_code="coordinator_shutdown")

        self._interested.setdefault(rid, set()).add(account.profile)

        if not await self._ensure_ready():
            return _result(SubmissionStatus.FAILED, error_code="teacher_not_ready")

        try:
            entry = await self._prepare(rid)
        except UnauthorizedError:
            self._login_ok = False
            return _result(SubmissionStatus.FAILED, error_code="teacher_unauthorized")
        except (TronHttpError, *_NETWORK_ERRORS):
            return _result(SubmissionStatus.FAILED, error_code="prepare_failed")
        except asyncio.CancelledError:
            return _result(SubmissionStatus.FAILED, error_code="coordinator_shutdown")

        client = self._client()
        deadline = time.monotonic() + self._confirm_window
        last: Optional[SubmissionResult] = None
        while time.monotonic() < deadline and not self._closed:
            try:
                payload = await client.fetch_teacher_qr_code(
                    entry["course_id"], entry["teacher_rollcall_id"]
                )
            except UnauthorizedError:
                self._login_ok = False
                return _result(SubmissionStatus.FAILED, error_code="teacher_unauthorized")
            except (TronHttpError, *_NETWORK_ERRORS):
                await self._sleep(self._poll_interval)
                continue
            data = str(payload.get("data") or "") if isinstance(payload, Mapping) else ""
            if data:
                qr_data = QrCodeData(fields={"rollcallId": rid, "data": data})
                last = await submit_parsed_qr_account(account, qr_data)
                if last.status in (
                    SubmissionStatus.CONFIRMED,
                    SubmissionStatus.SKIPPED_ALREADY_COMPLETE,
                ):
                    await self._mark_completed(rid, account.profile)
                    return last
                if last.status == SubmissionStatus.SUBMITTED_UNCONFIRMED:
                    return last
                if last.error_code == "unauthorized":
                    return last
            await self._sleep(self._poll_interval)
        if last is not None:
            return last
        return _result(SubmissionStatus.FAILED, error_code="not_confirmed")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _mark_completed(self, rid: str, profile: str) -> None:
        self._completed.setdefault(rid, set()).add(profile)
        interested = self._interested.get(rid, set())
        if interested and interested.issubset(self._completed.get(rid, set())):
            await self.stop_assist(rid)

    async def stop_assist(self, rid: str) -> None:
        rid = str(rid or "")
        entry = self._prepared.pop(rid, None)
        self._interested.pop(rid, None)
        self._completed.pop(rid, None)
        if entry is None or self._session is None:
            return
        with contextlib.suppress(TronHttpError, UnauthorizedError, *_NETWORK_ERRORS):
            await self._client().stop_teacher_rollcall(
                entry["teacher_rollcall_id"], rollcall_type="qr"
            )

    async def shutdown(self) -> None:
        self._closed = True
        for task in list(self._prepare_tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._prepare_tasks.clear()
        for rid in list(self._prepared.keys()):
            await self.stop_assist(rid)
        if self._session is not None and not getattr(self._session, "closed", True):
            await self._session.close()
        self._session = None
        self._login_ok = False

    def status(self) -> Dict[str, Any]:
        """A safe summary for consoles/bots; never includes QR data."""
        return {
            "login_status": self._login_status,
            "active": [
                {
                    "student_rollcall_id": entry["student_rollcall_id"],
                    "teacher_rollcall_id": entry["teacher_rollcall_id"],
                    "course_id": entry["course_id"],
                    "interested": sorted(self._interested.get(rid, set())),
                    "completed": sorted(self._completed.get(rid, set())),
                }
                for rid, entry in self._prepared.items()
            ],
        }
