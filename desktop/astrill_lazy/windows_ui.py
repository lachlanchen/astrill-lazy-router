from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTime,
    QTimer,
    Signal,
    Slot,
)
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .astrill import ASTRILL_PROTOCOL_NAMES, AstrillServer
from .endpoint_probe import EndpointProbeResult, EndpointProbeStatus, probe_servers
from .models import MatchKind, RouteTarget, Rule
from .native_settings import NativeAstrillSettings
from .router import _openssh_config_path
from .service_policy import ServiceRouteMode
from .windows_controller import WindowsController
from .windows_native_page import WindowsNativeSettingsPage
from .windows_ssh_setup import WindowsHostKey, WindowsKeyAuthorization

APP_NAME = "Astrill Lazy Router"

COLORS = {
    "window": "#f5f3ff",
    "sidebar": "#111827",
    "sidebar_hover": "#3730a3",
    "sidebar_active": "#7c3aed",
    "card": "#ffffff",
    "border": "#d8d5ff",
    "text": "#111827",
    "muted": "#64748b",
    "primary": "#6d28d9",
    "primary_dark": "#5b21b6",
    "green": "#059669",
    "blue": "#0284c7",
    "orange": "#ea580c",
    "red": "#dc2626",
}

STYLE_SHEET = f"""
QMainWindow, QWidget#root {{
    background: {COLORS["window"]};
    color: {COLORS["text"]};
    font-family: "Segoe UI";
    font-size: 10.5pt;
}}
QFrame#sidebar {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 {COLORS["sidebar"]},
        stop: 0.52 #312e81,
        stop: 1 #0f766e
    );
    border: none;
}}
QLabel#brand {{
    color: white;
    font-size: 19pt;
    font-weight: 800;
}}
QLabel#brandSub {{
    color: #a5f3fc;
    font-weight: 600;
}}
QLabel#sidebarStatus {{
    color: white;
    background: rgba(255, 255, 255, 28);
    border: 1px solid rgba(255, 255, 255, 45);
    border-radius: 9px;
    padding: 10px 12px;
}}
QListWidget#navigation {{
    background: transparent;
    border: none;
    color: #e0e7ff;
    outline: 0;
    font-size: 11pt;
}}
QListWidget#navigation::item {{
    border-radius: 10px;
    margin: 2px 2px;
    padding: 10px 14px;
}}
QListWidget#navigation::item:hover {{
    background: {COLORS["sidebar_hover"]};
    color: white;
}}
QListWidget#navigation::item:selected {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {COLORS["sidebar_active"]},
        stop: 1 #0891b2
    );
    color: white;
    font-weight: 700;
}}
QLabel#pageTitle {{
    font-size: 22pt;
    font-weight: 800;
    color: {COLORS["text"]};
}}
QLabel#pageSubtitle, QLabel.muted {{
    color: {COLORS["muted"]};
}}
QLabel#refreshMode {{
    color: {COLORS["muted"]};
    font-size: 9pt;
}}
QFrame.card, QGroupBox {{
    background: {COLORS["card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 12px;
}}
QFrame#metric_controller {{
    border-top: 4px solid #7c3aed;
}}
QFrame#metric_tunnel {{
    border-top: 4px solid #06b6d4;
}}
QFrame#metric_endpoint {{
    border-top: 4px solid #f97316;
}}
QFrame#metric_rules {{
    border-top: 4px solid #10b981;
}}
QFrame#latencyCard {{
    background: #ecfeff;
    border: 1px solid #67e8f9;
    border-left: 5px solid #06b6d4;
    border-radius: 12px;
}}
QLabel#latencyTitle {{
    color: #155e75;
    font-size: 12pt;
    font-weight: 800;
}}
QLabel#latencyNote {{
    color: #155e75;
}}
QLabel#latencyStatus {{
    color: #0e7490;
    font-weight: 600;
}}
QPushButton#latencyAction {{
    color: white;
    background: #0891b2;
    border-color: #0891b2;
    font-weight: 700;
}}
QPushButton#latencyAction:hover {{
    background: #0e7490;
    border-color: #0e7490;
}}
QPushButton#latencyAction:disabled {{
    color: #94a3b8;
    background: #e2e8f0;
    border-color: #cbd5e1;
}}
QGroupBox {{
    margin-top: 12px;
    padding: 14px;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}}
QLabel.metricValue {{
    color: #4f46e5;
    font-size: 17pt;
    font-weight: 800;
}}
QLabel.metricCaption {{
    color: {COLORS["muted"]};
    font-size: 9pt;
}}
QLabel#accessBanner {{
    background: #ffedd5;
    color: #9a3412;
    border: 1px solid #fb923c;
    border-radius: 9px;
    padding: 11px 14px;
    font-weight: 600;
}}
QLabel#statusGood {{
    color: {COLORS["green"]};
    font-weight: 600;
}}
QLabel#statusBad {{
    color: {COLORS["red"]};
    font-weight: 600;
}}
QLabel.nativeIntro {{
    color: #334155;
    font-size: 10.5pt;
}}
QLabel.nativeSummary {{
    color: #4338ca;
    background: #eef2ff;
    border: 1px solid #c7d2fe;
    border-radius: 9px;
    padding: 10px 12px;
    font-weight: 600;
}}
QLabel.nativeFieldTitle {{
    color: {COLORS["text"]};
    font-weight: 700;
}}
QLabel.nativeFieldDescription {{
    color: {COLORS["muted"]};
    font-size: 9.25pt;
}}
QLabel.nativeKey {{
    color: #7c3aed;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 8.5pt;
}}
QLabel.nativeReadOnly {{
    color: #334155;
    background: #f1f5f9;
    border: 1px solid #d8d5ff;
    border-radius: 8px;
    padding: 9px 11px;
}}
QPushButton {{
    background: white;
    border: 1px solid #c4b5fd;
    border-radius: 9px;
    padding: 8px 14px;
    min-height: 22px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #ede9fe;
    border-color: #8b5cf6;
}}
QPushButton:pressed {{
    background: #ddd6fe;
}}
QPushButton:disabled {{
    color: #94a3b8;
    background: #e9e7f2;
    border-color: #d8d5e5;
}}
QPushButton#primary {{
    color: white;
    background: {COLORS["primary"]};
    border-color: {COLORS["primary"]};
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background: {COLORS["primary_dark"]};
    border-color: {COLORS["primary_dark"]};
}}
QPushButton#primary:disabled {{
    color: #9aa5ab;
    background: #e7ebed;
    border-color: #d2d9dc;
}}
QPushButton#danger {{
    color: {COLORS["red"]};
    border-color: #fda4af;
}}
QPushButton#danger:hover {{
    color: white;
    background: {COLORS["red"]};
    border-color: {COLORS["red"]};
}}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTableWidget, QTreeWidget {{
    background: white;
    border: 1px solid #c7d2fe;
    border-radius: 8px;
    padding: 7px;
    selection-background-color: #c4b5fd;
    selection-color: {COLORS["text"]};
}}
QTreeWidget, QTableWidget {{
    alternate-background-color: #f8f7ff;
    gridline-color: #e5e7ff;
}}
QTreeWidget::item, QTableWidget::item {{
    padding: 5px 8px;
}}
QHeaderView::section {{
    background: #ede9fe;
    color: #4338ca;
    border: none;
    border-bottom: 1px solid #c4b5fd;
    padding: 8px 10px;
    font-weight: 700;
}}
QProgressBar {{
    background: #e5eaed;
    border: none;
    border-radius: 2px;
    max-height: 3px;
}}
QProgressBar::chunk {{
    background: {COLORS["primary"]};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #a78bfa;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #7c3aed;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
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
        self._clients_loading = False
        self._clients_loaded = False
        self._endpoint_catalog_loading = False
        self._endpoint_catalog_loaded = False
        self._endpoint_probe_running = False
        self._endpoint_probe_results: dict[
            tuple[int, int], tuple[EndpointProbeResult, str]
        ] = {}
        self.native_settings: NativeAstrillSettings | None = None
        self._native_settings_loading = False
        self._syncing_access = False
        self._endpoint_protocol_user_selected = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(960, 640)
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1360, 860)
        else:
            available = screen.availableGeometry()
            self.resize(
                min(1360, max(960, available.width() - 48)),
                min(860, max(640, available.height() - 48)),
            )
        self.setWindowIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        )

        self._build_shell()
        self._build_pages()
        self._render_all_local()
        self._select_page(0)
        self._refresh_status(quiet=True)

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(252)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 18)
        side.setSpacing(10)

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
        self.navigation.setUniformItemSizes(True)
        self.navigation.setSpacing(2)
        self.navigation.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        for _page_id, title, _subtitle in self.PAGE_DEFINITIONS:
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, 48))
            self.navigation.addItem(item)
        self.navigation.currentRowChanged.connect(self._select_page)
        side.addWidget(self.navigation, 1)

        self.sidebar_status = QLabel("Checking router...")
        self.sidebar_status.setObjectName("sidebarStatus")
        self.sidebar_status.setWordWrap(True)
        side.addWidget(self.sidebar_status)
        outer.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 22, 24, 22)
        content_layout.setSpacing(16)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.page_title = QLabel("Policies")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("pageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        header.addLayout(titles, 1)
        self.refresh_mode_label = QLabel("Startup check · manual refresh after this")
        self.refresh_mode_label.setObjectName("refreshMode")
        self.refresh_mode_label.setToolTip(
            "Astrill Lazy Router does not poll DD-WRT in the background."
        )
        header.addWidget(self.refresh_mode_label)
        self.refresh_button = QPushButton("Refresh router")
        self.refresh_button.setToolTip(
            "Read router status now. Status is not polled automatically."
        )
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
        self.stack.addWidget(self._scrollable_page(self._build_astrill_page()))
        self.stack.addWidget(self._build_router_page())
        self.stack.addWidget(self._scrollable_page(self._build_settings_page()))

    @staticmethod
    def _scrollable_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def _build_policies_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        metrics = QHBoxLayout()
        self.metric_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("controller", "Controller"),
            ("tunnel", "Astrill tunnel"),
            ("endpoint", "Active endpoint"),
            ("rules", "Enabled policies"),
        ):
            card = QFrame()
            card.setObjectName(f"metric_{key}")
            card.setProperty("class", "card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(4)
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
        layout.setSpacing(14)
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
        layout.setSpacing(14)
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
        layout.setSpacing(14)
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
        layout.setSpacing(14)
        scope = QLabel(
            "Choose the endpoint for the router's one shared Astrill tunnel. "
            "This controls DD-WRT only; it does not install, connect, or reroute "
            "a VPN on this Windows computer."
        )
        scope.setWordWrap(True)
        layout.addWidget(scope)

        row = QHBoxLayout()
        self.endpoint_search = QLineEdit()
        self.endpoint_search.setPlaceholderText("Search Astrill endpoints")
        self.endpoint_search.textChanged.connect(self._render_endpoints)
        row.addWidget(self.endpoint_search, 1)
        row.addWidget(QLabel("Protocol"))
        self.protocol = QComboBox()
        self.protocol.addItems(list(ASTRILL_PROTOCOL_NAMES))
        self.protocol.activated.connect(self._endpoint_protocol_selected)
        self.protocol.currentIndexChanged.connect(self._endpoint_protocol_changed)
        row.addWidget(self.protocol)
        self.load_endpoints_button = QPushButton("Load endpoints")
        self.load_endpoints_button.clicked.connect(self._load_endpoints)
        row.addWidget(self.load_endpoints_button)
        self.connect_endpoint_button = QPushButton(
            "Connect router to selected endpoint"
        )
        self.connect_endpoint_button.setObjectName("primary")
        self.connect_endpoint_button.clicked.connect(self._connect_endpoint)
        row.addWidget(self.connect_endpoint_button)
        layout.addLayout(row)

        latency_card = QFrame()
        latency_card.setObjectName("latencyCard")
        latency_layout = QVBoxLayout(latency_card)
        latency_layout.setContentsMargins(16, 13, 16, 13)
        latency_layout.setSpacing(8)
        latency_title = QLabel("PC latency test")
        latency_title.setObjectName("latencyTitle")
        latency_layout.addWidget(latency_title)
        latency_note = QLabel(
            "Runs one TCP connection check from this Windows PC over its current "
            "network path. It does not send commands to DD-WRT, switch the router "
            "endpoint, or measure VPN download speed."
        )
        latency_note.setObjectName("latencyNote")
        latency_note.setWordWrap(True)
        latency_layout.addWidget(latency_note)
        latency_controls = QHBoxLayout()
        latency_controls.addWidget(QLabel("Scope"))
        self.endpoint_probe_scope = QComboBox()
        self.endpoint_probe_scope.addItem("Selected endpoint", "selected")
        self.endpoint_probe_scope.addItem("Visible endpoints", "visible")
        self.endpoint_probe_scope.addItem("All loaded endpoints", "all")
        self.endpoint_probe_scope.currentIndexChanged.connect(
            lambda _index: self._sync_endpoint_action_ui()
        )
        latency_controls.addWidget(self.endpoint_probe_scope)
        self.endpoint_probe_button = QPushButton("Test PC latency")
        self.endpoint_probe_button.setObjectName("latencyAction")
        self.endpoint_probe_button.setToolTip(
            "Manual only: opens one bounded TCP connection per endpoint from "
            "this computer and immediately closes it."
        )
        self.endpoint_probe_button.clicked.connect(self._test_endpoint_latency)
        latency_controls.addWidget(self.endpoint_probe_button)
        self.endpoint_probe_clear_button = QPushButton("Clear results")
        self.endpoint_probe_clear_button.clicked.connect(
            self._clear_endpoint_probe_results
        )
        latency_controls.addWidget(self.endpoint_probe_clear_button)
        latency_controls.addStretch(1)
        latency_layout.addLayout(latency_controls)
        self.endpoint_probe_status = QLabel(
            "Not run · results appear only after you click Test PC latency."
        )
        self.endpoint_probe_status.setObjectName("latencyStatus")
        self.endpoint_probe_status.setWordWrap(True)
        latency_layout.addWidget(self.endpoint_probe_status)
        layout.addWidget(latency_card)

        self.endpoint_action_status = QLabel("")
        self.endpoint_action_status.setWordWrap(True)
        layout.addWidget(self.endpoint_action_status)

        self.endpoint_tree = QTreeWidget()
        self.endpoint_tree.setHeaderLabels(
            [
                "Endpoint",
                "Region",
                "Server ID",
                "Router state",
                "Nodes",
                "PC latency",
                "Reach",
                "Tested",
            ]
        )
        self.endpoint_tree.setAlternatingRowColors(True)
        self.endpoint_tree.setRootIsDecorated(False)
        self.endpoint_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.endpoint_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 8):
            self.endpoint_tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.endpoint_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._connect_endpoint()
        )
        self.endpoint_tree.currentItemChanged.connect(
            lambda _current, _previous: self._sync_endpoint_action_ui()
        )
        layout.addWidget(self.endpoint_tree)
        self._sync_endpoint_action_ui()
        return page

    def _build_astrill_page(self) -> QWidget:
        self.native_page = WindowsNativeSettingsPage(
            on_refresh=self._load_native_settings,
            on_save=self._save_native_settings,
        )
        self.save_native_button = self.native_page.save_button
        return self.native_page

    def _build_router_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

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
        self.companion_action_buttons: dict[str, QPushButton] = {}
        for column, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            if label == "Restore native only":
                button.setObjectName("danger")
            button.clicked.connect(callback)
            companion_layout.addWidget(button, 1, column)
            self.companion_action_buttons[label] = button
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
        layout.setSpacing(14)

        connection = QGroupBox("Router SSH")
        form = QFormLayout(connection)
        self.host_entry = QLineEdit(self.controller.store.router_host)
        self.host_entry.setPlaceholderText("192.168.1.1 or astrill-router")
        form.addRow("Host or alias", self.host_entry)
        self.user_entry = QLineEdit(self.controller.store.router_user)
        self.user_entry.setPlaceholderText("root")
        form.addRow("SSH user", self.user_entry)
        self.port_entry = QSpinBox()
        self.port_entry.setRange(1, 65535)
        self.port_entry.setValue(self.controller.store.router_port)
        form.addRow("SSH port", self.port_entry)
        self.identity_entry = QLineEdit(self.controller.store.router_identity)
        self.identity_entry.setPlaceholderText("~/.ssh/astrill_lazy_router_ed25519")
        form.addRow("Private key", self.identity_entry)
        self.ssh_config_check = QCheckBox(
            "Use OpenSSH config for user, port, and private key"
        )
        self.ssh_config_check.setChecked(self.controller.store.router_use_ssh_config)
        self.ssh_config_check.toggled.connect(self._sync_ssh_fields)
        form.addRow("", self.ssh_config_check)
        host_actions = QHBoxLayout()
        save_test = QPushButton("Test key access")
        save_test.clicked.connect(self._save_and_test_host)
        host_actions.addWidget(save_test)
        self.setup_key_button = QPushButton("Set up key via Telnet")
        self.setup_key_button.setObjectName("primary")
        self.setup_key_button.clicked.connect(self._setup_key_via_telnet)
        host_actions.addWidget(self.setup_key_button)
        trust = QPushButton("Open interactive SSH setup")
        trust.clicked.connect(self._open_ssh_setup)
        host_actions.addWidget(trust)
        host_actions.addStretch(1)
        form.addRow("", host_actions)
        guidance = QLabel(
            "Guided setup generates a dedicated Ed25519 key, shows the SSH "
            "fingerprint for confirmation, and sends only the public key through "
            "LAN Telnet port 23. Telnet is unencrypted, so use it only on a "
            "trusted local network. The password is used once and never saved."
        )
        guidance.setWordWrap(True)
        guidance.setProperty("class", "muted")
        form.addRow("", guidance)
        self.ssh_setup_status = QLabel("Key-only SSH has not been verified.")
        self.ssh_setup_status.setProperty("class", "muted")
        self.ssh_setup_status.setWordWrap(True)
        form.addRow("Setup status", self.ssh_setup_status)
        self._sync_ssh_fields()
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
        page_id, title, subtitle = self.PAGE_DEFINITIONS[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        if title == "Devices" and not self._clients_loaded:
            self._load_devices(quiet=True)
        if title == "Endpoints" and not self._endpoint_catalog_loaded:
            self._load_endpoints(quiet=True)
        if page_id == "astrill":
            if not self._clients_loaded:
                self._load_devices(quiet=True)
            if self.native_settings is None:
                self._load_native_settings()

    def _run_task(
        self,
        label: str,
        function: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        *,
        quiet: bool = False,
        finished_callback: Callable[[], None] | None = None,
        router_related: bool = True,
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
            lambda message: self._task_failed(
                label, message, quiet, router_related=router_related
            )
        )

        def finished() -> None:
            self.busy_count = max(0, self.busy_count - 1)
            if self.busy_count == 0:
                self.progress.hide()
            self._tasks.discard(task)
            if finished_callback is not None:
                finished_callback()
            self._sync_access_ui()

        task.signals.finished.connect(finished)
        self.thread_pool.start(task)
        self._sync_access_ui()

    def _task_failed(
        self, label: str, message: str, quiet: bool, *, router_related: bool
    ) -> None:
        self.statusBar().showMessage(f"{label}: {message}", 9000)
        if router_related:
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
        if self._clients_loading:
            return
        self._clients_loading = True
        self._run_task(
            "Loading LAN devices",
            self.controller.load_clients,
            self._devices_loaded,
            quiet=quiet,
            finished_callback=self._devices_finished,
        )

    def _devices_finished(self) -> None:
        self._clients_loading = False

    def _devices_loaded(self, clients: object) -> None:
        self._clients_loaded = True
        self.clients = list(clients)  # type: ignore[arg-type]
        self._render_devices()
        if hasattr(self, "native_page"):
            self.native_page.update_clients(self.clients)

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
        if self._endpoint_catalog_loading:
            return
        self._endpoint_catalog_loading = True
        self._run_task(
            "Loading Astrill endpoints",
            self.controller.load_servers,
            self._endpoints_loaded,
            quiet=quiet,
            finished_callback=self._endpoint_catalog_finished,
        )

    def _endpoint_catalog_finished(self) -> None:
        self._endpoint_catalog_loading = False

    def _endpoints_loaded(self, result: object) -> None:
        _catalog = result
        self._endpoint_catalog_loaded = True
        self._render_endpoints()
        self._render_countries()
        self._update_status_metrics()

    def _endpoint_protocol_selected(self, _index: int) -> None:
        self._endpoint_protocol_user_selected = True
        self._sync_endpoint_action_ui()

    def _endpoint_protocol_changed(self, _index: int) -> None:
        self._render_endpoints()

    def _render_endpoints(self) -> None:
        if not hasattr(self, "endpoint_tree"):
            return
        query = self.endpoint_search.text().strip().casefold()
        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        connected = self.router_status.get("vpn_state") == "up"
        selected_id = current_id
        selected_item = self.endpoint_tree.currentItem()
        if selected_item is not None:
            selected_server = selected_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(selected_server, AstrillServer):
                selected_id = selected_server.id
        group_by_id: dict[int, str] = {}
        for region_id, servers in self.controller.server_catalog.groups.items():
            for server in servers:
                group_by_id.setdefault(server.id, region_id)
        self.endpoint_tree.clear()
        item_to_select: QTreeWidgetItem | None = None
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
                    self._region_name(group_by_id.get(server.id, "")),
                    str(server.id),
                    state,
                    str(len(server.nodes)),
                    *self._endpoint_probe_cells(server),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, server)
            self._decorate_endpoint_probe_result(item, server)
            self.endpoint_tree.addTopLevelItem(item)
            if server.id == selected_id:
                item_to_select = item
        if item_to_select is not None:
            self.endpoint_tree.setCurrentItem(item_to_select)
        self._sync_endpoint_action_ui()

    def _endpoint_probe_cells(self, server: AstrillServer) -> list[str]:
        cached = self._endpoint_probe_results.get(
            (server.id, self.protocol.currentIndex())
        )
        if cached is None:
            return ["—", "Not tested", "—"]
        result, checked_at = cached
        latency = (
            f"{result.latency_ms:.1f} ms"
            if result.status is EndpointProbeStatus.REACHABLE
            and result.latency_ms is not None
            else "—"
        )
        reach = {
            EndpointProbeStatus.REACHABLE: "Reachable",
            EndpointProbeStatus.UNREACHABLE: "No reply",
            EndpointProbeStatus.UNAVAILABLE: "Unavailable",
        }[result.status]
        return [latency, reach, checked_at]

    def _decorate_endpoint_probe_result(
        self, item: QTreeWidgetItem, server: AstrillServer
    ) -> None:
        cached = self._endpoint_probe_results.get(
            (server.id, self.protocol.currentIndex())
        )
        if cached is None:
            return
        result, checked_at = cached
        if result.status is EndpointProbeStatus.REACHABLE:
            latency = result.latency_ms or 0.0
            color = (
                "#0f766e"
                if latency < 80
                else ("#d97706" if latency < 180 else "#e11d48")
            )
        else:
            color = "#b91c1c"
        for column in (5, 6, 7):
            item.setForeground(column, QColor(color))
        method = (
            f"{ASTRILL_PROTOCOL_NAMES[result.tested_protocol]} TCP counterpart"
            if result.used_tcp_counterpart and result.tested_protocol is not None
            else "TCP connect"
        )
        target = (
            f"{result.address}:{result.port}"
            if result.address is not None and result.port is not None
            else "No usable mapped target"
        )
        tooltip = (
            f"Tested from this Windows PC at {checked_at}\n"
            f"Target: {target}\n"
            f"Method: {method}\n"
            f"{result.detail}"
        )
        for column in (5, 6, 7):
            item.setToolTip(column, tooltip)

    def _endpoint_probe_selection(self) -> tuple[AstrillServer, ...]:
        scope = self.endpoint_probe_scope.currentData()
        if scope == "all":
            return tuple(self.controller.server_catalog.servers)
        if scope == "visible":
            visible: list[AstrillServer] = []
            for index in range(self.endpoint_tree.topLevelItemCount()):
                item = self.endpoint_tree.topLevelItem(index)
                value = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(value, AstrillServer):
                    visible.append(value)
            return tuple(visible)
        item = self.endpoint_tree.currentItem()
        if item is None:
            return ()
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return (value,) if isinstance(value, AstrillServer) else ()

    def _test_endpoint_latency(self) -> None:
        if self.busy_count or self._endpoint_probe_running:
            return
        servers = self._endpoint_probe_selection()
        if not self._endpoint_catalog_loaded or not servers:
            self.endpoint_probe_status.setText(
                "Load endpoints and choose a scope with at least one endpoint."
            )
            return
        protocol = self.protocol.currentIndex()
        self._endpoint_probe_running = True
        self.endpoint_probe_status.setText(
            f"Testing {len(servers)} endpoint"
            f"{'' if len(servers) == 1 else 's'} from this PC · no router commands…"
        )
        self._run_task(
            "Testing endpoint latency from this PC",
            lambda: probe_servers(servers, protocol),
            self._endpoint_probe_completed,
            finished_callback=self._endpoint_probe_finished,
            router_related=False,
        )

    def _endpoint_probe_completed(self, value: object) -> None:
        results = tuple(value)  # type: ignore[arg-type]
        checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        reachable = 0
        for result in results:
            if not isinstance(result, EndpointProbeResult):
                continue
            self._endpoint_probe_results[
                (result.server_id, result.selected_protocol)
            ] = (result, checked_at)
            if result.status is EndpointProbeStatus.REACHABLE:
                reachable += 1
        self.endpoint_probe_status.setText(
            f"Manual PC test complete · {reachable}/{len(results)} reachable · "
            "no DD-WRT commands sent."
        )
        self._render_endpoints()

    def _endpoint_probe_finished(self) -> None:
        self._endpoint_probe_running = False
        self._sync_endpoint_action_ui()

    def _clear_endpoint_probe_results(self) -> None:
        self._endpoint_probe_results.clear()
        self.endpoint_probe_status.setText(
            "Results cleared · tests run only when you click Test PC latency."
        )
        self._render_endpoints()

    def _connect_endpoint(self) -> None:
        if self.busy_count:
            self.statusBar().showMessage(
                "Wait for the current router action to finish.", 4000
            )
            return
        if self.controller.store.read_only:
            self._select_something(
                "The read-only guard blocks endpoint changes. Turn it off in "
                "Settings before connecting the router to another endpoint."
            )
            return
        if not self.controller.store.companion_enabled:
            self._select_something(
                "Install and enable the DD-WRT companion before switching the "
                "router's Astrill endpoint."
            )
            return
        item = self.endpoint_tree.currentItem()
        if item is None:
            self._select_something("Select an Astrill endpoint first.")
            return
        server = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(server, AstrillServer):
            return
        protocol = self.protocol.currentIndex()
        try:
            server.endpoint_for(protocol)
        except ValueError as exc:
            QMessageBox.warning(self, "Unsupported endpoint protocol", str(exc))
            return
        detail = (
            f"Connect the router's shared Astrill tunnel to {server.name} using "
            f"{ASTRILL_PROTOCOL_NAMES[protocol]}?\n\nThis writes the selected "
            "endpoint to DD-WRT and reconnects the router tunnel, so all "
            "Astrill-routed traffic will pause briefly. It does not change this "
            "Windows computer's VPN or local routing."
        )
        if (
            QMessageBox.warning(
                self,
                "Switch router Astrill endpoint",
                detail,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            f"Connecting router to {server.name}",
            lambda: self.controller.switch_server(server, protocol),
            self._endpoint_connected,
        )

    def _endpoint_connected(self, status: object) -> None:
        self._endpoint_protocol_user_selected = False
        self._status_loaded(status)

    def _sync_endpoint_action_ui(self) -> None:
        if not hasattr(self, "connect_endpoint_button"):
            return
        idle = self.busy_count == 0
        read_only = self.controller.store.read_only
        companion_enabled = self.controller.store.companion_enabled
        selected: AstrillServer | None = None
        item = self.endpoint_tree.currentItem()
        if item is not None:
            value = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(value, AstrillServer):
                selected = value

        protocol_supported = False
        if selected is not None:
            try:
                selected.endpoint_for(self.protocol.currentIndex())
                protocol_supported = True
            except ValueError:
                pass

        self.load_endpoints_button.setEnabled(idle)
        self.endpoint_search.setEnabled(idle)
        self.protocol.setEnabled(idle)
        self.endpoint_tree.setEnabled(idle)
        self.endpoint_probe_scope.setEnabled(idle)
        self.endpoint_probe_button.setEnabled(
            idle
            and self._endpoint_catalog_loaded
            and bool(self._endpoint_probe_selection())
        )
        self.endpoint_probe_clear_button.setEnabled(
            idle and bool(self._endpoint_probe_results)
        )
        self.connect_endpoint_button.setEnabled(
            idle
            and not read_only
            and companion_enabled
            and selected is not None
            and protocol_supported
        )

        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        connected = self.router_status.get("vpn_state") == "up"
        reconnecting = selected is not None and selected.id == current_id and connected
        self.connect_endpoint_button.setText(
            "Reconnect router to selected endpoint"
            if reconnecting
            else "Connect router to selected endpoint"
        )

        if self._endpoint_probe_running:
            message = (
                "The manual latency test is running on this Windows PC. "
                "No router command or endpoint change is in progress."
            )
        elif not idle:
            message = "A router action is in progress. Endpoint controls are locked."
        elif read_only:
            message = (
                "Inspection is available. Turn off the read-only guard in Settings "
                "to connect the router to a selected endpoint."
            )
        elif not companion_enabled:
            message = (
                "Inspection is available. Install the DD-WRT companion to enable "
                "safe endpoint switching and rollback."
            )
        elif selected is None:
            message = (
                "Select an endpoint, choose its protocol, then connect the router."
            )
        elif not protocol_supported:
            message = (
                f"{selected.name} does not offer "
                f"{ASTRILL_PROTOCOL_NAMES[self.protocol.currentIndex()]}. "
                "Choose another protocol or endpoint."
            )
        else:
            if reconnecting:
                message = (
                    f"{selected.name} is connected on the router. The action will "
                    "reconnect that shared tunnel using the chosen protocol."
                )
            else:
                message = (
                    f"Ready to connect the router's shared tunnel to {selected.name}."
                )
        self.endpoint_action_status.setText(message)

    def _load_native_settings(self) -> None:
        if self._native_settings_loading:
            return
        self._native_settings_loading = True
        self._run_task(
            "Loading native Astrill settings",
            self.controller.load_native_settings,
            self._native_settings_loaded,
            finished_callback=self._native_settings_finished,
        )

    def _native_settings_finished(self) -> None:
        self._native_settings_loading = False

    def _native_settings_loaded(self, settings: object) -> None:
        if not isinstance(settings, NativeAstrillSettings):
            return
        self.native_settings = settings
        self.native_page.render(settings, self.clients)
        self.statusBar().showMessage(
            "Native Astrill settings loaded and synchronized.", 4000
        )

    def _save_native_settings(self) -> None:
        if self.native_settings is None:
            self._select_something("Load native Astrill settings first.")
            return
        try:
            changes = self.native_page.collect_changes()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid native Astrill setting", str(exc))
            return
        if not changes:
            self.statusBar().showMessage("Native Astrill settings are unchanged.", 4000)
            return
        labels = "\n".join(f"  • {key}" for key in sorted(changes))
        if (
            QMessageBox.warning(
                self,
                "Save native Astrill settings",
                f"Validate and write {len(changes)} changed native setting(s) "
                f"to DD-WRT?\n\n{labels}\n\nThe router will commit once, then "
                "the app will read every changed value back before reporting "
                "success. Astrill account and router credentials are outside "
                "this page's allowlist.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
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
            self.controller.reconcile_status,
            self._status_loaded,
            quiet=quiet,
        )

    def _status_loaded(self, status: object) -> None:
        self.router_status = dict(status)  # type: ignore[arg-type]
        self.refresh_mode_label.setText(
            f"Updated {QTime.currentTime().toString('HH:mm')} · manual only"
        )
        protocol = int(self.router_status.get("astrill_protocol", 0) or 0)
        if (
            not self._endpoint_protocol_user_selected
            and 0 <= protocol < self.protocol.count()
        ):
            self.protocol.blockSignals(True)
            self.protocol.setCurrentIndex(protocol)
            self.protocol.blockSignals(False)
        self.raw_status.setPlainText(
            json.dumps(self.router_status, indent=2, sort_keys=True)
        )
        self._update_status_metrics()
        self._render_endpoints()
        self._render_countries()
        if self.controller.recovery_notice:
            notice = self.controller.recovery_notice
            QTimer.singleShot(
                0,
                lambda: self.statusBar().showMessage(notice, 12000),
            )

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
        was_read_only = self.controller.store.read_only
        guard_detail = (
            "\n\nThis confirmation also turns off the local read-only guard. "
            "The guard is restored automatically if installation fails."
            if was_read_only
            else ""
        )
        if (
            QMessageBox.warning(
                self,
                "Install DD-WRT companion",
                "This writes the validated companion package, startup hook, "
                "watchdog, routes, and MyPage entries to DD-WRT. It does not "
                "change Astrill account credentials or the selected endpoint."
                f"{guard_detail}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if was_read_only:
            self.controller.set_read_only(False)
            self._sync_access_ui()

        def installed(result: object) -> None:
            self._status_loaded(result.status)  # type: ignore[attr-defined]

        def install() -> object:
            try:
                return self.controller.install_companion()
            except Exception:
                if was_read_only:
                    self.controller.set_read_only(True)
                raise

        self._run_task(
            "Installing DD-WRT companion",
            install,
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
        if not self._save_router_fields():
            return
        self._run_task(
            "Testing key-only SSH",
            self.controller.test_connection,
            self._key_access_tested,
        )

    def _setup_key_via_telnet(self) -> None:
        if not self._save_router_fields():
            return
        if self.controller.store.router_use_ssh_config:
            QMessageBox.warning(
                self,
                "Guided key setup",
                "Disable OpenSSH config mode and use the explicit host, user, "
                "port, and private-key fields for guided setup.",
            )
            return
        self.ssh_setup_status.setText("Inspecting the router SSH host key...")
        self._run_task(
            "Inspecting router SSH fingerprint",
            self.controller.inspect_router_host_key,
            self._router_host_key_inspected,
        )

    def _router_host_key_inspected(self, result: object) -> None:
        if not isinstance(result, WindowsHostKey):
            return
        if result.trust_state == "changed":
            self.ssh_setup_status.setText("Blocked: the saved SSH host key changed.")
            QMessageBox.critical(
                self,
                "SSH host key changed",
                "The router presents a different SSH key than the pinned key. "
                "No password or public key was sent. Verify the router through "
                "a trusted path before replacing any known_hosts entry.",
            )
            return
        state = {
            "trusted": "already pinned",
            "additional": "an additional key type",
            "unknown": "not yet trusted",
        }.get(result.trust_state, result.trust_state)
        detail = (
            f"Router: {result.host}:{result.port}\n"
            f"Algorithm: {result.key_type}\n"
            f"Fingerprint: {result.fingerprint}\n"
            f"Local state: {state}\n\n"
            "Confirm only if this fingerprint belongs to your DD-WRT router.\n\n"
            "Continuing uses Telnet port 23 once. Telnet sends the supplied "
            "password unencrypted over the local network. The app sends only "
            "the generated public key, verifies strict key-only SSH, then "
            "disables SSH password and WAN SSH access. Telnet remains enabled "
            "as a recovery path."
        )
        answer = QMessageBox.warning(
            self,
            "Confirm router fingerprint and Telnet setup",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.ssh_setup_status.setText(
                "Cancelled before trusting the host key or requesting a password."
            )
            return
        password, accepted = QInputDialog.getText(
            self,
            "One-time router Telnet password",
            f"Password for {self.controller.store.router_user}@"
            f"{self.controller.store.router_host} (used once, never saved):",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            self.ssh_setup_status.setText("Cancelled before sending a Telnet password.")
            return
        if not password:
            QMessageBox.warning(
                self,
                "Router key setup",
                "The router Telnet password cannot be empty.",
            )
            return
        self.ssh_setup_status.setText("Generating and authorizing the dedicated key...")
        self._run_task(
            "Authorizing key through router Telnet",
            lambda supplied_password=password: (
                self.controller.authorize_router_key_via_telnet(
                    result,
                    supplied_password,
                    confirmed=True,
                )
            ),
            self._router_key_authorized,
        )
        password = ""

    def _router_key_authorized(self, result: object) -> None:
        if not isinstance(result, WindowsKeyAuthorization):
            return
        hardening = (
            "SSH password login and WAN SSH are disabled."
            if result.password_login_disabled
            else "SSH password login remains enabled."
        )
        self.ssh_setup_status.setText(
            f"Key-only SSH verified with {result.identity_file}. {hardening}"
        )
        QMessageBox.information(
            self,
            "Router key access is ready",
            "The dedicated public key was authorized and strict key-only SSH "
            f"was verified. {hardening} Telnet remains enabled for recovery.",
        )
        self._refresh_status()

    def _key_access_tested(self, ready: object) -> None:
        if bool(ready):
            self.ssh_setup_status.setText("Key-only SSH is verified and ready.")
        QMessageBox.information(
            self,
            "Router SSH",
            "Key-only SSH is ready."
            if ready
            else "The router did not return the expected response.",
        )

    def _save_router_fields(self) -> bool:
        try:
            self.controller.configure_router(
                self.host_entry.text(),
                user=self.user_entry.text(),
                port=self.port_entry.value(),
                identity_file=self.identity_entry.text(),
                use_ssh_config=self.ssh_config_check.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid SSH target", str(exc))
            return False
        self._show_saved_router_fields()
        return True

    def _open_ssh_setup(self) -> None:
        if not self._save_router_fields():
            return
        from PySide6.QtCore import QProcess

        arguments = [
            "/k",
            "ssh.exe",
            "-o",
            "StrictHostKeyChecking=ask",
        ]
        if self.controller.store.router_use_ssh_config:
            arguments.append(self.controller.store.router_host)
        else:
            target = (
                f"{self.controller.store.router_user}@"
                f"{self.controller.store.router_host}"
            )
            arguments.extend(("-p", str(self.controller.store.router_port)))
            identity = Path(self.controller.store.router_identity).expanduser()
            if identity.is_file():
                arguments.extend(("-i", str(identity)))
            arguments.extend(
                (
                    "-o",
                    f"UserKnownHostsFile={_openssh_config_path(self.controller.known_hosts_path)}",
                )
            )
            arguments.append(target)
        if not QProcess.startDetached(
            "cmd.exe",
            arguments,
        ):
            QMessageBox.warning(
                self,
                "SSH setup",
                "Could not open the interactive Windows SSH terminal.",
            )

    def _show_saved_router_fields(self) -> None:
        self.host_entry.setText(self.controller.store.router_host)
        self.user_entry.setText(self.controller.store.router_user)
        self.port_entry.setValue(self.controller.store.router_port)
        self.identity_entry.setText(self.controller.store.router_identity)
        self.ssh_config_check.setChecked(self.controller.store.router_use_ssh_config)
        self._sync_ssh_fields()

    def _sync_ssh_fields(self) -> None:
        explicit = not self.ssh_config_check.isChecked()
        self.user_entry.setEnabled(explicit)
        self.port_entry.setEnabled(explicit)
        self.identity_entry.setEnabled(explicit)
        self.setup_key_button.setEnabled(explicit and self.busy_count == 0)

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
        self.native_page.set_read_only(read_only)
        self.native_page.set_busy(self.busy_count != 0)
        self.refresh_button.setEnabled(self.busy_count == 0)
        idle = self.busy_count == 0
        if hasattr(self, "companion_action_buttons"):
            self.companion_action_buttons["Install / upgrade"].setEnabled(idle)
            for label in (
                "Repair",
                "Refresh domains",
                "Roll back",
                "Restore native only",
            ):
                self.companion_action_buttons[label].setEnabled(companion_writable)
        self._sync_endpoint_action_ui()
        self._sync_ssh_fields()

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
