from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import (
    PAGE_COMMANDS,
    STARTUP_LINE,
    RouterInstaller,
    find_router_root,
)
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.router import (
    HYBRID_POLICY_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
)
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
        self.auto_source = "192.168.1.166/32"
        self.auto_mac = "aa:bb:cc:dd:ee:ff"
        self.document: dict[str, Any] = {
            "schema_version": 1,
            "ok": True,
            "version": ROUTER_VERSION,
            "package_md5": RouterInstaller(self).expected_package_md5,
            "policy_health": "ready",
            "precedence_ok": True,
            "jump_installed": True,
            "watchdog": True,
            "runtime_epoch": "epoch-1",
            "core": {
                "generation": 0,
                "hash": payload_hash("# astrill-lazy-rules-v1\n"),
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
            "package_md5": RouterInstaller(self).expected_package_md5,
            "bootstrap_md5": RouterInstaller(self).expected_bootstrap_md5,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": " ".join(PAGE_COMMANDS),
            "package_integrity": True,
            "bootstrap_integrity": True,
        }

    def core_apply(
        self,
        expected_generation: int,
        payload: str,
    ) -> dict[str, Any]:
        if self.document["core"]["generation"] != expected_generation:
            raise RouterError("core generation conflict")
        self.writes.append(("core_apply", payload))
        core = self.document["core"]
        core["generation"] += 1
        core["hash"] = payload_hash(payload)
        core["origin_ids"] = ["core-rule"]
        return copy.deepcopy(self.document)

    def core_rollback(self, expected_generation: int) -> dict[str, Any]:
        if self.document["core"]["generation"] != expected_generation:
            raise RouterError("core generation conflict")
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
        *,
        expected_source: str | None = None,
        expected_mac: str | None = None,
    ) -> dict[str, Any]:
        current = next(
            (item for item in self.document["overlays"] if item["owner"] == owner),
            None,
        )
        generation = 0 if current is None else current["generation"]
        if generation != expected_generation:
            raise RouterError("overlay generation conflict")
        actual_source = (
            self.auto_source
            if source == "auto"
            else source
            if "/" in source
            else f"{source}/32"
        )
        actual_mac = self.auto_mac
        if expected_source is not None and actual_source != expected_source:
            raise RouterError("overlay source binding changed")
        if expected_mac is not None and actual_mac != expected_mac:
            raise RouterError("overlay MAC binding changed")
        self.writes.append(
            (
                "overlay_put",
                {
                    "owner": owner,
                    "generation": expected_generation,
                    "source": source,
                    "payload": payload,
                    "expected_source": expected_source,
                    "expected_mac": expected_mac,
                },
            )
        )
        replacement = {
            "owner": owner,
            "generation": generation + 1,
            "hash": payload_hash(payload),
            "source": actual_source,
            "mac": actual_mac,
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
        companion_package_md5="A" * 32,
        source="auto",
        resolved_source="192.168.1.166/32",
        source_mac="AA-BB-CC-DD-EE-FF",
        core_observed_hash="md5:" + ("c" * 32),
        core_runtime_epoch="core-epoch",
        overlay_rule_ids=("overlay-rule",),
        overlay_hash="md5:" + ("a" * 32),
        overlay_generation=3,
        restore_overlay_after_reboot=True,
        last_restore_attempt_epoch="epoch-attempted",
        last_restore_error="ARP lookup failed",
    )

    store.upsert_deployment(manifest)
    loaded = ConfigStore(path)

    assert loaded.controller_id == store.controller_id
    assert loaded.policy_deployments[0].companion_package_md5 == "a" * 32
    assert loaded.policy_deployments[0].resolved_source == "192.168.1.166/32"
    assert loaded.policy_deployments[0].source_mac == "aa:bb:cc:dd:ee:ff"
    assert loaded.policy_deployments[0].core_observed_hash == "md5:" + ("c" * 32)
    assert loaded.policy_deployments[0].core_runtime_epoch == "core-epoch"
    assert loaded.policy_deployments[0].restore_overlay_after_reboot is True
    assert loaded.policy_deployments[0].last_restore_attempt_epoch == "epoch-attempted"
    assert loaded.policy_deployments[0].last_restore_error == "ARP lookup failed"


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
    assert (
        manifest.companion_package_md5 == RouterInstaller(router).expected_package_md5
    )
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
            "package_md5": RouterInstaller(router).expected_package_md5,
            "bootstrap_md5": RouterInstaller(router).expected_bootstrap_md5,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": " ".join(PAGE_COMMANDS),
            "package_integrity": True,
            "bootstrap_integrity": True,
        },
        companion_status=copy.deepcopy(router.document),
    )
    first_notice = controller.recovery_notice
    second = controller.reconcile_status(
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
            "package_md5": RouterInstaller(router).expected_package_md5,
            "bootstrap_md5": RouterInstaller(router).expected_bootstrap_md5,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": " ".join(PAGE_COMMANDS),
            "package_integrity": True,
            "bootstrap_integrity": True,
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


@pytest.mark.parametrize("operation", ["apply", "rollback"])
@pytest.mark.parametrize("drift", ["hash", "generation"])
def test_core_mutations_refuse_manifest_hash_or_generation_drift(
    tmp_path: Path,
    operation: str,
    drift: str,
) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )
    if drift == "hash":
        router.document["core"]["hash"] = "md5:" + ("f" * 32)
    else:
        router.document["core"]["generation"] += 1
    router.writes.clear()

    with pytest.raises(ControllerError, match="persistent core changed"):
        if operation == "apply":
            controller.apply_persistent_core(("core-rule",))
        else:
            controller.rollback_persistent_core()

    assert router.writes == []


def test_core_generation_is_safely_adopted_after_runtime_epoch_change(
    tmp_path: Path,
) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )
    controller.apply_persistent_core(("core-rule",))
    trusted_hash = router.document["core"]["hash"]
    assert ConfigStore(store.path).policy_deployments[0].core_generation == 1

    router.document["runtime_epoch"] = "epoch-after-reboot"
    router.document["core"]["generation"] = 0
    router.document["core"]["hash"] = trusted_hash
    router.writes.clear()

    result = controller.apply_persistent_core(("core-rule",))

    assert result["core"]["generation"] == 1
    assert [name for name, _value in router.writes] == ["core_apply"]
    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.core_generation == 1
    assert saved.core_runtime_epoch == "epoch-after-reboot"


def test_core_epoch_change_does_not_adopt_a_different_document(
    tmp_path: Path,
) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )
    router.document["runtime_epoch"] = "epoch-after-reboot"
    router.document["core"]["generation"] = 0
    router.document["core"]["hash"] = "md5:" + ("f" * 32)
    router.writes.clear()

    with pytest.raises(ControllerError, match="trusted document hash"):
        controller.apply_persistent_core(("core-rule",))

    assert router.writes == []


def test_core_rebind_preserves_the_trusted_overlay_cas_state(
    tmp_path: Path,
) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    before = ConfigStore(store.path).policy_deployments[0]

    rebound = controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )

    assert rebound.overlay_hash == before.overlay_hash
    assert rebound.overlay_generation == before.overlay_generation
    assert rebound.last_runtime_epoch == before.last_runtime_epoch
    assert rebound.resolved_source == before.resolved_source
    assert rebound.source_mac == before.source_mac


def test_overlay_match_requires_saved_source_and_mac(tmp_path: Path) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))

    assert controller.hybrid_policy_status().overlay_matches is True

    router.document["overlays"][0]["mac"] = "00:11:22:33:44:55"
    comparison = controller.hybrid_policy_status()

    assert comparison.overlay_matches is False
    assert comparison.restore_needed is True


def test_same_epoch_overlay_generation_drift_is_not_silently_replaced(
    tmp_path: Path,
) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    router.document["overlays"][0]["generation"] += 1
    router.writes.clear()

    with pytest.raises(ControllerError, match="current router runtime"):
        controller.load_ram_overlay(("overlay-rule",))

    assert router.writes == []


def test_new_epoch_can_adopt_only_an_exact_existing_overlay(
    tmp_path: Path,
) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    router.document["runtime_epoch"] = "epoch-after-reboot"
    router.document["overlays"][0]["generation"] = 7
    router.writes.clear()

    result = controller.restore_ram_overlay_now()

    assert result["overlays"][0]["generation"] == 8
    assert router.writes[0][0] == "overlay_put"
    assert router.writes[0][1]["generation"] == 7


def test_auto_restore_never_overwrites_a_differing_new_epoch_owner(
    tmp_path: Path,
) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    router.document["runtime_epoch"] = "epoch-with-different-owner"
    router.document["overlays"][0]["generation"] = 1
    router.document["overlays"][0]["hash"] = "md5:" + ("f" * 32)
    router.writes.clear()

    result = controller.reconcile_status(
        presence=router.companion_presence(),
        companion_status=copy.deepcopy(router.document),
    )

    assert result["overlays"][0]["hash"] == "md5:" + ("f" * 32)
    assert router.writes == []
    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.last_restore_attempt_epoch == "epoch-with-different-owner"
    assert "refused to overwrite" in str(saved.last_restore_error)


def test_auto_restore_rejects_reassigned_source_before_router_mutation(
    tmp_path: Path,
) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    router.document["runtime_epoch"] = "epoch-after-dhcp-reassignment"
    router.document["overlays"] = []
    router.auto_mac = "00:11:22:33:44:55"
    router.writes.clear()

    result = controller.reconcile_status(
        presence=router.companion_presence(),
        companion_status=copy.deepcopy(router.document),
    )

    assert result["overlays"] == []
    assert router.writes == []
    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.last_restore_attempt_epoch == "epoch-after-dhcp-reassignment"
    assert "MAC binding changed" in str(saved.last_restore_error)


def test_restore_never_downgrades_a_verified_mac(tmp_path: Path) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        status=router.document,
        host_key=host_key,
    )
    controller.load_ram_overlay(("overlay-rule",))
    original_put = router.overlay_put

    def put_without_mac(
        owner: str,
        expected_generation: int,
        source: str,
        payload: str,
        *,
        expected_source: str | None = None,
        expected_mac: str | None = None,
    ) -> dict[str, Any]:
        result = original_put(
            owner,
            expected_generation,
            source,
            payload,
            expected_source=expected_source,
            expected_mac=expected_mac,
        )
        router.document["overlays"][0].pop("mac", None)
        result["overlays"][0].pop("mac", None)
        return result

    router.overlay_put = put_without_mac  # type: ignore[method-assign]

    with pytest.raises(ControllerError, match="validated LAN MAC"):
        controller.restore_ram_overlay_now()

    saved = ConfigStore(store.path).policy_deployments[0]
    assert saved.source_mac == "aa:bb:cc:dd:ee:ff"


def test_failed_auto_restore_attempt_is_persistent_across_restart(
    tmp_path: Path,
) -> None:
    controller, store, router, host_key = make_controller(tmp_path)
    controller.configure_policy_deployment(
        overlay_rule_ids=("overlay-rule",),
        source="auto",
        restore_overlay_after_reboot=True,
        status=router.document,
        host_key=host_key,
    )
    router.document["runtime_epoch"] = "epoch-failed"
    attempts: list[str] = []

    def fail_put(
        _owner: str,
        _expected_generation: int,
        _source: str,
        _payload: str,
        *,
        expected_source: str | None = None,
        expected_mac: str | None = None,
    ) -> dict[str, Any]:
        assert expected_source is None or isinstance(expected_source, str)
        assert expected_mac is None or isinstance(expected_mac, str)
        attempts.append("put")
        raise RouterError("simulated admission failure")

    router.overlay_put = fail_put  # type: ignore[method-assign]
    presence = {
        "installed": True,
        "version": ROUTER_VERSION,
        "runtime": True,
        "package_md5": RouterInstaller(router).expected_package_md5,
        "bootstrap_md5": RouterInstaller(router).expected_bootstrap_md5,
        "rc_startup": STARTUP_LINE,
        "mypage_scripts": " ".join(PAGE_COMMANDS),
        "package_integrity": True,
        "bootstrap_integrity": True,
    }

    controller.reconcile_status(
        presence=presence,
        companion_status=copy.deepcopy(router.document),
    )
    persisted = ConfigStore(store.path).policy_deployments[0]
    assert persisted.last_restore_attempt_epoch == "epoch-failed"
    assert persisted.last_restore_error == "simulated admission failure"

    restarted_store = ConfigStore(store.path)
    restarted = WindowsController(
        store=restarted_store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    restarted.inspect_router_host_key = lambda: host_key  # type: ignore[method-assign]
    restarted._ensure_hybrid_helper = lambda: "current"  # type: ignore[method-assign]
    restarted.reconcile_status(
        presence=presence,
        companion_status=copy.deepcopy(router.document),
    )

    assert attempts == ["put"]


def test_binding_refuses_an_unshipped_companion_version(tmp_path: Path) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    stale = copy.deepcopy(router.document)
    stale["version"] = "0.0.0-stale"

    with pytest.raises(ControllerError, match="requires companion"):
        controller.configure_policy_deployment(
            overlay_rule_ids=("overlay-rule",),
            source="auto",
            status=stale,
            host_key=host_key,
        )

    assert router.writes == []


def test_binding_and_mutation_refuse_same_version_with_wrong_package(
    tmp_path: Path,
) -> None:
    controller, _store, router, host_key = make_controller(tmp_path)
    router.document["package_md5"] = "0" * 32

    with pytest.raises(ControllerError, match="stored package MD5"):
        controller.configure_policy_deployment(
            core_rule_ids=("core-rule",),
            status=router.document,
            host_key=host_key,
        )

    router.document["package_md5"] = RouterInstaller(router).expected_package_md5
    controller.configure_policy_deployment(
        core_rule_ids=("core-rule",),
        status=router.document,
        host_key=host_key,
    )
    router.document["package_md5"] = "f" * 32
    router.writes.clear()

    with pytest.raises(ControllerError, match="stored package MD5"):
        controller.apply_persistent_core(("core-rule",))

    assert router.writes == []


def test_device_rows_are_rejected_from_source_scoped_overlays(
    tmp_path: Path,
) -> None:
    controller, store, _router, _host_key = make_controller(tmp_path)
    store.rules.append(
        Rule(
            id="device-rule",
            name="Whole LAN device",
            match_kind=MatchKind.DEVICE,
            selector="192.168.1.77",
            target=RouteTarget.DIRECT,
            region="direct",
            priority=100,
        )
    )

    summary = controller.policy_layer_preflight(("device-rule",), layer="overlay")

    assert summary.can_apply is False
    assert summary.compilation is None
    assert "cannot contain Device" in str(summary.error)


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
    client._ensure_policy_transaction_helper = lambda: None  # type: ignore[method-assign]
    client._policy_identity_args = lambda: (  # type: ignore[method-assign]
        ROUTER_VERSION,
        "a" * 32,
        "b" * 32,
    )

    client.core_apply(4, "# rules\n")
    client.core_rollback(5)
    client.overlay_put(
        "controller-abc",
        0,
        "auto",
        "# overlay\n",
        expected_source="192.168.1.166/32",
        expected_mac="AA-BB-CC-DD-EE-FF",
    )
    client.overlay_remove("controller-abc", 1)
    client.overlay_list()
    client.effective_status()

    assert calls == [
        (
            ["core-apply", ROUTER_VERSION, "a" * 32, "b" * 32, "4", "-"],
            b"# rules\n",
            HYBRID_POLICY_TIMEOUT,
        ),
        (
            [
                "core-rollback",
                ROUTER_VERSION,
                "a" * 32,
                "b" * 32,
                "5",
                "--json",
            ],
            None,
            HYBRID_POLICY_TIMEOUT,
        ),
        (
            [
                "overlay-put",
                ROUTER_VERSION,
                "a" * 32,
                "b" * 32,
                "controller-abc",
                "0",
                "auto",
                "192.168.1.166/32",
                "aa:bb:cc:dd:ee:ff",
                "-",
            ],
            b"# overlay\n",
            HYBRID_POLICY_TIMEOUT,
        ),
        (
            [
                "overlay-remove",
                ROUTER_VERSION,
                "a" * 32,
                "b" * 32,
                "controller-abc",
                "1",
            ],
            None,
            HYBRID_POLICY_TIMEOUT,
        ),
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
        return CommandResult(
            stdout="upload" if input_bytes is None else "installed",
            stderr="",
            returncode=0,
        )

    client._run_remote = run  # type: ignore[method-assign]

    assert (
        client.ensure_hybrid_helper(
            payload,
            digest,
            expected_version=ROUTER_VERSION,
            expected_package_md5="a" * 32,
        )
        == "installed"
    )
    assert len(calls) == 2
    probe_arguments, probe_payload, probe_timeout = calls[0]
    assert probe_arguments[:2] == ["/bin/sh", "-c"]
    assert "/tmp/astrill-lazy/alhybrid" in probe_arguments[2]
    assert probe_payload is None
    assert probe_timeout == 30
    arguments, uploaded, timeout = calls[1]
    assert arguments[:2] == ["/bin/sh", "-c"]
    assert "/tmp/astrill-lazy/alhybrid" in arguments[2]
    assert "md5sum" in arguments[2]
    assert "chmod 700" in arguments[2]
    assert "mv -f" in arguments[2]
    assert "/tmp/astrill-lazy/controller.lock" in arguments[2]
    assert 'printf \'%s\\n\' "$$" > "$lock/pid"' in arguments[2]
    assert "nvram get astrill_lazy_pkg_md5" in arguments[2]
    assert uploaded == payload
    assert timeout == 120


def test_installer_reads_hybrid_helper_without_adding_it_to_nvram_package(
    tmp_path: Path,
) -> None:
    router_root = tmp_path / "router"
    router_root.mkdir()
    payload = b"#!/bin/sh\nprintf volatile-helper\n"
    (router_root / "alhybrid").write_bytes(payload)
    for name in ("alctl", "alapi", "alpage"):
        (router_root / name).write_bytes(f"#!/bin/sh\n# {name}\n".encode())
    (router_root / "VERSION").write_text(ROUTER_VERSION + "\n", encoding="ascii")
    captured: list[tuple[bytes, str, str, str]] = []

    class Client:
        def ensure_hybrid_helper(
            self,
            value: bytes,
            digest: str,
            *,
            expected_version: str,
            expected_package_md5: str,
        ) -> str:
            captured.append((value, digest, expected_version, expected_package_md5))
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
    assert captured == [
        (
            payload,
            result.helper_md5,
            installer.expected_version,
            installer.expected_package_md5,
        )
    ]


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
