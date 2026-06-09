"""Account-scoped authentication tests (Phase 2.2).

login_account performs login for one account using its own session, endpoints,
credential resolver, and cookie repository, writing the result into
account.state. The legacy login(session) path is left untouched.
"""

import shutil
import unittest
import uuid
from pathlib import Path

import aiohttp

from troTHU import providers
from troTHU import tron
from troTHU import tron_http
from troTHU.account_context import AccountContext
from troTHU.account_models import (
    AccountConfig,
    AccountRuntimeState,
    AccountSpec,
    CredentialRef,
    CredentialSource,
)
from troTHU.account_state_repository import FileAccountStateRepository
from troTHU.auth_account import login_account
from troTHU.runtime_services import (
    CollectingEventSink,
    CredentialResolver,
    FixedClock,
    RuntimeServices,
)
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_workspace_temp_dir() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def make_config(accounts) -> dict:
    simple = {"now": "", "accounts": accounts, "groups": [], "operating": {}}
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


def make_spec(profile: str, user: str, provider: str, source=CredentialSource.CONFIG) -> AccountSpec:
    return AccountSpec(
        profile=profile,
        user=user,
        provider_key=provider,
        credential_ref=CredentialRef(source, profile, user),
    )


class AuthAccountTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_workspace_temp_dir()
        self.repo = FileAccountStateRepository(self.base)
        self.fake = await FakeTronServer().start()
        self.url_patch = self.fake.patch_tron_http_urls(tron_http)
        self.url_patch.__enter__()

    async def asyncTearDown(self) -> None:
        self.url_patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    def make_services(self, config) -> RuntimeServices:
        return RuntimeServices(
            credentials=CredentialResolver(config, environ={}),
            cookies=self.repo,
            states=self.repo,
            events=CollectingEventSink(),
            clock=FixedClock(0.0),
        )

    def make_context(self, config, spec, session) -> AccountContext:
        return AccountContext(
            spec=spec,
            config=AccountConfig.from_config(config),
            endpoints=self.fake.endpoints(),
            session=session,
            state=AccountRuntimeState(),
            services=self.make_services(config),
        )

    async def test_success_writes_state_and_persists_cookies(self) -> None:
        config = make_config([{"user": "user1", "passwd": "pass1", "school": "thu"}])
        spec = make_spec("alpha", "user1", "thu")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(config, spec, session)
            state = await login_account(context)

        self.assertTrue(state.ok)
        self.assertEqual(state.credential_source, "config")
        self.assertEqual(context.state.login.status, "success")
        self.assertFalse(context.state.login_in_progress)
        # Cookies were saved through the per-account repository.
        saved = self.repo.load_cookies("alpha")
        self.assertTrue(any(record.get("key") == "session" for record in saved))

    async def test_wrong_password_is_not_ok(self) -> None:
        config = make_config([{"user": "user1", "passwd": "WRONG", "school": "thu"}])
        spec = make_spec("alpha", "user1", "thu")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(config, spec, session)
            state = await login_account(context)

        self.assertFalse(state.ok)
        self.assertEqual(self.repo.load_cookies("alpha"), [])

    async def test_missing_credentials_short_circuits(self) -> None:
        config = make_config([{"user": "user1", "passwd": "", "school": "thu"}])
        spec = make_spec("alpha", "user1", "thu")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            context = self.make_context(config, spec, session)
            state = await login_account(context)

        self.assertEqual(state.status, "missing_credentials")
        self.assertFalse(state.ok)

    async def test_two_accounts_keep_isolated_cookies(self) -> None:
        config = make_config([{"user": "user1", "passwd": "pass1", "school": "thu"}])
        spec_a = make_spec("alpha", "user1", "thu")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await login_account(self.make_context(config, spec_a, session))

        # A different account owns a different cookie path; clearing it must not
        # touch account alpha's persisted cookies.
        self.repo.save_cookies("beta", [{"key": "session", "value": "BBB"}])
        self.repo.clear_cookies("beta")
        self.assertTrue(self.repo.load_cookies("alpha"))
        self.assertEqual(self.repo.load_cookies("beta"), [])


class ManualCookieAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_workspace_temp_dir()
        self.repo = FileAccountStateRepository(self.base)

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def make_context(self, session, *, has_cookie: bool) -> AccountContext:
        config = make_config([{"user": "fjuuser", "passwd": "", "school": "fju"}])
        endpoints = tron_http.endpoints_from_provider(providers.get_provider("fju").to_config())
        if has_cookie:
            session.cookie_jar.update_cookies({"session": "manual"})
        return AccountContext(
            spec=make_spec("fju", "fjuuser", "fju", source=CredentialSource.MANUAL_COOKIE),
            config=AccountConfig.from_config(config),
            endpoints=endpoints,
            session=session,
            state=AccountRuntimeState(),
            services=RuntimeServices(
                credentials=CredentialResolver(config, environ={}),
                cookies=self.repo,
                states=self.repo,
                events=CollectingEventSink(),
                clock=FixedClock(0.0),
            ),
        )

    async def test_manual_cookie_present_is_success(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            state = await login_account(self.make_context(session, has_cookie=True))
        self.assertTrue(state.ok)
        self.assertEqual(state.credential_source, "manual_cookie")

    async def test_manual_cookie_absent_requires_cookie(self) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            state = await login_account(self.make_context(session, has_cookie=False))
        self.assertEqual(state.status, "manual_cookie_required")


if __name__ == "__main__":
    unittest.main()
