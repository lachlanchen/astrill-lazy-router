from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillFavorite,
    AstrillPortOption,
    AstrillServer,
    parse_astrill_favorites,
    serialize_astrill_favorites,
)
from .native_settings import CIPHER_OPTIONS, NativeAstrillSettings

CONNECTION_KEYS = (
    "astrill_serverid",
    "astrill_sid",
    "astrill_ip",
    "astrill_port",
    "astrill_portindex",
    "astrill_protocol",
    "astrill_vpnmode",
    "astrill_cipher",
    "astrill_wanmtu",
    "astrill_accel",
    "astrill_blockinternet",
    "astrill_autocycle",
    "astrill_favlist",
    "astrill_autostart",
)


@dataclass(frozen=True)
class ConnectionDraft:
    selection: AstrillConnectionSelection
    changes: dict[str, str]


class AstrillConnectionPage(Gtk.Box):
    def __init__(
        self,
        *,
        on_refresh: Callable[[], None],
        on_save: Callable[[], None],
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page-content")
        self.settings: NativeAstrillSettings | None = None
        self.servers: tuple[AstrillServer, ...] = ()
        self.status: dict[str, Any] = {}
        self._server_ids: list[int | None] = []
        self._protocol_values: list[int] = []
        self._port_options: list[AstrillPortOption] = []
        self._favorite_records: dict[int, AstrillFavorite] = {}
        self._added_favorite_ids: set[int] = set()
        self._favorites_valid = True
        self._baseline: tuple[tuple[str, str], ...] | None = None
        self._loading = False
        self._dirty = False
        self._busy = False
        self._read_only = False
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        self.conflict_banner = Adw.Banner(
            title="Router connection settings changed while local edits are pending."
        )
        self.conflict_banner.set_button_label("Reload")
        self.conflict_banner.connect("button-clicked", lambda _banner: on_refresh())
        self.conflict_banner.set_revealed(False)
        self.append(self.conflict_banner)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Astrill Connection")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.add_css_class("section-title")
        heading.append(title)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Reload connection settings from the router")
        refresh.connect("clicked", lambda _button: on_refresh())
        heading.append(refresh)
        self.save_button = _button_with_icon(
            "Save", "document-save-symbolic", lambda _button: on_save()
        )
        heading.append(self.save_button)
        self.append(heading)

        status_list = _settings_list()
        self.status_row = Adw.ActionRow(
            title="Shared tunnel",
            subtitle="Waiting for router state",
        )
        self.status_row.set_use_markup(False)
        self.status_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.status_row.add_prefix(self.status_icon)
        self.connection_button = _button_with_icon(
            "Connect", "network-vpn-symbolic", self._connection_action
        )
        self.connection_button.set_valign(Gtk.Align.CENTER)
        self.status_row.add_suffix(self.connection_button)
        status_list.append(self.status_row)
        self.append(status_list)

        self.append(_section_heading("Endpoint"))
        endpoint_list = _settings_list()
        self.server_dropdown = Gtk.DropDown()
        self.server_dropdown.set_enable_search(True)
        self.server_dropdown.set_size_request(320, -1)
        self.server_dropdown.set_valign(Gtk.Align.CENTER)
        server_row = _control_row("Server", "Astrill endpoint")
        server_row.add_suffix(self.server_dropdown)
        endpoint_list.append(server_row)

        self.favorite_switch = Gtk.Switch()
        self.favorite_switch.set_valign(Gtk.Align.CENTER)
        self.favorite_row = _control_row("Favorite", "Not loaded")
        self.favorite_row.add_suffix(self.favorite_switch)
        endpoint_list.append(self.favorite_row)

        self.protocol_dropdown = Gtk.DropDown()
        self.protocol_dropdown.set_size_request(220, -1)
        self.protocol_dropdown.set_valign(Gtk.Align.CENTER)
        protocol_row = _control_row("Protocol", "Native transport mode")
        protocol_row.add_suffix(self.protocol_dropdown)
        endpoint_list.append(protocol_row)

        self.port_dropdown = Gtk.DropDown()
        self.port_dropdown.set_size_request(160, -1)
        self.port_dropdown.set_valign(Gtk.Align.CENTER)
        port_row = _control_row("Port", "Endpoint connection port")
        port_row.add_suffix(self.port_dropdown)
        endpoint_list.append(port_row)
        self.append(endpoint_list)

        self.append(_section_heading("Transport"))
        transport_list = _settings_list()
        self.cipher = Gtk.DropDown.new_from_strings(
            [label for _value, label in CIPHER_OPTIONS]
        )
        self.cipher.set_size_request(180, -1)
        cipher_row = _control_row("Encryption", "OpenVPN tunnel cipher")
        cipher_row.add_suffix(self.cipher)
        transport_list.append(cipher_row)

        self.mtu = Gtk.SpinButton.new_with_range(576, 1500, 1)
        self.mtu.set_valign(Gtk.Align.CENTER)
        mtu_row = _control_row("Internet MTU", "UDP tunnel packet size")
        mtu_row.add_suffix(self.mtu)
        transport_list.append(mtu_row)
        self.append(transport_list)

        self.append(_section_heading("Resilience"))
        resilience_list = _settings_list()
        self.switches: dict[str, Gtk.Switch] = {}
        for key, title_text, subtitle in (
            (
                "astrill_accel",
                "Hardware acceleration",
                "Accelerated mobile-device traffic",
            ),
            (
                "astrill_blockinternet",
                "Block Internet if VPN drops",
                "Native Astrill kill switch",
            ),
            (
                "astrill_autocycle",
                "Auto reconnect to next favorite server",
                "Try the next saved endpoint if the VPN drops",
            ),
            (
                "astrill_autostart",
                "Start automatically after router boot",
                "Connect Astrill when DD-WRT starts",
            ),
        ):
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            row = _control_row(title_text, subtitle)
            row.add_suffix(switch)
            resilience_list.append(row)
            self.switches[key] = switch
            switch.connect("notify::active", lambda *_args: self._changed())
        self.append(resilience_list)

        self.server_dropdown.connect("notify::selected", self._server_changed)
        self.protocol_dropdown.connect("notify::selected", self._protocol_changed)
        self.port_dropdown.connect("notify::selected", self._port_changed)
        self.favorite_switch.connect("notify::active", self._favorite_changed)
        self.cipher.connect("notify::selected", lambda *_args: self._changed())
        self.mtu.connect("value-changed", lambda *_args: self._changed())
        self._update_actions()

    @property
    def dirty(self) -> bool:
        return self._dirty

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_actions()

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only
        self._update_actions()

    def sync(
        self,
        settings: NativeAstrillSettings,
        servers: tuple[AstrillServer, ...],
        status: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        self.status = status
        self.servers = servers
        self._update_status()
        incoming = _settings_fingerprint(settings)
        if self._dirty and not force:
            self.conflict_banner.set_revealed(
                self._baseline is not None and incoming != self._baseline
            )
            self._update_actions()
            return

        self.settings = settings
        self._loading = True
        try:
            try:
                favorites = parse_astrill_favorites(settings.get("astrill_favlist"))
            except ValueError:
                favorites = ()
                self._favorites_valid = False
            else:
                self._favorites_valid = True
            self._favorite_records = {
                favorite.server_id: favorite for favorite in favorites
            }
            self._added_favorite_ids.clear()
            self._set_server_model(settings.integer("astrill_serverid"))
            self._set_protocol_model(settings.integer("astrill_protocol"))
            self._set_port_model(
                settings.integer("astrill_portindex"),
                settings.get("astrill_port"),
            )
            self.cipher.set_selected(
                _option_index(
                    CIPHER_OPTIONS,
                    settings.get("astrill_cipher", "default"),
                )
            )
            self.mtu.set_value(settings.integer("astrill_wanmtu", 1446))
            for key, switch in self.switches.items():
                switch.set_active(settings.enabled(key))
            self._sync_favorite_switch()
        finally:
            self._loading = False
        self._baseline = incoming
        self._dirty = False
        self.conflict_banner.set_revealed(False)
        self._update_capabilities()
        self._update_actions()

    def update_status(self, status: dict[str, Any]) -> None:
        self.status = status
        self._update_status()
        self._update_actions()

    def collect(self) -> ConnectionDraft:
        if self.settings is None:
            raise ValueError("Astrill connection settings have not loaded")
        selection = self._selection()
        values = self._control_values()
        changes = {
            key: value
            for key, value in values.items()
            if self.settings.get(key) != value
        }
        return ConnectionDraft(selection=selection, changes=changes)

    def _selection(self) -> AstrillConnectionSelection:
        server = self._selected_server()
        protocol = self._selected_protocol()
        if not self._port_options:
            raise ValueError(f"{server.name} has no ports for this protocol")
        selected = self.port_dropdown.get_selected()
        if selected >= len(self._port_options):
            raise ValueError("select an available Astrill port")
        port_index = self._port_options[selected].index
        return AstrillConnectionSelection.from_server(server, protocol, port_index)

    def _control_values(self) -> dict[str, str]:
        values = {
            "astrill_cipher": CIPHER_OPTIONS[self.cipher.get_selected()][0],
            "astrill_wanmtu": str(self.mtu.get_value_as_int()),
        }
        values.update(
            {
                key: "1" if switch.get_active() else "0"
                for key, switch in self.switches.items()
            }
        )
        if self._favorites_valid:
            values["astrill_favlist"] = serialize_astrill_favorites(
                self._favorite_records.values()
            )
        return values

    def _set_server_model(self, preferred_id: int) -> None:
        if not self.servers:
            self._server_ids = [None]
            self.server_dropdown.set_model(Gtk.StringList.new(["Not available"]))
            self.server_dropdown.set_selected(0)
            return
        self._server_ids = [server.id for server in self.servers]
        self.server_dropdown.set_model(
            Gtk.StringList.new([server.name for server in self.servers])
        )
        try:
            selected = self._server_ids.index(preferred_id)
        except ValueError:
            selected = 0
        self.server_dropdown.set_selected(selected)

    def _set_protocol_model(self, preferred: int) -> None:
        try:
            server = self._selected_server()
        except ValueError:
            self._protocol_values = []
            self.protocol_dropdown.set_model(Gtk.StringList.new(["Not available"]))
            self.protocol_dropdown.set_selected(0)
            return
        self._protocol_values = list(server.supported_protocols())
        if not self._protocol_values:
            self.protocol_dropdown.set_model(Gtk.StringList.new(["Not available"]))
            self.protocol_dropdown.set_selected(0)
            return
        self.protocol_dropdown.set_model(
            Gtk.StringList.new(
                [ASTRILL_PROTOCOL_NAMES[value] for value in self._protocol_values]
            )
        )
        selected = (
            self._protocol_values.index(preferred)
            if preferred in self._protocol_values
            else 0
        )
        self.protocol_dropdown.set_selected(selected)

    def _set_port_model(self, preferred_index: int, preferred_port: str = "") -> None:
        try:
            server = self._selected_server()
            protocol = self._selected_protocol()
        except ValueError:
            self._port_options = []
        else:
            self._port_options = list(server.port_options(protocol))
        if not self._port_options:
            self.port_dropdown.set_model(Gtk.StringList.new(["Not available"]))
            self.port_dropdown.set_selected(0)
            return
        self.port_dropdown.set_model(
            Gtk.StringList.new(
                [_port_label(option.port) for option in self._port_options]
            )
        )
        selected = next(
            (
                index
                for index, option in enumerate(self._port_options)
                if option.index == preferred_index
                and (not preferred_port or option.port == preferred_port)
            ),
            next(
                (
                    index
                    for index, option in enumerate(self._port_options)
                    if option.index == preferred_index
                ),
                0,
            ),
        )
        self.port_dropdown.set_selected(selected)

    def _selected_server(self) -> AstrillServer:
        selected = self.server_dropdown.get_selected()
        if selected >= len(self._server_ids) or self._server_ids[selected] is None:
            raise ValueError("select an available Astrill endpoint")
        server_id = self._server_ids[selected]
        server = next(
            (item for item in self.servers if item.id == server_id),
            None,
        )
        if server is None:
            raise ValueError("selected Astrill endpoint is unavailable")
        return server

    def _selected_protocol(self) -> int:
        selected = self.protocol_dropdown.get_selected()
        if selected >= len(self._protocol_values):
            raise ValueError("select a supported Astrill protocol")
        return self._protocol_values[selected]

    def _server_changed(self, _dropdown: Gtk.DropDown, _param: Any) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            preferred = self._selected_protocol() if self._protocol_values else 0
            self._set_protocol_model(preferred)
            self._set_port_model(0)
            self._sync_favorite_switch()
        finally:
            self._loading = False
        self._update_capabilities()
        self._changed()

    def _protocol_changed(self, _dropdown: Gtk.DropDown, _param: Any) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            self._set_port_model(0)
            self._refresh_added_favorite()
        finally:
            self._loading = False
        self._update_capabilities()
        self._changed()

    def _port_changed(self, _dropdown: Gtk.DropDown, _param: Any) -> None:
        if self._loading:
            return
        self._refresh_added_favorite()
        self._changed()

    def _favorite_changed(self, switch: Gtk.Switch, _param: Any) -> None:
        if self._loading or not self._favorites_valid:
            return
        try:
            selection = self._selection()
        except ValueError:
            return
        server_id = selection.server_id
        if switch.get_active():
            if server_id not in self._favorite_records:
                self._added_favorite_ids.add(server_id)
            self._favorite_records[server_id] = AstrillFavorite.from_selection(
                selection
            )
        else:
            self._favorite_records.pop(server_id, None)
            self._added_favorite_ids.discard(server_id)
        self._update_favorite_subtitle()
        self._changed()

    def _refresh_added_favorite(self) -> None:
        try:
            selection = self._selection()
        except ValueError:
            return
        if selection.server_id in self._added_favorite_ids:
            self._favorite_records[selection.server_id] = (
                AstrillFavorite.from_selection(selection)
            )

    def _sync_favorite_switch(self) -> None:
        try:
            server_id = self._selected_server().id
        except ValueError:
            server_id = -1
        self.favorite_switch.set_active(server_id in self._favorite_records)
        self._update_favorite_subtitle()

    def _update_favorite_subtitle(self) -> None:
        if not self._favorites_valid:
            self.favorite_row.set_subtitle(
                "The router value is preserved but cannot be edited"
            )
            return
        count = len(self._favorite_records)
        self.favorite_row.set_subtitle(
            f"{count} saved endpoint{'' if count == 1 else 's'}"
        )

    def _update_capabilities(self) -> None:
        try:
            protocol = self._selected_protocol()
        except ValueError:
            protocol = -1
        unlocked = not self._read_only and not self._busy
        self.cipher.set_sensitive(unlocked and protocol in {0, 1})
        self.mtu.set_sensitive(unlocked and protocol in {0, 2})

    def _changed(self) -> None:
        if self._loading or self.settings is None:
            return
        try:
            selection = self._selection()
            values = {**selection.native_values(), **self._control_values()}
        except ValueError:
            self._dirty = True
        else:
            self._dirty = any(
                self.settings.get(key) != value for key, value in values.items()
            )
        self._update_actions()

    def _connection_action(self, _button: Gtk.Button) -> None:
        if self.status.get("vpn_state") == "up" and not self._dirty:
            self._on_disconnect()
        else:
            self._on_connect()

    def _update_status(self) -> None:
        connected = self.status.get("vpn_state") == "up"
        server_id = _status_integer(self.status, "astrill_server_id")
        server = next(
            (item for item in self.servers if item.id == server_id),
            None,
        )
        protocol = _status_integer(self.status, "astrill_protocol")
        protocol_name = (
            ASTRILL_PROTOCOL_NAMES[protocol]
            if protocol in range(len(ASTRILL_PROTOCOL_NAMES))
            else f"Protocol {protocol}"
        )
        location = server.name if server is not None else f"Server {server_id}"
        self.status_row.set_subtitle(
            f"{'Connected' if connected else 'Disconnected'} | "
            f"{location} | {protocol_name}"
        )
        self.status_icon.set_from_icon_name(
            "network-vpn-symbolic" if connected else "network-offline-symbolic"
        )

    def _update_actions(self) -> None:
        connected = self.status.get("vpn_state") == "up"
        unlocked = (
            not self._read_only
            and not self._busy
            and bool(self.servers)
            and self.settings is not None
        )
        for control in (
            self.server_dropdown,
            self.protocol_dropdown,
            self.port_dropdown,
            self.favorite_switch,
            *self.switches.values(),
        ):
            control.set_sensitive(unlocked)
        self.favorite_switch.set_sensitive(unlocked and self._favorites_valid)
        self._update_capabilities()

        self.save_button.set_sensitive(unlocked and self._dirty and not connected)
        if connected and self._dirty:
            _set_button_content(
                self.connection_button,
                "Apply & Reconnect",
                "view-refresh-symbolic",
            )
        elif connected:
            _set_button_content(
                self.connection_button,
                "Disconnect",
                "network-offline-symbolic",
            )
        elif self._dirty:
            _set_button_content(
                self.connection_button,
                "Apply & Connect",
                "network-vpn-symbolic",
            )
        else:
            _set_button_content(
                self.connection_button,
                "Connect",
                "network-vpn-symbolic",
            )
        self.connection_button.set_sensitive(unlocked)


def _settings_fingerprint(
    settings: NativeAstrillSettings,
) -> tuple[tuple[str, str], ...]:
    return tuple((key, settings.get(key)) for key in CONNECTION_KEYS)


def _option_index(options: tuple[tuple[str, str], ...], value: str) -> int:
    return next(
        (
            index
            for index, (candidate, _label) in enumerate(options)
            if candidate == value
        ),
        0,
    )


def _port_label(value: str) -> str:
    return f"Auto ({value})" if "-" in value else value


def _status_integer(status: dict[str, Any], key: str) -> int:
    try:
        return int(status.get(key, 0))
    except (TypeError, ValueError):
        return 0


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


def _button_with_icon(
    label: str,
    icon_name: str,
    callback: Callable[[Gtk.Button], None],
) -> Gtk.Button:
    button = Gtk.Button()
    _set_button_content(button, label, icon_name)
    button.connect("clicked", callback)
    return button


def _set_button_content(button: Gtk.Button, label: str, icon_name: str) -> None:
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    content.append(Gtk.Image.new_from_icon_name(icon_name))
    content.append(Gtk.Label(label=label))
    button.set_child(content)
