from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .astrill import ASTRILL_PROTOCOL_NAMES, AstrillServer
from .models import MatchKind, RouteTarget, Rule
from .native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    WRITABLE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
)
from .service_policy import ServiceRouteMode
from .windows_controller import WindowsController

APP_NAME = "Astrill Lazy Router"

COLORS = {
    "window": "#f4f6f8",
    "sidebar": "#17251f",
    "sidebar_hover": "#243a31",
    "sidebar_active": "#2f5947",
    "card": "#ffffff",
    "border": "#d9e0e4",
    "text": "#182129",
    "muted": "#67747d",
    "green": "#18794e",
    "green_dark": "#11623f",
    "blue": "#176b9b",
    "orange": "#c76b17",
    "red": "#b42318",
}

STYLE_SHEET = f"""
QMainWindow, QWidget#root {{
    background: {COLORS["window"]};
    color: {COLORS["text"]};
    font-family: "Segoe UI";
    font-size: 10pt;
}}
QFrame#sidebar {{
    background: {COLORS["sidebar"]};
    border: none;
}}
QLabel#brand {{
    color: white;
    font-size: 17pt;
    font-weight: 700;
}}
QLabel#brandSub, QLabel#sidebarStatus {{
    color: #b8c7c0;
}}
QListWidget#navigation {{
    background: transparent;
    border: none;
    color: #d7e0dc;
    outline: 0;
}}
QListWidget#navigation::item {{
    border-radius: 7px;
    margin: 2px 8px;
    padding: 10px 12px;
}}
QListWidget#navigation::item:hover {{
    background: {COLORS["sidebar_hover"]};
}}
QListWidget#navigation::item:selected {{
    background: {COLORS["sidebar_active"]};
    color: white;
    font-weight: 600;
}}
QLabel#pageTitle {{
    font-size: 18pt;
    font-weight: 700;
    color: {COLORS["text"]};
}}
QLabel#pageSubtitle, QLabel.muted {{
    color: {COLORS["muted"]};
}}
QFrame.card, QGroupBox {{
    background: {COLORS["card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QGroupBox {{
    margin-top: 10px;
    padding: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}}
QLabel.metricValue {{
    font-size: 15pt;
    font-weight: 700;
}}
QLabel.metricCaption {{
    color: {COLORS["muted"]};
    font-size: 9pt;
}}
QLabel#accessBanner {{
    background: #fff4e5;
    color: #8d4900;
    border: 1px solid #f2c98b;
    border-radius: 6px;
    padding: 9px 12px;
}}
QLabel#statusGood {{
    color: {COLORS["green"]};
    font-weight: 600;
}}
QLabel#statusBad {{
    color: {COLORS["red"]};
    font-weight: 600;
}}
QPushButton {{
    background: white;
    border: 1px solid #bdc8ce;
    border-radius: 6px;
    padding: 7px 12px;
    min-height: 18px;
}}
QPushButton:hover {{
    background: #f0f4f2;
    border-color: #879a91;
}}
QPushButton:pressed {{
    background: #e3ebe7;
}}
QPushButton:disabled {{
    color: #9aa5ab;
    background: #f1f3f4;
    border-color: #d9dfe2;
}}
QPushButton#primary {{
    color: white;
    background: {COLORS["green"]};
    border-color: {COLORS["green"]};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {COLORS["green_dark"]};
}}
QPushButton#primary:disabled {{
    color: #9aa5ab;
    background: #e7ebed;
    border-color: #d2d9dc;
}}
QPushButton#danger {{
    color: {COLORS["red"]};
}}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTableWidget, QTreeWidget {{
    background: white;
    border: 1px solid #cbd4d9;
    border-radius: 5px;
    padding: 5px;
    selection-background-color: #dcebe4;
    selection-color: {COLORS["text"]};
}}
QTreeWidget, QTableWidget {{
    alternate-background-color: #f7f9fa;
    gridline-color: #e4e9ec;
}}
QHeaderView::section {{
    background: #eef2f4;
    color: #44515a;
    border: none;
    border-bottom: 1px solid #cbd4d9;
    padding: 7px;
    font-weight: 600;
}}
QProgressBar {{
    background: #e5eaed;
    border: none;
    border-radius: 2px;
    max-height: 3px;
}}
QProgressBar::chunk {{
    background: {COLORS["green"]};
}}
"""


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class BackgroundTask(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or type(exc).__name__
            self.signals.failed.emit(message)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class RuleDialog(QDialog):
    def __init__(
        self,
        regions: list[tuple[str, str]],
        *,
        rule: Rule | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rule = rule
        self.regions = regions
        self.setWindowTitle("Edit policy" if rule else "Add policy")
        self.setMinimumWidth(470)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        root.addLayout(form)

        self.name = QLineEdit(rule.name if rule else "")
        self.name.setPlaceholderText("Friendly policy name")
        form.addRow("Name", self.name)

        self.kind = QComboBox()
        kinds = (
            ("Website domain", MatchKind.DOMAIN),
            ("IPv4 address / network", MatchKind.CIDR),
            ("LAN device address", MatchKind.DEVICE),
        )
        if rule and rule.match_kind in {MatchKind.SERVICE, MatchKind.PROCESS}:
            kinds = ((rule.match_kind.value.title(), rule.match_kind), *kinds)
        for label, value in kinds:
            self.kind.addItem(label, value)
        if rule:
            index = self.kind.findData(rule.match_kind)
            self.kind.setCurrentIndex(max(index, 0))
            self.kind.setEnabled(False)
        form.addRow("Match type", self.kind)

        self.selector = QLineEdit(rule.selector if rule else "")
        self.selector.setPlaceholderText("example.com or 192.168.1.100")
        self.selector.setReadOnly(rule is not None)
        form.addRow("Selector", self.selector)

        self.route = QComboBox()
        self.route.addItem("Direct", RouteTarget.DIRECT)
        self.route.addItem("Astrill", RouteTarget.VPN)
        if rule:
            self.route.setCurrentIndex(max(self.route.findData(rule.target), 0))
        else:
            self.route.setCurrentIndex(1)
        form.addRow("Route", self.route)

        self.region = QComboBox()
        for region_id, label in regions:
            self.region.addItem(label, region_id)
        if rule:
            self.region.setCurrentIndex(max(self.region.findData(rule.region), 0))
        form.addRow("Astrill region", self.region)

        self.priority = QSpinBox()
        self.priority.setRange(0, 9999)
        self.priority.setSingleStep(100)
        self.priority.setValue(rule.priority if rule else 500)
        form.addRow("Priority", self.priority)

        self.enabled = QCheckBox("Policy enabled")
        self.enabled.setChecked(rule.enabled if rule else True)
        form.addRow("", self.enabled)

        if rule and rule.match_kind is MatchKind.PROCESS:
            note = QLabel(
                "This Ubuntu application identity can be routed here, but it "
                "can only be launched or cleaned up from the Ubuntu frontend."
            )
            note.setWordWrap(True)
            note.setProperty("class", "muted")
            root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.route.currentIndexChanged.connect(self._sync_route)
        self._sync_route()

    def _sync_route(self) -> None:
        direct = self.route.currentData() is RouteTarget.DIRECT
        self.region.setEnabled(not direct)
        if direct:
            direct_index = self.region.findData("direct")
            if direct_index >= 0:
                self.region.setCurrentIndex(direct_index)
        elif self.region.currentData() == "direct":
            active_index = self.region.findData("active-astrill")
            self.region.setCurrentIndex(max(active_index, 0))

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "match_kind": self.kind.currentData(),
            "selector": self.selector.text().strip(),
            "target": self.route.currentData(),
            "region": (
                "direct"
                if self.route.currentData() is RouteTarget.DIRECT
                else self.region.currentData()
            ),
            "priority": self.priority.value(),
            "enabled": self.enabled.isChecked(),
        }


class MainWindow(QMainWindow):
    PAGE_DEFINITIONS = (
        ("policies", "Policies", "Direct or Astrill routing rules"),
        ("services", "Services", "261 curated service profiles"),
        ("countries", "Countries", "Policy regions on one shared tunnel"),
        ("devices", "Devices", "Observed DD-WRT LAN clients"),
        ("endpoints", "Endpoints", "Choose the shared Astrill server"),
        ("astrill", "Astrill", "Native DD-WRT Astrill settings"),
        ("router", "Router", "Connection, runtime, and recovery"),
        ("settings", "Settings", "Windows frontend and SSH access"),
    )

    def __init__(self, controller: WindowsController | None = None) -> None:
        super().__init__()
        self.controller = controller or WindowsController()
        self.thread_pool = QThreadPool(self)
        self._tasks: set[BackgroundTask] = set()
        self.busy_count = 0
        self.router_status: dict[str, Any] = {}
        self.clients: list[dict[str, Any]] = []
        self.native_settings: NativeAstrillSettings | None = None
        self._syncing_access = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(940, 620)
        self.resize(1240, 790)
        self.setWindowIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        )

        self._build_shell()
        self._build_pages()
        self._render_all_local()
        self._select_page(0)
        self._refresh_status(quiet=True)

        self.monitor = QTimer(self)
        self.monitor.setInterval(60_000)
        self.monitor.timeout.connect(lambda: self._refresh_status(quiet=True))
        self.monitor.start()

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 22, 12, 16)
        side.setSpacing(8)

        brand = QLabel("Astrill Lazy")
        brand.setObjectName("brand")
        side.addWidget(brand)
        brand_sub = QLabel("Windows router control")
        brand_sub.setObjectName("brandSub")
        side.addWidget(brand_sub)
        side.addSpacing(18)

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for _page_id, title, _subtitle in self.PAGE_DEFINITIONS:
            self.navigation.addItem(QListWidgetItem(title))
        self.navigation.currentRowChanged.connect(self._select_page)
        side.addWidget(self.navigation, 1)

        self.sidebar_status = QLabel("Checking router...")
        self.sidebar_status.setObjectName("sidebarStatus")
        self.sidebar_status.setWordWrap(True)
        side.addWidget(self.sidebar_status)
        outer.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.page_title = QLabel("Policies")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("pageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        header.addLayout(titles, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self._refresh_status())
        header.addWidget(self.refresh_button)
        self.apply_button = QPushButton("Apply policies")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self._apply_policies)
        header.addWidget(self.apply_button)
        content_layout.addLayout(header)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        content_layout.addWidget(self.progress)

        self.access_banner = QLabel(
            "Read-only safety guard is on. Inspection works; router changes are blocked."
        )
        self.access_banner.setObjectName("accessBanner")
        content_layout.addWidget(self.access_banner)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        outer.addWidget(content, 1)

        self.statusBar().showMessage("Ready")

    def _build_pages(self) -> None:
        self.stack.addWidget(self._build_policies_page())
        self.stack.addWidget(self._build_services_page())
        self.stack.addWidget(self._build_countries_page())
        self.stack.addWidget(self._build_devices_page())
        self.stack.addWidget(self._build_endpoints_page())
        self.stack.addWidget(self._build_astrill_page())
        self.stack.addWidget(self._build_router_page())
        self.stack.addWidget(self._build_settings_page())

    def _build_policies_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        metrics = QHBoxLayout()
        self.metric_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("controller", "Controller"),
            ("tunnel", "Astrill tunnel"),
            ("endpoint", "Active endpoint"),
            ("rules", "Enabled policies"),
        ):
            card = QFrame()
            card.setProperty("class", "card")
            card_layout = QVBoxLayout(card)
            value = QLabel("...")
            value.setProperty("class", "metricValue")
            label = QLabel(caption)
            label.setProperty("class", "metricCaption")
            card_layout.addWidget(value)
            card_layout.addWidget(label)
            metrics.addWidget(card, 1)
            self.metric_labels[key] = value
        layout.addLayout(metrics)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Traffic policies"))
        toolbar.addStretch(1)
        add = QPushButton("Add")
        add.clicked.connect(self._add_policy)
        toolbar.addWidget(add)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit_policy)
        toolbar.addWidget(edit)
        toggle = QPushButton("Enable / disable")
        toggle.clicked.connect(self._toggle_policy)
        toolbar.addWidget(toggle)
        delete = QPushButton("Delete")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_policy)
        toolbar.addWidget(delete)
        layout.addLayout(toolbar)

        self.policy_tree = QTreeWidget()
        self.policy_tree.setHeaderLabels(
            ["Policy", "Type", "Selector", "Route", "Region", "Priority", "State"]
        )
        self.policy_tree.setAlternatingRowColors(True)
        self.policy_tree.setRootIsDecorated(False)
        self.policy_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.policy_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._edit_policy()
        )
        header = self.policy_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.policy_tree, 1)
        return page

    def _build_services_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        search_row = QHBoxLayout()
        self.service_search = QLineEdit()
        self.service_search.setPlaceholderText(
            "Search service, company, country, alias, or domain"
        )
        self.service_search.textChanged.connect(self._render_services)
        search_row.addWidget(self.service_search, 1)
        self.service_count = QLabel("")
        search_row.addWidget(self.service_count)
        layout.addLayout(search_row)

        self.service_tree = QTreeWidget()
        self.service_tree.setHeaderLabels(
            [
                "Service",
                "Company",
                "Category",
                "Provider country",
                "Suggested",
                "Policy",
            ]
        )
        self.service_tree.setAlternatingRowColors(True)
        self.service_tree.setRootIsDecorated(False)
        self.service_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        header = self.service_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.service_tree, 1)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("Add or update selected:"))
        for label, mode in (
            ("Suggested", ServiceRouteMode.SUGGESTED),
            ("Direct", ServiceRouteMode.DIRECT),
            ("Astrill", ServiceRouteMode.VPN),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, selected_mode=mode: self._add_services(
                    selected_mode
                )
            )
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_countries_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel(
            "Country preferences share one Astrill tunnel. Several requested "
            "countries cannot be active at the same time."
        )
        note.setWordWrap(True)
        note.setProperty("class", "muted")
        layout.addWidget(note)
        self.country_tree = QTreeWidget()
        self.country_tree.setHeaderLabels(
            ["Region", "Kind", "Enabled policies", "Known endpoints", "Active"]
        )
        self.country_tree.setAlternatingRowColors(True)
        self.country_tree.setRootIsDecorated(False)
        self.country_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 5):
            self.country_tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        layout.addWidget(self.country_tree)
        return page

    def _build_devices_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(
            QLabel("DHCP leases, static reservations, and active LAN neighbors")
        )
        row.addStretch(1)
        refresh = QPushButton("Load devices")
        refresh.clicked.connect(self._load_devices)
        row.addWidget(refresh)
        direct = QPushButton("Add Direct")
        direct.clicked.connect(lambda: self._add_selected_device(RouteTarget.DIRECT))
        row.addWidget(direct)
        vpn = QPushButton("Add Astrill")
        vpn.clicked.connect(lambda: self._add_selected_device(RouteTarget.VPN))
        row.addWidget(vpn)
        layout.addLayout(row)

        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderLabels(
            ["Device", "Address", "MAC", "State", "Sources"]
        )
        self.device_tree.setAlternatingRowColors(True)
        self.device_tree.setRootIsDecorated(False)
        self.device_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.device_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 5):
            self.device_tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        layout.addWidget(self.device_tree)
        return page

    def _build_endpoints_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.endpoint_search = QLineEdit()
        self.endpoint_search.setPlaceholderText("Search Astrill endpoints")
        self.endpoint_search.textChanged.connect(self._render_endpoints)
        row.addWidget(self.endpoint_search, 1)
        row.addWidget(QLabel("Protocol"))
        self.protocol = QComboBox()
        self.protocol.addItems(list(ASTRILL_PROTOCOL_NAMES))
        row.addWidget(self.protocol)
        load = QPushButton("Load endpoints")
        load.clicked.connect(self._load_endpoints)
        row.addWidget(load)
        connect = QPushButton("Connect selected")
        connect.setObjectName("primary")
        connect.clicked.connect(self._connect_endpoint)
        row.addWidget(connect)
        layout.addLayout(row)

        self.endpoint_tree = QTreeWidget()
        self.endpoint_tree.setHeaderLabels(
            ["Endpoint", "Server ID", "Groups", "Region", "State"]
        )
        self.endpoint_tree.setAlternatingRowColors(True)
        self.endpoint_tree.setRootIsDecorated(False)
        self.endpoint_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.endpoint_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 5):
            self.endpoint_tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.endpoint_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._connect_endpoint()
        )
        layout.addWidget(self.endpoint_tree)
        return page

    def _build_astrill_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        note = QLabel(
            "Allowlisted native Astrill values. Editable values are validated "
            "and read back after saving."
        )
        note.setWordWrap(True)
        row.addWidget(note, 1)
        reload_button = QPushButton("Load settings")
        reload_button.clicked.connect(self._load_native_settings)
        row.addWidget(reload_button)
        self.save_native_button = QPushButton("Save changed values")
        self.save_native_button.setObjectName("primary")
        self.save_native_button.clicked.connect(self._save_native_settings)
        row.addWidget(self.save_native_button)
        layout.addLayout(row)

        self.native_table = QTableWidget(len(SAFE_NATIVE_ASTRILL_KEYS), 2)
        self.native_table.setHorizontalHeaderLabels(["NVRAM key", "Value"])
        self.native_table.setAlternatingRowColors(True)
        self.native_table.verticalHeader().setVisible(False)
        self.native_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.native_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for row_index, key in enumerate(SAFE_NATIVE_ASTRILL_KEYS):
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.native_table.setItem(row_index, 0, key_item)
            value_item = QTableWidgetItem("")
            if key not in WRITABLE_NATIVE_ASTRILL_KEYS:
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                value_item.setBackground(QColor("#eef1f3"))
            self.native_table.setItem(row_index, 1, value_item)
        layout.addWidget(self.native_table)
        return page

    def _build_router_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        connection = QGroupBox("Astrill connection")
        connection_layout = QHBoxLayout(connection)
        self.router_connection_label = QLabel("Status not loaded")
        connection_layout.addWidget(self.router_connection_label, 1)
        connect = QPushButton("Connect")
        connect.clicked.connect(lambda: self._set_connection(True))
        connection_layout.addWidget(connect)
        disconnect = QPushButton("Disconnect")
        disconnect.clicked.connect(lambda: self._set_connection(False))
        connection_layout.addWidget(disconnect)
        layout.addWidget(connection)

        companion = QGroupBox("Optional DD-WRT companion")
        companion_layout = QGridLayout(companion)
        self.companion_label = QLabel(
            "Native-only mode. Policies are local until the companion is installed."
        )
        self.companion_label.setWordWrap(True)
        companion_layout.addWidget(self.companion_label, 0, 0, 1, 5)
        actions = (
            ("Install / upgrade", self._install_companion),
            ("Repair", self._repair_companion),
            ("Refresh domains", self._refresh_domains),
            ("Roll back", self._rollback),
            ("Restore native only", self._restore_native),
        )
        for column, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            if label == "Restore native only":
                button.setObjectName("danger")
            button.clicked.connect(callback)
            companion_layout.addWidget(button, 1, column)
        layout.addWidget(companion)

        self.raw_status = QPlainTextEdit()
        self.raw_status.setReadOnly(True)
        self.raw_status.setPlaceholderText("Router status appears here after refresh.")
        self.raw_status.setFont(QFont("Cascadia Mono", 9))
        layout.addWidget(self.raw_status, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        connection = QGroupBox("Router SSH")
        form = QFormLayout(connection)
        self.host_entry = QLineEdit(self.controller.store.router_host)
        self.host_entry.setPlaceholderText("astrill-router or root@192.168.1.1")
        form.addRow("SSH target", self.host_entry)
        host_actions = QHBoxLayout()
        save_test = QPushButton("Save and test")
        save_test.clicked.connect(self._save_and_test_host)
        host_actions.addWidget(save_test)
        trust = QPushButton("Open interactive SSH setup")
        trust.clicked.connect(self._open_ssh_setup)
        host_actions.addWidget(trust)
        host_actions.addStretch(1)
        form.addRow("", host_actions)
        guidance = QLabel(
            "The app never auto-accepts an SSH host key. Use the interactive "
            "setup button to inspect and accept the DD-WRT fingerprint, then "
            "return here and test key-only access."
        )
        guidance.setWordWrap(True)
        guidance.setProperty("class", "muted")
        form.addRow("", guidance)
        layout.addWidget(connection)

        safety = QGroupBox("Safety")
        safety_layout = QVBoxLayout(safety)
        self.read_only_check = QCheckBox(
            "Keep the local router write guard enabled (recommended)"
        )
        self.read_only_check.setChecked(self.controller.store.read_only)
        self.read_only_check.toggled.connect(self._toggle_read_only)
        safety_layout.addWidget(self.read_only_check)
        self.config_path_label = QLabel(f"Configuration: {self.controller.store.path}")
        self.config_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.config_path_label.setWordWrap(True)
        safety_layout.addWidget(self.config_path_label)
        layout.addWidget(safety)

        limitations = QGroupBox("Windows implementation")
        limitations_layout = QVBoxLayout(limitations)
        text = QLabel(
            "Policies, services, devices, endpoints, native Astrill settings, "
            "and the DD-WRT companion run natively through Windows OpenSSH. "
            "Ubuntu per-application identities are intentionally unavailable: "
            "they depend on Linux macvlan namespaces and pkexec. A real Windows "
            "equivalent requires a separately signed WFP service or driver."
        )
        text.setWordWrap(True)
        limitations_layout.addWidget(text)
        version = QLabel(f"{APP_NAME} {__version__} · Native Qt for Windows")
        version.setProperty("class", "muted")
        limitations_layout.addWidget(version)
        layout.addWidget(limitations)
        layout.addStretch(1)
        return page

    def _select_page(self, index: int) -> None:
        if not 0 <= index < len(self.PAGE_DEFINITIONS):
            return
        self.stack.setCurrentIndex(index)
        self.navigation.setCurrentRow(index)
        _page_id, title, subtitle = self.PAGE_DEFINITIONS[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        if title == "Devices" and not self.clients:
            self._load_devices(quiet=True)
        if title == "Endpoints" and not self.controller.server_catalog.servers:
            self._load_endpoints(quiet=True)

    def _run_task(
        self,
        label: str,
        function: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        *,
        quiet: bool = False,
    ) -> None:
        task = BackgroundTask(function)
        self._tasks.add(task)
        self.busy_count += 1
        self.progress.show()
        self.statusBar().showMessage(label)

        if success is not None:
            task.signals.succeeded.connect(success)
        task.signals.succeeded.connect(
            lambda _result: self.statusBar().showMessage(f"{label}: complete", 3500)
        )
        task.signals.failed.connect(
            lambda message: self._task_failed(label, message, quiet)
        )

        def finished() -> None:
            self.busy_count = max(0, self.busy_count - 1)
            if self.busy_count == 0:
                self.progress.hide()
            self._tasks.discard(task)
            self._sync_access_ui()

        task.signals.finished.connect(finished)
        self.thread_pool.start(task)
        self._sync_access_ui()

    def _task_failed(self, label: str, message: str, quiet: bool) -> None:
        self.statusBar().showMessage(f"{label}: {message}", 9000)
        self.sidebar_status.setText("Router unavailable · check Settings")
        if not quiet:
            QMessageBox.warning(self, label, message)

    def _render_all_local(self) -> None:
        self._render_policies()
        self._render_services()
        self._render_countries()
        self._render_devices()
        self._render_endpoints()
        self._sync_access_ui()

    def _render_policies(self) -> None:
        self.policy_tree.clear()
        for rule in sorted(
            self.controller.store.rules,
            key=lambda item: (item.priority, item.name.casefold()),
        ):
            kind = {
                MatchKind.SERVICE: "Service",
                MatchKind.DOMAIN: "Website",
                MatchKind.CIDR: "IP network",
                MatchKind.DEVICE: "Device",
                MatchKind.PROCESS: "Ubuntu app",
            }[rule.match_kind]
            item = QTreeWidgetItem(
                [
                    rule.name,
                    kind,
                    rule.selector,
                    "Direct" if rule.target is RouteTarget.DIRECT else "Astrill",
                    self._region_name(rule.region),
                    str(rule.priority),
                    "Enabled" if rule.enabled else "Disabled",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, rule.id)
            if not rule.enabled:
                item.setForeground(0, QColor(COLORS["muted"]))
            self.policy_tree.addTopLevelItem(item)
        enabled = sum(rule.enabled for rule in self.controller.store.rules)
        self.metric_labels["rules"].setText(str(enabled))

    def _selected_rule(self) -> Rule | None:
        item = self.policy_tree.currentItem()
        if item is None:
            return None
        return self.controller.rule_by_id(str(item.data(0, Qt.ItemDataRole.UserRole)))

    def _add_policy(self) -> None:
        dialog = RuleDialog(self._region_choices(), parent=self)
        dialog.priority.setValue(self.controller.next_priority())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            self.controller.add_custom_rule(
                name=values["name"],
                match_kind=values["match_kind"],
                selector=values["selector"],
                target=values["target"],
                region=values["region"],
                priority=values["priority"],
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Could not add policy", str(exc))
            return
        self._render_after_policy_change()

    def _edit_policy(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            self._select_something("Select a policy to edit.")
            return
        dialog = RuleDialog(self._region_choices(), rule=rule, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            self.controller.update_rule(
                rule.id,
                name=values["name"],
                target=values["target"],
                region=values["region"],
                enabled=values["enabled"],
                priority=values["priority"],
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Could not update policy", str(exc))
            return
        self._render_after_policy_change()

    def _toggle_policy(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            self._select_something("Select a policy to enable or disable.")
            return
        self.controller.update_rule(rule.id, enabled=not rule.enabled)
        self._render_after_policy_change()

    def _delete_policy(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            self._select_something("Select a policy to delete.")
            return
        detail = f"Remove “{rule.name}” from the next router apply?"
        if rule.match_kind is MatchKind.PROCESS:
            detail += (
                "\n\nIts Linux network namespace is not removed by the "
                "Windows frontend."
            )
        if (
            QMessageBox.question(self, "Delete policy", detail)
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.controller.delete_rule(rule.id)
        self._render_after_policy_change()

    def _render_after_policy_change(self) -> None:
        self._render_policies()
        self._render_services()
        self._render_countries()
        self.statusBar().showMessage("Local policy saved. Apply when ready.", 5000)

    def _render_services(self) -> None:
        if not hasattr(self, "service_tree"):
            return
        query = self.service_search.text().strip().casefold()
        existing = {
            rule.selector: rule
            for rule in self.controller.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        services = [
            service
            for service in self.controller.catalog.services
            if not query or query in service.search_text
        ]
        services.sort(key=lambda item: (item.company.casefold(), item.name.casefold()))
        self.service_tree.clear()
        for service in services:
            rule = existing.get(service.id)
            item = QTreeWidgetItem(
                [
                    service.name,
                    service.company,
                    service.category,
                    service.provider_country,
                    (
                        "Direct"
                        if service.default_route is RouteTarget.DIRECT
                        else "Astrill"
                    ),
                    (
                        "None"
                        if rule is None
                        else (
                            "Direct"
                            if rule.target is RouteTarget.DIRECT
                            else f"Astrill · {self._region_name(rule.region)}"
                        )
                    ),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, service.id)
            self.service_tree.addTopLevelItem(item)
        self.service_count.setText(
            f"{len(services)} of {len(self.controller.catalog.services)}"
        )

    def _add_services(self, mode: ServiceRouteMode) -> None:
        selected = [
            str(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self.service_tree.selectedItems()
        ]
        if not selected:
            self._select_something("Select one or more service rows first.")
            return
        try:
            summary = self.controller.add_services(selected, mode)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not update services", str(exc))
            return
        self._render_after_policy_change()
        self.statusBar().showMessage(
            f"Service policies: {summary.added} added, {summary.updated} updated",
            6000,
        )

    def _render_countries(self) -> None:
        if not hasattr(self, "country_tree"):
            return
        enabled = [rule for rule in self.controller.store.rules if rule.enabled]
        groups = self.controller.server_catalog.groups
        active_group = ""
        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        for region_id, servers in groups.items():
            if any(server.id == current_id for server in servers):
                active_group = region_id
                break
        self.country_tree.clear()
        for region in self.controller.catalog.regions:
            policies = sum(rule.region == region.id for rule in enabled)
            endpoints = (
                "WAN"
                if region.kind == "direct"
                else (
                    "Not loaded"
                    if not self.controller.server_catalog.servers
                    else str(len(groups.get(region.id, ())))
                )
            )
            active = (
                "Active"
                if (
                    region.id == active_group
                    and self.router_status.get("vpn_state") == "up"
                )
                else ""
            )
            self.country_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        region.name,
                        region.kind.title(),
                        str(policies),
                        endpoints,
                        active,
                    ]
                )
            )

    def _load_devices(self, *, quiet: bool = False) -> None:
        self._run_task(
            "Loading LAN devices",
            self.controller.load_clients,
            self._devices_loaded,
            quiet=quiet,
        )

    def _devices_loaded(self, clients: object) -> None:
        self.clients = list(clients)  # type: ignore[arg-type]
        self._render_devices()

    def _render_devices(self) -> None:
        if not hasattr(self, "device_tree"):
            return
        self.device_tree.clear()
        for client in sorted(
            self.clients,
            key=lambda item: (
                str(item.get("hostname", "")).casefold(),
                str(item.get("address", "")),
            ),
        ):
            hostname = str(client.get("hostname", "")).strip()
            if hostname.casefold() in {"", "*", "unknown"}:
                hostname = "Unknown device"
            item = QTreeWidgetItem(
                [
                    hostname,
                    str(client.get("address", "")),
                    str(client.get("mac", "")),
                    "Online" if client.get("active") is True else "Known",
                    str(client.get("source", "")),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, client)
            self.device_tree.addTopLevelItem(item)

    def _add_selected_device(self, target: RouteTarget) -> None:
        item = self.device_tree.currentItem()
        if item is None:
            self._select_something("Select a device first.")
            return
        client = dict(item.data(0, Qt.ItemDataRole.UserRole))
        hostname = str(client.get("hostname", "")).strip()
        if hostname.casefold() in {"", "*", "unknown"}:
            hostname = str(client["address"])
        try:
            self.controller.add_device(
                str(client["address"]),
                hostname,
                target,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not add device", str(exc))
            return
        self._render_after_policy_change()

    def _load_endpoints(self, *, quiet: bool = False) -> None:
        self._run_task(
            "Loading Astrill endpoints",
            self.controller.load_servers,
            self._endpoints_loaded,
            quiet=quiet,
        )

    def _endpoints_loaded(self, result: object) -> None:
        _catalog = result
        self._render_endpoints()
        self._render_countries()
        self._update_status_metrics()

    def _render_endpoints(self) -> None:
        if not hasattr(self, "endpoint_tree"):
            return
        query = self.endpoint_search.text().strip().casefold()
        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        connected = self.router_status.get("vpn_state") == "up"
        group_by_id: dict[int, str] = {}
        for region_id, servers in self.controller.server_catalog.groups.items():
            for server in servers:
                group_by_id.setdefault(server.id, region_id)
        self.endpoint_tree.clear()
        for server in self.controller.server_catalog.servers:
            if query and query not in server.name.casefold():
                continue
            configured = server.id == current_id
            state = (
                "Connected"
                if configured and connected
                else ("Configured" if configured else "")
            )
            item = QTreeWidgetItem(
                [
                    server.name,
                    str(server.id),
                    str(len(server.nodes)),
                    self._region_name(group_by_id.get(server.id, "")),
                    state,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, server)
            self.endpoint_tree.addTopLevelItem(item)

    def _connect_endpoint(self) -> None:
        item = self.endpoint_tree.currentItem()
        if item is None:
            self._select_something("Select an Astrill endpoint first.")
            return
        server = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(server, AstrillServer):
            return
        protocol = self.protocol.currentIndex()
        detail = (
            f"Connect the shared tunnel to {server.name} using "
            f"{ASTRILL_PROTOCOL_NAMES[protocol]}?\n\nVPN-routed traffic will "
            "pause briefly."
        )
        if (
            QMessageBox.question(self, "Switch Astrill endpoint", detail)
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Switching Astrill endpoint",
            lambda: self.controller.switch_server(server, protocol),
            self._status_loaded,
        )

    def _load_native_settings(self) -> None:
        self._run_task(
            "Loading native Astrill settings",
            self.controller.load_native_settings,
            self._native_settings_loaded,
        )

    def _native_settings_loaded(self, settings: object) -> None:
        if not isinstance(settings, NativeAstrillSettings):
            return
        self.native_settings = settings
        for row_index, key in enumerate(SAFE_NATIVE_ASTRILL_KEYS):
            self.native_table.item(row_index, 1).setText(settings.get(key))

    def _save_native_settings(self) -> None:
        if self.native_settings is None:
            self._select_something("Load native Astrill settings first.")
            return
        changes: dict[str, str] = {}
        for row_index, key in enumerate(SAFE_NATIVE_ASTRILL_KEYS):
            if key not in WRITABLE_NATIVE_ASTRILL_KEYS:
                continue
            value = self.native_table.item(row_index, 1).text()
            if value != self.native_settings.get(key):
                changes[key] = value
        if not changes:
            self.statusBar().showMessage("Native Astrill settings are unchanged.", 4000)
            return
        if (
            QMessageBox.question(
                self,
                "Save native Astrill settings",
                f"Validate and write {len(changes)} changed value(s) to DD-WRT?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Saving native Astrill settings",
            lambda: self.controller.save_native_settings(changes),
            self._native_settings_loaded,
        )

    def _refresh_status(self, *, quiet: bool = False) -> None:
        self._run_task(
            "Refreshing router status",
            self.controller.refresh_status,
            self._status_loaded,
            quiet=quiet,
        )

    def _status_loaded(self, status: object) -> None:
        self.router_status = dict(status)  # type: ignore[arg-type]
        self.raw_status.setPlainText(
            json.dumps(self.router_status, indent=2, sort_keys=True)
        )
        self._update_status_metrics()
        self._render_endpoints()
        self._render_countries()

    def _update_status_metrics(self) -> None:
        status = self.router_status
        healthy = status.get("health") == "healthy"
        native = not self.controller.store.companion_enabled
        self.metric_labels["controller"].setText(
            "Native Astrill"
            if native and healthy
            else ("Healthy" if healthy else "Needs attention")
        )
        tunnel = status.get("vpn_state") == "up"
        self.metric_labels["tunnel"].setText("Connected" if tunnel else "Disconnected")
        server_id = int(status.get("astrill_server_id", 0) or 0)
        server = next(
            (
                item
                for item in self.controller.server_catalog.servers
                if item.id == server_id
            ),
            None,
        )
        self.metric_labels["endpoint"].setText(
            (server.name if server else f"Server {server_id}")
            if tunnel
            else "No active tunnel"
        )
        self.metric_labels["rules"].setText(
            str(
                status.get(
                    "origin_count",
                    sum(rule.enabled for rule in self.controller.store.rules),
                )
            )
        )
        if healthy:
            self.sidebar_status.setText(
                "Native Astrill · connected" if native else "Router companion · healthy"
            )
        else:
            self.sidebar_status.setText("Router needs attention")
        self.router_connection_label.setText(
            f"{'Connected' if tunnel else 'Disconnected'} · "
            f"server {server_id} · protocol "
            f"{status.get('astrill_protocol', 'unknown')}"
        )
        if native:
            self.companion_label.setText(
                "Native-only mode · the optional companion is not enabled."
            )
        else:
            self.companion_label.setText(
                f"Version {status.get('version', 'unknown')} · "
                f"{status.get('active_chain') or 'no active chain'} · "
                f"{'watchdog active' if status.get('watchdog') else 'watchdog stopped'}"
            )
        self._sync_access_ui()

    def _apply_policies(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Apply policies",
                "Compile and transactionally install all enabled policies on DD-WRT?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Applying router policies",
            self.controller.apply_rules,
            self._status_loaded,
        )

    def _set_connection(self, connected: bool) -> None:
        verb = "connect" if connected else "disconnect"
        if (
            QMessageBox.question(
                self,
                f"{verb.title()} Astrill",
                f"{verb.title()} the shared Astrill tunnel?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            f"{verb.title()}ing Astrill",
            lambda: self.controller.set_connection(connected),
            self._status_loaded,
        )

    def _install_companion(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "Install DD-WRT companion",
                "This writes the validated companion package, startup hook, "
                "watchdog, routes, and MyPage entries to DD-WRT. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def installed(result: object) -> None:
            self._status_loaded(result.status)  # type: ignore[attr-defined]

        self._run_task(
            "Installing DD-WRT companion",
            self.controller.install_companion,
            installed,
        )

    def _repair_companion(self) -> None:
        def repaired(result: object) -> None:
            self._status_loaded(result.status)  # type: ignore[attr-defined]

        self._run_task(
            "Repairing DD-WRT companion",
            self.controller.repair_companion,
            repaired,
        )

    def _refresh_domains(self) -> None:
        self._run_task(
            "Refreshing domain routes",
            self.controller.refresh_domains,
            self._status_loaded,
        )

    def _rollback(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Roll back router policy",
                "Restore the previous policy on DD-WRT? Local desktop policies "
                "will not change.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Rolling back router policy",
            self.controller.rollback,
            self._status_loaded,
        )

    def _restore_native(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "Restore native Astrill only",
                "Remove all companion runtime, routes, saved package, watchdog, "
                "and MyPage entries while preserving Astrill's connection state?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Restoring native Astrill",
            self.controller.restore_native,
            self._status_loaded,
        )

    def _save_and_test_host(self) -> None:
        try:
            target = self.controller.configure_router(self.host_entry.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid SSH target", str(exc))
            return
        self.host_entry.setText(target)
        self._run_task(
            "Testing key-only SSH",
            self.controller.test_connection,
            lambda ready: QMessageBox.information(
                self,
                "Router SSH",
                "Key-only SSH is ready."
                if ready
                else "The router did not return the expected response.",
            ),
        )

    def _open_ssh_setup(self) -> None:
        try:
            target = self.controller.configure_router(self.host_entry.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid SSH target", str(exc))
            return
        from PySide6.QtCore import QProcess

        if not QProcess.startDetached(
            "cmd.exe",
            ["/k", "ssh.exe", target],
        ):
            QMessageBox.warning(
                self,
                "SSH setup",
                "Could not open the interactive Windows SSH terminal.",
            )

    def _toggle_read_only(self, checked: bool) -> None:
        if self._syncing_access:
            return
        if not checked:
            answer = QMessageBox.warning(
                self,
                "Enable router changes",
                "Turning off the guard allows confirmed actions in this app to "
                "write DD-WRT NVRAM, policies, and Astrill settings. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._syncing_access = True
                self.read_only_check.setChecked(True)
                self._syncing_access = False
                return
        self.controller.set_read_only(checked)
        self._sync_access_ui()

    def _sync_access_ui(self) -> None:
        read_only = self.controller.store.read_only
        self.access_banner.setVisible(read_only)
        self._syncing_access = True
        self.read_only_check.setChecked(read_only)
        self._syncing_access = False
        writable = not read_only and self.busy_count == 0
        companion_writable = writable and self.controller.store.companion_enabled
        self.apply_button.setEnabled(companion_writable)
        self.save_native_button.setEnabled(writable)
        self.refresh_button.setEnabled(self.busy_count == 0)

    def _region_choices(self) -> list[tuple[str, str]]:
        return [(region.id, region.name) for region in self.controller.catalog.regions]

    def _region_name(self, region_id: str) -> str:
        region = self.controller.catalog.regions_by_id.get(region_id)
        return region.name if region is not None else (region_id or "Unknown")

    def _select_something(self, message: str) -> None:
        QMessageBox.information(self, APP_NAME, message)


def _set_windows_identity() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "io.github.lachlanchen.AstrillLazyRouter.Windows"
        )
    except (AttributeError, OSError):
        pass


def run_windows_application(argv: Sequence[str] | None = None) -> int:
    _set_windows_identity()
    arguments = list(argv) if argv is not None else sys.argv
    application = QApplication(arguments)
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName(APP_NAME)
    application.setOrganizationName("Lachlan Chen")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.show()
    return application.exec()
