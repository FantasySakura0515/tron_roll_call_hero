"""Pure domain models for the multi-account runtime (Phase 1.1).

This module is intentionally self-contained:

- It must never import ``tron_roll_call_hero.runtime_context`` (architecture constraint).
- It must never carry cleartext passwords, cookies, or QR data in any model,
  serialized snapshot, or ``repr``.

The framework document (``docs/architecture/multi-account-framework.md``)
sketches some fields with types that do not yet exist in the codebase
(``LoginResult`` lives in the forbidden ``runtime_context`` module;
``MonitorPhase``/``LoginRetryState``/``UnsupportedRollcallState`` are not
defined anywhere). To keep these models decoupled, runtime values such as the
login outcome are represented by small local value objects and adapted from the
legacy types via duck typing (see :meth:`LoginState.from_login_result`).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple


RUNTIME_STATE_VERSION = 1
MAX_TEXT_LENGTH = 200

# Keys whose values must never be persisted or logged in cleartext.
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|passwd|password|secret|session|token|payload|qr_data|raw|response|body)",
    re.IGNORECASE,
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(authorization|cookie|passwd|password|secret|session|token|payload)=\S+"
)


def sanitize_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Redact inline ``key=value`` secrets and truncate overly long text."""
    text = str(value if value is not None else "")
    text = SENSITIVE_ASSIGNMENT_RE.sub(r"\1=[redacted]", text)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def sanitize_value(value: Any) -> Any:
    """Recursively redact sensitive keys and inline secrets from JSON-able data."""
    if isinstance(value, Mapping):
        safe: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text):
                safe[key_text] = "[redacted]"
            else:
                safe[key_text] = sanitize_value(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(value)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class CredentialSource(str, Enum):
    CONFIG = "config"
    KEYRING = "keyring"
    ENVIRONMENT = "environment"
    MANUAL_COOKIE = "manual_cookie"


class AttendanceType(str, Enum):
    NUMBER = "number"
    RADAR = "radar"
    QR = "qr"
    UNKNOWN = "unknown"


class SubmissionStatus(str, Enum):
    CONFIRMED = "confirmed"
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    SKIPPED_ALREADY_COMPLETE = "skipped_already_complete"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    LOGIN_FAILED = "login_failed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Immutable account specification (no secrets)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScheduleSpec:
    """How a worker decides when to monitor.

    The first multi-account release reuses the global ``operating`` schedule for
    every account (``source="global"``); per-account overrides are future work.
    """

    source: str = "global"


@dataclass(frozen=True)
class CredentialRef:
    """A reference to *where* a credential lives, never the credential itself."""

    source: CredentialSource
    profile: str
    user: str

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source.value, "profile": self.profile, "user": self.user}


@dataclass(frozen=True)
class AccountSpec:
    """Immutable, passwordless execution spec for one account."""

    profile: str
    user: str
    provider_key: str
    credential_ref: CredentialRef
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "user": self.user,
            "provider_key": self.provider_key,
            "credential_ref": self.credential_ref.to_dict(),
            "schedule": {"source": self.schedule.source},
            "enabled": self.enabled,
        }

    def fingerprint(self) -> Tuple[Any, ...]:
        """Secret-free identity used by the supervisor reconcile diff."""
        return (
            self.profile,
            self.user,
            self.provider_key,
            self.credential_ref.source.value,
            self.schedule.source,
            self.enabled,
        )


# ---------------------------------------------------------------------------
# Immutable per-account config view
# ---------------------------------------------------------------------------
def _sanitize_config_section(value: Any) -> Any:
    """Like :func:`sanitize_value`, but also redacts notification credential
    material. Within the config sections that ``AccountConfig`` exposes, a field
    literally named ``key`` only appears as a notification bot token, so it is
    treated as a secret here (the generic sanitizer leaves bare ``key`` alone)."""
    if isinstance(value, Mapping):
        safe: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text) or key_text.lower() == "key":
                safe[key_text] = "[redacted]"
            else:
                safe[key_text] = _sanitize_config_section(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_config_section(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(value)


def _read_only(section: Any) -> Mapping[str, Any]:
    sanitized = _sanitize_config_section(section if isinstance(section, Mapping) else {})
    return MappingProxyType(dict(sanitized))


@dataclass(frozen=True)
class AccountConfig:
    """A read-only, secret-free view of the config relevant to one account.

    Sections are deep-copied and sanitized so mutating the source config (or the
    view) cannot affect the other, and no secret material leaks into the model.
    """

    timezone: str
    http: Mapping[str, Any]
    monitor: Mapping[str, Any]
    number: Mapping[str, Any]
    radar: Mapping[str, Any]
    notifications: Mapping[str, Any]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "AccountConfig":
        config = config if isinstance(config, Mapping) else {}
        time_config = config.get("time") if isinstance(config.get("time"), Mapping) else {}
        timezone = str(time_config.get("timezone") or "UTC")
        return cls(
            timezone=timezone,
            # The runtime HTTP settings live under the legacy ``config`` section.
            http=_read_only(copy.deepcopy(config.get("config"))),
            monitor=_read_only(copy.deepcopy(config.get("monitor"))),
            number=_read_only(copy.deepcopy(config.get("number"))),
            radar=_read_only(copy.deepcopy(config.get("radar"))),
            notifications=_read_only(copy.deepcopy(config.get("notifications"))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timezone": self.timezone,
            "http": dict(self.http),
            "monitor": dict(self.monitor),
            "number": dict(self.number),
            "radar": dict(self.radar),
            "notifications": dict(self.notifications),
        }


# ---------------------------------------------------------------------------
# Mutable runtime state owned by exactly one worker
# ---------------------------------------------------------------------------
@dataclass
class LoginState:
    status: str = "missing_credentials"
    credential_source: str = "missing"
    auto_retry: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "success"

    @classmethod
    def from_login_result(cls, result: Any) -> "LoginState":
        """Adapt any object exposing ``status``/``credential_source``/...

        Accepts the legacy ``runtime_context.LoginResult`` without importing it.
        """
        if result is None:
            return cls()
        return cls(
            status=str(getattr(result, "status", "") or "missing_credentials"),
            credential_source=str(getattr(result, "credential_source", "") or "missing"),
            auto_retry=bool(getattr(result, "should_auto_retry", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": sanitize_text(self.status, limit=60),
            "credential_source": sanitize_text(self.credential_source, limit=60),
            "ok": self.ok,
            "should_auto_retry": self.auto_retry,
        }


@dataclass
class RetryState:
    attempts: int = 0
    next_delay_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"attempts": self.attempts, "next_delay_seconds": self.next_delay_seconds}


@dataclass
class AccountRuntimeState:
    """Mutable state owned by exactly one worker. Never written directly to disk;
    the worker converts it to an :class:`AccountStateSnapshot` for persistence."""

    phase: str = "created"
    poll_count: int = 0
    login_in_progress: bool = False
    login: LoginState = field(default_factory=LoginState)
    completed_number: Dict[str, str] = field(default_factory=dict)
    completed_radar: set = field(default_factory=set)
    completed_qr: set = field(default_factory=set)
    last_progress: Dict[str, Any] = field(default_factory=dict)
    retry: RetryState = field(default_factory=RetryState)
    last_error: Optional[Dict[str, Any]] = None

    def to_snapshot(self, *, profile: str, provider_key: str = "") -> "AccountStateSnapshot":
        return AccountStateSnapshot(
            profile=profile,
            provider_key=provider_key,
            phase=self.phase,
            poll_count=self.poll_count,
            login=self.login.to_dict(),
            last_error=dict(self.last_error) if isinstance(self.last_error, Mapping) else {},
            completed_number=dict(self.completed_number),
            completed_radar=sorted(str(item) for item in self.completed_radar),
            completed_qr=sorted(str(item) for item in self.completed_qr),
            last_progress=dict(self.last_progress),
            retry=self.retry.to_dict(),
        )


# ---------------------------------------------------------------------------
# Serializable persistence snapshot (sanitized, JSON-safe)
# ---------------------------------------------------------------------------
def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AccountStateSnapshot:
    """The persisted, secret-free runtime snapshot for one account."""

    profile: str
    provider_key: str = ""
    version: int = RUNTIME_STATE_VERSION
    updated_at: float = 0.0
    phase: str = "created"
    poll_count: int = 0
    monitor_state: str = "unknown"
    bot_state: str = "stopped"
    heartbeat_at: Optional[float] = None
    login: Dict[str, Any] = field(default_factory=dict)
    last_check: Dict[str, Any] = field(default_factory=dict)
    last_error: Dict[str, Any] = field(default_factory=dict)
    completed_number: Dict[str, str] = field(default_factory=dict)
    completed_radar: List[str] = field(default_factory=list)
    completed_qr: List[str] = field(default_factory=list)
    last_progress: Dict[str, Any] = field(default_factory=dict)
    retry: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    store_status: str = "ok"

    def to_dict(self, *, include_meta: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "version": int(self.version),
            "profile": self.profile,
            "provider_key": self.provider_key,
            "updated_at": _coerce_float(self.updated_at),
            "phase": sanitize_text(self.phase, limit=40),
            "poll_count": _coerce_int(self.poll_count),
            "monitor_state": sanitize_text(self.monitor_state, limit=40),
            "bot_state": sanitize_text(self.bot_state, limit=40),
            "heartbeat_at": self.heartbeat_at,
            "login": sanitize_value(self.login),
            "last_check": sanitize_value(self.last_check),
            "last_error": sanitize_value(self.last_error),
            "completed_number": sanitize_value(self.completed_number),
            "completed_radar": [str(item) for item in self.completed_radar],
            "completed_qr": [str(item) for item in self.completed_qr],
            "last_progress": sanitize_value(self.last_progress),
            "retry": sanitize_value(self.retry),
            "data": sanitize_value(self.data),
        }
        if include_meta:
            payload["store_status"] = self.store_status
        return payload

    @classmethod
    def from_dict(cls, profile: str, data: Any, *, store_status: str = "ok") -> "AccountStateSnapshot":
        if not isinstance(data, Mapping):
            return cls(profile=profile, store_status="corrupt" if data is not None else store_status)

        def _mapping(value: Any) -> Dict[str, Any]:
            return dict(sanitize_value(value)) if isinstance(value, Mapping) else {}

        def _str_list(value: Any) -> List[str]:
            if isinstance(value, (list, tuple, set)):
                return [str(item) for item in value]
            return []

        completed_number = data.get("completed_number")
        completed_number = (
            {str(k): str(v) for k, v in completed_number.items()}
            if isinstance(completed_number, Mapping)
            else {}
        )
        heartbeat = data.get("heartbeat_at")
        return cls(
            profile=profile,
            provider_key=str(data.get("provider_key", "") or ""),
            version=_coerce_int(data.get("version", RUNTIME_STATE_VERSION), RUNTIME_STATE_VERSION),
            updated_at=_coerce_float(data.get("updated_at")),
            phase=str(data.get("phase", "created") or "created"),
            poll_count=_coerce_int(data.get("poll_count")),
            monitor_state=str(data.get("monitor_state", "unknown") or "unknown"),
            bot_state=str(data.get("bot_state", "stopped") or "stopped"),
            heartbeat_at=_coerce_float(heartbeat) if heartbeat is not None else None,
            login=_mapping(data.get("login")),
            last_check=_mapping(data.get("last_check")),
            last_error=_mapping(data.get("last_error")),
            completed_number=completed_number,
            completed_radar=_str_list(data.get("completed_radar")),
            completed_qr=_str_list(data.get("completed_qr")),
            last_progress=_mapping(data.get("last_progress")),
            retry=_mapping(data.get("retry")),
            data=_mapping(data.get("data")),
            store_status=store_status,
        )


# ---------------------------------------------------------------------------
# Observable, secret-free worker snapshot (status/console/Bot)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AccountWorkerSnapshot:
    profile: str
    provider_key: str
    phase: str
    login_status: str = "unknown"
    poll_count: int = 0
    last_check_status: str = ""
    last_error_code: str = ""
    healthy: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "provider_key": self.provider_key,
            "phase": self.phase,
            "login_status": sanitize_text(self.login_status, limit=60),
            "poll_count": self.poll_count,
            "last_check_status": sanitize_text(self.last_check_status, limit=80),
            "last_error_code": sanitize_text(self.last_error_code, limit=80),
            "healthy": self.healthy,
        }


# ---------------------------------------------------------------------------
# Submission results (per-account and aggregated group)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SubmissionResult:
    profile: str
    provider_key: str
    rollcall_id: str
    attendance_type: AttendanceType
    status: SubmissionStatus
    error_code: str = ""

    @property
    def ok(self) -> bool:
        return self.status == SubmissionStatus.CONFIRMED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "provider_key": self.provider_key,
            "rollcall_id": self.rollcall_id,
            "attendance_type": self.attendance_type.value,
            "status": self.status.value,
            "error_code": sanitize_text(self.error_code, limit=80),
        }


@dataclass(frozen=True)
class GroupSubmissionResult:
    rollcall_id: str
    results: Tuple[SubmissionResult, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(result.ok for result in self.results)

    def counts(self) -> Dict[str, int]:
        tally: Dict[str, int] = {}
        for result in self.results:
            tally[result.status.value] = tally.get(result.status.value, 0) + 1
        return tally

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rollcall_id": self.rollcall_id,
            "ok": self.ok,
            "counts": self.counts(),
            "results": [result.to_dict() for result in self.results],
        }
