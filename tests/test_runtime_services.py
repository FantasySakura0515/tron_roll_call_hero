"""Unit tests for runtime services (Phase 2.1).

CredentialResolver resolves a password transiently at login time and never
stores it in a long-lived object. RuntimeServices bundles the injectable
collaborators so tests can swap in memory fakes.
"""

import unittest

from tron_roll_call_hero import tron
from tron_roll_call_hero.account_models import (
    AccountSpec,
    CredentialRef,
    CredentialSource,
    ScheduleSpec,
)
from tron_roll_call_hero.account_state_repository import FileAccountStateRepository
from tron_roll_call_hero.runtime_services import (
    CollectingEventSink,
    CookieRepository,
    CredentialResolver,
    FixedClock,
    ResolvedCredential,
    RuntimeServices,
    SystemClock,
)


def make_spec(user: str, source: CredentialSource = CredentialSource.CONFIG, provider: str = "thu") -> AccountSpec:
    return AccountSpec(
        profile=user,
        user=user,
        provider_key=provider,
        credential_ref=CredentialRef(source, user, user),
        schedule=ScheduleSpec(),
    )


def make_config(accounts) -> dict:
    simple = {"now": "", "accounts": accounts, "groups": [], "operating": {}}
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class ClockTest(unittest.TestCase):
    def test_fixed_clock_is_controllable(self) -> None:
        clock = FixedClock(100.0)
        self.assertEqual(clock.now(), 100.0)
        clock.advance(5.0)
        self.assertEqual(clock.now(), 105.0)

    def test_system_clock_returns_float(self) -> None:
        self.assertIsInstance(SystemClock().now(), float)


class ResolvedCredentialTest(unittest.TestCase):
    def test_password_is_hidden_from_repr(self) -> None:
        resolved = ResolvedCredential(user="s1", password="hunter2", source="config", has_password=True)
        self.assertNotIn("hunter2", repr(resolved))
        self.assertEqual(resolved.password, "hunter2")


class CredentialResolverTest(unittest.TestCase):
    def test_keyring_beats_config(self) -> None:
        config = make_config([{"user": "S1", "passwd": "CPW", "school": "thu"}])
        resolver = CredentialResolver(config, keyring_getter=lambda profile, user: "KPW", environ={})
        resolved = resolver.resolve(make_spec("S1"))
        self.assertEqual(resolved.source, "keyring")
        self.assertEqual(resolved.password, "KPW")

    def test_config_used_when_no_keyring(self) -> None:
        config = make_config([{"user": "S1", "passwd": "CPW", "school": "thu"}])
        resolver = CredentialResolver(config, environ={})
        resolved = resolver.resolve(make_spec("S1"))
        self.assertEqual(resolved.source, "config")
        self.assertEqual(resolved.password, "CPW")

    def test_environment_only_when_user_matches(self) -> None:
        config = make_config([{"user": "S1", "passwd": "", "school": "thu"}])
        resolver = CredentialResolver(config, environ={"TRON_USER": "S1", "TRON_PASS": "EPW"})
        resolved = resolver.resolve(make_spec("S1"))
        self.assertEqual(resolved.source, "environment")
        self.assertEqual(resolved.password, "EPW")

        other = CredentialResolver(config, environ={"TRON_USER": "OTHER", "TRON_PASS": "EPW"})
        self.assertFalse(other.resolve(make_spec("S1")).has_password)

    def test_manual_cookie_needs_no_password(self) -> None:
        config = make_config([{"user": "FJU", "passwd": "", "school": "fju"}])
        resolver = CredentialResolver(config, environ={})
        resolved = resolver.resolve(make_spec("FJU", source=CredentialSource.MANUAL_COOKIE, provider="fju"))
        self.assertFalse(resolved.has_password)
        self.assertEqual(resolved.source, "manual_cookie")

    def test_missing_credential(self) -> None:
        config = make_config([{"user": "S1", "passwd": "", "school": "thu"}])
        resolver = CredentialResolver(config, environ={})
        resolved = resolver.resolve(make_spec("S1"))
        self.assertFalse(resolved.has_password)
        self.assertEqual(resolved.source, "missing")


class EventSinkTest(unittest.TestCase):
    def test_collecting_event_sink_records_events(self) -> None:
        sink = CollectingEventSink()
        sink.emit({"event": "login", "profile": "s1"})
        self.assertEqual(sink.events[0]["event"], "login")


class RuntimeServicesTest(unittest.TestCase):
    def test_bundles_services_with_optional_future_fields(self) -> None:
        config = make_config([{"user": "S1", "passwd": "P", "school": "thu"}])
        repo = FileAccountStateRepository("/tmp/does-not-need-to-exist")
        services = RuntimeServices(
            credentials=CredentialResolver(config, environ={}),
            cookies=repo,
            states=repo,
            events=CollectingEventSink(),
            clock=FixedClock(0.0),
        )
        self.assertIsInstance(services.cookies, CookieRepository)
        self.assertIsNone(services.notifications)
        self.assertIsNone(services.artifacts)
        self.assertIsNone(services.teacher_qr)


if __name__ == "__main__":
    unittest.main()
