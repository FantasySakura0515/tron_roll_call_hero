"""Single-account worker tests (Phase 2.8)."""

import asyncio
import shutil
import unittest
import uuid
from pathlib import Path

import aiohttp

from troTHU import tron
from troTHU import tron_http
from troTHU.account_models import AccountSpec, CredentialRef, CredentialSource
from troTHU.account_state_repository import FileAccountStateRepository
from troTHU.account_worker import AccountWorker
from troTHU.runtime_services import (
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

    def make_worker(self, *, operating=None, login_backoff=(7.5, 15.5, 31.5)) -> AccountWorker:
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

        import troTHU.account_worker as module

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
