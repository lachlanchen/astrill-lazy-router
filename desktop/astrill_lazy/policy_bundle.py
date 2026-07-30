from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .catalog import Catalog
from .models import MatchKind, RouteTarget, Rule, validate_id
from .store import ConfigStore

POLICY_BUNDLE_SCHEMA_VERSION = 1
MAX_POLICY_BUNDLE_BYTES = 128 * 1024
MAX_POLICY_BUNDLE_RULES = 320
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")


@dataclass(frozen=True)
class PolicyBundleEntry:
    origin_id: str
    service_id: str
    target: RouteTarget
    region: str
    enabled: bool
    priority: int

    def validate(self) -> None:
        validate_id(self.origin_id)
        validate_id(self.service_id)
        validate_id(self.region)
        if self.target is RouteTarget.DIRECT and self.region != "direct":
            raise ValueError(
                f"direct policy {self.origin_id!r} must use the direct region"
            )
        if not isinstance(self.enabled, bool):
            raise TypeError("policy bundle enabled values must be booleans")
        if (
            not isinstance(self.priority, int)
            or isinstance(self.priority, bool)
            or not 0 <= self.priority <= 9999
        ):
            raise ValueError("policy bundle priorities must be between 0 and 9999")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "origin_id": self.origin_id,
            "service_id": self.service_id,
            "route": self.target.value,
            "region": self.region,
            "enabled": self.enabled,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PolicyBundleEntry:
        allowed = {
            "origin_id",
            "service_id",
            "route",
            "region",
            "enabled",
            "priority",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                "policy bundle rule contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        entry = cls(
            origin_id=str(value.get("origin_id", "")),
            service_id=str(value.get("service_id", "")),
            target=RouteTarget(value.get("route")),
            region=str(value.get("region", "")),
            enabled=value.get("enabled", True),
            priority=value.get("priority", 500),
        )
        entry.validate()
        return entry


@dataclass(frozen=True)
class PolicyBundle:
    bundle_id: str
    version: str
    catalog: str
    entries: tuple[PolicyBundleEntry, ...]
    description: str = ""

    def validate(self, catalog: Catalog | None = None) -> None:
        validate_id(self.bundle_id)
        if not BUNDLE_VERSION_RE.fullmatch(self.version):
            raise ValueError("policy bundle version is invalid")
        if self.catalog != "core-catalog":
            raise ValueError("policy bundle must target core-catalog")
        if not self.entries:
            raise ValueError("policy bundle must contain at least one rule")
        if len(self.entries) > MAX_POLICY_BUNDLE_RULES:
            raise ValueError(
                f"policy bundle contains more than {MAX_POLICY_BUNDLE_RULES} rules"
            )
        if len(self.description) > 500:
            raise ValueError("policy bundle description is too long")
        origins: set[str] = set()
        services: set[str] = set()
        for entry in self.entries:
            entry.validate()
            if entry.origin_id in origins:
                raise ValueError(f"policy bundle repeats origin {entry.origin_id!r}")
            if entry.service_id in services:
                raise ValueError(f"policy bundle repeats service {entry.service_id!r}")
            origins.add(entry.origin_id)
            services.add(entry.service_id)
        if catalog is None:
            return
        unknown_services = services - catalog.services_by_id.keys()
        if unknown_services:
            raise ValueError(
                "policy bundle references unknown services: "
                + ", ".join(sorted(unknown_services))
            )
        known_regions = {region.id for region in catalog.regions}
        unknown_regions = {
            entry.region for entry in self.entries if entry.region not in known_regions
        }
        if unknown_regions:
            raise ValueError(
                "policy bundle references unknown regions: "
                + ", ".join(sorted(unknown_regions))
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": POLICY_BUNDLE_SCHEMA_VERSION,
            "bundle_id": self.bundle_id,
            "version": self.version,
            "catalog": self.catalog,
            "description": self.description,
            "rules": [entry.to_dict() for entry in self.entries],
        }

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        catalog: Catalog | None = None,
    ) -> PolicyBundle:
        if not payload:
            raise ValueError("policy bundle is empty")
        if len(payload) > MAX_POLICY_BUNDLE_BYTES:
            raise ValueError(f"policy bundle exceeds {MAX_POLICY_BUNDLE_BYTES} bytes")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("policy bundle is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise TypeError("policy bundle root must be an object")
        allowed = {
            "schema_version",
            "bundle_id",
            "version",
            "catalog",
            "description",
            "rules",
        }
        unknown = set(document) - allowed
        if unknown:
            raise ValueError(
                "policy bundle contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        if document.get("schema_version") != POLICY_BUNDLE_SCHEMA_VERSION:
            raise ValueError("unsupported policy bundle schema")
        raw_rules = document.get("rules")
        if not isinstance(raw_rules, list):
            raise TypeError("policy bundle rules must be a list")
        if any(not isinstance(item, dict) for item in raw_rules):
            raise TypeError("each policy bundle rule must be an object")
        description = document.get("description", "")
        if not isinstance(description, str):
            raise TypeError("policy bundle description must be a string")
        bundle = cls(
            bundle_id=str(document.get("bundle_id", "")),
            version=str(document.get("version", "")),
            catalog=str(document.get("catalog", "")),
            description=description.strip(),
            entries=tuple(PolicyBundleEntry.from_dict(item) for item in raw_rules),
        )
        bundle.validate(catalog)
        return bundle


@dataclass(frozen=True)
class PolicyBundleDownload:
    bundle: PolicyBundle
    payload: bytes
    sha256: str
    source: str


@dataclass(frozen=True)
class PolicyBundleApplyResult:
    added: int
    updated: int
    removed: int
    unchanged: int
    bundle_id: str
    bundle_version: str
    bundle_sha256: str


def download_policy_bundle(
    source: str,
    *,
    catalog: Catalog,
    expected_sha256: str | None = None,
    timeout: int = 20,
) -> PolicyBundleDownload:
    normalized_hash = _normalize_expected_sha256(expected_sha256)
    parsed = urlsplit(source)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("remote policy bundle URL must use HTTPS")
    request = urllib.request.Request(
        source,
        headers={
            "Accept": "application/json",
            "User-Agent": "Astrill-Lazy-Router-policy-bundle/1",
        },
    )
    with urllib.request.urlopen(request, timeout=max(1, min(timeout, 60))) as response:
        final_url = response.geturl()
        final_parts = urlsplit(final_url)
        if final_parts.scheme != "https" or not final_parts.hostname:
            raise ValueError("policy bundle redirect left HTTPS")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared_length = int(length)
            except ValueError as exc:
                raise ValueError("policy bundle Content-Length is invalid") from exc
            if declared_length > MAX_POLICY_BUNDLE_BYTES:
                raise ValueError(
                    f"policy bundle exceeds {MAX_POLICY_BUNDLE_BYTES} bytes"
                )
        payload = response.read(MAX_POLICY_BUNDLE_BYTES + 1)
    if len(payload) > MAX_POLICY_BUNDLE_BYTES:
        raise ValueError(f"policy bundle exceeds {MAX_POLICY_BUNDLE_BYTES} bytes")
    digest = hashlib.sha256(payload).hexdigest()
    if normalized_hash is not None and digest != normalized_hash:
        raise ValueError("policy bundle SHA-256 mismatch; no local policy was changed")
    return PolicyBundleDownload(
        bundle=PolicyBundle.from_bytes(payload, catalog=catalog),
        payload=payload,
        sha256=digest,
        source=final_url,
    )


def load_policy_bundle(
    path: Path,
    *,
    catalog: Catalog,
    expected_sha256: str | None = None,
) -> PolicyBundleDownload:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    normalized_hash = _normalize_expected_sha256(expected_sha256)
    if normalized_hash is not None and digest != normalized_hash:
        raise ValueError("policy bundle SHA-256 mismatch; no local policy was changed")
    return PolicyBundleDownload(
        bundle=PolicyBundle.from_bytes(payload, catalog=catalog),
        payload=payload,
        sha256=digest,
        source=str(path.resolve()),
    )


def export_service_policy_bundle(
    rules: list[Rule] | tuple[Rule, ...],
    *,
    bundle_id: str,
    version: str,
    description: str = "",
) -> PolicyBundle:
    entries = tuple(
        PolicyBundleEntry(
            origin_id=rule.id,
            service_id=rule.selector,
            target=rule.target,
            region=rule.region,
            enabled=rule.enabled,
            priority=rule.priority,
        )
        for rule in rules
        if rule.match_kind is MatchKind.SERVICE
    )
    bundle = PolicyBundle(
        bundle_id=bundle_id,
        version=version,
        catalog="core-catalog",
        description=description,
        entries=entries,
    )
    bundle.validate()
    return bundle


def apply_policy_bundle(
    store: ConfigStore,
    catalog: Catalog,
    download: PolicyBundleDownload,
    *,
    replace_services: bool,
) -> PolicyBundleApplyResult:
    """Apply a validated catalog-only bundle with one atomic config save."""

    bundle = download.bundle
    bundle.validate(catalog)
    existing_by_service = {
        rule.selector: rule
        for rule in store.rules
        if rule.match_kind is MatchKind.SERVICE
    }
    non_service_rules = [
        rule for rule in store.rules if rule.match_kind is not MatchKind.SERVICE
    ]
    next_service_rules: list[Rule] = []
    added = 0
    updated = 0
    unchanged = 0
    bundle_service_ids = {entry.service_id for entry in bundle.entries}
    for entry in bundle.entries:
        service = catalog.services_by_id[entry.service_id]
        current = existing_by_service.get(entry.service_id)
        rule = (
            Rule(
                id=entry.origin_id,
                name=service.name,
                match_kind=MatchKind.SERVICE,
                selector=entry.service_id,
                target=entry.target,
                region=entry.region,
                enabled=entry.enabled,
                priority=entry.priority,
            )
            if current is None
            else Rule.from_dict(current.to_dict())
        )
        if current is None:
            added += 1
        before = rule.to_dict()
        rule.id = entry.origin_id
        rule.name = service.name
        rule.target = entry.target
        rule.region = entry.region
        rule.enabled = entry.enabled
        rule.priority = entry.priority
        rule.metadata["policy_bundle"] = {
            "id": bundle.bundle_id,
            "version": bundle.version,
            "sha256": download.sha256,
        }
        rule.validate()
        if current is not None:
            if rule.to_dict() == before:
                unchanged += 1
            else:
                updated += 1
        next_service_rules.append(rule)

    retained_service_rules: list[Rule] = []
    if not replace_services:
        retained_service_rules = [
            rule
            for rule in store.rules
            if rule.match_kind is MatchKind.SERVICE
            and rule.selector not in bundle_service_ids
        ]
    removed = (
        sum(
            rule.match_kind is MatchKind.SERVICE
            and rule.selector not in bundle_service_ids
            for rule in store.rules
        )
        if replace_services
        else 0
    )
    next_rules = [
        *non_service_rules,
        *next_service_rules,
        *retained_service_rules,
    ]
    seen_rule_ids: set[str] = set()
    duplicate_rule_ids: set[str] = set()
    for rule in next_rules:
        if rule.id in seen_rule_ids:
            duplicate_rule_ids.add(rule.id)
        seen_rule_ids.add(rule.id)
    if duplicate_rule_ids:
        raise ValueError(
            "policy bundle origin IDs collide with local rules: "
            + ", ".join(sorted(duplicate_rule_ids))
        )
    previous_rules = store.rules
    store.rules = next_rules
    try:
        store.save()
    except (OSError, TypeError, ValueError):
        store.rules = previous_rules
        raise
    return PolicyBundleApplyResult(
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.version,
        bundle_sha256=download.sha256,
    )


def _normalize_expected_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError("expected policy bundle SHA-256 must contain 64 hex digits")
    return normalized
