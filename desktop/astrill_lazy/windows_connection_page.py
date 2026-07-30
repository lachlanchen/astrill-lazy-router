from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillFavorite,
    AstrillPortOption,
    AstrillServer,
    parse_astrill_favorites,
    serialize_astrill_favorites,
)
from .native_settings import (
    CIPHER_OPTIONS,
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
)

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

SELECTION_KEYS = (
    "astrill_serverid",
    "astrill_sid",
    "astrill_ip",
    "astrill_port",
    "astrill_portindex",
    "astrill_protocol",
    "astrill_vpnmode",
)

RESILIENCE_OPTIONS = (
    (
        "astrill_accel",
        "Hardware acceleration",
        "Accelerate supported traffic through the shared tunnel.",
    ),
    (
        "astrill_blockinternet",
        "Block Internet if VPN drops",
        "Use Astrill's native kill switch while the tunnel recovers.",
    ),
    (
        "astrill_autocycle",
        "Cycle through favorite endpoints",
        "Try the next favorite if Astrill cannot keep the tunnel online.",
    ),
    (
        "astrill_autostart",
        "Start after router boot",
        "Ask native Astrill to connect when DD-WRT starts.",
    ),
)

if not set(CONNECTION_KEYS).issubset(SAFE_NATIVE_ASTRILL_KEYS):
    raise RuntimeError("Windows connection keys escaped the safe NVRAM allowlist")


@dataclass(frozen=True)
class ConnectionDraft:
    """A validated endpoint selection plus safe native setting changes."""

    selection: AstrillConnectionSelection
    changes: dict[str, str]
    favorite_changes: tuple[tuple[int, AstrillFavorite | None], ...] = ()


WindowsConnectionDraft = ConnectionDraft


class WindowsConnectionPage(QWidget):
    """Spacious native-Qt editor for the shared Astrill connection.

    The page intentionally owns no router client. Callers perform router work
    in the supplied callbacks and use :meth:`collect` to obtain one validated
    transactional draft.
    """

    def __init__(
        self,
        *,
        on_refresh: Callable[[], None],
        on_save: Callable[[], None],
        on_connect: Callable[[], None],
        on_apply_reconnect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None],
        on_dirty_changed: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("windowsConnectionPage")
        self.settings: NativeAstrillSettings | None = None
        self.servers: tuple[AstrillServer, ...] = ()
        self.status: dict[str, Any] = {}
        self._server_ids: list[int | None] = []
        self._protocol_values: list[int] = []
        self._port_options: list[AstrillPortOption] = []
        self._favorite_records: dict[int, AstrillFavorite] = {}
        self._original_favorite_records: dict[int, AstrillFavorite] = {}
        self._added_favorite_ids: set[int] = set()
        self._favorites_valid = True
        self._baseline: tuple[tuple[str, str], ...] | None = None
        self._original_boolean_values: dict[str, str] = {}
        self._touched_boolean_keys: set[str] = set()
        self._invalid_mtu_value: str | None = None
        self._mtu_touched = False
        self._loading = False
        self._busy = False
        self._read_only = False
        self._external_lock = ""
        self._dirty = False
        self._discard_on_next_sync = False
        self._on_refresh = on_refresh
        self._on_save = on_save
        self._on_connect = on_connect
        self._on_apply_reconnect = on_apply_reconnect or on_connect
        self._on_disconnect = on_disconnect
        self._on_dirty_changed = on_dirty_changed

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 14, 24)
        root.setSpacing(16)

        root.addLayout(self._build_heading())
        self.conflict_banner = self._build_conflict_banner()
        root.addWidget(self.conflict_banner)
        self.action_banner = self._build_action_banner()
        root.addWidget(self.action_banner)
        root.addWidget(self._build_status_panel())

        panels = QGridLayout()
        panels.setContentsMargins(0, 0, 0, 0)
        panels.setHorizontalSpacing(16)
        panels.setVerticalSpacing(16)
        panels.addWidget(self._build_endpoint_panel(), 0, 0, 1, 2)
        panels.addWidget(self._build_transport_panel(), 1, 0)
        panels.addWidget(self._build_resilience_panel(), 1, 1)
        panels.setColumnStretch(0, 1)
        panels.setColumnStretch(1, 1)
        root.addLayout(panels)
        root.addStretch(1)

        self._connect_control_signals()
        self._update_status_panel()
        self._update_actions()

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def has_pending_favorite_changes(self) -> bool:
        """Whether this draft would change router favorite membership."""

        return bool(self._favorite_changes())

    @property
    def presented_nvram_keys(self) -> tuple[str, ...]:
        return CONNECTION_KEYS

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_actions()

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = bool(read_only)
        self._update_actions()

    def set_external_lock(self, message: str) -> None:
        """Block overlapping writes while the Astrill editor has a draft."""

        previous = self._external_lock
        self._external_lock = message.strip()
        if self._external_lock:
            self.set_action_status(self._external_lock, level="warning")
        elif previous and self.action_status.text() == previous:
            self.set_action_status(
                "Astrill-page settings are synchronized; Connection edits are "
                "available.",
                level="info",
            )
        self._update_actions()

    def set_action_status(self, message: str, *, level: str = "info") -> None:
        """Persist the last local action or guard reason in the page."""

        normalized = message.strip()
        if not normalized:
            normalized = "No connection action has run in this session."
            level = "quiet"
        self.action_status.setText(normalized)
        self.action_banner.setProperty("level", level)
        self.action_status.setProperty("level", level)
        self.action_banner.style().unpolish(self.action_banner)
        self.action_banner.style().polish(self.action_banner)

    def set_status_message(self, message: str, *, level: str = "info") -> None:
        """Compatibility spelling for callers reporting an action outcome."""

        self.set_action_status(message, level=level)

    def sync(
        self,
        settings: NativeAstrillSettings,
        servers: Iterable[AstrillServer],
        status: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Synchronize a router snapshot without silently losing local edits."""

        self.servers = tuple(servers)
        if status is not None:
            self.status = dict(status)
        self._update_status_panel()

        incoming = _settings_fingerprint(settings)
        should_force = force or self._discard_on_next_sync
        if self._dirty and not should_force:
            conflicted = self._baseline is not None and incoming != self._baseline
            self.conflict_banner.setVisible(conflicted)
            self._update_actions()
            return
        self._discard_on_next_sync = False

        self.settings = settings
        self._loading = True
        try:
            self._load_favorites(settings)
            self._set_server_model(settings.integer("astrill_serverid"))
            self._set_protocol_model(
                settings.integer("astrill_protocol"),
                preserve_unavailable=True,
            )
            self._set_port_model(
                settings.integer("astrill_portindex"),
                settings.get("astrill_port"),
                preserve_unavailable=True,
            )
            self._set_cipher_value(settings.get("astrill_cipher", "default"))
            self._set_mtu_value(settings.get("astrill_wanmtu", "1446"))
            self._original_boolean_values = {
                key: settings.get(key) for key in self.switches
            }
            self._touched_boolean_keys.clear()
            for key, control in self.switches.items():
                control.setChecked(settings.enabled(key))
            self._sync_favorite_control()
        finally:
            self._loading = False

        self._baseline = incoming
        self._set_dirty(False)
        self.conflict_banner.setVisible(False)
        self._update_capability_hints()
        self._update_actions()

    def merge_external_favorites(self, settings: NativeAstrillSettings) -> None:
        """Accept a verified favorite list without discarding other draft edits.

        Endpoint-page favorite actions fresh-read and compare-and-swap only
        ``astrill_favlist``. When this editor has an unrelated endpoint or
        transport draft, folding that one verified value into its baseline
        prevents a false conflict and keeps a later save from using stale
        favorite membership.
        """

        if self.settings is None or self.has_pending_favorite_changes:
            return

        merged_values = dict(self.settings.values)
        merged_values["astrill_favlist"] = settings.get("astrill_favlist")
        self.settings = NativeAstrillSettings.from_dict(merged_values)

        self._loading = True
        try:
            self._load_favorites(self.settings)
            for server in self.servers:
                self._update_server_favorite_marker(server.id)
            self._sync_favorite_control()
        finally:
            self._loading = False

        self._baseline = _settings_fingerprint(self.settings)
        self._changed()

    def update_status(self, status: dict[str, Any]) -> None:
        self.status = dict(status)
        self._update_status_panel()
        self._update_actions()

    def collect(self) -> ConnectionDraft:
        if self.settings is None:
            raise ValueError("Astrill connection settings have not loaded")
        selection = self._selection()
        values = self._control_values()
        changes = {
            key: value
            for key, value in values.items()
            if key in CONNECTION_KEYS and self.settings.get(key) != value
        }
        return ConnectionDraft(
            selection=selection,
            changes=changes,
            favorite_changes=self._favorite_changes(),
        )

    def collect_changes(self) -> dict[str, str]:
        """Return all changed safe values, including the endpoint selection."""

        draft = self.collect()
        values = {**draft.selection.native_values(), **draft.changes}
        if self.settings is None:
            return {}
        changes = {
            key: value
            for key, value in values.items()
            if key in CONNECTION_KEYS and self.settings.get(key) != value
        }
        if draft.favorite_changes:
            changes["astrill_favlist"] = serialize_astrill_favorites(
                self._favorite_records.values()
            )
        return changes

    def discard_draft_and_refresh(self) -> None:
        """Request a refresh whose next successful snapshot replaces the draft."""

        self._discard_on_next_sync = True
        self._on_refresh()

    def _build_heading(self) -> QHBoxLayout:
        heading = QHBoxLayout()
        heading.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        title = QLabel("Astrill Connection")
        title.setProperty("class", "pageTitle")
        copy.addWidget(title)
        intro = QLabel(
            "Choose the shared router endpoint, transport, and recovery behavior. "
            "Changes remain local until you explicitly save or apply them."
        )
        intro.setWordWrap(True)
        intro.setProperty("class", "nativeIntro")
        copy.addWidget(intro)
        heading.addLayout(copy, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip(
            "Read current connection settings and state from DD-WRT"
        )
        self.refresh_button.clicked.connect(self._on_refresh)
        heading.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        return heading

    def _build_conflict_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("connectionConflictBanner")
        banner.setProperty("class", "warningBanner")
        banner.setVisible(False)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 10, 10, 10)
        message = QLabel(
            "DD-WRT connection settings changed while this page has unsaved edits. "
            "Your local draft is still intact."
        )
        message.setWordWrap(True)
        layout.addWidget(message, 1)
        reload_button = QPushButton("Discard draft and reload")
        reload_button.clicked.connect(self.discard_draft_and_refresh)
        layout.addWidget(reload_button)
        self.conflict_reload_button = reload_button
        return banner

    def _build_action_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("connectionActionBanner")
        banner.setProperty("class", "statusBanner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 9, 14, 9)
        caption = QLabel("Last action")
        caption.setProperty("class", "statusCaption")
        layout.addWidget(caption)
        self.action_status = QLabel("No connection action has run in this session.")
        self.action_status.setWordWrap(True)
        self.action_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.action_status, 1)
        return banner

    def _build_status_panel(self) -> QGroupBox:
        panel = QGroupBox("Shared tunnel")
        panel.setObjectName("connectionStatusPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 22, 18, 18)
        layout.setSpacing(12)

        state_row = QHBoxLayout()
        state_row.setSpacing(12)
        self.status_badge = QLabel("● Waiting")
        self.status_badge.setProperty("class", "connectionState")
        state_row.addWidget(self.status_badge)
        self.status_detail = QLabel("Waiting for router state")
        self.status_detail.setWordWrap(True)
        self.status_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_row.addWidget(self.status_detail, 1)
        layout.addLayout(state_row)

        self.guard_status = QLabel(
            "Read-only guard is on. Inspection is available; connection changes "
            "are blocked until the guard is turned off in Settings."
        )
        self.guard_status.setWordWrap(True)
        self.guard_status.setProperty("class", "guardNotice")
        self.guard_status.setVisible(False)
        layout.addWidget(self.guard_status)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip(
            "Save the draft to DD-WRT without starting the tunnel"
        )
        self.save_button.clicked.connect(self._on_save)
        actions.addWidget(self.save_button)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._on_connect)
        actions.addWidget(self.connect_button)
        self.apply_button = QPushButton("Apply & Connect")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self._on_apply_reconnect)
        actions.addWidget(self.apply_button)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self._on_disconnect)
        actions.addWidget(self.disconnect_button)
        layout.addLayout(actions)
        return panel

    def _build_endpoint_panel(self) -> QGroupBox:
        panel = QGroupBox("Endpoint")
        form = _form(panel)

        self.server_dropdown = QComboBox()
        self.server_combo = self.server_dropdown
        self.server_dropdown.setEditable(True)
        self.server_dropdown.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.server_dropdown.setMinimumContentsLength(34)
        self.server_dropdown.setMaxVisibleItems(18)
        self.server_dropdown.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        server_completer = self.server_dropdown.completer()
        server_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        server_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        server_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        if line_edit := self.server_dropdown.lineEdit():
            line_edit.setPlaceholderText("Search endpoints by country or city")
            line_edit.setClearButtonEnabled(True)
            line_edit.textEdited.connect(self._server_search_edited)
        form.addRow(
            _field_label(
                "Server",
                "Search the loaded Astrill catalog; ★ marks a router favorite.",
            ),
            self.server_dropdown,
        )

        self.favorite_switch = QCheckBox("Favorite on router")
        self.favorite_control = self.favorite_switch
        favorite_box = QWidget()
        favorite_layout = QVBoxLayout(favorite_box)
        favorite_layout.setContentsMargins(0, 0, 0, 0)
        favorite_layout.setSpacing(3)
        favorite_layout.addWidget(self.favorite_switch)
        self.favorite_detail = QLabel("Favorites have not loaded.")
        self.favorite_detail.setWordWrap(True)
        self.favorite_detail.setProperty("class", "nativeFieldDescription")
        favorite_layout.addWidget(self.favorite_detail)
        form.addRow(
            _field_label(
                "Favorite",
                "Membership is saved in Astrill's native endpoint list.",
            ),
            favorite_box,
        )

        self.protocol_dropdown = QComboBox()
        self.protocol_combo = self.protocol_dropdown
        self.protocol_dropdown.setMinimumWidth(240)
        form.addRow(
            _field_label(
                "Protocol",
                "Only transport modes supported by every node are shown.",
            ),
            self.protocol_dropdown,
        )

        self.port_dropdown = QComboBox()
        self.port_combo = self.port_dropdown
        self.port_dropdown.setMinimumWidth(180)
        form.addRow(
            _field_label(
                "Port",
                "Port profiles common to every node of this server.",
            ),
            self.port_dropdown,
        )
        return panel

    def _build_transport_panel(self) -> QGroupBox:
        panel = QGroupBox("Transport")
        form = _form(panel)

        self.cipher = QComboBox()
        self.cipher.setMinimumWidth(190)
        for value, label in CIPHER_OPTIONS:
            self.cipher.addItem(label, value)
        self.cipher_hint = QLabel("")
        cipher_box = _control_with_hint(self.cipher, self.cipher_hint)
        form.addRow(
            _field_label(
                "Encryption",
                "OpenVPN cipher; RouterPro manages its own transport.",
            ),
            cipher_box,
        )

        self.mtu = QSpinBox()
        self.mtu.setRange(576, 1500)
        self.mtu.setSuffix(" bytes")
        self.mtu.setMinimumWidth(150)
        self.mtu_hint = QLabel("")
        mtu_box = _control_with_hint(self.mtu, self.mtu_hint)
        form.addRow(
            _field_label(
                "Internet MTU",
                "UDP packet size. Existing unsupported values are preserved.",
            ),
            mtu_box,
        )
        return panel

    def _build_resilience_panel(self) -> QGroupBox:
        panel = QGroupBox("Resilience")
        form = _form(panel)
        self.switches: dict[str, QCheckBox] = {}
        for key, title, detail in RESILIENCE_OPTIONS:
            control = QCheckBox("Enabled")
            self.switches[key] = control
            form.addRow(_field_label(title, detail), control)
        return panel

    def _connect_control_signals(self) -> None:
        self.server_dropdown.currentIndexChanged.connect(self._server_changed)
        self.protocol_dropdown.currentIndexChanged.connect(self._protocol_changed)
        self.port_dropdown.currentIndexChanged.connect(self._port_changed)
        self.favorite_switch.checkStateChanged.connect(self._favorite_changed)
        self.cipher.currentIndexChanged.connect(self._changed)
        self.mtu.valueChanged.connect(self._mtu_changed)
        for key, control in self.switches.items():
            control.checkStateChanged.connect(
                lambda _state, option=key: self._boolean_changed(option)
            )

    def _load_favorites(self, settings: NativeAstrillSettings) -> None:
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
        self._original_favorite_records = dict(self._favorite_records)
        self._added_favorite_ids.clear()

    def _favorite_changes(
        self,
    ) -> tuple[tuple[int, AstrillFavorite | None], ...]:
        """Return membership edits without serializing a stale complete list."""

        if not self._favorites_valid:
            return ()
        changes: list[tuple[int, AstrillFavorite | None]] = []
        for server_id, original in self._original_favorite_records.items():
            current = self._favorite_records.get(server_id)
            if current is None:
                changes.append((server_id, None))
            elif current != original:
                changes.append((server_id, current))
        changes.extend(
            (server_id, favorite)
            for server_id, favorite in self._favorite_records.items()
            if server_id not in self._original_favorite_records
        )
        return tuple(changes)

    def _set_server_model(self, preferred_id: int) -> None:
        self.server_dropdown.clear()
        if not self.servers:
            self._server_ids = [None]
            self.server_dropdown.addItem("No endpoints available", None)
            self.server_dropdown.setCurrentIndex(0)
            return
        self._server_ids = [server.id for server in self.servers]
        selected = 0
        if preferred_id not in self._server_ids:
            if preferred_id > 0:
                self._server_ids.insert(0, preferred_id)
                self.server_dropdown.addItem(
                    f"Configured server {preferred_id} · unavailable",
                    preferred_id,
                )
            else:
                self._server_ids.insert(0, None)
                self.server_dropdown.addItem("Select an endpoint", None)
        for server in self.servers:
            self.server_dropdown.addItem(self._server_label(server), server.id)
        if preferred_id in {server.id for server in self.servers}:
            selected = self.server_dropdown.findData(preferred_id)
        self.server_dropdown.setCurrentIndex(selected)

    def _set_protocol_model(
        self,
        preferred: int,
        *,
        preserve_unavailable: bool = False,
    ) -> None:
        self.protocol_dropdown.clear()
        try:
            server = self._selected_server()
        except ValueError:
            self._protocol_values = []
            self.protocol_dropdown.addItem("Not available", None)
            self.protocol_dropdown.setCurrentIndex(0)
            return
        self._protocol_values = list(server.supported_protocols())
        if not self._protocol_values:
            self.protocol_dropdown.addItem("Not available", None)
            self.protocol_dropdown.setCurrentIndex(0)
            return
        for protocol in self._protocol_values:
            self.protocol_dropdown.addItem(
                ASTRILL_PROTOCOL_NAMES[protocol],
                protocol,
            )
        selected = self.protocol_dropdown.findData(preferred)
        if selected < 0 and preserve_unavailable:
            label = (
                ASTRILL_PROTOCOL_NAMES[preferred]
                if 0 <= preferred < len(ASTRILL_PROTOCOL_NAMES)
                else f"Protocol {preferred}"
            )
            self.protocol_dropdown.insertItem(
                0,
                f"Configured {label} · unsupported",
                preferred,
            )
            selected = 0
        self.protocol_dropdown.setCurrentIndex(max(selected, 0))

    def _set_port_model(
        self,
        preferred_index: int,
        preferred_port: str = "",
        *,
        preserve_unavailable: bool = False,
    ) -> None:
        self.port_dropdown.clear()
        try:
            server = self._selected_server()
            protocol = self._selected_protocol()
        except ValueError:
            self._port_options = []
        else:
            self._port_options = list(server.port_options(protocol))
        if not self._port_options:
            self.port_dropdown.addItem("Not available", None)
            self.port_dropdown.setCurrentIndex(0)
            return
        for option in self._port_options:
            self.port_dropdown.addItem(_port_label(option.port), option.index)
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
                -1,
            ),
        )
        if selected < 0 and preserve_unavailable:
            description = preferred_port or f"profile {preferred_index}"
            self.port_dropdown.insertItem(
                0,
                f"Configured {description} · unavailable",
                None,
            )
            selected = 0
        self.port_dropdown.setCurrentIndex(max(selected, 0))

    def _set_cipher_value(self, value: str) -> None:
        self.cipher.clear()
        for option, label in CIPHER_OPTIONS:
            self.cipher.addItem(label, option)
        selected = self.cipher.findData(value)
        if selected < 0:
            self.cipher.addItem(
                f"Unsupported current value ({value or 'empty'})",
                value,
            )
            selected = self.cipher.count() - 1
        self.cipher.setCurrentIndex(selected)

    def _set_mtu_value(self, value: str) -> None:
        try:
            parsed = int(value)
        except ValueError:
            parsed = 0
        if self.mtu.minimum() <= parsed <= self.mtu.maximum():
            self._invalid_mtu_value = None
            self.mtu.setValue(parsed)
        else:
            self._invalid_mtu_value = value
            self.mtu.setValue(1446)
        self._mtu_touched = False

    def _selected_server(self) -> AstrillServer:
        server_id = self.server_dropdown.currentData()
        if server_id is None or server_id not in self._server_ids:
            raise ValueError("select an available Astrill endpoint")
        server = next(
            (candidate for candidate in self.servers if candidate.id == server_id),
            None,
        )
        if server is None:
            raise ValueError("selected Astrill endpoint is unavailable")
        return server

    def _selected_protocol(self) -> int:
        protocol = self.protocol_dropdown.currentData()
        if (
            not isinstance(protocol, int)
            or isinstance(protocol, bool)
            or protocol not in self._protocol_values
        ):
            raise ValueError("select a supported Astrill protocol")
        return protocol

    def _selection(self) -> AstrillConnectionSelection:
        server = self._selected_server()
        protocol = self._selected_protocol()
        if not self._port_options:
            raise ValueError(f"{server.name} has no ports for this protocol")
        port_index = self.port_dropdown.currentData()
        option = next(
            (
                candidate
                for candidate in self._port_options
                if candidate.index == port_index
            ),
            None,
        )
        if option is None:
            raise ValueError("select an available Astrill port")
        return AstrillConnectionSelection.from_server(
            server,
            protocol,
            option.index,
        )

    def _control_values(self) -> dict[str, str]:
        cipher_value = self.cipher.currentData()
        if cipher_value is None:
            raise ValueError("select an Astrill encryption mode")
        cipher = str(cipher_value)
        mtu = (
            self._invalid_mtu_value
            if self._invalid_mtu_value is not None and not self._mtu_touched
            else str(self.mtu.value())
        )
        values = {
            "astrill_cipher": cipher,
            "astrill_wanmtu": mtu,
        }
        for key, control in self.switches.items():
            if key not in self._touched_boolean_keys:
                values[key] = self._original_boolean_values.get(
                    key,
                    "1" if control.isChecked() else "0",
                )
            else:
                values[key] = "1" if control.isChecked() else "0"
        return values

    def _server_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            preferred = self._selected_protocol() if self._protocol_values else 0
            self._set_protocol_model(preferred)
            self._set_port_model(0)
            self._sync_favorite_control()
        finally:
            self._loading = False
        self._update_capability_hints()
        self._changed()

    def _server_search_edited(self, text: str) -> None:
        if self._loading:
            return
        selected = self.server_dropdown.currentIndex()
        if selected >= 0 and text == self.server_dropdown.itemText(selected):
            return
        signals_blocked = self.server_dropdown.blockSignals(True)
        self._loading = True
        try:
            self.server_dropdown.setCurrentIndex(-1)
            if line_edit := self.server_dropdown.lineEdit():
                line_edit.setText(text)
            self._set_protocol_model(0)
            self._set_port_model(0)
            self._sync_favorite_control()
        finally:
            self._loading = False
            self.server_dropdown.blockSignals(signals_blocked)
        self._update_capability_hints()
        self._changed()

    def _protocol_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            self._set_port_model(0)
            self._refresh_added_favorite()
        finally:
            self._loading = False
        self._update_capability_hints()
        self._changed()

    def _port_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._refresh_added_favorite()
        self._changed()

    def _favorite_changed(self, _state: Qt.CheckState) -> None:
        if self._loading or not self._favorites_valid:
            return
        try:
            selection = self._selection()
        except ValueError:
            return
        server_id = selection.server_id
        if self.favorite_switch.isChecked():
            if server_id not in self._favorite_records:
                self._added_favorite_ids.add(server_id)
            self._favorite_records[server_id] = AstrillFavorite.from_selection(
                selection
            )
        else:
            self._favorite_records.pop(server_id, None)
            self._added_favorite_ids.discard(server_id)
        self._update_server_favorite_marker(server_id)
        self._update_favorite_detail()
        self._changed()

    def _boolean_changed(self, key: str) -> None:
        if self._loading:
            return
        self._touched_boolean_keys.add(key)
        self._changed()

    def _mtu_changed(self, _value: int) -> None:
        if self._loading:
            return
        self._mtu_touched = True
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

    def _sync_favorite_control(self) -> None:
        try:
            server_id = self._selected_server().id
        except ValueError:
            server_id = -1
        self.favorite_switch.setChecked(server_id in self._favorite_records)
        self._update_favorite_detail()

    def _update_favorite_detail(self) -> None:
        if not self._favorites_valid:
            self.favorite_detail.setText(
                "The router value is invalid, so it is preserved but cannot be edited."
            )
            return
        count = len(self._favorite_records)
        self.favorite_detail.setText(
            f"{count} saved endpoint{'' if count == 1 else 's'}."
        )

    def _update_server_favorite_marker(self, server_id: int) -> None:
        index = self.server_dropdown.findData(server_id)
        server = next(
            (candidate for candidate in self.servers if candidate.id == server_id),
            None,
        )
        if index >= 0 and server is not None:
            self.server_dropdown.setItemText(index, self._server_label(server))

    def _server_label(self, server: AstrillServer) -> str:
        marker = "★ " if server.id in self._favorite_records else ""
        return f"{marker}{server.name}"

    def _changed(self, *_args: object) -> None:
        if self._loading or self.settings is None:
            return
        try:
            selection = self._selection()
            values = {**selection.native_values(), **self._control_values()}
        except ValueError:
            dirty = True
        else:
            dirty = any(
                self.settings.get(key) != value for key, value in values.items()
            ) or bool(self._favorite_changes())
        self._set_dirty(dirty)
        self._update_actions()

    def _set_dirty(self, dirty: bool) -> None:
        changed = self._dirty != dirty
        self._dirty = dirty
        if changed and self._on_dirty_changed is not None:
            self._on_dirty_changed(dirty)

    def _update_status_panel(self) -> None:
        connected = self.status.get("vpn_state") == "up"
        self.status_badge.setText("● Connected" if connected else "○ Disconnected")
        self.status_badge.setProperty("connected", connected)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

        server_id = _status_integer(self.status, "astrill_server_id")
        server = next(
            (candidate for candidate in self.servers if candidate.id == server_id),
            None,
        )
        protocol = _status_integer(self.status, "astrill_protocol")
        parts = ["Connected" if connected else "Disconnected"]
        if server is not None:
            parts.append(server.name)
        elif server_id > 0:
            parts.append(f"Server {server_id}")
        if 0 <= protocol < len(ASTRILL_PROTOCOL_NAMES):
            parts.append(ASTRILL_PROTOCOL_NAMES[protocol])
        self.status_detail.setText("  ·  ".join(parts))

        router_reason = next(
            (
                str(self.status[key]).strip()
                for key in (
                    "last_connection_error",
                    "connection_error",
                    "last_attempt",
                    "guard_reason",
                )
                if self.status.get(key)
            ),
            "",
        )
        if router_reason:
            self.set_action_status(router_reason, level="warning")

    def _update_capability_hints(self) -> None:
        try:
            protocol = self._selected_protocol()
        except ValueError:
            protocol = -1
        if protocol in {0, 1}:
            self.cipher_hint.setText("Available for OpenVPN.")
        else:
            self.cipher_hint.setText("Managed by RouterPro for this protocol.")
        if protocol in {0, 2}:
            self.mtu_hint.setText("Available for UDP transports.")
        else:
            self.mtu_hint.setText("Not used by TCP transports.")
        if self._invalid_mtu_value is not None and not self._mtu_touched:
            self.mtu_hint.setText(
                f"Router value {self._invalid_mtu_value or 'empty'} is unsupported "
                "and will be preserved until edited."
            )

    def _update_actions(self) -> None:
        connected = self.status.get("vpn_state") == "up"
        controls_unlocked = (
            not self._read_only
            and not self._busy
            and not self._external_lock
            and self.settings is not None
            and bool(self.servers)
        )
        for control in (
            self.server_dropdown,
            self.protocol_dropdown,
            self.port_dropdown,
            self.favorite_switch,
            self.cipher,
            self.mtu,
            *self.switches.values(),
        ):
            control.setEnabled(controls_unlocked)
        self.favorite_switch.setEnabled(controls_unlocked and self._favorites_valid)

        try:
            protocol = self._selected_protocol()
        except ValueError:
            protocol = -1
        self.cipher.setEnabled(controls_unlocked and protocol in {0, 1})
        self.mtu.setEnabled(controls_unlocked and protocol in {0, 2})
        self._update_capability_hints()

        try:
            self._selection()
        except ValueError:
            draft_valid = False
        else:
            draft_valid = self.settings is not None
        write_unlocked = not self._read_only and not self._busy
        draft_unlocked = write_unlocked and draft_valid and not self._external_lock
        self.refresh_button.setEnabled(not self._busy)
        self.save_button.setEnabled(draft_unlocked and self._dirty and not connected)
        self.connect_button.setEnabled(
            draft_unlocked and not connected and not self._dirty
        )
        self.apply_button.setEnabled(draft_unlocked and self._dirty)
        self.apply_button.setText(
            "Apply & Reconnect" if connected else "Apply & Connect"
        )
        self.disconnect_button.setEnabled(write_unlocked and connected)
        self.guard_status.setVisible(self._read_only)

        if connected:
            self.connect_button.setToolTip("The shared tunnel is already connected")
        elif self._dirty:
            self.connect_button.setToolTip(
                "Save the draft or use Apply & Connect before connecting"
            )
        else:
            self.connect_button.setToolTip(
                "Start the shared tunnel with the saved endpoint"
            )
        if connected:
            self.save_button.setToolTip(
                "Use Apply & Reconnect to change a live tunnel safely"
            )
        else:
            self.save_button.setToolTip(
                "Save the draft to DD-WRT without starting the tunnel"
            )


def _settings_fingerprint(
    settings: NativeAstrillSettings,
) -> tuple[tuple[str, str], ...]:
    return tuple((key, settings.get(key)) for key in CONNECTION_KEYS)


def _port_label(value: str) -> str:
    return f"Auto ({value})" if "-" in value else value


def _status_integer(status: dict[str, Any], key: str) -> int:
    try:
        return int(status.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _form(parent: QGroupBox) -> QFormLayout:
    form = QFormLayout(parent)
    form.setContentsMargins(18, 24, 18, 18)
    form.setHorizontalSpacing(24)
    form.setVerticalSpacing(14)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return form


def _field_label(title: str, detail: str) -> QWidget:
    value = QWidget()
    layout = QVBoxLayout(value)
    layout.setContentsMargins(0, 2, 18, 2)
    layout.setSpacing(2)
    heading = QLabel(title)
    heading.setProperty("class", "nativeFieldTitle")
    layout.addWidget(heading)
    description = QLabel(detail)
    description.setWordWrap(True)
    description.setProperty("class", "nativeFieldDescription")
    layout.addWidget(description)
    return value


def _control_with_hint(control: QWidget, hint: QLabel) -> QWidget:
    value = QWidget()
    layout = QVBoxLayout(value)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    layout.addWidget(control)
    hint.setWordWrap(True)
    hint.setProperty("class", "nativeFieldDescription")
    layout.addWidget(hint)
    return value
