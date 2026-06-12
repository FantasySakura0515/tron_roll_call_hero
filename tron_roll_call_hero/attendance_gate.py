"""Attendance-rate gate for the account path (deployable server spec).

Mirrors the legacy ``monitor_runtime._attendance_rate_gate_passed``: do not
submit a detected rollcall until a real share of the class has signed in, so a
teacher's accidental open-then-close "fake" rollcall does not sign the student
in. Fails closed when the rate is unknown, unless the gate is ignored.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from typing import Any, Mapping

ATTENDANCE_RATE_GATE_PERCENT = 15.0


def attendance_gate_passed(progress: Any, *, ignore_gate: bool = False) -> bool:
    if ignore_gate:
        return True
    if not isinstance(progress, Mapping) or not progress.get("ok") or not progress.get("present_rate_known"):
        return False
    try:
        return float(progress.get("present_rate_percent") or 0.0) >= ATTENDANCE_RATE_GATE_PERCENT
    except (TypeError, ValueError):
        return False
