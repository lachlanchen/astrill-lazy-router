from __future__ import annotations

from pathlib import Path
from typing import Any

import astrill_lazy.windows_controller as controller_module
import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import CompanionCheck, EnsureResult
from astrill_lazy.router import RouterError
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import ControllerError, WindowsController


class RecoveryRouter:
    def __init__(
        self,
        presence: dict[str, Any] | None = None,
        *,
        presence_error: Exception | None = None,
    ) -> None:
        self.presence = presence or {
            "installed": True,
            "version": "0.2.3",
            "runtime": True,
        }
        self.presence_error = presence_error
        self.calls: list[str] = []

    def companion_presence(self) -> dict[str, Any]:
        self.calls.append("presence")
        if self.presence_error is not None:
            raise self.presence_error
        return dict(self.presence)

    def native_astrill_status(self) -> dict[str, Any]:
        self.calls.append("native_status")
        return {"health": "healthy", "native_mode": True}


def make_controller(
    path: Path,
    router: RecoveryRouter,
) -> WindowsController:
    store = ConfigStore(path)
    store.companion_enabled = True
    store.save()
    return WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )


def install_fake_installer(
    monkeypatch: pytest.MonkeyPatch,
    router: RecoveryRouter,
    check: CompanionCheck,
    *,
    repaired_status: dict[str, Any] | None = None,
    repair_action: str = "repaired",
) -> list[bool]:
    ensure_calls: list[bool] = []

    class FakeInstaller:
        def __init__(self, supplied_router: object) -> None:
            assert supplied_router is router

        def check(
            self,
            *,
            presence: dict[str, Any] | None = None,
            status: dict[str, Any] | None = None,
        ) -> CompanionCheck:
            assert presence == router.presence
            assert status is None
            return check

        def ensure(self, *, allow_install: bool = True) -> EnsureResult:
            ensure_calls.append(allow_install)
            if repaired_status is None:
                raise AssertionError("unexpected companion repair")
            return EnsureResult(repaired_status, repair_action)

    monkeypatch.setattr(controller_module, "RouterInstaller", FakeInstaller)
    return ensure_calls


def test_reconcile_uses_a_current_healthy_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = RecoveryRouter()
    controller = make_controller(tmp_path / "config.json", router)
    status = {
        "health": "healthy",
        "version": "0.2.3",
        "jump_installed": True,
        "watchdog": True,
    }
    ensure_calls = install_fake_installer(
        monkeypatch,
        router,
        CompanionCheck("none", "0.2.3", "0.2.3", status, "current"),
    )

    assert controller.reconcile_status() == status
    assert ensure_calls == []
    assert controller.store.companion_enabled is True
    assert controller.recovery_notice is None


def test_reconcile_restores_only_the_validated_stored_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = RecoveryRouter({"installed": True, "version": "0.2.3", "runtime": False})
    controller = make_controller(tmp_path / "config.json", router)
    repaired = {
        "health": "healthy",
        "version": "0.2.3",
        "jump_installed": True,
        "watchdog": True,
    }
    ensure_calls = install_fake_installer(
        monkeypatch,
        router,
        CompanionCheck(
            "repair",
            "0.2.3",
            "0.2.3",
            None,
            "runtime needs repair",
        ),
        repaired_status=repaired,
    )

    assert controller.reconcile_status() == repaired
    assert ensure_calls == [False]
    assert controller.store.companion_enabled is True
    assert "restored from router NVRAM" in str(controller.recovery_notice)


def test_reconcile_surfaces_a_present_but_degraded_policy_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = RecoveryRouter()
    controller = make_controller(tmp_path / "config.json", router)
    degraded = {
        "health": "degraded",
        "version": "0.2.3",
        "jump_installed": True,
        "watchdog": True,
        "vpn_state": "up",
        "policy_health": "degraded",
        "precedence_ok": False,
        "last_reconcile_error": "native rules did not stabilize",
    }
    ensure_calls = install_fake_installer(
        monkeypatch,
        router,
        CompanionCheck(
            "repair",
            "0.2.3",
            "0.2.3",
            degraded,
            "policy routing needs repair",
        ),
        repaired_status=degraded,
        repair_action="degraded",
    )

    assert controller.reconcile_status() == degraded
    assert ensure_calls == [False]
    assert controller.store.companion_enabled is True
    notice = str(controller.recovery_notice)
    assert "policy routing remains degraded" in notice
    assert "native rules did not stabilize" in notice
    assert "restored from router NVRAM" not in notice


def test_reconcile_falls_back_to_native_when_companion_was_not_retained(
    tmp_path: Path,
) -> None:
    router = RecoveryRouter({"installed": False, "version": None, "runtime": False})
    path = tmp_path / "config.json"
    controller = make_controller(path, router)

    assert controller.reconcile_status()["native_mode"] is True
    assert controller.store.companion_enabled is False
    assert ConfigStore(path).companion_enabled is False
    assert "separately confirmed" in str(controller.recovery_notice)
    assert router.calls == ["presence", "native_status"]


def test_reconcile_does_not_change_mode_while_router_is_unavailable(
    tmp_path: Path,
) -> None:
    router = RecoveryRouter(presence_error=RouterError("router rebooting"))
    path = tmp_path / "config.json"
    controller = make_controller(path, router)

    with pytest.raises(RouterError, match="router rebooting"):
        controller.reconcile_status()

    assert controller.store.companion_enabled is True
    assert ConfigStore(path).companion_enabled is True
    assert controller.recovery_notice is None


def test_reconcile_never_silently_rewrites_an_incompatible_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = RecoveryRouter({"installed": True, "version": "0.2.2", "runtime": True})
    path = tmp_path / "config.json"
    controller = make_controller(path, router)
    ensure_calls = install_fake_installer(
        monkeypatch,
        router,
        CompanionCheck(
            "install",
            "0.2.3",
            "0.2.2",
            None,
            "explicit rewrite required",
        ),
    )

    with pytest.raises(ControllerError, match="separately confirmed"):
        controller.reconcile_status()

    assert ensure_calls == []
    assert controller.store.companion_enabled is True
    assert ConfigStore(path).companion_enabled is True


def test_reconcile_refuses_inconsistent_ephemeral_runtime(
    tmp_path: Path,
) -> None:
    router = RecoveryRouter({"installed": False, "version": None, "runtime": True})
    path = tmp_path / "config.json"
    controller = make_controller(path, router)

    with pytest.raises(ControllerError, match="without its persistent"):
        controller.reconcile_status()

    assert controller.store.companion_enabled is True
    assert ConfigStore(path).companion_enabled is True
