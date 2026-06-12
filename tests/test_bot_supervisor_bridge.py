"""Bot-to-supervisor bridge tests (Phase 5.3)."""

import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

from troTHU import tron
from troTHU import tron_http
from troTHU.adapter_bridge import binding_key
from troTHU.application_runtime import MonitorApplication
from troTHU.bot_runtime import BotRuntime
from troTHU.bot_supervisor_bridge import create_supervisor_bot_handlers
from troTHU.runtime_services import CollectingEventSink
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]

QR_SECRET = "bridge-secret-qr-data"
QR_PAYLOAD = json.dumps({"rollcallId": 55, "data": QR_SECRET})


def make_temp() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def make_config() -> dict:
    simple = {
        "now": "class A",
        "accounts": [
            {"user": "user1", "passwd": "pass1", "school": "thu"},
            {"user": "user2", "passwd": "pass2", "school": "thu"},
        ],
        "groups": [{"class": "A", "school": "thu", "users": ["user1", "user2"]}],
        "operating": {},
    }
    config = tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))
    config["integrations"] = {
        "bindings": {
            binding_key("discord", "d-user1"): {
                "adapter": "discord",
                "external_user_id": "d-user1",
                "profile": "user1",
                "channel_id": "chan-1",
            },
        },
        "admins": {"discord": ["admin-1"]},
        "security": {"dangerous_cooldown_seconds": 0, "audit_log": False},
    }
    return config


class BotSupervisorBridgeTest(unittest.IsolatedAsyncioTestCase):
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
        self.config = make_config()
        self.app = MonitorApplication(
            self.config,
            base_dir=self.base,
            endpoints=self.fake.endpoints(),
            event_sink=CollectingEventSink(),
            use_schedule=False,
            poll_interval=30.0,
            standby_interval=0.01,
            login_backoff=(0.01, 0.02),
            restart_backoff=(0.01, 0.02),
        )
        await self.app.start()
        await self.wait_for(
            lambda: {snap.profile: snap.phase for snap in self.app.snapshots()}
            == {"user1": "monitoring", "user2": "monitoring"}
        )
        self.runtime = BotRuntime(
            self.config,
            handlers=create_supervisor_bot_handlers(self.app),
            runtime_base_dir=self.base,
        )

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

    async def test_status_shows_real_worker_state(self) -> None:
        result = await self.runtime.handle_text(
            "status", adapter="discord", source_user_id="d-user1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.profile, "user1")
        self.assertIn("monitoring", result.reply)
        self.assertEqual(result.data.get("phase"), "monitoring")
        self.assertEqual(result.data.get("login_status"), "success")

    async def test_regular_user_cannot_control_other_profile(self) -> None:
        result = await self.runtime.handle_text(
            "force user2", adapter="discord", source_user_id="d-user1", channel_id="chan-1"
        )
        self.assertFalse(result.ok)

    async def test_admin_force_routes_to_correct_worker(self) -> None:
        polls_user1 = self.app.worker("user1").state.poll_count
        polls_user2 = self.app.worker("user2").state.poll_count
        result = await self.runtime.handle_text(
            "force user2", adapter="discord", source_user_id="admin-1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.profile, "user2")
        self.assertEqual(self.app.worker("user2").state.poll_count, polls_user2 + 1)
        self.assertEqual(self.app.worker("user1").state.poll_count, polls_user1)

    async def test_admin_reauth_only_touches_target_worker(self) -> None:
        cookies_user1 = len(list(self.app.worker("user1").session.cookie_jar))
        self.assertGreater(cookies_user1, 0)
        result = await self.runtime.handle_text(
            "reauth user2", adapter="discord", source_user_id="admin-1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.profile, "user2")
        self.assertTrue(result.data.get("reauth_requested"))
        # user1's session cookies are untouched.
        self.assertEqual(len(list(self.app.worker("user1").session.cookie_jar)), cookies_user1)

    async def test_stop_and_start_really_control_worker(self) -> None:
        result = await self.runtime.handle_text(
            "stop", adapter="discord", source_user_id="d-user1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        self.assertEqual(self.app.supervisor.running_profiles(), ("user2",))

        result = await self.runtime.handle_text(
            "start", adapter="discord", source_user_id="d-user1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        await self.wait_for(lambda: "user1" in self.app.supervisor.running_profiles())

    async def test_qr_all_returns_per_account_results_with_redaction(self) -> None:
        result = await self.runtime.handle_text(
            "qr all {}".format(QR_PAYLOAD),
            adapter="discord",
            source_user_id="admin-1",
            channel_id="chan-1",
        )
        self.assertTrue(result.ok)
        results = result.data.get("results") or []
        statuses = {item["profile"]: item["status"] for item in results}
        self.assertEqual(statuses, {"user1": "confirmed", "user2": "confirmed"})
        encoded = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn(QR_SECRET, encoded)
        self.assertIn("user1", result.reply)
        self.assertIn("user2", result.reply)


class DecouplingTest(unittest.TestCase):
    def test_bridge_does_not_use_switch_profile_or_runtime_context(self) -> None:
        import ast
        import inspect

        import troTHU.bot_supervisor_bridge as module

        source = inspect.getsource(module)
        self.assertNotIn("switch_profile", source)
        tree = ast.parse(source)
        imported: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("runtime_context" in name for name in imported))


if __name__ == "__main__":
    unittest.main()
