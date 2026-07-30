from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import RouterInstaller, find_router_root
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.router import CommandResult, RouterClient, RouterError
from astrill_lazy.store import (
    ConfigStore,
    PolicyDeploymentManifest,
)
from astrill_lazy.windows_controller import (
    ControllerError,
    WindowsController,
)
from astrill_lazy.windows_ssh_setup import WindowsHostKey

ROUTER_VERSION = (find_router_root() / "VERSION").read_text(encoding="ascii").strip()


def payload_hash(payload: str) -> str:
    digest = hashlib.md5(payload.encode("ascii"), usedforsecurity=False).hexdigest()
    return f"md5:{digest}"


def rule(rule_id: str, selector: str) -> Rule:
    return Rule(
        id=rule_id,
        name=rule_id,
        match_kind=MatchKind.DOMAIN,
        selector=selector,
        target=RouteTarget.DIRECT,
        region="direct",
        priority=100,
    )


class HybridRouter:
    def __init__(self) -> None:
        self.reads = 0
        self.writes: list[tuple[str, object]] = []
        self.document: dict[str, Any] = {
            "schema_version": 1,
            "ok": True,
            "version": ROUTER_VERSION,
            "policy_health": "ready",
            "precedence_ok": True,
            "jump_installed": True,
            "watchdog": True,
            "runtime_epoch": "epoch-1",
            "core": {
                "generation": 0,
                "hash": None,
                "origin_ids": [],
                "rows": 0,
                "bytes": 0,
            },
            "overlays": [],
            "effective": {"hash": None, "rows": 0, "bytes": 0},
        }

    def status(self) -> dict[str, Any]:
        self.reads += 1
        return copy.deepcopy(self.document)

    def effective_status(self) -> dict[str, Any]:
        self.reads += 1
        return copy.deepcopy(self.document)

    def companion_presence(self) -> dict[str, Any]:
        self.reads += 1
        return {
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
        }

    def core_apply(self, payload: str) -> dict[str, Any]:
        self.writes.append(("core_apply", payload))
        core = self.document["core"]
        core["generation"] += 1
        core["hash"] = payload_hash(payload)
        core["origin_ids"] = ["core-rule"]
        return copy.deepcopy(self.document)

    def core_rollback(self) -> dict[str, Any]:
        self.writes.append(("core_rollback", None))
        core = self.document["core"]
        core["generation"] += 1
        core["hash"] = "md5:" + ("a" * 32)
        core["origin_ids"] = ["rolled-back-core"]
        return copy.deepcopy(self.document)

    def overlay_put(
        self,
        owner: str,
        expected_generation: int,
        source: str,
        payload: str,
    ) -> dict[str, Any]:
        current = next(
            (item for item in self.document["overlays"] if item["owner"] == owner),
            None,
        )
        generation = 0 if current is None else current["generation"]
        if generation != expected_generation:
            raise RouterError("overlay generation conflict")
        self.writes.append(
            (
                "overlay_put",
                {
                    "owner": owner,
                    "generation": expected_generation,
                    "source": source,
                    "payload": payload,
                },
            )
        )
        replacement = {
            "owner": owner,
            "generation": generation + 1,
            "hash": payload_hash(payload),
            "source": "192.168.1.166/32" if source == "auto" else source,
            "mac": "aa:bb:cc:dd:ee:ff",
            "origin_ids": ["overlay-rule"],
        }
        self.document["overlays"] = [
            item for item in self.document["overlays"] if item["owner"] != owner
        ]
        self.document["overlays"].append(replacement)
        return copy.deepcopy(self.document)

    def overlay_remove(
        self,
        owner: str,
        expected_generation: int,
    ) -> dict[str, Any]:
        self.writes.append(("overlay_remove", (owner, expected_generation)))
        self.document["overlays"] = [
            item for item in self.document["overlays"] if item["owner"] != owner
        ]
        return copy.deepcopy(self.document)


def make_controller(
    tmp_path: Path,
) -> tuple[WindowsController, ConfigStore, HybridRouter, WindowsHostKey]:
    store = ConfigStore(tmp_path / "config.json")
    store.read_only = False
    store.companion_enabled = True
    store.rules = [
        rule("core-rule", "core.example.com"),
        rule("overlay-rule", "overlay.example.com"),
        rule("rolled-back-core", "rollback.example.com"),
    ]
    router = HybridRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    host_key = WindowsHostKey(
        host=store.router_host,
        port=store.router_port,
        key_type="ssh-ed25519",
        key_base64="AAAATEST",
        fingerprint="SHA256:test",
        trust_state="trusted",
        known_hosts_path=tmp_path / "known_hosts",
    )
    controller.inspect_router_host_key = lambda: host_key  # type: ignore[method-assign]
    controller._ensure_hybrid_helper = lambda: "current"  # type: ignore[method-assign]
    return controller, store, router, host_key


def test_manifest_round_trip_keeps_stable_controller_and_router_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    manifest = PolicyDeploymentManifest(
        router_host="192.168.1.1",
        router_port=22,
        router_host_key_fingerprint="SHA256:test",
        companion_version="1.2.3",
        controller_id=store.controller_id,
        source="auto",
        resolved_source="192.168.1.166/32",
        source_mac="AA-BB-CC-DD-EE-FF",
        overlay_rule_ids=("overlay-rule",),
        overlay_hash="md5:" + ("a" * 32),
        overlay_generation=3,
        restore_overlay_after_reboot=True,
    )

    store.upsert_deployment(manifest)
    loaded = ConfigStore(path)

    assert loaded.controller_id == store.controller_id
    assert loaded.policy_deployments[0].resolved_source == "192.168.1.166/32"
    assert loaded.policy_deployments[0].source_mac == "aa:bb:cc:dd:ee:ff"
    assert loaded.policy_deployments[0].restore_overlay_after_reboot is True


def test_hybrid_apply_records_hash_generation_source_and_mac(tmp_path: Path) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    manifest = controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        status=router.document,
        host_key=host_key,
    )

    assert manifest.router_host_key_fingerprint == "SHA256:test"
    assert manifest.companion_version == ROUTER_VERSION
    assert manifest.source == "auto"

    core_result = controller.apply_persistent_core()
    overlay_result = controller.load_ram_overlay(("overlay-rule",))

    assert core_result["core"]["generation"] == 1
    assert overlay_result["overlays"][0]["generation"] == 1
    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.core_hash and saved.core_hash.startswith("md5:")
    assert saved.overlay_hash and saved.overlay_hash.startswith("md5:")
    assert saved.resolved_source == "192.168.1.166/32"
    assert saved.source_mac == "aa:bb:cc:dd:ee:ff"
    assert [name for name, _value in router.writes] == [
        "core_apply",
        "overlay_put",
    ]


def test_layer_overlap_is_rejected_before_router_io(tmp_path: Path) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )
    router.reads = 0
    router.writes.clear()

    with pytest.raises(ControllerError, match="same policy IDs"):
        controller.load_ram_overlay(("core-rule",))

    assert router.reads == 0
    assert router.writes == []


def test_overlay_preflight_uses_ram_limit_not_legacy_nvram_limit(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.rules = [
        rule(f"r{index:03d}", f"h{index:03d}.example") for index in range(250)
    ]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=HybridRouter(),  # type: ignore[arg-type]
    )
    selected = tuple(item.id for item in store.rules)

    legacy = controller.policy_preflight(selected)
    overlay = controller.policy_layer_preflight(selected, layer="overlay")

    assert legacy.can_apply is False
    assert legacy.limit_bytes == 6_144
    assert overlay.can_apply is True
    assert overlay.limit_bytes == 32_768
    assert overlay.compiled_rows == 250
    assert overlay.compiled_bytes is not None
    assert 6_144 < overlay.compiled_bytes < 32_768


def test_opted_in_reconcile_restores_once_per_runtime_epoch(tmp_path: Path) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )
    router.document["runtime_epoch"] = "epoch-2"
    router.writes.clear()

    first = controller.reconcile_status(
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
        },
        companion_status=copy.deepcopy(router.document),
    )
    first_notice = controller.recovery_notice
    second = controller.reconcile_status(
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
        },
        companion_status=copy.deepcopy(router.document),
    )

    assert first["overlays"][0]["owner"] == store.controller_id
    assert second["overlays"][0]["owner"] == store.controller_id
    assert [name for name, _value in router.writes] == ["overlay_put"]
    assert "restored after the router reboot" in str(first_notice)
    assert ConfigStore(store.path).policy_deployments[0].last_runtime_epoch == (
        "epoch-2"
    )


def test_core_rollback_uses_returned_origin_ids(tmp_path: Path) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )

    controller.rollback_persistent_core()

    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.core_rule_ids == ("rolled-back-core",)
    assert saved.core_hash == "md5:" + ("a" * 32)


def test_router_client_builds_layered_commands_without_shell_interpolation() -> None:
    client = RouterClient("router")
    calls: list[tuple[list[str], bytes | None, int | None]] = []

    def run(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        calls.append((arguments, input_bytes, timeout))
        return CommandResult(
            stdout=(
                '{"version":"1","runtime_epoch":"e","core":{},'
                '"overlays":[],"effective":{}}\n'
            ),
            stderr="",
            returncode=0,
        )

    client._run_alctl = run  # type: ignore[method-assign]

    client.core_apply("# rules\n")
    client.core_rollback()
    client.overlay_put("controller-abc", 0, "auto", "# overlay\n")
    client.overlay_remove("controller-abc", 1)
    client.overlay_list()
    client.effective_status()

    assert calls == [
        (["core-apply", "-"], b"# rules\n", 120),
        (["core-rollback", "--json"], None, 120),
        (
            ["overlay-put", "controller-abc", "0", "auto", "-"],
            b"# overlay\n",
            120,
        ),
        (["overlay-remove", "controller-abc", "1"], None, 120),
        (["overlay-list", "--json"], None, None),
        (["effective-status", "--json"], None, None),
    ]


def test_hybrid_helper_upload_is_atomic_verified_and_ram_only() -> None:
    client = RouterClient("router")
    calls: list[tuple[list[str], bytes | None, int | None]] = []
    payload = b"#!/bin/sh\nprintf helper\n"
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()

    def run(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        calls.append((arguments, input_bytes, timeout))
        return CommandResult(stdout="installed", stderr="", returncode=0)

    client._run_remote = run  # type: ignore[method-assign]

    assert client.ensure_hybrid_helper(payload, digest) == "installed"
    assert len(calls) == 1
    arguments, uploaded, timeout = calls[0]
    assert arguments[:2] == ["/bin/sh", "-c"]
    assert "/tmp/astrill-lazy/alhybrid" in arguments[2]
    assert "md5sum" in arguments[2]
    assert "chmod 700" in arguments[2]
    assert "mv -f" in arguments[2]
    assert "nvram" not in arguments[2]
    assert uploaded == payload
    assert timeout == 60


def test_installer_reads_hybrid_helper_without_adding_it_to_nvram_package(
    tmp_path: Path,
) -> None:
    router_root = tmp_path / "router"
    router_root.mkdir()
    payload = b"#!/bin/sh\nprintf volatile-helper\n"
    (router_root / "alhybrid").write_bytes(payload)
    captured: list[tuple[bytes, str]] = []

    class Client:
        def ensure_hybrid_helper(self, value: bytes, digest: str) -> str:
            captured.append((value, digest))
            return "installed"

    installer = RouterInstaller(Client())  # type: ignore[arg-type]
    installer.router_root = router_root

    result = installer.ensure_hybrid_helper()

    assert result.action == "installed"
    assert result.helper_bytes == len(payload)
    assert (
        result.helper_md5
        == hashlib.md5(
            payload,
            usedforsecurity=False,
        ).hexdigest()
    )
    assert captured == [(payload, result.helper_md5)]


def test_hybrid_status_reports_legacy_companion_without_mutating_it(
    tmp_path: Path,
) -> None:
    class LegacyRouter:
        def status(self) -> dict[str, Any]:
            return {"version": "0.2.5", "policy_health": "ready"}

        def effective_status(self) -> dict[str, Any]:
            raise RouterError("unknown command")

    controller = WindowsController(
        store=ConfigStore(tmp_path / "config.json"),
        catalog=load_catalog(),
        router=LegacyRouter(),  # type: ignore[arg-type]
    )

    with pytest.raises(ControllerError, match="legacy Apply policies"):
        controller.hybrid_policy_status()
