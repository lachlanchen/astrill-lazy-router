from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from astrill_lazy.catalog import load_catalog
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.store import ConfigStore, PolicyDeploymentManifest
from astrill_lazy.windows_controller import WindowsController
from astrill_lazy.windows_ui import MainWindow
from PySide6.QtCore import Qt, QTimer
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import QApplication, QMessageBox

CORE_HASH = "md5:" + ("a" * 32)
OVERLAY_HASH = "md5:" + ("b" * 32)
PEER_HASH = "md5:" + ("c" * 32)
EFFECTIVE_HASH = "md5:" + ("d" * 32)
COMPANION_VERSION = "1.2.3"
SOURCE = "192.168.1.166/32"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _rule(rule_id: str, name: str | None = None) -> Rule:
    return Rule(
        id=rule_id,
        name=name or rule_id,
        match_kind=MatchKind.DOMAIN,
        selector=f"{rule_id}.example.com",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=100,
    )


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rules: list[Rule] | None = None,
) -> MainWindow:
    store = ConfigStore(tmp_path / "config.json")
    store.read_only = False
    store.companion_enabled = True
    store.rules = list(rules or [_rule("core-rule"), _rule("overlay-rule")])
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        MainWindow,
        "_setup_network_recovery_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(
        MainWindow,
        "_refresh_status",
        lambda *_args, **_kwargs: None,
    )
    return MainWindow(controller)


def _status(
    window: MainWindow,
    *,
    owner_overlay: bool = True,
    peer_overlay: bool = True,
    degraded: bool = False,
) -> dict[str, Any]:
    overlays: list[dict[str, Any]] = []
    if owner_overlay:
        overlays.append(
            {
                "owner": window.controller.store.controller_id,
                "generation": 4,
                "hash": OVERLAY_HASH,
                "source": SOURCE,
                "origin_ids": ["overlay-rule"],
            }
        )
    if peer_overlay:
        overlays.append(
            {
                "owner": "controller-peer",
                "generation": 2,
                "hash": PEER_HASH,
                "source": "192.168.1.180/32",
                "origin_ids": ["peer-rule"],
            }
        )
    status: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "version": COMPANION_VERSION,
        "health": "healthy",
        "policy_health": "degraded" if degraded else "ready",
        "precedence_ok": not degraded,
        "runtime_epoch": "epoch-2",
        "core": {
            "generation": 3,
            "hash": CORE_HASH,
            "origin_ids": ["core-rule"],
            "rows": 2,
            "bytes": 140,
        },
        "overlays": overlays,
        "effective": {
            "hash": EFFECTIVE_HASH,
            "origin_ids": [
                "core-rule",
                *(["overlay-rule"] if owner_overlay else []),
                *(["peer-rule"] if peer_overlay else []),
            ],
            "rows": 4,
            "bytes": 360,
        },
    }
    if degraded:
        status["last_reconcile_error"] = "policy precedence verification failed"
    return status


def _save_manifest(
    window: MainWindow,
    *,
    auto_restore: bool = False,
    source: str = SOURCE,
) -> PolicyDeploymentManifest:
    manifest = PolicyDeploymentManifest(
        router_host=window.controller.store.router_host,
        router_port=window.controller.store.router_port,
        router_host_key_fingerprint="SHA256:test",
        companion_version=COMPANION_VERSION,
        controller_id=window.controller.store.controller_id,
        source=source,
        resolved_source=source,
        core_rule_ids=("core-rule",),
        overlay_rule_ids=("overlay-rule",),
        core_hash=CORE_HASH,
        overlay_hash=OVERLAY_HASH,
        core_generation=3,
        overlay_generation=4,
        restore_overlay_after_reboot=auto_restore,
        last_runtime_epoch="epoch-1",
    )
    window.controller.store.upsert_deployment(manifest)
    return manifest


def _select_first_policy(window: MainWindow) -> tuple[str, ...]:
    item = window.policy_tree.topLevelItem(0)
    item.setSelected(True)
    return (str(item.data(0, Qt.ItemDataRole.UserRole)),)


def _run_synchronously(
    _label: str,
    function: Any,
    success: Any = None,
    **_kwargs: Any,
) -> None:
    result = function()
    if success is not None:
        success(result)


def test_legacy_companion_keeps_apply_actions(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    window.router_status = {
        "health": "healthy",
        "origin_count": 1,
        "enabled_origin_count": 1,
    }

    window._update_policy_metric()

    assert window.policy_storage_group.isHidden()
    assert not window.apply_button.isHidden()
    assert not window.apply_selected_button.isHidden()
    window.close()


def test_hybrid_layers_render_with_neutral_amber_red_semantics(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _save_manifest(window)
    window.router_status = _status(window)

    window._update_policy_metric()

    assert not window.policy_storage_group.isHidden()
    assert window.apply_button.isHidden()
    assert window.apply_selected_button.isHidden()
    assert window.policy_storage_cells["local"].property("storageTone") == "neutral"
    assert window.policy_storage_cells["core"].property("storageTone") == "green"
    assert (
        window.policy_storage_cells["this_overlay"].property("storageTone") == "green"
    )
    assert (
        window.policy_storage_cells["other_overlays"].property("storageTone")
        == "neutral"
    )
    assert window.policy_storage_cells["effective"].property("storageTone") == "green"
    assert window.policy_storage_cells["this_overlay"].text().startswith("1 origin")
    assert window.policy_storage_cells["other_overlays"].text().startswith("1 owner")
    assert window.policy_overlay_source.text() == SOURCE
    assert window.metric_labels["rules"].text() == "2 / 3"
    assert "Router policy is up to date" in window.policy_sync_state.text()

    window.router_status = _status(window, owner_overlay=False)
    window._update_policy_metric()
    assert window.policy_storage_cells["this_overlay"].text() == "Not restored"
    assert (
        window.policy_storage_cells["this_overlay"].property("storageTone") == "amber"
    )
    assert window.policy_storage_cells["effective"].property("storageTone") == "amber"
    assert "needs restore" in window.policy_sync_state.text()

    window.router_status = _status(window, degraded=True)
    window._update_policy_metric()
    assert window.policy_storage_cells["effective"].property("storageTone") == "red"
    assert "Router policy needs attention" in window.policy_sync_state.text()

    source_mismatch = _status(window)
    source_mismatch["overlays"][0]["source"] = "192.168.1.199/32"
    window.router_status = source_mismatch
    window._update_policy_metric()
    assert window.policy_storage_cells["this_overlay"].property("storageTone") == "red"
    assert window.policy_overlay_source_state.property("storageTone") == "red"
    assert "Router reports 192.168.1.199/32" in (
        window.policy_overlay_source_state.text()
    )
    window.close()


def test_ram_load_refuses_missing_source(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch, rules=[_rule("overlay-rule")])
    window.router_status = _status(
        window,
        owner_overlay=False,
        peer_overlay=False,
    )
    window._update_policy_metric()
    _select_first_policy(window)
    window.policy_overlay_source.clear()
    warnings: list[tuple[str, str]] = []
    tasks: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        window,
        "_run_task",
        lambda label, *_args, **_kwargs: tasks.append(label),
    )

    window._load_selected_into_ram()

    assert not tasks
    assert warnings
    assert "RAM overlay actions refuse" in warnings[0][1]
    window.close()


def test_first_ram_load_configures_manifest_and_uses_keyword_source(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch, rules=[_rule("overlay-rule")])
    window.router_status = _status(
        window,
        owner_overlay=False,
        peer_overlay=False,
    )
    window._update_policy_metric()
    selected = _select_first_policy(window)
    window.policy_overlay_source.setText(SOURCE)
    configured: list[dict[str, Any]] = []
    loaded: list[tuple[tuple[str, ...], str]] = []

    def configure(**kwargs: Any) -> object:
        configured.append(kwargs)
        return SimpleNamespace()

    def load(rule_ids: tuple[str, ...], *, source: str) -> dict[str, Any]:
        loaded.append((rule_ids, source))
        return window.router_status

    monkeypatch.setattr(
        window.controller,
        "configure_policy_deployment",
        configure,
    )
    monkeypatch.setattr(window.controller, "load_ram_overlay", load)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(window, "_run_task", _run_synchronously)

    window._load_selected_into_ram()

    assert configured == [
        {
            "core_rule_ids": (),
            "overlay_rule_ids": selected,
            "source": SOURCE,
            "restore_overlay_after_reboot": False,
            "status": window.router_status,
            "host_key": None,
        }
    ]
    assert loaded == [(selected, SOURCE)]
    window.close()


def test_first_core_pin_configures_manifest_without_requiring_ram_source(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch, rules=[_rule("core-rule")])
    window.router_status = _status(
        window,
        owner_overlay=False,
        peer_overlay=False,
    )
    window._update_policy_metric()
    selected = _select_first_policy(window)
    window.policy_overlay_source.clear()
    configured: list[dict[str, Any]] = []
    pinned: list[tuple[str, ...]] = []

    def configure(**kwargs: Any) -> object:
        configured.append(kwargs)
        return SimpleNamespace()

    def pin(rule_ids: tuple[str, ...]) -> dict[str, Any]:
        pinned.append(rule_ids)
        return window.router_status

    monkeypatch.setattr(
        window.controller,
        "configure_policy_deployment",
        configure,
    )
    monkeypatch.setattr(window.controller, "apply_persistent_core", pin)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(window, "_run_task", _run_synchronously)

    window._pin_selected_to_core()

    assert configured == [
        {
            "core_rule_ids": selected,
            "overlay_rule_ids": (),
            "source": "auto",
            "restore_overlay_after_reboot": False,
            "status": window.router_status,
            "host_key": None,
        }
    ]
    assert pinned == [selected]
    window.close()


def test_restore_refuses_missing_or_changed_visible_source(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _save_manifest(window)
    window.router_status = _status(window)
    window._update_policy_metric()
    warnings: list[str] = []
    tasks: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    monkeypatch.setattr(
        window,
        "_run_task",
        lambda label, *_args, **_kwargs: tasks.append(label),
    )

    window.policy_overlay_source.clear()
    window._restore_ram_overlay_now()
    window.policy_overlay_source.setText("192.168.1.199/32")
    window._restore_ram_overlay_now()

    assert tasks == []
    assert "RAM overlay actions refuse" in warnings[0]
    assert "differs from the saved overlay binding" in warnings[1]
    window.close()


def test_restore_remove_and_auto_restore_use_exact_controller_contract(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _save_manifest(window)
    window.router_status = _status(window)
    window._update_policy_metric()
    calls: list[tuple[str, object]] = []

    def restore() -> dict[str, Any]:
        calls.append(("restore", None))
        return window.router_status

    def remove() -> dict[str, Any]:
        calls.append(("remove", None))
        return window.router_status

    def set_restore(
        enabled: bool,
        *,
        status: dict[str, Any] | None = None,
        host_key: object | None = None,
    ) -> PolicyDeploymentManifest:
        calls.append(("auto", (enabled, status, host_key)))
        return window.controller.store.policy_deployments[0]

    monkeypatch.setattr(window.controller, "restore_ram_overlay_now", restore)
    monkeypatch.setattr(window.controller, "remove_ram_overlay", remove)
    monkeypatch.setattr(
        window.controller,
        "set_overlay_restore_enabled",
        set_restore,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(window, "_run_task", _run_synchronously)

    window._restore_ram_overlay_now()
    window._remove_this_overlay()
    window.policy_auto_restore_check.setChecked(True)

    assert calls[0] == ("restore", None)
    assert calls[1] == ("remove", None)
    assert calls[2][0] == "auto"
    enabled, status, host_key = calls[2][1]  # type: ignore[misc]
    assert enabled is True
    assert status == window.router_status
    assert host_key is None
    window.close()


def test_status_render_never_duplicates_controller_auto_restore(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _save_manifest(window, auto_restore=True)
    calls: list[object] = []
    monkeypatch.setattr(
        window.controller,
        "restore_ram_overlay_now",
        lambda: calls.append(None),
    )

    window._status_loaded(_status(window, owner_overlay=False))
    app.processEvents()

    assert calls == []
    assert window.policy_auto_restore_check.isChecked()
    assert "needs restore" in window.policy_sync_state.text()
    window.close()


def test_network_return_reconciles_once_per_offline_cycle(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    refreshes: list[bool] = []
    monkeypatch.setattr(
        window,
        "_refresh_status",
        lambda *, quiet=False: refreshes.append(quiet),
    )
    monkeypatch.setattr(QTimer, "singleShot", lambda _delay, callback: callback())

    window._network_reachability_changed(QNetworkInformation.Reachability.Disconnected)
    window._network_reachability_changed(QNetworkInformation.Reachability.Online)
    window._network_reachability_changed(QNetworkInformation.Reachability.Online)
    assert refreshes == [True]

    window._network_reachability_changed(QNetworkInformation.Reachability.Disconnected)
    window._network_reachability_changed(QNetworkInformation.Reachability.Local)
    assert refreshes == [True, True]

    window.busy_count = 1
    window._network_reachability_changed(QNetworkInformation.Reachability.Disconnected)
    window._network_reachability_changed(QNetworkInformation.Reachability.Site)
    assert window._network_recovery_pending is True
    assert refreshes == [True, True]
    window.busy_count = 0
    window._resume_pending_network_recovery()
    assert refreshes == [True, True, True]
    window.close()
