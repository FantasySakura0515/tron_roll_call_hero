"""Unit tests for AccountContext assembly (Phase 2.1).

The factory builds an account-scoped context without mutating the source config
and without copying any password into the context.
"""

import copy
import dataclasses
import unittest

from troTHU import tron
from troTHU.account_context import AccountContext, AccountContextFactory
from troTHU.account_models import (
    AccountConfig,
    AccountRuntimeState,
    AccountSpec,
    CredentialRef,
    CredentialSource,
)


def make_spec(user: str, provider: str) -> AccountSpec:
    return AccountSpec(
        profile=user,
        user=user,
        provider_key=provider,
        credential_ref=CredentialRef(CredentialSource.CONFIG, user, user),
    )


def make_config() -> dict:
    simple = {
        "now": "S1",
        "accounts": [{"user": "S1", "passwd": "SECRET", "school": "thu"}],
        "groups": [],
        "operating": {},
    }
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class DummySession:
    pass


class AccountContextFactoryTest(unittest.TestCase):
    def test_endpoints_follow_account_provider(self) -> None:
        factory = AccountContextFactory(make_config())
        thu = factory.build(make_spec("S1", "thu"), session=DummySession())
        tku = factory.build(make_spec("S2", "tku"), session=DummySession())

        self.assertIn("ilearn.thu.edu.tw", thu.endpoints.base_url)
        self.assertIn("iclass.tku.edu.tw", tku.endpoints.base_url)

    def test_factory_honors_config_provider_endpoint_override(self) -> None:
        from troTHU import providers

        config = make_config()
        config["provider"] = providers.normalize_provider_config(
            {"current": "thu", "available": {"thu": {"base_url": "https://tenant.example.edu"}}}
        )
        factory = AccountContextFactory(config)
        context = factory.build(make_spec("S1", "thu"), session=DummySession())
        self.assertIn("tenant.example.edu", context.endpoints.base_url)
        self.assertIn("tenant.example.edu", context.endpoints.rollcalls_url)

    def test_factory_does_not_mutate_source_config(self) -> None:
        config = make_config()
        snapshot = copy.deepcopy(config)
        factory = AccountContextFactory(config)
        factory.build(make_spec("S1", "thu"), session=DummySession())
        self.assertEqual(config, snapshot)

    def test_context_holds_account_config_view(self) -> None:
        factory = AccountContextFactory(make_config())
        context = factory.build(make_spec("S1", "thu"), session=DummySession())
        self.assertIsInstance(context.config, AccountConfig)
        self.assertEqual(context.config.timezone, "Asia/Taipei")

    def test_context_does_not_hold_cleartext_password(self) -> None:
        factory = AccountContextFactory(make_config())
        context = factory.build(make_spec("S1", "thu"), session=DummySession())

        field_names = {f.name for f in dataclasses.fields(context)}
        self.assertNotIn("password", field_names)
        self.assertNotIn("passwd", field_names)
        # The whole config view must not contain the cleartext password either.
        import json

        self.assertNotIn("SECRET", json.dumps(context.config.to_dict(), ensure_ascii=False))

    def test_default_state_is_fresh_runtime_state(self) -> None:
        factory = AccountContextFactory(make_config())
        context = factory.build(make_spec("S1", "thu"), session=DummySession())
        self.assertIsInstance(context.state, AccountRuntimeState)
        self.assertEqual(context.state.phase, "created")

    def test_module_does_not_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.account_context as module

        tree = ast.parse(inspect.getsource(module))
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("runtime_context" in name for name in imported))


class AccountContextTest(unittest.TestCase):
    def test_is_a_dataclass_with_expected_fields(self) -> None:
        factory = AccountContextFactory(make_config())
        context = factory.build(make_spec("S1", "thu"), session=DummySession())
        self.assertIsInstance(context, AccountContext)
        self.assertEqual(context.spec.user, "S1")
        self.assertIsNotNone(context.session)


if __name__ == "__main__":
    unittest.main()
