"""Resolve a normalized config into passwordless account specs (Phase 1.2).

``AccountRegistry`` is the single place that turns the simple-config account /
group / ``now`` metadata into :class:`~tron_roll_call_hero.account_models.AccountSpec`
objects and resolves the ``now`` selector into the desired accounts. It performs
no network operations and never copies a password into a spec or resolution
result.

Differences from the legacy ``group_runtime`` resolver (intentional, per
ADR 0001):

- A group may mix providers, so members whose provider differs from the group's
  ``school`` are *not* skipped with ``school_mismatch``.
- Provider ownership lives on each ``AccountSpec``; there is no global active
  provider.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from tron_roll_call_hero import providers
from tron_roll_call_hero.account_models import (
    AccountSpec,
    CredentialRef,
    CredentialSource,
    ScheduleSpec,
)


CredentialLocator = Callable[[str, str], Optional[CredentialSource]]
_PLACEHOLDER_OPEN = ("(", "（")
_PLACEHOLDER_CLOSE = (")", "）")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _strip_value(value: Any) -> str:
    text = _text(value)
    if text.startswith(_PLACEHOLDER_OPEN) and text.endswith(_PLACEHOLDER_CLOSE):
        return ""
    return text


def _has_credential(value: Any) -> bool:
    return bool(_strip_value(value))


@dataclass(frozen=True)
class SkippedAccount:
    """A configured account that could not become a usable spec."""

    user: str
    reason: str
    profile: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"user": self.user, "reason": self.reason, "profile": self.profile}


@dataclass(frozen=True)
class TargetResolution:
    kind: str  # "account" | "group" | "empty" | "invalid"
    requested: str = ""
    profiles: Tuple[str, ...] = ()
    skipped: Tuple[SkippedAccount, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.kind in {"account", "group"} and bool(self.profiles)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "requested": self.requested,
            "profiles": list(self.profiles),
            "skipped": [item.to_dict() for item in self.skipped],
            "warnings": list(self.warnings),
        }


class AccountRegistry:
    def __init__(self, config: Mapping[str, Any], *, credential_locator: Optional[CredentialLocator] = None) -> None:
        self._config: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
        self._locator = credential_locator

        meta = self._simple_meta()
        self._now: str = _strip_value(meta.get("now"))
        self._groups: List[Dict[str, Any]] = [
            dict(group) for group in meta.get("groups", []) if isinstance(group, Mapping)
        ]

        self._order: List[str] = []  # profile names, deduped, in order
        self._resolved: Dict[str, Any] = {}  # profile-name.lower() -> AccountSpec | SkippedAccount
        self._user_to_profile: Dict[str, str] = {}  # user.lower() -> profile name
        self._specs: List[AccountSpec] = []
        self._spec_by_profile: Dict[str, AccountSpec] = {}

        for entry in self._account_entries(meta):
            user = _strip_value(entry.get("user"))
            if not user:
                continue
            profile = _strip_value(entry.get("profile")) or user
            key = profile.lower()
            if key in self._resolved:
                continue
            self._order.append(profile)
            resolved = self._resolve_entry(profile, user, entry)
            self._resolved[key] = resolved
            self._user_to_profile.setdefault(user.lower(), profile)
            if isinstance(resolved, AccountSpec):
                self._specs.append(resolved)
                self._spec_by_profile[resolved.profile] = resolved

    # -- config sourcing ---------------------------------------------------
    def _simple_meta(self) -> Dict[str, Any]:
        meta = self._config.get("_simple")
        if isinstance(meta, Mapping):
            return dict(meta)
        return {}

    def _account_entries(self, meta: Mapping[str, Any]) -> List[Dict[str, Any]]:
        accounts = meta.get("accounts")
        if isinstance(accounts, list) and accounts:
            entries: List[Dict[str, Any]] = []
            for item in accounts:
                if isinstance(item, Mapping):
                    entry = dict(item)
                    # Simple config has no separate profile name; the user is the identity.
                    entry.setdefault("profile", _strip_value(entry.get("user")))
                    entries.append(entry)
            return entries
        # Fallback for configs without simple metadata: use normalized profiles,
        # preserving the dict KEY as the local profile name (it may differ from user).
        profiles = self._config.get("accounts")
        if isinstance(profiles, Mapping):
            raw_profiles = profiles.get("profiles")
            if isinstance(raw_profiles, Mapping):
                entries = []
                for name, item in raw_profiles.items():
                    if isinstance(item, Mapping):
                        entry = dict(item)
                        entry["profile"] = name
                        entries.append(entry)
                return entries
        return []

    # -- spec construction -------------------------------------------------
    def _resolve_entry(self, profile: str, user: str, entry: Mapping[str, Any]):
        school = _strip_value(entry.get("school")) or _strip_value(entry.get("label"))
        provider_key = providers.normalize_provider_name(school)
        if provider_key not in providers.PROVIDERS:
            return SkippedAccount(user=user, reason="unknown_provider", profile=profile)

        provider = providers.PROVIDERS[provider_key]
        credential_ref = self._resolve_credential_ref(profile, user, provider, entry)
        if credential_ref is None:
            return SkippedAccount(user=user, reason="missing_credential", profile=profile)

        return AccountSpec(
            profile=profile,
            user=user,
            provider_key=provider_key,
            credential_ref=credential_ref,
            schedule=ScheduleSpec(source="global"),
            enabled=True,
        )

    def _resolve_credential_ref(
        self, profile: str, user: str, provider, entry: Mapping[str, Any]
    ) -> Optional[CredentialRef]:
        # Manual-cookie providers authenticate with an imported cookie, no password.
        if getattr(provider, "auth_flow", "") == "manual_cookie_only":
            return CredentialRef(CredentialSource.MANUAL_COOKIE, profile=profile, user=user)

        if self._locator is not None:
            located = self._locator(profile, user)
            if located is not None:
                return CredentialRef(located, profile=profile, user=user)

        if _has_credential(entry.get("passwd")):
            return CredentialRef(CredentialSource.CONFIG, profile=profile, user=user)

        return None

    # -- public API --------------------------------------------------------
    def list_specs(self) -> Tuple[AccountSpec, ...]:
        return tuple(self._specs)

    def resolve_target(self, now: Optional[str] = None) -> TargetResolution:
        requested = self._now if now is None else _strip_value(now)
        if not requested:
            return self._resolve_blank()
        if requested.lower().startswith("class "):
            return self._resolve_group(requested, _strip_value(requested[6:]))
        return self._resolve_account(requested)

    def desired_specs(self, now: Optional[str] = None) -> Tuple[AccountSpec, ...]:
        resolution = self.resolve_target(now)
        return tuple(
            self._spec_by_profile[profile]
            for profile in resolution.profiles
            if profile in self._spec_by_profile
        )

    # -- resolution helpers ------------------------------------------------
    def _resolve_blank(self) -> TargetResolution:
        if len(self._specs) == 1:
            return TargetResolution(kind="account", requested="", profiles=(self._specs[0].profile,))
        return TargetResolution(kind="empty", requested="")

    def _lookup(self, requested: str):
        """Match a selector against a profile name first, then a user."""
        entry = self._resolved.get(requested.lower())
        if entry is not None:
            return entry
        profile = self._user_to_profile.get(requested.lower())
        if profile is not None:
            return self._resolved.get(profile.lower())
        return None

    def _resolve_account(self, requested: str) -> TargetResolution:
        entry = self._lookup(requested)
        if isinstance(entry, AccountSpec):
            return TargetResolution(kind="account", requested=requested, profiles=(entry.profile,))
        if isinstance(entry, SkippedAccount):
            return TargetResolution(kind="account", requested=requested, skipped=(entry,))
        return TargetResolution(
            kind="invalid",
            requested=requested,
            skipped=(SkippedAccount(user=requested, reason="account_not_found"),),
        )

    def _find_group(self, name: str) -> Optional[Dict[str, Any]]:
        for group in self._groups:
            if _strip_value(group.get("class")).lower() == name.lower():
                return group
        return None

    def _resolve_group(self, requested: str, name: str) -> TargetResolution:
        group = self._find_group(name)
        if group is None:
            return TargetResolution(
                kind="group",
                requested=requested,
                warnings=("群組 `{}` 不存在。".format(name),),
            )

        profiles: List[str] = []
        skipped: List[SkippedAccount] = []
        warnings: List[str] = []
        seen: set = set()
        for raw_user in group.get("users", []) or []:
            user = _strip_value(raw_user)
            if not user:
                continue
            key = user.lower()
            if key in seen:
                continue
            seen.add(key)
            entry = self._lookup(user)
            if isinstance(entry, AccountSpec):
                profiles.append(entry.profile)
            elif isinstance(entry, SkippedAccount):
                skipped.append(entry)
                warnings.append("群組帳號 `{}` 已略過：{}。".format(user, entry.reason))
            else:
                skipped.append(SkippedAccount(user=user, reason="account_not_found"))
                warnings.append("群組帳號 `{}` 不存在於 account 區塊，已略過。".format(user))

        return TargetResolution(
            kind="group",
            requested=requested,
            profiles=tuple(profiles),
            skipped=tuple(skipped),
            warnings=tuple(warnings),
        )
