"""Dashboard event store tests (dashboard spec 2026-06-12)."""

import json
import shutil
import unittest
import uuid
from pathlib import Path

from tron_roll_call_hero.dashboard_events import DashboardEventStore
from tron_roll_call_hero.runtime_events import account_event
from tron_roll_call_hero.runtime_services import CollectingEventSink, FixedClock

TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_temp() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def submission(profile: str, status: str, *, event: str = "rollcall_submission"):
    return account_event(
        event,
        profile=profile,
        provider_key="thu",
        status=status,
        rollcall_id="42",
        attendance_type="number",
    )


class DashboardEventStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = make_temp()
        self.clock = FixedClock(1_700_000_000.0)
        self.store = DashboardEventStore(self.base, capacity=3, clock=self.clock)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def jsonl_dir(self) -> Path:
        return self.base / "state" / "dashboard"

    def test_ring_buffer_keeps_newest_up_to_capacity(self) -> None:
        for index in range(4):
            self.store.emit(submission("user{}".format(index), "confirmed"))
        recent = self.store.recent(50)
        self.assertEqual(len(recent), 3)
        # 新到舊
        self.assertEqual([item["profile"] for item in recent], ["user3", "user2", "user1"])

    def test_recent_respects_limit(self) -> None:
        for index in range(3):
            self.store.emit(submission("user{}".format(index), "confirmed"))
        self.assertEqual(len(self.store.recent(2)), 2)
        self.assertEqual(self.store.recent(2)[0]["profile"], "user2")

    def test_jsonl_rotates_per_day(self) -> None:
        self.store.emit(submission("user1", "confirmed"))
        self.clock.advance(86400)
        self.store.emit(submission("user1", "failed"))
        files = sorted(self.jsonl_dir().glob("events-*.jsonl"))
        self.assertEqual(len(files), 2)
        first_line = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first_line["profile"], "user1")
        self.assertIn("ts", first_line)

    def test_stats_aggregates_submissions_per_account(self) -> None:
        confirm_ts = self.clock.now()
        self.store.emit(submission("user1", "confirmed"))
        self.store.emit(submission("user1", "submitted_unconfirmed"))
        self.store.emit(submission("user2", "failed"))
        self.store.emit(submission("user2", "skipped_already_complete"))
        self.store.emit(submission("user1", "confirmed", event="qr_submission"))
        # login 事件不列入統計
        self.store.emit(
            account_event("login", profile="user1", provider_key="thu", status="success")
        )
        stats = self.store.stats(7)
        self.assertEqual(stats["days"], 7)
        self.assertEqual(stats["accounts"]["user1"]["confirmed"], 2)
        self.assertEqual(stats["accounts"]["user1"]["submitted_unconfirmed"], 1)
        self.assertEqual(stats["accounts"]["user1"]["last_confirmed_ts"], confirm_ts)
        self.assertEqual(stats["accounts"]["user2"]["failed"], 1)
        self.assertEqual(stats["accounts"]["user2"]["skipped"], 1)

    def test_stats_only_reads_requested_days(self) -> None:
        self.store.emit(submission("user1", "confirmed"))
        self.clock.advance(86400 * 10)
        stats = self.store.stats(7)
        self.assertEqual(stats["accounts"], {})

    def test_write_failure_degrades_without_raising(self) -> None:
        # 讓 state/dashboard 路徑變成「檔案」，mkdir/開檔都會失敗。
        (self.base / "state").mkdir()
        (self.base / "state" / "dashboard").write_text("not a dir", encoding="utf-8")
        self.store.emit(submission("user1", "confirmed"))  # 不可丟例外
        self.assertEqual(len(self.store.recent(10)), 1)

    def test_tees_into_inner_sink(self) -> None:
        inner = CollectingEventSink()
        store = DashboardEventStore(self.base, inner=inner, clock=self.clock)
        event = submission("user1", "confirmed")
        store.emit(event)
        self.assertEqual(inner.events, [event])

    def test_no_secret_strings_in_jsonl(self) -> None:
        event = account_event(
            "qr_submission",
            profile="user1",
            provider_key="thu",
            status="confirmed",
            data={"password": "super-secret-pw", "qr_data": "raw-qr-secret"},
        )
        self.store.emit(event)
        text = "".join(
            path.read_text(encoding="utf-8") for path in self.jsonl_dir().glob("*.jsonl")
        )
        self.assertNotIn("super-secret-pw", text)
        self.assertNotIn("raw-qr-secret", text)


if __name__ == "__main__":
    unittest.main()
