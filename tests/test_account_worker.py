"""Single-account worker tests (Phase 2.8)."""

import asyncio
import shutil
import unittest
import uuid
from pathlib import Path

import aiohttp

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.account_models import AccountSpec, CredentialRef, CredentialSource
from tron_roll_call_hero.account_state_repository import FileAccountStateRepository
from tron_roll_call_hero.account_worker import AccountWorker
from tron_roll_call_hero.teacher_qr_coordinator import TeacherQrCoordinator
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


class AccountWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer().start()
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.config = make_config()
        self.sink = CollectingEventSink()
        self.sleep_calls: list = []
        self.workers: list = []

    async def asyncTearDown(self) -> None:
        for worker in self.workers:
            await worker.stop()
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    def make_worker(
        self,
        *,
        operating=None,
        login_backoff=(7.5, 15.5, 31.5),
        ignore_attendance_rate_gate: bool = True,
    ) -> AccountWorker:
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
            events=self.sink,
            clock=FixedClock(0.0),
        )

        async def recording_sleep(seconds: float) -> None:
            self.sleep_calls.append(seconds)
            await asyncio.sleep(0)

        worker = AccountWorker(
            spec,
            self.config,
            services=services,
            endpoints=self.fake.endpoints(),
            operating=operating,
            poll_interval=0.01,
            login_backoff=login_backoff,
            sleep=recording_sleep,
            ignore_attendance_rate_gate=ignore_attendance_rate_gate,
        )
        self.workers.append(worker)
        return worker

    async def wait_for(self, predicate, timeout: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition not reached within {}s".format(timeout))

    async def test_worker_lifecycle(self) -> None:
        self.fake.rollcalls = []
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().phase == "monitoring")
        await self.wait_for(lambda: worker.snapshot().poll_count >= 2)
        self.assertEqual(worker.snapshot().login_status, "success")
        self.assertEqual(worker.snapshot().last_check_status, "not_call")
        await worker.stop()
        self.assertEqual(worker.snapshot().phase, "stopped")
        # The final snapshot is persisted per account.
        persisted = self.repo.load("alpha")
        self.assertEqual(persisted.phase, "stopped")

    async def test_shutdown_closes_session(self) -> None:
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().phase == "monitoring")
        session = worker.session
        self.assertIsNotNone(session)
        self.assertFalse(session.closed)
        await worker.stop()
        self.assertTrue(session.closed)

    async def test_worker_teacher_qr_end_to_end(self) -> None:
        # Single-account E2E: the worker drives teacher-assisted QR through
        # services.teacher_qr (a real TeacherQrCoordinator) against the fake
        # server's teacher endpoints, then persists completion per account.
        self.fake.credentials["teacher"] = "tpass"
        self.fake.rollcalls = [{"is_qrcode": True, "rollcall_id": 55, "course_id": "C7"}]

        coordinator = TeacherQrCoordinator(
            endpoints=self.fake.endpoints(),
            credentials=("teacher", "tpass"),
            poll_interval=0.01,
            confirm_window=2.0,
        )
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
            events=self.sink,
            clock=FixedClock(0.0),
            teacher_qr=coordinator,
        )

        async def recording_sleep(seconds: float) -> None:
            self.sleep_calls.append(seconds)
            await asyncio.sleep(0)

        worker = AccountWorker(
            spec,
            self.config,
            services=services,
            endpoints=self.fake.endpoints(),
            poll_interval=0.01,
            sleep=recording_sleep,
            ignore_attendance_rate_gate=True,
        )
        self.workers.append(worker)

        try:
            await worker.start()
            await self.wait_for(lambda: "55" in worker.state.completed_qr)
            await worker.stop()

            # (1) Exactly one QR submission recorded for this account's own user
            # (submitted with the student session, not the teacher's).
            qr_for_user = [a for a in self.fake.qr_answers if a["user"] == "user1"]
            self.assertEqual(len(qr_for_user), 1, self.fake.qr_answers)
            self.assertEqual(str(qr_for_user[0]["rollcall_id"]), "55")

            # (2) Persisted per-account state marks the QR rollcall completed.
            persisted = self.repo.load("alpha")
            self.assertIn("55", persisted.completed_qr)
        finally:
            await coordinator.shutdown()

    async def test_worker_skips_login_when_valid_cookie_cache(self) -> None:
        # FJU CAS+captcha primary account: restarting with a valid cached cookie
        # jar must reuse it and NOT trigger a fresh captcha round-trip (legacy
        # COOKIE_CACHE_RESTORED fast-path parity, commits 13612d5/fffa920).
        self.fake.captcha_login = True
        self.fake.rollcalls = []

        class _CountingSolver:
            def __init__(self) -> None:
                self.calls = 0

            def solve(self, image_bytes):  # pragma: no cover - must not be called
                self.calls += 1
                return "abcd"

        solver = _CountingSolver()
        spec = AccountSpec(
            profile="fju1",
            user="user1",
            provider_key="fju",
            credential_ref=CredentialRef(CredentialSource.CONFIG, "fju1", "user1"),
        )
        services = RuntimeServices(
            credentials=CredentialResolver(self.config, environ={}),
            cookies=self.repo,
            states=self.repo,
            events=self.sink,
            clock=FixedClock(0.0),
            captcha_solver=solver,
        )

        async def recording_sleep(seconds: float) -> None:
            self.sleep_calls.append(seconds)
            await asyncio.sleep(0)

        worker = AccountWorker(
            spec,
            self.config,
            services=services,
            endpoints=self.fake.endpoints(),
            poll_interval=0.01,
            sleep=recording_sleep,
            ignore_attendance_rate_gate=True,
        )
        self.workers.append(worker)

        # Pre-seed a VALID cookie jar (the cookie a real login would receive).
        self.repo.save_cookies(
            "fju1", [{"key": "session", "value": self.fake.session_cookie_for("user1")}]
        )
        self.fake.captcha_fetch_count = 0

        await worker.start()
        await self.wait_for(lambda: worker.snapshot().login_status == "success")
        await worker.stop()

        # The cached jar authenticated, so login was via cookie_cache and the
        # captcha image was never fetched and the OCR solver never invoked.
        self.assertEqual(worker.state.login.credential_source, "cookie_cache")
        self.assertEqual(self.fake.captcha_fetch_count, 0)
        self.assertEqual(solver.calls, 0)

    async def test_login_retry_backoff(self) -> None:
        # Three transient login failures, then the unscripted path succeeds.
        for _ in range(3):
            self.fake.queue_response("submit_login", status=500, text="boom")
        worker = self.make_worker(login_backoff=(7.5, 15.5, 31.5))
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().login_status == "success")
        backoff_sleeps = [delay for delay in self.sleep_calls if delay in (7.5, 15.5, 31.5)]
        self.assertEqual(backoff_sleeps, [7.5, 15.5, 31.5])
        await worker.stop()

    async def test_number_rollcall_end_to_end(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42}]
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: "42" in worker.state.completed_number)
        self.assertEqual(worker.state.completed_number["42"], "0001")
        # Direct read submits exactly once; later polls skip the completed id.
        await self.wait_for(lambda: worker.snapshot().poll_count >= 3)
        self.assertEqual(len(self.fake.number_attempts), 1)
        await worker.stop()

    async def test_number_submission_emits_runtime_event(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42}]
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: "42" in worker.state.completed_number)
        await worker.stop()
        submissions = [
            event
            for event in self.sink.events
            if getattr(event, "event", "") == "rollcall_submission"
        ]
        self.assertEqual(len(submissions), 1)
        event = submissions[0]
        self.assertEqual(event.profile, "alpha")
        self.assertEqual(event.provider_key, "thu")
        self.assertEqual(event.status, "confirmed")
        self.assertEqual(event.rollcall_id, "42")
        self.assertEqual(event.attendance_type, "number")

    async def test_attendance_gate_blocks_then_allows(self) -> None:
        # Low attendance -> do not submit; once a classmate signs in -> submit.
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "classmate", "status": "pending", "rollcall_status": "on_call"},
        ]
        worker = self.make_worker(ignore_attendance_rate_gate=False)
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().poll_count >= 2)
        # 0% present -> not submitted yet.
        self.assertEqual(len(self.fake.number_attempts), 0)
        self.assertNotIn("42", worker.state.completed_number)
        # One classmate present -> 50% -> next poll submits.
        self.fake.student_rollcalls[1]["rollcall_status"] = "on_call_fine"
        await self.wait_for(lambda: "42" in worker.state.completed_number)
        await worker.stop()

    async def test_ignore_gate_submits_immediately(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
        ]
        worker = self.make_worker(ignore_attendance_rate_gate=True)
        await worker.start()
        await self.wait_for(lambda: "42" in worker.state.completed_number)
        await worker.stop()

    async def test_number_read_sends_course_referer(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "course_id": "C9"}]
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: "42" in worker.state.completed_number)
        await worker.stop()
        self.assertTrue(self.fake.student_rollcalls_referers)
        self.assertTrue(
            any("/course/C9/rollcall" in ref for ref in self.fake.student_rollcalls_referers)
        )

    async def test_reauth_clears_saved_cookie_cache(self) -> None:
        self.fake.rollcalls = []
        worker = self.make_worker()
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().login_status == "success")
        # Login persisted cookies for this account.
        self.assertTrue(self.repo.load_cookies("alpha"))
        # Reauth drops both the live jar and the saved cache (sync, no await
        # in between, so the loop cannot re-save before we assert).
        self.assertTrue(worker.request_reauth())
        self.assertEqual(self.repo.load_cookies("alpha"), [])
        await worker.stop()

    async def test_standby_when_schedule_disabled(self) -> None:
        operating = {day: {"enable": False} for day in range(7)}
        worker = self.make_worker(operating=operating)
        await worker.start()
        await self.wait_for(lambda: worker.snapshot().phase == "standby")
        self.assertEqual(worker.snapshot().poll_count, 0)
        await worker.stop()
        self.assertEqual(worker.snapshot().phase, "stopped")


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.account_worker as module

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
