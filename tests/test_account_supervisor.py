"""Account supervisor tests (Phase 3.1)."""

import asyncio
import unittest

from troTHU.account_models import AccountSpec, AccountWorkerSnapshot, CredentialRef, CredentialSource
from troTHU.account_supervisor import AccountSupervisor


def make_spec(profile: str) -> AccountSpec:
    return AccountSpec(
        profile=profile,
        user="user-{}".format(profile),
        provider_key="thu",
        credential_ref=CredentialRef(CredentialSource.CONFIG, profile, "user-{}".format(profile)),
    )


class FakeWorker:
    """A controllable stand-in for AccountWorker."""

    def __init__(self, spec: AccountSpec, *, crash_times: int = 0) -> None:
        self.spec = spec
        self.crash_times = crash_times
        self.run_count = 0
        self.phase = "created"
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        self.run_count += 1
        if self.crash_times > 0:
            self.crash_times -= 1
            self.phase = "crashed"
            raise RuntimeError("boom")
        self.phase = "monitoring"
        await self._stop_event.wait()
        self.phase = "stopped"

    async def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> AccountWorkerSnapshot:
        return AccountWorkerSnapshot(
            profile=self.spec.profile,
            provider_key=self.spec.provider_key,
            phase=self.phase,
            healthy=self.phase not in ("crashed",),
        )


class AccountSupervisorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sleep_calls: list = []
        self.created: dict = {}
        self.crash_plan: dict = {}

    def factory(self, spec: AccountSpec) -> FakeWorker:
        remaining_crashes = self.crash_plan.get(spec.profile, 0)
        if remaining_crashes > 0:
            self.crash_plan[spec.profile] = remaining_crashes - 1
        worker = FakeWorker(spec, crash_times=1 if remaining_crashes > 0 else 0)
        self.created.setdefault(spec.profile, []).append(worker)
        return worker

    def make_supervisor(self, profiles, *, restart_backoff=(0.5, 1.5, 3.5)) -> AccountSupervisor:
        async def recording_sleep(seconds: float) -> None:
            self.sleep_calls.append(seconds)
            await asyncio.sleep(0)

        return AccountSupervisor(
            [make_spec(profile) for profile in profiles],
            worker_factory=self.factory,
            restart_backoff=restart_backoff,
            sleep=recording_sleep,
        )

    async def wait_for(self, predicate, timeout: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition not reached within {}s".format(timeout))

    async def test_starts_two_workers(self) -> None:
        supervisor = self.make_supervisor(["alpha", "beta"])
        await supervisor.start()
        await self.wait_for(
            lambda: {snap.profile: snap.phase for snap in supervisor.snapshots()}
            == {"alpha": "monitoring", "beta": "monitoring"}
        )
        self.assertEqual(supervisor.running_profiles(), ("alpha", "beta"))
        await supervisor.stop()

    async def test_crash_is_isolated_and_restarted(self) -> None:
        self.crash_plan = {"alpha": 1}
        supervisor = self.make_supervisor(["alpha", "beta"])
        await supervisor.start()
        # Alpha crashes once and is restarted; beta keeps running untouched.
        await self.wait_for(lambda: len(self.created.get("alpha", [])) == 2)
        await self.wait_for(lambda: self.created["alpha"][-1].phase == "monitoring")
        self.assertEqual(len(self.created["beta"]), 1)
        self.assertEqual(self.created["beta"][0].phase, "monitoring")
        self.assertEqual(supervisor.restart_count("alpha"), 1)
        self.assertEqual(supervisor.restart_count("beta"), 0)
        await supervisor.stop()

    async def test_restart_backoff_is_exponential(self) -> None:
        self.crash_plan = {"alpha": 3}
        supervisor = self.make_supervisor(["alpha"], restart_backoff=(0.5, 1.5, 3.5))
        await supervisor.start()
        await self.wait_for(lambda: len(self.created.get("alpha", [])) == 4)
        backoff_sleeps = [delay for delay in self.sleep_calls if delay in (0.5, 1.5, 3.5)]
        self.assertEqual(backoff_sleeps, [0.5, 1.5, 3.5])
        await supervisor.stop()

    async def test_isolated_stop(self) -> None:
        supervisor = self.make_supervisor(["alpha", "beta"])
        await supervisor.start()
        await self.wait_for(lambda: supervisor.running_profiles() == ("alpha", "beta"))
        stopped = await supervisor.stop_account("alpha")
        self.assertTrue(stopped)
        self.assertEqual(supervisor.running_profiles(), ("beta",))
        self.assertEqual(self.created["beta"][0].phase, "monitoring")
        await supervisor.stop()

    async def test_graceful_global_shutdown(self) -> None:
        supervisor = self.make_supervisor(["alpha", "beta"])
        await supervisor.start()
        await self.wait_for(lambda: supervisor.running_profiles() == ("alpha", "beta"))
        await supervisor.stop()
        self.assertEqual(supervisor.running_profiles(), ())
        phases = {worker.phase for workers in self.created.values() for worker in workers}
        self.assertEqual(phases, {"stopped"})

    async def test_unknown_profile_stop_returns_false(self) -> None:
        supervisor = self.make_supervisor(["alpha"])
        await supervisor.start()
        self.assertFalse(await supervisor.stop_account("ghost"))
        await supervisor.stop()


class DecouplingTest(unittest.TestCase):
    def test_module_does_not_directly_import_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.account_supervisor as module

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
