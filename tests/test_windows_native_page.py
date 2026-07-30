from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from astrill_lazy.native_settings import (
    CIPHER_OPTIONS,
    DNS_OPTIONS,
    MODE_KEYS,
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
)
from astrill_lazy.windows_native_page import (
    SECTION_DEFINITIONS,
    WindowsNativeSettingsPage,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def page(app: QApplication) -> WindowsNativeSettingsPage:
    value = WindowsNativeSettingsPage(
        on_refresh=lambda: None,
        on_save=lambda: None,
    )
    yield value
    value.close()


def native_settings(**overrides: str) -> NativeAstrillSettings:
    values = {
        "astrill_status": "3",
        "astrill_serverid": "998",
        "astrill_sid": "12",
        "astrill_ip": "123456",
        "astrill_port": "443",
        "astrill_portindex": "0",
        "astrill_protocol": "0",
        "astrill_vpnmode": "1",
        "astrill_favlist": ("998:123456:443:0:1:12,999:123457:443:0:1:13"),
        "astrill_routingmode": "2",
        "astrill_iplistraw": "example.com",
        "astrill_iplist": "",
        "astrill_devmode": "1",
        "astrill_devices": ("AA:BB:CC:DD:EE:FF/192.168.1.9/Office laptop"),
        "astrill_ifmode": "1",
        "astrill_iflist": "wl0",
        "astrill_ifexlist": "",
        "astrill_vlanmode": "2",
        "astrill_vlanlist": "vlan3",
        "astrill_dmzdevice": "",
        "astrill_dnsserver": "3",
        "astrill_userdns": "",
        "astrill_adsblock": "0",
        "astrill_nosplitdns": "0",
        "astrill_vpndnsallsites": "1",
        "astrill_cipher": "default",
        "astrill_wanmtu": "1446",
        "astrill_accel": "1",
        "astrill_blockinternet": "1",
        "astrill_autocycle": "0",
        "astrill_autostart": "1",
        "astrill_iplistext": "0",
        "astrill_iplistfile": "",
        "astrill_exflt": "",
    }
    values.update(overrides)
    return NativeAstrillSettings.from_dict(values)


def test_shared_human_options_cover_native_validation() -> None:
    assert {value for value, _label in DNS_OPTIONS} == MODE_KEYS["astrill_dnsserver"]
    assert {value for value, _label in CIPHER_OPTIONS} == MODE_KEYS["astrill_cipher"]


def test_page_covers_every_safe_key_and_starts_disabled(
    page: WindowsNativeSettingsPage,
) -> None:
    assert len(page.presented_nvram_keys) == len(SAFE_NATIVE_ASTRILL_KEYS) == 34
    assert set(page.presented_nvram_keys) == set(SAFE_NATIVE_ASTRILL_KEYS)
    assert page.device_table.minimumHeight() >= 200
    assert page.device_table.verticalHeader().defaultSectionSize() >= 40
    assert not page.save_button.isEnabled()
    assert all(not control.isEnabled() for control in page._write_controls)

    page.render(native_settings())

    assert not page.dirty
    assert page.collect_changes() == {}
    assert page._state_labels["astrill_status"].text() == "Connected (status 3)"
    assert "OpenVPN UDP" in page._state_labels["astrill_protocol"].text()
    assert any(
        label.text() == "NVRAM · astrill_serverid"
        for label in page.findChildren(QLabel)
    )


def test_page_has_clear_spacious_sections_and_keeps_controls_in_context(
    page: WindowsNativeSettingsPage,
) -> None:
    expected_names = tuple(title for _section_id, title, _help in SECTION_DEFINITIONS)
    assert page.section_names == expected_names
    assert isinstance(page.section_tabs, QTabWidget)
    assert page.section_tabs.count() == len(expected_names) == 7
    assert page.section_tabs.tabBar().expanding()
    assert (
        tuple(
            page.section_tabs.tabText(index)
            for index in range(page.section_tabs.count())
        )
        == expected_names
    )

    for section_id, _title, description in SECTION_DEFINITIONS:
        section = page._section_pages[section_id]
        summary = section.findChild(QLabel, f"nativeSectionSummary_{section_id}")
        assert summary is not None
        assert summary.text() == description
        assert summary.wordWrap()
        margins = page._section_layouts[section_id].contentsMargins()
        assert margins.left() >= 16
        assert page._section_layouts[section_id].spacing() >= 16

    controls_by_section = {
        "overview": page._state_labels["astrill_serverid"],
        "connection": page._direct_controls["astrill_cipher"],
        "routing": page.site_default,
        "privacy_dns": page._direct_controls["astrill_dnsserver"],
        "devices": page.device_table,
        "resilience": page._direct_controls["astrill_autocycle"],
        "advanced": page._direct_controls["astrill_iplistext"],
    }
    for section_id, control in controls_by_section.items():
        assert page._section_pages[section_id].isAncestorOf(control)


def test_section_navigation_preserves_rendered_draft_and_dirty_state(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings())
    page.section_tabs.setCurrentWidget(page._section_pages["privacy_dns"])
    ads = page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    page.section_tabs.setCurrentWidget(page._section_pages["overview"])

    assert page.dirty
    assert ads.isChecked()
    assert page.collect_changes() == {"astrill_adsblock": "1"}

    page.render(native_settings(astrill_adsblock="1"))
    assert not page.dirty
    assert ads.isChecked()
    assert page.collect_changes() == {}


def test_dirty_state_reverts_and_read_only_busy_guards_apply(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings())
    ads = page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    assert page.dirty
    assert page.save_button.isEnabled()
    assert page.collect_changes() == {"astrill_adsblock": "1"}

    ads.setChecked(False)
    assert not page.dirty
    assert page.collect_changes() == {}

    ads.setChecked(True)
    page.set_read_only(True)
    assert not page.save_button.isEnabled()
    assert all(not control.isEnabled() for control in page._write_controls)

    page.set_read_only(False)
    page.set_busy(True)
    assert not page.refresh_button.isEnabled()
    assert not page.save_button.isEnabled()
    assert all(not control.isEnabled() for control in page._write_controls)

    page.set_busy(False)
    assert page.refresh_button.isEnabled()
    assert page.save_button.isEnabled()
    page.render(native_settings())
    assert not page.dirty


def test_favorite_summary_refresh_preserves_unsaved_controls(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings())
    ads = page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)

    page.render_favorite_summary(
        native_settings(
            astrill_favlist="998:123456:443:0:1:12",
        )
    )

    assert page.dirty
    assert ads.isChecked()
    assert page._state_labels["astrill_favlist"].text() == "1 saved endpoint"

    page.render_favorite_summary(native_settings(astrill_favlist="malformed"))
    assert page.dirty
    assert ads.isChecked()
    assert (
        page._state_labels["astrill_favlist"].text()
        == "Invalid favorite data · preserved"
    )
    page.render(native_settings())


@pytest.mark.parametrize("routing_mode", ("2", "3", "4"))
def test_effectively_unchanged_website_text_stays_clean(
    page: WindowsNativeSettingsPage,
    routing_mode: str,
) -> None:
    page.render(native_settings(astrill_routingmode=routing_mode))

    page.site_list.setPlainText(" example.com \n")

    assert not page.dirty
    assert not page.save_button.isEnabled()
    assert page.collect_changes() == {}


def test_refresh_requires_confirmation_before_discarding_changes(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes: list[bool] = []
    page = WindowsNativeSettingsPage(
        on_refresh=lambda: refreshes.append(True),
        on_save=lambda: None,
    )
    page.render(native_settings())
    page.mtu.setValue(1400)
    assert page.dirty

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    page.refresh_button.click()
    assert refreshes == []

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    page.refresh_button.click()
    assert refreshes == [True]
    page.close()


def test_collect_changes_normalizes_composite_and_direct_values(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings())
    page.site_list.setPlainText("example.com\n192.0.2.8\n192.0.2.9")
    page.wifi_exceptions.setText(" wl0 ; wl1 ")
    custom_dns = page._direct_controls["astrill_userdns"]
    assert isinstance(custom_dns, QLineEdit)
    custom_dns.setText(" 1.1.1.1   8.8.8.8 ")

    changes = page.collect_changes()

    assert changes["astrill_iplistraw"] == ("example.com\n192.0.2.8\n192.0.2.9")
    assert "192.0.2.8/31" in changes["astrill_iplist"]
    assert changes["astrill_iflist"] == "wl0;wl1"
    assert changes["astrill_userdns"] == "1.1.1.1 8.8.8.8"


def test_automatic_website_mode_is_preserved_until_website_controls_change(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings(astrill_routingmode="3"))
    ads = page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    assert page.collect_changes() == {"astrill_adsblock": "1"}

    page.site_list.appendPlainText("192.0.2.1")
    changes = page.collect_changes()

    assert changes["astrill_routingmode"] == "1"
    assert changes["astrill_iplistraw"] == "example.com\n192.0.2.1"


def test_observed_clients_join_native_device_routes(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(native_settings())
    page.update_clients(
        [
            {
                "mac": "11:22:33:44:55:66",
                "address": "192.168.1.20",
                "hostname": "Living Room TV",
            }
        ]
    )
    assert len(page._device_controls) == 2
    observed = next(
        control
        for device, control in page._device_controls
        if device.mac == "11:22:33:44:55:66"
    )
    assert all(control.isEnabled() for _device, control in page._device_controls)
    page.set_read_only(True)
    assert all(not control.isEnabled() for _device, control in page._device_controls)
    page.set_read_only(False)
    page.set_busy(True)
    assert all(not control.isEnabled() for _device, control in page._device_controls)
    page.set_busy(False)
    observed.setCurrentIndex(observed.findData("vpn"))

    changes = page.collect_changes()

    assert "astrill_devmode" not in changes
    assert "11:22:33:44:55:66/192.168.1.20/Living Room TV" in changes["astrill_devices"]


def test_automatic_site_mode_and_invalid_devices_are_preserved(
    page: WindowsNativeSettingsPage,
) -> None:
    page.render(
        native_settings(
            astrill_routingmode="4",
            astrill_devices="not/a/valid-device",
        )
    )

    assert page.collect_changes() == {}
    assert not page.device_default.isEnabled()
    page.set_read_only(True)
    assert all(not control.isEnabled() for _device, control in page._device_controls)
    page.set_read_only(False)
    page.set_busy(True)
    assert all(not control.isEnabled() for _device, control in page._device_controls)
