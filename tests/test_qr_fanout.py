"""Manual QR fan-out tests (Phase 4.1)."""

import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

from troTHU import tron
from troTHU import tron_http
from troTHU.account_models import SubmissionStatus
from troTHU.application_runtime import MonitorApplication
from troTHU.qr_fanout import submit_group_qr_payload
from troTHU.runtime_services import CollectingEventSink
from tests.fake_tron_server import FakeTronServer


TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]

QR_SECRET = "fanout-secret-qr-data"
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


class QrFanoutTest(unittest.IsolatedAsyncioTestCase):
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
            make_config(),
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
        await self.wait_for(
            lambda: {snap.profile: snap.phase for snap in self.app.snapshots()}
            == {"user1": "monitoring", "user2": "monitoring"}
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

    async def test_two_accounts_confirmed(self) -> None:
        group = await submit_group_qr_payload(self.app.supervisor, QR_PAYLOAD)
        self.assertEqual(group.rollcall_id, "55")
        self.assertTrue(group.ok)
        statuses = {result.profile: result.status for result in group.results}
        self.assertEqual(
            statuses,
            {"user1": SubmissionStatus.CONFIRMED, "user2": SubmissionStatus.CONFIRMED},
        )
        users = sorted(answer["user"] for answer in self.fake.qr_answers)
        self.assertEqual(users, ["user1", "user2"])

    async def test_partial_failure_keeps_profile_detail(self) -> None:
        self.fake.fail_submit_users.add("user2")
        group = await submit_group_qr_payload(self.app.supervisor, QR_PAYLOAD)
        self.assertFalse(group.ok)
        by_profile = {result.profile: result for result in group.results}
        self.assertEqual(by_profile["user1"].status, SubmissionStatus.CONFIRMED)
        self.assertEqual(by_profile["user2"].status, SubmissionStatus.FAILED)
        counts = group.counts()
        self.assertEqual(counts.get("confirmed"), 1)
        self.assertEqual(counts.get("failed"), 1)

    async def test_provider_mismatch_is_skipped(self) -> None:
        group = await submit_group_qr_payload(
            self.app.supervisor, QR_PAYLOAD, provider="tku"
        )
        self.assertFalse(group.ok)
        for result in group.results:
            self.assertEqual(result.status, SubmissionStatus.SKIPPED_NOT_APPLICABLE)
            self.assertEqual(result.error_code, "provider_mismatch")
        self.assertEqual(len(self.fake.qr_answers), 0)

    async def test_invalid_payload_returns_empty_group(self) -> None:
        group = await submit_group_qr_payload(self.app.supervisor, json.dumps({"data": "x"}))
        self.assertFalse(group.ok)
        self.assertEqual(group.results, ())
        self.assertEqual(len(self.fake.qr_answers), 0)

    async def test_raw_payload_not_in_result_or_events(self) -> None:
        group = await submit_group_qr_payload(self.app.supervisor, QR_PAYLOAD)
        encoded = json.dumps(group.to_dict(), ensure_ascii=False)
        self.assertNotIn(QR_SECRET, encoded)
        events_json = json.dumps(
            [event.to_dict() for event in self.sink.events], ensure_ascii=False
        )
        self.assertNotIn(QR_SECRET, events_json)


if __name__ == "__main__":
    unittest.main()
