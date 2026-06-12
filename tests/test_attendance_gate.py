"""Attendance-rate gate tests (deployable server spec 2026-06-12)."""

import unittest

from tron_roll_call_hero.attendance_gate import (
    ATTENDANCE_RATE_GATE_PERCENT,
    attendance_gate_passed,
)


class AttendanceGateTest(unittest.TestCase):
    def test_rate_at_or_above_threshold_passes(self) -> None:
        progress = {"ok": True, "present_rate_known": True, "present_rate_percent": 15.0}
        self.assertTrue(attendance_gate_passed(progress))

    def test_rate_below_threshold_blocks(self) -> None:
        progress = {"ok": True, "present_rate_known": True, "present_rate_percent": 5.0}
        self.assertFalse(attendance_gate_passed(progress))

    def test_ignore_gate_always_passes(self) -> None:
        progress = {"ok": True, "present_rate_known": True, "present_rate_percent": 0.0}
        self.assertTrue(attendance_gate_passed(progress, ignore_gate=True))

    def test_unknown_rate_blocks(self) -> None:
        self.assertFalse(attendance_gate_passed({"ok": True, "present_rate_known": False}))
        self.assertFalse(attendance_gate_passed({"ok": False}))
        self.assertFalse(attendance_gate_passed(None))

    def test_threshold_constant(self) -> None:
        self.assertEqual(ATTENDANCE_RATE_GATE_PERCENT, 15.0)


if __name__ == "__main__":
    unittest.main()
