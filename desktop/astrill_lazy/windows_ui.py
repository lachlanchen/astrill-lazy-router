from __future__ import annotations

import ctypes
import ipaddress
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from time import time
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
from PySide6.QtGui import QColor, QFont, QMouseEvent
from PySide6.QtNetwork import QNetworkInformation
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
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillFavorite,
    AstrillServer,
    parse_astrill_favorites,
)
from .compiler import MAX_COMPILED_BYTES
from .endpoint_list import (
    EndpointListRow,
    sort_endpoint_rows,
    sort_endpoint_rows_by_header,
)
from .endpoint_probe import EndpointProbeResult, EndpointProbeStatus, probe_servers
from .endpoint_probe_store import (
    SavedEndpointProbe,
    SavedProbeState,
    assess_saved_endpoint_probe,
    endpoint_probe_cache_path,
    load_endpoint_probe_cache,
    save_endpoint_probe_cache,
)
from .models import MatchKind, RouteTarget, Rule, Service
from .native_settings import NativeAstrillSettings
from .router import AstrillConnectionResult, _openssh_config_path
from .service_policy import ServiceRouteMode
from .windows_connection_page import ConnectionDraft, WindowsConnectionPage
from .windows_controller import (
    MAX_OVERLAY_BYTES,
    ControllerError,
    HybridPolicyComparison,
    PolicyCompilationSummary,
    PolicyRuntimeSummary,
    ServerCatalog,
    WindowsConnectionState,
    WindowsController,
    summarize_policy_runtime,
)
from .windows_native_page import WindowsNativeSettingsPage
from .windows_ssh_setup import WindowsHostKey, WindowsKeyAuthorization

APP_NAME = "Astrill Lazy Router"

ENDPOINT_SELECT_COLUMN = 0
ENDPOINT_NAME_COLUMN = 1
ENDPOINT_REGION_COLUMN = 2
ENDPOINT_FAVORITE_COLUMN = 3
ENDPOINT_SERVER_ID_COLUMN = 4
ENDPOINT_ROUTER_STATE_COLUMN = 5
ENDPOINT_NODES_COLUMN = 6
ENDPOINT_LATENCY_COLUMN = 7
ENDPOINT_REACH_COLUMN = 8
ENDPOINT_TESTED_COLUMN = 9
ENDPOINT_COLUMN_COUNT = 10
ENDPOINT_LATENCY_RESULT_COLUMNS = (
    ENDPOINT_LATENCY_COLUMN,
    ENDPOINT_REACH_COLUMN,
    ENDPOINT_TESTED_COLUMN,
)
ENDPOINT_HEADER_SORT_FIELDS = {
    ENDPOINT_SELECT_COLUMN: "selected",
    ENDPOINT_NAME_COLUMN: "endpoint",
    ENDPOINT_REGION_COLUMN: "region",
    ENDPOINT_FAVORITE_COLUMN: "favorite",
    ENDPOINT_SERVER_ID_COLUMN: "server_id",
    ENDPOINT_ROUTER_STATE_COLUMN: "router_state",
    ENDPOINT_NODES_COLUMN: "nodes",
    ENDPOINT_LATENCY_COLUMN: "latency",
    ENDPOINT_REACH_COLUMN: "reach",
    ENDPOINT_TESTED_COLUMN: "tested",
}
ENDPOINT_HEADER_DEFAULT_DESCENDING = {
    ENDPOINT_SELECT_COLUMN: True,
    ENDPOINT_NAME_COLUMN: False,
    ENDPOINT_REGION_COLUMN: False,
    ENDPOINT_FAVORITE_COLUMN: True,
    ENDPOINT_SERVER_ID_COLUMN: False,
    ENDPOINT_ROUTER_STATE_COLUMN: True,
    ENDPOINT_NODES_COLUMN: True,
    ENDPOINT_LATENCY_COLUMN: False,
    ENDPOINT_REACH_COLUMN: False,
    ENDPOINT_TESTED_COLUMN: True,
}

HYBRID_POLICY_CAPABILITIES = frozenset(
    {
        "hybrid-policy-storage",
        "policy-storage-v2",
        "policy.core",
        "policy.overlays",
        "policy_storage.hybrid",
        "policy_storage.hybrid_v1",
    }
)

HYBRID_CONTROLLER_METHODS = {
    "pin": ("apply_persistent_core",),
    "load": ("load_ram_overlay",),
    "restore": ("restore_ram_overlay_now",),
    "remove": ("remove_ram_overlay",),
    "auto_restore": ("set_overlay_restore_enabled",),
}


class EndpointTreeWidget(QTreeWidget):
    """Tree with independent Select and Favorite cell actions."""

    selectCellClicked = Signal(object)
    favoriteCellClicked = Signal(object)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        position = event.position().toPoint()
        item = self.itemAt(position)
        if item is not None:
            column = self.columnAt(position.x())
            if column == ENDPOINT_SELECT_COLUMN:
                self.selectCellClicked.emit(item)
                event.accept()
                return
            if (
                column == ENDPOINT_FAVORITE_COLUMN
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self.favoriteCellClicked.emit(item)
                event.accept()
                return
        super().mousePressEvent(event)


class ServiceTreeWidget(QTreeWidget):
    """Tree whose first column toggles durable batch selection."""

    selectCellClicked = Signal(object)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        position = event.position().toPoint()
        item = self.itemAt(position)
        if item is not None and self.columnAt(position.x()) == 0:
            self.selectCellClicked.emit(item)
            event.accept()
            return
        super().mousePressEvent(event)


COLORS = {
    "window": "#f5f3ff",
    "sidebar": "#111827",
    "sidebar_hover": "#3730a3",
    "sidebar_active": "#7c3aed",
    "card": "#ffffff",
    "border": "#d8d5ff",
    "text": "#111827",
    "muted": "#5b677a",
    "primary": "#6d28d9",
    "primary_dark": "#5b21b6",
    "green": "#047857",
    "blue": "#0284c7",
    "orange": "#c2410c",
    "red": "#b91c1c",
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
QGroupBox#policyStorageGroup {{
    margin-top: 7px;
    padding: 6px;
}}
QLabel.storageLayerTitle {{
    color: {COLORS["muted"]};
    font-size: 8.5pt;
    font-weight: 700;
}}
QLabel.storageLayerValue {{
    font-size: 9.5pt;
    font-weight: 700;
}}
QLabel[storageTone="neutral"] {{
    color: {COLORS["muted"]};
}}
QLabel[storageTone="green"] {{
    color: {COLORS["green"]};
}}
QLabel[storageTone="amber"] {{
    color: {COLORS["orange"]};
}}
QLabel[storageTone="red"] {{
    color: {COLORS["red"]};
}}
QLabel#overlaySourceState {{
    font-size: 8.5pt;
}}
QFrame#latencyCard {{
    background: #ecfeff;
    border: 1px solid #67e8f9;
    border-left: 5px solid #06b6d4;
    border-radius: 12px;
}}
QFrame#favoriteCard {{
    background: #f5f3ff;
    border: 1px solid #c4b5fd;
    border-left: 5px solid #7c3aed;
    border-radius: 12px;
}}
QLabel#favoriteTitle {{
    color: #5b21b6;
    font-size: 12pt;
    font-weight: 800;
}}
QLabel#favoriteStatus {{
    color: #6d28d9;
    font-weight: 600;
}}
QPushButton#favoriteAction {{
    background: #7c3aed;
    border-color: #7c3aed;
    color: #ffffff;
}}
QPushButton#favoriteAction:hover {{
    background: #6d28d9;
}}
QPushButton#favoriteAction:disabled {{
    color: #94a3b8;
    background: #e2e8f0;
    border-color: #cbd5e1;
}}
QDialog#endpointLatencyDialog {{
    background: {COLORS["window"]};
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
QLabel.sectionTitle {{
    font-size: 12pt;
    font-weight: 800;
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
QTabWidget#nativeSectionTabs::pane {{
    background: #ffffff;
    border: 1px solid #c4b5fd;
    border-radius: 12px;
    top: -1px;
}}
QTabWidget#nativeSectionTabs QTabBar::tab {{
    color: #4338ca;
    background: #ede9fe;
    border: 1px solid #c4b5fd;
    border-bottom: none;
    padding: 10px 15px;
    margin-right: 3px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 700;
}}
QTabWidget#nativeSectionTabs QTabBar::tab:selected {{
    color: white;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #7c3aed,
        stop: 1 #0891b2
    );
    border-color: #6d28d9;
}}
QTabWidget#nativeSectionTabs QTabBar::tab:hover:!selected {{
    background: #ddd6fe;
}}
QFrame#connectionConflictBanner {{
    background: #fff7ed;
    border: 1px solid #fb923c;
    border-left: 5px solid #ea580c;
    border-radius: 10px;
}}
QFrame#connectionActionBanner {{
    background: #eef2ff;
    border: 1px solid #a5b4fc;
    border-left: 5px solid #6366f1;
    border-radius: 10px;
}}
QFrame#connectionActionBanner[level="success"] {{
    background: #ecfdf5;
    border-color: #34d399;
    border-left-color: #059669;
}}
QFrame#connectionActionBanner[level="warning"] {{
    background: #fff7ed;
    border-color: #fb923c;
    border-left-color: #ea580c;
}}
QFrame#connectionActionBanner[level="error"] {{
    background: #fff1f2;
    border-color: #fb7185;
    border-left-color: #dc2626;
}}
QLabel.connectionState {{
    color: #dc2626;
    font-size: 12pt;
    font-weight: 800;
}}
QLabel.connectionState[connected="true"] {{
    color: #059669;
}}
QLabel.guardNotice {{
    color: #9a3412;
    background: #ffedd5;
    border-radius: 8px;
    padding: 9px 11px;
    font-weight: 600;
}}
QGroupBox#connectionStatusPanel {{
    border-top: 4px solid #06b6d4;
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
        ("connection", "Connection", "Mirrored shared Astrill tunnel controls"),
        (
            "endpoints",
            "Endpoints",
            "DD-WRT shared tunnel only · Windows routing is unchanged",
        ),
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
        self.policy_preflight: PolicyCompilationSummary | None = None
        self._syncing_policy_storage = False
        self._overlay_source_user_edited = False
        self._network_information: QNetworkInformation | None = None
        self._network_was_offline = False
        self._network_recovery_scheduled = False
        self._network_recovery_pending = False
        self._network_recovery_used = False
        self._network_recovery_generation = 0
        self.clients: list[dict[str, Any]] = []
        self._selected_service_ids: set[str] = set()
        self._syncing_service_selection = False
        self._clients_loading = False
        self._clients_loaded = False
        self._endpoint_catalog_loading = False
        self._endpoint_catalog_loaded = False
        self._endpoint_probe_running = False
        self._endpoint_selected_server_id: int | None = None
        self._endpoint_selected_server_ids: set[int] = set()
        self._endpoint_selection_user_managed = False
        self._syncing_endpoint_selection = False
        self._endpoint_header_sort_column: int | None = None
        self._endpoint_header_sort_descending = False
        self._syncing_endpoint_sort = False
        self._endpoint_probe_cache_path = endpoint_probe_cache_path(
            self.controller.store.path
        )
        self._endpoint_probe_results = load_endpoint_probe_cache(
            self._endpoint_probe_cache_path
        )
        self._endpoint_favorite_records: dict[int, AstrillFavorite] = {}
        self._endpoint_favorites_valid: bool | None = None
        self.native_settings: NativeAstrillSettings | None = None
        self._native_settings_loading = False
        self._syncing_access = False
        self._syncing_endpoint_preferences = False
        self._endpoint_native_pending: set[str] = set()
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
        self._setup_network_recovery_hook()
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
        self.policies_scroll = self._scrollable_page(self._build_policies_page())
        self.stack.addWidget(self.policies_scroll)
        self.stack.addWidget(self._build_services_page())
        self.stack.addWidget(self._build_countries_page())
        self.stack.addWidget(self._build_devices_page())
        self.stack.addWidget(self._scrollable_page(self._build_connection_page()))
        self.stack.addWidget(self._build_endpoints_page())
        self.stack.addWidget(self._scrollable_page(self._build_astrill_page()))
        self.stack.addWidget(self._build_router_page())
        self.stack.addWidget(self._scrollable_page(self._build_settings_page()))

    def _build_connection_page(self) -> QWidget:
        self.connection_page = WindowsConnectionPage(
            on_refresh=self._refresh_connection_page,
            on_save=self._save_connection_page,
            on_connect=self._connect_connection_page,
            on_apply_reconnect=self._apply_connection_page,
            on_disconnect=self._disconnect_connection_page,
            on_dirty_changed=self._connection_draft_dirty_changed,
        )
        return self.connection_page

    def _connection_draft_dirty_changed(self, _dirty: bool) -> None:
        self._sync_cross_editor_guards()
        self._sync_endpoint_connection_controls()
        self._sync_endpoint_action_ui()

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
        layout.setSpacing(10)

        metrics = QHBoxLayout()
        self.metric_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("controller", "Controller"),
            ("tunnel", "Astrill tunnel"),
            ("endpoint", "Active endpoint"),
            ("rules", "Local / applied policies"),
        ):
            card = QFrame()
            card.setObjectName(f"metric_{key}")
            card.setProperty("class", "card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 8, 14, 8)
            card_layout.setSpacing(2)
            value = QLabel("...")
            value.setProperty("class", "metricValue")
            label = QLabel(caption)
            label.setProperty("class", "metricCaption")
            card_layout.addWidget(value)
            card_layout.addWidget(label)
            metrics.addWidget(card, 1)
            self.metric_labels[key] = value
        layout.addLayout(metrics)

        self.policy_storage_group = self._build_policy_storage_panel()

        toolbar = QHBoxLayout()
        policy_heading = QLabel("Traffic Policies")
        policy_heading.setProperty("class", "sectionTitle")
        toolbar.addWidget(policy_heading)
        self.apply_selected_button = QPushButton("Apply selected")
        self.apply_selected_button.setToolTip(
            "Install only the selected policy rows on this router. Other policies "
            "remain saved in the Windows configuration."
        )
        self.apply_selected_button.clicked.connect(self._apply_selected_policies)
        toolbar.addWidget(self.apply_selected_button)
        toolbar.addStretch(1)
        add_service = QPushButton("Add service…")
        add_service.setObjectName("primary")
        add_service.setToolTip(
            "Open the service catalog, where provider-country filters and "
            "batch selection are available."
        )
        add_service.clicked.connect(self._show_services_for_policy)
        toolbar.addWidget(add_service)
        add = QPushButton("Add custom…")
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
        layout.addWidget(self.policy_storage_group)

        self.policy_empty_note = QLabel(
            "No local policies are saved yet. Select services, choose a route, "
            "then use Add to Policies. Apply policies is a separate router step."
        )
        self.policy_empty_note.setWordWrap(True)
        self.policy_empty_note.setProperty("class", "muted")
        layout.addWidget(self.policy_empty_note)
        self.policy_capacity_state = QLabel("")
        self.policy_capacity_state.setWordWrap(True)
        self.policy_capacity_state.setProperty("class", "muted")
        layout.addWidget(self.policy_capacity_state)
        self.policy_sync_state = QLabel("")
        self.policy_sync_state.setWordWrap(True)
        self.policy_sync_state.setProperty("class", "muted")
        layout.addWidget(self.policy_sync_state)

        self.policy_tree = QTreeWidget()
        self.policy_tree.setHeaderLabels(
            ["Policy", "Type", "Selector", "Route", "Region", "Priority", "State"]
        )
        self.policy_tree.setAlternatingRowColors(True)
        self.policy_tree.setRootIsDecorated(False)
        self.policy_tree.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self.policy_tree.setMinimumHeight(145)
        self.policy_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.policy_tree.itemSelectionChanged.connect(self._policy_selection_changed)
        self.policy_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._edit_policy()
        )
        header = self.policy_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.policy_tree, 1)
        # The Policies page is hosted in a vertical scroll area. Keep enough
        # internal height for the complete storage panel and a usable policy
        # table; smaller windows scroll the page instead of compressing controls
        # until they overlap.
        page.setMinimumHeight(601)
        return page

    def _build_policy_storage_panel(self) -> QGroupBox:
        group = QGroupBox("Router policy storage")
        group.setObjectName("policyStorageGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 7, 8, 6)
        layout.setSpacing(5)

        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setHorizontalSpacing(10)
        status_grid.setVerticalSpacing(2)
        self.policy_storage_cells: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            (
                ("local", "Local library"),
                ("core", "Persistent core"),
                ("this_overlay", "This computer RAM overlay"),
                ("other_overlays", "Other overlays"),
                ("effective", "Effective router"),
            )
        ):
            heading = QLabel(title)
            heading.setProperty("class", "storageLayerTitle")
            heading.setWordWrap(True)
            value = QLabel("Not reported")
            value.setProperty("class", "storageLayerValue")
            value.setProperty("storageTone", "neutral")
            value.setWordWrap(True)
            value.setAccessibleName(f"{title} status")
            status_grid.addWidget(heading, 0, column)
            status_grid.addWidget(value, 1, column)
            status_grid.setColumnStretch(column, 1)
            self.policy_storage_cells[key] = value
        layout.addLayout(status_grid)

        source_grid = QGridLayout()
        source_grid.setContentsMargins(0, 0, 0, 0)
        source_grid.setHorizontalSpacing(8)
        source_grid.addWidget(QLabel("RAM source"), 0, 0)
        self.policy_overlay_source = QLineEdit()
        self.policy_overlay_source.setPlaceholderText(
            "auto (recommended), or an advanced IPv4 host/CIDR override"
        )
        self.policy_overlay_source.setText("auto")
        self.policy_overlay_source.setMaximumWidth(360)
        self.policy_overlay_source.textEdited.connect(
            self._policy_overlay_source_edited
        )
        self.policy_overlay_source.textChanged.connect(
            self._policy_overlay_source_changed
        )
        self.policy_overlay_source.editingFinished.connect(
            self._normalize_policy_overlay_source
        )
        source_grid.addWidget(self.policy_overlay_source, 0, 1)
        source_grid.setColumnStretch(1, 1)
        self.policy_overlay_source_state = QLabel(
            "A source binding is required for RAM actions."
        )
        self.policy_overlay_source_state.setObjectName("overlaySourceState")
        self.policy_overlay_source_state.setProperty("storageTone", "red")
        self.policy_overlay_source_state.setWordWrap(True)
        source_grid.addWidget(self.policy_overlay_source_state, 1, 1, 1, 2)
        self.policy_auto_restore_check = QCheckBox("Auto-restore after router reboot")
        self.policy_auto_restore_check.setToolTip(
            "Explicit opt-in: after a new router runtime is observed, restore "
            "only this computer's source-bound overlay once. No periodic polling."
        )
        self.policy_auto_restore_check.toggled.connect(
            self._policy_auto_restore_toggled
        )
        source_grid.addWidget(self.policy_auto_restore_check, 0, 2)
        layout.addLayout(source_grid)

        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(7)
        self.pin_core_button = QPushButton("Replace persistent core")
        self.pin_core_button.setToolTip(
            "Replace the complete persistent-core document with the selected "
            "policies. This writes router NVRAM."
        )
        self.pin_core_button.clicked.connect(self._pin_selected_to_core)
        actions.addWidget(self.pin_core_button, 0, 0)
        self.load_ram_button = QPushButton("Load selected into RAM")
        self.load_ram_button.setObjectName("primary")
        self.load_ram_button.setToolTip(
            "Load the selected policies into this computer's volatile, "
            "source-bound router overlay without an NVRAM commit."
        )
        self.load_ram_button.clicked.connect(self._load_selected_into_ram)
        actions.addWidget(self.load_ram_button, 0, 1)
        self.restore_ram_button = QPushButton("Restore RAM overlay now")
        self.restore_ram_button.clicked.connect(self._restore_ram_overlay_now)
        actions.addWidget(self.restore_ram_button, 0, 2)
        self.remove_overlay_button = QPushButton("Remove this overlay")
        self.remove_overlay_button.setObjectName("danger")
        self.remove_overlay_button.clicked.connect(self._remove_this_overlay)
        actions.addWidget(self.remove_overlay_button, 0, 3)
        for column in range(4):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        self.policy_storage_legend = QLabel(
            "Neutral = intentionally local · Amber = RAM restore needed · "
            "Red = router protection missing or failed"
        )
        self.policy_storage_legend.setProperty("class", "muted")
        layout.addWidget(self.policy_storage_legend)

        group.hide()
        return group

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

        filter_row = QHBoxLayout()
        self.service_category_filter = QComboBox()
        self.service_category_filter.setToolTip("Filter by service category")
        self.service_category_filter.addItem("All categories", "all")
        for category in sorted(
            {service.category for service in self.controller.catalog.services},
            key=str.casefold,
        ):
            self.service_category_filter.addItem(category, category)
        self.service_category_filter.currentIndexChanged.connect(self._render_services)
        filter_row.addWidget(self.service_category_filter)

        self.service_profile_filter = QComboBox()
        self.service_profile_filter.setToolTip("Filter by profile type")
        self.service_profile_filter.addItem("All profiles", "all")
        profile_types = sorted(
            {service.profile_type for service in self.controller.catalog.services},
            key=str.casefold,
        )
        for profile_type in profile_types:
            label = {
                "app": "Apps",
                "company": "Companies",
                "website": "Websites",
            }.get(profile_type, profile_type.replace("-", " ").title())
            self.service_profile_filter.addItem(label, profile_type)
        self.service_profile_filter.currentIndexChanged.connect(self._render_services)
        filter_row.addWidget(self.service_profile_filter)

        self.service_country_filter = QComboBox()
        self.service_country_filter.setToolTip("Filter by provider country")
        self.service_country_filter.addItem("All countries", "all")
        for country in sorted(
            {service.provider_country for service in self.controller.catalog.services},
            key=str.casefold,
        ):
            self.service_country_filter.addItem(country, country)
        self.service_country_filter.currentIndexChanged.connect(self._render_services)
        filter_row.addWidget(self.service_country_filter)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        batch = QHBoxLayout()
        self.service_select_visible = QCheckBox("Select visible")
        self.service_select_visible.setTristate(True)
        self.service_select_visible.setToolTip(
            "Select every service matching the current search and filters"
        )
        self.service_select_visible.checkStateChanged.connect(
            self._toggle_visible_service_selection
        )
        batch.addWidget(self.service_select_visible)
        self.service_clear_selection_button = QPushButton("Clear selection")
        self.service_clear_selection_button.clicked.connect(
            self._clear_service_selection
        )
        batch.addWidget(self.service_clear_selection_button)
        self.service_selection_count = QLabel("0 services selected")
        self.service_selection_count.setProperty("class", "muted")
        batch.addWidget(self.service_selection_count)
        batch.addStretch(1)
        batch.addWidget(QLabel("Route:"))
        self.service_route_mode = QComboBox()
        self.service_route_mode.addItem("Suggested", ServiceRouteMode.SUGGESTED)
        self.service_route_mode.addItem("Direct", ServiceRouteMode.DIRECT)
        self.service_route_mode.addItem("Astrill", ServiceRouteMode.VPN)
        batch.addWidget(self.service_route_mode)
        self.service_add_selected_button = QPushButton("Add to Policies")
        self.service_add_selected_button.setObjectName("primary")
        self.service_add_selected_button.setToolTip(
            "Save selected service policies locally; Apply policies writes them "
            "to the router"
        )
        self.service_add_selected_button.clicked.connect(
            lambda: self._add_services(
                ServiceRouteMode(str(self.service_route_mode.currentData()))
            )
        )
        batch.addWidget(self.service_add_selected_button)
        layout.addLayout(batch)

        self.service_tree = ServiceTreeWidget()
        self.service_tree.setHeaderLabels(
            [
                "Select",
                "Service",
                "Company",
                "Category",
                "Profile",
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
        self.service_tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.service_tree.itemChanged.connect(self._service_item_changed)
        self.service_tree.itemSelectionChanged.connect(
            self._service_row_selection_changed
        )
        self.service_tree.selectCellClicked.connect(self._service_select_cell_clicked)
        header = self.service_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in range(3, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.service_tree, 1)
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
        self.country_banner = QLabel("")
        self.country_banner.setWordWrap(True)
        self.country_banner.setObjectName("accessBanner")
        self.country_banner.hide()
        layout.addWidget(self.country_banner)
        self.country_result_count = QLabel("")
        self.country_result_count.setProperty("class", "muted")
        layout.addWidget(self.country_result_count)
        self.country_tree = QTreeWidget()
        self.country_tree.setHeaderLabels(
            ["Region", "Kind", "Policy summary", "Known endpoints", "Active"]
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
        self.country_tree.itemDoubleClicked.connect(
            lambda item, _column: self._open_region_endpoints(
                str(item.data(0, Qt.ItemDataRole.UserRole))
            )
        )
        self.country_tree.setToolTip(
            "Double-click an Astrill region to open its endpoints."
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
        layout.setSpacing(9)

        search_row = QHBoxLayout()
        self.endpoint_search = QLineEdit()
        self.endpoint_search.setPlaceholderText(
            "Search endpoints by name, country, region, or server ID"
        )
        self.endpoint_search.textChanged.connect(self._render_endpoints)
        search_row.addWidget(self.endpoint_search, 1)
        self.load_endpoints_button = QPushButton("Load endpoints")
        self.load_endpoints_button.clicked.connect(self._load_endpoints)
        search_row.addWidget(self.load_endpoints_button)
        layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Country"))
        self.endpoint_country_filter = QComboBox()
        self.endpoint_country_filter.setToolTip(
            "Show endpoints from one exact country; selections hidden by the "
            "filter stay selected."
        )
        self.endpoint_country_filter.addItem("All countries", "")
        self.endpoint_country_filter.setEnabled(False)
        self.endpoint_country_filter.currentIndexChanged.connect(self._render_endpoints)
        filter_row.addWidget(self.endpoint_country_filter)
        filter_row.addWidget(QLabel("Protocol"))
        self.protocol = QComboBox()
        self.protocol.addItems(list(ASTRILL_PROTOCOL_NAMES))
        self.protocol.activated.connect(self._endpoint_protocol_selected)
        self.protocol.currentIndexChanged.connect(self._endpoint_protocol_changed)
        filter_row.addWidget(self.protocol)
        filter_row.addWidget(QLabel("Sort"))
        self.endpoint_sort = QComboBox()
        self.endpoint_sort.addItem("Default order", "default")
        self.endpoint_sort.addItem("Region (A–Z)", "region")
        self.endpoint_sort.addItem("PC latency (fastest)", "latency")
        self.endpoint_sort.addItem("Header: Endpoint (A–Z)", "header")
        self.endpoint_sort.setToolTip(
            "Choose a preset or click any table header to order that column."
        )
        self.endpoint_sort.currentIndexChanged.connect(self._endpoint_sort_changed)
        filter_row.addWidget(self.endpoint_sort)
        filter_row.addStretch(1)
        self.endpoint_latency_dialog_button = QPushButton("PC latency…")
        self.endpoint_latency_dialog_button.setToolTip(
            "Open the manual PC latency test. Saved results remain visible in "
            "the endpoint table."
        )
        self.endpoint_latency_dialog_button.clicked.connect(
            self._show_endpoint_latency_dialog
        )
        filter_row.addWidget(self.endpoint_latency_dialog_button)
        self.connect_endpoint_button = QPushButton("Connect selected")
        self.connect_endpoint_button.setObjectName("primary")
        self.connect_endpoint_button.setToolTip(
            "Connect the router's shared Astrill tunnel to the one selected endpoint."
        )
        self.connect_endpoint_button.clicked.connect(self._connect_endpoint)
        filter_row.addWidget(self.connect_endpoint_button)
        layout.addLayout(filter_row)

        favorite_card = QFrame()
        favorite_card.setObjectName("favoriteCard")
        favorite_layout = QVBoxLayout(favorite_card)
        favorite_layout.setContentsMargins(14, 9, 14, 9)
        favorite_layout.setSpacing(6)

        favorite_heading = QHBoxLayout()
        favorite_heading.setSpacing(8)
        favorite_title = QLabel("Router favorites")
        favorite_title.setObjectName("favoriteTitle")
        favorite_heading.addWidget(favorite_title)
        self.endpoint_favorite_status = QLabel(
            "Not synced · load endpoints or select Sync favorites."
        )
        self.endpoint_favorite_status.setObjectName("favoriteStatus")
        self.endpoint_favorite_status.setWordWrap(False)
        favorite_heading.addWidget(self.endpoint_favorite_status, 1)
        self.endpoint_favorite_sync_button = QPushButton("Sync from router")
        self.endpoint_favorite_sync_button.setToolTip(
            "Read Astrill's current favorites from DD-WRT now. "
            "No background polling is used."
        )
        self.endpoint_favorite_sync_button.clicked.connect(
            self._sync_endpoint_favorites
        )
        favorite_heading.addWidget(self.endpoint_favorite_sync_button)
        favorite_layout.addLayout(favorite_heading)

        favorite_actions = QHBoxLayout()
        favorite_actions.setSpacing(7)
        self.endpoint_select_visible = QCheckBox("Select visible")
        self.endpoint_select_visible.setTristate(True)
        self.endpoint_select_visible.setToolTip(
            "Select every endpoint matching the current search and filters. "
            "Hidden selections are preserved."
        )
        self.endpoint_select_visible.checkStateChanged.connect(
            self._toggle_visible_endpoint_selection
        )
        favorite_actions.addWidget(self.endpoint_select_visible)
        self.endpoint_clear_selection_button = QPushButton("Clear")
        self.endpoint_clear_selection_button.setToolTip(
            "Clear all selected endpoints, including selections hidden by filters."
        )
        self.endpoint_clear_selection_button.clicked.connect(
            self._clear_endpoint_selection
        )
        favorite_actions.addWidget(self.endpoint_clear_selection_button)
        self.endpoint_selection_status = QLabel("0 selected")
        self.endpoint_selection_status.setObjectName("favoriteStatus")
        self.endpoint_selection_status.setWordWrap(False)
        self.endpoint_selection_status.setToolTip(
            "Use row checkboxes, Ctrl/Command, or Shift. Selections hidden by "
            "search or country filters are preserved."
        )
        favorite_actions.addWidget(self.endpoint_selection_status)
        favorite_actions.addStretch(1)
        self.endpoint_behavior_dialog_button = QPushButton("Behavior…")
        self.endpoint_behavior_dialog_button.setToolTip(
            "Open favorite failover and router-boot connection settings."
        )
        self.endpoint_behavior_dialog_button.clicked.connect(
            self._show_endpoint_behavior_dialog
        )
        favorite_actions.addWidget(self.endpoint_behavior_dialog_button)
        favorite_layout.addLayout(favorite_actions)

        favorite_buttons = QHBoxLayout()
        favorite_buttons.setSpacing(7)
        favorite_buttons.addStretch(1)
        self.endpoint_favorite_button = QPushButton("Favorite selected")
        self.endpoint_favorite_button.setObjectName("favoriteAction")
        self.endpoint_favorite_button.clicked.connect(
            lambda _checked=False: self._set_selected_endpoint_favorites(True)
        )
        favorite_buttons.addWidget(self.endpoint_favorite_button)
        self.endpoint_unfavorite_button = QPushButton("Unfavorite selected")
        self.endpoint_unfavorite_button.clicked.connect(
            lambda _checked=False: self._set_selected_endpoint_favorites(False)
        )
        favorite_buttons.addWidget(self.endpoint_unfavorite_button)
        favorite_layout.addLayout(favorite_buttons)
        layout.addWidget(favorite_card)

        self.endpoint_behavior_dialog = self._build_endpoint_behavior_dialog()
        self.endpoint_latency_dialog = self._build_endpoint_latency_dialog()

        self.endpoint_action_status = QLabel("")
        self.endpoint_action_status.setWordWrap(True)
        layout.addWidget(self.endpoint_action_status)

        self.endpoint_tree = EndpointTreeWidget()
        self.endpoint_tree.setHeaderLabels(
            [
                "Select",
                "Endpoint",
                "Region",
                "Favorite",
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
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.endpoint_tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        header = self.endpoint_tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._endpoint_header_clicked)
        header.setSectionResizeMode(
            ENDPOINT_NAME_COLUMN, QHeaderView.ResizeMode.Stretch
        )
        for column in range(ENDPOINT_COLUMN_COUNT):
            if column == ENDPOINT_NAME_COLUMN:
                continue
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.endpoint_tree.setSortingEnabled(False)
        self.endpoint_tree.selectCellClicked.connect(self._endpoint_select_cell_clicked)
        self.endpoint_tree.favoriteCellClicked.connect(
            self._endpoint_favorite_cell_clicked
        )
        self.endpoint_tree.itemChanged.connect(self._endpoint_item_changed)
        self.endpoint_tree.itemSelectionChanged.connect(
            self._endpoint_selection_set_changed
        )
        self.endpoint_tree.itemDoubleClicked.connect(self._endpoint_double_clicked)
        self.endpoint_tree.currentItemChanged.connect(self._endpoint_selection_changed)
        layout.addWidget(self.endpoint_tree)
        self._sync_endpoint_connection_controls()
        self._sync_endpoint_action_ui()
        return page

    def _build_endpoint_behavior_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("Router connection behavior")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dialog.setMinimumWidth(460)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("Recovery and router boot")
        title.setObjectName("favoriteTitle")
        root.addWidget(title)
        note = QLabel(
            "These are native DD-WRT Astrill preferences. Changing one writes "
            "and verifies that preference only; it does not connect the tunnel "
            "or start desktop polling."
        )
        note.setWordWrap(True)
        note.setProperty("class", "muted")
        root.addWidget(note)

        self.endpoint_autocycle = QCheckBox(
            "Auto reconnect to the next favorite endpoint"
        )
        self.endpoint_autocycle.setToolTip(
            "Try the next saved endpoint if the router VPN drops."
        )
        self.endpoint_autocycle.toggled.connect(
            lambda checked: self._endpoint_preference_changed(
                "astrill_autocycle",
                checked,
            )
        )
        root.addWidget(self.endpoint_autocycle)
        self.endpoint_autostart = QCheckBox("Start Astrill after the router boots")
        self.endpoint_autostart.setToolTip(
            "Connect native Astrill after DD-WRT starts."
        )
        self.endpoint_autostart.toggled.connect(
            lambda checked: self._endpoint_preference_changed(
                "astrill_autostart",
                checked,
            )
        )
        root.addWidget(self.endpoint_autostart)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.hide)
        root.addWidget(buttons)
        return dialog

    def _show_endpoint_behavior_dialog(self) -> None:
        self._sync_endpoint_connection_controls()
        self.endpoint_behavior_dialog.show()
        self.endpoint_behavior_dialog.raise_()
        self.endpoint_behavior_dialog.activateWindow()

    def _build_endpoint_latency_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName("endpointLatencyDialog")
        dialog.setWindowTitle("PC latency test")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dialog.setMinimumWidth(560)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("latencyCard")
        latency_layout = QVBoxLayout(card)
        latency_layout.setContentsMargins(16, 13, 16, 13)
        latency_layout.setSpacing(9)

        latency_title = QLabel("Manual endpoint latency")
        latency_title.setObjectName("latencyTitle")
        latency_layout.addWidget(latency_title)
        latency_note = QLabel(
            "Times one bounded TCP connection per endpoint from this PC, then "
            "closes it. It never commands the router, switches endpoints, or "
            "runs a bandwidth test."
        )
        latency_note.setObjectName("latencyNote")
        latency_note.setWordWrap(True)
        latency_layout.addWidget(latency_note)

        self.endpoint_probe_target_status = QLabel("")
        self.endpoint_probe_target_status.setObjectName("latencyStatus")
        self.endpoint_probe_target_status.setWordWrap(True)
        latency_layout.addWidget(self.endpoint_probe_target_status)

        latency_controls = QHBoxLayout()
        latency_controls.addWidget(QLabel("Test"))
        self.endpoint_probe_scope = QComboBox()
        self.endpoint_probe_scope.addItem("Selected endpoints", "selected")
        self.endpoint_probe_scope.addItem("Visible endpoints", "visible")
        self.endpoint_probe_scope.addItem("All loaded endpoints", "all")
        self.endpoint_probe_scope.currentIndexChanged.connect(
            lambda _index: self._sync_endpoint_action_ui()
        )
        latency_controls.addWidget(self.endpoint_probe_scope, 1)
        self.endpoint_probe_button = QPushButton("Run latency test")
        self.endpoint_probe_button.setObjectName("latencyAction")
        self.endpoint_probe_button.setToolTip(
            "Manual only: opens one bounded TCP connection per endpoint from "
            "this computer and immediately closes it."
        )
        self.endpoint_probe_button.clicked.connect(self._test_endpoint_latency)
        latency_controls.addWidget(self.endpoint_probe_button)
        self.endpoint_probe_clear_button = QPushButton("Clear saved results")
        self.endpoint_probe_clear_button.clicked.connect(
            self._clear_endpoint_probe_results
        )
        latency_controls.addWidget(self.endpoint_probe_clear_button)
        latency_layout.addLayout(latency_controls)

        saved_count = len(self._endpoint_probe_results)
        self.endpoint_probe_status = QLabel(
            (
                f"Loaded {saved_count} saved result"
                f"{'' if saved_count == 1 else 's'} · no automatic retest."
            )
            if saved_count
            else "Not run · results appear only after you start a test."
        )
        self.endpoint_probe_status.setObjectName("latencyStatus")
        self.endpoint_probe_status.setWordWrap(True)
        latency_layout.addWidget(self.endpoint_probe_status)
        root.addWidget(card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.hide)
        root.addWidget(buttons)
        return dialog

    def _show_endpoint_latency_dialog(self) -> None:
        self._sync_endpoint_action_ui()
        self.endpoint_latency_dialog.show()
        self.endpoint_latency_dialog.raise_()
        self.endpoint_latency_dialog.activateWindow()

    def _build_astrill_page(self) -> QWidget:
        self.native_page = WindowsNativeSettingsPage(
            on_refresh=self._load_native_settings,
            on_save=self._save_native_settings,
            on_dirty_changed=self._native_draft_dirty_changed,
        )
        self.save_native_button = self.native_page.save_button
        return self.native_page

    def _native_draft_dirty_changed(self, _dirty: bool) -> None:
        self._sync_cross_editor_guards()
        self._sync_endpoint_connection_controls()
        self._sync_endpoint_action_ui()

    def _sync_cross_editor_guards(self) -> None:
        if not hasattr(self, "native_page") or not hasattr(self, "connection_page"):
            return
        self.connection_page.set_external_lock(
            "Save or reload the unsaved Astrill-page draft before editing "
            "connection settings."
            if self.native_page.dirty
            else ""
        )
        self.native_page.set_external_lock(
            "Save or reload the unsaved Connection-page draft before editing "
            "the overlapping native settings."
            if self.connection_page.dirty
            else ""
        )

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
        if page_id == "connection":
            if (
                self.native_settings is not None
                and self.controller.server_catalog.servers
            ):
                self.connection_page.sync(
                    self.native_settings,
                    self.controller.server_catalog.servers,
                    self.router_status,
                )
            elif self.busy_count == 0:
                self._refresh_connection_page()
        if title == "Endpoints":
            self._sync_endpoint_action_ui()
            if not self._endpoint_catalog_loaded:
                self._load_endpoints(quiet=True)
            elif not self._native_settings_loading and not self.native_page.dirty:
                self._load_native_settings()
        if page_id == "astrill":
            if not self._clients_loaded:
                self._load_devices(quiet=True)
            if self.native_settings is None:
                self._load_native_settings()

    @classmethod
    def _page_index(cls, page_id: str) -> int:
        return next(
            index
            for index, (candidate, _title, _subtitle) in enumerate(cls.PAGE_DEFINITIONS)
            if candidate == page_id
        )

    def _setup_network_recovery_hook(self) -> None:
        """Reconcile once when Windows networking returns after going offline."""
        try:
            QNetworkInformation.loadDefaultBackend()
            information = QNetworkInformation.instance()
        except (AttributeError, RuntimeError):
            return
        if information is None:
            return
        self._network_information = information
        reachability = information.reachability()
        self._network_was_offline = (
            reachability == QNetworkInformation.Reachability.Disconnected
        )
        information.reachabilityChanged.connect(self._network_reachability_changed)

    def _network_reachability_changed(
        self,
        reachability: QNetworkInformation.Reachability,
    ) -> None:
        if reachability == QNetworkInformation.Reachability.Disconnected:
            if not self._network_was_offline:
                self._network_recovery_generation += 1
            self._network_recovery_used = False
            self._network_recovery_scheduled = False
            self._network_recovery_pending = False
            self._network_was_offline = True
            return
        online_states = {
            QNetworkInformation.Reachability.Local,
            QNetworkInformation.Reachability.Site,
            QNetworkInformation.Reachability.Online,
        }
        if (
            reachability not in online_states
            or not self._network_was_offline
            or self._network_recovery_used
            or self._network_recovery_scheduled
        ):
            return
        self._network_recovery_scheduled = True
        self._network_was_offline = False
        generation = self._network_recovery_generation
        QTimer.singleShot(
            1500,
            lambda: self._run_network_recovery(generation),
        )

    def _run_network_recovery(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._network_recovery_generation:
            return
        self._network_recovery_scheduled = False
        if self.busy_count:
            self._network_recovery_pending = True
            return
        self._network_recovery_used = True
        self._network_was_offline = False
        self._network_recovery_pending = False
        self._refresh_status(quiet=True)

    def _resume_pending_network_recovery(self) -> None:
        if (
            not self._network_recovery_pending
            or self.busy_count
            or self._network_recovery_used
            or self._network_was_offline
            or self._network_recovery_scheduled
        ):
            return
        self._network_recovery_pending = False
        self._network_recovery_scheduled = True
        generation = self._network_recovery_generation
        QTimer.singleShot(
            0,
            lambda: self._run_network_recovery(generation),
        )

    def _run_task(
        self,
        label: str,
        function: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        *,
        quiet: bool = False,
        finished_callback: Callable[[], None] | None = None,
        failure: Callable[[str], None] | None = None,
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
        if failure is not None:
            task.signals.failed.connect(failure)

        def finished() -> None:
            self.busy_count = max(0, self.busy_count - 1)
            if self.busy_count == 0:
                self.progress.hide()
            self._tasks.discard(task)
            if finished_callback is not None:
                finished_callback()
            self._sync_access_ui()
            if self.busy_count == 0:
                self._resume_pending_network_recovery()

        task.signals.finished.connect(finished)
        self.thread_pool.start(task)
        self._sync_access_ui()

    def _task_failed(
        self, label: str, message: str, quiet: bool, *, router_related: bool
    ) -> None:
        self.statusBar().showMessage(f"{label}: {message}", 9000)
        if router_related:
            self.sidebar_status.setText("Router unavailable · check Settings")
        if label == "Syncing Astrill favorites":
            self.endpoint_favorite_status.setText(
                f"Favorite sync failed · existing GUI state preserved: {message}"
            )
        elif label.startswith(("Adding ", "Removing ")) and "router favorite" in label:
            self.endpoint_favorite_status.setText(
                f"Favorite change failed · sync and retry: {message}"
            )
        if "RAM overlay" in label or "persistent core" in label:
            self.policy_sync_state.setText(f"{label} failed: {message}")
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['red']};")
        if "Astrill connection" in label or label in {
            "Connecting Astrill",
            "Reconnecting Astrill",
            "Disconnecting Astrill",
        }:
            self.connection_page.set_action_status(message, level="error")
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
        self._update_policy_metric()
        local_count = len(self.controller.store.rules)
        router_count = self.router_status.get("rules_count")
        if local_count:
            self.policy_empty_note.hide()
        else:
            router_detail = (
                ""
                if router_count in (None, "")
                else f" The last router refresh also reported {router_count} rules."
            )
            self.policy_empty_note.setText(
                "No local policies are saved yet. Select services, choose a "
                "route, then use Add to Policies. Apply policies is a separate "
                f"router step.{router_detail}"
            )
            self.policy_empty_note.show()

    def _hybrid_policy_storage(self) -> dict[str, Any] | None:
        status = self.router_status
        storage: dict[str, Any] = {}
        for key in ("policy_layers", "layered_policy", "policy_storage"):
            nested = status.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("core"), dict):
                storage = dict(nested)
                break

        raw_capabilities = status.get("capabilities", ())
        capabilities: set[str] = set()
        if isinstance(raw_capabilities, dict):
            capabilities.update(
                str(key) for key, enabled in raw_capabilities.items() if bool(enabled)
            )
            mode = raw_capabilities.get("policy_storage")
            if isinstance(mode, str):
                capabilities.add(f"policy_storage.{mode}")
        elif isinstance(raw_capabilities, (list, tuple, set, frozenset)):
            capabilities.update(str(item) for item in raw_capabilities)
        elif isinstance(raw_capabilities, str):
            capabilities.add(raw_capabilities)

        nested_mode = str(storage.get("mode", "")).strip().casefold()
        capability_present = bool(capabilities & HYBRID_POLICY_CAPABILITIES)
        capability_present = capability_present or nested_mode in {
            "hybrid",
            "core-overlays",
            "core+overlays",
        }
        top_level_shape = (
            isinstance(status.get("core"), dict)
            and isinstance(status.get("effective"), dict)
            and isinstance(status.get("overlays"), list)
        )
        if not storage and top_level_shape:
            storage = dict(status)
        nested_shape = bool(
            storage
            and any(
                key in storage
                for key in ("core", "effective", "overlays", "this_overlay")
            )
        )
        if not (capability_present or top_level_shape or nested_shape):
            return None

        if not storage:
            return None

        comparison: HybridPolicyComparison | None = None
        comparison_method = getattr(self.controller, "hybrid_policy_status", None)
        if callable(comparison_method):
            try:
                candidate = comparison_method(status)
            except (ControllerError, TypeError, ValueError) as exc:
                storage["_comparison_error"] = str(exc)
            else:
                if isinstance(candidate, HybridPolicyComparison):
                    comparison = candidate
        manifest = comparison.manifest if comparison is not None else None
        if comparison is not None:
            storage["runtime_epoch"] = comparison.runtime_epoch
            storage["restore_needed"] = comparison.restore_needed
            storage["overlay_present"] = comparison.overlay_present
            storage["core_matches"] = comparison.core_matches
            storage["overlay_matches"] = comparison.overlay_matches
        if manifest is not None:
            storage["controller_id"] = manifest.controller_id
            storage["expected_core_hash"] = manifest.core_hash
            storage["expected_overlay_hash"] = manifest.overlay_hash
            storage["source_request"] = manifest.source
            storage["source_binding"] = manifest.resolved_source or (
                manifest.source if manifest.source != "auto" else ""
            )
            storage["expected_source_mac"] = manifest.source_mac
            storage["auto_restore"] = manifest.restore_overlay_after_reboot
            if manifest.last_restore_error:
                storage["overlay_restore_error"] = manifest.last_restore_error
            storage["_manifest"] = manifest
        else:
            storage.setdefault(
                "controller_id",
                str(getattr(self.controller.store, "controller_id", "")),
            )
        for key in ("last_error", "overlay_restore_error"):
            if key not in storage and key in status:
                storage[key] = status[key]
        storage["_capabilities"] = capabilities
        storage["_comparison"] = comparison
        return storage

    @staticmethod
    def _storage_record(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _storage_integer(record: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = record.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value)
        return None

    @classmethod
    def _storage_origin_count(cls, record: dict[str, Any]) -> int | None:
        origins = cls._storage_integer(record, "origins", "origin_count")
        if origins is not None:
            return origins
        origin_ids = record.get("origin_ids")
        return len(origin_ids) if isinstance(origin_ids, list) else None

    @staticmethod
    def _storage_origin_ids(record: dict[str, Any]) -> frozenset[str] | None:
        value = record.get("origin_ids")
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return None
        return frozenset(item.strip() for item in value)

    @classmethod
    def _storage_record_summary(
        cls,
        record: dict[str, Any],
        *,
        empty: str,
    ) -> str:
        origins = cls._storage_origin_count(record)
        rows = cls._storage_integer(record, "rows", "rules_count")
        size = cls._storage_integer(record, "bytes", "compiled_bytes")
        parts: list[str] = []
        if origins is not None:
            parts.append(f"{origins} origin{'' if origins == 1 else 's'}")
        if rows is not None:
            parts.append(f"{rows:,} rows")
        if size is not None:
            parts.append(f"{size:,} B")
        if parts:
            return " · ".join(parts)
        return "Active" if record else empty

    def _this_policy_overlay(
        self,
        storage: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        controller_id = str(storage.get("controller_id", "")).strip()
        this_overlay = self._storage_record(storage.get("this_overlay"))
        overlays_value = storage.get("overlays", [])
        overlays = (
            [dict(item) for item in overlays_value if isinstance(item, dict)]
            if isinstance(overlays_value, list)
            else []
        )
        if not this_overlay and controller_id:
            this_overlay = next(
                (
                    item
                    for item in overlays
                    if str(item.get("owner", "")).strip() == controller_id
                ),
                {},
            )
        other_overlays = [
            item
            for item in overlays
            if not this_overlay
            or item is not this_overlay
            and (
                not controller_id or str(item.get("owner", "")).strip() != controller_id
            )
        ]
        return this_overlay, other_overlays

    @staticmethod
    def _policy_storage_state(record: dict[str, Any]) -> str:
        return str(record.get("state", record.get("status", ""))).strip().casefold()

    @staticmethod
    def _hash_value(record: dict[str, Any]) -> str:
        return str(record.get("hash", "")).strip()

    @staticmethod
    def _active_policy_overlay_mac(
        overlay: dict[str, Any],
    ) -> str | None:
        for key in ("source_mac", "mac"):
            value = str(overlay.get(key, "") or "").strip().casefold()
            normalized = value.replace("-", ":")
            parts = normalized.split(":")
            if len(parts) == 6 and all(
                len(part) == 2
                and all(character in "0123456789abcdef" for character in part)
                for part in parts
            ):
                return normalized
        return None

    def _policy_overlay_binding_summary(
        self,
        overlay: dict[str, Any],
    ) -> str:
        source = self._active_policy_overlay_source(overlay)
        mac = self._active_policy_overlay_mac(overlay)
        if source and mac:
            return f"{source} · {mac}"
        if source:
            return f"{source} · MAC not reported"
        return "Source binding not reported"

    def _policy_storage_tones(
        self,
        storage: dict[str, Any],
        this_overlay: dict[str, Any],
    ) -> dict[str, str]:
        core = self._storage_record(storage.get("core"))
        effective = self._storage_record(storage.get("effective"))
        core_state = self._policy_storage_state(core)
        overlay_state = self._policy_storage_state(this_overlay)
        expected_core = str(storage.get("expected_core_hash", "") or "").strip()
        expected_overlay = str(storage.get("expected_overlay_hash", "") or "").strip()
        actual_core = self._hash_value(core)
        actual_overlay = self._hash_value(this_overlay)
        expected_source = self._saved_policy_overlay_source(storage)
        active_source = self._active_policy_overlay_source(this_overlay)
        expected_mac = str(storage.get("expected_source_mac", "") or "").strip()
        active_mac = self._active_policy_overlay_mac(this_overlay)
        source_request = str(storage.get("source_request", "") or "").strip().casefold()
        source_protection_failed = bool(
            expected_overlay
            and this_overlay
            and (
                expected_source is None
                or active_source is None
                or active_source != expected_source
                or expected_mac
                and active_mac != expected_mac
                or source_request == "auto"
                and active_mac is None
            )
        )
        storage_error = str(
            storage.get("last_error")
            or storage.get("overlay_restore_error")
            or storage.get("_comparison_error")
            or self.router_status.get("last_reconcile_error")
            or ""
        ).strip()

        core_tone = "green"
        if (
            not core
            or core_state in {"missing", "corrupt", "error", "failed", "degraded"}
            or storage.get("core_matches") is False
            or expected_core
            and actual_core != expected_core
        ):
            core_tone = "red"

        overlay_tone = "neutral"
        if (
            storage_error
            or source_protection_failed
            or overlay_state in {"error", "failed", "degraded"}
        ):
            overlay_tone = "red"
        elif expected_overlay and (
            not this_overlay
            or actual_overlay != expected_overlay
            or storage.get("overlay_matches") is False
            or bool(storage.get("restore_needed"))
            or overlay_state in {"missing", "stale", "pending"}
        ):
            overlay_tone = "amber"
        elif this_overlay:
            overlay_tone = "green"

        policy_health = str(self.router_status.get("policy_health", "")).casefold()
        effective_state = self._policy_storage_state(effective)
        if (
            policy_health == "degraded"
            or storage_error
            or effective_state in {"missing", "corrupt", "error", "failed", "degraded"}
            or core_tone == "red"
            or overlay_tone == "red"
        ):
            effective_tone = "red"
        elif overlay_tone == "amber":
            effective_tone = "amber"
        elif effective:
            effective_tone = "green"
        else:
            effective_tone = "red"
        return {
            "local": "neutral",
            "core": core_tone,
            "this_overlay": overlay_tone,
            "other_overlays": "neutral",
            "effective": effective_tone,
        }

    def _set_policy_storage_cell(
        self,
        key: str,
        text: str,
        tone: str,
        *,
        tooltip: str = "",
    ) -> None:
        label = self.policy_storage_cells[key]
        label.setText(text)
        label.setToolTip(tooltip)
        spoken_state = {
            "neutral": "informational",
            "green": "verified",
            "amber": "restore needed",
            "red": "needs attention",
        }.get(tone, tone)
        label.setAccessibleDescription(f"Status: {spoken_state}. {tooltip}".strip())
        label.setProperty("storageTone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _render_hybrid_policy_storage(self, storage: dict[str, Any]) -> None:
        self.policy_storage_group.show()
        self.apply_button.hide()
        self.apply_selected_button.hide()

        local_total = len(self.controller.store.rules)
        local_enabled = sum(rule.enabled for rule in self.controller.store.rules)
        core = self._storage_record(storage.get("core"))
        effective = self._storage_record(storage.get("effective"))
        this_overlay, other_overlays = self._this_policy_overlay(storage)
        tones = self._policy_storage_tones(storage, this_overlay)

        self._set_policy_storage_cell(
            "local",
            f"{local_enabled} enabled · {local_total} saved",
            tones["local"],
            tooltip="The authoritative editable library stored on this computer.",
        )
        self._set_policy_storage_cell(
            "core",
            self._storage_record_summary(core, empty="Missing"),
            tones["core"],
            tooltip="Persistent NVRAM policy available before any computer signs in.",
        )
        overlay_empty = (
            "Not restored"
            if str(storage.get("expected_overlay_hash", "") or "").strip()
            else "Not loaded"
        )
        self._set_policy_storage_cell(
            "this_overlay",
            (
                self._storage_record_summary(this_overlay, empty=overlay_empty)
                + (
                    "\n" + self._policy_overlay_binding_summary(this_overlay)
                    if this_overlay
                    else ""
                )
            ),
            tones["this_overlay"],
            tooltip=(
                "Volatile policy owned by this controller and limited to the "
                "displayed IPv4 source and MAC binding."
            ),
        )
        other_origins = sum(
            self._storage_origin_count(item) or 0 for item in other_overlays
        )
        other_text = f"{len(other_overlays)} owner"
        if len(other_overlays) != 1:
            other_text += "s"
        if other_origins:
            other_text += (
                f" · {other_origins} origin{'' if other_origins == 1 else 's'}"
            )
        self._set_policy_storage_cell(
            "other_overlays",
            other_text,
            tones["other_overlays"],
            tooltip="RAM overlays restored by other paired controllers.",
        )
        self._set_policy_storage_cell(
            "effective",
            self._storage_record_summary(effective, empty="Unavailable"),
            tones["effective"],
            tooltip="The composed core plus all active overlays enforced by DD-WRT.",
        )

        suggested_source = self._suggest_policy_overlay_source(storage, this_overlay)
        if (
            suggested_source
            and not self._overlay_source_user_edited
            and self.policy_overlay_source.text().strip() != suggested_source
        ):
            self._syncing_policy_storage = True
            self.policy_overlay_source.setText(suggested_source)
            self._syncing_policy_storage = False
        self._render_policy_overlay_source_state(storage)

        auto_restore = storage.get("auto_restore")
        if not isinstance(auto_restore, bool):
            auto_restore = bool(
                getattr(
                    self.controller.store,
                    "policy_overlay_auto_restore",
                    False,
                )
            )
        self._syncing_policy_storage = True
        self.policy_auto_restore_check.setChecked(auto_restore)
        self._syncing_policy_storage = False

        effective_origins = self._storage_origin_count(effective)
        if effective_origins is None:
            effective_origins = (
                (self._storage_origin_count(core) or 0)
                + (self._storage_origin_count(this_overlay) or 0)
                + other_origins
            )
        self.metric_labels["rules"].setText(f"{local_enabled} / {effective_origins}")
        self.metric_labels["rules"].setToolTip(
            f"{local_enabled} enabled in this computer's library; "
            f"{effective_origins} origins in the effective router policy."
        )

        storage_error = str(
            storage.get("last_error")
            or storage.get("overlay_restore_error")
            or storage.get("_comparison_error")
            or self.router_status.get("last_reconcile_error")
            or ""
        ).strip()
        local_enabled_ids = frozenset(
            rule.id for rule in self.controller.store.rules if rule.enabled
        )
        manifest = storage.get("_manifest")
        expected_profile_ids = (
            frozenset(
                (
                    *tuple(getattr(manifest, "core_rule_ids", ()) or ()),
                    *tuple(getattr(manifest, "overlay_rule_ids", ()) or ()),
                )
            )
            if manifest is not None
            else frozenset()
        )
        core_origin_ids = self._storage_origin_ids(core)
        overlay_origin_ids = self._storage_origin_ids(this_overlay)
        owned_origin_ids = frozenset(
            (*tuple(core_origin_ids or ()), *tuple(overlay_origin_ids or ()))
        )
        identities_reported = bool(
            manifest is not None
            and (
                not tuple(getattr(manifest, "core_rule_ids", ()) or ())
                or core_origin_ids is not None
            )
            and (
                not tuple(getattr(manifest, "overlay_rule_ids", ()) or ())
                or not this_overlay
                or overlay_origin_ids is not None
            )
        )
        missing_expected_ids = (
            (local_enabled_ids & expected_profile_ids) - owned_origin_ids
            if identities_reported
            else frozenset()
        )
        local_outside_ids = (
            local_enabled_ids - expected_profile_ids
            if manifest is not None
            else frozenset()
        )
        if tones["effective"] == "red":
            detail = f": {storage_error}" if storage_error else ""
            self.policy_sync_state.setText(
                "Router policy needs attention"
                f"{detail}. The effective policy is not fully verified."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['red']};")
        elif tones["this_overlay"] == "amber":
            self.policy_sync_state.setText(
                "Router core is active. This computer's volatile RAM overlay "
                "needs restore; restore it now or explicitly enable reboot "
                "auto-restore."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['orange']};")
        elif manifest is None:
            self.policy_sync_state.setText(
                "Layered router policy is active, but this computer has no "
                "version-bound profile yet. Select policies and explicitly replace "
                "the persistent core or load this computer's RAM overlay."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['muted']};")
        elif missing_expected_ids:
            names = ", ".join(sorted(missing_expected_ids))
            self.policy_sync_state.setText(
                "The router hashes appear healthy, but reported origin identities "
                f"are missing from this computer's saved profile: {names}."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['red']};")
        elif local_outside_ids:
            local_outside = len(local_outside_ids)
            self.policy_sync_state.setText(
                f"Router policy is up to date. {local_outside} enabled local "
                f"polic{'y is' if local_outside == 1 else 'ies are'} deliberately "
                "outside the effective router profile."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['muted']};")
        else:
            self.policy_sync_state.setText(
                "Router policy is up to date. Persistent core and this computer's "
                "RAM overlay match the saved deployment."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['green']};")
        self._sync_policy_apply_ui()

    def _render_legacy_policy_storage(self) -> None:
        self.policy_storage_group.hide()
        self.apply_button.show()
        self.apply_selected_button.show()

    def _update_policy_metric(self) -> None:
        storage = self._hybrid_policy_storage()
        if storage is not None:
            self.policy_preflight = None
            self._render_hybrid_policy_capacity(storage)
            self._render_hybrid_policy_storage(storage)
            return
        self._render_legacy_policy_storage()
        comparison = self.controller.policy_origin_comparison(self.router_status)
        local_count = len(comparison.local_enabled_ids)
        self.policy_preflight = self.controller.policy_preflight()
        self._render_policy_capacity(self.policy_preflight)
        has_enabled_applied = "enabled_origin_count" in self.router_status
        applied_count = comparison.applied_count
        total_value = self.router_status.get("origin_count")
        total_count = (
            int(total_value)
            if (isinstance(total_value, int) and not isinstance(total_value, bool))
            or (isinstance(total_value, str) and total_value.strip().isdigit())
            else None
        )
        applied_text = "—" if applied_count is None else str(applied_count)
        self.metric_labels["rules"].setText(f"{local_count} / {applied_text}")
        if applied_count is None:
            router_detail = "router applied count has not been refreshed"
        elif comparison.exact:
            router_detail = (
                f"{applied_count} enabled origin IDs verified from router rule detail"
            )
            if total_count is not None:
                router_detail += f"; {total_count} total origins are stored"
        elif has_enabled_applied:
            router_detail = f"{applied_count} enabled origins applied on the router"
            if total_count is not None:
                router_detail += f"; {total_count} total origins are stored"
        else:
            router_detail = (
                f"{applied_count} total origins reported by an older companion"
            )
        exact_detail: list[str] = []
        if comparison.missing_ids:
            exact_detail.append(
                "Missing IDs: " + ", ".join(sorted(comparison.missing_ids))
            )
        if comparison.extra_ids:
            exact_detail.append("Extra IDs: " + ", ".join(sorted(comparison.extra_ids)))
        self.metric_labels["rules"].setToolTip(
            f"{local_count} enabled in the Windows config; {router_detail}"
            + (f"\n{'; '.join(exact_detail)}" if exact_detail else "")
        )
        action = (
            "Use Apply policies when ready."
            if self.policy_preflight.can_apply
            else (
                "The full document is too large; select the policies needed "
                "on this router and use Apply selected."
            )
        )
        if applied_count is None:
            self.policy_sync_state.setText(
                "Local policies are shown below. Refresh the router to compare "
                "the last applied count."
            )
            self.policy_sync_state.setStyleSheet("")
        elif comparison.exact and comparison.matches:
            self.policy_sync_state.setText(
                f"Local and router enabled policy IDs agree: {local_count}."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['green']};")
        elif comparison.exact:
            differences: list[str] = []
            if comparison.missing_ids:
                differences.append(
                    "Missing on router: "
                    + self._format_policy_origin_ids(comparison.missing_ids)
                )
            if comparison.extra_ids:
                differences.append(
                    "Extra on router: "
                    + self._format_policy_origin_ids(comparison.extra_ids)
                )
            self.policy_sync_state.setText(
                "Enabled policy IDs differ despite any matching count. "
                + ". ".join(differences)
                + f". {action}"
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['orange']};")
        elif applied_count == local_count:
            self.policy_sync_state.setText(
                f"Local and router counts agree at {local_count}; this older "
                "status did not include rule IDs for exact verification."
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['green']};")
        else:
            difference = local_count - applied_count
            if difference > 0:
                gap = (
                    f"{difference} enabled local "
                    f"polic{'y is' if difference == 1 else 'ies are'} not reflected "
                    "in the router count."
                )
            else:
                extra = abs(difference)
                gap = (
                    f"The router reports {extra} more origin"
                    f"{'' if extra == 1 else 's'} than the enabled local count."
                )
            self.policy_sync_state.setText(
                f"{local_count} enabled locally; the router reports "
                f"{applied_count} "
                f"{'enabled applied' if has_enabled_applied else 'applied origins'}. "
                f"{gap} {action}"
            )
            self.policy_sync_state.setStyleSheet(f"color: {COLORS['orange']};")
        self._sync_policy_apply_ui()

    def _format_policy_origin_ids(self, origin_ids: frozenset[str]) -> str:
        local_names = {rule.id: rule.name for rule in self.controller.store.rules}
        values = [
            (
                f"{local_names[origin_id]} [{origin_id}]"
                if origin_id in local_names
                else origin_id
            )
            for origin_id in sorted(origin_ids)
        ]
        visible = values[:2]
        if len(values) > len(visible):
            visible.append(f"+{len(values) - len(visible)} more")
        return ", ".join(visible)

    def _render_policy_capacity(
        self,
        summary: PolicyCompilationSummary,
    ) -> None:
        if summary.compiled_bytes is None:
            self.policy_capacity_state.setText(
                f"Capacity: unavailable / {summary.limit_bytes:,} bytes. "
                f"{summary.error or 'The policy document is invalid.'}"
            )
            self.policy_capacity_state.setStyleSheet(f"color: {COLORS['red']};")
        elif summary.can_apply:
            percent = round(summary.compiled_bytes * 100 / summary.limit_bytes)
            self.policy_capacity_state.setText(
                f"Capacity: {summary.compiled_rows:,} compiled rows · "
                f"{summary.compiled_bytes:,} / {summary.limit_bytes:,} bytes "
                f"({percent}%)."
            )
            self.policy_capacity_state.setStyleSheet(
                f"color: {COLORS['green']};"
                if percent < 90
                else f"color: {COLORS['orange']};"
            )
        else:
            self.policy_capacity_state.setText(
                f"Capacity: {summary.compiled_rows:,} compiled rows · "
                f"{summary.compiled_bytes:,} / {summary.limit_bytes:,} bytes. "
                "Full apply is blocked; use Apply selected."
            )
            self.policy_capacity_state.setStyleSheet(f"color: {COLORS['red']};")
        detail = summary.error or ""
        if summary.warnings:
            detail = "\n".join((detail, *summary.warnings)).strip()
        self.policy_capacity_state.setToolTip(detail)

    def _render_hybrid_policy_capacity(self, storage: dict[str, Any]) -> None:
        limits = self._storage_record(storage.get("policy_limits"))
        core_limit = self._storage_integer(limits, "core_bytes") or MAX_COMPILED_BYTES
        overlay_limit = (
            self._storage_integer(limits, "overlay_bytes") or MAX_OVERLAY_BYTES
        )
        selected_ids = self._selected_policy_ids()
        if not selected_ids:
            self.policy_capacity_state.setText(
                "Hybrid storage: the editable local library is not limited to one "
                f"router document. Select policies, then replace the persistent "
                f"core (up to {core_limit:,} B) or load this computer's RAM overlay "
                f"(up to {overlay_limit:,} B)."
            )
            self.policy_capacity_state.setStyleSheet(f"color: {COLORS['muted']};")
            self.policy_capacity_state.setToolTip(
                "The persistent core is global and survives reboot. A RAM overlay "
                "is volatile and source-bound to this computer."
            )
            return

        core = self.controller.policy_layer_preflight(selected_ids, layer="core")
        overlay = self.controller.policy_layer_preflight(
            selected_ids,
            layer="overlay",
        )

        def layer_text(label: str, summary: PolicyCompilationSummary) -> str:
            if summary.can_apply and summary.compiled_bytes is not None:
                return (
                    f"{label} {summary.compiled_rows:,} rows · "
                    f"{summary.compiled_bytes:,} / {summary.limit_bytes:,} B"
                )
            return f"{label} unavailable"

        self.policy_capacity_state.setText(
            f"Selected {len(selected_ids)}: {layer_text('core', core)} · "
            f"{layer_text('RAM', overlay)}."
        )
        if core.can_apply and overlay.can_apply:
            tone = COLORS["green"]
        elif core.can_apply or overlay.can_apply:
            tone = COLORS["orange"]
        else:
            tone = COLORS["red"]
        self.policy_capacity_state.setStyleSheet(f"color: {tone};")
        details: list[str] = []
        for label, summary in (("Persistent core", core), ("RAM overlay", overlay)):
            if summary.error:
                details.append(f"{label}: {summary.error}")
            details.extend(f"{label}: {warning}" for warning in summary.warnings)
        self.policy_capacity_state.setToolTip("\n".join(details))

    def _suggest_policy_overlay_source(
        self,
        storage: dict[str, Any],
        _this_overlay: dict[str, Any],
    ) -> str:
        candidates = (
            storage.get("source_request"),
            getattr(self.controller.store, "policy_overlay_source", ""),
            getattr(self.controller, "policy_overlay_source", ""),
        )
        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value:
                continue
            try:
                return self._normalized_policy_overlay_source(value)
            except ValueError:
                continue
        return "auto"

    @staticmethod
    def _normalized_policy_overlay_source(value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "Use 'auto' (recommended), or enter an advanced IPv4 host/CIDR."
            )
        if stripped.casefold() == "auto":
            return "auto"
        candidate = stripped if "/" in stripped else f"{stripped}/32"
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError as exc:
            raise ValueError(
                "The RAM overlay source must be a valid IPv4 CIDR."
            ) from exc
        if network.version != 4:
            raise ValueError("The RAM overlay source must use IPv4.")
        if (
            network.prefixlen == 0
            or network.is_unspecified
            or network.is_loopback
            or network.is_multicast
            or network.network_address == ipaddress.IPv4Address("255.255.255.255")
        ):
            raise ValueError(
                "Choose one LAN host or a dedicated source subnet, not a global "
                "or special-use range."
            )
        return str(network)

    def _policy_overlay_source_edited(self, _value: str) -> None:
        if not self._syncing_policy_storage:
            self._overlay_source_user_edited = True

    def _policy_overlay_source_changed(self, _value: str) -> None:
        if self._syncing_policy_storage:
            return
        self._render_policy_overlay_source_state(self._hybrid_policy_storage())
        self._sync_policy_apply_ui()

    def _normalize_policy_overlay_source(self) -> None:
        value = self.policy_overlay_source.text()
        try:
            normalized = self._normalized_policy_overlay_source(value)
        except ValueError:
            self._render_policy_overlay_source_state(self._hybrid_policy_storage())
            return
        if normalized != value:
            self._syncing_policy_storage = True
            self.policy_overlay_source.setText(normalized)
            self._syncing_policy_storage = False
        self._render_policy_overlay_source_state(self._hybrid_policy_storage())
        self._sync_policy_apply_ui()

    def _policy_overlay_source_value(self) -> str | None:
        try:
            return self._normalized_policy_overlay_source(
                self.policy_overlay_source.text()
            )
        except ValueError:
            return None

    def _saved_policy_overlay_source(
        self,
        storage: dict[str, Any],
    ) -> str | None:
        manifest = storage.get("_manifest")
        if manifest is None:
            return None
        configured_source = str(getattr(manifest, "source", "") or "").strip()
        candidates = (
            getattr(manifest, "resolved_source", None),
            configured_source if configured_source.casefold() != "auto" else None,
            storage.get("source_binding"),
        )
        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value:
                continue
            try:
                return self._normalized_policy_overlay_source(value)
            except ValueError:
                continue
        return None

    def _saved_policy_overlay_request(
        self,
        storage: dict[str, Any],
    ) -> str | None:
        manifest = storage.get("_manifest")
        if manifest is None:
            return None
        value = str(getattr(manifest, "source", "") or "").strip()
        if not value:
            return None
        try:
            return self._normalized_policy_overlay_source(value)
        except ValueError:
            return None

    def _active_policy_overlay_source(
        self,
        overlay: dict[str, Any],
    ) -> str | None:
        for key in ("source", "source_binding", "resolved_source"):
            value = str(overlay.get(key, "") or "").strip()
            if not value:
                continue
            try:
                return self._normalized_policy_overlay_source(value)
            except ValueError:
                continue
        return None

    def _render_policy_overlay_source_state(
        self,
        storage: dict[str, Any] | None = None,
    ) -> None:
        text = self.policy_overlay_source.text()
        try:
            normalized = self._normalized_policy_overlay_source(text)
        except ValueError as exc:
            self.policy_overlay_source_state.setText(str(exc))
            tone = "red"
        else:
            saved_source = (
                self._saved_policy_overlay_source(storage)
                if storage is not None
                else None
            )
            saved_request = (
                self._saved_policy_overlay_request(storage)
                if storage is not None
                else None
            )
            this_overlay, _other_overlays = (
                self._this_policy_overlay(storage) if storage is not None else ({}, [])
            )
            active_source = self._active_policy_overlay_source(this_overlay)
            active_mac = self._active_policy_overlay_mac(this_overlay)
            expected_mac = (
                str(storage.get("expected_source_mac", "") or "").strip().casefold()
                if storage is not None
                else ""
            )
            if (
                saved_source is not None
                and this_overlay
                and (
                    active_source != saved_source
                    or expected_mac
                    and active_mac != expected_mac
                    or saved_request == "auto"
                    and active_mac is None
                )
            ):
                reported = self._policy_overlay_binding_summary(this_overlay)
                expected = saved_source + (f" · {expected_mac}" if expected_mac else "")
                self.policy_overlay_source_state.setText(
                    f"Router reports {reported}; saved binding is {expected}. "
                    "Restore the RAM overlay before trusting it."
                )
                tone = "red"
            elif saved_request is not None and normalized != saved_request:
                self.policy_overlay_source_state.setText(
                    f"Edited request differs from saved overlay {saved_request}. "
                    "Load a selection to replace it."
                )
                tone = "amber"
            elif normalized == "auto":
                if active_source and active_mac:
                    self.policy_overlay_source_state.setText(
                        "Auto (recommended) resolved the authenticated SSH client "
                        f"to {active_source} · {active_mac}."
                    )
                elif saved_source and expected_mac:
                    self.policy_overlay_source_state.setText(
                        "Auto (recommended) will restore the saved binding "
                        f"{saved_source} · {expected_mac}."
                    )
                else:
                    self.policy_overlay_source_state.setText(
                        "Auto (recommended) asks the router to derive this SSH "
                        "client's LAN /32 and validated ARP MAC."
                    )
                tone = "green"
            else:
                network = ipaddress.ip_network(normalized, strict=False)
                if network.prefixlen == 32:
                    self.policy_overlay_source_state.setText(
                        "Advanced override bound to one IPv4 host. Auto is safer "
                        "because it requires the authenticated SSH peer's ARP MAC."
                    )
                    tone = "amber"
                else:
                    self.policy_overlay_source_state.setText(
                        f"Advanced CIDR binds {network.num_addresses:,} addresses; "
                        "use only a dedicated source subnet."
                    )
                    tone = "amber"
        self.policy_overlay_source_state.setProperty("storageTone", tone)
        self.policy_overlay_source_state.style().unpolish(
            self.policy_overlay_source_state
        )
        self.policy_overlay_source_state.style().polish(
            self.policy_overlay_source_state
        )

    def _require_saved_policy_overlay_source(
        self,
        title: str,
        storage: dict[str, Any],
    ) -> str | None:
        visible_source = self._require_policy_overlay_source(title)
        if visible_source is None:
            return None
        saved_request = self._saved_policy_overlay_request(storage)
        saved_source = self._saved_policy_overlay_source(storage)
        manifest = storage.get("_manifest")
        expected_mac = (
            str(getattr(manifest, "source_mac", "") or "").strip()
            if manifest is not None
            else ""
        )
        if saved_request is None or saved_source is None:
            QMessageBox.warning(
                self,
                title,
                "This deployment has no verified IPv4 binding for its saved RAM "
                "overlay. Select the intended policies and use Load selected into "
                "RAM first.",
            )
            return None
        if saved_request == "auto" and not expected_mac:
            QMessageBox.warning(
                self,
                title,
                "The saved automatic overlay has no verified MAC binding. Load the "
                "selected policies again with Auto so the router can bind this "
                "authenticated SSH client before enabling or restoring it.",
            )
            return None
        if visible_source != saved_request:
            QMessageBox.warning(
                self,
                title,
                f"The visible request ({visible_source}) differs from the saved "
                f"overlay request ({saved_request}). Load the intended selection "
                "into RAM to change its source before restoring it.",
            )
            return None
        return saved_request

    def _policy_storage_method(self, action: str) -> Callable[..., Any] | None:
        for name in HYBRID_CONTROLLER_METHODS[action]:
            method = getattr(self.controller, name, None)
            if callable(method):
                return method
        return None

    def _require_policy_overlay_source(self, title: str) -> str | None:
        try:
            return self._normalized_policy_overlay_source(
                self.policy_overlay_source.text()
            )
        except ValueError as exc:
            QMessageBox.warning(
                self,
                title,
                f"{exc}\n\nRAM overlay actions refuse an unscoped, LAN-global policy.",
            )
            self.policy_overlay_source.setFocus()
            return None

    def _sync_hybrid_policy_ui(self, storage: dict[str, Any]) -> None:
        writable = (
            not self.controller.store.read_only
            and self.controller.store.companion_enabled
            and self.busy_count == 0
        )
        selected_count = len(self._selected_policy_ids())
        visible_source = self._policy_overlay_source_value()
        source_ready = visible_source is not None
        saved_request = self._saved_policy_overlay_request(storage)
        manifest = storage.get("_manifest")
        saved_auto_has_mac = bool(
            saved_request != "auto"
            or manifest is not None
            and getattr(manifest, "source_mac", None)
        )
        source_matches = (
            visible_source is not None
            and saved_request is not None
            and visible_source == saved_request
            and saved_auto_has_mac
        )
        saved_overlay = bool(
            manifest is not None
            and tuple(getattr(manifest, "overlay_rule_ids", ()) or ())
            and getattr(manifest, "overlay_hash", None)
        )
        this_overlay, _other_overlays = self._this_policy_overlay(storage)

        methods = {
            action: self._policy_storage_method(action)
            for action in HYBRID_CONTROLLER_METHODS
        }
        configure_available = callable(
            getattr(self.controller, "configure_policy_deployment", None)
        )
        comparison_available = callable(
            getattr(self.controller, "hybrid_policy_status", None)
        )
        deployment_ready = manifest is not None or (
            configure_available and comparison_available
        )
        self.pin_core_button.setEnabled(
            writable
            and deployment_ready
            and selected_count > 0
            and methods["pin"] is not None
        )
        self.load_ram_button.setEnabled(
            writable
            and deployment_ready
            and selected_count > 0
            and source_ready
            and methods["load"] is not None
        )
        self.restore_ram_button.setEnabled(
            writable
            and saved_overlay
            and source_matches
            and methods["restore"] is not None
        )
        self.remove_overlay_button.setEnabled(
            writable
            and manifest is not None
            and bool(this_overlay)
            and methods["remove"] is not None
        )
        self.policy_auto_restore_check.setEnabled(
            writable
            and methods["auto_restore"] is not None
            and (
                self.policy_auto_restore_check.isChecked()
                or saved_overlay
                and source_matches
            )
        )
        self.policy_overlay_source.setEnabled(self.busy_count == 0)

        self.pin_core_button.setText("Replace persistent core")
        self.load_ram_button.setText("Load selected into RAM")
        unavailable = "Requires the hybrid policy-storage controller API."
        if methods["pin"] is None or not deployment_ready:
            self.pin_core_button.setToolTip(unavailable)
        elif not selected_count:
            self.pin_core_button.setToolTip(
                "Select the complete set of policies that should replace the "
                "persistent core."
            )
        else:
            self.pin_core_button.setToolTip(
                f"Replace the complete global persistent core with {selected_count} "
                f"selected polic{'y' if selected_count == 1 else 'ies'}. Policies "
                "not selected are removed from the core after the NVRAM commit."
            )
        if methods["load"] is None or not deployment_ready:
            self.load_ram_button.setToolTip(unavailable)
        elif not selected_count:
            self.load_ram_button.setToolTip(
                "Select one or more policy rows to load into this computer's RAM "
                "overlay."
            )
        elif not source_ready:
            self.load_ram_button.setToolTip(
                "Use Auto (recommended) or enter a valid advanced IPv4 source "
                "before loading a RAM overlay."
            )
        else:
            self.load_ram_button.setToolTip(
                f"Load {selected_count} selected "
                f"polic{'y' if selected_count == 1 else 'ies'} into this "
                "computer's volatile, source-bound overlay without an NVRAM "
                "commit."
            )
        if methods["restore"] is None:
            self.restore_ram_button.setToolTip(unavailable)
        elif not saved_overlay:
            self.restore_ram_button.setToolTip(
                "Load a selected, source-bound RAM overlay before restoring it."
            )
        elif not source_ready:
            self.restore_ram_button.setToolTip(
                "Use the saved Auto or advanced IPv4 request before restoring the "
                "overlay."
            )
        elif not source_matches:
            self.restore_ram_button.setToolTip(
                "The visible source request must match the saved request, including "
                "a verified MAC for Auto. Use Load selected into RAM to change it."
            )
        else:
            self.restore_ram_button.setToolTip(
                "Reconcile this controller's expected overlay once. Other owners "
                "are left unchanged."
            )
        if methods["remove"] is None:
            self.remove_overlay_button.setToolTip(unavailable)
        elif manifest is None:
            self.remove_overlay_button.setToolTip(
                "A trusted local deployment manifest is required to identify and "
                "remove this controller's overlay."
            )
        elif not this_overlay:
            self.remove_overlay_button.setToolTip(
                "This computer does not currently have an active RAM overlay."
            )
        else:
            self.remove_overlay_button.setToolTip(
                "Remove only this controller's RAM overlay. Other owners and the "
                "persistent core remain active."
            )
        if methods["auto_restore"] is None:
            self.policy_auto_restore_check.setToolTip(unavailable)
        elif not saved_overlay:
            self.policy_auto_restore_check.setToolTip(
                "Load a source-bound RAM overlay before opting into reboot restore."
            )
        elif not source_matches:
            self.policy_auto_restore_check.setToolTip(
                "The visible source request must match the saved request, including "
                "a verified MAC for Auto."
            )
        else:
            self.policy_auto_restore_check.setToolTip(
                "Explicit opt-in: after a new router runtime is observed, the "
                "controller makes at most one restore attempt. No periodic polling."
            )

    def _selected_policy_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self.policy_tree.selectedItems()
        )

    def _policy_selection_changed(self) -> None:
        self._sync_policy_apply_ui()

    def _sync_policy_apply_ui(self) -> None:
        if not hasattr(self, "apply_selected_button"):
            return
        storage = self._hybrid_policy_storage()
        if storage is not None:
            self.apply_button.hide()
            self.apply_selected_button.hide()
            self.policy_storage_group.show()
            self._render_hybrid_policy_capacity(storage)
            self._sync_hybrid_policy_ui(storage)
            return
        self._render_legacy_policy_storage()
        summary = self.policy_preflight or self.controller.policy_preflight()
        self.policy_preflight = summary
        writable = (
            not self.controller.store.read_only
            and self.controller.store.companion_enabled
            and self.busy_count == 0
        )
        self.apply_button.setEnabled(writable and summary.can_apply)
        if summary.can_apply:
            self.apply_button.setToolTip(
                f"Apply all {summary.rule_count} saved policies "
                f"({summary.compiled_rows:,} compiled rows; "
                f"{summary.compiled_bytes:,} / {summary.limit_bytes:,} bytes)."
            )
        else:
            self.apply_button.setToolTip(
                summary.error or "The complete policy document cannot be applied."
            )

        selected_ids = self._selected_policy_ids()
        self.apply_selected_button.setText(
            "Apply selected" + (f" ({len(selected_ids)})" if selected_ids else "")
        )
        if not selected_ids:
            self.apply_selected_button.setEnabled(False)
            self.apply_selected_button.setToolTip(
                "Select one or more policy rows. Applying a selection leaves "
                "all other policies saved locally."
            )
            return
        selected = self.controller.policy_preflight(selected_ids)
        self.apply_selected_button.setEnabled(writable and selected.can_apply)
        if selected.can_apply:
            self.apply_selected_button.setToolTip(
                f"Apply {selected.rule_count} selected policies "
                f"({selected.compiled_rows:,} compiled rows; "
                f"{selected.compiled_bytes:,} / {selected.limit_bytes:,} bytes). "
                "Unselected policies remain saved locally."
            )
        else:
            self.apply_selected_button.setToolTip(
                selected.error or "The selected policies cannot be applied."
            )

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

    def _show_services_for_policy(self) -> None:
        self.navigation.setCurrentRow(self._page_index("services"))
        self.service_search.setFocus()

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

    def _filtered_services(self) -> list[Service]:
        query = self.service_search.text().strip().casefold()
        category = self.service_category_filter.currentData()
        profile_type = self.service_profile_filter.currentData()
        country = self.service_country_filter.currentData()
        services = [
            service
            for service in self.controller.catalog.services
            if (not query or query in service.search_text)
            and (category == "all" or service.category == category)
            and (profile_type == "all" or service.profile_type == profile_type)
            and (country == "all" or service.provider_country == country)
        ]
        services.sort(key=lambda item: (item.company.casefold(), item.name.casefold()))
        return services

    def _render_services(self) -> None:
        if not hasattr(self, "service_tree"):
            return
        existing = {
            rule.selector: rule
            for rule in self.controller.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        services = self._filtered_services()
        catalog_ids = {service.id for service in self.controller.catalog.services}
        self._selected_service_ids.intersection_update(catalog_ids)
        self._syncing_service_selection = True
        try:
            self.service_tree.clear()
            for service in services:
                rule = existing.get(service.id)
                item = QTreeWidgetItem(
                    [
                        "",
                        service.name,
                        service.company,
                        service.category,
                        service.profile_type.replace("-", " ").title(),
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
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                item.setData(1, Qt.ItemDataRole.UserRole, service.id)
                selected = service.id in self._selected_service_ids
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked,
                )
                self.service_tree.addTopLevelItem(item)
                item.setSelected(selected)
        finally:
            self._syncing_service_selection = False
        self.service_count.setText(
            f"{len(services)} of {len(self.controller.catalog.services)}"
        )
        self._sync_service_selection_ui(services)

    def _visible_service_ids(self) -> set[str]:
        return {
            str(
                self.service_tree.topLevelItem(index).data(
                    1,
                    Qt.ItemDataRole.UserRole,
                )
            )
            for index in range(self.service_tree.topLevelItemCount())
        }

    def _service_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._syncing_service_selection or column != 0:
            return
        service_id = str(item.data(1, Qt.ItemDataRole.UserRole))
        self._set_service_selected(
            service_id,
            item.checkState(0) == Qt.CheckState.Checked,
        )

    def _service_select_cell_clicked(self, item: QTreeWidgetItem) -> None:
        service_id = str(item.data(1, Qt.ItemDataRole.UserRole))
        self._set_service_selected(
            service_id,
            service_id not in self._selected_service_ids,
        )

    def _service_row_selection_changed(self) -> None:
        if self._syncing_service_selection:
            return
        visible_ids = self._visible_service_ids()
        selected_visible = {
            str(item.data(1, Qt.ItemDataRole.UserRole))
            for item in self.service_tree.selectedItems()
        }
        self._selected_service_ids.difference_update(visible_ids)
        self._selected_service_ids.update(selected_visible)
        self._sync_service_selection_ui()

    def _set_service_selected(self, service_id: str, selected: bool) -> None:
        if selected:
            self._selected_service_ids.add(service_id)
        else:
            self._selected_service_ids.discard(service_id)
        self._sync_service_selection_ui()

    def _toggle_visible_service_selection(self, state: Qt.CheckState) -> None:
        if self._syncing_service_selection:
            return
        visible_ids = self._visible_service_ids()
        if state == Qt.CheckState.Checked:
            self._selected_service_ids.update(visible_ids)
        else:
            self._selected_service_ids.difference_update(visible_ids)
        self._sync_service_selection_ui()

    def _clear_service_selection(self) -> None:
        self._selected_service_ids.clear()
        self._sync_service_selection_ui()

    def _sync_service_selection_ui(
        self,
        visible_services: list[Service] | None = None,
    ) -> None:
        if not hasattr(self, "service_select_visible"):
            return
        visible_ids = (
            {service.id for service in visible_services}
            if visible_services is not None
            else self._visible_service_ids()
        )
        selected_visible = visible_ids & self._selected_service_ids
        all_visible_selected = bool(visible_ids) and selected_visible == visible_ids
        partially_selected = bool(selected_visible) and not all_visible_selected

        self._syncing_service_selection = True
        try:
            for index in range(self.service_tree.topLevelItemCount()):
                item = self.service_tree.topLevelItem(index)
                service_id = str(item.data(1, Qt.ItemDataRole.UserRole))
                selected = service_id in self._selected_service_ids
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked,
                )
                item.setSelected(selected)
            if partially_selected:
                self.service_select_visible.setCheckState(
                    Qt.CheckState.PartiallyChecked
                )
            elif all_visible_selected:
                self.service_select_visible.setCheckState(Qt.CheckState.Checked)
            else:
                self.service_select_visible.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._syncing_service_selection = False

        count = len(self._selected_service_ids)
        noun = "service" if count == 1 else "services"
        hidden_count = count - len(selected_visible)
        hidden = f" · {hidden_count} hidden by filters" if hidden_count else ""
        self.service_selection_count.setText(f"{count} {noun} selected{hidden}")
        self.service_clear_selection_button.setEnabled(count > 0)
        self.service_add_selected_button.setEnabled(count > 0)

    def _add_services(self, mode: ServiceRouteMode | str) -> None:
        try:
            route_mode = ServiceRouteMode(mode)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not update services", str(exc))
            return
        selected = [
            service.id
            for service in self.controller.catalog.services
            if service.id in self._selected_service_ids
        ]
        if not selected:
            self._select_something("Select one or more service rows first.")
            return
        try:
            summary = self.controller.add_services(selected, route_mode)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not update services", str(exc))
            return
        self._selected_service_ids.clear()
        self._render_after_policy_change()
        if summary.added == 0 and summary.updated == 0:
            detail = "Selected policies already use this route."
        else:
            detail = (
                f"Saved locally: {summary.added} added, "
                f"{summary.updated} updated. Apply policies when ready."
            )
        self.statusBar().showMessage(
            detail,
            6000,
        )

    def _render_countries(self) -> None:
        if not hasattr(self, "country_tree"):
            return
        enabled = [rule for rule in self.controller.store.rules if rule.enabled]
        requested = {
            rule.region
            for rule in enabled
            if rule.target is RouteTarget.VPN
            and rule.region not in {"direct", "active-astrill"}
        }
        groups = self.controller.server_catalog.groups
        active_group: str | None = None
        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        for region_id, servers in groups.items():
            if any(server.id == current_id for server in servers):
                active_group = region_id
                break
        tunnel_connected = self.router_status.get("vpn_state") == "up"
        if not tunnel_connected:
            active_group = None
        region_names = self.controller.catalog.regions_by_id
        if len(requested) > 1:
            names = ", ".join(
                region_names[region_id].name
                for region_id in sorted(requested)
                if region_id in region_names
            )
            self.country_banner.setText(
                f"Country conflict: {names} cannot be active on one shared tunnel."
            )
            self.country_banner.show()
        elif requested and active_group not in requested:
            requested_id = next(iter(requested))
            requested_name = region_names[requested_id].name
            active_name = (
                region_names[active_group].name
                if active_group in region_names
                else "no connected endpoint"
            )
            self.country_banner.setText(
                f"Policies request {requested_name}; active region is {active_name}."
            )
            self.country_banner.show()
        else:
            self.country_banner.hide()

        self.country_tree.clear()
        assigned_count = 0
        for region in self.controller.catalog.regions:
            policies = [rule for rule in enabled if rule.region == region.id]
            assigned_count += len(policies)
            policy_names = ", ".join(rule.name for rule in policies[:3])
            if len(policies) > 3:
                policy_names += f", +{len(policies) - 3} more"
            policy_summary = (
                "No enabled policies"
                if not policies
                else f"{len(policies)} · {policy_names}"
            )
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
                "Active" if (region.id == active_group and tunnel_connected) else ""
            )
            item = QTreeWidgetItem(
                [
                    region.name,
                    region.kind.title(),
                    policy_summary,
                    endpoints,
                    active,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, region.id)
            self.country_tree.addTopLevelItem(item)
        noun = "policy" if assigned_count == 1 else "policies"
        self.country_result_count.setText(
            f"{assigned_count} enabled {noun} across one shared Astrill tunnel · "
            "double-click a region to inspect endpoints."
        )

    def _open_region_endpoints(self, region_id: str) -> None:
        region = self.controller.catalog.regions_by_id.get(region_id)
        if region is None or region.kind == "direct":
            return
        self.endpoint_country_filter.setCurrentIndex(0)
        self.endpoint_search.setText(
            "" if region_id == "active-astrill" else region.name
        )
        self.navigation.setCurrentRow(self._page_index("endpoints"))

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
        if not isinstance(result, ServerCatalog):
            return
        self._apply_server_catalog(result)
        QTimer.singleShot(0, lambda: self._sync_endpoint_favorites(quiet=True))

    def _apply_server_catalog(self, catalog: ServerCatalog) -> None:
        servers = catalog.servers
        self._endpoint_catalog_loaded = True
        self.load_endpoints_button.setText("Reload endpoints")
        available_ids = {server.id for server in servers}
        if self._endpoint_selected_server_id not in available_ids:
            self._endpoint_selected_server_id = None
        self._endpoint_selected_server_ids.intersection_update(available_ids)
        self._refresh_endpoint_country_filter()
        self._render_endpoints()
        self._render_countries()
        self._update_status_metrics()
        if self.native_settings is not None:
            self.connection_page.sync(
                self.native_settings,
                servers,
                self.router_status,
            )

    def _refresh_endpoint_country_filter(self) -> None:
        selected = str(self.endpoint_country_filter.currentData() or "")
        countries = sorted(
            {
                server.country_name()
                for server in self.controller.server_catalog.servers
            },
            key=str.casefold,
        )
        self.endpoint_country_filter.blockSignals(True)
        try:
            self.endpoint_country_filter.clear()
            self.endpoint_country_filter.addItem("All countries", "")
            for country in countries:
                self.endpoint_country_filter.addItem(country, country)
            index = self.endpoint_country_filter.findData(selected)
            self.endpoint_country_filter.setCurrentIndex(max(index, 0))
            self.endpoint_country_filter.setEnabled(bool(countries))
        finally:
            self.endpoint_country_filter.blockSignals(False)

    def _endpoint_protocol_selected(self, _index: int) -> None:
        self._endpoint_protocol_user_selected = True
        self._sync_endpoint_action_ui()

    def _endpoint_protocol_changed(self, _index: int) -> None:
        self._render_endpoints()

    def _endpoint_sort_changed(self, _index: int) -> None:
        if self._syncing_endpoint_sort:
            return
        mode = str(self.endpoint_sort.currentData())
        if mode == "header" and self._endpoint_header_sort_column is None:
            self._endpoint_header_sort_column = ENDPOINT_NAME_COLUMN
            self._endpoint_header_sort_descending = False
        self._sync_endpoint_sort_indicator()
        self._render_endpoints()

    def _endpoint_header_clicked(self, column: int) -> None:
        if column not in ENDPOINT_HEADER_SORT_FIELDS:
            return
        if (
            self.endpoint_sort.currentData() == "header"
            and self._endpoint_header_sort_column == column
        ):
            self._endpoint_header_sort_descending = (
                not self._endpoint_header_sort_descending
            )
        else:
            self._endpoint_header_sort_column = column
            self._endpoint_header_sort_descending = ENDPOINT_HEADER_DEFAULT_DESCENDING[
                column
            ]
        label = self.endpoint_tree.headerItem().text(column)
        direction = (
            "high–low"
            if self._endpoint_header_sort_descending
            and column
            in {
                ENDPOINT_SERVER_ID_COLUMN,
                ENDPOINT_NODES_COLUMN,
                ENDPOINT_LATENCY_COLUMN,
                ENDPOINT_TESTED_COLUMN,
            }
            else "low–high"
            if not self._endpoint_header_sort_descending
            and column
            in {
                ENDPOINT_SERVER_ID_COLUMN,
                ENDPOINT_NODES_COLUMN,
                ENDPOINT_LATENCY_COLUMN,
                ENDPOINT_TESTED_COLUMN,
            }
            else "descending"
            if self._endpoint_header_sort_descending
            else "ascending"
        )
        header_index = self.endpoint_sort.findData("header")
        self._syncing_endpoint_sort = True
        try:
            self.endpoint_sort.setItemText(
                header_index,
                f"Header: {label} ({direction})",
            )
            self.endpoint_sort.setCurrentIndex(header_index)
        finally:
            self._syncing_endpoint_sort = False
        self._sync_endpoint_sort_indicator()
        self._render_endpoints()

    def _sync_endpoint_sort_indicator(self) -> None:
        if not hasattr(self, "endpoint_tree"):
            return
        mode = str(self.endpoint_sort.currentData())
        header = self.endpoint_tree.header()
        if mode == "default":
            header.setSortIndicatorShown(False)
            return
        if mode == "region":
            column = ENDPOINT_REGION_COLUMN
            descending = False
        elif mode == "latency":
            column = ENDPOINT_LATENCY_COLUMN
            descending = False
        else:
            column = self._endpoint_header_sort_column
            descending = self._endpoint_header_sort_descending
        if column is None:
            header.setSortIndicatorShown(False)
            return
        header.setSortIndicator(
            column,
            (
                Qt.SortOrder.DescendingOrder
                if descending
                else Qt.SortOrder.AscendingOrder
            ),
        )
        header.setSortIndicatorShown(True)

    def _endpoint_selection_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is not None:
            value = current.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
            if isinstance(value, AstrillServer):
                self._endpoint_selected_server_id = value.id
        self._sync_endpoint_action_ui()

    def _endpoint_selection_set_changed(self) -> None:
        if self._syncing_endpoint_selection:
            return
        self._endpoint_selection_user_managed = True
        visible_ids = {server.id for server in self._visible_endpoint_servers()}
        selected_visible = {
            server.id
            for item in self.endpoint_tree.selectedItems()
            if (
                server := item.data(
                    ENDPOINT_NAME_COLUMN,
                    Qt.ItemDataRole.UserRole,
                )
            )
            and isinstance(server, AstrillServer)
        }
        self._endpoint_selected_server_ids.difference_update(visible_ids)
        self._endpoint_selected_server_ids.update(selected_visible)
        self._sync_endpoint_selection_ui()
        if (
            self.endpoint_sort.currentData() == "header"
            and self._endpoint_header_sort_column == ENDPOINT_SELECT_COLUMN
        ):
            self._render_endpoints()
            return
        self._sync_endpoint_action_ui()

    def _endpoint_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if self._syncing_endpoint_selection or column != ENDPOINT_SELECT_COLUMN:
            return
        server = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        if not isinstance(server, AstrillServer):
            return
        selected = item.checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked
        self._set_endpoint_selected(server.id, selected, item=item)

    def _endpoint_select_cell_clicked(self, item: object) -> None:
        if not isinstance(item, QTreeWidgetItem):
            return
        server = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        if not isinstance(server, AstrillServer):
            return
        selected = server.id not in self._endpoint_selected_server_ids
        self._set_endpoint_selected(server.id, selected, item=item)

    def _endpoint_favorite_cell_clicked(self, item: object) -> None:
        if not isinstance(item, QTreeWidgetItem):
            return
        server = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        if not isinstance(server, AstrillServer):
            return
        self._toggle_selected_endpoint_favorite(server)

    def _set_endpoint_selected(
        self,
        server_id: int,
        selected: bool,
        *,
        item: QTreeWidgetItem | None = None,
    ) -> None:
        self._endpoint_selection_user_managed = True
        if selected:
            self._endpoint_selected_server_ids.add(server_id)
        else:
            self._endpoint_selected_server_ids.discard(server_id)
        self._syncing_endpoint_selection = True
        try:
            if item is not None:
                item.setCheckState(
                    ENDPOINT_SELECT_COLUMN,
                    (Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked),
                )
                item.setSelected(selected)
                if selected:
                    self.endpoint_tree.setCurrentItem(item)
        finally:
            self._syncing_endpoint_selection = False
        self._sync_endpoint_selection_ui()
        if (
            self.endpoint_sort.currentData() == "header"
            and self._endpoint_header_sort_column == ENDPOINT_SELECT_COLUMN
        ):
            self._render_endpoints()
            return
        self._sync_endpoint_action_ui()

    def _toggle_visible_endpoint_selection(self, state: Qt.CheckState) -> None:
        if self._syncing_endpoint_selection:
            return
        self._endpoint_selection_user_managed = True
        visible = self._visible_endpoint_servers()
        if state == Qt.CheckState.Checked:
            self._endpoint_selected_server_ids.update(server.id for server in visible)
        elif state == Qt.CheckState.Unchecked:
            self._endpoint_selected_server_ids.difference_update(
                server.id for server in visible
            )
        else:
            return
        self._sync_endpoint_selection_ui()
        if (
            self.endpoint_sort.currentData() == "header"
            and self._endpoint_header_sort_column == ENDPOINT_SELECT_COLUMN
        ):
            self._render_endpoints()

    def _clear_endpoint_selection(self) -> None:
        self._endpoint_selection_user_managed = True
        self._endpoint_selected_server_ids.clear()
        self._endpoint_selected_server_id = None
        self.endpoint_tree.clearSelection()
        self._sync_endpoint_selection_ui()
        if (
            self.endpoint_sort.currentData() == "header"
            and self._endpoint_header_sort_column == ENDPOINT_SELECT_COLUMN
        ):
            self._render_endpoints()

    def _sync_endpoint_selection_ui(self) -> None:
        if not hasattr(self, "endpoint_tree"):
            return
        self._syncing_endpoint_selection = True
        try:
            visible_ids: set[int] = set()
            for index in range(self.endpoint_tree.topLevelItemCount()):
                item = self.endpoint_tree.topLevelItem(index)
                server = item.data(
                    ENDPOINT_NAME_COLUMN,
                    Qt.ItemDataRole.UserRole,
                )
                if not isinstance(server, AstrillServer):
                    continue
                visible_ids.add(server.id)
                selected = server.id in self._endpoint_selected_server_ids
                item.setCheckState(
                    ENDPOINT_SELECT_COLUMN,
                    (Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked),
                )
                item.setSelected(selected)
            selected_visible = visible_ids & self._endpoint_selected_server_ids
            if visible_ids and selected_visible == visible_ids:
                master_state = Qt.CheckState.Checked
            elif selected_visible:
                master_state = Qt.CheckState.PartiallyChecked
            else:
                master_state = Qt.CheckState.Unchecked
            self.endpoint_select_visible.setCheckState(master_state)
        finally:
            self._syncing_endpoint_selection = False
        count = len(self._endpoint_selected_server_ids)
        visible_count = len(
            {server.id for server in self._visible_endpoint_servers()}
            & self._endpoint_selected_server_ids
        )
        hidden_count = count - visible_count
        suffix = f" · {hidden_count} hidden by filters" if hidden_count else ""
        self.endpoint_selection_status.setText(f"{count} selected{suffix}")
        self.endpoint_selection_status.setToolTip(
            f"{count} endpoint{'' if count == 1 else 's'} selected. "
            "Use row checkboxes, Ctrl/Command, or Shift; filtered selections "
            f"remain selected{f' ({hidden_count} currently hidden)' if hidden_count else ''}."
        )
        self.endpoint_clear_selection_button.setEnabled(
            self.busy_count == 0 and count > 0
        )

    def _visible_endpoint_servers(self) -> tuple[AstrillServer, ...]:
        values: list[AstrillServer] = []
        for index in range(self.endpoint_tree.topLevelItemCount()):
            item = self.endpoint_tree.topLevelItem(index)
            value = item.data(
                ENDPOINT_NAME_COLUMN,
                Qt.ItemDataRole.UserRole,
            )
            if isinstance(value, AstrillServer):
                values.append(value)
        return tuple(values)

    def _selected_endpoints(self) -> tuple[AstrillServer, ...]:
        selected: list[AstrillServer] = []
        seen: set[int] = set()
        for server in self._visible_endpoint_servers():
            if (
                server.id in self._endpoint_selected_server_ids
                and server.id not in seen
            ):
                selected.append(server)
                seen.add(server.id)
        for server in self.controller.server_catalog.servers:
            if (
                server.id in self._endpoint_selected_server_ids
                and server.id not in seen
            ):
                selected.append(server)
                seen.add(server.id)
        return tuple(selected)

    def _single_selected_endpoint(self) -> AstrillServer | None:
        selected = self._selected_endpoints()
        return selected[0] if len(selected) == 1 else None

    def _endpoint_double_clicked(
        self,
        _item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if column in {ENDPOINT_SELECT_COLUMN, ENDPOINT_FAVORITE_COLUMN}:
            return
        self._connect_endpoint()

    def _selected_endpoint(self) -> AstrillServer | None:
        item = self.endpoint_tree.currentItem()
        if item is None:
            return None
        value = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        return value if isinstance(value, AstrillServer) else None

    def _endpoint_preference_changed(self, key: str, checked: bool) -> None:
        if self._syncing_endpoint_preferences:
            return
        if self.native_settings is None:
            self._sync_endpoint_connection_controls()
            return
        if self.controller.store.read_only:
            self._sync_endpoint_connection_controls()
            self._select_something(
                "Turn off the read-only guard in Settings before changing "
                "router connection behavior."
            )
            return
        if self.native_page.dirty or self.connection_page.dirty:
            self._sync_endpoint_connection_controls()
            QMessageBox.information(
                self,
                "Unsaved Astrill settings",
                "Save or reload the pending edits on the Astrill or Connection "
                "page before changing router connection behavior.",
            )
            return
        value = "1" if checked else "0"
        if self.native_settings.get(key) == value:
            return
        self._endpoint_native_pending.add(key)

        def updated(settings: NativeAstrillSettings) -> None:
            self._endpoint_native_pending.discard(key)
            self._native_settings_loaded(settings)

        def finished() -> None:
            self._endpoint_native_pending.discard(key)
            self._sync_endpoint_connection_controls()

        self._run_task(
            "Updating router connection behavior",
            lambda: self.controller.save_native_settings({key: value}),
            updated,
            finished_callback=finished,
        )

    def _sync_endpoint_connection_controls(self) -> None:
        if not hasattr(self, "endpoint_autocycle"):
            return
        settings = self.native_settings
        self._syncing_endpoint_preferences = True
        try:
            if "astrill_autocycle" not in self._endpoint_native_pending:
                self.endpoint_autocycle.setChecked(
                    settings is not None and settings.enabled("astrill_autocycle")
                )
            if "astrill_autostart" not in self._endpoint_native_pending:
                self.endpoint_autostart.setChecked(
                    settings is not None and settings.enabled("astrill_autostart")
                )
        finally:
            self._syncing_endpoint_preferences = False

        dirty = hasattr(self, "native_page") and (
            self.native_page.dirty or self.connection_page.dirty
        )
        editable = (
            settings is not None
            and self.busy_count == 0
            and not self.controller.store.read_only
            and not dirty
        )
        self.endpoint_autocycle.setEnabled(
            editable and "astrill_autocycle" not in self._endpoint_native_pending
        )
        self.endpoint_autostart.setEnabled(
            editable and "astrill_autostart" not in self._endpoint_native_pending
        )

    def _render_endpoints(self) -> None:
        if not hasattr(self, "endpoint_tree"):
            return
        query = self.endpoint_search.text().strip().casefold()
        selected_country = str(self.endpoint_country_filter.currentData() or "")
        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        connected = self.router_status.get("vpn_state") == "up"
        selected_id = self._endpoint_selected_server_id
        if selected_id is None:
            selected_id = current_id
        if (
            not self._endpoint_selection_user_managed
            and not self._endpoint_selected_server_ids
            and selected_id
            and any(
                server.id == selected_id
                for server in self.controller.server_catalog.servers
            )
        ):
            self._endpoint_selected_server_ids.add(selected_id)
        group_by_id: dict[int, str] = {}
        for region_id, servers in self.controller.server_catalog.groups.items():
            for server in servers:
                group_by_id.setdefault(server.id, region_id)
        rows: list[EndpointListRow] = []
        for source_index, server in enumerate(self.controller.server_catalog.servers):
            region_id = group_by_id.get(server.id, "")
            region_name = self._region_name(region_id)
            country_name = server.country_name()
            searchable = (
                f"{server.name} {country_name} {region_name} {server.id}".casefold()
            )
            if (selected_country and country_name != selected_country) or (
                query and query not in searchable
            ):
                continue
            rows.append(
                EndpointListRow(
                    source_index=source_index,
                    server=server,
                    region_id=region_id,
                    region_name=region_name,
                )
            )
        sort_mode = str(self.endpoint_sort.currentData())
        if sort_mode == "header":
            column = self._endpoint_header_sort_column
            if column is None:
                column = ENDPOINT_NAME_COLUMN
            rows = list(
                sort_endpoint_rows_by_header(
                    rows,
                    ENDPOINT_HEADER_SORT_FIELDS[column],
                    self._endpoint_header_sort_descending,
                    self._endpoint_probe_results,
                    self.protocol.currentIndex(),
                    selected_server_ids=self._endpoint_selected_server_ids,
                    favorite_server_ids=(
                        self._endpoint_favorite_records.keys()
                        if self._endpoint_favorites_valid is True
                        else None
                    ),
                    current_server_id=current_id,
                    connected=connected,
                )
            )
        else:
            rows = list(
                sort_endpoint_rows(
                    rows,
                    sort_mode,
                    self._endpoint_probe_results,
                    self.protocol.currentIndex(),
                )
            )
        self._syncing_endpoint_selection = True
        self.endpoint_tree.clear()
        item_to_select: QTreeWidgetItem | None = None
        first_selected: QTreeWidgetItem | None = None
        try:
            for row in rows:
                server = row.server
                configured = server.id == current_id
                state = (
                    "Connected"
                    if configured and connected
                    else ("Configured" if configured else "")
                )
                item = QTreeWidgetItem(
                    [
                        "",
                        server.name,
                        row.region_name,
                        self._endpoint_favorite_cell(server),
                        str(server.id),
                        state,
                        str(len(server.nodes)),
                        *self._endpoint_probe_cells(server),
                    ]
                )
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                item.setData(
                    ENDPOINT_NAME_COLUMN,
                    Qt.ItemDataRole.UserRole,
                    server,
                )
                item.setCheckState(
                    ENDPOINT_SELECT_COLUMN,
                    (
                        Qt.CheckState.Checked
                        if server.id in self._endpoint_selected_server_ids
                        else Qt.CheckState.Unchecked
                    ),
                )
                self._decorate_endpoint_favorite(item, server)
                self._decorate_endpoint_probe_result(item, server)
                self.endpoint_tree.addTopLevelItem(item)
                if (
                    first_selected is None
                    and server.id in self._endpoint_selected_server_ids
                ):
                    first_selected = item
                if server.id == selected_id:
                    item_to_select = item
            if item_to_select is None:
                item_to_select = first_selected
            if item_to_select is not None:
                self.endpoint_tree.setCurrentItem(item_to_select)
        finally:
            self._syncing_endpoint_selection = False
        self._sync_endpoint_selection_ui()
        self._sync_endpoint_sort_indicator()
        self._sync_endpoint_action_ui()

    def _endpoint_favorite_cell(self, server: AstrillServer) -> str:
        if self._endpoint_favorites_valid is None:
            return "Not synced"
        if self._endpoint_favorites_valid is False:
            return "Invalid"
        return "★ Favorite" if server.id in self._endpoint_favorite_records else "—"

    def _decorate_endpoint_favorite(
        self,
        item: QTreeWidgetItem,
        server: AstrillServer,
    ) -> None:
        if self._endpoint_favorites_valid is None:
            tooltip = "Favorites have not been read from DD-WRT. Select Sync favorites."
            color = COLORS["muted"]
        elif self._endpoint_favorites_valid is False:
            tooltip = (
                "DD-WRT returned malformed favorite data. It is preserved, and "
                "favorite changes are disabled."
            )
            color = "#b91c1c"
        elif server.id in self._endpoint_favorite_records:
            favorite = self._endpoint_favorite_records[server.id]
            transport = "TCP" if favorite.mode else "UDP"
            tooltip = (
                f"Router favorite · {transport} · port {favorite.port}\n"
                "Click to remove it. Membership is synchronized by server ID."
            )
            color = "#7c3aed"
        else:
            tooltip = (
                "Not currently saved in DD-WRT's Astrill favorite list. "
                "Click to add it."
            )
            color = COLORS["muted"]
        item.setToolTip(ENDPOINT_FAVORITE_COLUMN, tooltip)
        item.setForeground(ENDPOINT_FAVORITE_COLUMN, QColor(color))

    def _endpoint_probe_cells(self, server: AstrillServer) -> list[str]:
        saved = self._endpoint_probe_results.get(
            (server.id, self.protocol.currentIndex())
        )
        if saved is None:
            return ["—", "Not tested", "—"]
        result = saved.result
        checked_at = self._format_endpoint_probe_time(saved.checked_at)
        saved_state = assess_saved_endpoint_probe(
            saved,
            server,
            self.protocol.currentIndex(),
        )
        if saved_state is SavedProbeState.ENDPOINT_CHANGED:
            return ["—", "Endpoint changed", checked_at]
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
        if saved_state is SavedProbeState.STALE:
            reach = "Saved · retest"
        return [latency, reach, checked_at]

    def _decorate_endpoint_probe_result(
        self, item: QTreeWidgetItem, server: AstrillServer
    ) -> None:
        saved = self._endpoint_probe_results.get(
            (server.id, self.protocol.currentIndex())
        )
        if saved is None:
            return
        result = saved.result
        checked_at = self._format_endpoint_probe_time(saved.checked_at)
        saved_state = assess_saved_endpoint_probe(
            saved,
            server,
            self.protocol.currentIndex(),
        )
        if saved_state is not SavedProbeState.CURRENT:
            color = COLORS["muted"]
        elif result.status is EndpointProbeStatus.REACHABLE:
            latency = result.latency_ms or 0.0
            color = (
                "#0f766e"
                if latency < 80
                else ("#d97706" if latency < 180 else "#e11d48")
            )
        else:
            color = "#b91c1c"
        for column in ENDPOINT_LATENCY_RESULT_COLUMNS:
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
            f"Saved PC test from {checked_at}\n"
            f"Target: {target}\n"
            f"Method: {method}\n"
            f"{result.detail}"
        )
        if saved_state is SavedProbeState.STALE:
            tooltip += "\nSaved result is over 24 hours old; retest manually."
        elif saved_state is SavedProbeState.ENDPOINT_CHANGED:
            tooltip += (
                "\nThe applet now advertises a different target; retest manually."
            )
        for column in ENDPOINT_LATENCY_RESULT_COLUMNS:
            item.setToolTip(column, tooltip)

    @staticmethod
    def _format_endpoint_probe_time(checked_at: int) -> str:
        return (
            datetime.fromtimestamp(checked_at)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )

    def _endpoint_probe_selection(self) -> tuple[AstrillServer, ...]:
        scope = self.endpoint_probe_scope.currentData()
        if scope == "all":
            return tuple(self.controller.server_catalog.servers)
        if scope == "visible":
            return self._visible_endpoint_servers()
        return self._selected_endpoints()

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
        checked_at = int(time())
        reachable = 0
        for result in results:
            if not isinstance(result, EndpointProbeResult):
                continue
            self._endpoint_probe_results[
                (result.server_id, result.selected_protocol)
            ] = SavedEndpointProbe(result=result, checked_at=checked_at)
            if result.status is EndpointProbeStatus.REACHABLE:
                reachable += 1
        try:
            save_endpoint_probe_cache(
                self._endpoint_probe_cache_path,
                self._endpoint_probe_results,
            )
        except (OSError, TypeError, ValueError) as exc:
            self.endpoint_probe_status.setText(
                f"Test complete · {reachable}/{len(results)} reachable · "
                f"could not save results: {exc}"
            )
        else:
            self.endpoint_probe_status.setText(
                f"Manual PC test saved · {reachable}/{len(results)} reachable · "
                "no DD-WRT commands sent."
            )
        self._render_endpoints()

    def _endpoint_probe_finished(self) -> None:
        self._endpoint_probe_running = False
        self._sync_endpoint_action_ui()

    def _clear_endpoint_probe_results(self) -> None:
        self._endpoint_probe_results.clear()
        try:
            save_endpoint_probe_cache(
                self._endpoint_probe_cache_path,
                self._endpoint_probe_results,
            )
        except OSError as exc:
            self.endpoint_probe_status.setText(
                f"Results cleared in this window, but the saved cache could not "
                f"be removed: {exc}"
            )
        else:
            self.endpoint_probe_status.setText(
                "Saved results cleared · tests run only when you start one here."
            )
        self._render_endpoints()

    def _sync_endpoint_favorites(self, *, quiet: bool = False) -> None:
        if self._native_settings_loading:
            return
        self._native_settings_loading = True
        self.endpoint_favorite_status.setText(
            "Reading the current favorite list from DD-WRT…"
        )
        self._run_task(
            "Syncing Astrill favorites",
            self.controller.load_native_settings,
            self._endpoint_favorites_loaded,
            quiet=quiet,
            finished_callback=self._native_settings_finished,
        )

    def _endpoint_favorites_loaded(self, settings: object) -> None:
        if not isinstance(settings, NativeAstrillSettings):
            return
        self._apply_native_settings(
            settings,
            force_native_page=False,
        )
        self.statusBar().showMessage(
            "Astrill favorites synchronized from DD-WRT.", 4000
        )

    def _toggle_selected_endpoint_favorite(
        self,
        endpoint: AstrillServer | None = None,
    ) -> None:
        selected = (endpoint,) if endpoint is not None else self._selected_endpoints()
        if len(selected) != 1:
            self._select_something("Select exactly one Astrill endpoint first.")
            return
        self._set_selected_endpoint_favorites(
            selected[0].id not in self._endpoint_favorite_records,
            endpoints=selected,
        )

    def _set_selected_endpoint_favorites(
        self,
        enabled: bool,
        *,
        endpoints: tuple[AstrillServer, ...] | None = None,
    ) -> None:
        if self.busy_count:
            self.statusBar().showMessage("Wait for the current action to finish.", 4000)
            return
        if self.controller.store.read_only:
            self._select_something(
                "The read-only guard blocks favorite changes. Turn it off in "
                "Settings first."
            )
            return
        if self.connection_page.has_pending_favorite_changes:
            self._select_something(
                "The Connection page has unsaved favorite edits. Save or reload "
                "that draft before changing the same favorite list here."
            )
            return
        if self._endpoint_favorites_valid is not True:
            self._select_something(
                "Sync a valid favorite list from DD-WRT before changing it."
            )
            return
        servers = self._selected_endpoints() if endpoints is None else endpoints
        if not servers:
            self._select_something(
                "Select one or more Astrill endpoints using the checkboxes, "
                "Ctrl/Command, or Shift."
            )
            return

        protocol = self.protocol.currentIndex()
        changed = tuple(
            server
            for server in servers
            if (
                server.id not in self._endpoint_favorite_records
                if enabled
                else server.id in self._endpoint_favorite_records
            )
        )
        if not changed:
            self.endpoint_favorite_status.setText(
                "Every selected endpoint already has the requested favorite state."
            )
            return
        if enabled:
            unsupported: list[str] = []
            for server in changed:
                try:
                    server.endpoint_for(protocol)
                except ValueError:
                    unsupported.append(server.name)
            if unsupported:
                names = "\n".join(f"  • {name}" for name in unsupported[:12])
                extra = (
                    f"\n  • …and {len(unsupported) - 12} more"
                    if len(unsupported) > 12
                    else ""
                )
                QMessageBox.warning(
                    self,
                    "Unsupported endpoint protocol",
                    f"{len(unsupported)} selected endpoint"
                    f"{'' if len(unsupported) == 1 else 's'} do not offer "
                    f"{ASTRILL_PROTOCOL_NAMES[protocol]}:\n\n"
                    f"{names}{extra}\n\nChoose another protocol or adjust the "
                    "selection. Nothing was written.",
                )
                return
            action = "Favorite"
            detail = (
                f"Add {len(changed)} selected endpoint"
                f"{'' if len(changed) == 1 else 's'} to the router's Astrill "
                f"favorites using {ASTRILL_PROTOCOL_NAMES[protocol]}?"
            )
        else:
            action = "Unfavorite"
            detail = (
                f"Remove {len(changed)} selected endpoint"
                f"{'' if len(changed) == 1 else 's'} from the router's Astrill "
                "favorites?"
            )

        preview = "\n".join(f"  • {server.name}" for server in changed[:10])
        if len(changed) > 10:
            preview += f"\n  • …and {len(changed) - 10} more"

        detail += (
            f"\n\n{preview}"
            "\n\nOnly the native astrill_favlist value will change. The app "
            "will fresh-read the complete list, preserve every other favorite, "
            "commit the whole batch once, and verify the "
            "readback. It will not reconnect Astrill, switch endpoints, run a "
            "latency test, or enable background polling."
        )
        if (
            QMessageBox.warning(
                self,
                f"{action} selected endpoints",
                detail,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        task_action = "Adding" if enabled else "Removing"
        self._run_task(
            f"{task_action} {len(changed)} router favorites",
            lambda: self.controller.set_endpoint_favorites(
                servers,
                protocol if enabled else None,
                enabled=enabled,
            ),
            lambda result: self._endpoint_favorite_changed(
                result,
                len(changed),
                enabled,
            ),
        )

    def _endpoint_favorite_changed(
        self,
        result: object,
        changed_count: int,
        enabled: bool,
    ) -> None:
        if not isinstance(result, NativeAstrillSettings):
            return
        self._apply_native_settings(result, force_native_page=False)
        action = "added to" if enabled else "removed from"
        self.endpoint_favorite_status.setText(
            f"{changed_count} endpoint"
            f"{'' if changed_count == 1 else 's'} {action} router favorites "
            "· one verified DD-WRT commit."
        )

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
        if self.native_page.dirty or self.connection_page.dirty:
            self._select_something(
                "Save or reload the unsaved Astrill or Connection-page edits "
                "before connecting from the Endpoints page."
            )
            return
        server = self._single_selected_endpoint()
        if server is None:
            self._select_something(
                "Select exactly one Astrill endpoint before connecting the router."
            )
            return
        protocol = self.protocol.currentIndex()
        try:
            server.endpoint_for(protocol)
        except ValueError as exc:
            QMessageBox.warning(self, "Unsupported endpoint protocol", str(exc))
            return
        connection_path = (
            "The installed companion will perform the switch and verify the "
            "native readback."
            if self.controller.store.companion_enabled
            else "Native-only mode will disconnect if needed, save the new "
            "selection, connect, verify the readback, and roll back the previous "
            "selection if connection fails."
        )
        detail = (
            f"Connect the router's shared Astrill tunnel to {server.name} using "
            f"{ASTRILL_PROTOCOL_NAMES[protocol]}?\n\nThis writes the selected "
            "endpoint to DD-WRT and reconnects the router tunnel, so all "
            "Astrill-routed traffic will pause briefly. It does not change this "
            f"Windows computer's VPN or local routing.\n\n{connection_path}"
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
            lambda: self.controller.apply_server_connection(server, protocol),
            self._endpoint_connection_applied,
        )

    def _endpoint_connection_applied(self, result: object) -> None:
        if not isinstance(result, AstrillConnectionResult):
            return
        self._apply_native_settings(result.settings, force_native_page=False)
        self._endpoint_connected(result.status)

    def _endpoint_connected(self, status: object) -> None:
        self._endpoint_protocol_user_selected = False
        self._status_loaded(status)

    def _sync_endpoint_action_ui(self) -> None:
        if not hasattr(self, "connect_endpoint_button"):
            return
        idle = self.busy_count == 0
        read_only = self.controller.store.read_only
        companion_enabled = self.controller.store.companion_enabled
        selected_endpoints = self._selected_endpoints()
        selected = selected_endpoints[0] if len(selected_endpoints) == 1 else None
        native_dirty = hasattr(self, "native_page") and (
            self.native_page.dirty or self.connection_page.dirty
        )
        pending_connection_favorites = (
            hasattr(self, "connection_page")
            and self.connection_page.has_pending_favorite_changes
        )

        protocol_supported = False
        if selected is not None:
            try:
                selected.endpoint_for(self.protocol.currentIndex())
                protocol_supported = True
            except ValueError:
                pass

        self.load_endpoints_button.setEnabled(idle)
        self.endpoint_search.setEnabled(idle)
        self.endpoint_country_filter.setEnabled(
            idle and bool(self.controller.server_catalog.servers)
        )
        self.protocol.setEnabled(idle)
        self.endpoint_sort.setEnabled(idle)
        self.endpoint_tree.setEnabled(idle)
        self.endpoint_select_visible.setEnabled(
            idle and bool(self._visible_endpoint_servers())
        )
        self.endpoint_clear_selection_button.setEnabled(
            idle and bool(selected_endpoints)
        )
        self.endpoint_probe_scope.setEnabled(idle)
        probe_targets = self._endpoint_probe_selection()
        self.endpoint_probe_button.setEnabled(
            idle and self._endpoint_catalog_loaded and bool(probe_targets)
        )
        self.endpoint_probe_clear_button.setEnabled(
            idle and bool(self._endpoint_probe_results)
        )
        target_count = len(probe_targets)
        self.endpoint_probe_target_status.setText(
            f"{target_count} endpoint{'' if target_count == 1 else 's'} in scope "
            f"· {ASTRILL_PROTOCOL_NAMES[self.protocol.currentIndex()]} "
            "· results persist until cleared."
        )
        self.endpoint_favorite_sync_button.setEnabled(idle)
        favorite_ids = self._endpoint_favorite_records.keys()
        missing_favorites = tuple(
            server for server in selected_endpoints if server.id not in favorite_ids
        )
        selected_favorites = tuple(
            server for server in selected_endpoints if server.id in favorite_ids
        )
        unsupported_missing: list[AstrillServer] = []
        for server in missing_favorites:
            try:
                server.endpoint_for(self.protocol.currentIndex())
            except ValueError:
                unsupported_missing.append(server)
        self.endpoint_favorite_button.setText(
            "Favorite selected"
            + (f" ({len(missing_favorites)})" if missing_favorites else "")
        )
        self.endpoint_unfavorite_button.setText(
            "Unfavorite selected"
            + (f" ({len(selected_favorites)})" if selected_favorites else "")
        )
        favorite_editable = (
            idle
            and not read_only
            and not pending_connection_favorites
            and self._endpoint_favorites_valid is True
        )
        self.endpoint_favorite_button.setEnabled(
            favorite_editable and bool(missing_favorites) and not unsupported_missing
        )
        self.endpoint_unfavorite_button.setEnabled(
            favorite_editable and bool(selected_favorites)
        )
        if pending_connection_favorites:
            favorite_tooltip = (
                "The Connection page has unsaved favorite edits. Save or reload "
                "that draft before editing the same favorite list here."
            )
        elif read_only:
            favorite_tooltip = "Turn off the read-only guard in Settings first."
        elif self._endpoint_favorites_valid is None:
            favorite_tooltip = "Sync favorites from DD-WRT first."
        elif self._endpoint_favorites_valid is False:
            favorite_tooltip = (
                "The malformed router favorite list is preserved; editing is blocked."
            )
        elif unsupported_missing:
            favorite_tooltip = (
                f"{len(unsupported_missing)} selected endpoint"
                f"{'' if len(unsupported_missing) == 1 else 's'} do not offer "
                f"{ASTRILL_PROTOCOL_NAMES[self.protocol.currentIndex()]}."
            )
        elif not selected_endpoints:
            favorite_tooltip = (
                "Select endpoints with their checkboxes, Ctrl/Command, or Shift."
            )
        else:
            favorite_tooltip = (
                "Add every selected nonfavorite endpoint in one verified commit."
            )
        self.endpoint_favorite_button.setToolTip(favorite_tooltip)
        self.endpoint_unfavorite_button.setToolTip(
            favorite_tooltip
            if (
                pending_connection_favorites
                or read_only
                or self._endpoint_favorites_valid is not True
            )
            else "Remove every selected current favorite in one verified commit."
        )
        self.connect_endpoint_button.setEnabled(
            idle
            and not read_only
            and not native_dirty
            and selected is not None
            and protocol_supported
        )
        self._sync_endpoint_connection_controls()

        current_id = int(self.router_status.get("astrill_server_id", 0) or 0)
        connected = self.router_status.get("vpn_state") == "up"
        reconnecting = selected is not None and selected.id == current_id and connected
        self.connect_endpoint_button.setText(
            "Reconnect selected" if reconnecting else "Connect selected"
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
        elif pending_connection_favorites:
            message = (
                "The Connection page has unsaved favorite edits. Save or reload "
                "that draft before editing favorites or connecting here."
            )
        elif native_dirty:
            dirty_page = (
                "Astrill and Connection pages"
                if self.native_page.dirty and self.connection_page.dirty
                else ("Astrill page" if self.native_page.dirty else "Connection page")
            )
            message = (
                f"The {dirty_page} has an unsaved draft, so router connection "
                "changes are locked. Favorite membership remains available and "
                "will be merged without discarding that draft."
            )
        elif not selected_endpoints:
            message = (
                "Select an endpoint, choose its protocol, then connect the router."
            )
        elif len(selected_endpoints) > 1:
            message = (
                f"{len(selected_endpoints)} endpoints selected. Favorite, "
                "Unfavorite, and selected-endpoint latency actions use the batch; "
                "connecting the router requires exactly one endpoint."
            )
        elif not protocol_supported:
            message = (
                f"{selected.name} does not offer "
                f"{ASTRILL_PROTOCOL_NAMES[self.protocol.currentIndex()]}. "
                "Choose another protocol or endpoint."
            )
        else:
            path = "companion" if companion_enabled else "transactional native"
            if reconnecting:
                message = (
                    f"{selected.name} is connected on the router. The action will "
                    "reconnect that shared tunnel using the chosen protocol "
                    f"through the {path} path."
                )
            else:
                message = (
                    f"Ready to connect the router's shared tunnel to {selected.name} "
                    f"through the {path} path."
                )
        self.endpoint_action_status.setText(message)

    def _refresh_connection_page(self) -> None:
        if self.busy_count:
            self.connection_page.set_action_status(
                "Wait for the current router action before refreshing.",
                level="warning",
            )
            return
        self.connection_page.set_action_status(
            "Reading one combined router snapshot, then the endpoint catalog.",
            level="info",
        )
        self._run_task(
            "Refreshing Astrill connection",
            lambda: self.controller.load_connection_state(refresh_servers=True),
            self._connection_state_loaded,
        )

    def _connection_state_loaded(self, result: object) -> None:
        if not isinstance(result, WindowsConnectionState):
            return
        self._apply_server_catalog(result.server_catalog)
        self._status_loaded(result.status)
        self._apply_native_settings(
            result.settings,
            force_native_page=False,
            force_connection_page=False,
        )
        self._set_connection_outcome_status(
            result.status,
            "Connection settings, status, and endpoint capabilities refreshed.",
        )

    def _connection_draft(self) -> ConnectionDraft | None:
        try:
            return self.connection_page.collect()
        except ValueError as exc:
            self.connection_page.set_action_status(str(exc), level="warning")
            QMessageBox.warning(self, "Invalid Astrill connection", str(exc))
            return None

    def _connection_editor_blocked(self) -> bool:
        if not self.native_page.dirty:
            return False
        message = (
            "Save or reload the unsaved Astrill-page draft before changing "
            "overlapping Connection settings."
        )
        self.connection_page.set_action_status(message, level="warning")
        return True

    def _save_connection_draft(
        self,
        draft: ConnectionDraft,
    ) -> NativeAstrillSettings:
        favorites_saved = False
        if draft.favorite_changes:
            self.controller.apply_endpoint_favorite_changes(draft.favorite_changes)
            favorites_saved = True
        try:
            return self.controller.save_astrill_connection(
                draft.selection,
                draft.changes,
            )
        except Exception as exc:
            if favorites_saved:
                raise RuntimeError(
                    "Favorite edits were saved and verified, but the remaining "
                    f"connection settings were not saved: {exc}. Refresh or retry "
                    "the Connection draft."
                ) from exc
            raise

    def _apply_connection_draft(
        self,
        draft: ConnectionDraft,
    ) -> AstrillConnectionResult:
        favorites_saved = False
        if draft.favorite_changes:
            self.controller.apply_endpoint_favorite_changes(draft.favorite_changes)
            favorites_saved = True
        try:
            return self.controller.apply_astrill_connection(
                draft.selection,
                draft.changes,
            )
        except Exception as exc:
            if favorites_saved:
                raise RuntimeError(
                    "Favorite edits were saved and verified, but the Astrill "
                    f"connection attempt failed: {exc}. The favorite edit remains "
                    "saved; refresh or retry the Connection draft."
                ) from exc
            raise

    def _save_connection_page(self) -> None:
        if self._connection_editor_blocked():
            return
        draft = self._connection_draft()
        if draft is None:
            return
        if not self.connection_page.dirty:
            self.connection_page.set_action_status(
                "Connection settings are already synchronized.",
                level="info",
            )
            return
        if self.router_status.get("vpn_state") == "up":
            self.connection_page.set_action_status(
                "The tunnel is connected; use Apply & Reconnect for live changes.",
                level="warning",
            )
            return

        self._run_task(
            "Saving Astrill connection",
            lambda: self._save_connection_draft(draft),
            lambda settings: self._connection_saved(settings, draft),
        )

    def _connection_saved(
        self,
        result: object,
        draft: ConnectionDraft,
    ) -> None:
        if not isinstance(result, NativeAstrillSettings):
            return
        status = dict(self.router_status)
        status["astrill_server_id"] = draft.selection.server_id
        status["astrill_protocol"] = draft.selection.protocol
        self._status_loaded(status)
        self._apply_native_settings(
            result,
            force_native_page=False,
            force_connection_page=True,
        )
        self.connection_page.set_action_status(
            "Connection settings saved and verified"
            + (
                "; favorite edits were fresh-merged with DD-WRT"
                if draft.favorite_changes
                else ""
            )
            + "; the tunnel remains disconnected.",
            level="success",
        )

    def _connect_connection_page(self) -> None:
        if self._connection_editor_blocked():
            return
        if self.connection_page.dirty:
            self.connection_page.set_action_status(
                "Save the draft or use Apply & Connect first.",
                level="warning",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Connect Astrill",
                "Start the router's shared Astrill tunnel with the saved "
                "connection settings?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Connecting Astrill",
            lambda: self.controller.set_connection(True),
            lambda status: self._connection_state_changed(status, connected=True),
        )

    def _apply_connection_page(self) -> None:
        if self._connection_editor_blocked():
            return
        draft = self._connection_draft()
        if draft is None:
            return
        if not self.connection_page.dirty:
            self._connect_connection_page()
            return
        connected = self.router_status.get("vpn_state") == "up"
        verb = "Reconnect" if connected else "Connect"
        server = next(
            (
                candidate
                for candidate in self.controller.server_catalog.servers
                if candidate.id == draft.selection.server_id
            ),
            None,
        )
        server_name = (
            server.name if server is not None else f"Server {draft.selection.server_id}"
        )
        if (
            QMessageBox.warning(
                self,
                f"{verb} Astrill",
                f"{verb} the shared tunnel to {server_name} using "
                f"{ASTRILL_PROTOCOL_NAMES[draft.selection.protocol]} on "
                f"{draft.selection.port}?\n\nThe complete connection draft will "
                "be saved and read back. Favorite edits are fresh-merged with "
                "DD-WRT using concurrent-change protection before the connection "
                "attempt; previous connection settings are recovered if it fails.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            f"{verb}ing Astrill",
            lambda: self._apply_connection_draft(draft),
            lambda result: self._connection_applied(result, draft),
        )

    def _connection_applied(
        self,
        result: object,
        draft: ConnectionDraft | None = None,
    ) -> None:
        if not isinstance(result, AstrillConnectionResult):
            return
        self._status_loaded(result.status)
        self._apply_native_settings(
            result.settings,
            force_native_page=False,
            force_connection_page=True,
        )
        self._set_connection_outcome_status(
            result.status,
            "Astrill connected and every changed connection value verified"
            + (
                "; favorite edits were fresh-merged with DD-WRT"
                if draft is not None and draft.favorite_changes
                else ""
            )
            + ".",
        )

    def _disconnect_connection_page(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Disconnect Astrill",
                "Disconnect the router's shared Astrill tunnel while preserving "
                "its saved endpoint and policies?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Disconnecting Astrill",
            lambda: self.controller.set_connection(False),
            lambda status: self._connection_state_changed(status, connected=False),
        )

    def _connection_state_changed(
        self,
        result: object,
        *,
        connected: bool,
    ) -> None:
        self._status_loaded(result)
        self._set_connection_outcome_status(
            self.router_status,
            (
                "Astrill connected with the saved settings."
                if connected
                else (
                    "Astrill disconnected; saved settings and policies were preserved."
                )
            ),
        )

    def _set_connection_outcome_status(
        self,
        status: dict[str, Any],
        success_message: str,
    ) -> None:
        policy = summarize_policy_runtime(status)
        if policy.degraded:
            self.connection_page.set_action_status(
                self._policy_degraded_message(
                    policy,
                    connected=status.get("vpn_state") == "up",
                ),
                level="warning",
            )
            return
        self.connection_page.set_action_status(success_message, level="success")

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
        self._sync_endpoint_connection_controls()

    def _native_settings_loaded(self, settings: object) -> None:
        if not isinstance(settings, NativeAstrillSettings):
            return
        self._apply_native_settings(settings, force_native_page=True)
        self.statusBar().showMessage(
            "Native Astrill settings loaded and synchronized.", 4000
        )

    def _apply_native_settings(
        self,
        settings: NativeAstrillSettings,
        *,
        force_native_page: bool,
        force_connection_page: bool = False,
    ) -> None:
        self.native_settings = settings
        try:
            favorites = parse_astrill_favorites(settings.get("astrill_favlist"))
        except ValueError:
            self._endpoint_favorite_records = {}
            self._endpoint_favorites_valid = False
            self.endpoint_favorite_status.setText(
                "DD-WRT returned malformed favorites · preserved; editing disabled."
            )
        else:
            self._endpoint_favorite_records = {
                favorite.server_id: favorite for favorite in favorites
            }
            self._endpoint_favorites_valid = True
            count = len(favorites)
            self.endpoint_favorite_status.setText(
                f"{count} router favorite{'' if count == 1 else 's'} synced "
                "from DD-WRT · manual only."
            )
        if (
            self.connection_page.dirty
            and not self.connection_page.has_pending_favorite_changes
        ):
            self.connection_page.merge_external_favorites(settings)
        if force_native_page or not self.native_page.dirty:
            self.native_page.render(settings, self.clients)
        else:
            self.native_page.render_favorite_summary(settings)
        self.connection_page.sync(
            settings,
            self.controller.server_catalog.servers,
            self.router_status,
            force=force_connection_page,
        )
        self._render_endpoints()
        self._sync_endpoint_connection_controls()
        self._sync_endpoint_action_ui()

    def _save_native_settings(self) -> None:
        if self.connection_page.dirty:
            message = (
                "Save or reload the unsaved Connection-page draft before saving "
                "overlapping Astrill settings."
            )
            self.statusBar().showMessage(message, 6000)
            QMessageBox.information(self, "Unsaved Connection settings", message)
            return
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
        self.connection_page.update_status(self.router_status)
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
        policy = summarize_policy_runtime(self.router_status)
        if policy.degraded:
            self.connection_page.set_action_status(
                self._policy_degraded_message(
                    policy,
                    connected=self.router_status.get("vpn_state") == "up",
                ),
                level="warning",
            )
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
        policy = summarize_policy_runtime(status)
        self.metric_labels["controller"].setText(
            "Native Astrill"
            if native and healthy
            else (
                "Policy degraded"
                if policy.degraded
                else ("Healthy" if healthy else "Needs attention")
            )
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
        self._update_policy_metric()
        if tunnel and policy.degraded:
            self.sidebar_status.setText("Astrill connected; policy routing degraded")
        elif policy.degraded:
            self.sidebar_status.setText(
                "Astrill disconnected · policy routing degraded"
            )
        elif healthy:
            sidebar = (
                f"Native Astrill · {'connected' if tunnel else 'disconnected'}"
                if native
                else (
                    "Router companion · "
                    f"{'connected' if tunnel else 'tunnel disconnected'}"
                    + (" · policy ready" if policy.state == "ready" else "")
                )
            )
            self.sidebar_status.setText(sidebar)
        else:
            self.sidebar_status.setText(
                "Router needs attention · "
                f"Astrill {'connected' if tunnel else 'disconnected'}"
            )
        policy_label = (
            "policy ready"
            if policy.state == "ready"
            else ("policy degraded" if policy.degraded else "policy status unavailable")
        )
        self.router_connection_label.setText(
            f"{'Connected' if tunnel else 'Disconnected'} · "
            f"server {server_id} · protocol "
            f"{status.get('astrill_protocol', 'unknown')}"
            + (f" · {policy_label}" if not native else "")
        )
        if native:
            self.companion_label.setText(
                "Native-only mode · the optional companion is not enabled."
            )
            self.companion_label.setToolTip("")
        else:
            runtime_parts = [
                f"Version {status.get('version', 'unknown')}",
                str(status.get("active_chain") or "no active chain"),
                ("watchdog active" if status.get("watchdog") else "watchdog stopped"),
                policy_label,
            ]
            preferences = self._policy_preference_text(policy)
            if preferences:
                runtime_parts.append(preferences)
            self.companion_label.setText(" · ".join(runtime_parts))
            self.companion_label.setToolTip(
                self._policy_runtime_detail(policy, connected=tunnel)
            )
        self._sync_access_ui()

    @staticmethod
    def _policy_preference_text(policy: PolicyRuntimeSummary) -> str:
        values = (
            ("native", policy.native_min_pref),
            ("direct", policy.direct_pref),
            ("VPN", policy.vpn_pref),
        )
        available = [f"{name} {value}" for name, value in values if value is not None]
        return f"priorities {' / '.join(available)}" if available else ""

    @classmethod
    def _policy_runtime_detail(
        cls,
        policy: PolicyRuntimeSummary,
        *,
        connected: bool,
    ) -> str:
        details = [
            f"Policy routing: {policy.state}",
            (
                "Precedence: verified"
                if policy.precedence_ok is True
                else (
                    "Precedence: not verified"
                    if policy.precedence_ok is False
                    else "Precedence: not reported"
                )
            ),
        ]
        preferences = cls._policy_preference_text(policy)
        if preferences:
            details.append(preferences.capitalize())
        if policy.table_readiness:
            details.append(
                "Tables: "
                + ", ".join(
                    f"{name} {'ready' if ready else 'not ready'}"
                    for name, ready in sorted(policy.table_readiness.items())
                )
            )
        if policy.vpn_fail_closed is not None:
            if policy.vpn_fail_closed:
                fail_closed = "verified"
            elif connected and not policy.degraded:
                fail_closed = "inactive while the VPN tunnel is up"
            else:
                fail_closed = "not verified"
            details.append(f"VPN-mark fail-closed: {fail_closed}")
        if policy.last_error:
            details.append(f"Last reconcile error: {policy.last_error}")
        return "\n".join(details)

    @classmethod
    def _policy_degraded_message(
        cls,
        policy: PolicyRuntimeSummary,
        *,
        connected: bool,
    ) -> str:
        reason = policy.last_error
        if not reason and policy.precedence_ok is False:
            reason = "companion policy rules do not precede Astrill's native rules"
        if not reason:
            unavailable = [
                name for name, ready in policy.table_readiness.items() if not ready
            ]
            if unavailable:
                reason = "routing tables not ready: " + ", ".join(unavailable)
        suffix = f": {reason}" if reason else ""
        if connected:
            return (
                "Astrill connected, but policy routing is degraded"
                f"{suffix}. The VPN tunnel is up; bypass/VPN policy selection may "
                "not be enforced."
            )
        return (
            "Astrill disconnected, but policy fail-closed is degraded"
            f"{suffix}. The tunnel is down; VPN-targeted traffic may not be "
            "blocked as intended."
        )

    def _hybrid_policy_action_completed(self, result: object) -> None:
        status: object | None = None
        if isinstance(result, dict):
            nested = result.get("status")
            status = nested if isinstance(nested, dict) else result
        else:
            candidate = getattr(result, "status", None)
            if isinstance(candidate, dict):
                status = candidate
        if isinstance(status, dict):
            self._status_loaded(status)
        else:
            QTimer.singleShot(0, lambda: self._refresh_status(quiet=True))

    def _policy_layer_preflight(
        self,
        rule_ids: tuple[str, ...],
        *,
        layer: str,
        title: str,
    ) -> PolicyCompilationSummary | None:
        preflight_method = getattr(
            self.controller,
            "policy_layer_preflight",
            None,
        )
        if callable(preflight_method):
            summary = preflight_method(rule_ids, layer=layer)
        else:
            summary = self.controller.policy_preflight(rule_ids)
        if summary.can_apply:
            return summary
        QMessageBox.warning(
            self,
            title,
            summary.error or f"The selected {layer} policies cannot be compiled.",
        )
        return None

    @staticmethod
    def _policy_layer_size_text(summary: PolicyCompilationSummary) -> str:
        parts = [f"{summary.compiled_rows:,} compiled rows"]
        if summary.compiled_bytes is not None:
            if summary.limit_bytes is None:
                parts.append(f"{summary.compiled_bytes:,} bytes")
            else:
                parts.append(
                    f"{summary.compiled_bytes:,} / {summary.limit_bytes:,} bytes"
                )
        return " · ".join(parts)

    def _configure_policy_deployment_if_missing(
        self,
        *,
        layer: str,
        rule_ids: tuple[str, ...],
        source: str,
        status: dict[str, Any],
    ) -> None:
        comparison_method = getattr(
            self.controller,
            "hybrid_policy_status",
            None,
        )
        configure_method = getattr(
            self.controller,
            "configure_policy_deployment",
            None,
        )
        if not callable(comparison_method) or not callable(configure_method):
            raise ControllerError(
                "the installed controller cannot bind hybrid policy storage "
                "to this router version"
            )
        comparison = comparison_method(status)
        if getattr(comparison, "manifest", None) is not None:
            return
        configure_method(
            core_rule_ids=rule_ids if layer == "core" else (),
            overlay_rule_ids=rule_ids if layer == "overlay" else (),
            source=source,
            restore_overlay_after_reboot=False,
            status=status,
            host_key=None,
        )

    def _bind_policy_deployment_for_core_replacement(
        self,
        *,
        rule_ids: tuple[str, ...],
        source: str,
        status: dict[str, Any],
    ) -> None:
        comparison_method = getattr(
            self.controller,
            "hybrid_policy_status",
            None,
        )
        configure_method = getattr(
            self.controller,
            "configure_policy_deployment",
            None,
        )
        if not callable(comparison_method) or not callable(configure_method):
            raise ControllerError(
                "the installed controller cannot bind a whole-core replacement "
                "to this router version"
            )
        comparison = comparison_method(status)
        manifest = getattr(comparison, "manifest", None)
        configure_method(
            core_rule_ids=rule_ids,
            overlay_rule_ids=(
                tuple(getattr(manifest, "overlay_rule_ids", ()) or ())
                if manifest is not None
                else ()
            ),
            source=(
                str(getattr(manifest, "source", source) or source)
                if manifest is not None
                else source
            ),
            restore_overlay_after_reboot=bool(
                getattr(manifest, "restore_overlay_after_reboot", False)
            ),
            status=status,
            host_key=None,
        )

    def _pin_selected_to_core(self) -> None:
        selected_ids = self._selected_policy_ids()
        if not selected_ids:
            self._select_something(
                "Select one or more policies for the complete persistent-core "
                "replacement."
            )
            return
        storage = self._hybrid_policy_storage()
        method = self._policy_storage_method("pin")
        configure_method = getattr(
            self.controller,
            "configure_policy_deployment",
            None,
        )
        comparison_method = getattr(
            self.controller,
            "hybrid_policy_status",
            None,
        )
        if (
            method is None
            or storage is None
            or storage.get("_manifest") is None
            and (not callable(configure_method) or not callable(comparison_method))
        ):
            QMessageBox.warning(
                self,
                "Replace persistent core",
                "The installed companion/controller does not support hybrid "
                "persistent-core policy storage.",
            )
            return
        summary = self._policy_layer_preflight(
            selected_ids,
            layer="core",
            title="Replace persistent core",
        )
        if summary is None:
            return
        size_text = self._policy_layer_size_text(summary)
        warning_text = "\n\n" + "\n".join(summary.warnings) if summary.warnings else ""
        current_ids = self._storage_origin_ids(
            self._storage_record(storage.get("core"))
        )
        selected_set = frozenset(selected_ids)
        if current_ids is None:
            diff_text = (
                "The router did not report current core policy IDs. Continuing "
                "replaces the entire current core document; it is not an append."
            )
        else:
            added = selected_set - current_ids
            removed = current_ids - selected_set
            retained = current_ids & selected_set
            diff_lines = [
                f"Current core: {len(current_ids)} policies",
                f"Selected replacement: {len(selected_set)} policies",
                (
                    f"Retained: {len(retained)} · Added: {len(added)} · "
                    f"Removed: {len(removed)}"
                ),
            ]
            if added:
                diff_lines.append("Add: " + self._format_policy_origin_ids(added))
            if removed:
                diff_lines.append("Remove: " + self._format_policy_origin_ids(removed))
            diff_text = "\n".join(diff_lines)
        if (
            QMessageBox.question(
                self,
                "Replace persistent core",
                "Replace the complete global persistent core with the selected "
                "policies?\n\nThis is a whole-document replacement, not an append. "
                "Policies shown as removed stop applying globally after the "
                "verified NVRAM commit.\n\n"
                f"{diff_text}\n\n{size_text}{warning_text}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        status = dict(self.router_status)
        manifest_source = self._policy_overlay_source_value() or "auto"

        def pin() -> object:
            # The whole-core confirmation is the deliberate trust point for the
            # currently displayed base. Binding that exact hash/generation here
            # lets the controller CAS detect any change between review and write.
            self._bind_policy_deployment_for_core_replacement(
                rule_ids=selected_ids,
                source=manifest_source,
                status=status,
            )
            return method(selected_ids)

        self._run_task(
            "Replacing persistent core policies",
            pin,
            self._hybrid_policy_action_completed,
        )

    def _load_selected_into_ram(self) -> None:
        selected_ids = self._selected_policy_ids()
        if not selected_ids:
            self._select_something(
                "Select one or more policies to load into the RAM overlay."
            )
            return
        source = self._require_policy_overlay_source("Load selected into RAM")
        if source is None:
            return
        storage = self._hybrid_policy_storage()
        method = self._policy_storage_method("load")
        configure_method = getattr(
            self.controller,
            "configure_policy_deployment",
            None,
        )
        comparison_method = getattr(
            self.controller,
            "hybrid_policy_status",
            None,
        )
        if (
            method is None
            or storage is None
            or storage.get("_manifest") is None
            and (not callable(configure_method) or not callable(comparison_method))
        ):
            QMessageBox.warning(
                self,
                "Load selected into RAM",
                "The installed companion/controller does not support "
                "owner-scoped RAM overlays.",
            )
            return
        summary = self._policy_layer_preflight(
            selected_ids,
            layer="overlay",
            title="Load selected into RAM",
        )
        if summary is None:
            return
        size_text = self._policy_layer_size_text(summary)
        warning_text = "\n\n" + "\n".join(summary.warnings) if summary.warnings else ""
        if (
            QMessageBox.question(
                self,
                "Load selected into RAM",
                f"Load {len(selected_ids)} selected "
                f"polic{'y' if len(selected_ids) == 1 else 'ies'} for source "
                f"{source}?\n\nThis overlay is volatile and performs no NVRAM "
                f"commit. It disappears when DD-WRT reboots.\n\n"
                f"{size_text}{warning_text}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        status = dict(self.router_status)

        def load() -> object:
            self._configure_policy_deployment_if_missing(
                layer="overlay",
                rule_ids=selected_ids,
                source=source,
                status=status,
            )
            return method(selected_ids, source=source)

        def loaded(result: object) -> None:
            self._overlay_source_user_edited = False
            self._hybrid_policy_action_completed(result)

        self._run_task(
            "Loading this computer's RAM overlay",
            load,
            loaded,
        )

    def _restore_ram_overlay_now(self) -> None:
        storage = self._hybrid_policy_storage()
        method = self._policy_storage_method("restore")
        manifest = storage.get("_manifest") if storage is not None else None
        saved_overlay = bool(
            manifest is not None
            and tuple(getattr(manifest, "overlay_rule_ids", ()) or ())
            and getattr(manifest, "overlay_hash", None)
        )
        if method is None or storage is None or not saved_overlay:
            QMessageBox.warning(
                self,
                "Restore RAM overlay now",
                "No trusted, source-bound RAM overlay is saved for this "
                "controller. Load a selected overlay first.",
            )
            return
        source = self._require_saved_policy_overlay_source(
            "Restore RAM overlay now",
            storage,
        )
        if source is None:
            return
        self._run_task(
            f"Restoring this computer's RAM overlay for {source}",
            method,
            self._hybrid_policy_action_completed,
        )

    def _remove_this_overlay(self) -> None:
        storage = self._hybrid_policy_storage()
        method = self._policy_storage_method("remove")
        if storage is None or storage.get("_manifest") is None or method is None:
            QMessageBox.warning(
                self,
                "Remove this overlay",
                "The installed companion/controller cannot remove an "
                "owner-scoped RAM overlay.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Remove this overlay",
                "Remove only this computer's volatile RAM overlay?\n\n"
                "The persistent core and overlays owned by other paired "
                "controllers remain unchanged.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Removing this computer's RAM overlay",
            method,
            self._hybrid_policy_action_completed,
        )

    def _policy_auto_restore_toggled(self, checked: bool) -> None:
        if self._syncing_policy_storage:
            return
        method = self._policy_storage_method("auto_restore")
        storage = self._hybrid_policy_storage()
        previous = not checked
        manifest = storage.get("_manifest") if storage is not None else None
        saved_overlay = bool(
            manifest is not None
            and tuple(getattr(manifest, "overlay_rule_ids", ()) or ())
            and getattr(manifest, "overlay_hash", None)
        )
        source: str | None = None
        if checked:
            if not saved_overlay:
                self._syncing_policy_storage = True
                self.policy_auto_restore_check.setChecked(previous)
                self._syncing_policy_storage = False
                QMessageBox.warning(
                    self,
                    "Auto-restore RAM overlay",
                    "Load a selected, source-bound RAM overlay before enabling "
                    "automatic restore.",
                )
                return
            source = self._require_saved_policy_overlay_source(
                "Auto-restore RAM overlay",
                storage,
            )
            if source is None:
                self._syncing_policy_storage = True
                self.policy_auto_restore_check.setChecked(previous)
                self._syncing_policy_storage = False
                return
            if (
                QMessageBox.question(
                    self,
                    "Auto-restore RAM overlay",
                    "Save an explicit opt-in to restore this computer's "
                    f"source-bound overlay ({source}) once after a new router "
                    "runtime is observed?\n\nThis does not enable periodic SSH "
                    "polling.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                self._syncing_policy_storage = True
                self.policy_auto_restore_check.setChecked(previous)
                self._syncing_policy_storage = False
                return
        if method is None or storage is None or manifest is None:
            self._syncing_policy_storage = True
            self.policy_auto_restore_check.setChecked(previous)
            self._syncing_policy_storage = False
            QMessageBox.warning(
                self,
                "Auto-restore RAM overlay",
                "The controller cannot persist the hybrid overlay auto-restore "
                "preference.",
            )
            return

        def revert(_message: str) -> None:
            self._syncing_policy_storage = True
            self.policy_auto_restore_check.setChecked(previous)
            self._syncing_policy_storage = False

        status = dict(self.router_status)
        self._run_task(
            (
                "Enabling RAM overlay auto-restore"
                if checked
                else "Disabling RAM overlay auto-restore"
            ),
            lambda: method(checked, status=status),
            self._hybrid_policy_action_completed,
            failure=revert,
        )

    def _apply_policies(self) -> None:
        self._apply_policy_scope(None)

    def _apply_selected_policies(self) -> None:
        selected_ids = self._selected_policy_ids()
        if not selected_ids:
            QMessageBox.information(
                self,
                "Apply selected policies",
                "Select one or more policy rows first.",
            )
            return
        self._apply_policy_scope(selected_ids)

    def _apply_policy_scope(
        self,
        rule_ids: tuple[str, ...] | None,
    ) -> None:
        preflight = self.controller.policy_preflight(rule_ids)
        if not preflight.can_apply:
            QMessageBox.warning(
                self,
                "Policies exceed router capacity"
                if preflight.compiled_bytes is not None
                else "Policies cannot be compiled",
                preflight.error or "The policy document cannot be applied.",
            )
            self.policy_preflight = self.controller.policy_preflight()
            self._render_policy_capacity(self.policy_preflight)
            self._sync_policy_apply_ui()
            return

        selected_scope = rule_ids is not None
        subject = (
            f"the {preflight.rule_count} selected policies"
            if selected_scope
            else f"all {preflight.rule_count} saved policies"
        )
        local_note = (
            "\n\nUnselected policies remain saved in Windows but will not be "
            "installed on this router."
            if selected_scope
            else ""
        )
        warning_note = (
            "\n\nCompiler notes:\n- " + "\n- ".join(preflight.warnings)
            if preflight.warnings
            else ""
        )
        if (
            QMessageBox.question(
                self,
                "Apply policies",
                f"Transactionally install {subject} on DD-WRT?\n\n"
                f"{preflight.enabled_count} enabled policies compile to "
                f"{preflight.compiled_rows:,} rows and "
                f"{preflight.compiled_bytes:,} / "
                f"{preflight.limit_bytes:,} bytes."
                f"{local_note}{warning_note}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_task(
            "Applying router policies",
            lambda: self.controller.apply_rules(rule_ids),
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
        self._sync_policy_apply_ui()
        self.native_page.set_read_only(read_only)
        self.native_page.set_busy(self.busy_count != 0)
        self.connection_page.set_read_only(read_only)
        self.connection_page.set_busy(self.busy_count != 0)
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
