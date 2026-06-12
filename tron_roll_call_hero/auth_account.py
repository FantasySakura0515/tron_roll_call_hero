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

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import asyncio
import ssl as ssl_module
from typing import Any, List, Mapping, Tuple

from tron_roll_call_hero import providers
from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import CredentialSource, LoginState
from tron_roll_call_hero.runtime_events import account_event
from tron_roll_call_hero.tron_http import (
    LoginPageChangedError,
    LoginRejectedError,
    TronHttpClient,
    TronHttpError,
    detect_captcha_field,
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

    manual_cookie = (
        auth_flow == "manual_cookie_only"
        or spec.credential_ref.source == CredentialSource.MANUAL_COOKIE
    )
    if manual_cookie:
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
            if auth_flow == "tronclass_form_captcha":
                return await _login_with_captcha(account, client, form, resolved)
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


AUTO_CAPTCHA_ATTEMPTS = 3
HUMAN_CAPTCHA_ATTEMPTS = 3


def _captcha_save_path(account: AccountContext) -> Any:
    import tempfile
    from pathlib import Path

    base = getattr(getattr(account.services, "states", None), "base_dir", None)
    root = Path(base) if base is not None else Path(tempfile.gettempdir())
    return root / "state" / "captcha" / "{}.jpg".format(account.spec.profile)


def _cleanup_captcha_file(save_path: Any) -> None:
    try:
        from pathlib import Path

        Path(save_path).unlink(missing_ok=True)
    except OSError:
        pass


def _status_from_reason(reason: str) -> str:
    if reason == "success":
        return "success"
    if reason == "rejected":
        return "rejected"
    return "captcha_failed"


def _finalize_login(account: AccountContext, resolved: Any, status: str) -> LoginState:
    if status == "success":
        _save_cookies(account)
    return _record(account, LoginState(status=status, credential_source=resolved.source))


async def _login_with_captcha(
    account: AccountContext,
    client: TronHttpClient,
    form: Any,
    resolved: Any,
) -> LoginState:
    """Auto-OCR a built-in form captcha, then fall back to a human prompt.

    Each captcha error re-fetches the form so a fresh image (and execution token)
    is used. A wrong password short-circuits to ``rejected`` instead of looping.
    The password and captcha answer stay local and never enter logs or events.
    """
    services = account.services
    solver = getattr(services, "captcha_solver", None)
    prompt = getattr(services, "captcha_prompt", None)
    save_path = _captcha_save_path(account)

    captcha_field = detect_captcha_field(form)
    if not captcha_field:
        result = await client.submit_builtin_form_login(form, resolved.user, resolved.password)
        return _finalize_login(account, resolved, _status_from_reason(result.reason))

    try:
        for _ in range(AUTO_CAPTCHA_ATTEMPTS):
            image = await client.fetch_captcha_image(form.action_url)
            answer = solver.solve(image) if solver is not None else None
            if not answer:
                break  # OCR unavailable -> hand off to the human prompt.
            result = await client.submit_builtin_form_login(
                form,
                resolved.user,
                resolved.password,
                captcha_field=captcha_field,
                captcha_answer=answer,
            )
            if result.reason != "captcha_error":
                return _finalize_login(account, resolved, _status_from_reason(result.reason))
            form = await client.fetch_login_form()
            captcha_field = detect_captcha_field(form) or captcha_field

        if prompt is None:
            return _record(
                account, LoginState(status="captcha_required", credential_source=resolved.source)
            )

        for attempt in range(HUMAN_CAPTCHA_ATTEMPTS):
            image = await client.fetch_captcha_image(form.action_url)
            answer = await prompt.prompt(image, attempt=attempt, save_path=save_path)
            if not answer:
                return _record(
                    account, LoginState(status="captcha_failed", credential_source=resolved.source)
                )
            result = await client.submit_builtin_form_login(
                form,
                resolved.user,
                resolved.password,
                captcha_field=captcha_field,
                captcha_answer=answer,
            )
            if result.reason != "captcha_error":
                return _finalize_login(account, resolved, _status_from_reason(result.reason))
            form = await client.fetch_login_form()
            captcha_field = detect_captcha_field(form) or captcha_field

        return _record(
            account, LoginState(status="captcha_failed", credential_source=resolved.source)
        )
    finally:
        _cleanup_captcha_file(save_path)
