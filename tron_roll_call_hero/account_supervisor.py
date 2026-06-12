"""Account worker supervision (Phase 3.1).

The supervisor owns one supervising task per desired account spec. Each task
runs the account's worker; if the worker crashes, the supervisor restarts it
with exponential backoff while every other account keeps running untouched.
Stopping one account never touches the others, and the global shutdown waits
for every worker to finish cleanly.

Workers are produced by an injected factory so tests can supervise lightweight
fakes; production wires it to :class:`tron_roll_call_hero.account_worker.AccountWorker`.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

from tron_roll_call_hero.account_models import AccountSpec, AccountWorkerSnapshot

DEFAULT_RESTART_BACKOFF: tuple = (1.0, 2.0, 5.0, 15.0, 60.0)


class _Supervised:
    """Book-keeping for one account's supervising task."""

    def __init__(self, spec: AccountSpec) -> None:
        self.spec = spec
        self.worker: Any = None
        self.task: Optional[asyncio.Task] = None
        self.restarts = 0
        self.stopping = False


class AccountSupervisor:
    """Supervises independent account workers."""

    def __init__(
        self,
        specs: Sequence[AccountSpec],
        *,
        worker_factory: Callable[[AccountSpec], Any],
        restart_backoff: Sequence[float] = DEFAULT_RESTART_BACKOFF,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._specs: Tuple[AccountSpec, ...] = tuple(specs)
        self._worker_factory = worker_factory
        self._restart_backoff = tuple(float(item) for item in restart_backoff) or DEFAULT_RESTART_BACKOFF
        self._sleep = sleep or asyncio.sleep
        self._supervised: Dict[str, _Supervised] = {}
        self._started = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def desired_profiles(self) -> Tuple[str, ...]:
        return tuple(spec.profile for spec in self._specs)

    def running_profiles(self) -> Tuple[str, ...]:
        return tuple(
            profile
            for profile, entry in self._supervised.items()
            if entry.task is not None and not entry.task.done()
        )

    def worker(self, profile: str) -> Any:
        entry = self._supervised.get(str(profile or ""))
        return entry.worker if entry is not None else None

    def restart_count(self, profile: str) -> int:
        entry = self._supervised.get(str(profile or ""))
        return entry.restarts if entry is not None else 0

    def snapshots(self) -> Tuple[AccountWorkerSnapshot, ...]:
        snapshots = []
        for entry in self._supervised.values():
            worker = entry.worker
            if worker is not None:
                snapshots.append(worker.snapshot())
            else:
                snapshots.append(
                    AccountWorkerSnapshot(
                        profile=entry.spec.profile,
                        provider_key=entry.spec.provider_key,
                        phase="created",
                    )
                )
        return tuple(snapshots)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for spec in self._specs:
            await self.start_account(spec.profile)

    async def start_account(self, profile: str) -> bool:
        profile = str(profile or "")
        spec = next((item for item in self._specs if item.profile == profile), None)
        if spec is None:
            return False
        entry = self._supervised.get(profile)
        if entry is not None and entry.task is not None and not entry.task.done():
            return True
        entry = _Supervised(spec)
        self._supervised[profile] = entry
        entry.task = asyncio.create_task(self._supervise(entry))
        return True

    async def stop_account(self, profile: str) -> bool:
        entry = self._supervised.get(str(profile or ""))
        if entry is None or entry.task is None or entry.task.done():
            return False
        entry.stopping = True
        if entry.worker is not None:
            await entry.worker.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await entry.task
        return True

    async def stop(self) -> None:
        for profile in list(self._supervised.keys()):
            await self.stop_account(profile)
        self._started = False

    async def reconcile(
        self,
        new_specs: Sequence[AccountSpec],
        *,
        force_restart: Optional[set] = None,
    ) -> Dict[str, Tuple[str, ...]]:
        """Apply a new desired spec set, touching only the accounts that changed.

        Kept workers (identical spec, not force-restarted) are left running with
        their sessions intact.
        """
        force_restart = force_restart or set()
        current = {spec.profile: spec for spec in self._specs}
        desired = {spec.profile: spec for spec in new_specs}

        added = tuple(profile for profile in desired if profile not in current)
        removed = tuple(profile for profile in current if profile not in desired)
        restarted = tuple(
            profile
            for profile in desired
            if profile in current and (desired[profile] != current[profile] or profile in force_restart)
        )
        kept = tuple(
            profile
            for profile in desired
            if profile in current and desired[profile] == current[profile] and profile not in force_restart
        )

        self._specs = tuple(new_specs)
        for profile in removed:
            await self.stop_account(profile)
            self._supervised.pop(profile, None)
        for profile in restarted:
            await self.stop_account(profile)
            self._supervised.pop(profile, None)
            await self.start_account(profile)
        for profile in added:
            await self.start_account(profile)
        return {"added": added, "removed": removed, "restarted": restarted, "kept": kept}

    # ------------------------------------------------------------------
    # Supervision loop
    # ------------------------------------------------------------------
    async def _supervise(self, entry: _Supervised) -> None:
        backoff_index = 0
        while not entry.stopping:
            entry.worker = self._worker_factory(entry.spec)
            try:
                await entry.worker.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a worker crash must not kill the supervisor
                if entry.stopping:
                    return
                delay = self._restart_backoff[min(backoff_index, len(self._restart_backoff) - 1)]
                backoff_index += 1
                entry.restarts += 1
                await self._sleep(delay)
