from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from astrill_lazy.astrill import (
    AstrillEndpoint,
    AstrillNode,
    AstrillServer,
)
from astrill_lazy.native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
)
from astrill_lazy.windows_connection_page import (
    CONNECTION_KEYS,
    ConnectionDraft,
    WindowsConnectionPage,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def servers() -> tuple[AstrillServer, ...]:
    return (
        _server(101, "United States - Seattle", 5101, 1_001),
        _server(202, "Japan - Tokyo", 5202, 2_001),
    )


@pytest.fixture()
def page(
    app: QApplication,
) -> WindowsConnectionPage:
    value = WindowsConnectionPage(
        on_refresh=lambda: None,
        on_save=lambda: None,
        on_connect=lambda: None,
        on_apply_reconnect=lambda: None,
        on_disconnect=lambda: None,
    )
    yield value
    value.close()


def native_settings(**overrides: str) -> NativeAstrillSettings:
    values = {
        "astrill_serverid": "101",
        "astrill_sid": "5101",
        "astrill_ip": "1001",
        "astrill_port": "443",
        "astrill_portindex": "0",
        "astrill_protocol": "0",
        "astrill_vpnmode": "1",
        "astrill_cipher": "default",
        "astrill_wanmtu": "1446",
        "astrill_accel": "1",
        "astrill_blockinternet": "1",
        "astrill_autocycle": "0",
        "astrill_favlist": "101:1001:443:0:1:5101",
        "astrill_autostart": "1",
        "astrill_status": "0",
    }
    values.update(overrides)
    return NativeAstrillSettings.from_dict(values)


def router_status(
    *,
    connected: bool = False,
    server_id: int = 101,
    protocol: int = 0,
    **extra: object,
) -> dict[str, object]:
    return {
        "vpn_state": "up" if connected else "down",
        "astrill_server_id": server_id,
        "astrill_protocol": protocol,
        **extra,
    }


def test_page_exposes_safe_transactional_api_and_spacious_groups(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(native_settings(), servers, router_status(connected=True))

    assert page.presented_nvram_keys == CONNECTION_KEYS
    assert set(CONNECTION_KEYS).issubset(SAFE_NATIVE_ASTRILL_KEYS)
    assert {group.title() for group in page.findChildren(QGroupBox)} >= {
        "Shared tunnel",
        "Endpoint",
        "Transport",
        "Resilience",
    }
    assert page.server_dropdown.isEditable()
    assert page.server_dropdown.completer().filterMode() == Qt.MatchFlag.MatchContains
    assert (
        page.server_dropdown.completer().caseSensitivity()
        == Qt.CaseSensitivity.CaseInsensitive
    )
    assert page.server_dropdown.currentData() == 101
    assert page.server_dropdown.currentText().startswith("★ ")
    assert page.protocol_dropdown.currentData() == 0
    assert page.port_dropdown.currentData() == 0
    assert "Connected" in page.status_detail.text()
    assert "United States - Seattle" in page.status_detail.text()

    draft = page.collect()

    assert isinstance(draft, ConnectionDraft)
    assert draft.selection.server_id == 101
    assert draft.selection.protocol == 0
    assert draft.selection.port == "443"
    assert draft.changes == {}
    assert page.collect_changes() == {}
    assert not page.dirty


def test_endpoint_protocol_port_and_favorite_form_one_draft(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(native_settings(), servers, router_status())

    page.server_dropdown.setCurrentIndex(page.server_dropdown.findData(202))
    page.protocol_dropdown.setCurrentIndex(page.protocol_dropdown.findData(0))
    page.port_dropdown.setCurrentIndex(page.port_dropdown.findData(1))
    page.favorite_switch.setChecked(True)
    page.cipher.setCurrentIndex(page.cipher.findData("AES-256-CBC"))
    page.mtu.setValue(1400)
    page.switches["astrill_autocycle"].setChecked(True)

    draft = page.collect()
    changes = page.collect_changes()

    assert page.dirty
    assert draft.selection.server_id == 202
    assert draft.selection.sid == 5202
    assert draft.selection.port == "4000-5000"
    assert draft.selection.port_index == 1
    assert draft.selection.protocol == 0
    assert len(draft.favorite_changes) == 1
    favorite_server_id, favorite = draft.favorite_changes[0]
    assert favorite_server_id == 202
    assert favorite is not None
    assert favorite.to_native() == "202:2002:4000-5000:0:1:5202"
    assert changes == {
        "astrill_serverid": "202",
        "astrill_sid": "5202",
        "astrill_ip": "2002",
        "astrill_port": "4000-5000",
        "astrill_portindex": "1",
        "astrill_cipher": "AES-256-CBC",
        "astrill_wanmtu": "1400",
        "astrill_autocycle": "1",
        "astrill_favlist": ("101:1001:443:0:1:5101,202:2002:4000-5000:0:1:5202"),
    }
    assert page.server_dropdown.currentText().startswith("★ ")

    page.protocol_dropdown.setCurrentIndex(page.protocol_dropdown.findData(3))

    assert not page.cipher.isEnabled()
    assert not page.mtu.isEnabled()
    assert "RouterPro" in page.cipher_hint.text()
    assert "TCP" in page.mtu_hint.text()


def test_typing_a_search_does_not_silently_keep_the_previous_server(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(native_settings(), servers, router_status())
    line_edit = page.server_dropdown.lineEdit()
    assert line_edit is not None

    line_edit.setText("Tokyo")
    line_edit.textEdited.emit("Tokyo")

    assert page.server_dropdown.currentIndex() == -1
    assert page.server_dropdown.currentText() == "Tokyo"
    assert page.dirty
    assert not page.connect_button.isEnabled()
    with pytest.raises(ValueError, match="select an available"):
        page.collect()

    page.server_dropdown.setCurrentIndex(page.server_dropdown.findData(202))

    assert page.server_dropdown.currentData() == 202
    assert page.protocol_dropdown.currentData() == 0
    assert page.port_dropdown.currentData() == 0


def test_dirty_conflict_preserves_draft_until_explicit_reload(
    app: QApplication,
    servers: tuple[AstrillServer, ...],
) -> None:
    refreshes: list[bool] = []
    dirty_states: list[bool] = []
    page = WindowsConnectionPage(
        on_refresh=lambda: refreshes.append(True),
        on_save=lambda: None,
        on_connect=lambda: None,
        on_apply_reconnect=lambda: None,
        on_disconnect=lambda: None,
        on_dirty_changed=dirty_states.append,
    )
    page.sync(native_settings(), servers, router_status())
    page.mtu.setValue(1400)
    assert page.dirty
    assert dirty_states == [True]

    remote = native_settings(astrill_wanmtu="1300")
    page.sync(remote, servers, router_status())

    assert page.mtu.value() == 1400
    assert page.dirty
    assert not page.conflict_banner.isHidden()
    assert page.collect().changes["astrill_wanmtu"] == "1400"

    page.conflict_reload_button.click()
    assert refreshes == [True]
    page.sync(remote, servers, router_status())

    assert page.mtu.value() == 1300
    assert not page.dirty
    assert page.conflict_banner.isHidden()
    assert dirty_states == [True, False]
    page.close()


def test_explicit_action_buttons_and_guards_are_state_aware(
    app: QApplication,
    servers: tuple[AstrillServer, ...],
) -> None:
    actions: list[str] = []
    callback = lambda value: lambda: actions.append(value)
    page = WindowsConnectionPage(
        on_refresh=callback("refresh"),
        on_save=callback("save"),
        on_connect=callback("connect"),
        on_apply_reconnect=callback("apply"),
        on_disconnect=callback("disconnect"),
    )
    page.sync(native_settings(), servers, router_status())

    assert page.connect_button.isEnabled()
    assert not page.save_button.isEnabled()
    assert not page.apply_button.isEnabled()
    assert not page.disconnect_button.isEnabled()
    page.connect_button.click()

    page.mtu.setValue(1400)
    assert not page.connect_button.isEnabled()
    assert page.save_button.isEnabled()
    assert page.apply_button.isEnabled()
    assert page.apply_button.text() == "Apply & Connect"
    page.save_button.click()
    page.apply_button.click()

    page.update_status(router_status(connected=True))
    assert not page.save_button.isEnabled()
    assert page.apply_button.isEnabled()
    assert page.apply_button.text() == "Apply & Reconnect"
    assert page.disconnect_button.isEnabled()
    page.disconnect_button.click()
    assert actions == ["connect", "save", "apply", "disconnect"]

    page.set_read_only(True)
    assert not page.guard_status.isHidden()
    assert not page.apply_button.isEnabled()
    assert not page.disconnect_button.isEnabled()
    assert page.refresh_button.isEnabled()
    assert all(not control.isEnabled() for control in page.switches.values())

    page.set_busy(True)
    assert not page.refresh_button.isEnabled()
    page.set_action_status("Connection was blocked by the local guard.", level="error")
    page.set_read_only(False)
    page.set_busy(False)
    assert "local guard" in page.action_status.text()
    page.close()


def test_invalid_router_values_are_preserved_until_intentionally_edited(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(
        native_settings(
            astrill_cipher="future-cipher",
            astrill_wanmtu="0",
            astrill_accel="2",
            astrill_favlist="malformed",
        ),
        servers,
        router_status(),
    )

    assert page.cipher.currentData() == "future-cipher"
    assert page.mtu.value() == 1446
    assert not page.switches["astrill_accel"].isChecked()
    assert not page.favorite_switch.isEnabled()
    assert "preserved" in page.favorite_detail.text()
    assert "preserved" in page.mtu_hint.text()
    assert not page.dirty
    assert page.collect().changes == {}

    page.cipher.setCurrentIndex(page.cipher.findData("default"))
    page.mtu.setValue(1400)
    page.switches["astrill_accel"].setChecked(True)

    assert page.collect().changes == {
        "astrill_cipher": "default",
        "astrill_wanmtu": "1400",
        "astrill_accel": "1",
    }
    assert "astrill_favlist" not in page.collect().changes


def test_favorite_removal_does_not_require_current_transport_support(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(native_settings(), servers, router_status())

    page.favorite_switch.setChecked(False)

    draft = page.collect()
    assert "astrill_favlist" not in draft.changes
    assert draft.favorite_changes == ((101, None),)
    assert page.collect_changes()["astrill_favlist"] == ""


def test_external_editor_lock_blocks_draft_writes_but_not_disconnect(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(native_settings(), servers, router_status(connected=True))

    page.set_external_lock("Resolve the Astrill-page draft first.")

    assert not page.favorite_switch.isEnabled()
    assert not page.apply_button.isEnabled()
    assert not page.save_button.isEnabled()
    assert page.disconnect_button.isEnabled()
    assert "Astrill-page draft" in page.action_status.text()


def test_disconnect_remains_available_without_a_server_catalog(
    page: WindowsConnectionPage,
) -> None:
    page.sync(native_settings(), (), router_status(connected=True))

    with pytest.raises(ValueError, match="available"):
        page.collect()
    assert page.disconnect_button.isEnabled()
    assert not page.connect_button.isEnabled()
    assert not page.save_button.isEnabled()
    assert not page.apply_button.isEnabled()


def test_unavailable_configured_selection_is_shown_without_silent_substitution(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(
        native_settings(astrill_serverid="999"),
        servers,
        router_status(server_id=999),
    )

    assert page.server_dropdown.currentData() == 999
    assert "unavailable" in page.server_dropdown.currentText()
    assert not page.dirty
    assert not page.connect_button.isEnabled()
    with pytest.raises(ValueError, match="unavailable"):
        page.collect()

    page.server_dropdown.setCurrentIndex(page.server_dropdown.findData(101))

    assert page.dirty
    assert page.collect().selection.server_id == 101


def test_unavailable_configured_port_is_preserved_until_a_valid_choice(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(
        native_settings(astrill_port="1194", astrill_portindex="99"),
        servers,
        router_status(),
    )

    assert page.port_dropdown.currentData() is None
    assert "unavailable" in page.port_dropdown.currentText()
    assert not page.dirty
    assert not page.connect_button.isEnabled()
    with pytest.raises(ValueError, match="available Astrill port"):
        page.collect()

    page.port_dropdown.setCurrentIndex(page.port_dropdown.findData(0))

    assert page.dirty
    assert page.collect().selection.port == "443"


def test_router_failure_reason_is_persistent(
    page: WindowsConnectionPage,
    servers: tuple[AstrillServer, ...],
) -> None:
    page.sync(
        native_settings(),
        servers,
        router_status(connection_error="Native connect command timed out."),
    )

    assert page.action_status.text() == "Native connect command timed out."
    page.set_read_only(True)
    assert page.action_status.text() == "Native connect command timed out."


def _server(
    server_id: int,
    name: str,
    node_id: int,
    encoded_base: int,
) -> AstrillServer:
    endpoints = (
        _endpoint(encoded_base, "443", 0, 1, 0),
        _endpoint(encoded_base + 1, "4000-5000", 0, 1, 1),
        _endpoint(encoded_base + 2, "443", 1, 1, 0),
        _endpoint(encoded_base + 3, "443", 0, 129, 0),
        _endpoint(encoded_base + 4, "443", 1, 129, 0),
    )
    return AstrillServer(
        id=server_id,
        name=name,
        nodes=(AstrillNode(id=node_id, weight=100, endpoints=endpoints),),
    )


def _endpoint(
    encoded_ip: int,
    port: str,
    mode: int,
    protocol_code: int,
    port_index: int,
) -> AstrillEndpoint:
    return AstrillEndpoint(
        encoded_ip=encoded_ip,
        port=port,
        mode=mode,
        protocol_code=protocol_code,
        port_index=port_index,
    )
