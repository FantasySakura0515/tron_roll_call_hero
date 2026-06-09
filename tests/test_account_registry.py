"""Unit tests for AccountRegistry target resolution (Phase 1.2).

The registry turns a normalized config into passwordless AccountSpecs and
resolves the ``now`` selector into desired accounts. It must never place a
password into a spec or resolution result, and must not import runtime_context.
"""

import json
import unittest

from troTHU import tron
from troTHU.account_models import CredentialSource
from troTHU.account_registry import AccountRegistry, SkippedAccount, TargetResolution


def make_config(simple: dict) -> dict:
    simple = dict(simple)
    simple.setdefault("operating", {})
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class ListSpecsTest(unittest.TestCase):
    def test_builds_passwordless_specs_in_order_and_dedupes(self) -> None:
        config = make_config(
            {
                "now": "",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "P2", "school": "tku"},
                    {"user": "S1", "passwd": "P1", "school": "thu"},  # duplicate
                ],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        specs = registry.list_specs()

        self.assertEqual([spec.user for spec in specs], ["S1", "S2"])
        self.assertEqual([spec.provider_key for spec in specs], ["thu", "tku"])
        self.assertEqual(specs[0].credential_ref.source, CredentialSource.CONFIG)
        encoded = json.dumps([spec.to_dict() for spec in specs], ensure_ascii=False)
        self.assertNotIn("P1", encoded)
        self.assertNotIn("P2", encoded)


class GroupResolutionTest(unittest.TestCase):
    def test_thu_group_resolves_all_members(self) -> None:
        config = make_config(
            {
                "now": "class A",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "P2", "school": "thu"},
                ],
                "groups": [{"class": "A", "school": "thu", "users": ["S1", "S2"]}],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "group")
        self.assertEqual(resolution.profiles, ("S1", "S2"))
        self.assertEqual([s.user for s in registry.desired_specs()], ["S1", "S2"])

    def test_mixed_provider_group_is_allowed(self) -> None:
        config = make_config(
            {
                "now": "class A",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "P2", "school": "tku"},
                ],
                "groups": [{"class": "A", "school": "thu", "users": ["S1", "S2"]}],
            }
        )
        registry = AccountRegistry(config)
        specs = registry.desired_specs()

        self.assertEqual([s.provider_key for s in specs], ["thu", "tku"])
        # Mixed providers must NOT produce a school_mismatch skip (ADR 0001).
        self.assertEqual(registry.resolve_target().skipped, ())

    def test_missing_credential_member_is_skipped_with_reason(self) -> None:
        config = make_config(
            {
                "now": "class A",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "", "school": "thu"},
                ],
                "groups": [{"class": "A", "school": "thu", "users": ["S1", "S2"]}],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.profiles, ("S1",))
        self.assertEqual([s.user for s in resolution.skipped], ["S2"])
        self.assertEqual(resolution.skipped[0].reason, "missing_credential")

    def test_group_member_not_in_accounts_is_skipped(self) -> None:
        config = make_config(
            {
                "now": "class A",
                "accounts": [{"user": "S1", "passwd": "P1", "school": "thu"}],
                "groups": [{"class": "A", "school": "thu", "users": ["S1", "GHOST"]}],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.profiles, ("S1",))
        self.assertEqual(resolution.skipped[0].user, "GHOST")
        self.assertEqual(resolution.skipped[0].reason, "account_not_found")

    def test_unknown_group_is_invalid(self) -> None:
        config = make_config(
            {
                "now": "class Z",
                "accounts": [{"user": "S1", "passwd": "P1", "school": "thu"}],
                "groups": [{"class": "A", "school": "thu", "users": ["S1"]}],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "group")
        self.assertEqual(resolution.profiles, ())
        self.assertTrue(resolution.warnings)
        self.assertEqual(registry.desired_specs(), ())


class AccountResolutionTest(unittest.TestCase):
    def test_specific_user_target(self) -> None:
        config = make_config(
            {
                "now": "S2",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "P2", "school": "tku"},
                ],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "account")
        self.assertEqual(resolution.profiles, ("S2",))
        self.assertEqual(registry.desired_specs()[0].provider_key, "tku")

    def test_unknown_account_is_invalid(self) -> None:
        config = make_config(
            {
                "now": "GHOST",
                "accounts": [{"user": "S1", "passwd": "P1", "school": "thu"}],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "invalid")
        self.assertEqual(resolution.profiles, ())

    def test_blank_now_infers_single_account(self) -> None:
        config = make_config(
            {
                "now": "",
                "accounts": [{"user": "ONLY", "passwd": "PASS", "school": "thu"}],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "account")
        self.assertEqual(resolution.profiles, ("ONLY",))

    def test_blank_now_with_multiple_accounts_is_empty(self) -> None:
        config = make_config(
            {
                "now": "",
                "accounts": [
                    {"user": "S1", "passwd": "P1", "school": "thu"},
                    {"user": "S2", "passwd": "P2", "school": "thu"},
                ],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()

        self.assertEqual(resolution.kind, "empty")
        self.assertEqual(resolution.profiles, ())


class CredentialSourceTest(unittest.TestCase):
    def test_keyring_credential_ref_via_locator(self) -> None:
        config = make_config(
            {
                "now": "S1",
                "accounts": [{"user": "S1", "passwd": "", "school": "thu"}],
                "groups": [],
            }
        )

        def locator(profile: str, user: str):
            return CredentialSource.KEYRING if user == "S1" else None

        registry = AccountRegistry(config, credential_locator=locator)
        specs = registry.list_specs()

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].credential_ref.source, CredentialSource.KEYRING)

    def test_manual_cookie_provider_is_usable_without_password(self) -> None:
        config = make_config(
            {
                "now": "FJUUSER",
                "accounts": [{"user": "FJUUSER", "passwd": "", "school": "fju"}],
                "groups": [],
            }
        )
        registry = AccountRegistry(config)
        specs = registry.list_specs()

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].provider_key, "fju")
        self.assertEqual(specs[0].credential_ref.source, CredentialSource.MANUAL_COOKIE)

    def test_unknown_provider_is_skipped(self) -> None:
        # Craft _simple metadata directly so the school bypasses canonicalization.
        config = {
            "_simple": {
                "now": "S1",
                "accounts": [{"user": "S1", "passwd": "P1", "school": "mars"}],
                "groups": [],
            }
        }
        registry = AccountRegistry(config)

        self.assertEqual(registry.list_specs(), ())
        resolution = registry.resolve_target()
        self.assertEqual(resolution.profiles, ())
        self.assertEqual(resolution.skipped[0].reason, "unknown_provider")


class SafetyTest(unittest.TestCase):
    def test_resolution_json_has_no_password(self) -> None:
        config = make_config(
            {
                "now": "class A",
                "accounts": [
                    {"user": "S1", "passwd": "SECRET1", "school": "thu"},
                    {"user": "S2", "passwd": "SECRET2", "school": "thu"},
                ],
                "groups": [{"class": "A", "school": "thu", "users": ["S1", "S2"]}],
            }
        )
        registry = AccountRegistry(config)
        resolution = registry.resolve_target()
        encoded = json.dumps(resolution.to_dict(), ensure_ascii=False)
        self.assertNotIn("SECRET1", encoded)
        self.assertNotIn("SECRET2", encoded)

    def test_skipped_account_is_a_dataclass(self) -> None:
        skipped = SkippedAccount(user="x", reason="missing_credential")
        self.assertEqual(skipped.user, "x")
        self.assertIsInstance(TargetResolution(kind="empty", requested=""), TargetResolution)

    def test_module_does_not_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.account_registry as registry_module

        tree = ast.parse(inspect.getsource(registry_module))
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("runtime_context" in name for name in imported))


if __name__ == "__main__":
    unittest.main()
