"""Teacher-assisted QR coordinator tests (Phase 4.2)."""

import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

import aiohttp

from troTHU import tron
from troTHU import tron_http
from troTHU.account_context import AccountContext
from troTHU.account_models import (
    AccountConfig,
    AccountRuntimeState,
    AccountSpec,
    CredentialRef,
    CredentialSource,
    SubmissionStatus,
)
from troTHU.account_state_repository import FileAccountStateRepository
from troTHU.auth_account import login_account
from troTHU.runtime_services import (
    CollectingEventSink,
    CredentialResolver,
    FixedClock,
    RuntimeServices,
)
from troTHU.teacher_qr_coordinator import TeacherQrCoordinator
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
        "accounts": [
            {"user": "user1", "passwd": "pass1", "school": "thu"},
            {"user": "user2", "passwd": "pass2", "school": "thu"},
        ],
        "groups": [],
        "operating": {},
    }
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


ROLLCALL = {"rollcall_id": 55, "type": "qr_rollcall"}


class TeacherQrCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer(
            credentials={"user1": "pass1", "user2": "pass2", "teacher": "tpass"}
        ).start()
        self.fake.per_account_state = True
        self.fake.courses = [{"id": 9001, "name": "Course X"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.config = make_config()
        self.sink = CollectingEventSink()
        self.coordinator = TeacherQrCoordinator(
            endpoints=self.fake.endpoints(),
            credentials=("teacher", "tpass"),
            poll_interval=0.01,
            confirm_window=2.0,
        )
        self.sessions: list = []

    async def asyncTearDown(self) -> None:
        await self.coordinator.shutdown()
        for session in self.sessions:
            if not session.closed:
                await session.close()
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    async def make_context(self, profile: str, user: str) -> AccountContext:
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.sessions.append(session)
        spec = AccountSpec(
            profile=profile,
            user=user,
            provider_key="thu",
            credential_ref=CredentialRef(CredentialSource.CONFIG, profile, user),
        )
        services = RuntimeServices(
            credentials=CredentialResolver(self.config, environ={}),
            cookies=self.repo,
            states=self.repo,
            events=self.sink,
            clock=FixedClock(0.0),
        )
        context = AccountContext(
            spec=spec,
            config=AccountConfig.from_config(self.config),
            endpoints=self.fake.endpoints(),
            session=session,
            state=AccountRuntimeState(),
            services=services,
        )
        await login_account(context)
        return context

    async def test_two_students_share_one_teacher_rollcall(self) -> None:
        context_a = await self.make_context("alpha", "user1")
        context_b = await self.make_context("beta", "user2")

        result_a, result_b = await asyncio.gather(
            self.coordinator.assist(context_a, ROLLCALL),
            self.coordinator.assist(context_b, ROLLCALL),
        )

        self.assertEqual(result_a.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        # Single-flight: only one teacher rollcall was created.
        self.assertEqual(len(self.fake.teacher_rollcalls), 1)
        # Each student submitted with its own session.
        users = sorted(answer["user"] for answer in self.fake.qr_answers)
        self.assertEqual(users, ["user1", "user2"])
        # All interested accounts completed, so the teacher rollcall was stopped.
        self.assertEqual(len(self.fake.teacher_rollcall_stops), 1)

    async def test_qr_data_rotation_uses_fresh_data(self) -> None:
        context_a = await self.make_context("alpha", "user1")
        context_b = await self.make_context("beta", "user2")

        self.fake.teacher_qr_data = "rotating-data-1"
        result_a = await self.coordinator.assist(context_a, ROLLCALL)
        self.fake.teacher_qr_data = "rotating-data-2"
        result_b = await self.coordinator.assist(context_b, ROLLCALL)

        self.assertEqual(result_a.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        submitted = [answer["body"].get("data") for answer in self.fake.qr_answers]
        self.assertEqual(submitted, ["rotating-data-1", "rotating-data-2"])

    async def test_teacher_login_failure_reports_not_ready(self) -> None:
        self.fake.fail_login_users.add("teacher")
        context_a = await self.make_context("alpha", "user1")
        result = await self.coordinator.assist(context_a, ROLLCALL)
        self.assertEqual(result.status, SubmissionStatus.FAILED)
        self.assertEqual(result.error_code, "teacher_not_ready")
        self.assertEqual(len(self.fake.teacher_rollcalls), 0)

    async def test_stop_assist_stops_teacher_rollcall(self) -> None:
        context_a = await self.make_context("alpha", "user1")
        result = await self.coordinator.assist(context_a, ROLLCALL)
        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        # The single interested account completed, so it is already stopped.
        stops_before = len(self.fake.teacher_rollcall_stops)
        await self.coordinator.stop_assist("55")
        self.assertEqual(len(self.fake.teacher_rollcall_stops), stops_before)

    async def test_shutdown_stops_remaining_rollcalls_and_session(self) -> None:
        context_a = await self.make_context("alpha", "user1")
        self.fake.fail_submit_users.add("user1")
        result = await self.coordinator.assist(context_a, ROLLCALL)
        self.assertNotEqual(result.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(len(self.fake.teacher_rollcalls), 1)
        await self.coordinator.shutdown()
        self.assertGreaterEqual(len(self.fake.teacher_rollcall_stops), 1)

    async def test_raw_qr_data_never_persisted(self) -> None:
        self.fake.teacher_qr_data = "super-secret-qr-data"
        context_a = await self.make_context("alpha", "user1")
        result = await self.coordinator.assist(context_a, ROLLCALL)
        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)

        snapshot = context_a.state.to_snapshot(profile="alpha", provider_key="thu")
        self.assertNotIn("super-secret-qr-data", json.dumps(snapshot.to_dict(), ensure_ascii=False))
        events_json = json.dumps([event.to_dict() for event in self.sink.events], ensure_ascii=False)
        self.assertNotIn("super-secret-qr-data", events_json)
        status_json = json.dumps(self.coordinator.status(), ensure_ascii=False)
        self.assertNotIn("super-secret-qr-data", status_json)


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.teacher_qr_coordinator as module

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
