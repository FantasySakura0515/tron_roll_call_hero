"""Account-scoped polling and progress tests (Phase 2.4)."""

import shutil
import unittest
import uuid
from pathlib import Path

import aiohttp

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.account_context import AccountContext
from tron_roll_call_hero.account_models import (
    AccountConfig,
    AccountRuntimeState,
    AccountSpec,
    CredentialRef,
    CredentialSource,
)
from tron_roll_call_hero.account_state_repository import FileAccountStateRepository
from tron_roll_call_hero.auth_account import login_account
from tron_roll_call_hero.rollcall_account import (
    account_completed,
    fetch_account_progress,
    poll_rollcall_decision,
)
from tron_roll_call_hero.runtime_services import (
    CollectingEventSink,
    CredentialResolver,
    FixedClock,
    RuntimeServices,
)
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_temp() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def make_config() -> dict:
    simple = {
        "now": "",
        "accounts": [{"user": "user1", "passwd": "pass1", "school": "thu"}],
        "groups": [],
        "operating": {},
    }
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class RollcallAccountTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer().start()
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.config = make_config()

    async def asyncTearDown(self) -> None:
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    def make_context(self, session) -> AccountContext:
        spec = AccountSpec(
            profile="alpha",
            user="user1",
            provider_key="thu",
            credential_ref=CredentialRef(CredentialSource.CONFIG, "alpha", "user1"),
        )
        services = RuntimeServices(
            credentials=CredentialResolver(self.config, environ={}),
            cookies=self.repo,
            states=self.repo,
            events=CollectingEventSink(),
            clock=FixedClock(0.0),
        )
        return AccountContext(
            spec=spec,
            config=AccountConfig.from_config(self.config),
            endpoints=self.fake.endpoints(),
            session=session,
            state=AccountRuntimeState(),
            services=services,
        )

    async def test_poll_decision_detects_number_and_counts(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42}]
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            decision = await poll_rollcall_decision(context)

        self.assertEqual(decision.status, "is_number")
        self.assertEqual(context.state.poll_count, 1)

    async def test_poll_decision_not_call_when_empty(self) -> None:
        self.fake.rollcalls = []
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            decision = await poll_rollcall_decision(context)

        self.assertEqual(decision.status, "not_call")

    async def test_fetch_progress_uses_account_user_and_writes_state(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            summary = await fetch_account_progress(context, 42)

        self.assertTrue(summary.get("ok"))
        self.assertEqual(summary.get("my_user_no"), "user1")
        self.assertTrue(context.state.last_progress)

    async def test_account_completed_reads_account_state(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            context.state.completed_number["42"] = "0001"
            self.assertTrue(account_completed(context, "number", "42"))
            self.assertFalse(account_completed(context, "number", "99"))
            self.assertFalse(account_completed(context, "radar", "42"))


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.rollcall_account as module

        tree = ast.parse(inspect.getsource(module))
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("runtime_context" in name for name in imported))


if __name__ == "__main__":
    unittest.main()
