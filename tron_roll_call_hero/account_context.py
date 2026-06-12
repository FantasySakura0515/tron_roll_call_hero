"""Account-scoped execution context (Phase 2.1).

``AccountContext`` is the single object that account-sensitive functions receive
instead of reading the global ``CONFIG`` or the active profile. The factory
builds it from an immutable normalized config without mutating that config and
without copying any password into the context.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from tron_roll_call_hero import providers
from tron_roll_call_hero.account_models import AccountConfig, AccountRuntimeState, AccountSpec
from tron_roll_call_hero.runtime_services import RuntimeServices
from tron_roll_call_hero.tron_http import TronHttpEndpoints, endpoints_from_provider


@dataclass
class AccountContext:
    """Everything one worker needs to act on behalf of a single account."""

    spec: AccountSpec
    config: AccountConfig
    endpoints: TronHttpEndpoints
    session: Any
    state: AccountRuntimeState = field(default_factory=AccountRuntimeState)
    services: Optional[RuntimeServices] = None

    @property
    def profile(self) -> str:
        return self.spec.profile

    @property
    def provider_key(self) -> str:
        return self.spec.provider_key


def _provider_config(config: Mapping[str, Any], provider_key: str) -> Mapping[str, Any]:
    """Prefer the normalized config's per-provider override (e.g. a custom tenant
    base_url) over the static provider definition."""
    provider = config.get("provider") if isinstance(config, Mapping) else None
    if isinstance(provider, Mapping):
        available = provider.get("available")
        if isinstance(available, Mapping) and isinstance(available.get(provider_key), Mapping):
            return available[provider_key]
    return providers.get_provider(provider_key).to_config()


def endpoints_for_provider(provider_key: str, config: Optional[Mapping[str, Any]] = None) -> TronHttpEndpoints:
    return endpoints_from_provider(_provider_config(config or {}, provider_key))


class AccountContextFactory:
    """Builds single-account contexts from one immutable normalized config."""

    def __init__(self, config: Mapping[str, Any], *, services: Optional[RuntimeServices] = None) -> None:
        self._config: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
        self._account_config = AccountConfig.from_config(self._config)
        self._services = services

    @property
    def account_config(self) -> AccountConfig:
        return self._account_config

    def build(
        self,
        spec: AccountSpec,
        *,
        session: Any,
        state: Optional[AccountRuntimeState] = None,
        services: Optional[RuntimeServices] = None,
    ) -> AccountContext:
        return AccountContext(
            spec=spec,
            config=self._account_config,
            endpoints=endpoints_for_provider(spec.provider_key, self._config),
            session=session,
            state=state if state is not None else AccountRuntimeState(),
            services=services if services is not None else self._services,
        )
