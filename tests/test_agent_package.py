from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from astrill_lazy.agent_package import (
    build_portable_agent_package,
    plan_balanced_policy,
)
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import RouterInstaller
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.policy_controller import PolicyController
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import ControllerError, HybridPolicyComparison
from astrill_lazy.windows_ssh_setup import WindowsHostKey


def service_rule(
    rule_id: str,
    service_id: str,
    target: RouteTarget,
    region: str,
    priority: int,
) -> Rule:
    return Rule(
        id=rule_id,
        name=service_id,
        match_kind=MatchKind.SERVICE,
        selector=service_id,
        target=target,
        region=region,
        priority=priority,
    )


def test_balanced_plan_keeps_minimum_and_devices_in_core(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.rules = [
        Rule(
            id="computer-direct",
            name="Computer",
            match_kind=MatchKind.DEVICE,
            selector="192.168.1.100/32",
            target=RouteTarget.DIRECT,
            region="direct",
            priority=50,
        ),
        service_rule(
            "uu-remote-direct",
            "uu-remote",
            RouteTarget.DIRECT,
            "direct",
            100,
        ),
        service_rule(
            "nutstore-direct",
            "nutstore",
            RouteTarget.DIRECT,
            "direct",
            110,
        ),
        service_rule(
            "chatgpt-vpn",
            "chatgpt",
            RouteTarget.VPN,
            "united-states",
            200,
        ),
    ]
    plan = plan_balanced_policy(store, load_catalog())

    assert plan.core_rule_ids == (
        "computer-direct",
        "uu-remote-direct",
        "nutstore-direct",
    )
    assert plan.overlay_rule_ids == ("chatgpt-vpn",)
    assert all(
        rule.kind != MatchKind.DEVICE.value for rule in plan.overlay_compilation.rules
    )


def test_portable_package_contains_only_public_policy_and_pinned_assets(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.rules = [
        service_rule(
            "uu-remote-direct",
            "uu-remote",
            RouteTarget.DIRECT,
            "direct",
            100,
        ),
        service_rule(
            "chatgpt-vpn",
            "chatgpt",
            RouteTarget.VPN,
            "united-states",
            200,
        ),
    ]
    store.rules[1].metadata["policy_bundle"] = {
        "id": "daily-balanced",
        "version": "1.0.0",
        "sha256": "a" * 64,
        "source": "/home/private/policy.json?token=secret",
    }
    host_key = WindowsHostKey(
        host="192.168.1.1",
        port=22,
        key_type="ssh-ed25519",
        key_base64="AAAATESTKEY",
        fingerprint="SHA256:dGVzdA==",
        trust_state="trusted",
        known_hosts_path=tmp_path / "ignored",
    )
    installer = RouterInstaller(object())  # type: ignore[arg-type]
    output = tmp_path / "package"

    result = build_portable_agent_package(
        output,
        store=store,
        catalog=load_catalog(),
        host_key=host_key,
        router_user="root",
        identity_file="~/.ssh/astrill_lazy_router_ed25519",
        controller_id="controller-test-package",
        router_installer=installer,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="ascii"))
    overlay = (output / "overlay.tsv").read_bytes()

    assert result.controller_id == "controller-test-package"
    assert result.core_rule_ids == ("uu-remote-direct",)
    assert result.overlay_rule_ids == ("chatgpt-vpn",)
    assert manifest["companion_version"] == installer.expected_version
    assert manifest["router_host_key_fingerprint"] == "SHA256:dGVzdA=="
    assert manifest["known_hosts_file"] == "known_hosts"
    assert manifest["policy_bundle"] == {
        "id": "daily-balanced",
        "version": "1.0.0",
        "sha256": "a" * 64,
    }
    assert manifest["overlay_sha256"] == hashlib.sha256(overlay).hexdigest()
    assert b"chatgpt-vpn" in overlay
    assert b"uu-remote-direct" not in overlay
    assert (output / "astrill-lazy-agent.py").stat().st_mode & 0o777 == 0o700
    assert (output / "install-agent.sh").stat().st_mode & 0o777 == 0o700
    assert (output / "uninstall-agent.sh").stat().st_mode & 0o777 == 0o700
    assert (output / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert "AAAATESTKEY" in (output / "known_hosts").read_text(encoding="ascii")
    assert b"/home/private" not in (output / "manifest.json").read_bytes()
    assert b"token=secret" not in (output / "manifest.json").read_bytes()
    checksums = (output / "SHA256SUMS").read_text(encoding="ascii")
    assert "install-agent.sh" in checksums
    assert "uninstall-agent.sh" in checksums


def test_platform_controller_deploys_only_changed_layers(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.companion_enabled = True
    store.read_only = False
    store.rules = [
        service_rule(
            "uu-remote-direct",
            "uu-remote",
            RouteTarget.DIRECT,
            "direct",
            100,
        ),
        service_rule(
            "chatgpt-vpn",
            "chatgpt",
            RouteTarget.VPN,
            "united-states",
            200,
        ),
    ]

    class Router:
        def effective_status(self) -> dict[str, object]:
            return {"stage": "initial"}

    controller = PolicyController(
        store=store,
        catalog=load_catalog(),
        router=Router(),  # type: ignore[arg-type]
    )
    host_key = WindowsHostKey(
        host="192.168.1.1",
        port=22,
        key_type="ssh-ed25519",
        key_base64="AAAATESTKEY",
        fingerprint="SHA256:dGVzdA==",
        trust_state="trusted",
        known_hosts_path=tmp_path / "known_hosts",
    )
    events: list[str] = []
    comparisons = iter(
        (
            HybridPolicyComparison(
                manifest=None,
                status={},
                runtime_epoch="epoch",
                core_matches=True,
                overlay_present=False,
                overlay_matches=False,
                restore_needed=True,
            ),
            HybridPolicyComparison(
                manifest=None,
                status={},
                runtime_epoch="epoch",
                core_matches=True,
                overlay_present=False,
                overlay_matches=False,
                restore_needed=True,
            ),
        )
    )
    controller.inspect_router_host_key = lambda: host_key  # type: ignore[method-assign]
    controller.bind_trusted_router = (  # type: ignore[method-assign]
        lambda _host_key: controller.router
    )
    controller.configure_policy_deployment = (  # type: ignore[method-assign]
        lambda **_options: events.append("configure")
    )
    controller.hybrid_policy_status = (  # type: ignore[method-assign]
        lambda _status=None: next(comparisons)
    )
    controller.apply_persistent_core = (  # type: ignore[method-assign]
        lambda: (_ for _ in ()).throw(AssertionError("core was already current"))
    )

    def load_overlay(
        _rule_ids: tuple[str, ...],
        _source: str,
    ) -> dict[str, object]:
        events.append("overlay")
        return {"stage": "overlay"}

    controller.load_ram_overlay = load_overlay  # type: ignore[method-assign]

    class Manifest:
        restore_overlay_after_reboot = True

    def enable_restore(
        _enabled: bool,
        _source: str,
        *,
        status: dict[str, object],
    ) -> Manifest:
        assert status == {"stage": "overlay"}
        events.append("restore")
        return Manifest()

    controller.set_overlay_restore_enabled = enable_restore  # type: ignore[method-assign]

    result = controller.deploy_balanced_policy()

    assert result.core_action == "current"
    assert result.overlay_action == "loaded"
    assert result.restore_enabled is True
    assert events == ["configure", "overlay", "restore"]


def test_platform_controller_binds_only_the_trusted_configured_router(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.router_host = "192.168.1.1"
    store.router_user = "root"
    store.router_port = 22
    store.router_identity = str(tmp_path / "identity")
    controller = PolicyController(
        store=store,
        catalog=load_catalog(),
        router=object(),  # type: ignore[arg-type]
    )
    trusted = WindowsHostKey(
        host=store.router_host,
        port=store.router_port,
        key_type="ssh-ed25519",
        key_base64="AAAATESTKEY",
        fingerprint="SHA256:dGVzdA==",
        trust_state="trusted",
        known_hosts_path=tmp_path / "known_hosts",
    )

    router = controller.bind_trusted_router(trusted)

    assert router.host_key_policy == "yes"
    assert router.known_hosts_file == str(tmp_path / "known_hosts")
    assert controller.router is router

    with pytest.raises(ControllerError, match="trusted key"):
        controller.bind_trusted_router(
            WindowsHostKey(
                host=store.router_host,
                port=store.router_port,
                key_type=trusted.key_type,
                key_base64=trusted.key_base64,
                fingerprint=trusted.fingerprint,
                trust_state="unknown",
                known_hosts_path=trusted.known_hosts_path,
            )
        )
