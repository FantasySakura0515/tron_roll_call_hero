"""Account-scoped student QR execution tests (Phase 2.7)."""

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
    AttendanceType,
    CredentialRef,
    CredentialSource,
    SubmissionStatus,
)
from troTHU.account_state_repository import FileAccountStateRepository
from troTHU.auth_account import login_account
from troTHU.pending_qr import add_pending_qr, list_pending_qr
from troTHU.qr_account import submit_qr_payload_account
from troTHU.runtime_services import (
    CollectingEventSink,
    CredentialResolver,
    FixedClock,
    RuntimeServices,
)
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]

QR_SECRET = "secret-qr-data-value"
QR_PAYLOAD = json.dumps({"rollcallId": 55, "data": QR_SECRET})


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


class QrAccountTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer().start()
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.config = make_config()
        self.sink = CollectingEventSink()

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
            events=self.sink,
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

    async def test_manual_payload_submits_for_each_account(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, profile="alpha")
                context_b = self.make_context(session_b, profile="beta")
                await login_account(context_a)
                await login_account(context_b)

                result_a = await submit_qr_payload_account(context_a, QR_PAYLOAD)
                result_b = await submit_qr_payload_account(context_b, QR_PAYLOAD)

        self.assertEqual(result_a.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_a.attendance_type, AttendanceType.QR)
        self.assertEqual({result_a.profile, result_b.profile}, {"alpha", "beta"})
        self.assertEqual(len(self.fake.qr_answers), 2)
        self.assertIn("55", context_a.state.completed_qr)
        self.assertIn("55", context_b.state.completed_qr)

    async def test_pending_registry_account_isolation(self) -> None:
        add_pending_qr(self.base, profile="alpha", rollcall_id="55", provider="thu")
        add_pending_qr(self.base, profile="beta", rollcall_id="55", provider="thu")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session, profile="alpha")
            await login_account(context)
            result = await submit_qr_payload_account(context, QR_PAYLOAD, pending_dir=self.base)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        remaining = list_pending_qr(self.base)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].profile, "beta")

    async def test_completed_qr_isolation(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, profile="alpha")
                context_b = self.make_context(session_b, profile="beta")
                await login_account(context_a)
                await login_account(context_b)
                context_a.state.completed_qr.add("55")

                result_a = await submit_qr_payload_account(context_a, QR_PAYLOAD)
                result_b = await submit_qr_payload_account(context_b, QR_PAYLOAD)

        self.assertEqual(result_a.status, SubmissionStatus.SKIPPED_ALREADY_COMPLETE)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(len(self.fake.qr_answers), 1)

    async def test_raw_payload_not_in_snapshot_or_events(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await submit_qr_payload_account(context, QR_PAYLOAD)

        self.assertEqual(result.status, SubmissionStatus.CONFIRMED)
        snapshot = context.state.to_snapshot(profile="alpha", provider_key="thu")
        snapshot_json = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        self.assertNotIn(QR_SECRET, snapshot_json)
        events_json = json.dumps(
            [event.to_dict() for event in self.sink.events], ensure_ascii=False
        )
        self.assertNotIn(QR_SECRET, events_json)
        result_json = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn(QR_SECRET, result_json)

    async def test_invalid_payload_fails_without_submission(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(session)
            await login_account(context)
            result = await submit_qr_payload_account(context, json.dumps({"data": "x"}))

        self.assertEqual(result.status, SubmissionStatus.FAILED)
        self.assertEqual(result.error_code, "invalid_payload")
        self.assertEqual(len(self.fake.qr_answers), 0)


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.qr_account as module

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
