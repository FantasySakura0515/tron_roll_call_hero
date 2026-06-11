"""Shared number-code discovery coordinator tests (Phase 3.3)."""

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
from troTHU.number_account import answer_number_rollcall
from troTHU.rollcall_artifact_coordinator import (
    CoordinatedNumberCodeResolver,
    RollcallArtifactCoordinator,
)
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


class CoordinatorUnitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FixedClock(0.0)
        self.coordinator = RollcallArtifactCoordinator(ttl_seconds=60.0, clock=self.clock)
        self.resolve_count = 0

    async def resolver(self) -> str:
        self.resolve_count += 1
        await asyncio.sleep(0.01)
        return "0001"

    async def test_concurrent_requests_resolve_once(self) -> None:
        results = await asyncio.gather(
            self.coordinator.get_or_resolve("thu", "42", self.resolver),
            self.coordinator.get_or_resolve("thu", "42", self.resolver),
            self.coordinator.get_or_resolve("thu", "42", self.resolver),
        )
        self.assertEqual(results, ["0001", "0001", "0001"])
        self.assertEqual(self.resolve_count, 1)

    async def test_different_providers_do_not_share(self) -> None:
        await self.coordinator.get_or_resolve("thu", "42", self.resolver)
        await self.coordinator.get_or_resolve("tku", "42", self.resolver)
        self.assertEqual(self.resolve_count, 2)

    async def test_resolver_failure_is_not_cached(self) -> None:
        async def failing() -> str:
            self.resolve_count += 1
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await self.coordinator.get_or_resolve("thu", "42", failing)
        # The error is not cached: the next caller re-runs the resolver.
        value = await self.coordinator.get_or_resolve("thu", "42", self.resolver)
        self.assertEqual(value, "0001")
        self.assertEqual(self.resolve_count, 2)

    async def test_result_ttl_expires(self) -> None:
        await self.coordinator.get_or_resolve("thu", "42", self.resolver)
        self.clock.advance(61.0)
        await self.coordinator.get_or_resolve("thu", "42", self.resolver)
        self.assertEqual(self.resolve_count, 2)

    async def test_publish_seeds_cache(self) -> None:
        self.coordinator.publish("thu", "42", "0007")
        value = await self.coordinator.get_or_resolve("thu", "42", self.resolver)
        self.assertEqual(value, "0007")
        self.assertEqual(self.resolve_count, 0)

    async def test_shutdown_cancels_pending(self) -> None:
        started = asyncio.Event()

        async def hanging() -> str:
            started.set()
            await asyncio.sleep(30)
            return "never"

        task = asyncio.create_task(self.coordinator.get_or_resolve("thu", "42", hanging))
        await started.wait()
        await self.coordinator.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await task


class CoordinatedNumberTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer(
            credentials={"user1": "pass1", "user2": "pass2"}
        ).start()
        self.fake.per_account_state = True
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        simple = {
            "now": "",
            "accounts": [
                {"user": "user1", "passwd": "pass1", "school": "thu"},
                {"user": "user2", "passwd": "pass2", "school": "thu"},
            ],
            "groups": [],
            "operating": {},
        }
        self.config = tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))
        self.coordinator = RollcallArtifactCoordinator(ttl_seconds=60.0)

    async def asyncTearDown(self) -> None:
        await self.coordinator.shutdown()
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    def make_context(self, session, profile: str, user: str) -> AccountContext:
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

    async def test_two_accounts_share_one_direct_read(self) -> None:
        resolver = CoordinatedNumberCodeResolver(self.coordinator)
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, "alpha", "user1")
                context_b = self.make_context(session_b, "beta", "user2")
                await login_account(context_a)
                await login_account(context_b)

                result_a, result_b = await asyncio.gather(
                    answer_number_rollcall(context_a, 42, resolver=resolver),
                    answer_number_rollcall(context_b, 42, resolver=resolver),
                )

        self.assertEqual(result_a.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        # The code was resolved once and each account submitted exactly once.
        self.assertEqual(resolver.direct_read_count, 1)
        self.assertEqual(len(self.fake.number_attempts), 2)
        users = sorted(attempt["user"] for attempt in self.fake.number_attempts)
        self.assertEqual(users, ["user1", "user2"])

    async def test_brute_force_discovery_is_published(self) -> None:
        self.fake.student_rollcalls_leaks_code = False
        resolver = CoordinatedNumberCodeResolver(self.coordinator)
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_a:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session_b:
                context_a = self.make_context(session_a, "alpha", "user1")
                context_b = self.make_context(session_b, "beta", "user2")
                await login_account(context_a)
                await login_account(context_b)

                # Account A discovers the code by brute force and publishes it.
                result_a = await answer_number_rollcall(context_a, 42, resolver=resolver)
                attempts_after_a = len(self.fake.number_attempts)
                # Account B reuses the published code with a single submission.
                result_b = await answer_number_rollcall(context_b, 42, resolver=resolver)

        self.assertEqual(result_a.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(result_b.status, SubmissionStatus.CONFIRMED)
        self.assertEqual(len(self.fake.number_attempts) - attempts_after_a, 1)
        self.assertEqual(self.fake.number_attempts[-1]["body"]["numberCode"], "0001")
        self.assertEqual(self.fake.number_attempts[-1]["user"], "user2")


if __name__ == "__main__":
    unittest.main()
