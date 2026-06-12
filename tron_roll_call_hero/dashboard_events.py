"""Dashboard event store (dashboard spec 2026-06-12).

A ``RuntimeEventSink`` that keeps the most recent events in a ring buffer for
the dashboard event stream and appends every event to a per-day JSONL file for
history stats. Events are sanitized at construction (``RuntimeEvent``), so no
secret can reach the buffer or disk. File write failures only degrade history;
monitoring and any wrapped sink keep working.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Mapping, Optional

from tron_roll_call_hero.runtime_services import Clock, SystemClock

_SUBMISSION_EVENTS = {"rollcall_submission", "qr_submission"}


class DashboardEventStore:
    """Tee event sink feeding the local dashboard."""

    def __init__(
        self,
        base_dir: Any,
        *,
        capacity: int = 200,
        inner: Any = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._dir = Path(base_dir) / "state" / "dashboard"
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(capacity)))
        self._inner = inner
        self._clock = clock if clock is not None else SystemClock()

    # -- sink ----------------------------------------------------------------
    def emit(self, event: Any) -> None:
        record = self._to_record(event)
        if record is not None:
            self._buffer.append(record)
            self._append_jsonl(record)
        if self._inner is not None:
            self._inner.emit(event)

    def _to_record(self, event: Any) -> Optional[Dict[str, Any]]:
        to_dict = getattr(event, "to_dict", None)
        if not callable(to_dict):
            return None
        try:
            record = dict(to_dict())
        except Exception:
            return None
        record["ts"] = float(self._clock.now())
        return record

    def _day_path(self, ts: float) -> Path:
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        return self._dir / "events-{}.jsonl".format(day)

    def _append_jsonl(self, record: Mapping[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._day_path(float(record["ts"])).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # History is best-effort; never let disk problems break monitoring.
            return

    # -- queries ---------------------------------------------------------------
    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._buffer)[-max(0, int(limit)):]
        return [dict(item) for item in reversed(items)]

    def stats(self, days: int = 7) -> Dict[str, Any]:
        accounts: Dict[str, Dict[str, Any]] = {}
        for record in self._iter_history(days):
            if record.get("event") not in _SUBMISSION_EVENTS:
                continue
            profile = str(record.get("profile") or "")
            status = str(record.get("status") or "")
            if not profile:
                continue
            entry = accounts.setdefault(
                profile,
                {
                    "confirmed": 0,
                    "submitted_unconfirmed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "last_confirmed_ts": None,
                },
            )
            if status == "confirmed":
                entry["confirmed"] += 1
                ts = record.get("ts")
                if isinstance(ts, (int, float)):
                    previous = entry["last_confirmed_ts"]
                    entry["last_confirmed_ts"] = ts if previous is None else max(previous, ts)
            elif status == "submitted_unconfirmed":
                entry["submitted_unconfirmed"] += 1
            elif status.startswith("skipped_"):
                entry["skipped"] += 1
            elif status in {"failed", "login_failed"}:
                entry["failed"] += 1
        return {"days": int(days), "accounts": accounts}

    def _iter_history(self, days: int) -> Iterator[Dict[str, Any]]:
        now = datetime.fromtimestamp(float(self._clock.now()))
        for offset in range(max(1, int(days))):
            day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            path = self._dir / "events-{}.jsonl".format(day)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
