"""Injectable runtime services for account workers (Phase 2.1).

These are the process- and account-level collaborators that an
``AccountContext`` exposes. They are constructed once and injected, so tests can
swap in memory fakes. Passwords are resolved transiently by
:class:`CredentialResolver` and are never stored in a long-lived object.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from tron_roll_call_hero.account_models import AccountSpec, CredentialSource


_PLACEHOLDER_OPEN = ("(", "（")
_PLACEHOLDER_CLOSE = (")", "）")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _strip_value(value: Any) -> str:
    text = _text(value)
    if text.startswith(_PLACEHOLDER_OPEN) and text.endswith(_PLACEHOLDER_CLOSE):
        return ""
    return text


def _real(value: Any) -> bool:
    return bool(_strip_value(value))


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------
@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class FixedClock:
    """A controllable clock for tests."""

    def __init__(self, value: float = 0.0) -> None:
        self._value = float(value)

    def now(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += float(seconds)

    def set(self, value: float) -> None:
        self._value = float(value)


# ---------------------------------------------------------------------------
# Credentials (resolved transiently, never persisted)
# ---------------------------------------------------------------------------
@dataclass
class ResolvedCredential:
    user: str
    password: str = field(default="", repr=False)
    source: str = "missing"
    has_password: bool = False


KeyringGetter = Callable[[str, str], str]


class CredentialResolver:
    """Resolves an account password at login time, per account.

    Precedence (per account, no global active profile): keyring -> config ->
    environment matching the account user. Manual-cookie providers need no
    password. The resolved password is returned to the immediate caller only and
    is never written into a context, snapshot, log, or event.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        keyring_getter: Optional[KeyringGetter] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._keyring_getter = keyring_getter
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ
        self._config_passwords = self._index_passwords(config if isinstance(config, Mapping) else {})

    @staticmethod
    def _index_passwords(config: Mapping[str, Any]) -> Dict[str, str]:
        passwords: Dict[str, str] = {}
        meta = config.get("_simple") if isinstance(config.get("_simple"), Mapping) else {}
        accounts = meta.get("accounts") if isinstance(meta.get("accounts"), list) else []
        for item in accounts:
            if isinstance(item, Mapping):
                user = _strip_value(item.get("user"))
                if user:
                    passwords[user.lower()] = _strip_value(item.get("passwd"))
        # Fallback to normalized profiles.
        accounts_config = config.get("accounts") if isinstance(config.get("accounts"), Mapping) else {}
        profiles = accounts_config.get("profiles") if isinstance(accounts_config.get("profiles"), Mapping) else {}
        for profile in profiles.values():
            if isinstance(profile, Mapping):
                user = _strip_value(profile.get("user"))
                if user and user.lower() not in passwords:
                    passwords[user.lower()] = _strip_value(profile.get("passwd"))
        return passwords

    def resolve(self, spec: AccountSpec) -> ResolvedCredential:
        user = spec.user
        if spec.credential_ref.source == CredentialSource.MANUAL_COOKIE:
            return ResolvedCredential(user=user, source="manual_cookie", has_password=False)

        if self._keyring_getter is not None:
            keyring_pw = self._keyring_getter(spec.profile, user)
            if _real(keyring_pw):
                return ResolvedCredential(user=user, password=_text(keyring_pw), source="keyring", has_password=True)

        config_pw = self._config_passwords.get(user.lower(), "")
        if _real(config_pw):
            return ResolvedCredential(user=user, password=_text(config_pw), source="config", has_password=True)

        env_user = _text(self._environ.get("TRON_USER"))
        env_pass = _text(self._environ.get("TRON_PASS"))
        if _real(env_user) and _real(env_pass) and env_user.lower() == user.lower():
            return ResolvedCredential(user=user, password=env_pass, source="environment", has_password=True)

        return ResolvedCredential(user=user, source="missing", has_password=False)


# ---------------------------------------------------------------------------
# Cookie repository / event sink protocols
# ---------------------------------------------------------------------------
@runtime_checkable
class CookieRepository(Protocol):
    def save_cookies(self, profile: str, records: Any) -> Any: ...

    def load_cookies(self, profile: str) -> List[Dict[str, Any]]: ...

    def clear_cookies(self, profile: str) -> bool: ...


@runtime_checkable
class RuntimeEventSink(Protocol):
    def emit(self, event: Any) -> None: ...


class CollectingEventSink:
    """An in-memory event sink for tests."""

    def __init__(self) -> None:
        self.events: List[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class NullEventSink:
    def emit(self, event: Any) -> None:  # pragma: no cover - trivial
        return None


class LoggingEventSink:
    """Dual-writes ``RuntimeEvent``s onto the legacy daily JSONL log and console.

    The account worker emits :class:`RuntimeEvent`s instead of calling the legacy
    ``logging_runtime.log`` / ``log_print`` directly. When the monitor is routed
    through workers this sink bridges each event back onto those legacy outputs
    so the daily JSONL audit record and the permanent console event line that the
    old ``monitor_loop`` produced are preserved.

    Writers are injectable for tests; the production defaults are resolved lazily
    so this module keeps its boundary free of ``runtime_context``.
    """

    def __init__(
        self,
        *,
        log_writer: Optional[Callable[..., Any]] = None,
        console_writer: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self._log_writer = log_writer
        self._console_writer = console_writer

    def emit(self, event: Any) -> None:
        log_writer, console_writer = self._writers()
        extra: Dict[str, Any] = {
            "profile": getattr(event, "profile", ""),
            "provider_key": getattr(event, "provider_key", ""),
        }
        data = getattr(event, "data", None)
        if isinstance(data, Mapping):
            extra.update(dict(data))
        log_writer(
            event=getattr(event, "event", ""),
            status=getattr(event, "status", ""),
            message=getattr(event, "message", ""),
            rollcall_id=getattr(event, "rollcall_id", "") or None,
            rollcall_type=getattr(event, "attendance_type", ""),
            extra=extra,
        )
        line = self._console_line(event)
        if line:
            console_writer(line)

    def _writers(self):
        if self._log_writer is not None and self._console_writer is not None:
            return self._log_writer, self._console_writer
        from tron_roll_call_hero import logging_runtime

        return (
            self._log_writer or logging_runtime.log,
            self._console_writer or logging_runtime.log_print,
        )

    @staticmethod
    def _console_line(event: Any) -> str:
        parts = [
            "[{}]".format(getattr(event, "profile", "")),
            getattr(event, "event", ""),
            getattr(event, "status", ""),
            getattr(event, "message", ""),
        ]
        return " ".join(part for part in parts if part).strip()


# ---------------------------------------------------------------------------
# RuntimeServices bundle
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimeServices:
    """Injectable collaborators shared by account workers.

    ``notifications``, ``artifacts`` and ``teacher_qr`` are introduced in later
    phases and default to ``None`` so this bundle can grow without churn.
    """

    credentials: CredentialResolver
    cookies: CookieRepository
    states: Any  # AccountStateRepository
    events: RuntimeEventSink
    clock: Clock
    notifications: Any = None
    artifacts: Any = None
    teacher_qr: Any = None
    captcha_solver: Any = None
    captcha_prompt: Any = None
