"""Dashboard server route tests (dashboard spec 2026-06-12)."""

import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

try:
    import aiohttp
    from aiohttp import web
except (ImportError, ModuleNotFoundError):
    aiohttp = None
    web = None

from tron_roll_call_hero import tron
from tron_roll_call_hero import tron_http
from tron_roll_call_hero.application_runtime import MonitorApplication
from tron_roll_call_hero.dashboard_events import DashboardEventStore
from tron_roll_call_hero.dashboard_server import register_dashboard_routes
from tests.fake_tron_server import FakeTronServer

TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]

TOKEN = "test-dashboard-token"
QR_SECRET = "dashboard-secret-qr-data"
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
    return tron.normalize_config(tron.merge_simple_and_advanced_config(simple, {}))


class RunningApp:
    def __init__(self, app) -> None:
        self.app = app
        self.runner = None
        self.base_url = ""

    async def __aenter__(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = "http://127.0.0.1:{}".format(port)
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.runner.cleanup()


@unittest.skipUnless(aiohttp is not None and web is not None, "aiohttp.web is required")
class DashboardServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.base = make_temp()
        self.fake = await FakeTronServer(
            credentials={"user1": "pass1", "user2": "pass2"}
        ).start()
        self.fake.per_account_state = True
        # 注意：active rollcall 由個別測試注入；setUp 先讓 worker 在空 feed
        # 下完成第一次輪詢，之後睡 30s，只有 force 會觸發下一次。
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]
        self.patch = self.fake.patch_tron_http_urls(tron_http)
        self.patch.__enter__()
        self.store = DashboardEventStore(self.base)
        self.app = MonitorApplication(
            make_config(),
            base_dir=self.base,
            endpoints=self.fake.endpoints(),
            event_sink=self.store,
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
        web_app = web.Application()
        register_dashboard_routes(web_app, self.app, self.store, token=TOKEN)
        self.server = RunningApp(web_app)
        await self.server.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.server.__aexit__(None, None, None)
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

    def url(self, path: str, *, token: str = TOKEN) -> str:
        sep = "&" if "?" in path else "?"
        return "{}{}{}token={}".format(self.server.base_url, path, sep, token)

    async def test_routes_reject_missing_or_wrong_token(self) -> None:
        async with aiohttp.ClientSession() as session:
            for path in (
                "/dashboard",
                "/dashboard/api/status",
                "/dashboard/api/events",
                "/dashboard/api/stats",
            ):
                response = await session.get(self.server.base_url + path)
                self.assertEqual(response.status, 401, path)
            response = await session.get(self.url("/dashboard/api/status", token="wrong"))
            self.assertEqual(response.status, 401)
            response = await session.post(
                self.server.base_url + "/dashboard/api/force", json={"profile": "user1"}
            )
            self.assertEqual(response.status, 401)

    async def test_header_token_is_accepted(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.get(
                self.server.base_url + "/dashboard/api/status",
                headers={"X-Dashboard-Token": TOKEN},
            )
            self.assertEqual(response.status, 200)

    async def test_status_returns_supervisor_report(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.get(self.url("/dashboard/api/status"))
            body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(sorted(body["running"]), ["user1", "user2"])
        profiles = {item["profile"]: item for item in body["accounts"]}
        self.assertEqual(profiles["user1"]["phase"], "monitoring")
        self.assertNotIn("pass1", json.dumps(body))

    async def test_events_returns_recent_login_events(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.get(self.url("/dashboard/api/events?limit=10"))
            body = await response.json()
        self.assertEqual(response.status, 200)
        events = body["events"]
        self.assertTrue(any(item["event"] == "login" for item in events))
        self.assertLessEqual(len(events), 10)

    async def test_force_targets_one_worker_only(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/force"), json={"profile": "user1"}
            )
            body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["profile"], "user1")
        # 只有 user1 真的提交了 number 答案。
        users = {item["user"] for item in self.fake.number_attempts}
        self.assertEqual(users, {"user1"})

    async def test_force_all_hits_every_running_worker(self) -> None:
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/force"), json={"profile": "all"}
            )
            body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(
            sorted(item["profile"] for item in body["results"]), ["user1", "user2"]
        )

    async def test_force_unknown_profile_is_404(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/force"), json={"profile": "ghost"}
            )
        self.assertEqual(response.status, 404)

    async def test_reauth_routes_to_worker(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/reauth"), json={"profile": "user1"}
            )
            body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(body["reauth_requested"])

    async def test_qr_fanout_returns_per_profile_results_without_payload_echo(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/qr"),
                json={"payload": QR_PAYLOAD, "profiles": None},
            )
            text = await response.text()
            body = json.loads(text)
        self.assertEqual(response.status, 200)
        profiles = sorted(item["profile"] for item in body["results"])
        self.assertEqual(profiles, ["user1", "user2"])
        self.assertNotIn(QR_SECRET, text)

    async def test_invalid_json_body_is_400(self) -> None:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                self.url("/dashboard/api/force"),
                data="not-json",
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
