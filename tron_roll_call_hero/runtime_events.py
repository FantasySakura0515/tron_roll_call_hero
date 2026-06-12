"""Account-aware runtime events (Phase 2.3).

A single event envelope that always carries ``profile`` and ``provider_key`` so
logs, status, and notifications can identify the account without leaking
secrets. Notification dedupe keys include the profile, so one account's result
never suppresses another account's.

The legacy ``logging_runtime.log`` and ``notification_bus`` are left intact; the
account worker emits ``RuntimeEvent`` to a ``RuntimeEventSink`` in addition to
the existing console logging during the migration.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from tron_roll_call_hero.account_models import sanitize_text, sanitize_value


@dataclass(frozen=True)
class RuntimeEvent:
    event: str
    profile: str
    provider_key: str
    status: str = ""
    message: str = ""
    rollcall_id: str = ""
    attendance_type: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Sanitize data at construction so no secret can ever live on the event.
        object.__setattr__(self, "data", sanitize_value(dict(self.data) if isinstance(self.data, Mapping) else {}))

    def dedupe_key(self) -> str:
        return "|".join(
            (self.profile, self.event, str(self.rollcall_id), str(self.attendance_type))
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "profile": self.profile,
            "provider_key": self.provider_key,
            "status": self.status,
            "message": sanitize_text(self.message),
            "rollcall_id": str(self.rollcall_id),
            "attendance_type": str(self.attendance_type),
            "data": dict(self.data),
        }


def account_event(
    event: str,
    *,
    profile: str,
    provider_key: str,
    status: str = "",
    message: str = "",
    rollcall_id: Any = "",
    attendance_type: str = "",
    data: Optional[Mapping[str, Any]] = None,
) -> RuntimeEvent:
    if not str(profile or "").strip():
        raise ValueError("account event requires a non-empty profile")
    return RuntimeEvent(
        event=event,
        profile=profile,
        provider_key=provider_key,
        status=status,
        message=message,
        rollcall_id=str(rollcall_id or ""),
        attendance_type=attendance_type,
        data=data or {},
    )


def group_event(
    name: str,
    event: str,
    *,
    provider_key: str = "",
    status: str = "",
    message: str = "",
    data: Optional[Mapping[str, Any]] = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event=event,
        profile="group:{}".format(name),
        provider_key=provider_key,
        status=status,
        message=message,
        data=data or {},
    )
