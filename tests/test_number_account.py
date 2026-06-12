"""Account-scoped number rollcall execution tests (Phase 2.5)."""

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
    AttendanceType,
    CredentialRef,
    CredentialSource,
    SubmissionStatus,
)
from tron_roll_call_hero.account_state_repository import FileAccountStateRepository
from tron_roll_call_hero.auth_account import login_account
from tron_roll_call_hero.number_account import answer_number_rollcall
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


class NumberAccountTest(unittest.IsolatedAsyncioTestCase):
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

    def make_context(self, session, profile: str = "alpha") -> AccountContext:
        spec = AccountSpec(
            profile=profile,
            user="user1",
            provider_key="thu",
            credential_ref=CredentialRef(CredentialSource.CONFIG, profile, "user1"),
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

    async def test_direct_code_success_confirmed(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_number_rollcall(context, 42)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result.attendance_type, AttendanceType.NUMBER)
        self.assertEqual(result.profile, "alpha")
        self.assertEqual(result.provider_key, "thu")
        self.assertEqual(result.rollcall_id, "42")
        # Direct read submits exactly one code.
        self.assertEqual(len(self.fake.number_attempts), 1)
        self.assertEqual(self.fake.number_attempts[0]["body"]["numberCode"], "0001")
        self.assertEqual(context.state.completed_number.get("42"), "0001")

    async def test_brute_force_fallback_confirmed(self) -> None:
        self.fake.student_rollcalls_leaks_code = False
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_number_rollcall(context, 42)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        # Brute force walks 0000 (wrong) then 0001 (correct).
        codes = [attempt["body"]["numberCode"] for attempt in self.fake.number_attempts]
        self.assertIn("0000", codes)
        self.assertIn("0001", codes)
        self.assertEqual(context.state.completed_number.get("42"), "0001")

    async def test_unauthorized_submit_fails_with_error_code(self) -> None:
        self.fake.queue_response("number", status=401, text="unauthorized")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_number_rollcall(context, 42)

        self.assertEqual(result.status, SubmissionStatus.FAILED)
        self.assertEqual(result.error_code, "unauthorized")
        self.assertNotIn("42", context.state.completed_number)

    async def test_submitted_unconfirmed_when_verification_missing(self) -> None:
        # Server accepts the code but never marks the student present.
        self.fake.queue_response("number", status=200, json_data={"success": True})
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_number_rollcall(context, 42)

        self.assertEqual(result.status, SubmissionStatus.SUBMITTED_UNCONFIRMED)
        self.assertNotIn("42", context.state.completed_number)

    async def test_already_completed_account_is_skipped(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            context.state.completed_number["42"] = "0001"
            result = await answer_number_rollcall(context, 42)

        self.assertEqual(result.status, SubmissionStatus.SKIPPED_ALREADY_COMPLETE)
        self.assertEqual(len(self.fake.number_attempts), 0)

    async def test_account_a_completion_does_not_skip_account_b(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, profile="alpha")
                context_b = self.make_context(session_b, profile="beta")
                await login_account(context_a)
                await login_account(context_b)
                context_a.state.completed_number["42"] = "0001"

                result_a = await answer_number_rollcall(context_a, 42)
                result_b = await answer_number_rollcall(context_b, 42)

        self.assertEqual(result_a.status, SubmissionStatus.SKIPPED_ALREADY_COMPLETE)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.profile, "beta")
        self.assertEqual(context_a.state.completed_number.get("42"), "0001")
        self.assertEqual(context_b.state.completed_number.get("42"), "0001")
        # Account A skipped, so only account B touched the server.
        self.assertEqual(len(self.fake.number_attempts), 1)


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.number_account as module

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
