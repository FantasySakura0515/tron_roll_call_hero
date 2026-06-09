"""Unit tests for the pure multi-account domain models (Phase 1.1).

These models must stay decoupled from troTHU.runtime_context and must never
carry cleartext passwords, cookies, or QR data.
"""

import dataclasses
import json
import unittest

from troTHU.account_models import (
    AccountConfig,
    AccountRuntimeState,
    AccountSpec,
    AccountStateSnapshot,
    AccountWorkerSnapshot,
    AttendanceType,
    CredentialRef,
    CredentialSource,
    GroupSubmissionResult,
    LoginState,
    ScheduleSpec,
    SubmissionResult,
    SubmissionStatus,
)


SECRET_FIELD_NAMES = {"passwd", "password", "secret", "cookie", "cookies", "qr_data", "token"}


def _field_names(obj) -> set:
    return {f.name for f in dataclasses.fields(obj)}


class EnumTest(unittest.TestCase):
    def test_credential_source_values(self) -> None:
        self.assertEqual(CredentialSource.CONFIG.value, "config")
        self.assertEqual(CredentialSource.KEYRING.value, "keyring")
        self.assertEqual(CredentialSource.ENVIRONMENT.value, "environment")
        self.assertEqual(CredentialSource.MANUAL_COOKIE.value, "manual_cookie")

    def test_submission_status_values(self) -> None:
        self.assertEqual(SubmissionStatus.CONFIRMED.value, "confirmed")
        self.assertEqual(SubmissionStatus.SUBMITTED_UNCONFIRMED.value, "submitted_unconfirmed")
        self.assertEqual(SubmissionStatus.SKIPPED_ALREADY_COMPLETE.value, "skipped_already_complete")
        self.assertEqual(SubmissionStatus.SKIPPED_NOT_APPLICABLE.value, "skipped_not_applicable")
        self.assertEqual(SubmissionStatus.LOGIN_FAILED.value, "login_failed")
        self.assertEqual(SubmissionStatus.FAILED.value, "failed")

    def test_attendance_type_values(self) -> None:
        self.assertEqual(AttendanceType.NUMBER.value, "number")
        self.assertEqual(AttendanceType.RADAR.value, "radar")
        self.assertEqual(AttendanceType.QR.value, "qr")


class CredentialRefTest(unittest.TestCase):
    def test_credential_ref_is_frozen_and_passwordless(self) -> None:
        ref = CredentialRef(source=CredentialSource.CONFIG, profile="s1", user="s1")
        self.assertEqual(ref.source, CredentialSource.CONFIG)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ref.user = "other"  # type: ignore[misc]
        self.assertFalse(_field_names(ref) & SECRET_FIELD_NAMES)

    def test_credential_ref_equality(self) -> None:
        a = CredentialRef(CredentialSource.KEYRING, "p", "u")
        b = CredentialRef(CredentialSource.KEYRING, "p", "u")
        self.assertEqual(a, b)


class AccountSpecTest(unittest.TestCase):
    def make_spec(self, **overrides) -> AccountSpec:
        params = dict(
            profile="s1",
            user="s1",
            provider_key="thu",
            credential_ref=CredentialRef(CredentialSource.CONFIG, "s1", "s1"),
        )
        params.update(overrides)
        return AccountSpec(**params)

    def test_spec_is_frozen_and_has_no_secret_fields(self) -> None:
        spec = self.make_spec()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.user = "x"  # type: ignore[misc]
        self.assertFalse(_field_names(spec) & SECRET_FIELD_NAMES)

    def test_spec_defaults(self) -> None:
        spec = self.make_spec()
        self.assertTrue(spec.enabled)
        self.assertEqual(spec.schedule, ScheduleSpec())

    def test_spec_equality(self) -> None:
        self.assertEqual(self.make_spec(), self.make_spec())
        self.assertNotEqual(self.make_spec(), self.make_spec(provider_key="tku"))

    def test_spec_to_dict_has_no_password(self) -> None:
        spec = self.make_spec()
        encoded = json.dumps(spec.to_dict(), ensure_ascii=False)
        self.assertIn("thu", encoded)
        self.assertNotIn("passwd", encoded)
        self.assertNotIn("password", encoded)

    def test_fingerprint_changes_with_provider_but_not_with_schedule_identity(self) -> None:
        a = self.make_spec()
        b = self.make_spec()
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertNotEqual(a.fingerprint(), self.make_spec(provider_key="tku").fingerprint())
        self.assertNotEqual(
            a.fingerprint(),
            self.make_spec(credential_ref=CredentialRef(CredentialSource.KEYRING, "s1", "s1")).fingerprint(),
        )


class AccountConfigTest(unittest.TestCase):
    def make_config(self) -> dict:
        return {
            "time": {"timezone": "Asia/Taipei"},
            "config": {"http_timeout": 10.0, "verify_ssl": True},
            "monitor": {"ignore_attendance_rate_gate": False},
            "number": {"concurrency": 50},
            "radar": {"strategy": "empty_answer"},
            "notifications": {"tg": {"enable": False, "key": "should-not-leak-token"}},
        }

    def test_from_config_is_isolated_from_source_mutation(self) -> None:
        source = self.make_config()
        account_config = AccountConfig.from_config(source)
        source["number"]["concurrency"] = 9999
        self.assertEqual(account_config.number["concurrency"], 50)
        self.assertEqual(account_config.timezone, "Asia/Taipei")

    def test_sections_are_read_only(self) -> None:
        account_config = AccountConfig.from_config(self.make_config())
        with self.assertRaises(TypeError):
            account_config.monitor["ignore_attendance_rate_gate"] = True  # type: ignore[index]

    def test_secret_values_are_redacted_from_config_view(self) -> None:
        account_config = AccountConfig.from_config(self.make_config())
        encoded = json.dumps(account_config.to_dict(), ensure_ascii=False)
        self.assertNotIn("should-not-leak-token", encoded)


class LoginStateTest(unittest.TestCase):
    def test_from_login_result_duck_types_without_importing_runtime_context(self) -> None:
        class FakeLoginResult:
            status = "success"
            credential_source = "config"
            ok = True
            should_auto_retry = False

        state = LoginState.from_login_result(FakeLoginResult())
        self.assertTrue(state.ok)
        self.assertEqual(state.status, "success")
        self.assertEqual(state.credential_source, "config")

    def test_default_login_state_is_not_ok(self) -> None:
        self.assertFalse(LoginState().ok)


class AccountRuntimeStateTest(unittest.TestCase):
    def test_defaults(self) -> None:
        state = AccountRuntimeState()
        self.assertEqual(state.phase, "created")
        self.assertEqual(state.poll_count, 0)
        self.assertEqual(state.completed_number, {})
        self.assertEqual(state.completed_radar, set())
        self.assertEqual(state.completed_qr, set())

    def test_to_snapshot_carries_identity_and_completed_state(self) -> None:
        state = AccountRuntimeState(poll_count=3)
        state.completed_number["88"] = "0837"
        state.completed_radar.add("90")
        snapshot = state.to_snapshot(profile="s1", provider_key="thu")
        self.assertEqual(snapshot.profile, "s1")
        self.assertEqual(snapshot.provider_key, "thu")
        self.assertEqual(snapshot.poll_count, 3)
        self.assertEqual(snapshot.completed_number, {"88": "0837"})
        self.assertEqual(sorted(snapshot.completed_radar), ["90"])


class AccountStateSnapshotTest(unittest.TestCase):
    def test_round_trip_through_dict(self) -> None:
        snapshot = AccountStateSnapshot(
            profile="s1",
            provider_key="thu",
            poll_count=2,
            completed_number={"5": "1234"},
            completed_radar=["7"],
        )
        restored = AccountStateSnapshot.from_dict("s1", snapshot.to_dict())
        self.assertEqual(restored.profile, "s1")
        self.assertEqual(restored.provider_key, "thu")
        self.assertEqual(restored.poll_count, 2)
        self.assertEqual(restored.completed_number, {"5": "1234"})
        self.assertEqual(restored.completed_radar, ["7"])

    def test_to_dict_redacts_sensitive_values(self) -> None:
        snapshot = AccountStateSnapshot(
            profile="s1",
            last_error={"message": "password=hunter2 token=abc"},
            data={"cookie": "session-cookie", "note": "ok"},
        )
        encoded = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        self.assertNotIn("hunter2", encoded)
        self.assertNotIn("session-cookie", encoded)
        self.assertIn("[redacted]", encoded)

    def test_from_dict_is_corrupt_safe(self) -> None:
        snapshot = AccountStateSnapshot.from_dict("s1", "not-a-mapping")
        self.assertEqual(snapshot.profile, "s1")
        self.assertEqual(snapshot.poll_count, 0)

    def test_snapshot_has_no_secret_fields(self) -> None:
        self.assertFalse(_field_names(AccountStateSnapshot(profile="s1")) & SECRET_FIELD_NAMES)


class AccountWorkerSnapshotTest(unittest.TestCase):
    def test_to_dict_and_no_secret_fields(self) -> None:
        snapshot = AccountWorkerSnapshot(
            profile="s1",
            provider_key="thu",
            phase="monitoring",
            login_status="success",
            poll_count=4,
        )
        data = snapshot.to_dict()
        self.assertEqual(data["profile"], "s1")
        self.assertEqual(data["phase"], "monitoring")
        self.assertFalse(_field_names(snapshot) & SECRET_FIELD_NAMES)


class SubmissionResultTest(unittest.TestCase):
    def make_result(self, status: SubmissionStatus, profile: str = "s1") -> SubmissionResult:
        return SubmissionResult(
            profile=profile,
            provider_key="thu",
            rollcall_id="99",
            attendance_type=AttendanceType.NUMBER,
            status=status,
        )

    def test_ok_only_for_confirmed(self) -> None:
        self.assertTrue(self.make_result(SubmissionStatus.CONFIRMED).ok)
        self.assertFalse(self.make_result(SubmissionStatus.FAILED).ok)
        self.assertFalse(self.make_result(SubmissionStatus.SUBMITTED_UNCONFIRMED).ok)

    def test_to_dict_uses_enum_values(self) -> None:
        data = self.make_result(SubmissionStatus.CONFIRMED).to_dict()
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["attendance_type"], "number")
        self.assertEqual(data["profile"], "s1")

    def test_group_aggregation_counts_and_ok(self) -> None:
        group = GroupSubmissionResult(
            rollcall_id="99",
            results=(
                self.make_result(SubmissionStatus.CONFIRMED, "s1"),
                self.make_result(SubmissionStatus.FAILED, "s2"),
            ),
        )
        counts = group.counts()
        self.assertEqual(counts["confirmed"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertFalse(group.ok)

        all_ok = GroupSubmissionResult(
            rollcall_id="99",
            results=(
                self.make_result(SubmissionStatus.CONFIRMED, "s1"),
                self.make_result(SubmissionStatus.CONFIRMED, "s2"),
            ),
        )
        self.assertTrue(all_ok.ok)

    def test_group_to_dict_is_json_safe(self) -> None:
        group = GroupSubmissionResult(
            rollcall_id="99",
            results=(self.make_result(SubmissionStatus.CONFIRMED),),
        )
        encoded = json.dumps(group.to_dict(), ensure_ascii=False)
        self.assertIn("confirmed", encoded)


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.account_models as models

        tree = ast.parse(inspect.getsource(models))
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(
            any("runtime_context" in name for name in imported),
            f"account_models must not import runtime_context; imports were {imported}",
        )


if __name__ == "__main__":
    unittest.main()
