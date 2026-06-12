"""Group monitor assembly tests (Phase 3.4)."""

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


def make_config(now: str = "class A", users=("user1", "user2")) -> dict:
    simple = {
        "now": now,
        "accounts": [
            {"user": "user1", "passwd": "pass1", "school": "thu"},
            {"user": "user2", "passwd": "pass2", "school": "thu"},
        ],
        "groups": [{"class": "A", "school": "thu", "users": list(users)}],
        "operating": {},
    }
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class MonitorApplicationTest(unittest.IsolatedAsyncioTestCase):
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
        self.app = None

    async def asyncTearDown(self) -> None:
        if self.app is not None:
            await self.app.stop()
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    def make_app(self, config=None) -> MonitorApplication:
        self.app = MonitorApplication(
            config or make_config(),
            base_dir=self.base,
            endpoints=self.fake.endpoints(),
            event_sink=self.sink,
            use_schedule=False,
            poll_interval=0.01,
            standby_interval=0.01,
            login_backoff=(0.01, 0.02),
            restart_backoff=(0.01, 0.02),
        )
        return self.app

    async def wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition not reached within {}s".format(timeout))

    async def test_group_start_reports_all_accounts(self) -> None:
        app = self.make_app()
        report = await app.start()
        self.assertEqual(report.kind, "group")
        self.assertEqual(report.started, ("user1", "user2"))
        self.assertEqual(report.skipped, ())
        await self.wait_for(
            lambda: {snap.profile: snap.phase for snap in app.snapshots()}
            == {"user1": "monitoring", "user2": "monitoring"}
        )

    async def test_group_start_reports_skipped_unknown_account(self) -> None:
        app = self.make_app(make_config(users=("user1", "GHOST")))
        report = await app.start()
        self.assertEqual(report.started, ("user1",))
        self.assertEqual(len(report.skipped), 1)
        self.assertEqual(report.skipped[0]["user"], "GHOST")

    async def test_group_number_each_account_confirmed(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        app = self.make_app()
        await app.start()
        await self.wait_for(
            lambda: all(
                "42" in app.worker(profile).state.completed_number
                for profile in ("user1", "user2")
                if app.worker(profile) is not None
            )
            and app.worker("user1") is not None
            and app.worker("user2") is not None
        )
        users = sorted(attempt["user"] for attempt in self.fake.number_attempts)
        self.assertEqual(users, ["user1", "user2"])

    async def test_group_radar_each_account_confirmed(self) -> None:
        self.fake.rollcalls = [{"is_radar": True, "rollcall_id": 77, "status": "absent"}]
        self.fake.radar_empty_answer_accepted = True
        app = self.make_app()
        await app.start()
        await self.wait_for(
            lambda: all(
                app.worker(profile) is not None
                and "77" in app.worker(profile).state.completed_radar
                for profile in ("user1", "user2")
            )
        )
        users = {answer["user"] for answer in self.fake.radar_answers}
        self.assertEqual(users, {"user1", "user2"})

    async def test_partial_login_failure_does_not_stop_group(self) -> None:
        self.fake.fail_login_users.add("user2")
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        app = self.make_app()
        await app.start()
        await self.wait_for(
            lambda: app.worker("user1") is not None
            and "42" in app.worker("user1").state.completed_number
        )
        snapshots = {snap.profile: snap for snap in app.snapshots()}
        self.assertEqual(snapshots["user1"].login_status, "success")
        self.assertNotEqual(snapshots["user2"].login_status, "success")
        report = app.status_report()
        accounts = {item["profile"]: item for item in report["accounts"]}
        self.assertTrue(accounts["user1"]["healthy"])
        self.assertNotEqual(accounts["user2"]["login_status"], "success")
        self.assertIn(accounts["user2"]["phase"], ("logging_in", "waiting_login", "login_failed"))
        self.assertEqual(report["running"], ["user1", "user2"])

    async def test_session_expiry_relogs_only_that_account(self) -> None:
        app = self.make_app()
        await app.start()
        await self.wait_for(
            lambda: {snap.profile: snap.phase for snap in app.snapshots()}
            == {"user1": "monitoring", "user2": "monitoring"}
        )
        polls_user1 = app.worker("user1").state.poll_count
        self.fake.expire_account_session("user2")
        # user2 hits 401, re-logs in, and resumes monitoring on its own.
        await self.wait_for(
            lambda: app.worker("user2").state.last_error is not None
            and app.worker("user2").state.last_error.get("code") == "unauthorized"
        )
        await self.wait_for(lambda: app.snapshot_for("user2").phase == "monitoring")
        # user1 kept polling the whole time.
        await self.wait_for(lambda: app.worker("user1").state.poll_count > polls_user1)


if __name__ == "__main__":
    unittest.main()
