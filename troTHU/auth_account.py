"""Account-scoped authentication (Phase 2.2).

``login_account`` performs login for a single :class:`AccountContext` using that
account's own session, endpoints, credential resolver, and cookie repository,
and writes the outcome into ``account.state.login``. It reads nothing from the
global ``CONFIG`` or the active profile.

The legacy ``auth_runtime.login(session)`` wrapper is intentionally left intact
as the single-account compatibility path; callers migrate to ``login_account``
as workers are introduced.

Deviations from the framework doc (recorded, not skipped):
- This lives in a new module instead of legacy ``auth_runtime`` so it can stay
  free of ``runtime_context``.
- Browser-assisted login and the insecure-SSL config fallback are not yet
  reproduced in the account path; the legacy single-account ``login`` keeps
  those behaviors. Account-path parity is deferred to a later pass.

This module must not import ``troTHU.runtime_context``.
"""

from __future__ import annotations

import asyncio
import ssl as ssl_module
from typing import Any, List, Mapping, Tuple

from troTHU import providers
from troTHU.account_context import AccountContext
from troTHU.account_models import LoginState
from troTHU.runtime_events import account_event
from troTHU.tron_http import (
    LoginPageChangedError,
    LoginRejectedError,
    TronHttpClient,
    TronHttpError,
    has_session_cookie,
)

try:  # pragma: no cover - aiohttp present in runtime/tests
    import aiohttp
except Exception:  # pragma: no cover - offline import guard
    aiohttp = None  # type: ignore


API_VALIDATED_AUTH_FLOWS = {"public_cloud_email"}


def _network_errors() -> Tuple[type, ...]:
    errors: List[type] = [asyncio.TimeoutError, ssl_module.SSLError]
    if aiohttp is not None and hasattr(aiohttp, "ClientError"):
        errors.append(aiohttp.ClientError)
    return tuple(errors)


def _verify_ssl(account: AccountContext) -> Any:
    http = account.config.http if account.config is not None else {}
    verify = bool(http.get("verify_ssl", True)) if isinstance(http, Mapping) else True
    return False if not verify else None


def _dump_session_cookies(session: Any) -> List[dict]:
    records: List[dict] = []
    for cookie in getattr(session, "cookie_jar", []) or []:
        records.append(
            {
                "key": getattr(cookie, "key", ""),
                "value": getattr(cookie, "value", ""),
                "domain": cookie.get("domain", "") if hasattr(cookie, "get") else "",
                "path": cookie.get("path", "/") if hasattr(cookie, "get") else "/",
            }
        )
    return records


def _record(account: AccountContext, state: LoginState) -> LoginState:
    account.state.login = state
    services = account.services
    events = getattr(services, "events", None) if services is not None else None
    if events is not None:
        events.emit(
            account_event(
                "login",
                profile=account.spec.profile,
                provider_key=account.spec.provider_key,
                status=state.status,
            )
        )
    return state


def _save_cookies(account: AccountContext) -> None:
    services = account.services
    cookies = getattr(services, "cookies", None) if services is not None else None
    if cookies is None:
        return
    cookies.save_cookies(account.spec.profile, _dump_session_cookies(account.session))


async def login_account(account: AccountContext) -> LoginState:
    spec = account.spec
    provider = providers.get_provider(spec.provider_key)
    auth_flow = str(getattr(provider, "auth_flow", "") or "").lower()
    domain = account.endpoints.session_cookie_domain

    if auth_flow == "manual_cookie_only":
        if has_session_cookie(account.session, domain):
            return _record(account, LoginState(status="success", credential_source="manual_cookie"))
        return _record(account, LoginState(status="manual_cookie_required", credential_source="manual_cookie"))

    resolved = account.services.credentials.resolve(spec)
    if not resolved.has_password:
        return _record(account, LoginState(status="missing_credentials", credential_source=resolved.source))

    account.state.login_in_progress = True
    try:
        client = TronHttpClient(account.session, request_ssl=_verify_ssl(account), endpoints=account.endpoints)
        try:
            account.session.cookie_jar.clear()
            form = await client.fetch_login_form()
            outcome = await client.submit_login(form, resolved.user, resolved.password)
        except LoginRejectedError:
            return _record(account, LoginState(status="rejected", credential_source=resolved.source))
        except LoginPageChangedError:
            return _record(account, LoginState(status="login_page_changed", credential_source=resolved.source))
        except (TronHttpError,) + _network_errors():
            return _record(account, LoginState(status="transient_error", credential_source=resolved.source))

        if not outcome.has_session or not has_session_cookie(account.session, domain):
            return _record(account, LoginState(status="missing_session", credential_source=resolved.source))

        if auth_flow in API_VALIDATED_AUTH_FLOWS:
            try:
                await client.fetch_current_semester()
            except (TronHttpError,) + _network_errors():
                try:
                    account.session.cookie_jar.clear()
                except Exception:
                    pass
                return _record(account, LoginState(status="missing_session", credential_source=resolved.source))

        _save_cookies(account)
        return _record(account, LoginState(status="success", credential_source=resolved.source))
    finally:
        account.state.login_in_progress = False
