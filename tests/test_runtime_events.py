"""Unit tests for account-aware runtime events (Phase 2.3)."""

import json
import unittest

from troTHU.runtime_events import RuntimeEvent, account_event, group_event


class AccountEventTest(unittest.TestCase):
    def test_account_event_requires_profile(self) -> None:
        with self.assertRaises(ValueError):
            account_event("login", profile="", provider_key="thu", status="success")

    def test_account_event_carries_identity_in_json(self) -> None:
        event = account_event("login", profile="s1", provider_key="thu", status="success")
        data = json.loads(json.dumps(event.to_dict(), ensure_ascii=False))
        self.assertEqual(data["profile"], "s1")
        self.assertEqual(data["provider_key"], "thu")
        self.assertEqual(data["status"], "success")

    def test_event_data_redacts_secrets(self) -> None:
        event = account_event(
            "qr_submit",
            profile="s1",
            provider_key="thu",
            data={"cookie": "abc", "qr_data": "secret", "note": "ok"},
        )
        encoded = json.dumps(event.to_dict(), ensure_ascii=False)
        self.assertNotIn("abc", encoded)
        self.assertNotIn("secret", encoded)
        self.assertIn("ok", encoded)


class GroupEventTest(unittest.TestCase):
    def test_group_event_profile_namespace(self) -> None:
        event = group_event("A", "group_started", provider_key="thu")
        self.assertEqual(event.profile, "group:A")


class DedupeTest(unittest.TestCase):
    def test_same_rollcall_different_accounts_do_not_dedupe(self) -> None:
        a = account_event("rollcall_done", profile="s1", provider_key="thu", rollcall_id="99", attendance_type="number")
        b = account_event("rollcall_done", profile="s2", provider_key="thu", rollcall_id="99", attendance_type="number")
        self.assertNotEqual(a.dedupe_key(), b.dedupe_key())

    def test_same_account_same_rollcall_dedupes(self) -> None:
        a = account_event("rollcall_done", profile="s1", provider_key="thu", rollcall_id="99", attendance_type="number")
        b = account_event("rollcall_done", profile="s1", provider_key="thu", rollcall_id="99", attendance_type="number")
        self.assertEqual(a.dedupe_key(), b.dedupe_key())


class FrozenTest(unittest.TestCase):
    def test_event_is_frozen(self) -> None:
        import dataclasses

        event = RuntimeEvent(event="x", profile="s1", provider_key="thu")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.status = "y"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
