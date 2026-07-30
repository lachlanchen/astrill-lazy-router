from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.policy_bundle import (
    PolicyBundle,
    PolicyBundleDownload,
    PolicyBundleEntry,
    apply_policy_bundle,
    export_service_policy_bundle,
)
from astrill_lazy.store import ConfigStore


def entry(
    service_id: str = "uu-remote",
    *,
    origin_id: str = "uu-remote-direct",
    route: RouteTarget = RouteTarget.DIRECT,
    region: str = "direct",
    priority: int = 100,
) -> PolicyBundleEntry:
    return PolicyBundleEntry(
        origin_id=origin_id,
        service_id=service_id,
        target=route,
        region=region,
        enabled=True,
        priority=priority,
    )


def bundle(*entries: PolicyBundleEntry) -> PolicyBundle:
    return PolicyBundle(
        bundle_id="daily-balanced",
        version="1.0.0",
        catalog="core-catalog",
        description="Catalog-only routing decisions.",
        entries=entries or (entry(),),
    )


def test_bundle_round_trip_is_deterministic_and_catalog_bound() -> None:
    catalog = load_catalog()
    original = bundle(
        entry(),
        entry(
            "chatgpt",
            origin_id="chatgpt-vpn",
            route=RouteTarget.VPN,
            region="united-states",
            priority=200,
        ),
    )

    payload = original.to_bytes()
    loaded = PolicyBundle.from_bytes(payload, catalog=catalog)

    assert loaded == original
    assert loaded.to_bytes() == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()


def test_bundle_rejects_unknown_fields_services_and_duplicate_origins() -> None:
    catalog = load_catalog()
    document = bundle().to_dict()
    document["command"] = "nvram commit"
    with pytest.raises(ValueError, match="unsupported fields"):
        PolicyBundle.from_bytes(json.dumps(document).encode(), catalog=catalog)

    unknown = bundle(entry("not-in-catalog", origin_id="unknown"))
    with pytest.raises(ValueError, match="unknown services"):
        unknown.validate(catalog)

    duplicate = bundle(entry(), entry("wechat", origin_id="uu-remote-direct"))
    with pytest.raises(ValueError, match="repeats origin"):
        duplicate.validate(catalog)


def test_bundle_apply_preserves_non_service_rules_and_is_idempotent(
    tmp_path: Path,
) -> None:
    catalog = load_catalog()
    store = ConfigStore(tmp_path / "config.json")
    device = Rule(
        id="computer-direct",
        name="Computer",
        match_kind=MatchKind.DEVICE,
        selector="192.168.1.100/32",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=50,
    )
    stale = Rule(
        id="stale-service",
        name="Stale",
        match_kind=MatchKind.SERVICE,
        selector="wechat",
        target=RouteTarget.VPN,
        region="singapore",
        priority=800,
    )
    store.rules = [device, stale]
    selected = bundle(entry())
    payload = selected.to_bytes()
    download = PolicyBundleDownload(
        bundle=selected,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        source="https://example.test/stable.json",
    )

    first = apply_policy_bundle(
        store,
        catalog,
        download,
        replace_services=True,
    )
    second = apply_policy_bundle(
        store,
        catalog,
        download,
        replace_services=True,
    )
    loaded = ConfigStore(store.path)

    assert first.added == 1
    assert first.removed == 1
    assert second.unchanged == 1
    assert [rule.id for rule in loaded.rules] == [
        "computer-direct",
        "uu-remote-direct",
    ]
    assert loaded.rules[1].metadata["policy_bundle"]["sha256"] == download.sha256


def test_export_omits_device_and_process_details() -> None:
    service = Rule(
        id="uu-remote-direct",
        name="UU Remote",
        match_kind=MatchKind.SERVICE,
        selector="uu-remote",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=100,
    )
    device = Rule(
        id="private-device",
        name="Private",
        match_kind=MatchKind.DEVICE,
        selector="192.168.1.99/32",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=200,
    )

    exported = export_service_policy_bundle(
        [service, device],
        bundle_id="daily-balanced",
        version="1.0.0",
    )

    assert [item.origin_id for item in exported.entries] == ["uu-remote-direct"]
    assert b"192.168.1.99" not in exported.to_bytes()


def test_apply_uses_bundle_origin_and_omits_source_location(tmp_path: Path) -> None:
    catalog = load_catalog()
    store = ConfigStore(tmp_path / "config.json")
    store.rules = [
        Rule(
            id="old-uu-policy",
            name="Old UU",
            match_kind=MatchKind.SERVICE,
            selector="uu-remote",
            target=RouteTarget.VPN,
            region="united-states",
            priority=800,
        )
    ]
    selected = bundle(entry())
    payload = selected.to_bytes()
    download = PolicyBundleDownload(
        bundle=selected,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        source="https://example.test/policy.json?token=private",
    )

    result = apply_policy_bundle(
        store,
        catalog,
        download,
        replace_services=True,
    )
    loaded = ConfigStore(store.path)

    assert result.updated == 1
    assert loaded.rules[0].id == "uu-remote-direct"
    assert loaded.rules[0].metadata["policy_bundle"] == {
        "id": "daily-balanced",
        "version": "1.0.0",
        "sha256": download.sha256,
    }
    assert "example.test" not in store.path.read_text(encoding="ascii")
    assert "token=private" not in store.path.read_text(encoding="ascii")


def test_apply_rejects_origin_collision_without_changing_store(tmp_path: Path) -> None:
    catalog = load_catalog()
    store = ConfigStore(tmp_path / "config.json")
    device = Rule(
        id="uu-remote-direct",
        name="Private device",
        match_kind=MatchKind.DEVICE,
        selector="192.168.1.99/32",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=50,
    )
    store.rules = [device]
    store.save()
    before = store.path.read_bytes()
    selected = bundle(entry())
    payload = selected.to_bytes()
    download = PolicyBundleDownload(
        bundle=selected,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        source="local.json",
    )

    with pytest.raises(ValueError, match="collide"):
        apply_policy_bundle(
            store,
            catalog,
            download,
            replace_services=True,
        )

    assert store.rules == [device]
    assert store.path.read_bytes() == before
