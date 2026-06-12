"""Unit tests for the per-account state repository (Phase 1.3).

Each account owns an isolated directory under ``state/accounts/<profile>/`` so
two accounts never overwrite each other. Writes are atomic (temp file +
os.replace), profile names cannot traverse outside the accounts directory,
corrupt files fall back safely, and legacy single-file state/cookies migrate.
"""

import json
import shutil
import unittest
import uuid
from pathlib import Path

from tron_roll_call_hero import account_runtime_store as legacy_runtime
from tron_roll_call_hero.account_models import AccountStateSnapshot
from tron_roll_call_hero.account_state_repository import (
    AccountStateRepository,
    FileAccountStateRepository,
)


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_workspace_temp_dir() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


class DummyLoginResult:
    status = "success"
    credential_source = "config"
    ok = True
    should_auto_retry = False


class RepositoryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = make_workspace_temp_dir()
        self.repo = FileAccountStateRepository(self.base)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


class ProtocolTest(RepositoryTestBase):
    def test_file_repository_satisfies_protocol(self) -> None:
        self.assertIsInstance(self.repo, AccountStateRepository)


class RuntimePersistenceTest(RepositoryTestBase):
    def test_two_accounts_do_not_overwrite_each_other(self) -> None:
        self.repo.save(AccountStateSnapshot(profile="S1", provider_key="thu", poll_count=1))
        self.repo.save(AccountStateSnapshot(profile="S2", provider_key="tku", poll_count=2))

        s1 = self.repo.load("S1")
        s2 = self.repo.load("S2")
        self.assertEqual(s1.provider_key, "thu")
        self.assertEqual(s1.poll_count, 1)
        self.assertEqual(s2.provider_key, "tku")
        self.assertEqual(s2.poll_count, 2)

    def test_atomic_write_leaves_no_tmp_and_round_trips(self) -> None:
        self.repo.save(
            AccountStateSnapshot(profile="S1", completed_number={"5": "1234"})
        )
        runtime_path = self.repo.runtime_path("S1")
        self.assertTrue(runtime_path.exists())
        self.assertFalse(runtime_path.with_suffix(".json.tmp").exists())

        loaded = self.repo.load("S1")
        self.assertEqual(loaded.completed_number, {"5": "1234"})
        self.assertEqual(loaded.store_status, "ok")

    def test_missing_returns_safe_missing_snapshot(self) -> None:
        snapshot = self.repo.load("NEVER_SAVED")
        self.assertEqual(snapshot.profile, "NEVER_SAVED")
        self.assertEqual(snapshot.store_status, "missing")
        self.assertEqual(snapshot.poll_count, 0)

    def test_corrupt_runtime_returns_safe_fallback(self) -> None:
        self.repo.save(AccountStateSnapshot(profile="S1"))
        self.repo.runtime_path("S1").write_text("{broken", encoding="utf-8")

        snapshot = self.repo.load("S1")
        self.assertEqual(snapshot.store_status, "corrupt")
        self.assertEqual(snapshot.poll_count, 0)

    def test_list_returns_all_saved_accounts(self) -> None:
        self.repo.save(AccountStateSnapshot(profile="S1"))
        self.repo.save(AccountStateSnapshot(profile="S2"))
        profiles = sorted(snap.profile for snap in self.repo.list())
        self.assertEqual(profiles, ["S1", "S2"])

    def test_save_redacts_sensitive_values(self) -> None:
        self.repo.save(
            AccountStateSnapshot(
                profile="S1",
                last_error={"message": "password=hunter2"},
                data={"cookie": "session-cookie"},
            )
        )
        raw = self.repo.runtime_path("S1").read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)
        self.assertNotIn("session-cookie", raw)
        self.assertIn("[redacted]", raw)


class PathSafetyTest(RepositoryTestBase):
    def test_profile_names_are_normalized_to_a_directory(self) -> None:
        path = self.repo.runtime_path("S 1")
        self.assertEqual(path.parent.name, "S-1")

    def test_traversal_profile_name_stays_within_accounts_dir(self) -> None:
        accounts_dir = self.repo.accounts_dir.resolve()
        malicious = self.repo.runtime_path("../../evil").resolve()
        self.assertTrue(str(malicious).startswith(str(accounts_dir)))

        self.repo.save(AccountStateSnapshot(profile="../../evil"))
        # Nothing must be written outside the accounts directory.
        self.assertFalse((self.base.parent / "evil").exists())
        self.assertEqual(len(list(self.repo.accounts_dir.iterdir())), 1)


class CookiePersistenceTest(RepositoryTestBase):
    def test_cookies_are_isolated_per_account(self) -> None:
        self.repo.save_cookies("S1", [{"key": "sid", "value": "AAA"}])
        self.repo.save_cookies("S2", [{"key": "sid", "value": "BBB"}])

        self.assertEqual(self.repo.load_cookies("S1")[0]["value"], "AAA")
        self.assertEqual(self.repo.load_cookies("S2")[0]["value"], "BBB")
        self.assertFalse(self.repo.cookie_path("S1").with_suffix(".json.tmp").exists())

    def test_missing_cookies_return_empty_list(self) -> None:
        self.assertEqual(self.repo.load_cookies("NOPE"), [])


class LegacyMigrationTest(RepositoryTestBase):
    def test_load_falls_back_to_legacy_runtime_file(self) -> None:
        legacy_runtime.mark_monitor_state(self.base, "S1", "running")
        legacy_runtime.mark_login_result(self.base, "S1", DummyLoginResult())

        # New per-account file does not exist yet -> fall back to legacy.
        snapshot = self.repo.load("S1")
        self.assertEqual(snapshot.monitor_state, "running")
        self.assertEqual(snapshot.login.get("status"), "success")
        self.assertEqual(snapshot.store_status, "migrated")

    def test_migrate_legacy_writes_new_files_without_deleting_old(self) -> None:
        legacy_runtime.mark_monitor_state(self.base, "S1", "running")
        legacy_cookie_path = self.base / "state" / "cookies" / "S1.json"
        legacy_cookie_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_cookie_path.write_text(
            json.dumps([{"key": "sid", "value": "AAA"}]), encoding="utf-8"
        )

        report = self.repo.migrate_legacy()

        self.assertIn("S1", report["runtime"])
        self.assertTrue(self.repo.runtime_path("S1").exists())
        self.assertTrue(self.repo.cookie_path("S1").exists())
        # Non-destructive: legacy files remain.
        self.assertTrue((self.base / "state" / "account_runtime.json").exists())
        self.assertTrue(legacy_cookie_path.exists())
        # New per-account store now serves the data.
        self.assertEqual(self.repo.load("S1").monitor_state, "running")
        self.assertEqual(self.repo.load_cookies("S1")[0]["value"], "AAA")

    def test_load_cookies_falls_back_to_legacy_cookie_file(self) -> None:
        legacy_cookie_path = self.base / "state" / "cookies" / "S1.json"
        legacy_cookie_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_cookie_path.write_text(
            json.dumps([{"key": "sid", "value": "LEGACY"}]), encoding="utf-8"
        )
        self.assertEqual(self.repo.load_cookies("S1")[0]["value"], "LEGACY")


class DecouplingTest(RepositoryTestBase):
    def test_module_does_not_import_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.account_state_repository as repo_module

        tree = ast.parse(inspect.getsource(repo_module))
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("runtime_context" in name for name in imported))


if __name__ == "__main__":
    unittest.main()
