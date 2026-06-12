"""Account-scoped radar rollcall execution tests (Phase 2.6)."""

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
from tron_roll_call_hero.global_radar_solver import global_anchor_points
from tron_roll_call_hero.radar_account import answer_radar_rollcall
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


ROLLCALL = {"is_radar": True, "rollcall_id": 55}


class RadarAccountTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_empty_answer_success_confirmed(self) -> None:
        self.fake.radar_empty_answer_accepted = True
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_radar_rollcall(context, ROLLCALL)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result.attendance_type, AttendanceType.RADAR)
        self.assertEqual(result.profile, "alpha")
        self.assertEqual(result.rollcall_id, "55")
        # Empty answer means exactly one coordinate-free submission.
        self.assertEqual(len(self.fake.radar_answers), 1)
        self.assertNotIn("latitude", self.fake.radar_answers[0]["body"])
        self.assertIn("55", context.state.completed_radar)

    async def test_global_solver_fallback_confirmed(self) -> None:
        # Empty answer is rejected; the first global anchor lands on the target.
        anchor = global_anchor_points(12)[0]
        self.fake.set_radar_target(anchor.lat, anchor.lon, success_radius_meters=5.0)
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_radar_rollcall(context, ROLLCALL)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        self.assertIn("55", context.state.completed_radar)
        # First answer is the rejected empty answer, then coordinates follow.
        self.assertGreaterEqual(len(self.fake.radar_answers), 2)
        self.assertNotIn("latitude", self.fake.radar_answers[0]["body"])
        self.assertIn("latitude", self.fake.radar_answers[-1]["body"])

    async def test_submitted_unconfirmed_empty_answer(self) -> None:
        self.fake.radar_empty_answer_accepted = True
        self.fake.radar_empty_answer_marks_present = False
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_radar_rollcall(context, ROLLCALL)

        self.assertEqual(result.status, SubmissionStatus.SUBMITTED_UNCONFIRMED)
        self.assertNotIn("55", context.state.completed_radar)

    async def test_unauthorized_submit_fails_with_error_code(self) -> None:
        self.fake.queue_response("radar", status=401, text="unauthorized")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await answer_radar_rollcall(context, ROLLCALL)

        self.assertEqual(result.status, SubmissionStatus.FAILED)
        self.assertEqual(result.error_code, "unauthorized")
        self.assertNotIn("55", context.state.completed_radar)

    async def test_account_a_completion_does_not_skip_account_b(self) -> None:
        self.fake.radar_empty_answer_accepted = True
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, profile="alpha")
                context_b = self.make_context(session_b, profile="beta")
                await login_account(context_a)
                await login_account(context_b)
                context_a.state.completed_radar.add("55")

                result_a = await answer_radar_rollcall(context_a, ROLLCALL)
                result_b = await answer_radar_rollcall(context_b, ROLLCALL)

        self.assertEqual(result_a.status, SubmissionStatus.SKIPPED_ALREADY_COMPLETE)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.profile, "beta")
        # Only account B touched the server.
        self.assertEqual(len(self.fake.radar_answers), 1)


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.radar_account as module

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
