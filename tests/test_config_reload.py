"""Config reload reconciliation tests (Phase 5.1)."""

import asyncio
import shutil
import unittest
import uuid
from pathlib import Path

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.application_runtime import MonitorApplication
from tron_roll_call_hero.runtime_services import CollectingEventSink
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_temp() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def make_config(users=("user1",), passwords=None) -> dict:
    passwords = passwords or {}
    simple = {
        "now": "class A",
        "accounts": [
            {"user": user, "passwd": passwords.get(user, "pass{}".format(index + 1)), "school": "thu"}
            for index, user in enumerate(sorted({"user1", "user2"} | set(users)))
        ],
        "groups": [{"class": "A", "school": "thu", "users": list(users)}],
        "operating": {},
    }
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class ConfigReloadTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.fake = await FakeTronServer(
            credentials={"user1": "pass1", "user2": "pass2"}
        ).start()
        self.fake.per_account_state = True
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.sink = CollectingEventSink()
        self.app = MonitorApplication(
            make_config(users=("user1",)),
            base_dir=self.base,
            endpoints=self.fake.endpoints(),
            event_sink=self.sink,
            use_schedule=False,
            poll_interval=0.01,
            standby_interval=0.01,
            login_backoff=(0.01, 0.02),
            restart_backoff=(0.01, 0.02),
        )
        await self.app.start()
        await self.wait_for(lambda: self.app.snapshot_for("user1") is not None
                            and self.app.snapshot_for("user1").phase == "monitoring")

    async def asyncTearDown(self) -> None:
        await self.app.stop()
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    async def wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition not reached within {}s".format(timeout))

    async def test_add_account_starts_only_new_worker(self) -> None:
        worker_before = self.app.worker("user1")
        report = await self.app.reload(make_config(users=("user1", "user2")))
        self.assertTrue(report.ok)
        self.assertEqual(report.added, ("user2",))
        self.assertEqual(report.removed, ())
        self.assertEqual(report.restarted, ())
        self.assertEqual(report.kept, ("user1",))
        # The kept worker (and its session) is untouched.
        self.assertIs(self.app.worker("user1"), worker_before)
        self.assertFalse(worker_before.session.closed)
        await self.wait_for(lambda: self.app.snapshot_for("user2") is not None
                            and self.app.snapshot_for("user2").phase == "monitoring")

    async def test_remove_account_stops_only_removed_worker(self) -> None:
        await self.app.reload(make_config(users=("user1", "user2")))
        await self.wait_for(lambda: self.app.snapshot_for("user2") is not None
                            and self.app.snapshot_for("user2").phase == "monitoring")
        worker_user1 = self.app.worker("user1")
        worker_user2 = self.app.worker("user2")

        report = await self.app.reload(make_config(users=("user1",)))
        self.assertEqual(report.removed, ("user2",))
        self.assertEqual(report.kept, ("user1",))
        self.assertIs(self.app.worker("user1"), worker_user1)
        self.assertFalse(worker_user1.session.closed)
        self.assertTrue(worker_user2.session.closed)
        self.assertEqual(self.app.supervisor.running_profiles(), ("user1",))

    async def test_changed_credential_restarts_only_that_worker(self) -> None:
        await self.app.reload(make_config(users=("user1", "user2")))
        await self.wait_for(lambda: self.app.snapshot_for("user2") is not None
                            and self.app.snapshot_for("user2").phase == "monitoring")
        worker_user1 = self.app.worker("user1")
        worker_user2 = self.app.worker("user2")

        report = await self.app.reload(
            make_config(users=("user1", "user2"), passwords={"user1": "newpass"})
        )
        self.assertEqual(report.restarted, ("user1",))
        self.assertEqual(report.kept, ("user2",))
        self.assertIsNot(self.app.worker("user1"), worker_user1)
        self.assertIs(self.app.worker("user2"), worker_user2)
        self.assertFalse(worker_user2.session.closed)

    async def test_invalid_config_keeps_existing_workers(self) -> None:
        worker_before = self.app.worker("user1")
        report = await self.app.reload({})
        self.assertFalse(report.ok)
        self.assertEqual(report.kept, ("user1",))
        self.assertIs(self.app.worker("user1"), worker_before)
        self.assertEqual(self.app.supervisor.running_profiles(), ("user1",))
        # The failure is reported as a group-identity event.
        events = [event for event in self.sink.events if event.event == "config_reload"]
        self.assertTrue(events)
        self.assertTrue(events[-1].profile.startswith("group:"))


if __name__ == "__main__":
    unittest.main()
