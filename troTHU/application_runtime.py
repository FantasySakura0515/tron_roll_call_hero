"""Multi-account monitor assembly (Phase 3.4).

``MonitorApplication`` is the composition root for real multi-account
monitoring: it resolves the configured ``now`` target through the account
registry, builds one :class:`AccountWorker` per desired account (all sharing
one artifact coordinator so number codes are discovered once), and runs them
under an :class:`AccountSupervisor`. Starting reports exactly which accounts
run and which were skipped with a reason; one account failing to log in never
stops the others.

This module must not import ``troTHU.runtime_context``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Tuple

from troTHU.account_models import AccountSpec, AccountWorkerSnapshot
from troTHU.account_registry import AccountRegistry, TargetResolution
from troTHU.account_state_repository import FileAccountStateRepository
from troTHU.account_supervisor import DEFAULT_RESTART_BACKOFF, AccountSupervisor
from troTHU.account_worker import DEFAULT_LOGIN_BACKOFF, AccountWorker
from troTHU.rollcall_artifact_coordinator import (
    CoordinatedNumberCodeResolver,
    RollcallArtifactCoordinator,
)
from troTHU.runtime_services import NullEventSink, CredentialResolver, RuntimeServices, SystemClock


@dataclass(frozen=True)
class StartupReport:
    """What happened when the application tried to start the desired accounts."""

    requested: str
    kind: str
    started: Tuple[str, ...] = ()
    skipped: Tuple[Dict[str, Any], ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.started)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "kind": self.kind,
            "started": list(self.started),
            "skipped": [dict(item) for item in self.skipped],
            "warnings": list(self.warnings),
        }


class MonitorApplication:
    """Builds and runs the supervisor for one normalized config."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        base_dir: Any,
        endpoints: Any = None,
        event_sink: Any = None,
        use_schedule: bool = True,
        poll_interval: float = 1.0,
        standby_interval: float = 60.0,
        login_backoff: Sequence[float] = DEFAULT_LOGIN_BACKOFF,
        restart_backoff: Sequence[float] = DEFAULT_RESTART_BACKOFF,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        clock: Any = None,
    ) -> None:
        self._config: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
        self._registry = AccountRegistry(self._config)
        self._repository = FileAccountStateRepository(base_dir)
        self._coordinator = RollcallArtifactCoordinator()
        self._services = RuntimeServices(
            credentials=CredentialResolver(self._config),
            cookies=self._repository,
            states=self._repository,
            events=event_sink if event_sink is not None else NullEventSink(),
            clock=clock if clock is not None else SystemClock(),
        )
        self._endpoints = endpoints
        operating = self._config.get("operating") if use_schedule else None
        self._operating = operating if isinstance(operating, Mapping) else None
        self._poll_interval = poll_interval
        self._standby_interval = standby_interval
        self._login_backoff = tuple(login_backoff)
        self._restart_backoff = tuple(restart_backoff)
        self._sleep = sleep
        self._supervisor: Optional[AccountSupervisor] = None
        self._resolution: Optional[TargetResolution] = None

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------
    def _worker_factory(self, spec: AccountSpec) -> AccountWorker:
        return AccountWorker(
            spec,
            self._config,
            services=self._services,
            endpoints=self._endpoints,
            operating=self._operating,
            poll_interval=self._poll_interval,
            standby_interval=self._standby_interval,
            login_backoff=self._login_backoff,
            sleep=self._sleep,
            number_resolver=CoordinatedNumberCodeResolver(self._coordinator),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self, now: Optional[str] = None) -> StartupReport:
        resolution = self._registry.resolve_target(now)
        self._resolution = resolution
        specs = self._registry.desired_specs(now)
        supervisor = AccountSupervisor(
            specs,
            worker_factory=self._worker_factory,
            restart_backoff=self._restart_backoff,
            sleep=self._sleep or asyncio.sleep,
        )
        self._supervisor = supervisor
        await supervisor.start()
        return StartupReport(
            requested=resolution.requested,
            kind=resolution.kind,
            started=tuple(spec.profile for spec in specs),
            skipped=tuple(item.to_dict() for item in resolution.skipped),
            warnings=tuple(resolution.warnings),
        )

    async def stop(self) -> None:
        if self._supervisor is not None:
            await self._supervisor.stop()
        await self._coordinator.shutdown()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def supervisor(self) -> Optional[AccountSupervisor]:
        return self._supervisor

    def worker(self, profile: str) -> Optional[AccountWorker]:
        if self._supervisor is None:
            return None
        return self._supervisor.worker(profile)

    def snapshots(self) -> Tuple[AccountWorkerSnapshot, ...]:
        if self._supervisor is None:
            return ()
        return self._supervisor.snapshots()

    def snapshot_for(self, profile: str) -> Optional[AccountWorkerSnapshot]:
        for snapshot in self.snapshots():
            if snapshot.profile == str(profile or ""):
                return snapshot
        return None

    def status_report(self) -> Dict[str, Any]:
        resolution = self._resolution
        return {
            "requested": resolution.requested if resolution else "",
            "kind": resolution.kind if resolution else "",
            "desired": list(resolution.profiles) if resolution else [],
            "running": list(self._supervisor.running_profiles()) if self._supervisor else [],
            "skipped": [item.to_dict() for item in resolution.skipped] if resolution else [],
            "accounts": [snapshot.to_dict() for snapshot in self.snapshots()],
        }
