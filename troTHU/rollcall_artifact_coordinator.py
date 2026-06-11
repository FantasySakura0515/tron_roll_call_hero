"""Shared rollcall artifact discovery across account workers (Phase 3.3).

When several account workers answer the same number rollcall, the 4-digit code
is the same for everyone — only the submission is per-account. The coordinator
deduplicates the discovery: keyed by ``(provider_key, rollcall_id)``, a direct
read happens once (single-flight), the result is cached with a TTL, errors are
never cached, and a brute-force discovery can be published so the remaining
accounts submit the known code once instead of guessing again.

This module must not import ``troTHU.runtime_context``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from troTHU.number_account import NumberCodeResolver
from troTHU.number_rollcall import NumberCodeLookup

DEFAULT_TTL_SECONDS = 120.0

_Key = Tuple[str, str]


class _TimeClock:
    def now(self) -> float:
        return time.time()


class RollcallArtifactCoordinator:
    """Single-flight cache for per-rollcall shared artifacts."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_TTL_SECONDS, clock: Any = None) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._clock = clock or _TimeClock()
        self._values: Dict[_Key, Tuple[Any, float]] = {}
        self._inflight: Dict[_Key, asyncio.Task] = {}
        self._closed = False

    @staticmethod
    def _key(provider_key: str, rollcall_id: Any) -> _Key:
        return (str(provider_key or ""), str(rollcall_id or ""))

    def _now(self) -> float:
        return float(self._clock.now())

    def peek(self, provider_key: str, rollcall_id: Any) -> Optional[Any]:
        key = self._key(provider_key, rollcall_id)
        entry = self._values.get(key)
        if entry is None:
            return None
        value, resolved_at = entry
        if self._ttl and self._now() - resolved_at > self._ttl:
            self._values.pop(key, None)
            return None
        return value

    def publish(self, provider_key: str, rollcall_id: Any, value: Any) -> None:
        self._values[self._key(provider_key, rollcall_id)] = (value, self._now())

    async def get_or_resolve(
        self,
        provider_key: str,
        rollcall_id: Any,
        resolver: Callable[[], Awaitable[Any]],
    ) -> Any:
        if self._closed:
            raise RuntimeError("artifact coordinator is shut down")
        cached = self.peek(provider_key, rollcall_id)
        if cached is not None:
            return cached
        key = self._key(provider_key, rollcall_id)
        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._run_resolver(key, resolver))
            self._inflight[key] = task
        # shield: one caller being cancelled must not kill the shared resolve,
        # but a coordinator shutdown (which cancels the task) must propagate.
        return await asyncio.shield(task)

    async def _run_resolver(self, key: _Key, resolver: Callable[[], Awaitable[Any]]) -> Any:
        try:
            value = await resolver()
        finally:
            self._inflight.pop(key, None)
        if value is not None:
            self._values[key] = (value, self._now())
        return value

    async def shutdown(self) -> None:
        self._closed = True
        tasks = list(self._inflight.values())
        self._inflight.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class CoordinatedNumberCodeResolver:
    """Drop-in resolver for ``answer_number_rollcall`` that shares direct reads.

    The first account performs the direct read; concurrent and later accounts
    reuse the cached code. A brute-force discovery published through
    :meth:`publish` short-circuits everyone else to a single submission.
    """

    def __init__(
        self,
        coordinator: RollcallArtifactCoordinator,
        *,
        inner: Optional[NumberCodeResolver] = None,
    ) -> None:
        self._coordinator = coordinator
        self._inner = inner or NumberCodeResolver()
        self.direct_read_count = 0

    async def resolve_direct(self, account: Any, rollcall_id: Any) -> NumberCodeLookup:
        async def _resolve() -> Optional[str]:
            self.direct_read_count += 1
            lookup = await self._inner.resolve_direct(account, rollcall_id)
            return lookup.code if lookup.has_code else None

        code = await self._coordinator.get_or_resolve(account.provider_key, rollcall_id, _resolve)
        if code:
            return NumberCodeLookup(code=str(code), source="coordinator")
        return NumberCodeLookup()

    def publish(self, provider_key: str, rollcall_id: Any, code: str) -> None:
        self._coordinator.publish(provider_key, rollcall_id, str(code))
