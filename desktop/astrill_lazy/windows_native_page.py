from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .astrill import ASTRILL_PROTOCOL_NAMES
from .models import RouteTarget
from .native_settings import (
    CIPHER_OPTIONS,
    DNS_OPTIONS,
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
    NativeDevice,
    binary_native_mode,
    device_policy_changes,
    normalize_native_changes,
    normalize_site_entries,
    site_policy_changes,
)

ROUTE_OPTIONS = (("direct", "Direct"), ("vpn", "Astrill"))
SITE_SOURCE_OPTIONS = (("0", "Inline website list"), ("1", "Router file"))


class WindowsNativeSettingsPage(QWidget):
    """Human-readable editor for the safe native Astrill NVRAM mirror."""

    def __init__(
        self,
        *,
        on_refresh: Callable[[], None],
        on_save: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setObjectName("nativeAstrillPage")
        self.settings: NativeAstrillSettings | None = None
        self.clients: list[dict[str, Any]] = []
        self._loading = False
        self._busy = False
        self._read_only = False
        self._dirty = False
        self._device_parse_error = ""
        self._presented_keys: list[str] = []
        self._write_controls: list[QWidget] = []
        self._device_controls: list[tuple[NativeDevice, QComboBox]] = []
        self._direct_controls: dict[str, QWidget] = {}
        self._state_labels: dict[str, QLabel] = {}
        self._on_refresh = on_refresh

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 12, 20)
        root.setSpacing(16)

        heading = QHBoxLayout()
        heading.setSpacing(10)
        intro = QLabel(
            "The same native Astrill controls as the Ubuntu app, translated "
            "into plain language. Only the explicit safe NVRAM allowlist is "
            "loaded; account names, passwords, tokens, and generated VPN "
            "credentials are never requested."
        )
        intro.setWordWrap(True)
        intro.setProperty("class", "nativeIntro")
        heading.addWidget(intro, 1)
        self.refresh_button = QPushButton("Load settings")
        self.refresh_button.clicked.connect(self._request_refresh)
        heading.addWidget(self.refresh_button)
        self.save_button = QPushButton("Save changed settings")
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(on_save)
        heading.addWidget(self.save_button)
        root.addLayout(heading)

        self.summary = QLabel("Native settings have not been loaded.")
        self.summary.setWordWrap(True)
        self.summary.setProperty("class", "nativeSummary")
        root.addWidget(self.summary)

        self._build_state(root)
        self._build_routing(root)
        self._build_dns(root)
        self._build_connection(root)
        self._build_advanced(root)
        root.addStretch(1)

        expected = set(SAFE_NATIVE_ASTRILL_KEYS)
        presented = set(self._presented_keys)
        if presented != expected or len(self._presented_keys) != len(expected):
            missing = sorted(expected - presented)
            repeated = sorted(
                key for key in presented if self._presented_keys.count(key) > 1
            )
            raise RuntimeError(
                f"native Astrill UI key coverage mismatch; "
                f"missing={missing!r}, repeated={repeated!r}"
            )
        self._sync_control_access()

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def presented_nvram_keys(self) -> tuple[str, ...]:
        return tuple(self._presented_keys)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        self._sync_control_access()

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only
        self._sync_control_access()

    def render(
        self,
        settings: NativeAstrillSettings,
        clients: list[dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        if clients is not None:
            self.clients = list(clients)
        self._loading = True
        try:
            self._set_route(self.site_default, settings.site_policy.default)
            self.site_list.setPlainText(settings.get("astrill_iplistraw"))
            self._set_route(self.device_default, settings.device_policy.default)

            wifi_mode = settings.integer("astrill_ifmode")
            self._set_route(
                self.wifi_default,
                RouteTarget.DIRECT if wifi_mode == 1 else RouteTarget.VPN,
            )
            self.wifi_exceptions.setText(
                settings.get("astrill_iflist")
                if wifi_mode == 1
                else settings.get("astrill_ifexlist")
                if wifi_mode == 2
                else ""
            )

            vlan_mode = settings.integer("astrill_vlanmode")
            self._set_route(
                self.vlan_default,
                RouteTarget.DIRECT if vlan_mode == 1 else RouteTarget.VPN,
            )
            self.vlan_exceptions.setText(
                settings.get("astrill_vlanlist") if vlan_mode in {1, 2} else ""
            )

            self._set_combo_value(
                self.dns_provider,
                DNS_OPTIONS,
                settings.get("astrill_dnsserver", "0"),
            )
            self._set_combo_value(
                self.cipher,
                CIPHER_OPTIONS,
                settings.get("astrill_cipher", "default"),
            )
            self._set_combo_value(
                self.site_source,
                SITE_SOURCE_OPTIONS,
                settings.get("astrill_iplistext", "0"),
            )
            self.mtu.setValue(settings.integer("astrill_wanmtu", 1446))

            for key, control in self._direct_controls.items():
                if isinstance(control, QCheckBox):
                    control.setChecked(settings.enabled(key))
                elif isinstance(control, QLineEdit):
                    control.setText(settings.get(key))

            self._render_devices()
            self._render_state(settings)
            self._render_summaries(settings)
        finally:
            self._loading = False
        self.mark_clean()

    def update_clients(self, clients: list[dict[str, Any]]) -> None:
        self.clients = list(clients)
        if self.settings is None or self._device_changed():
            return
        self._loading = True
        try:
            self._render_devices()
        finally:
            self._loading = False
        self._initial_device_state = self._device_state()
        self._sync_dirty()

    def collect_changes(self) -> dict[str, str]:
        if self.settings is None:
            raise ValueError("native Astrill settings have not loaded")

        changes: dict[str, str] = {}
        if self._site_changed():
            changes.update(
                site_policy_changes(
                    self._selected_route(self.site_default),
                    self.site_list.toPlainText(),
                )
            )
        if self._device_changed():
            default = self._selected_route(self.device_default)
            exceptions = [
                device
                for device, control in self._device_controls
                if self._selected_route(control) is not default
            ]
            changes.update(device_policy_changes(default, exceptions))
        if self._wifi_state() != self._initial_wifi_state:
            default = self._selected_route(self.wifi_default)
            exceptions = self._interface_list(self.wifi_exceptions.text())
            changes["astrill_ifmode"] = binary_native_mode(
                default, has_exceptions=bool(exceptions)
            )
            active_key = (
                "astrill_iflist"
                if default is RouteTarget.DIRECT
                else "astrill_ifexlist"
            )
            changes[active_key] = exceptions
        if self._vlan_state() != self._initial_vlan_state:
            default = self._selected_route(self.vlan_default)
            exceptions = self._interface_list(self.vlan_exceptions.text())
            changes["astrill_vlanmode"] = binary_native_mode(
                default, has_exceptions=bool(exceptions)
            )
            changes["astrill_vlanlist"] = exceptions

        for key, control in self._direct_controls.items():
            current = self._control_value(key, control)
            if current != self._initial_direct_values.get(key):
                changes[key] = current

        normalized = normalize_native_changes(changes)
        return {
            key: value
            for key, value in normalized.items()
            if value != self.settings.get(key)
        }

    def mark_clean(self) -> None:
        self._initial_site_state = self._site_state()
        self._initial_device_state = self._device_state()
        self._initial_wifi_state = self._wifi_state()
        self._initial_vlan_state = self._vlan_state()
        self._initial_direct_values = {
            key: self._control_value(key, control)
            for key, control in self._direct_controls.items()
        }
        self._dirty = False
        self._sync_control_access()

    def _request_refresh(self) -> None:
        if self._dirty:
            answer = QMessageBox.warning(
                self,
                "Reload native Astrill settings",
                "Reload from DD-WRT and discard the unsaved changes on this page?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._on_refresh()

    def _build_state(self, root: QVBoxLayout) -> None:
        group = QGroupBox("Current endpoint and connection (read only)")
        form = self._form(group)
        fields = (
            (
                "astrill_status",
                "Connection state",
                "Astrill's native connection status.",
            ),
            (
                "astrill_serverid",
                "Endpoint server ID",
                "The configured Astrill server.",
            ),
            ("astrill_sid", "Endpoint node ID", "The selected server node."),
            (
                "astrill_protocol",
                "Protocol",
                "The protocol used by the shared router tunnel.",
            ),
            (
                "astrill_ip",
                "Encoded server address",
                "Astrill's internal endpoint address value.",
            ),
            (
                "astrill_port",
                "Endpoint port or range",
                "Configured endpoint port value or port range.",
            ),
            (
                "astrill_portindex",
                "Port profile",
                "Astrill's endpoint port-profile index.",
            ),
            (
                "astrill_vpnmode",
                "VPN mode",
                "Astrill's internal endpoint mode.",
            ),
            (
                "astrill_favlist",
                "Favorite endpoints",
                "Saved native endpoint choices.",
            ),
        )
        for key, title, description in fields:
            value = QLabel("Not loaded")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setProperty("class", "nativeReadOnly")
            self._state_labels[key] = value
            form.addRow(self._field_label(title, description, key), value)
        root.addWidget(group)

    def _build_routing(self, root: QVBoxLayout) -> None:
        websites = QGroupBox("Website routing")
        form = self._form(websites)
        self.site_default = self._route_combo()
        form.addRow(
            self._field_label(
                "Default website route",
                "Route used for websites outside the native list.",
                "astrill_routingmode",
            ),
            self.site_default,
        )
        self.site_list_caption = self._field_label(
            "Website route exceptions",
            "One domain, IPv4 address, network, or range per line.",
            "astrill_iplistraw",
        )
        self.site_list = QPlainTextEdit()
        self.site_list.setPlaceholderText(
            "example.com\n192.0.2.0/24\n198.51.100.10 - 198.51.100.20"
        )
        self.site_list.setMinimumHeight(150)
        form.addRow(self.site_list_caption, self.site_list)
        self.compiled_sites = QLabel("Not loaded")
        self.compiled_sites.setProperty("class", "nativeReadOnly")
        form.addRow(
            self._field_label(
                "Compiled IPv4 routes",
                "Generated automatically from the website list.",
                "astrill_iplist",
            ),
            self.compiled_sites,
        )
        root.addWidget(websites)

        devices = QGroupBox("Device routing")
        form = self._form(devices)
        self.device_default = self._route_combo()
        form.addRow(
            self._field_label(
                "Default device route",
                "Route used for LAN devices outside the native list.",
                "astrill_devmode",
            ),
            self.device_default,
        )
        self.device_table = QTableWidget(0, 3)
        self.device_table.setHorizontalHeaderLabels(["Device", "Address", "Route"])
        self.device_table.setAlternatingRowColors(True)
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.device_table.setMinimumHeight(220)
        self.device_table.verticalHeader().setMinimumSectionSize(40)
        self.device_table.verticalHeader().setDefaultSectionSize(42)
        self.device_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.device_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.device_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        form.addRow(
            self._field_label(
                "Per-device route exceptions",
                "Known native records and observed DD-WRT clients.",
                "astrill_devices",
            ),
            self.device_table,
        )
        root.addWidget(devices)

        interfaces = QGroupBox("Router interfaces")
        form = self._form(interfaces)
        self.wifi_default = self._route_combo()
        form.addRow(
            self._field_label(
                "Default Wi-Fi route",
                "Route used by native wireless interfaces.",
                "astrill_ifmode",
            ),
            self.wifi_default,
        )
        self.wifi_exceptions = QLineEdit()
        self.wifi_exceptions.setPlaceholderText("wl0;wl1")
        form.addRow(
            self._field_label(
                "Wi-Fi route overrides",
                "Semicolon-separated DD-WRT interface names.",
                "astrill_iflist",
                "astrill_ifexlist",
            ),
            self.wifi_exceptions,
        )
        self.vlan_default = self._route_combo()
        form.addRow(
            self._field_label(
                "Default VLAN route",
                "Route used by native VLAN interfaces.",
                "astrill_vlanmode",
            ),
            self.vlan_default,
        )
        self.vlan_exceptions = QLineEdit()
        self.vlan_exceptions.setPlaceholderText("vlan2;vlan3")
        form.addRow(
            self._field_label(
                "VLAN route overrides",
                "Semicolon-separated DD-WRT VLAN interfaces.",
                "astrill_vlanlist",
            ),
            self.vlan_exceptions,
        )
        self.dmz_device = QLineEdit()
        form.addRow(
            self._field_label(
                "DMZ device",
                "Native Astrill device record used for the DMZ.",
                "astrill_dmzdevice",
            ),
            self.dmz_device,
        )
        self._direct_controls["astrill_dmzdevice"] = self.dmz_device
        root.addWidget(interfaces)

        self._write_controls.extend(
            [
                self.site_default,
                self.site_list,
                self.device_default,
                self.wifi_default,
                self.wifi_exceptions,
                self.vlan_default,
                self.vlan_exceptions,
                self.dmz_device,
            ]
        )
        self.site_default.currentIndexChanged.connect(self._changed)
        self.site_list.textChanged.connect(self._changed)
        self.device_default.currentIndexChanged.connect(self._changed)
        self.wifi_default.currentIndexChanged.connect(self._changed)
        self.wifi_exceptions.textChanged.connect(self._changed)
        self.vlan_default.currentIndexChanged.connect(self._changed)
        self.vlan_exceptions.textChanged.connect(self._changed)
        self.dmz_device.textChanged.connect(self._changed)

    def _build_dns(self, root: QVBoxLayout) -> None:
        group = QGroupBox("DNS")
        form = self._form(group)
        self.dns_provider = QComboBox()
        self._fill_combo(self.dns_provider, DNS_OPTIONS)
        form.addRow(
            self._field_label(
                "DNS provider",
                "Resolver selection used by native Astrill.",
                "astrill_dnsserver",
            ),
            self.dns_provider,
        )
        self.user_dns = QLineEdit()
        self.user_dns.setPlaceholderText("Up to two IPv4 addresses")
        form.addRow(
            self._field_label(
                "Custom DNS servers",
                "Used only when User defined is selected.",
                "astrill_userdns",
            ),
            self.user_dns,
        )
        self._direct_controls.update(
            {
                "astrill_dnsserver": self.dns_provider,
                "astrill_userdns": self.user_dns,
            }
        )
        for key, title, description in (
            ("astrill_adsblock", "Block ads", "Use Astrill's DNS filtering."),
            (
                "astrill_nosplitdns",
                "Disable split DNS",
                "Use one DNS route instead of split resolution.",
            ),
            (
                "astrill_vpndnsallsites",
                "VPN DNS for all websites",
                "Resolve both Direct and Astrill traffic through VPN DNS.",
            ),
        ):
            control = QCheckBox("Enabled")
            form.addRow(self._field_label(title, description, key), control)
            self._direct_controls[key] = control
        root.addWidget(group)
        self._connect_direct_controls()

    def _build_connection(self, root: QVBoxLayout) -> None:
        group = QGroupBox("Connection behavior")
        form = self._form(group)
        self.cipher = QComboBox()
        self._fill_combo(self.cipher, CIPHER_OPTIONS)
        form.addRow(
            self._field_label(
                "Encryption",
                "Cipher used by the native tunnel.",
                "astrill_cipher",
            ),
            self.cipher,
        )
        self.mtu = QSpinBox()
        self.mtu.setRange(576, 1500)
        self.mtu.setSuffix(" bytes")
        form.addRow(
            self._field_label(
                "Internet MTU",
                "Packet size accepted by Astrill (576 to 1500).",
                "astrill_wanmtu",
            ),
            self.mtu,
        )
        self._direct_controls.update(
            {"astrill_cipher": self.cipher, "astrill_wanmtu": self.mtu}
        )
        for key, title, description in (
            (
                "astrill_accel",
                "Hardware acceleration",
                "Use Astrill's native fast path.",
            ),
            (
                "astrill_blockinternet",
                "Block Internet when disconnected",
                "Native kill switch for Astrill-routed traffic.",
            ),
            (
                "astrill_autocycle",
                "Cycle favorite endpoints",
                "Reconnect through the saved endpoint list.",
            ),
            (
                "astrill_autostart",
                "Connect after router boot",
                "Start the native Astrill tunnel after DD-WRT boots.",
            ),
        ):
            control = QCheckBox("Enabled")
            form.addRow(self._field_label(title, description, key), control)
            self._direct_controls[key] = control
        root.addWidget(group)
        self._connect_direct_controls()

    def _build_advanced(self, root: QVBoxLayout) -> None:
        group = QGroupBox("Advanced website filters")
        form = self._form(group)
        self.site_source = QComboBox()
        self._fill_combo(self.site_source, SITE_SOURCE_OPTIONS)
        form.addRow(
            self._field_label(
                "Website list source",
                "Use the inline list above or a file on the router.",
                "astrill_iplistext",
            ),
            self.site_source,
        )
        self.site_file = QLineEdit()
        self.site_file.setPlaceholderText("Router path")
        form.addRow(
            self._field_label(
                "Website list file",
                "Path used when Router file is selected.",
                "astrill_iplistfile",
            ),
            self.site_file,
        )
        self.external_filter = QLineEdit()
        form.addRow(
            self._field_label(
                "External filter",
                "Native Astrill external-filter value.",
                "astrill_exflt",
            ),
            self.external_filter,
        )
        self._direct_controls.update(
            {
                "astrill_iplistext": self.site_source,
                "astrill_iplistfile": self.site_file,
                "astrill_exflt": self.external_filter,
            }
        )
        root.addWidget(group)
        self._connect_direct_controls()

    def _connect_direct_controls(self) -> None:
        connected = set(getattr(self, "_connected_direct_keys", set()))
        for key, control in self._direct_controls.items():
            if key in connected:
                continue
            if isinstance(control, QCheckBox):
                control.toggled.connect(self._changed)
            elif isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._changed)
            elif isinstance(control, QSpinBox):
                control.valueChanged.connect(self._changed)
            elif isinstance(control, QLineEdit):
                control.textChanged.connect(self._changed)
            connected.add(key)
            self._write_controls.append(control)
        self._connected_direct_keys = connected

    def _render_devices(self) -> None:
        assert self.settings is not None
        self.device_table.setRowCount(0)
        self._device_controls = []
        self._device_parse_error = ""
        try:
            selected = {device.mac: device for device in self.settings.devices}
        except ValueError as exc:
            selected = {}
            self._device_parse_error = str(exc)

        devices = dict(selected)
        for client in self.clients:
            mac = str(client.get("mac", "")).upper()
            address = str(client.get("address", ""))
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
                continue
            try:
                ipaddress.IPv4Address(address)
            except ValueError:
                continue
            raw_name = str(client.get("hostname", "")).strip()
            name = "Unknown device" if raw_name in {"", "*"} else raw_name
            name = re.sub(r"[;/\x00]", " ", name).strip() or "Unknown device"
            devices[mac] = NativeDevice(mac, address, name)

        if self._device_parse_error:
            self.device_table.setRowCount(1)
            item = QTableWidgetItem(
                "Native device records could not be parsed; they are preserved."
            )
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(self._device_parse_error)
            self.device_table.setItem(0, 0, item)
            self.device_table.setSpan(0, 0, 1, 3)
            return

        policy = self.settings.device_policy
        for device in sorted(
            devices.values(), key=lambda item: (item.address, item.mac)
        ):
            row = self.device_table.rowCount()
            self.device_table.insertRow(row)
            name = QTableWidgetItem(device.name)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name.setToolTip(device.mac)
            address = QTableWidgetItem(device.address)
            address.setFlags(address.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.device_table.setItem(row, 0, name)
            self.device_table.setItem(row, 1, address)
            route = self._route_combo()
            target = policy.exception if device.mac in selected else policy.default
            self._set_route(route, target)
            route.currentIndexChanged.connect(self._changed)
            self.device_table.setCellWidget(row, 2, route)
            self._device_controls.append((device, route))
        self._sync_control_access()

    def _render_state(self, settings: NativeAstrillSettings) -> None:
        for key, label in self._state_labels.items():
            value = settings.get(key)
            if key == "astrill_status":
                text = {
                    "0": "Disconnected (status 0)",
                    "3": "Connected (status 3)",
                }.get(value, f"Native status {value or 'not reported'}")
            elif key == "astrill_protocol":
                try:
                    protocol = int(value)
                except ValueError:
                    text = value or "Not reported"
                else:
                    text = (
                        f"{ASTRILL_PROTOCOL_NAMES[protocol]} (mode {protocol})"
                        if 0 <= protocol < len(ASTRILL_PROTOCOL_NAMES)
                        else f"Protocol mode {protocol}"
                    )
            elif key == "astrill_favlist":
                favorites = [item for item in value.split(",") if item]
                text = (
                    f"{len(favorites)} saved endpoint"
                    f"{'' if len(favorites) == 1 else 's'}"
                )
            else:
                text = value or "Not reported"
            label.setText(text)
            label.setToolTip(f"NVRAM {key}")

    def _render_summaries(self, settings: NativeAstrillSettings) -> None:
        compiled_count = len(settings.get("astrill_iplist").split())
        self.compiled_sites.setText(
            f"{compiled_count} generated IPv4 network"
            f"{'' if compiled_count == 1 else 's'}"
        )
        server = settings.get("astrill_serverid") or "none"
        protocol = settings.get("astrill_protocol") or "not reported"
        self.summary.setText(
            f"Loaded from DD-WRT · endpoint server {server} · protocol {protocol}. "
            "Only changed controls will be validated, committed once, and read back."
        )

    def _sync_control_access(self) -> None:
        editable = self.settings is not None and not self._read_only and not self._busy
        for control in self._write_controls:
            control.setEnabled(editable)
        device_editable = editable and not self._device_parse_error
        for _device, control in self._device_controls:
            control.setEnabled(device_editable)
        self.device_default.setEnabled(device_editable)
        self.save_button.setEnabled(editable and self._dirty)
        if self._read_only:
            self.save_button.setToolTip(
                "Turn off the read-only safety guard in Settings before saving."
            )
        elif not self._dirty:
            self.save_button.setToolTip("No native Astrill settings have changed.")
        else:
            self.save_button.setToolTip("Validate and save only changed settings.")

    def _changed(self, *_args: object) -> None:
        if self._loading or self.settings is None:
            return
        self._sync_dirty()

    def _sync_dirty(self) -> None:
        if self.settings is None:
            self._dirty = False
        else:
            try:
                self._dirty = bool(self.collect_changes())
            except ValueError:
                # Keep invalid edits visibly dirty so Save can explain the
                # validation error instead of silently treating them as clean.
                self._dirty = True
        self._sync_control_access()

    def _site_state(self) -> tuple[RouteTarget, str]:
        return self._selected_route(self.site_default), self.site_list.toPlainText()

    def _site_changed(self) -> bool:
        current_route, current_text = self._site_state()
        initial_route, initial_text = self._initial_site_state
        if current_route is not initial_route:
            return True
        if current_text == initial_text:
            return False
        try:
            return normalize_site_entries(current_text) != normalize_site_entries(
                initial_text
            )
        except ValueError:
            # Preserve an invalid router value if it is untouched, but keep a
            # newly invalid edit dirty so Save can report the validation error.
            return True

    def _device_state(self) -> tuple[RouteTarget, tuple[tuple[str, RouteTarget], ...]]:
        routes = tuple(
            sorted(
                (
                    (device.mac, self._selected_route(control))
                    for device, control in self._device_controls
                ),
                key=lambda item: item[0],
            )
        )
        return self._selected_route(self.device_default), routes

    def _device_changed(self) -> bool:
        return self._device_state() != self._initial_device_state

    def _wifi_state(self) -> tuple[RouteTarget, str]:
        return (
            self._selected_route(self.wifi_default),
            self._interface_list(self.wifi_exceptions.text()),
        )

    def _vlan_state(self) -> tuple[RouteTarget, str]:
        return (
            self._selected_route(self.vlan_default),
            self._interface_list(self.vlan_exceptions.text()),
        )

    @staticmethod
    def _interface_list(value: str) -> str:
        return ";".join(item.strip() for item in value.split(";") if item.strip())

    @staticmethod
    def _selected_route(control: QComboBox) -> RouteTarget:
        return (
            RouteTarget.DIRECT if control.currentData() == "direct" else RouteTarget.VPN
        )

    @staticmethod
    def _set_route(control: QComboBox, target: RouteTarget) -> None:
        value = "direct" if target is RouteTarget.DIRECT else "vpn"
        index = control.findData(value)
        control.setCurrentIndex(max(index, 0))

    @staticmethod
    def _route_combo() -> QComboBox:
        control = QComboBox()
        WindowsNativeSettingsPage._fill_combo(control, ROUTE_OPTIONS)
        control.setMinimumWidth(180)
        return control

    @staticmethod
    def _fill_combo(control: QComboBox, options: tuple[tuple[str, str], ...]) -> None:
        control.clear()
        for value, label in options:
            control.addItem(label, value)

    @classmethod
    def _set_combo_value(
        cls,
        control: QComboBox,
        options: tuple[tuple[str, str], ...],
        value: str,
    ) -> None:
        cls._fill_combo(control, options)
        index = control.findData(value)
        if index < 0:
            control.addItem(f"Unsupported current value ({value or 'empty'})", value)
            index = control.count() - 1
        control.setCurrentIndex(index)

    @staticmethod
    def _control_value(key: str, control: QWidget) -> str:
        if isinstance(control, QCheckBox):
            return "1" if control.isChecked() else "0"
        if isinstance(control, QComboBox):
            return str(control.currentData())
        if isinstance(control, QSpinBox):
            return str(control.value())
        if isinstance(control, QLineEdit):
            value = control.text().strip()
            if key == "astrill_userdns":
                return " ".join(value.split())
            return value
        raise TypeError(f"unsupported native Astrill control for {key}")

    def _field_label(
        self,
        title: str,
        description: str,
        *keys: str,
    ) -> QWidget:
        self._presented_keys.extend(keys)
        value = QWidget()
        layout = QVBoxLayout(value)
        layout.setContentsMargins(0, 3, 18, 3)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setProperty("class", "nativeFieldTitle")
        layout.addWidget(heading)
        detail = QLabel(description)
        detail.setWordWrap(True)
        detail.setProperty("class", "nativeFieldDescription")
        layout.addWidget(detail)
        key_label = QLabel("NVRAM · " + " / ".join(keys))
        key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        key_label.setProperty("class", "nativeKey")
        layout.addWidget(key_label)
        return value

    @staticmethod
    def _form(parent: QGroupBox) -> QFormLayout:
        form = QFormLayout(parent)
        form.setContentsMargins(18, 22, 18, 18)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(13)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        return form
