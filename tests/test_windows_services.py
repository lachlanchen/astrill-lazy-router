from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from astrill_lazy.catalog import load_catalog
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.service_policy import ServiceRouteMode
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import WindowsController
from astrill_lazy.windows_ui import MainWindow
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    store = ConfigStore(tmp_path / "config.json")
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(MainWindow, "_refresh_status", lambda *_args, **_kwargs: None)
    return MainWindow(controller)


def _writable_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rules: list[Rule] | None = None,
) -> MainWindow:
    store = ConfigStore(tmp_path / "config.json")
    store.read_only = False
    store.companion_enabled = True
    store.rules = list(rules or [])
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(MainWindow, "_refresh_status", lambda *_args, **_kwargs: None)
    return MainWindow(controller)


def _capacity_rules(count: int = 120) -> list[Rule]:
    return [
        Rule(
            id=f"ui-capacity-{index:03d}",
            name=f"UI capacity policy {index:03d}",
            match_kind=MatchKind.DOMAIN,
            selector=f"ui-capacity-{index:03d}.example.com",
            target=RouteTarget.DIRECT,
            region="direct",
            priority=100 + index,
        )
        for index in range(count)
    ]


def _select_combo_data(window: MainWindow, name: str, value: object) -> None:
    combo = getattr(window, name)
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_china_filter_adds_uu_remote_to_disk_and_policy_table(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _select_combo_data(window, "service_country_filter", "China")
    window.service_search.setText("UU Remote")

    assert window.service_tree.topLevelItemCount() == 1
    item = window.service_tree.topLevelItem(0)
    assert item.text(1) == "UU Remote"
    assert item.data(1, Qt.ItemDataRole.UserRole) == "uu-remote"

    item.setCheckState(0, Qt.CheckState.Checked)
    assert window._selected_service_ids == {"uu-remote"}
    assert window.service_add_selected_button.isEnabled()
    _select_combo_data(
        window,
        "service_route_mode",
        ServiceRouteMode.SUGGESTED,
    )
    window.service_add_selected_button.click()

    assert len(window.controller.store.rules) == 1
    rule = window.controller.store.rules[0]
    assert rule.name == "UU Remote"
    assert rule.selector == "uu-remote"
    assert rule.match_kind is MatchKind.SERVICE
    assert rule.target is RouteTarget.DIRECT
    assert rule.region == "direct"
    assert window.policy_tree.topLevelItemCount() == 1
    assert window.policy_tree.topLevelItem(0).text(0) == "UU Remote"
    assert window._selected_service_ids == set()
    assert window.metric_labels["rules"].text() == "1 / —"

    window.router_status = {
        "enabled_origin_count": 0,
        "origin_count": 5,
    }
    window._update_policy_metric()
    assert window.metric_labels["rules"].text() == "1 / 0"
    assert "Use Apply policies" in window.policy_sync_state.text()
    assert "0 enabled origins applied" in window.metric_labels["rules"].toolTip()
    assert "5 total origins are stored" in window.metric_labels["rules"].toolTip()

    document = json.loads(window.controller.store.path.read_text(encoding="utf-8"))
    assert document["rules"][0]["selector"] == "uu-remote"
    window.close()


def test_service_selection_survives_country_filter_and_select_visible(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    _select_combo_data(window, "service_country_filter", "China")
    window.service_search.setText("UU Remote")
    window.service_select_visible.setCheckState(Qt.CheckState.Checked)
    assert window._selected_service_ids == {"uu-remote"}

    _select_combo_data(window, "service_country_filter", "United States")
    assert window.service_tree.topLevelItemCount() == 0
    assert "1 hidden by filters" in window.service_selection_count.text()

    window.service_search.clear()
    assert window.service_tree.topLevelItemCount() > 0
    assert window._selected_service_ids == {"uu-remote"}
    assert window.service_select_visible.checkState() == Qt.CheckState.Unchecked

    window.service_clear_selection_button.click()
    assert window._selected_service_ids == set()
    assert not window.service_add_selected_button.isEnabled()
    window.close()


def test_service_save_failure_is_visible_and_preserves_selection(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    window.service_search.setText("UU Remote")
    item = window.service_tree.topLevelItem(0)
    item.setCheckState(0, Qt.CheckState.Checked)
    warnings: list[tuple[str, str]] = []

    monkeypatch.setattr(
        window.controller,
        "add_services",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window._add_services(ServiceRouteMode.SUGGESTED)

    assert warnings == [("Could not update services", "disk full")]
    assert window._selected_service_ids == {"uu-remote"}
    window.close()


def test_policy_add_service_navigates_to_catalog(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    window.show()
    app.processEvents()
    window.navigation.setCurrentRow(0)

    window._show_services_for_policy()

    assert window.navigation.currentRow() == 1
    assert window.stack.currentIndex() == 1
    app.processEvents()
    assert window.service_search.hasFocus()
    window.close()


def test_oversize_policy_capacity_blocks_full_apply_but_allows_selection(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _writable_window(
        tmp_path,
        monkeypatch,
        rules=_capacity_rules(),
    )

    assert not window.apply_button.isEnabled()
    assert "6,144" in window.policy_capacity_state.text()
    assert "Full apply is blocked" in window.policy_capacity_state.text()
    assert "Select a smaller set" in window.apply_button.toolTip()

    first = window.policy_tree.topLevelItem(0)
    second = window.policy_tree.topLevelItem(1)
    first.setSelected(True)
    second.setSelected(True)
    app.processEvents()

    assert window.apply_selected_button.isEnabled()
    assert window.apply_selected_button.text() == "Apply selected (2)"
    assert "2 selected policies" in window.apply_selected_button.toolTip()
    assert "Unselected policies remain saved locally" in (
        window.apply_selected_button.toolTip()
    )
    window.close()


def test_equal_policy_counts_with_different_origins_are_not_in_sync(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = _capacity_rules(2)
    window = _writable_window(
        tmp_path,
        monkeypatch,
        rules=rules,
    )
    window.router_status = {
        "enabled_origin_count": 2,
        "origin_count": 2,
        "rules": [
            {"origin": rules[0].id, "enabled": True},
            {"origin": "stale-router-origin", "enabled": True},
        ],
    }

    window._update_policy_metric()

    assert window.metric_labels["rules"].text() == "2 / 2"
    assert "Enabled policy IDs differ" in window.policy_sync_state.text()
    assert rules[1].id in window.policy_sync_state.text()
    assert "stale-router-origin" in window.policy_sync_state.text()
    assert "Missing IDs" in window.metric_labels["rules"].toolTip()
    assert "Extra IDs" in window.metric_labels["rules"].toolTip()
    window.close()


def test_oversize_apply_reports_local_error_without_router_unavailable(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _writable_window(
        tmp_path,
        monkeypatch,
        rules=_capacity_rules(),
    )
    warnings: list[tuple[str, str]] = []
    window.sidebar_status.setText("Router companion · connected")
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window._apply_policies()

    assert warnings
    assert warnings[0][0] == "Policies exceed router capacity"
    assert "router accepts at most 6,144" in warnings[0][1]
    assert window.sidebar_status.text() == "Router companion · connected"
    window.close()


def test_connected_policy_degradation_is_not_reported_as_vpn_failure(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _writable_window(tmp_path, monkeypatch)
    degraded = {
        "health": "degraded",
        "vpn_state": "up",
        "astrill_server_id": 1109,
        "astrill_protocol": 3,
        "policy_health": "degraded",
        "precedence_ok": False,
        "native_min_pref": 27998,
        "direct_pref": 27996,
        "vpn_pref": 27997,
        "table_readiness": {
            "direct": True,
            "vpn": True,
            "native": False,
        },
        "last_reconcile_error": "native rules did not stabilize",
        "vpn_fail_closed": False,
        "origin_count": 0,
    }

    window._status_loaded(degraded)

    assert window.metric_labels["tunnel"].text() == "Connected"
    assert window.metric_labels["controller"].text() == "Policy degraded"
    assert window.sidebar_status.text() == "Astrill connected; policy routing degraded"
    assert "policy degraded" in window.router_connection_label.text()
    assert "native 27998" in window.companion_label.text()
    assert "native rules did not stabilize" in window.companion_label.toolTip()
    assert "inactive while the VPN tunnel is up" not in (
        window.companion_label.toolTip()
    )
    action = window.connection_page.action_status.text()
    assert "Astrill connected, but policy routing is degraded" in action
    assert "VPN tunnel is up" in action

    window._status_loaded(
        {
            "health": "healthy",
            "vpn_state": "up",
            "policy_health": "ready",
            "precedence_ok": True,
            "vpn_fail_closed": False,
            "origin_count": 0,
        }
    )
    assert "inactive while the VPN tunnel is up" in (window.companion_label.toolTip())

    window._status_loaded(
        {
            "health": "healthy",
            "vpn_state": "down",
            "policy_health": "ready",
            "precedence_ok": True,
            "table_readiness": {
                "direct": True,
                "vpn": True,
                "native": False,
            },
            "vpn_fail_closed": True,
            "origin_count": 0,
        }
    )
    assert window.metric_labels["tunnel"].text() == "Disconnected"
    assert (
        window.sidebar_status.text()
        == "Router companion · tunnel disconnected · policy ready"
    )

    window._set_connection_outcome_status(
        {
            "vpn_state": "down",
            "policy_health": "degraded",
            "precedence_ok": True,
            "vpn_fail_closed": False,
            "last_reconcile_error": "fail-closed rule is missing",
        },
        "Astrill disconnected successfully.",
    )
    down_action = window.connection_page.action_status.text()
    assert "Astrill disconnected, but policy fail-closed is degraded" in down_action
    assert "tunnel is down" in down_action
    window.close()


def test_country_conflict_summary_and_endpoint_navigation(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    window.controller.add_custom_rule(
        name="Europe site",
        match_kind=MatchKind.DOMAIN,
        selector="eu.example",
        target=RouteTarget.VPN,
        region="europe",
    )
    window.controller.add_custom_rule(
        name="Japan site",
        match_kind=MatchKind.DOMAIN,
        selector="jp.example",
        target=RouteTarget.VPN,
        region="japan",
    )

    window._render_countries()

    assert not window.country_banner.isHidden()
    assert "Europe" in window.country_banner.text()
    assert "Japan" in window.country_banner.text()
    assert "cannot be active on one shared tunnel" in window.country_banner.text()
    rows = {
        window.country_tree.topLevelItem(index).data(
            0,
            Qt.ItemDataRole.UserRole,
        ): window.country_tree.topLevelItem(index)
        for index in range(window.country_tree.topLevelItemCount())
    }
    assert "Europe site" in rows["europe"].text(2)
    assert "Japan site" in rows["japan"].text(2)
    assert "2 enabled policies" in window.country_result_count.text()

    window._open_region_endpoints("europe")
    assert window.navigation.currentRow() == window._page_index("endpoints")
    assert window.endpoint_search.text() == "Europe"
    window.close()
