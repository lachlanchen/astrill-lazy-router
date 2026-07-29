from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .models import RouteTarget
from .native_settings import (
    EffectivePolicy,
    NativeAstrillSettings,
    NativeDevice,
    binary_native_mode,
    device_policy_changes,
    site_policy_changes,
)

DNS_OPTIONS = (
    ("0", "Astrill DNS"),
    ("1", "Google DNS"),
    ("2", "OpenDNS"),
    ("3", "Cloudflare"),
    ("7", "DNS Advantage"),
    ("8", "Comodo DNS"),
    ("9", "Level3 DNS"),
    ("254", "User defined"),
    ("255", "Unchanged"),
)

CIPHER_OPTIONS = (
    ("default", "Default"),
    ("AES-128-CBC", "AES 128-bit"),
    ("AES-256-CBC", "AES 256-bit"),
    ("none", "Disabled"),
)


class NativeSettingsPage(Gtk.Box):
    def __init__(
        self,
        *,
        on_refresh: Callable[[], None],
        on_save: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page-content")
        self.settings: NativeAstrillSettings | None = None
        self.clients: list[dict[str, Any]] = []
        self._loading = False
        self._site_dirty = False
        self._device_dirty = False
        self._wifi_dirty = False
        self._vlan_dirty = False
        self._dirty = False
        self._device_controls: list[tuple[NativeDevice, Gtk.DropDown]] = []

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Native Astrill")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.add_css_class("section-title")
        heading.append(title)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Reload native Astrill settings")
        refresh.connect("clicked", lambda _button: on_refresh())
        heading.append(refresh)
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.set_sensitive(False)
        self.save_button.connect("clicked", lambda _button: on_save())
        heading.append(self.save_button)
        self.append(heading)

        self._build_routing()
        self._build_dns()
        self._build_connection()
        self._build_advanced()

    @property
    def dirty(self) -> bool:
        return self._dirty

    def set_busy(self, busy: bool) -> None:
        self.save_button.set_sensitive(self._dirty and not busy)

    def render(
        self,
        settings: NativeAstrillSettings,
        clients: list[dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        if clients is not None:
            self.clients = clients
        self._loading = True
        try:
            self.site_default.set_selected(_route_index(settings.site_policy.default))
            self.site_buffer.set_text(settings.get("astrill_iplistraw"))
            self._update_site_title(settings.site_policy)

            self.device_default.set_selected(
                _route_index(settings.device_policy.default)
            )
            self._render_devices()

            wifi_policy = _filter_policy(settings.integer("astrill_ifmode"))
            self.wifi_default.set_selected(_route_index(wifi_policy.default))
            self.wifi_exceptions.set_text(
                settings.get("astrill_iflist")
                if settings.integer("astrill_ifmode") == 1
                else (
                    settings.get("astrill_ifexlist")
                    if settings.integer("astrill_ifmode") == 2
                    else ""
                )
            )

            vlan_policy = _filter_policy(settings.integer("astrill_vlanmode"))
            self.vlan_default.set_selected(_route_index(vlan_policy.default))
            self.vlan_exceptions.set_text(
                settings.get("astrill_vlanlist")
                if settings.integer("astrill_vlanmode") in {1, 2}
                else ""
            )

            self.dmz_device.set_text(settings.get("astrill_dmzdevice"))
            self.dns_provider.set_selected(
                _option_index(DNS_OPTIONS, settings.get("astrill_dnsserver", "0"))
            )
            self.user_dns.set_text(settings.get("astrill_userdns"))
            self._set_switches(settings)
            self.cipher.set_selected(
                _option_index(CIPHER_OPTIONS, settings.get("astrill_cipher", "default"))
            )
            self.mtu.set_value(settings.integer("astrill_wanmtu", 1446))
            self.site_source.set_selected(
                1 if settings.get("astrill_iplistext") == "1" else 0
            )
            self.site_file.set_text(settings.get("astrill_iplistfile"))
            self.external_filter.set_text(settings.get("astrill_exflt"))
            self.endpoint_row.set_subtitle(
                f"Server {settings.get('astrill_serverid') or 'none'} · "
                f"protocol {settings.get('astrill_protocol') or '0'}"
            )
            favorites = [
                value for value in settings.get("astrill_favlist").split(",") if value
            ]
            self.favorites_row.set_subtitle(
                f"{len(favorites)} saved endpoint{'' if len(favorites) == 1 else 's'}"
            )
            compiled = settings.get("astrill_iplist").split()
            self.compiled_sites_row.set_subtitle(
                f"{len(compiled)} generated IPv4 network"
                f"{'' if len(compiled) == 1 else 's'}"
            )
        finally:
            self._loading = False
        self.mark_clean()

    def update_clients(self, clients: list[dict[str, Any]]) -> None:
        self.clients = clients
        if self.settings is not None and not self._device_dirty:
            self._loading = True
            try:
                self._render_devices()
            finally:
                self._loading = False

    def collect_changes(self) -> dict[str, str]:
        if self.settings is None:
            raise ValueError("native Astrill settings have not loaded")
        settings = self.settings
        changes: dict[str, str] = {}

        if self._site_dirty:
            start, end = self.site_buffer.get_bounds()
            raw = self.site_buffer.get_text(start, end, False)
            changes.update(site_policy_changes(_selected_route(self.site_default), raw))

        if self._device_dirty:
            default = _selected_route(self.device_default)
            exceptions = [
                device
                for device, control in self._device_controls
                if _selected_route(control) is not default
            ]
            changes.update(device_policy_changes(default, exceptions))

        if self._wifi_dirty:
            default = _selected_route(self.wifi_default)
            exceptions = _normalize_interface_list(self.wifi_exceptions.get_text())
            changes["astrill_ifmode"] = binary_native_mode(
                default, has_exceptions=bool(exceptions)
            )
            active_key = (
                "astrill_iflist"
                if default is RouteTarget.DIRECT
                else "astrill_ifexlist"
            )
            changes[active_key] = exceptions

        if self._vlan_dirty:
            default = _selected_route(self.vlan_default)
            exceptions = _normalize_interface_list(self.vlan_exceptions.get_text())
            changes["astrill_vlanmode"] = binary_native_mode(
                default, has_exceptions=bool(exceptions)
            )
            changes["astrill_vlanlist"] = exceptions

        direct_values = {
            "astrill_dmzdevice": self.dmz_device.get_text().strip(),
            "astrill_dnsserver": DNS_OPTIONS[self.dns_provider.get_selected()][0],
            "astrill_userdns": " ".join(self.user_dns.get_text().split()),
            "astrill_cipher": CIPHER_OPTIONS[self.cipher.get_selected()][0],
            "astrill_wanmtu": str(self.mtu.get_value_as_int()),
            "astrill_iplistext": str(self.site_source.get_selected()),
            "astrill_iplistfile": self.site_file.get_text().strip(),
            "astrill_exflt": self.external_filter.get_text().strip(),
        }
        direct_values.update(
            {
                key: "1" if switch.get_active() else "0"
                for key, switch in self.switches.items()
            }
        )
        for key, value in direct_values.items():
            if settings.get(key) != value:
                changes[key] = value
        return changes

    def mark_clean(self) -> None:
        self._site_dirty = False
        self._device_dirty = False
        self._wifi_dirty = False
        self._vlan_dirty = False
        self._dirty = False
        self.save_button.set_sensitive(False)

    def _build_routing(self) -> None:
        self.append(_section_heading("Routing"))
        routing = _settings_list()
        self.site_default = _route_dropdown()
        site_default_row = _control_row(
            "Website default", "Effective route for websites outside the native list"
        )
        site_default_row.add_suffix(self.site_default)
        routing.append(site_default_row)

        self.device_default = _route_dropdown()
        device_default_row = _control_row(
            "Device default", "Effective route for devices outside the native list"
        )
        device_default_row.add_suffix(self.device_default)
        routing.append(device_default_row)
        self.append(routing)

        self.site_list_title = Gtk.Label(label="Website routes")
        self.site_list_title.set_xalign(0)
        self.site_list_title.add_css_class("section-title")
        self.site_list_title.add_css_class("toolbar-section")
        self.append(self.site_list_title)
        self.site_buffer = Gtk.TextBuffer()
        self.site_view = Gtk.TextView(buffer=self.site_buffer)
        self.site_view.set_monospace(True)
        self.site_view.set_wrap_mode(Gtk.WrapMode.NONE)
        site_scroll = Gtk.ScrolledWindow()
        site_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        site_scroll.set_min_content_height(180)
        site_scroll.set_child(self.site_view)
        site_scroll.add_css_class("catalog-list")
        self.append(site_scroll)

        device_heading = Gtk.Label(label="Device Routes")
        device_heading.set_xalign(0)
        device_heading.add_css_class("section-title")
        device_heading.add_css_class("toolbar-section")
        self.append(device_heading)
        self.device_list = _settings_list()
        self.append(self.device_list)

        network_heading = Gtk.Label(label="Router Interfaces")
        network_heading.set_xalign(0)
        network_heading.add_css_class("section-title")
        network_heading.add_css_class("toolbar-section")
        self.append(network_heading)
        network_list = _settings_list()
        self.wifi_default = _route_dropdown()
        wifi_row = _control_row("Wi-Fi default", "Native radio route")
        wifi_row.add_suffix(self.wifi_default)
        network_list.append(wifi_row)
        self.wifi_exceptions = Gtk.Entry()
        self.wifi_exceptions.set_width_chars(26)
        wifi_exception_row = _control_row("Wi-Fi route overrides", "Interface names")
        wifi_exception_row.add_suffix(self.wifi_exceptions)
        network_list.append(wifi_exception_row)
        self.vlan_default = _route_dropdown()
        vlan_row = _control_row("VLAN default", "Native VLAN route")
        vlan_row.add_suffix(self.vlan_default)
        network_list.append(vlan_row)
        self.vlan_exceptions = Gtk.Entry()
        self.vlan_exceptions.set_width_chars(26)
        vlan_exception_row = _control_row("VLAN route overrides", "Interface names")
        vlan_exception_row.add_suffix(self.vlan_exceptions)
        network_list.append(vlan_exception_row)
        self.dmz_device = Gtk.Entry()
        self.dmz_device.set_width_chars(26)
        dmz_row = _control_row("DMZ device", "Native Astrill device record")
        dmz_row.add_suffix(self.dmz_device)
        network_list.append(dmz_row)
        self.append(network_list)

        self.site_default.connect(
            "notify::selected", lambda *_args: self._mark_site_dirty()
        )
        self.device_default.connect(
            "notify::selected", lambda *_args: self._mark_device_dirty()
        )
        self.site_buffer.connect("changed", lambda *_args: self._mark_site_dirty())
        self.wifi_default.connect(
            "notify::selected", lambda *_args: self._mark_wifi_dirty()
        )
        self.wifi_exceptions.connect("changed", lambda *_args: self._mark_wifi_dirty())
        self.vlan_default.connect(
            "notify::selected", lambda *_args: self._mark_vlan_dirty()
        )
        self.vlan_exceptions.connect("changed", lambda *_args: self._mark_vlan_dirty())
        self.dmz_device.connect("changed", lambda *_args: self._mark_dirty())

    def _build_dns(self) -> None:
        self.append(_section_heading("DNS"))
        dns_list = _settings_list()
        self.dns_provider = Gtk.DropDown.new_from_strings(
            [label for _value, label in DNS_OPTIONS]
        )
        dns_row = _control_row("DNS provider", "Native Astrill DNS selection")
        dns_row.add_suffix(self.dns_provider)
        dns_list.append(dns_row)
        self.user_dns = Gtk.Entry()
        self.user_dns.set_width_chars(26)
        user_dns_row = _control_row("User DNS", "Up to two IPv4 addresses")
        user_dns_row.add_suffix(self.user_dns)
        dns_list.append(user_dns_row)
        self.switches: dict[str, Gtk.Switch] = {}
        for key, title, subtitle in (
            ("astrill_adsblock", "Block ads", "Astrill DNS filtering"),
            ("astrill_nosplitdns", "Disable split DNS", "Use one DNS route"),
            (
                "astrill_vpndnsallsites",
                "VPN DNS for all websites",
                "Resolve Direct and Astrill traffic through VPN DNS",
            ),
        ):
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            row = _control_row(title, subtitle)
            row.add_suffix(switch)
            dns_list.append(row)
            self.switches[key] = switch
        self.append(dns_list)
        self.dns_provider.connect("notify::selected", lambda *_args: self._mark_dirty())
        self.user_dns.connect("changed", lambda *_args: self._mark_dirty())
        for switch in self.switches.values():
            switch.connect("notify::active", lambda *_args: self._mark_dirty())

    def _build_connection(self) -> None:
        self.append(_section_heading("Connection"))
        connection_list = _settings_list()
        self.cipher = Gtk.DropDown.new_from_strings(
            [label for _value, label in CIPHER_OPTIONS]
        )
        cipher_row = _control_row("Encryption", "Native tunnel cipher")
        cipher_row.add_suffix(self.cipher)
        connection_list.append(cipher_row)
        self.mtu = Gtk.SpinButton.new_with_range(576, 1500, 1)
        self.mtu.set_valign(Gtk.Align.CENTER)
        mtu_row = _control_row("Internet MTU", "576 to 1500")
        mtu_row.add_suffix(self.mtu)
        connection_list.append(mtu_row)
        for key, title, subtitle in (
            ("astrill_accel", "Hardware acceleration", "Native fast path"),
            (
                "astrill_blockinternet",
                "Block Internet when disconnected",
                "Native kill switch",
            ),
            (
                "astrill_autocycle",
                "Cycle favorite endpoints",
                "Reconnect through saved endpoints",
            ),
            ("astrill_autostart", "Connect after router boot", "Native startup"),
        ):
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            row = _control_row(title, subtitle)
            row.add_suffix(switch)
            connection_list.append(row)
            self.switches[key] = switch
            switch.connect("notify::active", lambda *_args: self._mark_dirty())
        self.endpoint_row = _control_row("Endpoint", "Not loaded")
        connection_list.append(self.endpoint_row)
        self.favorites_row = _control_row("Favorites", "Not loaded")
        connection_list.append(self.favorites_row)
        self.append(connection_list)
        self.cipher.connect("notify::selected", lambda *_args: self._mark_dirty())
        self.mtu.connect("value-changed", lambda *_args: self._mark_dirty())

    def _build_advanced(self) -> None:
        self.append(_section_heading("Advanced"))
        advanced = _settings_list()
        self.site_source = Gtk.DropDown.new_from_strings(
            ["Inline website list", "Router file"]
        )
        source_row = _control_row("Website source", "Native site filter source")
        source_row.add_suffix(self.site_source)
        advanced.append(source_row)
        self.site_file = Gtk.Entry()
        self.site_file.set_width_chars(26)
        file_row = _control_row("Website list file", "Router path")
        file_row.add_suffix(self.site_file)
        advanced.append(file_row)
        self.external_filter = Gtk.Entry()
        self.external_filter.set_width_chars(26)
        filter_row = _control_row("External filter", "Native filter value")
        filter_row.add_suffix(self.external_filter)
        advanced.append(filter_row)
        self.compiled_sites_row = _control_row("Compiled IPv4 list", "Not loaded")
        advanced.append(self.compiled_sites_row)
        self.append(advanced)
        self.site_source.connect("notify::selected", lambda *_args: self._mark_dirty())
        self.site_file.connect("changed", lambda *_args: self._mark_dirty())
        self.external_filter.connect("changed", lambda *_args: self._mark_dirty())

    def _set_switches(self, settings: NativeAstrillSettings) -> None:
        for key, switch in self.switches.items():
            switch.set_active(settings.enabled(key))

    def _render_devices(self) -> None:
        if self.settings is None:
            return
        _clear_list(self.device_list)
        selected = {device.mac: device for device in self.settings.devices}
        devices = dict(selected)
        for client in self.clients:
            mac = str(client.get("mac", "")).upper()
            address = str(client.get("address", ""))
            if not _valid_mac(mac) or not address:
                continue
            raw_name = str(client.get("hostname", "")).strip()
            name = "Unknown device" if raw_name in {"", "*"} else raw_name
            name = re.sub(r"[;/\x00]", " ", name).strip() or "Unknown device"
            devices[mac] = NativeDevice(mac, address, name)

        self._device_controls = []
        if not devices:
            self.device_list.append(
                _empty_row("No devices", "No native device records")
            )
            return
        policy = self.settings.device_policy
        for device in sorted(
            devices.values(), key=lambda item: (item.address, item.mac)
        ):
            row = _control_row(device.name, f"{device.address} · {device.mac}")
            control = _route_dropdown()
            target = policy.exception if device.mac in selected else policy.default
            control.set_selected(_route_index(target))
            control.connect(
                "notify::selected", lambda *_args: self._mark_device_dirty()
            )
            row.add_suffix(control)
            self.device_list.append(row)
            self._device_controls.append((device, control))

    def _update_site_title(self, policy: EffectivePolicy) -> None:
        if (
            self.settings is not None
            and self.settings.integer("astrill_routingmode") == 0
        ):
            title = "Stored Website List"
        elif policy.automatic_mode is not None:
            title = "Native Automatic Website List"
        else:
            target = "Direct" if policy.exception is RouteTarget.DIRECT else "Astrill"
            title = f"Websites Routed {target}"
        self.site_list_title.set_label(title)

    def _mark_site_dirty(self) -> None:
        if self._loading:
            return
        self._site_dirty = True
        self._mark_dirty()

    def _mark_device_dirty(self) -> None:
        if self._loading:
            return
        self._device_dirty = True
        self._mark_dirty()

    def _mark_wifi_dirty(self) -> None:
        if self._loading:
            return
        self._wifi_dirty = True
        self._mark_dirty()

    def _mark_vlan_dirty(self) -> None:
        if self._loading:
            return
        self._vlan_dirty = True
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self.save_button.set_sensitive(True)


def _route_dropdown() -> Gtk.DropDown:
    control = Gtk.DropDown.new_from_strings(["Direct", "Astrill"])
    control.set_size_request(118, -1)
    control.set_valign(Gtk.Align.CENTER)
    return control


def _route_index(target: RouteTarget) -> int:
    return 0 if target is RouteTarget.DIRECT else 1


def _selected_route(control: Gtk.DropDown) -> RouteTarget:
    return RouteTarget.DIRECT if control.get_selected() == 0 else RouteTarget.VPN


def _filter_policy(mode: int) -> EffectivePolicy:
    if mode == 1:
        return EffectivePolicy(RouteTarget.DIRECT, RouteTarget.VPN)
    return EffectivePolicy(RouteTarget.VPN, RouteTarget.DIRECT)


def _option_index(options: tuple[tuple[str, str], ...], value: str) -> int:
    return next(
        (
            index
            for index, (candidate, _label) in enumerate(options)
            if candidate == value
        ),
        0,
    )


def _normalize_interface_list(value: str) -> str:
    return ";".join(item.strip() for item in value.split(";") if item.strip())


def _valid_mac(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", value))


def _section_heading(label: str) -> Gtk.Label:
    heading = Gtk.Label(label=label)
    heading.set_xalign(0)
    heading.add_css_class("section-title")
    heading.add_css_class("toolbar-section")
    return heading


def _settings_list() -> Gtk.ListBox:
    value = Gtk.ListBox()
    value.set_selection_mode(Gtk.SelectionMode.NONE)
    value.add_css_class("catalog-list")
    return value


def _control_row(title: str, subtitle: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_use_markup(False)
    return row


def _empty_row(title: str, subtitle: str) -> Adw.ActionRow:
    row = _control_row(title, subtitle)
    row.set_sensitive(False)
    return row


def _clear_list(list_box: Gtk.ListBox) -> None:
    child = list_box.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        list_box.remove(child)
        child = next_child
