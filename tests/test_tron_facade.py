import copy
import sys
import tempfile
import unittest
from pathlib import Path

from tron_roll_call_hero import (
    auth_runtime,
    config_runtime,
    monitor_runtime,
    qr_runtime,
    rollcall_runtime,
    runtime_context,
    status_reports,
    tron,
)


class TronFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_config = copy.deepcopy(tron.CONFIG)
        self.original_base_dir = tron.BASE_DIR

    def tearDown(self) -> None:
        tron.CONFIG.clear()
        tron.CONFIG.update(self.original_config)
        tron.BASE_DIR = self.original_base_dir

    def test_facade_exports_legacy_symbols(self) -> None:
        for name in (
            "CONFIG",
            "BASE_DIR",
            "normalize_config",
            "login",
            "number",
            "radar",
            "check_rollcall",
            "status_report",
            "doctor_report",
            "build_arg_parser",
            "main",
        ):
            self.assertTrue(hasattr(tron, name), name)

    def test_split_modules_share_runtime_state(self) -> None:
        self.assertIs(tron, runtime_context)
        for module in (config_runtime, auth_runtime, status_reports, qr_runtime, rollcall_runtime, monitor_runtime):
            self.assertIsNot(module, runtime_context)
        self.assertIs(runtime_context.CONFIG, tron.CONFIG)
        self.assertIs(config_runtime.CONFIG, tron.CONFIG)
        self.assertIs(auth_runtime.CONFIG, tron.CONFIG)
        self.assertIs(status_reports.CONFIG, tron.CONFIG)
        self.assertIs(qr_runtime.CONFIG, tron.CONFIG)
        self.assertIs(rollcall_runtime.CONFIG, tron.CONFIG)
        self.assertIs(monitor_runtime.CONFIG, tron.CONFIG)

    def test_config_mutation_visible_through_split_modules(self) -> None:
        tron.CONFIG.clear()
        tron.CONFIG.update(tron.normalize_config({"account": {"user": "facade-user", "passwd": "facade-pass"}}))
        self.assertEqual(config_runtime.resolve_credentials()[0], "facade-user")
        self.assertEqual(runtime_context.CONFIG["account"]["user"], "facade-user")

    def test_status_report_includes_redacted_multi_account_section(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as temp_dir:
            tron.BASE_DIR = Path(temp_dir)
            tron.CONFIG.clear()
            tron.CONFIG.update(
                tron.normalize_config(
                    tron.merge_simple_and_advanced_config(
                        {
                            "now": "",
                            "accounts": [
                                {"user": "s1", "passwd": "secretpw", "school": "thu"},
                                {"user": "s2", "passwd": "otherpw", "school": "thu"},
                            ],
                            "groups": [],
                            "operating": {},
                        },
                        {},
                    )
                )
            )
            report = status_reports.status_report()

        multi = report["multi_account"]
        # Every configured profile appears, with safe runtime fields only.
        self.assertGreaterEqual(len(multi["accounts"]), 2)
        users = {account.get("user") for account in multi["accounts"]}
        self.assertTrue({"s1", "s2"} <= users)
        for account in multi["accounts"]:
            self.assertIn("profile", account)
            self.assertIn("bot_state", account)
            self.assertIn("monitor_state", account)
        self.assertIn("desired", multi)
        self.assertIn("skipped", multi)
        # No password leaks into the status JSON.
        blob = json.dumps(report, default=str)
        self.assertNotIn("secretpw", blob)
        self.assertNotIn("otherpw", blob)

    def test_base_dir_assignment_visible_through_runtime_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tron.BASE_DIR = Path(temp_dir)
            self.assertEqual(runtime_context.BASE_DIR, Path(temp_dir))
            report = status_reports.account_state_report("default")
            self.assertIn("cookie", report)
            self.assertIn("pending_qr", report)

    def test_tron_py_is_thin_facade_file(self) -> None:
        facade_path = Path(__file__).resolve().parents[1] / "tron_roll_call_hero" / "tron.py"
        self.assertLessEqual(len(facade_path.read_text(encoding="utf-8").splitlines()), 60)

    def test_legacy_impl_module_is_removed(self) -> None:
        package_dir = Path(__file__).resolve().parents[1] / "tron_roll_call_hero"
        legacy_filename = "_" + "tron_impl.py"
        legacy_module = "tron_roll_call_hero." + "_" + "tron_impl"
        self.assertFalse((package_dir / legacy_filename).exists())
        self.assertNotIn(legacy_module, sys.modules)


if __name__ == "__main__":
    unittest.main()
