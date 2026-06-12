"""Bot-to-supervisor bridge tests (Phase 5.3)."""

import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.adapter_bridge import binding_key
from tron_roll_call_hero.application_runtime import MonitorApplication
from tron_roll_call_hero.bot_runtime import BotRuntime
from tron_roll_call_hero.bot_supervisor_bridge import create_supervisor_bot_handlers
from tron_roll_call_hero.runtime_services import CollectingEventSink
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
            binding_key("line", "line-user1"): {
                "adapter": "line",
                "external_user_id": "line-user1",
                "profile": "user1",
                "channel_id": "",
            },
        },
        "admins": {"discord": ["admin-1"]},
        "security": {"dangerous_cooldown_seconds": 0, "audit_log": False},
        "discord": {"public_key_env": "TEST_BRIDGE_DISCORD_PUBLIC_KEY"},
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

    async def test_admin_force_all_triggers_all_running_workers(self) -> None:
        polls_user1 = self.app.worker("user1").state.poll_count
        polls_user2 = self.app.worker("user2").state.poll_count
        result = await self.runtime.handle_text(
            "force all", adapter="discord", source_user_id="admin-1", channel_id="chan-1"
        )
        self.assertTrue(result.ok)
        results = result.data.get("results") or []
        self.assertEqual(
            sorted(item["profile"] for item in results), ["user1", "user2"]
        )
        self.assertEqual(self.app.worker("user1").state.poll_count, polls_user1 + 1)
        self.assertEqual(self.app.worker("user2").state.poll_count, polls_user2 + 1)

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


class SupervisedBotRuntimeFactoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_factory_builds_connected_app_and_runtime(self) -> None:
        from tron_roll_call_hero.bot_supervisor_bridge import create_supervised_bot_runtime

        base = make_temp()
        fake = await FakeTronServer(credentials={"user1": "pass1", "user2": "pass2"}).start()
        patch = fake.patch_tron_http_urls(tron_http)
        patch.__enter__()
        try:
            app, runtime = create_supervised_bot_runtime(
                make_config(),
                base_dir=base,
                endpoints=fake.endpoints(),
                use_schedule=False,
                poll_interval=30.0,
                login_backoff=(0.01, 0.02),
                restart_backoff=(0.01, 0.02),
            )
            report = await app.start()
            self.assertEqual(report.started, ("user1", "user2"))
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                snap = app.snapshot_for("user1")
                if snap is not None and snap.phase == "monitoring":
                    break
                await asyncio.sleep(0.01)
            result = await runtime.handle_text(
                "status", adapter="discord", source_user_id="d-user1", channel_id="chan-1"
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.data.get("phase"), "monitoring")
            await app.stop()
        finally:
            patch.__exit__(None, None, None)
            await fake.close()
            shutil.rmtree(base, ignore_errors=True)


class AdapterSupervisorE2ETest(BotSupervisorBridgeTest):
    """Full-chain E2E: platform webhook -> adapter server -> BotRuntime -> worker."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from unittest.mock import patch

        from aiohttp import web

        from tron_roll_call_hero.adapter_server import create_app

        self._env_patch = patch.dict(
            "os.environ", {"TEST_BRIDGE_DISCORD_PUBLIC_KEY": "test-public-key"}, clear=False
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        self.line_replies: list = []

        async def fake_line_sender(**kwargs):
            self.line_replies.append(kwargs)
            return {"ok": True}

        self.server_app = create_app(
            self.config,
            self.runtime,
            line_sender=fake_line_sender,
            adapter="all",
            discord_signature_verifier=lambda **_kwargs: True,
        )
        self.runner = web.AppRunner(self.server_app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.server_url = "http://127.0.0.1:{}".format(port)

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()
        await super().asyncTearDown()

    async def test_line_webhook_status_reflects_live_worker(self) -> None:
        import aiohttp

        event_body = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-1",
                    "message": {"type": "text", "text": "status"},
                    "source": {"userId": "line-user1"},
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.server_url + "/line/webhook", json=event_body
            ) as response:
                body = await response.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        result = body["results"][0]
        self.assertEqual(result["profile"], "user1")
        self.assertIn("monitoring", result["reply"])
        # The reply went back through the LINE sender, redacted and live.
        self.assertEqual(len(self.line_replies), 1)
        self.assertIn("monitoring", self.line_replies[0]["text"])

    async def test_discord_interaction_status_reflects_live_worker(self) -> None:
        import aiohttp

        payload = {
            "type": 2,
            "data": {"name": "status"},
            "member": {"user": {"id": "d-user1"}},
            "channel_id": "chan-1",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.server_url + "/discord/interactions",
                json=payload,
                headers={
                    "X-Signature-Ed25519": "sig",
                    "X-Signature-Timestamp": "ts",
                },
            ) as response:
                body = await response.json()

        self.assertEqual(response.status, 200)
        self.assertIn("monitoring", body["data"]["content"])

    async def test_line_qr_fanout_returns_per_account_results(self) -> None:
        import aiohttp

        event_body = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-2",
                    "message": {"type": "text", "text": "qr {}".format(QR_PAYLOAD)},
                    "source": {"userId": "line-user1"},
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.server_url + "/line/webhook", json=event_body
            ) as response:
                body = await response.json()

        self.assertEqual(response.status, 200)
        result = body["results"][0]
        self.assertTrue(result["ok"])
        self.assertIn("user1: confirmed", result["reply"])
        self.assertNotIn(QR_SECRET, json.dumps(body, ensure_ascii=False))


class DecouplingTest(unittest.TestCase):
    def test_bridge_does_not_use_switch_profile_or_runtime_context(self) -> None:
        import ast
        import inspect

        import tron_roll_call_hero.bot_supervisor_bridge as module

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
