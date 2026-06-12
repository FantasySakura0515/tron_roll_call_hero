"""Single-account monitor worker (Phase 2.8).

One ``AccountWorker`` owns everything for one account: its HTTP session, its
login/retry state machine, its schedule gate, and its poll/execute loop. All
account-sensitive work goes through the account-scoped executors from Phases
2.4-2.7; nothing here reads or writes the global active profile.

The worker persists an :class:`AccountStateSnapshot` through the services'
state repository on every phase transition, so consoles and bots can read
progress without touching the worker.

This module must not import ``troTHU.runtime_context``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence

try:
    import aiohttp

    _NETWORK_ERRORS: tuple = (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError)
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tests require aiohttp
    aiohttp = None
    _NETWORK_ERRORS = (asyncio.TimeoutError, ssl.SSLError)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - python < 3.9 fallback
    ZoneInfo = None

from troTHU.account_context import AccountContext, AccountContextFactory
from troTHU.account_models import (
    AccountRuntimeState,
    AccountSpec,
    AccountWorkerSnapshot,
    LoginState,
    SubmissionResult,
)
from troTHU.auth_account import login_account
from troTHU.number_account import answer_number_rollcall
from troTHU.qr_account import submit_qr_payload_account
from troTHU.radar_account import answer_radar_rollcall
from troTHU.rollcall_account import poll_rollcall_decision
from troTHU.runtime_helpers import is_within_any_schedule
from troTHU.tron_http import TronHttpError, UnauthorizedError

DEFAULT_LOGIN_BACKOFF: tuple = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
RETRIABLE_LOGIN_STATUSES = {"transient_error", "missing_session", "login_page_changed"}
UNHEALTHY_PHASES = {"login_failed", "crashed"}
FAILED_LOGIN_STATUSES = {"rejected"}


def _default_session_factory() -> Any:
    return aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))


def _rollcall_id(rollcall: Any) -> str:
    if not isinstance(rollcall, Mapping):
        return ""
    for key in ("rollcall_id", "rollcallId", "id"):
        value = rollcall.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


class AccountWorker:
    """Runs the monitor loop for exactly one account."""

    def __init__(
        self,
        spec: AccountSpec,
        config: Mapping[str, Any],
        *,
        services: Any,
        endpoints: Any = None,
        operating: Optional[Mapping[Any, Mapping[str, Any]]] = None,
        poll_interval: float = 1.0,
        standby_interval: float = 60.0,
        login_backoff: Sequence[float] = DEFAULT_LOGIN_BACKOFF,
        session_factory: Optional[Callable[[], Any]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        number_resolver: Any = None,
    ) -> None:
        self.spec = spec
        self._factory = AccountContextFactory(config, services=services)
        self._services = services
        self._endpoints = endpoints
        self._operating = operating
        self._poll_interval = max(0.001, float(poll_interval))
        self._standby_interval = max(0.001, float(standby_interval))
        self._login_backoff = tuple(float(item) for item in login_backoff) or DEFAULT_LOGIN_BACKOFF
        self._session_factory = session_factory or _default_session_factory
        self._custom_sleep = sleep
        self._now_provider = now_provider
        self._number_resolver = number_resolver

        self._state = AccountRuntimeState()
        self._phase = "created"
        self._last_check_status = ""
        self._last_result: Optional[SubmissionResult] = None
        self._session: Any = None
        self._context: Optional[AccountContext] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def state(self) -> AccountRuntimeState:
        return self._state

    @property
    def session(self) -> Any:
        return self._session

    @property
    def context(self) -> Optional[AccountContext]:
        return self._context

    @property
    def last_result(self) -> Optional[SubmissionResult]:
        return self._last_result

    def snapshot(self) -> AccountWorkerSnapshot:
        last_error = self._state.last_error or {}
        return AccountWorkerSnapshot(
            profile=self.spec.profile,
            provider_key=self.spec.provider_key,
            phase=self._phase,
            login_status=self._state.login.status,
            poll_count=self._state.poll_count,
            last_check_status=self._last_check_status,
            last_error_code=str(last_error.get("code") or ""),
            healthy=(
                self._phase not in UNHEALTHY_PHASES
                and self._state.login.status not in FAILED_LOGIN_STATUSES
            ),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is not None:
            self._task = None
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def run(self) -> None:
        self._set_phase("starting")
        session = self._session_factory()
        self._session = session
        context = self._factory.build(self.spec, session=session, state=self._state, services=self._services)
        if self._endpoints is not None:
            context.endpoints = self._endpoints
        self._context = context
        try:
            await self._run_loop(context)
        except Exception as exc:  # noqa: BLE001 - workers must not kill the supervisor
            self._state.last_error = {"code": "worker_crashed", "error": type(exc).__name__}
            self._set_phase("crashed")
            raise
        finally:
            if self._phase != "crashed":
                self._set_phase("stopping")
            if session is not None and not getattr(session, "closed", True):
                await session.close()
            if self._phase != "crashed":
                self._set_phase("stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def _run_loop(self, context: AccountContext) -> None:
        backoff_index = 0
        logged_in = False
        while not self._stop_event.is_set():
            if not logged_in:
                self._set_phase("logging_in")
                login_state = await login_account(context)
                if login_state.ok:
                    logged_in = True
                    backoff_index = 0
                    self._state.retry.next_delay_seconds = 0.0
                    continue
                if login_state.status == "manual_cookie_required":
                    self._set_phase("manual_cookie_required")
                    await self._sleep(self._standby_interval)
                    continue
                if login_state.status in RETRIABLE_LOGIN_STATUSES:
                    delay = self._login_backoff[min(backoff_index, len(self._login_backoff) - 1)]
                    backoff_index += 1
                    self._state.retry.attempts += 1
                    self._state.retry.next_delay_seconds = delay
                    self._set_phase("waiting_login")
                    await self._sleep(delay)
                    continue
                self._state.last_error = {"code": "login_{}".format(login_state.status)}
                self._set_phase("login_failed")
                await self._sleep(self._standby_interval)
                continue

            if not self._schedule_allows():
                self._set_phase("standby")
                await self._sleep(self._standby_interval)
                continue

            self._set_phase("monitoring")
            try:
                decision = await poll_rollcall_decision(context)
            except UnauthorizedError:
                logged_in = False
                self._state.last_error = {"code": "unauthorized"}
                continue
            except (TronHttpError, *_NETWORK_ERRORS):
                self._state.last_error = {"code": "poll_error"}
                await self._sleep(self._poll_interval)
                continue

            self._last_check_status = str(decision.status or "")
            logged_in = await self._execute_decision(context, decision)
            await self._sleep(self._poll_interval)

    async def _execute_decision(self, context: AccountContext, decision: Any) -> bool:
        """Run the matching account executor. Returns False when re-login is needed."""
        status = str(getattr(decision, "status", "") or "")
        rollcall = getattr(decision, "rollcall", None)
        result: Optional[SubmissionResult] = None
        try:
            if status == "is_number":
                result = await answer_number_rollcall(
                    context, _rollcall_id(rollcall), resolver=self._number_resolver
                )
            elif status == "is_radar":
                result = await answer_radar_rollcall(context, rollcall if isinstance(rollcall, Mapping) else {})
        except UnauthorizedError:
            self._state.last_error = {"code": "unauthorized"}
            return False
        if result is not None:
            self._last_result = result
            if result.error_code == "unauthorized":
                self._state.last_error = {"code": "unauthorized"}
                return False
        return True

    async def force_check(self) -> Dict[str, Any]:
        """Run one immediate poll/execute cycle for this account only."""
        context = self._context
        if context is None or self._session is None or getattr(self._session, "closed", True):
            return {"ok": False, "reason": "not_running"}
        try:
            decision = await poll_rollcall_decision(context)
        except UnauthorizedError:
            self._state.last_error = {"code": "unauthorized"}
            return {"ok": False, "reason": "unauthorized"}
        except (TronHttpError, *_NETWORK_ERRORS):
            return {"ok": False, "reason": "poll_error"}
        self._last_check_status = str(decision.status or "")
        await self._execute_decision(context, decision)
        outcome: Dict[str, Any] = {"ok": True, "decision": self._last_check_status}
        if self._last_result is not None:
            outcome["result"] = self._last_result.to_dict()
        return outcome

    def request_reauth(self) -> bool:
        """Drop this account's session cookies so the loop re-logs in on its own."""
        if self._session is None or getattr(self._session, "closed", True):
            return False
        with contextlib.suppress(Exception):
            self._session.cookie_jar.clear()
        self._state.login = LoginState(
            status="reauth_requested",
            credential_source=self._state.login.credential_source,
        )
        return True

    async def submit_manual_qr(self, raw_payload: str, **kwargs: Any) -> SubmissionResult:
        """Submit a manually supplied QR payload through this worker's account."""
        if self._context is None:
            raise RuntimeError("worker has not started")
        result = await submit_qr_payload_account(self._context, raw_payload, **kwargs)
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self._state.phase = phase
        self._persist_snapshot()

    def _persist_snapshot(self) -> None:
        states = getattr(self._services, "states", None) if self._services is not None else None
        if states is None:
            return
        with contextlib.suppress(OSError, ValueError):
            states.save(
                self._state.to_snapshot(
                    profile=self.spec.profile,
                    provider_key=self.spec.provider_key,
                )
            )

    def _now(self) -> datetime:
        if self._now_provider is not None:
            return self._now_provider()
        timezone_name = ""
        account_config = getattr(self._factory, "account_config", None)
        if account_config is not None:
            timezone_name = str(getattr(account_config, "timezone", "") or "")
        if ZoneInfo is not None and timezone_name:
            with contextlib.suppress(Exception):
                return datetime.now(ZoneInfo(timezone_name))
        return datetime.now()

    def _schedule_allows(self) -> bool:
        if not self._operating:
            return True
        now = self._now()
        schedule = self._operating.get(now.weekday())
        if schedule is None:
            schedule = self._operating.get(str(now.weekday()))
        if not isinstance(schedule, Mapping) or not schedule.get("enable", False):
            return False
        ranges = schedule.get("ranges", schedule.get("range"))
        return is_within_any_schedule(ranges, now.time())

    async def _sleep(self, seconds: float) -> None:
        if self._custom_sleep is not None:
            await self._custom_sleep(seconds)
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.0, float(seconds)))
