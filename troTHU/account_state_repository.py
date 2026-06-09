"""Per-account state persistence (Phase 1.3).

Each account owns an isolated directory so concurrent workers never read or
write each other's files:

    state/accounts/<normalized-profile>/runtime.json
    state/accounts/<normalized-profile>/cookies.json

Writes are atomic (temp file in the same directory + ``os.replace``). Profile
names are normalized to a single safe directory component, so a malicious or
malformed profile cannot traverse outside the accounts directory. Corrupt files
fall back to a safe empty snapshot. The legacy single-file
``state/account_runtime.json`` and ``state/cookies/<profile>.json`` are read as a
backward-compatible fallback and can be migrated into the new layout.

Deviation from the framework doc: it sketches an *async* ``AccountStateRepository``
Protocol. Phase 1 does not run the async monitor and per-account files have no
cross-worker contention, so this ships a synchronous repository (matching the
existing ``account_runtime_store`` sync API). It can be wrapped for async use
when workers are introduced in a later phase.

This module must not import ``troTHU.runtime_context``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple, runtime_checkable

from troTHU.account_models import AccountStateSnapshot


RUNTIME_FILENAME = "runtime.json"
COOKIE_FILENAME = "cookies.json"
LEGACY_RUNTIME_FILENAME = "account_runtime.json"
PROFILE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def normalize_profile_name(value: Any) -> str:
    """Reduce a profile name to a single safe directory component."""
    name = PROFILE_NAME_PATTERN.sub("-", str(value or "").strip()).strip("-._")
    return name or "default"


def _snapshot_from_legacy(profile: str, record: Mapping[str, Any]) -> AccountStateSnapshot:
    data = dict(record)
    # The legacy store named the login outcome ``last_login``.
    data["login"] = data.pop("last_login", {})
    return AccountStateSnapshot.from_dict(profile, data, store_status="migrated")


@runtime_checkable
class AccountStateRepository(Protocol):
    def load(self, profile: str) -> AccountStateSnapshot: ...

    def save(self, snapshot: AccountStateSnapshot) -> AccountStateSnapshot: ...

    def list(self) -> Tuple[AccountStateSnapshot, ...]: ...


class FileAccountStateRepository:
    """Filesystem-backed per-account state and cookie repository."""

    def __init__(self, base_dir: Any) -> None:
        self.base_dir = Path(base_dir)

    # -- paths -------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.base_dir / "state"

    @property
    def accounts_dir(self) -> Path:
        return self.state_dir / "accounts"

    def account_dir(self, profile: str) -> Path:
        return self.accounts_dir / normalize_profile_name(profile)

    def runtime_path(self, profile: str) -> Path:
        return self.account_dir(profile) / RUNTIME_FILENAME

    def cookie_path(self, profile: str) -> Path:
        return self.account_dir(profile) / COOKIE_FILENAME

    def legacy_runtime_path(self) -> Path:
        return self.state_dir / LEGACY_RUNTIME_FILENAME

    def legacy_cookie_path(self, profile: str) -> Path:
        return self.state_dir / "cookies" / "{}.json".format(normalize_profile_name(profile))

    # -- atomic write ------------------------------------------------------
    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -- runtime snapshots -------------------------------------------------
    def save(self, snapshot: AccountStateSnapshot) -> AccountStateSnapshot:
        snapshot.updated_at = time.time()
        payload = snapshot.to_dict(include_meta=False)
        self._atomic_write(
            self.runtime_path(snapshot.profile),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return snapshot

    def load(self, profile: str) -> AccountStateSnapshot:
        path = self.runtime_path(profile)
        if path.exists():
            data = self._read_json(path)
            if not isinstance(data, Mapping):
                return AccountStateSnapshot(profile=profile, store_status="corrupt")
            return AccountStateSnapshot.from_dict(profile, data, store_status="ok")
        legacy = self._legacy_runtime_record(profile)
        if legacy is not None:
            return _snapshot_from_legacy(profile, legacy)
        return AccountStateSnapshot(profile=profile, store_status="missing")

    def list(self) -> Tuple[AccountStateSnapshot, ...]:
        if not self.accounts_dir.is_dir():
            return ()
        snapshots: List[AccountStateSnapshot] = []
        for child in sorted(self.accounts_dir.iterdir()):
            if child.is_dir() and (child / RUNTIME_FILENAME).exists():
                snapshots.append(self.load(child.name))
        return tuple(snapshots)

    # -- cookies -----------------------------------------------------------
    def save_cookies(self, profile: str, records: Any) -> List[Dict[str, Any]]:
        clean = [dict(record) for record in (records or []) if isinstance(record, Mapping)]
        self._atomic_write(
            self.cookie_path(profile),
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        )
        return clean

    def load_cookies(self, profile: str) -> List[Dict[str, Any]]:
        path = self.cookie_path(profile)
        if path.exists():
            return self._read_cookie_file(path)
        legacy = self.legacy_cookie_path(profile)
        if legacy.exists():
            return self._read_cookie_file(legacy)
        return []

    def clear_cookies(self, profile: str) -> bool:
        path = self.cookie_path(profile)
        if not path.exists():
            return False
        os.remove(path)
        return True

    @staticmethod
    def _read_cookie_file(path: Path) -> List[Dict[str, Any]]:
        data = FileAccountStateRepository._read_json(path)
        if not isinstance(data, list):
            return []
        return [dict(record) for record in data if isinstance(record, Mapping)]

    # -- legacy migration --------------------------------------------------
    def _read_legacy_runtime_profiles(self) -> Dict[str, Any]:
        path = self.legacy_runtime_path()
        if not path.exists():
            return {}
        data = self._read_json(path)
        if not isinstance(data, Mapping):
            return {}
        profiles = data.get("profiles")
        return dict(profiles) if isinstance(profiles, Mapping) else {}

    def _legacy_runtime_record(self, profile: str) -> Optional[Mapping[str, Any]]:
        target = normalize_profile_name(profile)
        for key, record in self._read_legacy_runtime_profiles().items():
            if isinstance(record, Mapping) and normalize_profile_name(key) == target:
                return record
        return None

    def migrate_legacy(self, *, write: bool = True) -> Dict[str, List[str]]:
        """Copy legacy single-file state/cookies into the per-account layout.

        Non-destructive: existing per-account files are left untouched and legacy
        files are not deleted. Returns the normalized profiles touched.
        """
        report: Dict[str, List[str]] = {"runtime": [], "cookies": []}

        for key, record in self._read_legacy_runtime_profiles().items():
            if not isinstance(record, Mapping):
                continue
            normalized = normalize_profile_name(key)
            if write and not self.runtime_path(key).exists():
                self.save(_snapshot_from_legacy(key, record))
            report["runtime"].append(normalized)

        legacy_cookies_dir = self.state_dir / "cookies"
        if legacy_cookies_dir.is_dir():
            for path in sorted(legacy_cookies_dir.glob("*.json")):
                profile = path.stem
                if write and not self.cookie_path(profile).exists():
                    self.save_cookies(profile, self._read_cookie_file(path))
                report["cookies"].append(normalize_profile_name(profile))

        return report
