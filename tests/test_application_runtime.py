"""Group monitor assembly tests (Phase 3.4)."""

import asyncio
import copy
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.account_state_repository import FileAccountStateRepository
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

    def make_app(self, config=None, *, ignore_attendance_rate_gate: bool = True) -> MonitorApplication:
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
            ignore_attendance_rate_gate=ignore_attendance_rate_gate,
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

    async def test_qr_auto_assist_when_teacher_configured(self) -> None:
        self.fake.credentials["teacher"] = "tpass"
        self.fake.rollcalls = [{"is_qrcode": True, "rollcall_id": 77, "status": "absent"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        config = make_config()
        config["teacher"] = {"user": "teacher", "passwd": "tpass", "school": "thu", "course": "course-1"}
        app = self.make_app(config)
        await app.start()
        await self.wait_for(
            lambda: all(
                app.worker(p) is not None and "77" in app.worker(p).state.completed_qr
                for p in ("user1", "user2")
            ),
            timeout=8.0,
        )
        # single-flight: teacher rollcall created exactly once.
        self.assertEqual(len(self.fake.teacher_rollcalls), 1)
        await app.stop()

    async def test_qr_without_teacher_reports_manual_required(self) -> None:
        self.fake.rollcalls = [{"is_qrcode": True, "rollcall_id": 88, "status": "absent"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        app = self.make_app()  # no teacher block
        await app.start()
        await self.wait_for(
            lambda: app.worker("user1") is not None
            and app.worker("user1").last_result is not None
            and app.worker("user1").last_result.error_code == "qr_manual_required",
            timeout=8.0,
        )
        await app.stop()

    def test_services_default_to_ocr_solver_without_prompt(self) -> None:
        from tron_roll_call_hero.captcha_solver import OcrCaptchaSolver

        app = MonitorApplication(make_config(), base_dir=self.base)
        services = app._services
        self.assertIsInstance(services.captcha_solver, OcrCaptchaSolver)
        self.assertIsNone(services.captcha_prompt)

    def test_explicit_captcha_collaborators_are_kept(self) -> None:
        solver = object()
        prompt = object()
        app = MonitorApplication(
            make_config(), base_dir=self.base, captcha_solver=solver, captcha_prompt=prompt
        )
        self.assertIs(app._services.captcha_solver, solver)
        self.assertIs(app._services.captcha_prompt, prompt)


class AppMainWorkerPathTest(unittest.IsolatedAsyncioTestCase):
    """Phase 2.8: the single-account CLI entrypoint app_main runs through the
    AccountWorker/supervisor (worker_enabled), not the legacy monitor_loop."""

    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.fake = await FakeTronServer().start()  # default user1 / pass1
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        # app_main reads the process-global runtime context; override and restore.
        self._orig_config = copy.deepcopy(tron.CONFIG)
        self._orig_base = tron.BASE_DIR
        self._orig_path = tron.PATH
        self._orig_bootstrapped = tron.CONFIG_BOOTSTRAPPED
        self._orig_status = dict(tron.MONITOR_STATUS)
        simple = {
            "now": "",
            "accounts": [{"user": "user1", "passwd": "pass1", "school": "thu"}],
            "groups": [],
            "operating": {},
        }
        config = tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))
        # The worker derives endpoints per-spec from the config's provider section
        # (not the patched tron_http globals the legacy loop reads), so point the
        # thu provider override at the fake server.
        ep = self.fake.endpoints()
        config.setdefault("provider", {}).setdefault("available", {})["thu"] = {
            "base_url": ep.base_url,
            "login_url": ep.login_url,
            "rollcalls_url": ep.rollcalls_url,
            "current_semester_url": ep.current_semester_url,
            "courses_url": ep.courses_url,
            "auth_flow": ep.auth_flow,
        }
        tron.CONFIG.clear()
        tron.CONFIG.update(config)
        tron.CONFIG["config"]["enable_log"] = True
        tron.CONFIG_BOOTSTRAPPED = True
        tron.BASE_DIR = self.base
        tron.PATH = self.base / "log"

    async def asyncTearDown(self) -> None:
        tron.CONFIG.clear()
        tron.CONFIG.update(self._orig_config)
        tron.BASE_DIR = self._orig_base
        tron.PATH = self._orig_path
        tron.CONFIG_BOOTSTRAPPED = self._orig_bootstrapped
        tron.MONITOR_STATUS.clear()
        tron.MONITOR_STATUS.update(self._orig_status)
        self.patch.__exit__(None, None, None)
        await self.fake.close()
        shutil.rmtree(self.base, ignore_errors=True)

    async def test_app_main_single_account_submits_number_through_worker(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42}]
        shutdown = asyncio.Event()

        async def stop_when_submitted() -> None:
            for _ in range(500):
                if self.fake.number_attempts:
                    break
                await asyncio.sleep(0.01)
            shutdown.set()

        stopper = asyncio.create_task(stop_when_submitted())
        with (
            patch.object(tron, "bootstrap_config"),
            patch.object(tron, "consume_bootstrap_warnings", return_value=[]),
        ):
            # No worker_enabled arg: rely on the flipped default to prove the
            # production CLI entrypoint now routes through the worker.
            await tron.app_main(
                input_enabled=False,
                external_shutdown_event=shutdown,
                ignore_attendance_rate_gate=True,
            )
        await stopper

        # (1) The single account submitted the number rollcall through the worker
        # (real HTTP against the fake server), not the legacy monitor_loop.
        self.assertTrue(self.fake.number_attempts)

        # (2) Completion was persisted to the per-account repository.
        repo = FileAccountStateRepository(self.base)
        completed = [s for s in repo.list() if "42" in s.completed_number]
        self.assertTrue(completed, "no account persisted completion of rollcall 42")

        # (3) The worker's submission event was dual-written to the daily JSONL
        # audit log (LoggingEventSink wired through the worker path).
        log_path = tron.daily_log_path()
        events = [
            json.loads(line)["event"]
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertIn("rollcall_submission", events)

    async def test_app_main_worker_interactive_renders_status_from_snapshot(self) -> None:
        # input_enabled worker path projects the worker snapshot onto the legacy
        # MONITOR_STATUS so the console status line keeps working.
        self.fake.rollcalls = []  # idle monitoring; we only need a status line
        shutdown = asyncio.Event()

        async def stop_when_status_ready() -> None:
            for _ in range(500):
                if (
                    tron.MONITOR_STATUS.get("phase") == "monitoring"
                    and int(tron.MONITOR_STATUS.get("check_count") or 0) >= 1
                ):
                    break
                await asyncio.sleep(0.01)
            shutdown.set()

        stopper = asyncio.create_task(stop_when_status_ready())
        with (
            patch.object(tron, "bootstrap_config"),
            patch.object(tron, "consume_bootstrap_warnings", return_value=[]),
            patch.object(tron, "console_is_interactive", return_value=True),
            patch.object(tron, "render_status_line"),
        ):
            await tron.app_main(
                input_enabled=True,
                external_shutdown_event=shutdown,
                worker_enabled=True,
                ignore_attendance_rate_gate=True,
            )
        await stopper

        self.assertEqual(tron.MONITOR_STATUS.get("phase"), "monitoring")
        self.assertGreaterEqual(int(tron.MONITOR_STATUS.get("check_count") or 0), 1)


if __name__ == "__main__":
    unittest.main()
